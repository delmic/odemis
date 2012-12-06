# -*- coding: utf-8 -*-
'''
Created on 5 Dec 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis import model, __version__
import glob
import logging
import os
import re
import serial
import sys
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
# characteristics.
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

class SpectraPro(model.Actuator):
    def __init__(self, name, role, port, turret=None, _noinit=False, **kwargs):
        """
        port (string): name of the serial port to connect to.
        turret (None or 1<=int<=3): turret number set-up. If None, consider that
          the current turret known by the device is correct.
        _noinit (boolean): for internal use only, don't try to initialise the device 
        """
        # TODO: allow to specify the currently installed turret? And change to it at init?
        
        # start with this opening the port: if it fails, we are done
        self._serial = self.openSerialPort(port)
        self._port = port
        
        # to acquire before sending anything on the serial port
        self._ser_access = threading.Lock()
        
        self._try_recover = False
        if _noinit:
            return
        
        model.Actuator.__init__(self, name, role, **kwargs)
    
        self._initDevice()
        self._try_recover = True
        
        # according to the model determine how many gratings per turret
        model = self.GetModel()
        self.max_gratings = self.model2max_gratings.get(model, 3)
        
        if turret is not None:
            if turret < 1 or turret > self.max_gratings:
                raise ValueError("Turret number given is %s, while expected a value between 1 and %d" %
                                 (turret, self.max_gratings))
            self.SetTurret(turret)
            self._turret = turret
        else:
            self._turret = self.GetTurret()
    
        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (__version__.version, self.getSerialDriver(port))
        self._hwVersion = "%s (s/n: %s)" % model, (self.GetSerialNumber() or "Unknown")
        
        # One absolute axis: wavelength
        # One enumerated int: grating number (between 1 and 3: only the current turret)
        # if so, how to let know that the grating is done moving? Or should it be an axis with 3 positions? range is a dict instead of a 2-tuple  
        
    
    # Low-level methods: to access the hardware (should be called with the lock acquired)
    
    def _sendOrder(self, com, timeout=1):
        """
        Send a command which does not expect any report back (just OK)
        com (str): command to send (including the \r if necessary)
        raise
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
        
        res = self._sendQuery(com, timeout)
        # nothing to do with the response
        
    def _sendQuery(self, com, timeout=1):
        """
        Send a command which expects a report back (in addition to the OK)
        com (str): command to send (including the \r if necessary)
        timeout (0<=float): maximum read timeout for the response
        return (str): the response received (without the ok) 
        raises:
            SPError: if the command doesn't answer the expected OK.
            IOError: in case of timeout
        """
        self._serial.timeout = timeout
        
        assert(len(com) > 1 and len(com) <= 100) # commands cannot be long
        logging.debug("Sending: %s", com.encode('string_escape'))
        while True:
            try:
                self._serial.write(com)
                break
            except IOError:
                if self._try_recover:
                    self._tryRecover()
                else:
                    raise
        
        response = ""
        while not response.endswith("\r\n"):
            char = self._serial.read()
            if not char:
                if self._try_recover:
                    self._tryRecover()
                else:
                    raise IOError("Device timeout after receiving '%s'." % response.encode('string_escape'))
            response += char
        
        logging.debug("Received: %s", response.encode('string_escape'))
        if response.endswith(" ok\r\n"):
            return response[:-5]
        else:
            # empty the serial port
            self._serial.timeout = 1
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

    # default is 3, so no need to list models with 3 grating per turret
    model2max_gratings = {"SP-2-150i", 2}
    def _initDevice(self):
        # If no echo is desired, the command NO-ECHO will suppress the echo. The
        # command ECHO will return the SP-2150i to the default echo state.
        #
        # If is connected via the real serial port (not USB), it is in echo
        # mode, so we first need to disable it, while allowing echo of the 
        # command we've just sent.
        
        try:
            r = self._sendQuery("no-echo")
        except SPError:
            logging.info("Failed to disable echo, hopping the device has not echo anyway")
        
        # empty the serial port
        self._serial.timeout = 1
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
    
    def GetGratingChoices(self):
        """
        return (dict int -> string): grating number to description
        """
        # ?GRATINGS Returns the list of installed gratings with position groove density and blaze. The
        #  present grating is specified with an arrow.
        # Example output:
        # TODO

        # FIXME does the response include "\r\n"?
        res = self._sendQuery("?gratings")
        #TODO
        gratings = {}
        for line in res.split("\n"):
            m = re.search(".(\n) (.*)", line)
            if not m:
                logging.debug("failed to decode gratting description '%s'", line)
            num = m.group(1)
            desc = m.group(2)
            # TODO: skip gratings "Not installed"
            gratings[num] = desc
        
        return gratings
    
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
        Note: the gratting is dependant on turret number (and the self.max_gratting)!
        Note: after changing the grating, the wavelength, might have changed
        """
        #GRATING Places specified grating in position to the [current] wavelength

        assert(1 <= g and g <= (3 * self.max_gratings))
        # TODO check that it's indeed synchronous
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
        wl (0<=float<=1e-6): wavelength in meter
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

        # TODO check that it indeed returns only when the move is complete
        
        assert(0 <= wl and wl <= 1e-6)
        # TODO: check that the value fit the grating configuration?
        self._sendOrder("%.2f goto" % (wl * 1e9), timeout=20)
    
    def GetModel(self):
        """
        Return (str): the model name
        """ 
        # MODEL Returns model number of the Acton SP series monochromator.
        res = self._sendQuery("model")
        return res
    
    def GetSerialNumber(self):
        """
        Return the serial number or None if it cannot be determined
        """
        try:
            res = self._sendQuery("serial")
        except SPError:
            logging.exception("Device doesn't support serial number query")
            return None
        return res
    
    # TODO diverter (mirror) functions: no diverter on SP-2??0i anyway.
    
    
    # high-level methods (interface)
    
    
    
    def terminate(self):
        if self._serial:
            self._serial.close()
            self._serial = None
        
    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        try:
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
                    logging.info("Device on port '%s' responded correctly, but with unexpected model name '%s'.", port, model)
            except:
                continue

        return found
    
    # copy from lle.LLE
    @staticmethod
    def getSerialDriver(name):
        """
        return (string): the name of the serial driver used for the given port
        """
        # In linux, can be found as link of /sys/class/tty/tty*/device/driver
        if sys.platform.startswith('linux'):
            path = "/sys/class/tty/" + os.path.basename(name) + "/device/driver"
            try:
                return os.path.basename(os.readlink(path))
            except OSError:
                return "Unknown"
        else:
            return "Unknown"
        
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
            