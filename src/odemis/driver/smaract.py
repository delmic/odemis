# -*- coding: utf-8 -*-
'''
Created on 17 April 2019

@author: Anders Muskens, Philip Winkler

Copyright © 2012-2020 Anders Muskens, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

This driver supports SmarAct and SmarPod actuators, which are accessed via a C DLL library
provided by SmarAct. This must be installed on the system for this actuator to run. Please
refer to the SmarAct readme for Linux installation instructions.
'''
from concurrent.futures import CancelledError, TimeoutError
import copy
from ctypes import *
import logging
import math
from odemis import model
from odemis import util
from odemis.model import CancellableFuture, CancellableThreadPoolExecutor, isasync, VigilantAttribute, roattribute
from odemis.util import driver, RepeatingTimer, almost_equal
import os
import threading
import time
from typing import Optional, Dict


def add_coord(pos1, pos2):
    """
    Adds two coordinate dictionaries together and returns a new coordinate dictionary.

    All of the keys (axis names) in pos2 must be present in pos1

    pos1: dict (axis name str) -> (float)
    pos2: dict (axis name str) -> (float)
    Returns ret
        dict (axis name str) -> (float)
    """
    ret = pos1.copy()
    for an, v in pos2.items():
        ret[an] += v

    return ret


class Smarpod_Pose(Structure):
    """
    SmarPod Pose Structure (C Struct used by DLL)

    Note: internally, the system uses metres and degrees for rotation
    """
    _fields_ = [
        ("positionX", c_double),
        ("positionY", c_double),
        ("positionZ", c_double),
        ("rotationX", c_double),
        ("rotationY", c_double),
        ("rotationZ", c_double),
        ]
    
    def __add__(self, o):
        pose = Smarpod_Pose()
        pose.positionX = self.positionX + o.positionX
        pose.positionY = self.positionY + o.positionY
        pose.positionZ = self.positionZ + o.positionZ
        pose.rotationX = self.rotationX + o.rotationX
        pose.rotationY = self.rotationY + o.rotationY
        pose.rotationZ = self.rotationZ + o.rotationZ
        return pose

    def __sub__(self, o):
        pose = Smarpod_Pose()
        pose.positionX = self.positionX - o.positionX
        pose.positionY = self.positionY - o.positionY
        pose.positionZ = self.positionZ - o.positionZ
        pose.rotationX = self.rotationX - o.rotationX
        pose.rotationY = self.rotationY - o.rotationY
        pose.rotationZ = self.rotationZ - o.rotationZ
        return pose

    def __str__(self):
        return "SmarPod_Pose. x: %f, y: %f, z: %f, rx: %f, ry: %f, rz: %f" % \
            (self.positionX, self.positionY, self.positionZ,
             self.rotationX, self.rotationY, self.rotationZ)

    def asdict(self):
        """
        Convert the pose to a coordinate dictionary
        returns (dict str -> float): Coordinates as axis name -> value.
          Distance is in meters, and rotation are in radians.
        """
        # Note: internally, the system uses meters for positions and degrees for rotation
        pos = {
            'x': self.positionX,
            'y': self.positionY,
            'z': self.positionZ,
            'rx': math.radians(self.rotationX),
            'ry': math.radians(self.rotationY),
            'rz': math.radians(self.rotationZ)
        }
        return pos

    def update(self, pos):
        """
        Changes the values of some of the axes.
        pos (dict str -> float): Coordinates as axis name -> value. Not all axes have
          to be defined. Distance is in meters, and rotation are in radians.
        raises ValueError if an unsupported axis name is input
        """
        # Note: internally, the system uses meters for positions and degrees for rotation
        for an, v in pos.items():
            if an == "x":
                self.positionX = v
            elif an == "y":
                self.positionY = v
            elif an == "z":
                self.positionZ = v
            elif an == "rx":
                self.rotationX = math.degrees(v)
            elif an == "ry":
                self.rotationY = math.degrees(v)
            elif an == "rz":
                self.rotationZ = math.degrees(v)
            else:
                raise ValueError(f"Invalid axis {an}")


class SmarPodDLL(CDLL):
    """
    Subclass of CDLL specific to SmarPod library, which handles error codes for
    all the functions automatically.
    """
    
    # Status
    OK = 0
    OTHER_ERROR = 1
    SYSTEM_NOT_INITIALIZED_ERROR = 2
    NO_SYSTEMS_FOUND_ERROR = 3
    INVALID_PARAMETER_ERROR = 4
    COMMUNICATION_ERROR = 5
    UNKNOWN_PROPERTY_ERROR = 6
    RESOURCE_TOO_OLD_ERROR = 7
    FEATURE_UNAVAILABLE_ERROR = 8
    INVALID_SYSTEM_LOCATOR_ERROR = 9
    QUERYBUFFER_SIZE_ERROR = 10
    COMMUNICATION_TIMEOUT_ERROR = 11
    DRIVER_ERROR = 12
    STATUS_CODE_UNKNOWN_ERROR = 500
    INVALID_ID_ERROR = 501
    INITIALIZED_ERROR = 502
    HARDWARE_MODEL_UNKNOWN_ERROR = 503
    WRONG_COMM_MODE_ERROR = 504
    NOT_INITIALIZED_ERROR = 505
    INVALID_SYSTEM_ID_ERROR = 506
    NOT_ENOUGH_CHANNELS_ERROR = 507
    INVALID_CHANNEL_ERROR = 508
    CHANNEL_USED_ERROR = 509
    SENSORS_DISABLED_ERROR = 510
    WRONG_SENSOR_TYPE_ERROR = 511
    SYSTEM_CONFIGURATION_ERROR = 512
    SENSOR_NOT_FOUND_ERROR = 513
    STOPPED_ERROR = 514
    BUSY_ERROR = 515
    NOT_REFERENCED_ERROR = 550
    POSE_UNREACHABLE_ERROR = 551
    COMMAND_OVERRIDDEN_ERROR = 552
    ENDSTOP_REACHED_ERROR = 553
    NOT_STOPPED_ERROR = 554
    COULD_NOT_REFERENCE_ERROR = 555
    COULD_NOT_CALIBRATE_ERROR = 556

    # For SensorPowerMode
    SENSORS_DISABLED = 0
    SENSORS_ENABLED = 1
    SENSORS_POWERSAVE = 2

    # Property symbols
    FREF_METHOD = 1000
    FREF_ZDIRECTION = 1002
    FREF_XDIRECTION = 1003
    FREF_YDIRECTION = 1004
    PIVOT_MODE = 1010
    REF_AND_CAL_FREQUENCY = 1020
    POSITIONERS_MIN_SPEED = 1100  # double

    # For PivotMode
    PIVOT_RELATIVE = 0
    PIVOT_FIXED = 1

    # move-status constants
    STOPPED = 0
    HOLDING = 1
    MOVING = 2
    CALIBRATING = 3
    REFERENCING = 4
    STANDBY = 5
    
    HOLDTIME_INFINITE = 60000

    err_code = {
        0: "OK",
        1: "OTHER_ERROR",
        2: "SYSTEM_NOT_INITIALIZED_ERROR",
        3: "NO_SYSTEMS_FOUND_ERROR",
        4: "INVALID_PARAMETER_ERROR",
        5: "COMMUNICATION_ERROR",
        6: "UNKNOWN_PROPERTY_ERROR",
        7: "RESOURCE_TOO_OLD_ERROR",
        8: "FEATURE_UNAVAILABLE_ERROR",
        9: "INVALID_SYSTEM_LOCATOR_ERROR",
        10: "QUERYBUFFER_SIZE_ERROR",
        11: "COMMUNICATION_TIMEOUT_ERROR",
        12: "DRIVER_ERROR",
        500: "STATUS_CODE_UNKNOWN_ERROR",
        501: "INVALID_ID_ERROR",
        503: "HARDWARE_MODEL_UNKNOWN_ERROR",
        504: "WRONG_COMM_MODE_ERROR",
        505: "NOT_INITIALIZED_ERROR",
        506: "INVALID_SYSTEM_ID_ERROR",
        507: "NOT_ENOUGH_CHANNELS_ERROR",
        510: "SENSORS_DISABLED_ERROR",
        511: "WRONG_SENSOR_TYPE_ERROR",
        512: "SYSTEM_CONFIGURATION_ERROR",
        513: "SENSOR_NOT_FOUND_ERROR",
        514: "STOPPED_ERROR",
        515: "BUSY_ERROR",
        550: "NOT_REFERENCED_ERROR",
        551: "POSE_UNREACHABLE_ERROR",
        552: "COMMAND_OVERRIDDEN_ERROR",
        553: "ENDSTOP_REACHED_ERROR",
        554: "NOT_STOPPED_ERROR",
        555: "COULD_NOT_REFERENCE_ERROR",
        556: "COULD_NOT_CALIBRATE_ERROR",
    }

    def __init__(self):
        if os.name == "nt":
            raise NotImplementedError("Windows not yet supported")
            # WinDLL.__init__(self, "libsmarpod.dll")  # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libsmarpod.so", RTLD_GLOBAL)

    def __getitem__(self, name):
        try:
            func = super(SmarPodDLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        func.errcheck = self.sp_errcheck
        return func

    @staticmethod
    def sp_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result != SmarPodDLL.OK:
            raise SmarPodError(result)

        return result


class SmarPodError(Exception):
    """
    SmarPod Exception
    """

    def __init__(self, errno, *args, **kwargs):
        # Needed for pickling, cf https://bugs.python.org/issue1692335 (fixed in Python 3.3)
        super(SmarPodError, self).__init__(errno, *args, **kwargs)
        self.errno = errno
        self.strerror = "Error %d. %s" % (errno, SmarPodDLL.err_code.get(errno, ""))

    def __str__(self):
        return self.strerror


class SmarPod(model.Actuator):
    
    def __init__(self, name: str, role: str, locator: str, hwmodel: int=10001,
                 axes: dict=None, ref_on_init: bool=False,
                 speed: float=1e-3, accel: float=1e-3,
                 hold_time=float("inf"), **kwargs):
        """
        A driver for a SmarAct SmarPod Actuator.
        This driver uses the SmarPod DLL provided by SmarAct which connects via
        USB or TCP/IP using a locator string.

        name:
        role:
        locator: Use "fake" for a simulator.
            For a real device, MCS2 controllers with USB interface can be addressed with the
            following locator syntax:
                usb:sn:<serialnumber>
            where <serialnumber> is the serial number of an MCS2 controller.
            Be aware that on Linux, a udev rules must be added to disable the standard
            tty driver from connecting to the device.
            If the controller has a TCP/IP connection, use one of:
                network:<ipv4>
                network:sn:<serialnumber>
        hwmodel: the hardware model code (typically between 10000 and 10100)
        axes (dict str (axis name) -> dict (axis parameters)):
            Typically, axes are x, y, z, rx, ry, rz
            axis parameters: {
                range: [float, float], default is -1 -> 1
                unit: (str) default will be set to 'm'
            }
        ref_on_init: determines if the controller should automatically reference
            on initialization
        speed: the maximum speed (in m/s) of a point on the stage
        accel: the maximum acceleration (in m/s²) of a point on the stage
        hold_time (float>=0): the hold time, in seconds, for the actuator after the target position is reached.
            Default is infinite (float('inf') in Python, .inf in YAML). Can be also set to 0 to disable hold.
        """
        if not axes:
            raise ValueError("Needs at least 1 axis.")

        if hold_time < 0:
            raise ValueError(f"hold_time should be > 0, but got {hold_time}")
        self._hold_time = hold_time

        if locator == "fake":
            self.core = FakeSmarPodDLL()
        else:
            self.core = SmarPodDLL()
            
        # Not to be mistaken with axes which is a simple public view
        self._axis_map = {}  # axis name -> axis number used by controller
        axes_def = {}  # axis name -> Axis object

        for axis_name, param in axes.items():
            try:
                axis_range = param['range']
            except KeyError:
                logging.info("Axis %s has no range. Assuming (-1, 1)", axis_name)
                axis_range = (-1, 1)

            try:
                axis_unit = param['unit']
            except KeyError:
                logging.info("Axis %s has no unit. Assuming m", axis_name)
                axis_unit = "m"

            ad = model.Axis(canAbs=True, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad

        # Connect to the device
        self._id = c_uint()
        try:
            self.core.Smarpod_Open(byref(self._id), c_ulong(hwmodel),
                                   c_char_p(locator.encode("ascii")),
                                   c_char_p(b""))  # last arg is unused
        except SmarPodError as ex:
            if ex.errno == SmarPodDLL.NO_SYSTEMS_FOUND_ERROR:
                raise model.HwError("Failed to find device, check it is connected and turned on") from ex
            raise

        logging.debug("Successfully connected to SmarPod Controller ID %d", self._id.value)
        self.core.Smarpod_SetSensorMode(self._id, SmarPodDLL.SENSORS_ENABLED)

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        # Add metadata
        self._swVersion = "%u.%u.%u" % self.GetDLLVersion()
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        logging.debug("Using SmarPod library version %s", self._swVersion)

        self.position = model.VigilantAttribute({}, readonly=True)
        self._updatePosition()

        # For now we always set the pivot point to "fixed" mode.
        # When the stage moves, the pivot point does not, which is useful if
        # needed to rotate around external object (eg, a lens).
        self.Set_ui(SmarPodDLL.PIVOT_MODE, SmarPodDLL.PIVOT_FIXED)
        self._metadata[model.MD_PIVOT_POS] = self.GetPivot()

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(1)  # one task at a time

        # Use a default actuator speed
        self.SetSpeed(speed)
        self.speed = VigilantAttribute({}, unit="m/s", readonly=True)
        self._updateSpeed()
        self.SetAcceleration(accel)
        self._accel = self.GetAcceleration()

        referenced = self.IsReferenced()
        # define the referenced VA from the query
        axes_ref = {a: referenced for a in self.axes}
        # VA dict str(axis) -> bool
        self.referenced = model.VigilantAttribute(axes_ref, readonly=True)
        # If ref_on_init, referenced immediately.
        if referenced:
            logging.debug("SmarPod is referenced")
        else:
            if ref_on_init:
                self.reference(set(axes_ref.keys()))  # will reference in background
            else:
                logging.warning("SmarPod is not referenced. The device will not function until referencing occurs.")

        self._update_position_timer = RepeatingTimer(1.0, self._updatePositionInBackground)
        self._update_position_timer.start()

    def terminate(self):
        self._update_position_timer.cancel()

        if self._executor:
            self.Stop()
            self._executor.shutdown()
            self._executor = None
            self.core.Smarpod_Close(self._id)

        # should be safe to close the device multiple times if terminate is called more than once.
        super(SmarPod, self).terminate()

    def updateMetadata(self, md):
        if model.MD_PIVOT_POS in md:
            pivot = md[model.MD_PIVOT_POS]
            if not (isinstance(pivot, dict) and set(pivot.keys()) == {"x", "y", "z"}):
                raise ValueError("Invalid metadata, should be a coordinate dictionary with x, y, z keys but got %s." % (pivot,))

            logging.debug("Updating pivot point to %s.", pivot)
            self.SetPivot(pivot)

        super().updateMetadata(md)
        
    def GetDLLVersion(self):
        """
        Request the software version of the DLL file
        return (int, int, int): major, minor, update version numbers
        """
        major = c_uint()
        minor = c_uint()
        update = c_uint()
        self.core.Smarpod_GetDLLVersion(byref(major), byref(minor), byref(update))
        return major.value, minor.value, update.value

    def Set_ui(self, property: int, value: int):
        """
        Set a property of type unsigned integer
        property (> 0): the ID of the property (typically, between 1000 and 1100)
        value (> 0): the value to set
        """
        self.core.Smarpod_Set_ui(self._id, c_uint(property), c_uint(value))

    def Set_d(self, property: int, value: float):
        """
        Set a property of type double float
        property (> 0): the ID of the property (typically, between 1000 and 1100)
        value: the value to set
        """
        self.core.Smarpod_Set_d(self._id, c_uint(property), c_double(value))

    # Note: There is also getter/setter for "integer" properties, but there are no such properties

    def Get_ui(self, property: int) -> int:
        """
        Reads a property of type unsigned integer
        property (> 0): the ID of the property (typically, between 1000 and 1100)
        return value (> 0): the value of the property
        """
        value = c_uint()
        self.core.Smarpod_Get_ui(self._id, c_uint(property), byref(value))
        return value.value

    def Get_d(self, property: int) -> float:
        """
        Reads a property of type double float
        property (> 0): the ID of the property (typically, between 1000 and 1100)
        return value (> 0): the value of the property
        """
        value = c_double()
        self.core.Smarpod_Get_d(self._id, c_uint(property), byref(value))
        return value.value

    def IsReferenced(self):
        """
        Ask the controller if it is referenced
        """
        referenced = c_int()
        self.core.Smarpod_IsReferenced(self._id, byref(referenced))
        return bool(referenced.value)

    def GetMoveStatus(self):
        """
        Gets the move status from the controller.
        Returns:
            SmarPodDLL.MOVING is returned if moving
            SmarPodDLL.STOPPED when stopped
            SmarPodDLL.HOLDING when holding between moves
            SmarPodDLL.CALIBRATING when calibrating
            SmarPodDLL.REFERENCING when referencing
            SmarPodDLL.STANDBY
        """
        status = c_uint()
        self.core.Smarpod_GetMoveStatus(self._id, byref(status))
        return status.value

    def Move(self, pos, hold_time=0, block=False):
        """
        Move to pose command. It is possible and safe to call it if a previous
          movement has been called in non-blocking mode.
        pos: (dict str -> float) axis name -> position
            This is converted to the pose C-struct which is sent to the SmarPod DLL
        hold_time: (float >=0) specify in seconds how long to hold after the move.
            If set to float(inf), will hold forever until a stop command is issued.
        block: (bool) Set to True if the function should block until the move completes

        Raises: SmarPodError if a problem occurs
        """
        # convert into a SmarpodPose
        newPose = self.GetPose()
        newPose.update(pos)

        if hold_time < 0:
            raise ValueError(f"hold_time should be >= 0, is {hold_time}")

        if hold_time == float("inf"):
            ht = SmarPodDLL.HOLDTIME_INFINITE
        else:
            ht = int(hold_time * 1000)

        self.core.Smarpod_Move(self._id, byref(newPose), c_uint(ht), c_int(block))

    def GetPose(self):
        """
        Get the current pose of the SmarPod

        returns: (dict str -> float): axis name -> position
        """
        pose = Smarpod_Pose()
        self.core.Smarpod_GetPose(self._id, byref(pose))
        return pose

    def Stop(self):
        """
        Stop command sent to the SmarPod
        """
        logging.debug("Stopping...")
        self.core.Smarpod_Stop(self._id)

    def SetSpeed(self, value: Optional[float]):
        """
        Set the speed of the SmarPod motion
        value: (float or None) the maximum velocity (m/s) of the fastest moving positioner,
          the other positioners move so that points on the stage move at constant
          speed.
          If None, disable speed control: all positioners go at the maximum speed
        """
        # TODO: allow to pass None to disable constant speed
        logging.debug("Setting speed to %f", value)
        if value is None:  # Disable speed control
            self.core.Smarpod_SetSpeed(self._id, c_int(0), c_double(0))
        else:
            # the second argument (1) turns on speed control.
            self.core.Smarpod_SetSpeed(self._id, c_int(1), c_double(value))

    def GetSpeed(self) -> Optional[float]:
        """
        Returns (float or None) the speed of the SmarPod motion.
          If speed control is disabled, it returns None.
        """
        speed_control = c_int()
        speed = c_double()
        self.core.Smarpod_GetSpeed(self._id, byref(speed_control), byref(speed))
        if speed_control.value:
            return speed.value
        else:
            return None

    def SetAcceleration(self, value: Optional[float]):
        """
        Set the acceleration of the SmarPod motion
        value: (float or None) indicating acceleration for all axes.
          If None, disable acceleration control: speed is kept constant
        """
        logging.debug("Setting acceleration to %f", value)
        if value is None:  # Disable acceleration control
            self.core.Smarpod_SetAcceleration(self._id, c_int(0), c_double(0))
        else:
            # Passing 1 enables acceleration control.
            self.core.Smarpod_SetAcceleration(self._id, c_int(1), c_double(value))

    def GetAcceleration(self) -> Optional[float]:
        """
        Returns (float or None) the acceleration of the SmarPod motion
          If acceleration control is disabled, it return None.
        """
        acceleration_control = c_int()
        acceleration = c_double()
        self.core.Smarpod_GetAcceleration(self._id, byref(acceleration_control), byref(acceleration))
        if acceleration_control.value:
            return acceleration.value
        else:
            return None
    
    def IsPoseReachable(self, pos):
        """
        Ask the controller if a pose is reachable
        pos: (dict of str -> float): a coordinate dictionary of axis name to value
        returns: true if the pose is reachable - false otherwise.
        """
        reachable = c_int()
        newPose = self.GetPose()
        newPose.update(pos)
        self.core.Smarpod_IsPoseReachable(self._id, byref(newPose), byref(reachable))
        return bool(reachable.value)

    def SetPivot(self, piv_dict: Dict[str, float]):
        """
        Set the pivot point of the device

        piv_dict: Position dictionary, with 'x', 'y', 'z' position.
        """
        pivot = (c_double * 3)(piv_dict["x"], piv_dict["y"], piv_dict["z"])
        self.core.Smarpod_SetPivot(self._id, pivot)

    def GetPivot(self) -> Dict[str, float]:
        """
        Get the pivot point from the controller

        returns: position as axis name -> position of axis
        """
        pivot = (c_double * 3)()
        self.core.Smarpod_GetPivot(self._id, pivot)
        return {'x': pivot[0], 'y': pivot[1], 'z': pivot[2]}

    def _updatePositionInBackground(self):
        """
        Callback to update the position regularly in background in a separate thread
        """
        try:
            # Don't update if not referenced, as it cannot read the position,
            # and would just log a warning instead.
            if any(self.referenced.value.values()):
                self._updatePosition()
        except Exception:
            logging.exception("Failed to update the position")

    def _updatePosition(self):
        """
        update the position VA
        """
        try:
            p = self.GetPose().asdict()
        except SmarPodError as ex:
            if ex.errno == SmarPodDLL.NOT_REFERENCED_ERROR:
                logging.warning("Position unknown because SmarPod is not referenced")
                p = {a: 0 for a in self.axes}
            else:
                raise

        p = self._applyInversion(p)
        logging.debug("Updated position to %s", p)
        self.position._set_value(p, force_write=True)

    def _updateSpeed(self):
        """
        update the speed
        """
        # The set speed is the maximum speed of the positioners, not the axes
        # (which are composed by movements from several positioners), but let's
        # just approximate it as the same speed on all linear axes
        s = {}
        speed = self.GetSpeed()
        if speed is not None:
            for axis_name, axis_def in self.axes.items():
                if axis_def.unit == "m":
                    s[axis_name] = speed

        logging.debug("Updated speed to %s", s)
        self.speed._set_value(s, force_write=True)

    def _createMoveFuture(self, ref=False):
        """
        ref: if true, will use a different canceller
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f.task_canceller = self._cancelCurrentMove
        return f

    @isasync
    def reference(self, axes):
        """
        axes (set of str): Typically, this contains the set of axes to reference.
          However, as the SmarPod references all axes together, as long as axes
          contains (only) valid axes, all axes are referenced.
        returns (Future): object to control the reference request
        """
        self._checkReference(axes)

        f = self._createMoveFuture()
        self._executor.submitf(f, self._doReference, f)
        return f

    def _doReference(self, future):
        """
        Actually runs the referencing code
        future (Future): the future it handles
        raise:
            IOError: if referencing failed due to hardware
            CancelledError if was cancelled
        """
        try:
            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()
                # Reset reference so that if it fails, it states the axes are not
                # referenced (anymore)
                self.referenced._value = {a: False for a in self.axes.keys()}

            # The SmarPod references all axes at once. This function blocks
            logging.debug("Starting reference procedure")
            self.core.Smarpod_FindReferenceMarks(self._id)

            if self.IsReferenced():
                logging.info("Referencing successful.")
                self.referenced._value = {a: True for a in self.axes.keys()}

        except SmarPodError as ex:
            # This occurs if a stop command interrupts referencing
            if ex.errno == SmarPodDLL.STOPPED_ERROR:
                logging.info("Referencing stopped: %s", ex)
                raise CancelledError()
            else:
                logging.error("Referencing failed: %s", ex)
                raise
        except CancelledError:
            logging.debug("Referencing canceled")
            raise  # No fuss, pass it as-is
        except Exception:
            logging.exception("Referencing failure")
            raise
        finally:
            self._updatePosition()
            # We only notify after updating the position so that when a listener
            # receives updates both values are already updated.
            # read-only so manually notify
            self.referenced.notify(self.referenced.value)

    @isasync
    def moveAbs(self, pos):
        """
        API call to absolute move
        """
        if not pos:
            return model.InstantaneousFuture()
        
        self._checkMoveAbs(pos)

        pos = self._applyInversion(pos)
        if not self.IsPoseReachable(pos):
            raise ValueError("Pose %s is not reachable by the SmarPod controller" % (pos,))

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def _estimateMoveDuration(self, new_pos) -> float:
        """
        Estimate the maximum duration of a move
        new_pos: (dict str -> float): axis name -> absolute target position
        returns: the duration of the move in seconds
        """
        pos = self._applyInversion(self.position.value)
        # TODO: Calculate an estimated move duration
        # Probably using the speed + accel on the translation could work as a
        # conservative estimate for translation moves. However for the rotational
        # moves that's harder. => Just use a hard-coded value for rotations
        return 30  # s

    def _doMoveAbs(self, future, pos):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        _pos (dict str -> float): axis name -> absolute target position
        raise:
            SmarPodError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        last_upd = time.time()
        dur = self._estimateMoveDuration(pos)
        end = time.time() + dur
        max_dur = dur * 2 + 1
        logging.debug("Expecting a move of %g s, will wait up to %g s", dur, max_dur)
        timeout = last_upd + max_dur

        try:
            # Start the move
            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()
                self.Move(pos, self._hold_time, block=False)

            # Wait until the move is done
            while not future._must_stop.is_set():
                status = self.GetMoveStatus()
                # check if move is done
                if status == SmarPodDLL.STOPPED:
                    break

                now = time.time()
                if now > timeout:
                    logging.warning("Stopping move due to timeout after %g s.", max_dur)
                    self.Stop()
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
            else:
                raise CancelledError()
        except SmarPodError as ex:
            if ex.errno == MC_5DOF_DLL.SA_MC_ERROR_CANCELED:
                logging.debug("Movement stopped: %s", ex)
                raise CancelledError()
            elif future._must_stop.is_set():
                raise CancelledError()
            else:
                logging.error("Move failed: %s", ex)
                raise
        except CancelledError:
            logging.debug("Movement canceled")
            raise  # No fuss, pass it as-is
        except Exception:
            logging.exception("Move failure")
            raise
        finally:
            self._updatePosition()

        logging.debug("Move successfully completed")

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute, relative, or referencing). Non-blocking.
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
            self.Stop()

        return True

    @isasync
    def moveRel(self, shift):
        """
        API call for relative move
        """
        if not shift:
            return model.InstantaneousFuture()

        self._checkMoveRel(shift)

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f
    
    def _doMoveRel(self, future, shift):
        """
        Do a relative move by converting it into an absolute move
        """
        pos = self._applyInversion(add_coord(self.position.value, shift))
        self._doMoveAbs(future, pos)

    def stop(self, axes=None):
        """
        Stop the SmarPod controller and update position
        """
        self.Stop()
        self._executor.cancel()
        self._updatePosition()

    @staticmethod
    def scan():
        """
        Search for connected devices
        return (list of 2-tuple: name (str), args (dict))
        """
        core = SmarPodDLL()
        systems = create_string_buffer(4096)
        bufferSize = c_size_t(len(systems))
        core.Smarpod_FindSystems(c_char_p(b""), byref(systems), byref(bufferSize))
        locators = systems.value.decode("latin1")

        ret = []
        for locator in locators.split("\n"):  # TODO: check which character is used to separate locators
            # Use the ID or SN (ie, last part of the locator) as name
            ret.append((locator.split(":")[-1], {"locator": locator}))
        return ret

# Only for testing/simulation purpose
# Very rough version that is just enough so that if the wrapper behaves correctly,
# it returns the expected values.


def _deref(p, typep):
    """
    p (byref object)
    typep (c_type): type of pointer
    Use .value to change the value of the object
    """
    # This is using internal ctypes attributes, that might change in later
    # versions. Ugly!
    # Another possibility would be to redefine byref by identity function:
    # byref= lambda x: x
    # and then dereferencing would be also identity function.
    return typep.from_address(addressof(p._obj))


class FakeSmarPodDLL(object):
    """
    Fake SmarPod DLL for simulator
    """

    def __init__(self):
        self.pose = Smarpod_Pose()
        self.target = Smarpod_Pose()
        self.properties = {
            SmarPodDLL.PIVOT_MODE: SmarPodDLL.PIVOT_RELATIVE
        }
        self._speed = c_double(0)
        self._speed_control = c_int(0)
        self._accel_control = c_int(0)
        self._accel = c_double(0)
        self.referenced = False

        self._pivot = [0, 0, 0]

        # Specify ranges
        self._range = {}
        self._range['x'] = (-1, 1)
        self._range['y'] = (-1, 1)
        self._range['z'] = (-1, 1)
        self._range['rx'] = (-45, 45)
        self._range['ry'] = (-45, 45)
        self._range['rz'] = (-45, 45)

        self.stopping = threading.Event()

        self._current_move_start = time.time()
        self._current_move_finish = time.time()

    def _pose_in_range(self, pose):
        if self._range['x'][0] <= pose.positionX <= self._range['x'][1] and \
            self._range['y'][0] <= pose.positionY <= self._range['y'][1] and \
            self._range['z'][0] <= pose.positionZ <= self._range['z'][1] and \
            self._range['rx'][0] <= pose.rotationX <= self._range['rx'][1] and \
            self._range['ry'][0] <= pose.rotationY <= self._range['ry'][1] and \
            self._range['rz'][0] <= pose.rotationZ <= self._range['rz'][1]:
            return True
        else:
            return False

    """
    DLL functions (fake)
    These functions are provided by the real SmarPod DLL
    """

    def Smarpod_Open(self, p_id, model, locator, options):
        pass

    def Smarpod_Close(self, id):
        pass

    def Smarpod_SetSensorMode(self, id, mode):
        pass

    def Smarpod_FindReferenceMarks(self, id):
        self.stopping.clear()
        if self.stopping.wait(2):
            self.referenced = False
            raise SmarPodError(SmarPodDLL.STOPPED_ERROR)
        else:
            self.referenced = True

    def Smarpod_IsPoseReachable(self, id, p_pos, p_reachable):
        reachable = _deref(p_reachable, c_int)
        pos = _deref(p_pos, Smarpod_Pose)
        if self._pose_in_range(pos):
            reachable.value = 1
        else:
            reachable.value = 0

    def Smarpod_IsReferenced(self, id, p_referenced):
        referenced = _deref(p_referenced, c_int)
        referenced.value = 1 if self.referenced else 0

    def Smarpod_Move(self, id, p_pose, hold_time, block):
        self.stopping.clear()
        pose = _deref(p_pose, Smarpod_Pose)
        if self._pose_in_range(pose):
            self._current_move_finish = time.time() + 1.0
            self.target.positionX = pose.positionX
            self.target.positionY = pose.positionY
            self.target.positionZ = pose.positionZ
            self.target.rotationX = pose.rotationX
            self.target.rotationY = pose.rotationY
            self.target.rotationZ = pose.rotationZ
        else:
            raise SmarPodError(SmarPodDLL.POSE_UNREACHABLE_ERROR)

    def Smarpod_GetPose(self, id, p_pose):
        pose = _deref(p_pose, Smarpod_Pose)
        pose.positionX = self.pose.positionX
        pose.positionY = self.pose.positionY
        pose.positionZ = self.pose.positionZ
        pose.rotationX = self.pose.rotationX
        pose.rotationY = self.pose.rotationY
        pose.rotationZ = self.pose.rotationZ
        return SmarPodDLL.OK

    def Smarpod_GetMoveStatus(self, id, p_status):
        status = _deref(p_status, c_int)

        if time.time() > self._current_move_finish:
            self.pose = copy.copy(self.target)
            status.value = SmarPodDLL.STOPPED
        else:
            status.value = SmarPodDLL.MOVING

    def Smarpod_Stop(self, id):
        self.stopping.set()

    def Smarpod_SetSpeed(self, id, speed_control, speed):
        self._speed = speed
        self._speed_control = speed_control

    def Smarpod_GetSpeed(self, id, p_speed_control, p_speed):
        speed = _deref(p_speed, c_double)
        speed.value = self._speed.value
        speed_control = _deref(p_speed_control, c_int)
        speed_control.value = self._speed_control.value

    def Smarpod_SetAcceleration(self, id, accel_control, accel):
        self._accel = accel
        self._accel_control = accel_control

    def Smarpod_GetAcceleration(self, id, p_accel_control, p_accel):
        accel = _deref(p_accel, c_double)
        accel.value = self._accel.value
        accel_control = _deref(p_accel_control, c_int)
        accel_control.value = self._accel_control.value

    def Smarpod_GetDLLVersion(self, p_major, p_minor, p_update):
        major = _deref(p_major, c_uint)
        major.value = 1
        minor = _deref(p_minor, c_uint)
        minor.value = 2
        update = _deref(p_update, c_uint)
        update.value = 3

    def Smarpod_GetPivot(self, id, pivot):
        for i in range(3):
            pivot[i] = self._pivot[i]
        return SmarPodDLL.OK

    def Smarpod_SetPivot(self, id, pivot):
        for i in range(3):
            self._pivot[i] = pivot[i]
        return SmarPodDLL.OK

    def Smarpod_Set_ui(self, id, prop, val):
        if not prop.value in self.properties:
            raise SmarPodError(SmarPodDLL.INVALID_PARAMETER_ERROR, "error")

        self.properties[prop.value] = val

    def Smarpod_Set_d(self, id, prop, val):
        if not prop.value in self.properties:
            raise SmarPodError(SmarPodDLL.INVALID_PARAMETER_ERROR, "error")

        self.properties[prop.value] = val

    def Smarpod_Get_ui(self, id, prop, p_val):
        if not prop.value in self.properties:
            raise SmarPodError(SmarPodDLL.INVALID_PARAMETER_ERROR, "error")

        val = _deref(p_val, c_uint)
        val.value = self.properties[prop.value].value

    def Smarpod_Get_d(self, id, prop, p_val):
        if not prop.value in self.properties:
            raise SmarPodError(SmarPodDLL.INVALID_PARAMETER_ERROR, "error")

        val = _deref(p_val, c_double)
        val.value = self.properties[prop.value].value

"""
Classes associated with the SmarAct MC 5DOF Controller (custom for Delmic)
"""


class SA_MC_EventData(Union):
    """
    SA_MC event data is stored as this type of union (A C union used by DLL)
    """
    _fields_ = [
         ("i32", c_int32),
         ("i64", c_int64),
         ("reserved", c_int8 * 32),
         ]


class SA_MC_Event(Structure):
    """
    SA_MC Event structure (C struct used by DLL)
    """
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", c_uint32),
        ("unused", c_int8 * 28),
        ("u", SA_MC_EventData),
        ]

    def __str__(self):
        return "SA_MC_Event {type: %s, i32: %s}" % \
            (MC_5DOF_DLL.event_name.get(self.type, self.type),
             MC_5DOF_DLL.err_code.get(self.i32, self.i32))


class SA_MC_Vec3(Structure):
    """
    SA_MC 3d vector Structure (C Struct used by DLL)
    """
    _fields_ = [
        ("x", c_double),
        ("y", c_double),
        ("z", c_double),
        ]


class SA_MC_Pose(Structure):
    """
    SA_MC Pose Structure (C Struct used by DLL)

    Note: internally, the system uses metres and degrees for rotation
    """
    _fields_ = [
        ("x", c_double),
        ("y", c_double),
        ("z", c_double),
        ("rx", c_double),
        ("ry", c_double),
        ("rz", c_double),
    ]

    def __add__(self, o):
        pose = SA_MC_Pose()
        pose.x = self.x + o.x
        pose.y = self.y + o.y
        pose.z = self.z + o.z
        pose.rx = self.rx + o.rx
        pose.ry = self.ry + o.ry
        pose.rz = self.rz + o.rz
        return pose

    def __sub__(self, o):
        pose = SA_MC_Pose()
        pose.x = self.x - o.x
        pose.y = self.y - o.y
        pose.z = self.z - o.z
        pose.rx = self.rx - o.rx
        pose.ry = self.ry - o.ry
        pose.rz = self.rz - o.rz
        return pose
    
    def __str__(self):
        return "5DOF Pose. x: %f, y: %f, z: %f, rx: %f, ry: %f, rz: %f" % \
            (self.x, self.y, self.z, self.rx, self.ry, self.rz)

    def asdict(self):
        """
        Convert the pose to a coordinate dictionary (str) -> (double)
        returns (dict str -> float): Coordinates as axis name -> value.
        """
        # Note: internally, the system uses metres and degrees for rotation
        pos = {
            'x': self.x,
            'y': self.y,
            'z': self.z,
            'rx': math.radians(self.rx),
            'rz': math.radians(self.rz)
        }
        return pos

    def update(self, pos):
        """
        Changes the values of some of the axes.
        pos (dict str -> float): Coordinates as axis name -> value. Not all axes have
          to be defined.
        raises ValueError if an unsupported axis name is input
        """
        # Note: internally, the system uses metres and degrees for rotation
        for an, v in pos.items():
            if an == "x":
                self.x = v
            elif an == "y":
                self.y = v
            elif an == "z":
                self.z = v
            elif an == "rx":
                self.rx = math.degrees(v)
            elif an == "rz":
                self.rz = math.degrees(v)
            else:
                raise ValueError(f"Invalid axis {an}")


class MC_5DOF_DLL(CDLL):
    """
    Subclass of CDLL specific to SA_MC library, which handles error codes for
    all the functions automatically.
    """

    hwModel = 22000  # The only supported hardware (Delmic specific)

    # SmarAct MC error codes

    # No error
    SA_MC_OK = 0x0000
    # Unspecified error
    SA_MC_ERROR_OTHER = 0x0001
    # Invalid parameter in function call
    SA_MC_ERROR_INVALID_PARAMETER = 0x0002
    # Invalid locator in call to Open function
    SA_MC_ERROR_INVALID_LOCATOR = 0x0003
    # Invalid handle in call to function
    SA_MC_ERROR_INVALID_HANDLE = 0x0005
    # Tried to use an unsupported feature
    SA_MC_ERROR_NOT_SUPPORTED = 0x0006
    # Reached limit of simultaneously controllable devices
    SA_MC_ERROR_DEVICE_LIMIT_REACHED = 0x0007
    # Supplied buffer too small
    SA_MC_ERROR_QUERYBUFFER_SIZE = 0x0008
    # An operation has been canceled while waiting for a result
    SA_MC_ERROR_CANCELED = 0x0100
    # An operation has timed out
    SA_MC_ERROR_TIMEOUT = 0x0101
    # Undefined or inaccessible property in function call
    SA_MC_ERROR_INVALID_PROPERTY = 0x0020
    # The pose specified in the Move command is invalid/unreachable
    SA_MC_ERROR_POSE_UNREACHABLE = 0x0200
    # Device has not been referenced
    SA_MC_ERROR_NOT_REFERENCED = 0x0201
    # An operation could not be started because the device is busy
    SA_MC_ERROR_BUSY = 0x0203
    # Positioners were blocked during movement
    SA_MC_ERROR_ENDSTOP_REACHED = 0x0300
    # The following error limit has been exceeded during movement
    SA_MC_ERROR_FOLLOWING_ERROR_LIMIT_REACHED = 0x0301
    # Positioner referencing failed
    SA_MC_ERROR_REFERENCING_FAILED = 0x0320
    # Could not load required hardware driver
    SA_MC_ERROR_DRIVER_FAILED = 0x0500
    # Could not find/connect to controller
    SA_MC_ERROR_CONNECT_FAILED = 0x0501
    # The device is not connected
    SA_MC_ERROR_NOT_CONNECTED = 0x0502
    # The controller doesn't provide the require features or configuration
    SA_MC_ERROR_CONTROLLER_CONFIGURATION = 0x0503
    # Error when communicating with controller
    SA_MC_ERROR_COMMUNICATION_FAILED = 0x0504

    # property symbols
    SA_MC_PKEY_PIVOT_POINT_MODE = 0x00001001
    SA_MC_PKEY_IS_REFERENCED = 0x00002a01
    SA_MC_PKEY_HOLD_TIME = 0x00002000
    SA_MC_PKEY_MAX_SPEED_LINEAR_AXES = 0x00002010
    SA_MC_PKEY_MAX_SPEED_ROTARY_AXES = 0x00002011
    SA_MC_PKEY_PIEZO_MAX_CLF_LINEAR_AXES = 0x00002020
    SA_MC_PKEY_PIEZO_MAX_CLF_ROTARY_AXES = 0x00002021

    SA_MC_PKEY_REF_DIR_Y = 0x1532  # 2 = positive direction, 1 = negative direction
    SA_MC_PKEY_REF_DIR_TILT = 0x1542  # 2 = positive direction, 1 = negative direction

    SA_MC_PIVOT_POINT_MODE_RELATIVE = 0
    SA_MC_PIVOT_POINT_MODE_ABSOLUTE = 1

    # events
    SA_MC_EVENT_MOVEMENT_FINISHED = 0x0001

    event_name = {
        SA_MC_EVENT_MOVEMENT_FINISHED: "MOVEMENT_FINISHED"
        }

    # handles
    # handle value that means no object
    SA_MC_INVALID_HANDLE = 0xffffffff
    SA_MC_GLOBAL_HANDLE = 1
    SA_MC_INFINITE = -1

    SA_MC_PKEY_MODEL_CODE = 0x0a02
    SA_MC_PKEY_MODEL_NAME = 0x0a03
    SA_MC_PKEY_VERSION_STRING = 0x0100

    err_code = {
0x0000: "No error",
0x0001: "Unspecified error",
0x0002: "Invalid parameter in function call ",
0x0003: "Invalid locator in call to Open function ",
0x0005: "Invalid handle in call to function ",
0x0006: "Tried to use an unsupported feature",
0x0007: "Reached limit of simultaneously controllable devices ",
0x0008: "Supplied buffer too small",
0x0100: "An operation has been canceled while waiting for a result ",
0x0101: "An operation has timed out ",
0x0020: "Undefined or inaccessible property in function call ",
0x0200: "The pose specified in the Move command is invalid/unreachable ",
0x0201: "Device has not been referenced ",
0x0203: "An operation could not be started because the device is busy ",
0x0300: "Positioners were blocked during movement ",
0x0301: "The following error limit has been exceeded during movement ",
0x0320: "Positioner referencing failed ",
0x0500: "Could not load required hardware driver",
0x0501: "Could not find/connect to controller",
0x0502: "The device is not connected",
0x0503: "The controller doesn't provide the require features or configuration",
0x0504: "Error when communicating with controller",
        }

    def __init__(self):
        if os.name == "nt":
            raise NotImplemented("Windows not yet supported")
            # WinDLL.__init__(self, "libSA_MC.dll")  # TODO check it works
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libsmaractmc.so", RTLD_GLOBAL)

    def __getitem__(self, name):
        try:
            func = super(MC_5DOF_DLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        func.errcheck = self.sp_errcheck
        return func

    @staticmethod
    def sp_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result != MC_5DOF_DLL.SA_MC_OK:
            raise SA_MCError(result, "Call to %s() failed with error 0x%x: %s" %
                             (func.__name__, result, MC_5DOF_DLL.err_code.get(result, "")))

        return result


class SA_MCError(IOError):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(SA_MCError, self).__init__(errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror


class MC_5DOF(model.Actuator):

    def __init__(self, name, role, locator, axes, ref_on_init=False, linear_speed=0.01,
                 rotary_speed=0.0174533, hold_time=float("inf"), settle_time=0,
                 pos_deactive_after_ref=False, **kwargs):
        """
        A driver for a SmarAct SA_MC Actuator, custom build for Delmic.
        Has 5 degrees of freedom
        This driver uses a DLL provided by SmarAct which connects via
        USB or TCP/IP using a locator string.

        name: (str)
        role: (str)
        locator: (str) Use "fake" for a simulator.
            For a real device, MCS controllers with USB interface can be addressed with the
            following locator syntax:
                usb:id:<id>
            where <id> is the first part of a USB devices serial number which
            is printed on the MCS controller.
            If the controller has a TCP/IP connection, use:
                network:<ip>:<port>
        ref_on_init: (bool) determines if the controller should automatically reference
            on initialization
        hold_time (float>=0): the hold time, in seconds, for the actuator after the target position is reached.
            Default is infinite (float('inf') in Python, .inf in YAML). Can be also set to 0 to disable hold.
            Is set to the same value for all channels.
        settle_time (float>=0): extra waiting time after a move, to ensure that
          vibrations are entirely stopped on the sample.
        linear_speed: (float) the default speed (in m/s) of the linear actuators
        rotary_speed: (float) the default speed (in rad/s) of the rotary actuators
        axes: dict str (axis name) -> dict (axis parameters)
            The following axes must all be present:
            x, y, z, rx, rz
            Note: internally in the driver, ry exists, but has a range of (0,0),
            so it is not included here.

            axis parameters: {
                range: [float, float], default is -1 -> 1
                unit: (str) default is "m" for x, y, z and "rad" for the r*
            }
        pos_deactive_after_ref (bool): if True, will move to the deactive position
            defined in metadata after referencing
        """
        if locator != "fake":
            self.core = MC_5DOF_DLL()
        else:
            self.core = FakeMC_5DOF_DLL(axes)
        # Not to be mistaken with axes which is a simple public view
        self._axis_map = {}  # axis name -> axis number used by controller
        axes_def = {}  # axis name -> Axis object
        self._locator = locator

        # Require the user to define all 5 axes: x, y, z, rx, rz
        if set(axes.keys()) != {'x', 'y', 'z', 'rx', 'rz'}:
            raise ValueError("Invalid axes definition. Axes should contain x, y, z, rx, rz")

        for axis_name, axis_par in axes.items():
            try:
                axis_range = axis_par['range']
            except KeyError:
                logging.info("Axis %s has no range. Assuming (-1, 1)", axis_name)
                axis_range = (-1, 1)

            try:
                axis_unit = axis_par['unit']
            except KeyError:
                # m if linear, "rad" otherwise
                axis_unit = "m" if axis_name in {'x', 'y', 'z'} else "rad"
                logging.info("Axis %s has no unit. Assuming %s", axis_name, axis_unit)

            ad = model.Axis(canAbs=True, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad

        # Connect to the device
        self._id = c_uint32(MC_5DOF_DLL.SA_MC_INVALID_HANDLE)
        option_string = "model %d\n locator %s" % (MC_5DOF_DLL.hwModel, locator)
        options = c_char_p(option_string.encode("ascii"))
        try:
            self.core.SA_MC_Open(byref(self._id), options)
        except SA_MCError as ex:
            if ex.errno == MC_5DOF_DLL.SA_MC_ERROR_CONNECT_FAILED:
                raise model.HwError("Failed to find device, check it is connected and turned on")
            raise
        logging.debug("Successfully connected to SA_MC Controller ID %d", self._id.value)
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)
        # Add metadata
        self._hwVersion = "%s (model code: %d)" % (self.GetProperty_s(MC_5DOF_DLL.SA_MC_PKEY_MODEL_NAME),
                                                   self.GetProperty_i32(MC_5DOF_DLL.SA_MC_PKEY_MODEL_CODE))
        self._swVersion = self.GetProperty_s(MC_5DOF_DLL.SA_MC_PKEY_VERSION_STRING, handle=MC_5DOF_DLL.SA_MC_GLOBAL_HANDLE)
        logging.debug("Using MC_5DOF library version %s to connect to %s.", self._swVersion, self._hwVersion)

        self.position = model.VigilantAttribute({}, readonly=True)
        self._metadata[model.MD_PIVOT_POS] = self.GetPivot()

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(1)  # one task at a time

        # Reference tilted positioners towards the negative position
        # Normally this is not needed. Some old version of the controller needed
        # some hint when the stage was at "bad" positions. Left here just for
        # reference.
        # self.SetProperty_i32(MC_5DOF_DLL.SA_MC_PKEY_REF_DIR_TILT, 1)
        # self.SetProperty_i32(MC_5DOF_DLL.SA_MC_PKEY_REF_DIR_Y, 1)

        # Position to report when not referenced. Ideally, we could just not
        # report the axis at all in the .position. However, there is too much
        # code that expects a value all the time for now.
        # TODO: remove this once the rest of Odemis handles non-reported positions.
        # Use 0, if it's within the range, otherwise use the center of the range.
        self._unknown_pos = {an: 0 if ad.range[0] <= 0 <= ad.range[1] else self._applyInversion(sum(ad.range) / 2)
                             for an, ad in axes_def.items()}

        # Indicates moving to a deactive position after referencing.
        self._pos_deactive_after_ref = pos_deactive_after_ref

        if settle_time < 0:
            raise ValueError("settle_time should be >= 0, but got %s" % (settle_time,))
        self._settle_time = settle_time

        referenced = self._is_referenced()
        # define the referenced VA from the query
        axes_ref = {a: referenced for a, i in self.axes.items()}
        # VA dict str(axis) -> bool
        self.referenced = model.VigilantAttribute(axes_ref, readonly=True)

        # If ref_on_init, reference immediately.
        if referenced:
            logging.debug("SA_MC is referenced")
        elif ref_on_init:
            self.reference(self.axes.keys())  # will reference now in background.

        # Use a default actuator speed
        self.linear_speed = linear_speed
        self.set_linear_speed(self.linear_speed)
        self.rotary_speed = rotary_speed
        self.set_rotary_speed(math.degrees(self.rotary_speed))

        self.speed = VigilantAttribute({'x': self.linear_speed,
                                   'y': self.linear_speed,
                                   'z': self.linear_speed,
                                   'rx': self.rotary_speed,
                                   'rz': self.rotary_speed},
                                   readonly=True)

        # create a timer thread that will be used to update the position while waiting for events
        self.update_position_timer = RepeatingTimer(1.0, self._updatePosition)
        self.update_position_timer.start()
        self.set_hold_time(hold_time)
        self._updatePosition()

    def terminate(self):
        # should be safe to close the device multiple times if terminate is called more than once.
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None
            self.core.SA_MC_Close(self._id)
        super(MC_5DOF, self).terminate()

    def updateMetadata(self, md):
        if model.MD_PIVOT_POS in md:
            pivot = md[model.MD_PIVOT_POS]
            if not (isinstance(pivot, dict) and set(pivot.keys()) == {"x", "y", "z"}):
                raise ValueError("Invalid metadata, should be a coordinate dictionary but got %s." % (pivot,))

            # TODO: warn if rx or rz != 0, as this means the current position is not correct anymore
            #   or update the current position, based on the new pivot point.
            logging.debug("Updating pivot point to %s.", pivot)
            self.SetPivot(pivot)
        if model.MD_FAV_POS_DEACTIVE in md:
            deactive_pos = md[model.MD_FAV_POS_DEACTIVE]
            if not isinstance(deactive_pos, dict) or not set(deactive_pos.keys()) <= set(self.axes.keys()):
                raise ValueError("Invalid metadata, should be a coordinate dictionary but got %s." % (deactive_pos,))

        super().updateMetadata(md)

    # API Calls
    # Functions to set the property values in the controller, categorized by data type

    def GetProperty_s(self, property_key, bufferSize=256, handle=None):
        """
        Parameters:
         - property_key: The property key.
         - bufferSize = 256: In: the size of the buffer.  Out: the written
        number of characters +1 (for the string termination 0-byte)  if
        successful or the required buffer size, if not.
        - handle: int or None: If None, it will use ._id

        Return value(s):
         - outBuffer: A string
        """
        if handle is None:
            handle = self._id
        else:
            handle = c_uint32(handle)
        buf = create_string_buffer(bufferSize)
        slen = c_size_t(len(buf))
        self.core.SA_MC_GetProperty_s(handle, c_uint32(property_key), buf, byref(slen))
        return buf.value.decode("latin1")

    def SetProperty_f64(self, property_key, value):
        self.core.SA_MC_SetProperty_f64(self._id, c_uint32(property_key), c_double(value))

    def SetProperty_i32(self, property_key, value):
        self.core.SA_MC_SetProperty_i32(self._id, c_uint32(property_key), c_int32(value))

    def GetProperty_f64(self, property_key):
        ret_val = c_double()
        self.core.SA_MC_GetProperty_f64(self._id, c_uint32(property_key), byref(ret_val))
        return ret_val.value

    def GetProperty_i32(self, property_key):
        ret_val = c_int32()
        self.core.SA_MC_GetProperty_i32(self._id, c_uint32(property_key), byref(ret_val))
        return ret_val.value

    def WaitForEvent(self, timeout=float("inf")):
        """
        Blocks until event is triggered or timeout.
        timeout (float): maximum time to wait in s. If inf, it will wait forever.
        returns (SA_MC_Event): the event code that was triggered
        """
        if timeout == float("inf"):
            t = MC_5DOF_DLL.SA_MC_INFINITE
        else:
            t = c_uint(int(timeout * 1000))
        ev = SA_MC_Event()
        self.core.SA_MC_WaitForEvent(self._id, byref(ev), t)
        return ev

    def Reference(self):
        # Reference the controller. Note - this is asynchronous
        self.core.SA_MC_Reference(self._id)

    def _is_referenced(self):
        """
        Ask the controller if it is referenced
        """
        return bool(self.GetProperty_i32(MC_5DOF_DLL.SA_MC_PKEY_IS_REFERENCED))

    def Move(self, pos):
        """
        Move to pose command.
        pos: (dict str -> float) axis name -> position
            This is converted to the pose C-struct which is sent to the SA_MC DLL

        Raises: SA_MCError if a problem occurs
        """
        # convert into a pose, using the current position for non-moving axes
        newPose = self.GetPose()
        newPose.update(pos)
        self.core.SA_MC_Move(self._id, byref(newPose))

    def GetPose(self):
        """
        Get the current pose of the SA_MC

        returns: (dict str -> float): axis name -> position
        """
        pose = SA_MC_Pose()
        self.core.SA_MC_GetPose(self._id, byref(pose))
        return pose

    def Stop(self):
        """
        Stop command sent to the SA_MC
        """
        logging.debug("Stopping...")
        self.core.SA_MC_Stop(self._id)

    def get_linear_speed(self):
        """
        Returns (float) the linear speed of the SA_MC motion in m/s
        """
        return self.GetProperty_f64(MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_LINEAR_AXES)

    def set_linear_speed(self, value):
        """
        Set the linear speed of the SA_MC motion on all axes
        value: (float) indicating speed for all axes in m/s
        """
        logging.debug("Setting linear speed to %f", value)
        self.SetProperty_f64(MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_LINEAR_AXES, value)

    def get_rotary_speed(self):
        """
        Returns (float) the rotary speed of the SA_MC motion in deg/s
        """
        return self.GetProperty_f64(MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_ROTARY_AXES)

    def set_rotary_speed(self, value):
        """
        Set the rotary speed of the SA_MC motion for all axes
        value: (float) indicating speed for all axes in deg/s
        """
        logging.debug("Setting rotary speed to %f", value)
        self.SetProperty_f64(MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_ROTARY_AXES, value)

    def get_hold_time(self, value):
        """
        returns (float): time to hold the axis in s. float("inf") if holds forever.
        """
        ht = self.GetProperty_i32(MC_5DOF_DLL.SA_MC_PKEY_HOLD_TIME)

        if ht == MC_5DOF_DLL.SA_MC_INFINITE:
            return float("inf")
        else:
            return ht / 1000

    def set_hold_time(self, value):
        """
        Set the duration that the axis should actively hold in position after the
          end of a move.
        value: (float) time to hold the axis in s. Use inf to hold forever.
        """
        if value == float("inf"):
            ht = MC_5DOF_DLL.SA_MC_INFINITE
        else:
            ht = int(value * 1000)

        logging.debug("Setting hold time to %s", ht)
        self.SetProperty_i32(MC_5DOF_DLL.SA_MC_PKEY_HOLD_TIME, ht)

    def SetPivot(self, piv_dict):
        """
        Set the pivot point of the device

        piv_dict (dict str -> float): Position dictionary
            must have 'x', 'y', 'z'
        """
        pivot = SA_MC_Vec3()
        pivot.x = piv_dict["x"]
        pivot.y = piv_dict["y"]
        pivot.z = piv_dict["z"]
        self.core.SA_MC_SetPivot(self._id, byref(pivot))

    def GetPivot(self):
        """
        Get the pivot point from the controller

        returns: a dictionary (str -> float) of the axis and the pivot point
        """
        pivot = SA_MC_Vec3()
        self.core.SA_MC_GetPivot(self._id, byref(pivot))
        return {'x': pivot.x, 'y': pivot.y, 'z': pivot.z}

    def stop(self, axes=None):
        """
        Stop the SA_MC controller and update position
        """
        self.Stop()
        self._executor.cancel()
        self._updatePosition()

    def _updatePosition(self):
        """
        update the position VA
        """
        try:
            p = self.GetPose().asdict()
        except SA_MCError as ex:
            if ex.errno != MC_5DOF_DLL.SA_MC_ERROR_NOT_REFERENCED:
                raise

            logging.warning("Position unknown because SA_MC is not referenced")
            p = self._unknown_pos
        p = self._applyInversion(p)
        logging.debug("Updated position to %s", p)
        self.position._set_value(p, force_write=True)

    def _createMoveFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.RLock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f.task_canceller = self._cancelCurrentMove
        return f

    @isasync
    def reference(self, axes):
        """
        reference usually takes axes as an argument. However, the SA_MC references all
        axes together so this argument is extraneous.
        """
        self._checkReference(axes)

        f = self._createMoveFuture()
        self._executor.submitf(f, self._doReference, f)
        return f

    def _doReference(self, future):
        """
        Actually runs the referencing code
        future (Future): the future it handles
        raise:
            IOError: if referencing failed due to hardware
            CancelledError if was cancelled
        """
        try:
            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()

                # Reset reference so that if it fails, it states the axes are not
                # referenced (anymore)
                self.referenced._value = {a: False for a in self.axes.keys()}

                # The SA_MC references all axes at once.
                logging.debug("Starting referencing")
                self.Reference()

            # wait till reference completes
            while not future._must_stop.is_set():
                ev = self.WaitForEvent(100)  # large timeout
                # check if move is done
                if ev.type == MC_5DOF_DLL.SA_MC_EVENT_MOVEMENT_FINISHED:
                    logging.debug("Referencing finished")
                    if ev.i32 != MC_5DOF_DLL.SA_MC_OK:
                        raise SA_MCError(ev.i32, "Referencing failed with error 0x%x: %s" %
                                                 (ev.i32, MC_5DOF_DLL.err_code.get(ev.i32, "")))
                    break
                else:
                    logging.warning("Returned event type 0x%x", ev.type)
                    # keep waiting as the referencing continues

                logging.info("Referencing successful.")

            # if referenced, move to the safe position if requested
            if self._pos_deactive_after_ref and self._is_referenced():
                try:
                    deactive_pos = self._metadata[model.MD_FAV_POS_DEACTIVE]
                except KeyError:
                    logging.warning("Cannot move to deactive position. Missing MD_FAV_POS_DEACTIVE")
                else:
                    logging.info("Moving axes to deactivated position %s after referencing", deactive_pos)
                    self._checkMoveAbs(deactive_pos)
                    deactive_pos = self._applyInversion(deactive_pos)
                    self._doMoveAbs(future, deactive_pos)

        except SA_MCError as ex:
            # This occurs if a stop command interrupts referencing
            if ex.errno == MC_5DOF_DLL.SA_MC_ERROR_CANCELED:
                logging.info("Referencing stopped: %s", ex)
                raise CancelledError()
            elif future._must_stop.is_set():
                raise CancelledError()
            else:
                logging.error("Referencing failed: %s", ex)
                self.state._set_value(ex, force_write=True)
                raise
        except CancelledError:
            logging.debug("Movement canceled")
            raise  # No fuss, pass it as-is
        except Exception:
            logging.exception("Referencing failure")
            raise
        finally:
            # We only notify after updating the position so that when a listener
            # receives updates both values are already updated.
            if self._is_referenced():
                self.referenced._value = {a: True for a in self.axes.keys()}
                self._updatePosition()

            self.referenced.notify(self.referenced.value)

    @isasync
    def moveAbs(self, pos):
        """
        API call to absolute move
        """
        if not pos:
            return model.InstantaneousFuture()

        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def _estimateMoveDuration(self, new_pos):
        """
        Estimate the maximum duration of a move
        new_pos: (dict str -> float): axis name -> absolute target position
        returns: the duration of the move in seconds
        """
        pos = self._applyInversion(self.position.value)
        return max(
            abs(new_pos.get('x', 0) - pos['x']) / self.linear_speed,
            abs(new_pos.get('y', 0) - pos['y']) / self.linear_speed,
            abs(new_pos.get('z', 0) - pos['z']) / self.linear_speed,
            abs(new_pos.get('rx', 0) - pos['rx']) / self.rotary_speed,
            abs(new_pos.get('rz', 0) - pos['rz']) / self.rotary_speed,
            )

    def _doMoveAbs(self, future, pos, retrial=False):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        _pos (dict str -> float): axis name -> absolute target position
        retrial (bool): a boolean to retry the movement in case of timeout error
        raise:
            SA_MCError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        dur = self._estimateMoveDuration(pos)
        end = time.time() + dur
        max_dur = dur * 2 + 1
        logging.debug("Expecting a move of %f s, will wait up to %g s", dur, max_dur)

        try:
            # TODO: the period is only updated on the next repetition, so it might
            # take up to 1s before the first position update happens.
            self.update_position_timer.period = 0.05
            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()
                self.Move(pos)

            # Wait until the move is done
            while not future._must_stop.is_set():
                ev = self.WaitForEvent(max_dur)
                # check if move is done
                if ev.type == MC_5DOF_DLL.SA_MC_EVENT_MOVEMENT_FINISHED:
                    if ev.i32 != MC_5DOF_DLL.SA_MC_OK:
                        raise SA_MCError(ev.i32, "Move failed with error 0x%x: %s" %
                                                 (ev.i32, MC_5DOF_DLL.err_code.get(ev.i32, "")))
                    break
                else:
                    logging.warning("Unknown event type %s", ev.type)

                now = time.time()
                if now > end:
                    logging.warning("Stopping move due to timeout after %g s.", max_dur)
                    self.Stop()
                    raise TimeoutError("Move is not over after %g s, while "
                                       "expected it takes only %g s" %
                                       (max_dur, dur))

            else:
                raise CancelledError()

            # Extra settling time, typically to wait for the sample (connected to the hardware) to stop vibrating
            if self._settle_time:
                if self._executor.get_next_future(future) is not None:
                    # Another move queued means that the user wants to keep
                    # moving. So no need to wait extra time to ensure the
                    # sample is perfectly still, and immediately finish that
                    # move, which will then wait the settle time.
                    logging.debug("Not waiting for axis settling as another move is queued")
                else:
                    logging.debug("Waiting %g s for settling of the axis", self._settle_time)
                    # TODO: if there is a new move coming while waiting, stop early
                    # Use the Event, so that a cancellation can stop it
                    if future._must_stop.wait(self._settle_time):
                        raise CancelledError()
        except SA_MCError as ex:
            # This occurs if a stop command interrupts moves
            if ex.errno == MC_5DOF_DLL.SA_MC_ERROR_CANCELED:
                logging.debug("Movement stopped: %s", ex)
                raise CancelledError()
            elif future._must_stop.is_set():
                raise CancelledError()
            elif ex.errno == MC_5DOF_DLL.SA_MC_ERROR_TIMEOUT:
                # A timeout error can happen if the movement of some axes pulled others slightly out of their
                # position, and because they are not expected to move by the kinematics (e.g. the x-positioners in
                # case of a pure y movement), they go back to their position very slowly, causing the movement event
                # to time out. As a fix for this, the same movement will be tried again.
                if retrial:
                    logging.error("Move timed out after %g s: %s", max_dur, ex)
                    raise TimeoutError("Move timed out after %g s" % (max_dur,))
                else:
                    logging.warning("Movement to {} timed out while current position is {}. Retrying the same movement...".format(pos, self.position.value))
                    self._doMoveAbs(future, pos, retrial=True)
            elif ex.errno == MC_5DOF_DLL.SA_MC_ERROR_POSE_UNREACHABLE:
                raise IndexError(str(ex))
            else:
                logging.error("Move failed: %s", ex)
                raise
        except CancelledError:
            logging.debug("Movement canceled")
            raise  # No fuss, pass it as-is
        except Exception:
            logging.exception("Move failure")
            raise
        finally:
            self.update_position_timer.period = 1.0
            self._updatePosition()

        logging.debug("Move successfully completed")

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
            self.Stop()

        return True

    @isasync
    def moveRel(self, shift):
        """
        API call for relative move
        """
        if not shift:
            return model.InstantaneousFuture()

        self._checkMoveRel(shift)
        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _doMoveRel(self, future, shift):
        """
        Do a relative move by converting it into an absolute move
        """
        pos = self._applyInversion(add_coord(self.position.value, shift))
        self._doMoveAbs(future, pos)


class FakeMC_5DOF_DLL(object):
    """
    Fake TrGlide DLL for simulator
    """

    def __init__(self, axes=None):
        """
        axes: dict str (axis name) -> dict (axis parameters).
          Allows to change the default range for some of the axes.
          The parameters should include "range", which is in m for the linear axes,
          and in rad for the rotational axes.
        """
        self.pose = SA_MC_Pose()
        self.target = SA_MC_Pose()
        self.properties = {
            MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_LINEAR_AXES: c_double(0.1),
            MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_ROTARY_AXES: c_double(5),
            MC_5DOF_DLL.SA_MC_PKEY_IS_REFERENCED: c_int32(0),
            MC_5DOF_DLL.SA_MC_PKEY_MODEL_CODE: c_int32(1),
            MC_5DOF_DLL.SA_MC_PKEY_MODEL_NAME: b"Simulated",
            MC_5DOF_DLL.SA_MC_PKEY_VERSION_STRING: b"Simulated version",
            MC_5DOF_DLL.SA_MC_PKEY_HOLD_TIME: c_int32(1000),
            }
        self._pivot = SA_MC_Vec3()

        # Specify ranges
        self._range = {
            'x': (-1.6e-2, 1.6e-2),
            'y': (-1.5e-2, 1.5e-2),
            'z': (-1.0e-2, 0.003),
            'rx': (-28, 28),
            'ry': (0, 0),
            'rz': (-28, 28),
        }
        if axes:
            # adjust internal range with the requested one
            for a, ad in axes.items():
                orig_rng = self._range[a]
                user_rng = ad['range']
                # rotation values are in internally in degrees => convert from rad
                if a.startswith("r"):
                    user_rng = [math.degrees(v) for v in user_rng]
                self._range[a] = (max(orig_rng[0], user_rng[0]), min(orig_rng[1], user_rng[1]))

        self.stopping = threading.Event()

        self._referencing = False

        self._last_time = time.time()
        self._current_move_finish = time.time()

    def _pose_in_range(self, pose):
        return self._range['x'][0] <= pose.x <= self._range['x'][1] and \
            self._range['y'][0] <= pose.y <= self._range['y'][1] and \
            self._range['z'][0] <= pose.z <= self._range['z'][1] and \
            self._range['rx'][0] <= pose.rx <= self._range['rx'][1] and \
            self._range['ry'][0] <= pose.ry <= self._range['ry'][1] and \
            self._range['rz'][0] <= pose.rz <= self._range['rz'][1]

    def _calc_move_after_dt(self, a, speed, dt):
        """
        Calculates the new position for a given axis after a time dt at speed
        a (str): the axis name attribute ('x', 'y', 'z', 'rx', 'rz')
        speed (float): the speed of that axis
        dt (float): time differential
        """
        d = getattr(self.target, a) - getattr(self.pose, a)
        if d >= 0:
            new_pos = getattr(self.pose, a) + speed * dt
            if new_pos >= getattr(self.target, a):
                new_pos = getattr(self.target, a)
        elif d < 0:
            new_pos = getattr(self.pose, a) - speed * dt
            if new_pos < getattr(self.target, a):
                new_pos = getattr(self.target, a)

        return new_pos

    def _update_current_pos(self):
        """
        Update the self.pose if a move is active
        """
        cur_time = time.time()
        if cur_time > self._current_move_finish:
            return

        lin_speed = self.properties[MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_LINEAR_AXES].value
        rad_speed = self.properties[MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_ROTARY_AXES].value
        dt = cur_time - self._last_time

        # calculate intermediate positions
        self.pose.x = self._calc_move_after_dt('x', lin_speed, dt)
        self.pose.y = self._calc_move_after_dt('y', lin_speed, dt)
        self.pose.z = self._calc_move_after_dt('z', lin_speed, dt)
        self.pose.rx = self._calc_move_after_dt('rx', rad_speed, dt)
        self.pose.rz = self._calc_move_after_dt('rz', rad_speed, dt)
        logging.debug("Updating simulated position to %s", self.pose)

        self._last_time = cur_time

    """
    DLL functions (fake)
    These functions are provided by the real SA_MC DLL
    """

    def SA_MC_Open(self, id, options):
        logging.debug("sim MC5DOF: Starting")

    def SA_MC_Close(self, id):
        logging.debug("sim MC5DOF: Closing")

    def SA_MC_GetPivot(self, id, p_piv):
        val = _deref(p_piv, SA_MC_Vec3)
        val.value = self._pivot
        logging.debug("sim MC5DOF: Get pivot: (%f, %f, %f)" % (self._pivot.x, self._pivot.y, self._pivot.z))

    def SA_MC_SetPivot(self, id, p_piv):
        self._pivot = _deref(p_piv, SA_MC_Vec3)
        logging.debug("sim MC5DOF: Setting pivot to (%f, %f, %f)" % (self._pivot.x, self._pivot.y, self._pivot.z))

    ATOL_LINEAR_POS = 100e-6

    def _check_reachable_position(self, current_pos, target_pos):
        """
        Check that the target position is unreachable from the current stage position. This would happen if one the
        stage axes is near to the maximum range, and the target movement is not done on all linear axes.
        N.B.: This is to simulate the actual behaviour of the stage.
        :param current_pos: (dict) current position of the stage.
        :param target_pos: (target) target position to move the stage to.
        :raises: (SA_MCError) if the target move is unreachable
        """
        linear_axes = {'x', 'y', 'z'}
        rot_axes = {'rx', 'rz'}
        edge_axes = {a for a in linear_axes
                     if any(almost_equal(r, current_pos[a], self.ATOL_LINEAR_POS) for r in self._range[a])}

        # Simulate unreachable move when all linear axes near the range and the target move is rotational
        if edge_axes == linear_axes and any(target_pos.get(a, 0) != current_pos[a] for a in rot_axes):
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_POSE_UNREACHABLE, "Unreachable target position, please move "
                                                                           "all x,y,z axes at the same time.")

    def SA_MC_Move(self, id, p_pose):
        self.stopping.clear()
        pose = _deref(p_pose, SA_MC_Pose)
        if self._pose_in_range(pose):
            self._check_reachable_position(self.pose.asdict(), pose.asdict())
            self.target.x = pose.x
            self.target.y = pose.y
            self.target.z = pose.z
            self.target.rx = pose.rx
            self.target.ry = pose.ry
            self.target.rz = pose.rz

            lin_speed = self.properties[MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_LINEAR_AXES].value
            rad_speed = self.properties[MC_5DOF_DLL.SA_MC_PKEY_MAX_SPEED_ROTARY_AXES].value

            # estimate move duration
            dur = max(
                abs(self.target.x - self.pose.x) / lin_speed,
                abs(self.target.y - self.pose.y) / lin_speed,
                abs(self.target.z - self.pose.z) / lin_speed,
                abs(self.target.rx - self.pose.rx) / rad_speed,
                abs(self.target.rz - self.pose.rz) / rad_speed,
                )

            self._current_move_finish = time.time() + dur
            self._last_time = time.time()
            logging.debug("sim MC5DOF: moving to target: %s duration %f s" % (self.target, dur))
        else:
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_POSE_UNREACHABLE, f"Position not in range: {pose}.")

    def SA_MC_GetPose(self, id, p_pose):
        if not self.properties[MC_5DOF_DLL.SA_MC_PKEY_IS_REFERENCED].value:
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_NOT_REFERENCED, "Not referenced error")

        self._update_current_pos()

        pose = _deref(p_pose, SA_MC_Pose)
        pose.x = self.pose.x
        pose.y = self.pose.y
        pose.z = self.pose.z
        pose.rx = self.pose.rx
        pose.ry = self.pose.ry
        pose.rz = self.pose.rz

        logging.debug("sim MC5DOF: position: %s" % (pose,))
        return MC_5DOF_DLL.SA_MC_OK

    def SA_MC_Stop(self, id):
        logging.debug("sim MC5DOF: Stopping")
        self.stopping.set()
        self._update_current_pos()
        self._current_move_finish = time.time()

    def SA_MC_Reference(self, id):
        logging.debug("sim MC5DOF: Starting referencing...")
        self.properties[MC_5DOF_DLL.SA_MC_PKEY_IS_REFERENCED] = c_int32(0)
        self.stopping.clear()
        self._current_move_finish = time.time() + 5.0
        self._referencing = True

    def SA_MC_SetProperty_f64(self, id, prop, val):
        if not prop.value in self.properties:
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_INVALID_PROPERTY, "error")

        self.properties[prop.value] = val

    def SA_MC_SetProperty_i32(self, id, prop, val):
        if not prop.value in self.properties:
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_INVALID_PROPERTY, "error")

        self.properties[prop.value] = val

    def SA_MC_GetProperty_f64(self, id, prop, p_val):
        if not prop.value in self.properties:
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_INVALID_PROPERTY, "error")

        val = _deref(p_val, c_double)
        val.value = self.properties[prop.value].value

    def SA_MC_GetProperty_i32(self, id, prop, p_val):
        if not prop.value in self.properties:
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_INVALID_PROPERTY, "error")

        val = _deref(p_val, c_int32)
        val.value = self.properties[prop.value].value

    def SA_MC_GetProperty_s(self, id, property_key, val, ioArraySize):
        if not property_key.value in self.properties:
            raise SA_MCError(MC_5DOF_DLL.SA_MC_ERROR_INVALID_PROPERTY, "error")
        val.value = self.properties[property_key.value]

    def SA_MC_WaitForEvent(self, id, p_ev, timeout):
        ev = _deref(p_ev, SA_MC_Event)
        start_time = time.time()
        # flags to indicate possible cancellations or timeouts
        stopped = False
        timedout = False
        while time.time() < self._current_move_finish:
            if  time.time() > start_time + timeout.value:
                stopped = True
                timedout = True
                break
            if self.stopping.wait(0.05):
                break

        # Check if it was cancelled (cancelling sets the current move to now,
        # so it might come out of the loop due to the while)
        stopped = stopped or self.stopping.is_set()

        ev.type = MC_5DOF_DLL.SA_MC_EVENT_MOVEMENT_FINISHED
        if not stopped:
            ev.i32 = MC_5DOF_DLL.SA_MC_OK
            self.pose = copy.copy(self.target)
        elif timedout:
            ev.i32 = MC_5DOF_DLL.SA_MC_ERROR_TIMEOUT
        else:
            ev.i32 = MC_5DOF_DLL.SA_MC_ERROR_CANCELED

        # if a reference move was in process...
        if self._referencing and not stopped and not timedout:
            self.properties[MC_5DOF_DLL.SA_MC_PKEY_IS_REFERENCED] = c_int32(1)
            self._referencing = False  # finished referencing
            logging.debug("sim MC5DOF: Referencing complete")

        logging.debug("sim MC5DOF: issued event %s", ev)

# Classes associated with the SmarAct MCS2 Controller (standard)


class SA_CTL_TransmitHandle_t(c_uint32):
    pass


class SA_CTLDLL(CDLL):
    """
    Subclass of CDLL specific to SA_CTL library, which handles error codes for
    all the functions automatically.
    """

    # SmarAct MCS2 error codes
    SA_CTL_ERROR_NONE = 0x0000
    SA_CTL_ERROR_UNKNOWN_COMMAND = 0x0001
    SA_CTL_ERROR_INVALID_PACKET_SIZE = 0x0002
    SA_CTL_ERROR_TIMEOUT = 0x0004
    SA_CTL_ERROR_INVALID_PROTOCOL = 0x0005
    SA_CTL_ERROR_BUFFER_UNDERFLOW = 0x000c
    SA_CTL_ERROR_BUFFER_OVERFLOW = 0x000d
    SA_CTL_ERROR_INVALID_FRAME_SIZE = 0x000e
    SA_CTL_ERROR_INVALID_PACKET = 0x0010
    SA_CTL_ERROR_INVALID_KEY = 0x0012
    SA_CTL_ERROR_INVALID_PARAMETER = 0x0013
    SA_CTL_ERROR_INVALID_DATA_TYPE = 0x0016
    SA_CTL_ERROR_INVALID_DATA = 0x0017
    SA_CTL_ERROR_HANDLE_LIMIT_REACHED = 0x0018
    SA_CTL_ERROR_ABORTED = 0x0019

    SA_CTL_ERROR_INVALID_DEVICE_INDEX = 0x0020
    SA_CTL_ERROR_INVALID_MODULE_INDEX = 0x0021
    SA_CTL_ERROR_INVALID_CHANNEL_INDEX = 0x0022

    SA_CTL_ERROR_PERMISSION_DENIED = 0x0023
    SA_CTL_ERROR_COMMAND_NOT_GROUPABLE = 0x0024
    SA_CTL_ERROR_MOVEMENT_LOCKED = 0x0025
    SA_CTL_ERROR_SYNC_FAILED = 0x0026
    SA_CTL_ERROR_INVALID_ARRAY_SIZE = 0x0027
    SA_CTL_ERROR_OVERRANGE = 0x0028
    SA_CTL_ERROR_INVALID_CONFIGURATION = 0x0029

    SA_CTL_ERROR_NO_HM_PRESENT = 0x0100
    SA_CTL_ERROR_NO_IOM_PRESENT = 0x0101
    SA_CTL_ERROR_NO_SM_PRESENT = 0x0102
    SA_CTL_ERROR_NO_SENSOR_PRESENT = 0x0103
    SA_CTL_ERROR_SENSOR_DISABLED = 0x0104
    SA_CTL_ERROR_POWER_SUPPLY_DISABLED = 0x0105
    SA_CTL_ERROR_AMPLIFIER_DISABLED = 0x0106
    SA_CTL_ERROR_INVALID_SENSOR_MODE = 0x0107
    SA_CTL_ERROR_INVALID_ACTUATOR_MODE = 0x0108
    SA_CTL_ERROR_INVALID_INPUT_TRIG_MODE = 0x0109
    SA_CTL_ERROR_INVALID_CONTROL_OPTIONS = 0x010a
    SA_CTL_ERROR_INVALID_REFERENCE_TYPE = 0x010b
    SA_CTL_ERROR_INVALID_ADJUSTMENT_STATE = 0x010c
    SA_CTL_ERROR_INVALID_INFO_TYPE = 0x010d
    SA_CTL_ERROR_NO_FULL_ACCESS = 0x010e
    SA_CTL_ERROR_ADJUSTMENT_FAILED = 0x010f
    SA_CTL_ERROR_MOVEMENT_OVERRIDDEN = 0x0110
    SA_CTL_ERROR_NOT_CALIBRATED = 0x0111
    SA_CTL_ERROR_NOT_REFERENCED = 0x0112
    SA_CTL_ERROR_NOT_ADJUSTED = 0x0113
    SA_CTL_ERROR_SENSOR_TYPE_NOT_SUPPORTED = 0x0114
    SA_CTL_ERROR_CONTROL_LOOP_INPUT_DISABLED = 0x0115
    SA_CTL_ERROR_INVALID_CONTROL_LOOP_INPUT = 0x0116
    SA_CTL_ERROR_UNEXPECTED_SENSOR_DATA = 0x0117
    SA_CTL_ERROR_NOT_PHASED = 0x0118
    SA_CTL_ERROR_POSITIONER_FAULT = 0x0119
    SA_CTL_ERROR_DRIVER_FAULT = 0x011a
    SA_CTL_ERROR_POSITIONER_TYPE_NOT_SUPPORTED = 0x011b
    SA_CTL_ERROR_POSITIONER_TYPE_NOT_IDENTIFIED = 0x011c
    SA_CTL_ERROR_POSITIONER_TYPE_NOT_WRITEABLE = 0x011e
    SA_CTL_ERROR_INVALID_ACTUATOR_TYPE = 0x0121

    SA_CTL_ERROR_BUSY_MOVING = 0x0150
    SA_CTL_ERROR_BUSY_CALIBRATING = 0x0151
    SA_CTL_ERROR_BUSY_REFERENCING = 0x0152
    SA_CTL_ERROR_BUSY_ADJUSTING = 0x0153

    SA_CTL_ERROR_END_STOP_REACHED = 0x0200
    SA_CTL_ERROR_FOLLOWING_ERR_LIMIT = 0x0201
    SA_CTL_ERROR_RANGE_LIMIT_REACHED = 0x0202
    SA_CTL_ERROR_POSITIONER_OVERLOAD = 0x0203
    SA_CTL_ERROR_POWER_SUPPLY_FAILURE = 0x0205
    SA_CTL_ERROR_OVER_TEMPERATURE = 0x0206
    SA_CTL_ERROR_POWER_SUPPLY_OVERLOAD = 0x0208

    SA_CTL_ERROR_INVALID_STREAM_HANDLE = 0x0300
    SA_CTL_ERROR_INVALID_STREAM_CONFIGURATION = 0x0301
    SA_CTL_ERROR_INSUFFICIENT_FRAMES = 0x0302
    SA_CTL_ERROR_BUSY_STREAMING = 0x0303

    SA_CTL_ERROR_HM_INVALID_SLOT_INDEX = 0x0400
    SA_CTL_ERROR_HM_INVALID_CHANNEL_INDEX = 0x0401
    SA_CTL_ERROR_HM_INVALID_GROUP_INDEX = 0x0402
    SA_CTL_ERROR_HM_INVALID_CH_GRP_INDEX = 0x0403

    SA_CTL_ERROR_INTERNAL_COMMUNICATION = 0x0500

    SA_CTL_ERROR_FEATURE_NOT_SUPPORTED = 0x7ffd
    SA_CTL_ERROR_FEATURE_NOT_IMPLEMENTED = 0x7ffe

    SA_CTL_ERROR_DEVICE_LIMIT_REACHED = 0xf000
    SA_CTL_ERROR_INVALID_LOCATOR = 0xf001
    SA_CTL_ERROR_INITIALIZATION_FAILED = 0xf002
    SA_CTL_ERROR_NOT_INITIALIZED = 0xf003
    SA_CTL_ERROR_COMMUNICATION_FAILED = 0xf004
    SA_CTL_ERROR_INVALID_QUERYBUFFER_SIZE = 0xf006
    SA_CTL_ERROR_INVALID_DEVICE_HANDLE = 0xf007
    SA_CTL_ERROR_INVALID_TRANSMIT_HANDLE = 0xf008
    SA_CTL_ERROR_UNEXPECTED_PACKET_RECEIVED = 0xf00f
    SA_CTL_ERROR_CANCELED = 0xf010
    SA_CTL_ERROR_DRIVER_FAILED = 0xf013
    SA_CTL_ERROR_BUFFER_LIMIT_REACHED = 0xf016
    SA_CTL_ERROR_INVALID_PROTOCOL_VERSION = 0xf017
    SA_CTL_ERROR_DEVICE_RESET_FAILED = 0xf018
    SA_CTL_ERROR_BUFFER_EMPTY = 0xf019
    SA_CTL_ERROR_DEVICE_NOT_FOUND = 0xf01a
    SA_CTL_ERROR_THREAD_LIMIT_REACHED = 0xf01b
    SA_CTL_ERROR_NO_APPLICATION = 0xf01c

    err_code = {
        0x0000: "NONE",
        0x0001: "UNKNOWN_COMMAND",
        0x0002: "INVALID_PACKET_SIZE",
        0x0004: "TIMEOUT",
        0x0005: "INVALID_PROTOCOL",
        0x000c: "BUFFER_UNDERFLOW",
        0x000d: "BUFFER_OVERFLOW",
        0x000e: "INVALID_FRAME_SIZE",
        0x0010: "INVALID_PACKET",
        0x0012: "INVALID_KEY",
        0x0013: "INVALID_PARAMETER",
        0x0016: "INVALID_DATA_TYPE",
        0x0017: "INVALID_DATA",
        0x0018: "HANDLE_LIMIT_REACHED",
        0x0019: "ABORTED",
        0x0020: "INVALID_DEVICE_INDEX",
        0x0021: "INVALID_MODULE_INDEX",
        0x0022: "INVALID_CHANNEL_INDEX",
        0x0023: "PERMISSION_DENIED",
        0x0024: "COMMAND_NOT_GROUPABLE",
        0x0025: "MOVEMENT_LOCKED",
        0x0026: "SYNC_FAILED",
        0x0027: "INVALID_ARRAY_SIZE",
        0x0028: "OVERRANGE",
        0x0029: "INVALID_CONFIGURATION",
        0x0100: "NO_HM_PRESENT",
        0x0101: "NO_IOM_PRESENT",
        0x0102: "NO_SM_PRESENT",
        0x0103: "NO_SENSOR_PRESENT",
        0x0104: "SENSOR_DISABLED",
        0x0105: "POWER_SUPPLY_DISABLED",
        0x0106: "AMPLIFIER_DISABLED",
        0x0107: "INVALID_SENSOR_MODE",
        0x0108: "INVALID_ACTUATOR_MODE",
        0x0109: "INVALID_INPUT_TRIG_MODE",
        0x010a: "INVALID_CONTROL_OPTIONS",
        0x010b: "INVALID_REFERENCE_TYPE",
        0x010c: "INVALID_ADJUSTMENT_STATE",
        0x010d: "INVALID_INFO_TYPE",
        0x010e: "NO_FULL_ACCESS",
        0x010f: "ADJUSTMENT_FAILED",
        0x0110: "MOVEMENT_OVERRIDDEN",
        0x0111: "NOT_CALIBRATED",
        0x0112: "NOT_REFERENCED",
        0x0113: "NOT_ADJUSTED",
        0x0114: "SENSOR_TYPE_NOT_SUPPORTED",
        0x0115: "CONTROL_LOOP_INPUT_DISABLED",
        0x0116: "INVALID_CONTROL_LOOP_INPUT",
        0x0117: "UNEXPECTED_SENSOR_DATA",
        0x0118: "NOT_PHASED",
        0x0119: "POSITIONER_FAULT",
        0x011a: "DRIVER_FAULT",
        0x011b: "POSITIONER_TYPE_NOT_SUPPORTED",
        0x011c: "POSITIONER_TYPE_NOT_IDENTIFIED",
        0x011e: "POSITIONER_TYPE_NOT_WRITEABLE",
        0x0121: "INVALID_ACTUATOR_TYPE",
        0x0150: "BUSY_MOVING",
        0x0151: "BUSY_CALIBRATING",
        0x0152: "BUSY_REFERENCING",
        0x0153: "BUSY_ADJUSTING",
        0x0200: "END_STOP_REACHED",
        0x0201: "FOLLOWING_ERR_LIMIT",
        0x0202: "RANGE_LIMIT_REACHED",
        0x0203: "POSITIONER_OVERLOAD",
        0x0205: "POWER_SUPPLY_FAILURE",
        0x0206: "OVER_TEMPERATURE",
        0x0208: "POWER_SUPPLY_OVERLOAD",
        0x0300: "INVALID_STREAM_HANDLE",
        0x0301: "INVALID_STREAM_CONFIGURATION",
        0x0302: "INSUFFICIENT_FRAMES",
        0x0303: "BUSY_STREAMING",
        0x0400: "HM_INVALID_SLOT_INDEX",
        0x0401: "HM_INVALID_CHANNEL_INDEX",
        0x0402: "HM_INVALID_GROUP_INDEX",
        0x0403: "HM_INVALID_CH_GRP_INDEX",
        0x0500: "INTERNAL_COMMUNICATION",
        0x7ffd: "FEATURE_NOT_SUPPORTED",
        0x7ffe: "FEATURE_NOT_IMPLEMENTED",
        0xf000: "DEVICE_LIMIT_REACHED",
        0xf001: "INVALID LOCATOR STRING",
        0xf002: "INITIALIZATION_FAILED",
        0xf003: "NOT INITIALIZED",
        0xf004: "COMMUNICATION FAILED",
        0xf006: "INVALID_QUERYBUFFER_SIZE",
        0xf007: "INVALID DEVICE HANDLE",
        0xf008: "INVALID TRANSMIT HANDLE",
        0xf00f: "UNEXPECTED_PACKET_RECEIVED",
        0xf010: "CANCELLED",
        0xf013: "DRIVER FAILURE",
        0xf016: "BUFFER_LIMIT_REACHED",
        0xf017: "INVALID_PROTOCOL_VERSION",
        0xf018: "DEVICE_RESET_FAILED",
        0xf019: "BUFFER_EMPTY",
        0xf01a: "DEVICE_NOT_FOUND",
        0xf01b: "THREAD_LIMIT_REACHED",
        0xf01c: "NO_APPLICATION",
    }

    SA_CTL_STRING_MAX_LENGTH = 63

    # device states
    SA_CTL_DEV_STATE_BIT_HM_PRESENT = 0x00000001
    SA_CTL_DEV_STATE_BIT_MOVEMENT_LOCKED = 0x00000002
    SA_CTL_DEV_STATE_BIT_INTERNAL_COMM_FAILURE = 0x00000100
    SA_CTL_DEV_STATE_BIT_IS_STREAMING = 0x00001000

    # module states
    SA_CTL_MOD_STATE_BIT_SM_PRESENT = 0x00000001
    SA_CTL_MOD_STATE_BIT_BOOSTER_PRESENT = 0x00000002
    SA_CTL_MOD_STATE_BIT_ADJUSTMENT_ACTIVE = 0x00000004
    SA_CTL_MOD_STATE_BIT_IOM_PRESENT = 0x00000008
    SA_CTL_MOD_STATE_BIT_INTERNAL_COMM_FAILURE = 0x00000100
    SA_CTL_MOD_STATE_BIT_FAN_FAILURE = 0x00000800
    SA_CTL_MOD_STATE_BIT_POWER_SUPPLY_FAILURE = 0x00001000
    SA_CTL_MOD_STATE_BIT_HIGH_VOLTAGE_FAILURE = 0x00001000  # deprecated
    SA_CTL_MOD_STATE_BIT_POWER_SUPPLY_OVERLOAD = 0x00002000
    SA_CTL_MOD_STATE_BIT_HIGH_VOLTAGE_OVERLOAD = 0x00002000  # deprecated
    SA_CTL_MOD_STATE_BIT_OVER_TEMPERATURE = 0x00004000

    # channel states
    SA_CTL_CH_STATE_BIT_ACTIVELY_MOVING = 0x00000001
    SA_CTL_CH_STATE_BIT_CLOSED_LOOP_ACTIVE = 0x00000002
    SA_CTL_CH_STATE_BIT_CALIBRATING = 0x00000004
    SA_CTL_CH_STATE_BIT_REFERENCING = 0x00000008
    SA_CTL_CH_STATE_BIT_MOVE_DELAYED = 0x00000010
    SA_CTL_CH_STATE_BIT_SENSOR_PRESENT = 0x00000020
    SA_CTL_CH_STATE_BIT_IS_CALIBRATED = 0x00000040
    SA_CTL_CH_STATE_BIT_IS_REFERENCED = 0x00000080
    SA_CTL_CH_STATE_BIT_END_STOP_REACHED = 0x00000100
    SA_CTL_CH_STATE_BIT_RANGE_LIMIT_REACHED = 0x00000200
    SA_CTL_CH_STATE_BIT_FOLLOWING_LIMIT_REACHED = 0x00000400
    SA_CTL_CH_STATE_BIT_MOVEMENT_FAILED = 0x00000800
    SA_CTL_CH_STATE_BIT_IS_STREAMING = 0x00001000
    SA_CTL_CH_STATE_BIT_POSITIONER_OVERLOAD = 0x00002000
    SA_CTL_CH_STATE_BIT_OVER_TEMPERATURE = 0x00004000
    SA_CTL_CH_STATE_BIT_REFERENCE_MARK = 0x00008000
    SA_CTL_CH_STATE_BIT_IS_PHASED = 0x00010000
    SA_CTL_CH_STATE_BIT_POSITIONER_FAULT = 0x00020000
    SA_CTL_CH_STATE_BIT_AMPLIFIER_ENABLED = 0x00040000

    # hand control module states
    SA_CTL_HM_STATE_BIT_INTERNAL_COMM_FAILURE = 0x0100
    SA_CTL_HM_STATE_BIT_IS_INTERNAL = 0x0200

    # property keys
    SA_CTL_PKEY_NUMBER_OF_CHANNELS = 0x020F0017
    SA_CTL_PKEY_NUMBER_OF_BUS_MODULES = 0x020F0016
    SA_CTL_PKEY_INTERFACE_TYPE = 0x020F0066
    SA_CTL_PKEY_DEVICE_STATE = 0x020F000F
    SA_CTL_PKEY_DEVICE_SERIAL_NUMBER = 0x020F005E
    SA_CTL_PKEY_DEVICE_NAME = 0x020F003D
    SA_CTL_PKEY_EMERGENCY_STOP_MODE = 0x020F0088
    SA_CTL_PKEY_NETWORK_DISCOVER_MODE = 0x020F0159
    SA_CTL_PKEY_NETWORK_DHCP_TIMEOUT = 0x020F015C
    # module
    SA_CTL_PKEY_POWER_SUPPLY_ENABLED = 0x02030010
    SA_CTL_PKEY_NUMBER_OF_BUS_MODULE_CHANNELS = 0x02030017
    SA_CTL_PKEY_MODULE_TYPE = 0x02030066
    SA_CTL_PKEY_MODULE_STATE = 0x0203000F
    # positioner
    SA_CTL_PKEY_STARTUP_OPTIONS = 0x0A02005D
    SA_CTL_PKEY_AMPLIFIER_ENABLED = 0x0302000D
    SA_CTL_PKEY_AMPLIFIER_MODE = 0x030200BF
    SA_CTL_PKEY_POSITIONER_CONTROL_OPTIONS = 0x0302005D
    SA_CTL_PKEY_ACTUATOR_MODE = 0x03020019
    SA_CTL_PKEY_CONTROL_LOOP_INPUT = 0x03020018
    SA_CTL_PKEY_SENSOR_INPUT_SELECT = 0x0302009D
    SA_CTL_PKEY_POSITIONER_TYPE = 0x0302003C
    SA_CTL_PKEY_POSITIONER_TYPE_NAME = 0x0302003D
    SA_CTL_PKEY_MOVE_MODE = 0x03050087
    SA_CTL_PKEY_CHANNEL_TYPE = 0x02020066
    SA_CTL_PKEY_CHANNEL_STATE = 0x0305000F
    SA_CTL_PKEY_POSITION = 0x0305001D
    SA_CTL_PKEY_TARGET_POSITION = 0x0305001E
    SA_CTL_PKEY_SCAN_POSITION = 0x0305001F
    SA_CTL_PKEY_SCAN_VELOCITY = 0x0305002A
    SA_CTL_PKEY_HOLD_TIME = 0x03050028
    SA_CTL_PKEY_MOVE_VELOCITY = 0x03050029
    SA_CTL_PKEY_MOVE_ACCELERATION = 0x0305002B
    SA_CTL_PKEY_MAX_CL_FREQUENCY = 0x0305002F
    SA_CTL_PKEY_DEFAULT_MAX_CL_FREQUENCY = 0x03050057
    SA_CTL_PKEY_STEP_FREQUENCY = 0x0305002E
    SA_CTL_PKEY_STEP_AMPLITUDE = 0x03050030
    SA_CTL_PKEY_FOLLOWING_ERROR_LIMIT = 0x03050055
    SA_CTL_PKEY_FOLLOWING_ERROR = 0x03020055
    SA_CTL_PKEY_BROADCAST_STOP_OPTIONS = 0x0305005D
    SA_CTL_PKEY_SENSOR_POWER_MODE = 0x03080019
    SA_CTL_PKEY_SENSOR_POWER_SAVE_DELAY = 0x03080054
    SA_CTL_PKEY_POSITION_MEAN_SHIFT = 0x03090022
    SA_CTL_PKEY_SAFE_DIRECTION = 0x03090027
    SA_CTL_PKEY_CL_INPUT_SENSOR_VALUE = 0x0302001D
    SA_CTL_PKEY_CL_INPUT_AUX_VALUE = 0x030200B2
    SA_CTL_PKEY_TARGET_TO_ZERO_VOLTAGE_HOLD_TH = 0x030200B9
    # scale
    SA_CTL_PKEY_LOGICAL_SCALE_OFFSET = 0x02040024
    SA_CTL_PKEY_LOGICAL_SCALE_INVERSION = 0x02040025
    SA_CTL_PKEY_RANGE_LIMIT_MIN = 0x02040020
    SA_CTL_PKEY_RANGE_LIMIT_MAX = 0x02040021
    SA_CTL_PKEY_DEFAULT_RANGE_LIMIT_MIN = 0x020400C0
    SA_CTL_PKEY_DEFAULT_RANGE_LIMIT_MAX = 0x020400C1
    # calibration
    SA_CTL_PKEY_CALIBRATION_OPTIONS = 0x0306005D
    SA_CTL_PKEY_SIGNAL_CORRECTION_OPTIONS = 0x0306001C
    # referencing
    SA_CTL_PKEY_REFERENCING_OPTIONS = 0x0307005D
    SA_CTL_PKEY_DIST_CODE_INVERTED = 0x0307000E
    SA_CTL_PKEY_DISTANCE_TO_REF_MARK = 0x030700A2
    # tuning and customizing
    SA_CTL_PKEY_POS_MOVEMENT_TYPE = 0x0309003F
    SA_CTL_PKEY_POS_IS_CUSTOM_TYPE = 0x03090041
    SA_CTL_PKEY_POS_BASE_UNIT = 0x03090042
    SA_CTL_PKEY_POS_BASE_RESOLUTION = 0x03090043
    SA_CTL_PKEY_POS_HEAD_TYPE = 0x0309008E
    SA_CTL_PKEY_POS_REF_TYPE = 0x03090048
    SA_CTL_PKEY_POS_P_GAIN = 0x0309004B
    SA_CTL_PKEY_POS_I_GAIN = 0x0309004C
    SA_CTL_PKEY_POS_D_GAIN = 0x0309004D
    SA_CTL_PKEY_POS_PID_SHIFT = 0x0309004E
    SA_CTL_PKEY_POS_ANTI_WINDUP = 0x0309004F
    SA_CTL_PKEY_POS_ESD_DIST_TH = 0x03090050
    SA_CTL_PKEY_POS_ESD_COUNTER_TH = 0x03090051
    SA_CTL_PKEY_POS_TARGET_REACHED_TH = 0x03090052
    SA_CTL_PKEY_POS_TARGET_HOLD_TH = 0x03090053
    SA_CTL_PKEY_POS_SAVE = 0x0309000A
    SA_CTL_PKEY_POS_WRITE_PROTECTION = 0x0309000D
    # streaming
    SA_CTL_PKEY_STREAM_BASE_RATE = 0x040F002C
    SA_CTL_PKEY_STREAM_EXT_SYNC_RATE = 0x040F002D
    SA_CTL_PKEY_STREAM_OPTIONS = 0x040F005D
    SA_CTL_PKEY_STREAM_LOAD_MAX = 0x040F0301
    # diagnostic
    SA_CTL_PKEY_CHANNEL_ERROR = 0x0502007A
    SA_CTL_PKEY_CHANNEL_TEMPERATURE = 0x05020034
    SA_CTL_PKEY_BUS_MODULE_TEMPERATURE = 0x05030034
    SA_CTL_PKEY_POSITIONER_FAULT_REASON = 0x05020113
    SA_CTL_PKEY_MOTOR_LOAD = 0x05020115
    # io module
    SA_CTL_PKEY_IO_MODULE_OPTIONS = 0x0603005D
    SA_CTL_PKEY_IO_MODULE_VOLTAGE = 0x06030031
    SA_CTL_PKEY_IO_MODULE_ANALOG_INPUT_RANGE = 0x060300A0
    # auxiliary
    SA_CTL_PKEY_AUX_POSITIONER_TYPE = 0x0802003C
    SA_CTL_PKEY_AUX_POSITIONER_TYPE_NAME = 0x0802003D
    SA_CTL_PKEY_AUX_INPUT_SELECT = 0x08020018
    SA_CTL_PKEY_AUX_IO_MODULE_INPUT_INDEX = 0x081100AA
    SA_CTL_PKEY_AUX_SENSOR_MODULE_INPUT_INDEX = 0x080B00AA
    SA_CTL_PKEY_AUX_IO_MODULE_INPUT0_VALUE = 0x08110000
    SA_CTL_PKEY_AUX_IO_MODULE_INPUT1_VALUE = 0x08110001
    SA_CTL_PKEY_AUX_SENSOR_MODULE_INPUT0_VALUE = 0x080B0000
    SA_CTL_PKEY_AUX_SENSOR_MODULE_INPUT1_VALUE = 0x080B0001
    SA_CTL_PKEY_AUX_DIRECTION_INVERSION = 0x0809000E
    SA_CTL_PKEY_AUX_DIGITAL_INPUT_VALUE = 0x080300AD
    SA_CTL_PKEY_AUX_DIGITAL_OUTPUT_VALUE = 0x080300AE
    SA_CTL_PKEY_AUX_DIGITAL_OUTPUT_SET = 0x080300B0
    SA_CTL_PKEY_AUX_DIGITAL_OUTPUT_CLEAR = 0x080300B1
    SA_CTL_PKEY_AUX_ANALOG_OUTPUT_VALUE0 = 0x08030000
    SA_CTL_PKEY_AUX_ANALOG_OUTPUT_VALUE1 = 0x08030001
    # threshold detector
    SA_CTL_PKEY_THD_INPUT_SELECT = 0x09020018
    SA_CTL_PKEY_THD_IO_MODULE_INPUT_INDEX = 0x091100AA
    SA_CTL_PKEY_THD_SENSOR_MODULE_INPUT_INDEX = 0x090B00AA
    SA_CTL_PKEY_THD_THRESHOLD_HIGH = 0x090200B4
    SA_CTL_PKEY_THD_THRESHOLD_LOW = 0x090200B5
    SA_CTL_PKEY_THD_INVERSION = 0x0902000E
    # input trigger
    SA_CTL_PKEY_DEV_INPUT_TRIG_MODE = 0x060D0087
    SA_CTL_PKEY_DEV_INPUT_TRIG_CONDITION = 0x060D005A
    # output trigger
    SA_CTL_PKEY_CH_OUTPUT_TRIG_MODE = 0x060E0087
    SA_CTL_PKEY_CH_OUTPUT_TRIG_POLARITY = 0x060E005B
    SA_CTL_PKEY_CH_OUTPUT_TRIG_PULSE_WIDTH = 0x060E005C
    SA_CTL_PKEY_CH_POS_COMP_START_THRESHOLD = 0x060E0058
    SA_CTL_PKEY_CH_POS_COMP_INCREMENT = 0x060E0059
    SA_CTL_PKEY_CH_POS_COMP_DIRECTION = 0x060E0026
    SA_CTL_PKEY_CH_POS_COMP_LIMIT_MIN = 0x060E0020
    SA_CTL_PKEY_CH_POS_COMP_LIMIT_MAX = 0x060E0021
    # hand control module
    SA_CTL_PKEY_HM_STATE = 0x020C000F
    SA_CTL_PKEY_HM_LOCK_OPTIONS = 0x020C0083
    SA_CTL_PKEY_HM_DEFAULT_LOCK_OPTIONS = 0x020C0084
    # api
    SA_CTL_PKEY_API_EVENT_NOTIFICATION_OPTIONS = 0xF010005D
    SA_CTL_PKEY_EVENT_NOTIFICATION_OPTIONS = 0xF010005D  # deprecated
    SA_CTL_PKEY_API_AUTO_RECONNECT = 0xF01000A1
    SA_CTL_PKEY_AUTO_RECONNECT = 0xF01000A1  # deprecated

    # move modes
    SA_CTL_MOVE_MODE_CL_ABSOLUTE = 0
    SA_CTL_MOVE_MODE_CL_RELATIVE = 1
    SA_CTL_MOVE_MODE_SCAN_ABSOLUTE = 2
    SA_CTL_MOVE_MODE_SCAN_RELATIVE = 3
    SA_CTL_MOVE_MODE_STEP = 4

    # referencing options
    SA_CTL_REF_OPT_BIT_NORMAL = 0x00000000
    SA_CTL_REF_OPT_BIT_START_DIR = 0x00000001
    SA_CTL_REF_OPT_BIT_REVERSE_DIR = 0x00000002
    SA_CTL_REF_OPT_BIT_AUTO_ZERO = 0x00000004
    SA_CTL_REF_OPT_BIT_ABORT_ON_ENDSTOP = 0x00000008
    SA_CTL_REF_OPT_BIT_CONTINUE_ON_REF_FOUND = 0x00000010
    SA_CTL_REF_OPT_BIT_STOP_ON_REF_FOUND = 0x00000020

    # calibration options
    SA_CTL_CALIB_OPT_BIT_DIRECTION = 0x00000001
    SA_CTL_CALIB_OPT_BIT_DIST_CODE_INV_DETECT = 0x00000002
    SA_CTL_CALIB_OPT_BIT_ASC_CALIBRATION = 0x00000004
    SA_CTL_CALIB_OPT_BIT_REF_MARK_TEST = 0x00000008
    SA_CTL_CALIB_OPT_BIT_LIMITED_TRAVEL_RANGE = 0x00000100

    SA_CTL_INFINITE = 0xffffffff

    def __init__(self):
        if os.name == "nt":
            raise NotImplementedError("Windows not yet supported")
            # WinDLL.__init__(self, "libSA_CTL.dll")  # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libsmaractctl.so", RTLD_GLOBAL)

        self.SA_CTL_GetFullVersionString.restype = c_char_p
        self.SA_CTL_GetFullVersionString.errcheck = lambda r, f, a: r  # Always happy

    def __getitem__(self, name):
        try:
            func = super(SA_CTLDLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        if func.errcheck is None:
            func.errcheck = self.sp_errcheck
        return func

    @staticmethod
    def sp_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result != SA_CTLDLL.SA_CTL_ERROR_NONE:
            raise SA_CTLError(result, "Call to %s() failed with error 0x%x: %s" %
                              (func.__name__, result, SA_CTLDLL.err_code.get(result, "")))

        return result

class SA_CTLError(IOError):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(SA_CTLError, self).__init__(errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror


class MCS2(model.Actuator):

    def __init__(self, name, role, locator, ref_on_init=False, axes=None, speed=1e-3, accel=1e-3,
                 hold_time=float('inf'), pos_deactive_after_ref=False, **kwargs):
        """
        A driver for a SmarAct MCS2 Actuator.
        This driver uses a DLL provided by SmarAct which connects via
        USB or TCP/IP using a locator string.

        name: (str)
        role: (str)
        locator: (str) Use "fake" for a simulator.
            For a real device, MCS controllers with USB interface can be addressed with the
            following locator syntax:
                usb:sn:<serialnumber>
            where <serialnumber> is the serial number of an MCS2 controller.
            If the controller has a TCP/IP connection, use one of:
                network:<ipv4>
                network:sn:<serialnumber>
        ref_on_init: (bool) determines if the controller should automatically reference
            on initialization
        hold_time (float): the hold time, in seconds, for the actuator after the target position is reached.
            Default is float('inf') or infinite. Can be also set to 0 to disable hold.
            Is set to the same value for all channels.
        axes: dict str (axis name) -> dict (axis parameters)
            axis parameters: {
                range: [float, float], default is -1 -> 1
                unit: (str) default will be set to 'm'
                channel: (int) the corresponding axis number on the controller
            }
        pos_deactive_after_ref (bool): if True, will move to the deactive position
            defined in metadata after referencing
        """
        if not axes:
            raise ValueError("Needs at least 1 axis.")

        if locator != "fake":
            self.core = SA_CTLDLL()
        else:
            self.core = FakeMCS2_DLL()

        # Not to be mistaken with axes which is a simple public view
        self._axis_map = {}  # axis name -> axis number used by controller
        axes_def = {}  # axis name -> Axis object
        self._locator = locator

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

            try:
                axis_channel = axis_par['channel']
            except KeyError:
                raise ValueError("Axis %s has no channel." % axis_name)

            ad = model.Axis(canAbs=True, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad
            self._axis_map[axis_name] = axis_channel

        # Connect to the device
        logging.debug("Connecting to locator %s", locator)
        self._id = c_uint32(0)
        try:
            self.core.SA_CTL_Open(byref(self._id), c_char_p(locator.encode("ascii")), c_char_p(b""))
        except SA_CTLError as ex:
            if ex.errno == SA_CTLDLL.SA_CTL_ERROR_DEVICE_NOT_FOUND:
                raise model.HwError("Failed to find device, check it is connected and turned on")
            elif ex.errno == SA_CTLDLL.SA_CTL_ERROR_NO_SENSOR_PRESENT:
                raise model.HwError("Failed to find any axis, check the actuators are connected to the controller")

            raise
        logging.debug("Connected to SA_CTL Controller ID %d with %d channels", self._id.value, self._get_number_of_channels())
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        # Add metadata
        self._swVersion = self.GetFullVersionString()
        devname = self.GetProperty_s(SA_CTLDLL.SA_CTL_PKEY_DEVICE_NAME, 0)
        sn = self.GetProperty_s(SA_CTLDLL.SA_CTL_PKEY_DEVICE_SERIAL_NUMBER, 0)
        pos_types = [self.GetProperty_s(SA_CTLDLL.SA_CTL_PKEY_POSITIONER_TYPE_NAME, self._axis_map[a])
                     for a in sorted(self._axis_map)]
        self._hwVersion = "SmarAct %s (s/n %s) with positioners %s" % (devname, sn, ", ".join(pos_types))

        logging.debug("Using SA_CTL library version %s to connect to %s", self._swVersion, self._hwVersion)

        for name, channel in self._axis_map.items():
            self._set_speed(channel, speed)
            self._set_accel(channel, accel)
            self._set_hold_time(channel, hold_time)

            # Log referencing mode, and warn if it's not normal (autozero)
            ref_mode = self.GetProperty_i32(SA_CTLDLL.SA_CTL_PKEY_POSITIONER_TYPE, channel)
            log_lvl = logging.INFO
            if ref_mode & SA_CTLDLL.SA_CTL_REF_OPT_BIT_AUTO_ZERO:
                log_lvl = logging.WARNING
            logging.log(log_lvl, "Current referencing mode = {}.".format(ref_mode))

        self.position = model.VigilantAttribute({}, readonly=True)

        try:
            self._updatePosition()
        except SA_CTLError as ex:
            if ex.errno == SA_CTLDLL.SA_CTL_ERROR_NO_SENSOR_PRESENT:
                # This happens if the axis is not connected to the controller
                raise model.HwError("Check the connection between controller and axis: %s" % (ex,))
            raise

        # Indicates moving to a deactive position after referencing.
        self._pos_deactive_after_ref = pos_deactive_after_ref

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(1)  # one task at a time

        # define the referenced VA from the query
        axes_ref = {a: self._is_channel_referenced(i) for a, i in self._axis_map.items()}
        # VA dict str(axis) -> bool
        self.referenced = model.VigilantAttribute(axes_ref, readonly=True)

        # If ref_on_init, referenced immediately.
        if all(referenced for _, referenced in axes_ref.items()):
            logging.debug("SA_CTL is referenced")
        else:
            if ref_on_init:
                self.reference(set(axes_ref.keys()))  # will reference in background
            else:
                logging.warning("SA_CTL is not referenced. The device will not function until referencing occurs.")

        self._update_position_timer = RepeatingTimer(1.0, self._updatePosition)
        self._update_position_timer.start()

        self.speed = VigilantAttribute({}, unit="m/s", readonly=True)
        self._updateSpeed()

        self._accel = {}
        self._updateAccel()

    def terminate(self):
        self._update_position_timer.cancel()

        # should be safe to close the device multiple times if terminate is called more than once.
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None
            self.core.SA_CTL_Close(self._id)

        super(MCS2, self).terminate()

    def updateMetadata(self, md):
        if model.MD_FAV_POS_DEACTIVE in md:
            deactive_pos = md[model.MD_FAV_POS_DEACTIVE]
            if not (isinstance(deactive_pos, dict) and set(deactive_pos.keys()).intersection(set(self._axis_map.keys()))):
                raise ValueError("Invalid metadata, should be a coordinate dictionary but got %s." % (deactive_pos,))
        super(MCS2, self).updateMetadata(md)

    @staticmethod
    def scan():
        """
        Util function to find all of the MCS2 controllers
        returns: set of tuples (name, dict) with dict str -> str
            the dict just has the locator string
        """
        core = SA_CTLDLL()
        b_len = 1024
        buf = create_string_buffer(b_len)
        core.SA_CTL_FindDevices(c_char_p(""), buf, byref(c_size_t(b_len)))
        locators = buf.value.encode('ascii')

        devices = set()
        for counter, loc in enumerate(locators):
            devices.add(("MCS2 %d" % (counter,), {"locator": loc}))

        return devices

    # API Calls

    def GetFullVersionString(self):
        ver = self.core.SA_CTL_GetFullVersionString()
        return ver.decode("latin1")

    # Functions to set the property values in the controller, categorized by data type
    def SetProperty_f64(self, property_key, idx, value):
        """
        property_key (int32): property key symbol
        idx (int): channel
        value (double): value to set
        """
        self.core.SA_CTL_SetProperty_f64(self._id, c_int8(idx), c_uint32(property_key), c_double(value))

    def SetProperty_i32(self, property_key, idx, value):
        """
        property_key (int32): property key symbol
        idx (int): channel
        value (int32): value to set
        """
        self.core.SA_CTL_SetProperty_i32(self._id, c_int8(idx), c_uint32(property_key), c_int32(value))

    def SetProperty_i64(self, property_key, idx, value):
        """
        property_key (int64): property key symbol
        idx (int): channel
        value (int64): value to set
        """
        self.core.SA_CTL_SetProperty_i64(self._id, c_int8(idx), c_uint32(property_key), c_int64(value))

    def GetProperty_f64(self, property_key, idx):
        """
        property_key (int32): property key symbol
        idx (int): channel
        returns (float) the value
        """
        ret_val = c_double()
        self.core.SA_CTL_GetProperty_f64(self._id, c_int8(idx), c_uint32(property_key), byref(ret_val), c_size_t(0))
        return ret_val.value

    def GetProperty_i32(self, property_key, idx):
        """
        property_key (int32): property key symbol
        idx (int): channel
        returns (int) the value
        """
        ret_val = c_int32()
        self.core.SA_CTL_GetProperty_i32(self._id, c_int8(idx), c_uint32(property_key), byref(ret_val), c_size_t(0))
        return ret_val.value

    def GetProperty_i64(self, property_key, idx):
        """
        property_key (int64): property key symbol
        idx (int): channel
        returns (int) the value
        """
        ret_val = c_int64()
        self.core.SA_CTL_GetProperty_i64(self._id, c_int8(idx), c_uint32(property_key), byref(ret_val), c_size_t(0))
        return ret_val.value

    def GetProperty_s(self, property_key, idx):
        """
        property_key (int32): property key symbol
        idx (int): channel
        returns (str): the value
        """
        ret_val = create_string_buffer(SA_CTLDLL.SA_CTL_STRING_MAX_LENGTH)
        slen = c_size_t(len(ret_val))
        self.core.SA_CTL_GetProperty_s(self._id, c_int8(idx), c_uint32(property_key),
                                       ret_val, byref(slen))
        return ret_val.value.decode("latin1")

    def Reference(self, channel):
        # Reference the controller. Note - this is asynchronous
        self.core.SA_CTL_Reference(self._id, c_int8(channel), c_int8(0))

    def Calibrate(self, channel):
        # Calibrate the controller. Note - this is blocking
        self.core.SA_CTL_Calibrate(self._id, c_int8(channel), c_int8(0))
        while self._is_channel_moving(channel):
            time.sleep(0.1)

    def Move(self, pos, channel, moveMode):
        """
        Move to position specified
        pos (float): position to move to
        moveMode (int32): one of the move modes of the controller
            SA_CTLDLL.SA_CTL_MOVE_MODE_CL_ABSOLUTE
            SA_CTLDLL.SA_CTL_MOVE_MODE_CL_RELATIVE
            etc...

        Raises: SA_CTLError if a problem occurs
        """
        # convert pos from m to picometres (the unit used by teh controller)
        pos_pm = int(pos * 1e12)
        self.SetProperty_i32(SA_CTLDLL.SA_CTL_PKEY_MOVE_MODE, channel, moveMode)
        self.core.SA_CTL_Move(self._id, c_int8(channel), c_int64(pos_pm), SA_CTL_TransmitHandle_t(0))

    def Stop(self, channel):
        """
        Stop command sent to the SA_CTL
        """
        logging.debug("Stopping channel %d..." % (channel,))
        self.core.SA_CTL_Stop(self._id, c_int8(channel), c_int8(0))

    # Basic functions

    def _get_number_of_channels(self):
        return self.GetProperty_i32(SA_CTLDLL.SA_CTL_PKEY_NUMBER_OF_CHANNELS, 0)

    def _get_channel_state(self, channel):
        """
        Gets the channel state and logs any errors
        channel (int): the channel
        returns (int32): the state
        """

        return self.GetProperty_i32(SA_CTLDLL.SA_CTL_PKEY_CHANNEL_STATE, channel)

    def _check_channel_error(self, channel):
        """
        channel (int)
        raise a HwError if the channel reports an error
        """
        state = self._get_channel_state(channel)
        if state & SA_CTLDLL.SA_CTL_CH_STATE_BIT_MOVEMENT_FAILED:
            if state & SA_CTLDLL.SA_CTL_CH_STATE_BIT_END_STOP_REACHED:
                raise model.HwError("Channel %d: reached end-stop" % (channel,))
            elif state & SA_CTLDLL.SA_CTL_CH_STATE_BIT_RANGE_LIMIT_REACHED:
                raise model.HwError("Channel %d reached range limit" % (channel,))
            elif state & SA_CTLDLL.SA_CTL_CH_STATE_BIT_FOLLOWING_LIMIT_REACHED:
                raise model.HwError("Channel %d reached following limit" % (channel,))
            else:
                raise model.HwError("Channel %d movement failed for unknown reason" % (channel,))

    def _is_channel_referenced(self, channel):
        """
        channel (int)
        return (bool): True if the axis is referenced
        """
        return bool(self._get_channel_state(channel) & SA_CTLDLL.SA_CTL_CH_STATE_BIT_IS_REFERENCED)

    def _is_channel_moving(self, channel):
        """
        channel (int)
        return (bool): True if the axis is moving
        """
        return bool(self._get_channel_state(channel) & SA_CTLDLL.SA_CTL_CH_STATE_BIT_ACTIVELY_MOVING)

    def _get_position(self, channel):
        """
        Get the position on a specified channel
        returns: the position in m (convert from device unit of pm)
        """
        return self.GetProperty_i64(SA_CTLDLL.SA_CTL_PKEY_POSITION, channel) / 1e12

    def _set_speed(self, channel, value):
        """
        Set the speed of the SA_CTL motion
        value: (float) indicating speed for all axes in m/s
        """
        logging.debug("Setting speed to %f", value)
        # convert value to pm/s for the controller
        speed = int(value * 1e12)
        self.SetProperty_i64(SA_CTLDLL.SA_CTL_PKEY_MOVE_VELOCITY, channel, speed)

    def _get_speed(self, channel):
        """
        Returns (float) the linear speed of the SA_CTL motion in m/s
        """
        # value is given in pm/s
        speed = self.GetProperty_i64(SA_CTLDLL.SA_CTL_PKEY_MOVE_VELOCITY, channel)
        # convert to m/s
        return float(speed) * 1e-12

    def _set_accel(self, channel, value):
        """
        Set the speed of the SA_CTL motion
        value: (float) indicating speed for all axes
        """
        logging.debug("Setting accel to %f", value)
        # convert value to pm/s2 for the controller
        accel = int(value * 1e12)
        self.SetProperty_i64(SA_CTLDLL.SA_CTL_PKEY_MOVE_ACCELERATION, channel, accel)

    def _get_accel(self, channel):
        """
        Returns (float) the accel of the SA_CTL motion
        """
        # value is given in pm/s2
        accel = self.GetProperty_i64(SA_CTLDLL.SA_CTL_PKEY_MOVE_ACCELERATION, channel)
        # convert to m/s
        return float(accel) * 1e-12

    def _set_hold_time(self, channel, hold_time):
        """
        Set the hold time of the channel after the actuator reached the target position
        channel (int): the channel
        hold_time (float): The hold time, in seconds. Use float('inf") for infinte hold time
            or 0 for no hold time
        """
        # hold time is specified in ms in the controller
        if hold_time == float('inf'):
            ht = SA_CTLDLL.SA_CTL_INFINITE
        else:
            ht = int(hold_time * 1e3)

        self.SetProperty_i32(SA_CTLDLL.SA_CTL_PKEY_HOLD_TIME, channel, ht)

    def stop(self, axes=None):
        """
        Stop the SA_CTL controller and update position
        if axes = None, stop all axes
        """

        if axes is None:
            axes = self._axis_map.keys()
            self._executor.cancel()

        for axis_name in axes:
            self.Stop(self._axis_map.get(axis_name))

        self._updatePosition()

    def _updatePosition(self):
        """
        update the position VA
        """
        p = {}
        try:
            for axis_name, axis_channel in self._axis_map.items():
                p[axis_name] = self._get_position(axis_channel)

        except SA_CTLError as ex:
            if ex.errno != SA_CTLDLL.SA_CTL_ERROR_NOT_REFERENCED:
                raise

            logging.warning("Position unknown because SA_CTL is not referenced")
            p = {a: 0 for a in self.axes}
        p = self._applyInversion(p)
        logging.debug("Updated position to %s", p)
        self.position._set_value(p, force_write=True)

    def _updateSpeed(self):
        """
        update the speeds
        """
        s = {}
        for axis_name, axis_channel in self._axis_map.items():
            s[axis_name] = self._get_speed(axis_channel)

        logging.debug("Updated speed to %s", s)
        self.speed._set_value(s, force_write=True)

    def _updateAccel(self):
        """
        update the accels
        """
        a = {}
        for axis_name, axis_channel in self._axis_map.items():
            a[axis_name] = self._get_accel(axis_channel)

        logging.debug("Updated accel to %s", a)
        self._accel = a

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
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
            try:
                end = 0  # expected end
                moving_axes = set()
                for an, v in pos.items():
                    channel = self._axis_map[an]
                    moving_axes.add(channel)
                    self.Move(v, channel, SA_CTLDLL.SA_CTL_MOVE_MODE_CL_RELATIVE)
                    # compute expected end
                    dur = driver.estimateMoveDuration(abs(v),
                                    self.speed.value[an],
                                    self._accel[an])

                    end = max(time.time() + dur, end)

                self._waitEndMove(future, moving_axes, end)
            except Exception as ex:
                logging.error("Move by %s failed: %s", pos, ex)
                raise

        logging.debug("Relative move successfully completed")

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
            try:
                end = 0  # expected end
                old_pos = self._applyInversion(self.position.value)
                moving_axes = set()
                for an, v in pos.items():
                    channel = self._axis_map[an]
                    moving_axes.add(channel)
                    self.Move(v, channel, SA_CTLDLL.SA_CTL_MOVE_MODE_CL_ABSOLUTE)
                    d = abs(v - old_pos[an])
                    dur = driver.estimateMoveDuration(d,
                                                      self.speed.value[an],
                                                      self._accel[an])
                    end = max(time.time() + dur, end)
                self._waitEndMove(future, moving_axes, end)
            except Exception as ex:
                logging.error("Move to %s failed: %s", pos, ex)
                raise

        logging.debug("Absolute move successfully completed")

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
                for channel in moving_axes.copy():  # need copy to remove during iteration
                    if not self._is_channel_moving(channel):
                        moving_axes.discard(channel)
                        self._check_channel_error(channel)

                if not moving_axes:
                    # no more axes to wait for
                    break

                now = time.time()
                if now > timeout:
                    logging.warning("Stopping move due to timeout after %g s.", max_dur)
                    for i in moving_axes:
                        self.Stop(i)
                    raise TimeoutError("Move is not over after %g s, while "
                                       "expected it takes only %g s" %
                                       (max_dur, dur))

                # Update the position from time to time (10 Hz)
                if now - last_upd > 0.1 or last_axes != moving_axes:
                    last_names = set(n for n, i in self._axis_map.items() if i in last_axes)
                    self._updatePosition()
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
                    self.Stop(i)
                future._was_stopped = True
                raise CancelledError()
        finally:
            # TODO: check if the move succeded ? (= Not failed due to stallguard/limit switch)
            self._updatePosition()  # update (all axes) with final position

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

    def _createMoveFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.RLock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    @isasync
    def reference(self, axes):
        self._checkReference(axes)

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
                moving_channels = set()
                for a in axes:
                    if future._must_stop.is_set():
                        raise CancelledError()
                    channel = self._axis_map[a]
                    moving_channels.add(channel)
                    self.referenced._value[a] = False
                    self.Reference(channel)  # search for the negative limit signal to set an origin

                self._waitEndMove(future, moving_channels, time.time() + 100)  # block until it's over

                for a in axes:
                    self.referenced._value[a] = self._is_channel_referenced(self._axis_map[a])

                    if not self.referenced._value[a]:
                        logging.warning("Axis %s not referenced after the end of referencing", a)
                        # TODO: Raise some error here

                # if referenced, move to the safe position (if requested)
                all_axes_referenced = all(self.referenced._value[a] for a in self._axis_map)

                if self._pos_deactive_after_ref and all_axes_referenced:
                    try:
                        deactive_pos = self._metadata[model.MD_FAV_POS_DEACTIVE]
                    except KeyError:
                        logging.warning("Cannot move to deactive position. Missing MD_FAV_POS_DEACTIVE")
                    else:
                        logging.info("Moving axes to deactivated position %s after referencing", deactive_pos)
                        self._checkMoveAbs(deactive_pos)
                        self._doMoveAbs(future, self._applyInversion(deactive_pos))

                self._waitEndMove(future, moving_channels, time.time() + 100)

            except CancelledError:
                # FIXME: if the referencing is stopped, the device refuses to
                # move until referencing is run (and successful).
                # => Need to put back the device into a mode where at least
                # relative moves work.
                logging.warning("Referencing cancelled, device will not move until another referencing")
                future._was_stopped = True
                raise
            except Exception as ex:
                self.state._set_value(ex, force_write=True)
                logging.exception("Referencing failure")
                raise
            finally:
                # We only notify after updating the position so that when a listener
                # receives updates both values are already updated.
                self._updatePosition()  # all the referenced axes should be back to 0
                # read-only so manually notify
                self.referenced.notify(self.referenced.value)


class FakeMCS2_DLL(object):
    """
    Fake MCS2 DLL for simulator
    """

    def __init__(self):
        self.properties = {
            SA_CTLDLL.SA_CTL_PKEY_DEVICE_NAME: [0],
            SA_CTLDLL.SA_CTL_PKEY_NUMBER_OF_CHANNELS: [3],
            SA_CTLDLL.SA_CTL_PKEY_MOVE_MODE: [
                    SA_CTLDLL.SA_CTL_MOVE_MODE_CL_ABSOLUTE,
                    SA_CTLDLL.SA_CTL_MOVE_MODE_CL_ABSOLUTE,
                    SA_CTLDLL.SA_CTL_MOVE_MODE_CL_ABSOLUTE,
                    ],
            SA_CTLDLL.SA_CTL_PKEY_CHANNEL_STATE: [0, 0, 0],
            SA_CTLDLL.SA_CTL_PKEY_POSITION: [0, 0, 0],
            SA_CTLDLL.SA_CTL_PKEY_MOVE_VELOCITY: [1, 1, 1],
            SA_CTLDLL.SA_CTL_PKEY_MOVE_ACCELERATION: [1, 1, 1],
            SA_CTLDLL.SA_CTL_PKEY_HOLD_TIME: [0, 0, 0],
            SA_CTLDLL.SA_CTL_PKEY_DEVICE_NAME: [b"Simulated"],
            SA_CTLDLL.SA_CTL_PKEY_DEVICE_SERIAL_NUMBER: [b"1234"],
            SA_CTLDLL.SA_CTL_PKEY_REFERENCING_OPTIONS: [0, 0, 0],
            SA_CTLDLL.SA_CTL_PKEY_CALIBRATION_OPTIONS: [0, 0, 0],
            SA_CTLDLL.SA_CTL_PKEY_LOGICAL_SCALE_OFFSET: [0, 0, 0],
            SA_CTLDLL.SA_CTL_PKEY_POSITIONER_TYPE_NAME: [b"F4K3", b"F4K3", b"F4K3"],
            SA_CTLDLL.SA_CTL_PKEY_POSITIONER_TYPE: [0, 0, 0],
        }

        # Specify ranges
        self._range = [(-10e12, 10e12), (-10e12, 10e12), (-10e12, 10e12)]

        self._move_start_pos = [0] * 3
        self._move_start_time = [time.time()] * 3
        self._move_finish_time = [time.time()] * 3
        self._target = [0] * 3

    def _get_current_pos(self, axis):
        """
        return (int): position
        """
        now = time.time()
        startt = self._move_start_time[axis]
        endt = self._move_finish_time[axis]
        startp = self._move_start_pos[axis]
        endp = self._target[axis]
        if endt < now:
            return endp
        # model as if it was linear (it's not, it's ramp-based positioning)
        pos = startp + (endp - startp) * (now - startt) / (endt - startt)
        return int(pos)

    def _pos_in_range(self, ch, pos):
        return (self._range[ch][0] <= pos <= self._range[0][1])

    """
    DLL functions (fake)
    These functions are provided by the real SA_MC DLL
    """

    def SA_CTL_Open(self, id, locator, options):
        logging.debug("sim MCS2: Starting MCS2 Sim")

    def SA_CTL_Close(self, id):
        logging.debug("sim MCS2: Closing MCS2 Sim")

    def SA_CTL_GetFullVersionString(self):
        return b"1.2.3.123"

    def SA_CTL_SetProperty_f64(self, handle, ch, property_key, value):
        if not property_key.value in self.properties:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_INVALID_KEY, "error")
        self.properties[property_key.value][ch.value] = value.value

    def SA_CTL_SetProperty_i32(self, handle, ch, property_key, value):
        if not property_key.value in self.properties:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_INVALID_KEY, "error")
        self.properties[property_key.value][ch.value] = value.value

    def SA_CTL_SetProperty_i64(self, handle, ch, property_key, value):
        if not property_key.value in self.properties:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_INVALID_KEY, "error")
        self.properties[property_key.value][ch.value] = value.value

    def SA_CTL_GetProperty_f64(self, handle, ch, property_key, p_val, size):
        if not property_key.value in self.properties:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_INVALID_KEY, "error")
        val = _deref(p_val, c_double)
        val.value = self.properties[property_key.value][ch.value]

    def SA_CTL_GetProperty_i32(self, handle, ch, property_key, p_val, size):
        if not property_key.value in self.properties:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_INVALID_KEY, "error")

        # Handle movement states before setting the value
        elif (property_key.value == SA_CTLDLL.SA_CTL_PKEY_CHANNEL_STATE and
              self._move_finish_time[ch.value] < time.time()):  # move is finished
            self.properties[SA_CTLDLL.SA_CTL_PKEY_CHANNEL_STATE][ch.value] &= \
                ~(SA_CTLDLL.SA_CTL_CH_STATE_BIT_ACTIVELY_MOVING)

        # update the value of the key
        val = _deref(p_val, c_int32)
        val.value = self.properties[property_key.value][ch.value]

    def SA_CTL_GetProperty_i64(self, handle, ch, property_key, p_val, size):
        if not property_key.value in self.properties:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_INVALID_KEY, "error")
        if property_key.value == SA_CTLDLL.SA_CTL_PKEY_POSITION:
            self.properties[SA_CTLDLL.SA_CTL_PKEY_POSITION][ch.value] = self._get_current_pos(ch.value)
        val = _deref(p_val, c_int64)
        val.value = self.properties[property_key.value][ch.value]

    def SA_CTL_GetProperty_s(self, handle, ch, property_key, val, ioArraySize):
        if not property_key.value in self.properties:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_INVALID_KEY, "error")
        val.value = self.properties[property_key.value][ch.value]

    def SA_CTL_Reference(self, handle, ch, _):
        logging.debug("sim MCS2: Referencing channel %d", ch.value)
        self.properties[SA_CTLDLL.SA_CTL_PKEY_CHANNEL_STATE][ch.value] |= SA_CTLDLL.SA_CTL_CH_STATE_BIT_IS_REFERENCED

        # Simulating a move to 0 in 5 s
        self.properties[SA_CTLDLL.SA_CTL_PKEY_CHANNEL_STATE][ch.value] |= SA_CTLDLL.SA_CTL_CH_STATE_BIT_ACTIVELY_MOVING
        self._move_start_pos[ch.value] = self.properties[SA_CTLDLL.SA_CTL_PKEY_POSITION][ch.value]
        self._move_start_time[ch.value] = time.time()
        self._move_finish_time[ch.value] = time.time() + 5
        self._target[ch.value] = 0

    def SA_CTL_Calibrate(self, handle, ch, _):
        logging.debug("sim MCS2: Calibrating channel %d", ch.value)

    def SA_CTL_Move(self, handle, ch, pos_pm, _):
        if self._pos_in_range(ch.value, pos_pm.value):
            self._move_start_pos[ch.value] = self.properties[SA_CTLDLL.SA_CTL_PKEY_POSITION][ch.value]
            self._move_start_time[ch.value] = time.time()
            if self.properties[SA_CTLDLL.SA_CTL_PKEY_MOVE_MODE][ch.value] == SA_CTLDLL.SA_CTL_MOVE_MODE_CL_ABSOLUTE:
                self._target[ch.value] = pos_pm.value
                logging.debug("sim MCS2: Abs move channel %d to %d pm" % (ch.value, pos_pm.value))
            elif self.properties[SA_CTLDLL.SA_CTL_PKEY_MOVE_MODE][ch.value] == SA_CTLDLL.SA_CTL_MOVE_MODE_CL_RELATIVE:
                self._target[ch.value] = pos_pm.value + self.properties[SA_CTLDLL.SA_CTL_PKEY_POSITION][ch.value]
                logging.debug("sim MCS2: Rel move channel %d to %d pm" % (ch.value, self._target[ch.value]))
            else:
                raise IOError("sim move has unknown move mode")

            dur = driver.estimateMoveDuration(abs(self._target[ch.value] - self._move_start_pos[ch.value]),
                                              self.properties[SA_CTLDLL.SA_CTL_PKEY_MOVE_VELOCITY][ch.value],
                                              self.properties[SA_CTLDLL.SA_CTL_PKEY_MOVE_ACCELERATION][ch.value])
            dur += 0.001  # simulates overhead, and also handles moves with distance == 0
            self._move_finish_time[ch.value] = self._move_start_time[ch.value] + dur
            logging.debug("Simulating move of %s s", dur)
            self.properties[SA_CTLDLL.SA_CTL_PKEY_CHANNEL_STATE][ch.value] |= SA_CTLDLL.SA_CTL_CH_STATE_BIT_ACTIVELY_MOVING
        else:
            raise SA_CTLError(SA_CTLDLL.SA_CTL_ERROR_RANGE_LIMIT_REACHED, "error")

    def SA_CTL_Stop(self, handle, ch, _):
        self._target[ch.value] = self._get_current_pos(ch.value)
        self._move_finish_time[ch.value] = 0


SA_SI_TIMEOUT_INFINITE = 0xffffffff
SA_SI_STRING_MAX_LENGTH = 63

# SmarAct Picoscale error codes
SA_SI_ERROR_NONE = 0x0000
SA_SI_ERROR_UNKNOWN_COMMAND = 0x0001
SA_SI_ERROR_INVALID_PACKET_SIZE = 0x0002
SA_SI_ERROR_TIMEOUT = 0x0004
SA_SI_ERROR_INVALID_PROTOCOL = 0x0005
SA_SI_ERROR_BUFFER_UNDERFLOW = 0x000c
SA_SI_ERROR_BUFFER_OVERFLOW = 0x000d
SA_SI_ERROR_INVALID_PACKET = 0x0010
SA_SI_ERROR_INVALID_STREAM_PACKET = 0x0011
SA_SI_ERROR_INVALID_PROPERTY = 0x0012
SA_SI_ERROR_INVALID_PARAMETER = 0x0013
SA_SI_ERROR_INVALID_CHANNEL_INDEX = 0x0014
SA_SI_ERROR_INVALID_DSOURCE_INDEX = 0x0015
SA_SI_ERROR_INVALID_DATA_TYPE = 0x0016
SA_SI_ERROR_PERMISSION_DENIED = 0x001f
SA_SI_ERROR_NO_DATA_SOURCES_ENABLED = 0x0020
SA_SI_ERROR_STREAMING_ACTIVE = 0x0021
SA_SI_ERROR_SOURCE_NOT_STREAMABLE = 0x0022
SA_SI_ERROR_UNKNOWN_DATA_OBJECT = 0x0030
SA_SI_ERROR_COMMAND_NOT_PROCESSABLE = 0x00ff
SA_SI_ERROR_FEATURE_NOT_SUPPORTED = 0x7ffd
SA_SI_ERROR_NOT_IMPLEMENTED = 0x7ffe
SA_SI_ERROR_OTHER = 0x7fff
SA_PS_ERROR_REQUEST_DENIED = 0x8000
SA_PS_ERROR_INTERNAL_COMMUNICATION = 0x8001
SA_PS_ERROR_NO_FULL_ACCESS = 0x8002
SA_PS_ERROR_WORKING_DISTANCE_NOT_SET = 0x8200
SA_SI_ERROR_DEVICE_LIMIT = 0xf000
SA_SI_ERROR_INVALID_LOCATOR = 0xf001
SA_SI_ERROR_INITIALIZATION = 0xf002
SA_SI_ERROR_NOT_INITIALIZED = 0xf003
SA_SI_ERROR_COMMUNICATION = 0xf004
SA_SI_ERROR_QUERYBUFFER_SIZE = 0xf006
SA_SI_ERROR_INVALID_HANDLE = 0xf007
SA_SI_ERROR_DATA_SOURCE_ENABLED = 0xf008
SA_SI_ERROR_INVALID_STREAMBUFFER_ID = 0xf009
SA_SI_ERROR_STREAM_SEQUENCE = 0xf00a
SA_SI_ERROR_NO_DATABUFFER_AVAILABLE = 0xf00b
SA_SI_ERROR_NO_STREAMBUFFER_ACQUIRED = 0xf00d
SA_SI_ERROR_UNEXPECTED_PACKET_RECEIVED = 0xf00f
SA_SI_ERROR_CANCELLED = 0xf010
SA_SI_ERROR_BUFFER_INTERLEAVING = 0xf012
SA_SI_ERROR_DRIVER = 0xf013
SA_SI_ERROR_DATA_OBJECT_BUSY = 0xf014

# Properties
SA_SI_PROTOCOL_VERSION_PROP = 0x0000
SA_SI_PROTOCOL_VERSION_STRING_PROP = 0x0001
SA_SI_DEVICE_TYPE_PROP = 0x0002
SA_SI_DEVICE_ID_PROP = 0x0003
SA_SI_DEVICE_SERIAL_NUMBER_PROP = 0x0003
SA_SI_DEVICE_NAME_PROP = 0x0004
SA_SI_NUMBER_OF_FIRMWARE_VERSIONS_PROP = 0x0005
SA_SI_FIRMWARE_VERSION_PROP = 0x0006
SA_SI_FIRMWARE_VERSION_STRING_PROP = 0x0007
SA_SI_MAX_DATA_OBJECT_CHUNK_SIZE_PROP = 0x0008
SA_SI_NUMBER_OF_CHANNELS_PROP = 0x0011
SA_SI_MAX_FRAME_RATE_PROP = 0x0020
SA_SI_FRAME_RATE_PROP = 0x0021
SA_SI_MAX_FRAME_AGGREGATION_PROP = 0x0022
SA_SI_FRAME_AGGREGATION_PROP = 0x0023
SA_SI_FRAME_INDEX_ENABLED_PROP = 0x0024
SA_SI_PRECISE_FRAME_RATE_PROP = 0x0025
SA_SI_EVENT_NOTIFICATION_ENABLED_PROP = 0x0030
SA_SI_STREAMING_ACTIVE_PROP = 0x0040
SA_SI_STREAMING_MODE_PROP = 0x0041
SA_SI_NUMBER_OF_DATA_SOURCES_PROP = 0x1001
SA_SI_CHANNEL_NAME_PROP = 0x1002
SA_SI_DATA_SOURCE_TYPE_PROP = 0x2001
SA_SI_DATA_TYPE_PROP = 0x2002
SA_SI_AVAILABLE_COMPRESSION_MODES_PROP = 0x2003
SA_SI_COMPRESSION_MODE_PROP = 0x2004
SA_SI_STREAMING_ENABLED_PROP = 0x2005
SA_SI_BASE_UNIT_PROP = 0x2006
SA_SI_BASE_RESOLUTION_PROP = 0x2007
SA_SI_RESOLUTION_SHIFT_PROP = 0x2008
SA_SI_DATA_SOURCE_NAME_PROP = 0x2009
SA_SI_IS_STREAMABLE_PROP = 0x200a
SA_SI_COMPONENT_ID_PROP = 0x200b
SA_SI_COMPONENT_INDEX_PROP = 0x200c

SA_PS_SYS_FULL_ACCESS_CONNECTION_PROP = 0x8000
SA_PS_SYS_LVDS_LS_CONNECTED_PROP = 0x8012
SA_PS_SYS_PILOT_LASER_ACTIVE_PROP = 0x8020
SA_PS_SYS_IS_STABLE_PROP = 0x8030
SA_PS_SYS_WORKING_DISTANCE_MIN_PROP = 0x8040
SA_PS_SYS_WORKING_DISTANCE_MAX_PROP = 0x8041
SA_PS_SYS_WORKING_DISTANCE_ACTIVATE_PROP = 0x8042
SA_PS_SYS_WORKING_DISTANCE_SHRINK_MODE_PROP = 0x8043
SA_PS_SYS_NETWORK_CURRENT_IP_PROP = 0x8052
SA_PS_SYS_NETWORK_CONFIG_ACTIVATE_PROP = 0x8060
SA_PS_SYS_NETWORK_CONFIG_DHCP_PROP = 0x8061
SA_PS_SYS_NETWORK_CONFIG_IP_PROP = 0x8062
SA_PS_SYS_NETWORK_CONFIG_GATEWAY_PROP = 0x8063
SA_PS_SYS_NETWORK_CONFIG_NETMASK_PROP = 0x8064
SA_PS_SYS_NETWORK_CONFIG_NAMESERVER_PROP = 0x8065
SA_PS_SYS_NETWORK_CONFIG_DOMAINNAME_PROP = 0x8066
SA_PS_SYS_NETWORK_MAC_PROP = 0x8070
SA_PS_SYS_HEAD_TYPE_CATEGORY_COUNT_PROP = 0x8081
SA_PS_SYS_HEAD_TYPE_COUNT_PROP = 0x8082
SA_PS_SYS_HEAD_TYPE_CATEGORY_NAME_PROP = 0x8083
SA_PS_SYS_HEAD_TYPE_NAME_PROP = 0x8084
SA_PS_SYS_FIBERLENGTH_HEAD_PROP = 0x8090
SA_PS_SYS_FIBERLENGTH_EXTENSION_PROP = 0x8091
SA_PS_SYS_POSITION_ALL_CH_PROP = 0x80a0
SA_PS_SYS_CONFIGURATION_SAVE_PROP = 0x80c0
SA_PS_SYS_CONFIGURATION_LOAD_PROP = 0x80c1
SA_PS_SYS_CONFIGURATION_NAME_PROP = 0x80c2
SA_PS_SYS_CONFIGURATION_COUNT_PROP = 0x80c3
SA_PS_SYS_PRECISION_MODE_PROP = 0x80dd
SA_PS_SYS_FILTER_CUTOFF_FREQUENCY_PROP = 0x80de
SA_PS_SYS_FILTER_RATE_PROP = 0x80df
SA_PS_SYS_BOOTLOADER_VERSION_PROP = 0x80e0
SA_PS_SYS_BOOTLOADER_VERSION_STRING_PROP = 0x80e1
SA_PS_SYS_HARDWARE_VERSION_PROP = 0x80e2
SA_PS_SYS_HARDWARE_VERSION_STRING_PROP = 0x80e3
SA_PS_SYS_PRODUCT_VERSION_PROP = 0x80e4
SA_PS_SYS_PRODUCT_VERSION_STRING_PROP = 0x80e5
SA_PS_SYS_FEATURE_COUNT_PROP = 0x80f0
SA_PS_SYS_FEATURE_NAME_PROP = 0x80f1
SA_PS_SYS_FEATURE_TIME_PROP = 0x80f2
SA_PS_SYS_FEATURE_EVALUATE_PROP = 0x80f3
SA_PS_CH_ENABLED_PROP = 0x8100
SA_PS_CH_IS_VALID_PROP = 0x8101
SA_PS_CH_POSITION_PROP = 0x8102
SA_PS_CH_SCALE_INVERSION_PROP = 0x8103
SA_PS_CH_DEAD_PATH_CORRECTION_ENABLED_PROP = 0x8110
SA_PS_CH_DEAD_PATH_PROP = 0x8111
SA_PS_CH_HEAD_TYPE_PROP = 0x8112
SA_PS_CH_BEAM_INTERRUPT_TOLERANCE_PROP = 0x8113
SA_PS_CH_SIGNAL_CORRECTION_ENABLED_PROP = 0x8114
SA_PS_CH_POS_CALC_ENABLED_PROP = 0x8115
SA_PS_CH_POS_CALC_MODE_PROP = 0x8116
SA_PS_CH_POS_CALC_TRIGGER_CONDITION_PROP = 0x8117
SA_PS_CH_POS_CALC_TRIGGER_INDEX_PROP = 0x8118
SA_PS_CH_POS_CALC_TRIGGER_AUTO_RESET_MODE_PROP = 0x8119
SA_PS_CH_POS_CALC_STATE_PROP = 0x811a
SA_PS_CH_DEAD_PATH_CORRECTION_SOURCE_PROP = 0x811b
SA_PS_CH_DEAD_PATH_CORRECTION_USER_VALUE_PROP = 0x811c
SA_PS_CH_DEAD_PATH_CORRECTION_MODE_PROP = 0x811d

SA_PS_AF_ADJUSTMENT_STATE_PROP = 0x9010
SA_PS_AF_ADJUSTMENT_PROGRESS_PROP = 0x9011
SA_PS_AF_ADJUSTMENT_SIGNAL_CONTROL_ACTIVE_PROP = 0x9012
SA_PS_AF_ADJUSTMENT_AUTOSTART_AUTOADJUST_ACTIVE_PROP = 0x9013
SA_PS_AF_ADJUSTMENT_RESULT_SAVE_PROP = 0x901a
SA_PS_AF_ADJUSTMENT_RESULT_LOAD_PROP = 0x901b
SA_PS_AF_ADJUSTMENT_RESULT_NAME_PROP = 0x901c
SA_PS_AF_ADJUSTMENT_RESULT_COUNT_PROP = 0x901d
SA_PS_AF_CHANNEL_VALIDATION_STATE_PROP = 0x9040

# States
SA_SI_DISABLED = 0x00
SA_SI_ENABLED = 0x01

SA_PS_ADJUSTMENT_STATE_DISABLED = 0x00
SA_PS_ADJUSTMENT_STATE_MANUAL_ADJUST = 0x01
SA_PS_ADJUSTMENT_STATE_AUTO_ADJUST = 0x02
SA_PS_CHANNEL_VALIDATION_STATE_DISABLED = 0x00
SA_PS_CHANNEL_VALIDATION_STATE_ENABLED = 0x01
SA_PS_WORKING_DISTANCE_SHRINK_MODE_LEFT_RIGHT = 0x00
SA_PS_WORKING_DISTANCE_SHRINK_MODE_LEFT = 0x01
SA_PS_WORKING_DISTANCE_SHRINK_MODE_RIGHT = 0x02

# Events
SA_SI_STREAM_ABORTED_EVENT = 0x0001
SA_PS_FULL_ACCESS_CONNECTION_LOST_EVENT = 0x8000
SA_PS_BEAM_INTERRUPT_EVENT = 0x8010
SA_PS_OVERRANGE_EVENT = 0x8011
SA_PS_OVERHEAT_EVENT = 0x8012
SA_PS_CALC_SYS_DATA_INTERRUPT_EVENT = 0x8013
SA_PS_STABLE_STATE_CHANGED_EVENT = 0x8100
SA_PS_CHANNEL_ENABLED_STATE_CHANGED_EVENT = 0x8101
SA_PS_CHANNEL_VALID_STATE_CHANGED_EVENT = 0x8102
SA_PS_PILOT_LASER_STATE_CHANGED_EVENT = 0x8103
SA_PS_ENV_SENSOR_STATE_CHANGED_EVENT = 0x8104
SA_PS_COUNTER_STATE_CHANGED_EVENT = 0x8105
SA_PS_CLOCK_GEN_STATE_CHANGED_EVENT = 0x8106
SA_PS_SIG_GEN_STATE_CHANGED_EVENT = 0x8107
SA_PS_BOB_CONNECT_STATE_CHANGED_EVENT = 0x8108
SA_PS_LVDS_LS_CONNECT_STATE_CHANGED_EVENT = 0x8109
SA_PS_ENV_DEVICE_STATE_CHANGED_EVENT = 0x810A
SA_PS_AF_ADJUSTMENT_PROGRESS_EVENT = 0x8a80
SA_PS_FILTER_SETTING_CHANGED_EVENT = 0x8a82
SA_PS_AF_CHANNEL_VALIDATION_PROGRESS_EVENT = 0x8a83

# Features
FEATURE_ADVANCED_TRIGGER_SYSTEM = 0
FEATURE_SIGNAL_GENERATORS = 1
FEATURE_CALCULATION_SYSTEM = 2
FEATURE_PRECISION_MODE = 3


class SA_SIDLL(CDLL):
    """
    Subclass of CDLL specific to SA_SI library, which handles error codes for
    all the functions automatically.
    """

    err_code = {
        SA_SI_ERROR_NONE: "NONE",
        SA_SI_ERROR_UNKNOWN_COMMAND: "UNKNOWN_COMMAND",
        SA_SI_ERROR_INVALID_PACKET_SIZE: "INVALID_PACKET_SIZE",
        SA_SI_ERROR_TIMEOUT: "TIMEOUT",
        SA_SI_ERROR_INVALID_PROTOCOL: "INVALID_PROTOCOL",
        SA_SI_ERROR_BUFFER_UNDERFLOW: "BUFFER_UNDERFLOW",
        SA_SI_ERROR_BUFFER_OVERFLOW: "BUFFER_OVERFLOW",
        SA_SI_ERROR_INVALID_PACKET: "INVALID_PACKET",
        SA_SI_ERROR_INVALID_STREAM_PACKET: "INVALID_STREAM_PACKET",
        SA_SI_ERROR_INVALID_PROPERTY: "INVALID_PROPERTY",
        SA_SI_ERROR_INVALID_PARAMETER: "INVALID_PARAMETER",
        SA_SI_ERROR_INVALID_CHANNEL_INDEX: "INVALID_CHANNEL_INDEX",
        SA_SI_ERROR_INVALID_DSOURCE_INDEX: "INVALID_DSOURCE_INDEX",
        SA_SI_ERROR_INVALID_DATA_TYPE: "INVALID_DATA_TYPE",
        SA_SI_ERROR_PERMISSION_DENIED: "PERMISSION_DENIED",
        SA_SI_ERROR_NO_DATA_SOURCES_ENABLED: "NO_DATA_SOURCES_ENABLED",
        SA_SI_ERROR_STREAMING_ACTIVE: "STREAMING_ACTIVE",
        SA_SI_ERROR_SOURCE_NOT_STREAMABLE: "SOURCE_NOT_STREAMABLE",
        SA_SI_ERROR_UNKNOWN_DATA_OBJECT: "UNKNOWN_DATA_OBJECT",
        SA_SI_ERROR_COMMAND_NOT_PROCESSABLE: "COMMAND_NOT_PROCESSABLE",
        SA_SI_ERROR_FEATURE_NOT_SUPPORTED: "FEATURE_NOT_SUPPORTED",
        SA_SI_ERROR_NOT_IMPLEMENTED: "NOT_IMPLEMENTED",
        SA_SI_ERROR_OTHER: "OTHER_ERROR",
        SA_PS_ERROR_REQUEST_DENIED: "REQUEST_DENIED",
        SA_PS_ERROR_INTERNAL_COMMUNICATION: "INTERNAL_COMMUNICATION_ERROR",
        SA_PS_ERROR_NO_FULL_ACCESS: "NO_FULL_ACCESS",
        SA_PS_ERROR_WORKING_DISTANCE_NOT_SET: "WORKING_DISTANCE_NOT_SET",
        SA_SI_ERROR_DEVICE_LIMIT: "DEVICE_LIMIT_REACHED",
        SA_SI_ERROR_INVALID_LOCATOR: "INVALID LOCATOR STRING",
        SA_SI_ERROR_INITIALIZATION: "INITIALIZATION_FAILED",
        SA_SI_ERROR_NOT_INITIALIZED: "NOT INITIALIZED",
        SA_SI_ERROR_COMMUNICATION: "COMMUNICATION FAILED",
        SA_SI_ERROR_QUERYBUFFER_SIZE: "INVALID_QUERYBUFFER_SIZE",
        SA_SI_ERROR_INVALID_HANDLE: "INVALID DEVICE HANDLE",
        SA_SI_ERROR_DATA_SOURCE_ENABLED: "DATA_SOURCE_ENABLED",
        SA_SI_ERROR_INVALID_STREAMBUFFER_ID: "INVALID_STREAMBUFFER_ID",
        SA_SI_ERROR_STREAM_SEQUENCE: "STREAM_SEQUENCE_ERROR",
        SA_SI_ERROR_NO_DATABUFFER_AVAILABLE: "NO_DATABUFFER_AVAILABLE",
        SA_SI_ERROR_NO_STREAMBUFFER_ACQUIRED: "NO_STREAMBUFFER_ACQUIRED",
        SA_SI_ERROR_UNEXPECTED_PACKET_RECEIVED: "UNEXPECTED_PACKET_RECEIVED",
        SA_SI_ERROR_CANCELLED: "CANCELLED",
        SA_SI_ERROR_BUFFER_INTERLEAVING: "BUFFER_INTERLEAVING",
        SA_SI_ERROR_DRIVER: "DRIVER_ERROR",
        SA_SI_ERROR_DATA_OBJECT_BUSY: "DATA_OBJECT_BUSY",
    }

    def __init__(self):
        if os.name == "nt":
            raise NotImplementedError("Windows not yet supported")
            # WinDLL.__init__(self, "libSA_si.dll")  # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libsmaractsi.so.2", RTLD_GLOBAL)

        self.SA_SI_GetFullVersionString.restype = c_char_p
        self.SA_SI_GetFullVersionString.errcheck = lambda r, f, a: r  # Always happy
        self.SA_SI_EPK.restype = c_uint32
        self.SA_SI_EPK.errcheck = lambda r, f, a: r  # Always happy

    def __getitem__(self, name):
        try:
            func = super(SA_SIDLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        if func.errcheck is None:
            func.errcheck = self.sp_errcheck
        return func

    @staticmethod
    def sp_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result != SA_SI_ERROR_NONE:
            raise SA_SIError(result, "Call to %s() failed with error 0x%x: %s" %
                             (func.__name__, result, SA_SIDLL.err_code.get(result, "")))

        return result


class SA_SIError(IOError):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(SA_SIError, self).__init__(errno, strerror, *args, **kwargs)


class SA_SI_EventData(Union):
    """
    SA_SI event data is stored as this type of union (A C union used by DLL)
    """
    _fields_ = [
        ("error", c_uint32),
        ("bufferId", c_uint32),
        ("devEventParameter", c_int32),
        ("unused", c_int8 * 24),
         ]


class SA_SI_Event(Structure):
    """
    SA_SI Event structure (C struct used by DLL)
    """
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", c_uint32),
        ("u", SA_SI_EventData),
        ]


class Picoscale(model.HwComponent):
    """
    A driver for a SmarAct Picoscale interferometer system.

    The device does not contain any actuators. Its main functionality is to provide the position for each
    of its channels through a VA.

    Attributes
    ==========
    .position (VA: str --> float): position in m for each channel
    .referenced (VA: str --> bool): indicates which channels have been referenced

    Functions
    =========
    .reference: performs adjustment routine required for precise position values
    .scan (static method): returns list of all available Picoscale controllers
    (+ wrapper functions for Picoscale API)
    """

    def __init__(self, name, role, locator, channels, ref_on_init=False, precision_mode=0, *args, **kwargs):
        """
        name: (str)
        role: (str)
        locator: (str) Use "fake" for a simulator.
            For a real device, Picoscale controllers with USB interface can be addressed with the
            following locator syntax:
                usb:ix:<id>
            where <id> is the first part of a USB devices serial number which
            is printed on the Picoscale controller.
            If the controller has a TCP/IP connection, use:
                network:<ip>:<port>
            The device can also be addressed by its serial number:
                usb:sn:<serial_number>
                network:sn:<serial_number>
        channels: (str --> int) dictionary mapping channel names to channel numbers
        ref_on_init: (True, False, "always", "if necessary", "never")
            * "always": Run referencing procedure every time the driver is initialized, no matter the state it was in.
            * True / "if necessary": If the channels are already in a valid state (i.e. the device was not turned off
                since the last referencing), don't reference again. Only reference if the channels are not valid (i.e.
                the device was turned off after the last referencing). In any case, the device can be used after
                the referencing procedure is complete. It is recommended to reference the system frequently though.
                In case the system has not been power cycled in a long time, the referencing parameters might become
                outdated and the reported position values might not be accurate.
            * False / "never": Never reference. This means that the device might not be able to produce position data,
                if it was not previously referenced.
        precision_mode: (0 <= int <= 5) strength of digital lowpass filter, a higher level corresponds to higher
            precision, but lower velocity. Not available on all systems.
        """
        model.HwComponent.__init__(self, name, role, *args, **kwargs)

        # Connection
        if locator == "fake":
            self.core = FakePicoscale_DLL()
        else:
            self.core = SA_SIDLL()
        self._locator = locator
        self._id = self._openConnection(locator)

        # Device information
        devname = self.GetProperty_s(SA_SI_DEVICE_NAME_PROP)
        sn = self.GetProperty_s(SA_SI_DEVICE_SERIAL_NUMBER_PROP)
        num_ch = self.GetNumberOfChannels()
        self._hwVersion = "SmarAct %s (s/n %s) with %s channels." % (devname, sn, num_ch,)
        self._swVersion = self.GetFullVersionString()
        logging.debug("Using Picoscale library version %s to connect to %s. ", self._swVersion, self._hwVersion)

        # Check channels
        if not channels:
            raise ValueError("Needs at least 1 axis.")
        for num in channels.values():
            if not 0 <= num <= num_ch - 1:
                raise ValueError("Channel %s not available, needs to be 0 < channel < %s." % (num, num_ch - 1))
        self._channels = channels  # channel name -> channel number used by controller
        self._axes = {ch: model.Axis(range=(-10, 10), unit="m") for ch in self._channels}  # range is arbitrarily large

        # Device setup
        self.EnableFullAccess()
        # If referencing is still running, cancel it
        self.core.SA_SI_Cancel(self._id)
        # Reset referencing state if necessary (in case referencing has been stopped improperly)
        state = self.GetProperty_i32(SA_PS_AF_ADJUSTMENT_STATE_PROP)
        if state != SA_PS_ADJUSTMENT_STATE_DISABLED:
            self.SetProperty_i32(SA_PS_AF_ADJUSTMENT_STATE_PROP, SA_PS_ADJUSTMENT_STATE_DISABLED)
        try:
            self._load_configuration()
        except SA_SIError as ex:
            raise ValueError("Failed to load configuration. This might indicate that the device is not "
                             "configured properly. Error: %s." % ex)

        # Log configuration
        flen = self.GetFiberLength()
        ext_flen = self.GetExtensionFiberLength()
        wdist_min, wdist_max = self.GetWorkingDistance()
        logging.debug("Picoscale configuration: fiber length %s, extension fiber length %s, "
                      "working distance [%s, %s].", flen, ext_flen, wdist_min, wdist_max)

        # Position polling thread
        # Will be started later, either after referencing, or in __init__ if we're not referencing on startup
        # During referencing, ._polling_thread will be stopped and set back to None.
        self.position = model.VigilantAttribute({}, getter=self._getPosition, readonly=True)
        self._polling_thread = None

        # Precision mode
        # The precision mode is a special feature that needs to be purchased separately, so it is not available
        # by default.
        # TODO: this functionality has not yet been tested
        try:
            self.SetPrecisionMode(precision_mode)
        except SA_SIError as ex:
            if ex.errno == SA_SI_ERROR_INVALID_PROPERTY:
                if precision_mode > 0:
                    raise ValueError("Precision mode not available.")
                else:
                    logging.debug("Precision mode not available.")
            else:
                raise

        # State: starting until first referencing/validation procedure is done
        self.state._set_value(model.ST_STARTING, force_write=True)

        # Referencing
        self._executor = CancellableThreadPoolExecutor(1)  # one task at a time
        channel_ref = {ch: self.IsValid(num) for ch, num in self._channels.items()}
        self.referenced = model.VigilantAttribute(channel_ref, readonly=True)  # VA dict str(channel) -> bool

        all_channels_enabled = all(channel_ref.values())
        f = None
        if ref_on_init == "always":
            f = self.reference()
        elif ref_on_init in (True, "if necessary"):
            if not all_channels_enabled:
                f = self.reference()
            else:
                logging.debug("System already referenced, not referencing again.")
        elif ref_on_init in (False, "never"):
            if not all_channels_enabled:
                logging.warning("Picoscale is not referenced. The device cannot be used until the referencing "
                                "procedure is called.")
        else:
            raise ValueError("Invalid parameter %s for ref_on_init." % ref_on_init)
        # These procedure can take a while (up to 10 minutes), especially after the power of the system
        # has just been turned on. Therefore, we don't wait until the procedure is complete.
        # Starting from standby is generally much faster.
        if f:
            f.add_done_callback(self._on_referenced)
        else:
            self.state._set_value(model.ST_RUNNING, force_write=True)
            self._polling_thread = util.RepeatingTimer(1, self._updatePosition, "Position polling")
            self._polling_thread.start()

    def terminate(self):
        # should be safe to close the device multiple times if terminate is called more than once.
        if self._polling_thread:
            self._polling_thread.cancel()
        if self._executor:
            self._executor.cancel()
            self.core.SA_SI_Close(self._id)
            self._executor = None
        super(Picoscale, self).terminate()

    @roattribute
    def axes(self):
        """ dict str->Axis: name of each axis available -> their definition."""
        return self._axes

    @staticmethod
    def scan():
        """
        Find all available Picoscale controllers.
        returns: (list of 2-tuple) name, args
        """
        core = SA_SIDLL()
        b_len = 1024
        buf = create_string_buffer(b_len)
        core.SA_SI_EnumerateDevices(c_char_p(b""), buf, byref(c_size_t(b_len)))
        # sometimes the same locator is returned multiple times, convert to set for unique values
        locators = set(buf.value.decode('latin1').split("\n"))
        devices = []
        for locator in locators:
            try:
                dev = _PicoscaleScanned(locator)
            except model.HwError:
                logging.error("Couldn't open device with locator %s." % locator)
                continue

            # Add device
            devices.append((dev.devname, {"locator": locator, "channels": dev.channels}))

        return devices

    def _openConnection(self, locator):
        """
        Open usb/ethernet connection to device.
        locator: (str) Use "fake" for a simulator.
            For a real device, Picoscale controllers with USB interface can be addressed with the
            following locator syntax:
                usb:ix:<id>
            where <id> is the first part of a USB devices serial number which
            is printed on the Picoscale controller.
            If the controller has a TCP/IP connection, use:
                network:<ip>:<port>
            The device can also be addressed by its serial number:
                usb:sn:<serial_number>
                network:sn:<serial_number>
        returns: (int) device handle for API functions
        """
        logging.debug("Connecting to locator %s.", locator)
        id = c_uint32(0)
        try:
            self.core.SA_SI_Open(byref(id), c_char_p(locator.encode("ascii")), c_char_p(b""))
        except SA_SIError as ex:
            # After a cold start of the device, an initialization error is raised the first time
            # we try to establish a connection. It always works on the second trial.
            logging.debug("Failed to connect to device with ID %d, trying again." % id.value)
            try:
                self.core.SA_SI_Open(byref(id), c_char_p(locator.encode("ascii")), c_char_p(b""))
            except SA_SIError as ex:
                if ex.errno == SA_SI_ERROR_INITIALIZATION:
                    raise model.HwError("Failed to find device, check it is connected and turned on.")
                raise
        logging.debug("Connected to Picoscale Controller with ID %d.", id.value)
        return id

    # API functions
    def GetFiberLength(self):
        """
        returns: (float) fiber length in mm
        """
        return self.GetProperty_f64(SA_PS_SYS_FIBERLENGTH_HEAD_PROP)

    def GetExtensionFiberLength(self):
        """
        returns: (float) extension fiber length in mm
        """
        return self.GetProperty_f64(SA_PS_SYS_FIBERLENGTH_EXTENSION_PROP)

    def GetWorkingDistance(self):
        """
        returns: (float, float) minimum and maximum working distance in mm
        """
        min_dist = self.GetProperty_f64(SA_PS_SYS_WORKING_DISTANCE_MIN_PROP)
        max_dist = self.GetProperty_f64(SA_PS_SYS_WORKING_DISTANCE_MAX_PROP)
        return min_dist, max_dist

    def GetAdjustmentState(self):
        """
        returns: (SA_PS_ADJUSTMENT_STATE_DISABLED, SA_PS_ADJUSTMENT_STATE_MANUAL_ADJUST,
        SA_PS_ADJUSTMENT_STATE_AUTO_ADJUST)
        """
        return self.GetProperty_i32(SA_PS_AF_ADJUSTMENT_STATE_PROP)

    def GetNumberOfChannels(self):
        """
        returns: (int) number of channels offered by device
        """
        return self.GetProperty_i32(SA_SI_NUMBER_OF_CHANNELS_PROP)

    def IsStable(self):
        """
        Indicates whether the system is stable and ready to produce position data.
        returns: (bool)
        """
        return bool(self.GetProperty_i32(SA_PS_SYS_IS_STABLE_PROP))

    def IsValid(self, ch):
        """
        Indicates whether a channel is ready to produce position data.
        A channel should be valid after referencing or after channel validation.
        returns: (bool)
        """
        return bool(self.GetProperty_i32(SA_PS_CH_IS_VALID_PROP, idx0=ch))

    def IsFeatureEnabled(self, feature):
        """
        Check if hardware has extra feature which is not available on the standard system.
        feature: (int) must be one of
            FEATURE_ADVANCED_TRIGGER_SYSTEM
            FEATURE_SIGNAL_GENERATORS
            FEATURE_CALCULATION_SYSTEM
            FEATURE_PRECISION_MODE
        returns: (bool)
        """
        return bool(self.GetProperty_i32(SA_PS_SYS_FEATURE_TIME_PROP, idx0=feature))

    def SetPrecisionMode(self, precision_mode):
        """
        Configure precision mode to desired level. This only works if the precision_mode feature has been
        purchased.
        precision_mode: (0 <= int <= 5) higher level corresponds to higher precision, 0 means feature is disabled.
        """
        self.SetProperty_f64(SA_PS_SYS_PRECISION_MODE_PROP, precision_mode)

    def GetFullVersionString(self):
        """
        Returns the version of the library.
        returns: (str)
        """
        ver = self.core.SA_SI_GetFullVersionString()
        return ver.decode("latin1")

    def EnableFullAccess(self):
        """
        Get full access to all device functionality (e.g. for adjustment procedure, loading properties, etc).
        """
        self.SetProperty_i32(SA_PS_SYS_FULL_ACCESS_CONNECTION_PROP, SA_SI_ENABLED)

    def EnableEventNotification(self, event_type):
        """
        Enables notifications for an event type.
        event_type: (int) SA_SIDLL event type
        """
        encoded_key = SA_SI_EVENT_NOTIFICATION_ENABLED_PROP << 16 | event_type
        self.core.SA_SI_SetProperty_i32(self._id, c_uint32(encoded_key), c_int32(SA_SI_ENABLED))

    def DisableEventNotification(self, event_type):
        """
        Disables notifications for an event type.
        event_type: (int) SA_SIDLL event type
        """
        encoded_key = SA_SI_EVENT_NOTIFICATION_ENABLED_PROP << 16 | event_type
        self.core.SA_SI_SetProperty_i32(self._id, c_uint32(encoded_key), c_int32(SA_SI_DISABLED))

    # Functions to set the property values in the controller, categorized by data type
    def SetProperty_f64(self, property_key, value, idx0=0, idx1=0):
        """
        property_key (int32): property key symbol
        value (double): value to set
        idx0 (int): index value, meaning depends on property key
        idx1 (int): index value, meaning depends on property key
        """
        encoded_key = self.core.SA_SI_EPK(property_key, idx0, idx1)
        self.core.SA_SI_SetProperty_f64(self._id, c_uint32(encoded_key), c_double(value))

    def SetProperty_i32(self, property_key, value, idx0=0, idx1=0):
        """
        property_key (int32): property key symbol
        value (int32): value to set
        idx0 (int): index value, meaning depends on property key
        idx1 (int): index value, meaning depends on property key
        """
        encoded_key = self.core.SA_SI_EPK(property_key, idx0, idx1)
        self.core.SA_SI_SetProperty_i32(self._id, c_uint32(encoded_key), c_int32(value))

    def SetProperty_i64(self, property_key, value, idx0=0, idx1=0):
        """
        property_key (int64): property key symbol
        value (int64): value to set
        idx0 (int): index value, meaning depends on property key
        idx1 (int): index value, meaning depends on property key
        """
        encoded_key = self.core.SA_SI_EPK(property_key, idx0, idx1)
        self.core.SA_SI_SetProperty_i64(self._id, c_uint32(encoded_key), c_int64(value))

    def GetProperty_f64(self, property_key, idx0=0, idx1=0):
        """
        property_key (int32): property key symbol
        idx0 (int): index value, meaning depends on property key
        idx1 (int): index value, meaning depends on property key
        returns (float) the value
        """
        ret_val = c_double()
        encoded_key = self.core.SA_SI_EPK(property_key, idx0, idx1)
        self.core.SA_SI_GetProperty_f64(self._id, c_uint32(encoded_key), byref(ret_val), c_size_t(0))
        return ret_val.value

    def GetProperty_i32(self, property_key, idx0=0, idx1=0):
        """
        property_key (int32): property key symbol
        idx0 (int): index value, meaning depends on property key
        idx1 (int): index value, meaning depends on property key
        returns (int) the value
        """
        ret_val = c_int32()
        encoded_key = self.core.SA_SI_EPK(property_key, idx0, idx1)
        self.core.SA_SI_GetProperty_i32(self._id, c_uint32(encoded_key), byref(ret_val), c_size_t(0))
        return ret_val.value

    def GetProperty_i64(self, property_key, idx0=0, idx1=0):
        """
        property_key (int64): property key symbol
        idx0 (int): index value, meaning depends on property key
        idx1 (int): index value, meaning depends on property key
        returns (int) the value
        """
        ret_val = c_int64()
        encoded_key = self.core.SA_SI_EPK(property_key, idx0, idx1)
        self.core.SA_SI_GetProperty_i64(self._id, c_uint32(encoded_key), byref(ret_val), c_size_t(0))
        return ret_val.value

    def GetProperty_s(self, property_key, idx0=0, idx1=0):
        """
        property_key (int32): property key symbol
        idx0 (int): index value, meaning depends on property key
        idx1 (int): index value, meaning depends on property key
        returns (str): the value
        """
        ret_val = create_string_buffer(SA_SI_STRING_MAX_LENGTH)
        encoded_key = self.core.SA_SI_EPK(property_key, idx0, idx1)
        self.core.SA_SI_GetProperty_s(self._id, c_uint32(encoded_key), ret_val, byref(c_size_t(len(ret_val))))
        return ret_val.value.decode("latin1")

    def GetValue_f64(self, channel, data_source_idx):
        """
        property_key (int32): property key symbol
        data_source_idx:
        returns (float) the value
        """
        ret_val = c_double()
        self.core.SA_SI_GetValue_f64(self._id, channel, data_source_idx, byref(ret_val))
        return ret_val.value

    def WaitForEvent(self, timeout=float("inf")):
        """
        Blocks until device reports an event.
        timeout (int > 0): timeout in seconds
        returns (SA_SI_Event): event
        raises
            TimeoutError: timeout exceeded
            CancelledError: function was cancelled by SA_SI_Cancel
            SA_SI_Error: something went wrong
        """
        if timeout == float("inf"):
            t = SA_SI_TIMEOUT_INFINITE
        else:
            t = c_uint(int(timeout * 1000))  # SA_SI_WaitForEvent accepts timeout in ms

        ev = SA_SI_Event()
        try:
            self.core.SA_SI_WaitForEvent(self._id, byref(ev), t)
        except SA_SIError as ex:
            if ex.errno == SA_SI_ERROR_TIMEOUT:
                raise TimeoutError("Picoscale reported timeout error.")
            elif ex.errno == SA_SI_ERROR_CANCELLED:
                raise CancelledError("WaitForEvent was cancelled.")
            raise

        return ev

    def _load_configuration(self):
        """
        Load the configuration of the device. The loaded parameters are
        * fiber length
        * extension fiber length
        * number of active channels
        * sensor head types
        * minimum and maximum working distance
        These configuration parameters need to be set during the installation of the system.
        Additionally, the parameters from the previously saved referencing procedure are loaded.
        This function is blocking.
        """
        # Loading the configuration will cause the state to become temporarily unstable. Once the process is
        # finished, the state will be stable again.
        self.EnableEventNotification(SA_PS_STABLE_STATE_CHANGED_EVENT)
        self.SetProperty_i32(SA_PS_AF_ADJUSTMENT_RESULT_LOAD_PROP, SA_SI_ENABLED)
        self._wait_for_progress_event(SA_SI_ENABLED, timeout=600)  # this can take a long time
        self.DisableEventNotification(SA_PS_STABLE_STATE_CHANGED_EVENT)

    def _save_configuration(self):
        """
        Saves the configuration parameters (cf _load_configuration) and internal parameters of the
        referencing procedure. This function is blocking.
        """
        self.SetProperty_i32(SA_PS_AF_ADJUSTMENT_RESULT_SAVE_PROP, SA_SI_ENABLED)
        # There is no documentation on loading/saving the configuration in the manual, even though
        # the functions are available in the header file. It is not clear how much time saving the
        # parameters takes. It can be assumed that it's fast --> wait 1 s.
        time.sleep(1)

    def _wait_for_progress_event(self, end_state, timeout=float("inf")):
        """
        Blocks until progress event is triggered or timeout.
        It is assumed that only one event type is enabled. The function will wait until the
        .devEventParameter contains the end_state. This can mean different things depending on the event.
        end_state (int): state of event variable to wait for
        timeout (float): maximum time to wait in s. If inf, it will wait forever.
        raises
            TimeoutError: timeout exceeded
            CancelledError: function was cancelled by SA_SI_Cancel
            SA_SI_Error: other problem reported by device
        """
        if timeout == float("inf"):
            tend = None
        else:
            tend = time.time() + timeout

        state = None
        while state != end_state:
            if tend is not None:
                t = tend - time.time()
                if t < 0:
                    raise TimeoutError("Timeout limit of %s s exceeded." % timeout)
            else:
                t = float("inf")

            ev = self.WaitForEvent(t)  # raise TimeOut/CancelledError by itself
            if ev.type == SA_PS_AF_ADJUSTMENT_PROGRESS_EVENT:
                # lowest 16-bits is the adjustment state
                state = ev.devEventParameter & 0xffff
            elif ev.type == SA_PS_STABLE_STATE_CHANGED_EVENT:
                # new state, 0 means unstable, 1 means stable
                state = ev.devEventParameter
            elif ev.type == SA_SI_STREAM_ABORTED_EVENT:
                if ev.error == SA_SI_ERROR_CANCELLED:
                    raise CancelledError()
                else:
                    raise SA_SIError(ev.error, self.core.err_code[ev.error])
            else:
                logging.debug("Skipped event 0x%x" % ev.type)

    @isasync
    def reference(self, _=None):
        """
        This is not a "normal" referencing procedure since the hardware doesn't have any actuators. Instead,
        it performs an internal adjustment routine which is required to get accurate position values.
        """
        f = self._createReferenceFuture()
        self._executor.submitf(f, self._doReference, f)
        return f

    def _doReference(self, future):
        """
        Actually runs the referencing code.
        future (Future): the future it handles
        raise:
            IOError: if referencing failed due to hardware
            CancelledError if was cancelled
        """
        try:
            # TODO: can we leave the position polling thread on? It does not seem
            # to interfere with the referencing procedure.
            if self._polling_thread:
                self._polling_thread.cancel()
                self._polling_thread = None

            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()
                # Reset reference so that if it fails, it states the axes are not referenced (anymore)
                self.referenced._value = {a: False for a in self._channels.keys()}
                logging.debug("Starting referencing.")
                # Cannot go immediately to automatic adjustment --> first switch to manual adjustment
                self.EnableEventNotification(SA_PS_AF_ADJUSTMENT_PROGRESS_EVENT)
                self.SetProperty_i32(SA_PS_AF_ADJUSTMENT_STATE_PROP,
                                     SA_PS_ADJUSTMENT_STATE_MANUAL_ADJUST)
            self._wait_for_progress_event(SA_PS_ADJUSTMENT_STATE_MANUAL_ADJUST, timeout=600)

            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()
                # Activate working distance
                self.SetProperty_i32(SA_PS_SYS_WORKING_DISTANCE_ACTIVATE_PROP,
                                     SA_PS_WORKING_DISTANCE_SHRINK_MODE_LEFT_RIGHT)
            # attribute is write-only and there is no corresponding event type (yet some waiting time is necessary)
            # --> wait 1 s for command to be processed
            if future._must_stop.wait(1):
                raise CancelledError()

            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()
                # Switch to automatic adjustment
                self.SetProperty_i32(SA_PS_AF_ADJUSTMENT_STATE_PROP,
                                     SA_PS_ADJUSTMENT_STATE_AUTO_ADJUST)
            self._wait_for_progress_event(SA_PS_ADJUSTMENT_STATE_DISABLED, timeout=600)  # state will be DISABLED when done
            logging.debug("Finished referencing.")

            # We could save the referencing parameters to memory here. This could be useful for the validation,
            # because it initializes the system by loading the stored referencing parameters. However, we don't
            # currently use validation, so there is no need to store the values every time we reference.
        except SA_SIError as ex:
            # This occurs if a stop command interrupts referencing
            if ex.errno == SA_SI_ERROR_CANCELLED:
                logging.info("Referencing stopped: %s", ex)
                raise CancelledError()
            elif future._must_stop.is_set():
                raise CancelledError()
            else:
                logging.error("Referencing failed: %s", ex)
                raise
        except CancelledError:
            logging.debug("Referencing cancelled.")
            raise  # No fuss, pass it as-is
        except Exception:
            logging.exception("Referencing failure.")
            raise
        finally:
            # If the state of the device is stable and all channels are valid, the referencing procedure
            # succeeded. Typically, all channels become valid at the same time during the reference procedure.
            # We still check all of them individually to be sure.
            if self.IsStable():
                for ch, num in self._channels.items():
                    self.referenced._value[ch] = self.IsValid(num)
                # Start polling thread
                self._polling_thread = util.RepeatingTimer(1, self._updatePosition, "Position polling")
                self._polling_thread.start()
                self._updatePosition()
            # We only notify after updating the position so that when a listener
            # receives updates both values are already updated.
            self.referenced.notify(self.referenced.value)

            # The adjustment state should be "disabled" after automatic adjustment. If the referencing
            # procedure was stopped prematurely, right after it was set to "manual", it might be in the
            # wrong state --> make sure it's set to disabled here.
            if self.GetAdjustmentState() != SA_PS_ADJUSTMENT_STATE_DISABLED:
                self.SetProperty_i32(SA_PS_AF_ADJUSTMENT_STATE_PROP,
                                     SA_PS_ADJUSTMENT_STATE_DISABLED)
                self._wait_for_progress_event(SA_PS_ADJUSTMENT_STATE_DISABLED, timeout=600)  # state will be DISABLED when done
            self.DisableEventNotification(SA_PS_AF_ADJUSTMENT_PROGRESS_EVENT)

    # The validation procedure is currently not used, we either use full referencing or nothing at all
    @isasync
    def _validate(self):
        """
        Short "referencing" procedure. On startup, either referencing or validation needs to be performed.
        Validation uses the referencing parameters stored in memory. It is in general much faster than
        referencing. However, it is recommended to do the full referencing procedure on startup for
        more accurate results.
        """
        f = self._createReferenceFuture()
        self._executor.submitf(f, self._doValidation, f)
        return f

    def _doValidation(self, future):
        """
        Actually runs the validation code.
        After the device was just turned on, it might take ~5 minutes, otherwise it is very fast.
        future (Future): the future it handles
        raise:
            IOError: if validation failed due to hardware
            CancelledError if was cancelled
        """
        try:
            # Don't poll positions during the referencing
            if self._polling_thread:
                self._polling_thread.cancel()
                self._polling_thread = None

            with future._moving_lock:
                if future._must_stop.is_set():
                    raise CancelledError()
                # There is also a channel validation progress event, but it's triggered too early. We need
                # to wait until the state becomes stable again before continuing.
                self.EnableEventNotification(SA_PS_STABLE_STATE_CHANGED_EVENT)
                self.SetProperty_i32(SA_PS_AF_CHANNEL_VALIDATION_STATE_PROP, SA_SI_ENABLED)
            self._wait_for_progress_event(SA_SI_ENABLED, timeout=600)
            self.DisableEventNotification(SA_PS_STABLE_STATE_CHANGED_EVENT)

            # Enable channels
            for ch in self._channels.values():
                with future._moving_lock:
                    if future._must_stop.is_set():
                        raise CancelledError()
                    self.SetProperty_i32(SA_PS_CH_ENABLED_PROP, SA_SI_ENABLED, idx0=ch)
                # Channel enabled event not triggered if it was already enabled
                # --> poll SA_PS_CH_ENABLED_PROP attribute instead
                while self.GetProperty_i32(SA_PS_CH_ENABLED_PROP, idx0=ch) != SA_SI_ENABLED:
                    if future._must_stop.is_set():
                        raise CancelledError()
                    time.sleep(0.01)
        except SA_SIError as ex:
            # This occurs if a stop command interrupts referencing
            if ex.errno == SA_SI_ERROR_CANCELLED:
                logging.info("Validation stopped: %s", ex)
                raise CancelledError()
            elif future._must_stop.is_set():
                raise CancelledError()
            else:
                logging.error("Validation failed: %s", ex)
                raise
        except CancelledError:
            logging.debug("Validation cancelled.")
            raise  # No fuss, pass it as-is
        except Exception:
            logging.exception("Validation failure.")
            raise
        finally:
            if not self.IsStable():
                raise IOError("Validation failed, device not stable.")
            for ch, num in self._channels.items():
                if not self.IsValid(num):
                    raise IOError("Failed to validate channel %s" % num)

            # Start polling thread
            self._polling_thread = util.RepeatingTimer(1, self._updatePosition, "Position polling")
            self._polling_thread.start()
            self._updatePosition()

    def _on_referenced(self, future):
        """
        Callback function for referencing/validation future in __init__.
        """
        try:
            future.result()
            self.state._set_value(model.ST_RUNNING, force_write=True)
        except Exception as e:
            self.state._set_value(e, force_write=True)
            logging.exception(e)

    def _updatePosition(self):
        """
        Updates the position VA.
        """
        pos = self._getPosition()
        self.position._set_value(pos, force_write=True)
        logging.debug("Updated position to %s.", pos)

    def _getPosition(self):
        """
        Getter for .position VA. Requests position from device.
        returns: dict (str --> float)
        """
        # Polling thread is running all the time except during referencing
        if self._polling_thread is None:
            logging.warning("Cannot report position, device needs to be referenced first.")
            return {}
        pos = {}
        for name, num in self._channels.items():
            pos[name] = self.GetValue_f64(num, 0)  # position value is at index 0
        return pos

    def _createReferenceFuture(self):
        """
        returns: (CancellableFuture)
        """
        f = CancellableFuture()
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._moving_lock = threading.Lock()  # taken while moving
        f.task_canceller = self._cancelReference
        return f

    def _cancelReference(self, future):
        """
        Cancels the referencing procedure.
        future (Future): the future to stop
        """
        logging.debug("Cancelling referencing...")
        future._must_stop.set()  # tell the thread taking care of the referencing it's over

        # Synchronise with the ending of the future
        with future._moving_lock:
            self.core.SA_SI_Cancel(self._id)

        return True


class _PicoscaleScanned(Picoscale):
    """
    Basic controller for Picoscale.scan() function.

    Attributes
    ==========
    .name: (str) name of the device as reported by the hardware
    .channels: (str --> int) dictionary mapping (generic) channel names to channel numbers
    """

    def __init__(self, locator):
        """
        locator (str): locator string, see Picoscale class
        """
        # Connection
        logging.debug("Connecting to locator %s.", locator)
        self._locator = locator
        if locator == "fake":
            self.core = FakePicoscale_DLL()
        else:
            self.core = SA_SIDLL()
        self._id = self._openConnection(locator)

        # Get device name
        self.devname = self.GetProperty_s(SA_SI_DEVICE_NAME_PROP)

        # Get channels
        num_ch = self.GetNumberOfChannels()
        self.channels = {}
        for ch in range(num_ch):
            self.channels["ch%s" % ch] = ch


class FakePicoscale_DLL(object):
    """
    Simulated Picoscale DLL.
    """

    def __init__(self):
        self.properties = {
            SA_SI_DEVICE_NAME_PROP: b"Simulated Picoscale",
            SA_SI_DEVICE_SERIAL_NUMBER_PROP: b"1234",
            SA_PS_SYS_FULL_ACCESS_CONNECTION_PROP: 0,
            SA_SI_EVENT_NOTIFICATION_ENABLED_PROP: 0,
            SA_PS_AF_ADJUSTMENT_RESULT_LOAD_PROP: 0,
            SA_PS_AF_CHANNEL_VALIDATION_STATE_PROP: 0,
            SA_PS_CH_ENABLED_PROP: 0,
            SA_PS_SYS_FIBERLENGTH_HEAD_PROP: 1500,
            SA_PS_SYS_FIBERLENGTH_EXTENSION_PROP: 0,
            SA_PS_SYS_WORKING_DISTANCE_MAX_PROP: 80,
            SA_PS_SYS_WORKING_DISTANCE_MIN_PROP: 40,
            SA_PS_AF_ADJUSTMENT_STATE_PROP: 0,
            SA_SI_NUMBER_OF_CHANNELS_PROP: 3,
            SA_PS_SYS_IS_STABLE_PROP: 1,
            SA_PS_CH_IS_VALID_PROP: 0,
            SA_PS_SYS_WORKING_DISTANCE_ACTIVATE_PROP: 0,
            SA_PS_SYS_PRECISION_MODE_PROP: 0,
        }

        self.positions = [10e-6, 20e-6, 30e-6]

        self.active_property = None  # property which is requested in _wait_for_progress_event function
        self.active_event = None  # event to wait for
        self.cancel_referencing = False  # let referencing thread know about cancellation
        self.cancel_event = False  # let _wait_for_progress_event function know about cancellation
        self.waiting = None  # set to True after waiting for first event (otherwise cancel function in init of Picoscale causes problems)

        self.event_to_property = {
            SA_PS_AF_ADJUSTMENT_PROGRESS_EVENT: SA_PS_AF_ADJUSTMENT_STATE_PROP,
            SA_PS_STABLE_STATE_CHANGED_EVENT: SA_PS_SYS_IS_STABLE_PROP,
                                  }

        self.executor = CancellableThreadPoolExecutor(1)  # one task at a time

    def SA_SI_Open(self, id, locator, options):
        logging.debug("Starting Picoscale Simulator.")
        return 0

    def SA_SI_Close(self, id):
        logging.debug("Closing Picoscale Simulator.")
        return 0

    def SA_SI_Cancel(self, id):
        logging.debug("Cancelling.")
        if self.waiting:
            self.cancel_referencing = True
            self.cancel_event = True

    def SA_SI_EPK(self, key, idx0=0, idx1=0):
        return key << 16 | idx0 << 8 | idx1

    def SA_SI_GetFullVersionString(self):
        return b"1.2.3.123"

    def SA_SI_WaitForEvent(self, handle, event, timeout):
        ev = _deref(event, SA_SI_Event)
        time.sleep(0.01)  # make sure we don't use the CPU at full throttle during simulated calibration
        ev.type = self.active_event
        ev.devEventParameter = self.properties[self.active_property]
        if self.cancel_event:
            raise SA_SIError(SA_SI_ERROR_CANCELLED, "CANCELLED")
        return SA_SI_ERROR_NONE

    def SA_SI_SetProperty_f64(self, handle, property_key, value):
        shifted_key = property_key.value >> 16
        if shifted_key not in self.properties:
            raise SA_SIError(SA_SI_ERROR_INVALID_PARAMETER, "INVALID_PARAMETER")
        self.properties[shifted_key] = value.value

    def SA_SI_SetProperty_i32(self, handle, property_key, value):
        shifted_key = property_key.value >> 16
        if shifted_key not in self.properties:
            raise SA_SIError(SA_SI_ERROR_INVALID_PARAMETER, "INVALID_PARAMETER")
        self.properties[shifted_key] = value.value

        # Change active property (for SA_SI_WaitForEvent function)
        if shifted_key == SA_SI_EVENT_NOTIFICATION_ENABLED_PROP:
            event = property_key.value & 0xffff
            self.waiting = True
            self.active_event = event
            self.active_property = self.event_to_property[event]

        # Create thread for adjustment
        if shifted_key == SA_PS_AF_ADJUSTMENT_STATE_PROP:
            # System not stable while adjusting
            self.properties[SA_PS_SYS_IS_STABLE_PROP] = 0
            self.cancel_event = False
            # Reset cancelling flag
            self.cancel_referencing = False
            self.executor.submit(self._adjustment_thread, value.value)

    def SA_SI_SetProperty_i64(self, handle, property_key, value):
        shifted_key = property_key.value >> 16
        if shifted_key not in self.properties:
            raise SA_SIError(SA_SI_ERROR_INVALID_PARAMETER, "INVALID_PARAMETER")
        self.properties[shifted_key] = value.value

    def SA_SI_GetProperty_f64(self, handle, property_key, p_val, size):
        shifted_key = property_key.value >> 16
        if shifted_key not in self.properties:
            raise SA_SIError(SA_SI_ERROR_INVALID_PARAMETER, "INVALID_PARAMETER")
        val = _deref(p_val, c_double)
        val.value = self.properties[shifted_key]

    def SA_SI_GetProperty_i32(self, handle, property_key, p_val, size):
        shifted_key = property_key.value >> 16
        if shifted_key not in self.properties:
            raise SA_SIError(SA_SI_ERROR_INVALID_PARAMETER, "INVALID_PARAMETER")
        val = _deref(p_val, c_int32)
        val.value = self.properties[shifted_key]

    def SA_SI_GetProperty_i64(self, handle, property_key, p_val, size):
        shifted_key = property_key.value >> 16
        if shifted_key not in self.properties:
            raise SA_SIError(SA_SI_ERROR_INVALID_PARAMETER, "INVALID_PARAMETER")
        val = _deref(p_val, c_int64)
        val.value = self.properties[shifted_key]

    def SA_SI_GetProperty_s(self, handle, property_key, val, size):
        shifted_key = property_key.value >> 16
        if shifted_key not in self.properties:
            raise SA_SIError(SA_SI_ERROR_INVALID_PARAMETER, "INVALID_PARAMETER")
        val.value = self.properties[shifted_key]

    def SA_SI_GetValue_f64(self, handle, channel, data_source_idx, val):
        # Different positions for different channels
        val = _deref(val, c_double)
        if channel == 0:
            val.value = self.positions[0]
        elif channel == 1:
            val.value = self.positions[1]
        else:
            val.value = self.positions[2]

    def _adjustment_thread(self, level):
        # Different behaviour depending on adjustment level
        if level == SA_PS_ADJUSTMENT_STATE_AUTO_ADJUST:
            # after autoadjust, state is set to 0
            finished_param = 0
            wait_time = 6  # auto adjustment takes a bit longer
        else:
            # otherwise return the state that was requested
            finished_param = level
            wait_time = 1

        # Wait
        startt = time.time()
        while time.time() < startt + wait_time:
            if self.cancel_referencing:
                break
            time.sleep(0.1)

        # Set parameters
        if not self.cancel_referencing:
            self.properties[SA_PS_SYS_IS_STABLE_PROP] = 1  # system stable
            self.properties[SA_PS_CH_IS_VALID_PROP] = 1  # channel is referenced
            self.properties[SA_PS_AF_ADJUSTMENT_STATE_PROP] = finished_param
        else:
            logging.debug("Picoscale sim adjustment cancelled")
