# -*- coding: utf-8 -*-
"""
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
"""
from __future__ import division

import glob
import logging
from odemis import model
from odemis.model import Actuator, CancellableThreadPoolExecutor, CancellableFuture
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
DEFAULT_AXIS_SPEED = 0.01  # m / s

EOL = b'\r'  # 0x0D, carriage return
sEOL = '\r'  # string version of EOL

# Waveforms
WAVEFORM_RHOMB = 1  # fast max speed
WAVEFORM_DELTA = 2  # preferred, higher accuracy
WAVEFORM_PARK = 4  # power off

# Length of the rod available for moves
STROKE_RANGE = 23.1e-3  # m

# There are two different units for a move: motor (waveform) steps and encoder counts.
# The encoder counts are fixed and given by the encoder resolution. The motor steps
# vary depending on the load of the motor. Therefore, a calibration is required before
# the first use of the driver to determine the conversion factor between encoder counts
# and motor steps. This conversion factor can be stored in the controller flash memory.
# A motor step is equivalent to 8192 microsteps.
DEFAULT_COUNTS_PER_METER = 1 / 1.25e-6  # TODO: update default value (current value is for starter kit)
DEFAULT_SPC = 1.25e-6 / 4.5e-6  # steps per count (encoder steplength / motor steplength)
USTEPS_PER_STEP = 8192


class PMDError(Exception):
    def __init__(self, errno, strerror, *args):
        super(PMDError, self).__init__(errno, strerror, *args)
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
        :param axes (dict: {"x", "y", "z"} --> dict): axis name --> axis parameters
            Each axis is specified by a set of parameters.
            After successful configuration with the pmconfig.py script, the only required parameter for a default motor
            is the address which was set during the configuration process.
            The spc parameter (conversion between motor steps and encoder counts) is typically saved in the flash
            memory of the controller during the configuration process. The flash value is overridden by the
            value in the parameter dict.
            Depending on the type of motor, the encoder_resolution and range might need to be adjusted.

            Axis parameters:
                axis_number (0 <= int <= 127): typically 1-3 for x-z, required
                closed_loop (bool): True for closed loop (with encoder), default to True
                encoder_resolution (float): encoder resolution in m/step
                spc (float): motor steps per encoder count, default to value in non-volatile memory
                limit_type (0 <= int <= 2): type of limit switch, 0: no limit, 1: active high, 2: active low, default 0
                range (tuple of float): in m, default to (0, STROKE_RANGE)
                speed (float): speed in m/s
                unit (str), default to m
        """
        self._axis_map = {}  # axis name -> axis number used by controller
        self._closed_loop = {}  # axis name (str) -> bool (True if closed loop)
        self._speed_steps = {}  # axis name (str) -> int, speed in steps per meter
        self._portpattern = port

        # Conversion factors
        # Count refers to encoder counts, step refers to motor steps. The encoder counts are fixed and given
        # as a parameter to the axis, the motor counts are determined during configuration and are usually
        # stored in flash memory.
        # ._steps_per_meter is redundant, but convenient
        self._steps_per_count = {}  # axis name (str) -> float
        self._steps_per_meter = {}  # axis name (str) -> float
        self._counts_per_meter = {}  # axis name (str) -> float

        # Parse axis parameters and create axis
        axes_def = {}  # axis name -> Axis object
        for axis_name, axis_par in axes.items():
            if 'axis_number' in axis_par:
                axis_num = axis_par['axis_number']
                if axis_num not in range(128):
                    raise ValueError("Invalid axis number %s, needs to be 0 <= int <= 127." % axis_num)
                elif axis_num in self._axis_map.values():
                    axname = self._axis_map[axis_num]
                    raise ValueError("Invalid axis number %s, already assigned to axis %s." % (axis_num, axname))
                else:
                    self._axis_map[axis_name] = axis_par['axis_number']
            else:
                raise ValueError("Axis %s has no axis number." % axis_name)

            if 'closed_loop' in axis_par:
                closed_loop = axis_par['closed_loop']
            else:
                closed_loop = False
                logging.info("Axis mode (closed/open loop) not specified for axis %s. Assuming closed loop.", axis_name)
            self._closed_loop[axis_name] = closed_loop

            if 'encoder_resolution' in axis_par:
                self._counts_per_meter[axis_name] = 1 / axis_par['encoder_resolution']  # approximately 5e-6 m / step
            else:
                self._counts_per_meter[axis_name] = DEFAULT_COUNTS_PER_METER
                logging.info("Axis %s has no encoder resolution, assuming %s." % (axis_name, 1 / DEFAULT_COUNTS_PER_METER))

            if 'limit_type' in axis_par:
                limit_type = axis_par['limit_type']
            else:
                logging.info("Axis %s has not limit switch." % axis_name)
                limit_type = 0

            if 'range' in axis_par:
                axis_range = axis_par['range']
            else:
                axis_range = (0, STROKE_RANGE)
                logging.info("Axis %s has no range. Assuming %s", axis_name, axis_range)

            if 'spc' in axis_par:
                self._steps_per_count[axis_name] = axis_par['spc']  # approximately 5e-6 m / step
            else:
                logging.info("Axis %s has no spc parameter, will use value from flash." % axis_name)
                # None for now, will read value from flash later.
                self._steps_per_count[axis_name] = None

            if 'speed' in axis_par:
                self._speed = axis_par['speed']
            else:
                self._speed = DEFAULT_AXIS_SPEED
                logging.info("Axis %s was not given a speed value. Assuming %s", axis_name, self._speed)

            if 'unit' in axis_par:
                axis_unit = axis_par['unit']
            else:
                axis_unit = "m"
                logging.info("Axis %s has no unit. Assuming %s", axis_name, axis_unit)

            ad = model.Axis(canAbs=closed_loop, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad

        Actuator.__init__(self, name, role, axes=axes_def, inverted=inverted, **kwargs)
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
        self._swVersion = "Serial driver: %s" % (driver_name,)

        # Position and referenced VAs
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self.referenced = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()
        for axname in self._axis_map.keys():
            self.referenced.value[axname] = False  # just assume they haven't been referenced

        # Load values from flash, write spc if provided, otherwise read spc
        for axname, axis in self._axis_map.items():
            # Load values from flash (most importantly spc parameter)
            self.initFromFlash(axis)

            if self._steps_per_count[axname]:
                # Write SPC if provided
                # Value that's written to register needs to be multiplied by (65536 * 4) (see manual)
                self.writeParam(axis, 11, self._steps_per_count[axname] * (65536 * 4))
            else:
                # Read spc from flash. If value is not reasonable, use default
                val = int(self.readParam(axis, 11))
                if not 20000 <= val <= 150000:
                    # that's not a reasonable value, the flash was probably not configured
                    logging.warning("Axis %s spc value not configured properly, current value: %s" % (axis, val))
                    logging.info("Axis %s using spc value %s" % (axis, DEFAULT_SPC))
                    val = DEFAULT_SPC
                else:
                    val = val / (65536 * 4)
                    logging.info("Axis %s is using spc value from flash: %s" % (axis, val))
                self._steps_per_count[axname] = val
            self._steps_per_meter[axname] = self._steps_per_count[axname] * self._counts_per_meter[axname]
            self._speed_steps[axname] = round(self._speed * self._steps_per_meter[axname])

        # Limit switch
        for axis in self._axis_map.values():
            self.setLimitType(axis, limit_type)

    def terminate(self):
        # terminate can be called several times, do nothing if ._serial is already None
        if self._serial is None:
            return
        self._serial.close()
        self._serial = None
        for axis in self._axis_map.values():
            self.setWaveform(axis, WAVEFORM_PARK)  # power off

        super(PMD401Bus, self).terminate()

    def stop(self, axes=None):
        self._executor.cancel()
        axes = axes or self._axis_map.keys()
        for ax in axes:
            self.stopAxis(self._axis_map[ax])

    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)
        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def moveAbs(self, pos):
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)
        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def reference(self, axes):
        self._checkReference(axes)
        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doReference, f, axes)
        return f

    def _doReference(self, f, axes):
        self._check_hw_error()
        # Request referencing on all axes
        for axname in axes:
            # If we're already at the index position or behind (index is at 8.9 µm from limit),
            # referencing doesn't work.
            # To avoid this, we first make a small move in the opposite direction (10 µm).
            fmove = self._createMoveFuture()
            self._doMoveRel(fmove, {axname:  1e-5})
            self.startIndexMode(self._axis_map[axname])
            self.moveToIndex(self._axis_map[axname])

        # Wait until referencing is done
        timeout = 2 * (abs(self.axes[axname].range[1] - self.axes[axname].range[0]) / self._speed)
        startt = time.time()
        for axname in axes:
            self.referenced._value[axname] = False
            while not self.getIndexStatus(self._axis_map[axname])[-1]:
                if f._must_stop.is_set():
                    return
                if time.time() - startt > timeout:
                    self.stop()  # exit index mode
                    raise ValueError("Referencing axis %s failed." % self._axis_map[axname])
                time.sleep(0.1)
            logging.debug("Finished referencing axis %s." % axname)
            self.stopAxis(self._axis_map[axname])
            self.referenced._value[axname] = True
        # read-only so manually notify
        self.referenced.notify(self.referenced.value)
        self._updatePosition()

    def _doMoveAbs(self, f, pos):
        self._check_hw_error()
        targets = {}
        for axname, val in pos.items():
            if self._closed_loop[axname]:
                encoder_cnts = round(val * self._counts_per_meter[axname])
                self.runAbsTargetMove(self._axis_map[axname], encoder_cnts, self._speed_steps[axname])
                targets[axname] = val
            else:
                target = val - self.position.value[axname]
                steps_float = target * self._steps_per_meter[axname]
                steps = int(steps_float)
                usteps = int((steps_float - steps) * USTEPS_PER_STEP)
                self.runMotorJog(self._axis_map[axname], steps, usteps, self._speed_steps[axname])
                targets[axname] = None
        self._waitEndMotion(f, targets)
        # Leave target mode in case of closed-loop move
        for ax in pos:
            self.stopAxis(self._axis_map[ax])
        self._updatePosition()

    def _doMoveRel(self, f, shift):
        self._check_hw_error()
        targets = {}
        self._updatePosition()
        for axname, val in shift.items():
            if self._closed_loop[axname]:
                targets[axname] = self.position.value[axname] + val
                encoder_cnts = val * self._counts_per_meter[axname]
                self.runRelTargetMove(self._axis_map[axname], encoder_cnts)
            else:
                steps_float = val * self._steps_per_meter[axname]
                steps = int(steps_float)
                usteps = int((steps_float - steps) * USTEPS_PER_STEP)
                self.runMotorJog(self._axis_map[axname], steps, usteps, self._speed_steps[axname])
                targets[axname] = None
        self._waitEndMotion(f, targets)
        # Leave target mode in case of closed-loop move
        for ax in shift:
            self.stopAxis(self._axis_map[ax])
        self._updatePosition()

    def _waitEndMotion(self, f, targets):
        """
        Wait until move is done.
        :param f: (CancellableFuture) move future
        :param targets: (dict: str --> int) target (for closed-loop), None for open loop
        """
        move_length = 0
        self._updatePosition()
        for ax, target in targets.items():
            if target is not None:
                move_length = max(abs(self.position.value[ax] - target), move_length)
        if move_length == 0:  # open loop
            move_length = STROKE_RANGE
        dur = move_length / self._speed
        max_dur = max(dur * 2, 0.1)  # wait at least 0.1 s
        logging.debug("Expecting a move of %g s, will wait up to %g s", dur, max_dur)

        for axname, target in targets.items():
            axis = self._axis_map[axname]
            moving = True
            end_time = time.time() + max_dur
            while moving:
                if f._must_stop.is_set():
                    return
                if time.time() > end_time:
                    raise IOError("Timeout while waiting for end of motion on axis %s" % axis)
                if self._closed_loop[axname]:
                    if target is None:
                        raise ValueError("No target provided for closed-loop move on axis %s." % axis)
                    moving = self.isMovingClosedLoop(axis)
                else:
                    moving = self.isMovingOpenLoop(axis)
                self._check_hw_error()
                time.sleep(0.05)

    def _check_hw_error(self):
        """
        Read hardware status and raise exception if error is detected.
        """
        for ax, axnum in self._axis_map.items():
            status = self.getStatus(axnum)
            if status[0] & 8:
                raise PMDError(1, "Communication Error (wrong baudrate, data collision, or buffer overflow)")
            elif status[0] & 4:
                raise PMDError(2, "Encoder error(serial communication or reported error from serial encoder)")
            elif status[0] & 2:
                raise PMDError(3, "Supply voltage or motor fault was detected.")
            elif status[0] & 1:
                raise PMDError(4, "Command timeout occurred or a syntax error was detected when response was not "
                                  "allowed.")
            elif status[1] & 8:
                raise PMDError(5, "Power-on/reset has occurred.")

    def _updatePosition(self):
        """
        Update the position VA.
        """
        pos = {}
        for axname, axis in self._axis_map.items():
            pos[axname] = self.getEncoderPosition(axis)
        logging.debug("Reporting new position at %s", pos)
        pos = self._applyInversion(pos)
        self.position._set_value(pos, force_write=True)

    def stopAxis(self, axis):
        self._sendCommand(b'X%dS' % axis)

    def getVersion(self, axis):
        """
        :param axis: (int) axis number
        :returns (str): controller type and firmware version, e.g. 'PMD401 V13'
        """
        return self._sendCommand(b'X%d?' % axis)

    def getSerialNumber(self, axis):
        """
        :param axis: (int) axis number
        :returns (str): serial number
        """
        return self._sendCommand(b'X%dY42' % axis)

    def initFromFlash(self, axis):
        """
        Initialize settings from values stored in flash.
        :param axis: (int) axis number
        """
        # 2 for init from flash, 3 for factory values
        self._sendCommand(b'X%dY1,2' % axis)

    def setLimitType(self, axis, limit_type):
        """
        :param axis: (int) axis number
        :param limit_type: (0 <= int <= 3) 0 no limit, 1 active high, 2 active low
        """
        self._sendCommand(b'X%dY2,%d' % (axis, limit_type))

    def getEncoderPosition(self, axis):
        """
        :param axis: (int) axis number
        :returns (float): current position of the axis as reported by encoders (in m)
        """
        axname = [name for name, num in self._axis_map.items() if num == axis][0]  # get axis name from number
        return int(self._sendCommand(b'X%dE' % axis)) / self._counts_per_meter[axname]

    def runRelTargetMove(self, axis, encoder_cnts):
        """
        Closed-loop relative move.
        :param axis: (int) axis number
        :param encoder_cnts: (int)
        """
        # There are two possibilities: move relative to current position (XC) and move relative to
        # target position (XR). We are moving relative to the current position (might be more intuitive
        # if something goes wrong and we're stuck in the wrong position).
        self._sendCommand(b'X%dC%d,%d' % (axis, encoder_cnts, self._speed_steps['x']))

    def runMotorJog(self, axis, motor_steps, usteps, speed):
        """
        Open loop stepping.
        :param axis: (int) axis number
        :param motor_steps: (int) number of motor steps to move
        :param usteps: (int) number of microsteps (1 motor step = 8192 microsteps)
        :param speed: (int) speed in steps / m
        """
        self._sendCommand(b'X%dJ%d,%d,%d' % (axis, motor_steps, usteps, speed))

    def runAbsTargetMove(self, axis, encoder_cnts, speed):
        """
        Closed loop move.
        :param axis: (int) axis number
        :param encoder_cnts: (int)
        :param speed: speed in motor steps per s
        """
        self._sendCommand(b'X%dT%d,%d' % (axis, encoder_cnts, speed))

    def setWaveform(self, axis, wf):
        """
        :param wf: (WAVEFORM_RHOMB, WAVEFORM_DELTA, WAVEFORM_PARK) waveform to set
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

    def isMovingClosedLoop(self, axis):
        """
        :param axis: (int) axis number
        :param target:  (float) target position for axes
        :returns: (bool) True if moving, False otherwise
        """
        # Check d3 (third status value) bit 2 (targetLimit) and bit 0 (targetReached)
        _, _, d3, _ = self.getStatus(axis)
        if d3 & 0b0101:
            # target or limit reached
            return False
        else:
            return True

    def isMovingOpenLoop(self, axis):
        """
        :param axis: (int) axis number
        :returns: (bool) True if moving, False otherwise
        """
        resp = self._sendCommand(b'X%dJ' % axis)  # will be 0 if finished, otherwise +/-222 (contrary to manual!)
        return int(resp) != 0

    def startIndexMode(self, axis):
        """
        Enters index mode.
        """
        self._sendCommand(b'X%dN4' % axis)

    def moveToIndex(self, axis):
        """
        Move towards the index until it's found.
        """
        axname = [name for name, num in self._axis_map.items() if num == axis][0]  # get axis name from number
        # Move towards fixed end (negative direction)
        maxdist = int(-self._axes[axname].range[1] * self._counts_per_meter[axname] * DEFAULT_SPC)  # complete rodlength
        self._sendCommand(b'X%dI%d,0,%d' % (axis, maxdist, self._speed_steps[axname]))

    def getIndexStatus(self, axis):
        """
        Returns a description of the index status.
        :returns (tuple of 4):
            mode (0 or 1): index mode (1 if position has been reset at index)
            position (float):
            logged (bool): position was logged since last report
            indexed (bool): position has been reset at index
        """
        # Check if referencing was successful
        # Response looks like this: "1,132.,indexed"
        ret = self._sendCommand(b'X%dN?' % axis).split(',')
        try:
            mode = ret[0]
            if mode == '1':
                mode = 1
            else:  # empty string means mode 0
                mode = 0
            if ret[1][-1] == '.':
                # . means position was logged since last report
                logged = True
                position = ret[1][:-1]
            else:
                logged = False
                position = ret[1]
            if len(ret) > 2 and 'indexed' in ret[2]:
                indexed = True
            else:
                indexed = False
            return mode, position, logged, indexed
        except Exception as ex:
            logging.exception("Failed to parse index status %s", ret)
            raise

    def setAxisAddress(self, current_address, new_address):
        """
        Set the address of the axis. The factory default is 0 for all boards. Don't use this
        command if multiple axes with the same number are connected.
        :param current_address: (int) current axis number
        :param new_address: (int) new axis number
        """
        self._sendCommand(b"X%dY40,%d" % (current_address, new_address))

    def runAutoConf(self, axis):
        """
        Runs automatic configuration for the encoder parameters.
        :param axis: (int) axis number
        """
        self._sendCommand(b"X%dY25,1" % axis)

    def writeParamsToFlash(self, axis):
        self._sendCommand(b"X%dY32" % axis)

    def setParam(self, axis, param, value):
        self._sendCommand(b"X%dY%d,%d" % (axis, param, value))

    def readParam(self, axis, param):
        """
        :returns (str): parameter value from device
        """
        return self._sendCommand(b"X%dY%d" % (axis, param))

    def _createMoveFuture(self):
        """
        :returns: (CancellableFuture) a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute or relative). Non-blocking.
        future (Future): the future to stop. Unused, only one future must be
         running at a time.
        return (bool): True if it successfully cancelled (stopped) the move.
        """
        # The difficulty is to synchronise correctly when:
        #  * the task is just starting (not finished requesting axes to move)
        #  * the task is finishing (about to say that it finished successfully)
        logging.debug("Cancelling current move")

        future._must_stop.set()  # tell the thread taking care of the move it's over
        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped

    def _sendCommand(self, cmd):
        """
        :param cmd: (bytes) command to be sent to the hardware
        :returns: (str) response
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
                    logging.warning("Failed to read from PMT Control firmware, "
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
            if not resp.startswith(cmd[:-len(EOL)-1]):
                raise IOError("Response starts with %s != %s" % (resp[:len(cmd)], cmd))
            if b"_??_" in resp:
                raise ValueError("Received response %s, command %s not understood." % (resp, cmd))
            if b"!" in resp:
                raise PMDError(0, resp)
            # Format:
            #    * for query with response: <cmd>:<ret><EOL> (will return <ret>)
            #    * for set command without response: <cmd><EOL> (will return "")
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
                        if self._serial:
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
                logging.debug("Port %s doesn't seem to have a PMD device connected: %s",
                              n, ex)
            serial.close()  # make sure to close/unlock that port
        else:
            raise IOError("Failed to find a PMD controller with adress %s on ports '%s'. "
                          "Check that the device is turned on and "
                          "connected to the computer." % (address, port,))

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
        self.status = "0000"
        self.indexing = True

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
        :param msg (str): the message to parse
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
                    self.waveform[axis] = int(args[0])
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
                    if self.is_moving:
                        self._output_buf += ":222"
                    else:
                        self._output_buf += ":0"
                elif len(args) == 1:
                    pass  # simulate move
                elif len(args) == 2:
                    pass # simulate move
                elif len(args) == 3:
                    # simulate move
                    self.speed = int(args[1])
                else:
                    raise ValueError()
            elif cmd == "I":
                self.find_index()
            elif cmd == "N":
                if self.indexing:
                    self._output_buf += "1,132.,indexed"
                else:
                    self._output_buf += "1,132"
            elif cmd == "Y":
                if len(args) == 1:
                    if int(args[0]) == 42:  # serial number
                        self._output_buf += "12345678"
                    elif int(args[0]) == 11:  # spc parameter
                        self._output_buf += "70000"
            elif cmd == "U":
                self._output_buf += ":%s" % self.status
                if self.is_moving:
                    self.status = "0000"
                else:
                    self.status = "0010"
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

    def find_index(self):
        t = Thread(target=self._do_indexing)
        t.start()

    def _do_indexing(self):
        self.indexing = True
        time.sleep(1)
        self.indexing = False

    def _do_move(self, closed_loop=True):
        self.is_moving = True
        time.sleep(1)
        self.current_pos = copy.deepcopy(self.target_pos)
        self.is_moving = False
