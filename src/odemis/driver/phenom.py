# -*- coding: utf-8 -*-
'''
Created on 30 April 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

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

from abc import abstractmethod, ABCMeta
import base64
import collections
import functools
import logging
import math
import numpy
from odemis import model, util
from odemis.model import isasync, CancellableThreadPoolExecutor
import suds
from suds.client import Client
import threading
import time
import weakref
from numpy.linalg import norm
from odemis.model import HwError
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING

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
#       scan parameter scale to 0. To be sure the center is at the same place
#       as the center of the standard scanning, we also temporarily reset the
#       HFW to minimum value.
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
TILT_BLANK = (-1, -1)  # tilt to imitate beam blanking

# SEM ranges in order to allow scanner initialization even if Phenom is in
# unloaded state
HFW_RANGE = [2.5e-06, 0.0031]
TENSION_RANGE = [4797.56, 10000.0]
# REFERENCE_TENSION = 10e03 #Volt
# BEAM_SHIFT_AT_REFERENCE = 19e-06  # Maximum beam shit at the reference tension #m
SPOT_RANGE = [0.0, 5.73018379531] # TODO: what means a spot of 0? => small value like 1e-3?
NAVCAM_PIXELSIZE = (1.3267543859649122e-05, 1.3267543859649122e-05)
DELPHI_OVERVIEW_FOCUS = 0.0052  # Good focus position for navcam focus initialization

class SEM(model.HwComponent):
    '''
    This represents the bare Phenom SEM.
    '''
    def __init__(self, name, role, children, host, username, password, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner" and "detector"
            They will be provided back in the .children VA
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
        self._device = client.service

        # Access to service objects
        self._objects = client.factory
        try:
            info = self._device.VersionInfo().versionInfo
        except AttributeError:
            raise KeyError("Failed to connect to Phenom. The username or password is incorrect.")

        try:
            start = info.index("'Product Name'>") + len("'Product Name'>")
            end = info.index("</Property", start)
            hwname = "%s" % (info[start:end])
            self._metadata[model.MD_HW_NAME] = hwname
            # TODO: how to retrieve the edition information?
            hwver = "G4"
            self._hwVersion = "%s %s" % (hwname, hwver)
            self._metadata[model.MD_HW_VERSION] = self._hwVersion

            start = info.index("'Version'>") + len("'Version'>")
            end = info.index("</Property", start)
            self._swVersion = "%s" % (info[start:end])
            self._metadata[model.MD_SW_VERSION] = self._swVersion

            logging.info("Connected to %s v%s", self._hwVersion, self._swVersion)
        except ValueError:
            logging.warning("Phenom version could not be retrieved")

        # Lock in order to synchronize all the child component functions
        # that acquire data from the SEM while we continuously acquire images
        self._acq_progress_lock = threading.Lock()

        self._imagingDevice = self._objects.create('ns0:imagingDevice')

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
            raise KeyError("PhenomSEM was not given a 'pressure' child")
        self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._pressure)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        # Don't need to close the connection, it's already closed by the time
        # suds returns the data
        self._scanner.terminate()
        self._detector.terminate()
        self._stage.terminate()
        self._focus.terminate()
        self._navcam.terminate()
        self._navcam_focus.terminate()
        self._pressure.terminate()

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
        self.last_fov = self.horizontalFoV.value

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        self.pixelSize = model.VigilantAttribute((0, 0), unit="m", readonly=True)

        # (.resolution), .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in m => physically moves the e-beam. The move is
        # clipped within the actual limits by the setter function.
        shift_rng = ((-1, -1),
                    (1, 1))
        self.shift = model.TupleContinuous((0, 0), shift_rng,
                                              cls=(int, long, float), unit="m",
                                              setter=self._setShift)
        self.shift.subscribe(self._onShift, init=True)

        # (float, float) in px => Supposed to move center of acquisition by this
        # amount independent of scale and rotation. In this case does nothing.
        tran_rng = [(-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2)]
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                              cls=(int, long, float), unit="px")

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        resolution = (self._shape[0] // 8, self._shape[1] // 8)
        self.resolution = model.ResolutionVA(resolution, [(1, 1), self._shape],
                                             setter=self._setResolution)
        self._resolution = resolution

        # (float, float) as a ratio => how big is a pixel, compared to pixelSize
        # it basically works the same as binning, but can be float
        # (Default to scan the whole area)
        self._scale = (self._shape[0] / resolution[0], self._shape[1] / resolution[1])
        self.scale = model.TupleContinuous(self._scale, [(1, 1), self._shape],
                                           cls=(int, long, float),
                                           unit="", setter=self._setScale)
        self.scale.subscribe(self._onScale, init=True)  # to update metadata

        self._updatePixelSize() # needs .scale

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
        spot_rng = SPOT_RANGE
        self._spotSize = numpy.mean(SPOT_RANGE)
        self.spotSize = model.FloatContinuous(self._spotSize, spot_rng,
                                              setter=self._setSpotSize)

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

        # we don't set it explicitly, to avoid calling .SetSEMHFW()
        self.horizontalFoV._value = fov
        self.horizontalFoV.notify(fov)

    def _onHorizontalFoV(self, fov):
        # Update current pixelSize and magnification
        self._updatePixelSize()
        self._updateMagnification()

    def _setHorizontalFoV(self, value):
        # Make sure you are in the current range
        try:
            rng = self.parent._device.GetSEMHFWRange()
            new_fov = numpy.clip(value, rng.min, rng.max)
            self.parent._device.SetSEMHFW(new_fov)
            return new_fov
        except suds.WebFault:
            logging.debug("Cannot set HFW when the sample is not in SEM.")

        return self.horizontalFoV.value

    def _updateMagnification(self):

        # it's read-only, so we change it only via _value
        mag = self._hfw_nomag / self.horizontalFoV.value
        self.magnification._value = mag
        self.magnification.notify(mag)

    def _setDwellTime(self, dt):
        # Calculate number of frames
        self._nr_frames = int(math.ceil(dt / DWELL_TIME))
        new_dt = DWELL_TIME * self._nr_frames

        # Abort current scanning when dwell time is changed
        try:
            self.parent._device.SEMAbortImageAcquisition()
        except suds.WebFault:
            logging.debug("No acquisition in progress to be aborted.")

        return new_dt

    def _onRotation(self, rot):
        with self.parent._acq_progress_lock:
            self.parent._device.SetSEMRotation(-rot)

    def _onVoltage(self, volt):
        # When we change voltage while SEM stream is off
        # beam is unblanked. Thus we keep and reset the
        # last known source tilt
        current_tilt = self.parent._device.GetSEMSourceTilt()
        self.parent._device.SEMSetHighTension(-volt)
        new_tilt = self.parent._device.GetSEMSourceTilt()
        self.parent._detector._tilt_unblank = (new_tilt.aX, new_tilt.aY)
        if ((current_tilt.aX, current_tilt.aY) == TILT_BLANK):
            self.parent._device.SetSEMSourceTilt(current_tilt.aX, current_tilt.aY, False)
        # Brightness and contrast have to be adjusted just once
        # we set up the detector (see SEMACB())
        # TODO reset the beam shift so it is within boundaries

    def _setSpotSize(self, value):
        # Set the corresponding spot size to Phenom SEM
        self._spotSize = value
        try:
            current_tilt = self.parent._device.GetSEMSourceTilt()
            self.parent._device.SEMSetSpotSize(value)
            new_tilt = self.parent._device.GetSEMSourceTilt()
            self.parent._detector._tilt_unblank = (new_tilt.aX, new_tilt.aY)
            if ((current_tilt.aX, current_tilt.aY) == TILT_BLANK):
                self.parent._device.SetSEMSourceTilt(current_tilt.aX, current_tilt.aY, False)
            return self._spotSize
        except suds.WebFault:
            logging.debug("Cannot set Spot Size when the sample is not in SEM.")

        return self.spotSize.value

    def _onScale(self, s):
        # Abort current scanning when scale is changed
        try:
            self.parent._device.SEMAbortImageAcquisition()
        except suds.WebFault:
            logging.debug("No acquisition in progress to be aborted.")
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the scale and FoV
        """
        fov = self.horizontalFoV.value

        pxs = (fov / self._shape[0],
               fov / self._shape[1])

        # it's read-only, so we change it only via _value
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setScale(self, value):
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
         the original pixel size. It will adapt the resolution to
         have the same ROI (just different amount of pixels scanned)
        return the actual value used
        """
        prev_scale = self._scale
        self._scale = value

        # adapt resolution so that the ROI stays the same
        change = (prev_scale[0] / self._scale[0],
                  prev_scale[1] / self._scale[1])
        old_resolution = self.resolution.value
        new_resolution = (max(int(round(old_resolution[0] * change[0])), 1),
                          max(int(round(old_resolution[1] * change[1])), 1))

        self.resolution.value = new_resolution  # will call _setResolution()

        return value

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the
         resolution is not possible, it will pick the most fitting one.
        returns the actual value used
        """
        # In case of resolution 1,1 store the current fov and set the spot mode
        # if value == (1, 1) and self._resolution != (1, 1):
        #    self.last_fov = self.horizontalFoV.value
        #    self.horizontalFoV.value = self.horizontalFoV.range[0]
        # If we are going back from spot mode to normal scanning, reset fov
        # elif self._resolution == (1, 1):
        #    self.horizontalFoV.value = self.last_fov

        max_size = (int(self._shape[0] // self._scale[0]),
                    int(self._shape[1] // self._scale[1]))

        # at least one pixel, and at most the whole area
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size

        return size

    def _onShift(self, shift):
        beamShift = self.parent._objects.create('ns0:position')
        with self.parent._acq_progress_lock:
            new_shift = (shift[0], shift[1])
            beamShift.x, beamShift.y = new_shift[0], new_shift[1]
            logging.debug("EBeam shifted by %s m,m", new_shift)
            self.parent._device.SetSEMImageShift(beamShift, True)

    def _setShift(self, value):
        """
        value (float, float): shift from the center. It will always ensure that
          the shift is within the hardware limits.
        returns actual shift accepted
        """
        # Clip shift (i.e. beam shift) within 50 microns due to
        # Phenom limitation
        # Calculate shift distance
        shift_d = norm(numpy.asarray([0, value[0]]) - numpy.asarray([value[1], 0]))
        # Change to the actual maximum beam shift
        # limit = (REFERENCE_TENSION / self.accelVoltage.value) * BEAM_SHIFT_AT_REFERENCE
        rng = self.parent._device.GetSEMImageShiftRange()
        limit = rng.max
        # The ratio between the shift distance and the limit
        ratio = 1
        if shift_d > limit:
            ratio = shift_d / limit
        # Clip within limit
        clipped_shift = (value[0] / ratio, value[1] / ratio)
        return clipped_shift

class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image
    is captured.
    """
    def __init__(self, name, role, parent, **kwargs):
        """
        Note: parent should have a child "scanner" already initialised
        """
        # It will set up ._shape and .parent
        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._hwVersion = parent._hwVersion
        self._swVersion = parent._swVersion

        # will take care of executing autocontrast asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # 16 or 8 bits image
        self.bpp = model.IntEnumerated(8, set([8, 16]),
                                          unit="", setter=self._setBpp)

        # HW contrast and brightness
        self.contrast = model.FloatContinuous(0.5, [0, 1], unit="")
        self.contrast.subscribe(self._onContrast)
        self.brightness = model.FloatContinuous(0.5, [0, 1], unit="")
        self.brightness.subscribe(self._onBrightness)

        # setup detector
        self._scanParams = self.parent._objects.create('ns0:scanParams')
        # use all detector segments
        detectorMode = 'SEM-DETECTOR-MODE-ALL'
        self._scanParams.detector = detectorMode

        # adjust brightness and contrast
        # self.parent._device.SEMACB()

        self.data = SEMDataFlow(self, parent)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._grid_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # The shape is just one point, the depth
        self._shape = (2 ** 16,)  # only one point

        # Updated by the chamber pressure
        self._tilt_unblank = None

        # Used for grid scanning
        self._spot_scanner = None
        self._coordinates = None
        self._is_scanning = False
        self._last_res = None
        self._updater = functools.partial(self._scanSpots)

        # Start dedicated connection for acquisition stream
        acq_client = Client(self.parent._host + "?om", location=self.parent._host,
                        username=self.parent._username, password=self.parent._password,
                        timeout=SOCKET_TIMEOUT)
        self._acq_device = acq_client.service

        # Start dedicated connection for grid scanning thread to avoid collision with
        # acquisition stream
        grid_client = Client(self.parent._host + "?om", location=self.parent._host,
                        username=self.parent._username, password=self.parent._password,
                        timeout=SOCKET_TIMEOUT)
        self._grid_device = grid_client.service

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
        # Check if we need to temporarily unblank the ebeam
        cur_tilt = self.parent._device.GetSEMSourceTilt()
        beam_blanked = ((cur_tilt.aX, cur_tilt.aY) == TILT_BLANK)
        if beam_blanked:
            try:
                # "Unblank" the beam
                self.beam_blank(False)
            except suds.WebFault:
                logging.warning("Beam might still be blanked!")
        with self.parent._acq_progress_lock:
            self.parent._device.SEMACB()
        if beam_blanked:
            try:
                # "Blank" the beam
                self.beam_blank(True)
            except suds.WebFault:
                logging.warning("Beam might still be unblanked!")
        # Update with the new values after automatic procedure is completed
        self._updateContrast()
        self._updateBrightness()

    def _setBpp(self, value):
        return value

    def _onContrast(self, value):
        with self.parent._acq_progress_lock:
            # Actual range in Phenom is (0,4]
            contr = numpy.clip(4 * value, 0.00001, 4)
            try:
                self.parent._device.SetSEMContrast(contr)
            except suds.WebFault:
                logging.debug("Setting SEM contrast may be unsuccesful")


    def _onBrightness(self, value):
        with self.parent._acq_progress_lock:
            try:
                self.parent._device.SetSEMBrightness(value)
            except suds.WebFault:
                logging.debug("Setting SEM brightness may be unsuccesful")

    def _updateContrast(self):
        """
        Reads again the hardware setting and update the VA
        """
        contr = (self.parent._device.GetSEMContrast() / 4)
        contr = self.contrast.clip(contr)

        # we don't set it explicitly, to avoid calling .onContrast()
        self.contrast._value = contr
        self.contrast.notify(contr)

    def _updateBrightness(self):
        """
        Reads again the hardware setting and update the VA
        """
        bright = self.parent._device.GetSEMBrightness()
        bright = self.brightness.clip(bright)

        # we don't set it explicitly, to avoid calling .onBrightness()
        self.brightness._value = bright
        self.brightness.notify(bright)

    def _scanSpots(self):
        try:
            with self._grid_lock:
                logging.debug("Grid scanning %s...", self._coordinates)
                for shift_pos in self._coordinates:
                    if self._scan_params_view.scale != 0:
                        self._scan_params_view.scale = 0
                    # Also compensate for spot_shift
                    md_bsd = self.getMetadata()
                    spot_shift = md_bsd.get(model.MD_SPOT_SHIFT, (0, 0))
                    self._scan_params_view.center.x = shift_pos[0] + spot_shift[0]
                    self._scan_params_view.center.y = shift_pos[1] + spot_shift[1]
                    try:
                        self._grid_device.SetSEMViewingMode(self._scan_params_view, 'SEM-SCAN-MODE-IMAGING')
                    except suds.WebFault:
                        logging.warning("Spot scan failure.")

        except Exception:
            logging.exception("Unexpected failure during spot scanning")

    def update_parameters(self):
        # Update stage and focus position
        self.parent._stage._updatePosition()
        self.parent._focus._updatePosition()
        self.parent._navcam_focus._updatePosition()

        # Update all the Scanner VAs upon stream start
        # Get current field of view and compute magnification
        fov = self._acq_device.GetSEMHFW()
        self.parent._scanner.horizontalFoV.value = fov

        rotation = self._acq_device.GetSEMRotation()
        self.parent._scanner.rotation.value = -rotation

        volt = self._acq_device.SEMGetHighTension()
        self.parent._scanner.accelVoltage.value = -volt

        # Get current spot size
        self.parent._scanner._spotSize = self._acq_device.SEMGetSpotSize()
        self.parent._scanner.spotSize.value = self.parent._scanner._spotSize

        # Update all Detector VAs
        contr = (self._acq_device.GetSEMContrast() / 4)
        # Handle cases where Phenom returns weird values
        self.parent._detector.contrast.value = self.parent._detector.contrast.clip(contr)
        bright = self._acq_device.GetSEMBrightness()
        self.parent._detector.brightness.value = self.parent._detector.brightness.clip(bright)

    def start_acquire(self, callback):
        # Check if Phenom is in the proper mode
        area = self._acq_device.GetProgressAreaSelection().target
        if area != "LOADING-WORK-AREA-SEM":
            raise IOError("Cannot initiate stream, Phenom is not in SEM mode.")
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            try:
                # "Unblank" the beam
                self.beam_blank(False)
            except suds.WebFault:
                logging.warning("Beam might still be blanked!")
            target = self._acquire_thread
            self._acquisition_thread = threading.Thread(target=target,
                    name="PhenomSEM acquire flow thread",
                    args=(callback,))
            self._acquisition_thread.start()

    def beam_blank(self, blank):
        """
        (Un)blank the beam
        blank (boolean): If True, will blank the beam, otherwise will unblank it
        """
        with self.parent._acq_progress_lock:
            if blank:
                self.parent._device.SetSEMSourceTilt(TILT_BLANK[0], TILT_BLANK[1], False)
            else:
                self.parent._device.SetSEMSourceTilt(self._tilt_unblank[0], self._tilt_unblank[1], False)

    def stop_acquire(self):
        with self._acquisition_lock:
            with self._acquisition_init_lock:
                self._acquisition_must_stop.set()
                try:
                    self._acq_device.SEMAbortImageAcquisition()
                except suds.WebFault:
                    logging.debug("No acquisition in progress to be aborted.")
                # "Blank" the beam
                self.beam_blank(True)

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

    def _acquire_image(self):
        """
        Acquires the SEM image based on the resolution and
        current drift.
        """
        with self.parent._acq_progress_lock:
            res = self.parent._scanner.resolution.value
            # Set dataType based on current bpp value
            bpp = self.bpp.value
            if bpp == 16:
                dataType = numpy.uint16
            else:
                dataType = numpy.uint8

            self._scanParams.nrOfFrames = self.parent._scanner._nr_frames
            self._scanParams.HDR = bpp == 16

            md_bsd = self.getMetadata()
            # SEM image shift correction parameters
            AX, AY = md_bsd.get(model.MD_RESOLUTION_SLOPE, (0, 0))
            BX, BY = md_bsd.get(model.MD_RESOLUTION_INTERCEPT, (0, 0))
            CX, CY = md_bsd.get(model.MD_HFW_SLOPE, (0, 0))
            # SEM spot shift correction parameters
            spot_shift = md_bsd.get(model.MD_SPOT_SHIFT, (0, 0))
            resolution = self.parent._scanner.resolution.value
            self._scanParams.center.x = -(1 / (2 * math.pi) * numpy.arctan(-AX / (resolution[0] + BX)) + CX / 100)
            self._scanParams.center.y = -(1 / (2 * math.pi) * numpy.arctan(-AY / (resolution[1] + BY)) + CY / 100)

            # update changed metadata
            metadata = dict(self.parent._metadata)
            metadata[model.MD_ACQ_DATE] = time.time()
            metadata[model.MD_BPP] = bpp

            self._scan_params_view = self._acq_device.GetSEMViewingMode().parameters
            self._scan_params_view.resolution.width = 456
            self._scan_params_view.resolution.height = 456
            self._scan_params_view.nrOfFrames = 1
            logging.debug("Acquiring SEM image of %s with %d bpp and %d frames",
                          res, bpp, self._scanParams.nrOfFrames)
            # Check if spot mode is required
            if res == (1, 1):
                # Cancel possible grid scanning
                if self._is_scanning:
                    self._spot_scanner.cancel()
                    self._is_scanning = False
                    # Wait grid scanner to stop
                    with self._grid_lock:
                        # Move back to the center
                        self._scan_params_view.center.x = spot_shift[0]
                        self._scan_params_view.center.y = spot_shift[1]
                        try:
                            self._acq_device.SetSEMViewingMode(self._scan_params_view, 'SEM-SCAN-MODE-IMAGING')
                        except suds.WebFault:
                            logging.warning("Move to centre failure.")

                # Avoid setting resolution to 1,1
                # Set scale so the FoV is reduced to something really small
                # even if the current HFW is the maximum
                if self._scan_params_view.scale != 0:
                    self._scan_params_view.scale = 0
                    self._scan_params_view.center.x = spot_shift[0]
                    self._scan_params_view.center.y = spot_shift[1]
                    self._acq_device.SetSEMViewingMode(self._scan_params_view, 'SEM-SCAN-MODE-IMAGING')
                time.sleep(0.1)
                # MD_POS is hopefully set via updateMetadata
                return model.DataArray(numpy.array([[0]], dtype=dataType), metadata)
            elif (res[0] <= 128 or res[1] <= 128):
                # Handle resolution values that may be used for fine alignment
                # Compute the exact spot coordinates within the current fov
                # and scan spot by spot
                # Start scanning
                if not self._is_scanning:
                    fov = (1.0 - (1.0 / res[0]), 1.0 - (1.0 / res[1]))
                    spot_dist = (fov[0] / (res[0] - 1),
                                 fov[1] / (res[1] - 1))
                    self._coordinates = []
                    bound = (fov[0] / 2.0,
                             fov[1] / 2.0)

                    for i in range(res[0]):
                        for j in range(res[1]):
                            self._coordinates.append((-bound[0] + i * spot_dist[0],
                                                      - bound[1] + j * spot_dist[1]))
                    # Straight use of Phenom API. In the future the Stage component
                    # will only move the physical stage (BACKLASH-ONLY') and not the
                    # ebeam thus we avoid using it.
                    self._stagePos = self.parent._objects.create('ns0:position')
                    # self._navAlgorithm = self.parent._objects.create('ns0:navigationAlgorithm')
                    self._navAlgorithm = 'NAVIGATION-AUTO'
                    self._spot_scanner = util.RepeatingTimer(0, self._updater, "Grid scanner")
                    self._spot_scanner.start()
                    self._is_scanning = True
                elif self._last_res != res:
                    self._spot_scanner.cancel()
                    self._is_scanning = False

                self._last_res = res
                return model.DataArray(numpy.array([[0]], dtype=dataType), metadata)

            else:
                # Cancel possible grid scanning
                if self._is_scanning:
                    self._spot_scanner.cancel()
                    self._is_scanning = False

                self._scanParams.scale = 1
                self._scanParams.resolution.width = res[0]
                self._scanParams.resolution.height = res[1]
                if self._scan_params_view.scale != 1:
                    self._scan_params_view.scale = 1
                    # Move back to the center
                    self._scan_params_view.center.x = 0
                    self._scan_params_view.center.y = 0
                    self._acq_device.SetSEMViewingMode(self._scan_params_view, 'SEM-SCAN-MODE-IMAGING')
                img_str = self._acq_device.SEMAcquireImageCopy(self._scanParams)
                # Use the metadata from the string to update some metadata
                # metadata[model.MD_POS] = (img_str.aAcqState.position.x, img_str.aAcqState.position.y)
                metadata[model.MD_EBEAM_VOLTAGE] = img_str.aAcqState.highVoltage
                metadata[model.MD_EBEAM_CURRENT] = img_str.aAcqState.emissionCurrent
                metadata[model.MD_ROTATION] = -img_str.aAcqState.rotation
                metadata[model.MD_DWELL_TIME] = img_str.aAcqState.dwellTime * img_str.aAcqState.integrations
                metadata[model.MD_PIXEL_SIZE] = (img_str.aAcqState.pixelWidth,
                                                 img_str.aAcqState.pixelHeight)
                metadata[model.MD_HW_NAME] = self._hwVersion + " (s/n %s)" % img_str.aAcqState.instrumentID

                # image to ndarray
                sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]),
                                           dtype=dataType)
                sem_img.shape = res[::-1]
                return model.DataArray(sem_img, metadata)

    def _acquire_thread(self, callback):
        """
        Thread that performs the SEM acquisition. It calculates and updates the
        center (e-beam) position and provides the new generated output to the
        Dataflow.
        """
        try:
            while not self._acquisition_must_stop.is_set():
                with self._acquisition_init_lock:
                    if self._acquisition_must_stop.is_set():
                        break
                callback(self._acquire_image())
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
        try:
            # "Unblank" the beam
            if self._tilt_unblank is not None:
                self.beam_blank(False)
        except suds.WebFault:
            logging.warning("Beam might still be blanked!")

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
        self._navAlgorithm = parent._objects.create('ns0:navigationAlgorithm')
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
                                    self._applyInversionAbs(self._position),
                                    unit="m", readonly=True)

    def _updatePosition(self):
        """
        update the position VA
        """
        mode_pos = self.parent._device.GetStageModeAndPosition()
        self._position["x"] = mode_pos.position.x
        self._position["y"] = mode_pos.position.y

        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    def _doMoveAbs(self, pos):
        """
        move to the position
        """
        with self.parent._acq_progress_lock:
            self._stagePos.x = pos.get("x", self._position["x"])
            self._stagePos.y = pos.get("y", self._position["y"])
            self.parent._device.MoveTo(self._stagePos, self._navAlgorithm)

            # Obtain the finally reached position after move is performed.
            # This is mainly in order to keep the correct position in case the
            # move we tried to perform was greater than the maximum possible
            # one.
            # with self.parent._acq_progress_lock:
            self._updatePosition()

    def _doMoveRel(self, shift):
        """
        move by the shift
        """
        with self.parent._acq_progress_lock:
            self._stageRel.x, self._stageRel.y = shift.get("x", 0), shift.get("y", 0)
            self.parent._device.MoveBy(self._stageRel, self._navAlgorithm)

            # Obtain the finally reached position after move is performed.
            # This is mainly in order to keep the correct position in case the
            # move we tried to perform was greater than the maximum possible
            # one.
            # with self.parent._acq_progress_lock:
            self._updatePosition()

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        shift = self._applyInversionRel(shift)
        return self._executor.submit(self._doMoveRel, shift)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionAbs(pos)

        # self._doMove(pos)
        return self._executor.submit(self._doMoveAbs, pos)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

class PhenomFocus(model.Actuator):
    """
    This is an extension of the model.Actuator class and represents a focus
    actuator. This is an abstract class that should be inherited.
    """
    __metaclass__ = ABCMeta
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
        self.position._value = self._applyInversionAbs(pos)
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
        shift = self._applyInversionRel(shift)
        logging.debug("Submit relative move of %s...", shift)
        self._moves_queue.append(("moveRel", shift))
        return self._executor.submit(self._checkQueue)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionAbs(pos)
        logging.info("Submit absolute move of %s...", pos)
        self._moves_queue.append(("moveAbs", pos))
        return self._executor.submit(self._checkQueue)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))

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
        hfw (float): NavCam HFW #m
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
        self._hfw = hfw

        resolution = NAVCAM_RESOLUTION
        # RGB
        self._shape = resolution + (3, 2 ** 8)
        self.resolution = model.ResolutionVA(resolution,
                                      [NAVCAM_RESOLUTION, NAVCAM_RESOLUTION])
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
            # Start to a good focus position
            logging.debug("Setting initial overview focus to %f", DELPHI_OVERVIEW_FOCUS)
            f = self.parent._navcam_focus.moveAbs({"z": DELPHI_OVERVIEW_FOCUS})
            f.result()

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
            logging.exception("Failure during acquisition")
        finally:
            self.acquisition_lock.release()
            logging.debug("Acquisition thread closed")
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
VACUUM_TIMEOUT = 5  # s
class ChamberPressure(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    adjusting the chamber pressure. It actually allows the user to move the sample
    between the NavCam and SEM areas or even unload it.
    """
    def __init__(self, name, role, parent, ranges=None, **kwargs):
        axes = {"pressure": model.Axis(unit="Pa",
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
                                    {"pressure": self._position},
                                    unit="Pa", readonly=True)
        logging.debug("Chamber in position: %s", self.position)

        # Start dedicated connection for api calls during the change of pressure state
        # The main purpose is to avoid collisions with the calls from the Time updater
        pressure_client = Client(self.parent._host + "?om", location=self.parent._host,
                        username=self.parent._username, password=self.parent._password,
                        timeout=SOCKET_TIMEOUT)
        self._pressure_device = pressure_client.service

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # Tuple containing sample holder ID and type, or None, None if absent
        self.sampleHolder = model.TupleVA((None, None), readonly=True)

        # VA used for the sample holder registration
        self.registeredSampleHolder = model.BooleanVA(False, readonly=True)

        self._updatePosition()
        self._updateSampleHolder()

        # Start thread that continuously listens to chamber state changes
        chamber_client = Client(self.parent._host + "?om", location=self.parent._host,
                        username=self.parent._username, password=self.parent._password,
                        timeout=SOCKET_TIMEOUT)
        self._chamber_device = chamber_client.service
        # Event to indicate that any move requested by the user has been
        # completed and thus position can be updated
        self._chamber_event = threading.Event()
        self._chamber_event.set()
        # Event to prevent move applied from the user while another move is
        # in progress
        self._move_event = threading.Event()
        self._move_event.set()
        # Event to prevent future from returning before position is updated
        self._position_event = threading.Event()
        self._position_event.set()

        # Thread that listens to pressure state changes
        self._chamber_must_stop = threading.Event()
        target = self._chamber_move_thread
        self._chamber_thread = threading.Thread(target=target,
                name="Phenom chamber pressure state change")
        self._chamber_thread.start()

        # Event for reconnection thread
        self._reconnection_must_stop = threading.Event()

    def _updatePosition(self):
        """
        update the position VA and .pressure VA
        """
        logging.debug("About to update chamber position...")
        area = self.parent._device.GetProgressAreaSelection().target  # last official position
        logging.debug("Targeted area: %s", area)
        if area == "LOADING-WORK-AREA-SEM":
            # Once moved in SEM, get current tilt and use as beam unblank value
            # Then blank the beam and unblank it once SEM stream is started
            self.parent._detector.update_parameters()
            self.parent._detector._tilt_unblank = self.parent._device.GetSEMSourceTilt()
            self.parent._detector.beam_blank(True)
            self._position = PRESSURE_SEM
        elif area == "LOADING-WORK-AREA-NAVCAM":
            self._position = PRESSURE_NAVCAM
        else:
            self._position = PRESSURE_UNLOADED

        # .position contains the last known/valid position
        # it's read-only, so we change it via _value
        self.position._value = {"pressure": self._position}
        self.position.notify(self.position.value)
        logging.debug("Chamber in position: %s", self.position)

    def _updateSampleHolder(self):
        """
        update the sampleHolder VAs
        """
        holder = self._pressure_device.GetSampleHolder()
        if holder.status == "SAMPLE-ABSENT":
            val = (None, None)
        else:
            # Convert base64 to long int
            s = base64.decodestring(holder.holderID.id[0])
            holderID = reduce(lambda a, n: (a << 8) + n, (ord(v) for v in s), 0)
            val = (holderID, holder.holderType)

        # Status can be of 4 kinds, only when it's "present" that it means the
        # sample holder is registered
        registered = (holder.status == "SAMPLE-PRESENT")

        self.sampleHolder._value = val
        self.registeredSampleHolder._value = registered
        self.sampleHolder.notify(val)
        self.registeredSampleHolder.notify(registered)

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

        return self._executor.submitf(f, self._changePressure, f, pos)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.info("Stopping all axes: %s", ", ".join(self.axes))

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
        self._chamber_event.clear()
        self._move_event.wait()
        with self.parent._acq_progress_lock:
            logging.debug("Moving to another chamber state...")
            try:
                if p["pressure"] == PRESSURE_SEM:
                    if self._pressure_device.GetInstrumentMode() != "INSTRUMENT-MODE-OPERATIONAL":
                        self._pressure_device.SetInstrumentMode("INSTRUMENT-MODE-OPERATIONAL")
                    semmod = self._pressure_device.GetSEMDeviceMode()
                    if semmod not in ("SEM-MODE-BLANK", "SEM-MODE-IMAGING"):
                        # If in standby or currently waking up, open event channel
                        self._wakeUp(future)
                    if future._move_state == CANCELLED:
                        raise CancelledError()
                    try:
                        self._pressure_device.SelectImagingDevice(self._imagingDevice.SEMIMDEV)
                    except suds.WebFault:
                        # TODO, check why this exception appears only in CRUK
                        logging.debug("Move appears not to be completed.")
                    TimeUpdater.cancel()
                    # Take care of the calibration that takes place when we move to SEM
                    self._waitForDevice()
                elif p["pressure"] == PRESSURE_NAVCAM:
                    if self._pressure_device.GetInstrumentMode() != "INSTRUMENT-MODE-OPERATIONAL":
                        self._pressure_device.SetInstrumentMode("INSTRUMENT-MODE-OPERATIONAL")
                    # Typically we can now move to NavCam without waiting to wake up.
                    # We only open the channel in order to obtain the updates in
                    # waking up remaining time, assuming that eventually we will
                    # try to move to the SEM.
                    semmod = self._pressure_device.GetSEMDeviceMode()
                    if semmod not in ("SEM-MODE-BLANK", "SEM-MODE-IMAGING"):
                        self._wakeUp(future)
                    if future._move_state == CANCELLED:
                        raise CancelledError()
                    self._pressure_device.SelectImagingDevice(self._imagingDevice.NAVCAMIMDEV)
                    TimeUpdater.cancel()
                    # Wait for NavCam
                    self._waitForDevice()
                else:
                    self._pressure_device.UnloadSample()
                    TimeUpdater.cancel()
            except suds.WebFault:
                logging.warning("Acquisition in progress, cannot move to another state.", exc_info=True)
        self._chamber_event.set()
        # Wait for position to be updated
        self._position_event.wait()

    def _updateTime(self, future, target):
        try:
            remainingTime = self.parent._device.GetProgressAreaSelection().progress.timeRemaining
            area = self.parent._device.GetProgressAreaSelection().target
            if area == "LOADING-WORK-AREA-SEM":
                waiting_time = 6
            else:
                waiting_time = 0
            future.set_end_time(time.time() + self.wakeUpTime + remainingTime + waiting_time)
        except suds.WebFault:
            logging.warning("Time updater failed, cannot move to another state.", exc_info=True)

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
        # Make sure system is waking up
        self.parent._device.SetInstrumentMode("INSTRUMENT-MODE-OPERATIONAL")
        eventSpecArray = self.parent._objects.create('ns0:EventSpecArray')

        # Event for remaining time update
        eventID = "SEM-PROGRESS-DEVICE-MODE-CHANGED-ID"
        eventSpec = self.parent._objects.create('ns0:EventSpec')
        eventSpec.eventID = eventID
        eventSpec.compressed = False

        eventSpecArray.item = [eventSpec]
        ch_id = self._pressure_device.OpenEventChannel(eventSpecArray)

        while(True):
            if future._move_state == CANCELLED:
                break
            self.wakeUpTime = self._pressure_device.ReadEventChannel(ch_id)[0][0].SEMProgressDeviceModeChanged.timeRemaining
            logging.debug("Time to wake up: %f seconds", self.wakeUpTime)
            if self.wakeUpTime == 0:
                break
        self._pressure_device.CloseEventChannel(ch_id)
        # Wait before move
        time.sleep(1)

    def _waitForDevice(self):
        eventSpecArray = self.parent._objects.create('ns0:EventSpecArray')

        # Event for performed calibration
        eventID1 = "SEM-IMAGE-UPDATED-CHANGED-ID"
        eventSpec1 = self.parent._objects.create('ns0:EventSpec')
        eventSpec1.eventID = eventID1
        eventSpec1.compressed = False

        # Event for NavCam viewing mode
        eventID2 = "NAV-CAM-IMAGE-UPDATED-CHANGED-ID"
        eventSpec2 = self.parent._objects.create('ns0:EventSpec')
        eventSpec2.eventID = eventID2
        eventSpec2.compressed = False

        eventSpecArray.item = [eventSpec1, eventSpec2]
        ch_id = self._pressure_device.OpenEventChannel(eventSpecArray)

        api_frames = 0
        while(True):
            logging.debug("Device wait function about to read event...")
            expected_event = self._pressure_device.ReadEventChannel(ch_id)
            if expected_event == "":
                logging.debug("Event listener timeout")
            else:
                newEvent = expected_event[0][0].eventID
                logging.debug("Try to read event: %s", newEvent)
                if (newEvent == eventID1):
                    # Allow few phenom api acquisitions before you start acquiring
                    # via odemis. An alternative would be to wait for the second
                    # ACB performed by phenom API each time we load to SEM.
                    api_frames += 1
                    if api_frames >= 25:
                        break
                elif (newEvent == eventID2):
                    break
                else:
                    logging.warning("Unexpected event received")
        self._pressure_device.CloseEventChannel(ch_id)
        # Wait before allow acquisition
        time.sleep(1)

    def _chamber_move_thread(self):
        """
        Thread that listens to changes in Phenom chamber pressure.
        """
        eventSpecArray = self.parent._objects.create('ns0:EventSpecArray')

        # Event for performed sample holder move
        eventID1 = "PROGRESS-AREA-SELECTION-CHANGED-ID"
        eventSpec1 = self.parent._objects.create('ns0:EventSpec')
        eventSpec1.eventID = eventID1
        eventSpec1.compressed = False

        # Event for sample holder insertion
        eventID2 = "SAMPLEHOLDER-STATUS-CHANGED-ID"
        eventSpec2 = self.parent._objects.create('ns0:EventSpec')
        eventSpec2.eventID = eventID2
        eventSpec2.compressed = False

        eventSpecArray.item = [eventSpec1, eventSpec2]
        ch_id = self._chamber_device.OpenEventChannel(eventSpecArray)
        try:
            while not self._chamber_must_stop.is_set():
                logging.debug("Chamber move thread about to read event...")
                expected_event = self._pressure_device.ReadEventChannel(ch_id)
                if expected_event == "":
                    logging.debug("Event listener timeout")
                else:
                    newEvent = expected_event[0][0].eventID
                    logging.debug("Try to read event: %s", newEvent)
                    if (newEvent == eventID1):
                        try:
                            time_remaining = expected_event[0][0].ProgressAreaSelectionChanged.progress.timeRemaining
                            logging.debug("Time remaining to reach new chamber position: %f seconds", time_remaining)
                            if (time_remaining == 0):
                                # Move in progress is completed
                                self._move_event.set()
                                # Wait until any move performed by the user is completed
                                self._chamber_event.wait()
                                self._updatePosition()
                                self._position_event.set()
                            else:
                                self._move_event.clear()
                                self._position_event.clear()
                        except Exception:
                            logging.warning("Received event does not have the expected attribute or format")
                    elif (newEvent == eventID2):
                        logging.debug("Sample holder insertion, about to update sample holder id if needed")
                        self._updateSampleHolder()  # in case new sample holder was loaded
                    else:
                        logging.warning("Unexpected event received")
        except Exception as e:
            logging.exception("Unexpected failure during chamber pressure event listening. Lost connection to Phenom.")
            # Update the state of SEM component so the backend is aware of the error occured
            hw_error = HwError("Unexpected failure during chamber pressure event listening. Lost connection to Phenom.")
            self.parent.state._value = hw_error
            self.parent.state.notify(hw_error)
            # Keep on trying to reconnect
            target = self._reconnection_thread
            self._reconnect_thread = threading.Thread(target=target,
                    name="Phenom reconnection attempt")
            self._reconnect_thread.start()
        finally:
            self._chamber_device.CloseEventChannel(ch_id)
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
                        target = self._chamber_move_thread
                        self._chamber_thread = threading.Thread(target=target,
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
