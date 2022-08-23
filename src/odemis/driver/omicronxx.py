# -*- coding: utf-8 -*-
'''
Created on 6 Nov 2013

@author: Éric Piel

Copyright © 2013-2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Driver for the Omicron LuxX laser light engines and LedHub
# cf xX-Laser Series and LED Programmers Guide v1.9.pdf for documentation.
# It is currently only supported in rudimentary form. Only USB connection is
# supported.
#
# Note that the USB connection uses a standard FTDI device ID, so it's necessary
# for the driver to communicate with the device to check it's really a Omicron
# one.
#
# There are two kinds of devices: the one that contain just one source, and the
# one which contain multiple source (ie, the LedHUB). In the second case, the
# commands are indexed with the source number: [X].

from future.utils import with_metaclass
from abc import ABCMeta, abstractmethod
import fcntl
import glob
import logging
from odemis import model
import odemis
from odemis.model import HwError
from odemis.util import driver, to_str_escape
import os
import re
import serial
import time
from past.builtins import long

class OXXError(Exception):
    """
    Error returned by the hardware
    """
    pass


OXX_DEVID = {
    3: "PhoxX",
    4: "LuxX",
    18: "LuxX+",
    100: "BrixX",
    19: "LEDMOD2+",
    20: "LedHUB",
}


class USBAccesser(object):
    """
    Represents the connection to a device via serial-over-USB
    """
    def __init__(self, port):
        """
        port (string): serial port to use
        raise IOError: if port cannot be used
        """
        self.port = port
        self._serial = self._openSerialPort(port)
        self.flushInput() # can have some \x00 bytes at the beginning
        self.driver = driver.getSerialDriver(port)

    def terminate(self):
        if self._serial:
            self._serial.close()
            self._serial = None

        if self._file:
            self._file.close()
            self._file = None

    def __del__(self):
        self.terminate()

    def _openSerialPort(self, port):
        """
        Opens the given serial port the right way for the Omicron xX devices.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        raise IOError: if port cannot be used
        """
        if port == "/dev/fakehub":
            self._file = None
            return HubxXSimulator(timeout=1)

        # Ensure no one will talk to it simultaneously, and we don't talk to devices already in use
        self._file = open(port)  # Open in RO, just to check for lock
        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # Raises IOError if cannot lock

        ser = serial.Serial(
            port=port,
            baudrate=500000, # TODO: only correct for USB connections
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1  # s
        )

        return ser

    def flushInput(self):
        """
        Ensure there is no more data queued to be read on the bus (=serial port)
        """
        self._serial.flush()
        self._serial.flushInput()
        while True:
            data = self._serial.read(100)
            if len(data) < 100:
                break
            logging.debug("Flushing data %s", to_str_escape(data))

    def sendCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (not including the ? and the \r)
        return (string): the report without prefix ("!") nor carriage return.
        """
        assert(len(com) <= 50)
        full_com = ("?" + com + "\r").encode('latin1')
        logging.debug("Sending: '%s'", to_str_escape(full_com))
        self._serial.write(full_com)

        # ensure everything is received, before expecting an answer
        self._serial.flush()

        # Read lines per line until it's an answer (!)
        while True:
            line = self.readMessage()
            if line[0] == "$": # ad-hoc message => we don't care
                logging.debug("Skipping ad-hoc message '%s'", to_str_escape(line))
            else:
                break

        if not line[0] == "!":
            raise IOError("Answer prefix (!) not found.")
        if line.startswith("!UK"): # !UK or !UK[n]
            raise OXXError("Unknown command (%s)." % to_str_escape(com))

        return line[1:]

    def readMessage(self):
        """
        Reads one message from the device (== any character until \r)
        return str: the message (raw, without the ending \r)
        raise: IOError in case of timeout
        """
        line = b""
        char = self._serial.read() # empty if timeout
        while char and char != b"\r":
            # FIXME: it seems that flushing the input doesn't work. It's
            # still possible to receives 0's at the beginning.
            # This is a kludge to workaround that
            if not line and char == b"\x00":
                logging.debug("Discarding null byte")
                char = b""

            # normal char
            line += char
            char = self._serial.read()
        logging.debug("Received: '%s'", to_str_escape(line))

        # Check it's a valid answer
        if not char: # should always finish by a "\r"
            raise IOError("Controller timeout.")

        return line.decode('latin1')


class DevxX(object):
    """
    Represent one PhoxX/LuxX/BrixX laser emitter or one light source of a
    LightHub.
    """
#     Note: On USB, the device sends (by default) regularly "ad-hoc" messages,
#       to indicate new values.

    def __init__(self, acc, channel=None):
        """
        acc (USBAccesser): an opened connection
        channel (None or 0 <= int): If None, will expect to drive directly a
          device with a single source. If a number >= 1, then will expect to
          drive the channel corresponding to the given number. If 0, will
          expect to just get enough information on the channels provided by the
          device (it will provide .channels with the available channel numbers).
        raise IOError if no device answering or not a xX device
        """
        self.acc = acc
        self._channel = channel
        self._com_chan = ""

        # As the devices do not have special USB vendor ID or product ID, it's
        # quite possible that it's not a xX device actually at the other end of
        # the serial connection, so we first must make sure of that
        try:
            modl, devid, fw = self.GetFirmware()
        except IOError:
            raise IOError("No xX device detected on port %s" % acc.port)

        hwname = OXX_DEVID.get(devid, modl)

        # Fill in some info
        wl, power, subdev = self.GetSpecInfo()
        if channel is None:
            if subdev:
                raise TypeError("Multi-channel device found but no channel selected")
        elif channel == 0:  # master
            if not subdev:
                raise TypeError("Single-channel device found while master device requested")
            # wl is always 0, and power is the total power
            self.channels = subdev
        else:
            if channel not in subdev:
                raise HwError("No channel %d found in device on port %s" % acc.port)
            self._com_chan = "[%d]" % channel
            # Now we can ask again, to get the actual values
            wl, _, _ = self.GetSpecInfo()

        sn = self.GetSerialNumber()
        if channel in (None, 0):
            self.hwVersion = "%s v%s (s/n %s)" % (hwname, fw, sn)
        else:  # Sub channel
            self.hwVersion = "s/n[%d] %s" % (channel, sn)

        # If there is error => reset
        status = self.GetActualStatus()
        logging.debug("Device (on port %s) status = 0x%X", acc.port, status)

        if status & 1:  # bit 0: error state
            error = self.GetFailureByte()
            if error & (1 << 9):  # bit 9: external interlock
                raise HwError("External interlock loop is opened, close the interlock to activate the device")

            lerror = self.GetLatchedFailure()
            if not (error & 0xfffe):
                # no other bit? could be just soft-interlock => reset will fix it
                logging.info("Device (on port %s) reports error 0x%X (latched 0x%X), will reset it",
                             acc.port, error, lerror)
            else:
                # any other bit set? a soft-reset will probably not be enough
                logging.error("Device (on port %s) reports hardware error 0x%X (latched 0x%X), see manual.",
                              acc.port, error, lerror)

            # always reset the master, so temporarily do not send the channel
            com_chan_orig = self._com_chan
            self._com_chan = ""
            try:
                self.ResetController()
            finally:
                self._com_chan = com_chan_orig

            # Check if that has helped
            error = self.GetFailureByte()
            status = self.GetActualStatus()
            if error:
                raise HwError("Device reports error 0x%04X, power cycle the light source. "
                              "If the problem persists, contact a support technician." %
                              (error,))

        if status & (1 << 8):  # bit 8: Need to toggle key
            raise HwError("Device needs to have the key switch toggled off and on")

        if not (status & (1 << 7)):  # bit 7: key switch allows laser (=1)
            raise HwError("Key switch interlock prevents laser output, close the interlock loop to activate the device")

        if not (status & (1 << 6)):  # bit 6: "external" light enabler (=1)
            raise HwError("Electronic shutter active, open the shutter by pressing the button on the device")

        # Disable ad-hoc mode (on the master device)
        # (alternatively, we could listen to the messages, and update info such
        # as the temperature)
        # Also disable external modulation, to control fully by software
        mode = self.GetOperatingMode()
        # Disable: Ad-hoc mode (13), analog modulation (7), digital modulation (5)
        mode &= ~((1 << 13) | (1 << 7) | (1 << 5))
        self.SetOperatingMode(mode)

        if channel == 0:  # master
            return

        # Select the right command to change the level power
        if devid in (19, 20):  # LEDMOD, LedHUB
            # Not only it avoids writing in the memory, but it also works
            self.setLightPower = self.SetTemporaryPower
        else:
            # old style
            self.setLightPower = self.SetLevelPower

        if devid in (19, 20):  # LEDMOD, LedHUB => led
            # The wavelength range is not precisely provided by the hardware,
            # but it's usually around 20 nm
            self.wavelength = (wl - 10e-9, wl - 3e-9, wl, wl + 3e-9, wl + 10e-9)
        else:
            # Lasers => spectrum is almost just one wl, but make it 2 nm wide
            # to avoid a bandwidth of exactly 0.
            self.wavelength = (wl - 1e-9, wl - 0.5e-9, wl, wl + 0.5e-9, wl + 1e-9)

        self.max_power = self.GetMaxPower()

        if channel is None:
            devname = acc.port
        else:
            devname = "%d" % channel

        # Just for info
        wh = self.GetWorkingHours()
        logging.info("Device %s has %d working hours", devname, wh)

        try:
            tempd = self.MeasureTemperatureDiode()
            tempa = self.MeasureTemperatureAmbient()
            logging.info("Temperature of %s: diode = %g °C, ambient = %g °C",
                         devname, tempd, tempa)
        except Exception:
            # Some hardware just don't support it
            logging.debug("Unable to read temperature")

        self.LightOff() # for safety
        self.SetLevelPower(0)  # saved in memory, so next reboot it will start off
        self.activate(False)  # stand-by
        self.PowerOn()

    def activate(self, active):
        """
        Go out or in stand-by.
        active (bool): if True, will turn on the operation and bias release.
          Otherwise, will disable them.
        """
        mode = self.GetOperatingMode()
        if active:
            mode |= (1 << 4) + (1 << 3)  # bit 4 = operation release, bit 3 = bias release
        else:
            mode &= ~((1 << 4) + (1 << 3))
        self.SetOperatingMode(mode)

    def terminate(self):
        if self.acc:
            self.LightOff()
            self.PowerOff()
            self.activate(False)  # Go to stand-by
            self.acc = None

    def _getValue(self, com):
        """
        Read a value (str)
        com (str): 3 characters command
        return (str): the value returned
        raise:
            IOError if problem decoding the answer or timeout
            OXXError: if the device is unhappy (eg, unknown command)
        """
        fullcom = "%s%s" % (com, self._com_chan)
        ans = self.acc.sendCommand(fullcom)
        if not ans.startswith(fullcom):
            raise IOError("Expected answer to start with %s but got %s" % (fullcom, to_str_escape(ans)))
        return ans[len(fullcom):]

    def _setValue(self, com, val=None):
        """
        Write a value (str)
        com (str): 3 characters command
        val (None or str): value to set
        raise:
            IOError if problem decoding the answer or timeout
            OXXError: if the device is unhappy (eg, unknown command, out of range)
        """
        if val is None:
            val = ""
        ans = self.acc.sendCommand("%s%s%s" % (com, self._com_chan, val))
        if not ans.startswith(com):
            raise IOError("Expected answer to start with %s but got %s" %
                          (com, to_str_escape(ans)))
        status = ans[len(com) + len(self._com_chan):]
        if not status:
            logging.warning("Answer too short after setting %s: %s",
                            com, to_str_escape(ans))
        elif status[0] == "x":
            raise OXXError("Failed to set %s to %s" % (com, val))
        elif status[0] == ">":
            pass
        else:
            logging.warning("Unexpected answer after setting %s: %s",
                            com, to_str_escape(ans))

    # Wrappers from each command into a method
    def GetFirmware(self):
        """
        return (str, int, str): model name, device ID, firmware version
        raise ValueError if problem decoding the answer
        """
        ans = self._getValue("GFw")
        # Expects something like:
        # GFw Model code § Device-ID § Firmware
        try:
            m = re.match(r"(?P<model>.*)\xa7(?P<devid>.*)\xa7(?P<fw>.*)", ans)
            modl, devid, fw = m.group("model"), int(m.group("devid")), m.group("fw")
        except Exception:
            raise ValueError("Failed to decode firmware answer '%s'" % to_str_escape(ans))

        return modl, devid, fw

    def GetSpecInfo(self):
        """
        Return:
            wavelength (float): in meters
            power (float): theoretical maximum power (W)
            subdev (set of int): subdevices available
        """
        ans = self._getValue("GSI")
        # Expects something like:
        # GSI [m63] (optional) int (wl in nm) § int (power in mW)
        try:
            m = re.match(r"(\[m(?P<mdev>\d+)])?(?P<wl>\d+)\xa7(?P<power>\d+)", ans)
            mdev = m.group("mdev")
            if mdev is None:
                mdev = 0 # None if no mdev bitmask
            else:
                mdev = int(mdev)
            wl = int(m.group("wl")) * 1e-9 # m
            power = int(m.group("power")) * 1e-3 # W
        except Exception:
            raise ValueError("Failed to decode spec info answer '%s'" % to_str_escape(ans))

        # Convert the bitmask into a set of int
        subdev = set()
        n = 1
        while mdev:
            if mdev & 0x1:
                subdev.add(n)
            n += 1
            mdev >>= 1

        return wl, power, subdev

    def GetSerialNumber(self):
        """
        Return str: the serial number of the device
        """
        return self._getValue("GSN")

    def GetMaxPower(self):
        """
        Return (float) actual maximum power in W
        """
        ans = self._getValue("GMP")
        # Expects something like:
        # GMP int (power in mW)
        try:
            power = int(ans) * 1e-3 # W
        except Exception:
            raise ValueError("Failed to decode max power answer '%s'" % ans.encode('string_escape'))

        return power

    def SetLevelPower(self, power):
        """
        Set the power (and save in device memory)
        power (0<=float<=1): power value as a ratio between 0 and the maximum power
        """
        # On the LedHub, this doesn't seem to always work => use TPP or SPP

        # value as a a ASCII HEX number ranging from 0x000 to 0xFFF representing 0% to 100%.
        assert(0 <= power <= 1)
        val = int(round(power * 0xFFF))
        self._setValue("SLP", "%03X" % val)

    def SetPowerPercent(self, power):
        """
        Set the power (and save in device memory)
        Note: only available on new devices
        power (0<=float<=1): power value as a ratio between 0 and the maximum power
        """
        assert(0 <= power <= 1)
        val = power * 100  # in percentage
        self._setValue("SPP", "%0.5f" % val)

    def SetTemporaryPower(self, power):
        """
        Set the power (avoid writing it in memory)
        Note: only available on LEDMOD
        power (0<=float<=1): power value as a ratio between 0 and the maximum power
        """
        assert(0 <= power <= 1)
        val = power * 100 # in percentage
        self._setValue("TPP", "%0.5f" % val)

    def GetWorkingHours(self):
        """
        Get the actual operating status
        return (int): number of hours that the light has been on (in hours)
        """
        ans = self._getValue("GWH")
        return int(ans)

    def GetActualStatus(self):
        """
        Get the actual operating status
        return (int): bit mask of the status, cf documentation
        """
        ans = self._getValue("GAS")
        return int(ans, 16)

    def GetLatchedFailure(self):
        """
        Get the error that caused a lockout of the laser
        return (int): bit mask of the status, cf documentation
        """
        ans = self._getValue("GLF")
        return int(ans, 16)

    def GetFailureByte(self):
        """
        Get the error info
        return (int): bit mask of the error status, cf documentation
          Note: it's a 16 bits integer
        """
        ans = self._getValue("GFB")
        return int(ans, 16)

    def MeasureTemperatureDiode(self):
        """
        Get the diode temperature
        return (float or None): temperature in °C, None if unknown
        """
        # returns "25.3" or "x" when not available
        ans = self._getValue("MTD")
        return float(ans)  # Raise ValueError if not a float

    def MeasureTemperatureAmbient(self):
        """
        Get the ambient temperature inside the head
        return (float): temperature in °C
        raise ValueError: if the temperature is unknown/unavailable
        """
        ans = self._getValue("MTA")
        return float(ans)

    def GetOperatingMode(self):
        """
        Get the operating mode
        return (int): bit mask of the mode, cf documentation
        """
        ans = self._getValue("GOM")
        return int(ans, 16)

    def SetOperatingMode(self, mode):
        """
        Set the operating mode
        mode (int): bit mask of the mode, cf documentation on Get Operating Mode
        """
        assert(0 <= mode < 2 ** 16)
        # Note: The master of a Hub only supports bits 13->15 (the other ones
        # are unused)

        self._setValue("SOM", "%2X" % mode)

    def RecallOperatingMode(self, mode):
        """
        Set the "preset" operating mode.
        Note: each device has different operating mode presets, so you need to
        check the documentation.
        mode (0<=int): one of the operating mode for the device. 0 is "stand-by",
          the rest is device dependent.
        """
        assert(0 <= mode < 256)
        self._setValue("ROM", "%d" % mode)  # Unknown above 8 so unclear if it's decimal or hexadecimal

    def ResetController(self):
        self._setValue("RsC")
        # TODO: discard potential garbage & wait for reset ready message $RsC>
        while True: # TODO timeout
            try:
                msg = self.acc.readMessage()
            except IOError:
                continue
            if "$RsC" in msg:
                break

    def CalibrateLaserDiode(self):
        # TODO: untested
        self._setValue("CLD")
        # Immediately return !CLD
        # Then send $CLDc1 when the procedure is over (about 2 minutes later)

        while True:  # TODO timeout
            try:
                msg = self.acc.readMessage()
            except IOError:
                continue
            if "$CLD" in msg:
                break

    def LightOn(self):
        """
        Turns on the laser/led
        """
        self._setValue("LOn")

    def LightOff(self):
        self._setValue("LOf")

    def PowerOn(self):
        self._setValue("POn")

    def PowerOff(self):
        self._setValue("POf")


class GenericxX(with_metaclass(ABCMeta, model.Emitter)):

    def __init__(self, name, role, ports, **kwargs):
        """
        ports (string): pattern of the name of the serial ports to try to connect to
          find the devices. It can have a "glob", for example: "/dev/ttyUSB*"
        """
        model.Emitter.__init__(self, name, role, **kwargs)
        self._ports = ports
        self._master, self._devices = self._getAvailableDevices(ports)
        if not self._devices:
            raise HwError("No Omicron xX device found for ports '%s', check "
                          "that '%s' is turned on and connected to the computer."
                          % (ports, name))

        spectra = [] # list of tuples: 99% low, 25% low, centre, 25% high, 99% high in m
        max_power = [] # list of float (W)
        for d in self._devices:
            spectra.append(d.wavelength)
            max_power.append(d.max_power)

        self._shape = ()

        # power of the whole device (=> max power of the device with max power)
        self.power = model.ListContinuous(value=[0.0] * len(self._devices),
                                          range=(tuple([0.0] * len(self._devices)), tuple(max_power),),
                                          unit="W", cls=(int, long, float),)
        self.power.subscribe(self._updatePower)

        # info on what device is which wavelength
        self.spectra = model.ListVA(spectra, unit="m", readonly=True)

        # Ensure the whole Hub is turned on
        if self._master:
            try:
                self._master.PowerOn()
            except OXXError:
                raise HwError("Failed to power on the master device, check the interlock.")

        # make sure everything is off (turning on the HUB will turn on the lights)
        self._updateIntensities(self.power.value)

        # set SW version
        driver_name = self._devices[0].acc.driver
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)

    @classmethod
    @abstractmethod
    def _getAvailableDevices(cls, ports):
        """
        return:
         master (None or DevxX): the master device (if any)
         devices (list of DevxX): the actual devices to control
        """
        return None, []

    def terminate(self):
        for d in self._devices:
            d.terminate()
        self._devices = []
        if self._master:
            self._master.terminate()

        super(GenericxX, self).terminate()

    def _updateIntensities(self, power):
        # TODO: compare to the previous (known) state, and only send commands for
        # the difference, to save some time (each command takes ~5 ms)
        # set the actual values
        all_off = all(p == 0 for p in power)
        if not all_off and self._master:
            # On the LedHUB, when the master goes from off to on, all the
            # devices are turned on too. In theory, as the level has been set to
            # 0, it's not an issue... but some (non-properly calibrated) lights
            # seem to still emit a little bit at level 0. That's why we also set
            # them in stand-by, so that the light cannot be emitting anyway.
            self._master.LightOn()

        for d, p in zip(self._devices, power):
            p = min(p, d.max_power)
            if p > 0:
                d.LightOn()
                d.setLightPower(p / d.max_power)
                d.activate(True)
            else:
                d.activate(False)
                # In theory, not need to turn light off as the stand-by does it,
                # but as it cannot hurt, we do it to be extra-safe.
                d.LightOff()
                d.setLightPower(0)

        if all_off and self._master:
            # On the LedHUB, it's necessary to turn off the master to get the
            # "emission" status led off.
            self._master.LightOff()

    def _updatePower(self, value):
        self._updateIntensities(value)



class MultixX(GenericxX):
    """
    Represent a group of PhoxX/LuxX/BrixX laser emitters with different
    wavelengths
    """

    def __init__(self, name, role, ports, **kwargs):
        """
        ports (string): pattern of the name of the serial ports to try to connect to
          find the devices. It can have a "glob", for example: "/dev/ttyUSB*"
        """
        super(MultixX, self).__init__(name, role, ports, **kwargs)
        # Hw version is different if multi-channel
        self._hwVersion = "Omicron %s" % ", ". join(d.hwVersion for d in self._devices)

    @classmethod
    def _getAvailableDevices(cls, ports):
        if ports.startswith("/dev/fake"):
            names = [ports]
        elif os.name == "nt":
            # TODO
            # ports = ["COM" + str(n) for n in range(15)]
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        devices = []
        for n in names:
            try:
                acc = USBAccesser(n)
                d = DevxX(acc)
                devices.append(d)
            except (TypeError, IOError):
                logging.info("Port %s doesn't seem to have a Omicron single-channel device connected", n)

        return None, devices

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

        _, devices = cls._getAvailableDevices(ports)
        if devices:
            return [("Omicron LuxX", {"ports": ports})]
        else:
            return []


class HubxX(GenericxX):
    """
    Represents one Omicron device with multiple sources (ie, wavelengths), such
    as the LedHUB
    """

    def __init__(self, name, role, port, **kwargs):
        """
        port (string): name of the serial port to try to connect to
          find the device. It can have a "glob", for example: "/dev/ttyUSB*", in
          which case it will pick the first lighthub it finds.
        """
        super(HubxX, self).__init__(name, role, ports=port, **kwargs)
        self._hwVersion = ("Omicron %s (%s)" %
                           (self._master.hwVersion,
                            ", ". join(d.hwVersion for d in self._devices)))

    @classmethod
    def _getMasterDevices(cls, ports):
        if ports.startswith("/dev/fake"):
            names = [ports]
        elif os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        mdevs = []
        last_hwe = None
        for n in names:
            # Get the "master" device
            try:
                acc = USBAccesser(n)
                d = DevxX(acc, 0)
                mdevs.append(d)
            except HwError as ex:
                logging.info("Got HwError %s from device on port %s, will see if another device is ready", ex, n)
                last_hwe = ex
                continue
            except (TypeError, IOError):
                logging.info("Port %s doesn't seem to have a Omicron Hub device connected", n, exc_info=True)
                continue

        if not mdevs and last_hwe:
            # That's probably the device the user is looking for, so pass on the error
            raise last_hwe

        return mdevs

    @classmethod
    def _getAvailableDevices(cls, ports):
        mdevs = cls._getMasterDevices(ports)

        if len(mdevs) > 1:
            logging.warning("Multiple Omicron devices found on ports %s, will "
                            "only use port %s", ports, mdevs[0].acc.port)
        elif not mdevs:
            return None, []

        # Create a separate device for each channel
        devices = []
        md = mdevs[0]
        for c in md.channels:
            sd = DevxX(md.acc, c)
            devices.append(sd)

        return md, devices

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

        ret = []
        for d in cls._getMasterDevices(ports):
            ret.append(("Omicron Hub", {"port": d.acc.port}))

        return ret


class HubxXSimulator(object):
    """
    Simulates a LedHUB (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = b""  # what the commands sends back to the "host computer"
        self._input_buf = b""  # what we receive from the "host computer"

        # For simulating a "small error" (calling reset will fix it)
        self._error = 0x0001  # In error state, but it's now fine

        # Sub devices info: channel -> wavelength (nm) / power (mw)
        self._csi = {1: (400, 1400),
                     5: (500, 525),
        }

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

    def _sendAnswer(self, com, chan=None, ans=b""):
        if chan is None:
            rep = com + ans
        else:
            rep = b"%s[%d]%s" % (com, chan, ans)
        self._output_buf += b"!%s\r" % (rep,)

    def _parseMessage(self, msg):
        """
        msg (byte str): the message to parse (without the \r)
        return None: self._output_buf is updated if necessary
        """
        logging.debug("SIM: parsing %s", msg)
        m = re.match(br"\?(?P<com>[A-Za-z]{3})(\[(?P<chan>\d+)\])?((?P<args>.*))", msg)
        if not m:
            logging.error("Received unexpected message %s", to_str_escape(msg))
            return

        com = m.group("com")
        if m.group("chan"):
            chan = int(m.group("chan"))
        else:
            chan = None

        if m.group("args"):
            args = m.group("args").split(b"\xa7")
        else:
            args = None
        logging.debug("SIM: decoded message as %s [%s] %s", com, chan, args)

        # decode the command
        if com == b"GFw":
            self._sendAnswer(b"GFw", chan, b"LEDHUB\xa720\xa710.FAKE")
        elif com == b"GSN":
            self._sendAnswer(b"GSN", chan, b"123456.7")
        elif com == b"GAS":
            if self._error:
                self._sendAnswer(b"GAS", chan, b"00C9")  # Error
            else:
                self._sendAnswer(b"GAS", chan, b"02C2")  # Device on (bit 1) + Led ready (bit 6)
        elif com == b"GFB":
            self._sendAnswer(b"GFB", chan, b"%04X" % self._error)
        elif com == b"GLF":
            self._sendAnswer(b"GLF", chan, b"0201")  # External interlock
        elif com == b"RsC":
            # Hack to send both a confirmation and a $end command
            self._sendAnswer(b"RsC", chan, b">\r$RsC")
            self._error = 0
        elif com == b"GOM":
            self._sendAnswer(b"GOM", chan, b"FCFB")
        elif com == b"SOM":
            if len(args) == 1:
                om = int(args[0], 16)
                # We don't care actually
                self._sendAnswer(b"SOM", chan, b">")
            else:
                self._sendAnswer(b"UK")  # wrong instruction
        elif com == b"GMP":
            if chan is None:
                pw = 0
            else:
                _, pw = self._csi[chan]
            self._sendAnswer(b"GMP", chan, b"%d" % (pw,))
        elif com == b"GWH":
            self._sendAnswer(b"GWH", chan, b"23")
        elif com == "MTD":
            self._sendAnswer(b"MTD", chan, b"35.6")
        elif com == b"MTA":
            self._sendAnswer(b"MTA", chan, b"28.3")
        elif com == b"GSI":
            if chan is None:
                # Master -> return the sub devices
                mdev = sum(1 << (n - 1) for n in self._csi.keys())
                self._sendAnswer(b"GSI", chan, b"[m%d]0\xa70" % (mdev,))
            else:
                self._sendAnswer(b"GSI", chan, b"%d\xa7%d" % self._csi[chan])
        elif com == b"LOf":
            self._sendAnswer(b"LOf", chan, b">")
        elif com == b"LOn":
            self._sendAnswer(b"LOn", chan, b">")
        elif com == b"POf":
            self._sendAnswer(b"POf", chan, b">")
        elif com == b"POn":
            self._sendAnswer(b"POn", chan, b">")
        elif com == b"SLP":
            if chan in self._csi and len(args) == 1:
                pw = int(args[0], 16)
                _, mpw = self._csi[chan]
                # self._cpw[chan] = mpw * pw / 0xfff
                self._sendAnswer(b"SLP", chan, b">")
            else:
                self._sendAnswer(b"UK")  # wrong instruction
        elif com == b"SPP":
            if chan in self._csi and len(args) == 1:
                per = float(args[0])
                _, mpw = self._csi[chan]
                # self._cpw[chan] = mpw * per / 100
                self._sendAnswer(b"SPP", chan, b">")
            else:
                self._sendAnswer(b"UK")  # wrong instruction
        elif com == b"TPP":
            if chan in self._csi and len(args) == 1:
                per = float(args[0])
                _, mpw = self._csi[chan]
                # self._cpw[chan] = mpw * per / 100
                self._sendAnswer(b"TPP", chan, b">")
            else:
                self._sendAnswer(b"UK")  # wrong instruction
        else:
            logging.warning("SIM: Unsupported instruction %s", to_str_escape(com))
            self._sendAnswer(b"UK")  # unknown instruction
