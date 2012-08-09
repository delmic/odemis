# -*- coding: utf-8 -*-
'''
Created on 7 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import logging
import serial

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
    def __init__(self, ser, address=None):
        """
        ser: a serial port (opened)
        address 1<int<16: address as configured on the controller
        If not address is given, it just allows to do some raw commands
        """
        self.serial = ser
        self.address = address
        # did the user asked for a raw access only?
        if address is None:
            return
        
        self._channels = set([]) # available channels (=axes)
    
    
    def _sendOrderCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \n if necessary)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: %s", full_com.encode('string_escape'))
        self.serial.write(full_com)
        
    def _sendQueryCommand(self, com):
        """
        Send a command and return its report (first line sent)
        com (string): the command to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix 
           (e.g.,"0 1") nor newline. If multiline: returns a list of each line 
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        full_com = "%d %s" % (self.address, com)
        logging.debug("Sending: %s", full_com.encode('string_escape'))
        self.serial.write(full_com)
        
        # TODO see if it's really necessary to have multiline: it should just
        # keep reading if it's " \n"
        
        char = self.serial.read() # empty if timeout
        line = ""
        lines = []
        while char:
            if char == "\n":
                if len(line) > 0 and line[-1] == " ":
                    # multiline
                    lines.append(line[:-1])# don't include the space
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
            # TODO try to recover (RBT) and resend the command
            raise IOError("controller %d timeout, not recovered." % self.address)
        
        assert len(lines) > 0
            
        logging.debug("Receive: %s", "\n".join(lines).encode('string_escape'))
        prefix = "0 %d " % self.address
        if not lines[0].startswith(prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (full_com, lines[0]))
        lines[0] = lines[0][len(prefix):]

        if len(lines) == 1:
            return lines[0]
        else:
            return lines
    
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
        #SAI? (Get List Of Current Axis Identifiers)
        #SAI? ALL: list all axes (included disabled ones)
        axes = self._sendQueryCommand("SAI? ALL\n")
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

    def GetRecoderConfig(self):
        """
        you don't need this
        """
        #DRC? (get Data Recorder Configuration)
        return self._sendQueryCommand("DRC?\n")
    
    def hasLimitSwitches(self, axis):
        """
        Report where the given axis has limit switches (is able to detect 
         the end of the axis.
        axis (1<int<16): axis number
        """
        assert((1 <= axis) and (axis <= 16))
        
        #LIM? (Indicate Limit Switches)
        #Seems to just read a configuration value in flash memory. Can be configured
        #easily with PIMikroMove
        answer = self._sendQueryCommand("LIM? %d\n" % axis)
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
    
    # "\x07" (Request Controller Ready Status)

    
    def GetErrorNum(self):
        """
        return (int): the error number (can be negative) of last error
        See p.192 of manual for the error codes
        """
        #ERR? (Get Error Number): get error code of last error
        answer = self._sendQueryCommand("ERR?\n")
        error = int(answer, 10)
        return error

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

    def Stop(self):
        """
        Stop immediately motion on all axes
        """
        #STP = "\x24" (Stop All Axes): immediate stop (high deceleration != HLT)
        self._sendOrderCommand("\x24")

    def SetServo(self, axis, activated):
        """
        Activate or de-activate the servo
        axis (1<int<16): axis number
        activated (boolean): True if the servo should be activated (closed-loop)
        """
        #SVO (Set Servo State)
        assert(axis in self._channels)
        
        if activated:
            state = 1
        else:
            state = 0
        self._sendOrderCommand("SVO %d %d\n" % (axis, state))

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
        self._sendOrderCommand("OSM %d %d\n" % (axis, steps))
        
        
#
#
#OAD (Open-Loop Analog Driving): move using analog
#The allowable range is between -55 and 55. Float
#
#
#SSA (Set Step Amplitude) : for nanostepping 
#between 0 and 55. Float
#=> just use the max and modify the velocity
#
#OVL (Set Open-Loop Velocity)
#in step-cycles/s
#OAC (Set Open-Loop Acceleration)
#in step-cycles/s²
#ODC (Set Open-Loop Deceleration)
#in step-cycles/s²
#
#




      
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
            logging.info("Querying address %d", i)

            # is it answering?
            try:
                ctrl.address = i
                version = ctrl.GetIdentification()
                present[i] = (version, ctrl.GetSyntaxVersion(), ctrl.GetAxes(), ctrl.hasLimitSwitches(1))
                print ctrl.GetAvailableCommands()
                print ctrl.GetAvailableParameters()
                print ctrl.GetRecoderConfig()
                print ctrl.GetMotionStatus()
                print ctrl.GetStatus()
                print ctrl.GetErrorNum()
                
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
    
addresses = Controller.scan("/dev/ttyUSB1", max_add=1)
print addresses

#

#
#Abs (with sensor = closed-loop):
#MOV (Set Target Position)
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
#
#OMR (Relative Open-Loop Motion)
#
#OMA (Absolute Open-Loop Motion)
#
#
#Example:
#SVO 1 0
#OAD 1 40
#RNP 1
#SSA 1 40
#OSM 1 100
#OMA 1 6
#OMR 1 -3
#RNP 1
#OAD 1 -40
