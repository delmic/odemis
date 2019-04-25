# -*- coding: utf-8 -*-
'''
Created on 17 April 2019

@author: Anders Muskens

Copyright © 2012-2019 Anders Muskens, Delmic

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

import os
import logging
import time
from ctypes import *
import threading

from odemis import model
from odemis.util import driver
from odemis.model import HwError, CancellableFuture, CancellableThreadPoolExecutor, isasync


def add_coord(pos1, pos2):
    """
    Adds two coordinate dictionaries together and returns a new coordinate dictionary.
    pos1: dict (axis name str) -> (float)
    pos2: dict (axis name str) -> (float)
    Returns ret
        dict (axis name str) -> (float)
    """
    ret = pos1.copy()
    for an, v in pos2.items():
        ret[an] += v

    return ret


class SmartPodDLL(CDLL):
    """
    Subclass of CDLL specific to SmartPod library, which handles error codes for
    all the functions automatically.
    """
    
    # Status
    SMARPOD_OK= c_uint(0)
    SMARPOD_OTHER_ERROR = c_uint(1)
    SMARPOD_SYSTEM_NOT_INITIALIZED_ERROR = c_uint(2)
    SMARPOD_NO_SYSTEMS_FOUND_ERROR = c_uint(3)
    SMARPOD_INVALID_PARAMETER_ERROR = c_uint(4)
    SMARPOD_COMMUNICATION_ERROR = c_uint(5)
    SMARPOD_UNKNOWN_PROPERTY_ERROR = c_uint(6)
    SMARPOD_RESOURCE_TOO_OLD_ERROR= c_uint(7)
    SMARPOD_FEATURE_UNAVAILABLE_ERROR= c_uint(8)
    SMARPOD_INVALID_SYSTEM_LOCATOR_ERROR = c_uint(9)
    SMARPOD_QUERYBUFFER_SIZE_ERROR = c_uint(10)
    SMARPOD_COMMUNICATION_TIMEOUT_ERROR = c_uint(11)
    SMARPOD_DRIVER_ERROR = c_uint(12)
    
    SMARPOD_STATUS_CODE_UNKNOWN_ERROR = c_uint(500)
    SMARPOD_INVALID_ID_ERROR = c_uint(501)
    SMARPOD_INITIALIZED_ERROR = c_uint(502)
    SMARPOD_HARDWARE_MODEL_UNKNOWN_ERROR = c_uint(503)
    SMARPOD_WRONG_COMM_MODE_ERROR = c_uint(504)
    SMARPOD_NOT_INITIALIZED_ERROR = c_uint(505)
    SMARPOD_INVALID_SYSTEM_ID_ERROR = c_uint(506)
    SMARPOD_NOT_ENOUGH_CHANNELS_ERROR = c_uint(507)
    SMARPOD_INVALID_CHANNEL_ERROR = c_uint(508)
    SMARPOD_CHANNEL_USED_ERROR = c_uint(509)
    SMARPOD_SENSORS_DISABLED_ERROR = c_uint(510)
    SMARPOD_WRONG_SENSOR_TYPE_ERROR = c_uint(511)
    SMARPOD_SYSTEM_CONFIGURATION_ERROR = c_uint(512)
    SMARPOD_SENSOR_NOT_FOUND_ERROR = c_uint(513)
    SMARPOD_STOPPED_ERROR = c_uint(514)
    SMARPOD_BUSY_ERROR = c_uint(515)

    # Defines
    SMARPOD_SENSORS_DISABLED = c_uint(0)
    SMARPOD_SENSORS_ENABLED = c_uint(1)
    SMARPOD_SENSORS_POWERSAVE = c_uint(2)

    # property symbols
    SMARPOD_FREF_METHOD = c_uint(1000)
    SMARPOD_FREF_ZDIRECTION = c_uint(1002)
    SMARPOD_FREF_XDIRECTION = c_uint(1003)
    SMARPOD_FREF_YDIRECTION = c_uint(1004)
    SMARPOD_PIVOT_MODE = c_uint(1010)
    SMARPOD_FREF_AND_CAL_FREQUENCY = c_uint(1020)
    SMARPOD_POSITIONERS_MIN_SPEED = c_uint(1100)

    # move-status constants
    SMARPOD_STOPPED = 0
    SMARPOD_HOLDING = c_uint(1)
    SMARPOD_MOVING = c_uint(2)
    SMARPOD_CALIBRATING = c_uint(3)
    SMARPOD_REFERENCING = c_uint(4)
    SMARPOD_STANDBY = c_uint(5)

    def __init__(self):
        if os.name == "nt":
            raise NotImplemented("Windows not yet supported")
            # WinDLL.__init__(self, "libsmarpod.dll")  # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libsmarpod.so", RTLD_GLOBAL)
            self.major = c_uint()
            self.minor = c_uint()
            self.update = c_uint()
            self.Smarpod_GetDLLVersion(byref(self.major), byref(self.minor), byref(self.update))
            logging.debug("Using SmarPod library version %u.%u.%u", self.major.value, self.minor.value, self.update.value)


class SmartPodError(model.HwError):

    def __init__(self, error_status):
        self.status = error_status
        super(SmartPodError, self).__init__("Code %d" % error_status)


def CheckErr(st):
    if st != SmartPodDLL.SMARPOD_OK.value:
        raise SmartPodError(st)


class Pose(Structure):
    _fields_ = [
        ("positionX", c_double),
        ("positionY", c_double),
        ("positionZ", c_double),
        ("rotationX", c_double),
        ("rotationY", c_double),
        ("rotationZ", c_double),
        ]


class SmartPod(model.Actuator):
    
    def __init__(self, name, role, locator, options, axes=None, **kwargs):

        if len(axes) == 0:
            raise ValueError("Needs at least 1 axis.")
        self.core = SmartPodDLL()

        # Not to be mistaken with axes which is a simple public view
        self._axis_map = {}  # axis name -> axis number used by controller
        axes_def = {}  # axis name -> Axis object
        self._locator = c_char_p(locator)
        self._options = c_char_p(options)

        for axis_name, axis_par in axes.items():
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

            ad = model.Axis(canAbs=True, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad
            
            
        # Connect to the device
        self._id = c_uint()
        CheckErr(self.core.Smarpod_Open(byref(self._id), c_uint(10001), self._locator, self._options))
        logging.debug("Successfully connected to SmartPod Controller ID %d", self._id.value)
        CheckErr(self.core.Smarpod_SetSensorMode(self._id, SmartPodDLL.SMARPOD_SENSORS_ENABLED))

        # Check referencing
        self._referenced = c_int()
        CheckErr(self.core.Smarpod_IsReferenced(self._id, byref(self._referenced)))
        if not self._referenced.value:
            logging.debug("SmartPod is not referenced. Referncing...")
            CheckErr(self.core.Smarpod_FindReferenceMarks(self._id))
            CheckErr(self.core.Smarpod_IsReferenced(self._id, byref(self._referenced)))
            logging.debug("Referencing complete.")
        else:
            logging.debug("SmartPod is referenced")

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        self.position = model.VigilantAttribute({}, readonly=True)
        self._updatePosition()
        self.speed = self.GetSpeed()

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(1)  # one task at a time

    def terminate(self):
        self.core.Smarpod_Close(self._id)
        model.Actuator.terminate(self)
        
    def SetProperty(self, prop, value):
        """
        Set SmartPod internal property register
        """
        if isinstance(value, c_uint):
            CheckErr(self.core.Smarpod_Set_ui(self._id, prop, value))
        elif isinstance(value, c_int):
            CheckErr(self.core.Smarpod_Set_i(self._id, prop, value))
        elif isinstance(value, c_double):
            CheckErr(self.core.Smarpod_Set_d(self._id, prop, value))
        else:
            raise ValueError("value must be a C-type (uint, int, or double)")
    
    def GetMoveStatus(self):
        """
        Gets the move status from the controller
        """
        status = c_uint()
        self.core.Smarpod_GetMoveStatus(self._id, byref(status))
        return status

    def MoveToPose(self, pos):
        """
        Move to pose command. Non-blocking
        pos: (dict str -> float): axis name -> position
            This is converted to the pose C-struct which is sent to the SmartPod DLL
        Raises: SmartPodError if a problem occurs
        """
        # convert into a smartpad pose
        newPose = Pose()
        for an, v in pos.items():
            if an == "x":
                newPose.positionX = v
            elif an == "y":
                newPose.positionY = v
            elif an == "z":
                newPose.positionZ = v
            elif an == "theta_x":
                newPose.rotationX = v
            elif an == "theta_y":
                newPose.rotationY = v
            elif an == "theta_z":
                newPose.rotationZ = v
            else:
                raise ValueError("Invalid axis")

        CheckErr(self.core.Smarpod_Move(self._id, byref(newPose), c_uint(6000), c_int(0)))

    def GetPose(self):
        """
        Get the current pose of the SmartPod

        returns: (dict str -> float): axis name -> position
        """

        pose = Pose()

        CheckErr(self.core.Smarpod_GetPose(self._id, byref(pose)))
        pos = {}
        pos['x'] = pose.positionX
        pos['y'] = pose.positionY
        pos['z'] = pose.positionZ
        pos['theta_x'] = pose.rotationX
        pos['theta_y'] = pose.rotationY
        pos['theta_z'] = pose.rotationZ

        return pos

    def StopCommand(self):
        """
        Stop command sent to the SmartPod
        """
        logging.debug("Stopping...")
        CheckErr(self.core.Smarpod_Stop(self._id))

    def SetSpeed(self, value):
        """
        Set the speed of the SmartPod motion
        value: (double) indicating speed for all axes
        """
        logging.debug("Setting speed to %f", value)
        CheckErr(self.core.Smarpod_SetSpeed(self._id, c_int(1), c_double(value)))
        self.speed = value

    def GetSpeed(self):
        """
        Returns (double) the speed of the SmartPod motion
        """
        speed_control = c_int()
        speed = c_double()

        CheckErr(self.core.Smarpod_GetSpeed(self._id, byref(speed_control), byref(speed)))
        return speed

    def stop(self, axes=None):
        """
        Stop the SmartPod controller and update position
        """
        self.StopCommand()
        self._updatePosition()

    def _updatePosition(self):
        """
        update the position VA
        """
        pos = self.GetPose()
        self.position._set_value(pos, force_write=True)

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
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def _doMoveAbs(self, future, pos):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        _pos (dict str -> float): axis name -> absolute target position
        raise:
            SmartPodError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        old_pos = self.position.value
        d = 0.5
        dur = driver.estimateMoveDuration(d, self.speed, 0.0001)
        end = time.time() + dur

        last_upd = time.time()
        dur = max(0.01, min(end - last_upd, 60))
        max_dur = dur * 2 + 1
        logging.debug("Expecting a move of %g s, will wait up to %g s", dur, max_dur)
        timeout = last_upd + max_dur

        with future._moving_lock:
            self.MoveToPose(pos)  # blocking function
            while not future._must_stop.is_set():
                status = self.GetMoveStatus()
                if status.value == SmartPodDLL.SMARPOD_STOPPED:
                    break

                now = time.time()
                if now > timeout:
                    logging.warning("Stopping move due to timeout after %g s.", max_dur)
                    self.stop()
                    raise TimeoutError("Move is not over after %g s, while "
                                       "expected it takes only %g s" %
                                       (max_dur, dur))

                # Update the position from time to time (10 Hz)
                if now - last_upd > 0.1:
                    self._updatePosition()
                    last_upd = time.time()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0.001, min(left / 2, 0.1))
                future._must_stop.wait(sleept)

                time.sleep(0.1)
            else:
                self.stop()
                future._was_stopped = True
                raise CancelledError()

        self._updatePosition()

        logging.debug("move successfully completed")

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
        logging.debug("Canceling current move")

        future._must_stop.set()  # tell the thread taking care of the move it's over
        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Canceling failed")
            self._updatePosition()
            return future._was_stopped

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()

        pos = add_coord(self.position.value, shift)

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

