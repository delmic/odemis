# -*- coding: utf-8 -*-
'''
Created on 7 Jan 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division

from Pyro4.core import isasync
import glob
import logging
from odemis import model
import odemis
from odemis.util import driver
import os
import re
import serial
import threading


class HwError(Exception):
    """
    Represents an error reported by the hardware
    """
    pass

class fw102c(model.Actuator):
    """
    Represents a Thorlabs filter wheel FW102C as an actuator.
    It provides one enumerated axis, whose actual band values are provided by
    the user at init.
    """
    
    # Regex matching the compatible identification strings
    re_idn = "THORLABS.*FW102C.*"
    def __init__(self, name, role, port, bands, **kwargs):
        """
        port (string): serial port to use
        bands (dict 1<=int<=6 -> 2-tuple of floats > 0):
          filter position -> lower and higher bound of the wavelength (m) of the
          light which goes _through_. If it's a list, it implies that the filter
          is multi-band.
        raise IOError if no device answering or not a compatible device
        """
        # TODO: accept a regex as port, and each port will be scan till the
        # first compatible device is found. cf omicronxx
        self.port = port
        self._serial = self._openSerialPort(port)
        self._ser_access = threading.Lock()
        self._flushInput() # can have some \x00 bytes at the beginning

        idn = self.GetIdentification()
        if not re.match(self.re_idn, idn):
            raise IOError("Device on port %s is not a FW102C (reported: %s)" %
                          (port, idn))

        # TODO: bands


        model.Actuator.__init__(self, name, role, axes=axes, **kwargs)
        
        driver_name = driver.getSerialDriver(port)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = idn

    def terminate(self):
        with self._ser_access:
            self._serial.close()
            self._serial = None

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for the FW102C.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=115200, # only correct if setting was not changed
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1 #s
        )

        return ser

    def _flushInput(self):
        """
        Ensure there is no more data queued to be read on the bus (=serial port)
        """
        with self._ser_access:
            self._serial.flush()
            self._serial.flushInput()

            # Shouldn't be necessary, but just in case
            while True:
                char = self._serial.read()
                if not char:
                    break
                logging.info("Skipping input %s", char)

    re_err = r"Command error (.*)"
    def _sendQuery(self, com):
        """
        Send a command which expects an answer
        com (string): command to send (not including the ? and the \r)
        return (string): the answer without newline and suffix ("> ")
        raises
            IOError: if there is a timeout
            HwError: if the hardware reports an error 
        """
        assert(len(com) <= 50) # commands cannot be long
        full_com = com + "\r"
        with self._ser_access:
            logging.debug("Sending: '%s'", full_com.encode('string_escape'))
            self._serial.write(full_com)
    
            # ensure everything is received, before expecting an answer
            self._serial.flush()
    
            # Read until end of answer
            line = b""
            while True:
                char = self._serial.read() # empty if timeout
                if not char: # should always finish by a "> "
                    raise IOError("Controller timeout, after receiving %s" % line)
    
                # normal char
                line += char
                if line[-2:] == "> ":
                    break

            logging.debug("Received: '%s'", line.encode('string_escape'))

        # remove echo + suffix + new line
        line = line[len(full_com):-2].rstrip("\r")
        
        # if it's an error message => raise an error
        m = re.match(self.re_err, line)
        if m:
            err = m.group(1)
            raise HwError("Device rejected command '%s': %s" % (com, err))
        
        return line

    def _sendCommand(self, com):
        """
        Send a command which does not expect any answer
        com (string): command to send (not including the ? and the \r)
        return when the command is finished processed
        raises
            IOError: if there is a timeout
            HwError: if the hardware reports an error
        """
        self._sendQuery(com)
        # don't return anything
    
    def GetIdentification(self):
        """
        return (str): model name as reported by the device
        """
        # answer is like "THORLABS FW102C/FW212C Filter Wheel version 1.04"
        return self._sendQuery("*idn?")

    def GetMaxPositions(self):
        """
        return (1<int): maximum number of positions available (eg, 6, 12)
        """
        ans = self._sendQuery("pcount?")
        return int(ans)

    def GetPosition(self):
        """
        return (1<=int<=6): current position
        Note: might be different from the last position set if the user has
         manually changed it.
        """
        ans = self._sendQuery("pos?")
        return int(ans)

    # What we don't need:
    # speed?\r1\r>
    # trig?\r0\r>
    # sensors?\r0\r>

    @isasync
    def moveRel(self, shift):
        logging.warning("Relative move is not advised for enumerated axes")
        pass # TODO
        
    @isasync
    def moveAbs(self, pos):
        pass # TODO
    
    def stop(self, axes=None):
        pass # TODO

    
# TODO: Emulator
