# -*- coding: utf-8 -*-
'''
Created on 17 Feb 2014

@author: Éric Piel

Copyright © 2014-2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

from ctypes import *
import ctypes
import logging
import math
from odemis import model
import odemis
from odemis.driver import andorcam2
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
import os
import time


class ShamrockError(Exception):
    def __init__(self, errno, strerror):
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

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
            # FIXME: might not fly if parent is not a WinDLL => use __new__()
            WinDLL.__init__(self, "libshamrockcif.dll") # TODO check it works
        else:
            # libandor.so must be loaded first. If there is a camera, that has
            # already been done, but if not, we need to do it here. It's not a
            # problem to do it multiple times.
            self._dllandor = CDLL("libandor.so.2", RTLD_GLOBAL)
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
        if result not in ShamrockDLL.ok_code:
            if result in ShamrockDLL.err_code:
                raise ShamrockError(result, "Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, ShamrockDLL.err_code[result]))
                # TODO: Use ShamrockGetFunctionReturnDescription(result, )
            else:
                raise ShamrockError(result, "Call to %s failed with unknown error code %d" %
                               (str(func.__name__), result))
        return result

    def __getitem__(self, name):
        try:
            func = super(ShamrockDLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

    ok_code = {
20202: "SHAMROCK_SUCCESS",
}
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
SLITWIDTHMIN = 10
SLITWIDTHMAX = 2500
# SHAMROCK_I24SLITWIDTHMAX 24000
# SHAMROCK_SHUTTERMODEMIN 0
# SHAMROCK_SHUTTERMODEMAX 1
# SHAMROCK_DET_OFFSET_MIN -240000
# SHAMROCK_DET_OFFSET_MAX 240000
# SHAMROCK_GRAT_OFFSET_MIN -20000
# SHAMROCK_GRAT_OFFSET_MAX 20000

SLIT_INDEX_MIN = 1
SLIT_INDEX_MAX = 4

INPUT_SLIT_SIDE = 1
INPUT_SLIT_DIRECT = 2
OUTPUT_SLIT_SIDE = 3
OUTPUT_SLIT_DIRECT = 4

FLIPPER_INDEX_MIN = 1
FLIPPER_INDEX_MAX = 2
PORTMIN = 0
PORTMAX = 1

INPUT_FLIPPER = 1
OUTPUT_FLIPPER = 2
DIRECT_PORT = 0
SIDE_PORT = 1

# SHAMROCK_ERRORLENGTH 64

class HwAccessMgr(object):
    def __init__(self, ccd):
        """
        ccd (AndorCam2 or None)
        """
        self._ccd = ccd

    def __enter__(self):
        if self._ccd is None:
            return
        self._ccd.request_hw.append(None) # let the acquisition thread know it should release the lock
        logging.debug("requesting access to hw")
        self._ccd.hw_lock.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        """
        returns True if the exception is to be suppressed (never)
        """
        if self._ccd is None:
            return
        self._ccd.request_hw.pop() # hw no more needed
        self._ccd.hw_lock.release()

# The two values exported by the Odemis API for the flipper positions
FLIPPER_OFF = 0
FLIPPER_ON = math.radians(90)

FLIPPER_TO_PORT = {FLIPPER_OFF: DIRECT_PORT,
                   FLIPPER_ON: SIDE_PORT}

class Shamrock(model.Actuator):
    """
    Component representing the spectrograph part of the Andor Shamrock
    spectrometers.
    On Linux, the SR303i is supported since SDK 2.97, and the other ones,
    including the SR193i since SDK 2.99.
    The SR303i must be connected via the I²C cable on the iDus. With SDK 2.100+,
    it should also work via the direct USB connection.
    Note: we don't handle changing turret.
    """
    def __init__(self, name, role, device, camera=None, children=None, **kwargs):
        """
        device (0<=int or "fake"): device number
        camera (None or AndorCam2): Needed if the connection is done via the
          I²C connector of the camera. In such case, no children should be
          provided.
        children (dict str -> Components): "ccd" should be the CCD used to acquire
          the spectrum, if the connection is directly via USB.
        inverted (None): it is not allowed to invert the axes
        """
        # TODO: allow to set the TTL high, when a led (might be) is on, which
        # happens when the slits move. cf ShamrockSetAccessory


        # From the documentation:
        # If controlling the shamrock through i2c it is important that both the
        # camera and spectrograph are being controlled through the same calling
        # program and that the DLLs used are contained in the same working
        # folder. The camera MUST be initialized before attempting to
        # communicate with the Shamrock.
        if kwargs.get("inverted", None):
            raise ValueError("Axis of spectrograph cannot be inverted")

        if device == "fake":
            self._dll = FakeShamrockDLL(camera)
            device = 0
        else:
            self._dll = ShamrockDLL()
        self._device = device

        try:
            self._camera = children["ccd"]
        except (TypeError, KeyError):  # no "ccd" child => camera?
            if camera is None:
                raise ValueError("Spectrograph needs a child 'ccd' or a camera")
            self._camera = camera
            self._is_via_camera = True
            self._hw_access = HwAccessMgr(camera)
        else:
            self._is_via_camera = False
            self._hw_access = HwAccessMgr(None)

        try:
            self.Initialize()
        except ShamrockError:
            raise HwError("Failed to find Andor Shamrock (%s) as device %d" %
                          (name, device))
        try:
            nd = self.GetNumberDevices()
            if device >= nd:
                raise HwError("Failed to find Andor Shamrock (%s) as device %d" %
                              (name, device))

            # for now, it's fixed (and it's unlikely to be useful to allow less than the max)
            max_speed = 1000e-9 / 5 # about 1000 nm takes 5s => max speed in m/s
            self.speed = model.MultiSpeedVA({"wavelength": max_speed},
                                            range=[max_speed, max_speed],
                                            unit="m/s",
                                            readonly=True)

            # FIXME: for now the SDK 2.99 with SR193, commands will fail if not
            # separated by some delay (eg, 1s)
            gchoices = self._getGratingChoices()

            # The actual limits are per grating. We cannot provide this much
            # info via the .axes attribute, so just lowest and largest
            # wavelength reachable
            wl_range = (float("inf"), float("-inf"))
            for g in gchoices:
                try:
                    wmin, wmax = self.GetWavelengthLimits(1)
                except ShamrockError:
                    logging.exception("Failed to find wavelength limit for grating %d", g)
                    continue
                wl_range = min(wl_range[0], wmin), max(wl_range[1], wmax)

            # Slit (we only actually care about the input side slit for now)
            slits = {"input side": INPUT_SLIT_SIDE,
                     "input direct": INPUT_SLIT_DIRECT,
                     "output side": OUTPUT_SLIT_SIDE,
                     "output direct": OUTPUT_SLIT_DIRECT,
                     }
            for slitn, i in slits.items():
                logging.info("Slit %s is %spresent", slitn,
                             "" if self.AutoSlitIsPresent(i) else "not ")

            axes = {"wavelength": model.Axis(unit="m", range=wl_range,
                                             speed=(max_speed, max_speed)),
                    "grating": model.Axis(choices=gchoices)
                    }

            # add slit input direct if available
            # Note: the documentation mentions the width is in mm,
            # but it's probably actually µm (10 is the minimum).
            if self.AutoSlitIsPresent(INPUT_SLIT_SIDE):
                self._slit = INPUT_SLIT_SIDE
                axes["slit"] = model.Axis(unit="m",
                                          range=[SLITWIDTHMIN * 1e-6,
                                                 SLITWIDTHMAX * 1e-6]
                                          )
            else:
                self._slit = None

            # TODO: allow to define the name of the axis? or anyway, we can use
            # MultiplexActuator to rename the axis?
            if self.FlipperMirrorIsPresent(OUTPUT_FLIPPER):
                # The position values are arbitrary, but these are the one we
                # typically use in Odemis for switchin between two positions
                axes["flip-out"] = model.Axis(unit="rad",
                                              choices={FLIPPER_OFF, FLIPPER_ON}
                                              )
            else:
                logging.info("Out mirror flipper is not present")

            # provides a ._axes
            model.Actuator.__init__(self, name, role, axes=axes, **kwargs)

            # set HW and SW version
            self._swVersion = "%s" % (odemis.__version__)
            # TODO: EEPROM contains name of the device, but there doesn't seem to be any function for getting it?!
            self._hwVersion = "%s (s/n: %s)" % ("Andor Shamrock", self.GetSerialNumber())

            # will take care of executing axis move asynchronously
            self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

            # RO, as to modify it the client must use .moveRel() or .moveAbs()
            self.position = model.VigilantAttribute({}, unit="m", readonly=True)
            self._updatePosition()

        except Exception:
            self.Close()
            raise

    def Initialize(self):
        """
        Initialise the currently selected device
        """
        # Can take quite a lot of time due to the homing
        logging.debug("Initialising Andor Shamrock...") # ~20s
        if self._is_via_camera:
            path = self._camera._initpath
        else:
            path = ""
        self._dll.ShamrockInitialize(path)

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

        # Seems currently the SDK sometimes fail with SHAMROCK_COMMUNICATION_ERROR
        # as in SetWavelength()
        with self._hw_access:
            retry = 0
            while True:
                try:
                    self._dll.ShamrockSetGrating(self._device, grating)
                except ShamrockError as (errno, strerr):
                    if errno != 20201 or retry >= 5: # SHAMROCK_COMMUNICATION_ERROR
                        raise
                    # just try again
                    retry += 1
                    logging.info("Failed to set wavelength, will try again")
                    time.sleep(0.1 * retry)
                else:
                    break

    def GetGrating(self):
        """
        return (0<int<=3): current grating
        """
        with self._hw_access:
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
        with self._hw_access:
            self._dll.ShamrockWavelengthReset(self._device)

    #ShamrockAtZeroOrder(self._device, int *atZeroOrder);

    def GetGratingInfo(self, grating):
        """
        grating (0<int<=3)
        return:
              lines (float): number of lines / m
              blaze (None or float): wavelength in m or None if a mirro
              home (int): beginning of the grating in steps
              offset (int): offset to the grating in steps
        """
        assert 1 <= grating <= 3
        Lines = c_float() # in l/mm
        Blaze = create_string_buffer(64) # decimal of wavelength in nm
        Home = c_int()
        Offset = c_int()
        self._dll.ShamrockGetGratingInfo(self._device, grating,
                         byref(Lines), Blaze, byref(Home), byref(Offset))
        logging.debug("Grating is %f, %s, %d, %d", Lines.value, Blaze.value, Home.value, Offset.value)

        if Blaze.value: # empty string if no blaze (= mirror)
            blaze = float(Blaze.value) * 1e-9
        else:
            blaze = None
        return Lines.value * 1e3, blaze, Home.value, Offset.value

    def SetWavelength(self, wavelength):
        """
        Sets the required wavelength.
        wavelength (0<=float): wavelength in m
        """
        assert 0 <= wavelength <= 50e-6

        # Note: When connected via the I²C bus of the camera, it is not
        # possible to change the wavelength (or the grating) while the CCD
        # is acquiring. So this will fail with an exception, and that's
        # probably the best we can do (unless we want to synchronize with the
        # CCD and ask to temporarily stop the acquisition).

        # Currently the SDK sometimes fail with 20201: SHAMROCK_COMMUNICATION_ERROR
        # when changing wavelength by a few additional nm. It _seems_ that it
        # works anyway (but not sure).
        # It seems that retrying a couple of times just works
        with self._hw_access:
            retry = 0
            while True:
                try:
                    # set in nm
                    self._dll.ShamrockSetWavelength(self._device, c_float(wavelength * 1e9))
                except ShamrockError as (errno, strerr):
                    if errno != 20201 or retry >= 5: # SHAMROCK_COMMUNICATION_ERROR
                        raise
                    # just try again
                    retry += 1
                    logging.info("Failed to set wavelength, will try again")
                    time.sleep(0.1)
                else:
                    break

    def GetWavelength(self):
        """
        Gets the current wavelength.
        return (0<=float): wavelength in m
        """
        with self._hw_access:
            wavelength = c_float() # in nm
            self._dll.ShamrockGetWavelength(self._device, byref(wavelength))
        return wavelength.value * 1e-9

    def GetWavelengthLimits(self, grating):
        """
        grating (0<int<=3)
        return (0<=float< float): min, max wavelength in m
        """
        logging.debug("grating = %d", grating)
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
        return (present.value != 0)

# TODO: for SR193i
# ShamrockSetFocusMirror(int device, int focus)
# ShamrockGetFocusMirror(int device, int *focus)
# ShamrockGetFocusMirrorMaxSteps(int device, int *steps)
# ShamrockFocusMirrorReset(int device)
# ShamrockFocusMirrorIsPresent(int device, int *present)


    def GetCalibration(self, npixels):
        """
        npixels (0<int): number of pixels on the sensor. It's actually the
           length of the list that is being returned.
        return (list of floats of length npixels): wavelength in m
        """
        assert(0 < npixels)
        # TODO: this is pretty slow, and could be optimised either by using a
        # numpy array or returning directly the C array. We could also just
        # allocate one array at the init, and reuse it.
        CalibrationValues = (c_float * npixels)()
        self._dll.ShamrockGetCalibration(self._device, CalibrationValues, npixels)
        return [v * 1e-9 for v in CalibrationValues]

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

    def SetAutoSlitWidth(self, index, width):
        """
        index (1<=int<=4): Slit number
        width (0<float): slit opening width in m
        """
        assert(1 <= index <= 4)
        width_um = c_float(width * 1e6)

        with self._hw_access:
            self._dll.ShamrockSetAutoSlitWidth(self._device, index, width_um)

    def GetAutoSlitWidth(self, index):
        """
        index (1<=int<=4): Slit number
        return (0<float): slit opening width in m
        """
        assert(1 <= index <= 4)
        width_um = c_float()
        with self._hw_access:
            self._dll.ShamrockGetAutoSlitWidth(self._device, index, byref(width_um))
        return width_um.value * 1e-6

    def AutoSlitReset(self, index):
        """
        index (1<=int<=4): Slit number
        """
        assert(1 <= index <= 4)
        with self._hw_access:
            self._dll.ShamrockAutoSlitReset(self._device, index)

    def AutoSlitIsPresent(self, index):
        """
        Finds if a specified slit is present.
        index (1<=int<=4): Slit number
        return (bool): True if slit is present
        """
        assert(1 <= index <= 4)
        present = c_int()
        self._dll.ShamrockAutoSlitIsPresent(self._device, index, byref(present))
        return (present.value != 0)

# Mirror flipper management
    def SetFlipperMirror(self, flipper, port):
        assert(1 <= flipper <= 2)
        assert(0 <= port <= 1)

        with self._hw_access:
            self._dll.ShamrockSetFlipperMirror(self._device, flipper, port)

    def GetFlipperMirror(self, flipper):
        assert(1 <= flipper <= 2)
        port = c_int()

        with self._hw_access:
            self._dll.ShamrockGetFlipperMirror(self._device, flipper, byref(port))
        return port.value

# def ShamrockFlipperMirrorReset(int device, int flipper);

    def FlipperMirrorIsPresent(self, flipper):
        assert(1 <= flipper <= 2)
        present = c_int()
        self._dll.ShamrockFlipperMirrorIsPresent(self._device, flipper, byref(present))
        return (present.value != 0)

# def ShamrockGetCCDLimits(int device, int port, float *Low, float *High);

    # "Accessory" port control (= 2 TTL lines)
    def SetAccessory(self, line, val):
        """
        line (1 <= int <= 2): line number
        val (boolean): True = On, False = Off
        """
        if val:
            state = 1
        else:
            state = 0

        self._dll.ShamrockSetAccessory(self._device, line, state)

    # def ShamrockGetAccessoryState(int device,int Accessory, int *state);
    def AccessoryIsPresent(self):
        present = c_int()
        self._dll.ShamrockAccessoryIsPresent(self._device, byref(present))
        return (present.value != 0)

    # Helper functions
    def _getGratingChoices(self):
        """
        return (dict int -> string): grating number to description
        """
        ngratings = self.GetNumberGratings()
        gchoices = {}
        for g in range(1, ngratings + 1):
            try:
                lines, blaze, home, offset = self.GetGratingInfo(g)
                if blaze is None:
                    gchoices[g] = "%.1f l/mm (mirror)" % (lines * 1e-3)
                else:
                    gchoices[g] = "%.1f l/mm (blaze: %g nm)" % (lines * 1e-3, blaze * 1e9)
            except ShamrockError:
                logging.exception("Failed to get grating info for %d", g)
                gchoices[g] = "unknown"

        return gchoices

    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        """
        pos = {"wavelength": self.GetWavelength(),
               "grating": self.GetGrating()
              }

        if self._slit:
            pos["slit"] = self.GetAutoSlitWidth(self._slit)

        if "flip-out" in self.axes:
            v = self.GetFlipperMirror(OUTPUT_FLIPPER)
            for userv, port in FLIPPER_TO_PORT.items():
                if v == port:
                    pos["flip-out"] = userv
                    break

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    def getPixelToWavelength(self):
        """
        return (list of floats): pixel number -> wavelength in m
        """
        # If wavelength is 0, report empty list to indicate it makes no sense
        if self.position.value["wavelength"] == 0:
            return []

        npixels = self._camera.resolution.value[0]

        self.SetNumberPixels(npixels)
        self.SetPixelWidth(self._camera.pixelSize.value[0] * self._camera.binning.value[0])
        # TODO: can GetCalibration() return several values identical? eg, 0's if
        # cw is near 0 nm? If so, something should be done, as GUI hates that...
        return self.GetCalibration(npixels)

    @isasync
    def moveRel(self, shift):
        """
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        returns (Future): future that control the asynchronous move
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        fs = []
        for axis in shift:
            if axis == "wavelength":
                # cannot convert it directly to an absolute move, because
                # several in a row must mean they accumulate. So we queue a
                # special task. That also means the range check is delayed until
                # the actual position is known.
                f = self._executor.submit(self._doSetWavelengthRel, shift[axis])
                fs.append(f)
            elif axis == "slit":
                f = self._executor.submit(self._doSetSlitRel, shift[axis])
                fs.append(f)
        # TODO: handle correctly when more than one future
        return fs[-1]

    @isasync
    def moveAbs(self, pos):
        """
        Move the stage the defined values in m for each axis given.
        pos dict(string-> float): name of the axis and new position in m
        returns (Future): future that control the asynchronous move
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        # If grating needs to be changed, change it first, then the wavelength
        fs = []
        if "grating" in pos:
            g = pos["grating"]
            wl = pos.get("wavelength")
            fs.append(self._executor.submit(self._doSetGrating, g, wl))
        elif "wavelength" in pos:
            wl = pos["wavelength"]
            fs.append(self._executor.submit(self._doSetWavelengthAbs, wl))

        # TODO: handle correctly more than one future
        if "slit" in pos:
            width = pos["slit"]
            fs.append(self._executor.submit(self._doSetSlitAbs, width))

        if "flip-out" in pos:
            fs.append(self._executor.submit(self._doSetFlipper, OUTPUT_FLIPPER, pos["flip-out"]))

        return fs[-1]

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

        # don't complain if the user asked for non reachable wl: he couldn't know
        minp, maxp = self.GetWavelengthLimits(self.GetGrating())
        pos = min(max(minp, pos), maxp)

        self.SetWavelength(pos)
        self._updatePosition()

    def _doSetWavelengthAbs(self, pos):
        """
        Change the wavelength to a value
        """
        # don't complain if the user asked for non reachable wl: he couldn't know
        minp, maxp = self.GetWavelengthLimits(self.GetGrating())
        pos = min(max(minp, pos), maxp)

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

    def _doSetSlitRel(self, shift):
        """
        Change the slit width by a value
        """
        width = self.GetAutoSlitWidth(self._slit) + shift
        # it's only now that we can check the absolute position is wrong
        minp, maxp = self.axes["slit"].range
        if not minp <= width <= maxp:
            raise ValueError("Position %f of axis '%s' not within range %f→%f" %
                             (width, "slit", minp, maxp))

        self.SetAutoSlitWidth(self._slit, width)
        self._updatePosition()

    def _doSetSlitAbs(self, width):
        """
        Change the slit width to a value
        """
        self.SetAutoSlitWidth(self._slit, width)
        self._updatePosition()

    def _doSetFlipper(self, flipper, pos):
        """
        Change the flipper position to one of the two positions
        """
        v = FLIPPER_TO_PORT[pos]
        self.SetFlipperMirror(flipper, v)
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

        if self._device is not None:
            logging.debug("Shutting down the spectrograph")
            self.Close()
            self._device = None

#     def __del__(self):
#         self.terminate()

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

    # TODO scan

# Only for testing/simulation purpose
# Very rough version that is just enough so that if the wrapper behaves correctly,
# it returns the expected values. Copied from andorcam2

def _deref(p, typep):
    """
    p (byref object)
    typep (c_type): type of pointer
    Use .value to change the value of the object
    """
    # This is using internal ctypes attributes, that might change in later
    # versions. Ugly!
    # Another possibility would be to redefine byref by identity function:
    # byref= lambda x: x
    # and then dereferencing would be also identity function.
    return typep.from_address(addressof(p._obj))

def _val(obj):
    """
    return the value contained in the object. Needed because ctype automatically
    converts the arguments to c_types if they are not already c_type
    obj (c_type or python object)
    """
    if isinstance(obj, ctypes._SimpleCData):
        return obj.value
    else:
        return obj


class FakeShamrockDLL(object):
    """
    Fake ShamrockDLL. It basically simulates a spectrograph connected.
    """

    def __init__(self, ccd=None):
        # gratings: l/mm, blaze, home, offset, min wl, max wl
        self._gratings = [(299.9, "300.0", 1000, -200, 0.0, 5003.6),
                          (601.02, "500.0", 10000, 26, 0.0, 1578.95),
                          (1200.1, "500.0", 30000, -65, 0.0, 808.65)]

        self._cw = 300.2 # current wavelength (nm)
        self._cg = 1 # current grating (1->3)
        self._pw = 0 # pixel width
        self._np = 0 # number of pixels

        # TODO: simulate slit and mirror flipper

        # just for simulating the limitation of the iDus
        self._ccd = ccd

    def _check_hw_access(self):
        """
        Simulate hw connection failure if the CCD is acquiring, like the
        SR303i via the I²C connection of the iDus
        """
        if self._ccd and self._ccd.GetStatus() == andorcam2.AndorV2DLL.DRV_ACQUIRING:
            raise ShamrockError(20201, ShamrockDLL.err_code[20201])

    def ShamrockInitialize(self, path):
        pass

    def ShamrockClose(self):
        self._cw = None # should cause failure if calling anything else

    def ShamrockGetNumberDevices(self, p_nodevices):
        nodevices = _deref(p_nodevices, c_int)
        nodevices.value = 1

    def ShamrockGetSerialNumber(self, device, serial):
        serial.value = "SR303fake"

#    def ShamrockEepromGetOpticalParams(self, device,
#                 byref(FocalLength), byref(AngularDeviation), byref(FocalTilt)):
#        pass

    def ShamrockSetGrating(self, device, grating):
        self._check_hw_access()
        new_g = _val(grating)
        time.sleep(min(1, abs(new_g - self._cg)) * 5) # very bad estimation
        self._cg = new_g

    def ShamrockGetGrating(self, device, p_grating):
        self._check_hw_access()
        grating = _deref(p_grating, c_int)
        grating.value = self._cg

    def ShamrockGetNumberGratings(self, device, p_nogratings):
        nogratings = _deref(p_nogratings, c_int)
        nogratings.value = len(self._gratings)

    def ShamrockWavelengthReset(self, device):
        self._check_hw_access()
        time.sleep(abs(self._cw) / 1000)
        self._cw = 0

    def ShamrockGetGratingInfo(self, device, grating,
                               p_lines, s_blaze, p_home, p_offset):
        lines = _deref(p_lines, c_float)
        home = _deref(p_home, c_int)
        offset = _deref(p_offset, c_int)
        info = self._gratings[_val(grating) - 1][0:4]
        lines.value, s_blaze.value, home.value, offset.value = info

    def ShamrockSetWavelength(self, device, wavelength):
        self._check_hw_access()
        # TODO: raise if outside of the grating range
        new_wl = _val(wavelength)
        time.sleep(abs(self._cw - new_wl) / 1000)
        self._cw = new_wl

    def ShamrockGetWavelength(self, device, p_wavelength):
        self._check_hw_access()
        wavelength = _deref(p_wavelength, c_float)
        wavelength.value = self._cw

    def ShamrockGetWavelengthLimits(self, device, grating, p_min, p_max):
        minwl, maxwl = _deref(p_min, c_float), _deref(p_max, c_float)
        minwl.value, maxwl.value = self._gratings[_val(grating) - 1][4:6]

    def ShamrockWavelengthIsPresent(self, device, p_present):
        present = _deref(p_present, c_int)
        present.value = 1 # yes!

    def ShamrockGetCalibration(self, device, calibval, npixels):
        center = (self._np - 1) / 2 # pixel containing center wl
        px_wl = self._pw / 50 # in nm
        minwl = self._gratings[self._cg - 1][4]
        for i in range(npixels):
            # return stupid values (that look slightly correct)
            calibval[i] = max(minwl, self._cw + (i - center) * px_wl)

    def ShamrockSetPixelWidth(self, device, width):
        self._pw = _val(width)

    def ShamrockSetNumberPixels(self, device, npixels):
        self._np = _val(npixels)

    def ShamrockAutoSlitIsPresent(self, device, index, p_present):
        present = _deref(p_present, c_int)
        present.value = 0 # no!

    def ShamrockFlipperMirrorIsPresent(self, device, flipper, p_present):
        present = _deref(p_present, c_int)
        present.value = 0  # no!

    def ShamrockAccessoryIsPresent(self, device, p_present):
        present = _deref(p_present, c_int)
        present.value = 0  # no!


class AndorSpec(model.Detector):
    """
    Spectrometer component, based on a AndorCam2 and a Shamrock
    """
    def __init__(self, name, role, children=None, daemon=None, **kwargs):
        """
        All the arguments are identical to AndorCam2, expected: 
        children (dict string->kwargs): name of child must be "shamrock" and the
          kwargs contains the arguments passed to instantiate the Shamrock component
        """
        # we will fill the set of children with Components later in ._children
        model.Detector.__init__(self, name, role, daemon=daemon, **kwargs)

        # Create the detector (ccd) child
        try:
            dt_kwargs = children["andorcam2"]
        except Exception:
            raise ValueError("AndorSpec excepts one child named 'andorcam2'")

        # We could inherit from it, but difficult to not mix up .binning, .shape
        # .resolution...
        self._detector = andorcam2.AndorCam2(parent=self, daemon=daemon, **dt_kwargs)
        self.children.value.add(self._detector)
        dt = self._detector

        # Copy and adapt the VAs and roattributes from the detector
                # set up the detector part
        # check that the shape is "horizontal"
        if dt.shape[0] <= 1:
            raise ValueError("Child detector must have at least 2 pixels horizontally")
        if dt.shape[0] < dt.shape[1]:
            logging.warning("Child detector is shaped vertically (%dx%d), "
                            "this is probably incorrect, as wavelengths are "
                            "expected to be along the horizontal axis",
                            dt.shape[0], dt.shape[1])
        # shape is same as detector (raw sensor), but the max resolution is always flat
        self._shape = tuple(dt.shape) # duplicate

        # The resolution and binning are derived from the detector, but with
        # settings set so that there is only one horizontal line.
        if dt.binning.range[1][1] < dt.resolution.range[1][1]:
            # without software binning, we are stuck to the max binning
            logging.info("Spectrometer %s will only use a %d px band of the %d "
                         "px of the sensor", name, dt.binning.range[1][1],
                         dt.resolution.range[1][1])

        resolution = (dt.resolution.range[1][0], 1) # max,1
        # vertically: 1, with binning as big as possible
        binning = (dt.binning.value[0],
                   min(dt.binning.range[1][1], dt.resolution.range[1][1]))

        min_res = (dt.resolution.range[0][0], 1)
        max_res = (dt.resolution.range[1][0], 1)
        self.resolution = model.ResolutionVA(resolution, [min_res, max_res],
                                             setter=self._setResolution)
        # 2D binning is like a "small resolution"
        self._binning = binning
        self.binning = model.ResolutionVA(self._binning, dt.binning.range,
                                          setter=self._setBinning)

        self._setBinning(binning) # will also update the resolution

        # TODO: update also the metadata MD_SENSOR_PIXEL_SIZE
        pxs = dt.pixelSize.value[0], dt.pixelSize.value[1] * dt.binning.value[1]
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)
        # Note: the metadata has no MD_PIXEL_SIZE, but a MD_WL_LIST

        # TODO: support software binning by rolling up our own dataflow that
        # does data merging
        assert dt.resolution.range[0][1] == 1
        self.data = dt.data

        # duplicate every other VA and Event from the detector
        # that includes required VAs like .exposureTime
        for aname, value in model.getVAs(dt).items() + model.getEvents(dt).items():
            if not hasattr(self, aname):
                setattr(self, aname, value)
            else:
                logging.debug("skipping duplication of already existing VA '%s'", aname)

        assert hasattr(self, "exposureTime")

        # Create the spectrograph (actuator) child
        try:
            sp_kwargs = children["shamrock"]
        except Exception:
            raise ValueError("AndorSpec excepts one child named 'shamrock'")

        self._spectrograph = Shamrock(parent=self, camera=self._detector,
                                      daemon=daemon, **sp_kwargs)
        self.children.value.add(self._spectrograph)

        self._spectrograph.position.subscribe(self._onPositionUpdate)
        self.resolution.subscribe(self._onResBinningUpdate)
        self.binning.subscribe(self._onResBinningUpdate, init=True)

    def _setBinning(self, value):
        """
        Called when "binning" VA is modified. It also updates the resolution so
        that the horizontal AOI is approximately the same. The vertical size
        stays 1.
        value (int): how many pixels horizontally and vertically
          are combined to create "super pixels"
        """
        prev_binning = self._binning
        self._binning = tuple(value) # duplicate

        # adapt horizontal resolution so that the AOI stays the same
        changeh = prev_binning[0] / self._binning[0]
        old_resolution = self.resolution.value
        assert old_resolution[1] == 1
        new_resh = int(round(old_resolution[0] * changeh))
        new_resh = max(min(new_resh, self.resolution.range[1][0]), self.resolution.range[0][0])
        new_resolution = (new_resh, 1)

        # setting resolution and binning is slightly tricky, because binning
        # will change resolution to keep the same area. So first set binning, then
        # resolution
        self._detector.binning.value = value
        self.resolution.value = new_resolution
        return value

    def _setResolution(self, value):
        """
        Called when the resolution VA is to be updated.
        """
        # only the width might change
        assert value[1] == 1

        # fit the width to the maximum possible given the binning
        max_size = int(self.resolution.range[1][0] // self._binning[0])
        min_size = int(math.ceil(self.resolution.range[0][0] / self._binning[0]))
        size = (max(min(value[0], max_size), min_size), 1)

        self._detector.resolution.value = size
        assert self._detector.resolution.value[1] == 1 # TODO: handle this by software mean

        return size

    def _onResBinningUpdate(self, value):
        """
        Called when the resolution or the binning changes
        """
        self._updateWavelengthList()

    def _onPositionUpdate(self, pos):
        """
        Called when the wavelength position or grating (ie, groove density)
          of the spectrograph is changed.
        """
        self._updateWavelengthList()

    def _updateWavelengthList(self):
        wll = self._spectrograph.getPixelToWavelength()
        md = {model.MD_WL_LIST: wll}
        self._detector.updateMetadata(md)

    def terminate(self):
        self._spectrograph.terminate()
        self._detector.terminate()

    def selfTest(self):
        return super(AndorSpec, self).selfTest() and self._spectrograph.selfTest()
