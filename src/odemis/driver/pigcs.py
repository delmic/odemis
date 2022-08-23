# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012-2015 Éric Piel, Delmic

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
from past.builtins import basestring
import queue
from concurrent.futures import CancelledError
import glob
import logging
from odemis import model
from odemis.model import isasync, CancellableFuture, CancellableThreadPoolExecutor
from odemis.util import driver, TimeoutError, to_str_escape
import os
# import random
import re
import serial
import socket
import threading
import time


# Driver to handle PI's piezo motor controllers that follow the 'GCS' (General
# Command Set). In particular it handles the PI E-861, C-867 and E-725 controllers.
# Information can be found in the manual E-861_User_PZ205E121.pdf (p.107).
# For the PI C-170 aka "redstone", see the pi driver.
# Note that although they officially support the 'command set', each controller
# type (and firmware) has a subset of commands supported, has different parameters
# and even slightly different way to expect commands and to send answers.
#
# It can access the controllers over RS-232, (serial over) USB and TCP/IP.
# In a daisy-chain, connected via USB or via RS-232, there must be one
# controller with address 1 (=DIP 1111). There is also a broadcast address: 255.
# In _some_ TCP/IP configuration, there is a master controller at address 254.
#
# The controller contains many parameters in flash memory. These parameters must
# have been previously written correctly to fit the stage it is driving. This
# driver uses and expects the parameters to be correct.
# The configuration database is available in a file called pistages2.dat. The
# PIMikroMove Windows program allows to load it, but by default doesn't copy it to
# the non-volatile memory, so you need to explicitly save them in persistent
# memory. (Can also be done with the WPA command and password "100".)
# In particular, for closed-loop stages, ensure that the settling windows and time
# are correctly set so that a move is considered on target quickly (< 1 s).
# With Odemis, the 'piconfig' utility can also be used to read and write the
# persistent memory.
#
# The controller supports closed-loop mode (i.e., absolute positioning) but only
# if it is associated to a sensor (not software detectable). When the hardware
# has no sensor, the controller should be used only in open-loop mode, to avoid
# damaging the actuator. So when there is no sensor:
# * Do not switch servo on (SVO command)
# * Do not send commands for closed-loop motion, like MOV or MVR
# * Do not send the open-loop commands OMA and OMR, since they
#    use a sensor, too
#
# The controller accepts several baud rates. We choose 38400 (DIP=01) as it's fast
# and it seems accepted by every version. Other settings are 8 data, 1 stop,
# no parity.
#
# In open-loop, the controller has 2 ways to move the actuators:
#  * Nanostepping: high-speed, and long distance
#       1 step ~ 10 μm without load (less with load)
#  * Analog: very precise, but moves maximum ~5μm
#      "40 volts corresponds to a motion of approx. 3.3μm"
#      "20 volts corresponds to a motion of approx. 1μm"
#
# As an exception, the C-867 only supports officially closed-loop. However, there
# is a "testing" command, SMO, that allows to move in open-loop by simulating the input
# to the PID controller. PI assured us that as long as the stage used can reach
# the limit without getting damaged, it is safe. It's pretty straightforward to
# use the command. The voltage defines the speed (and direction) of the move. The
# voltage should be set to 0 again when the position desired is reached. 3V is
# approximately the minimum to move, and 10V is the maximum. Voltage is more or
# less linear between -32766 and 32766 -> -10 and 10V. So the distance moved
# depends on the time the SMO is set, which is obviously very imprecise.
# The recommended maximum step frequency is 800 Hz.
#
# In closed-loop, it's almost all automagical.
# There are two modes in closed-loop: before and after referencing. Referencing
# consists in going to at least one "reference" point so that the actual position
# is known.
#  * Non referenced: that's the only one possible just after boot. It's only
#    possible to do relative moves. Just a sensor (which indicates a distance)
#    is needed.
#  * Referenced: both absolute and relative moves are possible. It's the default
#    mode. In addition to the sensor, the hardware will also include a reference
#    switch (which indicates a point, usually in the middle) and/or 2 limit
#    switches (which indicate the borders).
# The problem with referencing is that for some cases, it might be dangerous to
# move the actuator, so a user feedback is needed. This means an explicit request
# via the API must be done before this is going on, and stopping must be possible.
# In addition, in many cases, relative move is sufficient.
#
# The architecture of the driver relies on four main classes:
#  * Controller: represent one controller with one or several axes (E-861 has only
#     one). Each subclass defines a different type of control. The subclass is
#     picked dynamically according to the physical controller.
#  * Bus: represent the whole group of controllers daisy-chained from the same
#     serial port. It's also the Actuator interface for the rest of Odemis.
#  * SerialBusAccesser: wrapper around the serial port which handles the low-level GCS
#     protocol.
#  * ActionManager: handles all the actions (move/stop) sent to the controller so
#     that the asynchronous ones are ordered.
#
# In the typical usage, Odemis asks to moveRel() an axis to the Bus. The Bus converts
# it into an action, returns a Future and queues the action on the Executor.
# When the Controller is free, the Executor picks the next action, call the right
# method which converts it into a command for the Controller, which sends it to
# the actual PI controller and waits for it to finish.
#
# Note: in some rare cases, the controller might not answer to commands correctly,
# reporting error 555. In that case, it's possible to do a factory reset with the
# hidden command (which must be followed by the reconfiguration of the parameters):
# zzz 100 parameter

class PIGCSError(Exception):

    def __init__(self, errno, *args, **kwargs):
        # Needed for pickling, cf https://bugs.python.org/issue1692335 (fixed in Python 3.3)
        super(PIGCSError, self).__init__(errno, *args, **kwargs)
        self.errno = errno
        desc = self._errordict.get(errno, "Unknown error")
        self.strerror = "PIGCS error %d: %s" % (errno, desc)

    def __str__(self):
        return self.strerror

    _errordict = {
        0: "No error",
        1: "Parameter syntax error",
        2: "Unknown command",
        3: "Command length out of limits or command buffer overrun",
        4: "Error while scanning",
        5: "Unallowable move attempted on unreferenced axis, or move attempted with servo off",
        6: "Parameter for SGA not valid",
        7: "Position out of limits",
        8: "Velocity out of limits",
        9: "Attempt to set pivot point while U,V and W not all 0",
        10: "Controller was stopped by command",
        11: "Parameter for SST or for one of the embedded scan algorithms out of range",
        12: "Invalid axis combination for fast scan",
        13: "Parameter for NAV out of range",
        14: "Invalid analog channel",
        15: "Invalid axis identifier",
        16: "Unknown stage name",
        17: "Parameter out of range",
        18: "Invalid macro name",
        19: "Error while recording macro",
        20: "Macro not found",
        21: "Axis has no brake",
        22: "Axis identifier specified more than once",
        23: "Illegal axis",
        24: "Incorrect number of parameters",
        25: "Invalid floating point number",
        26: "Parameter missing",
        27: "Soft limit out of range",
        28: "No manual pad found",
        29: "No more step-response values",
        30: "No step-response values recorded",
        31: "Axis has no reference sensor",
        32: "Axis has no limit switch",
        33: "No relay card installed",
        34: "Command not allowed for selected stage(s)",
        35: "No digital input installed",
        36: "No digital output configured",
        37: "No more MCM responses",
        38: "No MCM values recorded",
        39: "Controller number invalid",
        40: "No joystick configured",
        41: "Invalid axis for electronic gearing, axis cannot be slave",
        42: "Position of slave axis is out of range",
        43: "Slave axis cannot be commanded directly when electronic gearing is enabled",
        44: "Calibration of joystick failed",
        45: "Referencing failed",
        46: "OPM (Optical Power Meter) missing",
        47: "OPM (Optical Power Meter) not initialized or cannot be initialized",
        48: "OPM (Optical Power Meter) Communication Error",
        49: "Move to limit switch failed",
        50: "Attempt to reference axis with referencing disabled",
        51: "Selected axis is controlled by joystick",
        52: "Controller detected communication error",
        53: "MOV! motion still in progress",
        54: "Unknown parameter",
        55: "No commands were recorded with REP",
        56: "Password invalid",
        57: "Data Record Table does not exist",
        58: "Source does not exist; number too low or too high",
        59: "Source Record Table number too low or too high",
        60: "Protected Param: current Command Level (CCL) too low",
        61: "Command execution not possible while Autozero is running",
        62: "Autozero requires at least one linear axis",
        63: "Initialization still in progress",
        64: "Parameter is read-only",
        65: "Parameter not found in non-volatile memory",
        66: "Voltage out of limits",
        67: "Not enough memory available for requested wave curve",
        68: "Not enough memory available for DDL table; DDL cannot be started",
        69: "Time delay larger than DDL table; DDL cannot be started",
        70: "The requested arrays have different lengths; query them separately",
        71: "Attempt to restart the generator while it is running in single step mode",
        72: "Motion commands and wave generator activation are not allowed when analog target is active",
        73: "Motion commands are not allowed when wave generator is active",
        74: "No sensor channel or no piezo channel connected to selected axis (sensor and piezo matrix)",
        75: "Generator started (WGO) without having selected a wave table (WSL).",
        76: "Interface buffer did overrun and command couldn't be received correctly",
        77: "Data Record Table does not hold enough recorded data",
        78: "Data Record Table is not configured for recording",
        79: "Open-loop commands (SVA, SVR) are not allowed when servo is on",
        80: "Hardware error affecting RAM",
        81: "Not macro command",
        82: "Macro counter out of range",
        83: "Joystick is active",
        84: "Motor is off",
        85: "Macro-only command",
        86: "Invalid joystick axis",
        87: "Joystick unknown",
        88: "Move without referenced stage",
        89: "Command not allowed in current motion mode",
        90: "No tracing possible while digital IOs are used on this HW revision. Reconnect to switch operation mode.",
        91: "Move not possible, would cause collision",
        92: "Stage is not capable of following the master. Check the gear ratio.",
        100: "PI LabVIEW driver reports error. See source control for details.",
        200: "No stage connected to axis",
        201: "File with axis parameters not found",
        202: "Invalid axis parameter file",
        203: "Backup file with axis parameters not found",
        204: "PI internal error code 204",
        205: "SMO with servo on",
        206: "uudecode: incomplete header",
        207: "uudecode: nothing to decode",
        208: "uudecode: illegal UUE format",
        209: "CRC32 error",
        210: "Illegal file name (must be 8-0 format)",
        211: "File not found on controller",
        212: "Error writing file on controller",
        213: "VEL command not allowed in DTR Command Mode",
        214: "Position calculations failed",
        215: "The connection between controller and stage may be broken",
        216: "The connected stage has driven into a limit switch, some controllers need CLR to resume operation",
        217: "Strut test command failed because of an unexpected strut stop",
        218: "While MOV! is running position can only be estimated!",
        219: "Position was calculated during MOV motion",
        230: "Invalid handle",
        231: "No bios found",
        232: "Save system configuration failed",
        233: "Load system configuration failed",
        301: "Send buffer overflow",
        302: "Voltage out of limits",
        303: "Open-loop motion attempted when servo ON",
        304: "Received command is too long",
        305: "Error while reading/writing EEPROM",
        306: "Error on I2C bus",
        307: "Timeout while receiving command",
        308: "A lengthy operation has not finished in the expected time",
        309: "Insufficient space to store macro",
        310: "Configuration data has old version number",
        311: "Invalid configuration data",
        333: "Internal hardware error",
        400: "Wave generator index error",
        401: "Wave table not defined",
        402: "Wave type not supported",
        403: "Wave length exceeds limit",
        404: "Wave parameter number error",
        405: "Wave parameter out of range",
        406: "WGO command bit not supported",
        502: "Position consistency check failed",
        503: "Hardware collision sensor(s) are activated",
        504: "Strut following error occurred, e.g. caused by overload or encoder failure",
        555: "BasMac: unknown controller error",
        601: "not enough memory",
        602: "hardware voltage error",
        603: "hardware temperature out of range",
        1000: "Too many nested macros",
        1001: "Macro already defined",
        1002: "Macro recording not activated",
        1003: "Invalid parameter for MAC",
        1004: "PI internal error code 1004",
        1005: "Controller is busy with some lengthy operation (e.g. reference move, fast scan algorithm)",
        2000: "Controller already has a serial number",
        4000: "Sector erase failed",
        4001: "Flash program failed",
        4002: "Flash read failed",
        4003: "HW match code missing/invalid",
        4004: "FW match code missing/invalid",
        4005: "HW version missing/invalid",
        4006: "FW version missing/invalid",
        4007: "FW update failed",
        5000: "PicoCompensation scan data is not valid",
        5001: "PicoCompensation is running, some actions cannot be executed during scanning/recording",
        5002: "Given axis cannot be defined as PPC axis",
        5003: "Defined scan area is larger than the travel range",
        5004: "Given PicoCompensation type is not defined",
        5005: "PicoCompensation parameter error",
        5006: "PicoCompensation table is larger than maximum table length",
        5100: "Common error in NEXLINE® firmware module",
        5101: "Output channel for NEXLINE® cannot be redefined for other usage",
        5102: "Memory for NEXLINE® signals is too small",
        5103: "RNP cannot be executed if axis is in closed loop",
        5104: "Relax procedure (RNP) needed",
        5200: "Axis must be configured for this action",
        -1001: "Unknown axis identifier",
        -1008: "Controller is busy with some lengthy operation",
        -1015: "One or more arguments given to function is invalid",
        -1024: "Motion error: position error too large, servo is switched off automatically",
        -1025: "Controller is already running a macro",
        -1041: "Parameter could not be set with SPA--parameter not defined for this controller",
    }

# constants for model number
MODEL_C867 = 867
MODEL_E709 = 709
MODEL_E725 = 725
MODEL_E861 = 861
MODEL_UNKNOWN = 0

class Controller(object):

    idn_matches = {
        MODEL_C867: "Physik Instrumente.*,.*C-867",
        MODEL_E709: "(?i)physik instrumente.*,.*E-709",
        MODEL_E725: "Physik Instrumente.*,.*E-725",
        MODEL_E861: "Physik Instrumente.*,.*E-861",
    }

    def __new__(cls, busacc, address=None, axes=None, _stem=False, *args, **kwargs):
        """
        Takes care of selecting the right class of controller depending on the
        hardware.
        For the arguments, see __init__()
        """
        busacc.flushInput()
        # Three types of controllers: Closed-loop (detected just from the axes
        # arguments), normal open-loop, and open-loop via SMO test command.
        # Difference between the 2 open-loop is hard-coded on the model as it's
        # faster than checking for the list of commands available.
        if _stem is True:
            subcls = Controller # just for tests/scan
        elif any(axes.values()):
            if not all(axes.values()):
                raise ValueError("Controller %d, mix of closed-loop and "
                                 "open-loop axes is not supported", address)
            idn = busacc.sendQueryCommand(address, "*IDN?\n")
            if re.search(cls.idn_matches[MODEL_E725], idn) or re.search(cls.idn_matches[MODEL_E709], idn):
                subcls = CLAbsController
            else:
                subcls = CLRelController
        else:
            # Check controller model by asking it, but cannot rely on the
            # normal commands as nothing is ready, so do all "manually"
            # Note: IDN works even if error is set
            idn = busacc.sendQueryCommand(address, "*IDN?\n")
            if re.search(cls.idn_matches[MODEL_C867], idn):
                subcls = SMOController
            else:
                subcls = OLController

        return super(Controller, cls).__new__(subcls)

    def __init__(self, busacc, address=None, axes=None, _stem=False):
        """
        busacc: a BusAccesser
        address (None or 1<=int<=16): address as configured on the controller
        axes (dict int -> boolean): determine which axis will be used and whether
          it will be used closed-loop (True) or open-loop (False).
        _stem (bool): just allows to do some raw commands, and changing address
          is allowed
        """
        # TODO: calibration values should be per axis (but for now we only have controllers with 1 axis)
        self.busacc = busacc
        self.address = address
        self._try_recover = False # for now, fully raw access
        # did the user asked for a raw access only?
        if _stem:
            self._channels = tuple("%d" % v for v in range(1, 17))  # allow commands to work on any axis
            return
        if axes is None:
            raise ValueError("Need to have at least one axis configured")

        self.GetErrorNum() # make it happy again (in case it wasn't)
        # We don't reboot by default because:
        # * in almost any case we can assume it's in a good state (if not, it's
        #   up to the controller to force it.
        # * if it's in a really bad state, GetErrorNum will fail and cause a
        #   reboot anyway
        # * it sometimes actually cause more harm by putting the controller in
        #   a zombie state
        # * it's slow (especially if you have 5 controllers)

        version = self.GetSyntaxVersion()
        if version != "2.0":
            logging.warning("Controller %d announces untested GCS %s", address, version)

        self._model = self.getModel()
        if self._model == MODEL_UNKNOWN:
            logging.warning("Controller %d is an unsupported version (%s)",
                            address, self.GetIdentification())

        self._channels = self.GetAxes() # available channels (=axes)
        self._avail_cmds = self.GetAvailableCommands()
        self._avail_params = self.GetAvailableParameters()
        # dict axis -> boolean
        try:
            self._hasLimitSwitches = {a: self.HasLimitSwitches(a) for a in self._channels}
        except NotImplementedError:
            self._hasLimitSwitches = {a: False for a in self._channels}
        # dict axis -> boolean
        try:
            self._hasRefSwitch = {a: self.HasRefSwitch(a) for a in self._channels}
        except NotImplementedError:
            self._hasRefSwitch = {a: False for a in self._channels}

        # Dict of axis (str) -> bool: whether the axis supports position update
        self.canUpdate = {a: False for a in self._channels}

        self._position = {} # m (dict axis-> position)
        self.pos_rng = {}  # m, dict axis -> min,max position

        self._speed = {}  # m/s dict axis -> speed
        self.speed_rng = {}  # m/s, dict axis -> min,max speed
        self._accel = {}  # m/s² dict axis -> acceleration/deceleration

        # only for interpolated position (on open-loop)
        self._target = {} # m (dict axis-> expected position at the end of the move)
        self._end_move = {a: 0 for a in self._channels} # m (dict axis -> time the move will finish)
        self._start_move = {} # m (dict axis -> time the move started)

        # If the controller is mis-configured for the actuator, things can go quite
        # wrong, so make it clear
        for c in self._channels:
            logging.info("Controller %s is configured for actuator %s", address, self.GetStageName(c))
            logging.info("Axis %s has %slimit switches and has %sreference switch",
                         c,
                         "" if self._hasLimitSwitches[c] else "no ",
                         "a " if self._hasRefSwitch[c] else "no ")

        self._try_recover = True # full feature only after init

    def terminate(self):
        pass

    def _sendOrderCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \n if necessary)
        """
        self.busacc.sendOrderCommand(self.address, com)

    def _sendQueryCommand(self, com):
        """
        Send a command and return its report
        com (string or list of strings): the command to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix
           (e.g.,"0 1") nor newline. If answer is multiline: returns a list of each line
        """
        # Hold the lock until we get an _correct_ answer, so that if recovery
        # is needed, it's not messed up by other communications
        with self.busacc.ser_access:
            try:
                lines = self.busacc.sendQueryCommand(self.address, com)
            except IOError:
                if not self._try_recover:
                    raise

                success = self.recoverTimeout()
                if isinstance(com, list):
                    full_com = "".join(com)
                else:
                    full_com = com

                if success:
                    logging.warning("Controller %s timeout after '%s', but recovered.",
                                    self.address, to_str_escape(full_com))
                    # try one more time (and it has to work this time)
                    lines = self.busacc.sendQueryCommand(self.address, com)
                else:
                    logging.error("Controller %s timeout after '%s', not recovered.",
                                  self.address, to_str_escape(full_com))
                    raise IOError("Controller %s timeout after '%s', not recovered." %
                                  self.address, to_str_escape(full_com))

        return lines

    re_err_ans = r"(-?\d+)$" # ex: ("0 1 ")[-54](\n)
    def recoverTimeout(self):
        """
        Try to recover from error in the controller state
        return (boolean): True if it recovered
        raise PIGCSError: if the timeout was due to a controller error (in which
            case the controller will be set back to working state if possible)
        """
        logging.warning("Trying to recover controller %s from timeout", self.address)
        # TODO: update the .state of the component to HwError

        # Reading error code makes the controller more comfortable...
        try:
            for i in range(2):
                self.busacc.flushInput()
                resp = self.busacc.sendQueryCommand(self.address, "ERR?\n")

                if isinstance(resp, list):
                    logging.debug("Got multi-line answer, will try again")
                    continue

                m = re.match(self.re_err_ans, resp)
                if m:  # looks like an answer to err?
                    err = int(m.group(1))
                    if err == 0:
                        return True
                    else:
                        # Everything is fine, it's probably just that the
                        # original command was not accepted
                        raise PIGCSError(err)

                logging.debug("Controller returned weird answer, will try harder to recover")
        except IOError:
            pass

        # We timed out again, try harder: reboot
        logging.debug("Trying harder to recover by rebooting controller")
        self.Reboot()
        try:
            resp = self.busacc.sendQueryCommand(self.address, "ERR?\n")
            m = re.match(self.re_err_ans, resp)
            if m:  # looks like an answer to err?
                # TODO Check if error == 307 or 308?
                err = int(m.group(1))
                if err != 0:
                    logging.warning("Controller %s still has error %d after reboot",
                                    self.address, err)
                return True
        except IOError:
            pass

        # that's getting pretty hopeless
        return False

    # The following are function directly mapping to the controller commands.
    # In general it should not be need to use them directly from outside this class
    def GetIdentification(self):
        # *IDN? (Get Device Identification):
        # ex: 0 2 (c)2010 Physik Instrumente(PI) Karlsruhe,E-861 Version 7.2.0
        version = self._sendQueryCommand("*IDN?\n")
        return version

    def GetSyntaxVersion(self):
        # CSV? (Get Current Syntax Version)
        # GCS version, can be 1.0 (for GCS 1.0) or 2.0 (for GCS 2.0)
        return self._sendQueryCommand("CSV?\n")

    def GetStageName(self, axis):
        """
        return (str) the name of the stage for which the controller is configured.
        Note that the actual stage might be different.
        """
        # CST? does this as well
        # parameter 0x3c
        # return self.GetParameter(axis, 0x3C)
        return self._readAxisValue("CST?", axis)

    def GetAxes(self, all=False):
        """
        all (bool): also list the disabled axes
        returns (tuple of str): all the available axes
        """
        # SAI? (Get List Of Current Axis Identifiers)
        # SAI? ALL: list all axes (included disabled ones), one per line
        answer = self._sendQueryCommand("SAI?%s\n" % (" ALL" if all else "",))
        axes = tuple(answer)
        return axes

    def GetAvailableCommands(self):
        """
        return (dict str -> str): command name -> command description
        """
        # HLP? (Get List Of Available Commands), returns lines in such formats:
        # "HLP? - Get List Of Available Commands \n" or
        # "HLP? Get List Of Available Commands \n" or
        # "#24 - Stop All Motion \n"
        # first line sometimes starts with \x00
        lines = self._sendQueryCommand("HLP?\n")
        lines[0] = lines[0].lstrip("\x00")
        cmds = {}
        for l in lines:
            ll = l.lower()
            if re.match(r"(the following.*:|^end of.*)", ll):
                logging.debug("Line doesn't seem to be a command: '%s'", l)
            else:
                cd = re.split(r"[\s-]", l, 1)
                if len(cd) != 2:
                    logging.debug("Line doesn't seem to be a command: '%s'", l)
                else:
                    cmd = cd[0]
                    if cmd.startswith("#"):  # One character command
                        try:
                            cmd = int(cmd[1:])
                        except ValueError:
                            logging.info("Unexpected command %s", cmd)

                    cmds[cmd] = cd[1]
        return cmds

    def GetAvailableParameters(self):
        """
        Returns the available parameters
        return (dict int -> str): parameter number and string
         used to describe it (typically: 0 1 FLOAT description)
        """
        # HPA? (Get List Of Available Parameters)
        lines = self._sendQueryCommand("HPA?\n")
        lines[0] = lines[0].lstrip("\x00")
        params = {}
        # first and last lines are typically just user-friendly text
        # look for something like '0x412=\t0\t1\tINT\tmotorcontroller\tI term 1'
        # (and old firmwares report like: '0x412 XXX')
        for l in lines:
            m = re.match(r"0x(?P<param>[0-9A-Fa-f]+)[= ]\s*(?P<desc>.+)", l)
            if not m:
                logging.debug("Line doesn't seem to be a parameter: '%s'", l)
                continue
            param, desc = int(m.group("param"), 16), m.group("desc")
            params[param] = desc
        return params

    def GetParameter(self, axis, param):
        """
        axis (str): axis name
        param (0<int): parameter id (cf p.35)
        returns (str): the string representing this parameter
        """
        # SPA? (Get Volatile Memory Parameters)
        assert(isinstance(axis, basestring) and 1 <= len(axis) <= 8)
        assert 0 <= param
        if hasattr(self, "_avail_params") and param not in self._avail_params:
            raise ValueError("Parameter %s %d not available" % (axis, param))

        answer = self._sendQueryCommand("SPA? %s %d\n" % (axis, param))
        try:
            value = answer.split("=")[1]
        except IndexError:
            # no "=" => means the parameter is unknown
            raise ValueError("Parameter %s %d unknown" % (axis, param))
        return value

    def GetParameters(self):
        """
        Return all the parameters values for all the axes
        returns (dict (str, int)->str): the axis/parameter number -> value
        """
        # SPA? (Get Volatile Memory Parameters)
        lines = self._sendQueryCommand("SPA?\n")
        lines[0] = lines[0].lstrip("\x00")
        params = {}
        # look for something like '1 0x412=5.000'
        for l in lines:
            m = re.match(r"(?P<axis>\d+)\s0x(?P<param>[0-9A-Fa-f]+)=\s*(?P<value>(\S+))", l)
            if not m:
                logging.debug("Line doesn't seem to be a parameter: '%s'", l)
                continue
            a, param, value = m.group("axis"), int(m.group("param"), 16), m.group("value")
            params[(a, param)] = value
        return params

    def SetParameter(self, axis, param, val, check=True):
        """
        axis (str): axis name
        param (0<int): parameter id (cf p.35)
        val (str): value to set (if not a string, it will be converted)
        check (bool): if True, will check whether the hardware raised an error
        Raises ValueError if hardware complains
        """
        # SPA (Set Volatile Memory Parameters)
        assert(isinstance(axis, basestring) and 1 <= len(axis) <= 8)
        assert(0 <= param)
        self._sendOrderCommand("SPA %s 0x%X %s\n" % (axis, param, val))
        if check:
            err = self.GetErrorNum()
            if err:
                raise ValueError("Error %d: setting param 0x%X with val %s failed." %
                                 (err, param, val), err)

    def GetParameterNonVolatile(self, axis, param):
        """
        Read the value of the parameter in the non-volatile memory
        axis (str): axis name
        param (0<int): parameter id (cf p.35)
        returns (str): the string representing this parameter
        """
        # SEP? (Get Non-Volatile Memory Parameters)
        assert(isinstance(axis, basestring) and 1 <= len(axis) <= 8)
        assert 0 <= param
        if hasattr(self, "_avail_params") and param not in self._avail_params:
            raise ValueError("Parameter %s %d not available" % (axis, param))

        answer = self._sendQueryCommand("SEP? %s %d\n" % (axis, param))
        try:
            value = answer.split("=")[1]
        except IndexError:
            # no "=" => means the parameter is unknown
            raise ValueError("Parameter %s %d unknown" % (axis, param))
        return value

    def SetCommandLevel(self, level, pwd):
        """
        Change the authorization level
        level (0<=int): 0 is standard, 1 allows to change parameters
        pwd (str): 'advanced' for level 1
        """
        assert(0 <= level)
        self._sendOrderCommand("CCL %d %s\n" % (level, pwd))
        self.checkError()

    def _readAxisValue(self, com, axis):
        """
        Returns the value for a command with axis.
        Ex: POS? 1 -> 1=25.3
        com (str): the 4 letter command (including the ?)
        axis (str): axis name
        returns (int or float or str): value returned depending on the type detected
        """
        assert(axis in self._channels)
        assert(2 < len(com) < 8)
        if com not in self._avail_cmds:
            raise NotImplementedError("Command %s not supported by the controller" % (com,))

        resp = self._sendQueryCommand("%s %s\n" % (com, axis))
        try:
            value_str = resp.split("=")[1]
        except IndexError:
            raise ValueError("Failed to parse answer from %s %s: %r" % (com, axis, resp))
        try:
            value = int(value_str)
        except ValueError:
            try:
                value = float(value_str)
            except ValueError:
                value = value_str

        return value

    def HasLimitSwitches(self, axis):
        """
        Report whether the given axis has limit switches (is able to detect
         the ends of the axis).
        Note: It's just read from a configuration value in flash
        memory. Can be configured easily with PIMikroMove
        axis (str): axis name
        returns (bool)
        """
        # LIM? (Indicate Limit Switches)
        # 1 => True, 0 => False
        return self._readAxisValue("LIM?", axis) == 1

    def HasRefSwitch(self, axis):
        """
        Report whether the given axis has a reference switch (is able to detect
         the "middle" of the axis).
        Note: apparently it's just read from a configuration value in flash
        memory. Can be configured easily with PIMikroMove
        axis (str): axis name
        returns (bool)
        """
        # TRS? (Indicate Reference Switch)
        # 1 => True, 0 => False
        return self._readAxisValue("TRS?", axis) == 1

    def GetMotionStatus(self, check=True):
        """
        returns (set of str): the set of moving axes
        Note: it seems the controller doesn't report moves when using OL via PID
        raise PIGCSError if check is True and an error on a controller happened
        """
        # "\x05" (Request Motion Status)
        # hexadecimal number bitmap of which axis is moving => 0 if everything is stopped
        # Ex: 4 => 3rd axis moving
        if check:
            errs, answer = self._sendQueryCommand(["ERR?\n", "\x05"])
            err = int(errs)
            if err:
                raise PIGCSError(err)
        else:
            answer = self._sendQueryCommand("\x05")

        bitmap = int(answer, 16)
        # convert to a set
        i = 1
        mv_axes = set()
        while bitmap > 0:
            if bitmap & 1:
                try:
                    mv_axes.add(self._channels[i - 1])
                except IndexError:
                    logging.debug("Reported moving axis %d which is out of known axes", i)
            i += 1
            bitmap >>= 1
        return mv_axes

    def GetStatus(self):
        # SRG? = "\x04" (Query Status Register Value)
        # SRG? 1 1
        # Check status
        # hexadecimal number, a bitmap corresponding to status flags (cf documentation)
        # Ex: 0x9004
        bitmap = self._sendQueryCommand("\x04")
        assert(bitmap.startswith("0x"))
        value = int(bitmap[2:], 16)
        # TODO change to constants
        return value

    def IsReady(self):
        """
        return (boolean): True if ready for new command
        """
        # "\x07" (Request Controller Ready Status)
        # returns 177 if ready, 178 if not
        ans = self._sendQueryCommand("\x07").encode('latin1')
        if ans == b"\xb1":
            return True
        elif ans == b"\xb0":
            return False

        logging.warning("Controller %s replied unknown ready status '%s'", self.address, ans)
        return None

    def IsReferenced(self, axis):
        """
        Report whether the given axis has been referenced
        Note: setting position with RON disabled will also put it in this mode
        axis (str): axis name
        returns (bool)
        """
        # FRF? (Get Referencing Result)
        # 1 => True, 0 => False
        return self._readAxisValue("FRF?", axis) == 1

    def IsOnTarget(self, axis, check=True):
        """
        Report whether the given axis is considered on target (for closed-loop
          moves only)
        axis (str): axis name
        returns (bool)
        raise PIGCSError if check is True and an error on a controller happened
        """
        # ONT? (Get On Target State)
        # 1 => True, 0 => False
        # cf parameters 0x3F (settle time), and 0x4D (algo), 0x406 (window size)
        # 0x407 (window off size)
        if check:
            com = ["ERR?\n", "ONT? %s\n" % (axis,)]
            lresp = self._sendQueryCommand(com)
            err = int(lresp[0])
            if err:
                raise PIGCSError(err)
            r = lresp[1]
            ss = r.split("=")
            if len(ss) != 2:
                raise ValueError("Failed to parse answer from %s: %r" %
                                 (com, lresp))
            return ss[1] == "1"
        else:
            return self._readAxisValue("ONT?", axis) == 1

    def GetErrorNum(self):
        """
        return (int): the error number (can be negative) of last error
        See p.192 of manual for the error codes
        """
        # ERR? (Get Error Number): get error code of last error
        answer = self._sendQueryCommand("ERR?\n")
        error = int(answer)
        return error

    def Reboot(self):
        self._sendOrderCommand("RBT\n")

        # empty the serial buffer
        self.busacc.flushInput()

        # Sending commands before it's fully rebooted can seriously mess it up.
        # It might end up in a state where only power cycle can reset it.
        # Give it some time to reboot before it's accessible again.
        time.sleep(2)

        self.busacc.flushInput()

    def RelaxPiezos(self, axis):
        """
        Call relaxing procedure. Reduce voltage, to increase lifetime and needed
          to change between modes
        axis (str): axis name
        """
        # RNP (Relax PiezoWalk Piezos): reduce voltage when stopped to increase lifetime
        # Also needed to change between nanostepping and analog
        assert(axis in self._channels)
        self._sendOrderCommand("RNP %s 0\n" % axis)

    def Halt(self, axis=None):
        """
        Stop motion with deceleration
        Note: see Stop
        axis (None or str): axis name. If None, all axes are stopped
        """
        # HLT (Stop All Axes): immediate stop (high deceleration != HLT)
        # set error code to 10
        # => Hold the access to the serial bus until we get rid of the error
        with self.busacc.ser_access:
            if axis is None:
                self._sendOrderCommand("HLT\n")
            else:
                assert(axis in self._channels)
                self._sendOrderCommand("HLT %s\n" % axis)

            # need to recover from the "error", otherwise nothing works
            error = self.GetErrorNum()
            if error != 10:  # PI_CNTR_STOP
                logging.warning("Stopped controller %s, but error code is %d instead of 10", self.address, error)

    def Stop(self):
        """
        Stop immediately motion on all axes
        Note: it's not efficient enough with SMO commands
        """
        # STP = "\x18" (Stop All Axes): immediate stop (high deceleration != HLT)
        # set error code to 10
        # => Hold the access to the serial bus until we get rid of the error
        # TODO: could be a lock just on this controller
        with self.busacc.ser_access:
            self._sendOrderCommand("\x18")

            # need to recover from the "error", otherwise nothing works
            error = self.GetErrorNum()
            if error != 10:  # PI_CNTR_STOP
                logging.warning("Stopped controller %s, but error code is %d instead of 10", self.address, error)

    def GetServo(self, axis):
        """
        Return whether the servo is active or not
        axis (str): axis name
        return (bool): True if the servo is active (closed-loop)
        """
        # SVO? (Get Servo State)
        assert(axis in self._channels)

        ans = self._sendQueryCommand("SVO? %s\n" % (axis,))
        ss = ans.split("=")
        if len(ss) != 2:
            raise IOError("Failed to parse answer from SVO?: %r" % (ans,))
        return ss[1] == "1"

    def SetServo(self, axis, activated):
        """
        Activate or de-activate the servo.
        Note: only activate it if there is a sensor (cf .HasRefSwitch and ._hasRefSwitch)
        axis (str): axis name
        activated (boolean): True if the servo should be activated (closed-loop)
        """
        # SVO (Set Servo State)
        assert(axis in self._channels)

        if activated:
            state = 1
        else:
            state = 0
        # FIXME: on E861 it seems recommended to first relax piezo.
        # On C867, RNP doesn't even exists
        self._sendOrderCommand("SVO %s %d\n" % (axis, state))

    def SetReferenceMode(self, axis, absolute):
        """
        Select the reference mode.
        Note: only useful for closed-loop moves
        axis (str): axis name
        absolute (bool): If True, absolute moves can be used, but needs to have
          been referenced.
          If False only relative moves can be used, but only needs a sensor to
          be used.
        """
        # RON (Set Reference Mode)
        assert(axis in self._channels)

        if absolute:
            state = 1
        else:
            state = 0
        self._sendOrderCommand("RON %s %d\n" % (axis, state))

    # Functions for relative move in open-loop (no sensor)
    def OLMoveStep(self, axis, steps):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        If the axis is already moving, the number of steps to perform is
        reset to the new number. IOW, it is not added up.
        axis (str): axis name
        steps (float): number of steps to do (can be a float). If negative, goes
          the opposite direction. 1 step is about 10µm.
        """
        # OSM (Open-Loop Step Moving): move using nanostepping
        assert(axis in self._channels)
        if steps == 0:
            return
        self._sendOrderCommand("OSM %s %.6f\n" % (axis, steps))

    def SetStepAmplitude(self, axis, amplitude):
        """
        Set the amplitude of one step (in nanostep mode). It affects the velocity
        of OLMoveStep.
        Note: probably it's best to set it to 55 and use OVL to change speed.
        axis (str): axis name
        amplitude (0<=float<=55): voltage applied (the more the further)
        """
        # SSA (Set Step Amplitude) : for nanostepping
        assert(axis in self._channels)
        assert((0 <= amplitude) and (amplitude <= 55))
        self._sendOrderCommand("SSA %s %.6f\n" % (axis, amplitude))

    def GetStepAmplitude(self, axis):
        """
        Get the amplitude of one step (in nanostep mode).
        Note: mostly just for self-test
        axis (str): axis name
        returns (0<=float<=55): voltage applied
        """
        # SSA? (Get Step Amplitude), returns something like:
        # 1=10.0000
        assert(axis in self._channels)
        answer = self._sendQueryCommand("SSA? %s\n" % axis)
        amp = float(answer.split("=")[1])
        return amp

    def OLAnalogDriving(self, axis, amplitude):
        """
        Use analog mode to move the axis by a given amplitude.
        axis (str): axis name
        amplitude (-55<=float<=55): Amplitude of the move. It's only a small move.
          55 is approximately 5 um.
        """
        # OAD (Open-Loop Analog Driving): move using analog
        assert(axis in self._channels)
        assert((-55 <= amplitude) and (amplitude <= 55))
        self._sendOrderCommand("OAD %s %.6f\n" % (axis, amplitude))

    def GetOLVelocity(self, axis):
        """
        Get velocity for open-loop montion.
        axis (str): axis name
        return float: velocity in step-cycles/s
        """
        assert(axis in self._channels)
        return self._readAxisValue("OVL?", axis)

    def SetOLVelocity(self, axis, velocity):
        """
        Set velocity for open-loop nanostepping motion.
        axis (str): axis name
        velocity (0<float): velocity in step-cycles/s. Default is 200 (~ 0.002 m/s)
        """
        # OVL (Set Open-Loop Velocity)
        assert(axis in self._channels)
        assert(velocity > 0)
        self._sendOrderCommand("OVL %s %.6f\n" % (axis, velocity))

    def GetOLAcceleration(self, axis):
        """
        Get acceleration for open-loop montion.
        axis (str): axis name
        return float: acceleration in step-cycles/s²
        """
        return self._readAxisValue("OAC?", axis)

    def SetOLAcceleration(self, axis, value):
        """
        Set open-loop acceleration of given axis.
        axis (str): axis name
        value (0<float): acceleration in step-cycles/s². Default is 2000
        """
        # OAC (Set Open-Loop Acceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("OAC %s %.6f\n" % (axis, value))

    def SetOLDeceleration(self, axis, value):
        """
        Set the open-loop deceleration.
        axis (str): axis name
        value (0<float): deceleration in step-cycles/s². Default is 2000
        """
        # ODC (Set Open-Loop Deceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("ODC %s %.6f\n" % (axis, value))

    # Methods for closed-loop functionality. For all of them, servo must be on
    def MoveAbs(self, axis, pos):
        """
        Start an absolute move of an axis to specific position.
         Can only be done with servo on and referenced.
        axis (str): axis name
        pos (float): position in "user" unit
        """
        # MOV (Set Target Position)
        assert(axis in self._channels)
        self._sendOrderCommand("MOV %s %.6f\n" % (axis, pos))

    def MoveRel(self, axis, shift):
        """
        Start an relative move of an axis to specific position.
         Can only be done with servo on and referenced.
        If the axis is moving, the target position is updated, and so the moves
        will add up.
        axis (str): axis name
        shift (float): change of position in "user" unit
        """
        # MVR (Set Target Relative To Current Position)
        assert(axis in self._channels)
        self._sendOrderCommand("MVR %s %.6f\n" % (axis, shift))

    def ReferenceToLimit(self, axis, lim=1):
        """
        Start to move the axis to the switch position (typically, the center)
        Note: Servo and referencing must be on
        See IsReferenced()
        axis (str): axis name
        lim (-1 or 1): -1 for negative limit and 1 for positive limit
        """
        # FNL (Fast Reference Move To Negative Limit)
        # FPL (Fast Reference Move To Positive Limit)
        assert(axis in self._channels)
        assert(lim in (-1, 1))
        if lim == 1:
            self._sendOrderCommand("FPL %s\n" % axis)
        else:
            self._sendOrderCommand("FNL %s\n" % axis)

    def ReferenceToSwitch(self, axis):
        """
        Start to move the axis to the switch position (typically, the center)
        Note: Servo and referencing must be on
        See IsReferenced()
        axis (str): axis name
        """
        # FRF (Fast Reference Move To Reference Switch)
        assert(axis in self._channels)
        self._sendOrderCommand("FRF %s\n" % axis)

    def AutoZero(self, axes=None, voltage=None):
        """
        Set Automatic Zero Calibration Point. Runs the calibration procedure.
        cf E-725 manual p. 29.
        axes (None or list of int): If None, all axes, otherwise a set of axes
        voltage (None or list of floats): If None, uses the default low voltage,
          otherwise uses the voltage given for each axis
        Note that the axes on which it is not run are automatically reset.
        So the recommanded usage is to not set any arguments.
        Check it is over by using GetMotionStatus()
        """
        acmd = ""
        if axes is not None:
            for i, a in enumerate(axes):
                if voltage is None:
                    acmd += " %s NAN" % (a,)
                else:
                    acmd += " %s %g" % (a, voltage[i])

        self._sendOrderCommand("ATZ%s\n" % acmd)

    def GetAutoZero(self, axis):
        """
        Return the status of the Auto Zero procedure for the given axis
        axis (str): axis name
        return (bool): True if successfull, False otherwise
        """
        ans = self._readAxisValue("ATZ?", axis)
        return ans == 1

    def GetPosition(self, axis):
        """
        Get the position (in "user" units)
        axis (str): axis name
        return (float): pos can be negative
        Note: after referencing, a constant is added by the controller
        """
        # POS? (GetRealPosition)
        return self._readAxisValue("POS?", axis)

    def GetTargetPosition(self, axis):
        """
        Get the target position (in "user" units)
        Note: only works for closed loop controllers
        axis (str): axis name
        return (float): pos can be negative
        """
        # MOV? (Get Target Position)
        return self._readAxisValue("MOV?", axis)

    def SetPosition(self, axis, pos):
        """
        Assign a position value (in "user" units) for the current location.
        No move is performed.
        axis (str): axis name
        pos (float): pos can be negative
        """
        # POS (SetRealPosition)
        return self._sendOrderCommand("POS %s %.6f\n" % (axis, pos))

    def GetMinPosition(self, axis):
        """
        Get the minimum reachable position (in "user" units)
        axis (str): axis name
        return (float): pos can be negative
        """
        # TMN? (Get Minimum Commandable Position)
        return self._readAxisValue("TMN?", axis)

    def GetMaxPosition(self, axis):
        """
        Get the maximum reachable position (in "user" units)
        axis (str): axis name
        return (float): pos can be negative
        """
        # TMX? (Get Maximum Commandable Position)
        assert(axis in self._channels)
        return self._readAxisValue("TMX?", axis)

    def GetCLVelocity(self, axis):
        """
        Get velocity for closed-loop motion.
        axis (str): axis name
        """
        # VEL (Get Closed-Loop Velocity)
        assert(axis in self._channels)
        return self._readAxisValue("VEL?", axis)

    def SetCLVelocity(self, axis, velocity):
        """
        Set velocity for closed-loop motion.
        axis (str): axis name
        velocity (0<float): velocity in units/s
        """
        # VEL (Set Closed-Loop Velocity)
        assert(axis in self._channels)
        assert(velocity > 0)
        self._sendOrderCommand("VEL %s %.6f\n" % (axis, velocity))

    def GetCLAcceleration(self, axis):
        """
        Get acceleration for closed-loop motion.
        axis (str): axis name
        """
        # VEL (Get Closed-Loop Acceleration)
        assert(axis in self._channels)
        return self._readAxisValue("ACC?", axis)

    def SetCLAcceleration(self, axis, value):
        """
        Set closed-loop acceleration of given axis.
        axis (str): axis name
        value (0<float): acceleration in units/s²
        """
        # ACC (Set Closed-Loop Acceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("ACC %s %.6f\n" % (axis, value))

    def SetCLDeceleration(self, axis, value):
        """
        Set the closed-loop deceleration.
        axis (str): axis name
        value (0<float): deceleration in units/s²
        """
        # DEC (Set Closed-Loop Deceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("DEC %s %.6f\n" % (axis, value))

    def SetRecordRate(self, value):
        """
        Set the record table rate
        Note: on the E-861, a cycle is 20µs
        value (1<= int): number of cycles when recording.
        """
        assert(value > 0)
        self._sendOrderCommand("RTR %d\n" % (value,))

    def GetRecordRate(self):
        """
        Get the record table rate
        Note: on the E-861, a cycle is 20µs
        return (1<= int): number of cycles when recording
        """
        ans = self._sendQueryCommand("RTR?\n")
        return int(ans)

    def SetRecordConfig(self, table, source, opt):
        """
        Set Data Recorder Configuration
        table (1<=int): record table ID
        source (str): Depends on the option. For axis-related signal, it's the axis name
        opt (0<=int): type of signal to be recorded. See documentation for values.
        """
        assert(table > 0)
        assert(isinstance(source, basestring))
        assert(opt >= 0)
        self._sendOrderCommand("DRC %d %s %d\n" % (table, source, opt))

    def SetRecordTrigger(self, table, source, val):
        """
        Set Data Recorder Configuration
        table (0<=int): record table ID or 0 for all the tables
        source (0<=int): ID of the trigger source, see documentation for values.
          0 = STE, 1 = any command moving, 2 = any command
        val (0<=int): Depends on the source. See documentation for values.
        """
        assert(table >= 0)
        assert(source >= 0)
        assert(val >= 0)
        self._sendOrderCommand("DRT %d %d %d\n" % (table, source, val))

    def GetRecordedData(self, start=None, num=None, table=None):
        """
        Get the recorded data
        start (None or 1<=int): first item to be read. If None, it will read everything.
        num (None or 1<=int): number of items to read. If None, start must also be None.
        table (None or 1<=int): record table ID. If None, it will read all the tables.
        return (list of tuple of floats): for each cycle, the values for each table
        """
        assert(start is None or start > 0)
        assert(num is None or num > 0)
        assert((start is None) == (num is None))
        assert(table is None or table > 0)
        if start is None and table is not None:
            raise ValueError("Table must be None if start/num are None")

        # Answer should look like:
#         # REM E-861
#         #
#         :
#         #
#         # NAME0 = Actual Position of Axis AXIS:1
#         # NAME1 = Position Error of Axis AXIS:1
#         #
#         # END_HEADER
#         5.00000 0.00000
#         4.99998 0.00002
#         5.00000 0.00000
#         5.00000 0.00000
#         5.00000 0.00000

        args = ""
        if start is not None:
            args += " %d %d" % (start, num)
        if table is not None:
            args += " %d" % (table,)
        ans = self._sendQueryCommand("DRR?%s\n" % (args,))

        data = []
        for l in ans:
            if l.startswith("#"):
                logging.debug("Skipping line %s", l)
                continue
            try:
                vals = tuple(float(s) for s in l.split(" "))
                data.append(vals)
            except ValueError:
                logging.warning("Failed to decode data line %s", l)

        return data

    def MoveRelRecorded(self, axis, shift):
        """
        Moves an axis for a given distance. While it's moving, data will be
        recorded. Can be done only if not referenced.
        axis (str): axis name
        shift (float): relative distance in user unit
        """
        assert(axis in self._channels)
        self._sendOrderCommand("STE %s %.6f\n" % (axis, shift))

# Different from OSM because they use the sensor and are defined in physical unit.
# Servo must be off! => Probably useless... compared to MOV/MVR
# OMR (Relative Open-Loop Motion)
# OMA (Absolute Open-Loop Motion)
#

    # Below are methods for manipulating the controller
    def getModel(self):
        """
        returns a model constant
        """
        idn = self.GetIdentification()
        for c, m in self.idn_matches.items():
            if re.search(m, idn):
                return c
        return MODEL_UNKNOWN

    def checkError(self):
        """
        Check whether the controller has reported an error
        return nothing
        raise PIGCSError if an error on a controller happened
        """
        err = self.GetErrorNum()
        if err:
            raise PIGCSError(err)

    def _storeMove(self, axis, shift, duration):
        """
        Save move information for interpolating the position
        To be called when a new move is started
        axis (str): the channel
        shift (float): relative change in position (im m)
        duration (0<float): time it will take (in s)
        """
        now = time.time()
        cur_pos = self._interpolatePosition(axis)
        self._position[axis] = cur_pos
        self._start_move[axis] = now
        self._end_move[axis] = now + duration
        self._target[axis] = cur_pos + shift

    def _storeStop(self, axis):
        """
        Save the fact that a move was stop immediately (maybe not achieved)
        """
        self._position[axis] = self._interpolatePosition(axis)
        self._target[axis] = self._position[axis]
        self._end_move[axis] = 0

    def _storeMoveComplete(self, axis):
        """
        Save the fact that the current move is complete (even if the end time
        is not yet achieved)
        """
        self._position[axis] = self._target.get(axis, self._position[axis])
        self._end_move[axis] = 0

    def _interpolatePosition(self, axis):
        """
        return (float): interpolated position at the current time
        """
        now = time.time()
        end_move = self._end_move[axis]
        if now > end_move:
            target = self._target.get(axis, self._position[axis])
            logging.debug("Interpolating move by reporting target position: %g",
                          target)
            self._end_move[axis] = 0
            return target
        else:
            start = self._start_move[axis]
            completion = (now - start) / (end_move - start)
            pos = self._position[axis]
            cur_pos = pos + (self._target[axis] - pos) * completion
            logging.debug("Interpolating move to %g %% of complete move: %g",
                          completion * 100, cur_pos)
            return cur_pos

    def getPosition(self, axis):
        """
        Note: in open-loop mode it's very approximate (and interpolated)
        return (float): the current position of the given axis
        """
        # This is using interpolation, closed-loop must override this method
        assert(axis in self._channels)

        # make sure that if a move finished early, we report the final position
        if self._end_move[axis] != 0 and not self.isMoving({axis}):
            self._storeMoveComplete(axis)

        return self._interpolatePosition(axis)

    def setSpeed(self, axis, speed):
        """
        Changes the move speed of the motor (for the next move).
        Note: in open-loop mode, it's very approximate.
        speed (0<float<10): speed in m/s.
        axis (str): the axis
        """
        assert (axis in self._channels)
        assert (self.speed_rng[axis][0] <= speed <= self.speed_rng[axis][1])
        self._speed[axis] = speed

    def getSpeed(self, axis):
        return self._speed[axis]

    def getAccel(self, axis):
        return self._accel[axis]

    def moveRel(self, axis, distance):
        """
        Move on a given axis for a given distance.
        It's asynchronous: the method might return before the move is complete.
        axis (str): the axis
        distance (float): the distance of move in m (can be negative)
        returns (float): approximate distance actually moved
        """
        raise NotImplementedError("This method must be overridden by a subclass")

    def moveAbs(self, axis, position):
        """
        Move on a given axis to a given position.
        It's asynchronous: the method might return before the move is complete.
        axis (str): the axis
        position (float): the target position in m (can be negative)
        returns (float): approximate distance actually moved
        """
        # This is a default implementation relying on the moveRel
        # It should be overriden, if it can be done directly
        old_pos = self.getPosition(axis)
        shift = position - old_pos
        return self.moveRel(axis, shift)

    def isMoving(self, axes=None):
        """
        Indicate whether the motors are moving.
        axes (None or set of bytes): axes to check whether for move, or all if None
        return (boolean): True if at least one of the axes is moving, False otherwise
        raise PIGCSError if an error on a controller happened
        """
        # TODO: the interface is not useful, we typically want to know whether
        # each axis is moving or not, so for now all the callers do one axis at
        # a time => return a set(int) = axes moving? or just take one axis?
        if axes is None:
            axes = self._channels
        else:
            assert axes.issubset(set(self._channels))

        # Note that "isOnTarget" would also work (both for OL and CL), but it
        # takes more characters and for CL, we need a more clever code anyway
        return not axes.isdisjoint(self.GetMotionStatus())

    def stopMotion(self):
        """
        Stop the motion on all axes immediately
        """
        self.Stop()

    def waitEndMotion(self, axes=None):
        """
        Wait until the motion of all the given axis is finished.
        Note: there is a 5 s timeout
        axes (None or set of str): axes to check whether for move, or all if None
        """
        timeout = 5 # s
        end = time.time() + timeout
        while self.isMoving(axes):
            if time.time() >= end:
                raise IOError("Timeout while waiting for end of motion")
            time.sleep(0.005)

    def isReferenced(self, axis):
        """
        return (bool or None): None if the axis cannot be referenced, or a boolean
          indicating the status of the referencing.
        """
        return None

    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        try:
            error = self.GetErrorNum()
            if error:
                logging.warning("Controller %s had error status %d", self.address, error)

            version = self.GetSyntaxVersion()
            logging.info("GCS version: '%s'", version)
            ver_num = float(version)
            if ver_num < 1 or ver_num > 2:
                logging.error("Controller %s has unexpected GCS version %s", self.address, version)
                return False

            axes = self.GetAxes()
            if len(axes) == 0 or len(axes) > 16:
                logging.error("Controller %s report axes %s", self.address, axes)
                return False

            if self._model in (MODEL_E861,): # support open-loop mode
                for a in self._channels:
                    self.SetStepAmplitude(a, 10)
                    amp = self.GetStepAmplitude(a)
                    if amp != 10:
                        logging.error("Failed to modify amplitude of controller %s (%f instead of 10)", self.address, amp)
                        return False

            if self._model in (MODEL_C867,): # support temperature reading
                # No support for direct open-loop mode
                # TODO put the temperature as a RO VA?
                current_temp = float(self.GetParameter("1", 0x57))
                max_temp = float(self.GetParameter("1", 0x58))
                if current_temp >= max_temp:
                    logging.error("Motor of controller %s too hot (%f C)", self.address, current_temp)
                    return False
        except Exception:
            return False

        return True

    @staticmethod
    def scan(busacc, max_add=16):
        """
        Scan the serial network for all the PI GCS compatible devices available.
        Note this is the low-level part, you probably want to use Controller.scan()
         for scanning devices on a computer.
        bus: the bus
        max_add (1<=int<=16): maximum address to scan
        return (dict int -> tuple): addresses of available controllers associated
            to number of axes, and presence of limit switches/sensor
        """
        ctrl = Controller(busacc, _stem=True)

        present = {}
        for i in range(1, max_add + 1):
            # ask for controller #i
            logging.debug("Querying address %d", i)

            # is it answering?
            try:
                ctrl.address = i
                ctrl._avail_cmds = ctrl.GetAvailableCommands()
                axes = {}
                for a in ctrl.GetAxes():
                    axes = {a: ctrl.HasRefSwitch(a)}
                if not axes:
                    logging.info("Found controller %d with no axis", i)
                else:
                    version = ctrl.GetIdentification()
                    logging.info("Found controller %d with ID '%s'.", i, version)
                    present[i] = axes
            except IOError:
                pass

        ctrl.address = None
        return present


class CLAbsController(Controller):
    """
    Controller managed via closed-loop commands and which is always referenced
     (ex: E-725 with scan stage).
    """
    def __init__(self, busacc, address=None, axes=None):
        super(CLAbsController, self).__init__(busacc, address, axes)
        self._upm = {} # ratio to convert values in user units to meters
        # For _getPositionCached()
        self._lastpos = {}  # axis (str) -> (pos (float), timestamp (float))

        # It's pretty much required to reference the axes, and fast and
        # normally not dangerous (travel range is very small).
        # It's also import to reference all the axes simultaneously
        # TODO: just use a standard parameter to request referencing on init?
        # TODO: only do if needed
        referenced = all(self.isReferenced(a) for a in axes)
        if not referenced:
            logging.info("Referencing the axes (via AutoZero)")
            self.AutoZero()
            tstart = time.time()
            while self.GetMotionStatus():
                time.sleep(0.01)
                if time.time() > tstart + 10:
                    self.stopMotion()
                    raise IOError("AutoZero refenrencing is taking more that 10s, stopping")
            logging.debug("Referencing took %f s", time.time() - tstart)

        for a, cl in axes.items():
            if a not in self._channels:
                raise LookupError("Axis %s is not supported by controller %s" % (a, address))

            if not cl:  # want open-loop?
                raise ValueError("Initialising CLAbsController with request for open-loop")

            if self._model == MODEL_E709:
                self._upm[a] = 1e-6
            else:
                # Check the unit is um
                # TODO: cf PUN? Seems less supported than the parameter (eg, E-861 doesn't)
                unit = self.GetParameter(a, 0x7000601)
                if unit.lower() == "um":
                    self._upm[a] = 1e-6  # m
                else:
                    raise IOError("Controller %s configured with unit %s, but only "
                                  "micrometers (UM) is supported." % (address, unit))

            # Start the closed loop
            self.SetServo(a, True)

            self._lastpos[a] = (None, 0)  # Looong time ago not read
            # Movement range before referencing is max range in both directions
            self.pos_rng[a] = (self.GetMinPosition(a) * self._upm[a],
                               self.GetMaxPosition(a) * self._upm[a])

            # Read speed/accel ranges
            self._speed[a] = self.GetCLVelocity(a) * self._upm[a]  # m/s
            # TODO: get range from the parameters?
            self.speed_rng[a] = (10e-6, 1) # m/s (default large values)
            # Doesn't support accel setting => just assume it's very high (to not wait too long for a move)
            self._accel[a] = self._speed[a] * 1000  # m/s²
            # self._accel[a] = self.GetCLAcceleration(a) * self._upm[a]  # m/s²

            # TODO: sometimes (mostly after autozero) the axis position might
            # be slightly outside of the range, which tends to confuse the clients.
            # => move to the closest allowed position automatically?
    def terminate(self):
        super(CLAbsController, self).terminate()

        # Disable servo, to allow the user to move the axis manually
        for a in self._channels:
            self.SetServo(a, False)

    def moveRel(self, axis, distance):
        """
        See Controller.moveRel
        """
        assert(axis in self._channels)

        # self._updateSpeedAccel(axis)
        # We trust the caller that it knows it's in range
        # (worst case the hardware will not go further)
        self.MoveRel(axis, distance / self._upm[axis])
        self.checkError()
        self._lastpos[axis] = (None, 0)
        return distance

    def moveAbs(self, axis, position):
        """
        See Controller.moveAbs
        """
        # TODO: support multiple axes to reduce init latency?
        assert(axis in self._channels)

        # TODO
        # self._updateSpeedAccel(axis)
        # We trust the caller that it knows it's in range
        # (worst case the hardware will not go further)
        old_pos = self.getPosition(axis, maxage=1)  # It's just a rough estimation anyway

        self.MoveAbs(axis, position / self._upm[axis])
        self.checkError()

        distance = position - old_pos
        self._lastpos[axis] = (None, 0)
        return distance

    def getPosition(self, axis, maxage=0):
        """
        Find current position as reported by the sensor
        axis (str)
        maxage (0 < float): maximum time (in s) since last reading before the
          position will be re-read from the hardware.
        return (float): the current position of the given axis
        """
        # Use cached info if it's not too old
        if maxage > 0:
            pos, ts = self._lastpos[axis]
            if time.time() - ts < maxage:
                return pos

        pos = self.GetPosition(axis) * self._upm[axis]
        self._lastpos[axis] = (pos, time.time())
        return pos

    def isMoving(self, axes=None):
        """
        Indicate whether the motors are moving (ie, last requested move is over)
        axes (None or set of bytes): axes to check whether for move, or all if None
        return (boolean): True if at least one of the axes is moving, False otherwise
        raise PIGCSError: if there is an error with the controller
        """
        if axes is None:
            axes = set(self._upm.keys())
        else:
            assert axes.issubset(set(self._channels))

        # With servo on, it might constantly be _slightly_ moving (around the
        # target), so it's much better to use IsOnTarget info. The controller
        # needs to be correctly configured with the right window size.
        for a in axes:
            if not self.IsOnTarget(a):
                return True

        return False

    # TODO allow to reference, but need to get multiple axes, and to check the
    # status, isMoving() cannot be used, but just GetMotionStatus()
    # def startReferencing(self, axis):

    def isReferenced(self, axis):
        """
        returns (bool or None): True if the axis is referenced, or None if it's
        not possible
        """
        return self.GetAutoZero(axis)


# Messages to the encoder manager
MNG_TERMINATE = "T"
MNG_START = "S"
# To stop the encoder: send a float representing the earliest time at which it is
# possible to stop it. 0 will stop it immediately.


class CLRelController(Controller):
    """
    Controller managed via closed-loop commands (ex: C-867 with encoder).
    Note that it knows if there is a reference or a limit switch only based on
    what is written in the controller parameters. If none are available,
    referencing will not be available.
    Note: when the axis is used unreferenced, the range (params 0x15 and 0x30),
    should be doubled so that where ever the axis is when the controller is
    powered (aka 0), it is still possible to reach any position. For instance,
    if the controller is powered while the axis is at a limit, to reach the other
    limit, it would need to travel the entire range in a direction.
    """

    def __init__(self, busacc, address=None, axes=None, auto_suspend=10, suspend_mode="read"):
        """
        auto_suspend (False or 0 < float): delay before stopping the servo (and
          encoder if possible). Useful as the encoder might cause some warm up,
          and also ensures that no vibrations are caused by trying to stay on target.
          If False, it will never turn the servo off between nornal moves.
        suspend_mode ("read" or "full"): How to suspend the servo.
          "full" will stop the servo and turn off the encoder. The main advantage
            is that it avoids heating up while not moving (ie, reduce drift).
            Only possible if sensor can be turned off (param 0x56).
          "read" will pause the servo, and keep reading the sensor. This allows
            to monitor the drift even when not moving. Default is "read".
        """
        super(CLRelController, self).__init__(busacc, address, axes)
        self._upm = {} # ratio to convert values in user units to meters

        if not (auto_suspend is False or auto_suspend > 0):
            raise ValueError("auto_suspend should be False or > 0 but got %s" % (auto_suspend,))
        self._auto_suspend = auto_suspend

        # for managing starting/stopping the encoder:
        # * one queue to request turning on/off the encoder and terminating the thread
        #   It uses MNG_TERMINATE, MNG_START, and a float to indicate the time
        #   at which it should be stopped earliest.
        # * one event to know when the encoder is ready
        self._suspend_req = {}
        self._axis_ready = {}
        self._encoder_mng = {}
        self._pos_lock = {}  # acquire to read/write position
        self._slew_rate = {}  # in s, copy of 0x7000002: slew rate, for E-861

        # Referencing:
        # When setting the reference mode (SetReferenceMode) with RON disabled ("RON", False),
        # the axes are not actually referenced. It needs to be followed by SetPosition() call, which assigns a value to
        # the current position. After that command, the axis will report to be referenced, though it is actually
        # not (frf? returns True). This allows to do absolute/relative moves without referencing.
        self._referenced = False   # tracking if the axis is actually referenced and not only pretending to be

        for a, cl in axes.items():
            if a not in self._channels:
                raise LookupError("Axis %s is not supported by controller %d" % (a, address))

            if not cl:  # want open-loop?
                raise ValueError("Initialising CLRelController with request for open-loop")
            if not self._hasRefSwitch[a]:
                logging.warning("Closed-loop control requested but controller "
                                "%d reports no reference sensor for axis %s",
                                address, a)

            # Check the unit is mm
            unit = self.GetParameter(a, 0x7000601).lower()
            if unit == "mm":
                self._upm[a] = 1e-3
            elif unit == "um":
                self._upm[a] = 1e-6
            else:
                raise IOError("Controller %d configured with unit %s, but only "
                              "millimeters (mm) is supported." % (address, unit))

            # To be taken when reading position or affecting encoder reading
            self._pos_lock[a] = threading.RLock()

            # We have two modes for auto_suspend:
            #  * full: Servo and encoder off.
            #    That typically happens with the C-867. It allows to reduce heat
            #    due to encoder using infra-red light (and ensures the motor
            #    doesn't move).
            #  * read: PID values set to 0,0,0.
            #    That typically happens with the E-861. It avoids going through
            #    the piezo "relax" procedure that takes time (4 x slew rate) and
            #    causes up to 100 nm move. It also allows to monitor the position.
            if suspend_mode == "full" and 0x56 not in self._avail_params:
                logging.warning("Controller %d cannot turn off sensor so suspend_mode full is not useful", address)
                suspend_mode = "read"
            if suspend_mode == "full":
                self._servo_suspend = True
                if self._auto_suspend:
                    logging.info("Will turn off servo when axis not in use")

            elif suspend_mode == "read":
                self._servo_suspend = False
                # Save the "real" PID values, from the EEPROM, so that even if
                # the driver catastrophically finished with PID set to 0,0,0 ,
                # we will use the correct values.
                self._pid = tuple(int(self.GetParameterNonVolatile(a, p)) for p in (1, 2, 3))
                # Activate the servo from now on
                self._startServo(a)

                if self._auto_suspend:
                    logging.info("Will use PID = 0,0,0 when axis not in use")
            else:
                raise ValueError("Unexpected suspend_mode '%s' (should be read or full)" % (suspend_mode,))

            try:  # Only exists on E-861 (to know how long it takes to start the servo)
                # slew rate is stored in ms
                self._slew_rate[a] = float(self.GetParameter(a, 0x7000002)) / 1000
            except ValueError:  # param doesn't exist => no problem
                pass

            self.canUpdate[a] = True

            # At start (and unreferenced) the controller was either already
            # powered on from a previous session or has just been turned on. In
            # any case, the current position is the most likely one: either it
            # has moved with the encoder off, and the position is entirely
            # unknown anyway, or it hasn't moved and the position is correct.
            # TODO: a slightly more likely position would be to reuse the last
            #  position of the previous session. That would basically involve
            #  storing in a file the current position during terminate(), and
            #  reading it back at init.

            # To know the actual range width, one could look at params 0x17 + 0x2f
            # (ie, distance between limit switches and origin).
            self.pos_rng[a] = (self.GetMinPosition(a) * self._upm[a],
                               self.GetMaxPosition(a) * self._upm[a])

            # Read speed/accel ranges
            self._speed[a] = self.GetCLVelocity(a) * self._upm[a] # m/s
            self._accel[a] = self.GetCLAcceleration(a) * self._upm[a] # m/s²

            try:
                max_speed = float(self.GetParameter(a, 0xA)) * self._upm[a] # m/s
                # max_accel = float(self.GetParameter(a, 0x4A)) * self._upm[a] # m/s²
            except (IOError, ValueError):
                max_speed = self._speed[a]
                # max_accel = self._accel[a]
            self.speed_rng[a] = (10e-6, max_speed)  # m/s (default low value for min)

            self._suspendAxis(a)  # in case it was not off yet
            self._suspend_req[a] = queue.Queue()
            self._axis_ready[a] = threading.Event()
            t = threading.Thread(target=self._suspend_mng_run,
                                 name="Suspend manager ctrl %d axis %s" % (address, a),
                                 args=(a,))
            t.daemon = True
            self._encoder_mng[a] = t
            t.start()

        self._prev_speed_accel = ({}, {})

    def terminate(self):
        super(CLRelController, self).terminate()

        # Disable servo, to allow the user to move the axis manually
        for a in self._channels:
            self._suspend_req[a].put(MNG_TERMINATE)
            self._stopServo(a)
            if not self._servo_suspend:
                # Make sure the PID values are back to the normal move
                P, I, D = self._pid
                self.SetParameter(a, 1, P, check=False)  # P
                self.SetParameter(a, 2, I, check=False)  # I
                self.SetParameter(a, 3, D, check=False)  # D

    def recoverTimeout(self):
        ret = Controller.recoverTimeout(self)
        if ret and not self._servo_suspend:
            try:
                # We expect the servo to be always on, so put it back on
                for a in self._upm.keys():
                    self._startServo(a)
            except Exception:
                logging.exception("Failed to restart the servo")
                return False

        return ret

    def _stopServo(self, axis):
        """
        Turn off the servo the supply power of the encoder.
        That means during this time it's not possible to move the axes.
        Referencing is lost.
        Should only be called when no move is taking place.
        axis (str): the axis
        """
        with self._pos_lock[axis]:
            self._referenced = False
            self.SetServo(axis, False)
            # This can only be done if the servo is turned off
            if self._servo_suspend:
                # Store the position before turning off the encoder because while
                # turning off the encoder, some signal will be received which will
                # make the controller believe it has moved.
                pos = self.GetPosition(axis)
                self.SetParameter(axis, 0x56, 0)  # 0 = off
                # SetParameter checks the error num, which gives a bit of time to
                # the encoder signal to fully settle down
                self.SetPosition(axis, pos)

    def _startServo(self, axis):
        """
        Turn on the servo and the power supply of the encoder.
        axis (str): the axis
        """
        with self._pos_lock[axis]:
            # Param 0x56 is only for C-867 and newer E-861 and allows to control encoder power
            # Param 0x7000002 is only for E-861 and indicates time to start servo
            pos = None
            if self._servo_suspend:
                pos = self.GetPosition(axis)
                # Warning: turning on the encoder can reset the USB connection
                # (if it's on this very controller)
                # Turning on the encoder resets the current position
                self.SetParameter(axis, 0x56, 1, check=False)  # 1 = on
                time.sleep(2)  # 2 s seems long enough for the encoder to initialise

            self.SetServo(axis, True)

            self.SetReferenceMode(axis, False)
            if pos is None:
                pos = self.GetPosition(axis)
            self.SetPosition(axis, pos)

            if axis in self._slew_rate:
                # According to the documentation, changing mode can take up to
                # 4 times the "slew rate". If you don't wait that time before
                # moving, the move will sometimes fail with error -1008 (BUSY),
                # and the controller will go crazy causing lots of vibrations
                # on the axis.
                # Note: we could try to also check whether the controller is ready
                # with self.IsReady(), and stop sooner if it's possible).
                # But that could lead to orders/queries to several controllers
                # to be intertwined, which causes sometimes the "garbage" bug.
                time.sleep(4 * self._slew_rate[axis])

            # The controller is normally ready, as it should be taken cared by
            # the slewrate, but check to be really sure.
            for i in range(100):
                if self.IsReady():
                    break
                logging.debug("Controller not yet ready, waiting a bit more")
                time.sleep(0.01)
            else:
                logging.warning("Controller indicates it's still not ready, but will not wait any longer")

    def _suspendAxis(self, axis):
        if self._servo_suspend:
            self._stopServo(axis)
        else:
            with self.busacc.ser_access:  # To avoid garbage when using IP com
                if self.IsOnTarget(axis, check=False):
                    # Force PID to 0, 0, 0
                    logging.debug("Suspending servo of axis %s/%s", self.address, axis)
                    self.SetParameter(axis, 1, 0, check=False)  # P
                    self.SetParameter(axis, 2, 0, check=False)  # I
                    self.SetParameter(axis, 3, 0, check=False)  # D
                    # Normally the servo should be on, but in case of error,
                    # it might have been automatically stopped.
                    if not self.GetServo(axis):
                        logging.info("Servo of axis %s/%s was off, need to restart it",
                                     self.address, axis)
                        self._startServo(axis)

                    try:
                        self.checkError()
                    except PIGCSError as ex:
                        logging.error("Changing PID seems to have failed: %s", ex)
                else:
                    # The axis is not on target. IOW, current position != target
                    # position. The main reason for this to happen is that the
                    # axis limit was reached. If we do nothing, the controller
                    # keeps the target position, and when doing another move,
                    # it might still be out of the range, so for the user
                    # the axis will look "stuck". => The target position should
                    # be synchronised with the current position, so that the next
                    # move starts from where the axis is. We could try to set
                    # the target position to the current position, but there is
                    # no explicit command to do so (MoveAbs/MoveRel will do that,
                    # but after following a motion profile, which can be long).
                    # The simplest and also safest is to just stop the servo. On
                    # start it will set target position as the current position.
                    logging.warning("Turing off servo of axis %s/%s, as it is off-target",
                                    self.address, axis)
                    self.SetServo(axis, False)
                    try:
                        self.checkError()
                    except PIGCSError as ex:
                        logging.error("Stopping servo seems to have failed: %s", ex)

    def _resumeAxis(self, axis):
        if self._servo_suspend:
            self._startServo(axis)
            try:
                self.checkError()
            except PIGCSError as ex:
                logging.error("Axis %s/%s failed during resume: %s",
                                self.address, axis, ex)
        else:
            # The servo should be on all the time, but if for some reason there
            # was an error, the servo might have been disabled => need to put
            # it back on now.
            with self.busacc.ser_access:  # To avoid garbage when using IP com
                try:
                    self.checkError()
                except PIGCSError as ex:
                    logging.warning("Axis %s/%s reported while suspended: %s",
                                    self.address, axis, ex)

                if not self.GetServo(axis):
                    logging.info("Servo of axis %s/%s was off, need to restart it",
                                 self.address, axis)
                    self._startServo(axis)
                else:
                    # Since it's suspended, some drift (or encoder error) might
                    # have caused the current position to be away from the
                    # target position. As we don't restart the servo, which
                    # resets the target position to the current pos, turning on
                    # the PID again would cause immediately a move (back) to the
                    # target position, which is not useful and even potentially
                    # dangerous.
                    # => With PID=0, we request a "move" to the current position.
                    # It resets the target position, and as it's already there,
                    # it's very fast.
                    pos = self.GetPosition(axis)
                    tpos = self.GetTargetPosition(axis)
                    if not self.IsOnTarget(axis, check=False):
                        logging.warning("Axis %s/%s is at %g, far from target %g",
                                        self.address, axis, pos, tpos)

                    self.MoveAbs(axis, tpos)

                try:
                    self.checkError()
                except PIGCSError as ex:
                    logging.error("Axis %s/%s failed during resume: %s",
                                    self.address, axis, ex)

                # Put back PID values
                P, I, D = self._pid
                self.SetParameter(axis, 1, P, check=False)  # P
                self.SetParameter(axis, 2, I, check=False)  # I
                self.SetParameter(axis, 3, D, check=False)  # D
                try:
                    self.checkError()
                except PIGCSError as ex:
                    logging.error("Changing PID back seems to have failed: %s", ex)

    def _suspend_mng_run(self, axis):
        """
        Main loop for encoder manager thread:
        Turn on/off the encoder based on the requests received
        """
        try:
            q = self._suspend_req[axis]
            stopt = None  # None if must be on, otherwise time to stop
            while True:
                # wait for a new message or for the time to stop the encoder
                now = time.time()
                if stopt is None or not q.empty():
                    msg = q.get()
                elif now < stopt:  # soon time to turn off the encoder
                    timeout = stopt - now
                    try:
                        msg = q.get(timeout=timeout)
                    except queue.Empty:
                        # time to stop the encoder => just do the loop again
                        continue
                else:  # time to stop
                    # the queue should be empty (with some high likelyhood)
                    logging.debug("Turning off the encoder at %f > %f (queue has %d element)",
                                  now, stopt, q.qsize())
                    self._axis_ready[axis].clear()
                    self._suspendAxis(axis)
                    stopt = None
                    continue

                # parse the new message
                logging.debug("Decoding encoder message %s", msg)
                if msg == MNG_TERMINATE:
                    return
                elif msg == MNG_START:
                    if not self._axis_ready[axis].is_set():
                        self._resumeAxis(axis)
                        self._axis_ready[axis].set()
                    stopt = None
                else:  # time at which to stop the encoder
                    stopt = msg

        except Exception:
            logging.exception("Encoder manager failed:")
        finally:
            logging.info("Encoder manager %d/%s thread over", self.address, axis)

    def prepareAxisForMove(self, axis):
        """
        Request the axis to be ready for a move. Non-blocking.
        Can be called before really asking to move to save a bit of time.
        """
        self._suspend_req[axis].put(MNG_START)
        # Just in case eventually no move is requested, it will automatically
        # stop the encoder.
        if self._auto_suspend:
            self._releaseAxis(axis, delay=10 + self._auto_suspend)

    def _acquireAxis(self, axis):
        """
        Ensure the axis servo and encoder are on.
        Need to call _releaseAxis once not needed.
        It will block until the encoder is actually ready
        """
        # TODO: maybe provide a public method as a non-blocking call, to
        # allow starting the encoders of multiple axes simultaneously
        self._suspend_req[axis].put(MNG_START)
        self._axis_ready[axis].wait()

    def _releaseAxis(self, axis, delay=0):
        """
        Let the axis servo be turned off (within some time)
        delay (0<float): time (in s) before actually turning off the encoder
        """
        self._suspend_req[axis].put(time.time() + delay)

    def _updateSpeedAccel(self, axis):
        """
        Update the speed and acceleration values for the given axis.
        It's only done if necessary, and only for the current closed- or open-
        loop mode.
        axis (str): the axis
        """
        prev_speed = self._prev_speed_accel[0].get(axis, None)
        new_speed = self._speed[axis]
        if prev_speed != new_speed:
            # TODO: check it's within range
            self.SetCLVelocity(axis, new_speed / self._upm[axis])
            self._prev_speed_accel[0][axis] = new_speed

        prev_accel = self._prev_speed_accel[1].get(axis, None)
        new_accel = self._accel[axis]
        if prev_accel != new_accel:
            # TODO: check it's within range
            self.SetCLAcceleration(axis, new_accel / self._upm[axis])
            self.SetCLDeceleration(axis, new_accel / self._upm[axis])
            self._prev_speed_accel[1][axis] = new_accel

    def moveRel(self, axis, distance):
        """
        See Controller.moveRel
        """
        assert(axis in self._channels)
        self._acquireAxis(axis)

        # TODO: instead of handling it ad-hoc for each series of message, put
        #  the trick in the bus accesser: if the last command was an order to
        #  another controller, first force a ERR? on that previous controller,
        #  and then send the requested query.

        # The E861 over the network controller send (sometimes) garbage if
        # several controllers get an OSM command without any query in between.
        # This ensures there is one query after each command.
        with self.busacc.ser_access:
            self._updateSpeedAccel(axis)
            # We trust the caller that it knows it's in range
            # (worst case the hardware will not go further)
            self.MoveRel(axis, distance / self._upm[axis])

            # In case it's an update, the actual distance can be different
            # Note: merging the queries doesn't seem to save time... actually it
            # appears to even slow down the answers (maybe because the controller
            # waits for more answers to send)
            pos = self.getPosition(axis)
            tpos = self.getTargetPosition(axis)
            act_dist = tpos - pos

            self.checkError()

        return act_dist

    def moveAbs(self, axis, position):
        """
        See Controller.moveAbs
        """
        assert(axis in self._channels)
        self._acquireAxis(axis)

        # The E861 over the network controller send (sometimes) garbage if
        # several controllers get an OSM command without any query in between.
        # This ensures there is one query after each command.
        with self.busacc.ser_access:
            self._updateSpeedAccel(axis)
            # We trust the caller that it knows it's in range
            # (worst case the hardware will not go further)

            self.MoveAbs(axis, position / self._upm[axis])

            old_pos = self.getPosition(axis)
            distance = position - old_pos

            self.checkError()

        return distance

    def getPosition(self, axis):
        """
        Find current position as reported by the sensor
        return (float): the current position of the given axis
        """
        with self._pos_lock[axis]:
            return self.GetPosition(axis) * self._upm[axis]

    def getTargetPosition(self, axis):
        return self.GetTargetPosition(axis) * self._upm[axis]

    # Warning: if the settling window is too small or settling time too big,
    # it might take several seconds to reach target (or even never reach it)
    def isMoving(self, axes=None):
        """
        Indicate whether the motors are moving (ie, last requested move is over)
        axes (None or set of bytes): axes to check whether for move, or all if None
        return (boolean): True if at least one of the axes is moving, False otherwise
        raise PIGCSError: if there is an error with the controller
        """
        if axes is None:
            axes = self._channels
        else:
            assert axes.issubset(set(self._channels))

        # With servo on, it might constantly be _slightly_ moving (around the
        # target), so it's much better to use IsOnTarget info. The controller
        # needs to be correctly configured with the right window size.
        for a in axes:
            # A merge of the query with error check causes a long delay (~40 ms)
            # in the answer
            if not self.IsOnTarget(a, check=False):
                return True

        # Nothing is moving => turn off encoder (in a few seconds)
        for a in axes:
            # Note: this will also turn off the servo, which leads to relax mode
            if self._auto_suspend:
                self._releaseAxis(a, self._auto_suspend)  # release in 10 s (5x the cost to start)

        return False

        # TODO: handle the fact that if the stage reaches the physical limit without knowing,
        #  the move will fail with:
        #  PIGCSError: PIGCS error -1024: Motion error: position error too large, servo is switched off automatically
        #  => put back the servo if necessary
        #  => keep checking for errors at the same time as ONT?

        # FIXME: it seems that on the C867 if the axis is stopped while moving, isontarget()
        #  will sometimes keep saying it's not reached forever. However, the documentation
        #  says that the target position is set to the current position after a
        #  stop (to avoid this very problem). On E861 it does update the target position fine.
        #  Need to investigate
        #  MOV 1 1.1
        #  MOV? 1  # read target pos
        #  time.sleep(0.01)
        #  ONT? 1  # should be false
        #  STP # also try HLT
        #  MOV? 1  # should be new pos
        #  POS? 1  # Should be very close
        #  ONT? 1 # Should be true at worst a little after the settle time window

    def stopMotion(self):
        super(CLRelController, self).stopMotion()
        for c in self._channels:
            self._releaseAxis(c, delay=1)

    def startReferencing(self, axis):
        """
        Start a referencing move. Position will change, as well as absolute positions.
        axis (str)
        """
        self._acquireAxis(axis)

        # Note: Setting position only works if ron is disabled when not referencable.
        # It's possible also to indirectly set it after referencing, but then it will conflict
        # with TMN/TMX and some correct moves will fail.

        if self._hasRefSwitch[axis]:
            self.SetReferenceMode(axis, True)
            self.ReferenceToSwitch(axis)
            self._referenced = True
        elif self._hasLimitSwitches[axis]:
            self._referenced = False
            raise NotImplementedError("Don't know how to reference to limit yet")
            # TODO code for reference support to be implemented, for now code unreachable
            self.ReferenceToLimit(axis)
            # TODO: need to do that after the move is complete
            self.waitEndMotion(set(axis))
            # Go to 0 (="home")
            self.MoveAbs(axis, 0)
        else:
            self._referenced = False
            # TODO: we _could_ think of hacky way, such as moving a lot to
            # one direction to be sure to hit the physical limit, and then
            # marking it as the lower limit, using the range.
            raise ValueError("Axis has no reference or limit switch so cannot be referenced")

    def isReferenced(self, axis):
        """
        returns (bool or None): True if the axis is referenced, or None if it's
        not possible
        """
        if not self._hasRefSwitch[axis] and not self._hasLimitSwitches[axis]:
            return None

        # Check flag referenced and HW report the same status for referencing
        if self.IsReferenced(axis) != self._referenced:
            if self._referenced:
                logging.warning("Axis %d/%s is not referenced, although it was expected to be.",
                                self.address, axis)
                self._referenced = False
            else:
                # The controller always thinks it's referenced, because we use SetReferenceMode()
                # to allow moves even if not referencable.
                logging.debug("Axis %d/%s is not referenced yet", self.address, axis)

        return self._referenced


class OLController(Controller):
    """
    Controller managed via standard open-loop commands (ex: E-861)
    """
    def __init__(self, busacc, address=None, axes=None,
                 dist_to_steps=None, min_dist=None):
        """
        dist_to_steps (0 < float): allows to calibrate how many steps correspond
          to a given distance (in step/m). Default is 1e5, a value that could
          make sense.
        min_dist (0 <= float < 1): minimum distance required for the axis to
          even move (in m). Below this distance, a command will be sent, but it
          is expected that the actuator doesn't move at all. Default is 0.01
          step (= 0.01 / dist_to_steps).
        """
        if dist_to_steps and not (0 < dist_to_steps):
            raise ValueError("dist_to_steps (%s) must be > 0" % dist_to_steps)
        if min_dist and not (0 <= min_dist < 1):
            raise ValueError("min_dist (%s) must be between 0 and 1 m" % min_dist)

        super(OLController, self).__init__(busacc, address, axes)

        # TODO: params should be per axis
        # TODO: allow to pass a polynomial
        self._dist_to_steps = dist_to_steps or 1e5 # step/m
        if min_dist is None:
            self.min_stepsize = 0.01 # step, under this, no move at all
        else:
            self.min_stepsize = min_dist * self._dist_to_steps

        for a, cl in axes.items():
            if a not in self._channels:
                raise LookupError("Axis %s is not supported by controller %d" % (a, address))

            if cl: # want closed-loop?
                raise ValueError("Initialising OLController with request for closed-loop")
            # that should be the default, but for safety we force it
            self.SetServo(a, False)
            self.SetStepAmplitude(a, 55) # maximum is best
            self._position[a] = 0
            # TODO: use LIM? values * 2, as for the CLRel version?
            # Unknown range => give room
            self.pos_rng[a] = (-1, 1) # m

            try:
                # (max m/s) = (max step/s) * (step/m)
                max_speed = float(self.GetParameter("1", 0x7000204)) / self._dist_to_steps # m/s
                # Note: the E-861 claims max 0.015 m/s but actually never goes above 0.004 m/s
                # (max m/s²) = (max step/s²) * (step/m)
#                 max_accel = float(self.GetParameter(1, 0x7000205)) / self._dist_to_steps # m/s²
            except (IOError, ValueError) as err:
                # TODO use CL info if not available?
                # TODO detect better that it's just a problem of sending unsupported command/value
                # Put default (large values)
                self.GetErrorNum() # reset error (just in case)
                max_speed = 0.5 # m/s
#                 max_accel = 0.01 # m/s²
                logging.debug("Using default speed value after error '%s'", err)

            # TODO just read the current values
            self.speed_rng[a] = (10e-6, max_speed)  # m/s (default low value)
            self._speed[a] = (self.speed_rng[a][0] + self.speed_rng[a][1]) / 2  # m/s

            # acceleration (and deceleration)
            try:
                self._accel[a] = self.GetOLAcceleration(a) / self._dist_to_steps  # m/s² (both acceleration and deceleration)
            except (IOError, NotImplementedError):
                try:
                    # Try with the parameter (for older versions of E-861)
                    self._accel[a] = float(self.GetParameter("1", 0x7000202)) / self._dist_to_steps  # m/s²
                except (IOError, ValueError) as err:
                    self._accel[a] = 0.01  # m/s² # Unknown
                    logging.debug("Using default acceleration value after error '%s'", err)

        self._prev_speed_accel = ({}, {})

    def _convertDistanceToDevice(self, distance):
        """
        converts meters to the unit for this device (steps) in open-loop.
        distance (float): meters (can be negative)
        return (float): number of steps, <0 if going opposite direction
            0 if too small to move.
        """
        steps = distance * self._dist_to_steps
        if abs(steps) < self.min_stepsize:
            return 0

        return steps

    def _convertSpeedToDevice(self, speed):
        """
        converts meters/s to the unit for this device (steps/s) in open-loop.
        distance (float): meters/s (can be negative)
        return (float): number of steps/s, <0 if going opposite direction
        """
        steps_ps = speed * self._dist_to_steps
        return max(1, steps_ps) # don't go at 0 m/s!

    # in linear approximation, it's the same
    _convertAccelToDevice = _convertSpeedToDevice

    def _updateSpeedAccel(self, axis):
        """
        Update the speed and acceleration values for the given axis.
        It's only done if necessary, and only for the current closed- or open-
        loop mode.
        axis (str): the axis
        """
        prev_speed = self._prev_speed_accel[0].get(axis, None)
        new_speed = self._speed[axis]
        if prev_speed != new_speed:
            steps_ps = self._convertSpeedToDevice(new_speed)
            self.SetOLVelocity(axis, steps_ps)
            self._prev_speed_accel[0][axis] = new_speed

        prev_accel = self._prev_speed_accel[1].get(axis, None)
        new_accel = self._accel[axis]
        if prev_accel != new_accel:
            steps_pss = self._convertAccelToDevice(new_accel)
            self.SetOLAcceleration(axis, steps_pss)
            self.SetOLDeceleration(axis, steps_pss)
            self._prev_speed_accel[1][axis] = new_accel

    def moveRel(self, axis, distance):
        """
        See Controller.moveRel
        """
        assert(axis in self._channels)

        self._updateSpeedAccel(axis)
        steps = self._convertDistanceToDevice(distance)
        if steps == 0: # if distance is too small, report it
            return 0
            # TODO: try to move anyway, just in case it works

        self.OLMoveStep(axis, steps)
        # TODO use OLAnalogDriving for very small moves (< 5µm)?

        # Warning: this is not just what is looks like!
        # The E861 over the network controller send (sometimes) garbage if
        # several controllers get an OSM command without any query in between.
        # This ensures there is one query after each command.
        self.checkError()

        duration = abs(distance) / self._speed[axis]
        self._storeMove(axis, distance, duration)
        return distance

    # TODO: call RelaxPiezos after the end of a move

    def stopMotion(self):
        super(OLController, self).stopMotion()
        for c in self._channels:
            self._storeStop(c)
            self.RelaxPiezos(c)


class SMOController(Controller):
    """
    Controller managed via the test open-loop command "SMO" (ex: C-867)
    """
    def __init__(self, busacc, address=None, axes=None, vmin=2., speed_base=0.03):
        """
        vmin (0.5 < float < 10): lowest voltage at which the actuator moves
          reliably in V. This is the voltage used when performing small moves
          (~< 50 µm).
        speed_base (0<float<10): speed in m/s at the base voltage (3.5V). The
          base voltage is used for long moves (~> 50 µm).
        """
        # TODO: need 4 settings:
        # vmin/speed_min: voltage/speed for the smallest moves (will be used < 50 µm)
        # vmax/speed_max: voltage/speed for the big moves (will be used above)

        if not (0.5 < vmin < 10):
            raise ValueError("vmin (%s) must be between 0.5 and 10 V" % vmin)
        if not (0 < speed_base < 10):
            raise ValueError("speed_base (%s) must be between 0 and 10 m/s" % speed_base)

        super(SMOController, self).__init__(busacc, address, axes)
        for a, cl in axes.items():
            if a not in self._channels:
                raise LookupError("Axis %d is not supported by controller %d" % (a, address))

            if cl: # want closed-loop?
                raise ValueError("Initialising OLController with request for closed-loop")
            # that should be the default, but for safety we force it
            self.SetServo(a, False)
            self._position[a] = 0
            # Unknown range => give room
            self.pos_rng[a] = (-1, 1)  # m

            # Don't authorize different speeds or accels
            self._speed_base = speed_base
            self.speed_rng[a] = (speed_base, speed_base)  # m/s
            self._speed[a] = speed_base  # m/s
            self._accel[a] = 0.01  # m/s² (actually I've got no idea)

        # Get maximum motor output parameter (0x9) allowed
        # Because some type of stages cannot bear as much as the full maximum
        # The maximum output voltage is calculated following this formula:
        # 200 Vpp*Maximum motor output/32767
        self._max_motor_out = int(self.GetParameter("1", 0x9))
        # official approx. min is 3V, but from test, it can sometimes go down to 1.5V.
        self._min_motor_out = int((vmin / 10) * 32767) # encoded as a ratio of 10 V * 32767
        if self._max_motor_out < self._min_motor_out:
            raise ValueError("Controller report max voltage lower than vmin=%g V" % vmin)

        # FIXME: the manual somehow implies that writing macros writes on the
        # flash, which is only possible a number of times. So only write macro
        # if it's different?

        # Set up a macro that will do the job
        # To be called like "MAC START OS 16000 500"
        # First param is voltage between -32766 and 32766
        # Second param is delay in ms between 1 and 9999
        # Note: it seems it doesn't work to have a third param is the axis
        # WARNING: old firmware (<1.2) don't support macro arguments, but there
        # is not a clear way to detect them (but checking firmware version)
        # Note: macro name is short to be sure to have a short command line
        mac = "MAC BEG OS\n" \
              "%(n)d SMO 1 $1\n" \
              "%(n)d DEL $2\n"   \
              "%(n)d SMO 1 0\n"  \
              "%(n)d MAC END\n" % {"n": self.address}
        self._sendOrderCommand(mac)

    def StopOLViaPID(self, axis):
        """
        Stop the fake PID driving when doing open-loop
        """
        self._sendOrderCommand("SMO %s 0\n" % axis)

    def OLMovePID(self, axis, voltage, t):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (str): axis name
        voltage (-32766<=int<=32766): voltage for the PID control. <0 to go towards
          the negative direction. 32766 is 10V
        t (0<int <= 9999): time in ms.
        """
        # Uses MAC OS, based on SMO
        assert(axis == '1') # seems not possible to have 3 parameters?!
        assert(-32768 <= voltage <= 32767)
        assert(0 < t <= 9999)

        # From experiment: a delay of 0 means actually 2**16, and >= 10000 it's 0
        self._sendOrderCommand("MAC START OS %d %d\n" % (voltage, t))

    def _isAxisMovingOLViaPID(self, axis):
        """
        axis (str): axis name
        returns (boolean): True moving axes for the axes controlled via PID
        raise PIGCSError if an error on a controller happened
        """
        # "SMO?" (Get Control Value)
        # Reports the speed set. If it's 0, it's not moving, otherwise, it is.
        errs, answer = self._sendQueryCommand(["ERR?\n", "SMO? %s\n" % axis])
        err = int(errs)
        if err:
            raise PIGCSError(err)

        value = answer.split("=")[1]
        if value == "0":
            return False
        else:
            return True

    # TODO: automatically pick it based on the firmware name
    cycles_per_s = 20000 # 20 kHz for new experimental firmware
    #cycles_per_s = 1000 # 1 kHz for normal firmware

    _base_motor_out = int((3.5 / 10) * 32767) # High enough to ensures it's never stuck

    def _convertDistanceSpeedToPIDControl(self, distance, speed):
        """
        converts meters and speed to the units for this device (~V, ms) in
        open-loop via PID control.
        distance (float): meters (can be negative)
        speed (0<float): meters/s UNUSED
        return (tuple: int, 0<number, float): PID control (in device unit),
          duration (in device cycles), expected actual distance (in m)
        """
        # TODO: smooth transition of voltage between 50µm and 20µm?
        if abs(distance) > 50e-6: # big move
            # Use the fastest speed
            voltage_u = max(self._min_motor_out, self._base_motor_out)
            speed = self._speed_base # m/s
        else: # small move
            # Consider the speed linear of the voltage with:
            # * vmin - 0.5 -> 0 m/s
            # * base voltage -> speed_base
            # * vmin -> 0.5 * speed_base / (bv - vmin +0.5)
            # It's totally wrong, but approximately correct
            voltage_u = self._min_motor_out
            bv = self._base_motor_out * (10 / 32767)
            vmin = self._min_motor_out * (10 / 32767)
            speed = 0.5 * self._speed_base / (bv - vmin + 0.5)

        mv_time = abs(distance) / speed # s
        mv_time_cy = int(round(mv_time * self.cycles_per_s)) # cycles
        if mv_time_cy < 1:
            # really no hope
            return 0, 0, 0
        elif mv_time_cy < 5:
            # On fine grain firmware, below 6 cyles gives always the same time
            mv_time_cy = 5
        elif mv_time_cy >= 10000:
            logging.debug("Too big distance of %f m, shortening it", distance)
            mv_time_cy = 9999

        act_dist = (mv_time_cy / self.cycles_per_s) * speed # m (very approximate)

        if distance < 0:
            voltage_u = -voltage_u
            act_dist = -act_dist

        return voltage_u, mv_time_cy, act_dist

    def moveRel(self, axis, distance):
        """
        See Controller.moveRel
        """
        assert(axis in self._channels)

        speed = self._speed[axis]
        v, t, ad = self._convertDistanceSpeedToPIDControl(distance, speed)
        if t == 0: # if distance is too small, report it
            logging.debug(u"Move of %g µm too small, not moving", distance * 1e-6)
            return 0
        else:
            self.OLMovePID(axis, v, t)
            duration = t / self.cycles_per_s
        logging.debug("Moving axis at %f V, for %f ms", v * (10 / 32767), duration)

        self._storeMove(axis, ad, duration)
        return ad

    def isMoving(self, axes=None):
        """
        See Controller.isMoving
        """
        if axes is None:
            axes = self._channels
        else:
            assert axes.issubset(set(self._channels))

        for c in axes:
            if self._isAxisMovingOLViaPID(c):
                return True
        return False

    def stopMotion(self):
        """
        Stop the motion on all axes immediately
        Implementation for open-loop PID control
        """
        self.Stop() # doesn't seem to be effective with SMO macro
        for c in self._channels:
            self.StopOLViaPID(c)
            self._storeStop(c)
            self.RelaxPiezos(c)


class Bus(model.Actuator):
    """
    Represent a chain of PIGCS controllers over a serial port
    """
    def __init__(self, name, role, port, axes, baudrate=38400,
                 dist_to_steps=None, min_dist=None,
                 vmin=None, speed_base=None, auto_suspend=None,
                 suspend_mode=None, master=254,
                 _addresses=None, **kwargs):
        """
        port (string): name of the serial port to connect to the controllers
         (starting with /dev on Linux or COM on windows) or "autoip" for
         automatically finding an ip address or a "host[:port]".
        axes (dict string -> 3-tuple(1<=int<=16, 1<=int, boolean): the configuration
         of the network. For each axis name associates the controller address,
         channel, and whether it's closed-loop (absolute positioning) or not.
         Note that even if it's made of several controllers, each controller is
         _not_ seen as a child from the odemis model point of view.
        baudrate (int): baudrate of the serial port (default is the recommended
          38400). Use .scan() to detect it.
        auto_suspend (dict str -> (False or 0 < float)): see CLRelController.
          Default is 10 s.
        suspend_mode (dict str -> "read" or "full"): see CLRelController.
          Default is "read".
        master (0<=int<=255): The address of the "master" controller when connecting over
            TCP/IP to multiple controllers. It is unused when connecting over serial port.
        Next 3 parameters are for calibration, see Controller for definition
        dist_to_steps (dict string -> (0 < float)): axis name -> value
        min_dist (dict string -> (0 <= float < 1)): axis name -> value
        vpms (dict string -> (0 < float)): axis name -> value
        """
        dist_to_steps = dist_to_steps or {}
        min_dist = min_dist or {}
        vmin = vmin or {}
        speed_base = speed_base or {}
        auto_suspend = auto_suspend or {}
        suspend_mode = suspend_mode or {}

        # Prepare initialisation by grouping axes from the same controller
        ac_to_axis = {} # address, channel -> axis name
        controllers = {} # address -> kwargs (axes, dist_to_steps, min_dist, vpms...)
        for axis, (add, channel, isCL) in axes.items():
            if isinstance(channel, int):
                channel = "%d" % channel # str
            if add not in controllers:
                controllers[add] = {"axes": {}}
            kwc = controllers[add]
            if channel in kwc["axes"]:
                raise ValueError("Multiple axes got assigned to controller %d channel %d" % (add, channel))
            ac_to_axis[(add, channel)] = axis
            kwc["axes"].update({channel: isCL})
            # FIXME: for now we rely on the fact 1 axis = 1 controller for the calibration values
            if axis in dist_to_steps:
                kwc["dist_to_steps"] = dist_to_steps[axis]
            if axis in min_dist:
                kwc["min_dist"] = min_dist[axis]
            if axis in vmin:
                kwc["vmin"] = vmin[axis]
            if axis in speed_base:
                kwc["speed_base"] = speed_base[axis]
            if axis in auto_suspend:
                kwc["auto_suspend"] = auto_suspend[axis]
            if axis in suspend_mode:
                kwc["suspend_mode"] = suspend_mode[axis]

        # Special support for no address
        if len(controllers) == 1 and None in controllers:
            master = None  # direct connection to the controller

        self.accesser = self._openPort(port, baudrate, _addresses, master=master)

        # Init each controller
        self._axis_to_cc = {} # axis name => (Controller, channel)
        axes_def = {} # axis name => axis definition
        # TODO also a rangesRel : min and max of a step
        speed = {}
        referenced = {}
        for address, kwc in controllers.items():
            try:
                controller = Controller(self.accesser, address, **kwc)
            except IOError:
                logging.exception("Failed to find a controller with address %s on %s", address, port)
                raise
            except LookupError:
                logging.exception("Failed to initialise controller %s on %s", address, port)
                raise
            channels = kwc["axes"]
            for c, isCL in channels.items():
                axis = ac_to_axis[(address, c)]
                self._axis_to_cc[axis] = (controller, c)

                rng = controller.pos_rng[c]
                speed_rng = controller.speed_rng[c]
                # Just to make sure it doesn't go too fast
                speed[axis] = controller.getSpeed(c) # m/s
                ad = model.Axis(unit="m", range=rng, speed=speed_rng,
                                canAbs=isCL, canUpdate=controller.canUpdate[c])
                axes_def[axis] = ad

                refed = controller.isReferenced(c)
                if refed is not None:
                    referenced[axis] = refed

        # this set ._axes
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        # It seems it can cause garbage data with E-861/IP if multiple commands
        # are sent (ex, ERR + ...) and almost at the same time, another axis is
        # sent command (ex, OSM). Anyway, it doesn't make much sense to refresh
        # the position while a move is on going (which already takes care of
        # updating the position for the axes that matter).
        # To be hold when updating position, or moving axis, so they don't
        # interfere with each other
        self._axis_moving_lock = threading.Lock()

        # TODO: allow to override the unit (per axis)
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        self._cl_axes = set(an for an, ax in self.axes.items() if ax.canAbs)
        self._pos_needs_update = threading.Event()
        if self._cl_axes:
            # To detect position changes not related to a requested move (only CL axes can do that)
            self._pos_updater = threading.Thread(target=self._refreshPosition,
                                                 name="PIGCS position refresher")
            self._pos_updater.daemon = True
            self._pos_updater.start()
            self._pos_updater_stop = threading.Event()
        else:
            self._pos_updater = None

        # RO VA dict axis -> bool: True if the axis has been referenced
        # Only axes which can be referenced are listed
        self.referenced = model.VigilantAttribute(referenced, readonly=True)

        # min speed = don't be crazy slow. max speed from hardware spec
        gspeed_rng = (min(ad.speed[0] for ad in self.axes.values()),
                      max(ad.speed[1] for ad in self.axes.values()))
        self.speed = model.MultiSpeedVA(speed, range=gspeed_rng,
                                        unit="m/s", setter=self._setSpeed)

        # set HW and SW version
        self._swVersion = self.accesser.driverInfo
        hwversions = []
        for axis, (ctrl, channel) in self._axis_to_cc.items():
            hwversions.append("'%s': %s (GCS %s) for %s" %
                              (axis, ctrl.GetIdentification(),
                               ctrl.GetSyntaxVersion(), ctrl.GetStageName(channel))
                             )
        self._hwVersion = ", ".join(hwversions)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

    def _updatePosition(self, axes=None):
        """
        update the position VA
        axes (None or set of str): the axes to update (None indicates all of them)
        """
        if axes is None:
            pos = {}
        else:
            pos = self.position._value.copy()

        npos = {}
        for a, (controller, channel) in self._axis_to_cc.items():
            if axes is None or a in axes:
                try:
                    npos[a] = controller.getPosition(channel)
                except PIGCSError:
                    logging.warning("Failed to update position of axis %s", a, exc_info=True)

        pos.update(self._applyInversion(npos))
        logging.debug("Reporting new position at %s", pos)

        self.position._set_value(pos, force_write=True)

    def _refreshPosition(self):
        """
        Called regularly to update the position of the closed-loop axes
        """
        try:
            while True:
                # wait until a pos update is requested... or once in a while.
                self._pos_needs_update.wait(10)
                if self._pos_updater_stop.is_set():
                    return

                # Don't immediately read the position, because there are some
                # chances the pos update is due to a move ending, which could
                # be followed by another move. The start next move has a higher
                # priority than reading position.
                if self._pos_updater_stop.wait(0.3):
                    return

                with self._axis_moving_lock:
                    logging.debug("Will refresh position of axes %s", self._cl_axes)
                    self._updatePosition(self._cl_axes)

                self._pos_needs_update.clear()
        except Exception:
            logging.exception("Position refresher thread failed")
        finally:
            logging.debug("Ending position refresher")

    def _setSpeed(self, value):
        """
        value (dict string-> float): speed for each axis
        returns (dict string-> float): the new value
        """
        for axis, v in value.items():
            rng = self._axes[axis].speed
            if not rng[0] <= v <= rng[1]:
                raise ValueError("Requested speed of %f for axis %s not within %f->%f" %
                                 (v, axis, rng[0], rng[1]))
            controller, channel = self._axis_to_cc[axis]
            controller.setSpeed(channel, v)
        return value

    def _createFuture(self, axes, update):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        axes (set of str): the axes that are moved
        update (bool): if it's an update move
        """
        # TODO: do this via the __init__ of subclass of Future?
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful

        f._update_axes = set()  # axes handled by the move, if update
        if update:
            # Check if all the axes support it
            if all(self.axes[a].canUpdate for a in axes):
                f._update_axes = axes
            else:
                logging.warning("Trying to do a update move on axes %s not supporting update", axes)

        f.task_canceller = self._cancelCurrentMove
        return f

    @isasync
    def moveRel(self, shift, update=False):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        # TODO: drop an axis if the distance is too small to make sense

        f = self._createFuture(set(shift.keys()), update)
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f
    moveRel.__doc__ = model.Actuator.moveRel.__doc__

    @isasync
    def moveAbs(self, pos, update=False):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createFuture(set(pos.keys()), update)
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f
    moveAbs.__doc__ = model.Actuator.moveAbs.__doc__

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)

        f = self._createFuture(axes, False)
        f = self._executor.submitf(f, self._doReference, f, axes)

        return f
    reference.__doc__ = model.Actuator.reference.__doc__

    def _doReference(self, future, axes):
        """
        Actually runs the referencing code.
        :param axes: (set of str) The axes that should be referenced.
        :raises:
            IOError: if referencing failed due to hardware
            CancelledError: if was cancelled
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
                    controller, channel = self._axis_to_cc[a]
                    self.referenced._value[a] = False
                    controller.startReferencing(channel)
                    self._waitEndMove(future, (a,), time.time() + 100)  # block until it's over
                    self.referenced._value[a] = controller.isReferenced(channel)
            except CancelledError:
                future._was_stopped = True
                raise
            except Exception:
                logging.exception("Referencing failure")
                raise
            finally:
                # We only notify after updating the position so that when a listener
                # receives updates both values are already updated.
                self._updatePosition(axes)  # all the referenced axes should be back to reference position
                # read-only so manually notify
                self.referenced.notify(self.referenced.value)

    def stop(self, axes=None):
        """
        stops the motion on all axes
        Warning: this might stop the motion even of axes not managed (it stops
          all the axes of the related controllers).
        axes (set of str)
        """
        self._executor.cancel()

        # For safety, request a stop on all axes
        # TODO: use the broadcast address to request a stop to all
        # controllers on the bus at the same time?
        axes = axes or self._axes
        ctlrs = set(self._axis_to_cc[an][0] for an in axes)
        for controller in ctlrs:
            controller.stopMotion()

    def _doMoveRel(self, future, shift):
        """
        Blocking and cancellable relative move
        future (Future): the future it handles
        shift (dict str -> float): axis name -> relative target position
        """
        with future._moving_lock, self._axis_moving_lock:
            # Prepare the encoder of all the axes first (non-blocking)
            for an, v in shift.items():
                controller, channel = self._axis_to_cc[an]
                if hasattr(controller, "prepareAxisForMove"):
                    controller.prepareAxisForMove(channel)

            end = 0  # expected end
            moving_axes = set()
            try:
                for an, v in shift.items():
                    moving_axes.add(an)
                    controller, channel = self._axis_to_cc[an]
                    dist = controller.moveRel(channel, v)
                    # compute expected end
                    dur = driver.estimateMoveDuration(abs(dist),
                                                      controller.getSpeed(channel),
                                                      controller.getAccel(channel))
                    logging.debug("Expecting to move %g m = %g s", dist, dur)
                    end = max(time.time() + dur, end)
            except PIGCSError as ex:
                # If one axis failed, better be safe than sorry: stop the other
                # ones too.
                logging.info("Failure during start of move (%s), will cancel all of it.", ex)
                ctlrs = set(self._axis_to_cc[an][0] for an in moving_axes)
                for controller in ctlrs:
                    try:
                        controller.stopMotion()
                    except Exception:
                        logging.exception("Failed to stop axis %s after failure", an)
                self._updatePosition()
                raise

            self._waitEndMove(future, moving_axes, end)
        logging.debug("Relative move completed")

    def _doMoveAbs(self, future, pos):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> absolute target position
        """
        with future._moving_lock, self._axis_moving_lock:
            for an, v in pos.items():
                controller, channel = self._axis_to_cc[an]
                if hasattr(controller, "prepareAxisForMove"):
                    controller.prepareAxisForMove(channel)

            end = 0  # expected end
            moving_axes = set()
            try:
                for an, v in pos.items():
                    moving_axes.add(an)
                    controller, channel = self._axis_to_cc[an]
                    dist = controller.moveAbs(channel, v)
                    # compute expected end
                    dur = driver.estimateMoveDuration(abs(dist),
                                                      controller.getSpeed(channel),
                                                      controller.getAccel(channel))
                    logging.debug("Expecting to move %g m = %g s", dist, dur)
                    end = max(time.time() + dur, end)
            except PIGCSError:
                # If one axis failed, better be safe than sorry: stop the other
                # ones too.
                ctlrs = set(self._axis_to_cc[an][0] for an in moving_axes)
                for controller in ctlrs:
                    try:
                        controller.stopMotion()
                    except Exception:
                        logging.exception("Failed to stop axis %s after failure", an)
                self._updatePosition()
                raise

            self._waitEndMove(future, moving_axes, end)
        logging.debug("Absolute move completed")

    def _waitEndMove(self, future, axes, end):
        """
        Wait until all the given axes are finished moving, or a request to
        stop has been received.
        future (Future): the future it handles
        axes (set of str): the axes names to check
        end (float): expected end time
        raise:
            CancelledError: if cancelled before the end of the move
            PIGCSError: if a controller reported an error
            TimeoutError: if took too long to finish the move
        """
        moving_axes = set(axes)

        need_pos_update = True
        raise_exp = None  # exception to raise at the end

        last_upd = time.time()
        dur = max(0.01, min(end - last_upd, 60))
        max_dur = dur * 2 + 3
        timeout = last_upd + max_dur
        last_axes = moving_axes.copy()  # moving axes as of last position update
        try:
            while not future._must_stop.is_set():
                # If next future is update and all moving_axes are in next future axes
                # => stop immediately without updating the positions
                nf = self._executor.get_next_future(future)
                if nf is not None and moving_axes <= nf._update_axes:
                    need_pos_update = False
                    # TODO: make sure the position gets updated from time to time
                    # there is non-ending series of update moves.
                    # => reuse the .GetPosition() of the controller.moveRel()?
                    logging.debug("Ending move control early as next move is an update containing %s", moving_axes)
                    return

                for an in moving_axes.copy():  # need copy to remove during iteration
                    controller, channel = self._axis_to_cc[an]
                    # TODO: change isMoving to report separate info on multiple channels
                    if not controller.isMoving({channel}):
                        moving_axes.discard(an)
                        try:
                            controller.checkError()
                        except PIGCSError as ex:
                            raise_exp = ex  # Keep it for the end, while waiting for other axes
                            logging.error("Move on axis %s has failed: %s", an, ex)
                if not moving_axes:
                    # no more axes to wait for
                    break

                now = time.time()
                if now > timeout:
                    logging.info("Stopping move due to timeout after %g s.", max_dur)
                    ctlrs = set(self._axis_to_cc[an][0] for an in moving_axes)
                    for controller in ctlrs:
                        controller.stopMotion()
                    raise TimeoutError("Move is not over after %g s, while "
                                       "expected it takes only %g s" %
                                       (max_dur, dur))

                # Update the position from time to time (10 Hz)
                if now - last_upd > 0.1 or last_axes != moving_axes:
                    self._updatePosition(last_axes)
                    last_upd = now
                    last_axes = moving_axes.copy()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0.001, min(left / 2, 0.1))
                future._must_stop.wait(sleept)
            else:
                logging.debug("Move of axes %s cancelled before the end", axes)
                # stop all axes still moving
                ctlrs = set(self._axis_to_cc[an][0] for an in moving_axes)
                for controller in ctlrs:
                    controller.stopMotion()
                future._was_stopped = True
                raise CancelledError()
        except Exception:
            raise
        else:
            if raise_exp:
                raise raise_exp
        finally:
            if need_pos_update:
                # Position update takes quite some time, which increases latency for
                # the caller to know the move is done => only update the last axes
                # moving (and don't notify the VA) and update the rest of axes in a
                # separate thread
                self._updatePosition(last_axes)
            self._pos_needs_update.set()

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

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        if self._pos_updater:
            self._pos_updater_stop.set()
            self._pos_needs_update.set()  # To force the thread to check the stop event
            self._pos_updater = None

        ctlrs = set(ct for ct, ch in self._axis_to_cc.values())
        for controller in ctlrs:
            controller.terminate()

        super(Bus, self).terminate()

    def selfTest(self):
        """
        No move should be going one while doing a self-test
        """
        passed = True
        ctlrs = set(ct for ct, ch in self._axis_to_cc.values())
        for controller in ctlrs:
            logging.info("Testing controller %d", controller.address)
            passed &= controller.selfTest()

        return passed

    @classmethod
    def _openPort(cls, port, baudrate=38400, _addresses=None, master=254):
        if port.startswith("/dev/") or port.startswith("COM"):
            ser = cls._openSerialPort(port, baudrate, _addresses)
            return SerialBusAccesser(ser)
        else: # ip address
            if port == "autoip": # Search for IP (and hope there is only one result)
                ipmasters = cls._scanIPMasters()
                if not ipmasters:
                    raise model.HwError("Failed to find any PI network master controller")
                host, ipport = ipmasters[0]
                logging.info("Will connect to %s:%d", host, ipport)
            else:
                # split the (IP) port, separated by a :
                if ":" in port:
                    host, ipport_str = port.split(":")
                    ipport = int(ipport_str)
                else:
                    host = port
                    ipport = 50000 # default

            sock = cls._openIPSocket(host, ipport)
            return IPBusAccesser(sock, master)

    @classmethod
    def scan(cls, port=None):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        returns (list of 2-tuple): name, kwargs (port, axes(channel -> CL?)
        Note: it's obviously not advised to call this function if moves on the motors are ongoing
        """
        if port:
            ports = [port]
        else:
            # TODO: use serial.tools.list_ports.comports() (but only availabe in pySerial 2.6)
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range(0, 8)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')

        logging.info("Serial network scanning for PI-GCS controllers in progress...")
        found = []  # (list of 2-tuple): name, args (port, axes(channel -> CL?)
        axes_names = "xyzabcdefghijklmnopqrstuvw"
        for p in ports:
            try:
                # check all possible baud rates, in the most likely order
                for br in (38400, 9600, 19200, 115200):
                    logging.debug("Trying port %s at baud rate %d", p, br)
                    ser = cls._openSerialPort(p, br)
                    controllers = Controller.scan(SerialBusAccesser(ser))
                    if controllers:
                        axis_num = 0
                        arg = {}
                        for add, axes in controllers.items():
                            for a, cl in axes.items():
                                arg[axes_names[axis_num]] = (add, a, cl)
                                axis_num += 1
                        found.append(("Actuator " + os.path.basename(p),
                                     {"port": p, "baudrate": br, "axes": arg}))
                        # it implies the baud rate was correct, and as it's impossible
                        # to have devices on different baud rate, so we are done
                        break
            except (serial.SerialException, model.HwError):
                # not possible to use this port? next one!
                pass
            except Exception:
                logging.exception("Skipping controller %s due to unexpected error", p)

        # Scan for controllers via each IP master controller
        ipmasters = cls._scanIPMasters()
        for ipadd in ipmasters:
            try:
                logging.debug("Scanning controllers on master %s:%d", ipadd[0], ipadd[1])
                sock = cls._openIPSocket(*ipadd)
                controllers = Controller.scan(IPBusAccesser(sock, master=None))
                if controllers:
                    axis_num = 0
                    arg = {}
                    for add, axes in controllers.items():
                        for a, cl in axes.items():
                            arg[axes_names[axis_num]] = (add, a, cl)
                            axis_num += 1
                    found.append(("Actuator IP",
                                 {"port": "%s:%d" % ipadd, "axes": arg}))
            except IOError:
                logging.info("Failed to scan on master %s:%d", ipadd[0], ipadd[1])
            except Exception:
                logging.exception("Skipping controller %s:%d due to unexpected error", ipadd[0], ipadd[1])

        return found

    @classmethod
    def _scanIPMasters(cls):
        """
        Scans the IP network for master controllers
        return (list of tuple of str, int): list of ip add and port of the master
          controllers found.
        """
        logging.info("Ethernet network scanning for PI-GCS controllers in progress...")
        found = set()  # (set of 2-tuple): ip address, ip port

        # Find all the broadcast addresses possible (one or more per network interfaces)
        # In the ideal world, we could just use '<broadcast>', but apprently if
        # there is not gateway to WAN, it will not work.
        bdc = []
        try:
            import netifaces
            for itf in netifaces.interfaces():
                try:
                    for addrinfo in netifaces.ifaddresses(itf)[socket.AF_INET]:
                        bdc.append(addrinfo["broadcast"])
                except KeyError:
                    pass # no INET or no "broadcast"
        except ImportError:
            bdc = ['<broadcast>']

        for bdcaddr in bdc:
            for port, msg, ansstart in ((50000, b"PI", b"PI"), (30718, b"\x00\x00\x00\xf8", b"\x00\x00\x00\xf9")):
                # Special protocol by PI (reversed-engineered):
                # * Broadcast "PI" on a (known) port
                # * Listen for an answer
                # * Answer should contain something like "PI C-863K016 SN 0 -- listening on port 50000 --"
                # It's more or less the same thing for port 30718 (used by E-725)
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.bind(('', 0))
                    logging.debug("Broadcasting on %s:%d", bdcaddr, port)
                    s.sendto(msg, (bdcaddr, port))
                    s.settimeout(1.0)  # It should take less than 1 s to answer

                    while True:
                        data, fulladdr = s.recvfrom(1024)
                        if not data:
                            break
                        if data.startswith(ansstart):
                            # TODO: decode the message to know to which port it's actually listening to
                            # (in practice, it seems to always be 50000)
                            found.add((fulladdr[0], 50000))
                        else:
                            logging.info("Received %s from %s", to_str_escape(data), fulladdr)
                except socket.timeout:
                    pass
                except socket.error:
                    logging.info("Couldn't broadcast on %s:%d", bdcaddr, port)
                except Exception:
                    logging.exception("Failed to broadcast on %s:%d", bdcaddr, port)

        return list(found)

    @staticmethod
    def _openSerialPort(port, baudrate=38400, _addresses=None):
        """
        Opens the given serial port the right way for the PI controllers.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        baudrate (int): baudrate to use, default is the recommended 38400
        _addresses (unused): only for testing (cf FakeBus)
        return (serial): the opened serial port
        """
        try:
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5 # s
            )
        except serial.SerialException:
            raise model.HwError("Failed to open '%s', check the device is "
                                "plugged in and turned on." % port)

        return ser

    @staticmethod
    def _openIPSocket(host, port=50000):
        """
        Opens a socket connection to an PI master controller over IP.
        host (string): the IP address or host name of the master controller
        port (int): the (IP) port number
        return (socket): the opened socket connection
        """
        try:
            sock = socket.create_connection((host, port), timeout=5)
        except socket.timeout:
            raise model.HwError("Failed to connect to '%s:%d', check the master "
                                "controller is connected to the network, turned "
                                " on, and correctly configured." % (host, port))
        except socket.error:
            raise model.HwError("Failed to connect to '%s:%d', check the master "
                                "controller is not already in use." % (host, port))
        sock.settimeout(1.0) # s
        # Immediately send the packet, as small as it is (to avoid latency)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock


class SerialBusAccesser(object):
    """
    Manages connections to the low-level bus
    """
    def __init__(self, ser):
        self.serial = ser
        # to acquire before sending anything on the serial port
        self.ser_access = threading.RLock()
        self.driverInfo = "serial driver: %s" % (driver.getSerialDriver(ser.port),)

    def terminate(self):
        self.serial.close()

    def sendOrderCommand(self, addr, com):
        """
        Send a command which does not expect any report back
        addr (None or 1<=int<=16): address of the controller. If None, no address
        is used (and it's typically controller 1 answering)
        com (string): command to send (including the \n if necessary)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        assert(addr is None or 1 <= addr <= 16 or addr == 254 or addr == 255)
        if addr is None:
            full_com = com
        else:
            full_com = "%d %s" % (addr, com)
        with self.ser_access:
            logging.debug("Sending: '%s'", full_com)
            self.serial.write(full_com.encode('ascii'))
            # We don't flush, as it will be done anyway if an answer is needed

    def sendQueryCommand(self, addr, com):
        """
        Send a command and return its report (raw)
        addr (None or 1<=int<=16): address of the controller
        com (string or list of strings): the command to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix
           (e.g.,"0 1") nor newline.
           If answer is multiline: returns a list of each line
        Note: multiline answers seem to always begin with a \x00 character, but
         it's left as is.
        raise:
           HwError: if error communicating with the hardware, probably due to
              the hardware not being in a good state (or connected)
           IOError: if error during the communication (such as the protocol is
              not respected)
        """
        assert(addr is None or 1 <= addr <= 16 or addr == 254)

        if isinstance(com, basestring):
            com = [com]
            multicom = False
        else:
            multicom = True

        for c in com:
            assert(len(c) <= 100)  # commands can be quite long (with floats)

        if addr is None:
            full_com = "".join(com)
            prefix = b""
        else:
            full_com = "".join("%d %s" % (addr, c) for c in com)
            prefix = b"0 %d " % addr

        with self.ser_access:
            logging.debug("Sending: '%s'", to_str_escape(full_com))
            self.serial.write(full_com.encode('ascii'))

            # ensure everything is received, before expecting an answer
            self.serial.flush()

            ans = b""  # received data not yet processed
            ret = []  # one answer per command
            continuing = False
            while True:
                char = self.serial.read()  # empty if timeout
                if not char:
                    raise model.HwError("Controller %s timed out, check the device is "
                                        "plugged in and turned on." % addr)
                ans += char

                anssplited = ans.split(b"\n")
                # if the answer finishes with \n, last split is empty
                anssplited, ans = anssplited[:-1], anssplited[-1]
                if anssplited:
                    anssplited_str = [to_str_escape(i) for i in anssplited]
                    logging.debug("Received: '%s'", "\n".join(anssplited_str))

                for l in anssplited:
                    if not continuing:
                        lines = []  # one string per answer line
                        # remove the prefix
                        if l.startswith(prefix):
                            l = l[len(prefix):]
                        else:
                            # Maybe the previous line was actually continuing (but the hardware is strange)?
                            if ret and ret[-1] == "":
                                logging.debug("Reconsidering previous line as beginning of multi-line")
                                ret = ret[:-1]
                            else:
                                logging.debug("Failed to decode answer '%s'", to_str_escape(l))
                                raise IOError("Report prefix unexpected after '%s': '%s'." % (full_com, l))

                    if l[-1:] == b" ":  # multi-line
                        continuing = True
                        lines.append(l[:-1].decode("latin1"))  # remove the space indicating multi-line
                    else:
                        # End of the answer for that command
                        continuing = False
                        lines.append(l.decode("latin1"))
                        if len(lines) == 1:
                            ret.append(lines[0])
                        else:
                            ret.append(lines)

                # does it look like we received the end of an answer?
                if not continuing and not ans and len(ret) >= len(com):
                    break

        if len(ret) > len(com):
            logging.warning("Skipping previous answers from hardware %r",
                            ret[:-len(com)])
            ret = ret[-len(com):]
        elif len(ret) < len(com):
            logging.error("Expected %d answers but only got %d", len(com), len(ret))

        if not multicom:
            return ret[0]
        else:
            return ret

    def flushInput(self):
        """
        Ensure there is no more data queued to be read on the bus (=serial port)
        """
        with self.ser_access:
            # Flush buffer + give it some time to recover from whatever
            self.serial.flush()
            self.serial.flushInput()
            while True:
                data = self.serial.read(100)
                if len(data) < 100:
                    break
                logging.debug("Flushing data %s", to_str_escape(data))


class IPBusAccesser(object):
    """
    Manages connections to the low-level bus
    """
    def __init__(self, socket, master=254):
        """
        master (1<=int<=255 or None): address of the master
        """

        self.socket = socket
        # to acquire before sending anything on the socket
        self.ser_access = threading.RLock()

        if master is None:
            self.driverInfo = "TCP/IP connection"
        else:
            # recover the main controller from previous errors (just in case)
            err = self.sendQueryCommand(master, "ERR?\n")

            # Get the master controller version
            version = self.sendQueryCommand(master, "*IDN?\n")
            self.driverInfo = "%s" % (version,)

    def terminate(self):
        self.socket.close()

    def sendOrderCommand(self, addr, com):
        """
        Send a command which does not expect any report back
        addr (None or 1<=int<=16): address of the controller. If None, no address
        is used (and it's typically controller 1 answering)
        com (string): command to send (including the \n if necessary)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        assert(addr is None or 1 <= addr <= 16 or addr == 254 or addr == 255)  # 255 means "broadcast"
        if addr is None:
            full_com = com
        else:
            full_com = "%d %s" % (addr, com)
        with self.ser_access:
            logging.debug("Sending: '%s'", to_str_escape(full_com))
            self.socket.sendall(full_com.encode('ascii'))

    def sendQueryCommand(self, addr, com):
        """
        Send a command and return its report (raw)
        addr (None or 1<=int<=16): address of the controller
        com (str or list of str): the command(s) to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix
           (e.g.,"0 1") nor newline.
           If answer is multiline: returns a list of each line
           If command was a list: one str or list of str per command
        raise:
           HwError: if error communicating with the hardware, probably due to
              the hardware not being in a good state (or connected)
           IOError: if error during the communication (such as the protocol is
              not respected)
        """
        assert(addr is None or 1 <= addr <= 16 or addr == 254)

        if isinstance(com, basestring):
            com = [com]
            multicom = False
        else:
            multicom = True

        for c in com:
            assert(len(c) <= 100)  # commands can be quite long (with floats)

        if addr is None:
            full_com = "".join(com)  # can be a list of str, so don't try to join with b""
            prefix = b""
        else:
            full_com = "".join("%d %s" % (addr, c) for c in com)
            prefix = b"0 %d " % addr
        full_com = full_com.encode('latin1')

        with self.ser_access:
            logging.debug("Sending: '%s'", to_str_escape(full_com))
            self.socket.sendall(full_com)

            # Read the answer
            # The basic is simple. An answer starts with a prefix, and finishes
            # with \n. If it actually finishes with " \n", then it's just a new
            # line and not the end of the answer.
            # However, it gets muddy sometimes with empty answers. For instance,
            # it can answer "0 1 \n", which is an empty answer. But some
            # controllers answer "1 HLP\n" with "0 1 \nBla bla \nBla\n"
            end_time = time.time() + 0.5
            ans = b""  # received data not yet processed
            ret = []  # one answer per command
            continuing = False
            while True:
                try:
                    data = self.socket.recv(4096)
                except socket.timeout:
                    raise model.HwError("Controller %s timed out, check the device is "
                                        "plugged in and turned on." % addr)
                # If the master is already accessed from somewhere else it will just
                # immediately answer an empty message
                if not data:
                    if time.time() > end_time:
                        raise model.HwError("Controller not answering. "
                                            "It might be already connected with another client.")
                    else:
                        logging.debug("Received empty data packet")
                    time.sleep(0.01)
                    continue

                logging.debug("Received: '%s'", to_str_escape(data))
                ans += data

                anssplited = ans.split(b"\n")
                # if the answer finishes with \n, last split is empty
                anssplited, ans = anssplited[:-1], anssplited[-1]

                for l in anssplited:
                    # logging.debug("Processing %s", l)
                    if not continuing:
                        lines = []  # one string per answer line
                        # remove the prefix
                        if l.startswith(prefix):
                            l = l[len(prefix):]
                        else:
                            # Maybe the previous line was actually continuing (but the hardware is strange)?
                            if ret and ret[-1] == "":
                                logging.debug("Reconsidering previous line as beginning of multi-line")
                                ret = ret[:-1]
                            else:
                                # TODO: maybe we got some garbage data from before,
                                # check if there is already data available that fits the
                                # prefix. (=> keep reading but with a short timeout)
                                logging.debug("Failed to decode answer '%s'", to_str_escape(l))
                                raise IOError("Report prefix unexpected after '%s': '%s'." % (full_com, l))

                    if l[-1:] == b" ":  # multi-line
                        continuing = True
                        lines.append(l[:-1].decode('latin1'))  # remove the space indicating multi-line
                    else:
                        # End of the answer for that command
                        continuing = False
                        lines.append(l.decode('latin1'))
                        if len(lines) == 1:
                            ret.append(lines[0])
                        else:
                            ret.append(lines)

                # does it look like we received the end of an answer?
                if not continuing and not ans and len(ret) >= len(com):
                    break

        if len(ret) > len(com):
            logging.warning("Skipping previous answers from hardware %r",
                            ret[:-len(com)])
            ret = ret[-len(com):]
        elif len(ret) < len(com):
            logging.error("Expected %d answers but only got %d", len(com), len(ret))

        if not multicom:
            return ret[0]
        else:
            return ret

    def flushInput(self):
        """
        Ensure there is no more data queued to be read on the bus
        """
        with self.ser_access:
            try:
                end = time.time() + 1
                while True:
                    data = self.socket.recv(4096)
                    logging.debug("Flushing data '%s'", to_str_escape(data))
                    if time.time() > end:
                        logging.warning("Still trying to flush data after 1 s")
                        return
            except socket.timeout:
                pass
            except Exception:
                logging.exception("Failed to flush correctly the socket")


# All the classes below are for the simulation of the hardware

class SimulatedError(Exception):
    """
    Special exception class to simulate error in the controller
    """
    pass


class E861Simulator(object):
    """
    Simulates a GCS controller (+ serial port at 38400). Only used for testing.
    1 axis, open-loop only, very limited behaviour
    Same interface as the serial port
    """
    _idn = b"(c)2013 Delmic Fake Physik Instrumente(PI) Karlsruhe, E-861 Version 7.2.0"
    _csv = b"2.0"

    def __init__(self, port, baudrate=9600, timeout=0, address=1,
                 closedloop=False, *args, **kwargs):
        """
        parameters are the same as a serial port
        address (1<=int<=16): the address of the controller
        closedloop (bool): whether it simulates a closed-loop actuator or not
        """
        self.port = port
        self._address = address
        self._has_encoder = closedloop
        # we don't care about the actual parameters but timeout
        self.timeout = timeout

        self._init_mem()

        self._end_move = 0 # time the last requested move is over

        # only used in closed-loop
        # If move is over:
        #   position == target
        # else:
        #   position = original position
        #   target = requested position
        #   current position = weighted average (according to time)
        self._position = 0.012  # m
        self._target = self._position  # m
        self._start_move = 0

        self._output_buf = b"" # what the commands sends back to the "host computer"
        self._input_buf = b"" # what we receive from the "host computer"

        # special trick to only answer if baudrate is correct
        if baudrate != 38400:
            logging.debug("Baudrate incompatible: %d", baudrate)
            self.write = (lambda s: "")

    def _init_mem(self):
        # internal values to simulate the device
        # Note: the type is used to know how it should be decoded, so it's
        # important to differentiate between float and int.
        # Parameter table: address -> value
        self._parameters = {0x01: 80,  # P
                            0x02: 5,  # I
                            0x03: 130,  # D
                            0x14: 1 if self._has_encoder else 0,  # 0 = no ref switch, 1 = ref switch
                            0x32: 0 if self._has_encoder else 1, # 0 = limit switches, 1 = no limit switches
                            0x3c: "DEFAULT-FAKE", # stage name
                            0x15: 25.0, # TMX (in mm)
                            0x30: 0.0, # TMN (in mm)
                            0x16: 0.0125, # value at ref pos
                            0x49: 10.0, # VEL
                            0x0B: 3.2, # ACC
                            0x0C: 0.9, # DEC
                            0x0A: 50.0, # max vel
                            0x4A: 5.0, # max acc
                            0x4B: 5.0, # max dec
                            0x0E: 10000000, # unit num (note: normal default is 10000)
                            0x0F: 1,       # unit denum
                            0x56: 1,  # encoder on
                            0x7000002: 50,  # slew rate in ms
                            0x7000003: 10.0, # SSA
                            0x7000201: 3.2, # OVL
                            0x7000202: 0.9, # OAC
                            0x7000204: 15.3, # max step/s
                            0x7000205: 1.2, # max step/s²
                            0x7000206: 0.9, # ODC
                            0x7000601: "MM", # unit
                            }
        self._servo = 0 # servo state
        self._ready = True # is ready?
        self._referenced = 0
        self._ref_mode = 1
        self._errno = 0 # last error set

    _re_command = b".*?[\n\x04\x05\x07\x08\x18\x24]"
    def write(self, data):
        self._input_buf += data
        # process each commands separated by a "\n" or is short command
        while len(self._input_buf) > 0:
            m = re.match(self._re_command, self._input_buf)
            if not m:
                return # no more full command available
            c = m.group(0)
            self._processCommand(c)
            self._input_buf = self._input_buf[m.end(0):] # all the left over

    def read(self, size=1):
        # simulate timeout
        end_time = time.time() + self.timeout

        # FIXME: to be correct, we'd need to take a lock
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        while len(ret) < size:
            time.sleep(0.01)
            left = size - len(ret)
            ret += self._output_buf[:left]
            self._output_buf = self._output_buf[len(ret):]
            if self.timeout and time.time() > end_time:
                break

        return ret

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _get_cur_pos_cl(self):
        """
        Computes the current position, in closed loop mode
        """
        now = time.time()
        if now > self._end_move:
            self._position = self._target
            return self._position
        else:
            completion = (now - self._start_move) / (self._end_move - self._start_move)
            cur_pos = self._position + (self._target - self._position) * completion
            return cur_pos

    # TODO: some commands are read-only
    # Command name -> parameter number
    _com_to_param = {b"LIM": 0x32, # LIM actually report the opposite of 0x32
                     b"TRS": 0x14,
                     b"CST": 0x3c,
                     b"TMN": 0x30,
                     b"TMX": 0x15,
                     b"VEL": 0x49,
                     b"ACC": 0x0B,
                     b"DEC": 0x0C,
                     b"OVL": 0x7000201,
                     b"OAC": 0x7000202,
                     b"ODC": 0x7000206,
                     b"SSA": 0x7000003,
    }
    _re_addr_com = br"((?P<addr>\d+) (0 )?)?(?P<com>.*)"
    def _processCommand(self, com):
        """
        process the command, and put the result in the output buffer
        com (str): command
        """
        out = None # None means error while decoding command

        # command can start with a prefix like "5 0 " or "5 "
        m = re.match(self._re_addr_com, com)
        assert m # anything left over should be in com
        if m.group("addr"):
            addr = int(m.group("addr"))
            prefix = b"0 %d " % addr
        else:
            addr = 1 # default is address == 1
            prefix = b""

        if addr != self._address and addr != 255: # message is for us?
#             logging.debug("Controller %d skipping message for %d",
#                           self._address, addr)
            return
        logging.debug("Fake controller %d processing command '%s'",
                      self._address, to_str_escape(com))

        com = m.group("com") # also removes the \n at the end if it's there
        # split into arguments separated by spaces (not including empty strings)
        args = [a for a in com.split(b" ") if bool(a)]
        logging.debug("Command decoded: %s", args)

        if self._errno:
            # if errno is not null, most commands don't work any more
            if com not in [b"*IDN?", b"RBT", b"ERR?", b"CSV?"]:
                logging.debug("received command %s while errno = %d",
                              to_str_escape(com), self._errno)
                return

        # TODO: to support more commands, we should have a table, with name of
        # the command + type of arguments (+ number of optional args)
        try:
            if com == b"*IDN?": # identification
                out = self._idn
            elif com == b"CSV?": # command set version
                out = self._csv
            elif com == b"ERR?": # last error number
                out = b"%d" % self._errno
                self._errno = 0 # reset error number
            elif com == b"RBT": # reboot
                self._init_mem()
                time.sleep(0.1)
            elif com == b"\x04": # Query Status Register Value
                # return hexadecimal bitmap of moving axes
                # TODO: to check, much more info returned
                val = 0
                if time.time() < self._end_move:
                    val |= 0x400  # first axis moving
                if self._servo:
                    val |= 0x1000  # servo on
                out = b"0x%x" % val
            elif com == b"\x05": # Request Motion Status
                # return hexadecimal bitmap of moving axes
                if time.time() > self._end_move:
                    val = 0
                else:
                    val = 1 # first axis moving
                out = b"%x" % val
            elif com == b"\x07": # Request Controller Ready Status
                if self._ready:  # TODO: when is it not ready?? (for a little while after changing servo mode)
                    out = b"\xb1"
                else:
                    out = b"\xb0"
            elif com == b"\x18" or com == b"STP": # Stop immediately
                self._end_move = 0
                self._errno = 10 # PI_CNTR_STOP
            elif args[0].startswith(b"HLT"): # halt motion with deceleration: axis (optional)
                self._end_move = 0
            elif args[0].startswith(b"RNP"):  # relax
                pass
            elif args[0][:3] in self._com_to_param:
                param = self._com_to_param[args[0][:3]]
                logging.debug("Converting command %s to param %d", args[0], param)
                axis = int(args[1])
                if axis != 1:
                    raise SimulatedError(15)
                if args[0][3:4] == b"?" and len(args) == 2: # query
                    out = ("%s=%s" % (args[1], self._parameters[param])).encode('ascii')
                elif len(args[0]) == 3 and len(args) == 3: # set
                    # convert according to the current type of the parameter
                    typeval = type(self._parameters[param])
                    self._parameters[param] = typeval(args[2])
                else:
                    raise SimulatedError(15)
            elif args[0] == b"SPA?" and len(args) == 3: # GetParameter: axis, address
                # TODO: when no arguments -> list all parameters
                axis, addr = int(args[1]), int(args[2])
                if axis != 1:
                    raise SimulatedError(15)
                try:
                    out = ("%s=%s" % (addr, self._parameters[addr])).encode('ascii')
                except KeyError:
                    logging.debug("Unknown parameter %d", addr)
                    raise SimulatedError(56)
            elif args[0] == b"SPA" and len(args) == 4: # SetParameter: axis, address, value
                axis = int(args[1])
                if args[2].startswith(b"0x"):
                    addr = int(args[2][2:], 16)
                else:
                    addr = int(args[2])
                if axis != 1:
                    raise SimulatedError(15)
                if addr in [0x0E, 0x0F] and self._parameters[addr] != int(args[3]):
                    # TODO: have a list of parameters to update
                    raise NotImplementedError("Simulator cannot change unit")
                try:
                    typeval = type(self._parameters[addr])
                    self._parameters[addr] = typeval(args[3])
                except KeyError:
                    logging.debug("Unknown parameter %d", addr)
                    raise SimulatedError(56)
            elif args[0] == b"SEP?" and len(args) == 3:  # GetParameterNonVolatile: axis, address
                # TODO: when no arguments -> list all parameters
                axis, addr = int(args[1]), int(args[2])
                if axis != 1:
                    raise SimulatedError(15)
                try:
                    out = ("%d=%s" % (addr, self._parameters[addr])).encode('ascii')
                except KeyError:
                    logging.debug("Unknown parameter %d", addr)
                    raise SimulatedError(56)
            elif args[0] == b"LIM?" and len(args) == 2: # Get Limit Switches
                axis = int(args[1])
                if axis == 1:
                    # opposite of param 0x32
                    out = b"%s=%d" % (args[1], 1 - self._parameters[0x32])
                else:
                    self._errno = 15
            elif args[0] == b"SVO" and len(args) == 3: # Set Servo State
                axis, state = int(args[1]), int(args[2])
                if axis == 1:
                    self._servo = state
                else:
                    self._errno = 15
            elif args[0] == b"RON" and len(args) == 3: # Set Reference mode
                axis, state = int(args[1]), int(args[2])
                if axis == 1:
                    self._ref_mode = state
                else:
                    self._errno = 15
            elif args[0] == b"OSM" and len(args) == 3: # Open-Loop Step Moving
                axis, steps = int(args[1]), float(args[2])
                if axis != 1:
                    raise SimulatedError(15)
                speed = self._parameters[self._com_to_param[b"OVL"]]
                duration = abs(steps) / speed
                logging.debug("Simulating a move of %f s", duration)
                self._end_move = time.time() + duration # current move stopped
            elif args[0] == b"MOV" and len(args) == 3: # Closed-Loop absolute move
                axis, pos = int(args[1]), float(args[2])
                if axis != 1:
                    raise SimulatedError(15)
                if self._ref_mode and not self._referenced:
                    raise SimulatedError(8)
                speed = self._parameters[self._com_to_param[b"VEL"]]
                cur_pos = self._get_cur_pos_cl()
                distance = cur_pos - pos
                duration = abs(distance) / speed + 0.05
                logging.debug("Simulating a move of %f s", duration)
                self._start_move = time.time()
                self._end_move = self._start_move + duration
                self._position = cur_pos
                self._target = pos
            elif args[0] == b"MVR" and len(args) == 3: # Closed-Loop relative move
                axis, distance = int(args[1]), float(args[2])
                if axis != 1:
                    raise SimulatedError(15)
                if self._ref_mode and not self._referenced:
                    raise SimulatedError(8)
                speed = self._parameters[self._com_to_param[b"VEL"]]
                duration = abs(distance) / speed + 0.05
                logging.debug("Simulating a move of %f s", duration)
                cur_pos = self._get_cur_pos_cl()
                self._start_move = time.time()
                self._end_move = self._start_move + duration
                self._position = cur_pos
                self._target = cur_pos + distance

#                 # Introduce an error from time to time, just to try the error path
#                 if random.randint(0, 10) == 0:
#                     raise SimulatedError(7)
            elif args[0] == b"POS" and len(args) == 3: # Closed-Loop position set
                axis, pos = int(args[1]), float(args[2])
                if axis != 1:
                    raise SimulatedError(15)
                self._position = pos
            elif args[0] == b"POS?" and len(args) == 2: # Closed-Loop position query
                axis = int(args[1])
                if axis != 1:
                    raise SimulatedError(15)
#                 if 0 == random.randint(0, 20):  # To test with issue about generated garbage
#                     self._output_buf += "\n\x8a\xea\x82r\x82\xa2\x9a\xa2\xca\x8a\n"
#                 else:
                out = b"%s=%f" % (args[1], self._get_cur_pos_cl())
            elif args[0] == b"MOV?" and len(args) == 2:  # Closed-Loop target position query
                axis = int(args[1])
                if axis != 1:
                    raise SimulatedError(15)
                out = b"%s=%f" % (args[1], self._target)
            elif args[0] == b"SVO?" and len(args) == 2:  # Servo on?
                axis = int(args[1])
                if axis != 1:
                    raise SimulatedError(15)
                out = b"%s=%s" % (args[1], b"1" if self._servo else b"0")
            elif args[0] == b"ONT?" and len(args) == 2: # on target
                axis = int(args[1])
                if axis != 1:
                    raise SimulatedError(15)
                ont = time.time() > self._end_move
                out = b"%s=%d" % (args[1], 1 if ont else 0)
            elif args[0] == b"FRF?" and len(args) == 2: # is referenced?
                axis = int(args[1])
                if axis != 1:
                    raise SimulatedError(15)
                out = b"%s=%d" % (args[1], self._referenced)
            elif args[0] == b"FRF" and len(args) == 2: # reference to ref switch
                axis = int(args[1])
                if axis != 1:
                    raise SimulatedError(15)

                # simulate moving to reference position
                ref_pos = self._parameters[0x16] * 1e3  # value at reference in m
                speed = self._parameters[self._com_to_param[b"VEL"]]
                cur_pos = self._get_cur_pos_cl()
                distance = cur_pos - ref_pos
                duration = abs(distance) / speed + 0.05
                logging.debug("Simulating a referencing move of %f s", duration)
                self._start_move = time.time()
                self._end_move = self._start_move + duration
                self._position = cur_pos
                self._target = ref_pos
                self._referenced = 1
            elif args[0] == b"SAI?" and len(args) <= 2: # List Of Current Axis Identifiers
                # Can be followed by "ALL", but for us, it's the same
                out = b"1"
            elif com == b"HLP?":
                # The important part is " \n" at the end of each line
                out = ("\x00The following commands are available: \n"
                       "#4 request status register \n"
                       "HLP list the available commands \n"
                       "LIM?  booo \n"
                       "TRS?  booo \n"
                       "SVO  booo \n"
                       "RON  booo \n"
                       "HTL  booo \n"
                       "STP  booo \n"
                       "CST?  booo \n"
                       "VEL?  booo \n"
                       "ACC?  booo \n"
                       "OVL?  booo \n"
                       "OAC?  booo \n"
                       "TMN?  booo \n"
                       "TMX?  booo \n"
                       "FRF  booo \n"
                       "FRF?  booo \n"
                       "SAI?  booo \n"
                       "POS  booo \n"
                       "POS?  booo \n"
                       "ONT?  booo \n"
                       "MOV  booo \n"
                       "MOV?  booo \n"
                       "MVR  booo \n"
                       "OSM  booo \n"
                       "RNP  relax \n"
                       "ERR? get error number \n"
                       "VEL {<AxisId> <Velocity>} set closed-loop velocity \n"
                       "end of help"
                       ).encode('ascii')
            elif com == b"HPA?":
                out = ("\x00The following parameters are valid: \n" +
                       "0x1=\t0\t1\tINT\tmotorcontroller\tP term 1 \n" +
                       "0x2=\t0\t1\tINT\tmotorcontroller\tI term 1 \n" +
                       "0x3=\t0\t1\tINT\tmotorcontroller\tD term 1 \n" +
                       "0x32=\t0\t1\tINT\tmotorcontroller\thas limit\t(0=limitswitchs 1=no limitswitchs) \n" +
                       "0x3C=\t0\t1\tCHAR\tmotorcontroller\tStagename \n" +
                       # "0x56=\t0\t1\tCHAR\tencoder\tactive \n" + # Uncomment to simulate a C-867
                       "0x7000000=\t0\t1\tFLOAT\tmotorcontroller\ttravel range minimum \n" +
                       "0x7000002=\t0\t1\tFLOAT\tmotorcontroller\tslew rate \n" +
                       "0x7000601=\t0\t1\tCHAR\tunit\tuser unit \n" +
                       "end of help"
                       ).encode('ascii')
            else:
                logging.debug("Unknown command '%s'", to_str_escape(com))
                self._errno = 1
        except SimulatedError as ex:
            logging.debug("Error detected while processing command '%s'", to_str_escape(com))
            self._errno = ex.args[0]
        except Exception as ex:
            logging.debug("Failed to process command '%s' with exception %s", to_str_escape(com), ex)
            self._errno = 1

        # add the response header
        if out is None:
            #logging.debug("Fake controller %d doesn't respond", self._address)
            pass
        else:
            out = b"%s%s\n" % (prefix, out)
            logging.debug("Fake controller %d responding '%s'", self._address,
                          to_str_escape(out))
            self._output_buf += out

class DaisyChainSimulator(object):
    """
    Simulated serial port that can simulate daisy chain on the controllers
    Same interface as the serial port + list of (fake) serial ports to connect
    """
    def __init__(self, port, timeout=0, *args, **kwargs):
        """
        subports (list of open ports): the ports to receive the data
        """
        self.port = port
        self.timeout = timeout
        self._subports = kwargs["subports"]
        self._output_buf = b""
        self._obuf_lock = threading.Lock()

        # For each port, put a thread listening on the read and push to output
        self._is_terminated = False
        for p in self._subports:
            t = threading.Thread(target=self._thread_read_serial, args=(p,))
            t.daemon = True
            t.start()

    def flush(self):
        return

    def flushInput(self):
        return

    def write(self, data):
        # just duplicate
        for p in self._subports:
            p.write(data)

    def read(self, size=1):
        # simulate timeout
        end_time = time.time() + self.timeout

        with self._obuf_lock:
            ret = self._output_buf[:size]
            self._output_buf = self._output_buf[len(ret):]

        while len(ret) < size:
            time.sleep(0.01)
            left = size - len(ret)
            with self._obuf_lock:
                ret += self._output_buf[:left]
                self._output_buf = self._output_buf[len(ret):]
            if self.timeout and time.time() > end_time:
                break

        return ret

    def _thread_read_serial(self, ser):
        """
        Push the output of the given serial port into our output
        """
        try:
            while not self._is_terminated:
                c = ser.read(1)
                if len(c) == 0:
                    time.sleep(0.01)
                else:
                    with self._obuf_lock:
                        self._output_buf += c
        except Exception:
            logging.exception("Fake daisy chain thread received an exception")

    def close(self):
        self._is_terminated = True
        # using read or write will fail after that
        del self._output_buf
        del self._subports

class FakeBus(Bus):
    """
    Same as the normal Bus, but connects to simulated controllers
    """
    def __init__(self, name, role, port, axes, baudrate=38400, **kwargs):
        # compute the addresses from the axes declared
        addresses = dict([(d[0], d[2]) for d in axes.values()])
        Bus.__init__(self, name, role, port, axes, baudrate=baudrate,
                     _addresses=addresses, **kwargs)

    @classmethod
    def scan(cls, port=None):
        # force only one port
        return super(FakeBus, cls).scan(port="/dev/fake")

    @staticmethod
    def _openSerialPort(port, baudrate=38400, _addresses=None):
        """
        Opens a fake serial port
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        _addresses (dict of int -> bool, or None): list of each address that should have
         a simulated controller created and whether it is closed-loop or not.
         Default to {1: False, 2:False} (used for scan).
        return (serial): the opened serial port
        """
        _addresses = _addresses or {1: False, 2: False}
        simulators = []
        for addr, cl in _addresses.items():
            sim = E861Simulator(
                    port=port,
                    baudrate=baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.5, #s
                    address=addr,
                    closedloop=cl,
                   )
            simulators.append(sim)

        # link all of them in daisy chain
        ser = DaisyChainSimulator(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5, #s
                subports=simulators,
              )

        return ser

    @classmethod
    def _scanIPMasters(cls):
        return []  # Nothing

