# -*- coding: utf-8 -*-
'''
Created on 17 Feb 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

from Pyro4.core import isasync
from ctypes import *
import logging
from odemis import model
import odemis
from odemis.model._futures import CancellableThreadPoolExecutor
import os


class ShamrockError(Exception):
    def __init__(self, errno, strerror):
        self.args = (errno, strerror)

    def __str__(self):
        return self.args[1]

class ShamrockDLL(CDLL):
    """
    Subclass of CDLL specific to Andor Shamrock library, which handles error 
    codes for all the functions automatically.
    It works by setting a default _FuncPtr.errcheck.
    """

    def __init__(self):
        if os.name == "nt":
            #FIXME: might not fly if parent is not a WinDLL => use __new__()
            WinDLL.__init__(self, "libshamrockcif.dll") # TODO check it works
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libshamrockcif.so.2", RTLD_GLOBAL)

    @staticmethod
    def at_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of 
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns DRV_SUCCESS on correct usage, _except_ GetTemperature()
        if not result in ShamrockDLL.ok_code:
            if result in ShamrockDLL.err_code:
                raise ShamrockError(result, "Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, ShamrockDLL.err_code[result]))
                # TODO: Use ShamrockGetFunctionReturnDescription()
            else:
                raise ShamrockError(result, "Call to %s failed with unknown error code %d" %
                               (str(func.__name__), result))
        return result

    def __getitem__(self, name):
        try:
            func = super(ShamrockDLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s", name)
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

    ok_code = {
20202: "SHAMROCK_SUCCESS",
}
    # Not all of them are actual error code, but having them is not a problem
    err_code = {
20201: "SHAMROCK_COMMUNICATION_ERROR",
20266: "SHAMROCK_P1INVALID",
20267: "SHAMROCK_P2INVALID",
20268: "SHAMROCK_P3INVALID",
20269: "SHAMROCK_P4INVALID",
20275: "SHAMROCK_NOT_INITIALIZED",
20292: "SHAMROCK_NOT_AVAILABLE",
}

# Other constants
# SHAMROCK_ACCESSORYMIN 1
# SHAMROCK_ACCESSORYMAX 2
# SHAMROCK_FILTERMIN 1
# SHAMROCK_FILTERMAX 6
# SHAMROCK_TURRETMIN 1
# SHAMROCK_TURRETMAX 3
# SHAMROCK_GRATINGMIN 1
# SHAMROCK_SLITWIDTHMIN 10
# SHAMROCK_SLITWIDTHMAX 2500
# SHAMROCK_I24SLITWIDTHMAX 24000
# SHAMROCK_SHUTTERMODEMIN 0
# SHAMROCK_SHUTTERMODEMAX 1
# SHAMROCK_DET_OFFSET_MIN -240000
# SHAMROCK_DET_OFFSET_MAX 240000
# SHAMROCK_GRAT_OFFSET_MIN -20000
# SHAMROCK_GRAT_OFFSET_MAX 20000

# SHAMROCK_SLIT_INDEX_MIN    1
# SHAMROCK_SLIT_INDEX_MAX    4

# SHAMROCK_INPUT_SLIT_SIDE   1
# SHAMROCK_INPUT_SLIT_DIRECT  2
# SHAMROCK_OUTPUT_SLIT_SIDE  3
# SHAMROCK_OUTPUT_SLIT_DIRECT 4

# SHAMROCK_FLIPPER_INDEX_MIN    1
# SHAMROCK_FLIPPER_INDEX_MAX    2
# SHAMROCK_PORTMIN 0
# SHAMROCK_PORTMAX 1

# SHAMROCK_INPUT_FLIPPER   1
# SHAMROCK_OUTPUT_FLIPPER  2
# SHAMROCK_DIRECT_PORT  0
# SHAMROCK_SIDE_PORT    1

# SHAMROCK_ERRORLENGTH 64


class Shamrock(model.Actuator):
    """
    Component representing the spectrograph part of the Andor Shamrock
    spectrometers.
    On Linux, only the SR303i is supported, via the I²C cable connected to the
    iDus. Support only works since SDK 2.97.
    Note: we don't handle changing turret.
    """
    def __init__(self, name, role, device, path=None, parent=None, **kwargs):
        """
        device (0<=int): device number
        path (None or string): initialisation path of the Andorcam2 SDK or None
          if independent of a camera. If the path is set, a parent should also
          be passed, which is a DigitalCamera component.
        inverted (None): it is not allowed to invert the axes
        """
        # From the documentation:
        # If controlling the shamrock through i2c it is important that both the
        # camera and spectrograph are being controlled through the same calling
        # program and that the DLLs used are contained in the same working
        # folder. The camera MUST be initialized before attempting to
        # communicate with the Shamrock.
        if kwargs.get("inverted", None):
            raise ValueError("Axis of spectrograph cannot be inverted")

        self._dll = ShamrockDLL()
        self._path = path or ""
        self._device = device

        try:
            self.Initialize()
        except ShamrockDLL, err:
            raise IOError("Failed to find Andor Shamrock (%s) as device %d" %
                          (name, device))
        try:
            nd = self.GetNumberDevices()
            if device >= nd:
                raise IOError("Failed to find Andor Shamrock (%s) as device %d" %
                              (name, device))

            if path is None or parent is None:
                raise NotImplementedError("Shamrock without parent a camera is not implemented")

            # for now, it's fixed (and it's unlikely to be useful to allow less than the max)
            max_speed = 1000e-9 / 5 # about 1000 nm takes 5s => max speed in m/s
            self.speed = model.MultiSpeedVA(max_speed, range=[max_speed, max_speed], unit="m/s",
                                            readonly=True)

            gchoices = self._getGratingChoices()

            # Find lowest and largest wavelength reachable
            wl_range = (float("inf"), float("-inf"))
            for g in gchoices:
                wmin, wmax = self.GetWavelengthLimits(g)
                wl_range = min(wl_range[0], wmin), max(wl_range[1], wmax)

            axes = {"wavelength": model.Axis(unit="m", range=wl_range,
                                             speed=(max_speed, max_speed)),
                    "grating": model.Axis(choices=gchoices)
                    }
            # provides a ._axes
            model.Actuator.__init__(self, name, role, axes=axes, parent=parent, **kwargs)

            # set HW and SW version
            self._swVersion = "%s" % (odemis.__version__)
            # TODO: EEPROM contains name of the device, but there doesn't seem to be any function for getting it?!
            self._hwVersion = "%s (s/n: %s)" % ("Andor Shamrock", self.GetSerialNumber())

            # will take care of executing axis move asynchronously
            self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

            pos = {"wavelength": self.GetWavelength(),
                   "grating": self.GetGrating()}
            # RO, as to modify it the client must use .moveRel() or .moveAbs()
            self.position = model.VigilantAttribute(pos, unit="m", readonly=True)

        except Exception:
            self.Close()
            raise

    def Initialize(self):
        """
        Initialise the currently selected device
        """
        # Can take quite a lot of time due to the homing
        logging.debug("Initialising Andor Shamrock...") # ~20s
        self._dll.ShamrockInitialize(self._path)
    
    def Close(self):
        self._dll.ShamrockClose()
    
    def GetNumberDevices(self):
        """
        Returns (0<=int) the number of available Shamrocks
        """
        nodevices = c_int()
        self._dll.ShamrockGetNumberDevices(byref(nodevices))
        return nodevices.value
        
    def GetSerialNumber(self):
        """
        Returns the device serial number
        """
        serial = create_string_buffer(64) # hopefully always fit! (normally 6 bytes)
        self._dll.ShamrockGetSerialNumber(self._device, serial)
        return serial.value

    # Probably not needed, as ShamrockGetCalibration returns everything already 
    # computed  
    def EepromGetOpticalParams(self):
        """
        Returns (tuple of 3 floats): Focal Length (m), Angular Deviation (degree) and 
           Focal Tilt (degree) from the Shamrock device.
        """
        FocalLength = c_float()
        AngularDeviation = c_float()
        FocalTilt = c_float()
        self._dll.ShamrockEepromGetOpticalParams(self._device, 
                 byref(FocalLength), byref(AngularDeviation), byref(FocalTilt))
        
        return FocalLength.value, AngularDeviation.value, FocalTilt.value
    
    def SetGrating(self, grating):
        """
        grating (0<int<=3)
        """
        assert 1 <= grating <= 3
        self._dll.ShamrockSetGrating(self._device, grating)
    
    def GetGrating(self):
        """
        return (0<int<=3): current grating
        """
        grating = c_int()
        self._dll.ShamrockGetGrating(self._device, byref(grating))
        return grating.value
        
    def GetNumberGratings(self):
        """
        return (0<int<=3): number of gratings present
        """
        noGratings = c_int()
        self._dll.ShamrockGetNumberGratings(self._device, byref(noGratings))
        return noGratings.value
    
    def WavelengthReset(self):
        """
        Resets the wavelength to 0 nm.
        """
        # Same as ShamrockGotoZeroOrder()
        self._dll.ShamrockWavelengthReset(self._device)
        
    #ShamrockAtZeroOrder(self._device, int *atZeroOrder);

    def GetGratingInfo(self, grating):
        """
        grating (0<int<=3)
        return:
              lines (float): number of lines / m
              blaze (float): wavelength in m
              home (int): beginning of the grating in steps
              offset (int): offset to the grating in steps
        """
        assert 1 <= grating <= 3
        Lines = c_float() # in l/mm
        Blaze = create_string_buffer(64) # decimal of wavelength in nm
        Home = c_int()
        Offset = c_int()
        self._dll.ShamrockGetGratingInfo(self._device, grating, 
                         byref(Lines), byref(Blaze), byref(Home), byref(Offset))
        return Lines.value * 1e3, float(Blaze.value) * 1e-9, Home.value, Offset.value


    def SetWavelength(self, wavelength):
        """
        Sets the required wavelength.
        wavelength (0<=float): wavelength in m
        """
        assert 0 <= wavelength <= 50e-6
        # set in nm
        self._dll.ShamrockSetWavelength(self._device, c_float(wavelength * 1e9))
        
    def GetWavelength(self):
        """
        Gets the current wavelength.
        return (0<=float): wavelength in m
        """
        wavelength = c_float() # in nm
        self._dll.ShamrockGetWavelength(self._device, byref(wavelength))
        return wavelength.value * 1e-9
        
    def GetWavelengthLimits(self, grating):
        """
        grating (0<int<=3)
        return (0<=float< float): min, max wavelength in m
        """
        assert 1 <= grating <= 3
        Min, Max = c_float(), c_float() # in nm
        self._dll.ShamrockGetWavelengthLimits(self._device, grating, 
                                              byref(Min), byref(Max))
        return Min.value * 1e-9, Max.value * 1e-9
        
    def WavelengthIsPresent(self):
        """
        return (boolean): True if it's possible to change the wavelength
        """
        present = c_int()
        self._dll.ShamrockWavelengthIsPresent(self._device, byref(present))
        return (present != 0)
        
    def GetCalibration(self, npixels):
        """
        npixels (0<int): number of pixels on the sensor. It's actually the 
        length of the list that is being returned.
        return (list of floats of length npixels)
        """
        assert 0< npixels
        # TODO: this is pretty slow, and could be optimised either by using a 
        # numpy array or returning directly the C array. We could also just
        # allocate one array at the init, and reuse it.
        CalibrationValues = (c_float * npixels)()
        self._dll.ShamrockGetCalibration(self._device, CalibrationValues, npixels)
        return [v for v in CalibrationValues]

    def SetPixelWidth(self, width):
        """
        Defines the size of each pixel (horizontally).
        Needed to get correct information from GetCalibration()
        width (float): size of a pixel in m
        """
        # set in µm
        self._dll.ShamrockSetPixelWidth(self._device, c_float(width * 1e6))

    def SetNumberPixels(self, npixels):
        """
        Defines how many pixels (around the center) are used.
        Needed to get correct information from GetCalibration()
        npixels (int): number of pixels on the attached sensor
        """
        self._dll.ShamrockSetNumberPixels(self._device, npixels)


#self._dll.ShamrockGetPixelWidth(self._device, float* Width)
#self._dll.ShamrockGetNumberPixels(self._device, int* NumberPixels)

    # Helper functions
    def _getGratingChoices(self):
        """
        return (dict int -> string): grating number to description
        """
        ngratings = self.GetNumberGratings()
        gchoices = {}
        for g in range(1, ngratings + 1):
            lines, blaze, home, offset = self.GetGratingInfo(g)
            gchoices[g] = "%g l/mm (%g nm)" % (lines * 1e-3, blaze * 1e9)

        return gchoices


    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        Note: it should not be called while holding _ser_access
        """
        pos = {"wavelength": self.GetWavelength(),
               "grating": self.GetGrating()
              }

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    def getPixelToWavelength(self):
        """
        return (list of floats): pixel number -> wavelength in m
        """
        ccd = self.parent
        # TODO: allow to override these values by ones passes as arguments?
        npixels = ccd.shape[0]
        self.SetNumberPixels(npixels)
        self.SetPixelWidth(ccd.pixelSize.value[0] * ccd.binning.value[0])
        return self.GetCalibration(npixels)
        
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
                raise LookupError("Axis '%s' doesn't exist" % axis)

            try:
                maxp = self.axes[axis].range[1]
            except AttributeError:
                raise ValueError("Axis %s cannot be moved relative" % axis)

            if abs(value) > maxp:
                raise ValueError("Move by %f of axis '%s' bigger than %f" %
                                 (value, axis, maxp))

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
                raise LookupError("Axis '%s' doesn't exist" % axis)

            axis_def = self.axes[axis]
            if hasattr(axis_def, "range"):
                minp, maxp = axis_def.range
                if not minp <= value <= maxp:
                    raise ValueError("Position %f of axis '%s' not within range %f→%f" %
                                     (value, axis, minp, maxp))
            else:
                if not value in axis_def.choices:
                    raise ValueError("Position %f of axis '%s' not within choices %s" %
                                     (value, axis, axis_def.choices))

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
        pos = self.GetWavelength() + shift
        # it's only now that we can check the absolute position is wrong
        minp, maxp = self.axes["wavelength"].range
        if not minp <= pos <= maxp:
            raise ValueError("Position %f of axis '%s' not within range %f→%f" %
                             (pos, "wavelength", minp, maxp))
        self.SetWavelength(pos)
        self._updatePosition()

    def _doSetWavelengthAbs(self, pos):
        """
        Change the wavelength to a value
        """
        self.SetWavelength(pos)
        self._updatePosition()

    def _doSetGrating(self, g, wl=None):
        """
        Setter for the grating VA.
        g (1<=int<=3): the new grating
        wl (None or float): wavelength to set afterwards. If None, will try to 
          put the same wavelength as before the change of grating.
        returns the actual new grating
        Warning: synchronous until the grating is finished (up to 20s)
        """
        try:
            self.SetGrating(g)
            # By default the Shamrock library keeps the same wavelength
            if wl is not None:
                self.SetWavelength(wl)
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

    # TODO: method that returns the current MD_WL_LIST

    def terminate(self):
        if self._device is not None:
            logging.debug("Shutting down the spectrograph")
            self.Close()
            self._device = None


    def __del__(self):
        self.terminate()

    def selfTest(self):
        """
        Check whether the connection to the spectrograph works.
        return (boolean): False if it detects any problem
        """
        try:
            if 0 <= self.GetWavelength() <= 10e-6:
                return True
        except Exception:
            logging.exception("Self test failed")

        return False


