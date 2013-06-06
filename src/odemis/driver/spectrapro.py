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
from __future__ import division
from Pyro4.core import isasync
from concurrent.futures.thread import ThreadPoolExecutor
from odemis import model, __version__
from odemis.util import driver
import collections
import glob
import logging
import math
import os
import re
import serial
import threading
import time

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

class SPError(IOError):
    """Error related to the hardware behaviour"""
    pass

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
                         "SP-2-150i": 24.66, 
                         "SP-2-300i": 15.15,
                         "SP-2-500i": 8.59,
                         "SP-2-750i": 6.55,
                         "SP-FAKE": 15.15,
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
    def __init__(self, name, role, port, turret=None, _noinit=False, **kwargs):
        """
        port (string): name of the serial port to connect to.
        turret (None or 1<=int<=3): turret number set-up. If None, consider that
          the current turret known by the device is correct.
        inverted (None): it is not allowed to invert the axes
        _noinit (boolean): for internal use only, don't try to initialise the device 
        """
        if kwargs.get("inverted", None):
            raise ValueError("Axis of spectrograph cannot be inverted")
        
        # start with this opening the port: if it fails, we are done
        self._serial = self.openSerialPort(port)
        self._port = port
        
        # to acquire before sending anything on the serial port
        self._ser_access = threading.Lock()
        
        self._try_recover = False
        if _noinit:
            return
        
        self._initDevice()
        self._try_recover = True
        
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
    
        # TODO: a more precise way to find the maximum wavelength (looking at the available gratings?)
        # TODO: what's the min? 200nm seems the actual min working, although wavelength is set to 0 by default !?
        # provides a ._axes and ._range
        model.Actuator.__init__(self, name, role, axes=["wavelength"], 
                                ranges={"wavelength": (0, 2400e-9)}, **kwargs)
    
        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (__version__.version, driver.getSerialDriver(port))
        self._hwVersion = "%s (s/n: %s)" % (model_name, (self.GetSerialNumber() or "Unknown"))
    
        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time
        
        # One absolute axis: wavelength
        # TODO: second dimension: enumerated int: grating/groove density (l/m or g/m) 
        # TODO: how to let know that the grating is done moving? Or should it be an axis with 3 positions? range is a dict instead of a 2-tuple  
        
        pos = {"wavelength": self.GetWavelength()}
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(pos, unit="m", readonly=True)
        
        # for now, it's fixed (and it's unlikely to be useful to allow less than the max)
        max_speed = 1000e-9/10 # about 1000 nm takes 10s => max speed in m/s
        self.speed = model.MultiSpeedVA(max_speed, range=[max_speed, max_speed], unit="m/s",
                                        readonly=True)

        grating = self.GetGrating()
        gchoices = self.GetGratingChoices()
        # remove the choices which are not valid for the current turret
        for c in gchoices:
            t = 1 + (c - 1) // self.max_gratings
            if t != self._turret:
                del gchoices[c]

        # TODO: report the grating with its wavelength range (possible to compute from groove density + blaze wl?)
        # range also depends on the max grating angle (40°, CCD pixel size, CCD horizontal size, focal length,+ efficienty curve?) 
        # cf http://www.roperscientific.de/gratingcalcmaster.html
        self.grating = model.IntEnumerated(grating, choices=gchoices, unit="", 
                                           setter=self._setGrating)
        
        # store focal length and inclusion angle for the polynomial computation
        try:
            self._focal_length = FOCAL_LENGTH_OFFICIAL[model_name]
            self._inclusion_angle = math.radians(INCLUSION_ANGLE_OFFICIAL[model_name])
        except KeyError:
            self._focal_length = None
            self._inclusion_angle = None
                
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
        #MODEL\r
        # \x00X\xf0~\x00X\xf0~MODEL ? \r\n
        #?\r
        # \r\nAddress Error \r\nA=3F4F4445 PC=81444
        
        assert(len(com) > 1 and len(com) <= 100) # commands cannot be long
        com += "\r"
        
        logging.debug("Sending: %s", com.encode('string_escape'))
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
        response = ""
        timeend = time.time() + timeout
        while ((time.time() <= timeend) and
               not (response.endswith(" ok\r\n") or response.endswith("? \r\n"))):
            self._serial.timeout = max(0.1, timeend - time.time())
            char = self._serial.read()
            if not char: # timeout
                break
            response += char
        
        logging.debug("Received: %s", response.encode('string_escape'))
        if response.endswith(" ok\r\n"):
            return response[:-5]
        else:
            # if the device hasn't answered anything, it might have been disconnected
            if len(response) == 0:
                if self._try_recover:
                    self._tryRecover()
                else:
                    raise IOError("Device timeout after receiving '%s'." % response.encode('string_escape'))
            else: # just non understood command
                # empty the serial port
                self._serial.timeout = 0.1
                garbage = self._serial.read(100)
                if len(garbage) == 100:
                    raise IOError("Device keeps sending data")
                response += garbage
                raise SPError("Sent '%s' and received error: '%s'" % 
                              (com.encode('string_escape'), response.encode('string_escape')))
    
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
            r = self._sendOrder("no-echo")
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
            raise SPError("Unexpected turret number '%s'", res)
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

        assert(1 <= t and t <= 3)
        # TODO check that there is grating configured for this turret (using GetGratingChoices)
        self._sendOrder("%d turret" % t)
    
    # regex to read the gratings
    RE_NOTINSTALLED = re.compile("\D*(\d+)\s+Not Installed")
    RE_INSTALLED = re.compile("\D*(\d+)\s+(\d+)\s*g/mm BLZ=\s*(\d+)\s*(nm|NM)")
    RE_GRATING = re.compile("\D*(\d+)\s+(.+\S)\s*\r")
    def GetGratingChoices(self):
        """
        return (dict int -> string): grating number to description
        """
        # ?GRATINGS Returns the list of installed gratings with position groove density and blaze. The
        #  present grating is specified with an arrow.
        # Example output:
        #  \r\n 1  300 g/mm BLZ=  500NM \r\n\x1a2  300 g/mm BLZ=  750NM \r\n 3  Not Installed     \r\n 4  Not Installed     \r\n 5  Not Installed     \r\n 6  Not Installed     \r\n 7  Not Installed     \r\n 8  Not Installed     \r\n ok\r\n
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
        gstring = self.grating.choices[gid]
        m = self.RE_GDENSITY.search(gstring)
        if not m:
            raise ValueError("Failed to find groove density in '%s'", gstring)
        density = float(m.group(1)) * 1e3 # l/m
        return density
    
    def GetGrating(self):
        """
        Retuns the current grating in use
        returns (1<=int<=9) the grating in use
        """
        # ?GRATING Returns the number of gratings presently being used numbered 1 - 9.
        # On the SP-2150i, it's only up to 6
        
        res = self._sendQuery("?grating")
        val = int(res)
        if val < 1 or val > 9:
            raise SPError("Unexpected grating number '%s'", res)
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
        #GRATING Places specified grating in position to the [current] wavelength
        # Note: it always reports ok, and doesn't change the grating if not
        # installed or wrong value 

        assert(1 <= g and g <= (3 * self.max_gratings))
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
            raise SPError("Unexpected wavelength of '%s'", res)
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
        
        assert(0 <= wl and wl <= 10e-6)
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
    
    
    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        Note: it should not be called while holding _ser_access
        """
        with self._ser_access:
            pos = {"wavelength": self.GetWavelength()}
        
        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)
        
    def _setGrating(self, g):
        """
        Setter for the grating VA.
        g (1<=int<=3): the new grating
        returns the actual new grating
        Warning: synchronous until the grating is finished (up to 20s)
        """
        try:
            self.stop() # stop all wavelength changes (not meaningful anymore)
            with self._ser_access:
                self.SetGrating(g)
        except:
            # let's see what is the actual grating
            g = self.GetGrating()
        
        # after changing the grating, the wavelength might be different
        self._updatePosition()
        return g
    
    
    @isasync
    def moveRel(self, shift):
        """
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        returns (Future): future that control the asynchronous move
        """
        # light check it's in the ranges (can only check it's not too huge)    
        for axis, value in shift.items():
            if not axis in self._axes:
                raise LookupError("Axis '%s' doesn't exist", axis)
                
            minp, maxp = self._ranges[axis] 
            if abs(value) > maxp:
                raise ValueError("Move by %f of axis '%s' bigger than %f",
                                 value, axis, maxp) 
        
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
        # check it's in the ranges    
        for axis, value in pos.items():
            if not axis in self._axes:
                raise LookupError("Axis '%s' doesn't exist", axis)
                
            minp, maxp = self._ranges[axis] 
            if value < minp or maxp < value:
                raise ValueError("Position %f of axis '%s' not within range %f→%f",
                                 value, axis, minp, maxp) 
    
        for axis in pos:
            if axis == "wavelength":
                return self._executor.submit(self._doSetWavelengthAbs, pos[axis])
    
    
    def _doSetWavelengthRel(self, shift):
        """
        Change the wavelength by a value
        """
        with self._ser_access:
            pos = self.GetWavelength() + shift
            # it's only now that we can check the absolute position is wrong
            minp, maxp = self._ranges["wavelength"]
            if pos < minp or maxp < pos:
                raise ValueError("Position %f of axis '%s' not within range %f→%f",
                                 pos, "wavelength", minp, maxp)
            self.SetWavelength(pos)
        self._updatePosition()
        
    def _doSetWavelengthAbs(self, pos):
        """
        Change the wavelength to a value
        """
        with self._ser_access:
            self.SetWavelength(pos)
        self._updatePosition()
        
    
    def stop(self):
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
    
    def getPolyToWavelength(self):
        """
        Compute the right polynomial to convert from a position on the sensor to the
          wavelength detected. It depends on the current grating, center 
          wavelength (and focal length of the spectrometer). 
        Note: It will always return some not-too-stupid values, but the only way
          to get precise values is to have provided a calibration data file.
          Without it, it will just base the calculations on the theoretical 
          perfect spectrometer. 
        returns (list of float): polynomial coefficients to apply to get the current
          wavelength corresponding to a given distance from the center: 
          w = p[0] + p[1] * x + p[2] * x²... 
          where w is the wavelength (in m), x is the position from the center
          (in m, negative are to the left), and p is the polynomial (in m, m^0, m^-1...).
        """
        # FIXME: shall we report the error on the polynomial? At least say if it's
        # using calibration or not.
        # TODO: have a calibration procedure, a file format, and load it at init
        # See fsc2, their calibration is like this for each grating:
        # INCLUSION_ANGLE_1  =   30.3
        # FOCAL_LENGTH_1     =   301.2 mm
        # DETECTOR_ANGLE_1   =   0.324871
        fl = self._focal_length # m
        ia = self._inclusion_angle # rad
        cw = self.position.value["wavelength"] # m
        if not fl:
            # "very very bad" calibration
            return [cw]
        
        # When no calibration available, fallback to theoretical computation
        # based on http://www.roperscientific.de/gratingcalcmaster.html
        gl = self._getGrooveDensity(self.grating.value) # g/m
        # fL = focal length (mm)
        # wE = inclusion angle (°) = the angle between the incident and the reflected beam for the center wavelength of the grating
        # gL = grating lines (l/mm)
        # cW = center wavelength (nm)
        #   Grating angle
        #A8 = (cW/1000*gL/2000)/Math.cos(wE* Math.PI/180);
        # E8 = Math.asin(A8)*180/Math.PI;
        try:
            a8 = (cw * gl/2) / math.cos(ia)
            ga = math.asin(a8) # radians
        except (ValueError, ZeroDivisionError):
            logging.exception("Failed to compute polynomial for wavelength conversion")
            return [cw]
        # if (document.forms[0].E8.value == "NaN deg." || E8 > 40){document.forms[0].E8.value = "> 40 deg."; document.forms[0].E8.style.color="red";  
        if 0.5 > math.degrees(ga) or math.degrees(ga) > 40:
            logging.warning("Failed to compute polynomial for wavelength "
                            "conversion, got grating angle = %g°", math.degrees(ga))
            return [cw]
        
        # dispersion: wavelength(m)/distance(m) 
        # F8a = Math.cos(Math.PI/180*(wE*1 + E8))*(1000000)/(gL*fL); // nm/mm
        # to convert from nm/mm -> m/m : *1e-6
        dispersion = math.cos(ia + ga) / (gl*fl) # m/m
        if 0 > dispersion or dispersion > 0.5e-3: # < 500 nm/mm
            logging.warning("Computed dispersion is not within expected bounds: %f nm/mm",
                            dispersion * 1e6)
            return [cw]
        
        # polynomial is cw + dispersion * x
        return [cw, dispersion]
        
    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        try:
            with self._ser_access:
                model = self.GetModel()
                if not model.startswith("SP-"):
                    # accept it anyway
                    logging.warning("Device reports unexpected model '%s'", model)
                    
                turret = self.GetTurret()
                if not turret in (1,2,3):
                    return False
                return True
        except:
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
                ports = ["COM" + str(n) for n in range (0,8)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')
        
        logging.info("Serial ports scanning for Acton SpectraPro spectrograph in progress...")
        found = []  # (list of 2-tuple): name, args (port, axes(channel -> CL?)
        for p in ports:
            try:
                logging.debug("Trying port %s", p)
                dev = SpectraPro(None, None, p, _noinit=True)
            except serial.SerialException:
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
            port = port,
            baudrate = 9600,
            bytesize = serial.EIGHTBITS,
            parity = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout = 2 #s
        )
        
        return ser 


class CancellableThreadPoolExecutor(ThreadPoolExecutor):
    """
    An extended ThreadPoolExecutor that can cancel all the jobs not yet started.
    """
    def __init__(self, *args, **kwargs):
        ThreadPoolExecutor.__init__(self, *args, **kwargs)
        self._queue = collections.deque() # thread-safe queue of futures
    
    def submit(self, fn, *args, **kwargs):
        logging.debug("queuing action %s with arguments %s", fn, args)
        f = ThreadPoolExecutor.submit(self, fn, *args, **kwargs)
        # add to the queue and track the task
        self._queue.append(f)
        f.add_done_callback(self._on_done)
        return f
        
    def _on_done(self, future):
        # task is over
        try:
            self._queue.remove(future)
        except ValueError:
            # can happen if it was cancelled
            pass
     
    def cancel(self):
        """
        Cancels all the tasks still in the work queue, if they can be cancelled
        Returns when all the tasks have been cancelled or are done.
        """
        uncancellables = []
        # cancel one task at a time until there is nothing in the queue
        while True:
            try:
                # Start with the last one added as it's the most likely to be cancellable
                f = self._queue.pop()
            except IndexError:
                break
            if not f.cancel():
                uncancellables.append(f)
        
        # wait for the non cancellable tasks to finish
        for f in uncancellables:
            try:
                f.result()
            except:
                # the task raised an exception => we don't care
                pass

# Additional classes used for testing without the actual hardware
class FakeSpectraPro(SpectraPro):
    """
    Same as SpectraPro but connects to the simulator. Only used for testing.
    """
    
    @staticmethod
    def scan(port=None):
        return SpectraPro.scan(port) + [("fakesp", {"port":"fake"})]
    
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
            port = port,
            baudrate = 9600,
            bytesize = serial.EIGHTBITS,
            parity = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout = 2 #s
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
        self._output_buf = "" # what the commands sends back to the "host computer"
        self._input_buf = "" # what we receive from the "host computer"
        
    def write(self, data):
        self._input_buf += data
        # process each commands separated by "\r"
        commands = self._input_buf.split("\r")
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
        if com == "?turret":
            out = "%d" % self._turret
        elif com == "?grating":
            out = "%d" % self._grating
        elif com == "?nm":
            out = "%.2f nm" % self._wavelength
        elif com == "model":
            out = "SP-FAKE"
        elif com == "serial":
            out = "12345"
        elif com == "no-echo":
            out = "" # echo is always disabled anyway
        elif com == "?gratings":
            out = (" 1 300 g/mm BLZ=  345NM \r\n" +
                   ">2 600 g/mm BLZ=   89NM \r\n" +
                   " 3 1200 g/mm BLZ= 700NM \r\n" +
                   " 4 Not Installed    \r\n")
        elif com.endswith("goto"):
            m = re.match("(\d+.\d+) goto", com)
            if m:
                new_wl = max(0, min(float(m.group(1)), 5000)) # clamp value silently
                move = abs(self._wavelength - new_wl)
                self._wavelength = new_wl
                out = ""
                time.sleep(move / 500) # simulate 500nm/s speed
        elif com.endswith("turret"):
            m = re.match("(\d+) turret", com)
            if m:
                self._turret = int(m.group(1))
                out = ""
        elif com.endswith("grating"):
            m = re.match("(\d+) grating", com)
            if m:
                self._grating = int(m.group(1))
                out = ""
                time.sleep(2) # simulate long move
                
        # add the response end
        if out is None:
            out = " %s? \r\n" % com
        else:
            out = " " + out + "  ok\r\n"
        self._output_buf += out
        