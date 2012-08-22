# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from concurrent import futures
from odemis.model import isasync
from odemis import model
from odemis import __version__
import collections
import glob
import logging
import os
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

The controller accepts several baud rate. We choose 38400 (DIP=01) as it's fast
and it seems accepted by every version. Other settings are 8 data, 1 stop, 
no parity.


In open-loop, the controller has 2 ways to move the actuators:
 * Nanostepping: high-speed, and long distance
      1 step ~ 10 μm without load (less with load)
 * Analog: very precise, but moves maximum ~5μm
     "40 volts corresponds to a motion of approx. 3.3μm"
     "20 volts corresponds to a motion of approx. 1μm"

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

class Controller(object):
    def __init__(self, ser, address=None, axes=None):
        """
        ser: a serial port (opened)
        address 1<int<16: address as configured on the controller
        If not address is given, it just allows to do some raw commands
        axes (dict int -> boolean): determine which axis will be used and whether
          it will be used closed-loop (True) or open-loop (False). 
        """
        self.serial = ser
        self.address = address
        self._try_recover = False # for now, fully raw access
        # did the user asked for a raw access only?
        if address is None:
            return
        if axes is None:
            raise LookupError("Need to have at least one axis configured")
        
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
        
        self._channels = self.GetAxes() # available channels (=axes)
        # dict axis -> boolean
        self._hasLimit = dict([(a, self.hasLimitSwitches(a)) for a in self._channels])
        # dict axis -> boolean
        self._hasSensor = dict([(a, self.hasSensor(a)) for a in self._channels])
        # dict axis (string) -> servo activated (boolean): updated by SetServo
        self._hasServo = dict(axes)
        self._position = {} # m (dict axis-> position), only used in open-loop
        
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
                self.SetStepAmplitude(a, 55) # maximum is best
                self._position[a] = 0
        
        self._try_recover = True # full feature only after init 
        
        # For open-loop. For now, keep it simple: linear, using info from manual
        # TODO: allow to pass it in parameters
        self.move_calibration = 1e5 # step/m 
        self.min_stepsize = 0.01 # step, under this, no move at all
        
        # actually set just before a move
        # The max using closed-loop info seem purely arbitrary
        # (max m/s) = (max step/s) / (step/m)
        self.speed_max = float(self.GetParameter(1, 0x7000204)) / self.move_calibration # m/s
        # Note: the E-861 claims max 0.015 m/s but actually never goes above 0.004 m/s
        self._speed = dict([(a, self.speed_max/2) for a in axes]) # m/s
        # (max m/s²) = (max step/s²) / (step/m)
        self.accel_max = float(self.GetParameter(1, 0x7000205)) / self.move_calibration # m/s²
        self._accel = dict([(a, self.accel_max/2) for a in axes]) # m/s² (both acceleration and deceleration)
        self._prev_speed_accel = (dict(), dict()) 
    
    def _sendOrderCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \n if necessary)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: %s", full_com.encode('string_escape'))
        self.serial.write(full_com)
        
    def _sendQueryCommandRaw(self, com):
        """
        Send a command and return its report (raw)
        com (string): the command to send (without address prefix but with \n)
        return (list of strings): the complete report with each line separated and without \n 
        """
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: %s", full_com.encode('string_escape'))
        self.serial.write(full_com)
        
        char = self.serial.read() # empty if timeout
        line = ""
        lines = []
        while char:
            if char == "\n":
                if len(line) > 0 and line[-1] == " ": # multiline: " \n"
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

        logging.debug("Received: %s", "\n".join(lines).encode('string_escape'))
        prefix = "0 %d " % self.address
        if not lines[0].startswith(prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (com, lines[0]))
        lines[0] = lines[0][len(prefix):]

        if len(lines) == 1:
            return lines[0]
        else:
            return lines
    
    def recoverTimeout(self):
        """
        Try to recover from error in the controller state
        return (boolean): True if it recovered
        """
        # Give it some time to recover from whatever
        time.sleep(0.5)
        
        # It appears to make the controller more comfortable...
        self._sendOrderCommand("ERR?\n")
        char = self.serial.read()
        while char:
            if char == "\n":
                # TOOD Check if error == 307 or 308?
                return True
            char = self.serial.read()

        # We timed out again, try harder: reboot
        self.Reboot()
        self._sendOrderCommand("ERR?\n")
        char = " "
        while char:
            if char == "\n":
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
    
    def GetAxes(self):
        """
        returns (set of int): all the available axes
        """
        #SAI? (Get List Of Current Axis Identifiers)
        #SAI? ALL: list all axes (included disabled ones)
        answer = self._sendQueryCommand("SAI? ALL\n")
        # TODO check it works with multiple axes
        axes = set([int(a) for a in answer.split(" ")])
        return axes
    
    def GetAvailableCommands(self):
        #HLP? (Get List Of Available Commands)
        # first line starts with \x00
        lines = self._sendQueryCommand("HLP?\n")
        lines[0].lstrip("\x00")
        return lines 

    def GetAvailableParameters(self):
        #HPA? (Get List Of Available Parameters)
        # first line starts with \x00
        lines = self._sendQueryCommand("HPA?\n")
        lines[0].lstrip("\x00")
        return lines
     
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
        value = answer.split("=")[1]
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
        Note: apparently it's just read from a configuration value in flash 
        memory. Can be configured easily with PIMikroMove
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
            bitmap = bitmap >> 1
        return mv_axes

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
        time.sleep(1) # give it some time to reboot before it's accessible again

    def RelaxPiezos(self, axis):
        """
        Call relaxing procedure. Reduce voltage, to increase lifetime and needed
          to change between modes
        axis (1<int<16): axis number
        """
        #RNP (Relax PiezoWalk Piezos): reduce voltage when stopped to increase lifetime
        #Also needed to change between nanostepping and analog
        assert(axis in self._channels)
        self._sendOrderCommand("RNP %d\n" % axis)

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
          the opposite direction. 1 step is about 10um.
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
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        velocity (0<float): velocity in step-cycles/s. Default is 200 (~ 0.002 m/s)
        """
        #OVL (Set Open-Loop Velocity)
        assert(axis in self._channels)
        assert(velocity > 0)
        self._sendOrderCommand("OVL %d %.5g\n" % (axis, velocity))
    
    def SetOLAcceleration(self, axis, value):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        value (0<float): acceleration in step-cycles/s. Default is 2000 
        """
        #OAC (Set Open-Loop Acceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("OAC %d %.5g\n" % (axis, value))
        
    def SetOLDeceleration(self, axis, value):
        """
        Moves an axis for a number of steps. Can be done only with servo off.
        axis (1<int<16): axis number
        value (0<float): deceleration in step-cycles/s. Default is 2000 
        """
        #ODC (Set Open-Loop Deceleration)
        assert(axis in self._channels)
        assert(value > 0)
        self._sendOrderCommand("ODC %d %.5g\n" % (axis, value))

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
        assert((0 < speed) and (speed <= self.speed_max))
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
        assert((0 < accel) and (accel <= self.accel_max))
        assert(axis in self._channels)
        self._accel[axis] = accel
    
    def _updateSpeedAccel(self, axis):
        """
        Update the speed and acceleration values for the given axis. 
        It's only done if necessary, and only for the current closed- or open-
        loop mode.
        axis (1<=int<=16): the axis
        """
        prev_speed = self._prev_speed_accel[0].get(axis, None)
        new_speed = self._speed[axis]
        if prev_speed != new_speed:
            if self._hasServo[axis]:
                raise NotImplementedError("No closed-loop support")
            else:
                steps_ps = self.convertSpeedToDevice(new_speed)
                self.SetOLVelocity(axis, steps_ps)
            self._prev_speed_accel[0][axis] = new_speed
        
        prev_accel = self._prev_speed_accel[1].get(axis, None)
        new_accel = self._accel[axis]
        if prev_accel != new_accel:
            if self._hasServo[axis]:
                raise NotImplementedError("No closed-loop support")
            else:
                steps_pss = self.convertAccelToDevice(new_accel)
                self.SetOLAcceleration(axis, steps_pss)
                self.SetOLDeceleration(axis, steps_pss)
            self._prev_speed_accel[1][axis] = new_accel       
        
    def moveRel(self, axis, distance):
        """
        Move on a given axis for a given pulse length, will repeat the steps if
        it requires more than one step. It's asynchronous: the method might return
        before the move is complete.
        axis (1<=int<=16): the axis
        distance (float): the distance of move in m (can be negative)
        returns (float): approximate distance actually moved
        """
        assert(axis in self._channels)
        
        self._updateSpeedAccel(axis)
        
        # open-loop and closed-loop use different commands
        if self._hasServo[axis]:
            # closed-loop
            raise NotImplementedError("No closed-loop support")
            # call MVR
        else:
            steps = self.convertDistanceToDevice(distance)
            if steps == 0: # if distance is too small, report it
                return 0
            
            self.OLMoveStep(axis, steps)
            # TODO use OLAnalogDriving for very small moves (< 5um)?
            
            self._position[axis] += distance
        
        return distance
    
    def convertDistanceToDevice(self, distance):
        """
        converts meters to the unit for this device (steps) in open-loop.
        distance (float): meters (can be negative)
        return (float): number of steps, <0 if going opposite direction
            0 if too small to move.
        """
        steps = distance * self.move_calibration
        if abs(steps) < self.min_stepsize:
            return 0
        
        return steps
    
    def convertSpeedToDevice(self, speed):
        """
        converts meters/s to the unit for this device (steps/s) in open-loop.
        distance (float): meters/s (can be negative)
        return (float): number of steps/s, <0 if going opposite direction
        """
        steps_ps = speed * self.move_calibration
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
            if time.time() <= end:
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
        
            for a in self._channels:
                self.SetStepAmplitude(a, 10)
                amp = self.GetStepAmplitude(a)
                if amp != 10:
                    logging.error("Failed to modify amplitude of controller %d (%f instead of 10)", self.address, amp)
                    return False
        except:
            return False
        
        return True
    
    @staticmethod
    def scan(port, max_add=16):
        """
        Scan the serial network for all the PI C-170 available.
        port (string): name of the serial port
        max_add (1<=int<=16): maximum address to scan
        return (dict int -> tuple): addresses of available controllers associated
            to number of axes, and presence of limit switches/sensor
        """
        ser = Controller.openSerialPort(port)
        ctrl = Controller(ser)
        
        logging.info("Serial network scanning for PI-GCS controllers in progress...")
        present = {}
        for i in range(1, max_add+1):
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
                    present[i] = axes
            except IOError:
                pass
        
        ctrl.address = None
        return present
    
    @staticmethod
    def openSerialPort(port):
        """
        Opens the given serial port the right way for the PI-E861.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port = port,
            baudrate = 38400,
            bytesize = serial.EIGHTBITS,
            parity = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout = 0.3 #s
        )
        
        return ser


class Bus(model.Actuator):
    """
    Represent a chain of PI controller over a serial port
    """
    def __init__(self, name, role, port, axes, children=None, **kwargs):
        """
        port (string): name of the serial port to connect to the controllers
        axes (dict string=> 3-tuple(1<=int<=16, 1<=int, boolean): the configuration
         of the network. For each axis name associates the controller address,
         channel, and whether it's closed-loop (absolute positioning) or not.
         Note that even if it's made of several controllers, each controller is 
         _not_ seen as a child from the odemis model point of view.
        """
        # this set ._axes and ._ranges
        model.Actuator.__init__(self, name, role, axes=axes.keys(), children=children, **kwargs)
        
        ser = Controller.openSerialPort(port)

        # Prepare initialisation by grouping axes from the same controller
        ac_to_axis = {} # address, channel -> axis name
        controllers = {} # address -> dict (axis -> boolean)
        for axis, (add, channel, isCL) in axes.items():
            if not add in controllers:
                controllers[add] = {}
            elif channel in controllers[add]:
                raise ValueError("Cannot associate multiple axes to controller %d:%d" % (add, channel))
            ac_to_axis[(add, channel)] = axis 
            controllers[add].update({channel: isCL})

        # Init each controller            
        self._axis_to_cc = {} # axis name => (Controller, channel)
        # TODO also a rangesRel : min and max of a step
        position = {} 
        speed = {}
        max_speed = 1 # m/s
        for address, channels in controllers.items():
            try:
                controller = Controller(ser, address, channels)
            except IOError:
                logging.exception("Failed to find a controller with address %d on %s", address, port)
                raise
            except LookupError:
                logging.exception("Failed to initialise controller %d on %s", address, port)
                raise
            for c in channels:
                axis = ac_to_axis[(address, c)]
                self._axis_to_cc[axis] = (controller, c)
                
                position[axis] = controller.getPosition(c)
                # TODO if closed-loop, the ranges should be updated after homing
                # For now we put very large one
                self._ranges[axis] = [0, 1] # m
                # Just to make sure it doesn't go too fast
                speed[axis] = 0.001 # m/s
                max_speed = max(max_speed, controller.speed_max)
        
        
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(position, unit="m", readonly=True)
        
        # min speed = don't be crazy slow. max speed from hardware spec
        self.speed = model.MultiSpeedVA(speed, range=[10e-6, max_speed], unit="m/s",
                                        setter=self.setSpeed)
        self.setSpeed(speed)
        
        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (__version__.version, self.getSerialDriver(port))
        hwversions = []
        for axis, (ctrl, channel) in self._axis_to_cc.items():
            hwversions.append("'%s': %s (GCS %s)" % (axis, ctrl.GetIdentification(), ctrl.GetSyntaxVersion()))
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
        
        return position
    
    # TODO needs to be triggered by end of action, or directly controller? 
    # maybe whenever a controller updates it's position?
    # How to avoid updating each axis one-by-one?
    # Maybe we should just do it regularly as long as there are actions in the queue
    def _updatePosition(self):
        """
        update the position VA
        Note: it should not be called while holding the lock to the serial port
        """
        pos = self._getPosition() # TODO: improve efficiency
        
        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)
    
    def getSerialDriver(self, name):
        """
        return (string): the name of the serial driver used for the given port
        """
        # In linux, can be found as link of /sys/class/tty/tty*/device/driver
        if sys.platform.startswith('linux'):
            path = "/sys/class/tty/" + os.path.basename(name) + "/device/driver"
            try:
                return os.path.basename(os.readlink(path))
            except OSError:
                return "Unknown"
        else:
            return "Unknown"
    
    def setSpeed(self, value):
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
        # converts the request into one action (= a dict controller -> channels + distance) 
        action_axes = {}
        for axis, distance in shift.items():
            if axis not in self.axes:
                raise Exception("Axis unknown: " + str(axis))
            if abs(distance) > self.ranges[axis][1]:
                raise Exception("Trying to move axis %s by %f m> %f m." % 
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
    
    @staticmethod
    def scan(port=None):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        returns (list of 2-tuple): name, args (port, axes(channel -> CL?)
        Note: it's obviously not advised to call this function if moves on the motors are ongoing
        """ 
        if port:
            ports = [port]
        else:
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range (0,8)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')
        
        axes_names = "xyzabcdefghijklmnopqrstuvw"
        found = []  # (list of 2-tuple): name, args (port, axes(channel -> CL?)
        for p in ports:
            try:
                controllers = Controller.scan(p)
            except serial.SerialException:
                # not possible to use this port? next one!
                continue
            
            if controllers:
                axis_num = 0
                arg = {}
                for add, axes in controllers.items():
                    for a, cl in axes.items():
                        arg[axes_names[axis_num]] = (add, a, cl)
                        axis_num += 1
                found.append(("Actuator " + os.path.basename(p),
                             {"port": p, "axes": arg}))
        
        return found


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
            self._bus._updatePosition()
    
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
        Start the physical action, and immediatly return. It also set the 
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
                        
            duration = self._expected_end - time.time()
            duration = max(0, duration) 
            logging.debug("Waiting %f s for the move to finish", duration)
            self._condition.wait(duration)
            
            # it's over when either all axes are finished moving, it's too late,
            # or the action was cancelled
            while (self._state == RUNNING and time.time() <= self._timeout
                   and self._isMoving(controllers)):
                self._condition.wait(0.01)
            
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
                if len(channels) == 1:
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

