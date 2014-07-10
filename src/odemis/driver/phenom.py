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

from abc import abstractmethod
import base64
import collections
import functools
import logging
import math
import numpy
from odemis import model, util
from odemis.model import isasync
from odemis.model._futures import CancellableThreadPoolExecutor
import suds
from suds.client import Client
import threading
import time
import weakref

# Fixed dwell time of Phenom SEM
DWELL_TIME = 1.92e-07  # s
# Fixed max number of frames per acquisition
MAX_FRAMES = 255
SOCKET_TIMEOUT = 1e03  # timeout for suds client
TILT_BLANK = (-1, -1)  # tilt to imitate beam blanking

# SEM ranges in order to allow scanner initialization even if Phenom is in
# unloaded state
HFW_RANGE = [2.5e-06, 0.003]
TENSION_RANGE = [4797.56, 20006.84]
SPOT_RANGE = [0.0, 5.73018379531]
NAVCAM_PIXELSIZE = (1.3267543859649122e-05, 1.3267543859649122e-05)

class SEM(model.HwComponent):
    '''
    This is an extension of the model.HwComponent class. It instantiates the scanner 
    and se-detector children components and provides an update function for its 
    metadata. 
    '''

    def __init__(self, name, role, children, host, username, password, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner" and "detector"
            They will be provided back in the .children roattribute
        Raise an exception if the device cannot be opened
        '''

        # TODO: only reduce the logging level for suds
        # Avoid unnecessary logging from suds.client
        logging.getLogger().setLevel(logging.INFO)

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        # you can change the 'localhost' string and provide another SEM addres
        client = Client(host + "?om", location=host, username=username, password=password, timeout=SOCKET_TIMEOUT)
        self._device = client.service
        # Access to service objects
        self._objects = client.factory

        # Lock in order to synchronize all the child component functions
        # that acquire data from the SEM while we continuously acquire images
        self._acq_progress_lock = threading.Lock()

        self._imagingDevice = self._objects.create('ns0:imagingDevice')

        self._metadata = {model.MD_HW_NAME: "PhenomSEM"}
        info = self._device.VersionInfo().versionInfo
        start = info.index("'Version'>") + len("'Version'>")
        end = info.index("</Property", start)
        self._swVersion = "SEM sw %s" % (info[start:end])
        self._metadata[model.MD_SW_VERSION] = self._swVersion

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._scanner)

        # create the detector child
        try:
            kwargs = children["detector"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'detector' child")
        self._detector = Detector(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._detector)

        # create the stage child
        try:
            kwargs = children["stage"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'stage' child")
        self._stage = Stage(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._stage)

        # create the focus child
        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'focus' child")
        self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._focus)

        # create the camera child
        try:
            kwargs = children["camera"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'camera' child")
        self._camera = NavCam(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._camera)

        # create the NavCam focus child
        try:
            kwargs = children["navcam-focus"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'navcam-focus' child")
        self._navcam_focus = NavCamFocus(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._navcam_focus)

        # create the pressure child
        try:
            kwargs = children["pressure"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'pressure' child")
        self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._pressure)

    def updateMetadata(self, md):
        self._metadata.update(md)

    def getMetadata(self):
        return self._metadata

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterward.
        """
        # Don't need to close the connection, it's already closed by the time
        # suds returns the data
        pass

class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant 
    Attributes and setters for magnification, pixel size, translation, resolution,
    scale, rotation and dwell time. Whenever one of these attributes is changed, 
    its setter also updates another value if needed e.g. when scale is changed, 
    resolution is updated, when resolution is changed, the translation is recentered 
    etc. Similarly it subscribes to the VAs of scale and magnification in order 
    to update the pixel size.
    """
    def __init__(self, name, role, parent, **kwargs):
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self._shape = (2048, 2048)

        # Distance between borders if magnification = 1. It should be found out
        # via calibration. We assume that image is square, i.e., VFW = HFW
        self._hfw_nomag = 0.268128  # m

        # Just the initialization of the FoV. The actual value will be acquired
        # once we start the stream
        fov = numpy.mean(HFW_RANGE)
        mag = self._hfw_nomag / fov

        self.magnification = model.VigilantAttribute(mag, unit="", readonly=True)
        fov_range = HFW_RANGE
        self.horizontalFoV = model.FloatContinuous(fov, range=fov_range, unit="m",
                                                   setter=self._setHorizontalFoV)
        self.horizontalFoV.subscribe(self._onHorizontalFoV)  # to update metadata

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = (self._hfw_nomag / (self._shape[0] * mag),
               self._hfw_nomag / (self._shape[1] * mag))
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

        # (.resolution), .translation, .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in px => moves center of acquisition by this amount
        # independent of scale and rotation.
        tran_rng = [(-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2)]
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                              cls=(int, long, float), unit="",
                                              setter=self._setTranslation)

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

        # (float) in rad => rotation of the image compared to the original axes
        # Just the initialization of rotation. The actual value will be acquired
        # once we start the stream
        rotation = 0
        rot_range = (0, 2 * math.pi)
        self.rotation = model.FloatContinuous(rotation, rot_range, unit="rad")
        self.rotation.subscribe(self._onRotation)

        # Compute dwellTime range based on max number of frames and the fixed
        # phenom dwellTime
        dt_range = [DWELL_TIME, DWELL_TIME * MAX_FRAMES]
        dt = DWELL_TIME
        # Corresponding nr of frames for initial DWELL_TIME
        self._nr_frames = 1
        self.dwellTime = model.FloatContinuous(dt, dt_range, unit="s")
        self.dwellTime.subscribe(self._onDwellTime)

        # Range is according to min and max voltages accepted by Phenom API
        volt_range = TENSION_RANGE
        # Just the initialization of voltage. The actual value will be acquired
        # once we start the stream
        volt = numpy.mean(TENSION_RANGE)
        self.accelVoltage = model.FloatContinuous(volt, volt_range, unit="V")
        self.accelVoltage.subscribe(self._onVoltage)

        # 16 or 8 bits image
        self.bpp = model.IntEnumerated(16, set([8, 16]),
                                          unit="", setter=self._setBpp)

        range = SPOT_RANGE
        # Convert A/sqrt(V) to just A
        pc_range = [(range[0] * math.sqrt(volt_range[1])), (range[1] * math.sqrt(volt_range[1]))]
        self._spotSize = numpy.mean(SPOT_RANGE)
        self._probeCurrent = self._spotSize * math.sqrt(volt)
        self.probeCurrent = model.FloatContinuous(self._probeCurrent, pc_range, unit="A",
                                                  setter=self._setPC)

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def getMetadata(self):
        return self.parent.getMetadata()

    def _onHorizontalFoV(self, s):
        # Update current pixelSize and magnification
        self._updatePixelSize()
        self._updateMagnification()

    def _setHorizontalFoV(self, value):
        #Make sure you are in the current range
        rng = self.parent._device.GetSEMHFWRange()
        new_fov = numpy.clip(value, rng.min, rng.max)
        self.parent._device.SetSEMHFW(new_fov)

        return new_fov

    def _updateMagnification(self):

        # it's read-only, so we change it only via _value
        mag = self._hfw_nomag / self.horizontalFoV.value
        self.magnification._value = mag
        self.magnification.notify(mag)

    def _onDwellTime(self, dt):
        # Abort current scanning when dwell time is changed
        try:
            self.parent._device.SEMAbortImageAcquisition()
        except suds.WebFault:
            logging.debug("No acquisition in progress to be aborted.")

        # Calculate number of frames
        self._nr_frames = int(math.ceil(dt / DWELL_TIME))

    def _onRotation(self, rot):
        with self.parent._acq_progress_lock:
            self.parent._device.SetSEMRotation(rot)
        pass

    def _onVoltage(self, volt):
        self.parent._device.SEMSetHighTension(-volt)
        # Brightness and contrast have to be adjusted just once
        # we set up the detector (see SEMACB())

    def _setBpp(self, value):
        return value

    def _setPC(self, value):
        # Set the corresponding spot size to Phenom SEM
        self._probeCurrent = value
        volt = self.accelVoltage.value
        new_spotSize = value / math.sqrt(volt)
        self.parent._device.SEMSetSpotSize(new_spotSize)

        return self._probeCurrent

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
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setScale(self, value):
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
         the original pixel size. It will adapt the translation and resolution to
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
        # no need to update translation, as it's independent of scale and will
        # be checked by setting the resolution.
        self.resolution.value = new_resolution  # will call _setResolution()

        return value

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the 
         resolution is not possible, it will pick the most fitting one. It will
         recenter the translation if otherwise it would be out of the whole
         scanned area.
        returns the actual value used
        """
        max_size = (int(self._shape[0] // self._scale[0]),
                    int(self._shape[1] // self._scale[1]))

        # at least one pixel, and at most the whole area
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size

        # setting the same value means it will recheck the boundaries with the
        # new resolution, and reduce the distance to the center if necessary.
        self.translation.value = self.translation.value
        return size

    def _setTranslation(self, value):
        """
        value (float, float): shift from the center. It will always ensure that
          the whole ROI fitself.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)s the screen.
        returns actual shift accepted
        """
        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the scaling.
        max_tran = ((self._shape[0] - self._resolution[0] * self._scale[0]) / 2,
                    (self._shape[1] - self._resolution[1] * self._scale[1]) / 2)

        # between -margin and +margin
        tran = (max(min(value[0], max_tran[0]), -max_tran[0]),
                max(min(value[1], max_tran[1]), -max_tran[1]))
        return tran

    def pixelToPhy(self, px_pos):
        """
        Converts a position in pixels to physical (at the current magnification)
        Note: the convention is that in internal coordinates Y goes down, while
        in physical coordinates, Y goes up.
        px_pos (tuple of 2 floats): position in internal coordinates (pixels)
        returns (tuple of 2 floats): physical position in meters 
        """
        pxs = self.pixelSize.value  # m/px
        phy_pos = (px_pos[0] * pxs[0], -px_pos[1] * pxs[1])  # - to invert Y
        return phy_pos

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

        # setup detector
        self._scanParams = self.parent._objects.create('ns0:scanParams')
        # use all detector segments
        detectorMode = 'SEM-DETECTOR-MODE-ALL'
        self._scanParams.detector = detectorMode
        # always acquire to the center of FOV
        self._scanParams.center.x = 0
        self._scanParams.center.y = 0
        self._scanParams.scale = 1

        # adjust brightness and contrast
        # self.parent._device.SEMACB()

        self.data = SEMDataFlow(self, parent)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # The shape is just one point, the depth
        self._shape = (2 ** 16 - 1,)  # only one point

        # Get current tilt and use it to unblank the beam
        self._tilt_unblank = self.parent._device.GetSEMSourceTilt()

    def start_acquire(self, callback):
        # Update stage position
        self.parent._stage._updatePosition()

        # Update all the Scanner VAs upon stream start
        # Get current field of view and compute magnification
        fov = self.parent._device.GetSEMHFW()
        self.parent._scanner.horizontalFoV.value = fov

        rotation = self.parent._device.GetSEMRotation()
        self.parent._scanner.rotation.value = rotation

        volt = self.parent._device.SEMGetHighTension()
        self.parent._scanner.accelVoltage.value = -volt

        # Calculate current pc
        self.parent._scanner._spotSize = self.parent._device.SEMGetSpotSize()
        self.parent._scanner._probeCurrent = self.parent._scanner._spotSize * math.sqrt(-volt)
        self.parent._scanner.probeCurrent.value = self.parent._scanner._probeCurrent


        # Check if Phenom is in the proper mode
        area = self.parent._device.GetProgressAreaSelection().target
        if area != "LOADING-WORK-AREA-SEM":
            raise IOError("Cannot initiate stream, Phenom is not in SEM mode.")

        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            try:
                # "Unblank" the beam
                self.parent._device.SetSEMSourceTilt(self._tilt_unblank[0], self._tilt_unblank[1], False)
            except suds.WebFault:
                logging.warning("Beam might still be blanked!")
            target = self._acquire_thread
            self._acquisition_thread = threading.Thread(target=target,
                    name="PhenomSEM acquire flow thread",
                    args=(callback,))
            self._acquisition_thread.start()

    def stop_acquire(self):
        with self._acquisition_lock:
            try:
                self.parent._device.SEMAbortImageAcquisition()
                # "Blank" the beam
                self.parent._device.SetSEMSourceTilt(TILT_BLANK[0], TILT_BLANK[1], False)
            except suds.WebFault:
                logging.debug("No acquisition in progress to be aborted.")
            self._acquisition_must_stop.set()

    def _wait_acquisition_stopped(self):
        """
        Waits until the acquisition thread is fully finished _iff_ it was requested
        to stop.
        """
        # "if" is to not wait if it's already finished
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
            if not self._acquisition_thread is None:
                self._acquisition_thread.join(10)  # 10s timeout for safety
                if self._acquisition_thread.isAlive():
                    logging.exception("Failed to stop the acquisition thread")
                    # Now let's hope everything is back to normal...
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    def _acquire_image(self):
        """
        Acquires the SEM image based on the translation, resolution and
        current drift.
        """
        pxs = self.parent._scanner.pixelSize.value  # m/px
        scale = self.parent._scanner.scale.value
        res = (self.parent._scanner.resolution.value[0],
               self.parent._scanner.resolution.value[1])

        # Set dataType based on current bpp value
        bpp = self.parent._scanner.bpp.value
        if bpp == 16:
            dataType = numpy.uint16
        else:
            dataType = numpy.uint8

        metadata = dict(self.parent._metadata)

        # update changed metadata
        metadata[model.MD_PIXEL_SIZE] = (pxs[0] * scale[0], pxs[1] * scale[1])
        metadata[model.MD_ACQ_DATE] = time.time()
        metadata[model.MD_ROTATION] = self.parent._scanner.rotation.value
        metadata[model.MD_DWELL_TIME] = self.parent._scanner.dwellTime.value
        metadata[model.MD_BPP] = bpp

        self._scanParams.resolution.width = res[0]
        self._scanParams.resolution.height = res[1]
        self._scanParams.nrOfFrames = self.parent._scanner._nr_frames
        self._scanParams.HDR = bpp == 16
        # TODO beam shift
        self._scanParams.center.x = 0
        self._scanParams.center.y = 0

        with self.parent._acq_progress_lock:
            # Check if spot mode is required
            if res == (1, 1):
                # FIXME
                # Avoid setting resolution to 1,1
                self._scanParams.resolution.width = 512
                self._scanParams.resolution.height = 512
                self.parent._device.SetSEMViewingMode(self._scanParams, 'SEM-SCAN-MODE-SPOT')
                # MD_POS is hopefully set via updateMetadata
                return model.DataArray(numpy.array([], dtype=dataType), metadata)
            else:
                self.parent._device.SetSEMViewingMode(self._scanParams, 'SEM-SCAN-MODE-IMAGING')
                img_str = self.parent._device.SEMAcquireImageCopy(self._scanParams)
                # get MD_POS from metadata returned from Phenom
                metadata[model.MD_POS] = (img_str.aAcqState.position.x, img_str.aAcqState.position.y)
                # image to ndarray
                sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]),
                                           dtype=dataType)
                sem_img.shape = res[::-1]
                return model.DataArray(sem_img, metadata)

    def _acquire_thread(self, callback):
        """
        Thread that performs the SEM acquisition. It calculates and updates the
        center (e-beam) position based on the translation and provides the new 
        generated output to the Dataflow. 
        """
        try:
            while not self._acquisition_must_stop.is_set():
                callback(self._acquire_image())
        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            # "Blank" the beam
            self.parent._device.SetSEMSourceTilt(TILT_BLANK[0], TILT_BLANK[1], False)
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def getMetadata(self):
        return self.parent.getMetadata()

    def terminate(self):
        logging.info("Terminating SEM stream...")
        try:
            # "Unblank" the beam
            self.parent._device.SetSEMSourceTilt(self._tilt_unblank[0], self._tilt_unblank[1], False)
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
        axes_def = {}
        self._position = {}

        # Position phenom object
        self._stagePos = parent._objects.create('ns0:position')
        self._stageRel = parent._objects.create('ns0:position')
        self._navAlgorithm = parent._objects.create('ns0:navigationAlgorithm')
        self._navAlgorithm = 'NAVIGATION-AUTO'

        rng = [-0.5, 0.5]
        axes_def["x"] = model.Axis(unit="m", range=rng)
        axes_def["y"] = model.Axis(unit="m", range=rng)

        # TODO, may be needed in case setting a referencial point is required
        # First calibrate
#         calib_pos = parent._device.GetStageCenterCalib()
#         if calib_pos.x != 0 or calib_pos.y != 0:
#             logging.warning("Stage was not calibrated. We are performing calibration now.")
#             self._stagePos.x, self._stagePos.y = 0, 0
#             parent._device.SetStageCenterCalib(self._stagePos)

        # Just initialization, actual position updated once stage is moved
        self._position["x"] = 0
        self._position["y"] = 0

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

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
            next_pos = {}
            for axis, new_pos in pos.items():
                next_pos[axis] = new_pos
            self._stagePos.x, self._stagePos.y = next_pos["x"], next_pos["y"]
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
            rel = {}
            for axis, change in shift.items():
                rel[axis] = change
            self._stageRel.x, self._stageRel.y = rel["x"], rel["y"]
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
    def __init__(self, name, role, parent, axes, rng, pos, **kwargs):
        assert len(axes) > 0
        axes_def = {}

        # Just z axis
        a = axes[0]
        axes_def[a] = model.Axis(unit="m", range=rng)

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({},
                                    unit="m", readonly=True)

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
            logging.info("Blocked by the acquisition...")
            with self.parent._acq_progress_lock:
                wd = self.GetWD()
                while True:
                    try:
                        typ, mov = self._moves_queue.popleft()
                    except IndexError:
                        break
                    if typ == "moveRel":
                        wd += mov["z"]
                    else:
                        wd = mov["z"]
                self.SetWD(wd)
                self._updatePosition()

    @isasync
    def moveRel(self, shift):
        logging.info("Focus relative move...")
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversionRel(shift)
        logging.info("Submit relative move...")
        self._moves_queue.append(("moveRel", shift))
        return self._executor.submit(self._checkQueue)

    @isasync
    def moveAbs(self, pos):
        logging.info("Focus absolute move...")
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionAbs(pos)
        logging.info("Submit absolute move...")
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
        range = parent._device.GetSEMWDRange()
        rng = [range.min, range.max]
        pos = parent._device.GetSEMWD()

        PhenomFocus.__init__(self, name, role, parent=parent, axes=axes, rng=rng,
                             pos=pos, **kwargs)

    def _updatePosition(self):
        """
        update the position VA
        """
        super(EbeamFocus, self)._updatePosition()

        # Changing WD results to change in fov
        self.parent._scanner.horizontalFoV._value = self.parent._device.GetSEMHFW()

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
    def __init__(self, name, role, parent, **kwargs):
        """
        Initialises the device.
        Raise an exception if the device cannot be opened.
        """
        model.DigitalCamera.__init__(self, name, role, parent=parent, **kwargs)

        resolution = NAVCAM_RESOLUTION
        # RGB
        self._shape = resolution + (3, 2 ** 8 - 1)
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
            raise IOError("Cannot initiate stream, Phenom is not in NAVCAM mode.")

        # if there is a very quick unsubscribe(), subscribe(), the previous
        # thread might still be running
        self.wait_stopped_flow()  # no-op is the thread is not running
        self.acquisition_lock.acquire()

        target = self._acquire_thread_continuous
        self.acquire_thread = threading.Thread(target=target,
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
            while not self.acquire_must_stop.is_set():
                with self.parent._acq_progress_lock:
                    try:
                        img_str = self.parent._device.NavCamAcquireImageCopy(self._camParams)
                    except suds.WebFault as e:
                        if e.message == NAVCAM_LOCKED_MSG:
                            logging.warning("NavCam firmware has locked up. Please power cycle Phenom.")
                        else:
                            logging.debug("NavCam acquisition failed.")

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

        except:
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
        range = parent._device.GetNavCamWDRange()
        rng = [range.min, range.max]
        pos = parent._device.GetNavCamWD()

        PhenomFocus.__init__(self, name, role, parent=parent, axes=axes, rng=rng,
                             pos=pos, **kwargs)

    def GetWD(self):
        return self.parent._device.GetNavCamWD()

    def SetWD(self, wd):
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
        axes = {"pressure": model.Axis(unit="",
                                       choices={PRESSURE_UNLOADED: "unloaded",
                                                PRESSURE_NAVCAM: "overview",
                                                PRESSURE_SEM: "SEM"})}
        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)
        self._imagingDevice = self.parent._objects.create('ns0:imagingDevice')

        # Handle the cases of stand-by and hibernate mode
        mode = self.parent._device.GetInstrumentMode()
        if mode == 'INSTRUMENT-MODE-HIBERNATE' or mode == 'INSTRUMENT-MODE-STANDBY':
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

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # Tuple containing sample holder ID and type
        self.sampleHolder = model.TupleVA((0, 0), unit="", readonly=True)
        self._updateSampleHolder()


    def _updatePosition(self):
        """
        update the position VA and .pressure VA
        """
        area = self.parent._device.GetProgressAreaSelection().target  # last official position
        if area == "LOADING-WORK-AREA-SEM":
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
        update the sampleHolder VA 
        """
        # TODO: set to (None, None) if not sample holder in?
        # Convert base64 to long int
        s = base64.decodestring(self.parent._device.GetSampleHolder().holderID.id[0])
        holderID = reduce(lambda a, n: (a << 8) + n, (ord(v) for v in s), 0)
        self._sampleHolder = (holderID,
                              self.parent._device.GetSampleHolder().holderType)

        self.sampleHolder._value = self._sampleHolder
        self.sampleHolder.notify(self.sampleHolder.value)

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
        f._move_lock = threading.Lock()

        return self._executor.submitf(f, self._changePressure, f, pos)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

    def _estimateMoveTime(self):
        """
        Estimates move procedure duration
        """
        # Just an indicative time. It will be updated by polling the remaining
        # time.
        timeRemaining = 15
        return timeRemaining  # s

    def _changePressure(self, future, p):
        """
        Change of the pressure
        p (float): target pressure
        """
        with self.parent._acq_progress_lock:
            # Keep remaining time up to date
            updater = functools.partial(self._updateTime, future, p)
            TimeUpdater = util.RepeatingTimer(1, updater, "Pressure time updater")
            TimeUpdater.start()

            try:
                if p["pressure"] == PRESSURE_SEM:
                    self.parent._device.SelectImagingDevice(self._imagingDevice.SEMIMDEV)
                elif p["pressure"] == PRESSURE_NAVCAM:
                    if self.parent._device.GetInstrumentMode() != "INSTRUMENT-MODE-OPERATIONAL":
                        self.parent._device.SetInstrumentMode("INSTRUMENT-MODE-OPERATIONAL")
                    self._updateSampleHolder()  # in case new sample holder was loaded
                    self.parent._device.SelectImagingDevice(self._imagingDevice.NAVCAMIMDEV)
                else:
                    self.parent._device.UnloadSample()
            except suds.WebFault:
                raise IOError("Acquisition in progress, cannot move to another state.")

            # FIXME
            # Enough time before we start an acquisition
            time.sleep(7)
            self._updatePosition()
            TimeUpdater.cancel()

    def _updateTime(self, future, target):
        remainingTime = self.parent._device.GetProgressAreaSelection().progress.timeRemaining
        future.set_end_time(time.time() + remainingTime)
