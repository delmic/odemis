# -*- coding: utf-8 -*-
'''
Created on 5 Dec 2012

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
from past.builtins import basestring
from collections.abc import Iterable
import glob
import logging
import math
import numbers
from odemis import model
import odemis
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
from odemis.util import driver, to_str_escape
import os
import re
import serial
import struct
import threading
import time


def hextof(s):
    """
    Helper function to convert float coded in hexadecimal as in the Windows
    registry into a standard float number.
    s (str): comma separated values of 8 x 8 bit hexadecimals.
      ex: 29,5c,8f,c2,f5,28,06,c0
    return (float): value
    raise ValueError: if not possible to convert
    """
    if len(s) != 23:
        raise ValueError("Cannot convert %s to a float" % (s,))

    try:
        d = bytearray(int(c, 16) for c in s.split(","))
        return struct.unpack("<d", d)[0]
    except (ValueError, struct.error):
        raise ValueError("Cannot convert %s to a float" % (s,))


# This module drives the Acton SpectraPro spectrograph devices. It is tested with
# the SpectraPro 2150i, but should work with many other devices as the commands
# are the same since the SpectraPro 300i. This drivers is targeted at using the
# device as a spectrograph (and not as a monograph), associated to a CCD. The
# device is supposed to have already been configured and calibrated.
#
# The basic of this device is to move mirror and lenses in order to select a
# specific range of wavelength observed. Therefore it's an actuator, with special
# characteristics. For background knowledge on such system, see wikipedia entry
# on "Czerny-Turner monochromator".
#
# Some vocabulary:
# Turret: a rotating holder that allows to change the current grating
# (Diffraction) grating: the optical element that diffracts the light. It is
# composed of many parallel grooves. It's "power" is expressed in groove density
# (g/mm).
# Blaze: another property of a grating that optimise the diffraction at a certain
# wavelength, defined in m (or nm).
#
# The devices have a turret with 2 or 3 positions (gratings). Each grating can
# be shifted to be centred on a specific wavelength. The devices might also
# have mirrors to select input and outputs.
#
# All the documentation can be found online at:
# ftp://ftp.piacton.com/Public/Manuals/Acton/
# fsc2 also supports the SpectraPro 300i, which has similar commands.
#
# The documentation says turning the turret can take up to 20 s (i.e., far from
# instantaneous). The documentation gives all the commands in uppercase, but
# from experiments, only commands in lowercase work.
#
# Note that acquiring data directly gives you _uncalibrated_ data.
# The calibration is typically done in Princeton Instruments's Winspec (old) or
# LightField (new). The calibration of the centre wavelength _could_ be saved
# in the spectrograph flash, but it's not currently what is done. In any case,
# the CCD pixel -> wavelength calibration data must be exported from the
# calibrating software. In the case of Winspec, the data is sorted in the
# Windows registry, and currently it must be manually copy-pasted.
#
class SPError(IOError):
    """Error related to the hardware behaviour"""
    pass

# TODO: all these values seem available from MONO-EESTATUS
# Or maybe from:
#  ?EECCD-CALIBRATED
#  ?EECCD-OFFSETS
#  ?EECCD-GADJUSTS
#  ?EECCD-FOCALLENS
#  ?EECCD-HALFANGLES
#  ?EECCD-DETANGLES
#  ?EELEFT-EDGES
#  ?EECENTER-PIXELS
#  ?EERIGHT-EDGE

# From the specifications
# string -> value : model name -> length (m)/angle (°)
FOCAL_LENGTH_OFFICIAL = { # m
                         "SP-2-150i": 150e-3, # 150mm
                         "SP-2-300i": 300e-3,
                         "SP-2-500i": 500e-3,
                         "SP-2-750i": 750e-3,
                         "SP-FAKE": 300e-3,
                         }
INCLUSION_ANGLE_OFFICIAL = { # in degrees
                         "SP-2-150i": 24.66 * 2,
                         "SP-2-300i": 15.15 * 2,
                         "SP-2-500i": 8.59 * 2,
                         "SP-2-750i": 6.55 * 2,
                         "SP-FAKE": 15.15 * 2,
                         }
# maximum number of gratings per turret
MAX_GRATINGS_NUM = { # gratings
                     "SP-2-150i": 2,
                     "SP-2-300i": 3,
                     "SP-2-500i": 3,
                     "SP-2-750i": 3,
                     "SP-FAKE": 3,
                     }

class SpectraPro(model.Actuator):
    def __init__(self, name, role, port, turret=None, calib=None,
                 _noinit=False, dependencies=None, **kwargs):
        """
        port (string): name of the serial port to connect to.
        turret (None or 1<=int<=3): turret number set-up. If None, consider that
          the current turret known by the device is correct.
        calib (None or list of (int, int and 5 x (float or str))):
          calibration data, as saved by Winspec. Data can be either in float
          or as an hexadecimal value "hex:9a,99,99,99,99,79,40,40"
           blaze in nm, groove gl/mm, center adjust, slope adjust,
           focal length, inclusion angle, detector angle
        inverted (None): it is not allowed to invert the axes
        dependencies (dict str -> Component): "ccd" should be the CCD used to acquire
         the spectrum.
        _noinit (boolean): for internal use only, don't try to initialise the device
        """
        if kwargs.get("inverted", None):
            raise ValueError("Axis of spectrograph cannot be inverted")

        # start with this opening the port: if it fails, we are done
        try:
            self._serial = self.openSerialPort(port)
        except serial.SerialException:
            raise HwError("Failed to find spectrograph %s (on port '%s'). "
                          "Check the device is turned on and connected to the "
                          "computer. You might need to turn it off and on again."
                          % (name, port))
        self._port = port

        # to acquire before sending anything on the serial port
        self._ser_access = threading.Lock()

        self._try_recover = False
        if _noinit:
            return

        self._initDevice()
        self._try_recover = True

        try:
            self._ccd = dependencies["ccd"]
        except (TypeError, KeyError):
            # TODO: only needed if there is calibration info (for the pixel size)
            # otherwise it's fine without CCD.
            raise ValueError("Spectrograph needs a dependency 'ccd'")

        # according to the model determine how many gratings per turret
        model_name = self.GetModel()
        self.max_gratings = MAX_GRATINGS_NUM.get(model_name, 3)

        if turret is not None:
            if turret < 1 or turret > self.max_gratings:
                raise ValueError("Turret number given is %s, while expected a value between 1 and %d" %
                                 (turret, self.max_gratings))
            self.SetTurret(turret)
            self._turret = turret
        else:
            self._turret = self.GetTurret()

        # for now, it's fixed (and it's unlikely to be useful to allow less than the max)
        wl_speed = 1000e-9 / 10  # about 1000 nm takes 10s => max speed in m/s
        self.speed = model.MultiSpeedVA({"wavelength": wl_speed}, range=[wl_speed, wl_speed], unit="m/s",
                                        readonly=True)

        gchoices = self.GetGratingChoices()
        # remove the choices which are not valid for the current turret
        for c in gchoices:
            t = 1 + (c - 1) // self.max_gratings
            if t != self._turret:
                del gchoices[c]

        # TODO: report the grating with its wavelength range (possible to compute from groove density + blaze wl?)
        # range also depends on the max grating angle (40°, CCD pixel size, CCD horizontal size, focal length,+ efficienty curve?)
        # cf http://www.roperscientific.de/gratingcalcmaster.html

        # TODO: a more precise way to find the maximum wavelength (looking at the available gratings?)
        # TODO: what's the min? 200nm seems the actual min working, although wavelength is set to 0 by default !?
        axes = {"wavelength": model.Axis(unit="m", range=(0, 2400e-9),
                                         speed=(wl_speed, wl_speed)),
                "grating": model.Axis(choices=gchoices)
                }
        # provides a ._axes
        model.Actuator.__init__(self, name, role, axes=axes, dependencies=dependencies, **kwargs)

        # First step of parsing calib parmeter: convert to (int, int) -> ...
        calib = calib or ()
        if not isinstance(calib, Iterable):
            raise ValueError("calib parameter must be in the format "
                             "[blz, gl, ca, sa, fl, ia, da], "
                             "but got %s" % (calib,))
        dcalib = {}
        for c in calib:
            if not isinstance(c, Iterable) or len(c) != 7:
                raise ValueError("calib parameter must be in the format "
                                 "[blz, gl, ca, sa, fl, ia, da], "
                                 "but got %s" % (c,))
            gt = (c[0], c[1])
            if gt in dcalib:
                raise ValueError("calib parameter contains twice calibration for "
                                 "grating (%d nm, %d gl/mm)" % gt)
            dcalib[gt] = c[2:]

        # store calibration for pixel -> wavelength conversion and wavelength offset
        # int (grating number 1 -> 9) -> center adjust, slope adjust,
        #     focal length, inclusion angle/2, detector angle
        self._calib = {}
        # TODO: read the info from MONO-EESTATUS (but it's so
        # huge that it's not fun to parse). There is also detector angle.
        dfl = FOCAL_LENGTH_OFFICIAL[model_name] # m
        dia = math.radians(INCLUSION_ANGLE_OFFICIAL[model_name]) # rad
        for i in gchoices:
            # put default values
            self._calib[i] = (0, 0, dfl, dia, 0)
            try:
                blz = self._getBlaze(i) # m
                gl = self._getGrooveDensity(i) # gl/m
            except ValueError:
                logging.warning("Failed to parse info of grating %d" % i, exc_info=True)
                continue

            # parse calib info
            gt = (int(blz * 1e9), int(gl * 1e-3))
            if gt in dcalib:
                calgt = dcalib[gt]
                ca = self._readCalibVal(calgt[0]) # ratio
                sa = self._readCalibVal(calgt[1]) # ratio
                fl = self._readCalibVal(calgt[2]) * 1e-3 # mm -> m
                ia = math.radians(self._readCalibVal(calgt[3])) # ° -> rad
                da = math.radians(self._readCalibVal(calgt[4])) # ° -> rad
                self._calib[i] = ca, sa, fl, ia, da
                logging.info("Calibration data for grating %d (%d nm, %d gl/mm) "
                             "-> %s" % (i, gt[0], gt[1], self._calib[i]))
            else:
                logging.warning("No calibration data for grating %d "
                                "(%d nm, %d gl/mm)" % (i, gt[0], gt[1]))

        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver.getSerialDriver(port))
        self._hwVersion = "%s (s/n: %s)" % (model_name, (self.GetSerialNumber() or "Unknown"))

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

        # for storing the latest calibrated wavelength value
        self._wl = (None, None, None) # grating id, raw center wl, calibrated center wl
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

    def _readCalibVal(self, rawv):
        """
        rawv (str or number)
        return (float)
        """
        if isinstance(rawv, basestring):
            if rawv.startswith("hex:"):
                rawv = rawv[4:]
            return hextof(rawv)
        elif isinstance(rawv, numbers.Real):
            return rawv
        else:
            raise ValueError("Cannot convert %s to a number" % (rawv,))

    # Low-level methods: to access the hardware (should be called with the lock acquired)

    def _sendOrder(self, *args, **kwargs):
        """
        Send a command which does not expect any report back (just OK)
        com (str): command to send (non including the \r)
        raise
            SPError: if the command doesn't answer the expected OK.
            IOError: in case of timeout
        """
        # same as a query but nothing to do with the response
        self._sendQuery(*args, **kwargs)

    def _sendQuery(self, com, timeout=1):
        """
        Send a command which expects a report back (in addition to the OK)
        com (str): command to send (non including the \r)
        timeout (0<float): maximum read timeout for the response
        return (str): the response received (without the ok)
        raises:
            SPError: if the command doesn't answer the expected OK.
            IOError: in case of timeout
        """
        # All commands or strings of commands must be terminated with a carriage
        # return (0D hex). The monochromator responds to a command when the
        # command has been completed by returning the characters " ok" followed by
        # carriage return and line feed (hex ASCII sequence 20 6F 6B 0D 0A).
        # Examples of error answers:
        # MODEL\r
        # \x00X\xf0~\x00X\xf0~MODEL ? \r\n
        # ?\r
        # \r\nAddress Error \r\nA=3F4F4445 PC=81444

        assert(1 < len(com) <= 100) # commands cannot be long
        com += "\r"
        com = com.encode('latin1')
        logging.debug("Sending: %s", to_str_escape(com))
        # send command until it succeeds
        while True:
            try:
                self._serial.write(com)
                break
            except IOError:
                if self._try_recover:
                    self._tryRecover()
                else:
                    raise

        # read response until timeout or known end of response
        response = b""
        timeend = time.time() + timeout
        while ((time.time() <= timeend) and
               not (response.endswith(b" ok\r\n") or response.endswith(b"? \r\n"))):
            self._serial.timeout = max(0.1, timeend - time.time())
            char = self._serial.read()
            if not char: # timeout
                break
            response += char

        logging.debug("Received: %s", to_str_escape(response))
        if response.endswith(b" ok\r\n"):
            return response[:-5].decode('latin1')
        else:
            # if the device hasn't answered anything, it might have been disconnected
            if len(response) == 0:
                if self._try_recover:
                    self._tryRecover()
                else:
                    raise IOError("Device timeout after receiving '%s'." % to_str_escape(response))
            else: # just non understood command
                # empty the serial port
                self._serial.timeout = 0.1
                garbage = self._serial.read(100)
                if len(garbage) == 100:
                    raise IOError("Device keeps sending data")
                response += garbage
                raise SPError("Sent '%s' and received error: '%s'" %
                              (to_str_escape(com), to_str_escape(response)))

    def _tryRecover(self):
        # no other access to the serial port should be done
        # so _ser_access should already be acquired

        # Retry to open the serial port (in case it was unplugged)
        while True:
            try:
                self._serial.close()
                self._serial = None
            except:
                pass
            try:
                logging.debug("retrying to open port %s", self._port)
                self._serial = self.openSerialPort(self._port)
            except IOError:
                time.sleep(2)
            except Exception:
                logging.exception("Unexpected error while trying to recover device")
                raise
            else:
                break

        self._try_recover = False # to avoid recursion
        self._initDevice()
        self._try_recover = True

    def _initDevice(self):
        # If no echo is desired, the command NO-ECHO will suppress the echo. The
        # command ECHO will return the SP-2150i to the default echo state.
        #
        # If is connected via the real serial port (not USB), it is in echo
        # mode, so we first need to disable it, while allowing echo of the
        # command we've just sent.

        try:
            self._sendOrder("no-echo")
        except SPError:
            logging.info("Failed to disable echo, hopping the device has not echo anyway")

        # empty the serial port
        self._serial.timeout = 0.1
        garbage = self._serial.read(100)
        if len(garbage) == 100:
            raise IOError("Device keeps sending data")

    def GetTurret(self):
        """
        returns (1 <= int <= 3): the current turret number
        """
        # ?TURRET Returns the correctly installed turret numbered 1 - 3
        res = self._sendQuery("?turret")
        val = int(res)
        if val < 1 or val > 3:
            raise SPError("Unexpected turret number '%s'" % res)
        return val

    def SetTurret(self, t):
        """
        Set the number of the current turret (for correct settings by the hardware)
        t (1 <= int <= 3): the turret number
        Raise:
            ValueError if the turret has no grating configured
        """
        # TURRET  Specifies the presently installed turret or the turret to be installed.
        # Doesn't change the hardware, just which gratings are available

        assert(1 <= t <= 3)
        # TODO check that there is grating configured for this turret (using GetGratingChoices)
        self._sendOrder("%d turret" % t)

    # regex to read the gratings
    RE_NOTINSTALLED = re.compile("\D*(\d+)\s+Not Installed")
    RE_INSTALLED = re.compile("\D*(\d+)\s+(\d+)\s*g/mm BLZ=\s*([0-9][.0-9]*)\s*(nm|NM|um|UM)")
    RE_GRATING = re.compile("\D*(\d+)\s+(.+\S)\s*\r")
    def GetGratingChoices(self):
        """
        return (dict int -> string): grating number to description
        """
        # ?GRATINGS Returns the list of installed gratings with position groove density and blaze. The
        #  present grating is specified with an arrow.
        # Example output:
        #  \r\n 1  300 g/mm BLZ=  500NM \r\n\x1a2  300 g/mm BLZ=  750NM \r\n 3  Not Installed     \r\n 4  Not Installed     \r\n 5  Not Installed     \r\n 6  Not Installed     \r\n 7  Not Installed     \r\n 8  Not Installed     \r\n ok\r\n
        #  \r\n\x1a1  600 g/mm BLZ=  1.6UM \r\n 2  150 g/mm BLZ=    2UM \r\n 3  Not Installed     \r\n 4  Not Installed     \r\n 5  Not Installed     \r\n 6  Not Installed     \r\n 7  Not Installed     \r\n 8  Not Installed     \r\n 9  Not Installed     \r\n ok\r\n

        # From the spectrapro_300i_ll.c of fsc2, it seems the format is:
        # non-digit*,digits=grating number,spaces,"Not Installed"\r\n
        # non-digit*,digits=grating number,space+,digit+:g/mm,space*,"g/mm BLZ=", space*,digit+:blaze wl in nm,space*,"nm"\r\n

        res = self._sendQuery("?gratings")
        gratings = {}
        for line in res[:-1].split("\n"): # avoid the last \n to not make an empty last line
            m = self.RE_NOTINSTALLED.search(line)
            if m:
                logging.debug("Decoded grating %s as not installed, skipping.", m.group(1))
                continue
            m = self.RE_GRATING.search(line)
            if not m:
                logging.debug("Failed to decode grating description '%s'", line)
                continue
            num = int(m.group(1))
            desc = m.group(2)
            # TODO: provide a nicer description, using RE_INSTALLED?
            gratings[num] = desc

        return gratings

    RE_GDENSITY = re.compile("(\d+)\s*g/mm")
    def _getGrooveDensity(self, gid):
        """
        Returns the groove density of the given grating
        gid (int): index of the grating
        returns (float): groove density in lines/meter
        raise
           LookupError if the grating is not installed
           ValueError: if the groove density cannot be found out
        """
        gstring = self.axes["grating"].choices[gid]
        m = self.RE_GDENSITY.search(gstring)
        if not m:
            raise ValueError("Failed to find groove density in '%s'" % gstring)
        density = float(m.group(1)) * 1e3 # l/m
        return density

    RE_BLZ = re.compile("BLZ=\s+(?P<blz>[0-9.]+)\s*(?P<unit>[NU]M)")
    def _getBlaze(self, gid):
        """
        Returns the blaze (=optimal center wavelength) of the given grating
        gid (int): index of the grating
        returns (float): blaze (in m)
        raise
           LookupError if the grating is not installed
           ValueError: if the groove density cannot be found out
        """
        gstring = self.axes["grating"].choices[gid]
        m = self.RE_BLZ.search(gstring)
        if not m:
            raise ValueError("Failed to find blaze in '%s'" % gstring)
        blaze, unit = float(m.group("blz")), m.group("unit").upper()
        blaze *= {"UM": 1e-6, "NM": 1e-9}[unit] # m
        return blaze

    def GetGrating(self):
        """
        Retuns the current grating in use
        returns (1<=int<=9) the grating in use
        """
        # ?GRATING Returns the number of gratings presently being used numbered 1 - 9.
        # On the SP-2150i, it's only up to 6

        res = self._sendQuery("?grating")
        val = int(res)
        if not 1 <= val <= 9:
            raise SPError("Unexpected grating number '%s'" % res)
        return val

    def SetGrating(self, g):
        """
        Change the current grating (the turret turns).
        g (1<=int<=9): the grating number to change to
        The method is synchronous, it returns once the grating is selected. It
          might take up to 20 s.
        Note: the grating is dependent on turret number (and the self.max_gratting)!
        Note: after changing the grating, the wavelength, might have changed
        """
        # GRATING Places specified grating in position to the [current] wavelength
        # Note: it always reports ok, and doesn't change the grating if not
        # installed or wrong value

        assert(1 <= g <= (3 * self.max_gratings))
        # TODO check that the grating is configured

        self._sendOrder("%d grating" % g, timeout=20)

    def GetWavelength(self):
        """
        Return (0<=float): the current wavelength at the center (in m)
        """
        # ?NM Returns present wavelength in nm to 0.01nm resolution with units
        #  nm appended.
        # Note: For the SP-2150i, it seems there is no unit appended
        # ?NM 300.00 nm

        res = self._sendQuery("?nm")
        m = re.search("\s*(\d+.\d+)( nm)?", res)
        wl = float(m.group(1)) * 1e-9
        if wl > 1e-3:
            raise SPError("Unexpected wavelength of '%s'" % res)
        return wl

    def SetWavelength(self, wl):
        """
        Change the wavelength at the center
        wl (0<=float<=10e-6): wavelength in meter
        returns when the move is complete
        The method is synchronous, it returns once the grating is selected. It
          might take up to 20 s.
        """
        # GOTO: Goes to a destination wavelength at maximum motor speed. Accepts
        #  destination wavelength in nm as a floating point number with up to 3
        #  digits after the decimal point or whole number wavelength with no
        #  decimal point.
        # 345.65 GOTO
        # Note: NM goes to the wavelength slowly (in order to perform a scan).
        #  It shouldn't be needed for spectrometer
        # Out of bound values are silently ignored by going to the min or max.

        assert(0 <= wl <= 10e-6)
        # TODO: check that the value fit the grating configuration?
        self._sendOrder("%.3f goto" % (wl * 1e9), timeout=20)

    def GetModel(self):
        """
        Return (str): the model name
        """
        # MODEL Returns model number of the Acton SP series monochromator.
        # returns something like ' SP-2-150i '
        res = self._sendQuery("model")
        return res.strip()

    def GetSerialNumber(self):
        """
        Return the serial number or None if it cannot be determined
        """
        try:
            res = self._sendQuery("serial")
        except SPError:
            logging.exception("Device doesn't support serial number query")
            return None
        return res.strip()

    # TODO diverter (mirror) functions: no diverter on SP-2??0i anyway.

    def _getCalibratedWavelength(self):
        """
        Read the center wavelength, and adapt it based on the calibration (if
         it is available for the current grating)
        return (float): wavelength in m
        """
        gid = self.GetGrating()
        rawwl = self.GetWavelength()
        # Do we already now the answer?
        if (gid, rawwl) == self._wl[0:2]:
            return self._wl[2]

        ca, sa, fl, ia, da = self._calib[gid]

        # It's pretty hard to reverse the formula, so we approximate a8 using
        # rawwl (instead of wl), which usually doesn't bring error > 0.01 nm
        gl = self._getGrooveDensity(gid)
        psz = self._ccd.pixelSize.value[0] # m/px
        a8 = (rawwl * gl / 2) / math.cos(ia / 2)
        ga = math.asin(a8) # rad
        dispersion = math.cos(ia / 2 + ga) / (gl * fl) # m/m
        pixbw = psz * dispersion
        wl = (rawwl - ca * pixbw) / (sa + 1)
        wl = max(0, wl)
        return wl

    def _setCalibratedWavelength(self, wl):
        """
        wl (float): center wavelength in m
        """
        gid = self.GetGrating()
        ca, sa, fl, ia, da = self._calib[gid]

        # This is approximately what Winspec does, but it seems not exactly,
        # because the values differ ± 0.1nm
        gl = self._getGrooveDensity(gid)
        psz = self._ccd.pixelSize.value[0] # m/px
        a8 = (wl * gl / 2) / math.cos(ia / 2)
        ga = math.asin(a8) # rad
        dispersion = math.cos(ia / 2 + ga) / (gl * fl) # m/m
        pixbw = psz * dispersion
        offset = ca * pixbw + sa * wl
        if abs(offset) > 50e-9:
            # we normally don't expect offset more than 10 nm
            logging.warning("Center wavelength offset computed of %g nm", offset * 1e9)
        else:
            logging.debug("Center wavelength offset computed of %g nm", offset * 1e9)
        rawwl = max(0, wl + offset)
        self.SetWavelength(rawwl)

        # store the corresponding official wl value as it's hard to inverse the
        # conversion (for displaying in .position)
        self._wl = (gid, self.GetWavelength(), wl)

    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        Note: it should not be called while holding _ser_access
        """
        with self._ser_access:
            pos = {"wavelength": self._getCalibratedWavelength(),
                   "grating": self.GetGrating()
                  }

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        """
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        returns (Future): future that control the asynchronous move
        """
        self._checkMoveRel(shift)

        for axis in shift:
            if axis == "wavelength":
                # cannot convert it directly to an absolute move, because
                # several in a row must mean they accumulate. So we queue a
                # special task. That also means the range check is delayed until
                # the actual position is known.
                return self._executor.submit(self._doSetWavelengthRel, shift[axis])

    @isasync
    def moveAbs(self, pos):
        """
        Move the stage the defined values in m for each axis given.
        pos dict(string-> float): name of the axis and new position in m
        returns (Future): future that control the asynchronous move
        """
        self._checkMoveAbs(pos)

        # If grating needs to be changed, change it first, then the wavelength
        if "grating" in pos:
            g = pos["grating"]
            wl = pos.get("wavelength")
            return self._executor.submit(self._doSetGrating, g, wl)
        elif "wavelength" in pos:
            wl = pos["wavelength"]
            return self._executor.submit(self._doSetWavelengthAbs, wl)
        else: # nothing to do
            return model.InstantaneousFuture()

    def _doSetWavelengthRel(self, shift):
        """
        Change the wavelength by a value
        """
        with self._ser_access:
            pos = self.position.value["wavelength"] + shift
            # it's only now that we can check the absolute position is wrong
            minp, maxp = self.axes["wavelength"].range
            if not minp <= pos <= maxp:
                raise ValueError("Position %f of axis '%s' not within range %f→%f" %
                                 (pos, "wavelength", minp, maxp))
            self._setCalibratedWavelength(pos)
        self._updatePosition()

    def _doSetWavelengthAbs(self, pos):
        """
        Change the wavelength to a value
        """
        with self._ser_access:
            self._setCalibratedWavelength(pos)
        self._updatePosition()

    def _doSetGrating(self, g, wl=None):
        """
        Setter for the grating VA.
        g (1<=int<=3): the new grating
        wl (None or float): wavelength to set afterwards. If None, will put the
          same wavelength as before the change of grating.
        returns the actual new grating
        Warning: synchronous until the grating is finished (up to 20s)
        """
        try:
            with self._ser_access:
                if wl is None:
                    wl = self.position.value["wavelength"]
                self.SetGrating(g)
                self._setCalibratedWavelength(wl)
        except Exception:
            logging.exception("Failed to change grating to %d", g)
            raise

        self._updatePosition()

    def stop(self, axes=None):
        """
        stops the motion
        Warning: Only not yet-executed moves can be cancelled, this hardware
          doesn't support stopping while a move is going on.
        """
        self._executor.cancel()

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

        if self._serial:
            self._serial.close()
            self._serial = None

        super(SpectraPro, self).terminate()

    def getPixelToWavelength(self, npixels, pxs):
        """
        Return the lookup table pixel number of the CCD -> wavelength observed.
        npixels (1 <= int): number of pixels on the CCD (horizontally), after
          binning.
        pxs (0 < float): pixel size in m (after binning)
        return (list of floats): pixel number -> wavelength in m
        """
        centerpixel = (npixels - 1) / 2
        cw = self.position.value["wavelength"] # m
        gid = self.position.value["grating"]
        gl = self._getGrooveDensity(gid)
        ca, sa, fl, ia, da = self._calib[gid]

        # Formula based on the Winspec documentation:
        # "Equations used in WinSpec Wavelength Calibration", p. 257 of the manual
        # ftp://ftp.piacton.com/Public/Manuals/Princeton%20Instruments/WinSpec%202.6%20Spectroscopy%20Software%20User%20Manual.pdf
        # Converted to code by Benjamin Brenny (from AMOLF)
        G = math.asin(cw / (math.cos(ia / 2) * 2 / gl))

        wllist = []
        for i in range(npixels):
            pxd = pxs * (i - centerpixel)  # distance of pixel to sensor centre
            E = math.atan((pxd * math.cos(da)) / (fl + pxd * math.sin(da)))
            wl = (math.sin(G - ia / 2) + math.sin(G + ia / 2 + E)) / gl
            wllist.append(wl)

        return wllist

#     def getPolyToWavelength(self):
#         """
#         Compute the right polynomial to convert from a position on the sensor to the
#           wavelength detected. It depends on the current grating, center
#           wavelength (and focal length of the spectrometer).
#         Note: It will always return some not-too-stupid values, but the only way
#           to get precise values is to have provided a calibration data file.
#           Without it, it will just base the calculations on the theoretical
#           perfect spectrometer.
#         returns (list of float): polynomial coefficients to apply to get the current
#           wavelength corresponding to a given distance from the center:
#           w = p[0] + p[1] * x + p[2] * x²...
#           where w is the wavelength (in m), x is the position from the center
#           (in m, negative are to the left), and p is the polynomial (in m, m^0, m^-1...).
#         """
#         # FIXME: shall we report the error on the polynomial? At least say if it's
#         # using calibration or not.
#         # TODO: have a calibration procedure, a file format, and load it at init
#         # See fsc2, their calibration is like this for each grating:
#         # INCLUSION_ANGLE_1  =   30.3
#         # FOCAL_LENGTH_1     =   301.2 mm
#         # DETECTOR_ANGLE_1   =   0.324871
#         # TODO: use detector angle
#         fl = self._focal_length # m
#         ia = self._inclusion_angle # rad
#         cw = self.position.value["wavelength"] # m
#         if not fl:
#             # "very very bad" calibration
#             return [cw]
#
#         # When no calibration available, fallback to theoretical computation
#         # based on http://www.roperscientific.de/gratingcalcmaster.html
#         gl = self._getGrooveDensity(self.position.value["grating"]) # g/m
#         # fL = focal length (mm)
#         # wE = inclusion angle (°) = the angle between the incident and the reflected beam for the center wavelength of the grating
#         # gL = grating lines (l/mm)
#         # cW = center wavelength (nm)
#         #   Grating angle
#         # A8 = (cW/1000*gL/2000)/Math.cos(wE* Math.PI/180);
#         # E8 = Math.asin(A8)*180/Math.PI;
#         try:
#             a8 = (cw * gl/2) / math.cos(ia)
#             ga = math.asin(a8) # radians
#         except (ValueError, ZeroDivisionError):
#             logging.exception("Failed to compute polynomial for wavelength conversion")
#             return [cw]
#         # if (document.forms[0].E8.value == "NaN deg." || E8 > 40){document.forms[0].E8.value = "> 40 deg."; document.forms[0].E8.style.colour="red";
#         if 0.5 > math.degrees(ga) or math.degrees(ga) > 40:
#             logging.warning("Failed to compute polynomial for wavelength "
#                             "conversion, got grating angle = %g°", math.degrees(ga))
#             return [cw]
#
#         # dispersion: wavelength(m)/distance(m)
#         # F8a = Math.cos(Math.PI/180*(wE*1 + E8))*(1000000)/(gL*fL); // nm/mm
#         # to convert from nm/mm -> m/m : *1e-6
#         dispersion = math.cos(ia + ga) / (gl*fl) # m/m
#         if 0 > dispersion or dispersion > 0.5e-3: # < 500 nm/mm
#             logging.warning("Computed dispersion is not within expected bounds: %f nm/mm",
#                             dispersion * 1e6)
#             return [cw]
#
#         # polynomial is cw + dispersion * x
#         return [cw, dispersion]

    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        try:
            with self._ser_access:
                modl = self.GetModel()
                if not modl.startswith("SP-"):
                    # accept it anyway
                    logging.warning("Device reports unexpected model '%s'", modl)

                turret = self.GetTurret()
                if turret not in (1, 2, 3):
                    return False
                return True
        except Exception:
            logging.exception("Selftest failed")

        return False

    @staticmethod
    def scan(port=None):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        returns (list of 2-tuple): name, args (port)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        if port:
            ports = [port]
        else:
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range(8)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')

        logging.info("Serial ports scanning for Acton SpectraPro spectrograph in progress...")
        found = []  # (list of 2-tuple): name, kwargs
        for p in ports:
            try:
                logging.debug("Trying port %s", p)
                dev = SpectraPro(None, None, p, _noinit=True)
            except (serial.SerialException, HwError):
                # not possible to use this port? next one!
                continue

            # Try to connect and get back some answer.
            try:
                model = dev.GetModel()
                if model.startswith("SP-"):
                    found.append((model, {"port": p}))
                else:
                    logging.info("Device on port '%s' responded correctly, but with unexpected model name '%s'.", p, model)
            except:
                continue

        return found

    @staticmethod
    def openSerialPort(port):
        """
        Opens the given serial port the right way for the SpectraPro.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        # according to doc:
        # "port set-up is 9600 baud, 8 data bits, 1 stop bit and no parity"
        ser = serial.Serial(
            port=port,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=2 # s
        )

        return ser


# Additional classes used for testing without the actual hardware
class FakeSpectraPro(SpectraPro):
    """
    Same as SpectraPro but connects to the simulator. Only used for testing.
    """

    # FIXME: global scan ends up scanning twice, once for SpectraPro and once for FakeSpectraPro
    # maybe link it to a special "/fake/*" port in openSerialPort() and don't
    # duplicate class? Or have the class be also of a FakeComponent class?
    # Or just return the fake port only
    @staticmethod
    def scan(port=None):
        return SpectraPro.scan(port) + [("fakesp", {"port": "fake"})]

    @staticmethod
    def openSerialPort(port):
        """
        Opens the given serial port the right way for the SpectraPro.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        # according to doc:
        # "port set-up is 9600 baud, 8 data bits, 1 stop bit and no parity"
        ser = SPSimulator(
            port=port,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=2 # s
        )

        return ser

class SPSimulator(object):
    """
    Simulates a SpectraPro (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout

        # internal values to simulate the device
        self._turret = 1
        self._grating = 2
        self._wavelength = 0 # nm
        self._output_buf = b"" # what the commands sends back to the "host computer"
        self._input_buf = b"" # what we receive from the "host computer"

    def write(self, data):
        self._input_buf += data
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

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _processCommand(self, com):
        """
        process the command, and put the result in the output buffer
        com (str): command
        """
        out = None # None means error
        if com == b"?turret":
            out = b"%d" % self._turret
        elif com == b"?grating":
            out = b"%d" % self._grating
        elif com == b"?nm":
            out = b"%.2f nm" % self._wavelength
        elif com == b"model":
            out = b"SP-2-300i"
        elif com == b"serial":
            out = b"12345"
        elif com == b"no-echo":
            out = b"" # echo is always disabled anyway
        elif com == b"?gratings":
            out = (b" 1  150 g/mm BLZ=  500NM \r\n"
                   b"\x1a2  600 g/mm BLZ=  1.6UM \r\n"
                   b" 3 1200 g/mm BLZ= 700NM \r\n"
                   b" 4  Not Installed    \r\n")
        elif com.endswith(b"goto"):
            m = re.match(b"(\d+.\d+) goto", com)
            if m:
                new_wl = max(0, min(float(m.group(1)), 5000)) # clamp value silently
                move = abs(self._wavelength - new_wl)
                self._wavelength = new_wl
                out = b""
                time.sleep(move / 500) # simulate 500nm/s speed
        elif com.endswith(b"turret"):
            m = re.match(b"(\d+) turret", com)
            if m:
                self._turret = int(m.group(1))
                out = b""
        elif com.endswith(b"grating"):
            m = re.match(b"(\d+) grating", com)
            if m:
                self._grating = int(m.group(1))
                out = b""
                time.sleep(2) # simulate long move
        elif com.endswith(b"mono-eestatus"):
            out = (b"\r\nSP-2-300i \r\nserial number 12345 \r\n"
                   b"turret  1 \r\ngrating 1 \r\ng/t     3 \r\n\r\n"
                   b" 1  150 g/mm BLZ=  500NM \r\n"
                   b"\x1a2  600 g/mm BLZ=  1.6UM \r\n"
                   b" 3 1200 g/mm BLZ=  700NM \r\n"
                   b" 4  Not Installed     \r\n"
                   b"\r\n           0       1       2       3       4       5       6       7       8\r\n"
                   b"offset       27 1536018 3072000       0 1536000 3072000       0 1536000 3072000\r\nadjust   979505  979820  980000  980000  980000  980000  980000  980000  980000\r\n"
                   b"delay 0 \r\nwavelength      0.000\r\nrate          100.000\r\ndouble 0 \r\nbacklash 25600 \r\noptions 0110310 \r\n"
                   b"focal length 300 \r\nhalf angle 15.20 \r\ndetector angle 1.38 \r\n"
                   b"date code 06/03/2008 \r\nboard serial number 085138715 \r\ngear 581632 25425 \r\n90 deg 1152000 \r\nmath sine\r\ngoto at 17000 pps \r\n25600 steps/rev\r\n"
                   b"                 on #2   on #3  off #1  off #2  off #3  off #4   on #1   mono\r\nchan                 8      10       2       2       2       2      12      14\r\nstop             10485   10485   10485   10485   10485   10485    5242     100\r\naccel                8       8       8       8       8       8       8       8\r\nlraf                 8       8       8       8       8       8       8       3\r\nhraf                 8       8       8       8       8       8       8      70\r\nmper                32      32      32      32      32      32      32      32\r\n                  on #2    on #3   off #1   off #2   off #3   off #4    on #1\r\nmotor app            22        0        0        0        0        0       51\r\nmotor min pos         0        0        0        0        0        0        1\r\nmotor max pos         1        0        0        0        0        0        6\r\nmotor speed         200      200      200      200      200      200      800\r\nmotor offset          0        0        0        0        0        0        0\r\nmotor s/rev         400      400      400      400      400      400     2800\r\nmotor positions \r\n        0             0        0        0        0        0        0        0\r\n        1           -70        0        0        0        0        0      467\r\n        2             0        0        0        0        0        0      933\r\n        3             0        0        0        0        0        0     1400\r\n        4             0        0        0        0        0        0     1867\r\n        5             0        0        0        0        0        0     2333\r\n        6             0        0        0        0        0        0        0\r\n        7             0        0        0        0        0        0        0\r\n        8             0        0        0        0        0        0        0\r\n        9             0        0        0        0        0        0        0\r\n\r\n           0           1           2           3           4           5           6           7           8\r\nleft edge \r\n       1.000       1.000       1.000       1.000       1.000       1.000       1.000       1.000       1.000\r\ncenter pixel \r\n       0.000       0.000       0.000       0.000       0.000       0.000       0.000       0.000       0.000\r\nright edge \r\n       1.000       1.000       1.000       1.000       1.000       1.000       1.000       1.000       1.000\r\nomega        0       0       0       0\r\nphi          0       0       0       0\r\namp          0       0       0       0\r\n"
                   )
        else:
            logging.error("SIM: Unknown command %s", to_str_escape(com))

        # add the response end
        if out is None:
            out = b" %s? \r\n" % com
        else:
            out = b" " + out + b"  ok\r\n"
        self._output_buf += out

