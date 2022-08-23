# -*- coding: utf-8 -*-
'''
Created on 25 Nov 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import fcntl
import glob
import logging
from odemis import model
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
from odemis.util import driver, to_str_escape
import os
import serial
import threading


class DPSSError(Exception):
    """
    Exception used to indicate a problem reported by the device.
    """
    pass


class DPSS(model.PowerSupplier):
    '''
    Implements the PowerSupplier class to regulate the power supply of the
    Cobolt DPSS laser, connected via USB.
    '''

    def __init__(self, name, role, port, light_name, max_power, **kwargs):
        '''
        port (str): port name. Can be a pattern, in which case it will pick the
          first one which responds well
        ligth_name (str): the name of the component that is controlled by this
          power supplier
        max_power (float): maximum power, in W. Will be set at initialisation.
        '''
        # TODO: allow to pass the serial number, to select the right device
        model.PowerSupplier.__init__(self, name, role, **kwargs)

        self._light_name = light_name
        self._ser_access = threading.Lock()
        self._port = self._findDevice(port)  # sets ._serial
        logging.info("Found Cobolt DPSS device on port %s", self._port)

        self._sn = self.GetSerialNumber()

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "serial driver: %s" % (driver_name,)
        self._hwVersion = "Cobolt DPSS (s/n: %s)" % (self._sn,)

        # Reset sequence
        # TODO: do a proper one. For now it's just everything we can throw, so
        # that it's a bit easier to debug
        self._sendCommand("ilk?")
        self._sendCommand("leds?")
        self._sendCommand("@cobasky?")
        self._sendCommand("cf")  # Clear fault
        # self._sendCommand("@cob1") # used to force the laser on after interlock opened error

        # will take care of executing switch asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # Dict str -> bool: component name -> turn on/off
        self.supplied = model.VigilantAttribute({light_name: False}, readonly=True)
        self._updateSupplied()

        self.SetOutputPower(max_power)

    # Wrapper for the actual firmware functions
    def GetSerialNumber(self):
        return self._sendCommand("sn?")

    def SetOutputPower(self, p):
        """
        p (0 < float): power in W
        """
        assert 1e-6 < p < 1e6
        self._sendCommand("p %.5f" % p)

    def SetLaser(self, state):
        """
        state (bool): True to turn on
        """
        v = 1 if state else 0
        self._sendCommand("l%d" % v)  # No space, as they are different commands

    @isasync
    def supply(self, sup):
        """
        Change the power supply to the defined state for each component given.
        This is an asynchronous method.
        sup dict(string-> boolean): name of the component and new state
        returns (Future): object to control the supply request
        """
        if not sup:
            return model.InstantaneousFuture()
        self._checkSupply(sup)

        return self._executor.submit(self._doSupply, sup)

    def _doSupply(self, sup):
        """
        supply power
        """
        for comp, val in sup.items():
            self.SetLaser(val)
        self._updateSupplied()

    def _updateSupplied(self):
        """
        update the supplied VA
        """
        res = self._sendCommand("l?")
        pwrd = (res == "1")

        # it's read-only, so we change it via _value
        self.supplied._set_value({self._light_name: pwrd}, force_write=True)

    def terminate(self):
        if self._executor:
            self._executor.cancel()
            self._executor.shutdown()
            self._executor = None

        if self._serial:
            self.SetLaser(False)  # TODO: allow to configure with argument to DPSS
            with self._ser_access:
                self._serial.close()
                self._serial = None
                self._file.close()

        super(DPSS, self).terminate()

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to device (without the CR)
        returns (str): answer received from the device (without \n or \r)
        raises:
            DPSSError: if an ERROR is returned by the device.
        """
        cmd = cmd + "\r"
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd.encode('latin1'))

            ans = b''
            while ans[-2:] != b'\r\n':
                char = self._serial.read()
                if not char:
                    raise IOError("Timeout after receiving %s" % to_str_escape(ans))
                ans += char

            logging.debug("Received answer %s", to_str_escape(ans))

            # TODO: check for other error answer?
            # Normally the device either answers OK, or a value, for commands finishing with a "?"
            if ans.startswith("Syntax error"):
                raise DPSSError(ans)

            return ans.decode('latin1').rstrip()

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a Power control device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=1  # s
        )

        # Purge
        ser.flush()
        ser.flushInput()

        # Try to read until timeout to be extra safe that we properly flushed
        while True:
            char = ser.read()
            if char == b'':
                break

        return ser

    def _findDevice(self, ports):
        """
        Look for a compatible device
        ports (str): pattern for the port name
        return (str): the name of the port used
        It also sets ._serial and ._idn to contain the opened serial port, and
        the identification string.
        raises:
            IOError: if no device are found
        """
        # TODO: For debugging purpose
#         if ports == "/dev/fake":
#             self._serial = DPSSSimulator(timeout=1)
#             return ports

        if os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        for n in names:
            try:
                # Ensure no one will talk to it simultaneously, and we don't talk to devices already in use
                self._file = open(n)  # Open in RO, just to check for lock
                try:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # Raises IOError if cannot lock
                except IOError:
                    logging.info("Port %s is busy, will not use", n)
                    continue

                self._serial = self._openSerialPort(n)

                try:
                    sn = self.GetSerialNumber()
                except DPSSError:
                    # Can happen if the device has received some weird characters
                    # => try again (now that it's flushed)
                    logging.info("Device answered by an error, will try again")
                    sn = self.GetSerialNumber()
                return n
            except (IOError, DPSSError):
                logging.info("Skipping device on port %s, which didn't seem to be a Cobolt", n)
                # not possible to use this port? next one!
                continue
        else:
            raise HwError("Failed to find a Cobolt device on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

