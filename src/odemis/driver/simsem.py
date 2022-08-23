# -*- coding: utf-8 -*-
'''
Created on 31 Jan 2014

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

from builtins import str
import queue
from past.builtins import long
import logging
import math
import numpy
from odemis import model, util, dataio
from odemis.model import isasync, oneway
from odemis.util import img
import os
import random
from scipy import ndimage
import threading
import time
import weakref


class SimSEM(model.HwComponent):
    '''
    This is an extension of the model.HwComponent class. It first reads and
    keeps the image that is used and manipulated in order to generate the fake output.
    This is a high resolution (2048x2048) SEM image. It then instantiates the scanner
    and se-detector children components and provides an update function for its metadata.
    '''

    def __init__(self, name, role, children, image=None, drift_period=None,
                 daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner", "detector0", and the optional "focus"
            They will be provided back in the .children VA
        image (str or None): path to a file to use as fake image (relative to
         the directory of this class)
        drift_period (None or 0<float): time period for drift updating in seconds
        Raise an exception if the device cannot be opened
        '''
        # fake image setup
        if image is None:
            image = u"simsem-fake-output.h5"
        image = str(image)
        # ensure relative path is from this file
        if not os.path.isabs(image):
            image = os.path.join(os.path.dirname(__file__), image)
        converter = dataio.find_fittest_converter(image, mode=os.O_RDONLY)
        self.fake_img = img.ensure2DImage(converter.read_data(image)[0])

        self._drift_period = drift_period

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        self._metadata[model.MD_HW_NAME] = "FakeSEM"

        # create the scanner child
        try:
            ckwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("SimSEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **ckwargs)
        self.children.value.add(self._scanner)

        # create the detector children
        self._detectors = []
        for c, ckwargs in children.items():
            if c.startswith("detector"):
                self._detectors.append(Detector(parent=self, daemon=daemon, **ckwargs))

        if not self._detectors:
            raise KeyError("SimSEM was not given a 'detector0' child")
        self.children.value.update(set(self._detectors))

        try:
            ckwargs = children["focus"]
        except (KeyError, TypeError):
            logging.info("Will not simulate focus")
            self._focus = None
        else:
            self._focus = EbeamFocus(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._focus)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        for d in self._detectors:
            d.terminate()


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
    def __init__(self, name, role, parent, aperture=100e-6, wd=10e-3, **kwargs):
        """
        aperture (0 < float): aperture diameter of the electron lens
        wd (0 < float): working distance
        """
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)
        self._aperture = aperture
        self._working_distance = wd

        fake_img = self.parent.fake_img
        if parent._drift_period:
            # half the size, to keep some margin for the drift
            self._shape = tuple(v // 2 for v in fake_img.shape[::-1])
        else:
            self._shape = fake_img.shape[::-1]

        # next two values are just to determine the pixel size
        # Distance between borders if magnification = 1. It should be found out
        # via calibration. We assume that image is square, i.e., VFV = HFV
        self._hfw_nomag = 0.25  # m

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = fake_img.metadata[model.MD_PIXEL_SIZE]
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

        # the horizontalFoV VA indicates that it's possible to control the zoom
        hfv = pxs[0] * self._shape[0]
        self.horizontalFoV = model.FloatContinuous(hfv, range=[10e-9, 10e-3],
                                                   unit="m")
        self.magnification = model.VigilantAttribute(self._hfw_nomag / hfv,
                                                     unit="", readonly=True)
        self.horizontalFoV.subscribe(self._onHFV)

        # To provide some rough idea of the step size when changing focus
        # Depends on the pixelSize, so will be updated whenever the HFW changes
        self.depthOfField = model.FloatContinuous(1e-6, range=(0, 1e9),
                                                  unit="m", readonly=True)
        self._updateDepthOfField()  # needs .pixelSize

        # (.resolution), .translation, .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in m => physically moves the e-beam.
        shift_rng = ((-50e-06, -50e-06),
                    (50e-06, 50e-06))
        self.shift = model.TupleContinuous((0, 0), shift_rng,
                                              cls=(int, long, float), unit="m")

        # (float, float) in m => moves center of acquisition by this amount
        # independent of scale and rotation.
        tran_rng = [(-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2)]
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                              cls=(int, long, float), unit="px",
                                              setter=self._setTranslation)

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        resolution = (self._shape[0] // 4, self._shape[1] // 4)
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
        self.scale.subscribe(self._onScale, init=True) # to update metadata

        # (float) in rad => rotation of the image compared to the original axes
        self.rotation = model.FloatContinuous(0, [0, 2 * math.pi], unit="rad")

        self.dwellTime = model.FloatContinuous(1e-06, (1e-06, 1000), unit="s")

        # VAs to control the ebeam, purely fake
        self.probeCurrent = model.FloatEnumerated(1.3e-9,
                          {0.1e-9, 1.3e-9, 2.6e-9, 3.4e-9, 11.564e-9, 23e-9},
                          unit="A")
        self.accelVoltage = model.FloatContinuous(10e3, (1e3, 30e3), unit="V")

        # Pretend it's ready to acquire an image
        self.power = model.BooleanVA(True)
        # Blanker has a None = "auto" mode which automatically blanks when not scanning
        self.blanker = model.VAEnumerated(None, choices={True: 'blanked', False: 'unblanked', None: 'auto'})

    def _onHFV(self, hfv):
        self._updatePixelSize()
        self._updateDepthOfField()

    def _onScale(self, s):
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the scale, and horizontalFoV.
        Also updates magnification, using HFWNoMag
        """
        hfv = self.horizontalFoV.value
        pxs = (hfv / self._shape[0], hfv / self._shape[0]) # always square

        # it's read-only, so we change it only via _value
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

        # magnification
        mag = self._hfw_nomag / hfv
        self.magnification._value = mag
        self.magnification.notify(mag)
        self.parent._metadata[model.MD_LENS_MAG] = mag

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
        dof = 2 * pxs / (self._aperture / (2 * self._working_distance))
        self.depthOfField._set_value(dof, force_write=True)

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
        max_size = (int(self._shape[0] / self._scale[0]),
                    int(self._shape[1] / self._scale[1]))

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
    of the fake SEM. It sets up a Dataflow and notifies it every time that a fake
    SEM image is generated. It also keeps and updates a “drift vector”
    """
    def __init__(self, name, role, parent, **kwargs):
        """
        Note: parent should have a child "scanner" already initialised
        """
        # It will set up ._shape and .parent
        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self.data = SEMDataFlow(self, parent)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        self.fake_img = self.parent.fake_img
        # The shape is just one point, the depth
        idt = numpy.iinfo(self.fake_img.dtype)
        data_depth = idt.max - idt.min + 1
        self._shape = (data_depth,) # only one point

        # 8 or 16 bits image
        if data_depth == 255:
            bpp = 8
        else:
            bpp = 16
        self.bpp = model.IntEnumerated(bpp, {8, 16})

        # Simulate the Hw brightness/contrast, but don't actually do anything
        self.contrast = model.FloatContinuous(0.5, [0, 1], unit="")
        self.brightness = model.FloatContinuous(0.5, [0, 1], unit="")

        self.drift_factor = 2  # dummy value for drift in pixels
        self.current_drift = 0
        # Given that max resolution is half the shape of fake_img,
        # we set the drift bound to stay inside the fake_img bounds
        self.drift_bound = min(v // 4 for v in self.fake_img.shape[::-1])
        self._update_drift_timer = util.RepeatingTimer(parent._drift_period,
                                                       self._update_drift,
                                                       "Drift update")
        if parent._drift_period:
            self._update_drift_timer.start()

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL

    def terminate(self):
        self._update_drift_timer.cancel()
        self.stop_acquire()

    @isasync
    def applyAutoContrast(self):
        """
        (Simulation of) run the calibration for the brightness/contrast.
        (Identical interface as the phenom driver)
        """
        self.contrast.value = 0.5
        self.brightness.value = 0.5
        return model.InstantaneousFuture()

    def start_acquire(self, callback):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            target = self._acquire_thread
            self._acquisition_thread = threading.Thread(target=target,
                    name="SimSEM acquire flow thread",
                    args=(callback,))
            self._acquisition_thread.start()

    def stop_acquire(self):
        with self._acquisition_lock:
            with self._acquisition_init_lock:
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

    def _update_drift(self):
        """
        Periodically updates drift according to drift_factor and drift_period.
        """
        drift = self.current_drift + random.random() * self.drift_factor
        if abs(drift) >= self.drift_bound:
            # Make it bounce back
            drift = math.copysign(1, drift) * (2 * self.drift_bound - abs(drift))
            self.drift_factor = -self.drift_factor

        self.current_drift = drift

    def _simulate_image(self):
        """
        Generates the fake output based on the translation, resolution and
        current drift.
        """
        metadata = self.parent._metadata.copy()
        scanner = self.parent._scanner
        metadata.update(scanner._metadata)
        metadata.update(self._metadata)

        with self._acquisition_init_lock:
            logging.debug("Simulating an image")
            pxs = scanner.pixelSize.value  # m/px

            pxs_pos = scanner.translation.value
            scale = scanner.scale.value
            res = scanner.resolution.value
            shi = scanner.shift.value

            phy_pos = metadata.get(model.MD_POS, (0, 0))
            trans = scanner.pixelToPhy(pxs_pos)
            updated_phy_pos = (phy_pos[0] + trans[0], phy_pos[1] + trans[1])

            shape = self.fake_img.shape
            # Simulate shift and drift
            center = (shape[1] / 2 - shi[0] / pxs[0] - self.current_drift,
                      shape[0] / 2 - shi[1] / pxs[1] + self.current_drift)

            # First and last index (eg, 0 -> 255)
            ltrb = [center[0] + pxs_pos[0] - (res[0] / 2) * scale[0],
                    center[1] + pxs_pos[1] - (res[1] / 2) * scale[1],
                    center[0] + pxs_pos[0] + ((res[0] / 2) - 1) * scale[0],
                    center[1] + pxs_pos[1] + ((res[1] / 2) - 1) * scale[1]
                    ]
            # If the shift caused the image to go out of bounds, limit it
            if ltrb[0] < 0:
                ltrb[0] = 0
            elif ltrb[2] > shape[1] - 1:
                ltrb[0] -= ltrb[2] - (shape[1] - 1)
            if ltrb[1] < 0:
                ltrb[1] = 0
            elif ltrb[3] > shape[0] - 1:
                ltrb[1] -= ltrb[3] - (shape[0] - 1)
            assert(ltrb[0] >= 0 and ltrb[1] >= 0)

            # compute each row and column that will be included
            coord = ([int(round(ltrb[0] + i * scale[0])) for i in range(res[0])],
                     [int(round(ltrb[1] + i * scale[1])) for i in range(res[1])])
            sim_img = self.fake_img[numpy.ix_(coord[1], coord[0])] # copy

            # reduce image depth if requested
            bpp = self.bpp.value
            if bpp < 16:
                mind, maxd = sim_img.min(), sim_img.max()
                maxf = 2 ** bpp - 1
                b = maxf / max(1, (maxd - mind))
                # Multiply by a float and drop to the original dtype
                numpy.multiply(sim_img - mind, b, out=sim_img, casting="unsafe")
                if bpp <= 8:
                    sim_img = sim_img.astype(numpy.uint8)

            metadata[model.MD_BPP] = bpp

            if self.parent._focus:
                # apply the defocus
                pos = self.parent._focus.position.value['z']
                dist = abs(pos - self.parent._focus._good_focus) * 1e4
                sim_img = ndimage.gaussian_filter(sim_img, sigma=dist)

            if not scanner.power.value:
                sim_img[:] = 0
            elif scanner.blanker.value:  # None (auto) and False (unblank) are handled the same here
                # Leave a tiny bit of signal
                numpy.multiply(sim_img, 0.001, out=sim_img, casting="unsafe")

            # update fake output metadata
            metadata[model.MD_POS] = updated_phy_pos
            metadata[model.MD_PIXEL_SIZE] = (pxs[0] * scale[0], pxs[1] * scale[1])
            metadata[model.MD_ACQ_DATE] = time.time()
            metadata[model.MD_ROTATION] = scanner.rotation.value
            metadata[model.MD_DWELL_TIME] = scanner.dwellTime.value
            metadata[model.MD_EBEAM_CURRENT] = scanner.probeCurrent.value
            metadata[model.MD_EBEAM_VOLTAGE] = scanner.accelVoltage.value
            return model.DataArray(sim_img, metadata)

    def _acquire_thread(self, callback):
        """
        Thread that simulates the SEM acquisition. It calculates and updates the
        center (e-beam) position based on the translation, imitates the delay according
        to the dwell time and resolution and provides the new generated output to
        the Dataflow.
        """
        try:
            while not self._acquisition_must_stop.is_set():
                dwelltime = self.parent._scanner.dwellTime.value
                resolution = self.parent._scanner.resolution.value
                duration = numpy.prod(resolution) * dwelltime
                if self._acquisition_must_stop.wait(duration):
                    break
                # TODO: it's not a very proper simulation for multiple detectors,
                # as in Odemis the convention for SEM is that the ebeam waits
                # for _all_ the detectors to be ready before scanning.
                self.data._waitSync()
                callback(self._simulate_image())
        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()


class SEMDataFlow(model.DataFlow):
    """
    This is an extension of model.DataFlow. It receives notifications from the
    detector component once the fake output is generated. This is the dataflow to
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

class EbeamFocus(model.Actuator):
    """
    Simulated focus component.
    Just pretends to be able to move Z (instantaneously).
    """
    def __init__(self, name, role, **kwargs):
        self._good_focus = 0.1
        axes_def = {"z": model.Axis(unit="m", range=[1e-6, 0.3])}
        self._position = {"z": self._good_focus}

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversion(self._position),
                                    unit="m", readonly=True)

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversion(self._position)
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        for axis, change in shift.items():
            self._position[axis] += change
            rng = self.axes[axis].range
            if not rng[0] < self._position[axis] < rng[1]:
                logging.warning("moving axis %s to %f, outside of range %r",
                                axis, self._position[axis], rng)
            else:
                logging.info("moving axis %s to %f", axis, self._position[axis])

        self._updatePosition()
        return model.InstantaneousFuture()

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        for axis, new_pos in pos.items():
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])

        self._updatePosition()
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        logging.warning("Stopping z axis")
