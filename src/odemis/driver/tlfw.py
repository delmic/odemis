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

from past.builtins import basestring
import glob
import logging
from odemis import model
import odemis
from odemis.model import CancellableThreadPoolExecutor, HwError, isasync
from odemis.util import driver, to_str_escape
import os
import re
import serial
import threading
import time


class TLFWError(Exception):
    """
    Represents an error reported by the hardware
    """
    pass


class FW102c(model.Actuator):
    """
    Represents a Thorlabs filter wheel FW102C as an actuator.
    It provides one enumerated axis, whose actual band values are provided by
    the user at init.
    """
    # Regex matching the compatible identification strings
    re_idn = "THORLABS.*FW102C.*"

    def __init__(self, name, role, port, bands, _scan=False, **kwargs):
        """
        port (string): name of the serial port to connect to. Can be a pattern,
         in which case, all the ports fitting the pattern will be tried, and the
         first one which looks like an FW102C will be used.
        bands (dict 1<=int<=12 -> 2-tuple of floats > 0 or str):
          filter position -> lower and higher bound of the wavelength (m) of the
          light which goes _through_. If it's a list, it implies that the filter
          is multi-band.
        _scan (bool): only for internal usage
        raise IOError if no device answering or not a compatible device
        """
        self._ser_access = threading.Lock()
        self._port = self._findDevice(port)
        logging.info("Found FW102C device on port %s", self._port)
        if _scan:
            return

        # check bands contains correct data
        self._maxpos = self.GetMaxPosition()
        if not bands:
            raise ValueError("Argument bands must contain at least one band")
        try:
            for pos, band in bands.items():
                if not 1 <= pos <= self._maxpos:
                    raise ValueError("Filter position should be between 1 and "
                                     "%d, but got %d." % (self._maxpos, pos))
                # To support "weird" filter, we accept strings
                if isinstance(band, basestring):
                    if not band.strip():
                        raise ValueError("Name of filter %d is empty" % pos)
                else:
                    driver.checkLightBand(band)
        except Exception:
            logging.exception("Failed to parse bands %s", bands)
            raise

        curpos = self.GetPosition()
        if curpos not in bands:
            logging.info("Current position %d is not configured, will add it", curpos)
            bands[curpos] = "unknown"

        axes = {"band": model.Axis(choices=bands)}
        model.Actuator.__init__(self, name, role, axes=axes, **kwargs)

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = self._idn

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

        self._speed = self.GetSpeed()

        self.position = model.VigilantAttribute({"band": curpos}, readonly=True)

    def getMetadata(self):
        return self._metadata

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

        super(FW102c, self).terminate()

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
        if os.name == "nt":
            # TODO
            # ports = ["COM" + str(n) for n in range(15)]
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        for n in names:
            try:
                self._serial = self._openSerialPort(n)
            except serial.SerialException:
                # not possible to use this port? next one!
                continue

            # check whether it looks like a FW102C
            try:
                # If any garbage was previously received, make it discarded.
                self._serial.write(b"\r")
                # can have some \x00 bytes at the beginning + "CMD_NOT_DEFINED"
                self._flushInput()
                idn = self.GetIdentification()
                if re.match(self.re_idn, idn):
                    self._idn = idn
                    return n # found it!
            except Exception as ex:
                logging.debug("Port %s doesn't seem to have a FW102C device connected. " +
                              "Identification failed with exception: %s", n, ex)
        else:
            raise HwError("Failed to find a filter wheel FW102C on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

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
            timeout=10  # s (can take time when filter changes)
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
            skipped = self._serial.read(1000) # More than 1000 chars => give up
            logging.debug("Skipping input %s", to_str_escape(skipped))

    re_err = br"Command error (.*)"
    def _sendQuery(self, com):
        """
        Send a command which expects an answer
        com (byte string): command to send (not including the ? and the \r)
        return (byte string): the answer without newline and suffix ("> ")
        raises
            IOError: if there is a timeout
            TLFWError: if the hardware reports an error
        """
        # TODO: handle IOError and automatically try to reconnect (cf LLE)

        assert isinstance(com, bytes), 'com argument needs to be a byte string'
        assert(len(com) <= 50) # commands cannot be long
        full_com = com + b"\r"
        with self._ser_access:
            logging.debug("Sending: '%s'", to_str_escape(full_com))
            self._serial.write(full_com)

            # ensure everything is received, before expecting an answer
            self._serial.flush()

            # Read until end of answer
            line = b""
            while True:
                char = self._serial.read() # empty if timeout
                if not char: # should always finish by a "> "
                    raise IOError("Controller timeout, after receiving '%s'" % to_str_escape(line))

                # normal char
                line += char
                if line[-2:] == b"> ":
                    break

            logging.debug("Received: '%s'", to_str_escape(line))

        # remove echo + suffix + new line
        line = line[len(full_com):-2].rstrip(b"\r")

        # if it's an error message => raise an error
        m = re.match(self.re_err, line)
        if m:
            err = m.group(1)
            raise TLFWError("Device rejected command '%s': %s" % (com, err))

        return line

    def _sendCommand(self, com):
        """
        Send a command which does not expect any answer
        com (byte string): command to send (not including the ? and the \r)
        return when the command is finished processed
        raises
            IOError: if there is a timeout
            TLFWError: if the hardware reports an error
        """
        self._sendQuery(com)
        # don't return anything

    def GetIdentification(self):
        """
        return (str): model name as reported by the device
        """
        # answer is like "THORLABS FW102C/FW212C Filter Wheel version 1.04"
        return self._sendQuery(b"*idn?").decode("latin1")

    def GetMaxPosition(self):
        """
        return (1<int): maximum number of positions available (eg, 6, 12)
        """
        ans = self._sendQuery(b"pcount?")
        return int(ans)

    def GetPosition(self):
        """
        return (1<=int<=maxpos): current position
        Note: might be different from the last position set if the user has
         manually changed it.
        """
        ans = self._sendQuery(b"pos?")
        return int(ans)

    def GetSpeed(self):
        """
        return (0 or 1): current "speed" of the wheel, the bigger the faster
        """
        ans = self._sendQuery(b"speed?")
        return int(ans)

    def SetPosition(self, pos):
        """
        pos (1<=int<=maxpos): current position
        returns when the new position is set
        raise Exception in case of error
        """
        assert(1 <= pos <= self._maxpos)

        # Estimate how long it'll take
        cur_pos = self.position.value["band"]
        p1, p2 = sorted([pos, cur_pos])
        dist = min(p2 - p1, (6 + p1) - p2)
        if self._speed == 0:
            dur_one = 2  # s
        else:
            dur_one = 1  # s
        maxdur = 1 + dist * dur_one * 2 # x 2 as a safe bet
        prev_timeout = self._serial.timeout
        try:
            self._serial.timeout = maxdur
            self._sendCommand(b"pos=%d" % pos)
        finally:
            self._serial.timeout = prev_timeout
        logging.debug("Move to pos %d finished", pos)

    # What we don't need:
    # speed?\r1\r>
    # trig?\r0\r>
    # sensors?\r0\r>

    def _doMoveBand(self, pos):
        """
        move to the position and updates the metadata and position once it's over
        """
        self.SetPosition(pos)
        self._updatePosition()

    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        Note: it should not be called while holding _ser_access
        """
        pos = {"band": self.GetPosition()}

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        # TODO move to the +N next position? (and modulo number of axes)
        raise NotImplementedError("Relative move on enumerated axis not supported")

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        return self._executor.submit(self._doMoveBand, pos["band"])

    def stop(self, axes=None):
        self._executor.cancel()

    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        try:
            pos = self.GetPosition()
            maxpos = self.GetMaxPosition()
            if 1 <= pos <= maxpos:
                return True
        except Exception:
            logging.exception("Selftest failed")

        return False

    @classmethod
    def scan(cls, port=None):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        returns (list of 2-tuple): name, args (port)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        if port:
            ports = [port]
        else:
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range(15)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')

        logging.info("Serial ports scanning for Thorlabs filter wheel in progress...")
        found = []  # (list of 2-tuple): name, kwargs
        for p in ports:
            try:
                logging.debug("Trying port %s", p)
                dev = cls(None, None, p, bands=None, _scan=True)
            except (serial.SerialException, IOError):
                # not possible to use this port? next one!
                continue

            # Get some more info
            try:
                maxpos = dev.GetMaxPosition()
            except Exception:
                continue
            else:
                # create fake band argument
                bands = {}
                for i in range(1, maxpos + 1):
                    bands[i] = (i * 100e-9, (i + 1) * 100e-9)
                found.append((dev._idn, {"port": p, "bands": bands}))

        return found


# Emulator
class FakeFW102c(FW102c):
    """
    For testing purpose only. To test the driver without hardware.
    Pretends to connect but actually just print the commands sent.
    """
    def __init__(self, name, role, port, *args, **kwargs):
        # force a port pattern with just one existing file
        FW102c.__init__(self, name, role, port="/dev/null", *args, **kwargs)

    @staticmethod
    def _openSerialPort(port):
        """
        opens a fake port, connected to the simulator
        """
        ser = FW102cSimulator(
            port=port,
            baudrate=115200, # only correct if setting was not changed
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1  # s
        )

        return ser

    @classmethod
    def scan(cls, port=None):
        return super(FakeFW102c, cls).scan(port="/dev/null")


class FW102cSimulator(object):
    """
    Simulates a FW102C (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = b"" # what the commands sends back to the "host computer"
        self._input_buf = b"" # what we receive from the "host computer"

        # internal values (same as command names)
        self._state = {b"speed": 1,
                       b"trig": 0,
                       b"pos": 3,
                       b"pcount": 6,
                       b"sensors": 0,
                       }

    def write(self, data):
        self._input_buf += data
        # echo is active
        self._output_buf += data

        # process each commands separated by "\r"
        commands = self._input_buf.split(b"\r")
        self._input_buf = commands.pop() # last one is not complete yet
        for c in commands:
            self._processCommand(c)

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

    def _processCommand(self, com):
        """
        process the command, and put the result in the output buffer
        com (str): command
        """
        logging.debug("Simulator received command %s", to_str_escape(com))
        out = None
        try:
            if com == b"*idn?":
                out = b"THORLABS FW102C/FW212C Fake Filter Wheel version 1.01"
            elif com.endswith(b"?"):
                name = com[:-1]
                val = self._state[name]
                out = b"%d" % val
            elif com.startswith(b"pos="):
                val = int(com[4:])
                if not 1 <= val <= self._state[b"pcount"]:
                    raise ValueError("%d" % val)

                # simulate a move
                curpos = self._state[b"pos"]
                p1, p2 = sorted([val, curpos])
                dist = min(p2 - p1, (6 + p1) - p2)
                if self._state[b"speed"] == 0:
                    dur = 2
                else:
                    dur = 1
                time.sleep(dist * dur)
                self._state[b"pos"] = val
                # no output
            else:
                # TODO: set of speed, trig, sensors,
                logging.debug("Command '%s' unknown", to_str_escape(com))
                raise KeyError("%s" % to_str_escape(com))
        except ValueError:
            out = b"Command error CMD_ARG_INVALID\n"
        except KeyError:
            out = b"Command error CMD_NOT_DEFINED\n"

        # add the response end
        if out is None:
            out = b""
        else:
            out += b"\r"
        out += b"> "
        self._output_buf += out
