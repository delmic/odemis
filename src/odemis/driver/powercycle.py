# -*- coding: utf-8 -*-
'''
Created on 1 Apr 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

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

import glob
import logging
from odemis import model
from odemis.model import HwError
import os
import serial
import sys
import threading
import time


class Relay(model.HwComponent):
    '''
    This is a component strictly used for the SPARC system in Monash. It is in
    charge of sending a high/low signal via an ARM microcontroller to the relay,
    pulling/pushing the relay contact and thus controls the power supply from 
    the power board to the flippers and filter wheel. 
    '''
    def __init__(self, name, role, sn=None, port=None, **kwargs):
        '''
        sn (str): serial number (recommended)
        port (str): port name (only if sn is not specified)
        Raise an exception if the device cannot be opened
        '''
        model.HwComponent.__init__(self, name, role, **kwargs)

        if (sn is None and port is None) or (sn is not None and port is not None):
            raise ValueError("sn or port argument must be specified (but not both)")
        if sn is not None:
            self._port = self._getSerialPort(sn)
        else:
            self._port = port

        # TODO: catch errors and convert to HwError
        self._serial = self._openSerialPort(self._port)
        self._ser_access = threading.Lock()

        # When True, the relay contact is connected
        self.contact = model.BooleanVA(True, setter=self._setContact)
        self._setContact(True)

    def terminate(self):
        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    def _setContact(self, value):
        if value:
            self._sendCommand("RELAY 0")
        else:
            self._sendCommand("RELAY 1")

        return value

    def _getContact(self):
        ans = self._sendCommand("RELAY?")
        if ans == "0":
            status = True
        else:
            status = False

        return status

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to ARM microcontroller.
        returns 
                ans (str): answer received from the ARM microcontroller.
        raises    
                IOError: if an ERROR is returned by the ARM microcontroller.
        """
        cmd = cmd + "\n"
        with self._ser_access:
            self._serial.write(cmd)

            ans = ''
            char = None
            while (char != '\n'):
                char = self._serial.read(1)
                # Handle ERROR coming from ARM microcontroller
                ans += char

            if ans.startswith("ERROR"):
                raise ARMError(ans.split(' ', 1)[1])

            return ans.rstrip()

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for an ARM microcontroller.
        port (string): the name of the serial port (e.g., /dev/ttyACM0)
        return (serial): the opened serial port
        """
        # For debugging purpose
        if port == "/dev/fake":
           return ARMSimulator(timeout=1)

        ser = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=1  # s
        )

        # Purge (as recommended in the documentation)
        time.sleep(0.05)  # 50 ms
        ser.flush()
        ser.flushInput()
        time.sleep(0.05)  # 50 ms

        # Prepare the port
        ser.setRTS()

        return ser

    def _getSerialPort(self, sn):
        """
        sn (str): serial number of the device
        return (str): serial port name (eg: "/dev/ttyACM0" on Linux)
        """
        if sys.platform.startswith('linux'):
            # Look for each USB device, if the serial number is good
            sn_paths = glob.glob('/sys/bus/usb/devices/*/serial')
            for p in sn_paths:
                try:
                    f = open(p)
                    snp = f.read().strip()
                except IOError:
                    logging.debug("Failed to read %s, skipping device", p)
                if snp == sn:
                    break
            else:
                raise HwError("No ACM device with S/N %s. "
                              "Check that the ARM microcontroller is "
                              "connected to the host computer." % sn)

            # Deduce the tty:
            # .../3-1.2/serial => .../3-1.2/3-1.2:1.0/ttyUSB1
            sys_path = os.path.dirname(p)
            usb_num = os.path.basename(sys_path)
            tty_paths = glob.glob("%s/%s/tty/ttyACM?*" % (sys_path, usb_num + ":1.0"))
            if not tty_paths:
                raise ValueError("Failed to find tty for device with S/N %s" % sn)
            tty = os.path.basename(tty_paths[0])

            # Convert to /dev
            # Note: that works because udev rules create a dev with the same name
            # otherwise, we would need to check the char numbers
            return "/dev/%s" % (tty,)
        else:
            # TODO: Windows version
            raise NotImplementedError("OS not yet supported")

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, args (sn)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        logging.info("Serial ports scanning for ARM microcontroller in progress...")
        found = []  # (list of 2-tuple): name, kwargs

        if sys.platform.startswith('linux'):
            # Look for each USB device, if the serial number is potentially good
            sn_paths = glob.glob('/sys/bus/usb/devices/*/serial')
            for p in sn_paths:
                try:
                    f = open(p)
                    snp = f.read().strip()
                except IOError:
                    logging.debug("Failed to read %s, skipping device", p)

                # Deduce the tty:
                # .../3-1.2/serial => .../3-1.2/3-1.2:1.0/ttyUSB1
                sys_path = os.path.dirname(p)
                usb_num = os.path.basename(sys_path)
                logging.info("Looking at device %s with S/N=%s", usb_num, snp)
                tty_paths = glob.glob("%s/%s/tty/ttyACM?*" % (sys_path, usb_num + ":1.0"))
                if not tty_paths:  # 0 or 1 paths
                    continue
                tty = os.path.basename(tty_paths[0])

                # Convert to /dev
                # Note: that works because udev rules create a dev with the same name
                # otherwise, we would need to check the char numbers
                port = "/dev/%s" % (tty,)

                # open and try to communicate
                try:
                    dev = cls(name="test", role="test", port=port)
                    found.append({"sn": snp})
                except Exception:
                    pass
        else:
            # TODO: Windows version
            raise NotImplementedError("OS not yet supported")

        return found
    

class ARMError(IOError):
    """
    Exception used to indicate a problem coming from the ARM microcontroller.
    """
    pass


class ARMSimulator(object):
    """
    Simulates an ARM microcontroller (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        self.timeout = timeout
        self._output_buf = ""  # what the ARM sends back to the "host computer"
        self._input_buf = ""  # what ARM receives from the "host computer"

        # internal values
        self._sn = 37000002
        self._contact = True

    def write(self, data):
        self._input_buf += data

        self._parseMessages()  # will update _input_buf

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
        self._output_buf = ""

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _parseMessages(self):
        """
        Parse as many messages available in the buffer
        """
        while len(self._input_buf) >= 1:
            # read until '\n'
            sep = self._input_buf.index('\n')
            msg = self._input_buf[0:sep + 1]

            # remove the bytes we've just read
            self._input_buf = self._input_buf[len(msg):]

            self._processMessage(msg)

    def _processMessage(self, msg):
        """
        process the msg, and put the result in the output buffer
        msg (str): raw message (including header)
        """
        res = None
        wspaces = msg.count(' ')
        qmarks = msg.count('?')
        tokens = msg.split()
        if ((wspaces > 0) and (qmarks > 0)) or (wspaces > 1) or (qmarks > 1):
            res = "ERROR: Cannot parse this command\n"
        elif wspaces:
            value = float(tokens[1])
            if tokens[0] == "RELAY":
                if (value != 0) and (value != 1):
                    res = "ERROR: Out of range set value\n"
                else:
                    if value:
                        self._contact = False
                    else:
                        self._contact = True
                    res = '\n'
            else:
                res = "ERROR: Cannot parse this command\n"
        elif qmarks:
            if tokens[0] == "RELAY?":
                if self._contact:
                    res = "0" + '\n'
                else:
                    res = "1" + '\n'
            else:
                res = "ERROR: Cannot parse this command\n"
        else:
            res = "ERROR: Cannot parse this command\n"

        # add the response end
        if res is not None:
            self._output_buf += res
