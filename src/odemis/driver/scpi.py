# -*- coding: utf-8 -*-
'''
Created on 26 Sep 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import fcntl
import glob
import logging
import numpy
from odemis import model, util
from odemis.model import HwError
from odemis.util import driver, to_str_escape
import os
import random
import re
import serial
import threading
import time


class SCPIError(Exception):
    """
    Exception used to indicate a problem reported by the device.
    """
    pass


class Ammeter(model.Detector):
    '''
    Implements a simple detector to report/measure current intensity.
    It currently supports only the Keithley 6485.
    Note from the documentation: the model 6485 can be used within one minute
      after it is turned on. However, the instrument should be turned on and
      allowed to warm up for at least one hour before use to achieve rated accuracy.
    Note: the Keithley needs to be properly configured to use the RS-232 connection.
    '''
    def __init__(self, name, role, port, baudrate=9600, idn=None, **kwargs):
        '''
        port (str): port name. Can be a pattern, in which case it will pick the
          first one which responds well
        baudrate (int): the baudrate setting for the RS232 connection
        idn (str or None): If present, a regex to match the *IDN command. For
         instance "KEITHLEY.+MODEL 6485.+12345678".
        '''
        model.Detector.__init__(self, name, role, **kwargs)

        self._ser_access = threading.Lock()
        self._serial = None
        self._file = None
        self._port, self._idn = self._findDevice(port, baudrate, idn)  # sets ._serial and ._file
        logging.info("Found SPCI device on port %s", self._port)

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "serial driver: %s" % (driver_name,)
        self._hwVersion = self._idn

        # Just for logging, check if there are any errors reported
        while True:
            n, msg = self.ReadNextError()
            if n is not None:
                logging.warning("Discarding previous error %s (%d)", msg, n)
            else:
                break

        stat = self.ReadStatusByte()
        if stat & (1 << 2):  # Bit 2 = error available
            # It seems that some status is not bad anyway
            logging.warning("Status byte is %d", stat)

        self.ClearStatus()

        self._lfr = self.GetLineFrequency()

        # Force range to auto
        self._sendOrder(":CURR:RANG:AUTO ON")
        self._checkError()
        # Prepare to measure current
        self.ConfigureCurrent()
        self._checkError()

        # TODO: that's probably very Keithley 6485
        rate = self.GetIntegrationRate()
        self._checkError()
        # Note: the lowest noise is at rate between 1 and 10, so ~20ms to 200ms
        # The max rate is the line frequency (=> 1 s)
        self.dwellTime = model.FloatContinuous(rate / self._lfr, (0.01 / self._lfr, 1),
                                               unit="s",
                                               setter=self._setDwellTime)

        self._shape = (float("inf"),)  # only one point, with float values
        self._generator = None
        self.data = BasicDataFlow(self)
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL

    def terminate(self):
        self.stop_generate()

        if self._serial:
            # There doesn't seem any command to stop the measurement
            with self._ser_access:
                self._serial.close()
                self._serial = None
                if self._file:
                    self._file.close()

        super(Ammeter, self).terminate()

    @staticmethod
    def _openSerialPort(port, baudrate):
        """
        Opens the given serial port the right way for a Power control device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        baudrate (int)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=1  # s
        )

        # Purge
        ser.flush()
        ser.flushInput()

        # Try to read until timeout to be extra safe that we properly flushed
        ser.timeout = 0
        while True:
            char = ser.read()
            if char == b'':
                break
        ser.timeout = 1

        return ser

    def _findDevice(self, ports, baudrate=9600, midn=None):
        """
        Look for a compatible device
        ports (str): pattern for the port name
        baudrate (0<int)
        midn (str or None): regex to match the *IDN answer
        return:
           (str): the name of the port used
           (str): the identification string
           Note: will also update ._file and ._serial
        raises:
            IOError: if no device are found
        """
        # For debugging purpose
        if ports == "/dev/fake":
            self._serial = K6485Simulator(timeout=1)
            self._file = None
            idn = self.GetIdentification()
            return ports, idn

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

                self._serial = self._openSerialPort(n, baudrate)

                try:
                    idn = self.GetIdentification()
                except SCPIError:
                    # Can happen if the device has received some weird characters
                    # => try again (now that it's flushed)
                    logging.info("Device answered by an error, will try again")
                    idn = self.GetIdentification()
                if midn and not re.search(midn, idn):
                    logging.info("Skipping device on port %s, which identified as %s",
                                 n, idn)
                    continue
                return n, idn
            except (IOError, SCPIError):
                logging.info("Skipping device on port %s, which didn't seem to be compatible", n)
                # not possible to use this port? next one!
                continue
        else:
            raise HwError("Failed to find a device on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

    def _sendOrder(self, cmd):
        """
        cmd (str): command to be sent to device (without the CR)
        """
        cmd = (cmd + "\r").encode('ascii')
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)

    def _sendQuery(self, cmd, timeout=1):
        """
        cmd (str): command to be sent to device (without the CR, but with the ?)
        timeout (int): maximum time to receive the answer
        returns (str): answer received from the device (without \n or \r)
        raise:
            IOError if no answer is returned in time
        """
        cmd = (cmd + "\r").encode('ascii')
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)

            self._serial.timeout = timeout
            ans = b''
            while ans[-1:] != b'\r':
                char = self._serial.read()
                if not char:
                    raise IOError("Timeout after receiving %s" % to_str_escape(ans))
                ans += char

            logging.debug("Received answer %s", to_str_escape(ans))

            return ans.rstrip().decode('latin1')

    # Wrapper for the actual firmware functions
    def GetIdentification(self):
        """
        return (str): the identification string as-is
        """
        # Returns something like:
        # KEITHLEY INSTRUMENTS INC.,MODEL 6485,4126216,C01   Jun 23 2010 12:22:00/A02  /J
        return self._sendQuery("*IDN?")

    def ClearStatus(self):
        self._sendOrder("*CLS")

    def ReadNextError(self):
        """
        Read the next error in the error queue
        return int or None, str: the error number (None if no error) and message
        """
        # Returns something like:
        # 0,"No error"
        # -113,"Undefined header"
        res = self._sendQuery("STAT:QUE?")
        if "," not in res:
            raise IOError("Failed to read error queue (got %s)" % (res,))
        sn, smes = res.split(",")
        if sn == "0":
            return None, None
        else:
            return int(sn), smes.strip("\"")

    def ReadStatusByte(self):
        # cf p. 10.8 for information on the status byte
        return int(self._sendQuery("*STB?"))

    def ConfigureCurrent(self):
        """
        Configure the device for "one-shot" measurement of current intensity
        """
        self._sendOrder("CONF:CURR")

    def ReadMeasurement(self):
        """
        return:
            measurement (0<=float): intensity in Amp
            time (0<=float): time of the measurement (since the last reset of the device)
            status (int): error bits, cf p 13.6
        """
        # Returns something like:
        # -1.121149E-10A,+2.305817E+03,+5.120000E+02
        # value A, time, error bit

        timeout = 1 + self.dwellTime.value * 4
        res = self._sendQuery("READ?", timeout)
        values = res.split(",")
        if len(values) != 3:
            raise IOError("Failed to read measurement (got %s)" % (res,))
        if values[0][-1] != "A":
            logging.warning("Unexpected unit for measurement (got %s)", values[0])

        try:
            val, ts, err = float(values[0][:-1]), float(values[1]), int(float(values[2]))
        except (TypeError, ValueError):
            raise IOError("Failed to read measurement (got %s)" % (res,))

        return val, ts, err

    def SetIntegrationRate(self, rate):
        """
        rate (0.01 <= float <= 50): the number of reads to be accumulated for one
          measurement. That's a factor of the "PLC", the power frequency (ie, 50Hz
          or 60Hz)
        """
        assert 0.01 <= rate <= 50
        self._sendOrder(":NPLC %.2f" % (rate,))

    def GetIntegrationRate(self):
        """
        return (int): the number of reads to integrate for a given measurement.
        """
        res = self._sendQuery(":NPLC?")
        return float(res)

    def GetLineFrequency(self):
        """
        return (float): the line frequency in Hz
        """
        # Returns 50 or 60
        res = self._sendQuery("SYST:LFR?")
        return float(res)

    def _checkError(self):
        """
        Check if an error is reported by the hardware
        raise SCPIError: if the hardware has an error queued
        """
        n, msg = self.ReadNextError()
        if n is not None:
            raise SCPIError("%s (%d)" % (msg, n))

    # For the Odemis API

    def _setDwellTime(self, value):
        # Note: a measurement takes more time than just the dwell time. Ex:
        # dt = 1 s -> ~3 s
        # dt = 0.1 -> ~0.4 s
        self.SetIntegrationRate(value * self._lfr)
        return value

    def start_generate(self):
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        # Fixed sleep period of 1ms, and the acquisition is blocking on the dwellTime
        self._generator = util.RepeatingTimer(1e-3,
                                              self._generate,
                                              "Current reading")
        self._generator.start()

    def stop_generate(self):
        if self._generator is not None:
            self._generator.cancel()
            self._generator = None

    def _generate(self):
        """
        Read the current detector rate and make it a data
        """
        # update metadata
        metadata = self._metadata.copy()
        metadata[model.MD_ACQ_DATE] = time.time()
        metadata[model.MD_DWELL_TIME] = self.dwellTime.value  # s

        # Read data and make it a DataArray
        d, t, stat = self.ReadMeasurement()
        if stat:
            logging.warning("Measurement status is 0x%x", stat)
        # [d] makes an array of shape (1), "d" would make an array of shape (),
        # but as it's a scalar, that confuses some code.
        nd = numpy.array([d], dtype=numpy.float)
        img = model.DataArray(nd, metadata)

        # Send the data to anyone intersted
        self.data.notify(img)


class BasicDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (PH300): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._detector.start_generate()

    def stop_generate(self):
        self._detector.stop_generate()


class K6485Simulator(object):
    """
    Simulates a Keithley 6485
    Same interface as the serial port
    """

    def __init__(self, timeout=1, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = b""  # what the commands sends back to the "host computer"
        self._input_buf = b""  # what we receive from the "host computer"

        self._lfr = 50
        self._nplc = 5
        self._time_start = time.time()

        self._errorq = [] # list of int

    def write(self, data):
        self._input_buf += data
        msgs = self._input_buf.split(b"\r")
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

    def _addError(self, err):
        self._errorq.append(err)

    def _sendAnswer(self, ans):
        self._output_buf += b"%s\r" % (ans,)

    def _parseMessage(self, msg):
        """
        msg (str): the message to parse (without the \r)
        return None: self._output_buf is updated if necessary
        """
        logging.debug("SIM: parsing %s", to_str_escape(msg))
        m = re.match(br"(?P<com>\*?[A-Za-z:]+\??)\W*(?P<args>.*)", msg)
        if not m:
            logging.error("Received unexpected message %s", msg)
            return

        com = m.group("com").upper()

        if m.group("args"):
            args = m.group("args").strip()
        else:
            args = None

        logging.debug("SIM: decoded message as %s %s", to_str_escape(com), args)

        # decode the command
        if com == b"*IDN?":
            self._sendAnswer(b"KEITHLEY INSTRUMENTS INC.,MODEL 6485,123456,C01   Sep 27 2017 12:22:00/A02  /J")
        elif com == b"*CLS":
            pass
        elif com == b"*STB?":
            self._sendAnswer(b"0")  # It's all fine
        elif com == b"STAT:QUE?":
            if not self._errorq:
                self._sendAnswer(b"0,\"No error\"")
            else:
                err = self._errorq.pop(0)
                self._sendAnswer(b"%d,\"Error %d\"" % (err, err))
        elif com == b"CONF:CURR":
            pass
        elif com == b":CURR:RANG:AUTO":
            pass
        elif com == b"SYST:LFR?":
            self._sendAnswer(b"%g" % self._lfr)
        elif com == b":NPLC?":
            self._sendAnswer(b"%g" % self._nplc)
        elif com == b":NPLC":
            if not args:
                self._addError(6)
            else:
                self._nplc = float(args)
        elif com in (b"MEAS:CURR?", b"READ?"):
            dur = (self._nplc / self._lfr) * 3 + 0.01
            time.sleep(dur)
            ts = time.time() - self._time_start
            val = random.uniform(-1e-9, 1e-9)
            self._sendAnswer(b"%EA,%E,%E" % (val, ts, 0))
        else:
            logging.warning(b"SIM: Unsupported instruction %s", to_str_escape(com))
            # TODO: add an error to the queue
            self._addError(1)
