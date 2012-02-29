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
import io
import re
import serial

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
#Bit 2: Pulse output on channel 1 (X)
#Bit 3: Pulse output on channel 2 (Y)
#Bit 4: Pulse delay in progress (X)
#Bit 5: Pulse delay in progress (Y)
STATUS_MOVING_X = 0x004000 #Bit 6: Is moving (X)
STATUS_MOVING_Y = 0x008000 #Bit 7: Is moving (Y)
# byte 3
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

VERBOSE = True

class PIRedStone(object):
    '''
    This represents the bare PI C-170 piezo motor controller (Redstone), the 
    information comes from the manual C-170_User_MS133E104.pdf. Note that this
    controller uses only native commands, which are different from the "PI GCS". 
    
    From the device description:
    The distance and velocity travelled corresponds to the width, frequency and 
    number of motor-on pulses. By varying the pulse width, the step length and
    thus the motor velocity can be controlled. As the mechanical environment
    also influences the motion, the size of single steps is not highly
    repeatable. For precise position control, a system with a position feedback
    device is recommended (closed-loop operation).
    Miniature-stages can achieve speeds of 500 mm/s and more with minimum
    incremental motion of 50 nm.
    
    The smallest step a piezo motor can make is typically on the order of 
    0.05 μm and corresponds to a 10 μs pulse (shorter pulses have no effect).

    In practice: if you give a too small duration to a step, it will not move 
    at all. In experiments, 50µs for duration of a pulse is the minimum that
    moves the axis (of about 500 nm). Note that it's not linear:
    50 µs  => 500nm
    255 µs => 5µm
    '''

    def __init__(self, ser, address=None):
        '''
        Initialise the given controller #id over the given serial port
        ser: a serial port
        address 0<int<15: the address of the controller as defined by its jumpers 1-4
        if no address is given, then no controller is selected
        '''
        # FIXME: use io.TextIOWrapper(io.BufferedRWPair(ser, ser))?
        # not sure it handles correctly \r\n\x03
        self.serial = ser
        #self.serial.timeout = 0.1 # s
        
        self.min_duration = 30 # µs minimum duration of a step to move
        self.scale = 2e-8 # m/µs very rough scale (if it was linear)
        
        self.address = address
        # allow to not initialise the controller (mostly for ScanNetwork())
        if address is None:
            return
        
        # Small check to verify it's responding
        self.select()
        try:
            add = self.tellBoardAddress()
            if add != address:
                print "Warning: asked for PI controller %d and was answered by controller %d." % (address, add)
        except IOError:
            raise IOError("No answer from PI controller %d" % address)

    def _sendSetCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \r if necessary)
        """
        assert(len(com) < 10)
        if VERBOSE:
            print com.encode('string_escape')
        self.serial.write(com)
        # TODO allow to check for error via TellStatus afterwards
    
    def _sendGetCommand(self, com, report_prefix=""):
        """
        Send a command and return its report
        com (string): the command to send
        report_prefix (string): the prefix to the report,
            it will be removed from the return value
        return (string): the report without prefix nor newline
        """
        assert(len(com) <= 10)
        assert(len(report_prefix) <= 2)
        self.serial.write(com)
        report = self.serial.readline() # get up to "\r\n"
        # TODO: add more lines until reading "\x03"
        report += self.serial.read(1) # get "\x03"
        if VERBOSE:
            print "%s" % report.encode('string_escape')
        if not report.startswith(report_prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (com, report))

        return report.lstrip(report_prefix).rstrip("\r\n\x03")
    
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
        return self._sendGetCommand("HE\r", "\x00\x00")
    
    def waitMotorStop(self, time=1):
        """
        Force the controller to wait until a burst is done before reading the 
        next command.
        time (1 <= int <= 65537): additional time to wait after the burst (ms)
        """
        assert((1 <= time) and (time <= 65537))
        self._sendSetCommand("WS%d\r" % time)
    
    def abortMotion(self):
        """
        Stops the running output pulse sequences started by GP or GN.
        """
        self._sendSetCommand("AB\r")

    def pulseOutput(self, axis, duration):
        """
        Outputs pulses of length duration on channel axis
        axis (int 1 or 2): the output channel
        duration (1<=int<=255): the duration of the pulse
        """
        assert((1 <= axis) and (axis <= 2))
        assert((1 <= duration) and (duration <= 255))
        self._sendSetCommand("%dCA%d\r" % (axis, duration))

    def setDirection(self, axis, direction):
        """
        Applies a static direction flag (positive or negative) to the axis. 
        axis (int 1 or 2): the output channel
        direction (int 0 or 1): 0=low(positive) and 1=high(negative)
        """
        assert((1 <= axis) and (axis <= 2))
        assert((0 <= direction) and (direction <= 1))
        self._sendSetCommand("%dCD%d\r" % (axis, direction))
        
    def goPositive(self, axis):
        """
        Used to execute a move in the positive direction as defined by
            the SS, SR and SW values.
        axis (int 1 or 2): the output channel
        """
        assert((1 <= axis) and (axis <= 2))
        self._sendSetCommand("%dGP\r" % axis)

    def goNegative(self, axis):
        """
        Used to execute a move in the negative direction as defined by
            the SS, SR and SW values.
        axis (int 1 or 2): the output channel
        """
        assert((1 <= axis) and (axis <= 2))
        self._sendSetCommand("%dGN\r" % axis)

    def setRepeatCounter(self, axis, repetitions):
        """
        Set the repeat counter for the given axis
        axis (int 1 or 2): the output channel
        repetitions (0<=int<=65535): the amount of repetitions
        """
        assert((1 <= axis) and (axis <= 2))
        assert((0 <= repetitions) and (repetitions <= 65535))
        self._sendSetCommand("%dSR%d\r" % (axis, repetitions))

    def setStepSize(self, axis, duration):
        """
        Set the step size that corresponds with the length of the output
            pulse for the given axis
        axis (int 1 or 2): the output channel
        duration (0<=int<=255): the length of pulse in μs
        """
        assert((1 <= axis) and (axis <= 2))
        #assert((1 <= duration) and (duration <= 255)) # XXX
        self._sendSetCommand("%dSS%d\r" % (axis, duration))


    def setWaitTime(self, axis, duration):
        """
        This command sets the delay time (wait) between the output of pulses when
            commanding a burst move for the given axis.
        axis (int 1 or 2): the output channel
        duration (0<=int<=65535): the wait time (ms), 1 gives the highest output frequency.
        """
        assert((1 <= axis) and (axis <= 2))
        assert((1 <= duration) and (duration <= 65535))
        self._sendSetCommand("%dSW%d\r" % (axis, duration))

    
    # TODO: is there something to do to activate the "CW mode" for high acceleration?
    # CW = continuous wave?
    
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
        axis (int 1 or 2): the output channel
        duration (-255<=int<=255): the duration of pulse in μs,
                                   negative to go negative direction
        """
        assert((1 <= axis) and (axis <= 2))
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
        axis (int 1 or 2): the output channel
        duration (int): the duration of pulse in μs 
        """
        assert((1 <= axis) and (axis <= 2))
        if duration == 0:
            return

        self.select()
        steps, left = divmod(abs(duration), 255)
        sign = cmp(duration, 0)
        
        # we can only ask 65535 repetitions at most
        # Bigger values would be unrealistic, so just clamp
        if abs(steps) > 65536:
            steps = 65536
            left = 0
        
        # Run the main length
        if steps > 0:
            self.setWaitTime(axis, 1) # as fast as possible
            self.setStepSize(axis, 255) # big steps
            self.setRepeatCounter(axis, round(steps - 1))
            if duration > 0:
                self.goPositive(axis)
            else:
                self.goNegative(axis)
            
        # TODO use the same commands
        # Finish with the small left over
        self.moveRelSmall(axis, sign * left)
    
    def isMoving(self, axis=None):
        """
        Indicate whether the motors are moving. 
        axis (None, 1, or 2): axis to check whether it is moving, or both if None
        return (boolean): True if moving, False otherwise
        """
        self.select()
        (st, err) = self.tellStatus()
        if axis == 1:
            mask = STATUS_MOVING_X
        elif axis == 2:
            mask = STATUS_MOVING_Y
        else:
            mask = STATUS_MOVING_X | STATUS_MOVING_Y
        
        return bool(st & mask)
    
    def stopMotion(self, axis):
        """
        Stop the motion of all the given axis.
        For the Redstone, both axes are stopped simultaneously
        """
        self.select()
        self.abortMotion()
          
    def waitEndMotion(self, axis):
        """
        Stop the motion of all the given axis.
        For the Redstone, both axes are stopped simultaneously
        """
        # FIXME: unlikely to work!
        self.select()
        self.waitMotorStop()
        self.tellBoardAddress() # we are not interested by the address, just a report
        
    def scanNetwork(self, max_add=15):
        """
        Scan the serial network for all the PI C-170 available.
        max_add (0<=int<=15): maximum address to scan
        return (set of (0<=int<=15)): set of addresses of available controllers
        Note: after the scan the selected device is unspecified
        """
        # TODO see MRC_initNetwork, which takes 400ms per address
        
        # TODO to speed up, we could try to send address selection and TB in burst
        # to all the range and then listen.
        
        print "Serial network scanning in progress..."
        present = set([])
        for i in range(max_add + 1):
            # ask for controller #i
            print "Querying address " + str(i)
            self.addressSelection(i)

            # is it answering?
            try:
                add = self.tellBoardAddress()
                if add == i:
                    present.add(add)
                else:
                    print "Warning: asked for controller %d and was answered by controller %d." % (i, add)
            except IOError:
                pass
        
        return present
    
    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        self.addressSelection(self.address)
        reported_add = self.tellBoardAddress()
        if reported_add != self.address:
            print("Failed to select controller " + str(self.address))
            return False
        st, err = self.tellStatus()
        if err:
            print("Select Controller returned error " + str(err))
            return False
        if not (st & STATUS_BOARD_ADDRESSED):
            print("Failed to select controller " + str(self.address) + ", status is " + str(st))
            return False
        
        print "Selected controller %d." % self.address
        
        version = self.versionReport()
        print("Version: '%s'" % version)
        
        commands = self.help()
        print("Accepted commands: '%s'" % commands)

        # try to modify the values to see if it would work
        self.setWaitTime(1, 1)
        st, err = self.tellStatus()
        if err:
            print("SetWaitTime returned error " + str(err))
            return False
        self.setStepSize(2, 255)
        st, err = self.tellStatus()
        if err:
            print("SetStepSize returned error " + str(err))
            return False
        self.setRepeatCounter(1, 10)
        st, err = self.tellStatus()
        if err:
            print("SetRepeatCounter returned error " + str(err))
            return False
        
        return True
        
    def convertMToDevice(self, m):
        """
        converts meters to the unit for this device (step duration).
        m (float): meters (can be negative)
        return (float): device units
        """
        if m == 0: # already handled, but make it more explicit
            return 0
        
        duration = m / self.scale
        if abs(duration) < self.min_duration:
            duration = cmp(duration, 0) * self.min_duration # cmp == sign
        return duration
        
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
            timeout = 1 #s
        )
        
        # Currently selected one is unknown
        ser._pi_select = -1
        return ser
        
    
# FIXME: Move to more generic place than PI?
class Stage(object):
    """
    An object representing a stage = a set of axes that can be moved and
    optionally report their position. 
    """

    def __init__(self):
        """
        Constructor
        """ 
        self.axes = {} # dict of axes
        
    def canAbsolute(self, axis):
        """
        report whether an axis can do absolute positioning (and report position)
        or only relative.
        axis (string): the axis name
        return (boolean): True if the controller supports absolute positioning
        """
        return "moveAbs" in dir(self.axes[axis][0])
        
    def moveRel(self, shift, sync=False):
        u"""
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        """
        # TODO check values are within range
        for axis, distance in shift.items():
            if axis not in self.axes:
                raise Exception("Axis unknown: " + str(axis))
            controller, arg = self.axes[axis]
            print distance, "=", controller.convertMToDevice(distance)
            controller.moveRel(arg, controller.convertMToDevice(distance))
                 
        # wait until every motor is finished if requested
        if not sync:
            return
        
        for axis in shift:
            controller, arg = self.axes[axis]
            controller.waitEndMotion(arg)
            
    def moveAbs(self, pos, sync=False):
        u"""
        Move the stage to the defined position in m for each axis given.
        pos dict(string-> float): name of the axis and position in m
        sync (boolean): whether the moves should be done asynchronously or the 
        method should return only when all the moves are over (sync=True)
        """
        # TODO what's the origin? => need a different conversion?
        # TODO check values are within range
        for axis, distance in pos.items():
            if axis not in self.axes:
                raise Exception("Axis unknown: " + str(axis))
            controller, arg = self.axes[axis]
            controller.moveAbs(arg, controller.convertMToDevice(distance))
        
        # wait until every motor is finished if requested
        if not sync:
            return
        
        for axis in pos:
            controller, arg = self.axes[axis]
            controller.waitEndMotion(arg)
    
    # TODO need a 'report position' and a 'calibrate' for the absolute axes 
    
    def stopMotion(self, axis = None):
        """
        stops the motion
        axis (string): name of the axis to stop, or all of them if not indicated 
        """
        if not axis:
            for controller, arg in self.axes.values():
                controller.stopMotion(arg)
        else:
            controller, arg = self.axes[axis]
            controller.stopMotion(arg)
        
    def waitStop(self, axis = None):
        """
        wait until the stops the motion
        axis (string): name of the axis to stop, or all of them if not indicated 
        """
        if not axis:
            for controller, arg in self.axes.values():
                controller.stopMotion(arg)
        else:
            controller, arg = self.axes[axis]
            controller.stopMotion(arg)
        
class StageSECOM(Stage):
    """
    The SECOM has two Redstone controllers, each controlling one axis of the stage.
    """
    
    def __init__(self, port):
        """
        port (string): name of the serial port to connect to the controllers
        """ 
        Stage.__init__(self)
        
        ser = PIRedStone.openSerialPort(port)
        
        red1 = PIRedStone(ser, 1)
        red2 = PIRedStone(ser, 2)
        #     Axis name  (controller, arg)
        self.axes['x'] = (red1, 1) # add 1/channel 1
        self.axes['y'] = (red2, 1) # add 2/channel 1
        
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: