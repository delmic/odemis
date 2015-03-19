# -*- coding: utf-8 -*-
'''
Created on 13 Mar 2015

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

# Min and Max gain values in V
MAX_GAIN = 6
MIN_GAIN = 0


class PMTControl(model.HwComponent):
    '''
    This represents the PMT control unit.
    '''
    def __init__(self, name, role, sn=None, port=None, prot_time=None, prot_curr=None, daemon=None, **kwargs):
        '''
        sn (str): serial number (recommended)
        port (str): port name (only if sn is not specified)
        prot_time (float): protection trip time
        prot_curr (float): protection current threshold
        Raise an exception if the device cannot be opened
        '''
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        # get protection time (s) and current (A) properties
        self._prot_time = prot_time
        self._prot_curr = prot_curr

        if (sn is None and port is None) or (sn is not None and port is not None):
            raise ValueError("sn or port argument must be specified (but not both)")
        if sn is not None:
            self._port = self._getSerialPort(sn)
        else:
            self._port = port

        self._serial = self._openSerialPort(self._port)
        self._ser_access = threading.Lock()

        try:
            # Get identification of the PMT control device
            self._idn = self._sendCommand("*IDN?\n")
            # Set protection current and time
            if self._prot_curr is not None:
                self._sendCommand("PCURR " + str(self._prot_curr) + "\n")
            if self._prot_time is not None:
                self._sendCommand("PTIME " + str(self._prot_time) + "\n")
        except IOError as e:
            logging.exception(str(e))

        # gain, powerSupply and protection VAs
        gain_rng = [MIN_GAIN, MAX_GAIN]
        self._gain = 0
        self.gain = model.FloatContinuous(self._gain, gain_rng, unit="V", setter=self._setGain)
        # To initialize the voltage in the PMT control unit
        self.gain.value = self._gain  # Just start with no gain
        self.powerSupply = model.BooleanVA(False, setter=self._setPowerSupply)
        self.powerSupply.value = False
        self.protection = model.BooleanVA(False, setter=self._setProtection, getter=self._getProtection)
        self.protection.value = False

    def terminate(self):
        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    def _setGain(self, value):
        try:
            self._sendCommand("VOLT " + str(value) + "\n")
        except IOError as e:
            logging.exception(str(e))

        return value

    def _setPowerSupply(self, value):
        try:
            if value:
                self._sendCommand("PWR " + str(1) + "\n")
            else:
                self._sendCommand("PWR " + str(0) + "\n")
        except IOError as e:
            logging.exception(str(e))

        return value

    def _getPowerSupply(self):
        try:
            ans = self._sendCommand("PWR?\n")
            if ans == "1\r":
                status = True
            else:
                status = False
        except IOError as e:
            logging.exception(str(e))

        return status

    def _setProtection(self, value):
        try:
            if value:
                self._sendCommand("PROT " + str(1) + "\n")
            else:
                self._sendCommand("PROT " + str(0) + "\n")
        except IOError as e:
            logging.exception(str(e))

        return value

    def _getProtection(self):
        try:
            ans = self._sendCommand("PROT?\n")
            if ans == "1\r":
                status = True
            else:
                status = False
        except IOError as e:
            logging.exception(str(e))

        return status

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to PMT Control unit.
        returns 
                ans (str): answer received from the PMT Control unit
        raises    
                IOError: if an ERROR is returned by the PMT Control firmware.
        """
        with self._ser_access:
            self._serial.write(cmd)
            ans = ''
            # let's wait one second before reading output (let's give device time to answer)
            char = None
            while (char != '\r'):
                char = self._serial.read(1)
                # Handle ERROR coming from PMT control unit firmware
                if char == '\n':
                    raise IOError(ans.split(' ', 1)[1])
                ans += char

            return ans

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a PMT control device.
        port (string): the name of the serial port (e.g., /dev/ttyACM0)
        return (serial): the opened serial port
        """
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
                              "Check that the PMT control device is "
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
        logging.info("Serial ports scanning for PMT control device in progress...")
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
    
#Ranges similar to real PMT Control firmware
MAX_VOLT = 6
MIN_VOLT = 0
MAX_PCURR = 3
MIN_PCURR = 0
MAX_PTIME = 100
MIN_PTIME = 0.000001
IDN = "Delmic Analog PMT simulator"
SN = "F4K3"

class PMTControlSimulator(model.HwComponent):
    """
    Simulates a PMTControl (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, name, role, sn=None, port=None, prot_time=None, prot_curr=None, daemon=None, **kwargs):

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)
        # get protection time (s) and current (A) properties
        self._prot_time = prot_time
        self._prot_curr = prot_curr

        try:
            # Get identification of the PMT control device
            self._idn = self._sendCommand("*IDN?\n")
            # Set protection current and time
            if self._prot_curr is not None:
                self._sendCommand("PCURR " + str(self._prot_curr) + "\n")
            if self._prot_time is not None:
                self._sendCommand("PTIME " + str(self._prot_time) + "\n")
        except IOError as e:
            logging.exception(str(e))
        # gain, powerSupply and protection VAs
        gain_rng = [MIN_GAIN, MAX_GAIN]
        self._gain = 0
        self._powerSupply = False
        self._protection = False
        self.gain = model.FloatContinuous(self._gain, gain_rng, unit="V", setter=self._setGain)
        # To initialize the voltage in the PMT control unit
        self.gain.value = 0  # Just start with no gain
        self.powerSupply = model.BooleanVA(False, setter=self._setPowerSupply)
        self.powerSupply.value = False
        self.protection = model.BooleanVA(False, setter=self._setProtection, getter=self._getProtection)
        self.protection.value = False

    def _setGain(self, value):
        try:
            self._sendCommand("VOLT " + str(value) + "\n")
        except IOError as e:
            logging.exception(str(e))

        return self._gain

    def _setPowerSupply(self, value):
        try:
            if value:
                self._sendCommand("PWR " + str(1) + "\n")
            else:
                self._sendCommand("PWR " + str(0) + "\n")
        except IOError as e:
            logging.exception(str(e))

        return self._powerSupply

    def _getPowerSupply(self):
        try:
            ans = self._sendCommand("PWR?\n")
            if ans == "1\r":
                status = True
            else:
                status = False
        except IOError as e:
            logging.exception(str(e))

        return status

    def _setProtection(self, value):
        try:
            if value:
                self._sendCommand("PROT " + str(1) + "\n")
            else:
                self._sendCommand("PROT " + str(0) + "\n")
        except IOError as e:
            logging.exception(str(e))

        return self._protection

    def _getProtection(self):
        try:
            ans = self._sendCommand("PROT?\n")
            if ans == "1\r":
                status = True
            else:
                status = False
        except IOError as e:
            logging.exception(str(e))

        return status

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to PMT Control unit.
        """
        wspaces = cmd.count(' ')
        qmarks = cmd.count('?')
        tokens = cmd.split()
        if ((wspaces > 0) and (qmarks > 0)) or (wspaces > 1) or (qmarks > 1):
            raise IOError("Cannot parse this command")
        elif wspaces:
            value = float(tokens[1])
            if tokens[0] == "PWR":
                if (value != 0) and (value != 1):
                    raise IOError("Out of range set value")
                else:
                    if value:
                        self._powerSupply = True
                    else:
                        self._powerSupply = False
                    return '\r'

            elif tokens[0] == "PROT":
                if (value != 0) and (value != 1):
                    raise IOError("Out of range set value")
                else:
                    if value:
                        self._protection = True
                    else:
                        self._protection = False
                    return '\r'
            elif tokens[0] == "VOLT":
                if (value < MIN_VOLT) or (value > MAX_VOLT):
                    raise IOError("Out of range set value")
                else:
                    self._gain = value
                    return '\r'
            elif tokens[0] == "PCURR":
                if (value < MIN_PCURR) or (value > MAX_PCURR):
                    raise IOError("Out of range set value")
                else:
                    self._prot_curr = value
                    return '\r'
            elif tokens[0] == "PTIME":
                if (value < MIN_PTIME) or (value > MAX_PTIME):
                    raise IOError("Out of range set value")
                else:
                    self._prot_time = value
                    return '\r'
            else:
                raise IOError("Cannot parse this command")
        elif qmarks:
            if tokens[0] == "*IDN?":
                return IDN + '\r'
            elif tokens[0] == "PWR?":
                if self._powerSupply:
                    return "1" + '\r'
                else:
                    return "0" + '\r'
            elif tokens[0] == "VOLT?":
                return str(self._gain) + '\r'
            elif tokens[0] == "PCURR?":
                return str(self._prot_curr) + '\r'
            elif tokens[0] == "PTIME?":
                return str(self._prot_time) + '\r'
            elif tokens[0] == "PROT?":
                if self._protection:
                    return "1" + '\r'
                else:
                    return "0" + '\r'
            else:
                raise IOError("Cannot parse this command")
        else:
            raise IOError("Cannot parse this command")

    @classmethod
    def scan(cls):
        """
        returns the fake device.
        """
        found = []
        found.append({"sn": SN})

        return found
