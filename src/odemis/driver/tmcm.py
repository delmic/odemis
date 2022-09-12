# -*- coding: utf-8 -*-
'''
Created on 20 May 2014

@author: Éric Piel, Philip Winkler

Copyright © 2014-2020 Éric Piel, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Driver for Trinamic motion controller devices with TMCL firmware.
# Currently TMCM-3110 (3 axis stepper controller) and TMCM-6110 are supported.
# The documentation is available on trinamic.com (TMCM-3110_TMCL_firmware_manual.pdf,
# and TMCL_reference.pdf).

# On the TMCM-6110 (v1.31 and v1.35), if there is a power supply connected, but
# it's not actually giving power, the board will boot (with the USB power) and
# only restore some values. When the power supply is turned on, most of
# the values are then correctly set... but not all. Seems to only affect
# acceleration and soft stop flag. Currently we work around this by loading
# a routine that reset that values and put it to autostart (when actuator power
# is connected)
# Bug reported to Trinamic 2015-10-19. => They don't really seem to believe it.

from past.builtins import basestring
import glob
import logging
import os
import random
import re
import struct
import subprocess
import threading
from collections import OrderedDict
from concurrent.futures import CancelledError

try:
    import canopen
    from can import CanError
    from canopen.nmt import NmtError
    from canopen.sdo.exceptions import SdoAbortedError
    from canopen.profiles.p402 import Homing, State402
except ImportError:
    # Do not fail if python-can or python-canopen are not installed
    canopen = None

import fcntl
import math
import numpy
import serial
import time

import odemis
from odemis import model, util
from odemis.model import (isasync, ParallelThreadPoolExecutor, CancellableThreadPoolExecutor,
                          CancellableFuture, HwError)
from odemis.util import driver, TimeoutError, to_str_escape


class TMCLError(Exception):
    def __init__(self, status, value, cmd, *args, **kwargs):
        super(TMCLError, self).__init__(status, value, cmd, *args, **kwargs)
        self.args = (status, value, cmd)
        self.errno = status

    def __str__(self):
        status, value, cmd = self.args
        return ("%d: %s (val = %d, reply from %s)" %
                (status, TMCL_ERR_STATUS[status], value, cmd))


# Status codes from replies which indicate everything went fine
TMCL_OK_STATUS = {100, # successfully executed
                  101, # commanded loaded in memory
}

# Status codes from replies which indicate an error
TMCL_ERR_STATUS = {
    1: "Wrong checksum",
    2: "Invalid command",
    3: "Wrong type",
    4: "Invalid value",
    5: "Configuration EEPROM locked",
    6: "Command not available",
}

REFPROC_2XFF = "2xFinalForward" # fast then slow, always finishing by forward move
REFPROC_STD = "Standard"  # Use the standard reference search built in the controller (depends on the axis parameters)
REFPROC_FAKE = "FakeReferencing"  # was used for simulator when it didn't support referencing

# Model number (int) of devices tested
KNOWN_MODELS = {1140, 3110, 6110, 3214, 1211}
KNOWN_MODELS_CAN = {'PD-1240', 'PD-1240-fake'}
# These models report the velocity/accel in internal units of the TMC429
USE_INTERNAL_UNIT_429 = {1110, 1140, 3110, 6110}

# Info for storing config data that is not directly recordable in EEPROM
UC_FORMAT = 1  # Version number

# List of models which support the current UC_FORMAT
UC_SUPPORTED_MODELS = {1110, 1140, 3110, 6110}

# Contains also the number of axes
# Axis param number -> size (in bits) + un/signed (negative if signed)
UC_APARAM = OrderedDict((
    # Chopper (for each axis)
    (162, 2),  # Chopper blank time (1 = for low current applications, 2 is default)
    (163, 1),  # Chopper mode (0 is default)
    (167, 4),  # Chopper off time (2 = minimum)

    # Stallguard
    (173, 1),  # stallGuard2 filter
    (174, -7),  # stallGuard2 threshold
    (181, 11),  # Stop on stall
))

# Bank/add -> size of value (in bits)
UC_OUT = OrderedDict((
    ((0, 0), 2),  # Pull-ups for limit switches
))

UC_APARAM_3214 = OrderedDict((
    (4, 23),  # Maximum positioning speed
    (5, 23),  # Maximum acceleration
    (6, 8),  # Absolute max current
    (7, 8),  # Standby current
    (12, 1),  # Right limit switch disable
    (13, 1),  # Left limit switch disable
#    (24, 1),  # Right limit switch polarity
#    (25, 1),  # Left limit switch polarity
#    (26, 1),  # Soft stop enable
    (31, 4),  # Power down ramp (0.16384s)
    (140, 3),  # Microstep resolution

    # Chopper (for each axis)
    (162, 2),  # Chopper blank time (1 = for low current applications, 2 is default)
    (163, 1),  # Chopper mode (0 is default)
    (167, 4),  # Chopper off time (2 = minimum)

    # Stallguard
    (173, 1),  # stallGuard2 filter
    (174, -7),  # stallGuard2 threshold
    (181, 23),  # Stop on stall

    (193, 3),  # Reference search mode
    (194, 23),  # Reference search speed
    (195, 23),  # Reference switch speed (pps)
    (204, 2),  # Free wheeling mode
    # 210, 16 ?
    (212, 16), # Maximum encoder deviation (encoder steps)
    (214, 9),  # Power down delay (in 10ms)
    (251, 1),  # Reverse shaft
))

# Addresses and shift (for each axis) for the 2xFF referencing routines
ADD_2XFF_ROUT = 80  # Routine to start referencing
SHIFT_2XFF_ROUT = 15  # Each routine must be < 15 instructions
ADD_2XFF_INT = 50  # Interrupt handler for the referencing
SHIFT_2XFF_INT = 10  # Each interrupt handler must be < 10 instructions

# General purpose 32-bit variable in Bank 2
AREF_USER_VAR = 117  # Global parameter should be between 56-255


# CANopen constants

# Communication area
DEVICE_NAME = 0x1008
HW_VERSION = 0x1009
SW_VERSION = 0x100A
IDENTITY = 0x1018

# Manufacturer specific area
SWITCH_PARAM = 0x2005
PULLUP_RESISTORS = 0x2710

# Profile specific area
CONTROL_WORD = 0x6040
STATUS_WORD = 0x6041
MODE_OF_OPERATION = 0x6060
ACTUAL_POSITION = 0x6064
POSITION_WINDOW = 0x6067
POSITION_WINDOW_TIME = 0x6068
SENSOR_SELECTION = 0x606a
TARGET_POSITION = 0x607a
HOMING_METHOD = 0x6098
HOME_OFFSET = 0x607C
QUICK_STOP_OPTION = 0x605A
PROFILE_VELOCITY = 0x6081
PROFILE_ACCELERATION = 0x6083

# Mode of operation (node.op_mode)
HOMING_MODE = "HOMING"
PP_MODE = 'PROFILED POSITION'

# States
START = "START"
NOT_READY_TO_SWITCH_ON = "NOT READY TO SWITCH ON"
SWITCH_ON_DISABLED = "SWITCH ON DISABLED"
READY_TO_SWITCH_ON = "READY TO SWITCH ON"
SWITCHED_ON = "SWITCHED ON"
OPERATION_ENABLED = "OPERATION ENABLED"
QUICK_STOP_ACTIVE = "QUICK STOP ACTIVE"
IN_PROGRESS = 'IN PROGRESS'
TARGET_REACHED = 'TARGET REACHED'
ATTAINED = 'ATTAINED'
INTERRUPTED = 'INTERRUPTED'
ERROR_VEL_NOT_ZERO = 'ERROR VELOCITY IS NOT ZERO'
ERROR_VEL_ZERO = 'ERROR VELOCITY IS ZERO'


class TMCLController(model.Actuator):
    """
    Represents one Trinamic TMCL-compatible controller.
    Note: it must be set to binary communication mode (that's the default).
    """

    def __init__(self, name, role, port, axes, ustepsize, address=None,
                 rng=None, unit=None, abs_encoder=None,
                 refproc=None, refswitch=None, temp=False,
                 minpower=10.8, param_file=None, do_axes=None,
                 led_prot_do=None, **kwargs):
        """
        port (str): port name. Can be a pattern, in which case all the ports
          fitting the pattern will be tried.
          Use /dev/fake3 or /dev/fake6 for a simulator with 3 or 6 axes.
        address (None or 1 <= int <= 255): Address of the controller (set via the
          DIP). If None, any address will be accepted.
        axes (list of str): names of the axes, from the 1st to the last.
          If an axis is not connected, put a "".
        ustepsize (list of float): size of a microstep in m (the smaller, the
          bigger will be a move for a given distance in m)
        rng (list of tuples of 2 floats or None): min/max position allowed for
          each axis. 0 must be part of the range.
          Note: If the axis is inverted, the values provided will be inverted too.
        abs_encoder (None or list of True/False/None): Indicates for each axis
          whether the axis position can be read as an absolute position from the
          encoder (=True), a relative position of the encoder (=False),
          or no encoder is present (=None).
          With a "relative" encoder, a referencing is needed before the reported
          position corresponds to a known point on the physical axis, while
          absolute encoders are always referenced.
          If set (True of False), then .position reports the encoder position.
        unit (None or list of str): The unit of each axis. When it's None, it
          defaults to "m" for all the axes.
        refswitch (dict str -> int): if an axis needs to have its reference
          switch turn on during referencing, the digital output port is
          indicated by the number.
        refproc (str or None): referencing (aka homing) procedure type. Use
          None to indicate it's not possible (no reference/limit switch) or the
          name of the procedure. For now only "2xFinalForward" or "Standard"
          is accepted.
        temp (bool): if True, will read the temperature from the analogue input
         (10 mV <-> 1 °C)
        minpower (0<=float): minimum voltage supplied to be functional. If the
          device receives less than this, an error will be reported at initialisation.
        param_file (str or None): (absolute) path to a tmcm.tsv file which will
          be used to initialise the axis parameters (and IO).
        inverted (set of str): names of the axes which are inverted (IOW, either
         empty or the name of the axis)
        do_axes (dict int -> str, value, value, float): the digital output channel
         -> axis name, reported position when enabled (high),
         reported position when disabled (low), transition period (s).
         The axes created can only be moved via moveAbs(), and do not support referencing.
        led_prot_do (dict int -> bool): Digital output channel -> 
         value to set (True = high). Active when the leds (of the refswitch) are on.
        """
        # If DIP is set to 0, it will be using the value from global param 66
        if not (address is None or 1 <= address <= 255):
            raise ValueError("Address must be None or between 1 and 255, but got %d" % (address,))

        if len(axes) != len(ustepsize):
            raise ValueError("Expecting %d ustepsize (got %s)" %
                             (len(axes), ustepsize))

        # TODO: allow to specify the unit of the axis

        self._name_to_axis = {}  # str -> int: name -> axis number
        self._name_to_do_axis = {}  # str -> int: name -> digital output port
        if rng is None:
            rng = [None] * len(axes)
        rng += [None] * (len(axes) - len(rng)) # ensure it's long enough

        if abs_encoder is None:
            abs_encoder = [None] * len(axes)
        elif len(abs_encoder) != len(axes):
            raise ValueError("abs_encoder argument must be the same length as axes")

        if unit is None:
            unit = ["m"] * len(axes)
        elif len(unit) != len(axes):
            raise ValueError("unit argument must be the same length as axes")

        for i, n in enumerate(axes):
            if not n:  # skip this non-connected axis
                continue
            # sz is typically ~1µm, so > 1 cm is very fishy
            sz = ustepsize[i]
            if not (0 < sz <= 10e-3):
                raise ValueError("ustepsize should be between 0 and 10 mm, but got %g m" % (sz,))
            self._name_to_axis[n] = i

        self._refswitch = {} # int -> None or int: axis number -> out port to turn on the ref switch
        self._active_refswitchs = set()  # int: axes which currently need the ref switch on
        self._refswitch_lock = threading.Lock()  # to be taken when touching ref switchs
        for a, s in (refswitch or {}).items():
            if not (0 <= s <= 7):
                raise ValueError("Output port for axis %s is must be between 0 and 7 (but is %s)" % (a, s))
            try:
                aid = self._name_to_axis[a]
                self._refswitch[aid] = s
            except KeyError:
                raise ValueError("refswitch has unknown axis %s" % a)

        self._ustepsize = ustepsize

        if refproc == REFPROC_2XFF:
            self._startReferencing = self._startReferencing2xFF
            self._waitReferencing = self._waitReferencing2xFF
            self._cancelReferencing = self._cancelReferencing2xFF
        elif refproc == REFPROC_STD or refproc == REFPROC_FAKE:
            self._startReferencing = self._startReferencingStd
            self._waitReferencing = self._waitReferencingStd
            self._cancelReferencing = self._cancelReferencingStd
        elif refproc is None:
            pass
        else:
            raise ValueError("Reference procedure %s unknown" % (refproc, ))
        self._refproc = refproc
        self._refproc_cancelled = {}  # axis number -> event
        self._refproc_lock = {}  # axis number -> lock

        self._ser_access = threading.RLock()
        self._serial, ra = self._findDevice(port, address)
        self._target = ra  # same as address, but always the actual one
        self._portpattern = port

        # For ensuring only one updatePosition() at the same time
        self._pos_lock = threading.Lock()

        self._modl, vmaj, vmin = self.GetVersion()
        if self._modl not in KNOWN_MODELS:
            logging.warning("Controller TMCM-%d is not supported, will try anyway",
                            self._modl)

        if self._modl == 3110 and (vmaj + vmin / 100) < 1.09:
            # NTS told us the older version had some issues (wrt referencing?)
            raise ValueError("Firmware of TMCM controller %s is version %d.%02d, "
                             "while version 1.09 or later is needed" %
                             (name, vmaj, vmin))

        # Check that the device support that many axes
        try:
            self.GetAxisParam(max(self._name_to_axis.values()), 1) # current pos
        except TMCLError:
            raise ValueError("Device %s doesn't support %d axes (got %s)" %
                             (name, max(self._name_to_axis.values()) + 1, axes))

        if name is None and role is None: # For scan only
            return

        self._minpower = minpower
        if not self._isFullyPowered():
            raise model.HwError("Device %s has no power, check the power supply input" % name)
        # TODO: add a .powerSupply readonly VA ? Only if not already provided by HwComponent.

        try:
            axis_params, io_config = self.extract_config()
        except TypeError as ex:
            logging.warning("Failed to extract user config: %s", ex)
        except Exception:
            logging.exception("Error during user config extraction")
        else:
            logging.debug("Extracted config: %s, %s", axis_params, io_config)
            self.apply_config(axis_params, io_config)

        if param_file:
            try:
                f = open(param_file)
            except Exception as ex:
                raise ValueError("Failed to open file %s: %s" % (param_file, ex))
            try:
                axis_params, global_params, io_config = self.parse_tsv_config(f)
            except Exception as ex:
                raise ValueError("Failed to parse file %s: %s" % (param_file, ex))
            logging.debug("Extracted param file config: %s, %s, %s", axis_params, global_params, io_config)
            self.apply_config(axis_params, io_config, global_params)

        # will take care of executing axis move asynchronously
        self._executor = ParallelThreadPoolExecutor()  # one task at a time

        self._abs_encoder = {}  # int -> bool: axis ID -> use encoder position
        self._ref_max_length = {}  # int -> float: axis ID -> max distance during referencing
        axes_def = {}
        for n, i in self._name_to_axis.items():
            if not n:
                continue

            self._abs_encoder[i] = abs_encoder[i]
            if not abs_encoder[i] in {True, False, None}:
                raise ValueError("abs_encoder argument must only contain True, False, or None")
            # If abs_encoder is False (ie, there is a encoder, but it's not absolute),
            # it might be referenced or not, and it might be the same as the
            # controller "actual" position, or not. Anyway, the encoder as a
            # tiny more chance to be correct than the "actual" position, so we
            # use it as-is. The rest of the code can deal with the encoder and
            # actual position not being synchronized anyway.

            sz = ustepsize[i]
            phy_rng = ((-2 ** 31) * sz, (2 ** 31 - 1) * sz)
            sw_rng = rng[i]
            if sw_rng is not None:
                if not sw_rng[0] <= 0 <= sw_rng[1]:
                    raise ValueError("Range of axis %d doesn't include 0: %s" % (i, sw_rng))
                phy_rng = (max(phy_rng[0], sw_rng[0]), min(phy_rng[1], sw_rng[1]))
                self._ref_max_length[i] = phy_rng[1] - phy_rng[0]
            else:
                # For safety, for referencing timeout, consider that the range
                # is not too long (ie, 4M µsteps).
                # If it times out, the user should specify an axis range.
                self._ref_max_length[i] = sz * 4e6  # m

            if not isinstance(unit[i], basestring):
                raise ValueError("unit argument must only contain strings, but got %s" % (unit[i],))
            axes_def[n] = model.Axis(range=phy_rng, unit=unit[i])
            self._init_axis(i)
            try:
                self._checkErrorFlag(i)
            except HwError as ex:
                # Probably some old error left-over, no need to worry too much
                logging.warning(str(ex))

        # Add digital output axes
        self._do_axes = do_axes or {}
        self._led_prot_do = led_prot_do or {}
        for channel, (an, hpos, lpos, dur) in self._do_axes.items():
            if an in self._name_to_axis or an in self._name_to_do_axis:
                raise ValueError("Axis %s specified multiple times" % an)
            if not 0 <= dur < 1000:
                raise ValueError("Axis %s duration %s should be in seconds" % (an, dur))
            axes_def[an] = model.Axis(choices={lpos, hpos})
            self._name_to_do_axis[an] = channel

        for channel, pos in self._led_prot_do.items():
            if channel not in self._do_axes:
                raise ValueError("led_prot_do channel %s is not specified as a do-axis" % channel)
            if pos not in self._do_axes[channel][1:3]:
                raise ValueError("led_prot_do of channel %d has position %s, not in do_axes" % (channel, pos))

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        driver_name = driver.getSerialDriver(self._portpattern)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = "TMCM-%d (firmware %d.%02d)" % (self._modl, vmaj, vmin)

        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        # TODO: for axes with encoders, refresh position regularly

        # TODO: add support for changing speed. cf p.68: axis param 4 + p.81 + TMC 429 p.6
        self.speed = model.VigilantAttribute({}, unit="m/s", readonly=True)
        self._updateSpeed()

        self._accel = {}
        for n, i in self._name_to_axis.items():
            self._accel[n] = self._readAccel(i)
            if self._accel[n] == 0:
                logging.warning("Acceleration of axis %s is null, most probably due to a bad hardware configuration", n)

        # Check state of refswitch on startup
        self._expected_do_pos = {}  # do positions before referencing, will be reset after refswitch is released
        self._leds_on = any(self.GetIO(2, rs) for rs in self._refswitch.values())
        if self._leds_on:
            logging.debug("Refswitch is on during initialization, releasing refswitch for all axes.")
            for ax in self._name_to_axis.values():
                self._releaseRefSwitch(ax)

        if refproc is None:
            # Only the axes which are "absolute"
            axes_ref = {a: True for a, i in self._name_to_axis.items() if self._abs_encoder[i]}
        else:
            # str -> boolean. Indicates whether an axis has already been referenced
            # (considered already referenced if absolute)
            axes_ref = {a: self._is_already_ref(i) for a, i in self._name_to_axis.items()}

        self.referenced = model.VigilantAttribute(axes_ref, readonly=True)

        # Note: if multiple instances of the driver are running simultaneously,
        # the temperature reading will cause mayhem even if one of the instances
        # does nothing.
        if temp:
            # One sensor is at the top, one at the bottom of the sample holder.
            # The most interesting is the temperature difference, so just
            # report both.
            self.temperature = model.FloatVA(0, unit=u"°C", readonly=True)
            self.temperature1 = model.FloatVA(0, unit=u"°C", readonly=True)
            self._temp_timer = util.RepeatingTimer(1, self._updateTemperatureVA,
                                                   "TMCM temperature update")
            self._updateTemperatureVA() # make sure the temperature is correct
            self._temp_timer.start()

    def _update_ref(self):
        """
        It updates the global parameter AREF_USER_VAR with the axes that are referenced
        """
        # AREF_USER_VAR is a general purpose 32-bit variable in bank 2. It is used to store the axes that are already referenced.
        # Up to 256 variables are available, only the first 56 can be stored permanently in EEPROM. #117 is randomly chosen between 56-255.
        # In case the TMCM controller is turned off, or the main motor power is turned off/on, the variable is automatically reset to 0.
        # We use this behaviour to detect whether the axes could have been moved without the device control.
        aref = 0
        for n, i in self._name_to_axis.items():
            if self.referenced.value[n]:
                aref |= 1 << i
        # special format: one bit per axis + inverted bit on the high 2 bytes to double check
        aref |= ~aref << 16
        self.SetGlobalParam(2, AREF_USER_VAR, aref)

    def _is_already_ref(self, axis):
        """
        It checks if the axis is already referenced or not by using the global parameter AREF_USER_VAR

        Args:
            axis: axis number

        Returns (boolean): if the axis is referenced or not

        """
        if self._abs_encoder[axis] is True:
            return True
        aref = self.GetGlobalParam(2, AREF_USER_VAR)
        # Is arefd the right format? if not, return False
        if aref & 0xffff != ~(aref >> 16):
            # Reset the user variable in case it's not already zero
            if aref != 0:
                logging.warning("Reset AREF variable %d as it had unexpected value %d", AREF_USER_VAR, aref)
                self.SetGlobalParam(2, AREF_USER_VAR, 0)
            return False
        aref_axis = 1 << axis
        return bool(aref & aref_axis)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        if hasattr(self, "_temp_timer"):
            self._temp_timer.cancel()
            self._temp_timer.join(1)
            del self._temp_timer

        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

        super(TMCLController, self).terminate()

    def _init_axis(self, axis):
        """
        Initialise the given axis with "good" values for our needs (Delphi)
        axis (int): axis number
        """
        self._refproc_cancelled[axis] = threading.Event()
        self._refproc_lock[axis] = threading.Lock()

        if self._refproc == REFPROC_2XFF:
            # TODO: get rid of this once all the hardware have been updated with
            # the right EEPROM config (using tmcmconfig)
            self.SetAxisParam(axis, 163, 0)  # chopper mode (0 is default)
            self.SetAxisParam(axis, 162, 2)  # Chopper blank time (1 = for low current applications, 2 is default)
            self.SetAxisParam(axis, 167, 3)  # Chopper off time (2 = minimum)
            self.SetAxisParam(axis, 4, 1398)  # maximum velocity to 1398 == 2 mm/s
            self.SetAxisParam(axis, 5, 7)  # maximum acc to 7 == 20 mm/s2
            self.SetAxisParam(axis, 140, 8)  # number of usteps ==2^8 =256 per fullstep
            self.SetAxisParam(axis, 6, 15)  # maximum RMS-current to 15 == 15/255 x 2.8 = 165mA
            self.SetAxisParam(axis, 7, 0)  # standby current to 0
            self.SetAxisParam(axis, 204, 100)  # power off after 1 s standstill
            self.SetAxisParam(axis, 154, 0)  # step divider to 0 ==2^0 ==1
            self.SetAxisParam(axis, 153, 0)  # acc divider to 0 ==2^0 ==1
            self.MoveRelPos(axis, 0)  # activate parameter with dummy move

            # set up the programs needed for the referencing

            # Interrupt: stop the referencing
            # The original idea was to mark the current position as 0 ASAP, and then
            # later on move back to there. Now, we just stop ASAP, and hope it
            # takes always the same time to stop. This allows to read how far from
            # a previous referencing position we were during the testing.
            prog = [# (6, 1, axis), # GAP 1, Motid // read pos
                    # (35, 60 + axis, 2), # AGP 60, 2 // save pos to 2/60

                    # (32, 10 + axis, axis), # CCO 10, Motid // Save the current position # doesn't work??

                    # TODO: see if it's needed to do like in original procedure: set 0 ASAP
                    # (5, 1, axis, 0), # SAP 1, MotId, 0 // Set actual pos 0
                    (13, 1, axis), # RFS STOP, MotId   // Stop the reference search
                    (38,), # RETI
                    ]
            addr = ADD_2XFF_INT + SHIFT_2XFF_INT * axis  # at addr 50/60/70
            self.UploadProgram(prog, addr)

            # Program: start and wait for referencing
            # It's independent enough that even if the controlling computer
            # stops during the referencing the motor will always eventually stop.
            timeout = 20 # s (it can take up to 20 s to reach the home as fast speed)
            timeout_ticks = int(round(timeout * 100)) # 1 tick = 10 ms
            gparam = 128 + axis
            addr = ADD_2XFF_ROUT + SHIFT_2XFF_ROUT * axis  # Max with 3 axes: 80->120
            prog = [(9, gparam, 2, 0), # Set global param to 0 (=running)
                    (13, 0, axis), # RFS START, MotId
                    (27, 4, axis, timeout_ticks), # WAIT RFS until timeout
                    (21, 8, 0, addr + 6), # JC ETO, to TIMEOUT (= +6)
                    (9, gparam, 2, 1), # Set global param to 1 (=all went fine)
                    (28,), # STOP
                    (13, 1, axis), # TIMEOUT: RFS STOP, Motid
                    (9, gparam, 2, 2), # Set global param to 2 (=RFS timed-out)
                    (28,), # STOP
                    ]
            self.UploadProgram(prog, addr)

    # Communication functions

    @staticmethod
    def _instr_to_str(instr):
        """
        instr (buffer of 9 bytes)
        """
        target, n, typ, mot, val, chk = struct.unpack('>BBBBiB', instr)
        s = "%d, %d, %d, %d, %d (%d)" % (target, n, typ, mot, val, chk)
        return s

    @staticmethod
    def _reply_to_str(rep):
        """
        rep (buffer of 9 bytes)
        """
        ra, rt, status, rn, rval, chk = struct.unpack('>BBBBiB', rep)
        s = "%d, %d, %d, %d, %d (%d)" % (ra, rt, status, rn, rval, chk)
        return s

    def _resynchonise(self):
        """
        Ensures the device communication is "synchronised"
        return (int): the address reported by the device with connection
        """
        with self._ser_access:
            # Flush everything from the input
            self._serial.flushInput()
            garbage = self._serial.read(1000)
            if garbage:
                logging.debug("Received unexpected bytes '%s'", to_str_escape(garbage))
            if len(garbage) == 1000:
                # Probably a sign that it's not the device we are expecting
                logging.warning("Lots of garbage sent from device")

            # In case the device has received some data before, resynchronise by
            # sending one byte at a time until we receive a reply. This can
            # happen for instance if a program is checking whether it's a modem,
            # or another Odemis driver is probing devices.

            # As there is no command 0, either we will receive a "wrong command" or
            # a "wrong checksum", but it's unlikely to ever do anything more.
            msg = b"\x00" * 9  # a 9-byte message
            logging.debug("Sending '%s'", to_str_escape(msg))
            self._serial.write(msg)
            self._serial.flush()
            res = self._serial.read(10)  # See if the device is trying to talk too much
            if len(res) == 9:  # answer should be 9 bytes
                logging.debug("Received (for sync) %s", self._reply_to_str(res))
                ra, rt, status, rn, rval, chk = struct.unpack('>BBBBiB', res)
                if status == 1:  # Wrong checksum (=> got too many bytes)
                    # On some devices the timeout of the previous read is enough
                    # to reset the device input buffer, but on some other it's not.
                    time.sleep(1)
                elif status != 2:  # Unknown command (expected)
                    logging.warning("Unexpected error %d", status)
                # check the checksum is correct
                npres = numpy.frombuffer(res, dtype=numpy.uint8)
                good_chk = numpy.sum(npres[:-1], dtype=numpy.uint8)
                if chk == good_chk:
                    return rt  # everything is fine
                else:
                    logging.debug("Device message has wrong checksum")
            else:
                logging.debug("Device replied unexpected message: %s", to_str_escape(res))

            raise IOError("Device did not answer correctly to any sync message")

    # The next three methods are to handle the extra configuration saved in user
    # memory (global param, bank 2)
    # Note: there is no method to read the config from the live memory because it
    # is not possible to read the current output values (written by SetIO()).

    def apply_config(self, axis_params, io_config, global_params=None):
        """
        Configure the device according to the given 'user configuration'.
        axis_params (dict (int, int) -> int): axis number/param number -> value
        io_config (dict (int, int) -> int): bank/port -> value to pass to SetIO
        global_params (dict (int, int) -> int): bank/param number -> value
        """
        global_params = global_params or {}
        for (ax, ad), v in axis_params.items():
            self.SetAxisParam(ax, ad, v)
        for (b, p), v in io_config.items():
            self.SetIO(b, p, v)
        for (b, ad), v in global_params.items():
            self.SetGlobalParam(b, ad, v)

    def write_config(self, axis_params, io_config):
        """
        Converts the 'user configuration' into a packed data and store into the
        'user' EEPROM area (bank 2).
        axis_params (dict (int, int) -> int): axis number/param number -> value
          Note: all axes of the device must be present, and all the parameters
          defined in UC_APARAM must be present.
        io_config (dict (int, int) -> int): bank/port -> value to pass to SetIO
          Note that all the data in UC_OUT must be defined
        """
        if not self._modl in UC_SUPPORTED_MODELS:
            logging.warning("User config is not officially supported on TMCM-%s", self._modl)

        naxes = max(ax for ax, ad in axis_params.keys()) + 1

        # TODO: special format for storing _all_ the axes param (for the 3214)
        # or, alternatively, store it as a program, which writes the param at
        # init.

        # Pack IO, then axes
        sd = struct.pack("B", io_config[(0, 0)])
        for i in range(naxes):
            for p, l in UC_APARAM.items():
                v = axis_params[(i, p)]
                fmt = ">b" if abs(l) <= 7 else ">h"
                sd += struct.pack(fmt, v)

        # pad enough 0's to be a multiple of 4 (= uint32)
        sd += b"\x00" * (-len(sd) % 4)

        # Compute checksum (= sum of everything on 16 bits)
        hpres = numpy.frombuffer(sd, dtype=numpy.uint16)
        checksum = numpy.sum(hpres, dtype=numpy.uint16)

        # Compute header
        s = struct.pack(">HBB", checksum, UC_FORMAT, len(sd) // 4) + sd

        logging.debug("Encoded user config as '%s'", to_str_escape(s))

        # Write s as a series of uint32 into the user area
        assert(len(s) // 4 <= 56)
        for i, v in enumerate(struct.unpack(">%di" % (len(s) // 4), s)):
            logging.debug("Writing on %d, 0x%08x", i, v)
            self.SetGlobalParam(2, i, v)
            self.StoreGlobalParam(2, i)

    def extract_config(self):
        """
        Read the configuration stored in 'user' area and convert it to a python
        representation.
        return:
            axis_params (dict (int, int) -> int): axis number/param number -> value
            io_config (dict (int, int) -> int): bank/port -> value to pass to SetIO
        raise:
            TypeError: the configuration saved doesn't appear to be valid
        """
        if not self._modl in UC_SUPPORTED_MODELS:
            logging.info("User config doesn't support TMCM-%s, so not reading it", self._modl)
            return {}, {}

        # Read header (and check it makes sense)
        h = self.GetGlobalParam(2, 0)
        sh = struct.pack(">i", h)
        chks, fv, l = struct.unpack(">HBB", sh)
        if fv != UC_FORMAT:
            raise TypeError("User config format claim to be unsupported v%d" % fv)
        if not 2 <= l <= 55:
            raise TypeError("Impossible length of %d" % l)

        # Read the rest of the data
        s = b""
        for i in range(l):
            d = self.GetGlobalParam(2, i + 1)
            s += struct.pack(">i", d)

        logging.debug("Read user config as '%s%s'", to_str_escape(sh), to_str_escape(s))

        # Compute checksum (= sum of everything on 16 bits)
        hpres = numpy.frombuffer(s, dtype=numpy.uint16)
        act_chks = numpy.sum(hpres, dtype=numpy.uint16)
        if act_chks != chks:
            raise TypeError("User config has wrong checksum (expected %d)" % act_chks)

        # Decode
        afmt = ""
        for p, l in UC_APARAM.items():
            afmt += "b" if abs(l) <= 7 else "h"
        lad = struct.calcsize(">" + afmt)
        assert(lad >= 4)
        naxes = (len(s) - 1) // lad # works because lad >= 4
        assert(naxes > 0)
        fmt = ">B" + afmt * naxes
        s = s[:struct.calcsize(fmt)]  # discard the padding
        ud = struct.unpack(fmt, s)

        io_config = {(0, 0): ud[0]}

        i = 1
        axis_params = {}
        for ax in range(naxes):
            for p in UC_APARAM.keys():
                axis_params[(ax, p)] = ud[i]
                i += 1

        return axis_params, io_config

    @staticmethod
    def parse_tsv_config(f):
        """
        Parse a tab-separated value (TSV) file in the following format:
          bank/axis    address   value    # comment
          bank/axis can be either G0 -> G3 (global: bank), A0->A5 (axis: number), or O0 -> 02 (output: bank)
          address is between 0 and 255
          value is a number
        f (File): opened file
        return:
          axis_params (dict (int, int) -> int): axis number/param number -> value
          global_params (dict (int, int) -> int): bank/param number -> value
          io_config (dict (int, int) -> int): bank/port -> value to pass to SetIO
        """
        axis_params = {}  # (axis/add) -> val (int)
        global_params = {}  # (bank/add) -> val (int)
        io_config = {}  # (bank/port) -> val (int)

        # read the parameters "database" the file
        for l in f:
            # comment or empty line?
            mc = re.match(r"\s*(#|$)", l)
            if mc:
                logging.debug("Comment line skipped: '%s'", l.rstrip("\n\r"))
                continue
            m = re.match(r"(?P<type>[AGO])(?P<num>[0-9]+)\t(?P<add>[0-9]+)\t(?P<value>[0-9]+)\s*(#.*)?$", l)
            if not m:
                raise ValueError("Failed to parse line '%s'" % l.rstrip("\n\r"))
            typ, num, add, val = m.group("type"), int(m.group("num")), int(m.group("add")), int(m.group("value"))
            if typ == "A":
                axis_params[(num, add)] = val
            elif typ == "G":
                global_params[(num, add)] = val
            elif typ == "O":
                io_config[(num, add)] = val
            else:
                raise ValueError("Unexpected line '%s'" % l.rstrip("\n\r"))

        return axis_params, global_params, io_config

    # TODO: finish this method and use where possible
    def SendInstructionRecoverable(self, n, typ=0, mot=0, val=0):

        try:
            self.SendInstruction(n, typ, mot, val)

        except IOError:
            # TODO: could serial.outWaiting() give a clue on what is going on?


            # One possible reason is that the device disappeared because the
            # cable was pulled out, or the power got cut (unlikely, as it's
            # powered via 2 sources).

            # TODO: detect that the connection was lost if the port we have
            # leads to nowhere. => It seems os.path.exists should fail ?
            # or /proc/pid/fd/n link to a *(deleted)
            # How to handle the fact it will then probably get a different name
            # on replug? Use a pattern for the file name?

            self._resynchonise()

    def SendInstruction(self, n, typ=0, mot=0, val=0):
        """
        Sends one instruction, and return the reply.
        n (0<=int<=255): instruction ID
        typ (0<=int<=255): instruction type
        mot (0<=int<=255): motor/bank number
        val (-2**31<=int<2**31-1): value to send
        return (-2**31<=int<2**31-1): value of the reply (if status is good)
        raises:
            IOError: if problem with sending/receiving data over the serial port
            TMCLError: if status if bad
        """
        msg = numpy.empty(9, dtype=numpy.uint8)
        struct.pack_into('>BBBBiB', msg, 0, self._target, n, typ, mot, val, 0)
        # compute the checksum (just the sum of all the bytes)
        msg[-1] = numpy.sum(msg[:-1], dtype=numpy.uint8)
        with self._ser_access:
            logging.debug("Sending %s", self._instr_to_str(msg))
            try:
                self._serial.write(msg)
            except IOError:
                logging.warning("Failed to send command to TMCM, trying to reconnect.")
                self._tryRecover()
                # Failure here should mean that the device didn't get the (complete)
                # instruction, so it's safe to send the command again.
                return self.SendInstruction(n, typ, mot, val)
            self._serial.flush()
            while True:
                try:
                    res = self._serial.read(9)
                except IOError:
                    logging.warning("Failed to read from TMCM, trying to reconnect.")
                    self._tryRecover()
                    # We already sent the instruction before, so don't send it again
                    # here. Instead, raise an error and let the user decide what to do next
                    raise IOError("Failed to read from TMCM, restarted serial connection.")
                if len(res) < 9:  # TODO: TimeoutError?
                    logging.warning("Received only %d bytes after %s, will fail the instruction",
                                    len(res), self._instr_to_str(msg))
                    raise IOError("Received only %d bytes after %s" %
                                  (len(res), self._instr_to_str(msg)))
                logging.debug("Received %s", self._reply_to_str(res))
                ra, rt, status, rn, rval, chk = struct.unpack('>BBBBiB', res)

                # Check it's a valid message
                npres = numpy.frombuffer(res, dtype=numpy.uint8)
                good_chk = numpy.sum(npres[:-1], dtype=numpy.uint8)
                if chk == good_chk:
                    if self._target != 0 and self._target != rt:  # 0 means 'any device'
                        logging.warning("Received a message from %d while expected %d",
                                        rt, self._target)
                    if rn != n:
                        logging.info("Skipping a message about instruction %d (waiting for %d)",
                                     rn, n)
                        continue
                    if status not in TMCL_OK_STATUS:
                        raise TMCLError(status, rval, self._instr_to_str(msg))
                else:
                    # TODO: investigate more why once in a while (~1/1000 msg)
                    # the message is garbled
                    logging.warning("Message checksum incorrect (%d), will assume it's all fine", chk)

                return rval

    def _tryRecover(self):
        self.state._set_value(HwError("USB connection lost"), force_write=True)
        # Retry to open the serial port (in case it was unplugged)
        # _ser_access should already be acquired, but since it's an RLock it can be acquired
        # again in the same thread
        with self._ser_access:
            while True:
                try:
                    self._serial.close()
                    self._serial = None
                except Exception:
                    pass
                try:
                    logging.debug("Searching for the device on port %s", self._portpattern)
                    self._findDevice(self._portpattern)
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
        logging.info("Recovered device on port %s", self._portpattern)

    # Low level functions
    def GetVersion(self):
        """
        return (int, int, int):
             Controller ID: 3110 for the TMCM-3110
             Firmware major version number
             Firmware minor version number
        """
        val = self.SendInstruction(136, 1) # Ask for binary reply
        cont = val >> 16
        vmaj, vmin = (val & 0xff00) >> 8, (val & 0xff)
        return cont, vmaj, vmin

    def GetAxisParam(self, axis, param):
        """
        Read the axis/parameter setting from the RAM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        return (0<=int): the value stored for the given axis/parameter
        """
        val = self.SendInstruction(6, param, axis)
        return val

    def SetAxisParam(self, axis, param, val):
        """
        Write the axis/parameter setting from the RAM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        val (int): the value to store
        """
        self.SendInstruction(5, param, axis, val)

    def RestoreAxisParam(self, axis, param):
        """
        Restore the axis/parameter setting from the EEPROM into the RAM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(8, param, axis)

    def StoreAxisParam(self, axis, param):
        """
        Store the axis/parameter setting from the RAM into the EEPROM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(7, param, axis)

    def GetGlobalParam(self, bank, param):
        """
        Read the parameter setting from the RAM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        return (0<=int): the value stored for the given bank/parameter
        """
        val = self.SendInstruction(10, param, bank)
        return val

    def SetGlobalParam(self, bank, param, val):
        """
        Write the parameter setting from the RAM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        val (int): the value to store
        """
        self.SendInstruction(9, param, bank, val)

    def RestoreGlobalParam(self, axis, param):
        """
        Store the global parameter setting from the EEPROM into the RAM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(12, param, axis)

    def StoreGlobalParam(self, axis, param):
        """
        Store the global parameter setting from the RAM into the EEPROM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(11, param, axis)

    def SetIO(self, bank, port, value):
        """
        Write the output value
        bank (0 or 2): bank number
        port (0<=int<=255): port number
        value (0 or 1): value to write
        """
        self.SendInstruction(14, port, bank, value)

    def GetIO(self, bank, port):
        """
        Read the input/output value
        bank (0<=int<=2): bank number
        port (0<=int<=255): port number
        return (0<=int): the value read from the given bank/port
        """
        val = self.SendInstruction(15, port, bank)
        return val

    def GetCoordinate(self, axis, num):
        """
        Read the axis/parameter setting from the RAM
        axis (0<=int<=5): axis number
        num (0<=int<=20): coordinate number
        return (0<=int): the coordinate stored
        """
        val = self.SendInstruction(30, num, axis)
        return val

    def MoveAbsPos(self, axis, pos):
        """
        Requests a move to an absolute position. This is non-blocking.
        axis (0<=int<=5): axis number
        pos (-2**31 <= int 2*31-1): position
        """
        self.SendInstruction(4, 0, axis, pos) # 0 = absolute

    def MoveRelPos(self, axis, offset):
        """
        Requests a move to a relative position. This is non-blocking.
        axis (0<=int<=5): axis number
        offset (-2**31 <= int 2*31-1): relative position
        """
        self.SendInstruction(4, 1, axis, offset) # 1 = relative
        # it returns the expected final absolute position

    def MotorStop(self, axis):
        self.SendInstruction(3, mot=axis)

    def StartRefSearch(self, axis):
        self.SendInstruction(13, 0, axis) # 0 = start

    def StopRefSearch(self, axis):
        """
        Can be called even if no referencing takes place (will never raise an error)
        """
        self.SendInstruction(13, 1, axis) # 1 = stop

    def GetStatusRefSearch(self, axis):
        """
        return (bool): False if reference is not active, True if reference is active.
        """
        val = self.SendInstruction(13, 2, axis) # 2 = status
        # The value seems to go from 1 -> 9 corresponding to the referencing state.
        # After cancelling, it becomes 15 (but not sure when it becomes 0 again)
        return val != 0

    def _isOnTarget(self, axis):
        """
        return (bool): True if the target position is reached
        """
        reached = self.GetAxisParam(axis, 8)
        return reached != 0

    def UploadProgram(self, prog, addr):
        """
        Upload a program in memory
        prog (sequence of tuples of 4 ints): list of the arguments for SendInstruction
        addr (int): starting address of the program
        """
        # cf TMCL reference p. 50
        # http://pandrv.com/ttdg/phpBB3/viewtopic.php?f=13&t=992
        # To download a TMCL program into a module, the following steps have to be performed:
        # - Send the "enter download mode command" to the module (command 132 with value as address of the program)
        # - Send your commands to the module as usual (status byte return 101)
        # - Send the "exit download mode" command (command 133 with all 0)
        # Each instruction is numbered +1, starting from 0

        self.SendInstruction(132, val=addr)
        for inst in prog:
            # TODO: the controller sometimes fails to return the correct response
            # when uploading a program... not sure why, but for now we hope it
            # worked anyway.
            try:
                self.SendInstruction(*inst)
            except IOError:
                logging.warning("Controller returned wrong answer, but will assume it's fine")
        self.SendInstruction(133)

    def RunProgram(self, addr):
        """
        Run the progam at the given address
        addr (int): starting address of the program
        """
        self.SendInstruction(129, typ=1, val=addr) # type 1 = use specified address
        # To check the program runs (ie, it's not USB bus powered), you can
        # check the program counter increases:
        # assert self.GetGlobalParam(0, 130) > addr

    def StopProgram(self):
        """
        Stop a progam if any is running
        """
        self.SendInstruction(128)

    def SetInterrupt(self, id, addr):
        """
        Associate an interrupt to run a program at the given address
        id (int): interrupt number
        addr (int): starting address of the program
        """
        # Note: interrupts seem to only be executed when a program is running
        self.SendInstruction(37, typ=id, val=addr)

    def EnableInterrupt(self, id):
        """
        Enable an interrupt
        See global parameters to configure the interrupts
        id (int): interrupt number
        """
        self.SendInstruction(25, typ=id)

    def DisableInterrupt(self, id):
        """
        Disable an interrupt
        See global parameters to configure the interrupts
        id (int): interrupt number
        """
        self.SendInstruction(26, typ=id)

    def ResetMemory(self, check):
        """
        Reset the EEPROM values to factory default
        Note: it needs about 5 seconds to recover, and a new connection must be
        initiated.
        check (int): must be 1234 to work
        """
        try:
            self.SendInstruction(137, val=check)
        except IOError:
            logging.debug("Timeout after memory reset, as expected")

    def _isFullyPowered(self):
        """
        return (boolean): True if the device is "self-powered" (meaning the
         motors will be able to move) or False if the device is "USB bus powered"
         (meaning it does answer to the computer, but nothing more).
        """
        # It's undocumented, but the IDE uses this feature too:
        # supply voltage is reported on analog channel 8
        val = self.GetIO(1, 8)  # 1 <-> 0.1 V
        v_supply = 0.1 * val
        logging.debug("Supply power reported is %.1f V", v_supply)
        return v_supply >= self._minpower

        # Old method was to use a strange fact that programs will not run if the
        # device is not self-powered.
#         gparam = 100
#         self.SetGlobalParam(2, gparam, 0)
#         self.RunProgram(80) # our stupid program address
#         time.sleep(0.01) # 10 ms should be more than enough to run one instruction
#         status = self.GetGlobalParam(2, gparam)
#         return (status == 1)

    def _checkErrorFlag(self, axis):
        """
        Raises an HWError if the axis error flag reports an issue
        """
        # Extended Error Flag: automatically reset after reading it
        xef = self.GetAxisParam(axis, 207)
        if xef & 1:
            raise HwError("Stall detected on axis %d" % (axis,))
        elif xef & 2:  # only on TMCM-3214
            ap = self.GetAxisParam(axis, 1)
            ep = self.GetAxisParam(axis, 209)
            raise HwError("Encoder deviation too large (%d vs %d) on axis %d" %
                          (ep, ap, axis,))

    def _resetEncoderDeviation(self, axis, always=False):
        """
        Set encoder position to the actual position of the controller.
        Only done if there is an encoder and the controller checks for deviation.
        always (bool): If True, do it even if the controller doesn't checks for
          encoder deviation.
        """
        if self._abs_encoder[axis] is not None:
            # Param 212: max encoder deviation (0 = disabled)
            if always or self.GetAxisParam(axis, 212) > 0:
                # Without stopping the motor, the TMCM3214, will move the axis
                # instead of setting the parameter!
                self.MotorStop(axis)
                ep = self.GetAxisParam(axis, 209)
                ap = self.GetAxisParam(axis, 1)
                logging.debug("Reseting actual position from %d to %d usteps", ap, ep)
                self.SetAxisParam(axis, 1, ep)

    def _setInputInterruptFF(self, axis):
        """
        Setup the input interrupt handler for stopping the reference search with
         2xFF.
        axis (int): axis number
        """
        addr = ADD_2XFF_INT + SHIFT_2XFF_INT * axis  # at addr 50/60/70
        intid = 40 + axis  # axis 0 = IN1 = 40
        self.SetInterrupt(intid, addr)
        self.SetGlobalParam(3, intid, 3)  # configure the interrupt: look at both edges
        self.EnableInterrupt(intid)
        self.EnableInterrupt(255)  # globally switch on interrupt processing

    def _doReferenceFF(self, axis, speed):
        """
        Run synchronously one reference search
        axis (int): axis number
        speed (int): speed in (funky) hw units for the move
        return (bool): True if the search was done in the negative direction,
          otherwise False
        raise:
            TimeoutError: if the search failed within a timeout (20s)
        """
        timeout = 20 # s
        # Set speed
        self.SetAxisParam(axis, 194, speed) # maximum home velocity
        self.SetAxisParam(axis, 195, speed) # maximum switching point velocity (useless for us)
        # Set direction
        edge = self.GetIO(0, 1 + axis) # IN1 = bank 0, port 1->3
        logging.debug("Going to do reference search in dir %d", edge)
        if edge == 1: # Edge is high, so we need to go negative dir
            self.SetAxisParam(axis, 193, 7 + 128) # RFS with negative dir
        else: # Edge is low => go positive dir
            self.SetAxisParam(axis, 193, 8) # RFS with positive dir

        gparam = 128 + axis
        self.SetGlobalParam(2, gparam, 0)
        # Run the basic program (we need one, otherwise interrupt handlers are
        # not processed)
        addr = ADD_2XFF_ROUT + SHIFT_2XFF_ROUT * axis
        endt = time.time() + timeout + 2 # +2 s to let the program first timeout
        with self._refproc_lock[axis]:
            if self._refproc_cancelled[axis].is_set():
                raise CancelledError("Reference search dir %d cancelled" % edge)
            self.RunProgram(addr)

            status = self.GetGlobalParam(2, gparam)

        # Wait until referenced
        while status == 0:
            if self._refproc_cancelled[axis].wait(0.01):
                break
            status = self.GetGlobalParam(2, gparam)
            if time.time() > endt:
                self.StopRefSearch(axis)
                self.StopProgram()
                self.MotorStop(axis)
                raise IOError("Timeout during reference search from device")

        if self._refproc_cancelled[axis].is_set() or status == 3:
            raise CancelledError("Reference search dir %d cancelled" % edge)
        elif status == 2:
            # if timed out raise
            raise IOError("Timeout during reference search dir %d" % edge)

        return edge == 1

    # Special methods for referencing
    # One of the couple run/stop methods will be picked at init based on the
    # arguments. (so the referencing is the same for all the axes).
    # For now, runReferencing is synchronous because 2xFF is much easier to
    # handle this way. If really needed, we could have run/wait/stop and so
    # run them asynchronously.

    def _startReferencing2xFF(self, axis):
        """
        Do the 2x final forward referencing.
        The current implementation only supports one axis referencing at a time.
        raise:
            IOError: if timeout happen
            CancelledError: if cancelled
        """
        self._refproc_cancelled[axis].clear()

        logging.info("Starting referencing of axis %d", axis)
        if not self._isFullyPowered():
            raise IOError("Device is not powered, so motors cannot move")

    def _waitReferencing2xFF(self, axis):
        """
        Do actual 2x final forward referencing (this is synchronous).
        The current implementation only supports one axis referencing at a time.
        axis (int)
        raise:
            IOError: if timeout happen
            CancelledError: if cancelled
        """
        # Procedure devised by NTS:
        # It requires the ref signal to be active for half the length. Like:
        #                      ___________________ 1
        #                      |
        # 0 ___________________|
        # ----------------------------------------> forward
        # It first checks on which side of the length the actuator is, and
        # then goes towards the edge. If the movement was backward, then
        # it does the search a second time forward, to increase the
        # repeatability.
        # All this is done twice, once a fast speed finishing with positive
        # direction, then at slow speed to increase precision, finishing
        # in negative direction. Note that as the fast speed finishes with
        # positive direction, normally only one run (in negative direction)
        # is required on slow speed.
        # Note also that the reference signal is IN1-3, the "home switch".
        # Unfortunately the default referencing procedure only support home
        # as a "spike" signal (contrarily to left/right switch. It seems the
        # reason is that it was easier to connect them this way.
        # Because of that, we need a homemade RFS command. That is
        # done by setting an interrupt to stop the RFS command when the edge
        # changes. As interrupts only work when a program is running, we
        # have a small program that waits for the RFS and report the status.
        # In conclusion, RFS is used pretty much just to move at a constant
        # speed.

        try:
            self._setInputInterruptFF(axis)

            neg_dir = self._doReferenceFF(axis, 350)  # fast (~0.5 mm/s)
            if neg_dir:  # always finish first by positive direction
                self._doReferenceFF(axis, 350)  # fast (~0.5 mm/s)

            # Go back far enough that the slow referencing always need quite
            # a bit of move. This is not part of the official NTS procedure
            # but without that, the final reference position is affected by
            # the original position.
            with self._refproc_lock[axis]:
                if self._refproc_cancelled[axis].is_set():
                    raise CancelledError("Reference search cancelled before backward move")
                self.MoveRelPos(axis, -20000)  # ~ 100µm
            for i in range(100):
                if self._refproc_cancelled[axis].wait(0.01):
                    raise CancelledError("Reference search cancelled during backward move")
                if self._isOnTarget(axis):
                    break
            else:
                logging.warning("Relative move failed to finish in time")

            neg_dir = self._doReferenceFF(axis, 50)  # slow (~0.07 mm/s)
            if not neg_dir:  # if it was done in positive direction (unlikely), redo
                logging.debug("Doing one last reference move, in negative dir")
                # As it always waits for the edge to change, the second time
                # should be positive
                neg_dir = self._doReferenceFF(axis, 50)
                if not neg_dir:
                    logging.warning("Second reference search was again in positive direction")
        finally:
            # Disable interrupt
            intid = 40 + axis  # axis 0 = IN1 = 40
            self.DisableInterrupt(intid)
            # TODO: to support multiple axes referencing simultaneously,
            # only this global interrupt would need to be handle globally
            # (= only disable iff noone needs interrupt).
            self.DisableInterrupt(255)
            # For safety, but also necessary to make sure SetAxisParam() works
            self.MotorStop(axis)

        # Reset the absolute 0 (by setting current pos to 0)
        logging.debug("Changing referencing position by %d", self.GetAxisParam(axis, 1))
        self.SetAxisParam(axis, 1, 0)

    def _cancelReferencing2xFF(self, axis):
        """
        Cancel the referencing. Should only be called after the referencing
          has been started.
        axis (int)
        """
        self._refproc_cancelled[axis].set()
        with self._refproc_lock[axis]:
            self.StopRefSearch(axis)
            self.StopProgram()
            self.MotorStop(axis)
            gparam = 128 + axis
            self.SetGlobalParam(2, gparam, 3)  # 3 => indicate cancelled

    def _requestRefSwitch(self, axis):
        refswitch = self._refswitch.get(axis)
        if refswitch is None:
            return

        with self._refswitch_lock:
            # Set _leds_on attribute before closing shutters to make sure they are not
            # opened again in a concurrent thread
            leds_were_on = self._leds_on
            self._leds_on = True  # do this before closing shutters
            # Close shutters
            tsleep = 0  # max transition period for all shutters
            for channel, val in self._led_prot_do.items():
                do_an, hpos, lpos, dur = self._do_axes[channel]
                if not leds_were_on:
                    self._expected_do_pos[do_an] = self.position.value[do_an]
                # TODO: ideally, for each DO, we should know when was the last time it
                # was set, and if it's been set to the requested value for long
                # enough, we don't need to do the extra sleep
                self.SetIO(2, channel, val == hpos)
                tsleep = max(tsleep, dur)

            time.sleep(tsleep)
            self._updatePosition()

            self._active_refswitchs.add(axis)
            logging.debug("Activating ref switch power line %d (for axis %d)", refswitch, axis)
            # Turn on the ref switch (even if it was already on)
            self.SetIO(2, refswitch, 1)

    def _releaseRefSwitch(self, axis):
        """
        Indicate that an axis doesn't need its reference switch anymore.
        It's ok to call this function even if the axis was already released.
        If other axes use the same reference switch (power line) then it will
        stay on.
        axis (int): the axis for which to release the ref switch
        """
        refswitch = self._refswitch.get(axis)
        if refswitch is None:
            return

        with self._refswitch_lock:
            self._active_refswitchs.discard(axis)

            # Turn off the ref switch only if no other axis use it
            active = False
            for a, r in self._refswitch.items():
                if r == refswitch and a in self._active_refswitchs:
                    active = True
                    break

            if not active:
                logging.debug("Disabling ref switch power line %d", refswitch)
                self.SetIO(2, refswitch, 0)
                self._leds_on = bool(self._active_refswitchs)
            else:
                logging.debug("Leaving ref switch power line %d active", refswitch)
                
            # Set digital axis outputs to latest requested value
            if not self._leds_on:
                tsleep = 0  # max transition period for all shutters
                for an, val in self._expected_do_pos.items():
                    channel = self._name_to_do_axis[an]
                    _, hpos, lpos, dur = self._do_axes[channel]
                    self.SetIO(2, channel, val == hpos)
                    tsleep = max(tsleep, dur)
                time.sleep(tsleep)
                self._updatePosition()

    def _startReferencingStd(self, axis):
        """
        Start standard referencing procedure. The exact behaviour depends on the
          configuration of the controller.
        The current implementation only supports one axis referencing at a time.
        axis (int)
        raise:
            IOError: if timeout happen
        """
        self._refproc_cancelled[axis].clear()

        if not self._isFullyPowered():
            raise IOError("Device is not powered, so axis %d cannot reference" % (axis,))

        self._resetEncoderDeviation(axis)

        self._requestRefSwitch(axis)
        try:
            # Read the current reference switch value
            refmethod = self.GetAxisParam(axis, 193)
            if refmethod & 0xf >= 5:
                refs = 9  # Home switch
            elif refmethod & 0x40:
                refs = 10  # Right switch
            else:
                refs = 11  # Left switch
            refsval = self.GetAxisParam(axis, refs)
            logging.info("Starting referencing of axis %d (ref switch %d = %d)", axis, refs, refsval)

            self.StartRefSearch(axis)
        except Exception:
            self._releaseRefSwitch(axis)
            raise

    def _waitReferencingStd(self, axis):
        """
        Wait for referencing to be finished.
        axis (int)
        raise:
            IOError: if timeout happen
        """
        # Guess the maximum duration based on the whole range (can't move more
        # than that) at the search speed, + 50% for estimating the switch
        # search. Then double it and add 1 s for margin.
        # We could try to be even more clever, by checking the referencing mode,
        # but that shouldn't affect the time by much.
        ref_speed = self._readSpeed(axis, 194)  # The fast speed
        d = self._ref_max_length[axis]
        dur_search = driver.estimateMoveDuration(d, ref_speed, self._readAccel(axis))
        timeout = max(15, dur_search * 1.5 * 2 + 1)  # s
        logging.debug("Estimating a referencing of at most %g s", timeout)
        endt = time.time() + timeout
        try:
            while time.time() < endt:
                if self._refproc_cancelled[axis].wait(0.01):
                    break
                if not self.GetStatusRefSearch(axis):
                    logging.debug("Referencing procedure ended")
                    break
                try:
                    # Some errors stop the axis from moving but the procedure
                    # status is not updated => explicitly check for the errors.
                    self._checkErrorFlag(axis)
                except HwError:
                    self.StopRefSearch(axis)
                    raise
            else:
                self.StopRefSearch(axis)
                logging.warning("Reference search failed to finish in time")
                raise IOError("Timeout after %g s when referencing axis %d" % (timeout, axis))

            if self._refproc_cancelled[axis].is_set():
                logging.debug("Referencing for axis %d cancelled while running", axis)
                raise CancelledError("Referencing cancelled")

            # Position 0 is automatically set as the current coordinate
            # and the axis stops there. Axis param 197 contains position in the
            # old coordinates.
            oldpos = self.GetAxisParam(axis, 197)
            logging.debug("Changing referencing position by %d", oldpos)
        finally:
            self._releaseRefSwitch(axis)

    def _cancelReferencingStd(self, axis):
        """
        Cancel the referencing. Should only be called after the referencing
          has been started.
        axis (int)
        """
        self._refproc_cancelled[axis].set()
        self.StopRefSearch(axis)
        if self.GetStatusRefSearch(axis):
            logging.warning("Referencing on axis %d still happening after cancelling it", axis)

    # high-level methods (interface)
    def _updatePosition(self, axes=None, do_axes=None):
        """
        update the position VA
        axes (set of str): names of the axes to update or None if all should be
          updated
        """
        # uses the current values (converted to internal representation)
        pos = {}
        for n, i in self._name_to_axis.items():
            if axes is None or n in axes:
                if self._abs_encoder[i] is None:
                    # param 1 = current position
                    pos[n] = self.GetAxisParam(i, 1) * self._ustepsize[i]
                else:
                    # param 209 = encoder position
                    # Note: it's almost like param 215 * 512 / param 210, but
                    # as long as the controller is turned on, it will remember
                    # multiple rotations.
                    pos[n] = self.GetAxisParam(i, 209) * self._ustepsize[i]

        for i, (n, hpos, lpos, _) in self._do_axes.items():
            if do_axes is None or n in do_axes:
                if self.GetIO(2, i):
                    pos[n] = hpos
                else:
                    pos[n] = lpos

        pos = self._applyInversion(pos)

        # Need a lock to ensure that no other thread is updating the position
        # about another axis simultaneously. If this happened, our update would
        # be lost.
        with self._pos_lock:
            if axes is not None:
                pos_full = dict(self.position.value)
                pos_full.update(pos)
                pos = pos_full
            logging.debug("Updated position to %s", pos)
            self.position._set_value(pos, force_write=True)

    def _updateSpeed(self):
        """
        Update the speed VA from the controller settings
        """
        speed = {}
        for n, i in self._name_to_axis.items():
            speed[n] = self._readSpeed(i)
            if speed[n] == 0:
                logging.warning("Speed of axis %s is null, most probably due to a bad hardware configuration", n)

        # it's read-only, so we change it via _value
        self.speed._value = speed
        self.speed.notify(self.speed.value)

    def _readSpeed(self, a, param=4):
        """
        param (int): the parameter number from which to read the speed.
          It's normally 4 (= maximum speed), but could also be 194 (reference
          search) or 195 (reference switch)
        return (float): the speed of the axis in m/s
        """
        velocity = self.GetAxisParam(a, param)
        if self._modl in USE_INTERNAL_UNIT_429:
            # As described in section 6.1.1:
            #       fCLK * velocity
            # usf = ------------------------
            #       2**pulse_div * 2048 * 32
            pulse_div = self.GetAxisParam(a, 154)
            # fCLK = 16 MHz
            usf = (16e6 * velocity) / (2 ** pulse_div * 2048 * 32)
            return usf * self._ustepsize[a]  # m/s
        else:
            # Velocity is directly in µstep/s (aka pps)
            return velocity * self._ustepsize[a]  # m/s

    def _readAccel(self, a):
        """
        return (float): the acceleration of the axis in m/s²
        """
        accel = self.GetAxisParam(a, 5)
        if self._modl in USE_INTERNAL_UNIT_429:
            # Described in section 6.1.2:
            #       fCLK ** 2 * Accel_max
            # a = -------------------------------
            #       2**(pulse_div +ramp_div + 29)
            pulse_div = self.GetAxisParam(a, 154)
            ramp_div = self.GetAxisParam(a, 153)
            # fCLK = 16 MHz
            usa = (16e6 ** 2 * accel) / 2 ** (pulse_div + ramp_div + 29)
            return usa * self._ustepsize[a]  # m/s²
        else:
            # Acceleration is directly in µstep/s² (aka pps²)
            return accel * self._ustepsize[a]  # m/s²

    def _updateTemperatureVA(self):
        """
        Update the temperature VAs, assuming that the 2 analogue inputs are
        connected to a temperature sensor with mapping 10 mV <-> 1 °C. That's
        conveniently what is in the Delphi.
        """
        try:
            # The analogue port return 0..4095 -> 0..10 V
            val = self.GetIO(1, 0) # 0 = first (analogue) port
            v = val * 10 / 4095 # V
            t0 = v / 10e-3 # °C

            val = self.GetIO(1, 4) # 4 = second (analogue) port
            v = val * 10 / 4095 # V
            t1 = v / 10e-3 # °C
        except Exception:
            logging.exception("Failed to read the temperature")
            return

        logging.info(u"Temperature 0 = %g °C, temperature 1 = %g °C", t0, t1)

        self.temperature._value = t0
        self.temperature.notify(t0)
        self.temperature1._value = t1
        self.temperature1.notify(t1)

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
    def moveRel(self, shift):
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)
        dependences = set(shift.keys())

        # Check if the distance is big enough to make sense
        for an, v in list(shift.items()):
            if an in self._name_to_do_axis:
                raise NotImplementedError("Relative move on digital output axis not supported " +
                                          "(requested on axis %s)" % an)
            aid = self._name_to_axis[an]
            if abs(v) < self._ustepsize[aid]:
                # TODO: store and accumulate all the small moves instead of dropping them?
                del shift[an]
                logging.info("Dropped too small move of %g m < %g m",
                             abs(v), self._ustepsize[aid])

        if not shift:
            return model.InstantaneousFuture()

        f = self._createMoveFuture()
        f = self._executor.submitf(dependences, f, self._doMoveRel, f, shift)
        return f

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        for a, p in pos.items():
            if not self.referenced.value.get(a, True) and p != self.position.value[a]:
                logging.warning("Absolute move on axis '%s' which has not be referenced", a)

        pos = self._applyInversion(pos)
        dependences = set(pos.keys())
        f = self._createMoveFuture()
        self._executor.submitf(dependences, f, self._doMoveAbs, f, pos)
        return f
    moveAbs.__doc__ = model.Actuator.moveAbs.__doc__

    def _createRefFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage referencing
        """
        f = CancellableFuture()
        f._init_lock = threading.Lock()  # taken when starting a new axis
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f._current_axis = None  # (int or None) axis which is being referenced
        f.task_canceller = self._cancelReference
        return f

    @isasync
    def reference(self, axes):
        self._checkReference(axes)

        refaxes = set(axes)
        for an in axes:
            if an in self._name_to_do_axis:
                raise ValueError("Cannot reference digital output axis %s." % an)
            if self._abs_encoder[self._name_to_axis[an]]:
                # Absolute axes never need to be referenced
                logging.debug("Attempted to reference absolute axis %s", an)
                refaxes.remove(an)

        if not refaxes:
            return model.InstantaneousFuture()

        if self._refproc == REFPROC_2XFF:
            # Can only run one referencing at a time => block all the other axes too
            dependences = set(self.axes.keys())
        else:
            dependences = set(refaxes)

        f = self._createRefFuture()
        self._executor.submitf(dependences, f, self._doReference, f, refaxes)
        return f
    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self, axes=None):
        # TODO: only cancel the move related to the specified axes. That said,
        # it should be clear that this call might cause other axes to stop.
        # Especially, some hardware don't support per axis stop, and for now,
        # all the other drivers cancel all the axes all the time too.
        self._executor.cancel()

        # For safety, just force stop every axis
        for an, aid in self._name_to_axis.items():
            if axes is None or an in axes:
                self.MotorStop(aid)

    def _checkMoveRelFull(self, shift):
        """
        Check that the argument passed to moveRel() is within range
        shift (dict string -> float): the shift for a moveRel(), in user coordinates
        raise ValueError: if the argument is incorrect
        """
        cur_pos = self.position.value
        refd = self.referenced.value
        for axis, val in shift.items():
            axis_def = self.axes[axis]
            if not hasattr(axis_def, "range"):
                continue

            tgt_pos = cur_pos[axis] + val
            rng = axis_def.range
            if not refd.get(axis, False):
                # Double the range as we don't know where the axis started
                rng_mid = (rng[0] + rng[1]) / 2
                rng_width = rng[1] - rng[0]
                rng = (rng_mid - rng_width, rng_mid + rng_width)

            if not rng[0] <= tgt_pos <= rng[1]:
                # TODO: if it's already outside, then allow to go back
                rng = axis_def.range
                raise ValueError("Position %s for axis %s outside of range %f->%f"
                                 % (val, axis, rng[0], rng[1]))

    def _checkMoveAbs(self, pos):
        """
        Check that the argument passed to moveAbs() is (potentially) correct
        Same as super(), but allows to go 2x the range if the axis is not referenced
        pos (dict string -> float): the new position for a moveAbs()
        raise ValueError: if the argument is incorrect
        """
        refd = self.referenced.value
        for axis, val in pos.items():
            if axis in self.axes:
                axis_def = self.axes[axis]
                if hasattr(axis_def, "choices") and val not in axis_def.choices:
                    raise ValueError("Unsupported position %s for axis %s"
                                     % (val, axis))
                elif hasattr(axis_def, "range"):
                    rng = axis_def.range
                    # TODO: do we really need to allow this? Absolute move without
                    # referencing is not recommended anyway.
                    if not refd.get(axis, False):
                        # Double the range as we don't know where the axis started
                        rng_mid = (rng[0] + rng[1]) / 2
                        rng_width = rng[1] - rng[0]
                        rng = (rng_mid - rng_width, rng_mid + rng_width)

                    if not rng[0] <= val <= rng[1]:
                        raise ValueError("Position %s for axis %s outside of range %f->%f"
                                         % (val, axis, rng[0], rng[1]))
            else:
                raise ValueError("Unknown axis %s" % (axis,))

    def _doMoveRel(self, future, pos):
        """
        Blocking and cancellable relative move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> relative target position
        raise:
            ValueError: if the target position is
            TMCLError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:
            self._checkMoveRelFull(self._applyInversion(pos))

            end = 0 # expected end
            moving_axes = set()
            for an, v in pos.items():
                aid = self._name_to_axis[an]
                moving_axes.add(aid)
                usteps = int(round(v / self._ustepsize[aid]))
                # Reset the current position to the one reported by the encoder
                # so that the deviation doesn't accumulate, and eventually
                # triggers an error without proper reason.
                self._resetEncoderDeviation(aid)
                self.MoveRelPos(aid, usteps)
                # compute expected end
                try:
                    d = abs(usteps) * self._ustepsize[aid]
                    dur = driver.estimateMoveDuration(d, self.speed.value[an], self._accel[an])
                except Exception: # Can happen if config is wrong and report speed or accel == 0
                    logging.exception("Failed to estimate move duration")
                    dur = 60
                end = max(time.time() + dur, end)

            self._waitEndMove(future, moving_axes, None, end)
        logging.debug("move successfully completed")

    def _doMoveAbs(self, future, pos):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> absolute target position
        raise:
            TMCLError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:
            end = 0 # expected end
            old_pos = self._applyInversion(self.position.value)
            moving_axes = set()
            moving_do_axes = set()
            for an, v in pos.items():
                # Check if it's a digital output
                if an in self._name_to_do_axis:
                    channel = self._name_to_do_axis[an]
                    _, hpos, lpos, dur = self._do_axes[channel]
                    with self._refswitch_lock:  # don't start do move at the same time as referencing
                        if self._leds_on and channel in self._led_prot_do:
                            # don't move protected do axis now if leds are on, schedule for later
                            self._expected_do_pos[an] = v
                            if v != self._led_prot_do[channel]:
                                logging.info("Referencing LEDs are on, move on axis %s to %s will be delayed.", an, v)
                        else:
                            # otherwise allow change
                            logging.info("Setting digital output on channel %s to %s." % (channel, v == hpos))
                            self.SetIO(2, channel, v == hpos)
                            moving_do_axes.add(channel)
                            end = max(end, time.time() + dur)
                else:
                    # it's a regular move
                    aid = self._name_to_axis[an]
                    moving_axes.add(aid)
                    usteps = int(round(v / self._ustepsize[aid]))
                    # Actual position is the one used for absolute move, so need to
                    # always reset it when there is an encoder
                    self._resetEncoderDeviation(aid, always=True)
                    self.MoveAbsPos(aid, usteps)
                    # compute expected end
                    try:
                        d = abs(v - old_pos[an])
                        dur = driver.estimateMoveDuration(d, self.speed.value[an], self._accel[an])
                    except Exception:  # Can happen if config is wrong and report speed or accel == 0
                        logging.exception("Failed to estimate move duration")
                        dur = 60
                    end = max(time.time() + dur, end)
            self._waitEndMove(future, moving_axes, moving_do_axes, end)
        logging.debug("move successfully completed")

    def _waitEndMove(self, future, axes, do_axes=None, end=0):
        """
        Wait until all the given axes are finished moving, or a request to
        stop has been received.
        future (Future): the future it handles
        axes (set of int): the axes IDs to check
        do_axes (set of int): channel numbers of moves on digital output axes
        end (float): expected end time
        raise:
            TimeoutError: if took too long to finish the move
            CancelledError: if cancelled before the end of the move
        """
        do_axes = do_axes or {}
        moving_axes = set(axes)
        moving_do_axes = set(do_axes)
        last_upd = time.time()
        startt = time.time()
        dur = max(0.01, min(end - last_upd, 100))
        max_dur = dur * 2 + 1
        logging.debug("Expecting a move of %g s, will wait up to %g s", dur, max_dur)
        timeout = last_upd + max_dur
        last_axes = moving_axes.copy()
        try:
            while not future._must_stop.is_set():
                for aid in moving_axes.copy(): # need copy to remove during iteration
                    if self._isOnTarget(aid):
                        moving_axes.discard(aid)
                    # Check whether the move has stopped due to an error
                    self._checkErrorFlag(aid)

                now = time.time()
                for ch in moving_do_axes.copy():
                    if now > startt + self._do_axes[ch][3]:
                        # finished waiting for do channel
                        moving_do_axes.discard(ch)

                if not moving_axes and not moving_do_axes:
                    # no more axes to wait for
                    break

                if now > timeout:
                    logging.warning("Stopping move due to timeout after %g s.", max_dur)
                    for i in moving_axes:
                        self.MotorStop(i)
                    raise TimeoutError("Move is not over after %g s, while "
                                       "expected it takes only %g s" %
                                       (max_dur, dur))

                # Update the position from time to time (10 Hz)
                if now - last_upd > 0.1 or last_axes != moving_axes:
                    last_names = set(n for n, i in self._name_to_axis.items() if i in last_axes)
                    self._updatePosition(last_names)
                    last_upd = time.time()
                    last_axes = moving_axes.copy()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0.001, min(left / 2, 0.1))
                future._must_stop.wait(sleept)
            else:
                logging.debug("Move of axes %s, %s cancelled before the end", axes, do_axes)
                # stop all axes still moving them
                for i in moving_axes:
                    self.MotorStop(i)
                future._was_stopped = True
                raise CancelledError()
        finally:
            # TODO: check if the move succeded ? (= Not failed due to stallguard/limit switch)
            self._updatePosition() # update (all axes) with final position

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

        future._must_stop.set() # tell the thread taking care of the move it's over
        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped

    def _doReference(self, future, axes):
        """
        Actually runs the referencing code
        future (Future): the future it handles
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
                # TODO: with the "standard" referencing, we could run them
                # simultaneously
                for a in axes:
                    with future._init_lock:
                        if future._must_stop.is_set():
                            raise CancelledError()
                        aid = self._name_to_axis[a]
                        future._current_axis = aid
                        self.referenced._value[a] = False
                        self._startReferencing(aid)
                    self._waitReferencing(aid)  # block until it's over
                    # If the referencing went fine, the "actual" position and
                    # encoder position are reset to 0
                    self.referenced._value[a] = True
                    future._current_axis = None
            except CancelledError as ex:
                logging.info("Referencing cancelled: %s", ex)
                future._was_stopped = True
                raise
            except Exception:
                logging.exception("Referencing failure")
                raise
            finally:
                # We only notify after updating the position so that when a listener
                # receives updates both values are already updated.
                self._updatePosition(axes)
                # read-only so manually notify
                self.referenced.notify(self.referenced.value)
                # Update the global variable, based on the referenced axes
                self._update_ref()

    def _cancelReference(self, future):
        # The difficulty is to synchronise correctly when:
        #  * the task is just starting (about to request axes to move)
        #  * the task is finishing (about to say that it finished successfully)
        logging.debug("Cancelling current referencing")

        future._must_stop.set()  # tell the thread taking care of the referencing it's over
        with future._init_lock:
            # cancel the referencing on the current axis
            aid = future._current_axis
            if aid is not None:
                self._cancelReferencing(aid)  # It's ok to call this even if the axis is not referencing

        # Synchronise with the ending of the future
        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped

    def _findDevice(self, port, address=None):
        """
        Look for a compatible device
        port (str): pattern for the port name
        address (None or int): the address of the
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

        hwerror = None
        for n in names:
            try:
                serial = self._openSerialPort(n)
            except IOError as ex:
                if isinstance(ex, HwError):
                    hwerror = ex
                # not possible to use this port? next one!
                logging.info("Skipping port %s, which is not available (%s)", n, ex)
                continue

            # check whether it answers with the right address
            try:
                # If any garbage was previously received, make it discarded.
                self._serial = serial
                ra = self._resynchonise()
                if address is None or ra == address:
                    logging.debug("Found device with address %d on port %s", ra, n)
                    return serial, ra  # found it!
            except Exception as ex:
                logging.debug("Port %s doesn't seem to have a TMCM device connected: %s",
                              n, ex)
            serial.close()  # make sure to close/unlock that port
        else:
            if address is None:
                if len(names) == 1 and hwerror:
                    # The user wanted any device, and there is one, but which is
                    # not available => be more specific in the error message
                    raise hwerror
                raise HwError("Failed to find a TMCM controller on ports '%s'."
                              "Check that the device is turned on and "
                              "connected to the computer." % (port,))
            else:
                raise HwError("Failed to find a TMCM controller on ports '%s' with "
                              "address %s. Check that the device is turned on and "
                              "connected to the computer." % (port, address))

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a Thorlabs APT device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        raise HwError: if the serial port cannot be opened (doesn't exist, or
          already opened)
        """
        # For debugging purpose
        if port == "/dev/fake" or port == "/dev/fake3":
            return TMCMSimulator(timeout=0.1, naxes=3)
        elif port == "/dev/fake1":
            return TMCMSimulator(timeout=0.1, naxes=1)
        elif port == "/dev/fake6":
            return TMCMSimulator(timeout=0.1, naxes=6)

        # write_timeout is only support in PySerial v3+
        kwargs = {}
        ser_maj_ver = int(serial.VERSION.split(".")[0])
        if ser_maj_ver >= 3:
            # should never be needed... excepted that sometimes write() blocks
            kwargs["write_timeout"] = 1  # s

        try:
            ser = serial.Serial(
                port=port,
                baudrate=9600, # TODO: can be changed by RS485 setting p.85?
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,  # s
                **kwargs
            )
        except IOError:
            raise HwError("Failed to find a TMCM controller on port '%s'. "
                          "Check that the device is turned on and "
                          "connected to the computer." % (port,))

        # Ensure we are the only one connected to it
        try:
            fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            raise HwError("Device on port %s is already in use. Ensure Odemis "
                          "is not already running." % (port,))

        return ser

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, kwargs
        Note: it's obviously not advised to call this function if a device is already under use
        """
        # TODO: use serial.tools.list_ports.comports() (but only availabe in pySerial 2.6)
        if os.name == "nt":
            ports = ["COM" + str(n) for n in range(8)]
        else:
            ports = glob.glob('/dev/ttyACM?*')

        logging.info("Scanning for TMCM controllers in progress...")
        found = []  # (list of 2-tuple): name, kwargs
        for p in ports:
            try:
                logging.debug("Trying port %s", p)
                dev = cls(None, None, p, address=None, axes=["x"], ustepsize=[10e-9])
                modl, vmaj, vmin = dev.GetVersion()
                address = dev._target
                # Guess the number of axes based on the model name (ie, the first number)
                naxes = int(str(modl)[0])
            except IOError:
                # not possible to use this port? next one!
                continue
            except Exception:
                logging.exception("Error while communicating with port %s", p)
                continue

            found.append(("TMCM-%s" % modl,
                          {"port": p,
                           "address": address,
                           "axes": ["x", "y", "z"][:naxes],
                           "ustepsize": [10e-9] * naxes})
                        )

        return found


# Former name, just for compatibility with old config files
class TMCM3110(TMCLController):
    pass


class TMCMSimulator(object):
    """
    Simulates a TMCM-3110 or -6110 (+ serial port). Only used for testing.
    Same interface as the serial port
    """

    def __init__(self, timeout=0, naxes=3, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = b"" # what the commands sends back to the "host computer"
        self._input_buf = b"" # what we receive from the "host computer"

        self._naxes = naxes

        # internal state
        self._id = 1

        # internal global param values
        # 4 * dict(int -> int: param number -> value)
        self._gstate = [{}, {},
                        # Bank 2: example user config v1
                        {0: 168034562, 1: 50462723, 2: 17104896},
                        {}]

        # internal axis param values
        # int -> int: param number -> value
        self._orig_axis_state = {
                           0: 0,  # target position
                           1: 0, # current position (unused directly)
                           4: 1024, # maximum positioning speed
                           5: 7,  # maximum acceleration
                           6: 80,  # maximum current
                           8: 1, # target reached? (unused directly)
                           153: 0,  # ramp div
                           154: 3, # pulse div
                           194: 1024,  # reference search speed
                           195: 200,  # reference switch speed
                           197: 10,  # previous position before referencing (unused directly)
        }
        self._astates = [dict(self._orig_axis_state) for i in range(self._naxes)]
        self._do_states = [1, 0, 0, 0, 0, 0, 0, 0]  # state of digital outputs on bank 2 (0 or 1)

        # (float, float, int) for each axis
        # start, end, start position of a move
        self._axis_move = [(0, 0, 0)] * self._naxes

        # time at which the referencing ends
        self._ref_move = [0] * self._naxes

    def _getCurrentPos(self, axis):
        """
        return (int): position in microsteps
        """
        now = time.time()
        startt, endt, startp = self._axis_move[axis]
        endp = self._astates[axis][0]
        if endt < now:
            return endp
        # model as if it was linear (it's not, it's ramp-based positioning)
        pos = startp + (endp - startp) * (now - startt) / (endt - startt)
        return pos

    def _getMaxSpeed(self, axis):
        """
        return (float): speed in microsteps/s
        """
        velocity = self._astates[axis][4]
        pulse_div = self._astates[axis][154]
        usf = (16e6 * velocity) / (2 ** pulse_div * 2048 * 32)
        return usf # µst/s

    def write(self, data):
        # We accept both a string/bytes and numpy array
        if isinstance(data, numpy.ndarray):
            data = data.tostring()
        self._input_buf += data

        # each message is 9 bytes => take the first 9 and process them
        while len(self._input_buf) >= 9:
            msg = self._input_buf[:9]
            self._input_buf = self._input_buf[9:]
            self._parseMessage(msg) # will update _output_buf

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

    def _sendReply(self, inst, status=100, val=0):
        msg = numpy.empty(9, dtype=numpy.uint8)
        struct.pack_into('>BBBBiB', msg, 0, 2, self._id, status, inst, int(val), 0)
        # compute the checksum (just the sum of all the bytes)
        msg[-1] = numpy.sum(msg[:-1], dtype=numpy.uint8)

        self._output_buf += msg.tostring()

    def _parseMessage(self, msg):
        """
        msg (buffer of length 9): the message to parse
        return None: self._output_buf is updated if necessary
        """
        target, inst, typ, mot, val, chk = struct.unpack('>BBBBiB', msg)
#         logging.debug("SIM: parsing %s", TMCL._instr_to_str(msg))

        # Check it's a valid message... for us
        npmsg = numpy.frombuffer(msg, dtype=numpy.uint8)
        good_chk = numpy.sum(npmsg[:-1], dtype=numpy.uint8)
        if chk != good_chk:
            self._sendReply(inst, status=1) # "Wrong checksum" message
            return
        if target not in {self._id, 0}:
            logging.warning("SIM: skipping message for %d", target)
            # The real controller doesn't seem to care

        # decode the instruction
        if inst == 3: # Motor stop
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            # Note: the target position in axis param is not changed in the
            # real controller, but to more easily simulate half-way stop, we
            # update the target position to the current pos.
            self._astates[mot][0] = self._getCurrentPos(mot)
            self._axis_move[mot] = (0, 0, 0)
            self._sendReply(inst)
        elif inst == 4: # Move to position
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if typ not in (0, 1, 2):
                self._sendReply(inst, status=3) # wrong type
                return
            pos = self._getCurrentPos(mot)
            if typ == 1: # Relative
                # convert to absolute and continue
                val += pos
            elif typ == 2: # Coordinate
                raise NotImplementedError("simulator doesn't support coordinates")
            # new move
            now = time.time()
            end = now + abs(pos - val) / self._getMaxSpeed(mot)
            self._astates[mot][0] = val
            self._axis_move[mot] = (now, end, pos)
            self._sendReply(inst, val=val)
        elif inst == 5: # Set axis parameter
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3)  # wrong type
                return
            # Warning: we don't handle special addresses
            if typ == 1: # actual position
                self._astates[mot][0] = val # set target position, which will be used for current pos
            else:
                self._astates[mot][typ] = val
            self._sendReply(inst, val=val)
        elif inst == 6: # Get axis parameter
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3) # wrong type
                return
            # special code for special values
            if typ == 1: # actual position
                rval = self._getCurrentPos(mot)
            elif typ == 209:  # encoder position (simulated as actual position)
                rval = self._getCurrentPos(mot)
            elif typ == 8: # target reached?
                rval = 0 if self._axis_move[mot][1] > time.time() else 1
            elif typ in (10, 11):  # left/right switch
                rval = random.randint(0, 1)
            elif typ in (207, 208):  # error flags
                rval = 0  # no error
            else:
                rval = self._astates[mot].get(typ, 0) # default to 0
            self._sendReply(inst, val=rval)
        elif inst == 8:  # Restore axis param
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4)  # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3)  # wrong type
                return
            self._astates[mot][typ] = self._orig_axis_state.get(typ, 0)
            self._sendReply(inst, val=0)
        elif inst == 9:  # Set global param
            if not 0 <= mot < len(self._gstate):
                self._sendReply(inst, status=4)  # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3)  # wrong type
                return
            self._gstate[mot][typ] = val
            self._sendReply(inst, val=val)
        elif inst == 10:  # Get global param
            if not 0 <= mot < len(self._gstate):
                self._sendReply(inst, status=4)  # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3)  # wrong type
                return
            val = self._gstate[mot].get(typ, 0)  # 0 value by default
            self._sendReply(inst, val=val)
        elif inst == 11:  # Store global param
            if not 0 <= mot < len(self._gstate):
                self._sendReply(inst, status=4)  # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3)  # wrong type
                return
            # Nothing to really do
            self._sendReply(inst, val=0)
        elif inst == 13: # ref search-related instructions
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if typ == 0:  # start
                self._ref_move[mot] = time.time() + 5  # s, duration of ref search
                # Simulate previous position
                self._astates[mot][197] = random.randint(-1000, 1000)
                self._sendReply(inst)
            elif typ == 1: # stop
                self._ref_move[mot] = 0
                self._sendReply(inst)
            elif typ == 2:  # status
                if self._ref_move[mot] > time.time():
                    rval = 1  # still running
                else:
                    # Hack: it needs to set position to 0 once finished.
                    # Instead of using a timer, hope there will be a status
                    # check within a second.
                    if self._ref_move[mot] + 1 > time.time():
                        self._astates[mot][0] = 0
                        self._astates[mot][1] = 0
                    rval = 0
                self._sendReply(inst, val=rval)
            else:
                self._sendReply(inst, status=3) # wrong type
                return
        elif inst == 14:  # Set IO
            if mot not in (0, 2):
                self._sendReply(inst, status=4)  # invalid value
                return
            if not 0 <= typ <= 7:
                self._sendReply(inst, status=3)  # wrong type
                return
            # Change internal do value
            if mot == 2:
                self._do_states[typ] = val
            self._sendReply(inst)
        elif inst == 15: # Get IO
            if not 0 <= mot <= 2:
                self._sendReply(inst, status=4) # invalid value
                return
            if mot == 0: # digital inputs
                if not 0 <= typ <= 7:
                    self._sendReply(inst, status=3)  # wrong type
                    return
                rval = 0 # between 0..1
            elif mot == 1: # analogue inputs
                if typ not in (0, 4, 8):
                    self._sendReply(inst, status=3)  # wrong type
                    return
                rval = 178 # between 0..4095
            elif mot == 2: # digital outputs
                if not 0 <= typ <= 7:
                    self._sendReply(inst, status=3)  # wrong type
                    return
                rval = self._do_states[typ]  # between 0..1
            self._sendReply(inst, val=rval)
        elif inst == 136: # Get firmware version
            if typ == 0: # string
                raise NotImplementedError("Can't simulated GFV string")
            elif typ == 1: # binary
                modl = (self._naxes * 1000 + 110)
                val = (modl << 16) + 0x0109  # eg: 3110 v1.09
                self._sendReply(inst, val=val)
            else:
                self._sendReply(inst, status=3) # wrong type
        elif inst == 138: # Request Target Position Reached Event
            raise NotImplementedError("Can't simulated RTP string")
        else:
            logging.warning("SIM: Unsupported instruction %d", inst)
            self._sendReply(inst, status=2) # wrong instruction


class CANController(model.Actuator):
    """
    Represents one Trinamic TMCL-compatible controller using a CANopen interface.
    """

    def __init__(self, name, role, channel, node_id, datasheet, axes, ustepsize,
                 param_file=None, rng=None, unit=None, refproc=None, **kwargs):
        """
        channel (str): can port name, on linux, this is typically "can0". For testing
            with the simulator, use "fake".
        node_id (0 <= int <= 255): Address of the controller
        datasheet (str): absolute or relative path to .dcf configuration file
        axes (list of str): names of the axes, from the 1st to the last.
          If an axis is not connected, put a "".
        ustepsize (list of float): size of a microstep in unit (the smaller, the
          bigger will be a move for a given distance in m)
        param_file (str or None): (absolute) path to a tmcm.tsv file which will
          be used to initialise the axis parameters (and IO).
        rng (list of tuples of 2 floats or None): min/max position allowed for
          each axis. 0 must be part of the range.
          Note: If the axis is inverted, the values provided will be inverted too.
        unit (None or list of str): The unit of each axis. When it's None, it
          defaults to "m" for all the axes.
        refproc (str or None): referencing (aka homing) procedure type. Use
          None to indicate it's not possible (no reference/limit switch) or the
          name of the procedure. For now only "Standard" is accepted.
        """

        # If canopen module is not available, only fail loading at runtime, as
        # to allow still loading the Serial/USB TMCM component
        # TODO: drop it once python3-canopen is part of the Odemis dependencies
        if canopen is None:
            raise HwError("CANopen module missing, run \"sudo apt install python3-canopen\".")

        if len(axes) != len(ustepsize):
            raise ValueError("Expecting %d ustepsize (got %s)" % (len(axes), ustepsize))

        self._name_to_axis = {}  # str -> int: name -> axis number
        if rng is None:
            rng = [None] * len(axes)
        rng += [None] * (len(axes) - len(rng))  # ensure it's long enough

        if unit is None:
            unit = ["m"] * len(axes)
        elif len(unit) != len(axes):
            raise ValueError("unit argument must be the same length as axes")

        for i, n in enumerate(axes):
            if not n:  # skip this non-connected axis
                continue
            # sz is typically ~1µm, so > 1 cm is very fishy
            # in case of rad as unit, sz is typically ~1e-4 rad/m, so it should also be < 0.01
            sz = ustepsize[i]
            if not (0 < sz <= 10e-3):
                raise ValueError("ustepsize should be between 0 and 10 mm, but got %g m" % (sz,))
            self._name_to_axis[n] = i

        self._ustepsize = ustepsize

        # Only support standard referencing and None
        if not (refproc == REFPROC_STD or refproc is None):
            raise ValueError("Reference procedure %s unknown" % (refproc,))

        # Get path of datasheet
        if not os.path.isabs(datasheet):
            datasheet = os.path.join(os.path.dirname(__file__), datasheet)

        self._network, self._node = self._openCanNode(channel, node_id, datasheet)

        # For ensuring only one updatePosition() at the same time
        self._pos_lock = threading.Lock()

        self._modl, hw_version, sw_version, vmaj, vmin = self.GetVersion()
        if self._modl not in KNOWN_MODELS_CAN:
            logging.warning("Controller TMCM-%d is not supported, will try anyway",
                            self._modl)

        # Do not leave canopen log to DEBUG, even if the general log level is set
        # to DEBUG, because it generates logs for every CAN packet, which is too much.
        canlog = logging.getLogger("canopen")
        canlog.setLevel(max(canlog.getEffectiveLevel(), logging.INFO))

        # Check that the device support that many axes
        try:
            self.GetAxisParam(max(self._name_to_axis.values()), ACTUAL_POSITION)  # current pos
        except canopen.sdo.SdoCommunicationError as ex:
            raise ValueError("Device %s doesn't support %d axes (got %s)" %
                             (name, max(self._name_to_axis.values()) + 1, axes))

        # will take care of executing axis move asynchronously
        self._executor = ParallelThreadPoolExecutor()  # one task at a time

        self._ref_max_length = {}  # int -> float: axis ID -> max distance during referencing
        axes_def = {}
        for n, i in self._name_to_axis.items():
            if not n:
                continue
            sz = ustepsize[i]
            phy_rng = ((-2 ** 31) * sz, (2 ** 31 - 1) * sz)
            sw_rng = rng[i]
            if sw_rng is not None:
                if not sw_rng[0] <= 0 <= sw_rng[1]:
                    raise ValueError("Range of axis %d doesn't include 0: %s" % (i, sw_rng))
                phy_rng = (max(phy_rng[0], sw_rng[0]), min(phy_rng[1], sw_rng[1]))
                self._ref_max_length[i] = phy_rng[1] - phy_rng[0]
            else:
                # For safety, for referencing timeout, consider that the range
                # is not too long (ie, 4M µsteps).
                # If it times out, the user should specify an axis range.
                self._ref_max_length[i] = sz * 4e6  # m

            if not isinstance(unit[i], basestring):
                raise ValueError("unit argument must only contain strings, but got %s" % (unit[i],))
            axes_def[n] = model.Axis(range=phy_rng, unit=unit[i])
            try:
                self._checkErrorFlag(i)
            except HwError as ex:
                # Probably some old error left-over, no need to worry too much
                logging.warning(str(ex))

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        if param_file:
            try:
                f = open(param_file)
            except Exception as ex:
                raise ValueError("Failed to open file %s: %s" % (param_file, ex))
            try:
                axis_params = self.parse_tsv_config(f)
            except Exception as ex:
                raise ValueError("Failed to parse file %s: %s" % (param_file, ex))
            f.close()
            logging.debug("Extracted param file config: %s", axis_params)
            self.apply_config(axis_params)

        try:
            self._node.op_mode = PP_MODE
        except SdoAbortedError:
            # Will be raised if we're already in PP_MODE
            pass

        self._swVersion = "CANopen %s" % sw_version
        self._hwVersion = "%s (firmware %d.%02d)" % (self._modl, vmaj, vmin)

        self.position = model.VigilantAttribute({}, readonly=True)
        self._updatePosition()

        # TODO: for axes with encoders, refresh position regularly

        # TODO: add support for changing speed.
        self.speed = model.VigilantAttribute({}, readonly=True)
        self._updateSpeed()

        self._accel = {}
        for n, i in self._name_to_axis.items():
            self._accel[n] = self._readAccel(i)
            if self._accel[n] == 0:
                logging.warning("Acceleration of axis %s is null, most probably due to a bad hardware configuration", n)

        if refproc is None:
            axes_ref = {}
        else:
            axes_ref = {a: False for a, i in self._name_to_axis.items()}

        self.referenced = model.VigilantAttribute(axes_ref, readonly=True)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        # Disconnect from the CAN bus
        if self._network:
            logging.debug("Shutting down device...")
            self._node.nmt.state = 'PRE-OPERATIONAL'
            self._network.sync.stop()
            self._network.disconnect()

    def apply_config(self, axis_params):
        """
        Configure the device according to the given 'user configuration'.
        axis_params (dict (int, int) -> int): axis number/param number -> value
        """
        self._node.state = SWITCH_ON_DISABLED
        for (ax, ad), v in axis_params.items():
            self.SetAxisParam(ax, ad, v)
        self._node.state = OPERATION_ENABLED

        # There is a small bug in the canopen library. By default, after entering "QUICK STOP ACTIVE" state,
        # the state is automatically changed to "SWITCH ON DISABLED". However, when calling "QUICK STOP ACTIVE",
        # the state setter (canopen.profiles.p402 l 445) will try to change the state to "QUICK STOP ACTIVE" in a loop
        # until this state is reached. If the state is changed automatically from "QUICK STOP ACTIVE" to "SWITCHED ON
        # DISABLE" before the loop condition is evaluated, it will attempt a transition from "SWITCHED ON DISABLE" to
        # "QUICK STOP ACTIVE". This transition is not allowed, so it will raise an error.
        # To avoid this, we change the settings to disable the automatic change to "SWITCH ON DISABLED" state
        # and do it manually in the StopAxis() function.
        for axis in self._name_to_axis.values():
            val = self.GetAxisParam(axis, QUICK_STOP_OPTION)
            if val != 6:
                logging.warning("Quick stop option %s != 6 is not supported by current canopen version." % val)

    # Low level functions
    def GetVersion(self, axis=0):
        """
        return (str, str, str, int, int):
             Controller ID
             Hardware version
             Software version
             Firmware major version number
             Firmware minor version number
        """
        cont = self.GetAxisParam(axis, DEVICE_NAME)
        hw_version = self.GetAxisParam(axis, HW_VERSION)  # e.g. '1.0'
        sw_version = self.GetAxisParam(axis, SW_VERSION)
        id = int(self.GetAxisParam(axis, IDENTITY, 3))
        vmaj = (0xFF00 & id) >> 16  # first 16 bits
        vmin = 0x00FF & id  # last 16 bits
        return cont, hw_version, sw_version, vmaj, vmin

    def GetAxisParam(self, axis, param, idx=None):
        """
        Read the axis/parameter setting from the RAM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        return (0<=int): the value stored for the given axis/parameter
        idx in case of record object
        """
        if idx:
            val = self._node.sdo[param + 0x800 * axis][idx].raw
        else:
            val = self._node.sdo[param + 0x800 * axis].raw
        return val

    def SetAxisParam(self, axis, param, val):
        """
        Write the axis/parameter setting from the RAM
        axis (0<=int<=3): axis number
        param (0<=int<=255): parameter number
        val (int): the value to store
        """
        # Axis 0 start with 0, axis 1 with 0x800, etc.
        self._node.sdo[param + 0x800 * axis].raw = val

    def MoveAbsPos(self, axis, pos):
        """
        Requests a move to an absolute position. This is non-blocking.
        axis (0<=int<=5): axis number
        pos (-2**31 <= int 2*31-1): position
        """
        self.SetAxisParam(axis, TARGET_POSITION, pos)
        # Bit 6 of controlword: absolute (0) or relative (1)
        # Bits 0-3 to 1: switch on and enable (see p 71 of canopen firmware manual)
        # Bit 4: start positioning
        # Controlword has to be set twice, cf MoveRelPos
        self._node.controlword = 0b0001111  # switch on
        self._node.controlword = 0b0011111

    def MoveRelPos(self, axis, offset):
        """
        Requests a move to a relative position. This is non-blocking.
        axis (0<=int<=5): axis number
        offset (-2**31 <= int 2*31-1): relative position
        """
        self.SetAxisParam(axis, TARGET_POSITION, offset)
        # Bit 6 of controlword: absolute (0) or relative (1)
        # Bits 0-3 to 1: switch on and enable (see p 71 of canopen firmware manual)
        # Bit 4: start positioning
        # We need to set the controlword twice, first to get into the "switched on" state, then
        # to go to the "operation enabled" state.
        self._node.controlword = 0b0001111  # switch on (state transition 3)
        self._node.controlword = 0b1011111  # operation enable (state transition 4)

    def MotorStop(self):
        """
        Stop all axes. It's not possible to only stop a single axis.
        """
        if self._node.state == OPERATION_ENABLED:
            # only allowed state from which to transition to quick stop active
            self._node.state = QUICK_STOP_ACTIVE
            self._node.state = SWITCH_ON_DISABLED
        self._node.state = READY_TO_SWITCH_ON  # back to state from where we can start a command

    def GetStatusRefSearch(self, axis):
        """
        return (bool): False if reference is not active, True if reference is active.
        """
        self._node.op_mode = HOMING_MODE
        stat = self._node.statusword
        # 10th bit (0x400) is set if zero position has been found or homing has been stopped
        # 12th bit (0x1000) is set if position is found
        homing_active = not (stat & 0x400) or (stat & 0x400 and not stat & 0x1000)
        self._node.op_mode = PP_MODE
        return bool(homing_active)

    def _isOnTarget(self, axis):
        """
        return (bool): True if the target position is reached
        """
        # 14th bit (target reached): 1 if target reached
        # ._node.statusword is not updated frequently enough, so directly ask for the status via .sdo
        return bool((self._node.sdo[STATUS_WORD].raw & 0x400))

    def _checkErrorFlag(self, axis):
        """
        Raises an HwError if the axis error flag reports an issue
        """
        stat = self._node.statusword
        if stat & 0b1000:
            raise HwError("Fault detected.")

    def _cancelReference(self, future):
        # The difficulty is to synchronise correctly when:
        #  * the task is just starting (about to request axes to move)
        #  * the task is finishing (about to say that it finished successfully)
        logging.debug("Cancelling current referencing")

        future._must_stop.set()  # tell the thread taking care of the referencing it's over
        with future._init_lock:
            # cancel the referencing on the current axis
            self.MotorStop()  # It's ok to call this even if the axis is not referencing
        return True

    # high-level methods (interface)
    def _updatePosition(self, axes=None):
        """
        update the position VA
        axes (set of str): names of the axes to update or None if all should be
          updated
        """
        # uses the current values (converted to internal representation)
        pos = {}
        for n, i in self._name_to_axis.items():
            if axes is None or n in axes:
                pos[n] = self.GetAxisParam(i, ACTUAL_POSITION) * self._ustepsize[i]  # if encoder is not avilable? same? TODO

        pos = self._applyInversion(pos)

        # Need a lock to ensure that no other thread is updating the position
        # about another axis simultaneously. If this happened, our update would
        # be lost.
        with self._pos_lock:
            if axes is not None:
                pos_full = dict(self.position.value)
                pos_full.update(pos)
                pos = pos_full
            logging.debug("Updated position to %s", pos)
            self.position._set_value(pos, force_write=True)

    def _updateSpeed(self):
        """
        Update the speed VA from the controller settings
        """
        speed = {}
        for n, i in self._name_to_axis.items():
            speed[n] = self._readSpeed(i)
            if speed[n] == 0:
                logging.warning("Speed of axis %s is null, most probably due to a bad hardware configuration", n)

        # it's read-only, so we change it via _value
        self.speed._value = speed
        self.speed.notify(self.speed.value)

    def _readSpeed(self, a):
        """
        return (float): the speed of the axis in m/s
        """
        return float(self.GetAxisParam(a, PROFILE_VELOCITY)) * self._ustepsize[a]  # profile velocity

    def _readAccel(self, a):
        """
        return (float): the acceleration of the axis in m/s²
        """
        return float(self.GetAxisParam(a, PROFILE_ACCELERATION)) * self._ustepsize[a]  # profile acceleration

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
    def moveRel(self, shift):
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)
        dependences = set(shift.keys())

        # Check if the distance is big enough to make sense
        for an, v in list(shift.items()):
            aid = self._name_to_axis[an]
            if abs(v) < self._ustepsize[aid]:
                # TODO: store and accumulate all the small moves instead of dropping them?
                del shift[an]
                logging.info("Dropped too small move of %g m < %g m",
                             abs(v), self._ustepsize[aid])

        if not shift:
            return model.InstantaneousFuture()

        f = self._createMoveFuture()
        f = self._executor.submitf(dependences, f, self._doMoveRel, f, shift)
        return f

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        for a, p in pos.items():
            if not self.referenced.value.get(a, True) and p != self.position.value[a]:
                logging.warning("Absolute move on axis '%s' which has not be referenced", a)

        pos = self._applyInversion(pos)
        dependences = set(pos.keys())
        f = self._createMoveFuture()
        self._executor.submitf(dependences, f, self._doMoveAbs, f, pos)
        return f

    moveAbs.__doc__ = model.Actuator.moveAbs.__doc__

    def _createRefFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage referencing
        """
        f = CancellableFuture()
        f._init_lock = threading.Lock()  # taken when starting a new axis
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f._current_axis = None  # (int or None) axis which is being referenced
        f.task_canceller = self._cancelReference
        return f

    @isasync
    def reference(self, axes):
        self._checkReference(axes)

        refaxes = set(axes)
        if not refaxes:
            return model.InstantaneousFuture()

        dependences = set(refaxes)
        f = self._createRefFuture()
        self._executor.submitf(dependences, f, self._doReference, f, refaxes)
        return f

    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self, axes=None):
        """
        Stop all axes and cancel pending moves.
        It is not possible to stop the axes individually.
        :param axes: will be ignored (just there to match signature of Actuator class)
        """
        self._executor.cancel()
        self.MotorStop()

    def _checkMoveRelFull(self, shift):
        """
        Check that the argument passed to moveRel() is within range
        shift (dict string -> float): the shift for a moveRel(), in user coordinates
        raise ValueError: if the argument is incorrect
        """
        cur_pos = self.position.value
        refd = self.referenced.value
        for axis, val in shift.items():
            axis_def = self.axes[axis]
            if not hasattr(axis_def, "range"):
                continue

            tgt_pos = cur_pos[axis] + val
            rng = axis_def.range
            if not refd.get(axis, False):
                # Double the range as we don't know where the axis started
                rng_mid = (rng[0] + rng[1]) / 2
                rng_width = rng[1] - rng[0]
                rng = (rng_mid - rng_width, rng_mid + rng_width)

            if not rng[0] <= tgt_pos <= rng[1]:
                # TODO: if it's already outside, then allow to go back
                rng = axis_def.range
                raise ValueError("Position %s for axis %s outside of range %f->%f"
                                 % (val, axis, rng[0], rng[1]))

    def _checkMoveAbs(self, pos):
        """
        Check that the argument passed to moveAbs() is (potentially) correct
        Same as super(), but allows to go 2x the range if the axis is not referenced
        pos (dict string -> float): the new position for a moveAbs()
        raise ValueError: if the argument is incorrect
        """
        refd = self.referenced.value
        for axis, val in pos.items():
            if axis in self.axes:
                axis_def = self.axes[axis]
                if hasattr(axis_def, "choices") and val not in axis_def.choices:
                    raise ValueError("Unsupported position %s for axis %s"
                                     % (val, axis))
                elif hasattr(axis_def, "range"):
                    rng = axis_def.range
                    # TODO: do we really need to allow this? Absolute move without
                    # referencing is not recommended anyway.
                    if not refd.get(axis, False):
                        # Double the range as we don't know where the axis started
                        rng_mid = (rng[0] + rng[1]) / 2
                        rng_width = rng[1] - rng[0]
                        rng = (rng_mid - rng_width, rng_mid + rng_width)

                    if not rng[0] <= val <= rng[1]:
                        raise ValueError("Position %s for axis %s outside of range %f->%f"
                                         % (val, axis, rng[0], rng[1]))
            else:
                raise ValueError("Unknown axis %s" % (axis,))

    def _doMoveRel(self, future, pos):
        """
        Blocking and cancellable relative move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> relative target position
        raise:
            ValueError: if the target position is
            TMCLError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:
            self._checkMoveRelFull(self._applyInversion(pos))

            end = 0  # expected end
            moving_axes = set()
            for an, v in pos.items():
                aid = self._name_to_axis[an]
                moving_axes.add(aid)
                usteps = int(round(v / self._ustepsize[aid]))
                self.MoveRelPos(aid, usteps)
                # compute expected end
                try:
                    d = abs(usteps) * self._ustepsize[aid]
                    dur = driver.estimateMoveDuration(d, self.speed.value[an], self._accel[an])
                except Exception:  # Can happen if config is wrong and report speed or accel == 0
                    logging.exception("Failed to estimate move duration")
                    dur = 60
                end = max(time.time() + dur, end)

            self._waitEndMove(future, moving_axes, end)
        logging.debug("move successfully completed")

    def _doMoveAbs(self, future, pos):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> absolute target position
        raise:
            TMCLError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:
            end = 0  # expected end
            old_pos = self._applyInversion(self.position.value)
            moving_axes = set()
            for an, v in pos.items():
                    aid = self._name_to_axis[an]
                    moving_axes.add(aid)
                    usteps = int(round(v / self._ustepsize[aid]))
                    self.MoveAbsPos(aid, usteps)
                    # compute expected end
                    try:
                        d = abs(v - old_pos[an])
                        dur = driver.estimateMoveDuration(d, self.speed.value[an], self._accel[an])
                    except Exception:  # Can happen if config is wrong and report speed or accel == 0
                        logging.exception("Failed to estimate move duration")
                        dur = 60
                    end = max(time.time() + dur, end)
            self._waitEndMove(future, moving_axes, end)
        logging.debug("move successfully completed")

    def _waitEndMove(self, future, axes, end=0):
        """
        Wait until all the given axes are finished moving, or a request to
        stop has been received.
        future (Future): the future it handles
        axes (set of int): the axes IDs to check
        do_axes (set of int): channel numbers of moves on digital output axes
        end (float): expected end time
        raise:
            TimeoutError: if took too long to finish the move
            CancelledError: if cancelled before the end of the move
        """
        moving_axes = set(axes)
        last_upd = time.time()
        dur = max(0.01, min(end - last_upd, 100))
        max_dur = dur * 2 + 1
        logging.debug("Expecting a move of %g s, will wait up to %g s", dur, max_dur)
        timeout = last_upd + max_dur
        last_axes = moving_axes.copy()
        time.sleep(0.2)  # wait until it starts moving (onTarget bit needs to be reset)
        try:
            while not future._must_stop.is_set():
                for aid in moving_axes.copy():  # need copy to remove during iteration
                    if self._isOnTarget(aid):
                        moving_axes.discard(aid)
                    # Check whether the move has stopped due to an error
                    self._checkErrorFlag(aid)

                now = time.time()
                if not moving_axes:
                    # no more axes to wait for
                    break

                if now > timeout:
                    logging.warning("Stopping move due to timeout after %g s.", max_dur)
                    self.MotorStop()
                    raise TimeoutError("Move is not over after %g s, while expected it takes only %g s" % (max_dur, dur))

                # Update the position from time to time (10 Hz)
                if now - last_upd > 0.1 or last_axes != moving_axes:
                    last_names = set(n for n, i in self._name_to_axis.items() if i in last_axes)
                    self._updatePosition(last_names)
                    last_upd = time.time()
                    last_axes = moving_axes.copy()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0.001, min(left / 2, 0.1))
                future._must_stop.wait(sleept)
            else:
                logging.debug("Move of axes %s cancelled before the end", axes)
                future._was_stopped = True
                raise CancelledError()
        finally:
            # TODO: check if the move succeded ? (= Not failed due to stallguard/limit switch)
            self._updatePosition()  # update (all axes) with final position
            self.MotorStop()  # stop axes to make sure that the encoder stops adjusting the position

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute or relative). Non-blocking.
        future (Future): the future to stop. Unused, only one future must be
         running at a time.
        return (bool): True if it successfully cancelled (stopped) the move.
        """
        # The difficulty is to synchronise correctly when the task is just starting
        # (not finished requesting axes to move)
        logging.debug("Cancelling current move")
        future._must_stop.set()  # tell the thread taking care of the move it's over
        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped

    def _doReference(self, future, axes, timeout=300):
        """
        Actually runs the referencing code
        future (Future): the future it handles
        axes (set of str)
        raise:
            IOError: if referencing failed due to hardware
            CancelledError if was cancelled
        """
        # Reset reference so that if it fails, it states the axes are not
        # referenced (anymore)
        with future._moving_lock:
            try:
                for ax in axes:
                    self.referenced._value[ax] = False
                    homing_done = self.homing(future, self._name_to_axis[ax], timeout, True)
                    if homing_done:
                        self.referenced._value[ax] = True
            except RuntimeError as ex:
                # _node.homing doesn't distinguish between CancelledError, TimeoutError and other exceptions,
                # in any of those cases a Runtime exception is raised (but with a different message).
                logging.exception("Referencing failure.")
                raise
            finally:
                # We only notify after updating the position so that when a listener
                # receives updates both values are already updated.
                time.sleep(0.1)  # it takes some time to adjust position after referencing
                self._updatePosition(axes)
                # read-only so manually notify
                self.referenced.notify(self.referenced.value)
                # # Update the global variable, based on the referenced axes
                # self._update_ref()

    def homing(self, future, axis, timeout=30, set_new_home=True):
        """
        Function to execute the configured Homing Method on the node.
        This function does exactly the same as the corresponding function of canopen.BaseNode402 except that
        it is compatible with the cancelling procedure, especially when cancelling very early (this is not
        possible with the original function).
        :param CancellableFuture future: the future for the referencing thread
        :param int timeout: Timeout value (default: 30)
        :param bool set_new_home: Defines if the node should set the home offset
        object (0x607C) to the current position after the homing procedure (default: true)
        :return bool: If the homing was complete with success
        """
        # TODO: Homing currently only works with one axis
        if axis != 0:
            raise NotImplementedError("Homing not supported for axis %s != 0." % axis)

        # Use init lock to make sure we're not cancelling during the state changes.
        with future._init_lock:
            if future._must_stop.is_set():
                # Cancelled before homing started (init_lock acquired here after cancelling function)
                return False
            previous_op_mode = self._node.op_mode
            self._node.state = SWITCHED_ON
            self._node.op_mode = HOMING_MODE
            # The homing process will initialize at operation enabled
            self._node.state = OPERATION_ENABLED
            homingstatus = IN_PROGRESS
            self._node.controlword = State402.CW_OPERATION_ENABLED | Homing.CW_START
        t = time.time() + timeout
        try:
            while homingstatus not in (TARGET_REACHED, ATTAINED):
                for key, value in Homing.STATES.items():
                    # check if the value after applying the bitmask (value[0])
                    # corresponds with the value[1] to determine the current status
                    bitmaskvalue = self._node.statusword & value[0]
                    if bitmaskvalue == value[1]:
                        homingstatus = key
                if homingstatus in (INTERRUPTED, ERROR_VEL_ZERO, ERROR_VEL_NOT_ZERO):
                    raise RuntimeError('Unable to home. Reason: {0}'.format(homingstatus))
                time.sleep(0.001)
                if time.time() > t:
                    raise RuntimeError('Unable to home, timeout reached')
            if set_new_home:
                actual_position = self._node.sdo[ACTUAL_POSITION].raw
                self._node.sdo[HOME_OFFSET].raw = actual_position  # home offset (0x607C)
                logging.info('Homing offset set to {0}'.format(actual_position))
            logging.info('Homing mode carried out successfully.')
            return True
        except RuntimeError as e:
            logging.info(str(e))
            raise
        finally:
            self._node.op_mode = previous_op_mode
        return False

    @staticmethod
    def _openCanNode(channel, nodeid, datasheet):
        """
        Create a single-node network.

        raise HwError: if the CAN port cannot be opened (doesn't exist, or
          already opened)
        """
        # For debugging purpose
        if channel == "fake":
            return None, CANNodeSimulator(naxes=1)

        # Start with creating a network representing one CAN bus
        network = canopen.Network()

        # Connect to the CAN bus
        try:
            network.connect(bustype='socketcan', channel=channel)
            network.check()
        except CanError as ex:
            raise HwError("Failed to establish connection on channel %s, ex: %s" % (channel, ex))
        except OSError:
            raise HwError("CAN adapter not found on channel %s." % (channel,))

        # Add some nodes with corresponding Object Dictionaries
        node = canopen.BaseNode402(nodeid, datasheet)
        network.add_node(node)

        # Reset network
        try:
            node.nmt.state = 'RESET COMMUNICATION'
            node.nmt.wait_for_bootup(15)
            logging.debug('Device state after reset = {0}'.format(node.nmt.state))
        except NmtError:
            raise HwError("Node with id %s not present on channel %s." % (nodeid, channel))

        # Transmit SYNC every 100 ms
        network.sync.start(0.1)

        try:
            node.load_configuration()
            node.setup_402_state_machine()
        except ValueError as ex:
            raise HwError("Exception connecting to state machine for node %s on %s: %s." % (nodeid, channel, ex))
        return network, node

    @staticmethod
    def parse_tsv_config(f):
        """
        Parse a tab-separated value (TSV) file in the following format:
          bank/axis    param   value    # comment
          bank/axis A0->A5 (axis: number)
          param is the parameter number in hexadecimal format (starting with 0x)
          value is a number in hexadecimal format (starting with 0x)
        f (File): opened file
        return:
          axis_params (dict (int, int) -> int): axis number/param number -> value
        """
        axis_params = {}  # (axis/add) -> val (int)

        # read the parameters "database" the file
        for l in f:
            # comment or empty line?
            mc = re.match(r"\s*(#|$)", l)
            if mc:
                logging.debug("Comment line skipped: '%s'", l.rstrip("\n\r"))
                continue
            m = re.match(r"(?P<num>[0-9]+)\t(?P<param>0x[0-9a-fA-F]+)\t(?P<value>0x[0-9a-fA-F]+)\s*(#.*)?$", l)
            if not m:
                raise ValueError("Failed to parse line '%s'" % l.rstrip("\n\r"))
            num, add, val = int(m.group("num")), int(m.group("param"), 16), int(m.group("value"), 16)
            axis_params[(num, add)] = val

        return axis_params


class CANNodeSimulator(object):
    """
    Simulates the basic functionality of a CAN node.
    """

    def __init__(self, naxes=1):
        self._status = SDOObject(0)
        self.state = READY_TO_SWITCH_ON
        self._op_mode = PP_MODE
        self.device_name = SDOObject("PD-1240-fake")
        self.hw_version = SDOObject(1)
        self.sw_version = SDOObject(1)
        self.identity = SDOObject(1)
        self.position = SDOObject(0)
        self.switch_param = SDOObject(0)
        self.pullup = SDOObject(0)
        self.sensor = SDOObject(0)
        self.position_window = SDOObject(0)
        self.position_window_time = SDOObject(0)
        self.homing = SDOObject(0)
        self.homing_offset = SDOObject(0)
        self.quickstop = SDOObject(0)
        self.acceleration = SDOObject(1e-6)

        self._is_moving = False
        self.speed = SDOObject(10000)

        self.target_pos = SDOObject(0)

        # Each axis has its own attributes starting from 0x800 * i (i=0,1,2,...).
        self.sdo = {}
        for i in range(naxes):
            self.sdo.update({
                    0x800 * i + DEVICE_NAME: self.device_name,
                    0x800 * i + HW_VERSION: self.hw_version,
                    0x800 * i + SW_VERSION: self.sw_version,
                    0x800 * i + IDENTITY: [None, None, None, self.identity],
                    0x800 * i + ACTUAL_POSITION: self.position,
                    0x800 * i + SWITCH_PARAM: self.switch_param,
                    0x800 * i + PULLUP_RESISTORS: self.pullup,
                    0x800 * i + SENSOR_SELECTION: self.sensor,
                    0x800 * i + POSITION_WINDOW_TIME: self.position_window_time,
                    0x800 * i + POSITION_WINDOW: self.position_window,
                    0x800 * i + HOMING_METHOD: self.homing,
                    0x800 * i + HOME_OFFSET: self.homing_offset,
                    0x800 * i + QUICK_STOP_OPTION: self.quickstop,
                    0x800 * i + PROFILE_VELOCITY: self.speed,
                    0x800 * i + PROFILE_ACCELERATION: self.acceleration,
                    0x800 * i + TARGET_POSITION: self.target_pos,
                    0x800 * i + STATUS_WORD: self._status,
                    })

        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

    @property
    def controlword(self):
        raise RuntimeError('The Controlword is write-only.')

    @controlword.setter
    def controlword(self, val):
        if self.op_mode == HOMING_MODE:
            self.position.raw = 0
            self.state = READY_TO_SWITCH_ON
            self.statusword = 0x1400
        else:
            if val & 0b111 and self.state == READY_TO_SWITCH_ON:
                self.state = SWITCHED_ON
            elif val & 0b111 and (self.state == SWITCHED_ON or self.state == OPERATION_ENABLED):
                self.state = OPERATION_ENABLED
                self.statusword = 0
                if val >> 6:
                    # Relative move (6th bit set)
                    self._executor.submit(self._start_moving_rel, self.target_pos.raw)
                else:
                    # Absolute move (6th bit cleared)
                    self._executor.submit(self._start_moving_abs, self.target_pos.raw)

    @property
    def op_mode(self):
        return self._op_mode

    @op_mode.setter
    def op_mode(self, val):
        if self._op_mode == val:
            # canopen library raises error if new opmode is the same as previous opmode
            raise SdoAbortedError("Opmode already %s." % val)
        self._op_mode = val

    @property
    def statusword(self):
        # allow acces via .statusword and .sdo[STATUS_WORD].raw
        return self._status.raw

    @statusword.setter
    def statusword(self, val):
        self._status.raw = val

    def _check_transition(self, cur_val, new_val):
        pass

    def _start_moving_rel(self, shift):
        """ Simulate relative move of length shift. """
        self.statusword &= ~0x400
        time.sleep(max(0.1, abs(shift / self.speed.raw)))
        self.position.raw += shift
        self.state = READY_TO_SWITCH_ON
        self.statusword |= 0x400

    def _start_moving_abs(self, pos):
        """ Simulate absolute move of length shift. """
        self.statusword &= ~0x400
        time.sleep(max(0.1, abs(self.position.raw - pos) / self.speed.raw))
        self.position.raw = pos
        self.state = READY_TO_SWITCH_ON
        self.statusword |= 0x400


class SDOObject(object):
    """
    SDO Attributes are accessed via .raw. This class simulates a simple SDO object.
    """
    def __init__(self, val):
        self.raw = val
