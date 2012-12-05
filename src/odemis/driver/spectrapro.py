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
import serial
import sys
import threading

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
# The documentation says changing the turret can take up to 20 s (i.e., far from
# instantaneous).
#

class SpectraPro(model.Actuator):
    def __init__(self, name, role, port, _noinit=False, **kwargs):
        """
        port (string): name of the serial port to connect to.
        _noinit (boolean): for internal use only, don't try to initialise the device 
        """
        # TODO: allow to specify the currently installed turret? And change to it at init?
        
        # start with this opening the port: if it fails, we are done
        self._serial = self.openSerialPort(port)
        self._port = port
        
        # to acquire before sending anything on the serial port
        self._ser_access = threading.Lock()
        
        # Init the L
        self._initDevice()

        self._try_recover = False
        if _noinit:
            return
        
        model.Actuator.__init__(self, name, role, **kwargs)
    
        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (__version__.version, self.getSerialDriver(port))
        self._hwVersion = "%s (s/n: %s)" % self.GetModel(), self.GetSerialNumber()
    
    def GetTurret(self):
        """
        returns (1 <= int <= 3): the current turret number
        """
        
        pass
    
    def SetTurret(self, t):
        """
        t (1 <= int <= 3): the turret number
        """
        # Specifies the presently installed turret or the turret to be installed.
        # Doesn't change the hardware, just which gratings are available

        # TODO check that there is grating configured for this turret (using GetGratingChoices)
        pass
    
    def GetGratingChoices(self):
        """
        return (dict int -> string): grating number to description
        """
        # ?GRATINGS Returns the list of installed gratings with position groove density and blaze. The
        #  present grating is specified with an arrow.
        # Example output:
        # TODO

        pass
    
    def GetGrating(self):
        pass
    
    def SetGrating(self):
        # gratting is dependant on turret number! 
        pass
    
    def GetWavelength(self):
        pass
    
    def SetWavelength(self, wl):
        # GOTO: Goes to a destination wavelength at maximum motor speed. Accepts
        #  destination wavelength in nm as a floating point number with up to 3
        #  digits after the decimal point or whole number wavelength with no
        #  decimal point.
        # 345.65 GOTO

        pass
    
    def GetModel(self):
        pass
    
    def GetSerialNumber(self):
        pass
    
    
    
    
    
    
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
            