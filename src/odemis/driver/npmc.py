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
from concurrent.futures import CancelledError, TimeoutError
import copy
import fcntl
import glob
import logging
from odemis import model
from odemis.model import HwError, CancellableFuture, CancellableThreadPoolExecutor, isasync
from odemis.util import driver, to_str_escape
import os
import re
import serial
import threading
import time

# Unit definitions
UNIT_DEF = {
    'mm': 2,
    'um': 3,
    'in': 4,
    }

# Constants for referencing
REF_POSITIVE_LIMIT = 3
REF_NEGATIVE_LIMIT = 4

# Error codes (these values will have the axis number * 100 added to them.
# e.g. 104 is a positive limit error in axis 1
ERR_POSITIVE_LIMIT = 4
ERR_NEGATIVE_LIMIT = 5
ERR_PARAMETER_OUT_OF_RANGE = 7
ERR_AXIS_NUMBER_OUT_OF_RANGE = 9
ERR_HOMING_ABORTED = 20

# Lock keypad
KEYPAD_UNLOCK = 0
KEYPAD_LOCK_EXCEPT_STOP = 1
KEYPAD_ALL_LOCKED = 2


class ESPError(model.HwError):
    """
    Exception used to indicate a problem reported by the device.
    """

    def __init__(self, msg, code=None):
        self.code = code
        model.HwError.__init__(self, msg)


class ESP(model.Actuator):

    def __init__(self, name, role, port, axes=None, **kwargs):
        """
        A driver for a Newport ESP 301 Stage Actuator.
        This driver supports a serial connection. Note that as of the Linux
        kernel 4.13, the USB connection is known to _not_ work, as the TI 3410
        chipset apparently behind is not handled properly. Use a of the
        RS-232 port is required (via a USB adapter if necessary).

        name: (str)
        role: (str)
        port: (str) port name. Can be a pattern, in which case all the ports
          fitting the pattern will be tried.
          Use /dev/fake for a simulator
        axes: dict str (axis name) -> dict (axis parameters)
            axis parameters: {
                number (1 <= int <= 3): axis number on the hardware
                range: [float, float], default is -1 -> 1
                unit (str): the external unit of the axis (internal is mm),
                   default is "m".
                conv_factor (float): a conversion factor that converts to the
                   device internal unit (mm), default is 1000.
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

        self.LockKeypad(KEYPAD_LOCK_EXCEPT_STOP)  # lock user input for the controller

        # Clear errors at start
        try:
            self.checkError()
        except ESPError:
            pass

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

        # whether the axes are referenced
        self.referenced = model.VigilantAttribute({a: False for a in axes}, readonly=True)

        self._hwVersion = str(self._id)
        self._swVersion = self._version

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
        if self._serial.isOpen():
            self.LockKeypad(KEYPAD_UNLOCK)  # unlock user input for the controller

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
            raise ValueError("Invalid metadata, should be a coordinate dictionary but got %s." % (value,))
                
        # update all axes
        for n in self._axis_map.keys():
            if n in value:
                self._offset[n] = value[n]
        logging.debug("Updating offset to %s.", value)
        self._updatePosition()

    # Connection methods

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
            if char == b'':
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
                    if not "ESP301" in ve.upper():
                        raise IOError("Device at %s is not an ESP301 controller. Reported version string: %s" % (ports, ve))

                except ESPError as e:
                    # Can happen if the device has received some weird characters
                    # => try again (now that it's flushed)
                    logging.info("Device answered by an error %s, will try again", e)
                    ve = self.GetVersion()
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
        cmd (byte str): command to be sent to device (without the CR)
        """
        cmd = cmd + b"\r"
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)

    def _sendQuery(self, cmd):
        """
        cmd (byte str): command to be sent to device (without the CR, but with the ?)
        returns (str): answer received from the device (without \n or \r)
        raise:
            IOError if no answer is returned in time
        """
        cmd = cmd + b"\r"
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)

            self._serial.timeout = 1
            ans = b''
            while ans[-1:] != b'\r':
                char = self._serial.read()
                if not char:
                    raise IOError("Timeout after receiving %s" % to_str_escape(ans))
                ans += char

            logging.debug("Received answer %s", to_str_escape(ans))

            return ans.strip().decode("latin1")

    # Low level serial commands.
    # Note: These all convert to internal units of the controller

    def GetErrorCode(self):
        # Checks the device error register
        return int(self._sendQuery(b"TE?"))

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
            for err in err_q[:-1]:
                logging.warning("Discarding error %d", err)
            raise ESPError("Error code %d" % (err_q[-1],), err_q[-1])

    def SetAxisUnit(self, axis_num, unit):
        # Set the internal unit used by the controller
        if not unit in UNIT_DEF:
            raise ValueError("Unknown unit name %s" % (unit,))
        self._sendOrder(b"%d SN %d" % (axis_num, UNIT_DEF[unit]))

    def MoveLimit(self, aid, limit):
        """
        Requests a move to the positive or negative limit.
        limit (str): either '+' or '-', defining positive or negative limit
        """
        if not limit in ("+", "-"):
            raise ValueError("Asked to move %d to %s limit. Only + or - allowed." % (aid, limit,))
        self._sendOrder(b"%d MV %s" % (aid, limit))

    def LockKeypad(self, lock_type):
        """
        Lock keypad on device from preventing bad user input
        lock_type (KEYPAD_*)
        """
        self._sendOrder(b"LC %d" % (lock_type,))

    def HomeSearch(self, aid, search_type):
        """
        Searches for home using a search type (int 0,1,2,3,4,5,6) as per manual
        """
        self._sendOrder(b"%d OR %d" % (aid, search_type))

    def SetHome(self, aid, value):
        """
        Set the position value to use at the origin (home)
        """
        self._sendOrder(b"%d SH %f" % (aid, value))

    def SaveMemory(self):
        """
        Save configuration to non - volatile memory
        """
        self._sendOrder(b"SM")

    def MoveAbsPos(self, axis_num, pos):
        """
        Requests a move to an absolute position. This is non-blocking.
        Converts to internal unit of the controller
        """
        self._sendOrder(b"%d PA %f" % (axis_num, pos))

    def MoveRelPos(self, axis_num, rel):
        """
        Requests a move to a relative position. This is non-blocking.
        """
        self._sendOrder(b"%d PR %f" % (axis_num, rel))  # 0 = absolute

    def GetDesiredPos(self, axis_num):
        # Get the target position programmed into the controller
        return float(self._sendQuery(b"%d DP?" % (axis_num,)))

    def StopMotion(self, axis):
        # Stop the motion on the specified axis
        self._sendOrder(b"%d ST" % (axis,))

    def MotorOn(self, axis):
        # Start the motor
        self._sendOrder(b"%d MO" % (axis,))

    def MotorOff(self, axis):
        # Stop the motor
        self._sendOrder(b"%d MF" % (axis,))

    def GetMotionDone(self, axis_n):
        # Return true or false based on if the axis is still moving.
        done = int(self._sendQuery(b"%d MD?" % axis_n))
        logging.debug("Motion done: %d", done)
        return bool(done)

    def GetPosition(self, axis_n):
        # Get the position of the axis
        return float(self._sendQuery(b"%d TP?" % axis_n))

    def GetSpeed(self, axis_n):
        # Get the speed of the axis
        return float(self._sendQuery(b"%d VA?" % axis_n))

    def SetSpeed(self, axis_n, speed):
        # Set the axis speed
        self._sendOrder(b"%d VA %f" % (axis_n, speed,))

    def GetAcceleration(self, axis_n):
        # Get axis accel
        return float(self._sendQuery(b"%d AC?" % axis_n))

    def SetAcceleration(self, axis_n, ac):
        # Set axis accel
        self._sendOrder(b"%d AC %f" % (axis_n, ac,))

    def GetDeceleration(self, axis_n):
        return float(self._sendQuery(b"%d AG?" % axis_n))

    def SetDeceleration(self, axis_n, dc):
        self._sendOrder(b"%d AG %f" % (axis_n, dc,))

    def GetIdentification(self, axis):
        """
        return (str): the identification string as-is for the first axis
        """
        return self._sendQuery(b"%d ID?" % (axis,))

    def GetVersion(self):
        """
        return (str): the version string as-is
        """
        return self._sendQuery(b"VE?")

    def SaveMem(self):
        """
        Instruct the controller to save the current settings to non-volatile memory
        """
        self._sendOrder(b"SM")

    """
    High level commands (ie, Odemis Actuator API)
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
                try:
                    self.checkError()
                except ESPError as e:
                    logging.warning("Cancellation error %d", e.code)

                # Should now turn the motor back on
                self.MotorOn(aid)
        
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

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doReference, f, axes)
        return f

    def _doReference(self, future, axes):
        """
        Actually runs the referencing code
        axes (set of str)
        raise:
            IOError: if referencing failed due to hardware
            CancelledError if was cancelled
        """
        # Reset reference so that if it fails, it states the axes are not
        # referenced (anymore)
        with future._moving_lock:
            try:
                # do the referencing for each axis sequentially
                # (because each referencing is synchronous)
                for a in axes:
                    if future._must_stop.is_set():
                        raise CancelledError()
                    aid = self._axis_map[a]
                    self.referenced._value[a] = False
                    self.HomeSearch(aid, REF_NEGATIVE_LIMIT)  # search for the negative limit signal to set an origin
                    self._waitEndMove(future, (aid,), time.time() + 100)  # block until it's over
                    self.SetHome(aid, 0.0)  # set negative limit as origin
                    self.referenced._value[a] = True
            except CancelledError:
                # FIXME: if the referencing is stopped, the device refuses to
                # move until referencing is run (and successful).
                # => Need to put back the device into a mode where at least
                # relative moves work.
                logging.warning("Referencing cancelled, device will not move until another referencing")
                future._was_stopped = True
                raise
            except Exception:
                logging.exception("Referencing failure")
                raise
            finally:
                # We only notify after updating the position so that when a listener
                # receives updates both values are already updated.
                self._updatePosition(axes)  # all the referenced axes should be back to 0
                # read-only so manually notify
                self.referenced.notify(self.referenced.value)


class ESPSimulator(object):
    """
    Simulates a Newport ESP301
    Same interface as the serial port
    """

    def __init__(self, timeout=1):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = b""  # what the commands sends back to the "host computer"
        self._input_buf = b""  # what we receive from the "host computer"

        self._pos = [0, 0, 0]  # internal posiiton in mm
        self._start_pos = [0, 0, 0]  # mm
        self._range = [-100, 800]  # mm
        self._target_pos = [0, 0, 0]  # mm
        self._speed = [100, 100, 100]  # mm/s
        self._accel = [20, 20, 20]  # mm/s^2
        self._decel = [20, 20, 20]  # mm/s^2
        self._error_stack = []  # Stack that is populated by error codes
        
        self._current_move_start = time.time()
        self._current_move_finish = time.time()

    def write(self, data):
        self._input_buf += data
        msgs = self._input_buf.split(b"\r")
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
        self._output_buf = b""

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def isOpen(self):
        return hasattr(self, "_output_buf")

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
        self._output_buf += b"%s\r" % (ans,)
        
    def _isMoving(self):
        return time.time() < self._current_move_finish

    def _doMove(self, axis, new_pos):
        # Check that the position is within the range.
        if self._range[0] <= new_pos <= self._range[1]:
            self._target_pos[axis] = new_pos
            self._start_pos = copy.copy(self._pos)
            d = self._target_pos[axis] - self._start_pos[axis]
            dur = driver.estimateMoveDuration(abs(d), self._speed[axis], self._accel[axis])
            self._current_move_start = time.time()
            self._current_move_finish = time.time() + dur
        else:
            if new_pos >= self._range[1]:
                self._addError(axis * 100 + ERR_POSITIVE_LIMIT)  # Error - detected positive limit
                self._pos[axis] = self._range[1]
            elif new_pos <= self._range[0]:
                self._addError(axis * 100 + ERR_NEGATIVE_LIMIT)  # error - detected negative limit
                self._pos[axis] = self._range[0]

    def _parseMessage(self, msg):
        """
        msg (str): the message to parse (without the \r)
        return None: self._output_buf is updated if necessary
        """
        logging.debug("SIM: parsing %s", to_str_escape(msg))
        msg = msg.strip()  # remove leading and trailing whitespace
        msg = b"".join(msg.split())  # remove all space characters
        
        if msg == b"VE?":
            self._sendAnswer(b"ESP301 Version 3.0.1 6/1/99")
            
        elif msg == b"SM":
            # save memory to non-volatile RAM
            pass

        elif msg == b"TE?":
            # Query error code. Return no error code
            if len(self._error_stack) > 0:
                self._sendAnswer(b"%d" % self._error_stack.pop())  # pop the top element of the error stack
            else:
                self._sendAnswer(b'0')  # no error

        # Query the axis ID number
        elif re.match(br'\dID\?', msg):
            self._sendAnswer(b"12345")

        # Query absolute position
        elif re.match(br'\dTP\?', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                self._updateCurrentPosition()
                self._sendAnswer(b"%f" % (self._pos[axis - 1],))

        # Query current target
        elif re.match(br'\dDP\?', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                self._sendAnswer(b"%f" % (self._target_pos[axis - 1],))

        # Query current speed
        elif re.match(br'\dVA\?', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                self._sendAnswer(b"%f" % (self._speed[axis - 1],))

        # Query current accel
        elif re.match(br'\dAC\?', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                self._sendAnswer(b"%f" % (self._accel[axis - 1],))

        # Query current decel
        elif re.match(br'\dAG\?', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                self._sendAnswer(b"%f" % (self._decel[axis - 1],))

        # Query motion done
        elif re.match(br'\dMD\?', msg):
            self._updateCurrentPosition()
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                if self._isMoving():
                    self._sendAnswer(b"0")
                else:
                    self._sendAnswer(b"1")

        # Move to an absolute position
        elif re.match(br'\dPA', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                new_pos = float(msg[3:])
                self._doMove(axis - 1, new_pos)

        # Move to a limit
        elif re.match(br'\dMV', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                limit = msg[3:]
                if limit == "+":
                    self._doMove(axis - 1, self._range[1])
                elif limit == "-":
                    self._doMove(axis - 1, self._range[0])

        # Home search
        elif re.match(br'\dOR', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                search_type = int(msg[3:])
                if search_type == REF_NEGATIVE_LIMIT:
                    self._doMove(axis - 1, self._range[0])
                if search_type == REF_POSITIVE_LIMIT:
                    self._doMove(axis - 1, self._range[1])

        # Set home
        elif re.match(br'\dSH', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                origin = float(msg[3:])
                self._pos[axis - 1] = origin
                span = self._range[1] - self._range[0]
                self._range[0] = origin
                self._range[1] = origin + span
                self._target_pos = copy.copy(self._pos)
                self._updateCurrentPosition()
                logging.debug("SIM: Setting new home position %f for axis %d", origin, axis)

        # Move to a relative position
        elif re.match(br'\dPR', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                shift = float(msg[3:])
                new_pos = self._pos[axis - 1] + shift
                self._doMove(axis - 1, new_pos)

        # Set speed
        elif re.match(br'\dVA', msg):
            axis = int(msg[:1])
            if axis > 3:
                self._addError(ERR_AXIS_NUMBER_OUT_OF_RANGE)
            else:
                speed = float(msg[3:])
                self._speed[axis - 1] = speed

        # Set unit
        elif re.match(br'\dSN', msg):
            # we don't need to do anything here
            pass
        
        # Lock keypad
        elif re.match(br'LC\d', msg):
            val = int(msg[2:])
            if val not in (KEYPAD_ALL_LOCKED, KEYPAD_LOCK_EXCEPT_STOP, KEYPAD_UNLOCK):
                self._addError(ERR_PARAMETER_OUT_OF_RANGE)

        else:
            pass
