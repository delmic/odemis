# -*- coding: utf-8 -*-
'''
Created on 18 Mar 2020

@author: Philip Winkler

Copyright © 2020, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import glob
import logging
from odemis import model
from odemis.model import Actuator, CancellableThreadPoolExecutor
from odemis.util import to_str_escape
import os
import threading
import time
import serial
from threading import Thread
import copy
from odemis.model import HwError
from odemis.util.driver import getSerialDriver

BAUDRATE = 115200
# TODO: Is this always the same for all encoders? Otherwise, it needs to be a parameters in the yaml file.
# WFM_STEPSIZE = 5e-6  # m / step
USTEPS_PER_WFM = 8192
# TODO: set encoder stepsize to proper value
ENCODER_STEPSIZE = 1e-9  # m / step
DEFAULT_AXIS_SPEED = 0.01  # m / s

EOL = b'\r'  # 0x0D, carriage return
sEOL = '\r'  # string version of EOL

# Waveforms
WAVEFORM_RHOMB = 1  # fast max speed
WAVEFORM_DELTA = 2  # preferred, higher accuracy
WAVEFORM_PARK = 4  # power off

class PMDError(Exception):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(PMDError, self).__init__(errno, strerror, *args, **kwargs)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return self.args[1]


class PMD401Bus(Actuator):
    """
    This represents the PMD401 motor controller bus for the PiezoMotor LEGS motors. It supports multiple controllers
    (each one handling one axis), connected in a daisy chain. Only the first
    controller is directly connected to the computer.
    The specification for the hardware interface can be found in the document
     "PiezoMotor_PMD401_Technical_Manual.pdf".
    """

    def __init__(self, name, role, port, axes, inverted=None, **kwargs):
        """
        :param axes (dict: {"x", "y", "z"} --> dict): axis name --> axis parameters (address, range, unit, closed_loop, speed, wfm_stepsize)
            address (0 <= int <= 127), required (typically 1-3 for x-z)
            wfm stepsize (float), waveform stepsize
            range (tuple of float), default to [-1, 1]
            unit (str), default to m
            closed_loop (bool): True for closed loop (with encoder), default to True
            speed (float): speed in m/s
        """
        self._axis_map = {}  # axis name -> axis number used by controller
        self._closed_loop = {}  # axis name (str) -> bool (True if closed loop)
        self._wfm_stepsizes = {}

        # Parse axis parameters
        axes_def = {}  # axis name -> Axis object

        for axis_name, axis_par in axes.items():
            # Axis number
            try:
                axis_num = axis_par['address']
            except KeyError:
                raise ValueError("Axis %s has no axis number." % axis_name)
            if axis_num not in range(128):
                raise ValueError("Invalid axis number %s, needs to be 0 <= int <= 127." % axis_num)
            elif axis_num in self._axis_map.values():
                raise ValueError("Invalid axis number %s, already assigned to axis %s." % (axis_num, self._axis_map[axis_num]))
            else:
                self._axis_map[axis_name] = axis_par['axis_number']

            # WFM stepsize
            try:
                self._wfm_stepsizes[axis_name] = axis_par['wfm_stepsize']  # approximately 5e-6 m / step
            except KeyError:
                raise ValueError("Axis %s has no wfm stepsize." % axis_name)

            # Axis range
            try:
                axis_range = axis_par['range']
            except KeyError:
                logging.info("Axis %s has no range. Assuming (-1, 1)", axis_name)
                axis_range = (-1, 1)

            # Axis unit
            try:
                axis_unit = axis_par['unit']
            except KeyError:
                axis_unit = "m"
                logging.info("Axis %s has no unit. Assuming %s", axis_name, axis_unit)

            # Axis speed
            try:
                self._speed = axis_par['speed']
            except KeyError:
                # m if linear, "rad" otherwise
                self._speed = DEFAULT_AXIS_SPEED
                logging.info("Axis %s was not given a speed value. Assuming %s", axis_name, self._speed)

            # Axis mode (closed loop/ open loop)
            try:
                closed_loop = axis_par['closed_loop']
            except KeyError:
                closed_loop = False
                logging.info("Axis mode (closed/open loop) not specified for axis %s. Assuming closed loop.", axis_name)
            self._closed_loop[axis_name] = closed_loop

            ad = model.Axis(canAbs=closed_loop, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad

        Actuator.__init__(self, name, role, axes=axes, inverted=inverted, **kwargs)
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time
        self._ser_access = threading.RLock()

        # Connect to hardware
        self._port = None  # port number
        min_axis = min(self._axis_map.values())
        self._serial = self._findDevice(port, min_axis)
        self._recovering = False

        # Get version
        hwVersions = []
        for ax_name, ax_num in self._axis_map.items():
            ver = self.getVersion(ax_num)
            sn = self.getSerialNumber(ax_num)
            hwVersions.append("Axis %s ('%s') version: %s, " % (ax_num, ax_name, ver) +
                              "serial number: %s" % sn)
        self._hwVersion = ", ".join(hwVersions)

        # Configuration
        for axis in self._axis_map.values():
            self.setWaveform(axis, WAVEFORM_DELTA)

        driver_name = getSerialDriver(self._port)
        self._swVersion = "serial driver: %s" % (driver_name,)

        # Position and referenced VAs
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self.referenced = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()
        for ax, closed_loop in self._closed_loop:  # only add closed_loop axes to .referenced
            if closed_loop:
                self.referenced.add({ax: False})  # just assume they haven't been referenced


    def terminate(self):
        # terminate can be called several times, do nothing if ._serial is already None
        if self._serial is None:
            return
        self._serial = None
        for axis in self._axis_map.values():
            self.setWaveform(axis, WAVEFORM_PARK)  # power off
        self._serial.close()

    def stop(self, axes=None):
        self._executor.cancel()
        axes = axes or self.axes
        for ax in axes:
            self.stopAxis(ax)

    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)
        f = self._executor.submit(self._doMoveRel, shift)
        return f

    def moveAbs(self, pos):
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)
        f = self._executor.submit(self._doMoveAbs, pos)
        return f

    def reference(self, axes):
        self._checkReference(axes)
        f = self._executor.submit(self._doReference, axes)
        return f

    def _doReference(self, axes):
        self._check_hw_error()
        for axname in axes:
            self.startIndexMode(self._axis_map[axname])
            self.moveToIndex(self._axis_map[axname])
            if 'index' not in self.getIndexStatus(self._axis_map[axname]):
                self.stop()  # exit index mode
                raise ValueError("Referencing axis %s failed." % self._axis_map[axname])
            self.stop()  # exit index mode
            self.referenced[axname] = True

        # TODO: Note that this is a really basic referencing procedure. You typically want to do a fast search,
        #  and then do a second search at slow speed, to locate the index more precisely. Also, if the index is
        #  somewhere in the middle, you'll need to handle the fact you might have picked the wrong direction
        #  (in which case, after while you'll bump into the limit switch) and in such case, continue by going in
        #  the other direction.

    def _doMoveAbs(self, pos):
        self._check_hw_error()
        targets = {}
        for axis, val in pos.items():
            if self._closed_loop[axis]:
                encoder_cnts = round(val / ENCODER_STEPSIZE)
                speed_usteps = round(self._speed / self._wfm_stepsizes[axis])  # wfm steps / second
                self._sendCommand(b'X%dT%d,%d' % (self._axis_map[axis], encoder_cnts, speed_usteps))
                targets[axis] = encoder_cnts
            else:
                target = val - self.position.value[axis]
                wfm_steps = int(target / self._wfm_stepsizes[axis])  # number of waveform steps
                usteps = int((target % self._wfm_stepsizes[axis]) * USTEPS_PER_WFM)   # number of µsteps
                self._sendCommand(b'X%dJ%d,%d,%d' % (self._axis_map[axis], wfm_steps, usteps, speed_usteps))
        self._waitEndMotion(targets)
        self._updatePosition()

    def _doMoveRel(self, shift):
        self._check_hw_error()
        targets = {}
        self._updatePosition()
        for axis, val in shift.items():
            speed_usteps = round(self._speed / self._wfm_stepsizes[axis] * USTEPS_PER_WFM)  # steps / second
            if self._closed_loop[axis]:
                encoder_cnts = round(val / ENCODER_STEPSIZE)
                self.runRelTargetMove(self._axis_map[axis], encoder_cnts)
                targets[axis] = self.position.value[axis] / ENCODER_STEPSIZE + encoder_cnts
            else:
                wfm_steps = int(val / self._wfm_stepsizes[axis])  # number of waveform steps
                usteps = int((val % self._wfm_stepsizes[axis]) * USTEPS_PER_WFM)   # number of µsteps

                targets[axis] = None
        self._waitEndMotion(targets)
        self._updatePosition()

    def _waitEndMotion(self, targets):
        """
        Wait until move is done
        :arg targets (dict: str --> int): target (for closed-loop), None for open loop
        """
        # Expected time for move
        move_length = max(abs(self.position[ax] - target) for ax, target in targets.items())
        dur = move_length * self._speed
        max_dur = max(dur * 2, 0.1)  # wait at least 0.1 s
        logging.debug("Expecting a move of %g s, will wait up to %g s", dur, max_dur)

        for ax, target in targets.items():
            moving = True
            end_time = time.time() + 5  # 5 s until timeout
            while moving:
                if time.time() > end_time:
                    raise IOError("Timeout while waiting for end of motion on axis %s" % ax)
                if self._closed_loop[ax]:
                    if target is None:
                        raise ValueError("No target provided for closed-loop move on axis %s." % ax)
                    moving = self.isMovingClosedLoop(ax, target)
                else:
                    moving = self.isMovingOpenLoop(ax)
                self._check_hw_error()
                time.sleep(0.05)

    def _check_hw_error(self):
        for ax, axnum in self._axis_map.items():
            status = self.getStatus(axnum)
            if status[0] & 8:
                raise PMDError(1, "Communication Error (wrong baudrate, data collision, or buffer overflow)")
            elif status[0] & 4:
                raise PMDError(2, "Encoder error(serial communication or reported error from serial encoder)")
            elif status[0] & 2:
                raise PMDError(3, "Supply voltage or motor fault was detected.")
            elif status[1] & 1:
                raise PMDError(4, "Command timeout occurred or a syntax error was detected when response was not allowed.")
            elif status[1] & 8:
                raise PMDError(5, "Power-on/reset has occurred.")

    def _updatePosition(self):
        """
        update the position VA
        """
        pos = {}
        for axname, axis in self._axis_map.items():
            pos[axname] = self.getEncoderPosition(axis)
        logging.debug("Reporting new position at %s", pos)
        self.position._set_value(pos, force_write=True)

    def stopAxis(self, axis):
        self._sendCommand(b'X%dS' % self._axis_map[axis])

    def getVersion(self, axis):
        """
        :returns (str): controller type and firmware version, e.g. 'PMD401 V13'
        """
        return self._sendCommand(b'X%d?' % axis)

    def getSerialNumber(self, axis):
        """
        :returns (str): serial number
        """
        return self._sendCommand(b'X%dY42' % axis)

    def getEncoderPosition(self, axis):
        """
        :returns (float): current position of the axis as reported by encoders (in m)
        """
        return int(self._sendCommand(b'X%dE' % axis)) * ENCODER_STEPSIZE

    def runRelTargetMove(self, axis, encoder_cnts):
        """

        """


        # There are two possibilities: move relative to current position (XC) and move relative to
        # target position (XR). We are moving relative to the current position (might be more intuitive
        # if something goes wrong and we're stuck in the wrong position).
        self._sendCommand(b'X%dC%d,%d' % (axis, encoder_cnts, wfm_steps))

    def runMotorJog(self, axis, encoder_cnts):
        """
        Open loop stepping.
        """

        self._sendCommand(b'X%dJ%d,%d,%d' % (self._axis_map[axis], wfm_steps, usteps, speed_usteps))

    def setWaveform(self, axis, wf):
        """
        :arg wf (waveform): waveform to set
        """
        if wf not in (WAVEFORM_DELTA, WAVEFORM_RHOMB, WAVEFORM_PARK):
            raise ValueError("wf %s not a valid waveform" % wf)

        self._sendCommand(b'X%dM%d' % (axis, wf))

    def getStatus(self, axis):
        """
        :returns (list of 4 int): 4-bit status code
        The most important values are the following
        First bit:
        8: communication error (wrong baudrate, data collision, or buffer overflow)
        4: encoder error (serial communication or reported error from serial encoder)
        2: voltage error (supply voltage or motor fault was detected)
        1: command error (command timeout occurred or a syntax error was detected when response was not allowed)

        Second bit:
        8: reset (power on/ reset occurred)
        1: index (index signal was detected since last report)

        For all codes, please refer to the PMD-401 manual.
        """
        return [int(i) for i in self._sendCommand(b'X%dU0' % axis)]

    def isMovingClosedLoop(self, axis, target):
        """
        :param axis (int): axis number
        :param target (float): target position for axes
        :returns (bool): True if moving, False otherwise
        """
        resp = self._sendCommand(b'X%dE' % axis)
        if abs(float(target) - float(resp)) < 1e-9:  # 1 nm accuracy
            return True
        else:
            return False

    def isMovingOpenLoop(self, axis):
        """
        :param axis (int): axis number
        :returns (bool): True if moving, False otherwise
        """
        resp = self._sendCommand(b'X%dJ' % axis)  # will be 1 if finished, otherwise 0
        return int(resp) == 0

    def startIndexMode(self, axis):
        """
        Enters index mode.
        """
        self._sendCommand(b'X%dN4' % axis)

    def moveToIndex(self, axis):
        """
        Move towards the index until it's found.
        """
        self._sendCommand(b'X%dI-16' % axis)

    def getIndexStatus(self, axis):
        """
        Returns a description of the index status.
        :returns (tuple of 4):
            mode (int): index mode (1 if position has been reset at index)
            position (float):
            logged (bool): position was logged since last report
            indexed (bool): position has been reset at index
        """
        # Check if referencing was successful
        # Response looks like this: "1,132.,indexed"
        try:
            ret = self._sendCommand(b'X%dN?' % axis).split(',')
            mode = int(ret[0])
            if ret[1][-1] == '.':
                # . means position was logged since last report
                logged = True
                position = ret[1][:-1]
            else:
                logged = False
                position = ret[1]
            if len(ret) > 2 and ret[2] == 'indexed':
                indexed = True
            else:
                indexed = False
            return mode, position, logged, indexed
        except Exception as ex:
            logging.error("Failed to parse index status %s: %s" % (ret, ex))

    def setAxisAddress(self, current_address, new_address):
        """
        Set the address of the axis. The factory default is 0 for all boards. Don't use this
        command if multiple axes with the same number are connected.
        :arg current_address (int): current axis number
        :arg new_address (int): new axis number
        """
        self._sendCommand("X%dY40,%d" % (current_address, new_address))

    def runAutoConf(self, axis):
        """
        Runs automatic configuration for the encoder parameters.
        :arg axis (int): axis number
        """
        self._sendCommand("X%dY25" % axis)

    def writeParamsToFlash(self, axis):
        self._sendCommand("X%dY32" % axis)

    def setParam(self, axis, param, value):
        self._sendCommand("X%dY%d,%d" % (axis, param, value))

    def _sendCommand(self, cmd):
        """
        :arg cmd (bytes): command to be sent to the hardware
        :returns (bytes): response
        """
        cmd += EOL
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)

            resp = b""
            while resp[-len(EOL):] != EOL:
                try:
                    char = self._serial.read()
                except IOError:
                    logging.warn("Failed to read from PMT Control firmware, "
                                 "trying to reconnect.")
                    if self._recovering:
                        raise
                    else:
                        self._tryRecover()
                        # don't send command again
                        raise IOError("Failed to read from PMT Control firmware, "
                                      "restarted serial connection.")
                if not char:
                    raise IOError("Timeout after receiving %s" % to_str_escape(resp))
                else:
                    resp += char
            logging.debug("Received response %s", to_str_escape(resp))

            # Check response (command should be echoed back)
            if not resp.startswith(cmd[:-len(EOL)]):
                raise IOError("Response starts with %s != %s", resp[:len(cmd)], cmd)
            if b"_??_" in resp:
                raise ValueError("Received response %s, command %s not understood." % (resp, cmd))
            if b"!" in resp:
                raise PMDError(0, resp)
            # Format:
            #    * for query with response: <cmd>:<ret><EOL> (will return <ret>)
            #    * for set command without response: <cmd><EOL> (will return b"")
            return resp[len(cmd) + 1 - len(EOL):-len(EOL)].decode("latin1")

    def _tryRecover(self):
        self._recovering = True
        self.state._set_value(HwError("Connection lost, reconnecting..."), force_write=True)
        # Retry to open the serial port (in case it was unplugged)
        # _ser_access should already be acquired, but since it's an RLock it can be acquired
        # again in the same thread
        try:
            with self._ser_access:
                while True:
                    try:
                        self._serial.close()
                        self._serial = None
                    except Exception:
                        pass
                    try:
                        logging.debug("Searching for the device on port %s", self._portpattern)
                        min_axis = min(self._axis_map.values())
                        self._port = self._findDevice(self._portpattern, min_axis)
                    except IOError:
                        time.sleep(2)
                    except Exception:
                        logging.exception("Unexpected error while trying to recover device")
                        raise
                    else:
                        # We found it back!
                        break
            # it now should be accessible again
            self.state._set_value(model.ST_RUNNING, force_write=True)
            logging.info("Recovered device on port %s", self._port)
        finally:
            self._recovering = False

    def _findDevice(self, port, address=0):
        """
        Look for a compatible device
        port (str): pattern for the port name
        address (None or int): the address of the stage controller
        return (serial, int): the (opened) serial port used, and the actual address
        raises:
            IOError: if no device are found
        """
        if port.startswith("/dev/fake"):
            names = [port]
        elif os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(port)

        for n in names:
            try:
                serial = self._openSerialPort(n)
            except IOError as ex:
                # not possible to use this port? next one!
                logging.info("Skipping port %s, which is not available (%s)", n, ex)
                continue

            # check whether it answers with the right address
            try:
                # If any garbage was previously received, make it discarded.
                self._serial = serial
                self._serial.flush()
                if 'PMD401 ' in self.getVersion(address):
                    self._port = n
                    return serial  # found it!
            except Exception as ex:
                logging.debug("Port %s doesn't seem to have a TMCM device connected: %s",
                              n, ex)
            serial.close()  # make sure to close/unlock that port
        else:
            raise IOError("Failed to find a PMD controller on ports '%s'. "
                          "Check that the device is turned on and "
                          "connected to the computer." % (port,))


    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a PiezoMotor PMD device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        raise HwError: if the serial port cannot be opened (doesn't exist, or
          already opened)
        """
        # For debugging purpose
        if port == "/dev/fake":
            return PMDSimulator(timeout=0.1)

        try:
            ser = serial.Serial(
                port=port,
                baudrate=BAUDRATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.3,  # s
            )
        except IOError:
            raise HwError("Failed to find a PMD controller on port '%s'. "
                          "Check that the device is turned on and "
                          "connected to the computer." % (port,))

        return ser


class PMDSimulator(object):
    """
    Simulates beamshift controller with three axes on axes 1, 2 and 3.
    """
    def __init__(self, timeout=0.3):
        self.timeout = timeout
        self._input_buf = ""  # use str internally instead of bytes, makes indexing easier
        self._output_buf = ""

        self.waveform = {1: WAVEFORM_PARK, 2: WAVEFORM_PARK, 3: WAVEFORM_PARK}
        self.target_pos = {1: 0, 2: 0, 3: 0}
        self.current_pos = {1: 0, 2: 0, 3: 0}
        self.speed = 0
        self.is_moving = False

    def write(self, data):
        self._input_buf += data.decode('ascii')
        msg = ""
        while self._input_buf[:len(EOL)] != sEOL:
            msg += self._input_buf[0]
            self._input_buf = self._input_buf[1:]
        self._input_buf = self._input_buf[len(EOL):]  # remove EOL

        self._parseMessage(msg)

    def read(self, size=1):
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        if len(ret) < size:
            # simulate timeout
            time.sleep(self.timeout)
        return ret.encode('ascii')

    def flush(self):
        self._input_buf = ""

    def flushInput(self):
        self._output_buf = ""

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _parseMessage(self, msg):
        """
        :arg msg (str): the message to parse
        :returns (None): self._output_buf is updated if necessary
        """
        # Message structure:
        # X<axis><cmd><EOL> or
        # X<cmd><EOL> or
        # X<axis><cmd><arg0>,...,<argN><EOL>
        # Axis can in principle have multiple digits, but we only care about 1-3, so let's keep
        # it simple
        logging.debug("Received message %s" % msg)

        if msg[0] != "X":
            self._output_buf += "_??_%s" % msg[1:]
            logging.error("Command %s doesn't start with 'X'.", msg)
            return
        try:  # first symbol is axis number
            axis = int(msg[1])
            cmd = msg[2]
            args = msg[3:].split(',')
        except ValueError:
            # msg[1] is not an int --> axis number 0 assumed
            axis = 0
            cmd = msg[1]
            args = msg[2:].split(',')
        args = [] if args == [''] else args

        # Message is always echoed back
        self._output_buf += msg

        try:
            if cmd == "M":
                if not args:
                    self._output_buf += ":%d" % self.waveform[axis]
                elif len(args) == 1:
                    self.waveform = int(args[0])
                else:
                    raise ValueError()
            elif cmd == "?":
                if not args:
                    self._output_buf += ":PMD401 V1"
                else:
                    raise ValueError()
            elif cmd == "T":
                if not args:
                    self._output_buf += ":%d" % self.target_pos[axis]
                elif len(args) == 1:
                    self.target_pos[axis] = int(args[0])
                    self.move()
                elif len(args) == 2:
                    self.target_pos[axis] = int(args[0])
                    self.speed = int(args[1])
                    self.move()
                else:
                    raise ValueError()
            elif cmd == "S":
                self.is_moving = False
            elif cmd == "C":
                if not args:
                    self._output_buf += ":%d" % self.target_pos[axis]
                elif len(args) == 1:
                    self.target_pos[axis] += int(args[0])
                    self.move()
                elif len(args) == 2:
                    self.target_pos[axis] += int(args[0])
                    self.speed = int(args[1])
                    self.move()
                else:
                    raise ValueError()
            elif cmd == "E":
                if not args:
                    self._output_buf += ":%d" % self.current_pos[axis]
                elif len(args) == 1:
                    self.target_pos[axis] += int(args[0])
                else:
                    raise ValueError()
            elif cmd == "J":
                if not args:
                    self._output_buf += ":%d" % self.target_pos[axis]
                elif len(args) == 1:
                    pass  # simulate move
                elif len(args) == 2:
                    pass # simulate move
                elif len(args) == 3:
                    # simulate move
                    self.speed = int(args[1])
                else:
                    raise ValueError()
            elif cmd == "Y":
                if len(args) == 1:
                    if int(args[0]) == 42:  # serial number
                        self._output_buf += "12345678"
            elif cmd == "U":
                self._output_buf += ":0000"
            else:
                # Syntax error is indicated by inserting _??_ in the response
                self._output_buf = self._output_buf[:-len(msg)]
                self._output_buf += "X%s_??_%s" % (axis, ''.join(args))  # args can be str or list of str
                logging.error("Unknown command %s" % cmd)
        except ValueError as ex:
            # Assume something is wrong with the arguments
            self._output_buf = self._output_buf[:-len(msg)]
            self._output_buf += "X%s%s_??_" % (axis, cmd)
            logging.error("Parsing %s failed with exception %s" % (msg, ex))

        self._output_buf += sEOL

    def move(self):
        # simple move, same duration for every length, don't care about speed
        t = Thread(target=self._do_move)
        t.start()

    def _do_move(self):
        time.sleep(1)
        self.current_pos = copy.deepcopy(self.target_pos)