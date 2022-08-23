# -*- coding: utf-8 -*-
'''
Created on 19 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
import glob
import binascii
import logging
from odemis import model, util
import odemis
from odemis.model import HwError
from odemis.util import driver
import os
import serial
import threading
import time
from past.builtins import long

# Colour name (lower case) to source ID (as used in the device)
COLOUR_TO_SOURCE = {"red": 0,
                    "green": 1, # cf yellow
                    "cyan": 2,
                    "uv": 3,
                    "yellow": 4, # actually filter selection for green/yellow
                    "blue": 5,
                    "teal": 6,
                    }

# map of source number to bit & address for source intensity setting
SOURCE_TO_BIT_ADDR = {0: (3, 0x18),  # Red
                      1: (2, 0x18),  # Green
                      2: (1, 0x18),  # Cyan
                      3: (0, 0x18),  # UV
                      4: (2, 0x18),  # Yellow is the same source as Green
                      5: (0, 0x1A),  # Blue
                      6: (1, 0x1A),  # Teal
                     }

# The default sources, as found in the documentation, and as the default
# Spectra LLE can be bought. Used only by scan().
# source name -> 99% low, 25% low, centre, 25% high, 99% high in m
DEFAULT_SOURCES = {"red": (615.e-9, 625.e-9, 633.e-9, 640.e-9, 650.e-9),
                   "green": (525.e-9, 540.e-9, 550.e-9, 555.e-9, 560.e-9),
                   "cyan": (455.e-9, 465.e-9, 475.e-9, 485.e-9, 495.e-9),
                   "UV": (375.e-9, 390.e-9, 400.e-9, 402.e-9, 405.e-9),
                   "yellow": (565.e-9, 570.e-9, 575.e-9, 580.e-9, 595.e-9),
                   "blue": (420.e-9, 430.e-9, 438.e-9, 445.e-9, 455.e-9),
                   "teal": (495.e-9, 505.e-9, 513.e-9, 520.e-9, 530.e-9),
                  }
# Maximum power taken from the manual file 4487-LUM.54-10009B.SPECTRA.X.pdf
DEFAULT_SOURCES_POWERS = {"red":0.231,
                          "green": 0.260,
                          "cyan": 0.196,
                          "uv": 0.295,
                          "yellow": 0.310,
                          "blue": 0.256,
                          "teal": 0.62,
                  }


class LLE(model.Emitter):
    '''
    Represent (interfaces) a Lumencor Light Engine (multi-channels light engine). It
    is connected via a serial port (physically over USB). It is written for the
    Spectra, but might be compatible with other hardware with less channels.
    Documentation: Spectra TTL IF Doc.pdf. Micromanager's driver "LumencorSpectra"
    might also be a source of documentation (BSD license).

    The API doesn't allow asynchronous actions. So the switch of source/intensities
    is considered instantaneous by the software. It obviously is not, but the
    documentation states about 200 μs. As it's smaller than most camera frame
    rates, it shouldn't matter much.
    '''

    def __init__(self, name, role, port, sources, _serial=None, **kwargs):
        """
        port (string): name of the serial port to connect to. Can be a pattern,
         in which case, all the ports fitting the pattern will be tried, and the
         first one which looks like an LLE will be used.
        sources (dict string -> 5-tuple of float): the light sources (by colour).
         The string is one of the seven names for the sources: "red", "cyan",
         "green", "UV", "yellow", "blue", "teal". They correspond to fix
         number in the LLE (cf documentation). The tuple contains the wavelength
         in m for the 99% low, 25% low, centre/max, 25% high, 99% high. They do
         no have to be extremely precise. The most important is the centre, and
         that they are all increasing values. If the device doesn't have the
         source it can be skipped.
        _serial (serial): for internal use only, directly use a opened serial
         connection
        """
        # start with this opening the port: if it fails, we are done
        if _serial is not None:
            self._try_recover = False
            self._serial = _serial
            self._port = ""
        else:
            self._serial, self._port = self._findDevice(port)
            logging.info("Found LLE device on port %s", self._port)
            self._try_recover = True

        # to acquire before sending anything on the serial port
        self._ser_access = threading.Lock()

        # Init the LLE
        self._initDevice()

        if _serial is not None: # used for port testing => only simple init
            return

        # parse source and do some sanity check
        if not sources or not isinstance(sources, dict):
            logging.error("sources argument must be a dict of source name -> wavelength 5 points")
            raise ValueError("Incorrect sources argument")

        self._source_id = [] # source number for each spectra
        self._gy = [] # indexes of green and yellow source
        self._rcubt = [] # indexes of other sources
        spectra = [] # list of the 5 wavelength points
        self._max_power = []
        for cn, wls in sources.items():
            cn = cn.lower()
            if cn not in COLOUR_TO_SOURCE:
                raise ValueError("Sources argument contains unknown colour '%s'" % cn)
            if len(wls) != 5:
                raise ValueError("Sources colour '%s' doesn't have exactly 5 wavelength points" % cn)
            prev_wl = 0
            for wl in wls:
                if 0 > wl or wl > 100e-6:
                    raise ValueError("Sources colour '%s' has unexpected wavelength = %f nm"
                                     % (cn, wl * 1e9))
                if prev_wl > wl:
                    raise ValueError("Sources colour '%s' has unsorted wavelengths" % cn)
            self._source_id.append(COLOUR_TO_SOURCE[cn])
            if cn in ["green", "yellow"]:
                self._gy.append(len(spectra))
            else:
                self._rcubt.append(len(spectra))
            self._max_power.append(DEFAULT_SOURCES_POWERS[cn])
            spectra.append(tuple(wls))

        model.Emitter.__init__(self, name, role, **kwargs)

        self._shape = ()
        self.power = model.ListContinuous(value=[0.0] * len(spectra),
                                          range=((0.,) * len(spectra), tuple(self._max_power),),
                                          unit="W", cls=(int, long, float),)

        self.spectra = model.ListVA(spectra, unit="m", readonly=True)

        self._prev_power = [None] * len(spectra) # => will update for sure
        self._updateIntensities() # turn off every source

        self.power.subscribe(self._updatePower)
        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__,
                                                      driver.getSerialDriver(self._port))
        self._hwVersion = "Lumencor Light Engine" # hardware doesn't report any version

        # Update temperature every 10s
        current_temp = self.GetTemperature()
        self.temperature = model.FloatVA(current_temp, unit=u"°C", readonly=True)
        self._temp_timer = util.RepeatingTimer(10, self._updateTemperature,
                                               "LLE temperature update")
        self._temp_timer.start()

    def _sendCommand(self, com):
        """
        Send a command which does not expect any report back
        com (bytearray): command to send
        """
        assert(len(com) <= 10) # commands cannot be long
        logging.debug("Sending: %s", binascii.hexlify(com))
        while True:
            try:
                self._serial.write(com)
                break
            except IOError:
                if self._try_recover:
                    self._tryRecover()
                else:
                    raise

    def _readResponse(self, length):
        """
        receive a response from the engine
        length (0<int): length of the response to receive
        return (bytearray of length == length): the response received (raw)
        raises:
            IOError in case of timeout
        """
        response = bytearray()
        while len(response) < length:
            char = self._serial.read()
            if not char:
                if self._try_recover:
                    self._tryRecover()
                    # TODO resend the question
                    return b"\x00" * length
                else:
                    raise IOError("Device timeout after receiving '%s'." % binascii.hexlify(response))
            response.extend(char)

        logging.debug("Received: %s", binascii.hexlify(response))
        return response

    def _initDevice(self):
        """
        Initialise the device
        """
        with self._ser_access:
            # from the documentation:
            self._sendCommand(b"\x57\x02\xff\x50") # Set GPIO0-3 as open drain output
            self._sendCommand(b"\x57\x03\xab\x50") # Set GPI05-7 push-pull out, GPIO4 open drain out
            # empty the serial port (and also wait for the device to initialise)
            garbage = self._serial.read(100)
            if len(garbage) == 100:
                raise IOError("Device keeps sending unknown data")

    def _setDeviceManual(self):
        """
        Reset the device to the manual mode
        """
        with self._ser_access:
            # from the documentation:
            self._sendCommand(b"\x57\x02\x55\x50") # Set GPIO0-3 as input
            self._sendCommand(b"\x57\x03\x55\x50") # Set GPI04-7 as input

    def _tryRecover(self):
        # no other access to the serial port should be done
        # so _ser_access should already be acquired

        # Retry to open the serial port (in case it was unplugged)
        while True:
            try:
                self._serial.close()
                self._serial = None
            except Exception:
                pass
            try:
                logging.debug("retrying to open port %s", self._port)
                self._serial = self.openSerialPort(self._port)
                self._serial.write(b"\x57\x02\xff\x50")
            except IOError:
                time.sleep(2)
            except Exception:
                logging.exception("Unexpected error while trying to recover device")
                raise
            else:
                break

        # Now it managed to write, let's see if we manage to read
        while True:
            try:
                logging.debug("retrying to communicate with device on port %s", self._port)
                self._serial.write(b"\x57\x02\xff\x50") # init
                self._serial.write(b"\x57\x03\xab\x50")
                time.sleep(1)
                self._serial.write(b"\x53\x91\x02\x50") # temp
                resp = bytearray()
                for i in range(2):
                    char = self._serial.read()
                    if not char:
                        raise IOError()
                    resp.append(char)
                if resp not in [b"\x00\x00", b"\xff\xff"]:
                    break # it's look good
            except IOError:
                time.sleep(2)

        # it now should be accessible again
        self._prev_power = [None] * len(self.power.value) # => will update for sure
        self._ser_access.release() # because it will try to write on the port
        self._updateIntensities() # reset the sources
        self._ser_access.acquire()
        logging.info("Recovered device on port %s", self._port)

    # The source ID is more complicated than it looks like:
    # 0, 2, 3, 5, 6 are as is. 1 is for Yellow/Green. Setting 4 selects
    # whether yellow (activated) or green (deactivated) is used.
    def _enableSources(self, sources):
        """
        Select the light sources which must be enabled.
        Note: If yellow/green (1/4) are activated, no other channel will work.
        Yellow has precedence over green.
        sources (set of 0<= int <= 6): source to be activated, the rest will be turned off
        """
        com = bytearray(b"\x4F\x00\x50") # the second byte will contain the sources to activate

        # Do we need to activate Green filter?
        if (1 in sources or 4 in sources) and len(sources) > 1:
            logging.warning("Asked to activate multiple conflicting sources %r", sources)

        s_byte = 0x7f # reset a bit to 0 to activate
        for s in sources:
            assert(0 <= s <= 6)
            if s == 4: # for yellow, "green/yellow" (1) channel must be activated (=0)
                s_byte &= ~ (1 << 1)
            s_byte &= ~(1 << s)

        com[1] = s_byte
        with self._ser_access:
            self._sendCommand(com)

    def _setSourceIntensity(self, source, intensity):
        """
        Select the intensity of the given source (it needs to be activated separately).
        source (0 <= int <= 6): source number
        intensity (0<= int <= 255): intensity value 0=> off, 255 => fully bright
        """
        assert(0 <= source <= 6)
        bit, addr = SOURCE_TO_BIT_ADDR[source]

        com = bytearray(b"\x53\x18\x03\x0F\xFF\xF0\x50")
        #                       ^^       ^   ^  ^ : modified bits
        #                    address    bit intensity

        # address
        com[1] = addr
        # bit
        com[3] = 1 << bit

        # intensity is inverted
        b_intensity = 0xfff0 & (((~intensity) << 4) | 0xf00f)
        com[4] = b_intensity >> 8
        com[5] = b_intensity & 0xff

        with self._ser_access:
            self._sendCommand(com)

    def GetTemperature(self):
        """
        returns (-300 < float < 300): temperature in degrees
        """
        # From the documentation:
        # The most significant 11 bits of the two bytes are used
        # with a resolution of 0.125 deg C.
        with self._ser_access:
            self._sendCommand(b"\x53\x91\x02\x50")
            resp = self._readResponse(2)
        val = 0.125 * ((((resp[0] << 8) | resp[1]) >> 5) & 0x7ff)
        return val

    def _updateTemperature(self):
        temp = self.GetTemperature()
        self.temperature._value = temp
        self.temperature.notify(self.temperature.value)
        logging.debug("LLE temp is %g", temp)

    def _getIntensityGY(self, intensities):
        """
        return the intensity of green and yellow (they share the same intensity)
        """
        try:
            yellow_i = self._source_id.index(4)
        except ValueError:
            yellow_i = None

        try:
            green_i = self._source_id.index(1)
        except ValueError:
            green_i = None

        # Yellow has precedence over green
        if yellow_i is not None and intensities[yellow_i]: # don't use if None or 0
            return intensities[yellow_i], yellow_i
        elif green_i is not None:
            return intensities[green_i], green_i
        else:
            # In case neither yellow or green found, just return yellow (as it has precedence)
            return 0, yellow_i

    def _updateIntensities(self):
        """
        Update the sources setting of the hardware, if necessary
        """
        need_update = False
        for i, p in enumerate(self.power.value):
            if self._prev_power[i] != p:
                need_update = True
                # Green and Yellow share the same source => do it later
                if i in self._gy:
                    continue
                sid = self._source_id[i]
                self._setSourceIntensity(sid, int(round(p * 255 / self._max_power[i])))

        # special for Green/Yellow: merge them
        prev_gy = self._getIntensityGY(self._prev_power)
        gy = self._getIntensityGY(self.power.value)
        if prev_gy != gy:
            self._setSourceIntensity(1, int(round(gy[0] * 255 / self._max_power[gy[1]])))

        if need_update:
            toTurnOn = set()
            for i, p in enumerate(self.power.value):
                if p > self._max_power[i] / 255:
                    toTurnOn.add(self._source_id[i])
            self._enableSources(toTurnOn)

        self._prev_power = self.power.value

    def _updatePower(self, value):
        # set the actual values
        # TODO need to do better for selection
        # Green (1) and Yellow (4) can only be activated independently
        # If only one of them selected: easy
        # If only other selected: easy
        # If only green and yellow: pick the strongest
        # If mix: if the max of GY > max other => pick G or Y, other pick others
        intensities = list(value)  # duplicate
        max_gy = max([intensities[i] for i in self._gy] + [0])  # + [0] to always have a non-empty list
        max_others = max([intensities[i] for i in self._rcubt] + [0])
        if max_gy <= max_others:
            # we pick others => G/Y becomes 0
            for i in self._gy:
                intensities[i] = 0.0
        else:
            # We pick G/Y (the strongest of the two)
            for i in self._rcubt:
                intensities[i] = 0.0
            if len(self._gy) == 2:  # only one => nothing to do
                if intensities[self._gy[0]] > intensities[self._gy[1]]:
                    # first is the strongest
                    intensities[self._gy[1]] = 0.
                else:  # second is the strongest
                    intensities[self._gy[0]] = 0.

        # set the actual values
        for i, intensity in enumerate(intensities):
            # clip + indicate minimum step
            if intensity / self._max_power[i] < 1 / 256:
                logging.debug("Clipping intensity from %f to 0", intensity)
                intensity = 0.
            elif intensity / self._max_power[i] > 255 / 256:
                intensity = self._max_power[i]
            self.power.value[i] = intensity

        self._updateIntensities()

    def terminate(self):
        if hasattr(self, "_temp_timer") and self._temp_timer:
            self._temp_timer.cancel()
            self._temp_timer = None

        if self._serial:
            self._setDeviceManual()
            self._serial.close()
            self._serial = None

        super(LLE, self).terminate()

    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        # only the temperature response something
        try:
            temp = self.GetTemperature()
            if temp == 0:
                # means that we read only 0's
                logging.warning("device reports suspicious temperature of exactly 0°C.")
            if 0 < temp < 250:
                return True
        except Exception:
            logging.exception("Selftest failed")

        return False

    @classmethod
    def _findDevice(cls, ports):
        """
        Look for a compatible device
        ports (str): pattern for the port name
        return serial, port:
            serial: serial port found, and open
            port (str): the name of the port used
        raises:
            IOError: if no device are found
        """
        # We are called very early, so no attribute is to be expected
        if os.name == "nt":
            # TODO
            #ports = ["COM" + str(n) for n in range (15)]
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        for n in names:
            try:
                ser = cls.openSerialPort(n)
                dev = LLE(None, None, port=None, sources=None, _serial=ser)
            except serial.SerialException:
                # not possible to use this port? next one!
                continue

            # Try to connect and get back some answer.
            # The LLE only answers back for the temperature
            try:
                for i in range(3): # 3 times in a row good answer?
                    temp = dev.GetTemperature()
                    # avoid 0 and 255 (= only 000's or 1111's), which is bad sign
                    if not(0 < temp < 250):
                        raise IOError()
            except Exception:
                logging.debug("Port %s doesn't seem to have a LLE device connected", n)
                continue
            return ser, n # found it!
        else:
            raise HwError("Failed to find a Lumencor Light Engine on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

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
                ports = ["COM" + str(n) for n in range (0,8)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')

        logging.info("Serial ports scanning for Lumencor light engines in progress...")
        found = []  # (list of 2-tuple): name, kwargs
        for p in ports:
            try:
                logging.debug("Trying port %s", p)
                cls._findDevice(p)
            except Exception:
                continue
            else:
                found.append(("LLE", {"port": p, "sources": DEFAULT_SOURCES}))

        return found

    @staticmethod
    def openSerialPort(port):
        """
        Opens the given serial port the right way for the Spectra LLE.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1  # s
        )

        return ser


class FakeLLE(LLE):
    """
    For testing purpose only. To test the driver without hardware.
    Pretends to connect but actually just print the commands sent.
    """
    def __init__(self, name, role, port, *args, **kwargs):
        logging.info("Staring fakeLLE")
        # force a port pattern with just one existing file
        LLE.__init__(self, name, role, port="/dev/null", *args, **kwargs)

    @staticmethod
    def openSerialPort(port):
        """
        opens a fake port, connected to the simulator
        """
        ser = LLESimulator(
            port=port,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1  # s
        )

        return ser


class LLESimulator(object):
    """
    Simulates a LLE (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = b"" # what the commands sends back to the "host computer"
        self._input_buf = bytearray() # what we receive from the "host computer"

    def write(self, data):
        self._input_buf += data
        self._processCommand()

    def read(self, size=1):
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        if len(ret) < size:
            # simulate timeout
            time.sleep(self.timeout)
        return ret

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _processCommand(self):
        """
        process the command, and put the result in the output buffer
        com (str): command
        """

        while True:
            if self._input_buf[:4] == bytearray(b"\x53\x91\x02\x50"):
                # only the temperature returns something
                self._output_buf += b"\x26\xA0" # 38.625°C
                processed = 4
            elif len(self._input_buf) >= 4 and self._input_buf[0] == 0x57:
                processed = 4
            elif len(self._input_buf) >= 6 and self._input_buf[0] == 0x53:
                processed = 6
            elif len(self._input_buf) >= 3 and self._input_buf[0] == 0x4f:
                processed = 3
            else:
                processed = 0

            if processed:
                com = self._input_buf[:processed]
                self._input_buf = self._input_buf[processed:]
                logging.debug("Sim LLE received %s", binascii.hexlify(com))
            else:
                # remove everything useless
                changed = False
                while self._input_buf and self._input_buf[:1] not in [0x57, 0x53, 0x4f]:
                    changed = True
                    self._input_buf = self._input_buf[1:]
                if not changed:
                    return # reached the end of the flow, the rest is unfinished
