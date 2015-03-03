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
from __future__ import division

from Pyro4.core import isasync
import logging
import math
import numpy
from odemis import model, util, dataio
from odemis.util import img
import os.path
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
        image = unicode(image)
        # change to this directory to ensure relative path is from this file
        os.chdir(os.path.dirname(unicode(__file__)))
        exporter = dataio.find_fittest_exporter(image)
        self.fake_img = img.ensure2DImage(exporter.read_data(image)[0])

        self._drift_period = drift_period

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        self._metadata[model.MD_HW_NAME] = "FakeSEM"

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("SimSEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._scanner)

        # create the scanner child
        try:
            kwargs = children["detector0"]
        except (KeyError, TypeError):
            raise KeyError("SimSEM was not given a 'detector0' child")
        self._detector = Detector(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._detector)

        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            logging.info("Will not simulate focus")
            self._focus = None
        else:
            self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._focus)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        self._detector._update_drift_timer.cancel()

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
        # TODO: for now it's readonly because no rotation is supported
        self.rotation = model.FloatContinuous(0, [0, 2 * math.pi], unit="rad",
                                              readonly=True)

        self.dwellTime = model.FloatContinuous(1e-06, (1e-06, 1000), unit="s")

        # VAs to control the ebeam, purely fake
        self.probeCurrent = model.FloatEnumerated(1.3e-9,
                          {0.1e-9, 1.3e-9, 2.6e-9, 3.4e-9, 11.564e-9, 23e-9},
                          unit="A")
        self.accelVoltage = model.FloatContinuous(10e3, (1e3, 30e3), unit="V")

    # we share metadata with our parent
    def updateMetadata(self, md):
        self.parent.updateMetadata(md)

    def getMetadata(self):
        return self.parent.getMetadata()

    def _onHFV(self, hfv):
        self._updatePixelSize()

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
        self.bpp = model.IntEnumerated(bpp, set([8, 16]))

        # Simulate the Hw brightness/contrast, but don't actually do anything
        self.contrast = model.FloatContinuous(0.5, [0, 1], unit="")
        self.brightness = model.FloatContinuous(0.5, [0, 1], unit="")

        self.drift_factor = 1  # dummy value for drift in pixels
        self.current_drift = 0
        # Given that max resolution is half the shape of fake_img,
        # we set the drift bound to stay inside the fake_img bounds
        self.drift_bound = min(v // 4 for v in self.fake_img.shape[::-1])
        self._update_drift_timer = util.RepeatingTimer(parent._drift_period,
                                                       self._update_drift,
                                                       "Drift update")
        if parent._drift_period:
            self._update_drift_timer.start()

    # we share metadata with our parent
    def updateMetadata(self, md):
        self.parent.updateMetadata(md)

    def getMetadata(self):
        return self.parent.getMetadata()

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
        self.current_drift += self.drift_factor
        if abs(self.current_drift) == self.drift_bound:
            self.drift_factor = -self.drift_factor

    def _simulate_image(self):
        """
        Generates the fake output based on the translation, resolution and
        current drift.
        """
        metadata = dict(self.parent._metadata)
        scanner = self.parent._scanner

        with self._acquisition_init_lock:
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

            lt = (center[0] + pxs_pos[0] - (res[0] / 2) * scale[0],
                  center[1] + pxs_pos[1] - (res[1] / 2) * scale[1])
            assert(lt[0] >= 0 and lt[1] >= 0)
            # compute each row and column that will be included
            coord = ([int(round(lt[0] + i * scale[0])) for i in range(res[0])],
                     [int(round(lt[1] + i * scale[1])) for i in range(res[1])])
            sim_img = self.fake_img[numpy.ix_(coord[1], coord[0])] # copy

            # reduce image depth if requested
            bpp = self.bpp.value
            if bpp < 16:
                mind, maxd = sim_img.min(), sim_img.max()
                maxf = 2 ** bpp - 1
                b = maxf / (maxd - mind)
                sim_img -= mind
                sim_img *= b
                if bpp <= 8:
                    sim_img = sim_img.astype(numpy.uint8)

            metadata[model.MD_BPP] = bpp

            if self.parent._focus:
                # apply the defocus
                pos = self.parent._focus.position.value['z']
                dist = abs(pos - self.parent._focus._good_focus) * 1e4
                sim_img = ndimage.gaussian_filter(sim_img, sigma=dist)

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
                                    self._applyInversionAbs(self._position),
                                    unit="m", readonly=True)

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversionRel(shift)

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
        pos = self._applyInversionAbs(pos)

        for axis, new_pos in pos.items():
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])

        self._updatePosition()
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        logging.warning("Stopping z axis")
