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

from suds.client import Client
import Image
import base64
import urllib2
import os
import logging
import math
import numpy
from odemis import model, util
from odemis.dataio import hdf5
from odemis.util import img
from odemis.model import isasync
import os.path
import threading
import time
from random import randint
import weakref
import time
import Image
import re

# Fixed dwell time of Phenom SEM
DWELL_TIME = 1.92e-07  # s
#Fixed max number of frames per acquisition
MAX_FRAMES = 255

class PhenomSEM(model.HwComponent):
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
        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        # you can change the 'localhost' string and provide another SEM addres
        client = Client(host + "?om", location=host, username=username, password=password)
        self._device = client.service
        # Access to service objects
        self._objects = client.factory

        # Lock in order to synchronize all the child component functions
        # that acquire data from the SEM while we continuously acquire images
        self._acquisition_init_lock = threading.Lock()

        self._imagingDevice = self._objects.create('ns0:imagingDevice')

        self._metadata = {model.MD_HW_NAME: "PhenomSEM"}

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

#         # create the focus child
#         try:
#             kwargs = children["focus"]
#         except (KeyError, TypeError):
#             raise KeyError("PhenomSEM was not given a 'focus' child")
#         self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
#         self.children.add(self._focus)
#
#         # create the camera child
#         try:
#             kwargs = children["camera"]
#         except (KeyError, TypeError):
#             raise KeyError("PhenomSEM was not given a 'camera' child")
#         self._camera = ChamberView(parent=self, daemon=daemon, **kwargs)
#         self.children.add(self._camera)
#
#         # create the pressure child
#         try:
#             kwargs = children["pressure"]
#         except (KeyError, TypeError):
#             raise KeyError("PhenomSEM was not given a 'pressure' child")
#         self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
#         self.children.add(self._pressure)

    def updateMetadata(self, md):
        self._metadata.update(md)

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

        # Get current field of view and compute magnification
        fov = self.parent._device.GetSEMHFW()
        mag = self._hfw_nomag / fov

        self.magnification = model.VigilantAttribute(mag, unit="", readonly=True)

        range = self.parent._device.GetSEMHFWRange()
        fov_range = [range.min, range.max]
        self.horizontalFOV = model.FloatContinuous(fov, range=fov_range, unit="m",
                                                   setter=self._setHorizontalFOV)
        self.horizontalFOV.subscribe(self._onHorizontalFOV)  # to update metadata

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
        rot = parent._device.GetSEMRotation()
        rotation = numpy.deg2rad(rot)
        rot_range = (0, 2 * math.pi)
        self.rotation = model.FloatContinuous(rotation, rot_range, unit="rad")
        self.rotation.subscribe(self._onRotation)

        #Compute dwellTime range based on max number of frames and the fixed
        #phenom dwellTime        
        dt_range = [DWELL_TIME, DWELL_TIME * MAX_FRAMES]
        dt = DWELL_TIME
        # Corresponding nr of frames for initial DWELL_TIME
        self.nr_frames = 1
        self.dwellTime = model.FloatContinuous(dt, dt_range, unit="s")
        self.dwellTime.subscribe(self._onDwellTime)

        # Range is according to min and max voltages accepted by Phenom API
        range = parent._device.SEMGetHighTensionRange()
        volt_range = [-range.max, -range.min]
        volt = self.parent._device.SEMGetHighTension()
        self.accelVoltage = model.FloatContinuous(-volt, volt_range, unit="V")
        self.accelVoltage.subscribe(self._onVoltage)

        # 0 turns off the e-beam, 1 turns it on
        power_choices = set([0, 1])
        self._spotSize = self.parent._device.SEMGetSpotSize()
        # Don't change state
        if self._spotSize == 0:
            self._power = 0
        else:
            self._power = 1
        
        self.power = model.IntEnumerated(self._power, power_choices, unit="",
                                  setter=self._setPower)

        # Set maximum voltage just to get the min range of spot size (the
        # difference with max range is trivial, we just want to avoid out of
        # bounds when we reach the limits)
        parent._device.SEMSetHighTension(-volt_range[1])
        range = parent._device.SEMGetSpotSizeRange()
        parent._device.SEMSetHighTension(volt)
        # Convert A/sqrt(V) to just A
        pc_range = [(range.min * math.sqrt(volt_range[1])), (range.max * math.sqrt(volt_range[1]))]
        # Calculate current pc
        self._probeCurrent = self._spotSize * math.sqrt(-volt)
        self.probeCurrent = model.FloatContinuous(self._probeCurrent, pc_range, unit="A",
                                                  setter=self._setPC)

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def _onHorizontalFOV(self, s):
        # Update current pixelSize and magnification
        self._updatePixelSize()
        self._updateMagnification()

    def updateHorizontalFOV(self):
        with self.parent._acquisition_init_lock:
            new_fov = self.parent._device.GetSEMHFW()

        self.horizontalFOV.value = new_fov
        # Update current pixelSize and magnification
        self._updatePixelSize()
        self._updateMagnification()

    def _setHorizontalFOV(self, value):
        self.parent._device.SetSEMHFW(value)

#         # Ensure fov odemis field always shows the right value
#         # Also useful in case fov value that we try to set is
#         # out of range
#         with self.parent._acquisition_init_lock:
#             cur_fov = self.parent._device.GetSEMHFW()
#             value = cur_fov

        return value

    def _updateMagnification(self):

        # it's read-only, so we change it only via _value
        mag = self._hfw_nomag / self.horizontalFOV.value
        self.magnification._value = mag
        self.magnification.notify(mag)

    def _onDwellTime(self, dt):
        # Abort current scanning when dwell time is changed
        # self.parent._device.SEMAbortImageAcquisition()
        # Calculate number of frames
        self.nr_frames = int(math.ceil(dt / DWELL_TIME))

    def _onRotation(self, rot):
        # move = {'rz':rot}
        # self.parent._stage.moveAbs(move)
        pass

    def _onVoltage(self, volt):
        self.parent._device.SEMSetHighTension(-volt)
        # Brightness and contrast have to be adjusted just once
        # we set up the detector (see SEMACB())

    def _setPower(self, value):
        powers = self.power.choices

        self._power = util.find_closest(value, powers)
        if self._power == 0:
            self.parent._device.SEMSetSpotSize(0)
        else:
            volt = self.accelVoltage.value
            cur_spotSize = self._probeCurrent / math.sqrt(volt)
            self.parent._device.SEMSetSpotSize(cur_spotSize)
        return self._power

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
        Update the pixel size using the scale and FOV
        """
        fov = self.horizontalFOV.value

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
          the whole ROI fits the screen.
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
    def __init__(self, name, role, parent, channel, **kwargs):
        """
        Note: parent should have a child "scanner" already initialised
        """
        # It will set up ._shape and .parent
        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self.acq_shape = self.parent._scanner._shape

        # setup detector
        self._scanParams = self.parent._objects.create('ns0:scanParams')
        self._detectorMode = self.parent._objects.create('ns0:detector')
        # use all detector segments
        detectorMode = 'SEM-DETECTOR-MODE-ALL'
        self._scanParams.detector = detectorMode
        #always acquire to the center of FOV
        self._scanParams.center.x = 0
        self._scanParams.center.y = 0
        self._scanParams.scale = 1

        # adjust brightness and contrast
        self.parent._device.SEMACB()

        self.data = SEMDataFlow(self, parent)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # The shape is just one point, the depth
        self._shape = (2 ** 16,)  # only one point

    def start_acquire(self, callback):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            target = self._acquire_thread
            self._acquisition_thread = threading.Thread(target=target,
                    name="PhenomSEM acquire flow thread",
                    args=(callback,))
            self._acquisition_thread.start()

    def stop_acquire(self):
        with self._acquisition_lock:
            self._acquisition_must_stop.set()

    def _wait_acquisition_stopped(self):
        """
        Waits until the acquisition thread is fully finished _iff_ it was requested
        to stop.
        """
        # "if" is to not wait if it's already finished
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
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

        with self.parent._acquisition_init_lock:
            pxs = self.parent._scanner.pixelSize.value  # m/px

            pxs_pos = self.parent._scanner.translation.value
            scale = self.parent._scanner.scale.value
            res = (self.parent._scanner.resolution.value[0],
                   self.parent._scanner.resolution.value[1])

            metadata = dict(self.parent._metadata)
            phy_pos = metadata.get(model.MD_POS, (0, 0))
            trans = self.parent._scanner.pixelToPhy(pxs_pos)
            updated_phy_pos = (phy_pos[0] + trans[0], phy_pos[1] + trans[1])

            # update changed metadata
            metadata[model.MD_POS] = updated_phy_pos
            metadata[model.MD_PIXEL_SIZE] = (pxs[0] * scale[0], pxs[1] * scale[1])
            metadata[model.MD_ACQ_DATE] = time.time()
            metadata[model.MD_ROTATION] = self.parent._scanner.rotation.value,
            metadata[model.MD_DWELL_TIME] = self.parent._scanner.dwellTime.value

            scaled_shape = (self.acq_shape[0] / scale[0], self.acq_shape[1] / scale[1])
            center = (scaled_shape[0] / 2, scaled_shape[1] / 2)
            l = int(center[0] + pxs_pos[1] - (res[0] / 2))
            t = int(center[1] + pxs_pos[0] - (res[1] / 2))
            r = l + res[0] - 1
            b = t + res[1] - 1

            dt = self.parent._scanner.dwellTime.value
            self._scanParams.resolution.height = res[0]
            self._scanParams.resolution.width = res[1]
            self._scanParams.nrOfFrames = self.parent._scanner.nr_frames
            self._scanParams.HDR = True  # 16 bits
            self._scanParams.center.x = pxs_pos[0]
            self._scanParams.center.y = pxs_pos[1]
            img_str = self.parent._device.SEMAcquireImageCopy(self._scanParams)

            # image to ndarray
            sem_img = numpy.frombuffer(base64.b64decode(img_str.image.buffer[0]), dtype="uint16")
            sem_img.shape = res

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
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

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
        self._stagePos = self.parent._objects.create('ns0:position')
        self._navAlgorithm = self.parent._objects.create('ns0:navigationAlgorithm')
        self._navAlgorithm = 'NAVIGATION-AUTO'

        rng = [-0.5, 0.5]
        axes_def["x"] = model.Axis(unit="m", range=rng)
        axes_def["y"] = model.Axis(unit="m", range=rng)
        rng_rot = [0, 2 * math.pi]
        axes_def["rz"] = model.Axis(unit="rad", range=rng_rot)

        # First calibrate
        calib_pos = parent._device.GetStageCenterCalib()
        if calib_pos.position.x != 0 or calib_pos.position.y != 0:
            logging.warning("Stage was not calibrated. We are performing calibration now.")
            self._stagePos.x, self._stagePos.y = 0, 0
            parent._device.SetStageCenterCalib(self._stagePos)

        mode_pos = parent._device.GetStageModeAndPosition()
        self._position["x"] = mode_pos.position.x
        self._position["y"] = mode_pos.position.y

        # degrees to rad
        rot = parent._device.GetSEMRotation()
        self._position["rz"] = numpy.deg2rad(rot)

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
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    def _doMove(self, pos):
        """
        move to the position 
        """
        # Perform move through Tescan API
        # Position from m to mm and inverted
        self._stagePos.x, self._stagePos.y = pos["x"], pos["y"]
        self.parent._device.MoveTo(self._stagePos, self._navAlgorithm)
        self.parent._device.SetSEMRotation(numpy.rad2deg(pos["rz"]))

        # Obtain the finally reached position after move is performed.
        # This is mainly in order to keep the correct position in case the
        # move we tried to perform was greater than the maximum possible
        # one.
        with self.parent._acquisition_init_lock:
            mode_pos = self.parent._device.GetStageModeAndPosition()
            self._position["x"] = mode_pos.position.x
            self._position["y"] = mode_pos.position.y
            rot = self.parent._device.GetSEMRotation()
            self._position["rz"] = numpy.deg2rad(rot)

        self._updatePosition()

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        shift = self._applyInversionRel(shift)

        for axis, change in shift.items():
            self._position[axis] += change

        pos = self._position
        return self._executor.submit(self._doMove, pos)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionAbs(pos)

        for axis, new_pos in pos.items():
            self._position[axis] = new_pos

        pos = self._position
        return self._executor.submit(self._doMove, pos)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None
