# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from concurrent import futures
from odemis import model
from odemis.model import isasync
import collections
import glob
import logging
import odemis
import os
import re
import serial
import sys
import threading
import time

"""
Driver to handle PI's piezo motor controllers that follow the 'GCS' (General
Command Set). In particular it handle the PI E-861 controller. Information can
be found the manual E-861_User_PZ205E121.pdf (p.107). See PIRedStone for the PI C-170.

In a daisy-chain, connected via USB or via RS-232, there must be one
controller with address 1 (=DIP 1111).

The controller support closed-loop mode (i.e., absolute positioning) but only
if it is associated to a sensor (not software detectable). It can also work in 
open-loop mode but to avoid damaging the hardware (which is moved by this
actuator): 
* Do not switch servo on (SVO command)
* Do not send commands for closed-loop motion, like MOV or MVR
* Do not send the open-loop commands OMA and OMR, since they
   use a sensor, too

The controller accepts several baud rates. We choose 38400 (DIP=01) as it's fast
and it seems accepted by every version. Other settings are 8 data, 1 stop, 
no parity.

The controller can save in memory the configuration for a specific stage.
The configuration database is available in a file called pistages2.dat. The
PIMikroMove Windows program allows to load it, but by default doesn't copy it to
the non-volatile memory, so you need to also force the record. (Can also be done
with the WPA command and password "100".)

In open-loop, the controller has 2 ways to move the actuators:
 * Nanostepping: high-speed, and long distance
      1 step ~ 10 μm without load (less with load)
 * Analog: very precise, but moves maximum ~5μm
     "40 volts corresponds to a motion of approx. 3.3μm"
     "20 volts corresponds to a motion of approx. 1μm"

As an exception, the C-867 only supports officially closed-loop. However, there
is a "testing" command, SMO, that allows to move in open-loop by simulating the input
to the PID controller. PI assured us that as long as the stage used can reach 
the limit without getting damaged, it is safe. It's pretty straightforward to
use the command. The voltage defines the speed (and direction) of the move. The
voltage should be set to 0 again when the position desired is reached. 3V is 
approximately the minimum to move, and 10V is the maximum. Voltage is more or 
less linear between -32766 and 32766 -> -10 and 10V. So the distance moved 
depends on the time the SMO is set, which is obviously very unprecise.

In closed-loop, it's all automagical.

The recommended maximum step frequency is 800 Hz.

The architecture of the driver relies on three main classes:
 * Controller: represent one controller with one or several axes (E-861 has only one)
 * Bus: represent the whole group of controllers daisy-chained from the same
    serial port. It's also the Actuator interface for the rest of Odemis.
 * ActionManager: handles all the actions (move/stop) sent to the controller so
    that the asynchronous ones are ordered. 
    
In the typical usage, Odemis ask to moveRel() an axis to the Bus. The Bus converts
it into an action, returns a Future and queue the action on the ActionManager.
When the Controller is free, the ActionManager pick the next action and convert
it into a command for the Controller, which sends it to the actual PI controller
and waits for it to finish.  

"""
# constants for model number
MODEL_C867 = 867
MODEL_E861 = 861
MODEL_UNKNOWN = 0

class Controller(object):
    def __init__(self, ser, address=None, axes=None,
                 dist_to_steps=None, min_dist=None, vpms=None):
        """
        ser: a serial port (opened)
        address 1<int<16: address as configured on the controller
        If not address is given, it just allows to do some raw commands
        axes (dict int -> boolean): determine which axis will be used and whether
          it will be used closed-loop (True) or open-loop (False).
        Next 2 parameters are calibration values for E-861
        dist_to_steps (0 < float): allows to calibrate how many steps correspond
          to a given distance (in step/m). Default is 1e5, a value that could 
          make sense.
        min_dist (0 <= float < 1): minimum distance required for the axis to 
          even move (in m). Below this distance, a command will be sent, but it
          is expected that the actuator doesn't move at all. Default is 0.01 
          step (= 0.01 / dist_to_steps).
        Next parameter is calibration value for C-867
        vpms (0 < float): calibration value voltage -> speed, in V/(m/s), 
          default is a not too bad value of 87 V/(m/s). Note: it's not linear
          at all actually, but we tend to try to always go at lowest speed (near 3V)
        """
        # TODO: calibration values should be per axis (but for now we only have controllers with 1 axis)

        self.serial = ser
        self.address = address
        self._try_recover = False # for now, fully raw access
        # did the user asked for a raw access only?
        if address is None:
            return
        if axes is None:
            raise LookupError("Need to have at least one axis configured")

        if dist_to_steps and not (0 < dist_to_steps):
            raise ValueError("dist_to_steps (%s) must be > 0", dist_to_steps)
        if min_dist and not (0 <= min_dist < 1):
            raise ValueError("min_dist (%s) must be between 0 and 1 m", min_dist)
        if vpms and not (0 < vpms):
            raise ValueError("vpms (%s) must be > 0", vpms)

        # reinitialise: make sure it's back to normal and ensure it's responding
        try:
            # FIXME: there seems to be problems to recover sometimes from a
            # disturbed controller. Not sure what is required to do. Seems to
            # be just the right commands with the right timing....
            # In this state, the error led directly turns on when the usb cable
            # is connected in.
            # maybe self.GetErrorNum() first?
            self.Reboot()
            self.GetErrorNum()
        except IOError:
            raise IOError("No answer from controller %d" % address)

        self._model = self.getModel()
        if self._model == MODEL_UNKNOWN:
            logging.warning("Controller %d is an unsupported version (%s)", self.address, self.GetIdentification())

        self._channels = self.GetAxes() # available channels (=axes)
        # dict axis -> boolean
        self._hasLimit = dict([(a, self.hasLimitSwitches(a)) for a in self._channels])
        # dict axis -> boolean
        self._hasSensor = dict([(a, self.hasSensor(a)) for a in self._channels])
        # dict axis (string) -> servo activated (boolean): updated by SetServo
        self._hasServo = dict(axes)
        self._position = {} # m (dict axis-> position), only used in open-loop

        self.min_speed = 10e-6 # m/s (default low value)

        # If the controller is misconfigured for the actuator, things can go quite
        # wrong, so make it clear
        logging.info("Controller %d is configured for actuator %s", address, self.GetStageName())
        for c in self._channels:
            logging.info("Axis %s has %slimit switch and has %sreference switch",
                         c,
                         "" if self._hasLimit[c] else "no ",
                         "" if self._hasSensor[c] else "no ")
        self._avail_params = self.GetAvailableParameters()

        for a, cl in axes.items():
            if not a in self._channels:
                raise LookupError("Axis %d is not supported by controller %d" % (a, address))

            if cl: # want closed-loop?
                if not self._hasSensor[a]:
                    raise LookupError("Axis %d of controller %d does not support closed-loop mode" % (a, address))
                self.SetServo(a, True)
                # for now we don't handle closed-loop anyway...
                raise NotImplementedError("Closed-loop support not yet implemented")
            else:
                # that should be the default, but for safety we force it
                self.SetServo(a, False)
                if self._model == MODEL_C867: # only has testing command SMO
                    logging.warning("This controller model only supports imprecise open-loop mode.")
                    self._initOLViaPID(vpms=vpms)
                else:
                    self.SetStepAmplitude(a, 55) # maximum is best
                self._position[a] = 0

        self._try_recover = True # full feature only after init

        # For open-loop
        # TODO: allow to pass a polynomial
        self._dist_to_steps = dist_to_steps or 1e5 # step/m
        if min_dist is None:
            self.min_stepsize = 0.01 # step, under this, no move at all
        else:
            self.min_stepsize = min_dist * self._dist_to_steps

        # actually set just before a move
        # The max using closed-loop info seem purely arbitrary

#        # FIXME: how to use the closed-loop version?
#        max_vel = float(self.GetParameter(1, 0xA)) # in unit/s
#        num_unit = float(self.GetParameter(1, 0xE))
#        den_unit = float(self.GetParameter(1, 0xF))
#        max_accel = float(self.GetParameter(1, 0x4A)) # in unit/s²
#        # seems like unit = num_unit/den_unit mm
#        # and it should be initialised using PIStages.dat
#        self.max_speed = max_vel * 1e3 * (num_unit/den_unit)
#        self.max_accel = max_accel * 1e3 * (num_unit/den_unit)

        # FIXME 0x7000204 seems specific to E-861. need different initialisation per controller
        # Even the old firmware don't seem to support it
        self.max_speed = 0.5 # m/s
        self.max_accel = 0.01 # m/s²
        try:
            if 0x7000204 in self._avail_params:
                # (max m/s) = (max step/s) * (step/m)
                self.max_speed = float(self.GetParameter(1, 0x7000204)) / self._dist_to_steps # m/s
                # Note: the E-861 claims max 0.015 m/s but actually never goes above 0.004 m/s
            if 0x7000205 in self._avail_params:
                # (max m/s²) = (max step/s²) * (step/m)
                self.max_accel = float(self.GetParameter(1, 0x7000205)) / self._dist_to_steps # m/s²
        except (IOError, ValueError) as err:
            # TODO detect better that it's just a problem of sending unsupported command/value
            # Put default (large values)
            self.GetErrorNum() # reset error
            logging.debug("Using default speed and acceleration value after error '%s'", err)

        self._speed = dict([(a, (self.min_speed + self.max_speed) / 2) for a in axes]) # m/s
        self._accel = dict([(a, self.max_accel) for a in axes]) # m/s² (both acceleration and deceleration)
        self._prev_speed_accel = (dict(), dict())


    def _initOLViaPID(self, vpms=None):
        """
        Initialise the controller to move using the SMO command.
        vpms (0< float): calibration value voltage -> speed, in V/(m/s), 
          default is a not too bad value of 78 V/(m/s) (measured from observing
          a speed of 0.023 m/s at 1.8V). Note: it's not linear at all actually,
          but we tend to try to always go at lowest speed (3V). At 6 V (fastest),
          it goes at ~0.3 m/s.
        """
        vpms = vpms or 78 # V/(m/s)

        # Get maximum motor output parameter (0x9) allowed
        # Because some type of stages cannot bear as much as the full maximum
        # The maximum output voltage is calculated following this formula:
        # 200 Vpp*Maximum motor output/32767
        self._max_motor_out = int(self.GetParameter(1, 0x9))
        # official approx. min is 3V, but from test, it can go down to 1.5V,
        # so use 3V
        self._min_motor_out = int((3 / 10) * 32767) # encoded as a ratio of 10 V * 32767
        assert(self._max_motor_out > self._min_motor_out)

        # We simplify to a linear conversion, making sure that the min voltage
        # is approximately the min speed. It will tend to overshoot if the speed
        # is higher than the min speed and there is no load on the actuator.
        # So it's recommended to use it always at the min speed (~0.03 m/s),
        # which also gives the best precision.
        self._vpms = vpms * (32767 / 10)

        self.min_speed = self._min_motor_out / self._vpms # m/s

        # Set up a macro that will do the job
        # To be called like "MAC START OLSTEP 16000 500"
        # First param is voltage between -32766 and 32766
        # Second param is delay in ms between 1 and 9999
        # Note: it seems it doesn't work to have a third param is the axis
        mac = "MAC BEG OLSTEP\n" \
              "%(n)d SMO 1 $1\n" \
              "%(n)d DEL $2\n"   \
              "%(n)d SMO 1 0\n"  \
              "%(n)d MAC END\n" % {"n": self.address}
        self._sendOrderCommand(mac)

        # TODO: try a macro like this for short moves:
        mac = "MAC BEG OLSTEP0\n" \
              "%(n)d SMO 1 $1\n" \
              "%(n)d SAI? ALL\n" \
              "%(n)d SMO 1 0\n"  \
              "%(n)d MAC END\n" % {"n": self.address}
        self._sendOrderCommand(mac)

        # change the moveRel and isMoving methods to PID-aware versions
        self._moveRelOL = self._moveRelOLViaPID
        self.isMoving = self._isMovingViaPID
        self.stopMotion = self._stopMotionViaPID

    def _sendOrderCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \n if necessary)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: '%s'", full_com.encode('string_escape'))
        self.serial.write(full_com)

    def _sendQueryCommandRaw(self, com):
        """
        Send a command and return its report (raw)
        com (string): the command to send (without address prefix but with \n)
        return (list of strings): the complete report with each line separated and without \n 
        """
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: '%s'", full_com.encode('string_escape'))
        self.serial.write(full_com)

        char = self.serial.read() # empty if timeout
        line = ""
        lines = []
        while char:
            if char == "\n":
                if (len(line) > 0 and line[-1] == " " and  # multiline: "... \n"
                    not re.match(r"0 \d+ $", line)):  # excepted empty line "0 1 \n"
                    lines.append(line[:-1]) # don't include the space
                    line = ""
                else:
                    # full end
                    lines.append(line)
                    break
            else:
                # normal char
                line += char
            char = self.serial.read()

        if not char:
            raise IOError("Controller %d timeout." % self.address)

        return lines


    def _sendQueryCommand(self, com):
        """
        Send a command and return its report
        com (string): the command to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix 
           (e.g.,"0 1") nor newline. If answer is multiline: returns a list of each line 
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        try:
            lines = self._sendQueryCommandRaw(com)
        except IOError as ex:
            if not self._try_recover:
                raise

            success = self.recoverTimeout()
            if success:
                logging.warning("Controller %d timeout after '%s', but recovered.",
                                self.address, com.encode('string_escape'))
                # try one more time
                lines = self._sendQueryCommandRaw(com)
            else:
                raise IOError("Controller %d timeout after '%s', not recovered." %
                              (self.address, com.encode('string_escape')))

        assert len(lines) > 0

        logging.debug("Received: '%s'", "\n".join(lines).encode('string_escape'))
        prefix = "0 %d " % self.address
        if not lines[0].startswith(prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (com, lines[0]))
        lines[0] = lines[0][len(prefix):]

        if len(lines) == 1:
            return lines[0]
        else:
            return lines

    err_ans_re = ".* \\d+\n$" # ex: "0 1 54\n"
    def recoverTimeout(self):
        """
        Try to recover from error in the controller state
        return (boolean): True if it recovered
        """
        # Flush buffer + give it some time to recover from whatever
        while self.serial.read():
            pass

        # It appears to make the controller more comfortable...
        self._sendOrderCommand("ERR?\n")
        char = self.serial.read()
        resp = ""
        while char:
            resp += char
            if re.match(self.err_ans_re, resp): # looks like an answer to err?
                # TODO Check if error == 307 or 308?
                return True
            char = self.serial.read()

        # We timed out again, try harder: reboot
        self.Reboot()
        self._sendOrderCommand("ERR?\n")
        char = self.serial.read()
        resp = ""
        while char:
            resp += char
            if re.match(self.err_ans_re, resp): # looks like an answer to err?
                #TODO reset all the values (SetServo...)
                self._prev_speed_accel = (dict(), dict())
                return True
            char = self.serial.read()

        # that's getting pretty hopeless
        return False


    # The following are function directly mapping to the controller commands.
    # In general it should not be need to use them directly from outside this class
    def GetIdentification(self):
        #*IDN? (Get Device Identification):
        #ex: 0 2 (c)2010 Physik Instrumente(PI) Karlsruhe,E-861 Version 7.2.0
        version = self._sendQueryCommand("*IDN?\n")
        return version

    def GetSyntaxVersion(self):
        #CSV? (Get Current Syntax Version)
        #GCS version, can be 1.0 (for GCS 1.0) or 2.0 (for GCS 2.0)
        return self._sendQueryCommand("CSV?\n")

    def GetStageName(self):
        """
        return (str) the name of the stage for which the controller is configured.
        Note that the actual stage might be different.
        """
        #parameter 0x3c
        return self.GetParameter(1, 0x3C)

    def GetAxes(self):
        """
        returns (set of int): all the available axes
        """
        #SAI? (Get List Of Current Axis Identifiers)
        #SAI? ALL: list all axes (included disabled ones)
        answer = self._sendQueryCommand("SAI? ALL\n")
        # TODO check it works with multiple axes
        # FIXME: on the C867 the name of the axis can be a string of up to 8 char (see TVI)
        axes = set([int(a) for a in answer.split(" ")])
        return axes

    def GetAvailableCommands(self):
        #HLP? (Get List Of Available Commands)
        # first line starts with \x00
        lines = self._sendQueryCommand("HLP?\n")
        lines[0].lstrip("\x00")
        return lines

    def GetAvailableParameters(self):
        """
        Returns the available parameters
        return (dict param -> list of strings): parameter number and strings 
         used to describe it (typically: 0, 1, FLOAT, description)
        """
        #HPA? (Get List Of Available Parameters)
        lines = self._sendQueryCommand("HPA?\n")
        # first line doesn't seem to starts with \x00
#        lines[0].lstrip("\x00")
        params = {}
        # first and last lines are typically just user-friendly text
        # look for something like '0x412=\t0\t1\tINT\tmotorcontroller\tI term 1'
        for l in lines:
            m = re.match("0x(?P<param>[0-9A-Fa-f]+)=(?P<desc>(\t\S+)+)", l)
            if not m:
                logging.debug("Line doesn't seem to be a parameter: '%s'", l)
                continue
            param, desc = int(m.group("param"), 16), m.group("desc")
            params[param] = tuple(filter(bool, desc.split("\t")))
        return params

    def GetParameter(self, axis, param):
        """
        axis (1<int<16): axis number
        param (0<int): parameter id (cf p.35)
        returns (string): the string representing this parameter 
        """
        # SPA? (Get Volatile Memory Parameters)
        assert((1 <= axis) and (axis <= 16))
        assert(0 <= param)

        answer = self._sendQueryCommand("SPA? %d %d\n" % (axis, param))
        try:
            value = answer.split("=")[1]
        except IndexError:
            # no "=" => means the parameter is unknown
            raise ValueError("Parameter %d %d unknown" % (axis, param))
        return value

    def GetRecoderConfig(self):
        """
        you don't need this
        """
        #DRC? (get Data Recorder Configuration)
        return self._sendQueryCommand("DRC?\n")

    def hasLimitSwitches(self, axis):
        """
        Report whether the given axis has limit switches (is able to detect 
         the ends of the axis).
        Note: It's just read from a configuration value in flash 
        memory. Can be configured easily with PIMikroMove (paremeter 
        axis (1<int<16): axis number
        """
        #LIM? (Indicate Limit Switches)
        assert((1 <= axis) and (axis <= 16))

        answer = self._sendQueryCommand("LIM? %d\n" % axis)
        # 1 => True, 0 => False
        return answer == "1"

    def hasSensor(self, axis):
        """
        Report whether the given axis has a sensor (is able to measure the 
         distance travelled). 
        Note: apparently it's just read from a configuration value in flash 
        memory. Can be configured easily with PIMikroMove
        axis (1<int<16): axis number
        """
        # TRS? (Indicate Reference Switch)
        assert((1 <= axis) and (axis <= 16))

        answer = self._sendQueryCommand("TRS? %d\n" % axis)
        # 1 => True, 0 => False
        return answer == "1"


    def GetMotionStatus(self):
        """
        returns (set of int): the set of moving axes
        Note: it seems the controller doesn't report moves when using OL via PID
        """
        # "\x05" (Request Motion Status)
        # hexadecimal number bitmap of which axis is moving => 0 if everything is stopped
        # Ex: 4 => 3rd axis moving
        answer = self._sendQueryCommand("\x05")
        bitmap = int(answer, 16)
        # convert to a set
        i = 1
        mv_axes = set()
        while bitmap > 0:
            if bitmap & 1:
                mv_axes.add(i)
            i += 1
            bitmap >>= 1
        return mv_axes

    def isAxisMovingOLViaPID(self, axis):
        """
        axis (1<int<16): axis number
        returns (boolean): True moving axes for the axes controlled via PID
        """
        # "SMO?" (Get Control Value)
        # Reports the speed set. If it's 0, it's not moving, otherwise, it is.
        assert(not self._hasServo[axis])
        answer = self._sendQueryCommand("SMO? %d\n" % axis)
        value = answer.split("=")[1]
        if value == "0":
            return False
        else:
            return True

    def StopOLViaPID(self, axis):
        """
        Stop the fake PID driving when doing open-loop
        """
        self._sendOrderCommand("SMO %d 0\n" % axis)

    def GetStatus(self):
        #SRG? = "\x04" (Query Status Register Value)
        #SRG? 1 1
        #Check status
        # hexadecimal number bitmap of which axis is moving => 0 if everything is stopped
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
        ans = self._sendQueryCommand("\x07")
        if ans == "\xb1":
            return True
        elif ans == "\xb2":
            return False

        logging.warning("Controller %d replied unknown ready status '%s'", self.address, ans)

    def GetErrorNum(self):
        """
        return (int): the error number (can be negative) of last error
        See p.192 of manual for the error codes
        """
        #ERR? (Get Error Number): get error code of last error
        answer = self._sendQueryCommand("ERR?\n")
        error = int(answer, 10)
        return error

    def Reboot(self):
        self._sendOrderCommand("RBT\n")
        end_time = time.time() + 1 # give it some time to reboot before it's accessible again

        # empty the serial buffer
        while self.serial.read():
            pass

        time.sleep(max(0, end_time - time.time()))

    # TODO: use it when terminating?
    def RelaxPiezos(self, axis):
        """
        Call relaxing procedure. Reduce voltage, to increase lifetime and needed
          to change between modes
        axis (1<int<16): axis number
        """
        #RNP (Relax PiezoWalk Piezos): reduce voltage when stopped to increase lifetime
        #Also needed to change between nanostepping and analog
        assert(axis in self._channels)
        self._sendOrderCommand("RNP %d 0\n" % axis)

    def Halt(self, axis=None):
        """
        Stop motion with deceleration
        Note: see Stop
        axis (1<int<16): axis number, 
        """
        #HLT (Stop All Axes): immediate stop (high deceleration != HLT)
        # set error code to 10
        if axis is None:
            self._sendOrderCommand("HLT\n")
        else:
            assert(axis in self._channels)
            self._sendOrderCommand("HLT %d\n" % axis)
#        time.sleep(1) # give it some time to stop before it's accessible again

        # need to recover from the "error", otherwise nothing works
        error = self.GetErrorNum()
        if error != 10: #PI_CNTR_STOP
            logging.warning("Stopped controller %d, but error code is %d instead of 10", self.address, error)

    def Stop(self):
        """
        Stop immediately motion on all axes
        """
        #STP = "\x18" (Stop All Axes): immediate stop (high deceleration != HLT)
        # set error code to 10
        self._sendOrderCommand("\x18")

        # need to recover from the "error", otherwise nothing works
        error = self.GetErrorNum()
        if error != 10: #PI_CNTR_STOP
            logging.warning("Stopped controller %d, but error code is %d instead of 10", self.address, error)

    def SetServo(self, axis, activated):
        """
        Activate or de-activate the servo. 
        Note: only activate it if there is a sensor (cf .hasSensor and ._hasSensor)
        axis (1<int<16): axis number
        activated (boolean): True if the servo should be activated (closed-loop)
        """
        #SVO (Set Servo State)
        assert(axis in self._channels)

        if activated:
            assert(self._hasSensor[axis])
            state = 1
        else:
            state = 0
        self._sendOrderCommand("SVO %d %d\n" % (axis, state))
        self._hasServo[axis] = activated

    # Functions for relative move in open-loop (no sensor)
    def OLMoveStep(self, axis, steps):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        steps (float): number of steps to do (can be a float). If negative, goes
          the opposite direction. 1 step is about 10µm.
        """
        #OSM (Open-Loop Step Moving): move using nanostepping
        assert(axis in self._channels)
        if steps == 0:
            return
        self._sendOrderCommand("OSM %d %.5g\n" % (axis, steps))

    def SetStepAmplitude(self, axis, amplitude):
        """
        Set the amplitude of one step (in nanostep mode). It affects the velocity
        of OLMoveStep.
        Note: probably it's best to set it to 55 and use OVL to change speed.
        axis (1<int<16): axis number
        amplitude (0<=float<=55): voltage applied (the more the further)
        """
        #SSA (Set Step Amplitude) : for nanostepping
        assert(axis in self._channels)
        assert((0 <= amplitude) and (amplitude <= 55))
        self._sendOrderCommand("SSA %d %.5g\n" % (axis, amplitude))

    def GetStepAmplitude(self, axis):
        """
        Get the amplitude of one step (in nanostep mode).
        Note: mostly just for self-test
        axis (1<int<16): axis number
        returns (0<=float<=55): voltage applied
        """
        #SSA? (Get Step Amplitude), returns something like:
        # 1=10.0000
        assert(axis in self._channels)
        answer = self._sendQueryCommand("SSA? %d\n" % axis)
        amp = float(answer.split("=")[1])
        return amp

    def OLAnalogDriving(self, axis, amplitude):
        """
        Use analog mode to move the axis by a given amplitude.
        axis (1<int<16): axis number
        amplitude (-55<=float<=55): Amplitude of the move. It's only a small move.
          55 is approximately 5 um.
        """
        #OAD (Open-Loop Analog Driving): move using analog
        assert(axis in self._channels)
        assert((-55 <= amplitude) and (amplitude <= 55))
        self._sendOrderCommand("OAD %d %.5g\n" % (axis, amplitude))

    def SetOLVelocity(self, axis, velocity):
        """
        Set velocity for open-loop nanostepping motion.
        axis (1<int<16): axis number
        velocity (0<float): velocity in step-cycles/s. Default is 200 (~ 0.002 m/s)
        """
        #OVL (Set Open-Loop Velocity)
        assert(axis in self._channels)
        assert(velocity > 0)
        self._sendOrderCommand("OVL %d %.5g\n" % (axis, velocity))

    def SetOLAcceleration(self, axis, value):
        """
        Set open-loop acceleration of given axes.
        axis (1<int<16): axis number
        value (0<float): acceleration in step-cycles/s. Default is 2000 
        """
        #OAC (Set Open-Loop Acceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("OAC %d %.5g\n" % (axis, value))

    def SetOLDeceleration(self, axis, value):
        """
        Set the open-loop deceleration.
        axis (1<int<16): axis number
        value (0<float): deceleration in step-cycles/s. Default is 2000 
        """
        #ODC (Set Open-Loop Deceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("ODC %d %.5g\n" % (axis, value))


    def OLMovePID(self, axis, voltage, time):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        voltage (-32766<=int<=32766): voltage for the PID control. <0 to go towards
          the negative direction. 32766 is 10V
        time (0<int <= 9999): time in ms.
        """
        # Uses MAC OLSTEP, based on SMO
        assert(axis == 1) # seems not possible to have 3 parameters?!
        assert(-32766 <= voltage and voltage <= 32766)
        assert(0 < time <= 9999)

        # From experiment: a delay of 0 means actually 2**16, and >= 10000 it's 0
        self._sendOrderCommand("MAC START OLSTEP %d %d\n" % (voltage, time))


    def OLMovePID0(self, axis, voltage):
        """
        Moves an axis a very little bit. Can be done only with servo off.
        Warning: it's completely hacky, there is no idea if it even moves
        axis (1<int<16): axis number
        voltage (-32766<=int<=32766): voltage for the PID control. <0 to go towards
          the negative direction. 32766 is 10V
        """
        # Uses MAC OLSTEP0, based on SMO
        assert(axis == 1)
        assert(-32766 <= voltage and voltage <= 32766)

        self._sendOrderCommand("MAC START OLSTEP0 %d\n" % (voltage,))


#Abs (with sensor = closed-loop):
#MOV (Set Target Position)
#MVR (Set Target Relative To Current Position)
#
#FNL (Fast Reference Move To Negative Limit)
#FPL (Fast Reference Move To Positive Limit)
#FRF (Fast Reference Move To Reference Switch)
#
#POS? (GetRealPosition)
#ONT? (Get On Target State)
#
#TMN? (Get Minimum Commandable Position)
#TMX? (Get Maximum Commandable Position)
#Min-Max position in physical units (μm)
#
#VEL (Set Closed-Loop Velocity)
#ACC (Set Closed-Loop Acceleration)
#DEC (Set Closed-Loop Deceleration)
#
# Different from OSM because they use the sensor and are defined in physical unit.
# Servo must be off! => Probably useless... compared to MOV/MVR
#OMR (Relative Open-Loop Motion)
#OMA (Absolute Open-Loop Motion)
#

    idn_matches = {
               "Physik Instrumente.*,.*C-867": MODEL_C867,
               "Physik Instrumente.*,.*E-861": MODEL_E861
               }
    def getModel(self):
        """
        returns a model constant
        """
        idn = self.GetIdentification()
        for m, c in self.idn_matches.items():
            if re.search(m, idn):
                return c
        return MODEL_UNKNOWN

    def getPosition(self, axis):
        """
        Note: in open-loop mode it's very approximate.
        return (float): the current position of the given axis
        """
        assert(axis in self._channels)
        if self._hasServo[axis]:
            # closed-loop
            raise NotImplementedError("No closed-loop support")
            # call POS?
        else:
            return self._position[axis]

    def setSpeed(self, axis, speed):
        """
        Changes the move speed of the motor (for the next move).
        Note: in open-loop mode, it's very approximate.
        speed (0<float<10): speed in m/s.
        axis (1<=int<=16): the axis
        """
        assert((0 < speed) and (speed <= self.max_speed))
        assert(axis in self._channels)
        self._speed[axis] = speed

    def getSpeed(self, axis):
        return self._speed[axis]

    def setAccel(self, axis, accel):
        """
        Changes the move acceleration (and deceleration) of the motor (for the next move).
        Note: in open-loop mode, it's very approximate.
        accel (0<float<100): acceleration in m/s².
        axis (1<=int<=16): the axis
        """
        assert((0 < accel) and (accel <= self.max_accel))
        assert(axis in self._channels)
        self._accel[axis] = accel

    def _updateCLSpeedAccel(self, axis):
        """
        Update the speed and acceleration values for the given axis. 
        It's only done if necessary, and only for the current closed- or open-
        loop mode.
        axis (1<=int<=16): the axis
        """
        prev_speed = self._prev_speed_accel[0].get(axis, None)
        new_speed = self._speed[axis]
        if prev_speed != new_speed:
            raise NotImplementedError("No closed-loop support")
            self._prev_speed_accel[0][axis] = new_speed

        prev_accel = self._prev_speed_accel[1].get(axis, None)
        new_accel = self._accel[axis]
        if prev_accel != new_accel:
            raise NotImplementedError("No closed-loop support")
            self._prev_speed_accel[1][axis] = new_accel

    def _updateOLSpeedAccel(self, axis):
        """
        Update the speed and acceleration values for the given axis. 
        It's only done if necessary, and only for the current closed- or open-
        loop mode.
        axis (1<=int<=16): the axis
        """
        prev_speed = self._prev_speed_accel[0].get(axis, None)
        new_speed = self._speed[axis]
        if prev_speed != new_speed:
            steps_ps = self.convertSpeedToDevice(new_speed)
            self.SetOLVelocity(axis, steps_ps)
            self._prev_speed_accel[0][axis] = new_speed

        prev_accel = self._prev_speed_accel[1].get(axis, None)
        new_accel = self._accel[axis]
        if prev_accel != new_accel:
            steps_pss = self.convertAccelToDevice(new_accel)
            self.SetOLAcceleration(axis, steps_pss)
            self.SetOLDeceleration(axis, steps_pss)
            self._prev_speed_accel[1][axis] = new_accel

    def _moveRelCL(self, axis, distance):
        """
        See moveRel
        """
        self._updateCLSpeedAccel(axis)
        # closed-loop
        raise NotImplementedError("No closed-loop support")
        # call MVR

        return distance

    def _moveRelOLStep(self, axis, distance):
        """
        See moveRel
        """
        self._updateOLSpeedAccel(axis)
        steps = self.convertDistanceToDevice(distance)
        if steps == 0: # if distance is too small, report it
            return 0
            # TODO: try to move anyway, just in case it works

        self.OLMoveStep(axis, steps)
        # TODO use OLAnalogDriving for very small moves (< 5µm)?
        return distance

    def _moveRelOLViaPID(self, axis, distance):
        """
        See moveRel
        """
        speed = self._speed[axis]
        v, t, ad = self.convertDistanceSpeedToPIDControl(distance, speed)
        logging.debug("Moving axis at %f V, for %f ms", v * (10 / 32687), t)
        if t == 0: # if distance is too small, report it
            return 0
        elif t < 1: # special small move command
            self.OLMovePID0(axis, v)
        else:
            self.OLMovePID(axis, v, t)

        return ad

    _moveRelOL = _moveRelOLStep

    def moveRel(self, axis, distance):
        """
        Move on a given axis for a given distance.
        It's asynchronous: the method might return before the move is complete.
        axis (1<=int<=16): the axis
        distance (float): the distance of move in m (can be negative)
        returns (float): approximate distance actually moved
        """
        # TODO: also report expected time for the move?
        assert(axis in self._channels)

        if self._hasServo[axis]:
            distance = self._moveRelCL(axis, distance)
        else:
            distance = self._moveRelOL(axis, distance)

        self._position[axis] += distance
        return distance

    def convertDistanceSpeedToPIDControl(self, distance, speed):
        """
        converts meters and speed to the units for this device (~V, ms) in
        open-loop via PID control.
        distance (float): meters (can be negative)
        speed (0<float): meters/s (can be negative)
        return (tuple: int, 0<number, float): PID control (in device unit, duration, distance)
        """
        voltage_u = round(int(speed * self._vpms)) # uV
        # clamp it to the possible values
        voltage_u = min(max(self._min_motor_out, voltage_u), self._max_motor_out)
        act_speed = voltage_u / self._vpms # m/s

        mv_time = abs(distance) / act_speed # s
        mv_time_ms = int(round(mv_time * 1000)) # ms
        if mv_time_ms < 10 and act_speed > self.min_speed:
            # small distance => try with the minimum speed to have a better precision
            return self.convertDistanceSpeedToPIDControl(distance, self.min_speed)
        elif mv_time_ms < 1 and mv_time > 0.1e-3:
            # try our special super small step trick if at least 0.1 ms
            # TODO: check it actually does something, and get better idea of how
            # much it's moving
            mv_time_ms = 0.1 # ms
            voltage_u = self._max_motor_out # TODO: change according to requested distance?
        elif mv_time_ms < 1:
            # really no hope
            return 0, 0, 0
        elif mv_time_ms >= 10000:
            logging.debug("Too big distance of %f m, shortening it", distance)
            mv_time_ms = 9999

        if distance < 0:
            voltage_u = -voltage_u

        act_dist = mv_time_ms * 1e-3 * voltage_u / self._vpms # m (very approximate)
        return voltage_u, mv_time_ms, act_dist

    def convertDistanceToDevice(self, distance):
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

    def convertSpeedToDevice(self, speed):
        """
        converts meters/s to the unit for this device (steps/s) in open-loop.
        distance (float): meters/s (can be negative)
        return (float): number of steps/s, <0 if going opposite direction
        """
        steps_ps = speed * self._dist_to_steps
        return max(1, steps_ps) # don't go at 0 m/s!

    # in linear approximation, it's the same
    convertAccelToDevice = convertSpeedToDevice

    def isMoving(self, axes=None):
        """
        Indicate whether the motors are moving. 
        axes (None or set of int): axes to check whether for move, or all if None
        return (boolean): True if at least one of the axes is moving, False otherwise
        """
        if axes is None:
            axes = self._channels
        else:
            assert axes.issubset(self._channels)

        return not axes.isdisjoint(self.GetMotionStatus())

    def _isMovingViaPID(self, axes=None):
        """
        Replacement method for isMoving() when the OL moves are done using OLViaPID
        """
        if axes is None:
            axes = self._channels
        else:
            assert axes.issubset(self._channels)

        # TOOD: support multiple channels
        assert(len(self._channels) == 1)
        axis = list(self._channels)[0]
        if self._hasServo[axis]:
            return not axes.isdisjoint(self.GetMotionStatus())
        else:
            return self.isAxisMovingOLViaPID(axis)

    def _stopMotionViaPID(self):
        """
        Stop the motion on all axes immediately
        Implementation for open-loop PID control 
        """
        self.Stop()
        for axis, hs in self._hasServo.items():
            if not hs:
                self.StopOLViaPID(axis)

    def stopMotion(self):
        """
        Stop the motion on all axes immediately
        """
        self.Stop()

    def waitEndMotion(self, axes=None):
        """
        Wait until the motion of all the given axis is finished.
        Note: there is a 5 s timeout
        axes (None or set of int): axes to check whether for move, or all if None
        """
        #TODO use the time, distance, and speed of last move to evaluate the timeout
        # approximately the time for the longest move
        timeout = 5 #s
        end = time.time() + timeout
        while self.isMoving(axes):
            if time.time() >= end:
                raise IOError("Timeout while waiting for end of motion")
            time.sleep(0.005)

    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        try:
            error = self.GetErrorNum()
            if error:
                logging.warning("Controller %d had error status %d", self.address, error)

            version = self.GetSyntaxVersion()
            logging.info("GCS version: '%s'", version)
            ver_num = float(version)
            if ver_num < 1 or ver_num > 2:
                logging.error("Controller %d has unexpected GCS version %s", self.address, version)
                return False

            axes = self.GetAxes()
            if len(axes) == 0 or len(axes) > 16:
                logging.error("Controller %d report axes %s", self.address, str(axes))
                return False

            if self._model in (MODEL_E861,): # support open-loop mode
                for a in self._channels:
                    self.SetStepAmplitude(a, 10)
                    amp = self.GetStepAmplitude(a)
                    if amp != 10:
                        logging.error("Failed to modify amplitude of controller %d (%f instead of 10)", self.address, amp)
                        return False

            if self._model in (MODEL_C867,): # support temperature reading
                # No support for direct open-loop mode
                # TODO put the temperature as a RO VA?
                current_temp = float(self.GetParameter(1, 0x57))
                max_temp = float(self.GetParameter(1, 0x58))
                if current_temp >= max_temp:
                    logging.error("Motor of controller %d too hot (%f C)", self.address, current_temp)
                    return False
        except:
            return False

        return True

    @staticmethod
    def scan(ser, max_add=16):
        """
        Scan the serial network for all the PI GCS compatible devices available.
        Note this is the low-level part, you probably want to use Controller.scan()
         for scanning devices on a computer.
        ser: the (open) serial port
        max_add (1<=int<=16): maximum address to scan
        return (dict int -> tuple): addresses of available controllers associated
            to number of axes, and presence of limit switches/sensor
        """
        ctrl = Controller(ser)

        present = {}
        for i in range(1, max_add + 1):
            # ask for controller #i
            logging.debug("Querying address %d", i)

            # is it answering?
            try:
                ctrl.address = i
                axes = {}
                for a in ctrl.GetAxes():
                    axes = {a: ctrl.hasSensor(a)}
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

class Bus(model.Actuator):
    """
    Represent a chain of PIGCS controllers over a serial port
    """
    def __init__(self, name, role, port, axes, baudrate=38400,
                 dist_to_steps=None, min_dist=None, vpms=None, **kwargs):
        """
        port (string): name of the serial port to connect to the controllers
        axes (dict string -> 3-tuple(1<=int<=16, 1<=int, boolean): the configuration
         of the network. For each axis name associates the controller address,
         channel, and whether it's closed-loop (absolute positioning) or not.
         Note that even if it's made of several controllers, each controller is 
         _not_ seen as a child from the odemis model point of view.
        baudrate (int): baudrate of the serial port (default is the recommended 
          38400). Use .scan() to detect it.
        Next 3 parameters are for calibration, see Controller for definition
        dist_to_steps (dict string -> (0 < float)): axis name -> value
        min_dist (dict string -> (0 <= float < 1)): axis name -> value
        vpms (dict string -> (0 < float)): axis name -> value
        """
        # this set ._axes and ._ranges
        model.Actuator.__init__(self, name, role, axes=axes.keys(), **kwargs)

        ser = self.openSerialPort(port, baudrate)

        dist_to_steps = dist_to_steps or {}
        min_dist = min_dist or {}
        vpms = vpms or {}

        # Prepare initialisation by grouping axes from the same controller
        ac_to_axis = {} # address, channel -> axis name
        controllers = {} # address -> args (dict (axis -> boolean), dist_to_steps, min_dist, vpms)
        for axis, (add, channel, isCL) in axes.items():
            if not add in controllers:
                controllers[add] = [{}, None, None, None]
            elif channel in controllers[add]:
                raise ValueError("Cannot associate multiple axes to controller %d:%d" % (add, channel))
            ac_to_axis[(add, channel)] = axis
            args = controllers[add]
            args[0].update({channel: isCL})
            # FIXME: for now we rely on the fact 1 axis = 1 controller for the calibration values
            args[1] = dist_to_steps.get(axis)
            args[2] = min_dist.get(axis)
            args[3] = vpms.get(axis)

        # Init each controller
        self._axis_to_cc = {} # axis name => (Controller, channel)
        # TODO also a rangesRel : min and max of a step
        position = {}
        speed = {}
        max_speed = 0 # m/s
        min_speed = 1e6 # m/s
        for address, args in controllers.items():
            try:
                controller = Controller(ser, address, *args)
            except IOError:
                logging.exception("Failed to find a controller with address %d on %s", address, port)
                raise
            except LookupError:
                logging.exception("Failed to initialise controller %d on %s", address, port)
                raise
            channels = args[0]
            for c in channels:
                axis = ac_to_axis[(address, c)]
                self._axis_to_cc[axis] = (controller, c)

                position[axis] = controller.getPosition(c)
                # TODO if closed-loop, the ranges should be updated after homing
                # For now we put very large one
                self._ranges[axis] = [-1, 1] # m
                # Just to make sure it doesn't go too fast
                speed[axis] = 0.001 # m/s
                max_speed = max(max_speed, controller.max_speed)
                min_speed = min(min_speed, controller.min_speed)


        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(position, unit="m", readonly=True)

        # min speed = don't be crazy slow. max speed from hardware spec
        self.speed = model.MultiSpeedVA(speed, range=[min_speed, max_speed], unit="m/s",
                                        setter=self._setSpeed)
        self._setSpeed(speed)

        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, self.getSerialDriver(port))
        hwversions = []
        for axis, (ctrl, channel) in self._axis_to_cc.items():
            hwversions.append("'%s': %s (GCS %s) for %s" % (axis, ctrl.GetIdentification(), ctrl.GetSyntaxVersion(), ctrl.GetStageName()))
        self._hwVersion = ", ".join(hwversions)

        # to acquire before sending anything on the serial port
        self.ser_access = threading.Lock()

        self._action_mgr = ActionManager(self)
        self._action_mgr.start()

    def _getPosition(self):
        """
        return (dict string -> float): axis name to (absolute) position
        """
        position = {}
        with self.ser_access:
            # send stop to all controllers (including the ones not in action)
            for axis, (controller, channel) in self._axis_to_cc.items():
                position[axis] = controller.getPosition(channel)

        return self._applyInversionAbs(position)

    def _updatePosition(self):
        """
        update the position VA
        Note: it should not be called while holding the lock to the serial port
        """
        pos = self._getPosition() # TODO: improve efficiency
        logging.debug("Reporting new position at %s", pos)

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    @staticmethod
    def getSerialDriver(name):
        """
        return (string): the name of the serial driver used for the given port
        """
        # In linux, can be found as link of /sys/class/tty/tty*/device/driver
        if sys.platform.startswith('linux'):
            path = ("/sys/class/tty/" + os.path.basename(os.path.realpath(name))
                    + "/device/driver")
            try:
                return os.path.basename(os.readlink(path))
            except OSError:
                return "Unknown"
        else:
            return "Unknown"

    def _setSpeed(self, value):
        """
        value (dict string-> float): speed for each axis
        returns (dict string-> float): the new value
        """
        for axis, v in value.items():
            controller, channel = self._axis_to_cc[axis]
            controller.setSpeed(channel, v)
        return value

    @isasync
    def moveRel(self, shift):
        """
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        returns (Future): future that control the asynchronous move
        """
        logging.debug("received request to move by %s", shift)
        shift = self._applyInversionRel(shift)
        # converts the request into one action (= a dict controller -> channels + distance)
        action_axes = {}
        for axis, distance in shift.items():
            if axis not in self.axes:
                raise ValueError("Axis unknown: " + str(axis))
            if abs(distance) > self.ranges[axis][1]:
                raise ValueError("Trying to move axis %s by %f m> %f m." %
                                (axis, distance, self.ranges[axis][1]))
            controller, channel = self._axis_to_cc[axis]
            if not controller in action_axes:
                action_axes[controller] = []
            action_axes[controller].append((channel, distance))

        action = ActionFuture(MOVE_REL, action_axes, self.ser_access)
        self._action_mgr.append_action(action)
        return action

    # TODO implement moveAbs

    def stop(self):
        """
        stops the motion on all axes
        Warning: this might stop the motion even of axes not managed (it stops
        all the axes of all controller managed).
        """
        if self._action_mgr:
            self._action_mgr.cancel_all()

        # Stop every axes (even if there is no action going, or action on just
        # some axes
        with self.ser_access:
            # send stop to all controllers (including the ones not in action)
            controllers = set()
            for axis, (controller, channel) in self._axis_to_cc.items():
                if controller not in controllers:
                    controller.stopMotion()
                    controllers.add(controller)

            # wait all controllers are done moving
            for controller in controllers:
                controller.waitEndMotion()

    def terminate(self):
        if not hasattr(self, "_action_mgr"):
            # not even fully initialised
            return

        self.stop()

        if self._action_mgr:
            self._action_mgr.terminate()
            self._action_mgr = None

    def selfTest(self):
        """
        No move should be going one while doing a self-test
        """
        passed = True
        controllers = set([c for c, a in self._axis_to_cc.values()])
        with self.ser_access:
            for controller in controllers:
                logging.info("Testing controller %d", controller.address)
                passed &= controller.selfTest()

        return passed

    @classmethod
    def scan(cls, port=None, _cls=None):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        returns (list of 2-tuple): name, args (port, axes(channel -> CL?)
        Note: it's obviously not advised to call this function if moves on the motors are ongoing
        """
        _cls = _cls or cls # use _cls if forced
        if port:
            ports = [port]
        else:
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range (0, 8)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')

        logging.info("Serial network scanning for PI-GCS controllers in progress...")
        axes_names = "xyzabcdefghijklmnopqrstuvw"
        found = []  # (list of 2-tuple): name, args (port, axes(channel -> CL?)
        for p in ports:
            try:
                # check all possible baud rates, in the most likely order
                for br in [38400, 9600, 19200, 115200]:
                    logging.debug("Trying port %s at baud rate %d", p, br)
                    ser = _cls.openSerialPort(port, br)
                    controllers = Controller.scan(ser)
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
                        # to have devices on different devices, we are done
                        break
            except serial.SerialException:
                # not possible to use this port? next one!
                pass

        return found

    @staticmethod
    def openSerialPort(port, baudrate=38400):
        """
        Opens the given serial port the right way for the PI controllers.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        baudrate (int): baudrate to use, default is the recommended 38400
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.5 #s
        )

        return ser


class ActionManager(threading.Thread):
    """
    Thread running the requested actions (=moves)
    Provides a queue (deque) of actions (action_queue)
    For each action in the queue: performs and wait until the action is finished
    At the end of the action, call all the callbacks
    """
    def __init__(self, bus, name="PIGCS action manager"):
        threading.Thread.__init__(self, name=name)
        self.daemon = True # If the backend is gone, just die

        self.action_queue_cv = threading.Condition()
        self.action_queue = collections.deque()
        self.current_action = None
        self._bus = bus

    def run(self):
        while True:
            # Pick the next action
            with self.action_queue_cv:
                while not self.action_queue:
                    self.action_queue_cv.wait()
                self.current_action = self.action_queue.popleft()

            # Special action "None" == stop
            if self.current_action is None:
                return

            try:
                self.current_action._start_action()
                self.current_action._wait_action()
            except futures.CancelledError:
                # cancelled in the mean time: skip the action
                pass

            # update position after the action is done
            self._bus._updatePosition() # FIXME: should update position before calling the callbacks

    def cancel_all(self):
        must_terminate = False
        with self.action_queue_cv:
            # cancel current action
            if self.current_action:
                self.current_action.cancel()

            # cancel every action in the queue
            while self.action_queue:
                action = self.action_queue.popleft()
                if action is None: # asking to terminate the thread
                    must_terminate = True
                    continue
                action.cancel()

        if must_terminate:
            self.append_action(None)

    def append_action(self, action):
        """
        appends an action in the doer's queue
        action (Action)
        """
        with self.action_queue_cv:
            self.action_queue.append(action)
            self.action_queue_cv.notify()

    def terminate(self):
        """
        Ask the action manager to terminate (once all the queued actions are done)
        """
        self.append_action(None)


MOVE_REL = "moveRel"
MOVE_ABS = "moveAbs"

PENDING = 'PENDING'
RUNNING = 'RUNNING'
CANCELLED = 'CANCELLED'
FINISHED = 'FINISHED'

class ActionFuture(object):
    """
    Provides the interface for the clients to manipulate an (asynchronous) action 
    they requested.
    It follows http://docs.python.org/dev/library/concurrent.futures.html
    The result is always None, or raises an Exception.
    Internally, it has a reference to the action manager thread.
    """
    possible_types = [MOVE_REL, MOVE_ABS]

    # TODO handle exception in action
    def __init__(self, action_type, args, ser_access):
        """
        type (str): name of the action (only supported so far is "moveRel"
        args (tuple): arguments to pass to the action
        ser_access (Lock): lock to access the serial port
        """
        assert(action_type in self.possible_types)

        logging.debug("New action of type %s with arguments %s", action_type, args)
        self._type = action_type
        self._args = args
        self._ser_access = ser_access
        self._expected_end = None # when it expects to finish (only during RUNNING)
        self._timeout = None # really too late to be running normally

        # acquire to modify the state, wait to wait for it to be done
        self._condition = threading.Condition()
        self._state = PENDING
        self._callbacks = []

    def _invoke_callbacks(self):
        # do not call with _condition! And ensure it's called only once
        for callback in self._callbacks:
            try:
                callback(self)
            except Exception:
                logging.exception('exception calling callback for %r', self)

    def cancel(self):
        with self._condition:
            if self._state == CANCELLED:
                return True
            elif self._state == FINISHED:
                return False
            elif self._state == RUNNING:
                self._stop_action()
                # go through, like for state == PENDING

            self._state = CANCELLED
            self._condition.notify_all()

        self._invoke_callbacks()
        return True

    def cancelled(self):
        with self._condition:
            return self._state == CANCELLED

    def running(self):
        with self._condition:
            return self._state == RUNNING

    def done(self):
        with self._condition:
            return self._state in [CANCELLED, FINISHED]

    def result(self, timeout=None):
        with self._condition:
            if self._state == CANCELLED:
                raise futures.CancelledError()
            elif self._state == FINISHED:
                return None

            self._condition.wait(timeout)

            if self._state == CANCELLED:
                raise futures.CancelledError()
            elif self._state == FINISHED:
                return None
            else:
                raise futures.TimeoutError()

    def exception(self, timeout=None):
        """
        return None or return what result raises
        """
        try:
            return self.result(timeout)
        except (futures.TimeoutError, futures.CancelledError) as exp:
            raise exp
        except Exception as exp:
            return exp

    def add_done_callback(self, fn):
        with self._condition:
            if self._state not in [CANCELLED, FINISHED]:
                self._callbacks.append(fn)
                return
        fn(self)

    def _start_action(self):
        """
        Start the physical action, and immediately return. It also set the 
        state to RUNNING.
        Note: to be called without the lock (._condition) acquired.
        """
        with self._condition:
            if self._state == CANCELLED:
                raise futures.CancelledError()

            # Do the action
            if self._type == MOVE_REL:
                duration = self._moveRel(self._args)
            elif self._type == MOVE_ABS:
                duration = self._moveAbs(self._args)
            else:
                raise Exception("Unknown action %s" % self._type)

            self._state = RUNNING
            duration = min(duration, 60) # => wait maximum 2 min
            self._expected_end = time.time() + duration
            self._timeout = self._expected_end + duration + 1, # 2 *duration + 1s

    def _wait_action(self):
        """
        Wait for the action to finish normally. If the action finishes normally
        it's also in charge of calling all the callbacks.
        Note: to be called without the lock (._condition) acquired.
        """
        # create a dict of controllers => channels
        controllers = {}
        for controller, moves in self._args.items():
            channels = [c for c, d in moves]
            controllers[controller] = channels

        with self._condition:
            assert(self._expected_end is not None)
            # if it has been cancelled in the mean time
            if self._state != RUNNING:
                return

            # it's over when either all axes are finished moving, it's too late,
            # or the action was cancelled
            logging.debug("Waiting %f s for the move to finish", self._expected_end - time.time())
            while self._state == RUNNING and time.time() <= self._timeout:
                duration = (self._expected_end - time.time()) / 2
                duration = max(0.01, duration)
                self._condition.wait(duration)
                if not self._isMoving(controllers):
                    break

            # if cancelled, we don't update state
            if self._state != RUNNING:
                return

            self._state = FINISHED
            self._condition.notify_all()

        self._invoke_callbacks()

    def _stop_action(self):
        """
        Stop the action. Do not call directly, call cancel()
        Note: to be called with the lock (._condition) acquired.
        """
        # The only two possible actions are stopped the same way

        # create a dict of controllers => channels
        controllers = {}
        for controller, moves in self._args.items():
            channels = [c for c, d in moves]
            controllers[controller] = channels

        self._stopMotion(controllers)

    def _isMoving(self, axes):
        """
        axes (dict: Controller -> list (int)): controller to channel which must be check for move
        """
        with self._ser_access:
            moving = False
            for controller, channels in axes.items():
                if len(channels) == 0:
                    logging.warning("Asked to check move on a controller without any axis")
                else:
                    moving |= controller.isMoving(set(channels))
            return moving

    def _stopMotion(self, axes):
        """
        axes (dict: Controller -> list (int)): controller to channel which must be stopped
        """
        with self._ser_access:
            for controller in axes:
                # it can only stop all axes (that's the point anyway)
                controller.stopMotion()

    def _moveRel(self, axes):
        """
        axes (dict: Controller -> list (tuple(int, double)): 
            controller to list of channel/distance to move (m)
        returns (float): approximate time in s it will take (optimistic)
        """
        with self._ser_access:
            max_duration = 0 #s
            for controller, channels in axes.items():
                for channel, distance in channels:
                    actual_dist = controller.moveRel(channel, distance)
                    duration = abs(actual_dist) / controller.getSpeed(channel)
                    max_duration = max(max_duration, duration)

        return max_duration

    def _moveAbs(self, axes):
        """
        axes (dict: Controller -> list (tuple(int, double)): 
            controller to list of channel/distance to move (m)
        returns (float): approximate time in s it will take (optimistic)
        """
        with self._ser_access:
            max_duration = 0 #s
            for controller, channels in axes.items():
                for channel, distance in channels:
                    actual_dist = controller.moveAbs(channel, distance)
                    duration = abs(actual_dist) / controller.getSpeed(channel)
                    max_duration = max(max_duration, duration)

        return max_duration




class DaisyChainSimulator(object):
    """
    Simulated serial port that can simulate daisy chain on the controllers
    Same interface as the serial port + list of (fake) serial ports to connect 
    """
    def __init__(self, timeout=0, *args, **kwargs):
        """
        subports (list of open ports): the ports to receive the data
        """
        self.timeout = timeout
        self._subports = kwargs["subports"]
        self._output_buf = "" # TODO: probably cleaner to user lock to access it

        # TODO: for each port, put a thread listening on the read and push to output
        self._is_terminated = False
        for p in self._subports:
            t = threading.Thread(target=self._thread_read_serial, args=(p,))
            t.start()

    def write(self, data):
        # just duplicate
        for p in self._subports:
            p.write(data)

    def read(self, size=1):
        # simulate timeout
        end_time = time.time() + self.timeout

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

    def _thread_read_serial(self, ser):
        """
        Push the output of the given serial port into our output
        """
        try:
            while not self._is_terminated:
                c = ser.read(1)
                if len(c) == 0:
                    time.sleep(0.001)
                else:
                    self._output_buf += c
        except Exception:
            logging.exception("Fake daisy chain thread received an exception")

    def close(self):
        self._is_terminated = True
        # using read or write will fail after that
        del self._output_buf
        del self._subports

class E861Simulator(object):
    """
    Simulates a GCS controller (+ serial port at 38400). Only used for testing.
    1 axis, open-loop only, very limited behaviour
    Same interface as the serial port
    """
    _idn = "(c)2013 Delmic Fake Physik Instrumente(PI) Karlsruhe, E-861 Version 7.2.0"
    _csv = "2.0"
    def __init__(self, port, baudrate=9600, timeout=0, address=1, *args, **kwargs):
        """
        parameters are the same as a serial port
        address (1<=int<=16): the address of the controller  
        """
        self._address = address
        # we don't care about the actual parameters but timeout
        self.timeout = timeout

        self._init_mem()

        self._end_move = 0 # time the last requested move is over

        self._output_buf = "" # what the commands sends back to the "host computer"
        self._input_buf = "" # what we receive from the "host computer"

        # special trick to only answer if baudrate is correct
        if baudrate != 38400:
            logging.debug("Baudrate incompatible: %d", baudrate)
            self.write = (lambda s=1: "")

    def _init_mem(self):
        # internal values to simulate the device
        # Parameter table: address -> value
        self._parameters = {0x14: 0, # 0 = no ref switch, 1 = ref switch
                            0x32: 1, # 0 = limit switches, 1 = no limit switches
                            0x3c: "DEFAULT-FAKE", # stage name
                            0x7000003: 10.0, # SSA
                            0x7000201: 3.2, # OVL
                            0x7000202: 0.9, # OAC
                            0x7000204: 15.3, # max step/s
                            0x7000205: 1.2, # max step/s²
                            0x7000206: 0.9, # ODC
                            }
        self._servo = 0 # servo state
        self._ready = True # is ready?
        self._errno = 0 # last error set

    _re_command = ".*?[\n\x04\x05\x07\x08\x18]"
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
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        if len(ret) < size:
            # simulate timeout
            time.sleep(self.timeout)
        return ret

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    # Command name -> parameter number
    _com_to_param = {"OVL": 0x7000201,
                     "OAC": 0x7000202,
                     "ODC": 0x7000206,
                     "SSA": 0x7000003,
                     }
    _re_addr_com = r"((?P<addr>\d+) (0 )?)?(?P<com>.*)"
    def _processCommand(self, com):
        """
        process the command, and put the result in the output buffer
        com (str): command
        """
        logging.debug("Fake controller %d processing command '%s'",
                       self._address, com.encode('string_escape'))
        out = None # None means error decoding command

        # command can start with a prefix like "5 0 " or "5 "
        m = re.match(self._re_addr_com, com)
        assert m # anything left over should be in com
        if m.group("addr"):
            addr = int(m.group("addr"))
            if addr != self._address:
                return # skip message

            prefix = "0 %d " % addr
        else:
            if 1 != self._address: # default is address == 1
                return # skip message

            prefix = ""

        com = m.group("com") # also removes the \n at the end if it's there
        # split into arguments separated by spaces (not including empty strings)
        args = filter(bool, com.split(" "))
        logging.debug("Command decoded: %s", args)

        # FIXME: if errno is not null, most commands don't work any more
        if self._errno:
            logging.debug("received command %s while errno = %d", com, self._errno)

        # TODO: to support more commands, we should have a table, with name of
        # the command + type of arguments (+ number of optional args)
        try:
            if com == "*IDN?": # identification
                out = self._idn
            elif com == "CSV?": # command set version
                out = self._csv
            elif com == "ERR?": # last error number
                out = "%d" % self._errno
                self._errno = 0 # reset error number
            elif com == "RBT": # reboot
                self._init_mem()
                time.sleep(0.1)
            elif com == "\x04": # Query Status Register Value
                # return hexadecimal bitmap of moving axes
                # TODO: to check, much more info returned
                val = 0
                if time.time() < self._end_move:
                    val |= 0x400  #  first axis moving
                out = "0x%x" % val
            elif com == "\x05": # Request Motion Status
                # return hexadecimal bitmap of moving axes
                if time.time() > self._end_move:
                    val = 0
                else:
                    val = 1 # first axis moving
                out = "%x" % val
            elif com == "\x07": # Request Controller Ready Status
                if self._ready: # TODO: when is it not ready??
                    out = "\xb1"
                else:
                    out = "\xb2"
            elif com == "\x18" or com == "STP": # Stop immediately
                self._end_move = 0
                self._errno = 10 # PI_CNTR_STOP
            elif args[0].startswith("HLT"): # halt motion with deceleration: axis (optional)
                self._end_move = 0
            elif args[0][:3] in self._com_to_param:
                param = self._com_to_param[args[0][:3]]
                if args[0][3:4] == "?": # query
                    if len(args) == 2:
                        out = "%s=%s" % (args[1], self._parameters[param])
                else:
                    if len(args) == 3:
                        # TODO: convert according to the type of the parameter
                        axis, val = int(args[1]), float(args[2])
                        if axis == 1:
                            self._parameters[param] = val
                        else:
                            self._errno = 15
            elif args[0] == "SVO" and len(args) == 3: # Set Servo State
                axis, state = int(args[1]), int(args[2])
                if axis == 1:
                    self._servo = state
                else:
                    self._errno = 15
            elif args[0] == "OSM" and len(args) == 3: #Open-Loop Step Moving
                axis, steps = int(args[1]), float(args[2])
                speed = self._parameters[self._com_to_param["OVL"]]
                if axis == 1:
                    duration = steps / speed
                    self._end_move = time.time() + duration # current move stopped
                else:
                    self._errno = 15
            elif args[0] == "LIM?" and len(args) == 2: # Has limit switch: axis
                axis = int(args[1])
                if axis == 1:
                    out = "%d" % (1 - self._parameters[0x32]) # inverted parameter
                else:
                    self._errno = 15
            elif args[0] == "TRS?" and len(args) == 2: # Indicate Reference Switch: axis
                axis = int(args[1])
                if axis == 1:
                    out = "%d" % self._parameters[0x14]
                else:
                    self._errno = 15
            elif args[0] == "SAI?" and len(args) <= 2: # List Of Current Axis Identifiers
                # Can be followed by "ALL", but for us, it's the same
                out = "1"
            elif args[0] == "SPA?" and len(args) == 3: # GetParameter: axis, address
                axis, addr = int(args[1]), int(args[2])
                if axis == 1:
                    try:
                        out = "%d=%s" % (addr, self._parameters[addr])
                    except KeyError:
                        logging.debug("Unknown parameter %d", addr)
                        self._errno = 56
                else:
                    self._errno = 15
            elif com == "HLP?":
                out = ("The following commands are available: \n" +
                       "#4 request status register \n" +
                       "HLP list the available commands \n" +
                       "ERR? get error number \n" +
                       "VEL {<AxisId> <Velocity>} set closed-loop velocity \n" +
                       "end of help"
                       )
            elif com == "HPA?":
                out = ("The following parameters are valid: \n" +
                       "0x1=\t0\t1\tINT\tmotorcontroller\tP term 1 \n" +
                       "0x32=\t0\t1\tINT\tmotorcontroller\thas limit\t(0=limitswitchs 1=no limitswitchs) \n" +
                       "0x3C=\t0\t1\tCHAR\tmotorcontroller\tStagename \n" +
                       "0x7000000=\t0\t1\tFLOAT\tmotorcontroller\ttravel range minimum \n" +
                       "end of help"
                       )
            else:
                logging.debug("Unknown command '%s'", com)
                self._errno = 1
        except Exception:
            logging.debug("Failed to process command '%s'", com)
            self._errno = 1

        # add the response header
        if out is None:
            logging.debug("Fake controller %d doesn't respond", self._address)
        else:
            out = "%s%s\n" % (prefix, out)
            logging.debug("Fake controller %d responding '%s'", self._address,
                          out.encode('string_escape'))
            self._output_buf += out

class FakeBus(Bus):
    """
    Same as the normal Bus, but connects to simulated controllers
    """
    @classmethod
    def scan(cls, port=None):
        # force only one port
        return Bus.scan(port="/fake/ttyPIGCS", _cls=cls)

    @staticmethod
    def openSerialPort(port, baudrate=38400):
        """
        Opens a fake serial port
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        # TODO: daisychain + address
        ser = E861Simulator(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5 #s
            )

        return ser
