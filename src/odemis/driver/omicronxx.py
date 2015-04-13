# -*- coding: utf-8 -*-
'''
Created on 6 Nov 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Driver for the Omicron LuxX laser light engines
# cf PhoxX_ LuxX_BrixX Programmers Guide V1.3.pdf for documentation.
# It is currently only supported in rudimentary form.

from __future__ import division

import glob
import logging
from odemis import model
import odemis
from odemis.model import HwError
from odemis.util import driver
import os
import re
import serial


class OXXError(Exception):
    """
    Error returned by the hardware
    """
    pass


class DevxX(object):
    """
    Represent one PhoxX/LuxX/BrixX laser emitter

    Note: On USB, the device sends (by default) regularly "ad-hoc" messages,
      to indicate new values.
    """

    def __init__(self, port):
        """
        port (string): serial port to use
        raise IOError if no device answering or not a xX device
        """
        self.port = port
        self._serial = self._openSerialPort(port)
        self._flushInput() # can have some \x00 bytes at the beginning

        # As the devices do not have special USB vendor ID or product ID, it's
        # quite possible that it's not a xX device actually at the other end of
        # the serial connection, so we first must make sure of that
        try:
            self.GetFirmware()
        except Exception:
            raise IOError("No xX device detected on port '%s'" % port)

        # Fill in some info
        wl, power = self.GetSpecInfo()
        self.wavelength = wl
        self.max_power = self.GetMaxPower()

        self.PowerOn()
        self.LaserOff() # for safety

    def terminate(self):
        self.LaserOff()
        self.PowerOff()
        self._serial.close()
        self._serial = None

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for the Omicron xX devices.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=500000, # TODO: only correct for USB connections
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1  # s
        )

        return ser

    def _flushInput(self):
        """
        Ensure there is no more data queued to be read on the bus (=serial port)
        """
        self._serial.flush()
        self._serial.flushInput()
        while self._serial.read():
            pass

    def _sendCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (not including the ? and the \r)
        return (string): the report without prefix ("!") nor newline.
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        full_com = "?" + com + "\r"
        logging.debug("Sending: '%s'", full_com.encode('string_escape'))
        self._serial.write(full_com)

        # ensure everything is received, before expecting an answer
        self._serial.flush()

        # Read lines per line until it's an answer (!)
        while True:
            line = b""
            char = self._serial.read() # empty if timeout
            while char and char != "\r":
                # FIXME: it seems that flushing the input doesn't work. It's
                # still possible to receives 0's at the beginning.
                # This is a kludge to workaround that
                if not line and char == "\x00":
                    char = ""

                # normal char
                line += char
                char = self._serial.read()
            logging.debug("Received: '%s'", line.encode('string_escape'))

            # Check it's a valid answer
            if not char: # should always finish by a "\r"
                raise IOError("Controller timeout.")

            if line[0] != "$": # ad-hoc message => we don't care
                break
            else:
                logging.debug("Skipping ad-hoc message '%s'", line.encode('string_escape'))

        if not line[0] == "!":
            raise IOError("Answer prefix (!) not found.")
        if line == "!Uk":
            raise OXXError("Unknown command (%s)." % com)
        # TODO: if it's a set command, the answer should look like "com>", and
        # if it's "comx", it means it failed (eg, out of range).

        return line[1:]

    # TODO: _readMessage()
    # Expects and read a $... message

    # Wrappers from each command into a method
    def GetFirmware(self):
        """
        raise ValueError if problem decoding the answer
        """
        ans = self._sendCommand("GFw")
        # Expects something like:
        # GFw Model code § Device-ID § Firmware
        try:
            m = re.match(r"GFw(?P<model>.*)\xa7(?P<devid>.*)\xa7(?P<fw>.*)", ans)
            model, devid, fw = m.group("model"), m.group("devid"), m.group("fw")
        except Exception:
            raise ValueError("Failed to decode firmware answer '%s'" % ans.encode('string_escape'))

        return model, devid, fw

    def GetSpecInfo(self):
        """
        Return (float, float): wavelength (m), theoretical maximum power (W)
        """
        ans = self._sendCommand("GSI")
        # Expects something like:
        # GSi int (wl in nm) § int (power in mW)
        try:
            m = re.match(r"GSI(?P<wl>\d+)\xa7(?P<power>\d+)", ans)
            wl = int(m.group("wl")) * 1e-9 # m
            power = int(m.group("power")) * 1e-3 # W
        except Exception:
            raise ValueError("Failed to decode spec info answer '%s'" % ans.encode('string_escape'))

        return wl, power

    def GetMaxPower(self):
        """
        Return (float) actual maximum power in W
        """
        ans = self._sendCommand("GMP")
        # Expects something like:
        # GMP int (power in mW)
        try:
            m = re.match(r"GMP(?P<power>\d+)", ans)
            power = int(m.group("power")) * 1e-3 # W
        except Exception:
            raise ValueError("Failed to decode max power answer '%s'" % ans.encode('string_escape'))

        return power

    def SetLevelPower(self, power):
        """
        power (0<=float<=1): power value as a ratio between 0 and the maximum power
        """
        # value as a a ASCII HEX number ranging from 0x000 to 0xFFF representing 0% to 100%.
        assert(0 <= power <= 1)
        val = int(round(power * 0xFFF))
        ans = self._sendCommand("SLP%03X" % val)
        # TODO: ans should be "SLP>"

    def LaserOn(self):
        ans = self._sendCommand("LOn")

    def LaserOff(self):
        ans = self._sendCommand("LOf")

    def PowerOn(self):
        ans = self._sendCommand("POn")

    def PowerOff(self):
        ans = self._sendCommand("POf")


class MultixX(model.Emitter):
    """
    Represent a group of PhoxX/LuxX/BrixX laser emitters with different
    wavelengths
    """

    def __init__(self, name, role, ports, **kwargs):
        """
        ports (string): pattern of the name of the serial ports to try to connect to
          find the devices. It can have a "glob", for example: "/dev/ttyUSB*"
        """
        model.Emitter.__init__(self, name, role, **kwargs)
        self._ports = ports
        self._devices = self._getAvailableDevices(ports)
        if not self._devices:
            raise HwError("No Omicron xX device found for ports '%s', check "
                          "they are turned on and connected to the computer."
                          % ports)

        spectra = [] # list of tuples: 99% low, 25% low, centre, 25% high, 99% high in m
        max_power = [] # list of float (W)
        for d in self._devices:
            wl = d.wavelength
            # Lasers => spectrum is almost just one wl, but make it 2 nm wide
            # to avoid a bandwidth of exactly 0.
            spectra.append((wl - 1e-9, wl - 0.5e-9, wl, wl + 0.5e-9, wl + 1e-9))
            max_power.append(d.max_power)

        self._shape = ()

        # power of the whole device (=> max power of the device with max power)
        self.power = model.FloatContinuous(0., (0., max(max_power)), unit="W")
        self.power.subscribe(self._updatePower)

        # ratio of power per device
        # => if some device don't support max power, clamped before 1
        self.emissions = model.ListVA([0.] * len(self._devices), unit="",
                                      setter=self._setEmissions)
        # info on what device is which wavelength
        self.spectra = model.ListVA(spectra, unit="m", readonly=True)

        # make sure everything is off
        self._updateIntensities(self.power.value, self.emissions.value)

        # set HW and SW version
        driver_name = driver.getSerialDriver(self._devices[0].port)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = "Omicron xX" # TODO: get version from GetFirmware()

    def getMetadata(self):
        metadata = {}
        # MD_IN_WL expects just min/max => if multiple sources, we need to combine
        wl_range = (None, None) # min, max in m
        power = 0
        for i, intens in enumerate(self.emissions.value):
            if intens > 0:
                wl_range = (min(wl_range[0], self.spectra.value[i][1]),
                            max(wl_range[1], self.spectra.value[i][3]))
                # FIXME: not sure how to combine
                power += intens * self.power.value

        if wl_range == (None, None):
            wl_range = (0, 0) # TODO: needed?
        metadata[model.MD_IN_WL] = wl_range
        metadata[model.MD_LIGHT_POWER] = power
        return metadata

    def _updateIntensities(self, power, intensities):
        # set the actual values
        for d, intens in zip(self._devices, intensities):
            p = min(power * intens, d.max_power)
            if p > 0:
                d.LaserOn()
                d.SetLevelPower(p / d.max_power)
            else:
                d.LaserOff()

    def _updatePower(self, value):
        self._updateIntensities(value, self.emissions.value)

    def _setEmissions(self, intensities):
        """
        intensities (list of N floats [0..1]): intensity of each source
        """
        if len(intensities) != len(self._devices):
            raise ValueError("Emission must be an array of %d floats." % len(self._devices))

        # clamp intensities which cannot reach the maximum power
        cl_intens = []
        for d, intens in zip(self._devices, intensities):
            cl_intens.append(min(intens, d.max_power / self.power.range[1]))

        self._updateIntensities(self.power.value, cl_intens)
        return cl_intens

    def terminate(self):
        for d in self._devices:
            d.terminate()
        self._devices = []

    @staticmethod
    def _getAvailableDevices(ports):
        if os.name == "nt":
            # TODO
            # ports = ["COM" + str(n) for n in range(15)]
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        devices = []
        for n in names:
            try:
                d = DevxX(n)
                devices.append(d)
            except Exception:
                logging.info("Port %s doesn't seem to have a xX device connected", n)

        return devices

    @classmethod
    def scan(cls, ports=None):
        """
        ports (string): name (or pattern) of the serial ports. If None, all the serial ports are tried
        returns (list of 2 tuple): name, kwargs (ports)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        if ports is None:
            if os.name == "nt":
                ports = "COM*"
            else:
                ports = '/dev/ttyUSB?*'

        devices = cls._getAvailableDevices(ports)
        if devices:
            return [("OmicronxX", {"ports": ports})]
        else:
            return []

# TODO: simulator
