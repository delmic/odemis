# -*- coding: utf-8 -*-
'''
Created on 30 April 2014

@author: Kimon Tsitsikas

Copyright © 2014-2016 Kimon Tsitsikas, Delmic

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
from future.utils import with_metaclass
from past.builtins import long
from abc import abstractmethod, ABCMeta
import base64
import collections
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
from functools import reduce
import functools
import logging
import math
import numpy
from odemis import model, util
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError, oneway
import queue
import re
import suds
from suds.client import Client
import sys
import threading
import time
import weakref


# The Phenom API relies on the SOAP protocol. One good thing is that the standard
# Phenom GUI uses it. So anything the GUI can do can be performed via the
# API. The documentation is not very complete, and is written for the C++
# wrapper only. Reading the WSDL file can hint you on a lot of additional
# features. It is relatively straightforward to use in general however several
# peculiarities have to be taken into account:
#     * Viewing mode refers to the repeated acquisitions performed by Phenom
#       GUI. When an image is acquired via Odemis the acquisition is independent
#       of those performed by Phenom GUI. Thus when the SEM stream is active both
#       Phenom GUI and Odemis sequentially do separate scannings. To increase the
#       acquisition rate of Odemis we set the viewing mode settings to
#       minimum resolution and number of frames, so the Phenom GUI acquisitions
#       last the shortest time possible. Setting the scan parameters to some
#       (legal) values which are not handled by the Phenom GUI can crash the
#       GUI, and this often leads to a looping reboot of the Phenom, so beware.
#     * To make an acquisition via Odemis we call the SEMAcquireImageCopy
#       function of Phenom API. The scan parameters provided are explained below:
#       - resolution .width and .height (0 < int): dimensions of image to scan
#         in pixels.
#       - nrOfFrames (0 < int <= 255): Number of frames to average (to
#         improve the signal to noise ratio). Phenom uses a fixed dwell time,
#         thus from the "dwell time" VA actually changes the number of frames
#         required to be averaged.
#       - HDR (boolean): True to acquire 16 bits image (with a huge time
#         overhead), False for 8 bits.
#       - scale (0<=float<=1): Scale of the acquisition ROI within the field
#         of view. The minimum is 0, which can be used to force a spot (see
#         below about the "Spot Mode").
#       - center .x and .y (-0.5<=float<=0.5): Center of the acquisition ROI
#         within the field of view (0 is at the center of the field of view).
#     * "Spot Mode": The Phenom accepts scanning mode either "Imaging" or
#       "Spot". "Spot mode" is not what it sounds like. The main point
#       of Spot mode is to pause the Phenom GUI with a message letting the
#       user know that spot mode is active. It also disable the settling time
#       of the e-beam so that the e-beam position is only within the given ROI.
#       However spot mode does not reduce further the scanning area, and an
#       image can still be acquired (although the border will be wiggly as
#       there is no settle time). To actually get a spot, you must set the
#       scan parameter scale to 0. However, the positioning is not good in the
#       Phenom, and the center might be quite away from the center at scale = 1.
#     * Not all values are readable all the time. Some values, like the SEM
#       settings can only be read (and written) only when the sample holder is
#       in SEM mode.
#     * SUDS doesn't support simultaneous calls to the server (from different
#       threads). Doing so will cause from time to time the answers to be
#       mixed up. Thus, one connection needs to be opened per thread, or locks
#       should be used. That is why we use a dedicated client for the SEM
#       acquisition thread (and probably some locks would be needed to be
#       entirely safe).
#     * SUDS has been somehow abandoned, and one of the things it doesn't handle
#       well is errors. Passing the wrong login/password will cause some weird
#       error instead of a clear 403 message. A fork of SUDS by "jurko" fixes a
#       lot of such problems and might be worth to use if the standard SUDS
#       version gets really too much annoying.
# Fixed dwell time of Phenom SEM
DWELL_TIME = 1.92e-07  # s
# Fixed max number of frames per acquisition
MAX_FRAMES = 255
# For a 2048x2048 image with the maximum dt we need about 205 seconds plus some
# additional overhead for the transfer. In any case, 300 second should be enough
SOCKET_TIMEOUT = 300  # s, timeout for suds client

# TODO: depends on the Phenom -> Update range on first time it's possible to read them
# SEM ranges in order to allow scanner initialization even if Phenom is in
# unloaded state
HFW_RANGE = (2.5e-06, 0.0031)
TENSION_RANGE = (4797.56, 10000.0)
# REFERENCE_TENSION = 10e03 #Volt
# BEAM_SHIFT_AT_REFERENCE = 19e-06  # Maximum beam shit at the reference tension #m
SPOT_RANGE = (2.1, 3.3)
NAVCAM_PIXELSIZE = (1.3267543859649122e-05, 1.3267543859649122e-05)

DELPHI_WORKING_DISTANCE = 7e-3  # m, standard working distance (just to compute the depth of field)
PHENOM_EBEAM_APERTURE = 200e-6  # m, aperture size of the lens on the phenom


# Methods used for sw version comparison
def tryint(x):
    try:
        return int(x)
    except ValueError:
        return x


def splittedname(s):
    return tuple(tryint(x) for x in re.split('([0-9]+)', s))


class SEM(model.HwComponent):
    '''
    This represents the bare Phenom SEM.
    '''
    def __init__(self, name, role, children, host, username, password, phenom_gui=False, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner" and "detector"
            They will be provided back in the .children VA
        phenom_gui (bool): DEPRECATED.
        Raise an exception if the device cannot be opened
        '''

        if logging.getLogger().getEffectiveLevel() <= logging.DEBUG:
            # Avoid unnecessary logging from suds
            logging.getLogger("suds").setLevel(logging.INFO)

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        # you can change the 'localhost' string and provide another SEM addres
        self._host = host
        self._username = username
        self._password = password
        try:
            client = Client(host + "?om", location=host, username=username, password=password, timeout=SOCKET_TIMEOUT)
        except Exception:
            raise HwError("Failed to connect to Phenom host '%s'. "
                          "Check that the url is correct and Phenom connected to "
                          "the network." % (host,))
        # Decide if we still need to blank/unblank the beam by tweaking the
        # source tilt or we can just access the blanking Phenom API methods
        self._phenom_methods = [method for method in client.wsdl.services[0].ports[0].methods]
        logging.debug("Methods available in Phenom host: %s", self._phenom_methods)
        if "SEMBlankBeam" not in self._phenom_methods:
            raise HwError("This Phenom version doesn't support beam blanking! Version 4.4 or later is required.")
        self._device = client.service

        # check Phenom's state and raise HwError if it reports error mode
        instrument_mode = self._device.GetInstrumentMode()
        if instrument_mode == 'INSTRUMENT-MODE-ERROR':
            raise HwError("Phenom host is in error mode. Check Phenom "
                          "state, a reboot may be needed.")

        # Access to service objects
        self._objects = client.factory
        try:
            info = self._device.VersionInfo().versionInfo
        except AttributeError:
            raise KeyError("Failed to connect to Phenom. The username or password is incorrect.")

        try:
            start = info.index("'Product Name'>") + len("'Product Name'>")
            end = info.index("</Property", start)
            hwname = info[start:end]
            self._metadata[model.MD_HW_NAME] = hwname
            # TODO: how to retrieve the edition information?
            hwver = "G4"
            self._hwVersion = "%s %s" % (hwname, hwver)
            self._metadata[model.MD_HW_VERSION] = self._hwVersion

            start = info.index("'Version'>") + len("'Version'>")
            end = info.index("</Property", start)
            self._swVersion = info[start:end]
            self._metadata[model.MD_SW_VERSION] = self._swVersion

            logging.info("Connected to %s v%s", self._hwVersion, self._swVersion)
        except ValueError:
            logging.warning("Phenom version could not be retrieved")

        # Lock in order to synchronize all the child component functions
        # that acquire data from the SEM while we continuously acquire images
        self._acq_progress_lock = threading.Lock()

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._scanner)

        # create the detector child
        try:
            kwargs = children["detector"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'detector' child")
        self._detector = Detector(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._detector)

        # create the stage child
        try:
            kwargs = children["stage"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'stage' child")
        self._stage = Stage(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._stage)

        # create the focus child
        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'focus' child")
        self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._focus)

        # create the navcam child
        try:
            kwargs = children["navcam"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'navcam' child")
        self._navcam = NavCam(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._navcam)

        # create the NavCam focus child
        try:
            kwargs = children["navcam-focus"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'navcam-focus' child")
        self._navcam_focus = NavCamFocus(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._navcam_focus)

        # create the pressure child
        try:
            kwargs = children["pressure"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'pressure' dependency")
        self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._pressure)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        # Don't need to close the connection, it's already closed by the time
        # suds returns the data
        self._detector.terminate()
        self._scanner.terminate()
        self._stage.terminate()
        self._focus.terminate()
        self._navcam.terminate()
        self._navcam_focus.terminate()
        self._pressure.terminate()

        super(SEM, self).terminate()


class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant
    Attributes and setters for magnification, pixel size, resolution,
    scale, rotation and dwell time. Whenever one of these attributes is changed,
    its setter also updates another value if needed e.g. when scale is changed,
    resolution is updated, when resolution is changed etc. Similarly it
    subscribes to the VAs of scale and magnification in order to update the
    pixel size.
    """
    def __init__(self, name, role, parent, **kwargs):
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)
        self._hwVersion = parent._hwVersion
        self._swVersion = parent._swVersion

        self._shape = (2048, 2048)

        # TODO: document where this funky number comes from
        self._hfw_nomag = 0.268128  # m

        # Just the initialization of the FoV. The actual value will be acquired
        # once we start the stream
        fov = numpy.mean(HFW_RANGE)
        mag = self._hfw_nomag / fov

        self.magnification = model.VigilantAttribute(mag, unit="", readonly=True)
        fov_range = HFW_RANGE
        self.horizontalFoV = model.FloatContinuous(fov, range=fov_range, unit="m",
                                                   setter=self._setHorizontalFoV)
        self.horizontalFoV.subscribe(self._onHorizontalFoV)

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        self.pixelSize = model.VigilantAttribute((0, 0), unit="m", readonly=True)

        # To provide some rough idea of the step size when changing focus
        # Depends on the pixelSize, so will be updated whenever the HFW changes
        self.depthOfField = model.FloatContinuous(1e-6, range=(0, 1e9),
                                                  unit="m", readonly=True)

        # (.resolution), .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in m => physically moves the e-beam. The move is
        # clipped within the actual limits by the setter function.
        try:
            # Just to check that the SEMImageShift is allowed
            rng = self.parent._device.GetSEMImageShiftRange()
            shift_rng = ((-1, -1),
                         (1, 1))
            self.shift = model.TupleContinuous((0, 0), shift_rng,
                                                  cls=(int, long, float), unit="m",
                                                  setter=self._setShift)
            self.shift.subscribe(self._onShift, init=True)
        except suds.WebFault as ex:
            logging.warning("Disabling shift as ImageShift is not supported (%s)", ex)

        # (-0.5<=float<=0.5, -0.5<=float<=0.5) translation in ratio of the SEM
        # image shape
        self._trans = (0, 0)
        # (float, float) in m => moves center of acquisition by this amount
        # independent of scale and rotation.
        tran_rng = ((-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2))
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                                 cls=(int, long, float), unit="px",
                                                 setter=self._setTranslation)

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        resolution = (self._shape[0] // 8, self._shape[1] // 8)
        self.resolution = model.ResolutionVA(resolution, ((1, 1), self._shape),
                                             setter=self._setResolution)
        self._resolution = resolution

        # (float, float) as a ratio => how big is a pixel, compared to pixelSize
        # it basically works the same as binning, but can be float
        # With scale < 1, it's not possible to scan the whole area
        # (Default to scan the whole area)
        self._scale = (self._shape[0] / resolution[0], self._shape[1] / resolution[1])
        self.scale = model.TupleContinuous(self._scale, ((0, 0), self._shape),
                                           cls=(int, long, float),
                                           unit="", setter=self._setScale)
        self.scale.subscribe(self._onScale, init=True)  # to update metadata

        self._updatePixelSize() # needs .scale
        self._updateDepthOfField()  # needs .pixelSize

        # (float) in rad => rotation of the image compared to the original axes
        # Just the initialization of rotation. The actual value will be acquired
        # once we start the stream
        rotation = 0
        rot_range = (-2 * math.pi, 2 * math.pi) # TODO: only limit to 0->2 Pi?
        self.rotation = model.FloatContinuous(rotation, rot_range, unit="rad")
        self.rotation.subscribe(self._onRotation)

        # Compute dwellTime range based on max number of frames and the fixed
        # phenom dwellTime
        dt_range = (DWELL_TIME, DWELL_TIME * MAX_FRAMES)
        dt = DWELL_TIME
        # Corresponding nr of frames for initial DWELL_TIME
        self._nr_frames = 1
        self.dwellTime = model.FloatContinuous(dt, dt_range, unit="s",
                                               setter=self._setDwellTime)

        # Range is according to min and max voltages accepted by Phenom API
        volt_range = TENSION_RANGE
        # Just the initialization of voltage. The actual value will be acquired
        # once the sample holder is in SEM position
        volt = 5300
        self.accelVoltage = model.FloatContinuous(volt, volt_range, unit="V")
        self.accelVoltage.subscribe(self._onVoltage)

        # Directly set spot size instead of probe current due to Phenom API
        try:
            spotSize = parent._device.SEMGetSpotSize()
            res = parent._device.SEMGetSpotSizeRange()
            spot_rng = res.min, res.max
        except suds.WebFault:
            logging.info("Failed to read init spot size, will read it later")
            spot_rng = SPOT_RANGE
            spotSize = numpy.mean(SPOT_RANGE)
        self.spotSize = model.FloatContinuous(spotSize, spot_rng,
                                              setter=self._setSpotSize)

        # None, indicates "whenever the detector is acquiring"
        self.blanker = model.VAEnumerated(None, choices={None, True, False},
                                          setter=self._setBlanker)
        try:
            self._blank_beam(True)  # It's not acquiring at init
        except suds.WebFault as ex:
            # It's probably not even in SEM mode, so don't make a fuss about it
            logging.debug("Failed to update the blanker status now: %s", ex)

        # Mostly for testing/manual changes
        self.power = model.BooleanVA(True, setter=self._setPower)

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def getMetadata(self):
        return self.parent.getMetadata()

    def _updateHorizontalFoV(self):
        """
        Reads again the hardware setting and update the VA
        """
        fov = self.parent._device.GetSEMHFW()
        # take care of small deviations
        fov = self.horizontalFoV.clip(fov)

        # we don't set it explicitly, to avoid calling .SetSEMHFW()
        if fov != self.horizontalFoV._value:
            self.horizontalFoV._value = fov
            self.horizontalFoV.notify(fov)

    def _onHorizontalFoV(self, fov):
        # Update current pixelSize and magnification
        self._updatePixelSize()
        self._updateMagnification()
        self._updateDepthOfField()

    def _setHorizontalFoV(self, value):
        # Make sure you are in the current range
        try:
            rng = self.parent._device.GetSEMHFWRange()
            # Fow now only apply this value when beam is unblanked,
            # as the first versions of the Phenom firmware with blanker
            # support returned bogus HFW when the blanker was active.
            # TODO: once all systems are using newer versions, always read back
            if rng.max - rng.min < 1e-9:
                logging.info("HFW range currently only within %g->%g, will set HFW later",
                             rng.min, rng.max)
                return value
            new_fov = numpy.clip(value, rng.min, rng.max)

            logging.debug("Setting new hfw to: %g (was asked %g)", new_fov, value)
            self.parent._device.SetSEMHFW(new_fov)
            read_fov = self.parent._device.GetSEMHFW()
            if HFW_RANGE[0] <= new_fov <= HFW_RANGE[1]:
                return read_fov
            else:
                logging.warning("SEM reports HFW of %g", read_fov)
                return new_fov
        except suds.WebFault:
            logging.info("Cannot set HFW when the sample is not in SEM.")

        return self.horizontalFoV.value

    def _setBlanker(self, value):
        # Blanking will block until the acquisition is done, which can
        # take a long time, so try to only do it when needed.
        if value == self.blanker.value:
            return value

        if value is None:  # auto
            # Active if the detector is acquiring otherwise not
            blanked = self.parent._detector._acquisition_must_stop.is_set()
        else:
            blanked = value
        try:
            self._blank_beam(blanked)
        except suds.WebFault as ex:
            if value is None:
                logging.debug("Failed to update the blanker status now: %s", ex)
            else:
                # If the user is changing it explicitly, pass on the error
                # (typically because the Phenom is not in SEM imaging mode)
                logging.warning("Failed to update the blanker status: %s", ex)
                raise

        return value

    def _blank_beam(self, blank):
        """
        (Un)blank the beam.
          Note that the Phenom only allows to change the beam status if it's in
          SEM imaging mode.
        blank (boolean): If True, will blank the beam, otherwise will unblank it
        raise WebFault: if anything went wrong on the Phenom side
        """
        with self.parent._acq_progress_lock:
            logging.debug("Setting the blanker to %s", blank)
            if blank:
                self.parent._device.SEMBlankBeam()
            else:
                self.parent._device.SEMUnblankBeam()
                # We can now update hfw (range may have changed in the meantime)
                rng = self.parent._device.GetSEMHFWRange()
                current_fov = numpy.clip(self.parent._scanner.horizontalFoV.value, rng.min, rng.max)
                self.parent._device.SetSEMHFW(current_fov)
                # horizontalFoV setter would fail to call .SetSEMHFW()

    def _setPower(self, value):
        if value == self.power.value:
            return value

        if value:
            self.parent._device.SEMUnblankSource()
        else:
            self.parent._device.SEMBlankSource()

        return value

    def _updateMagnification(self):

        # it's read-only, so we change it only via _value
        mag = self._hfw_nomag / self.horizontalFoV.value
        self.magnification._set_value(mag, force_write=True)

    def _setDwellTime(self, dt):
        # Calculate number of frames
        self._nr_frames = int(math.ceil(dt / DWELL_TIME))
        new_dt = DWELL_TIME * self._nr_frames
        return new_dt

    def _onRotation(self, rot):
        with self.parent._acq_progress_lock:
            self.parent._device.SetSEMRotation(-rot)

    def _onVoltage(self, volt):
        self.parent._device.SEMSetHighTension(-volt)

    def _setSpotSize(self, value):
        # Set the corresponding spot size to Phenom SEM
        try:
            self.parent._device.SEMSetSpotSize(value)
        except suds.WebFault:
            logging.debug("Cannot set spot size (is the sample in SEM position?)", exc_info=True)
            return self.spotSize.value

        return value

        # TODO: read the exact value accepted by the SEM
#         try:
#             return self.parent._device.SEMGetSpotSize()
#         except suds.WebFault:
#             logging.warning("Failed to read back spot size after setting it")
#             return value

    def _onScale(self, s):
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the scale and FoV
        """
        fov = self.horizontalFoV.value

        pxs = (fov / self._shape[0],
               fov / self._shape[1])

        # it's read-only, so we change it only via _value
        self.pixelSize._set_value(pxs, force_write=True)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _updateDepthOfField(self):
        """
        Update the depth of field, based on the pixel size
        """
        # from http://www.emal.engin.umich.edu/courses/semlectures/focus.html
        # DoF = 2 e / (A / 2 Wd)
        # e is the scanner pixel size
        # A is the aperture
        # Wd is the working distance
        pxs = self.pixelSize.value[0]  # hopefully it's square
        dof = 2 * pxs / (PHENOM_EBEAM_APERTURE / (2 * DELPHI_WORKING_DISTANCE))
        self.depthOfField._set_value(dof, force_write=True)

    def _setScale(self, value):
        """
        value (0 < float, 0 < float): increase of size between pixels compared to
         the original pixel size. It will adapt the resolution to
         have the same ROI (just different amount of pixels scanned)
        return (float, float): the actual value used
        """
        # Only scales with the same values on X and Y are accepted. => just use X
        # For values between 0 and 1, max res is 2048, and pixels get closer
        # from each other => directly the same meaning as the .scale of the
        # Phenom scan params
        prev_scale = self._scale
        self._scale = value[0], value[0]

        # adapt resolution so that the ROI stays the same
        change = (prev_scale[0] / self._scale[0],
                  prev_scale[1] / self._scale[1])
        old_resolution = self.resolution.value
        new_res = (int(round(old_resolution[0] * change[0])),
                   int(round(old_resolution[1] * change[1])))
        new_res = (max(1, min(new_res[0], self._shape[0])),
                   max(1, min(new_res[1], self._shape[0])))

        self.resolution.value = new_res  # will call _setResolution()

        return value

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the
         resolution is not possible, it will pick the most fitting one.
        returns the actual value used
        """
        if self._scale[0] < 1:
            max_size = self._shape
        else:
            max_size = (int(self._shape[0] / self._scale[0]),
                        int(self._shape[1] / self._scale[1]))

        # TODO: ensure for resolution > 128, it is an even number

        # at least one pixel, and at most the whole area
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size

        return size

    def _setTranslation(self, value):
        """
        value (float, float): shift from the center in pixels. It will always ensure that
          the whole ROI fits the screen.
        returns actual shift accepted
        """
        # convert to Phenom coordinates (-0.5 -> 0.5)
        self._trans = (value[0] / self._shape[0], value[1] / self._shape[1])
        return value

    def transToPhy(self, trans):
        """
        Converts a position in ratio of FoV to physical unit (m), given the
          current magnification.
          Note: the convention is that in internal coordinates Y goes down, while
          in physical coordinates, Y goes up.
        trans (-0.5<float<0.5, -0.5<float<0.5): shift from the center of pixels
        returns (tuple of 2 floats): physical position in meters
        """
        pxs = self.pixelSize.value # m/px
        phy_pos = (trans[0] * self._shape[0] * pxs[0],
                   -trans[1] * self._shape[1] * pxs[1]) # - to invert Y
        return phy_pos

    def _onShift(self, shift):
        beamShift = self.parent._objects.create('ns0:position')
        with self.parent._acq_progress_lock:
            new_shift = (shift[0], shift[1])
            beamShift.x, beamShift.y = new_shift[0], new_shift[1]
            logging.debug("EBeam shifted by %s m,m", new_shift)
            self.parent._device.SetSEMImageShift(beamShift, True)

    def _setShift(self, value):
        """
        value (float, float): shift from the center (in m).
          It will always ensure that the shift is within the hardware limits.
        returns (float, float): actual shift accepted
        """
        # Clip beam shift within ~50 µm due to physical limitation
        shift_d = math.hypot(*value)
        # The exact limit depends on a lot of parameters
        # limit = (REFERENCE_TENSION / self.accelVoltage.value) * BEAM_SHIFT_AT_REFERENCE
        # Note: the range is 0->0 if the Phenom is in stand-by
        rng = self.parent._device.GetSEMImageShiftRange()
        limit = rng.max
        # The ratio between the shift distance and the limit
        ratio = 1
        if shift_d > limit and shift_d > 0:
            logging.debug("Shift range is %g->%g, will clip %s", rng.min, rng.max, value)
            ratio = limit / shift_d
        # Clip within limit
        clipped_shift = (value[0] * ratio, value[1] * ratio)
        return clipped_shift

    def terminate(self):
        # Unblank the beam, in case the Phenom should be used as-is
        try:
            self.parent._scanner._blank_beam(False)
        except suds.WebFault as ex:
            logging.info("Beam might still be blanked (%s)", ex)


class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image
    is captured.
    """
    def __init__(self, name, role, parent, **kwargs):
        """
        Note: parent should have a dependency "scanner" already initialised
        """
        # It will set up ._shape and .parent
        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._hwVersion = parent._hwVersion
        self._swVersion = parent._swVersion

        # TODO: if the SED is available, it should be seen as a separate
        # detector, to match the Odemis API.
        self._has_sed = "SEMHasSED" in parent._phenom_methods and parent._device.SEMHasSED()

        # will take care of executing autocontrast asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # 16 or 8 bits image
        self.bpp = model.IntEnumerated(8, {8, 16}, unit="")

        # HW contrast and brightness
        self.contrast = model.FloatContinuous(0.5, [0, 1], unit="",
                                              setter=self._setContrast)
        self.brightness = model.FloatContinuous(0.5, [0, 1], unit="",
                                                setter=self._setBrightness)

        self.data = SEMDataFlow(self, parent)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()
        # For the auto-blanker to know we are not acquiring at initialisation
        self._acquisition_must_stop.set()

        # The shape is just one point, the depth
        self._shape = (2 ** 16,)  # only one point

        # Start dedicated connection for acquisition stream
        acq_client = Client(self.parent._host + "?om", location=self.parent._host,
                        username=self.parent._username, password=self.parent._password,
                        timeout=SOCKET_TIMEOUT)
        self._acq_device = acq_client.service

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()

        self.updateMetadata({model.MD_DET_TYPE: model.MD_DT_NORMAL})

    @isasync
    def applyAutoContrast(self):
        # Create ProgressiveFuture and update its state to RUNNING
        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + 2)  # rough time estimation
        f._move_lock = threading.Lock()

        return self._executor.submitf(f, self._applyAutoContrast, f)

    def _applyAutoContrast(self, future):
        """
        Trigger Phenom's AutoContrast
        """
        with self.parent._acq_progress_lock:
            self.parent._device.SEMACB()
        # Update with the new values after automatic procedure is completed
        self._updateContrast()
        self._updateBrightness()

    def _setContrast(self, value):
        with self.parent._acq_progress_lock:
            # Actual range in Phenom is (0,4]
            contr = numpy.clip(4 * value, 0.00001, 4)
            try:
                self.parent._device.SetSEMContrast(contr)
            except suds.WebFault:
                logging.debug("Setting SEM contrast may be unsuccessful")
            return contr / 4

    def _setBrightness(self, value):
        with self.parent._acq_progress_lock:
            try:
                self.parent._device.SetSEMBrightness(value)
            except suds.WebFault:
                logging.debug("Setting SEM brightness may be unsuccessful")
            return value

    def _updateContrast(self):
        """
        Reads again the hardware setting and update the VA
        """
        contr = self.parent._device.GetSEMContrast() / 4
        contr = self.contrast.clip(contr)

        # we don't set it explicitly, to avoid calling .setContrast()
        if contr != self.contrast.value:
            self.contrast._value = contr
            self.contrast.notify(contr)

    def _updateBrightness(self):
        """
        Reads again the hardware setting and update the VA
        """
        bright = self.parent._device.GetSEMBrightness()
        bright = self.brightness.clip(bright)

        # we don't set it explicitly, to avoid calling .setBrightness()
        if bright != self.brightness.value:
            self.brightness._value = bright
            self.brightness.notify(bright)

    def update_parameters(self):
        # Update stage and focus position
        self.parent._stage._updatePosition()
        self.parent._focus._updatePosition()
        self.parent._navcam_focus._updatePosition()

        # Update all the Scanner VAs upon stream start
        # Get current field of view and compute magnification
        fov = self._acq_device.GetSEMHFW()
        # take care of small deviations
        fov = numpy.clip(fov, HFW_RANGE[0], HFW_RANGE[1])
        if fov != self.parent._scanner.horizontalFoV.value:
            self.parent._scanner.horizontalFoV._value = fov
            self.parent._scanner.horizontalFoV.notify(fov)

        rotation = self._acq_device.GetSEMRotation()
        if -rotation != self.parent._scanner.rotation.value:
            self.parent._scanner.rotation._value = -rotation
            self.parent._scanner.rotation.notify(-rotation)

        volt = self._acq_device.SEMGetHighTension()
        if -volt != self.parent._scanner.accelVoltage.value:
            self.parent._scanner.accelVoltage._value = -volt
            self.parent._scanner.accelVoltage.notify(-volt)

        # Get current spot size
        spotSize = self._acq_device.SEMGetSpotSize()
        if spotSize != self.parent._scanner.spotSize.value:
            self.parent._scanner.spotSize._value = spotSize
            self.parent._scanner.spotSize.notify(spotSize)

        # Update all Detector VAs
        self._updateContrast()
        self._updateBrightness()

    def start_acquire(self, callback):
        logging.debug("New SEM acquisition requested")
        with self._acquisition_lock:
            self._wait_acquisition_stopped()

            # Check if Phenom is in the proper mode
            area = self._acq_device.GetProgressAreaSelection().target
            if area != "LOADING-WORK-AREA-SEM":
                raise IOError("Cannot initiate stream, Phenom is not in SEM mode.")

            logging.debug("Starting acquisition thread")
            if self.parent._scanner.blanker.value is None:
                try:
                    self.parent._scanner._blank_beam(False)
                except suds.WebFault:
                    logging.warning("Beam might still be blanked!", exc_info=True)
            elif self.parent._scanner.blanker.value:
                logging.warning("Starting acquisition while the beam is blanked")
            self._acquisition_thread = threading.Thread(target=self._acquire_thread,
                    name="PhenomSEM acquire flow thread",
                    args=(callback,))
            self._acquisition_thread.start()

    def stop_acquire(self):
        with self._acquisition_lock, self._acquisition_init_lock:
            self._acquisition_must_stop.set()
            try:
                # TODO: it seems that in some cases, if the acquisition has just
                # started, this will fail (and then the whole acquisition will
                # go on until its end)
                self._acq_device.SEMAbortImageAcquisition()
            except suds.WebFault as ex:
                logging.debug("No acquisition in progress to be aborted. (%s)", ex)

            # Blank the beam if needed
            if self.parent._scanner.blanker.value is None:
                try:
                    self.parent._scanner._blank_beam(True)
                except suds.WebFault:
                    logging.warning("Beam might still be unblanked!", exc_info=True)

    def _wait_acquisition_stopped(self):
        """
        Waits until the acquisition thread is fully finished _iff_ it was requested
        to stop.
        """
        # "if" is to not wait if it's already finished
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
            if self._acquisition_thread is not None:
                self._acquisition_thread.join(10)  # 10s timeout for safety
                if self._acquisition_thread.isAlive():
                    logging.exception("Failed to stop the acquisition thread")
                    # Now let's hope everything is back to normal...
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    def _get_viewing_mode(self, res, nframes, center, scale):
        """
        Construct the scan parameters for the viewing mode, and check whether
         the SEM already uses them or not.
        res (int, int): resolution
        nframes (int): number of frames to accumulate
        center (float, float): ratio of the FoV (-0.5 -> 0.5)
        scale (0<=float<=1)
        returns (scan_params, bool): scan parameters to be sent, and whether the
          SEM needs to be reconfigure by getting these new params
        """
        scan_params = self._acq_device.GetSEMViewingMode().parameters
        prev_params = ((scan_params.resolution.width, scan_params.resolution.height),
                       scan_params.nrOfFrames,
                       scan_params.scale)
        prev_center = (scan_params.center.x, scan_params.center.y)
        # Center cannot be compared with "==", due to floating point error.
        if (prev_params == (res, nframes, scale) and
            all(util.almost_equal(c, p) for c, p in zip(prev_center, center))):
            return scan_params, False
        else:
            scan_params.resolution.width = res[0]
            scan_params.resolution.height = res[1]
            scan_params.nrOfFrames = nframes
            scan_params.center.x = center[0]
            scan_params.center.y = center[1]
            scan_params.scale = scale
            return scan_params, True

    def _get_res_hfw_shift(self, res):
        """
        return (float, float): shift in FoV ratio (-0.5 -> 0.5)
        """
        md_bsd = self.getMetadata()
        # SEM image shift correction parameters
        AX, AY = md_bsd.get(model.MD_RESOLUTION_SLOPE, (0, 0))
        BX, BY = md_bsd.get(model.MD_RESOLUTION_INTERCEPT, (0, 0))
        CX, CY = md_bsd.get(model.MD_HFW_SLOPE, (0, 0))  # % of the FoV

        # No need to know the FoV for C, as shift is relative to the FoV
        shift = (- ((1 / (2 * math.pi)) * math.atan(-AX / (res[0] + BX)) + CX / 100),
                 - ((1 / (2 * math.pi)) * math.atan(-AY / (res[1] + BY)) + CY / 100))
        return shift

    def _acquire_image(self, is_first):
        """
        Acquires the SEM image based on the resolution and
        current drift.
        is_first (bool): True if previously no image was being acquired (and so
          prevent fast live view update)
        """
        with self.parent._acq_progress_lock:
            res = self.parent._scanner.resolution.value
            trans = self.parent._scanner._trans
            scale = self.parent._scanner._scale
            # Set dataType based on current bpp value
            bpp = self.bpp.value
            dataType = {8: numpy.uint8, 16: numpy.uint16}[bpp]

            # update changed metadata
            metadata = self.parent._metadata.copy()
            metadata[model.MD_ACQ_DATE] = time.time()
            metadata[model.MD_BPP] = bpp
            # Update position (if there is one known) by the translation.
            # Note that the beam shift is not taken into account, as that control
            # is used for calibration, so to ensure that the center of the image
            # matches the given position metadata.
            center = metadata.get(model.MD_POS, (0, 0))
            trans_phy = self.parent._scanner.transToPhy(trans)
            metadata[model.MD_POS] = (center[0] + trans_phy[0],
                                      center[1] + trans_phy[1])

            # Spot is achieved by setting a scale = 0
            # The Phenom needs _some_ resolution. Smaller is better because
            # the acquisition finishes sooner, so it can be changed sooner.
            # However at resolutions < 256, the Phenom tends to compute the
            # position of the image at really weird/unpredictable places.
            # Moreover, to compute the spot shift, we use SEM image correlation
            # at different FoV scales, so the image must be big enough to
            # correlate it relatively precisely (although _maybe_ the shift is
            # stable in px, so any resolution would be fine to use).
            # Note: earlier versions of the Phenom GUI would crash with res < 456
            SPOT_RES = (256, 256)  # changing this will change spot shift required
            spot_shift = metadata.get(model.MD_SPOT_SHIFT, (0, 0))
            if res == (1, 1):
                # Do simple spot mode
                logging.debug("Setting the SEM to spot mode")
                # Set scale so the FoV is reduced to something really small
                # even if the current HFW is the maximum
                res_hfw_shift = self._get_res_hfw_shift(SPOT_RES)
                shift = (trans[0] + spot_shift[0] + res_hfw_shift[0],
                         trans[1] + spot_shift[1] + res_hfw_shift[1])
                scan_params, need_set = self._get_viewing_mode(SPOT_RES, 1, shift, 0)
                scan_params.HDR = (bpp == 16)
                scan_params.detector = 'SEM-DETECTOR-MODE-ALL'
                # TODO: allow the user to select the dwell time?

                if self._acquisition_must_stop.is_set():
                    raise CancelledError()
                if need_set:
                    self._acq_device.SetSEMViewingMode(scan_params, 'SEM-SCAN-MODE-IMAGING')

                # Get the actual data (as average of the whole "scan")
                img_str = self._acq_device.SEMGetLiveImageCopy(0)

                if self._acquisition_must_stop.is_set():
                    raise CancelledError()

                dtype = {8: numpy.uint8, 16: numpy.uint16}[img_str.image.descriptor.bits]
                sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]),
                                           dtype=dtype)
                if sem_img.size != (256 * 256):
                    logging.warning("Got data of length %d instead of 256*256", sem_img.size)
                sem_img = numpy.array(numpy.mean(sem_img).astype(sem_img.dtype))
                sem_img.shape = (1, 1)

                metadata[model.MD_EBEAM_VOLTAGE] = abs(img_str.aAcqState.highVoltage)
                metadata[model.MD_EBEAM_CURRENT] = img_str.aAcqState.emissionCurrent
                metadata[model.MD_DWELL_TIME] = (img_str.aAcqState.dwellTime * img_str.aAcqState.integrations * sem_img.size)
                metadata[model.MD_HW_NAME] = self._hwVersion + " (s/n %s)" % img_str.aAcqState.instrumentID

                logging.debug("Returning spot SEM image with data %d", sem_img[0, 0])
                return model.DataArray(sem_img, metadata)
            elif res[0] * res[1] <= (16 * 16):
                # Scan spot by spot (as fast as possible)
                logging.debug("Grid scanning of %s...", res)

                # FoV (0->1) is: (full_FoV * res_ratio) - 1 px
                shape = self.parent._scanner.shape
                fov = ((1 - (1 / res[0])) * (scale[0] * res[0] / shape[0]),
                       (1 - (1 / res[1])) * (scale[1] * res[1] / shape[1]))
                bound = (fov[0] / 2, fov[1] / 2)
                coordinates = (numpy.linspace(-bound[0], bound[0], res[0]),
                               numpy.linspace(-bound[1], bound[1], res[1]))

                res_hfw_shift = self._get_res_hfw_shift(SPOT_RES)
                shift = (trans[0] + spot_shift[0] + res_hfw_shift[0],
                         trans[1] + spot_shift[1] + res_hfw_shift[1])
                scan_params, _ = self._get_viewing_mode(SPOT_RES, 1, shift, 0)
                # Inverse dims, as numpy goes through the dims from last to first
                # and it's customary to scan X fast, Y slow.
                for i in numpy.ndindex(res[::-1]):
                    pos = coordinates[0][i[1]], coordinates[1][i[0]]
                    logging.debug("Positioning spot at %s", pos)
                    scan_params.center.x = shift[0] + pos[0]
                    scan_params.center.y = shift[1] + pos[1]
                    if self._acquisition_must_stop.is_set():
                        raise CancelledError()
                    try:
                        self._acq_device.SetSEMViewingMode(scan_params, 'SEM-SCAN-MODE-IMAGING')
                    except suds.WebFault:
                        logging.warning("Spot scan failure.")
                    # No sleep, just go as fast as possible, which is not really
                    # fast as this takes ~0.05 s, because the whole 256x256 px
                    # need to be scanned.

                logging.debug("Returning fake SEM image of res=%s", res)
                return model.DataArray(numpy.zeros(res[::-1], dtype=dataType), metadata)
            else:
                nframes = self.parent._scanner._nr_frames
                logging.debug("Acquiring SEM image of %s with %d bpp and %d frames",
                              res, bpp, nframes)
                # With the SW 5.4, Phenom XL works fine down to (at least) 32x32 px
                if res[0] < 256 or res[1] < 256:
                    logging.warning("Scanning at resolution < 256 %s, which is unsupported.", res)

                res_hfw_shift = self._get_res_hfw_shift(res)
                shift = (trans[0] + res_hfw_shift[0],
                         trans[1] + res_hfw_shift[1])
                fovscale = res[0] * scale[0] / self.parent._scanner._shape[0]
                if fovscale > 0.999:
                    fovscale = 1  # To keep from the floating errors
                else:
                    # There is no compensation for the shift induced by scale < 1
                    logging.info("Using FoV scale %f, which should be used only for calibration", fovscale)

                scan_params, need_set = self._get_viewing_mode(res, nframes, shift, fovscale)
                scan_params.HDR = (bpp == 16)

                if (self._has_sed and
                    self._acq_device.SEMGetSEDState() in ("SED-STATE-ENABLED", "SED-STATE-ENABLING")):
                    scan_params.detector = 'SEM-DETECTOR-MODE-SED'
                else:
                    scan_params.detector = 'SEM-DETECTOR-MODE-ALL'

                # last check before we initiate the actual acquisition
                if self._acquisition_must_stop.is_set():
                    raise CancelledError()
                if bpp == 8:
                    # In 8-bit mode, we can save time by sharing the same scan
                    # for the Phenom GUI (aka "live") and the image we need.
                    if need_set or is_first:
                        # Note: changing the viewing mode can take ~2 s
                        self._acq_device.SetSEMViewingMode(scan_params, 'SEM-SCAN-MODE-IMAGING')
                        # Just to wait long enough before we get a frame with the new
                        # parameters applied. In the meantime, it can be the case that
                        # Phenom generates semi-created frames that we want to avoid.
                        logging.debug("Acquiring full image accumulation")
                        img_str = self._acq_device.SEMGetLiveImageCopy(nframes)
                    else:
                        img_str = self._acq_device.SEMGetLiveImageCopy(0)
                else:
                    # 16-bit mode: need to acquire separately from the live mode
                    if need_set or is_first:
                        # TODO: set the live mode in a very low res all the time
                        # to save time
                        self._acq_device.SetSEMViewingMode(scan_params, 'SEM-SCAN-MODE-IMAGING')
                    img_str = self._acq_device.SEMAcquireImageCopy(scan_params)

                # Use the metadata from the string to update some metadata
                # metadata[model.MD_POS] = (img_str.aAcqState.position.x, img_str.aAcqState.position.y)
                metadata[model.MD_EBEAM_VOLTAGE] = abs(img_str.aAcqState.highVoltage)
                metadata[model.MD_EBEAM_CURRENT] = img_str.aAcqState.emissionCurrent
                metadata[model.MD_ROTATION] = -img_str.aAcqState.rotation
                metadata[model.MD_DWELL_TIME] = img_str.aAcqState.dwellTime * img_str.aAcqState.integrations
                metadata[model.MD_PIXEL_SIZE] = (img_str.aAcqState.pixelWidth,
                                                 img_str.aAcqState.pixelHeight)
                metadata[model.MD_HW_NAME] = self._hwVersion + " (s/n %s)" % img_str.aAcqState.instrumentID

                dtype = {8: numpy.uint8, 16: numpy.uint16}[img_str.image.descriptor.bits]
                if dtype != dataType:
                    logging.warning("Expected image of data type %s but got %s",
                                    dataType, dtype)
                sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]),
                                           dtype=dtype)
                sem_img.shape = res[::-1]
                logging.debug("Returning SEM image of %s with %d bpp and %d frames",
                              res, bpp, nframes)
                return model.DataArray(sem_img, metadata)

    def _acquire_thread(self, callback):
        """
        Thread that performs the SEM acquisition. It calculates and updates the
        center (e-beam) position and provides the new generated output to the
        Dataflow.
        """
        try:
            is_first = True
            while not self._acquisition_must_stop.is_set():
                with self._acquisition_init_lock:
                    if self._acquisition_must_stop.is_set():
                        break
                self.data._waitSync()
                callback(self._acquire_image(is_first))
                is_first = False
        except CancelledError:
            logging.debug("Acquisition thread cancelled")
        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def getMetadata(self):
        return self.parent.getMetadata()

    def terminate(self):
        logging.info("Terminating SEM stream...")
        if self._executor:
            self._executor.shutdown()
            self._executor = None


class SEMDataFlow(model.DataFlow):
    """
    This is an extension of model.DataFlow. It receives notifications from the
    detector component once the SEM output is captured. This is the dataflow to
    which the SEM acquisition streams subscribe.
    """
    def __init__(self, detector, sem):
        """
        detector (semcomedi.Detector): the detector that the dataflow corresponds to
        sem (semcomedi.SEMComedi): the SEM
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(detector)

        self._sync_event = None  # event to be synchronised on, or None
        self._evtq = None  # a Queue to store received events (= float, time of the event)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        try:
            self.component().start_acquire(self.notify)
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def stop_generate(self):
        try:
            self.component().stop_acquire()
            # Note that after that acquisition might still go on for a short time
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the scanner will start a new acquisition/scan.
          The DataFlow can be synchronized only with one Event at a time.
          However each DataFlow can be synchronized, separately. The scan will
          only start once each active DataFlow has received an event.
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        """
        if self._sync_event == event:
            return

        if self._sync_event:
            self._sync_event.unsubscribe(self)
            if not event:
                self._evtq.put(None)  # in case it was waiting for this event

        self._sync_event = event
        if self._sync_event:
            # if the df is synchronized, the subscribers probably don't want to
            # skip some data
            self._evtq = queue.Queue()  # to be sure it's empty
            self._sync_event.subscribe(self)

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        if not self._evtq.empty():
            logging.warning("Received synchronization event but already %d queued",
                            self._evtq.qsize())

        self._evtq.put(time.time())

    def _waitSync(self):
        """
        Block until the Event on which the dataflow is synchronised has been
          received. If the DataFlow is not synchronised on any event, this
          method immediately returns
        """
        if self._sync_event:
            self._evtq.get()


class Stage(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the Phenom stage and updating the position.
    """
    def __init__(self, name, role, parent, **kwargs):
        """
        axes (set of string): names of the axes
        """
        # Position phenom object
        # TODO: only one object needed?
        self._stagePos = parent._objects.create('ns0:position')
        self._stageRel = parent._objects.create('ns0:position')
        self._navAlgorithm = 'NAVIGATION-BACKLASH-ONLY'

        axes_def = {}
        stroke = parent._device.GetStageStroke()
        axes_def["x"] = model.Axis(unit="m", range=(stroke.semX.min, stroke.semX.max))
        axes_def["y"] = model.Axis(unit="m", range=(stroke.semY.min, stroke.semY.max))

        # TODO, may be needed in case setting a referencial point is required
        # cf .reference() and .referenced

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)
        self._hwVersion = parent._hwVersion
        self._swVersion = parent._swVersion

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # Just initialization, position will be updated once we move
        self._position = {"x": 0, "y": 0}

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversion(self._position),
                                    unit="m", readonly=True)

    def _updatePosition(self):
        """
        update the position VA
        """
        mode_pos = self.parent._device.GetStageModeAndPosition()
        self._position["x"] = mode_pos.position.x
        self._position["y"] = mode_pos.position.y

        # it's read-only, so we change it via _value
        self.position._value = self._applyInversion(self._position)
        self.position.notify(self.position.value)

    def _doMoveAbs(self, pos):
        """
        move to the position
        """
        with self.parent._acq_progress_lock:
            self._stagePos.x = pos.get("x", self._position["x"])
            self._stagePos.y = pos.get("y", self._position["y"])
            logging.debug("Requesting absolute move to %g, %g", self._stagePos.x, self._stagePos.y)
            self.parent._device.MoveTo(self._stagePos, self._navAlgorithm)

            # Obtain the finally reached position after move is performed.
            # This is mainly in order to keep the correct position in case the
            # move we tried to perform was greater than the maximum possible one.
            self._updatePosition()

    def _doMoveRel(self, shift):
        """
        move by the shift
        """
        with self.parent._acq_progress_lock:
            self._stageRel.x, self._stageRel.y = shift.get("x", 0), shift.get("y", 0)
            logging.debug("Requesting relative move by %g, %g", self._stageRel.x, self._stageRel.y)
            self.parent._device.MoveBy(self._stageRel, self._navAlgorithm)

            # Obtain the finally reached position after move is performed.
            # This is mainly in order to keep the correct position in case the
            # move we tried to perform was greater than the maximum possible one.
            self._updatePosition()

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        return self._executor.submit(self._doMoveRel, shift)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        return self._executor.submit(self._doMoveAbs, pos)

    def stop(self, axes=None):
        # HACK: the cancel() will wait until the current future/move is over.
        # => Stop() in anycase, and then cancel() to remove the queued futures.
        # TODO: make the futures cancellable, so that this hack is not necessary
        self.parent._device.Stop()

        # Empty the queue for the given axes
        self._executor.cancel()

        self.parent._device.Stop()
        logging.info("Stopping axes: %s", ", ".join(self.axes))

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None


class PhenomFocus(with_metaclass(ABCMeta, model.Actuator)):
    """
    This is an extension of the model.Actuator class and represents a focus
    actuator. This is an abstract class that should be inherited.
    """

    def __init__(self, name, role, parent, axes, rng, **kwargs):
        assert len(axes) > 0
        axes_def = {}
        self.rng = rng

        # Just z axis
        a = axes[0]
        axes_def[a] = model.Axis(unit="m", range=rng)
        self.rng = rng

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)
        self._hwVersion = parent._hwVersion
        self._swVersion = parent._swVersion

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        try:
            self._updatePosition()
        except suds.WebFault:
            logging.debug("Working distance not available yet.")

        # Queue maintaining moves to be done
        self._moves_queue = collections.deque()

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

    @abstractmethod
    def GetWD(self):
        pass

    @abstractmethod
    def SetWD(self, wd):
        pass

    def _updatePosition(self):
        """
        update the position VA
        """
        # Obtain the finally reached position after move is performed.
        wd = self.GetWD()
        pos = {"z": wd}

        # it's read-only, so we change it via _value
        self.position._value = self._applyInversion(pos)
        self.position.notify(self.position.value)

    def _checkQueue(self):
        """
        accumulates the focus actuator moves
        """
        if not self._moves_queue:
            return
        else:
            with self.parent._acq_progress_lock:
                logging.debug("Requesting focus move for %s", self.name)
                wd = self.GetWD()
                while True:
                    try:
                        # FIXME: don't add the moves if the future was cancelled
                        typ, mov = self._moves_queue.popleft()
                    except IndexError:
                        break
                    if typ == "moveRel":
                        wd += mov["z"]
                    else:
                        wd = mov["z"]
                # Clip within range
                wd = numpy.clip(wd, self.rng[0], self.rng[1])
                self.SetWD(wd)
                self._updatePosition()

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)
        logging.debug("Submit relative move of %s...", shift)
        self._moves_queue.append(("moveRel", shift))
        return self._executor.submit(self._checkQueue)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)
        logging.info("Submit absolute move of %s...", pos)
        self._moves_queue.append(("moveAbs", pos))
        return self._executor.submit(self._checkQueue)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.info("Cannot stop focus axes: %s.%s", self.name, ", ".join(self.axes))

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None


class EbeamFocus(PhenomFocus):
    """
    This is an extension of the PhenomFocus class. It provides functions for
    adjusting the ebeam focus by changing the working distance i.e. the distance
    between the end of the objective and the surface of the observed specimen
    """
    def __init__(self, name, role, parent, axes, **kwargs):
        rng = parent._device.GetSEMWDRange()
        # Some firmware versions of the Phenom sometimes return incorrect range
        # (eg, when e-beam is not active).
        # TODO: update the range as soon as the SEM area is reached.
        if rng.max - rng.min < 1e-9:
            logging.warning("SEM focus range reported is %s. Will replace by 1m", rng)
            rng.min = 0
            rng.max = 1

        PhenomFocus.__init__(self, name, role, parent=parent, axes=axes,
                             rng=(rng.min, rng.max), **kwargs)

    def _updatePosition(self):
        """
        update the position VA
        """
        super(EbeamFocus, self)._updatePosition()

        # Changing WD results to change in fov
        try:
            self.parent._scanner._updateHorizontalFoV()
        except suds.WebFault:
            pass # can happen at startup if not in SEM mode

    def GetWD(self):
        return self.parent._device.GetSEMWD()

    def SetWD(self, wd):
        return self.parent._device.SetSEMWD(wd)

# The improved NavCam in Phenom G2 and onwards delivers images with a native
# resolution of 912x912 pixels. When requesting a different size, the image is
# scaled by the Phenom to the requested resolution
NAVCAM_RESOLUTION = (912, 912)
# Order of dimensions in NAVCAM, colour per-pixel
NAVCAM_DIMS = 'YXC'
# Message generated by NavCam when firmware is locked up
NAVCAM_LOCKED_MSG = "Server raised fault: 'CaptureDevice Acquire failed, error: Error - 2019 - GrabFrame() - VIDIOCSYNC returned: -1'"

class NavCam(model.DigitalCamera):
    """
    Represents the optical camera that is activated after the Phenom door is
    closed and the sample is transferred to the optical imaging position.
    """
    def __init__(self, name, role, parent, contrast=0, brightness=1, hfw=None, **kwargs):
        """
        Initialises the device.
        contrast (0<=float<=1): "Contrast" ratio where 1 means bright-field, and 0
         means dark-field
        brightness (0<=float<=1): light intensity between 0 and 1
        hfw (None or float): NavCam horizontal field with to use (m). If None,
          will use the maximum available.
        Raise an exception if the device cannot be opened.
        """
        model.DigitalCamera.__init__(self, name, role, parent=parent, **kwargs)
        self._hwVersion = parent._hwVersion
        self._swVersion = parent._swVersion

        # TODO: provide contrast and brightness via a new Light component
        if not 0 <= contrast <= 1:
            raise ValueError("contrast argument = %s, not between 0 and 1" % contrast)
        if not 0 <= brightness <= 1:
            raise ValueError("brightness argument = %s, not between 0 and 1" % brightness)
        self._contrast = contrast
        self._brightness = brightness
        # TODO: provide a Lens component that provides a .magnification VA
        # based on this hfw. And/or provide enough LENS_* metadata to compute
        # the depth of field.
        self._hfw = hfw

        resolution = NAVCAM_RESOLUTION
        # RGB
        self._shape = resolution + (3, 2 ** 8)
        self.resolution = model.ResolutionVA(resolution,
                                      (NAVCAM_RESOLUTION, NAVCAM_RESOLUTION))
                                    # , readonly=True)
        self.exposureTime = model.FloatVA(1.0, unit="s", readonly=True)
        self.pixelSize = model.VigilantAttribute(NAVCAM_PIXELSIZE, unit="m",
                                                 readonly=True)

        # setup camera
        self._camParams = self.parent._objects.create('ns0:camParams')
        self._camParams.height = resolution[0]
        self._camParams.width = resolution[1]

        self.acquisition_lock = threading.Lock()
        self.acquire_must_stop = threading.Event()
        self.acquire_thread = None

        self.data = NavCamDataFlow(self)

        logging.debug("Camera component ready to use.")

    def start_flow(self, callback):
        """
        Set up the NavCam and start acquiring images.
        callback (callable (DataArray) no return):
         function called for each image acquired
        """
        # Check if Phenom is in the proper mode
        area = self.parent._device.GetProgressAreaSelection().target
        if area != "LOADING-WORK-AREA-NAVCAM":
            raise IOError("Cannot initiate stream, Phenom is not in NAVCAM mode. "
                          "Make sure the chamber pressure is set for overview.")

        # if there is a very quick unsubscribe(), subscribe(), the previous
        # thread might still be running
        self.wait_stopped_flow()  # no-op is the thread is not running
        self.acquisition_lock.acquire()

        self.acquire_thread = threading.Thread(
                target=self._acquire_thread_continuous,
                name="NavCam acquire flow thread",
                args=(callback,))
        self.acquire_thread.start()

    def req_stop_flow(self):
        """
        Cancel the acquisition of a flow of images: there will not be any notify() after this function
        Note: the thread should be already running
        Note: the thread might still be running for a little while after!
        """
        assert not self.acquire_must_stop.is_set()
        self.acquire_must_stop.set()
        try:
            self.parent._device.NavCamAbortImageAcquisition()
        except suds.WebFault:
            logging.debug("No acquisition in progress to be aborted.")

    def _acquire_thread_continuous(self, callback):
        """
        The core of the acquisition thread. Runs until acquire_must_stop is set.
        """
        try:
            # Common call for SEM and NavCam HFW. Set max if None
            try:
                rng = self.parent._device.GetSEMHFWRange()
                if self._hfw is None:
                    self.parent._device.SetSEMHFW(rng.max)
                elif not rng.min <= self._hfw <= rng.max:
                    raise ValueError("NavCam hfw out of range %s" % ((rng.min, rng.max),))
                else:
                    self.parent._device.SetSEMHFW(self._hfw)
            except suds.WebFault as e:
                logging.warning("Failed to set HFW to %f: %s", self._hfw, e)
            try:
                self.parent._device.SetNavCamContrast(self._contrast)
            except suds.WebFault as e:
                logging.warning("Failed to set contrast to %f: %s", self._contrast, e)
            try:
                self.parent._device.SetNavCamBrightness(self._brightness)
            except suds.WebFault:
                logging.warning("Failed to set brightness to %f: %s", self._brightness, e)

            while not self.acquire_must_stop.is_set():
                with self.parent._acq_progress_lock:
                    try:
                        logging.debug("Waiting for next navcam frame")
                        img_str = self.parent._device.NavCamAcquireImageCopy(self._camParams)
                        sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]), dtype="uint8")
                        sem_img.shape = (self._camParams.height, self._camParams.width, 3)

                        # Obtain pixel size and position as metadata
                        pixelSize = (img_str.aAcqState.pixelHeight, img_str.aAcqState.pixelWidth)
                        pos = (img_str.aAcqState.position.x, img_str.aAcqState.position.y)
                        metadata = {model.MD_POS: pos,
                                    model.MD_PIXEL_SIZE: pixelSize,
                                    model.MD_DIMS: NAVCAM_DIMS,
                                    model.MD_ACQ_DATE: time.time()}
                        array = model.DataArray(sem_img, metadata)
                        callback(self._transposeDAToUser(array))
                    except suds.WebFault as e:
                        if e.message == NAVCAM_LOCKED_MSG:
                            logging.warning("NavCam firmware has locked up. Please power cycle Phenom.")
                        else:
                            logging.debug("NavCam acquisition failed.")

        except Exception:
            logging.exception("Failure during navcam acquisition")
        finally:
            self.acquisition_lock.release()
            logging.debug("NavCam acquisition thread closed")
            self.acquire_must_stop.clear()

    def wait_stopped_flow(self):
        """
        Waits until the end acquisition of a flow of images. Calling from the
         acquisition callback is not permitted (it would cause a dead-lock).
        """
        # "if" is to not wait if it's already finished
        if self.acquire_must_stop.is_set():
            self.acquire_thread.join(10)  # 10s timeout for safety
            if self.acquire_thread.isAlive():
                raise OSError("Failed to stop the acquisition thread")
            # ensure it's not set, even if the thread died prematurely
            self.acquire_must_stop.clear()

    def terminate(self):
        """
        Must be called at the end of the usage
        """
        self.req_stop_flow()


class NavCamDataFlow(model.DataFlow):
    def __init__(self, camera):
        """
        camera: NavCam instance ready to acquire images
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(camera)

    def start_generate(self):
        comp = self.component()
        if comp is None:
            return
        comp.start_flow(self.notify)

    def stop_generate(self):
        comp = self.component()
        if comp is None:
            return
        comp.req_stop_flow()


class NavCamFocus(PhenomFocus):
    """
    This is an extension of the model.Actuator class. It provides functions for
    adjusting the overview focus by changing the working distance i.e. the distance
    between the end of the camera and the surface of the observed specimen
    """
    def __init__(self, name, role, parent, axes, ranges=None, **kwargs):
        rng = parent._device.GetNavCamWDRange()
        # Each Phenom seems to have different range, so generalise by making it
        # always start at 0.
        self._offset = rng.min
        diff = rng.max - self._offset
        PhenomFocus.__init__(self, name, role, parent=parent, axes=axes,
                             rng=(0, diff), **kwargs)

    def GetWD(self):
        wd = self.parent._device.GetNavCamWD()
        wd -= self._offset
        return wd

    def SetWD(self, wd):
        wd += self._offset
        return self.parent._device.SetNavCamWD(wd)

    def _checkQueue(self):
        """
        accumulates the focus actuator moves
        """
        super(NavCamFocus, self)._checkQueue()
        # FIXME
        # Although we are already on the correct position, if we acquire an
        # image just after a move, server raises a fault thus we wait a bit.
        # TODO polling until move is done, probably while loop with try-except
        time.sleep(1)


PRESSURE_UNLOADED = 1e05  # Pa
PRESSURE_NAVCAM = 1e04  # Pa
PRESSURE_SEM = 1e-02  # Pa

class ChamberPressure(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    adjusting the chamber pressure. It actually allows the user to move the sample
    between the NavCam and SEM areas or even unload it.
    """
    def __init__(self, name, role, parent, ranges=None, **kwargs):
        axes = {"vacuum": model.Axis(unit="Pa",
                                       choices={PRESSURE_UNLOADED: "vented",
                                                PRESSURE_NAVCAM: "overview",
                                                PRESSURE_SEM: "vacuum"})}
        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)
        self._hwVersion = parent._hwVersion
        self._swVersion = parent._swVersion

        self._imagingDevice = self.parent._objects.create('ns0:imagingDevice')
        self.wakeUpTime = 0

        # Handle the cases of stand-by and hibernate mode
        mode = self.parent._device.GetInstrumentMode()
        if mode in {'INSTRUMENT-MODE-HIBERNATE', 'INSTRUMENT-MODE-STANDBY'}:
            self.parent._device.SetInstrumentMode('INSTRUMENT-MODE-OPERATIONAL')

        area = self.parent._device.GetProgressAreaSelection().target  # last official position

        if area == "LOADING-WORK-AREA-SEM":
            self._position = PRESSURE_SEM
        elif area == "LOADING-WORK-AREA-NAVCAM":
            self._position = PRESSURE_NAVCAM
        else:
            self._position = PRESSURE_UNLOADED

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    {"vacuum": self._position},
                                    unit="Pa", readonly=True)
        logging.debug("Chamber in position: %s", self._position)

        # Start dedicated connection for api calls during the change of pressure state
        # The main purpose is to avoid collisions with the calls from the Time updater
        pressure_client = Client(self.parent._host + "?om", location=self.parent._host,
                        username=self.parent._username, password=self.parent._password,
                        timeout=SOCKET_TIMEOUT)
        self._pressure_device = pressure_client.service

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # Tuple containing sample holder ID (int) and type (int), or None, None if absent
        self.sampleHolder = model.TupleVA((None, None), readonly=True)

        # VA used for the sample holder registration
        self.registeredSampleHolder = model.BooleanVA(False, readonly=True)

        # VA connected to the door status, True if door is open
        self.opened = model.BooleanVA(False, readonly=True)
        self._updateOpened()

        self._updatePosition()
        if self._position == PRESSURE_SEM:
            self.parent._detector.update_parameters()
        self._updateSampleHolder()

        # Lock taken while "vacuum" (= sample loader) is changing, to prevent
        # position update too early
        self._pressure_changing = threading.Lock()
        # Event to prevent move to start while another move initiated from the
        # Phenom GUI is in progress
        self._move_in_progress = threading.Event()
        self._move_in_progress.set() # by default
        # Event indicating position has been updated (after a move), to prevent
        # future from returning before the position is updated
        self._position_event = threading.Event()
        self._position_event.set()

        # Thread that listens to pressure state changes
        self._chamber_must_stop = threading.Event()
        self._chamber_thread = threading.Thread(target=self._chamber_move_thread,
                                                name="Phenom chamber pressure state change")
        self._chamber_thread.start()

        # Event for reconnection thread
        self._reconnection_must_stop = threading.Event()

    def _updatePosition(self):
        """
        update the position VA
        """
        logging.debug("Updating chamber position...")
        area = self.parent._device.GetProgressAreaSelection().target  # last official position
        logging.debug("Latest targeted area: %s", area)
        # TODO: use OperationalMode instead?
        try:
            self._position = {"LOADING-WORK-AREA-SEM": PRESSURE_SEM,
                              "LOADING-WORK-AREA-NAVCAM": PRESSURE_NAVCAM,
                              "LOADING-WORK-AREA-UNLOAD": PRESSURE_UNLOADED,
                              }[area]
        except KeyError:
            logging.warning("Unknown area %s, will assume it's unloaded", area)
            self._position = PRESSURE_UNLOADED

        # .position contains the last known/valid position
        # it's read-only, so we change it via _value
        self.position._set_value({"vacuum": self._position}, force_write=True)
        logging.debug("Chamber in position: %s", self._position)

    def _updateSampleHolder(self):
        """
        update the sampleHolder VAs
        """
        holder = self._pressure_device.GetSampleHolder()
        if holder.status == "SAMPLE-ABSENT":
            val = (None, None)
        else:
            # Convert base64 (to raw bytes of uint128-be) to long int.
            # As it comes from HTTP, id[0] is already converted back to string, but
            # we need bytes for the base64 function => encode back to bytes.
            # TODO: once Python 3-only, switch to decodebytes
            s = base64.decodestring(holder.holderID.id[0].encode("ascii"))
            if sys.version_info[0] >= 3:  # Python 3
                holderID = reduce(lambda a, n: (a << 8) + n, (v for v in s), 0)
            else:
                holderID = reduce(lambda a, n: (a << 8) + n, (ord(v) for v in s), 0)
            val = (holderID, holder.holderType)

            logging.debug("Sample holder 0x%x of type %s has status %s",
                          val[0], val[1], holder.status)

        # Status can be of 4 kinds, only when it's "present" that it means the
        # sample holder is registered
        registered = (holder.status == "SAMPLE-PRESENT")

        self.sampleHolder._set_value(val, force_write=True)
        self.registeredSampleHolder._set_value(registered, force_write=True)

    def _updateOpened(self):
        """
        update the opened VA
        """
        opened = (self._pressure_device.GetDoorStatus() == 'STAGE-DOOR-STATUS-OPEN')
        self.opened._set_value(opened, force_write=True)

    @isasync
    def moveRel(self, shift):
        self._checkMoveRel(shift)

        # convert into an absolute move
        pos = {}
        for a, v in shift.items:
            pos[a] = self.position.value[a] + v

        return self.moveAbs(pos)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        # Create ProgressiveFuture and update its state to RUNNING
        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self._estimateMoveTime())
        f._move_state = RUNNING

        # Task to run
        f.task_canceller = self._CancelMove
        f._move_lock = threading.Lock()

        return self._executor.submitf(f, self._changePressure, f, pos["vacuum"])

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.warning("Not able to stop axes: %s", ", ".join(self.axes))

    def terminate(self):
        if self._executor:
            self._chamber_must_stop.set()
            self._reconnection_must_stop.set()
            self.stop()
            self._executor.shutdown()
            self._executor = None

    def _estimateMoveTime(self):
        """
        Estimates move procedure duration
        """
        # Just an indicative time. It will be updated by polling the remaining
        # time.
        semmod = self.parent._device.GetSEMDeviceMode()
        if semmod not in ("SEM-MODE-BLANK", "SEM-MODE-IMAGING"):
            # Usually about five minutes
            timeRemaining = 5 * 60
        else:
            timeRemaining = 65
        return timeRemaining  # s

    def _changePressure(self, future, p):
        """
        Change of the pressure
        p (float): target pressure
        """
        # Keep remaining time up to date
        updater = functools.partial(self._updateTime, future, p)
        TimeUpdater = util.RepeatingTimer(1, updater, "Pressure time updater")
        TimeUpdater.start()
        self._move_in_progress.wait()
        with self._pressure_changing, self.parent._acq_progress_lock:
            try:
                instmode = self._pressure_device.GetInstrumentMode()
                semmode = self._pressure_device.GetSEMDeviceMode()
                logging.debug("Moving to chamber pressure %g (Phenom currently in %s/%s)...",
                              p, instmode, semmode)
                if p == PRESSURE_SEM:
                    # Both instrument and SEM modes report standby state, but
                    # SEM is what really matters, so care about it most.
                    if instmode != "INSTRUMENT-MODE-OPERATIONAL":
                        self._pressure_device.SetInstrumentMode("INSTRUMENT-MODE-OPERATIONAL")
                    if semmode not in ("SEM-MODE-BLANK", "SEM-MODE-IMAGING"):
                        self._wakeUp(future)

                    if future._move_state == CANCELLED:
                        raise CancelledError()

                    # If it's already in the right place, we are done (important
                    # as we'll never receive a new event)
                    opmode = self._pressure_device.GetOperationalMode()
                    if opmode == "OPERATIONAL-MODE-LIVESEM":
                        logging.debug("Device already in %s", opmode)
                        return

                    # It's "almost blocking": it waits until the stage has
                    # finished its move, but doesn't fully wait until the
                    # new operational mode is reached. Moreover, immediately
                    # GetOperationalMode() returns the new mode, quite
                    # before the event is sent and the system is actually ready.
                    try:
                        self._pressure_device.SelectImagingDevice(self._imagingDevice.SEMIMDEV)
                    except suds.WebFault as ex:
                        opmode = self._pressure_device.GetOperationalMode()
                        if opmode in ("OPERATIONAL-MODE-ACQUIRESEMIMAGE", "OPERATIONAL-MODE-LIVESEM"):
                            logging.warning("Moved raised an error but seems to have moved as expected to %s (%s)", opmode, ex)
                        else:
                            raise

                    # Typically, it will first go to ACQUIRESEMIMAGE, then LIVESEM
                    try:
                        while True:
                            evt = self._waitForEvent("OPERATIONAL-MODE-CHANGED-ID", 20)
                            opmode = evt.OperationalModeChanged.opMode
                            logging.debug("Operational mode is now %s", opmode)
                            if opmode == "OPERATIONAL-MODE-LIVESEM":
                                break
                            elif opmode != "OPERATIONAL-MODE-ACQUIRESEMIMAGE":
                                logging.warning("Excepted to reach SEM mode, but got mode %s", opmode)
                    except IOError:
                        logging.warning("Failed to receive operational mode event", exc_info=True)

                elif p == PRESSURE_NAVCAM:
                    # We could move to NavCam without waiting to wake up.
                    # We force first a wake-up as a "hack" so that in the very
                    # likely case the user goes to SEM mode afterwards, the total
                    # needed time is counted as soon as loading starts.
                    if instmode != "INSTRUMENT-MODE-OPERATIONAL":
                        self._pressure_device.SetInstrumentMode("INSTRUMENT-MODE-OPERATIONAL")
                    if semmode not in ("SEM-MODE-BLANK", "SEM-MODE-IMAGING"):
                        self._wakeUp(future)

                    if future._move_state == CANCELLED:
                        raise CancelledError()

                    # If it's already in the right place, we are done (important
                    # as we'll never receive a new event)
                    opmode = self._pressure_device.GetOperationalMode()
                    if opmode == "OPERATIONAL-MODE-LIVENAVCAM":
                        logging.debug("Device already in %s", opmode)
                        return

                    self._pressure_device.SelectImagingDevice(self._imagingDevice.NAVCAMIMDEV)

                    # Typically, it will first go to ACQUIRENAVCAMIMAGE, then LIVENAVCAM
                    try:
                        while True:
                            evt = self._waitForEvent("OPERATIONAL-MODE-CHANGED-ID", 10)
                            opmode = evt.OperationalModeChanged.opMode
                            logging.debug("Operational mode is now %s", opmode)
                            if opmode == "OPERATIONAL-MODE-LIVENAVCAM":
                                break
                            elif opmode != "OPERATIONAL-MODE-ACQUIRENAVCAMIMAGE":
                                logging.warning("Excepted to reach NavCam mode, but got mode %s", opmode)
                    except IOError:
                        logging.warning("Failed to receive operational mode event", exc_info=True)

                elif p == PRESSURE_UNLOADED:
                    self._pressure_device.UnloadSample()
                else:
                    raise ValueError("Unexpected pressure %g" % (p,))
            except Exception as ex:
                logging.exception("Failed to move to pressure %g: %s", p, ex)
                raise
            finally:
                TimeUpdater.cancel()

        # Wait for position to be updated (via the chamber_move event listener thread)
        self._position_event.wait(10)
        logging.debug("Move to pressure %g completed", p)

    def _updateTime(self, future, pressure):
        try:
            prog_info = self.parent._device.GetProgressAreaSelection()
            remainingTime = prog_info.progress.timeRemaining
            area = prog_info.target
            if area == "LOADING-WORK-AREA-SEM":
                waiting_time = 10
            else:
                waiting_time = 0
            future.set_progress(end=time.time() + self.wakeUpTime + remainingTime + waiting_time)
        except suds.WebFault:
            logging.warning("Time updater failed while moving to pressure %g.",
                            pressure, exc_info=True)

    def registerSampleHolder(self, code):
        """
        Register sample holder
        code (string): registration code
        Raise an exception if the sample holder is absent or the code is wrong
        """
        holder = self.parent._device.GetSampleHolder()
        if holder.status == "SAMPLE-ABSENT" or holder.status == "SAMPLE-UNSUPPORTED":
            raise ValueError("Sample holder is absent or unsupported")
        if holder.status == "SAMPLE-PRESENT":
            logging.info("Trying to register a sample holder already registered")
        try:
            self.parent._device.RegisterSampleHolder(holder.holderID, code)
        except Exception:
            # TODO check if RegisterSampleHolder raises an exception in case
            # of wrong code
            raise ValueError("Wrong sample holder registration code")

        self._updateSampleHolder()
        # If it worked, it should be now registered
        if not self.registeredSampleHolder.value:
            raise ValueError("Wrong sample holder registration code")

    def _wakeUp(self, future):
        """
        Wakes up the system (if it's in suspended or hibernation state).
        It's blocking, and will take care of updating the wake up time
        """
        logging.debug("Waiting for instrument to wake up")
        # Make sure system is waking up
        self.parent._device.SetInstrumentMode("INSTRUMENT-MODE-OPERATIONAL")

        # Event for remaining time update
        eventSpec = self.parent._objects.create('ns0:EventSpec')
        eventSpec.eventID = "SEM-PROGRESS-DEVICE-MODE-CHANGED-ID"
        eventSpec.compressed = False
        eventSpecArray = self.parent._objects.create('ns0:EventSpecArray')
        eventSpecArray.item = [eventSpec]
        ch_id = self._pressure_device.OpenEventChannel(eventSpecArray)

        while True:
            if future._move_state == CANCELLED:
                break
            new_evts = self._pressure_device.ReadEventChannel(ch_id)
            if new_evts == "":
                logging.debug("Event listener timeout")
                continue

            new_evt_id = new_evts[0][0].eventID
            if new_evt_id == "SEM-PROGRESS-DEVICE-MODE-CHANGED-ID":
                self.wakeUpTime = new_evts[0][0].SEMProgressDeviceModeChanged.timeRemaining
                logging.debug("Time to wake up: %f seconds", self.wakeUpTime)
                if self.wakeUpTime == 0:
                    break
            else:
                logging.warning("Unexpected event %s received", new_evt_id)

        self._pressure_device.CloseEventChannel(ch_id)

        # Wait a little, to be really sure
        time.sleep(1)

    def _waitForEvent(self, evtid, timeout=None):
        """
        evtid (str or int): the ID of the event to wait for
        timeout (None or 0<float): maximum time to wait (in s). Note: it's very
          rough, as the check might happen only every ~30s.
        return (Event): the event received
        raise IOError: in case of timeout
        """
        logging.debug("Waiting for a %s event", evtid)

        eventSpec = self.parent._objects.create('ns0:EventSpec')
        eventSpec.eventID = evtid
        eventSpec.compressed = False
        eventSpecArray = self.parent._objects.create('ns0:EventSpecArray')
        eventSpecArray.item = [eventSpec]
        ch_id = self._pressure_device.OpenEventChannel(eventSpecArray)
        try:
            tstart = time.time()
            while timeout is None or time.time() - tstart < timeout:
                new_evts = self._pressure_device.ReadEventChannel(ch_id)
                if new_evts == "":
                    logging.debug("Event listener timeout")
                    continue
                new_evt_id = new_evts[0][0].eventID
                logging.debug("Received event: %s", new_evt_id)
                if new_evt_id == evtid:
                    return new_evts[0][0]
                else:
                    logging.warning("Unexpected event %s received", new_evt_id)
            else:
                raise IOError("Timeout waiting for event %s" % (evtid,))
        finally:
            self._pressure_device.CloseEventChannel(ch_id)

    def _chamber_move_thread(self):
        """
        Thread that listens to changes in Phenom chamber pressure.
        """
        client = Client(self.parent._host + "?om", location=self.parent._host,
                        username=self.parent._username, password=self.parent._password,
                        timeout=SOCKET_TIMEOUT)
        device = client.service

        eventSpecArray = self.parent._objects.create('ns0:EventSpecArray')
        eventSpecArray.item = []
        for evtid in ("PROGRESS-AREA-SELECTION-CHANGED-ID", # sample holder move
                      "SAMPLEHOLDER-STATUS-CHANGED-ID",  # sample holder insertion
                      "DOOR-STATUS-CHANGED-ID"):  # door open/closed
            eventSpec = self.parent._objects.create('ns0:EventSpec')
            eventSpec.eventID = evtid
            eventSpec.compressed = False
            eventSpecArray.item.append(eventSpec)

        ch_id = device.OpenEventChannel(eventSpecArray)
        try:
            while not self._chamber_must_stop.is_set():
                logging.debug("Chamber move thread about to read event...")
                new_evts = self._pressure_device.ReadEventChannel(ch_id)
                if new_evts == "":
                    logging.debug("Event listener timeout")
                    continue

                new_evt_id = new_evts[0][0].eventID
                logging.debug("Received event: %s", new_evt_id)
                if new_evt_id == "PROGRESS-AREA-SELECTION-CHANGED-ID":
                    try:
                        time_remaining = new_evts[0][0].ProgressAreaSelectionChanged.progress.timeRemaining
                        logging.debug("Time remaining to reach new chamber position: %f seconds", time_remaining)
                        if time_remaining == 0:
                            # Move in progress is completed
                            self._move_in_progress.set()
                            # Wait until any pressure move requested by us is completed
                            with self._pressure_changing:
                                self._updatePosition()

                            # When moved to SEM position, blank ASAP
                            if self._position == PRESSURE_SEM:
                                self.parent._detector.update_parameters()
                                if self.parent._scanner.blanker.value in (None, True):
                                    try:
                                        self.parent._scanner._blank_beam(True)
                                    except suds.WebFault as ex:
                                        logging.warning("Failed to blank the beam when moving to SEM mode: %s", ex)

                            self._position_event.set()
                        else:
                            self._move_in_progress.clear()
                            self._position_event.clear()
                    except Exception:
                        logging.warning("Received event does not have the expected attribute or format")
                elif new_evt_id == "SAMPLEHOLDER-STATUS-CHANGED-ID":
                    logging.debug("Sample holder insertion, about to update sample holder id if needed")
                    self._updateSampleHolder()  # in case new sample holder was loaded
                elif new_evt_id == "DOOR-STATUS-CHANGED-ID":
                    logging.debug("Door status changed")
                    self._updateOpened()  # in case door status is changed
                else:
                    logging.warning("Unexpected event received")
        except Exception:
            logging.exception("Unexpected failure during chamber pressure event listening. Lost connection to Phenom.")
            # Update the state of SEM component so the backend is aware of the error occured
            hw_error = HwError("Unexpected failure during chamber pressure event listening. Lost connection to Phenom.")
            self.parent.state._value = hw_error
            self.parent.state.notify(hw_error)
            # Keep on trying to reconnect
            self._reconnect_thread = threading.Thread(target=self._reconnection_thread,
                                                      name="Phenom reconnection attempt")
            self._reconnect_thread.start()
        finally:
            device.CloseEventChannel(ch_id)
            logging.debug("Chamber pressure thread closed")
            self._chamber_must_stop.clear()

    def _reconnection_thread(self):
        """
        Keeps on trying to reconnect after connection failure.
        """
        try:
            while not self._reconnection_must_stop.is_set():
                # Wait before retrying
                time.sleep(5)
                try:
                    mode = self._pressure_device.GetInstrumentMode()
                    logging.debug("Current Phenom mode: %s", mode)
                    if mode != 'INSTRUMENT-MODE-ERROR':
                        # Phenom up and running
                        st_running = model.ST_RUNNING
                        self.parent.state._value = st_running
                        self.parent.state.notify(st_running)
                        # Update with the current pressure state
                        self._updatePosition()
                        # We can now open the event channel again
                        self._chamber_thread = threading.Thread(target=self._chamber_move_thread,
                                                                name="Phenom chamber pressure state change")
                        self._chamber_thread.start()
                        break
                except Exception:
                    logging.warning("Retrying to connect to Phenom...")
        finally:
            logging.debug("Phenom reconnection attempt thread closed")
            self._reconnection_must_stop.clear()

    def _CancelMove(self, future):
        """
        Canceller of _changePressure task.
        """
        logging.debug("Cancelling chamber move...")

        with future._move_lock:
            if future._move_state == FINISHED:
                return False
            future._move_state = CANCELLED
            # TODO: actually stop what is current happening
            logging.debug("Delphi chamber move cancelled.")

        return True
