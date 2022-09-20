# -*- coding: utf-8 -*-
'''
Created on 1 Sep 2015

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
import fcntl
import glob
import logging
import numpy
from odemis import model
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
from odemis.util import driver, to_str_escape
import os
import serial
import sys
import tempfile
import threading
import yaml
import time

# FIXME: figure out when reaching memory end
EEPROM_CAPACITY = 512  # Memory space 0h-1ffh

class PowerControlUnit(model.PowerSupplier):
    '''
    Implements the PowerSupplier class to regulate the power supply of the
    components connected to the Power Control Unit board. It also takes care of
    communication with the PCU firmware.
    '''

    def __init__(self, name, role, port, pin_map=None, delay=None, init=None, ids=None,
                 termination=None, **kwargs):
        '''
        port (str): port name
        pin_map (dict of str -> int): names of the components
          and the pin where the component is connected.
        delay (dict str -> float): time to wait for each component after it is
            turned on.
        init (dict str -> boolean): turn on/off the corresponding component upon
            initialization.
        ids (list str): EEPROM ids expected to be detected during initialization.
        termination (dict str -> bool/None): indicate for every component
            if it should be turned off on termination (False), turned on (True)
            or left as-is (None).
        Raise an exception if the device cannot be opened
        '''
        if pin_map:
            self.powered = list(pin_map.keys())
        else:
            self.powered = []
        model.PowerSupplier.__init__(self, name, role, **kwargs)

        # TODO: catch errors and convert to HwError
        self._ser_access = threading.Lock()

        self._file = None
        self._port = self._findDevice(port)  # sets ._serial and ._file
        logging.info("Found Power Control device on port %s", self._port)

        # Get identification of the Power control device
        self._idn = self._getIdentification()

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "serial driver: %s" % (driver_name,)
        self._hwVersion = "%s" % (self._idn,)

        pin_map = pin_map or {}
        self._pin_map = pin_map

        delay = delay or {}
        # fill the missing pairs with 0 values
        self._delay = dict.fromkeys(pin_map, 0)
        self._delay.update(delay)
        self._last_start = dict.fromkeys(self._delay, time.time())

        # only keep components that should be changed on termination
        termination = termination or {}
        self._termination = {k: v for k, v in termination.items() if v is not None}
        for comp in self._termination:
            if comp not in pin_map:
                raise ValueError("Component %s in termination not found in pin_map." % comp)

        # will take care of executing switch asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._supplied = {}
        self.supplied = model.VigilantAttribute(self._supplied, readonly=True)
        self._updateSupplied()

        init = init or {}
        # Remove all None's from the dict, so it can be passed as-is to _doSupply()
        init = {k: v for k, v in init.items() if v is not None}
        for comp in init:
            if comp not in pin_map:
                raise ValueError("Component %s in init not found in pin_map." % comp)
        try:
            self._doSupply(init, apply_delay=False)
        except IOError as ex:
            # This is in particular to handle some cases where the device resets
            # when turning on the power. One or more trials and the
            logging.exception("Failure during turning on initial power.")
            raise HwError("Device error when initialising power: %s. "
                          "Try again or contact support if the problem persists." %
                          (ex,))

        self.memoryIDs = model.VigilantAttribute(None, readonly=True, getter=self._getIdentities)

        if ids:
            mem_ids = self.memoryIDs.value
            for eid in ids:
                if eid not in mem_ids:
                    raise HwError("EEPROM id %s was not detected. Make sure "
                                  "all EEPROM components are connected." % (eid,))

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

    def _doSupply(self, sup, apply_delay=True):
        """
        supply power
        apply_delay (bool): If true, wait the amount of time requested in delay
          after turning on the power
        """
        for comp, val in sup.items():
            # find pin and values corresponding to component
            pin = self._pin_map[comp]
            # should always be able to get the value, default values just to be
            # on the safe side
            if apply_delay:
                delay = self._delay.get(comp, 0)
            else:
                # We still wait a little, to avoid starting all components
                # _exactly_ at the same time, which could cause a power peak.
                delay = 1

            if val:
                self._sendCommand("PWR " + str(pin) + " 1")
                state = self.supplied.value[comp]
                if state:
                    # Already on, wait the time remaining
                    remaining = (self._last_start[comp] + delay) - time.time()
                    time.sleep(max(0, remaining))
                else:
                    # wait full time
                    self._last_start[comp] = time.time()
                    time.sleep(delay)

                # Check it really worked
                ans = self._sendCommand("PWR? " + str(pin))
                if ans != "1":
                    logging.warning("Failed to turn on component %s", comp)
            else:
                self._sendCommand("PWR " + str(pin) + " 0")

        self._updateSupplied()

    def _updateSupplied(self):
        """
        update the supplied VA
        """
        pins_updated = set(self._pin_map.values())  # to avoid asking for the same pin multiple times
        for pin in pins_updated:
            ans = self._sendCommand("PWR? " + str(pin))
            # Update all components that are connected to the same pin
            to_update = [c for c in self.powered if pin == self._pin_map[c]]
            for c_update in to_update:
                self._supplied[c_update] = (ans == "1")

        # it's read-only, so we change it via _value
        self.supplied._value = self._supplied
        self.supplied.notify(self.supplied.value)

    def terminate(self):
        if self._executor:
            self._executor.cancel()
            self._executor.shutdown()
            self._executor = None

        # Power components on/off according to ._termination
        # If nothing is specified, leave it as-is.
        logging.debug("Changing power supply on termination: %s" % self._termination)
        self._doSupply(self._termination)

        if self._serial:
            with self._ser_access:
                self._serial.close()
                self._serial = None

        if self._file:
            self._file.close()
            self._file = None

        super(PowerControlUnit, self).terminate()

    def _getIdentification(self):
        return self._sendCommand("*IDN?")

    def writeMemory(self, id, address, data):
        """
        Write data to EEPROM.
        id (str): EEPROM registration number #hex (little-endian format)
        address (str): starting address #hex
        data (str): data to be written #hex (little-endian format)
        """
        self._sendCommand("WMEM %s %s %s" % (id, address, data))

    def readMemory(self, id, address, length):
        """
        Read data from EEPROM.
        id (str): EEPROM registration number #hex (little-endian format)
        address (str): starting address #hex
        length (int): number of bytes to be read
        returns (str): data read back #hex (little-endian format)
        """
        ans = self._sendCommand("RMEM %s %s %s" % (id, address, length))
        return ans

    def readEEPROM(self, id):
        """
        We use this method to get a dict that contains all the data written in
        the EEPROM with the given id.
        id (str): EEPROM registration number #hex (little-endian format)
        """
        if id not in self.memoryIDs.value:
            raise KeyError("There was no EEPROM with the given id found")
        mem_cont = self.readMemory(id, "00", EEPROM_CAPACITY)
        mem_yaml = ""
        while mem_cont != "":
            if mem_cont[:2] != "00":
                mem_yaml += chr(int(mem_cont[:2], 16))
            mem_cont = mem_cont[2:]
        dct = yaml.load(mem_yaml)
        return dct

    def _getIdentities(self):
        """
        Return the ids of connected EEPROMs
        """
        try:
            ans = self._sendCommand("SID")
        except PowerControlError as e:
            # means there is no power provided
            raise HwError("There is no power provided to the Power Control Unit. "
                          "Please make sure the board is turned on.")
        return [x for x in ans.split(',') if x != '']

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to Power Control unit.
        returns (str): answer received from the Power Control unit
        raises:
            IOError: if an ERROR is returned by the Power Control firmware.
        """
        cmd = (cmd + "\n").encode('latin1')
        with self._ser_access:
            logging.debug("Sending command %s" % to_str_escape(cmd))
            self._serial.write(cmd)

            ans = b''
            char = None
            while char != b'\n':
                char = self._serial.read()
                if not char:
                    logging.error("Timeout after receiving %s", to_str_escape(ans))
                    # TODO: See how you should handle a timeout before you raise
                    # an HWError
                    raise HwError("Power Control Unit connection timeout. "
                                  "Please turn off and on the power to the box.")
                # Handle ERROR coming from Power control unit firmware
                ans += char

            logging.debug("Received answer %s", to_str_escape(ans))
            ans = ans.decode('latin1')
            if ans.startswith("ERROR"):
                raise PowerControlError(ans.split(' ', 1)[1])

            return ans.rstrip()

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a Power control device.
        port (string): the name of the serial port (e.g., /dev/ttyACM0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
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
        logging.debug("Nothing left to read, Power Control Unit can safely initialize.")

        ser.timeout = 5  # Sometimes the software-based USB can have some hiccups
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
        # For debugging purpose
        if ports == "/dev/fake":
            self._serial = PowerControlSimulator(timeout=1)
            return ports

        if os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        for n in names:
            try:
                self._file = open(n)  # Open in RO, just to check for lock
                try:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except IOError:
                    logging.info("Port %s is busy, will wait and retry", n)
                    time.sleep(11)
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._serial = self._openSerialPort(n)

                try:
                    idn = self._getIdentification()
                except PowerControlError:
                    # Can happen if the device has received some weird characters
                    # => try again (now that it's flushed)
                    logging.info("Device answered by an error, will try again")
                    idn = self._getIdentification()
                # Check that we connect to the right device
                if not idn.startswith("Delmic Analog Power"):
                    logging.info("Connected to wrong device on %s, skipping.", n)
                    continue
                return n
            except (IOError, PowerControlError):
                # not possible to use this port? next one!
                logging.debug("Skipping port %s which doesn't seem the right device", n)
                continue
        else:
            raise HwError("Failed to find a Power Control device on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, args (sn)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        logging.info("Serial ports scanning for Power control device in progress...")
        found = []  # (list of 2-tuple): name, kwargs

        if sys.platform.startswith('linux'):
            # Look for each ACM device, if the IDN is the expected one
            acm_paths = glob.glob('/dev/ttyACM?')
            for port in acm_paths:
                # open and try to communicate
                try:
                    dev = cls(name="test", role="test", port=port)
                    idn = dev._getIdentification()
                    if idn.startswith("Delmic Analog Power"):
                        found.append({"port": port})
                except Exception:
                    pass
        else:
            # TODO: Windows version
            raise NotImplementedError("OS not yet supported")

        return found


class PowerControlError(IOError):
    """
    Exception used to indicate a problem coming from the Power Control Unit.
    """
    pass

IDN = b"Delmic Analog Power Control simulator 1.0"
MASK = 1  # mask for the first bit


class PowerControlSimulator(object):
    """
    Simulates a PowerControl (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        self.timeout = timeout
        self._f = tempfile.TemporaryFile()  # for fileno
        self._output_buf = b""  # what the Power Control Unit sends back to the "host computer"
        self._input_buf = b""  # what Power Control Unit receives from the "host computer"
        self._i2crcv = 0  # fake expander response byte
        self._ids = [b"233c23f40100005a", b"238abe69010000c8"]
        self._mem = numpy.chararray(shape=(2, 512), itemsize=2)  # fake eeproms
        self._mem[:] = b'00'

    def fileno(self):
        return self._f.fileno()

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
        self._output_buf = b""

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
            sep = self._input_buf.index(b'\n')
            msg = self._input_buf[0:sep + 1]

            # remove the bytes we've just read
            self._input_buf = self._input_buf[len(msg):]

            self._processMessage(msg)

    def _processMessage(self, msg):
        """
        process the msg, and put the result in the output buffer
        msg (str): raw message (including header)
        """
        res = b""
        wspaces = msg.count(b' ')
        qmarks = msg.count(b'?')
        tokens = msg.split()
        if ((wspaces > 1) and (qmarks > 0)) or (wspaces > 3) or (qmarks > 1):
            res = b"ERROR: Cannot parse this command\n"
        elif qmarks:
            if tokens[0] == b"*IDN?":
                res = IDN + b'\n'
            elif tokens[0] == b"PWR?":
                pin = int(tokens[1])
                if (pin < 0) or (pin > 7):
                    res = b"ERROR: Out of range pin number\n"
                else:
                    ans = (self._i2crcv >> pin) & MASK
                    res = b'%d\n' % ans
            else:
                res = b"ERROR: Cannot parse this command\n"
        elif wspaces:
            if tokens[0] == b"PWR":
                pin = int(tokens[1])
                val = int(tokens[2])
                if (pin < 0) or (pin > 7):
                    res = b"ERROR: Out of range pin number\n"
                else:
                    self._i2crcv = (self._i2crcv & ~(1 << pin)) | ((val << pin) & (1 << pin))
                    res = b'\n'
            elif tokens[0] == b"WMEM":
                id = tokens[1]
                address = tokens[2]
                data = tokens[3]
                if len(id)%2 == 1:
                    res = b"ERROR: Invalid number of hexadecimal id characters. Must be an even number.\n"
                elif len(address) % 2 == 1:
                    res = b"ERROR: Invalid number of hexadecimal address characters. Must be an even number.\n"
                elif len(data) % 2 == 1:
                    res = b"ERROR: Invalid number of hexadecimal data characters. Must be an even number.\n"
                else:
                    id_ind = self._ids.index(id)
                    addr = int(address, 16)
                    for i in range(len(data) // 2):
                        self._mem[id_ind, addr + i] = data[i * 2:i * 2 + 2]
                    res = b'\n'
            elif tokens[0] == b"RMEM":
                id = tokens[1]
                address = tokens[2]
                length = int(tokens[3])
                if len(id) % 2 == 1:
                    res = b"ERROR: Invalid number of hexadecimal id characters. Must be an even number.\n"
                elif len(address) % 2 == 1:
                    res = b"ERROR: Invalid number of hexadecimal address characters. Must be an even number.\n"
                else:
                    id_ind = self._ids.index(id)
                    addr = int(address, 16)
                    for i in range(length):
                        res += self._mem[id_ind, addr + i]
                    res += b'\n'
            else:
                res = b"ERROR: Cannot parse this command\n"
        elif tokens[0] == b"SID":
            for id in self._ids:
                res += id
                if id != self._ids[-1]:
                    res += b","
            res += b'\n'
        else:
            res = b"ERROR: Cannot parse this command\n"

        # add the response end
        if res is not None:
            self._output_buf += res
