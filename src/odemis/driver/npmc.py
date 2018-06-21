# -*- coding: utf-8 -*-
'''
Created on 19 June 2018

@author: Anders Muskens

Copyright © 2018 Anders Muskens, Delmic

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
from concurrent.futures import CancelledError, TimeoutError
import fcntl
import glob
import serial
import logging
import os
import threading
import time
import copy
import re
from Pyro4.core import isasync
from odemis import model
from odemis.util import driver
from odemis.model import HwError, CancellableFuture, CancellableThreadPoolExecutor

# Unit definitions 
UNIT_DEF = {
    'mm': 2,
    'um': 3,
    'in': 4,
    }


class ESPError(model.HwError):
    """
    Exception used to indicate a problem reported by the device.
    """
    pass


class ESP(model.Actuator):

    def __init__(self, name, role, port, axes=None, inverted=None, **kwargs):
        """
        A driver for a Newport ESP 301 Stage Actuator. This driver supports a serial or USB
        connection to a Unix host.

        name: (str)
        role: (str)
        port: (str) port name. Can be a pattern, in which case all the ports
          fitting the pattern will be tried.
          Use /dev/fake for a simulator
        axes: dict str (axis name) -> dict (axis parameters)
            axis parameters: {
                number: (int) either 1,2,3
                range: [float, float]
                unit: (str) the external unit of the axis (internal is mm) which the conv_factor
                    should convert to. Typically metres by default.
                conv_factor (float): a conversion factor that converts to the devices internal unit (mm)
                    By default, the position VA is displayed as metres, so this value takes 1000.
            }

        inverted: (bool) defines if the axes are inverted

        The offset can be specified by setting MD_POS_COR as a coordinate dictionary

        """

        if len(axes) == 0:
            raise ValueError("Needs at least 1 axis.")

        # Connect to serial port
        self._ser_access = threading.Lock()
        self._serial = None
        self._file = None
        self._port, self._version = self._findDevice(port)  # sets ._serial and ._file
        logging.info("Found Newport ESP301 device on port %s, Ver: %s",
                     self._port, self._version)

        self._offset = {}
        self._axis_conv_factor = {}

        # Not to be mistaken with axes which is a simple public view
        self._axis_map = {}  # axis name -> axis number used by controller
        axes_def = {}  # axis name -> Axis object
        speed = {}
        accel = {}
        decel = {}
        self._id = {}

        for axis_name, axis_par in axes.items():
            # Unpack axis parameters from the definitions in the YAML
            try:
                axis_num = axis_par['number']
            except KeyError:
                raise ValueError("Axis %s must have a number to identify it. " % (axis_name,))

            try:
                axis_range = axis_par['range']
            except KeyError:
                logging.info("Axis %s has no range. Assuming (-1, 1)", axis_name)
                axis_range = (-1, 1)

            try:
                axis_unit = axis_par['unit']
            except KeyError:
                logging.info("Axis %s has no unit. Assuming m", axis_name)
                axis_unit = "m"

            if axis_unit not in ('m', 's'):
                raise ValueError("Invalid axis unit. Should be m or s.")

            try:
                conv_factor = float(axis_par['conv_factor'])
            except KeyError:
                logging.info("Axis %s has no conversion factor. Assuming 1000 (m to mm)", axis_name)
                conv_factor = 1000.0

            self._axis_map[axis_name] = axis_num
            self._axis_conv_factor[axis_num] = conv_factor
            self._id[axis_num] = self.GetIdentification(axis_num)
            speed[axis_name] = self.GetSpeed(axis_num)
            accel[axis_name] = self.GetAcceleration(axis_num)
            decel[axis_name] = self.GetDeceleration(axis_num)

            # Force millimetres for consistency as the internal unit.
            self.SetAxisUnit(axis_num, "mm")
            # initialize each motor
            self.MotorOn(axis_num)

            ad = model.Axis(canAbs=True, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        self._hwVersion = str(self._id)
        self._swversion = self._version

        # Get the position in object coord with the offset applied.

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, readonly=True)
        self._updatePosition()

        self._speed = speed
        self._accel = accel
        self._decel = decel

        # set offset due to mounting of components (float)
        self._metadata[model.MD_POS_COR] = {}

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(1)  # one task at a time

        # Check the error state
        self.checkError()
        
    def terminate(self):
        with self._ser_access:
            self._serial.close()
        model.Actuator.terminate(self)

    def updateMetadata(self, md):
        super(ESP, self).updateMetadata(md)
        try:
            value = md[model.MD_POS_COR]
        except KeyError:
            # there is no offset set.
            return

        if not isinstance(value, dict):
            raise ValueError("Invalid metadata value. Should be a coordinate dictionary.")
                
        # update all axes
        for n, i in self._axis_map.items():
            if n in value.keys():
                self._offset[n] = value[n]
        logging.debug("reporting metadata entry %s with value %s.", value, model.MD_POS_COR)
        self._updatePosition()

    """
    Low level serial commands.

    * note: These all convert to internal units of the controller
    """
    @staticmethod
    def _openSerialPort(port, baudrate):
        """
        Opens the given serial port the right way for a Power control device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        baudrate (int)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            rtscts=True,
            timeout=1  # s
        )

        # Purge
        ser.flush()
        ser.flushInput()

        # Try to read until timeout to be extra safe that we properly flushed
        ser.timeout = 0
        while True:
            char = ser.read()
            if char == '':
                break
        ser.timeout = 1

        return ser

    def _findDevice(self, ports, baudrate=19200):
        """
        Look for a compatible device
        ports (str): pattern for the port name
        baudrate (0<int)
        return:
           (str): the name of the port used
           (str): the hardware version string
           Note: will also update ._file and ._serial
        raises:
            IOError: if no devices are found
        """
        # For debugging purpose
        if ports == "/dev/fake":
            self._serial = ESPSimulator(timeout=1)
            self._file = None
            ve = self.GetVersion()
            return ports, ve

        if os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        for n in names:
            try:
                # Ensure no one will talk to it simultaneously, and we don't talk to devices already in use
                self._file = open(n)  # Open in RO, just to check for lock
                try:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # Raises IOError if cannot lock
                except IOError:
                    logging.info("Port %s is busy, will not use", n)
                    continue

                self._serial = self._openSerialPort(n, baudrate)

                try:
                    ve = self.GetVersion()
                except ESPError as e:
                    # Can happen if the device has received some weird characters
                    # => try again (now that it's flushed)
                    logging.info("Device answered by an error %d, will try again", e.msg)
                    ve = self.GetVersion()
                    continue
                return n, ve
            except (IOError, ESPError) as e:
                logging.debug(e)
                logging.info("Skipping device on port %s, which didn't seem to be compatible", n)
                # not possible to use this port? next one!
                continue
        else:
            raise HwError("Failed to find a device on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

    def _sendOrder(self, cmd):
        """
        cmd (str): command to be sent to device (without the CR)
        """
        cmd = cmd + "\r"
        with self._ser_access:
            logging.debug("Sending command %s", cmd.encode('string_escape'))
            self._serial.write(cmd.encode('ascii'))

    def _sendQuery(self, cmd, timeout=1):
        """
        cmd (str): command to be sent to device (without the CR, but with the ?)
        timeout (int): maximum time to receive the answer
        returns (str): answer received from the device (without \n or \r)
        raise:
            IOError if no answer is returned in time
        """
        cmd = cmd + "\r"
        with self._ser_access:
            logging.debug("Sending command %s", cmd.encode('string_escape'))
            self._serial.write(cmd.encode('ascii'))

            self._serial.timeout = 1
            ans = ''
            while ans[-1:] != '\r':
                char = self._serial.read()
                if not char:
                    raise IOError("Timeout after receiving %s" % ans.encode('string_escape'))
                ans += char

            logging.debug("Received answer %s", ans.encode('string_escape'))

            return ans.strip()

    def GetErrorCode(self):
        # Checks the device error register
        return int(self._sendQuery("TE?"))

    def checkError(self):
        # Checks if an error occurred and raises an exception accordingly.
        err_q = []

        # Get all of the errors in the error FIFO stack
        while True:
            errcode = self.GetErrorCode()

            if errcode == 0:  # No error
                break
            else:
                err_q.append(errcode)

        # After errors are collected
        if len(err_q) > 0:
            raise ESPError("Error code(s) %s" % (err_q))

    def SetAxisUnit(self, axis_num, unit):
        # Set the internal unit used by the controller
        if not unit in UNIT_DEF:
            raise ValueError("Unknown unit name %s", unit)
        self._sendOrder("%d SN %d" % (axis_num, UNIT_DEF[unit]))

    def MoveAbsPos(self, axis_num, pos):
        """
        Requests a move to an absolute position. This is non-blocking.
        Converts to internal unit of the controller
        """
        self._sendOrder("%d PA %f" % (axis_num, pos))

    def MoveRelPos(self, axis_num, rel):
        """
        Requests a move to a relative position. This is non-blocking.
        """
        self._sendOrder("%d PR %f" % (axis_num, rel))  # 0 = absolute

    def GetDesiredPos(self, axis_num):
        # Get the target position programmed into the controller
        return float(self._sendQuery("%d DP?" % (axis_num,)))

    def StopMotion(self, axis):
        # Stop the motion on the specified axis
        self._sendOrder("%d ST" % (axis,))

    def MotorOn(self, axis):
        # Start the motor
        self._sendOrder("%d MO" % (axis,))

    def MotorOff(self, axis):
        # Stop the motor
        self._sendOrder("%d MF" % (axis,))

    def GetMotionDone(self, axis_n):
        # Return true or false based on if the axis is still moving.
        done = int(self._sendQuery("%d MD?" % axis_n))
        logging.debug("Motion done: %d", done)
        return bool(done)

    def GetPosition(self, axis_n):
        # Get the position of the axis
        return float(self._sendQuery("%d TP?" % axis_n))

    def GetSpeed(self, axis_n):
        # Get the speed of the axis
        return float(self._sendQuery("%d VA?" % axis_n))

    def SetSpeed(self, axis_n, speed):
        # Set the axis speed
        self._sendOrder("%d VA %f" % (axis_n, speed,))

    def GetAcceleration(self, axis_n):
        # Get axis accel
        return float(self._sendQuery("%d AC?" % axis_n))

    def SetAcceleration(self, axis_n, ac):
        # Set axis accel
        self._sendOrder("%d AC %f" % (axis_n, ac,))

    def GetDeceleration(self, axis_n):
        return float(self._sendQuery("%d AG?" % axis_n))

    def SetDeceleration(self, axis_n, dc):
        self._sendOrder("%d AG %f" % (axis_n, dc,))

    def GetIdentification(self, axis):
        """
        return (str): the identification string as-is for the first axis
        """
        return self._sendQuery("%d ID?" % (axis,))

    def GetVersion(self):
        """
        return (str): the version string as-is
        """
        return self._sendQuery("VE?")

    def SaveMem(self):
        """
        Instruct the controller to save the current settings to non-volatile memory
        """
        self._sendOrder("SM")

    def GetRealPosition(self):
        """
        Gets the real position from the controller itself. (no offset, but with a conversion factor)
        retruns:
            dict of axis name str -> float
        """
        real_pos = {}

        for ax_n, i in self._axis_map.items():
            if ax_n in self._offset.keys():
                real_pos[ax_n] = self.GetPosition(i) / self._axis_conv_factor[i]

        return real_pos

    """
    High level commands
    """

    def _applyOffset(self, pos):
        """
        Apply the offset to the position and return it
        """
        ret = dict(pos)
        for axis in self._offset:
            if axis in ret:
                ret[axis] -= self._offset[axis]
        return ret

    def _removeOffset(self, pos):
        """
        Remove the offset from the position and return it
        """
        ret = dict(pos)
        for axis in self._offset:
            if axis in ret:
                ret[axis] += self._offset[axis]
        return ret

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        pos = self._removeOffset(pos)  # Get the position in controller coord.
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    @isasync
    def moveRel(self, shift):
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)
        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _doMoveRel(self, future, pos):
        """
        Blocking and cancellable relative move
        future (Future): the future it handles
        _pos (dict str -> float): axis name -> relative target position
        raise:
            ValueError: if the target position is
            TMCLError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:

            end = 0  # expected end
            moving_axes = set()
            for an, v in pos.items():
                aid = self._axis_map[an]
                moving_axes.add(aid)
                self.MoveRelPos(aid, v * self._axis_conv_factor[aid])
                # compute expected end
                # convert to mm units
                dur = driver.estimateMoveDuration(abs(v) * self._axis_conv_factor[aid],
                                self._speed[an],
                                self._accel[an])
                    
                end = max(time.time() + dur, end)

            self._waitEndMove(future, moving_axes, end)
        self.checkError()
        logging.debug("move successfully completed")

    def _doMoveAbs(self, future, pos):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        _pos (dict str -> float): axis name -> absolute target position
        raise:
            TMCLError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:
            end = 0  # expected end
            old_pos = self._applyInversion(self.position.value)
            moving_axes = set()
            for an, v in pos.items():
                aid = self._axis_map[an]
                moving_axes.add(aid)
                self.MoveAbsPos(aid, v * self._axis_conv_factor[aid])
                d = abs(v - old_pos[an])
                # convert displacement unit to mm
                dur = driver.estimateMoveDuration(d * self._axis_conv_factor[aid],
                                                  self._speed[an],
                                                  self._accel[an])
                end = max(time.time() + dur, end)

            self._waitEndMove(future, moving_axes, end)
        self.checkError()
        logging.debug("move successfully completed")

    def _waitEndMove(self, future, axes, end=0):
        """
        Wait until all the given axes are finished moving, or a request to
        stop has been received.
        future (Future): the future it handles
        axes (set of int): the axes IDs to check
        end (float): expected end time
        raise:
            TimeoutError: if took too long to finish the move
            CancelledError: if cancelled before the end of the move
        """
        moving_axes = set(axes)

        last_upd = time.time()
        dur = max(0.01, min(end - last_upd, 60))
        max_dur = dur * 2 + 1
        logging.debug("Expecting a move of %g s, will wait up to %g s", dur, max_dur)
        timeout = last_upd + max_dur
        last_axes = moving_axes.copy()
        try:
            while not future._must_stop.is_set():
                for aid in moving_axes.copy():  # need copy to remove during iteration
                    if self.GetMotionDone(aid):
                        moving_axes.discard(aid)
                if not moving_axes:
                    # no more axes to wait for
                    break

                now = time.time()
                if now > timeout:
                    logging.warning("Stopping move due to timeout after %g s.", max_dur)
                    for i in moving_axes:
                        self.StopMotion(i)
                    raise TimeoutError("Move is not over after %g s, while "
                                       "expected it takes only %g s" %
                                       (max_dur, dur))

                # Update the position from time to time (10 Hz)
                if now - last_upd > 0.1 or last_axes != moving_axes:
                    last_names = set(n for n, i in self._axis_map.items() if i in last_axes)
                    self._updatePosition(last_names)
                    last_upd = time.time()
                    last_axes = moving_axes.copy()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0.001, min(left / 2, 0.1))
                future._must_stop.wait(sleept)
            else:
                logging.debug("Move of axes %s cancelled before the end", axes)
                # stop all axes still moving them
                for i in moving_axes:
                    self.StopMotion(i)
                future._was_stopped = True
                raise CancelledError()
        finally:
            # TODO: check if the move succeded ? (= Not failed due to stallguard/limit switch)
            self._updatePosition()  # update (all axes) with final position

    # high-level methods (interface)
    def _updatePosition(self, axes=None):
        """
        update the position VA
        axes (set of str): names of the axes to update or None if all should be
          updated
        """
        # uses the current values (converted to internal representation)
        pos = self._applyInversion(self.position.value)

        for n, i in self._axis_map.items():
            if axes is None or n in axes:
                pos[n] = self.GetPosition(i) / self._axis_conv_factor[i]

        pos = self._applyInversion(pos)
        pos = self._applyOffset(pos)  # Appy the offset back for display

        logging.debug("Updated position to %s", pos)

        self.position._set_value(pos, force_write=True)

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

    def stop(self, axes=None):
        self._executor.cancel()
        # For safety, just force stop every axis
        for an, aid in self._axis_map.items():
            if axes is None or an in axes:
                self.StopMotion(aid)
        
    def _createMoveFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f


class ESPSimulator(object):
    """
    Simulates a Newport ESP301
    Same interface as the serial port
    """

    def __init__(self, timeout=1):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = ""  # what the commands sends back to the "host computer"
        self._input_buf = ""  # what we receive from the "host computer"

        self._pos = [0, 0, 0]  # internal posiiton in mm
        self._start_pos = [0, 0, 0]  # mm
        self._range = [-500, 500]  # mm
        self._target_pos = [0, 0, 0]  # mm
        self._speed = [50, 50, 50]  # mm/s
        self._accel = [20, 20, 20]  # mm/s^2
        self._decel = [20, 20, 20]  # mm/s^2
        self._error_stack = []  # Stack that is populated by error codes
        
        self._current_move_start = time.time()
        self._current_move_finish = time.time()

    def write(self, data):
        self._input_buf += data
        msgs = self._input_buf.split("\r")
        for m in msgs[:-1]:
            self._parseMessage(m)  # will update _output_buf

        self._input_buf = msgs[-1]

    def read(self, size=1):
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        if len(ret) < size:
            # simulate timeout
            time.sleep(self.timeout)
        return ret

    def flush(self):
        pass

    def flushInput(self):
        self._output_buf = ""

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _addError(self, err):
        logging.debug("SIM: Adding error %d", err)
        self._error_stack.append(err)

    def _updateCurrentPosition(self):
        if not self._isMoving():
            self._pos = copy.copy(self._target_pos)
        else:
            cur_position = {}
            for axis in range(0, len(self._target_pos)):
                startp = self._start_pos[axis]
                endp = self._target_pos[axis]
                now = time.time()
                startt = self._current_move_start
                endt = self._current_move_finish

                cur_position[axis] = startp + (endp - startp) * \
                    (now - startt) / (endt - startt)

            self._pos = cur_position

    def _sendAnswer(self, ans):
        self._output_buf += "%s\r" % (ans,)
        
    def _isMoving(self):
        return time.time() < self._current_move_finish

    def _doMove(self, axis, new_pos):
        # Check that the position is within the range.
        if new_pos >= self._range[0] and new_pos <= self._range[1]:
            self._target_pos[axis] = new_pos
            self._start_pos = copy.copy(self._pos)
            d = self._target_pos[axis] - self._start_pos[axis]
            dur = driver.estimateMoveDuration(abs(d), self._speed[axis], self._accel[axis])
            self._current_move_start = time.time()
            self._current_move_finish = time.time() + dur
        else:
            if new_pos > self._range[1]:
                self._addError(axis * 100 + 4)  # Error - detected positive limit
                self._pos[axis] = self._range[1]
            elif new_pos < self._range[0]:
                self._addError(axis * 100 + 5)  # error - detected negative limit
                self._pos[axis] = self._range[0]

    def _parseMessage(self, msg):
        """
        msg (str): the message to parse (without the \r)
        return None: self._output_buf is updated if necessary
        """
        logging.debug("SIM: parsing %s", msg)
        msg = msg.strip()  # remove leading and trailing whitespace
        msg = "".join(msg.split())  # remove all space characters
        
        if msg == "VE?":
            self._sendAnswer("1.0")
            
        elif msg == "SM":
            # save memory to non-volatile RAM
            pass

        elif msg == "TE?":
            # Query error code. Return no error code
            if len(self._error_stack) > 0:
                self._sendAnswer(self._error_stack.pop())  # pop the top element of the error stack
            else:
                self._sendAnswer(0)  # no error

        # Query the axis ID number
        elif re.match('\dID\?', msg):
            self._sendAnswer("12345")

        # Query absolute position
        elif re.match('\dTP\?', msg):
            axis = int(msg[0])
            self._updateCurrentPosition()
            self._sendAnswer(str(self._pos[axis]))

        # Query current target
        elif re.match('\dDP\?', msg):
            axis = int(msg[0])
            self._sendAnswer(str(self._target_pos[axis]))

        # Query current speed
        elif re.match('\dVA\?', msg):
            axis = int(msg[0])
            self._sendAnswer(str(self._speed[axis]))

        # Query current accel
        elif re.match('\dAC\?', msg):
            axis = int(msg[0])
            self._sendAnswer(str(self._accel[axis]))

        # Query current decel
        elif re.match('\dAG\?', msg):
            axis = int(msg[0])
            self._sendAnswer(str(self._decel[axis]))

        # Query motion done
        elif re.match('\dMD\?', msg):
            self._updateCurrentPosition()

            if self._isMoving():
                self._sendAnswer(0)
            else:
                self._sendAnswer(1)

        # Move to an absolute position
        elif re.match('\dPA', msg):
            axis = int(msg[0])
            new_pos = float(msg[3:])
            self._doMove(axis, new_pos)

        # Move to a relative position
        elif re.match('\dPR', msg):
            axis = int(msg[0])
            shift = float(msg[3:])
            new_pos = self._pos[axis] + shift
            self._doMove(axis, new_pos)

        # Set speed
        elif re.match('\dVA', msg):
            axis = int(msg[0])
            speed = float(msg[3:])
            self._speed[axis] = speed

        # Set unit
        elif re.match('\dSN', msg):
            # we don't need to do anything here
            pass

        else:
            pass
