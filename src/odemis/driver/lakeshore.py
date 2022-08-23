# -*- coding: utf-8 -*-
'''
Created on 12 June 2020

@author: Anders Muskens

Copyright © 2020 Anders Muskens, Delmic

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
import logging
import fcntl
import glob
from odemis import model
from odemis.model import HwError
from odemis.util import to_str_escape
import os
import re
import serial
import threading
import random
from odemis.util import RepeatingTimer
import time
from odemis.model import IntEnumerated, FloatContinuous, FloatVA

POLL_INTERVAL = 1.0  # interval to poll new temperature
KELVIN_CONVERT = 273.15  # shift to convert K to C

STATUS_BYTE = {
    128: "Power on",
    32: "Command Error",
    16: "Execution error",
    4: "Query Error",
    1: "Operation complete"
    }

POWER_ON = 128
COMMAND_ERROR = 32
EXECUTION_ERROR = 16
QUERY_ERROR = 4
OPC = 1


class LakeshoreError(IOError):
    """
    Exception used to indicate a problem reported by the device.
    """

    def __init__(self, errno, strerror, *args, **kwargs):
        super(LakeshoreError, self).__init__(errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror


class Lakeshore(model.HwComponent):

    def __init__(self, name, role, port, sensor_input='b', output_channel=2, **kwargs):
        """
        A driver for the Lakeshore 310 temperature controller.

        name: (str)
        role: (str)
        port: (str) port name. Can be a pattern, in which case all the ports
          fitting the pattern will be tried.
          Use /dev/fake for a simulator
        sensor_input (str): The sensor input to use, typically 'A' or 'B'
        output_channel: (int): The channel output to control, typically 1 or 2
        """
        super(Lakeshore, self).__init__(name, role, **kwargs)

        # Connect to serial port
        self._ser_access = threading.Lock()
        self._serial = None
        self._file = None

        # Check input and output channels
        self._sensor_input = sensor_input.upper()
        if not self._sensor_input in ('A', 'B'):
            raise ValueError("Sensor input must be either 'A' or 'B'")
        
        self._output_channel = output_channel
        if not self._output_channel in (1, 2):
            raise ValueError("Invalid output channel. Should be an int of 1 or 2")
        
        self._port = self._findDevice(port)  # sets ._serial and ._file
        logging.info("Found Lakeshore 335 device on port %s", self._port)

        manufacturer, md, serialn, firmware = self.GetIdentifier()

        self._hwVersion = "%s %s S/N: %s" % (manufacturer, md, serialn)
        self._swVersion = "Firmware: %s" % (firmware,)

        # Clear errors at start
        try:
            self.checkError()
        except LakeshoreError as ex:
            logging.warning("Discarding initial error status: %s", ex)

        # Vigilant attributes of the controller.
        self.temperature = FloatVA(unit=u"°C", value=self.GetSensorTemperature(), readonly=True)
        self.targetTemperature = FloatContinuous(value=self.GetSetpoint(), unit=u"°C", range=[-273, 50], setter=self._set_targetTemperature)
        self.heating = IntEnumerated(value=self.GetHeaterRange(), choices={0: "Off", 1: "Low", 2: "Medium", 3: "High"},
                                     setter=self._set_heating)

        self._poll_timer = RepeatingTimer(POLL_INTERVAL, self._poll, "Lakeshore temperature update")
        self._poll_timer.start()

        # lock the keypad
        self.LockKeypad(True)

    def terminate(self):
        self._poll_timer.cancel()
        time.sleep(0.1)

        if self._serial.isOpen():
            self.LockKeypad(False)

        with self._ser_access:
            self._serial.close()

        super(Lakeshore, self).terminate()

    @staticmethod
    def _openSerialPort(port, baudrate=57600):
        """
        Opens the given serial port the right way for a Power control device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        baudrate (int)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.SEVENBITS,
            parity=serial.PARITY_ODD,
            stopbits=serial.STOPBITS_ONE,
            timeout=2  # s
        )

        # Purge
        ser.flush()
        ser.flushInput()

        # Try to read until timeout to be extra safe that we properly flushed
        ser.timeout = 0.01
        while True:
            char = ser.read()
            if char == b'':
                break
        ser.timeout = 1

        return ser

    def _findDevice(self, ports):
        """
        Look for a compatible device
        ports (str): pattern for the port name
        baudrate (0<int)
        return:
           (str): the name of the port used
           Note: will also update ._file and ._serial
        raises:
            IOError: if no devices are found
        """
        # For debugging purpose
        if ports == "/dev/fake":
            self._serial = LakeshoreSimulator(timeout=1)
            self._file = None
            return ports

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
                manufacturer, md, _, _ = self.GetIdentifier()  # if value is incorrect, will throw an exception wile unpacking

                if manufacturer != "LSCI":
                    raise IOError("Invalid device manufacturer")
                if md != "MODEL335":
                    raise IOError("The model is %s, not MODEL335." % (md,))
                return n

            except (IOError, LakeshoreError) as e:
                logging.debug(e)
                logging.info("Skipping device on port %s, which didn't seem to be compatible", n)
                # not possible to use this port? next one!
                continue
        else:
            raise HwError("Failed to find a device on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

    def _sendOrder(self, cmd):
        """
        cmd (byte str): command to be sent to device (without the LF)
        """
        cmd = cmd + b"\n"
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)
            time.sleep(0.05)

    def _sendQuery(self, cmd):
        """
        cmd (byte str): command to be sent to device (without the LF, but with the ?)
        returns (byte str): answer received from the device (without \n or \r)
        raise:
            IOError if no answer is returned in time
        """
        cmd = cmd + b"\n"
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)

            self._serial.timeout = 1
            ans = b''
            while ans[-1:] != b'\n':
                char = self._serial.read()
                if not char:
                    raise IOError("Timeout after receiving %s" % to_str_escape(ans))
                ans += char

            logging.debug("Received answer %s", to_str_escape(ans))

            time.sleep(0.05)  # prevent overloading the device with messages
            return ans.strip()

    # Low level serial commands.
    # Note: These all convert to internal units of the controller

    def GetStatusByte(self):
        # Checks the device status event register
        return int(self._sendQuery(b"*ESR?"))

    def ClearStatusByte(self):
        # Clear the status register after checking it
        self._sendOrder(b"*CLS")

    def checkError(self):
        # Checks if an error occurred and raises an exception accordingly.
        status_byte = self.GetStatusByte()
        self.ClearStatusByte()
        
        errors = []
        
        for err in (COMMAND_ERROR, EXECUTION_ERROR, QUERY_ERROR):
            if status_byte & err:
                errors.append(STATUS_BYTE[err])

        if errors:
            error_msg = "Error %s (Status byte: 0x%X)" % (", ".join(errors), status_byte)
            raise LakeshoreError(status_byte, error_msg)

    def GetIdentifier(self):
        """
        Get the identifier from the controller
        Returns 4 strings: manufacturer, model number, serial number, and firmware version
        """
        identity = self._sendQuery(b'*IDN?')
        try:
            manufacturer, md, serialn, firmware = identity.decode("latin1").split(',')
        except TypeError:
            raise IOError("Invalid identifier received")

        return manufacturer, md, serialn, firmware
    
    def GetTemp(self):
        """
        Get the temperature at the thermocouple junction
        """
        return float(self._sendQuery(b"TEMP?"))

    def SetSetpoint(self, temp):
        """
        Set the temperature setpoint
        temp (float): the temperature to set, in Celsius
        """
        self._sendOrder(b"SETP %d,%.2f" % (self._output_channel, temp + KELVIN_CONVERT))

    def GetSetpoint(self):
        """
        Get the temperature setpoint. Returns a float in Celsius
        """
        val = self._sendQuery(b"SETP? %d" % (self._output_channel,))
        return float(val) - KELVIN_CONVERT

    def GetSensorTemperature(self):
        """
        Get the current temperature of the sensor input. Returns a float in Celsius
        """
        val = self._sendQuery(b"KRDG? %s" % (self._sensor_input.encode("latin1"),))
        return float(val) - KELVIN_CONVERT

    def LockKeypad(self, lock):
        """
        Lock or unlock keypad on device from preventing bad user input
        lock (bool): True to lock, False to unlock
        """
        if lock:
            self._sendOrder(b"LOCK 1")
        else:
            self._sendOrder(b"LOCK 0")

    def HeaterSetup(self,
                    output_type,
                    heater_resistance,
                    max_current,
                    max_user_current,
                    current_or_power):
        """
        Send a heater setup command to the device

        output_type (int): Output type (Output 2 only): 0=Current, 1=Voltage
        heater_resistance (int): Heater Resistance Setting: 1 = 25 Ohm, 2 = 50 Ohm.
        max_current (int): Specifies the maximum heater output current: 
            0 = User Specified, 1 = 0.707 A, 2 = 1 A, 3 = 1.141 A,
            4 = 1.732 A
        max_user_current (int): Specifies the maximum heater output current if 
            max current is set to User Specified.
        current_or_power (int): Specifies whether the heater output displays in current or 
            power (current mode only). Valid entries: 1 = current, 2 = power.
        """
        self._sendOrder(b"HTRSET %d,%d,%d,%d,%f,%d" % (self._output_channel,
                    output_type,
                    heater_resistance,
                    max_current,
                    max_user_current,
                    current_or_power))

    def GetHeaterSetup(self):
        """
        Query heater setup from device

        returns: tuple of ints with the setup parameters in sequence
        """
        htr_setup = self._sendQuery(b'HTRSET? %d' % (self._output_channel,))
        (output_type,
            heater_resistance,
            max_current,
            max_user_current,
            current_or_power) = htr_setup.split(',')

        return (int(output_type),
            int(heater_resistance),
            int(max_current),
            float(max_user_current),
            int(current_or_power))

    def SetHeaterRange(self, hrange):
        """
        Set the heater range
        hrange (int): For Outputs 1 and 2 in Current mode: 0 = Off, 1 = Low, 
            2 = Medium, 3 = High
            For Output 2 in Voltage mode: 0 = Off, 1 = On
        """
        self._sendOrder(b"RANGE %d,%d" % (self._output_channel, hrange))

    def GetHeaterRange(self):
        """
        Query heater range enum int from the device
        reutrns: (int): Depending on setup, for Outputs 1 and 2 in Current mode: 0 = Off, 1 = Low, 
            2 = Medium, 3 = High
            For Output 2 in Voltage mode: 0 = Off, 1 = On
        """
        return int(self._sendQuery(b'RANGE? %d' % (self._output_channel,)))

    # Internal API functions
    def _set_targetTemperature(self, value):
        """
        Setter for the targetTemperature VA
        VA is in Celsius, but controller uses Kelvin
        """
        value = float(value)
        self.SetSetpoint(value)
        # Read back the new value from the device
        svalue = self.GetSetpoint()
        self.checkError()
        if abs(value - svalue) >= 0.01:
            logging.warning("Did not set new target temperature to %f", value)
        return svalue

    def _set_heating(self, value):
        """
        Setter for the heater range VA
        """
        value = int(value)
        self.SetHeaterRange(value)
        # Read back the new value from the device
        svalue = self.GetHeaterRange()
        self.checkError()
        if svalue != value:
            logging.warning("Did not set new heating range to %d", value)
        return svalue

    def _poll(self):
        '''
        This method runs in a separate thread and polls the device for the temperature
        '''
        try:
            temp = self.GetSensorTemperature()
            self.temperature._set_value(temp, force_write=True)
            logging.debug(u"Lakeshore temperature: %f °C", self.temperature.value)
            self.checkError()
        except:
            # another exception.
            logging.exception("Failed to read sensor temperature.")


STABLE_TEMPERATURE = 77  # K, temperature reached without heating


class LakeshoreSimulator(object):
    """
    Simulates a Lakeshore 335
    Same interface as the serial port
    """

    def __init__(self, timeout=1):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = b""  # what the commands sends back to the "host computer"
        self._input_buf = b""  # what we receive from the "host computer"

        # Start with a command error, to check it's properly reset by the driver
        self._status_byte = POWER_ON | COMMAND_ERROR
        self._setpoint = 150  # K
        self._temperature = 100  # K
        self._heating = 3  # enum int 0,1,2, or 3

    def write(self, data):
        self._input_buf += data
        msgs = self._input_buf.split(b"\n")
        for m in msgs[:-1]:
            self._parseMessage(m)  # will update _output_buf

        self._input_buf = msgs[-1]

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

    def isOpen(self):
        return hasattr(self, "_output_buf")

    def _sendAnswer(self, ans):
        self._output_buf += b"%s\n" % (ans,)

    def _parseMessage(self, msg):
        """
        msg (str): the message to parse (without the \r)
        return None: self._output_buf is updated if necessary
        """
        logging.debug("SIM: parsing %s", to_str_escape(msg))
        msg = msg.decode("latin1").strip()  # remove leading and trailing whitespace
        msg = "".join(msg.split())  # remove all space characters

        if msg == "*ESR?":  # error status register
            self._sendAnswer(b"%d" % (self._status_byte,))
        elif msg == "*CLS":
            self._status_byte = POWER_ON
        elif msg == "*IDN?":
            self._sendAnswer(b"LSCI,MODEL335,fake,0.0")
        elif re.match('LOCK', msg):
            pass
        # Query setpoint
        elif re.match('SETP\?', msg):
            self._sendAnswer(b"+%.3f" % (self._setpoint,))
        # set setpoint
        elif re.match("SETP", msg):
            vals = msg[4:].split(',')
            self._setpoint = float(vals[1])
        # Query heating range
        elif re.match('RANGE\?', msg):
            self._sendAnswer(b"%d" % (self._heating,))
        # set heating range
        elif re.match("RANGE", msg):
            vals = msg[5:].split(',')
            self._heating = int(vals[1])
        # Query temperature
        elif re.match('KRDG\?', msg):
            # send temperature with some noise
            if os.path.exists(os.path.join(model.BASE_DIRECTORY, "temp_increase.txt")):
                logging.info("Simulator set to increase temperature by 1 deg each reading")
                self._temperature += 1
                self._sendAnswer(b"+%.3f" % (self._temperature))
            else:
                self._sendAnswer(b"+%.3f" % (self._temperature + random.uniform(-0.1, 0.1),))
                if self._heating:
                    if self._temperature < self._setpoint:  # heating is enabled
                        self._temperature += 0.05 * self._heating  # simulate heating
                    else:
                        self._temperature -= 0.1
                else:  # no heating so no temperature control
                    # maintain stable temperature
                    if self._temperature > STABLE_TEMPERATURE:
                        self._temperature -= 0.1  # cool off with no heating
        else:
            self._status_byte |= COMMAND_ERROR

