# -*- coding: utf-8 -*-
'''
Created on 17 Feb 2014

@author: Éric Piel

Copyright © 2014-2019 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# This is a driver for the Andor Shamrock & Kymera spectographs.

from past.builtins import basestring
from ctypes import *
import ctypes
import logging
import math
from odemis import model
import odemis
from odemis.driver import andorcam2
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
from odemis import util
from odemis.util import driver, to_str_escape
import os
import signal
import sys
import threading
import time
import itertools

# Constants from ShamrockCIF.h
ACCESSORYMIN = 0  # changed in the latest version (from 1->2)
ACCESSORYMAX = 1
FILTERMIN = 1
FILTERMAX = 6
TURRETMIN = 1
TURRETMAX = 3
GRATINGMIN = 1
# Note: the documentation mentions the width is in mm, but it's actually µm.
SLITWIDTHMIN = 10
SLITWIDTHMAX = 2500
# SHAMROCK_I24SLITWIDTHMAX 24000
SHUTTERMODEMIN = 0
SHUTTERMODEMAX = 2  # Note: 1 is max on SR303, 2 is max on SR193
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

ERRORLENGTH = 64

# A couple of handy constants
SHUTTER_CLOSE = 0
SHUTTER_OPEN = 1
SHUTTER_BNC = 2  # = driven by external signal


def callWithReconnect(fun):
    """
    Wrapper for functions that communicate with the shamrock.
    In case the connection is lost (e.g. due to em-interference), we
    try to reconnect and send the command again.
    """
    def wrapper(self, *args, **kwargs):
        try:
            return fun(self, *args, **kwargs)
        except ShamrockError as ex:
            if self._reconnecting:
                raise
            else:
                logging.error("Communication with shamrock failed with "
                              "exception '%s', trying to reconnect." % ex)
                if ex.errno in (20201, 20275):  # COMMUNICATION_ERROR / NOT_INITIALIZED
                    self._reconnect()
                    return fun(self, *args, **kwargs)
                else:
                    raise
    return wrapper

class ShamrockError(IOError):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(ShamrockError, self).__init__(errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror


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
            try:
                # Global so that its sub-libraries can access it
                CDLL.__init__(self, "libshamrockcif.so.2", RTLD_GLOBAL)
            except OSError:
                # Renamed to atspectrograph since v2.103
                # (and the functions have been renamed too, but the old names
                #  are still valid)
                CDLL.__init__(self, "libatspectrograph.so.2", RTLD_GLOBAL)

    def at_errcheck(self, result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns SHAMROCK_SUCCESS on correct usage
        if result not in ShamrockDLL.ok_code:
            errmsg = create_string_buffer(ERRORLENGTH)
            self.ShamrockGetFunctionReturnDescription(result, errmsg, len(errmsg))
            raise ShamrockError(result,
                                "Call to %s failed with error %d: %s" %
                                (func.__name__, result, errmsg.value.decode('latin1')))
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
        20202: "SUCCESS",
    }

    err_code = {
        20201: "COMMUNICATION_ERROR",
        20249: "ERROR",
        20266: "P1INVALID",
        20267: "P2INVALID",
        20268: "P3INVALID",
        20269: "P4INVALID",
        20270: "P5INVALID",
        20275: "NOT_INITIALIZED",
        20292: "NOT_AVAILABLE",
    }


class HwAccessMgr(object):
    """
    Context manager that ensures that the CCD is not doing any acquisition
    while within the context.
    """
    def __init__(self, ccd):
        """
        ccd (AndorCam2 or None)
        """
        self._ccd = ccd
        if ccd is None:
            self.hw_lock = threading.RLock()

    def __enter__(self):
        if self._ccd is None:
            # logging.debug("Taking spectrograph lock")
            self.hw_lock.acquire()
        else:
            self._ccd.request_hw.append(None)  # let the acquisition thread know it should release the lock
            logging.debug("Requesting access to CCD")
            self._ccd.hw_lock.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        """
        returns True if the exception is to be suppressed (never)
        """
        if self._ccd is None:
            # logging.debug("Released spectrograph lock")
            self.hw_lock.release()
        else:
            self._ccd.request_hw.pop()  # hw no more needed
            logging.debug("Released CCD lock")
            self._ccd.hw_lock.release()


class LedActiveMgr(object):
    """
    Context manager that signal that the leds (might) be on. The signal
    is a _low_ level on the TTL accessory output. When the leds are for sure
    off, the TTL level is set to _high_.
    This typically happens when the slits move, and can cause damage to some
    detectors.
    """

    def __init__(self, comps, settle_time):
        """
        comps (list of Components): the components to control, with .protection
        settle_time (0<=float): duration to wait when entering
        """
        self._comps = comps
        self._settle_time = settle_time
        self._prev_prot = [c.protection.value for c in comps]

    def __enter__(self):
        if not self._comps:
            return

        logging.debug("Indicating leds are on")

        for i, c in enumerate(self._comps):
            try:
                self._prev_prot[i] = c.protection.value
                if not self._prev_prot[i]:
                    c.protection.value = True
            except Exception:
                logging.exception("Failed to activate protection for %s", c.name)

        # wait a little to make sure the detectors are really off
        time.sleep(self._settle_time)

    def __exit__(self, exc_type, exc_value, traceback):
        """
        returns True if the exception is to be suppressed (never)
        """
        if not self._comps:
            return

        logging.debug("Indicating leds are off")

        # Unprotect iff previously the protection was off
        for c, pp in zip(self._comps, self._prev_prot):
            if not pp:
                try:
                    c.protection.value = pp
                except Exception:
                    logging.exception("Failed to disable protection for %s", c.name)


# default names for the slits
SLIT_NAMES = {INPUT_SLIT_SIDE: "slit-in-side",  # Note: previously it was called "slit"
              INPUT_SLIT_DIRECT: "slit-in-direct",
              OUTPUT_SLIT_SIDE: "slit-out-side",
              OUTPUT_SLIT_DIRECT: "slit-out-direct",
             }

# The two values exported by the Odemis API for the flipper positions
FLIPPER_TO_PORT = {0: DIRECT_PORT,
                   math.radians(90): SIDE_PORT}

MODEL_KY193 = "KY-193i"
MODEL_SR303 = "SR-303"
MODEL_KY328 = "KY-328i"


class Shamrock(model.Actuator):
    """
    Component representing the spectrograph part of the Andor Shamrock/Kymera
    spectrometers.
    On Linux, the SR303i is supported since SDK 2.97, and the other ones,
    including the KY193i since SDK 2.99. The KY328 is supported since SDK 2.103.
    The SR303i must be connected via the I²C cable on the iDus. With SDK 2.100+,
    it also work via the direct USB connection.
    Note: we don't handle changing turret (live).
    """
    def __init__(self, name, role, device, camera=None, accessory=None,
                 slitleds_settle_time=1e-3, slits=None, bands=None, rng=None,
                 fstepsize=1e-6, drives_shutter=None, dependencies=None, **kwargs):
        """
        device (0<=int or str): if int, device number, if str serial number or
          "fake" to use the simulator
        camera (None or AndorCam2): Needed if the connection is done via the
          I²C connector of the camera.
        inverted (None): it is not allowed to invert the axes
        slits (None, or dict int -> str, or dict int -> [str]): names of each slit,
          for 1 to 4: in-side, in-direct, out-side, out-direct
          Append "force_max" for a slit which requires to move to the maximum
          value before going to the requested position. This is a workaround for
          proper movement when the slit's reference switch doesn't work.
        accessory (str or None): if "slitleds", then a TTL signal will be set to
          high on line 1 whenever one of the slit leds might be turned on.
        slitleds_settle_time (0 <= float): duration wait before (potentially)
          turning on the slit leds. Useful to delay the move after the slitleds
          interlock is set.
        bands (None or dict 1<=int<=6 -> 2-tuple of floats > 0 or str):
          wavelength range or name of each filter for the filter wheel from 1->6.
          Positions without filters do not need to be defined.
        fstepsize (0<float): size of one step on the focus actuator. Not very
          important, mostly useful for providing to the user a rough idea of how
          much the image will change after a move.
        rng (dict str -> (float, float)): the min/max values for each axis.
          They should within the standard hardware limits. If an axis is not
          specified, the standard hardware limits are used.
          For now it *only* works for the focus axis.
        drives_shutter (list of float): flip-out angles for which the shutter
          should be set to BNC (external) mode. Otherwise, the shutter is left
          opened.
        dependencies (None or dict str -> HwComponent): if the key starts with
          "led_prot", it will set the .protection to True any time that the
          slit leds could be turned on.
        """
        # From the documentation:
        # If controlling the shamrock through I²C it is important that both the
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

        # Note: it used to need a "ccd" dependency, but not anymore
        self._camera = camera
        self._hw_access = HwAccessMgr(camera)
        self._is_via_camera = (camera is not None)

        dependencies = dependencies or {}
        rng = rng or {}

        self._slit_names = SLIT_NAMES.copy()
        self._force_slit_max = set()
        slits = slits or {}
        for i, slitn in slits.items():
            if not SLIT_INDEX_MIN <= i <= SLIT_INDEX_MAX:
                raise ValueError("Slit number must be between 1 and 4, but got %s" % (i,))
            if isinstance(slitn, basestring):
                self._slit_names[i] = slitn
            elif len(slitn) >= 2:  # ["name", "option"]
                self._slit_names[i] = slitn[0]
                if "force_max" in slitn[1:]:
                    self._force_slit_max.add(i)
            else:
                raise ValueError("Slit name should be string or a list of strings, but got %s" % (slitn,))

        self.Initialize()
        self._reconnecting = False

        try:
            if isinstance(device, basestring):
                self._device = self._findDevice(device)
            else:
                nd = self.GetNumberDevices()
                if device >= nd:
                    raise HwError("Failed to find Andor Shamrock (%s) as device %s" %
                                  (name, device))
                self._device = device

            # TODO: EEPROM contains name of the device, but there doesn't seem to be any function for getting it?!
            fl, ad, ft = self.EepromGetOpticalParams()
            if 0.190 <= fl <= 0.200:
                self._model = MODEL_KY193
            elif 0.296 <= fl <= 0.310:
                self._model = MODEL_SR303
            elif 0.326 <= fl <= 0.330:
                self._model = MODEL_KY328
            else:
                self._model = None
                logging.warning("Untested spectrograph with focus length %d mm", fl * 1000)

            led_comps = [] # list of tuples (Light, float) or (Shamrock, int, float)
            if accessory is not None and not self.AccessoryIsPresent():
                raise ValueError("Accessory set to '%s', but no accessory connected"
                                 % (accessory,))
            if accessory == "slitleds":
                # To control the ttl signal from outside the component
                self.protection = model.BooleanVA(True, setter=self._setProtection)
                self._setProtection(True)
                led_comps.append(self)

            for cr, dep in dependencies.items():
                if cr.startswith("led_prot"):
                    led_comps.append(dep)

            self._led_access = LedActiveMgr(led_comps, slitleds_settle_time)

            # for now, it's fixed (and it's unlikely to be useful to allow less than the max)
            max_speed = 1000e-9 / 5 # about 1000 nm takes 5s => max speed in m/s
            self.speed = model.MultiSpeedVA({"wavelength": max_speed},
                                            range=(max_speed, max_speed),
                                            unit="m/s",
                                            readonly=True)

            gchoices = self._getGratingChoices()

            # The actual limits are per grating. We cannot provide this much
            # info via the .axes attribute, so just lowest and largest
            # wavelength reachable
            wl_range = (float("inf"), float("-inf"))
            for g in gchoices:
                try:
                    wmin, wmax = self.GetWavelengthLimits(g)
                except ShamrockError:
                    logging.exception("Failed to find wavelength limit for grating %d", g)
                    continue
                wl_range = min(wl_range[0], wmin), max(wl_range[1], wmax)

            axes = {"wavelength": model.Axis(unit="m", range=wl_range,
                                             speed=(max_speed, max_speed)),
                    "grating": model.Axis(choices=gchoices)
                    }

            if self.FocusMirrorIsPresent():
                if not 0 < fstepsize <= 0.1:  # m
                    raise ValueError("fstepsize is %f but should be between 0 and 0.1m" % (fstepsize,))
                self._focus_step_size = fstepsize
                mx = self.GetFocusMirrorMaxSteps() * fstepsize
                frng = (fstepsize, mx)
                if "focus" in rng:
                    sw_frng = rng.pop("focus")
                    if not (frng[0] <= sw_frng[0] < sw_frng[1] <= frng[1]):
                        raise ValueError("rng focus should be within %s" % (frng,))
                    frng = sw_frng
                axes["focus"] = model.Axis(unit="m", range=frng)
                logging.info("Focus actuator added as 'focus'")

                # Store the focus position for each grating/output port combination.
                # The hardware is supposed to store them already, but actually
                # only stores it when on first output, and on other outputs, it
                # stores an offset. This leads to "surprises" because changing
                # a focus position at a given grating/output can change some other
                # focus positions. So we override this behaviour, and always use
                # the focus position last set for a grating/output. In case the
                # focus hasn't been set yet, we still rely on the hardware to
                # guess the best focus.
                self._go2focus = {}  # (int, int) -> int: grating/output -> focus steps
            else:
                logging.info("Focus actuator is not present")

            if self.FilterIsPresent():
                if bands is None:  # User gave no info => fallback to what the hardware knows
                    # TODO: way to detect that a position has no filter?
                    bands = {i: self.GetFilterInfo(i) for i in range(FILTERMIN, FILTERMAX + 1)}
                else:  # Check the content
                    try:
                        for pos, band in bands.items():
                            if not FILTERMIN <= pos <= FILTERMAX:
                                raise ValueError("Filter position should be between %d and "
                                                 "%d, but got %d." % (FILTERMIN, FILTERMAX, pos))
                            # To support "weird" filter, we accept strings
                            if isinstance(band, basestring):
                                if not band.strip():
                                    raise ValueError("Name of filter %d is empty" % pos)
                            else:
                                driver.checkLightBand(band)
                    except Exception:
                        logging.exception("Failed to parse bands %s", bands)
                        raise

                    # If the current position is not among the known positions =>
                    # add this position
                    b = self.GetFilter()
                    if b not in bands:
                        bands[b] = self.GetFilterInfo(b)

                axes["band"] = model.Axis(choices=bands)
                logging.info("Filter wheel added as 'band'")
            else:
                if bands is not None:
                    raise ValueError("Device %s has no filter wheel, but 'bands'"
                                     " argument provided." % (device,))
                logging.info("Filter wheel is not present")

            # add slits which are available
            for i, slitn in self._slit_names.items():
                if self.AutoSlitIsPresent(i):
                    axes[slitn] = model.Axis(unit="m",
                                             range=(SLITWIDTHMIN * 1e-6, SLITWIDTHMAX * 1e-6)
                                             )
                    logging.info("Slit %d added as %s", i, slitn)
                else:
                    logging.info("Slit %d (%s) is not present", i, slitn)

            if self.FlipperMirrorIsPresent(OUTPUT_FLIPPER):
                # The position values are arbitrary, but these are the one we
                # typically use in Odemis for switching between two positions
                axes["flip-out"] = model.Axis(unit="rad",
                                              choices=set(FLIPPER_TO_PORT.keys())
                                              )
                logging.info("Adding out mirror flipper as flip-out")
                self._sanitiesFlipper(OUTPUT_FLIPPER)
            else:
                logging.info("Out mirror flipper is not present")
            # TODO: support INPUT_FLIPPER

            # Associate the output port to the shutter position
            # TODO: have a RO VA to represent the position of the shutter?
            # Or a VA to allow overriding the state?
            if drives_shutter is None:
                drives_shutter = []
            self._drives_shutter = set()

            # "Convert" the roughly correct position values to the exact values
            for pos in drives_shutter:
                if "flip-out" in axes:
                    allowed_pos = axes["flip-out"].choices
                else:
                    # No flipper => only allow position 0 = direct port
                    allowed_pos = {0}
                closest = util.find_closest(pos, allowed_pos)
                if util.almost_equal(closest, pos, rtol=1e-3):
                    self._drives_shutter.add(closest)
                else:
                    raise ValueError("drives_shutter position %g not in %s" % (
                                     pos, allowed_pos))

            if self.ShutterIsPresent():
                if drives_shutter and not self._model == MODEL_KY193:
                    raise ValueError("Device doesn't support BNC mode for shutter")
                if "flip-out" in axes:
                    val = self.GetFlipperMirror(OUTPUT_FLIPPER)
                    userv = [k for k, v in FLIPPER_TO_PORT.items() if v == val][0]
                else:
                    userv = 0
                self._updateShutterMode(userv)
            else: # No shutter
                if drives_shutter:
                    raise ValueError("Device has no shutter, but drives_shutter provided")
                logging.info("No shutter is present")

            # provides a ._axes
            model.Actuator.__init__(self, name, role, axes=axes, **kwargs)

            # set HW and SW version
            self._swVersion = "%s" % (odemis.__version__,)
            sn = self.GetSerialNumber()
            self._hwVersion = ("%s (s/n: %s, focal length: %d mm)" %
                               ("Andor Shamrock", sn, round(fl * 1000)))

            # will take care of executing axis move asynchronously
            self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

            # RO, as to modify it the client must use .moveRel() or .moveAbs()
            self.position = model.VigilantAttribute({}, readonly=True)
            self._updatePosition()

            # For getPixelToWavelength()
            self._px2wl_lock = threading.Lock()

        except Exception:
            self.Close()
            raise

    def _setProtection(self, value):
        """
        value (bool): True = TTL signal down (off), False = TTL signal up (on)
        """
        line = 0  # just a fixed line
        self.SetAccessory(line, not value)

        return value

    def _sanitiesFlipper(self, flipper):
        """
        Make sure the flipper is in good order by working around hardware and
        software bugs.
        flipper (int): the flipper for which to apply the workaround
        """
        # Some hardware don't have a working mirror position detector, and the
        # only way to make sure it's at the right position is to ask to go there.

        assert(FLIPPER_INDEX_MIN <= flipper <= FLIPPER_INDEX_MAX)

        port = c_int()
        with self._hw_access:
            self._dll.ShamrockGetFlipperMirror(self._device, flipper, byref(port))

        # On the Kymera 193, there is a double firmware bug (as of 20160801/SDK 2.101.30001):
        # * When requesting a flipper move from the current position to the
        #  _same_ position, the focus offset is applied anyway.
        # * When opening the device via the SDK, the focus is moved by the
        #  (flipper) focus offset. Most likely, this is because the SDK or the
        #  firmware attempts to move the flipper to the same position as it's
        #  currently is (ie, first bug).
        # => workaround that second bug by 'taking advantage' of the first bug.
        # Since firmware 1.2 (ie 201611), both bugs are fixed, so both actions
        # are a no-op.
        if self._model == MODEL_KY193 and self.FocusMirrorIsPresent():
            with self._hw_access:
                # Init has already moved focus by +Foffset (cf bug #2)
                focus_init = c_int()
                self._dll.ShamrockGetFocusMirror(self._device, byref(focus_init))
                logging.info("Calling SetFlipperMirror back and forth to work-around "
                             "focus initialisation, starting with focus @ %d stp on port %d",
                             focus_init.value, port.value)
                other_port = {DIRECT_PORT: SIDE_PORT, SIDE_PORT: DIRECT_PORT}[port.value]
                self._dll.ShamrockSetFlipperMirror(self._device, flipper, other_port)  # -Foffset
                self._dll.ShamrockSetFlipperMirror(self._device, flipper, other_port)  # -Foffset (bug #1)
                self._dll.ShamrockSetFlipperMirror(self._device, flipper, port)  # +Foffset
                # => focus is at original position
                focus_end = c_int()
                self._dll.ShamrockGetFocusMirror(self._device, byref(focus_end))
                logging.info("Focus is now @ %d stp on port %d",
                             focus_end.value, port.value)
        else:
            # Just make sure it's at the right position
            # TODO: doesn't the SDK already do this?
            logging.info("Calling SetFlipperMirror on port %d to ensure the position", port.value)
            with self._hw_access:
                self._dll.ShamrockSetFlipperMirror(self._device, flipper, port)

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

        if sys.version_info[0] >= 3:  # Python 3
            path = os.fsencode(path)

        # TODO: Catch the signal and raise an HwError in case it took too long.
        # Unfortunately, as we are calling C code from Python it's really hard,
        # because the GIL is hold on and won't let us call any python code anymore.
        try:
            # Prepare to get killed (via SIGALRM) in case it took too long,
            # because Initialize() is buggy and can block forever if it's
            # confused by the hardware.
            # Note, SDK 2.100.30026+ has now a timeout of 2 minutes.
            signal.setitimer(signal.ITIMER_REAL, 150)
            self._dll.ShamrockInitialize(path)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def Close(self):
        self._dll.ShamrockClose()

    def _reconnect(self):
        """
        Attempt to reconnect the device. It will block until this happens.
        On return, the hardware should be ready to use as before, excepted it
        still needs the settings to be applied.
        """
        self.state._set_value(HwError("Spectrograph disconnected"), force_write=True)
        self._reconnecting = True
        logging.debug("Reconnecting spectrograph...")
        self.Close()

        # In order to make reestablish the connection, we need to turn the power off and on again and then
        # reinitialize.
        logging.debug("Cycling power...")
        if model.hasVA(self, 'powerSupply'):
            self.powerSupply.value = False
            time.sleep(2)  # wait a bit, otherwise the system doesn't notice
            self.powerSupply.value = True
            logging.debug("Cycling power complete.")
        else:
            raise ValueError("Spectrograph doesn't have a power supplier, aborting reconnect.")

        # Initialization
        self.Initialize()  # blocking, takes 2 min

        # Check if it's working
        # If the function is called on startup, we are not ready to call ._updatePosition yet
        try:
            self._updatePosition()
            logging.debug("Restarting spectrograph after power cycling was successful.")
        except ShamrockError as ex:
            logging.error("Unable to restart spectrograph. Try to turn the power off and on again. Failed "
                          "with exception %s." % ex)
        finally:
            self._reconnecting = False
        self.state._set_value(model.ST_RUNNING, force_write=True)

    def GetNumberDevices(self):
        """
        Returns (0<=int) the number of available Shamrocks
        """
        nodevices = c_int()
        with self._hw_access:
            self._dll.ShamrockGetNumberDevices(byref(nodevices))
        return nodevices.value

    @callWithReconnect
    def GetSerialNumber(self):
        """
        Returns the device serial number
        """
        serial = create_string_buffer(64) # hopefully always fit! (normally 6 bytes)
        with self._hw_access:
            self._dll.ShamrockGetSerialNumber(self._device, serial)
        return serial.value.decode('latin1')

    # Probably not needed, as ShamrockGetCalibration returns everything already
    # computed
    @callWithReconnect
    def EepromGetOpticalParams(self):
        """
        Returns (tuple of 3 floats): Focal Length (m), Angular Deviation (rad) and
           Focal Tilt (rad) from the Shamrock device.
        """
        FocalLength = c_float()
        AngularDeviation = c_float()
        FocalTilt = c_float()
        with self._hw_access:
            self._dll.ShamrockEepromGetOpticalParams(self._device,
                 byref(FocalLength), byref(AngularDeviation), byref(FocalTilt))

        return FocalLength.value, math.radians(AngularDeviation.value), math.radians(FocalTilt.value)

    @callWithReconnect
    def SetTurret(self, turret):
        """
        Changes the turret (=set of gratings installed in the spectrograph)
        Note: all gratings will be changed afterwards
        turret (1<=int<=3)
        """
        assert TURRETMIN <= turret <= TURRETMAX

        with self._hw_access:
            logging.debug("Moving turret to %d", turret)
            self._dll.ShamrockSetTurret(self._device, turret)

    @callWithReconnect
    def GetTurret(self):
        """
        return (1<=int<=3): current turret
        """
        turret = c_int()
        with self._hw_access:
            self._dll.ShamrockGetTurret(self._device, byref(turret))
        return turret.value

    @callWithReconnect
    def SetGrating(self, grating):
        """
        Note: it will update the focus position (if there is a focus)
        grating (1<=int<=4)
        """
        assert 1 <= grating <= 4

        # Seems currently the SDK sometimes fail with SHAMROCK_COMMUNICATION_ERROR
        # as in SetWavelength()
        with self._hw_access:
            retry = 0
            logging.debug("Moving grating to %d", grating)
            while True:
                try:
                    self._dll.ShamrockSetGrating(self._device, grating)
                except ShamrockError as ex:
                    if ex.errno != 20201 or retry >= 5: # SHAMROCK_COMMUNICATION_ERROR
                        raise
                    # just try again
                    retry += 1
                    logging.info("Failed to set grating, will try again")
                    time.sleep(0.1 * retry)
                else:
                    break

    @callWithReconnect
    def GetGrating(self):
        """
        return (1<=int<=4): current grating
        """
        grating = c_int()
        with self._hw_access:
            self._dll.ShamrockGetGrating(self._device, byref(grating))
        return grating.value

    @callWithReconnect
    def GetNumberGratings(self):
        """
        return (1<=int<=4): number of gratings present
        """
        noGratings = c_int()
        with self._hw_access:
            self._dll.ShamrockGetNumberGratings(self._device, byref(noGratings))
        return noGratings.value

    @callWithReconnect
    def WavelengthReset(self):
        """
        Resets the wavelength to 0 nm, and go to the first grating.
        (Probably this also ensures the wavelength axis is referenced again.)
        """
        # Same as ShamrockGotoZeroOrder()
        with self._hw_access:
            logging.debug("Reseting wavelength to 0 nm")
            self._dll.ShamrockWavelengthReset(self._device)

    #ShamrockAtZeroOrder(self._device, int *atZeroOrder);

    @callWithReconnect
    def GetGratingInfo(self, grating):
        """
        grating (1<=int<=4)
        return:
              lines (float): number of lines / m
              blaze (str): wavelength or mirror info, as reported by the device
                Note that some devices add a unit (eg, nm), and some don't.
                When it is a mirror, there is typically a "mirror" keyword.
              home (int): beginning of the grating in steps
              offset (int): offset to the grating in steps
        """
        assert 1 <= grating <= 4
        Lines = c_float() # in l/mm
        Blaze = create_string_buffer(64) # decimal of wavelength in nm
        Home = c_int()
        Offset = c_int()
        with self._hw_access:
            self._dll.ShamrockGetGratingInfo(self._device, grating,
                             byref(Lines), Blaze, byref(Home), byref(Offset))
        logging.debug("Grating %d is %f, %s, %d, %d", grating,
                      Lines.value, Blaze.value, Home.value, Offset.value)

        return Lines.value * 1e3, Blaze.value.decode('latin1'), Home.value, Offset.value

    # this function sometimes raises a 20201 error that's not related to a lost connection,
    # so try 5 times before reconnecting
    def SetWavelength(self, wavelength):
        """
        Sets the required wavelength.
        wavelength (0<=float): wavelength in m
        """
        assert 0 <= wavelength <= 50e-6

        # Currently the SDK sometimes fail with 20201: SHAMROCK_COMMUNICATION_ERROR
        # when changing wavelength by a few additional nm. It _seems_ that it
        # works anyway (but not sure).
        # It seems that retrying a couple of times just works
        with self._hw_access:
            logging.debug("Moving wavelength to %g nm", wavelength * 1e9)
            retry = 0
            while True:
                try:
                    # set in nm
                    self._dll.ShamrockSetWavelength(self._device, c_float(wavelength * 1e9))
                except ShamrockError as ex:
                    if ex.errno == 20201 and retry < 5: # COMMUNICATION_ERROR
                        # just try again
                        retry += 1
                        logging.info("Failed to set wavelength, will try again")
                        time.sleep(0.1)
                    elif ex.errno in (20201, 20275):  # COMMUNICATION_ERROR / NOT_INITIALIZED
                        self._reconnect()
                        retry = 0
                    else:
                        raise
                else:
                    break

    @callWithReconnect
    def GetWavelength(self):
        """
        Gets the current wavelength.
        return (0<=float): wavelength in m
        """
        with self._hw_access:
            wavelength = c_float() # in nm
            self._dll.ShamrockGetWavelength(self._device, byref(wavelength))
        return wavelength.value * 1e-9

    @callWithReconnect
    def GetWavelengthLimits(self, grating):
        """
        grating (1<=int<=4)
        return (0<=float< float): min, max wavelength in m
        """
        assert 1 <= grating <= 4
        Min, Max = c_float(), c_float() # in nm
        with self._hw_access:
            self._dll.ShamrockGetWavelengthLimits(self._device, grating,
                                                  byref(Min), byref(Max))
        return Min.value * 1e-9, Max.value * 1e-9

    @callWithReconnect
    def WavelengthIsPresent(self):
        """
        return (boolean): True if it's possible to change the wavelength
        """
        present = c_int()
        with self._hw_access:
            self._dll.ShamrockWavelengthIsPresent(self._device, byref(present))
        return present.value != 0

    @callWithReconnect
    def GetCalibration(self, npixels):
        """
        npixels (0<int): number of pixels on the sensor. It's actually the
          length of the list that is being returned. Note: on small center
          wavelength, the values might be meaningless, and multiple 0 nm can be
          returned.
        return (list of floats of length npixels): wavelength in m
        """
        assert(0 < npixels)
        # Warning: if npixels <= 7, very weird/large values are returned (with SDK 2.100).
        # Probably because GetPixelCalibrationCoefficients() returns also very
        # strange polynomial.
        if npixels <= 7:
            logging.warning("Requested calibration info for %d pixels, which is known to fail", npixels)
        logging.debug("Requesting calibration info for %d px", npixels)
        # TODO: this is pretty slow, and could be optimised either by using a
        # numpy array or returning directly the C array. We could also just
        # allocate one array at the init, and reuse it.
        CalibrationValues = (c_float * npixels)()
        # Note that although it looks like it could do without hardware access,
        # it is necessary. For example, the SDK call can completely block if a
        # move is currently happening.
        with self._hw_access:
            self._dll.ShamrockGetCalibration(self._device, CalibrationValues, npixels)
        logging.debug("Calibration info returned")
        # Note: it just applies the polynomial, so you can end up with negative
        # values. We used to change all to 0, but that was even more confusing
        # because multiple bins were associated to 0.
        return [v * 1e-9 for v in CalibrationValues]

    @callWithReconnect
    def GetPixelCalibrationCoefficients(self):
        """
        return (4 floats)
        """
        a, b, c, d = c_float(), c_float(), c_float(), c_float()
        with self._hw_access:
            self._dll.ShamrockGetPixelCalibrationCoefficients(self._device, byref(a), byref(b), byref(c), byref(d))
        return a.value, b.value, c.value, d.value

    @callWithReconnect
    def GetCCDLimits(self, port):
        """
        Gets the upper and lower accessible wavelength through the port.
        port (int)
        return (float, float): low/high wavelength in m
        """
        low = c_float()
        high = c_float()
        with self._hw_access:
            self._dll.ShamrockGetCCDLimits(self._device, port, byref(low), byref(high))
        return low.value * 1e-9, high.value * 1e-9

    @callWithReconnect
    def SetPixelWidth(self, width):
        """
        Defines the size of each pixel (horizontally).
        Needed to get correct information from GetCalibration()
        width (float): size of a pixel in m
        """
        # set in µm
        with self._hw_access:
            self._dll.ShamrockSetPixelWidth(self._device, c_float(width * 1e6))

    @callWithReconnect
    def SetNumberPixels(self, npixels):
        """
        Defines how many pixels (around the center) are used.
        Needed to get correct information from GetCalibration()
        npixels (int): number of pixels on the attached sensor
        """
        with self._hw_access:
            self._dll.ShamrockSetNumberPixels(self._device, npixels)

#self._dll.ShamrockGetPixelWidth(self._device, float* Width)
#self._dll.ShamrockGetNumberPixels(self._device, int* NumberPixels)

    # For hardware calibration
    @callWithReconnect
    def SetDetectorOffset(self, entrancep, exitp, offset):
        with self._hw_access:
            self._dll.ShamrockSetDetectorOffsetEx(self._device, entrancep, exitp, offset)

    @callWithReconnect
    def GetDetectorOffset(self, entrancep, exitp):
        offset = c_int()
        with self._hw_access:
            self._dll.ShamrockGetDetectorOffsetEx(self._device, entrancep, exitp, byref(offset))
        return offset.value

    @callWithReconnect
    def SetGratingOffset(self, grating, offset):
        with self._hw_access:
            self._dll.ShamrockSetGratingOffset(self._device, grating, offset)

    @callWithReconnect
    def GetGratingOffset(self, grating):
        offset = c_int()
        with self._hw_access:
            self._dll.ShamrockGetGratingOffset(self._device, grating, byref(offset))
        return offset.value

    # Focus mirror management
    @callWithReconnect
    def SetFocusMirror(self, steps):
        """
        Relative move on the focus
        Note: It's RELATIVE!!!
        Note: the position is saved per grating + offset for the detector.
        It seems that changing when on first detector updates the Fgrating_n value
        and changing on the second detector only updates the Fdetector offset.
        steps (int): relative numbers of steps to do
        """
        assert isinstance(steps, int)
        # The documentation states focus is >=0, but SR193 only accepts >0
        with self._hw_access:
            logging.debug("Moving focus mirror by %d stp", steps)
            self._dll.ShamrockSetFocusMirror(self._device, steps)

    @callWithReconnect
    def FocusMirrorReset(self):
        """
        Resets the filter to its default position. It references the focus.
        Note: it's unknown (yet) what is the effect on the focus position recording,
        but one could hope that it's equivalent to calling SetFocusMirror on that
        position.
        """
        with self._hw_access:
            logging.debug("Resetting focus mirror position")
            self._dll.ShamrockFocusMirrorReset(self._device)

    @callWithReconnect
    def GetFocusMirror(self):
        """
        Get the current position of the focus
        return (0<=int<=maxsteps): absolute position (in steps)
        """
        focus = c_int()
        with self._hw_access:
            self._dll.ShamrockGetFocusMirror(self._device, byref(focus))
        return focus.value

    @callWithReconnect
    def GetFocusMirrorMaxSteps(self):
        """
        Get the maximum position of the focus
        return (0 <= int): absolute max position (in steps)
        """
        focus = c_int()
        with self._hw_access:
            self._dll.ShamrockGetFocusMirrorMaxSteps(self._device, byref(focus))
        return focus.value

    @callWithReconnect
    def FocusMirrorIsPresent(self):
        present = c_int()
        self._dll.ShamrockFocusMirrorIsPresent(self._device, byref(present))
        return present.value != 0

    # Filter wheel support
    @callWithReconnect
    def SetFilter(self, pos):
        """
        Absolute move on the filter wheel
        pos (1<=int<=6): new position
        """
        assert(FILTERMIN <= pos <= FILTERMAX)
        with self._hw_access:
            logging.debug("Moving filter to %d", pos)
            self._dll.ShamrockSetFilter(self._device, pos)

    @callWithReconnect
    def GetFilter(self):
        """
        Return the current absolute position of the filter wheel
        return (1<=int<=6): current filter
        """
        pos = c_int()
        with self._hw_access:
            self._dll.ShamrockGetFilter(self._device, byref(pos))
        return pos.value

    @callWithReconnect
    def GetFilterInfo(self, pos):
        """
        pos (int): filter number
        return (str): the text associated to the given filter
        """
        info = create_string_buffer(64)  # TODO: what's a good size? The SDK doc says nothing
        with self._hw_access:
            self._dll.ShamrockGetFilterInfo(self._device, pos, info)
        return info.value.decode('latin1')

    @callWithReconnect
    def FilterIsPresent(self):
        present = c_int()
        self._dll.ShamrockFilterIsPresent(self._device, byref(present))
        return present.value != 0

    # Slits management
    @callWithReconnect
    def SetAutoSlitWidth(self, index, width):
        """
        index (1<=int<=4): Slit number
        width (0<float): slit opening width in m
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        width_um = c_float(width * 1e6)

        # TODO: on the SR-193 (and maybe other spectrographs), the led actually
        # is only turned on when going to the minimum position (probably because
        # it's used as a referencing move). If it's a long move, the led will
        # stay turned on for a long time. So, to avoid keeping the led on for
        # too long, we could first do a (long) move up to near the minimum, and
        # then a (short) move to the minimum.
        with self._hw_access:
            with self._led_access:
                logging.debug("Moving slit %d to %g um", index, width_um.value)
                self._dll.ShamrockSetAutoSlitWidth(self._device, index, width_um)

    @callWithReconnect
    def GetAutoSlitWidth(self, index):
        """
        index (1<=int<=4): Slit number
        return (0<float): slit opening width in m
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        width_um = c_float()
        with self._hw_access:
            self._dll.ShamrockGetAutoSlitWidth(self._device, index, byref(width_um))
        return width_um.value * 1e-6

    @callWithReconnect
    def AutoSlitReset(self, index):
        """
        index (1<=int<=4): Slit number
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        with self._hw_access:
            with self._led_access:
                self._dll.ShamrockAutoSlitReset(self._device, index)

    @callWithReconnect
    def AutoSlitIsPresent(self, index):
        """
        Finds if a specified slit is present.
        index (1<=int<=4): Slit number
        return (bool): True if slit is present
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        present = c_int()
        self._dll.ShamrockAutoSlitIsPresent(self._device, index, byref(present))
        return present.value != 0

    # Note: the following 4 functions are not documented (although advertised in
    # the changelog and in the include file)
    # Available since SDK 2.100, but not documented, and raise a "Not available"
    # error with the SR-193i
    @callWithReconnect
    def SetAutoSlitCoefficients(self, index, x1, y1, x2, y2):
        """
        No idea what this does! (Excepted guesses from the name)
        index (1<=int<=4): Slit number
        x1, y1, x2, y2 (ints): ???
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        with self._hw_access:
            self._dll.ShamrockSetAutoSlitCoefficients(self._device, index, x1, y1, x2, y2)

    @callWithReconnect
    def GetAutoSlitCoefficients(self, index):
        """
        No idea what this does! (Excepted guesses from the name)
        index (1<=int<=4): Slit number
        return: x1, y1, x2, y2 (ints) ???
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        x1 = c_int()
        y1 = c_int()
        x2 = c_int()
        y2 = c_int()
        with self._hw_access:
            self._dll.ShamrockGetAutoSlitCoefficients(self._device, index,
                                  byref(x1), byref(y1), byref(x2), byref(y2))

        return x1.value, y1.value, x2.value, y2.value

    # Available since SDK 2.101, but only with newer firmware (ie, 1.2+,
    # after 2016-11). Earlier firmware will raises either "Communication error"
    # (for the Set) or "Parameter 3 invalid" (for the Get)
    # So for now, we don't support auto-reconnect on these functions. Because
    # otherwise it would reboot the spectrograph every time the function is called.
    # @callWithReconnect
    def SetSlitZeroPosition(self, index, offset):
        """
        Changes the offset for the position of the given slit, to ensure that
        when the slit is at its minimum opening, any increase in opening will
        lead to an actual increase.
        After the call, the reported slit position is changed (even if it hasn't
        physically moved).
        index (1<=int<=4): Slit number
        offset (-200 <= int <= 0): some value representing the distance that needs to be moved
          by the actuator for the slit to just be closed when set to 0
          (ie, any further move would open a bit the slit)
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        with self._hw_access:
            self._dll.ShamrockSetSlitZeroPosition(self._device, index, offset)

    # @callWithReconnect
    def GetSlitZeroPosition(self, index):
        """
        Read the current calibration offset for the slit position.
        index (1<=int<=4): Slit number
        return (int): the offset
        """
        assert(SLIT_INDEX_MIN <= index <= SLIT_INDEX_MAX)
        offset = c_int()
        with self._hw_access:
            self._dll.ShamrockGetSlitZeroPosition(self._device, index, byref(offset))

        return offset.value

    # Shutter management
    @callWithReconnect
    def SetShutter(self, mode):
        assert(SHUTTERMODEMIN <= mode <= SHUTTERMODEMAX)
        with self._hw_access:
            logging.info("Setting shutter to mode %d", mode)
            self._dll.ShamrockSetShutter(self._device, mode)

    @callWithReconnect
    def GetShutter(self):
        mode = c_int()

        with self._hw_access:
            self._dll.ShamrockGetShutter(self._device, byref(mode))
        return mode.value

    @callWithReconnect
    def IsModePossible(self, mode):
        possible = c_int()

        # Note: mode = 2 causes a "Invalid argument" error. Reported 2016-09-16.
        with self._hw_access:
            self._dll.ShamrockIsModePossible(self._device, mode, byref(possible))
        return possible.value != 0

    @callWithReconnect
    def ShutterIsPresent(self):
        present = c_int()

        with self._hw_access:
            self._dll.ShamrockShutterIsPresent(self._device, byref(present))
        return present.value != 0

    # Mirror flipper management
    @callWithReconnect
    def SetFlipperMirror(self, flipper, port):
        """
        Switches the given mirror to a different position.
        Note: The focus position is updated, but the detector offset (= turret
          position extra angle) is _not_ updated.
        Note 2: As of 20160801, the focus position is not always correctly updated.
          It is seen as special focus offset, but if asked to move to the same
          position as it's currently in (= no move), it will still apply the offset.
          Also, if the offset would lead to moving the focus out of range, it's
          not applied _at all_ and is then saved as 0.
        flipper (int from *PUT_FLIPPER): the mirror index
        port (int from *_PORT): the new position
        """
        assert(FLIPPER_INDEX_MIN <= flipper <= FLIPPER_INDEX_MAX)
        assert(0 <= port <= 1)

        # If focus position is different for each flipper position, the SR-193
        # gets a bit confused and if changing to the same value, it will move
        # the focus. So avoid changing to the current value. (Reported 20160801)
        if self.GetFlipperMirror(flipper) == port:
            logging.info("Not changing again flipper %d to current pos %d", flipper, port)
            return

        with self._hw_access:
            logging.debug("Moving flipper %d to pos %d", flipper, port)
            self._dll.ShamrockSetFlipperMirror(self._device, flipper, port)

    @callWithReconnect
    def GetFlipperMirror(self, flipper):
        """
        flipper (int from *PUT_FLIPPER): the mirror index
        return (int from *_PORT): the port position
        """
        assert(FLIPPER_INDEX_MIN <= flipper <= FLIPPER_INDEX_MAX)
        port = c_int()

        with self._hw_access:
            self._dll.ShamrockGetFlipperMirror(self._device, flipper, byref(port))
        return port.value

# def ShamrockFlipperMirrorReset(int device, int flipper);

    @callWithReconnect
    def FlipperMirrorIsPresent(self, flipper):
        """
        flipper (int from *PUT_FLIPPER): the mirror index
        """
        assert(FLIPPER_INDEX_MIN <= flipper <= FLIPPER_INDEX_MAX)
        present = c_int()
        self._dll.ShamrockFlipperMirrorIsPresent(self._device, flipper, byref(present))
        return present.value != 0

    # "Accessory" port control (= 2 TTL lines)
    @callWithReconnect
    def SetAccessory(self, line, val):
        """
        line (0 <= int <= 1): line number
        val (boolean): True = On, False = Off
        """
        assert(ACCESSORYMIN <= line <= ACCESSORYMAX)
        if val:
            state = 1
        else:
            state = 0
        with self._hw_access:
            logging.debug("Setting accessory line %d to %d", line, state)
            self._dll.ShamrockSetAccessory(self._device, line, state)

            # HACK: the Andor driver has a problem and sets the spectrograph in a
            # bad state after setting the accessory to True. This puts it back in a
            # good state.
            self.GetGrating()

    @callWithReconnect
    def GetAccessoryState(self, line):
        """
        line (0 <= int <= 1): line number
        return (boolean): True = On, False = Off
        """
        assert(ACCESSORYMIN <= line <= ACCESSORYMAX)
        state = c_int()
        with self._hw_access:
            self._dll.ShamrockGetAccessoryState(self._device, line, byref(state))
        return state.value != 0

    @callWithReconnect
    def AccessoryIsPresent(self):
        present = c_int()
        self._dll.ShamrockAccessoryIsPresent(self._device, byref(present))
        return present.value != 0

    # Helper functions
    def _getGratingChoices(self):
        """
        return (dict int -> string): grating number to description
        """
        logging.debug("Current turret: %s", self.GetTurret())
        ngratings = self.GetNumberGratings()
        if ngratings < 1:
            logging.warning("No grating found on the current turret %s, it's probably not properly configured",
                            self.GetTurret())

        gchoices = {}
        for g in range(1, ngratings + 1):
            try:
                lines, blaze, home, offset = self.GetGratingInfo(g)
                if lines == 0 or "mirror" in blaze.lower():
                    logging.debug("Considering grating %d a mirror", g)
                    gchoices[g] = "mirror"
                else:
                    gchoices[g] = "%.0f l/mm (blaze: %s)" % (lines * 1e-3, blaze)
            except ShamrockError:
                logging.exception("Failed to get grating info for %d", g)
                gchoices[g] = "unknown"

        return gchoices

    # high-level methods (interface)
    def _updatePosition(self, must_notify=False):
        """
        update the position VA
        """
        # TODO: support "axes" to limit the axes to update
        pos = {"wavelength": self.GetWavelength(),
               "grating": self.GetGrating()
              }

        if "focus" in self.axes:
            # Note: can change after changing the grating
            pos["focus"] = self.GetFocusMirror() * self._focus_step_size

        if "band" in self.axes:
            pos["band"] = self.GetFilter()

        for i, name in self._slit_names.items():
            if name in self.axes:
                pos[name] = self.GetAutoSlitWidth(i)

        if "flip-out" in self.axes:
            val = self.GetFlipperMirror(OUTPUT_FLIPPER)
            userv = [k for k, v in FLIPPER_TO_PORT.items() if v == val][0]
            pos["flip-out"] = userv

        self.position._set_value(pos, must_notify=must_notify, force_write=True)

    def _storeFocus(self):
        """
        To be called whenever the user has changed the focus, to store the value,
        associated to the current grating & output port.
        """
        if "focus" not in self.axes:
            return

        g = self.GetGrating()
        if "flip-out" in self.axes:
            op = self.GetFlipperMirror(OUTPUT_FLIPPER)
        else:
            op = 0
        f = self.GetFocusMirror()
        self._go2focus[(g, op)] = f

    def _restoreFocus(self):
        """
        To be called whenever the grating or output flipper have been moved, to
        ensure that the focus is set back to the previous good position
        """
        if "focus" not in self.axes:
            return

        g = self.GetGrating()
        if "flip-out" in self.axes:
            op = self.GetFlipperMirror(OUTPUT_FLIPPER)
        else:
            op = DIRECT_PORT
        current_f = self.GetFocusMirror()

        try:
            f = self._go2focus[(g, op)]
        except KeyError:
            logging.debug("No known focus for %d/%d, using %d stp", g, op, current_f)
            return
        try:
            logging.debug("Restoring focus for %d/%d to %d stp", g, op, f)
            self.SetFocusMirror(f - current_f)  # relative move
        except Exception:
            logging.exception("Failed to set focus to %d stp", f)

    def getPixelToWavelength(self, npixels, pxs):
        """
        Return the lookup table pixel number of the CCD -> wavelength observed.
        Note: if an axis is moving, the call blocks until the move is complete.
        npixels (10 <= int): number of pixels on the CCD (horizontally), after
          binning.
        pxs (0 < float): pixel size in m (after binning)
        return (list of floats): pixel number -> wavelength in m
        """
        # If wavelength is 0, report empty list to indicate it makes no sense
        cw = self.position.value["wavelength"]
        if cw <= 1e-9:
            return []

        # We need a lock to ensure that in case this function is called twice
        # simultaneously, it doesn't interleave the calls
        with self._px2wl_lock:
            # Check again, in case it changed before acquiring the lock
            cw = self.position.value["wavelength"]
            if cw <= 1e-9:
                return []

            self.SetNumberPixels(npixels)
            self.SetPixelWidth(pxs)
            calib = self.GetCalibration(npixels)
        if calib[-1] < 1e-9:
            cw = self.position.value["wavelength"]
            logging.error("Calibration data doesn't seem valid, will use internal one (cw = %f nm): %s",
                          cw * 1e9, calib)
            try:
                return self._FallbackGetPixelToWavelength(npixels, pxs)
            except Exception:
                logging.exception("Failed to compute pixel->wavelength (cw = %f nm)",
                                  cw * 1e9)
                return []
        return calib

    def _FallbackGetPixelToWavelength(self, npixels, pxs):
        """
        Fallback version that only uses the basic optical properties of the
          spectrograph (and doesn't rely on the sometimes non-working SDK
          functions)
        Return the lookup table pixel number of the CCD -> wavelength observed.
        npixels (1 <= int): number of pixels on the CCD (horizontally), after
          binning.
        pxs (0 < float): pixel size in m (after binning)
        return (list of floats): pixel number -> wavelength in m
        """
        centerpixel = (npixels - 1) / 2
        cw = self.position.value["wavelength"]  # m
        gid = self.position.value["grating"]
        if self.axes["grating"].choices[gid] == "mirror":
            logging.debug("Returning no wavelength information for mirror grating")
            return []

        gl = self.GetGratingInfo(gid)[0]  # lines/meter
        if gl < 1e-5:
            logging.warning("Trying to compute pixel->wavelength with null lines/mm")
            return []
        # fl = focal length (m)
        # ia = inclusion angle (rad)
        # da = detector angle (rad)
        fl, adev, da = self.EepromGetOpticalParams()
        ia = -adev * 2

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

    def getOpeningToWavelength(self, width):
        """
        Computes the range of the wavelength observed for a given slit opening
        width (in front of the detector).
        That is correct for the current grating/wavelength.
        width (float): opening width in m
        return (float, float): minimum/maximum wavelength observed
        """
        # Pretend we have a small CCD and look at the wavelength at the side
        # Note: In theory, we could just say we have 2 pixels, but the SDK doesn't
        # seem to put the center exactly at the center of the sensor (ie, it
        # seems pixel npixels/2 get the center wavelength), and the SDK doesn't
        # like resolutions < 8 anyway.
        self.SetNumberPixels(10)
        self.SetPixelWidth(width / 10)
        calib = self.GetCalibration(10)
        return calib[0], calib[-1]

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

        # cannot convert it directly to an absolute move, because
        # several in a row must mean they accumulate. So we queue a
        # special task. That also means the range check is delayed until
        # the actual position is known.

        actions = []
        for axis, s in shift.items():  # order doesn't matter
            if axis == "wavelength":
                actions.append((axis, self._doSetWavelengthRel, s))
            elif axis == "focus":
                actions.append((axis, self._doSetFocusRel, s))
            elif axis in self._slit_names.values():
                sid = [k for k, v in self._slit_names.items() if v == axis][0]
                actions.append((axis, self._doSetSlitRel, sid, s))
            else:
                raise NotImplementedError("Relative move of axis %s not supported" % (axis,))

        f = self._executor.submit(self._doMultipleActions, actions)
        return f

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
        ordered_axes = ("grating", "wavelength", "band", "focus", "flip-out") + tuple(self._slit_names.values())
        actions = []
        for axis in ordered_axes:
            try:
                p = pos[axis]
            except KeyError:
                continue
            if axis == "grating":
                actions.append((axis, self._doSetGrating, p))
            elif axis == "wavelength":
                actions.append((axis, self._doSetWavelengthAbs, p))
            elif axis == "band":
                actions.append((axis, self._doSetFilter, p))
            elif axis == "focus":
                actions.append((axis, self._doSetFocusAbs, p))
            elif axis == "flip-out":
                actions.append((axis, self._doSetFlipper, OUTPUT_FLIPPER, p))
            elif axis in self._slit_names.values():
                sid = [k for k, v in self._slit_names.items() if v == axis][0]
                actions.append((axis, self._doSetSlitAbs, sid, p))

        f = self._executor.submit(self._doMultipleActions, actions)
        return f

    def _doMultipleActions(self, actions):
        """
        Run multiple actions sequentially (as long as they don't raise exceptions)
        actions (tuple of tuple(str, callable, *args)): ordered actions defined
          by the axis name, callable, and the arguments
        """
        for a in actions:
            an, func, args = a[0], a[1], a[2:]
            try:
                func(*args)
            except Exception:
                logging.exception("Failure during move of axis %s", an)
                raise

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
        rpos = min(max(minp, pos), maxp)
        if rpos != pos:
            logging.info("Limiting wavelength to %f nm (requested %f nm)",
                         rpos * 1e9, pos * 1e9)

        self.SetWavelength(rpos)
        self._updatePosition()

    def _doSetGrating(self, g):
        """
        Setter for the grating VA.
        It will try to put the same wavelength as before the change of grating.
        Synchronous until the grating is finished (up to 30s)
        g (1<=int<=3): the new grating
        """
        # Make sure that getPixelToWavelength() can be called in-between the
        # moves as the intermediary position might not accepted by the HW.
        with self._px2wl_lock:
            self.SetGrating(g)
            # This is a trick, to immediately report the new position, in case
            # getPixelToWavelength() uses it. It's not notified.
            self.position._value["grating"] = g

            # By default the Shamrock library keeps the same wavelength

            # With a mirror as grating, the SR193 always stays physically at wavelength 0,
            # but it doesn't report it back (aka changing wavelength value in the position VA)
            # after changing the grating to a mirror.
            # So this can lead to positions in the position VA, which are impossible to set
            # (e.g. grating = "mirror" and wavelength = "300").
            # Also, calling GetCalibration() on a mirror raises an error.
            # Setting the wavelength to 0 ensures that getPixelToWavelength()
            # never tries to call GetCalibration(), but simply returns an empty wavelength list
            # and that the position VA is correctly updated.
            if self.axes["grating"].choices[g] == "mirror":
                logging.debug("Grating is mirror, so resetting wavelength to 0")
                self.SetWavelength(0)
                self.position._value["wavelength"] = 0  # same trick

        self._restoreFocus()
        self._updatePosition(must_notify=True)

    def _doSetFocusRel(self, shift):
        # it's only now that we can check the goal (absolute) position is wrong
        shift_st = int(round(shift / self._focus_step_size))
        steps = self.GetFocusMirror() + shift_st  # absolute pos
        if not 0 < steps <= self.GetFocusMirrorMaxSteps():
            rng = self.axes["focus"].range
            raise ValueError(u"Position %f of axis 'focus' not within range %f→%f" %
                             (steps * self._focus_step_size, rng[0], rng[1]))

        self.SetFocusMirror(shift_st)  # needs relative value
        self._storeFocus()
        self._updatePosition()

    def _doSetFocusAbs(self, pos):
        steps = int(round(pos / self._focus_step_size))
        shift_st = steps - self.GetFocusMirror()
        self.SetFocusMirror(shift_st)  # needs relative value
        self._storeFocus()
        self._updatePosition()

    def _doSetFilter(self, pos):
        self.SetFilter(pos)
        self._updatePosition()

    def _doSetSlitRel(self, sid, shift):
        """
        Change the slit width by a value
        sid (int): slit ID
        shift (float): change in opening size in m
        """
        width = self.GetAutoSlitWidth(sid) + shift
        # it's only now that we can check the absolute position is wrong
        n = self._slit_names[sid]
        rng = self.axes[n].range
        if not rng[0] <= width <= rng[1]:
            raise ValueError(u"Position %f of axis '%s' not within range %f→%f" %
                             (width, n, rng[0], rng[1]))

        self._doSetSlitAbs(sid, width)

    def _doSetSlitAbs(self, sid, width):
        """
        Change the slit width to a value
        sid (int): slit ID
        width (float): new position in m
        """
        if sid in self._force_slit_max:
            # Workaround for broken reference sensor by first going to the maximum
            self.SetAutoSlitWidth(sid, SLITWIDTHMAX * 1e-6)

        self.SetAutoSlitWidth(sid, width)
        self._updatePosition()

    def _doSetFlipper(self, flipper, pos):
        """
        Change the flipper position to one of the two positions
        """
        v = FLIPPER_TO_PORT[pos]
        self.SetFlipperMirror(flipper, v)
        if flipper == OUTPUT_FLIPPER:
            self._updateShutterMode(pos)
            self._restoreFocus()
        # Note: That function _only_ changes the mirror position.
        # It doesn't update the turret position, based on the (new) detector offset
        # => Force it by moving an "empty" move
        # Note: Setting the detector offset or wavelength would also do the job
        try:
            self.SetGrating(self.GetGrating())
        except ShamrockError:
            logging.warning("Failed to update turret position, detector offset might be incorrect", exc_info=True)
        self._updatePosition()

    def _updateShutterMode(self, pos):
        """
        Update the state of the shutter depending on the detector used.
        pos (float): (user) position of the output flipper mirror
        """
        if not self.ShutterIsPresent():
            return
        if pos in self._drives_shutter:
            self.SetShutter(SHUTTER_BNC)
        else:
            self.SetShutter(SHUTTER_OPEN)

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

        if self.ShutterIsPresent():
            self.SetShutter(SHUTTER_CLOSE)

        if self._device is not None:
            logging.debug("Shutting down the spectrograph")
            self.Close()
            self._device = None

        super(Shamrock, self).terminate()

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

    def _findDevice(self, sn):
        """
        Look for a device with the given serial number
        sn (str): serial number
        return (int): the device number of the device with the given serial number
        raise HwError: If no device with the given serial number can be found
        """
        serial = create_string_buffer(64)
        for n in range(self.GetNumberDevices()):
            self._dll.ShamrockGetSerialNumber(n, serial)
            if serial.value.decode('latin1') == sn:
                return n
            else:
                logging.info("Skipping Andor Shamrock with S/N %s", to_str_escape(serial.value))
        else:
            raise HwError("Cannot find Andor Shamrock with S/N %s, check it is "
                          "turned on and connected." % (sn,))

    @staticmethod
    def scan():
        dll = ShamrockDLL()
        # TODO: for now it will only find the Shamrocks connected directly via
        # USB, the I²C connections are not detected.
        # => also try to find every AndorCam2 and connect via them?
        dll.ShamrockInitialize("")
        nodevices = c_int()
        dll.ShamrockGetNumberDevices(byref(nodevices))
        logging.info("Scanning %d Andor Shamrock devices", nodevices.value)
        dev = []
        serial = create_string_buffer(64)

        for i in range(nodevices.value):
            dll.ShamrockGetSerialNumber(i, serial)
            logging.debug("Found Shamrock %d with SN %s", i, serial.value)
            dev.append(("Andor Shamrock",
                        {"device": serial.value.decode('latin1')})
                      )

        return dev


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
        self._gratings = [(299.9, b"300.0", 1000, -200, 0.0, 5003.6),
                          # (601.02, "500.0", 10000, 26, 0.0, 1578.95),
                          (0.0, b"Mirror", 10000, 26, 0.0, 0.0),
                          (1200.1, b"500.0", 30000, -65, 0.0, 808.65)]

        self._ct = 1
        self._cw = 300.2 # current wavelength (nm)
        self._cg = 1 # current grating (1->3)
        self._pw = 0 # pixel width
        self._np = 0 # number of pixels

        # focus
        self._focus_pos = 25  # steps
        self._focus_max = 500  # steps
        # Focus is stored for each grating + an offset for each port
        self._gr2focus = [25, 25, 25]
        self._outflip_foff = [0, 0]

        # filter wheel
        self._filter = 1  # current position
        # filter info: pos - 1 -> str
        self._filters = (b"Filter 1",
                         b"Filter 2",
                         b"Filter 3",
                         b"",
                         b"Filter 5",
                         b"",
                         )

        # slits: int (id) -> float (position in µm)
        self._slits = {1: 10.3,
                       3: 1000,
                      }
        # flippers: int (id) -> int (port number, 0 or 1)
        self._flippers = {2: 0}

        # accessory: 2 lines -> int (0 or 1)
        self._accessory = [0, 0]

        # just for simulating the limitation of the iDus
        self._ccd = ccd

        self._shutter_mode = SHUTTER_CLOSE

        # offsets
        # gratting number -> offset (int)
        self._goffset = {i: 0 for i in range(len(self._gratings))}
        # enrance port (flipper #1) / exit port (flipper #2) -> offset (int)
        self._detoffset = {(0, 0): 0,
                           (0, 1): 0,
                           (1, 0): 0,
                           (1, 1): 0,
                          }

    def _check_hw_access(self):
        """
        Simulate hw connection failure if the CCD is acquiring, like the
        SR303i via the I²C connection of the iDus
        """
        if self._ccd and self._ccd.GetStatus() == andorcam2.AndorV2DLL.DRV_ACQUIRING:
            raise ShamrockError(20201, ShamrockDLL.err_code[20201])

    def _updateFocusPos(self):
        # It's not clear whether the SR193 use this algorithm, but at least it's similar.
        # It's sufficient to reproduce the same issue/weirdness as on the SR193
        p = self._gr2focus[self._cg - 1] + self._outflip_foff[self._flippers[OUTPUT_FLIPPER]]
        self._focus_pos = max(0, min(p, self._focus_max))
        if self._focus_pos != p:
            logging.warning("Clipping focus position from %d to %d", p, self._focus_pos)

    def ShamrockInitialize(self, path):
        pass

    def ShamrockClose(self):
        self._cw = None # should cause failure if calling anything else

    def ShamrockGetNumberDevices(self, p_nodevices):
        nodevices = _deref(p_nodevices, c_int)
        nodevices.value = 1

    def ShamrockGetSerialNumber(self, device, serial):
        serial.value = b"SR193fake"

    def ShamrockEepromGetOpticalParams(self, device, p_fl, p_ad, p_ft):
        fl = _deref(p_fl, c_float)
        ad = _deref(p_ad, c_float)
        ft = _deref(p_ft, c_float)
        fl.value = 0.194  # m
        ad.value = 2.3 # °
        ft.value = -2.1695098876953125  # °

    def ShamrockSetTurret(self, device, turret):
        self._ct = _val(turret)

    def ShamrockGetTurret(self, device, p_turret):
        turret = _deref(p_turret, c_int)
        turret.value = self._ct

    def ShamrockSetGrating(self, device, grating):
        self._check_hw_access()
        new_g = _val(grating)
        time.sleep(min(1, abs(new_g - self._cg)) * 5) # very bad estimation
        self._cg = new_g
        self._updateFocusPos()

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

    def ShamrockSetDetectorOffsetEx(self, device, entrancePort, exitPort, offset):
        self._detoffset[_val(entrancePort), _val(exitPort)] = _val(offset)

    def ShamrockGetDetectorOffsetEx(self, device, entrancePort, exitPort, p_offset):
        offset = _deref(p_offset, c_int)
        offset.value = self._detoffset[_val(entrancePort), _val(exitPort)]

    def ShamrockSetGratingOffset(self, device, grating, offset):
        self._goffset[_val(grating) - 1] = _val(offset)

    def ShamrockGetGratingOffset(self, device, grating, p_offset):
        offset = _deref(p_offset, c_int)
        offset.value = self._goffset[_val(grating) - 1]

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
        lpmm = self._gratings[self._cg - 1][0]
        if lpmm == 0:
            raise ShamrockError(20249, ShamrockDLL.err_code[20249])
        else:
            px_wl = self._pw / (lpmm / 6)  # in nm
        minwl = self._gratings[self._cg - 1][4]
        for i in range(npixels):
            # return stupid values (that look slightly correct)
            calibval[i] = max(minwl, self._cw + (i - center) * px_wl)

    def ShamrockSetPixelWidth(self, device, width):
        self._pw = _val(width)

    def ShamrockSetNumberPixels(self, device, npixels):
        self._np = _val(npixels)

    def ShamrockSetFocusMirror(self, device, focus):
        if 0 <= self._focus_pos + focus <= self._focus_max:
            self._focus_pos += focus
            time.sleep(abs(focus) / 100)  # 100 steps/s

            # Update the grating and flipper offset based on the new value
            of = self._flippers[OUTPUT_FLIPPER]
            if of == 0:  # Use the value as "base" for the grating
                self._gr2focus[self._cg - 1] = self._focus_pos
                logging.debug("SIM: Updating focus pos of grating %d to %d", self._cg, self._focus_pos)
            else:
                self._outflip_foff[of] = self._focus_pos - self._gr2focus[self._cg - 1]
                logging.debug("SIM: Updating focus offset of output %d to %d", of, self._outflip_foff[of])
        else:
            raise ShamrockError(20267, ShamrockDLL.err_code[20267])

    def ShamrockGetFocusMirror(self, device, p_focus):
        focus = _deref(p_focus, c_int)
        focus.value = self._focus_pos

    def ShamrockGetFocusMirrorMaxSteps(self, device, p_steps):
        steps = _deref(p_steps, c_int)
        steps.value = self._focus_max

    def ShamrockFocusMirrorReset(self, device):
        self._focus_pos = 0

    def ShamrockFocusMirrorIsPresent(self, device, p_present):
        present = _deref(p_present, c_int)
        present.value = 1 # yes !

    def ShamrockSetFilter(self, device, flter):
        if FILTERMIN <= flter <= FILTERMAX:
            dist = abs(self._filter - flter)
            time.sleep(dist)  # 1s / position
            # TODO: sleep based on most direct move
            self._filter = flter
        else:
            raise ShamrockError(20268, ShamrockDLL.err_code[20268])

    def ShamrockGetFilter(self, device, p_filter):
        flter = _deref(p_filter, c_int)
        flter.value = self._filter

    def ShamrockGetFilterInfo(self, device, flter, s_info):
        s_info.value = self._filters[flter - 1]

#     def ShamrockSetFilterInfo(self, device,int Filter, char* Info):
#     def ShamrockFilterReset(self, device):

    def ShamrockFilterIsPresent(self, device, p_present):
        present = _deref(p_present, c_int)
        if self._filters:
            present.value = 1
        else:
            present.value = 0

    def ShamrockAutoSlitIsPresent(self, device, index, p_present):
        present = _deref(p_present, c_int)
        if _val(index) in self._slits:
            present.value = 1
        else:
            present.value = 0

    def ShamrockGetAutoSlitWidth(self, device, index, p_width):
        width = _deref(p_width, c_float)
        width.value = self._slits[_val(index)]

    def ShamrockSetAutoSlitWidth(self, device, index, width):
        w = _val(width)
        if SLITWIDTHMIN <= w <= SLITWIDTHMAX:
            oldwidth = self._slits[_val(index)]
            time.sleep(abs(oldwidth - w) / 500)
            self._slits[_val(index)] = w
        else:
            raise ShamrockError(20268, ShamrockDLL.err_code[20268])

    def ShamrockGetSlitZeroPosition(self, device, index, p_offset):
        offset = _deref(p_offset, c_int)
        offset.value = -50  # default value

    def ShamrockSetSlitZeroPosition(self, device, index, offset):
        o = _val(offset)
        # raise ShamrockError(20201, ShamrockDLL.err_code[20201])

    def ShamrockShutterIsPresent(self, device, p_present):
        present = _deref(p_present, c_int)
        present.value = 1

    def ShamrockSetShutter(self, device, mode):
        self._shutter_mode = _val(mode)

    def ShamrockGetShutter(self, device, p_mode):
        mode = _deref(p_mode, c_int)
        mode.value = self._shutter_mode

    def ShamrockIsModePossible(self, device, mode, p_possible):
        possible = _deref(p_possible, c_int)
        if SHUTTERMODEMIN <= mode <= SHUTTERMODEMAX:
            possible.value = 1
        else:
            possible.value = 0

    def ShamrockFlipperMirrorIsPresent(self, device, flipper, p_present):
        present = _deref(p_present, c_int)
        if _val(flipper) in self._flippers:
            present.value = 1
        else:
            present.value = 0

    def ShamrockSetFlipperMirror(self, device, flipper, port):
        p = _val(port)
        f = _val(flipper)
        if PORTMIN <= p <= PORTMAX:
            oldport = self._flippers[f]
            time.sleep(abs(oldport - p))
            self._flippers[f] = p
            self._updateFocusPos()
        else:
            raise ShamrockError(20268, ShamrockDLL.err_code[20268])

    def ShamrockGetFlipperMirror(self, device, flipper, p_port):
        port = _deref(p_port, c_int)
        port.value = self._flippers[_val(flipper)]

    def ShamrockAccessoryIsPresent(self, device, p_present):
        present = _deref(p_present, c_int)
        present.value = 1  # yes!

    def ShamrockSetAccessory(self, device, line, state):
        l = _val(line)
        s = _val(state)
        if ACCESSORYMIN <= l <= ACCESSORYMAX:
            self._accessory[l] = s
        else:
            raise ShamrockError(20268, ShamrockDLL.err_code[20268])


class AndorSpec(model.Detector):
    """
    Spectrometer component, based on a AndorCam2 and a Shamrock
    """
    def __init__(self, name, role, children=None, daemon=None, **kwargs):
        """
        All the arguments are identical to AndorCam2, excepted:
        children (dict string->kwargs): Must have two children, one named
         "andorcam2" and the other one named "shamrock".
         The kwargs contains the arguments passed to instantiate the Andorcam2
         and Shamrock components.
        """
        # we will fill the set of children with Components later in ._children
        model.Detector.__init__(self, name, role, daemon=daemon, **kwargs)

        # TODO: update it to allow standard access to the CCD, like the
        # CompositedSpectrometer

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
        self.resolution = model.ResolutionVA(resolution, (min_res, max_res),
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

        assert dt.resolution.range[0][1] == 1
        self.data = dt.data

        # duplicate every other VA and Event from the detector
        # that includes required VAs like .exposureTime
        for aname, value in itertools.chain(model.getVAs(dt).items(), model.getEvents(dt).items()):
            if not hasattr(self, aname):
                setattr(self, aname, value)
            else:
                logging.debug("skipping duplication of already existing VA '%s'", aname)

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
        """
        Updates the wavelength list MD based on the current spectrograph position.
        """
        npixels = self.resolution.value[0]
        pxs = self.pixelSize.value[0] * self.binning.value[0]
        wll = self._spectrograph.getPixelToWavelength(npixels, pxs)
        if len(wll) == 0 and model.MD_WL_LIST in self._detector.getMetadata():
            del self._detector._metadata[model.MD_WL_LIST]  # remove WL list from MD if empty
        else:
            self._detector.updateMetadata({model.MD_WL_LIST: wll})

    def terminate(self):
        self._spectrograph.terminate()
        self._detector.terminate()

    def selfTest(self):
        return super(AndorSpec, self).selfTest() and self._spectrograph.selfTest()
