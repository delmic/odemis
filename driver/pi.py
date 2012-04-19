#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 22 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from model._components import Actuator
import glob
import logging
import math
import model
import os
import serial
import time

# Status:
# byte 1
STATUS_ECHO_ON = 0x0001 #Bit 0: Echo ON
#Bit 1: Wait in progress
STATUS_COMMAND_ERROR = 0x0004 #Bit 2: Command error
#Bit 3: Leading zero suppression active
#Bit 4: Macro command called
#Bit 5: Leading zero suppression disabled
#Bit 6: Number mode in effect
STATUS_BOARD_ADDRESSED = 0x000080 #Bit 7: Board addressed
# byte 2
#Bit 0: Joystick X enabled
#Bit 1: Joystick Y enabled
STATUS_PULSE_X = 0x000400 #Bit 2: Pulse output on channel 1 (X)
STATUS_PULSE_Y = 0x000800 #Bit 3: Pulse output on channel 2 (Y)
STATUS_DELAY_X = 0x001000 #Bit 4: Pulse delay in progress (X)
STATUS_DELAY_Y = 0x002000 #Bit 5: Pulse delay in progress (Y)
STATUS_MOVING_X = 0x004000 #Bit 6: Is moving (X)
STATUS_MOVING_Y = 0x008000 #Bit 7: Is moving (Y)
# byte 3 (always FF in practice)
#Bit 0: Limit Switch ON
#Bit 1: Limit switch active state HIGH
#Bit 2: Find edge operation in progress
#Bit 3: Brake ON
#Bit 4: n.a.
#Bit 5: n.a.
#Bit 6: n.a.
#Bit 7: n.a.
# byte 4
#Bit 0: n.a.
#Bit 1: Reference signal input
#Bit 2: Positive limit signal input
#Bit 3: Negative limit signal input
#Bit 4: n.a.
#Bit 5: n.a.
#Bit 6: n.a.
#Bit 7: n.a.
# byte 5 (Error codes)
ERROR_NO = 0x00 #00: no error
ERROR_COMMAND_NOT_FOUND = 0x01 #01: command not found
#02: First command character was not a letter
#05: Character following command was not a digit
#06: Value too large
#07: Value too small
#08: Continuation character was not a comma
#09: Command buffer overflow
#0A: macro storage overflow


# TODO: refactorise so that there is a more clear bus/controller objects distinction
# Currently each controller is in charge of selecting itself on the bus by using
# the serial port object as a global container. This should be moved to the bus 
# object. The bus object should be the only one directly accessible.  

class PIRedStone(object):
    '''
    This represents the bare PI C-170 piezo motor controller (Redstone), the 
    information comes from the manual C-170_User_MS133E104.pdf. Note that this
    controller uses only native commands, which are different from the "PI GCS",
    (general command set). 
    
    From the device description:
    The distance and velocity travelled corresponds to the width, frequency and 
    number of motor-on pulses. By varying the pulse width, the step length [...]
    the motor velocity can be controlled. As the mechanical environment
    also influences the motion, the size of single steps is not highly
    repeatable. For precise position control, a system with a position feedback
    device is recommended (closed-loop operation).
    Miniature-stages can achieve speeds of 500 mm/s and more with minimum
    incremental motion of 50 nm.
    :
    The smallest step a piezo motor can make is typically on the order of 
    0.05 μm and corresponds to a 10 μs pulse (shorter pulses have no effect).

    In practice: if you give a too small duration to a step, it will not move 
    at all. In experiments, 50µs for duration of a pulse is the minimum that
    moves the axis (of about 500 nm). Note that it's not linear:
    50 µs  => 0.5µm
    255 µs => 5µm
    
    The controller has also many undocumented behaviour (bugs):
        * when doing a burst move at maximum frequency (wait == 0), it cannot be
          stopped
        * if you ask the status when it is waiting (WA, WS) it will fail to
          report status and enter error mode. Only a set command can recover
          it (SR? works).
        * HE returns a string which starts with 2 null characters.
        
    Here are all the commands the controller reports:
    EM RM RZ TZ TM MD MC YF YN WE CP XF XN TA WF WN CF CN TC SW SS SR SJ SI PP JN JF IW IS IR GP GN CD CA BR HM DM UD DE SO HE FE LH LL LF LN AB WS TD SC TT TP TL TY SD SA SV GH DH MA MR TS CS EN EF TI TB RP WA RT VE 
    '''

    def __init__(self, ser, address=None):
        '''
        Initialise the given controller #id over the given serial port
        ser: a serial port
        address 0<int<15: the address of the controller as defined by its jumpers 1-4
        if no address is given, then no controller is selected
        '''
        self.serial = ser
        
        self.duration_range = (30, 1e6) # µs min, max duration of a step to move
        self.scale = 2e-8 # m/µs very rough scale (if it was linear)
        # Warning: waittime == 1 => unabortable!
        self.waittime = [2, 2] # ms for each axis how long to wait between 2 steps => speed (1=max speed) 
        
        self.address = address
        # allow to not initialise the controller (mostly for ScanNetwork())
        if address is None:
            self.try_recover = False # really raw mode
            return
        
        self.try_recover = True
        # Small check to verify it's responding
        self.select()
        try:
            add = self.tellBoardAddress()
            if add != address:
                logging.warning("asked for PI controller %d and was answered by controller %d.", address, add)
        except IOError:
            raise IOError("No answer from PI controller %d" % address)

    def setSpeed(self, speed, axis):
        """
        Changes the move speed of the motor (for the next move). It's very 
        approximate.
        speed (0<float<5): speed in m/s.
        axis (int 0 or 1): axis to pic  
        """
        assert(speed > 0)
        assert((0 <= axis) and (axis <= 1))
        
        # 5 m/s is the max speed according to specification
        # => 1 ms waiting time = 5 m/s

        # approximate by formula:
        # speed = k * 1/waittime
        # knowing that 5 = k * 1 / 0.001 
        k = 5e-3
        
        # In smallest speed, move is almost instantaneous
        # => one step = 255 * scale takes 65535 ms
        # speed = k * 1/waittime
        # distance/waittime = k/waittime
        # k = distance 
        # k = 255 * 2e-8 = 5.1e-6  (<<  5e-3!)
        
        waittime = k/speed # s
        waittime_ms = round(waittime * 1e3)
        # warning: waittime == 1 => faster but unabordable during move 
        self.waittime[axis] = min(max(2, waittime_ms), 65535)
        
    def _sendSetCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \r if necessary)
        """
        for sc in com.split(","):
            assert(len(sc) < 10)
            
        logging.debug("Sending: %s", com.encode('string_escape'))
        self.serial.write(com)
        # TODO allow to check for error via TellStatus afterwards
    
    def _sendGetCommand(self, com, prefix="", suffix="\r\n\x03"):
        """
        Send a command and return its report
        com (string): the command to send
        prefix (string): the prefix to the report,
            it will be removed from the return value
        suffix (string): the suffix of the report. Read will continue until it 
            is found or there is a timeout. It is removed from the return value.  
        return (string): the report without prefix nor newline
        """
        assert(len(com) <= 10)
        assert(len(prefix) <= 2)
        logging.debug("Sending: %s", com.encode('string_escape'))
        self.serial.write(com)
        
        char = self.serial.read() # empty if timeout
        report = char
        while char and not report.endswith(suffix):
            char = self.serial.read()
            report += char
            
        if not char:
            if not self.try_recover:
                raise IOError("PI controller %d timeout.")
                
            success = self.recoverTimeout()
            if success:
                logging.warning("PI controller %d timeout, but recovered.", self.address)
            else:
                raise IOError("PI controller %d timeout, not recovered." % self.address)
            
        logging.debug("Receive: %s", report.encode('string_escape'))
        if not report.startswith(prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (com, report))

        return report.lstrip(prefix).rstrip(suffix)
    
    def recoverTimeout(self):
        """
        Try to recover from error in the controller state
        return (boolean): True if it recovered
        """
        # It appears to make the controller comfortable...
        self._sendSetCommand("SR?\r%")
        
        char = self.serial.read()
        while char:
            if char == "\x03":
                return True
            char = self.serial.read()
        # we still timed out
        return False
    
    # Low-level functions
    def addressSelection(self, address):
        """
        Send the address selection command over the bus to select the given controller
        address 0<int<15: the address of the controller as defined by its jumpers 1-4  
        """
        assert((0 <= address) and (address <= 15))
        self._sendSetCommand("\x01%X" % address)
        
    def selectController(self, address):
        """
        Tell the currently selected controller that the given controller is selected
        Useless but for tests (or in macros)
        """
        assert((0 <= address) and (address <= 15))
        self._sendSetCommand("SC%d\r" % address)
        
    def tellStatus(self):
        """
        Call the Tell Status command and return the answer.
        return (2-tuple (status: int, error: int): 
            * status is a flag based value (cf STATUS_*)
            * error is a number corresponding to the last error (cf ERROR_*)
        """ 
        #bytes_str = self._sendGetCommand("TS\r", "S:")
        #The documentation claims the report prefix is "%", but it's just "S:"
        bytes_str = self._sendGetCommand("%", "S:") # short version
        # expect report like "S:A1 00 FF 00 00\r\n\x03"
        bytes_int = [int(b, 16) for b in bytes_str.split(" ")]
        st = bytes_int[0] + (bytes_int[1] << 8) + (bytes_int[2] << 16) + (bytes_int[3] << 24)
        err = bytes_int[4]
        assert((0 <= err) and (err <= 255))
        return (st, err)

    def tellBoardAddress(self):
        """
        returns the device address as set by DIP switches at the
        Redstone's front panel.
        return (0<=int<=15): device address
        """
        report = self._sendGetCommand("TB\r", "B:")
        address = int(report)
        assert((0 <= address) and (address <= 15))
        return address

    def versionReport(self):
        version = self._sendGetCommand("VE\r")
        # expects something like:
        #(C)2004 PI GmbH Karlsruhe, Ver. 2.20, 7 Oct, 2004 CR LF ETX 
        return version

    def help(self):
        """
        Lists all commands available.
        """
        # apparently returns a string starting with \0\0... so get rid of it
        return self._sendGetCommand("HE\r", "\x00\x00", "\n")
    
    def abortMotion(self):
        """
        Stops the running output pulse sequences started by GP or GN.
        """
        # Just AB doesn't stop all the time, need to be much more aggressive
        # SR1 is to stop any "wait" and return into a stable mode
        # PP0 immediately puts all lines to 00, that helps a bit AB
        self._sendSetCommand("SR1,PP0,AB\r")
        while self.isMoving():
            self._sendSetCommand("AB\r")

    def pulseOutput(self, axis, duration):
        """
        Outputs pulses of length duration on channel axis
        axis (int 0 or 1): the output channel
        duration (1<=int<=255): the duration of the pulse
        """
        assert((0 <= axis) and (axis <= 1))
        assert((1 <= duration) and (duration <= 255))
        self._sendSetCommand("%dCA%d\r" % (axis + 1, duration))

    def setDirection(self, axis, direction):
        """
        Applies a static direction flag (positive or negative) to the axis. 
        axis (int 0 or 1): the output channel
        direction (int 0 or 1): 0=low(positive) and 1=high(negative)
        """
        assert((0 <= axis) and (axis <= 1))
        assert((0 <= direction) and (direction <= 1))
        self._sendSetCommand("%dCD%d\r" % (axis + 1, direction))
        
    def stringGoPositive(self, axis):
        """
        Used to execute a move in the positive direction as defined by
            the SS, SR and SW values.
        axis (int 0 or 1): the output channel
        """
        assert((0 <= axis) and (axis <= 1))
        return "%dGP" % axis + 1
                
    def goPositive(self, axis):
        """
        Used to execute a move in the positive direction as defined by
            the SS, SR and SW values.
        axis (int 0 or 1): the output channel
        """
        self._sendSetCommand(self.stringGoPositive(axis) + "\r")

    def stringGoNegative(self, axis):
        """
        Used to execute a move in the negative direction as defined by
            the SS, SR and SW values.
        axis (int 0 or 1): the output channel
        """
        assert((0 <= axis) and (axis <= 1))
        return "%dGN" % axis + 1

    def goNegative(self, axis):
        """
        Used to execute a move in the negative direction as defined by
            the SS, SR and SW values.
        axis (int 0 or 1): the output channel
        """
        self._sendSetCommand(self.stringGoNegative(axis) + "\r")

    def stringSetRepeatCounter(self, axis, repetitions):
        """
        Set the repeat counter for the given axis (1 = one step)
        axis (int 1 or 2): the output channel
        repetitions (1<=int<=65535): the amount of repetitions
        """
        assert((1 <= axis) and (axis <= 2))
        assert((1 <= repetitions) and (repetitions <= 65535))
        return "%dSR%d" % (axis, repetitions)

    def setRepeatCounter(self, axis, repetitions):
        """
        Set the repeat counter for the given axis (1 = one step)
        axis (int 0 or 1): the output channel
        repetitions (1<=int<=65535): the amount of repetitions
        """
        self._sendSetCommand(self.stringSetRepeatCounter(axis, repetitions) + "\r")

    def stringSetStepSize(self, axis, duration):
        """
        Set the step size that corresponds with the length of the output
            pulse for the given axis
        axis (int 0 or 1): the output channel
        duration (0<=int<=255): the length of pulse in μs
        """
        return "%dSS%d" % (axis + 1, duration)

    def setStepSize(self, axis, duration):
        """
        Set the step size that corresponds with the length of the output
            pulse for the given axis
        axis (int 0 or 1): the output channel
        duration (0<=int<=255): the length of pulse in μs
        """
        self._sendSetCommand(self.stringSetStepSize(axis, duration) + "\r")

    def stringSetWaitTime(self, axis, duration):
        """
        This command sets the delay time (wait) between the output of pulses when
            commanding a burst move for the given axis.
        axis (int 0 or 1): the output channel
        duration (1<=int<=65535): the wait time (ms), 1 gives the highest output frequency.
        """
        assert((0 <= axis) and (axis <= 1))
        assert((1 <= duration) and (duration <= 65535))
        return"%dSW%d" % (axis + 1, duration)

    def setWaitTime(self, axis, duration):
        """
        This command sets the delay time (wait) between the output of pulses when
            commanding a burst move for the given axis.
        axis (int 0 or 1): the output channel
        duration (0<=int<=65535): the wait time (ms), 1 gives the highest output frequency.
        """
        self._sendSetCommand(self.stringSetWaitTime(axis, duration) + "\r")


    # High-level functions
    def select(self):
        """
        ensure the controller is selected to be managed
        """
        # Do not select it if it's already selected
        if self.serial._pi_select != self.address:
            self.addressSelection(self.address)
        self.serial._pi_select = self.address
    
    def moveRelSmall(self, axis, duration):
        """
        Move on a given axis for a given pulse length
        axis (int 0 or 1): the output channel
        duration (-255<=int<=255): the duration of pulse in μs,
                                   negative to go negative direction
        """
        assert((0 <= axis) and (axis <= 1))
        assert((-255 <= duration) and (duration <= 255))
        if duration == 0:
            return
        
        self.select()
        if duration > 0:
            self.setDirection(axis, 0)
        else:
            self.setDirection(axis, 1)
        
        self.pulseOutput(axis, round(abs(duration)))
    
    def moveRel(self, axis, duration):
        """
        Move on a given axis for a given pulse length, will repeat the steps if
        it requires more than one step.
        axis (int 0 or 1): the output channel
        duration (int): the duration of pulse in μs
        returns (float): approximate distance actually moved
        """
        assert((0 <= axis) and (axis <= 1))
        if duration == 0:
            return 0.0
        
        self.select()
        # Clamp the duration to min,max
        # Min because it's better to move too much than nothing XXX (maybe not, or only if at least half to what we can do min)
        # Max because we don't want to move too far, and repetition is limited
        sign = cmp(duration, 0)
        distance = sorted(self.duration_range + (abs(duration),))[1]

        # Tried to use a compound command with several big steps and one small.
        # eg: 1SW1,1SS255,1SR3,1GN,1WS2,1SS35,1SR1,1GN\r
        # A problem is that while it's waiting (WS) any new command (ex, TS)
        # will stop the wait and the rest of the compound.
        
        # TODO we could compute the waittime here, depending on the speed and stepsize
        
        if distance > 255:
            # We need several steps to do the move. The restriction is that 
            # every step should be the same size. So we try to make steps
            # as big as possible, which in total come as close as possible to
            # the requested distance. 
            steps = math.ceil(distance / 255.0)
            stepsize = round(distance / steps)
            distance = steps * stepsize # what we actually expect to move
            com = self.stringSetWaitTime(axis, self.waittime[axis])
            com += "," + self.stringSetStepSize(axis, stepsize)
            com += "," + self.stringSetRepeatCounter(axis, steps)
            if sign > 0:
                com += "," + self.stringGoPositive(axis)
            else:
                com += "," + self.stringGoNegative(axis)
        else:
            # we can do the move in one small step
            com = self.stringSetWaitTime(axis, self.waittime[axis])
            com += "," + self.stringSetStepSize(axis, distance)
            com += "," + self.stringSetRepeatCounter(axis, 1)
            if sign > 0:
                com += "," + self.stringGoPositive(axis)
            else:
                com += "," + self.stringGoNegative(axis)

        print duration, com
        self._sendSetCommand(com + "\r")
        return sign * distance
    
    def isMoving(self, axis=None):
        """
        Indicate whether the motors are moving. 
        axis (None, 1, or 2): axis to check whether it is moving, or both if None
        return (boolean): True if moving, False otherwise
        """
        self.select()
        st, err = self.tellStatus()
        if axis == 1:
            mask = STATUS_MOVING_X | STATUS_PULSE_X | STATUS_DELAY_X
        elif axis == 2:
            mask = STATUS_MOVING_Y | STATUS_PULSE_Y | STATUS_DELAY_Y
        else:
            mask = (STATUS_MOVING_X | STATUS_PULSE_X | STATUS_DELAY_X |
                    STATUS_MOVING_Y | STATUS_PULSE_Y | STATUS_DELAY_Y)
        
        return bool(st & mask)
    
    def stopMotion(self, axis):
        """
        Stop the motion of all the given axis.
        For the Redstone, both axes are stopped simultaneously
        """
        self.select()
        self.abortMotion()
          
    def waitEndMotion(self, axis=None):
        """
        Wait until the motion of all the given axis is finished.
        axis (None, 1, or 2): axis to check whether it is moving, or both if None
        """
        # approximately the time for the longest move
        if axis:
            waittime = self.waittime[axis]
        else:
            waittime = max(self.waittime)
        timeout = self.duration_range[1] * (1e-6 + waittime * 1e-3 / 255)
        end = time.time() + timeout
        while self.isMoving(axis) and time.time() <= end:
            time.sleep(0.001)
    
    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        self.addressSelection(self.address)
        self.tellStatus()
        reported_add = self.tellBoardAddress()
        if reported_add != self.address:
            logging.error("Failed to select controller " + str(self.address))
            return False
        st, err = self.tellStatus()
        if err:
            logging.error("Select Controller returned error " + str(err))
            return False
        if not (st & STATUS_BOARD_ADDRESSED):
            logging.error("Failed to select controller " + str(self.address) + ", status is " + str(st))
            return False
        
        logging.info("Selected controller %d.", self.address)
        
        version = self.versionReport()
        logging.info("Version: '%s'", version)
        
        commands = self.help()
        logging.info("Accepted commands: '%s'", commands)

        # try to modify the values to see if it would work
        self.setWaitTime(0, 1)
        st, err = self.tellStatus()
        if err:
            logging.error("SetWaitTime returned error %x", err)
            return False
        self.setStepSize(1, 255)
        st, err = self.tellStatus()
        if err:
            logging.error("SetStepSize returned error %x", err)
            return False
        self.setRepeatCounter(0, 10)
        st, err = self.tellStatus()
        if err:
            logging.error("SetRepeatCounter returned error %x", err)
            return False
        
        return True
        
    def convertMToDevice(self, m):
        """
        converts meters to the unit for this device (step duration).
        m (float): meters (can be negative)
        return (float): device units
        """
        duration = m / self.scale
        return duration
                
    @staticmethod
    def scan(port, max_add=15):
        """
        Scan the serial network for all the PI C-170 available.
        port (string): name of the serial port
        max_add (0<=int<=15): maximum address to scan
        return (set of (0<=int<=15)): set of addresses of available controllers
        Note: after the scan the selected device is unspecified
        """
        # TODO to speed up, we could try to send address selection and TB in burst
        # to all the range and then listen.
        ser = PIRedStone.openSerialPort(port)
        pi = PIRedStone(ser)
        
        logging.info("Serial network scanning in progress...")
        pi.try_recover = False # timeouts are expected!
        present = set([])
        for i in range(max_add + 1):
            # ask for controller #i
            logging.info("Querying address %d", i)
            pi.addressSelection(i)

            # is it answering?
            try:
                add = pi.tellBoardAddress()
                if add == i:
                    present.add(add)
                else:
                    logging.warning("asked for controller %d and was answered by controller %d.", i, add)
            except IOError:
                pass
        
        pi.try_recover = True
        return present
        
    @staticmethod
    def openSerialPort(port):
        """
        Opens the given serial port the right way for the PI-C170.
        port (string): the name of the serial port
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port = port,
            baudrate = 9600, # XXX: might be 19200 if switches are changed
            bytesize = serial.EIGHTBITS,
            parity = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout = 0.3 #s
        )
        
        # Currently selected one is unknown
        ser._pi_select = -1
        return ser
        
#TODO axis (channel) should be 0 or 1
class StageRedStone(Actuator):
    """
    An actuator made entirely of redstone controllers connected on the same serial port
    Can have an arbitrary number of axes (up to 32 in theory)
    """
    def __init__(self, name, role, children, port, axes):
        """
        port (string): name of the serial port to connect to the controllers
        axes (dict string=> 2-tuple): the configuration of the network.
         for each axis name the controller address and channel
         Note that even if it's made of several controllers, each controller is 
         _not_ seen as a child from the odemis model point of view.
        """ 
        Actuator.__init__(self, name, role, children)
        
        ser = PIRedStone.openSerialPort(port)
        
        # the axis names as required by Actuator
        self.axes = frozenset(axes.keys())
        
        # Not to be mistaken with axes which is a simple public view
        self._axes = {} # axis name => (PIRedStone, channel)
        self.ranges = {}
        # TODO also a rangesRel : min and max of a step 
        self._position = {}
        speed = {}
        controllers = {} # address => PIRedStone
        for axis, (add, channel) in axes.items():
            if not add in controllers:
                controllers[add] = PIRedStone(ser, add)
            controller = controllers[add]
            self._axes[axis] = (controller, channel)
            
            # TODO request also the ranges from the arguments?
            # For now we put very large one
            self.ranges[axis] = [0, 1] # m
            
            # TODO move to a known position (0,0) at init?
            # for now we have no idea where we are => in the middle so that we can always move
            self._position[axis] = 0.5 # m
            
            # Just to make sure it doesn't go too fast
            speed[axis] = 1 # m/s 
            
        # min/max speed is from using the opposite conversion formula with 256/2 
        self.speed = model.MultiSpeedProperty(speed, [7.6e-5, 3], "m/s")
        self.speed.subscribe(self.onSpeed, init=True)
        
    def getMetadata(self):
        # TODO
        metadata = {}
        metadata[model.MD_POS] = (self._position["x"], self._position["y"])
        return metadata
    
    def onSpeed(self, value):
        for axis, v in value.items():
            controller, channel = self._axes[axis]
            controller.setSpeed(v, channel)
            
    # to make it read-only
    @property
    def position(self):
        # TODO: position is optional, or is it really needed to simplify?
        # Used for the metadata of the picture
        return self._position
    
    def moveRel(self, shift):
        u"""
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        """
        # TODO check values are within range
        for axis, distance in shift.items():
            if axis not in self.axes:
                raise Exception("Axis unknown: " + str(axis))
            if abs(distance) > self.ranges[axis][1]:
                raise Exception("Trying to move axis %s by %f m> %f m." % 
                                (axis, distance, self.ranges[axis][1]))
            # TODO: wait for the previous move
            controller, channel = self._axes[axis]
            controller.moveRel(channel, controller.convertMToDevice(distance))
        
        # TODO future
    
    def stop(self, axis=None):
        """
        stops the motion
        axis (string): name of the axis to stop, or all of them if not indicated 
        """
        if not axis:
            for controller, channel in self._axes.values():
                controller.stopMotion(channel)
        else:
            controller, channel = self._axes[axis]
            controller.stopMotion(channel)
        
    def waitStop(self, axis=None):
        """
        wait until the stops the motion
        axis (string): name of the axis to stop, or all of them if not indicated 
        """
        if not axis:
            for controller, channel in self._axes.values():
                controller.waitEndMotion(channel)
        else:
            controller, channel = self._axes[axis]
            controller.waitEndMotion(channel)
            
    def selfTest(self):
        passed = True
        controllers = set([c for c, a in self._axes.values()])
        for controller in controllers:
            logging.info("Testing controller %d", controller.address)
            passed &= controller.selfTest()
        
        return passed
    
    @staticmethod
    def scan(port=None, max_add=15):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        max_add (0<=int<15): maximum address to be tried on each port. By default try everything possible.
        returns: a possible way to initialise the stage by using each controller as two axes
        Note: it is not possible to detect whether a controller has one or two axes (channel).
        """ 
        if port:
            ports = [port]
        else:
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range (0,8)]
            else:
                ports = glob.glob('/dev/ttyS?*') +  glob.glob('/dev/ttyUSB?*')
                
        found = []  # tuple of (name, dict (port=>port, axes =>list of addresses)
        for p in ports:
            try:
                addresses = PIRedStone.scan(p, max_add)
            except serial.SerialException:
                # not possible to use this port? next one!
                continue
            
            if addresses:
                arg = {}
                for add in addresses:
                    arg["axis" + str(add)] = (add, 0) # channel 0
                    arg["axis" + str(add)] = (add, 1) # channel 1
                found.append(("Actuator " + p, {"port": p, "axes": arg}))
        
        return found
    
    
    # Doer Thread:
    # Share a queue of actions with the interface
    # For each action in the queue: performs and wait until the action is finished
    # At the end of the action, call all the callbacks
    
    # Future:
    # Provides the interface for the clients to manipulate an (asynchronous) action 
    # they requested.
    # It follows http://docs.python.org/dev/library/concurrent.futures.html
    # Internally, it has a reference to the action in the action queue and to the
    # doer thread. 
    # cancel => if action still in the queue, remove it, Return True
    #           if action being under process. synchronously ask the doer thread to stop the move, return True
    #           if action is over, return False
    # cancelled => 
    # running() => 
    # done() => if action not in the queue or under process, return True
    #           otherwise return False
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: