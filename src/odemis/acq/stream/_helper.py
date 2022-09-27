# -*- coding: utf-8 -*-
'''
Created on 25 Jun 2014

@author: Éric Piel

Copyright © 2014-2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Contains special streams which are not proper, but can be used as a way to
# store or retrieve information.


from past.builtins import long
from abc import abstractmethod
from concurrent.futures._base import CancelledError
from functools import wraps
import logging
import math
import numbers
import numpy
from odemis import model
from odemis.acq import align
from odemis.model import VigilantAttributeBase, MD_POL_NONE
from odemis.util import img, almost_equal, get_best_dtype_for_acc, angleres
import time

from ._base import Stream, UNDEFINED_ROI, POL_POSITIONS
from ._live import LiveStream


class RepetitionStream(LiveStream):
    """
    Abstract class for streams which are actually a set of multiple acquisition
    repeated over a grid.

    Beware, these special streams are for settings only. So the image generated
    when active is only for quick feedback of the settings. To actually perform
    a full acquisition, the stream should be fed to a MultipleDetectorStream.
    Note that .estimateAcquisitionTime() returns the time needed for the whole
    acquisition.
    """

    def __init__(self, name, detector, dataflow, emitter, scanner=None, sstage=None, **kwargs):
        """
        emitter (Emitter): the component that generates energy, and possibly
          also controls the position of the energy (eg, e-beam).
        scanner (None or Scanner): the component that controls the position of
          the energy (eg, laser-mirror). If None, emitter is expected to
          control the position.
        sstage (None or Actuator): scan stage. If None, it will use the ebeam
          to scan (= standard behaviour).
        """
        super(RepetitionStream, self).__init__(name, detector, dataflow, emitter,
                                               **kwargs)
        self._scanner = scanner or emitter  # fallback to emitter if no scanner

        # all the information needed to acquire an image (in addition to the
        # hardware component settings which can be directly set).

        # ROI + repetition is sufficient, but pixel size is nicer for the user
        # and allow us to ensure each pixel is square. (Non-square pixels are
        # not a problem for the hardware, but annoying to display data in normal
        # software).
        # TODO: only have ROI + rep here, and add pixel size into the GUI controller?
        # This way, the code is much simpler here. It doesn't even need to know
        # about the physical unit (ie, the FoV). Changing any of the VA wouldn't
        # affect the other one. Of course, all the current complexity would go
        # into the GUI controller then (there is no free lunch!).

        # As the settings are over-specified, whenever ROI, repetition, or pixel
        # size changes, one (or more) other VA is updated to keep everything
        # consistent. In addition, there are also hardware constraints, which
        # must also be satisfied. The main rules followed are:
        #  * Try to keep the VA which was changed (by the user) as close as
        #    possible to the requested value (within hardware limits).
        #  * If the ROI is not the one changed, try to keep it as-is, or at
        #    least, try to keep the same center, and same area.
        # So in practice, the three setters behave in this way:
        #  * ROI set: ROI (as requested) + PxS (current) → repetition (updated)
        #  * PxS set: PxS (as requested) + ROI (current) → repetition (updated)
        #    The ROI is adjusted to ensure the repetition is a round number
        #    and acceptable by the hardware.
        #  * Rep set: Rep (as requested) + ROI (current) → PxS (updated)
        #    The repetition is adjusted to fit the hardware limits

        # If no local or hw exposureTime VA is requested, automatically provide an integrationTime VA,
        # which allows longer exposure by doing image integration.
        if ("exposureTime" not in (kwargs.get("detvas", set()) | kwargs.get("hwdetvas", set()))
            and model.hasVA(detector, "exposureTime")):
            self._hwExpTime = self.detector.exposureTime.value
            # Number of images that need to be acquired for the requested exposure time.
            # If not integration time, default is 1 image.
            self.integrationCounts = model.VigilantAttribute(1, readonly=True)

            # increase exposure time range to perform image integration
            # TODO: for now we specify a max integration time by using a fixed multiple of the exp time, but max 24h
            integrationTimeRange = (detector.exposureTime.range[0], min((detector.exposureTime.range[1] * 10000, 86400)))
            self.integrationTime = model.FloatContinuous(detector.exposureTime.value,
                                                         integrationTimeRange, unit="s",
                                                         setter=self._setIntegrationTime)
            self._img_intor = None

        # Region of interest as left, top, right, bottom (in ratio from the
        # whole area of the emitter => between 0 and 1)
        # We overwrite the VA provided by LiveStream to define a setter.
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float),
                                         setter=self._setROI)

        # Start with pixel size to fit 1024 px, as it's typically a sane value
        # for the user (and adjust for the hardware).
        spxs = self._scanner.pixelSize.value  # m, size at scale = 1
        sshape = self._scanner.shape  # px, max number of pixels scanned
        phy_size_x = spxs[0] * sshape[0]  # m
        pxs = phy_size_x / 1024  # one dim is enough (arbitrarily: X)

        roi, rep, pxs = self._updateROIAndPixelSize(self.roi.value, pxs)

        # the number of pixels acquired in each dimension
        # it will be assigned to the resolution of the emitter (but cannot be
        # directly set, as one might want to use the emitter while configuring
        # the stream).
        # TODO: If the acquisition code only acquires spot by spot, the
        # repetition is not limited by the resolution or the scale.
        self.repetition = model.ResolutionVA(rep,
                                             self._scanner.resolution.range,
                                             setter=self._setRepetition)

        # The size of the pixel (IOW, the distance between the center of two
        # consecutive pixels) used both horizontally and vertically.
        # The actual range is dynamic, as it changes with the magnification.
        self.pixelSize = model.FloatContinuous(pxs, range=(0, 1), unit="m",
                                               setter=self._setPixelSize)

        # fuzzy scanning avoids aliasing by sub-scanning each region of a pixel
        # Note: some subclasses for which it doesn't make sense will remove it
        self.fuzzing = model.BooleanVA(False)

        self._sstage = sstage
        # Can be True only if the sstage is not None
        self.useScanStage = model.BooleanVA(False)

        # exposure time of each pixel is the exposure time of the detector,
        # the dwell time of the emitter will be adapted before acquisition.

        # Update the pixel size whenever SEM magnification changes
        # This allows to keep the ROI at the same place in the SEM FoV.
        # Note: this is to be done only if the user needs to manually update the
        # magnification.
        # TODO: move the whole code to the GUI. and subscribe to emitter.pixelSize instead?
        try:
            magva = self._getScannerVA("magnification")
            self._prev_mag = magva.value
            magva.subscribe(self._onMagnification)
        except AttributeError:
            pass

    # Overrides method of LiveStream
    def _onNewData(self, dataflow, data):
        """
        Called when a new image has arrived from the detector. Usually the dataflow subscribes to onNewData.
        If there is integrationTime integrate one image after another for live display while running a stream.
        :param dataflow: (model.Dataflow) The dataflow.
        :param data: (model.DataArray). The new image that has arrived from the detector.
        """
        if hasattr(self, "integrationTime"):
            if self._img_intor is None:
                self._img_intor = img.ImageIntegrator(self.integrationCounts.value)

            # Reset in case the integrationCounts change while playing the stream
            if self._img_intor.steps != self.integrationCounts.value:
                self._img_intor.steps = self.integrationCounts.value

            # Catch the exception when the user changes some of the hardware settings (eg, resolution), while playing
            # the stream. To do so, restart everything and keep the last image received.
            try:
                self.raw = [self._img_intor.append(data)]
            except Exception as ex:
                logging.warning("Failed to integrate image (of shape %s): %s", data.shape, ex)
                self._img_intor = img.ImageIntegrator(self.integrationCounts.value)
                self.raw = [self._img_intor.append(data)]
        else:
            self.raw = [data]

        self._shouldUpdateHistogram()
        self._shouldUpdateImage()

    # Overrides method of Stream
    def _updateImage(self):
        """
        Recomputes the image with all the raw data available.
        """
        if not self.raw:
            return

        try:
            if not isinstance(self.raw, list):
                raise AttributeError(".raw must be a list of DA/DAS")
            # update and show the integrated image
            data = self.raw[0]

            if data.ndim != 2:
                data = img.ensure2DImage(data)  # Remove extra dimensions (of length 1)
            self.image.value = self._projectXY2RGB(data, self.tint.value)

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)

    # Overrides method of Stream
    def _updateHistogram(self, data=None):
        """
        Recomputes the histogram with all the raw data available.
        The intensityRange will be also updated if auto_bc is enabled.
        :param data: (DataArray) The raw data to use.
        """
        # Compute histogram and compact version
        if data is None:
            if not self.raw:
                logging.debug("Not computing histogram as .raw is empty")
                return
            data = self.raw[0]

        # Depth can change at each image (depends on hardware settings)
        self._updateDRange(data)

        # Initially, _drange might be None, in which case it will be guessed
        hist, edges = img.histogram(data, irange=self._drange)
        if hist.size > 256:
            chist = img.compactHistogram(hist, 256)
        else:
            chist = hist
        self.histogram._full_hist = hist
        self.histogram._edges = edges
        # First update the value, before the intensityRange subscribers are called...
        self.histogram._value = chist

        if self.auto_bc.value:
            self._recomputeIntensityRange()

        # Notify last, so intensityRange is correct when subscribers get the new histogram.
        self.histogram.notify(chist)

    def _setIntegrationTime(self, value):
        """
        Set the local integration time VA.
        :parameter value: (float) Integration time to be set.
        :return: (float) Current integration time on VA.
        """
        self._intTime2NumImages(value)

        return value

        # Override Stream._is_active_setter() in _base.py
    def _is_active_setter(self, active):
        """
        Called when stream is activated/played. Links integration time VA with
        detector exposure time VA when stream is active and unlink when inactive.
        :param active: (boolean) True if stream is playing.
        :returns: (boolean) If stream is playing or not.
        """
        active = super(RepetitionStream, self)._is_active_setter(active)
        if hasattr(self, "integrationTime"):
            if active:
                # reset raw data to not integrate images from previous acq with new acq
                self.raw = []
                self._linkIntTime2HwExpTime()
            else:
                self._unlinkIntTime2HwExpTime()

        return active

    def _linkIntTime2HwExpTime(self):
        """"
        Subscribe integration VA: link VA to detector exposure time VA.
        """
        self._intTime2NumImages(self.integrationTime.value)
        try:
            logging.debug("Set exposure time on detector to %s and number of "
                          "images that need to be integrated is %s.", self._hwExpTime, self.integrationCounts.value)
            self.detector.exposureTime.value = self._hwExpTime
        except Exception:
            logging.exception("Failed to set exposure time %s on detector.", self._hwExpTime)
        self.integrationTime.subscribe(self._onIntegrationTime)

    def _unlinkIntTime2HwExpTime(self):
        """
        Unsubscribe integration time VA: unlink VA from detector exposure time VA.
        """
        self.integrationTime.unsubscribe(self._onIntegrationTime)

    def _onIntegrationTime(self, intTime):
        """
        Callback, which calculates and updates the exposure time on the detector.
        Only called when stream is active.
        :param intTime: (float) Integration time requested via the stream VA (in GUI).
        """
        # set the exp time on the detector
        self.detector.exposureTime.value = self._hwExpTime

    def _intTime2NumImages(self, intTime):
        """
        Updates .integrationCounts and ._hwExpTime based on the integration time and the maximum exposure time
        of the detector. If not, it calculates how many images need to be integrated (summed) to
        result in an acquisition with the requested integration time. Also, the new exposure time
        to be set on the hardware for the image integration is calculated. If the requested
        integration time is in range of the detector, the exp time is not modified and number of images
        to be recorded is one.
        :param intTime: (float) Integration time requested via the stream VA (GUI).
        """
        hwExpTimeMax = self.detector.exposureTime.range[1]
        n = int(math.ceil(intTime / hwExpTimeMax))
        self._hwExpTime = intTime / n

        self.integrationCounts._set_value(n, force_write=True)

    @property
    def scanner(self):
        """
        The component used to scan. Either the scanner argument, or, if it was
        None, the emitter argument.
        """
        return self._scanner

    def _getScannerVA(self, vaname):

        # If it's actually the emitter, check the local VAs (eg "emtDwellTime")
        if self._scanner is self._emitter:
            return self._getEmitterVA(vaname)

        hwva = getattr(self._scanner, vaname)
        if not isinstance(hwva, VigilantAttributeBase):
            raise AttributeError("Scanner has not VA %s" % (vaname,))
        return hwva

    def _onMagnification(self, mag):
        """
        Called when the SEM magnification is updated
        """
        # Update the pixel size so that the ROI stays that the same place in the
        # SEM FoV and with the same repetition.
        # The bigger is the magnification, the smaller should be the pixel size
        ratio = self._prev_mag / mag
        self._prev_mag = mag
        self.pixelSize._value *= ratio
        self.pixelSize.notify(self.pixelSize._value)

    def _adaptROI(self, roi, rep, pxs):
        """
        Compute the ROI so that it's _exactly_ pixel size * repetition,
          while keeping its center fixed
        roi (4 floats): current ROI, just to know its center
        rep (2 ints)
        pxs (float)
        return ROI (4 floats): ltrb
        """
        # Rep + PxS (+ center of ROI) -> ROI
        roi_center = ((roi[0] + roi[2]) / 2,
                      (roi[1] + roi[3]) / 2)
        spxs = self._scanner.pixelSize.value
        sshape = self._scanner.shape
        phy_size = (spxs[0] * sshape[0], spxs[1] * sshape[1])  # max physical ROI
        roi_size = (rep[0] * pxs / phy_size[0],
                    rep[1] * pxs / phy_size[1])
        roi = (roi_center[0] - roi_size[0] / 2,
               roi_center[1] - roi_size[1] / 2,
               roi_center[0] + roi_size[0] / 2,
               roi_center[1] + roi_size[1] / 2)

        return roi

    def _fitROI(self, roi):
        """
        Ensure that a ROI fits within its bounds. If not, it will move it or
        reduce it.
        roi (4 floats)
        return (4 floats)
        """
        roi = list(roi)

        # Ensure it's not too big
        if roi[2] - roi[0] > 1:
            roi[2] = roi[0] + 1
        if roi[3] - roi[1] > 1:
            roi[3] = roi[1] + 1

        # shift the ROI if it's now slightly outside the possible area
        if roi[0] < 0:
            roi[2] = min(1, roi[2] - roi[0])
            roi[0] = 0
        elif roi[2] > 1:
            roi[0] = max(0, roi[0] - (roi[2] - 1))
            roi[2] = 1

        if roi[1] < 0:
            roi[3] = min(1, roi[3] - roi[1])
            roi[1] = 0
        elif roi[3] > 1:
            roi[1] = max(0, roi[1] - (roi[3] - 1))
            roi[3] = 1

        return roi

    def _computePixelSize(self, roi, rep):
        """
        Compute the pixel size based on the ROI + repetition (+ current scanner
          pixelSize)
        roi (4 floats)
        rep (2 ints)
        return pxs (float): the pixel size (based on the X dimension)
        """
        spxs = self._scanner.pixelSize.value
        sshape = self._scanner.shape
        phy_size_x = spxs[0] * sshape[0]  # one dim is enough
        roi_size_x = roi[2] - roi[0]
        pxs = roi_size_x * phy_size_x / rep[0]
        return pxs

    def _updateROIAndPixelSize(self, roi, pxs):
        """
        Adapt a ROI and pixel size so that they are correct. It checks that they
          are within bounds and if not, make them fit in the bounds by adapting
          the repetition.
        roi (4 floats): ROI wanted (might be slightly changed)
        pxs (float): new pixel size
        returns:
          4 floats: new ROI
          2 ints: new repetition
          float: pixel size
        """
        # If ROI is undefined => link rep and pxs as if the ROI was full
        if roi == UNDEFINED_ROI:
            _, rep, pxs = self._updateROIAndPixelSize((0, 0, 1, 1), pxs)
            return roi, rep, pxs

        roi = self._fitROI(roi)

        # Compute scale based on dim X, and ensure it's within range
        spxs = self._scanner.pixelSize.value
        scale = pxs / spxs[0]
        min_scale = max(self._scanner.scale.range[0])
        max_scale = min(self._scanner.shape)
        scale = max(min_scale, min(scale, max_scale))
        pxs = scale * spxs[0]

        # compute the repetition (ints) that fits the ROI with the pixel size
        sshape = self._scanner.shape
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        rep = (int(round(sshape[0] * roi_size[0] / scale)),
               int(round(sshape[1] * roi_size[1] / scale)))

        logging.debug("First trial with roi = %s, rep = %s, pxs = %g", roi, rep, pxs)

        # Ensure it's really compatible with the hardware
        rep = self._scanner.resolution.clip(rep)

        # update the ROI so that it's _exactly_ pixel size * repetition,
        # while keeping its center fixed
        roi = self._adaptROI(roi, rep, pxs)
        roi = self._fitROI(roi)

        # In case the ROI got modified again and the aspect ratio is not anymore
        # the same as the rep, we shrink it to ensure the pixels are square (and
        # it should still fit within the FoV).
        eratio = sshape[0] / sshape[1]
        rel_pxs = eratio * (roi[2] - roi[0]) / rep[0], (roi[3] - roi[1]) / rep[1]
        if rel_pxs[0] != rel_pxs[1]:
            logging.debug("Shrinking ROI to ensure pixel is square (relative pxs = %s)", rel_pxs)
            roi_center = ((roi[0] + roi[2]) / 2,
                          (roi[1] + roi[3]) / 2)
            sq_pxs = min(rel_pxs)
            roi_size = sq_pxs * rep[0] / eratio, sq_pxs * rep[1]
            roi = (roi_center[0] - roi_size[0] / 2,
                   roi_center[1] - roi_size[1] / 2,
                   roi_center[0] + roi_size[0] / 2,
                   roi_center[1] + roi_size[1] / 2)
            phy_size = (spxs[0] * sshape[0], spxs[1] * sshape[1])  # max physical ROI
            pxs = sq_pxs * phy_size[0] / eratio

        # Double check we didn't end up with scale out of range
        pxs_range = self._getPixelSizeRange()
        if not pxs_range[0] <= pxs <= pxs_range[1]:
            logging.error("Computed impossibly small pixel size %s, with range %s", pxs, pxs_range)
            # TODO: revert to some *acceptable* values for ROI + rep + PxS?
            # pxs = max(pxs_range[0], min(pxs, pxs_range[1]))

        logging.debug("Computed roi = %s, rep = %s, pxs = %g", roi, rep, pxs)

        return tuple(roi), tuple(rep), pxs

    def _setROI(self, roi):
        """
        Ensures that the ROI is always an exact number of pixels, and update
         repetition to be the correct number of pixels
        roi (tuple of 4 floats)
        returns (tuple of 4 floats): new ROI
        """
        # If only width or height changes, ensure we respect it by
        # adapting pixel size to be a multiple of the new size
        pxs = self.pixelSize.value

        old_roi = self.roi.value
        if old_roi != UNDEFINED_ROI and roi != UNDEFINED_ROI:
            old_size = (old_roi[2] - old_roi[0], old_roi[3] - old_roi[1])
            new_size = (roi[2] - roi[0], roi[3] - roi[1])
            if almost_equal(old_size[0], new_size[0], atol=1e-5):
                dim = 1
                # If dim 1 is also equal -> new pixel size will not change
            elif almost_equal(old_size[1], new_size[1], atol=1e-5):
                dim = 0
            else:
                dim = None

            if dim is not None:
                # Only one dimension changed:
                # -> Update rep to be fitting (while being integers) on that dim
                # -> adjust ROI (on that dim) while keeping pxs
                old_rep = self.repetition.value[dim]
                new_rep_flt = old_rep * new_size[dim] / old_size[dim]
                new_rep_int = max(1, round(new_rep_flt))
                req_rep = list(self.repetition.value)
                req_rep[dim] = new_rep_int
                req_rep = tuple(req_rep)
                hw_rep = self._scanner.resolution.clip(req_rep)
                if hw_rep != req_rep:
                    logging.debug("Hardware adjusted rep from %s to %s", req_rep, hw_rep)
                    req_rep = hw_rep

                # Note: on the "other dim", everything is the same as before,
                # so it will return the same ROI. On the dim, it will adjust the
                # center based on requested ROI while the new rep might be the
                # same as before, so it might cause some small unexpected shifts
                # For now, this is deemed acceptable.
                roi = self._adaptROI(roi, req_rep, pxs)
            else:
                # Both dimensions changed:
                # -> update rep to be fitting (while being integers)
                # -> Adjust ROI and pxs to be the same area as requested ROI
                old_rep = self.repetition.value
                new_rep_flt = (abs(old_rep[0] * new_size[0] / old_size[0]),
                               abs(old_rep[1] * new_size[1] / old_size[1]))
                req_rep = (max(1, round(new_rep_flt[0])),
                           max(1, round(new_rep_flt[1])))
                hw_rep = self._scanner.resolution.clip(req_rep)
                if hw_rep != req_rep:
                    logging.debug("Hardware adjusted from %s to %s", req_rep, hw_rep)
                    req_rep = hw_rep

                # Ideally the pxs stays the same, but if rep was adjusted,
                # compensate it to keep the same area.
                pxs *= math.sqrt(numpy.prod(new_rep_flt) / numpy.prod(req_rep))
                roi = self._adaptROI(roi, req_rep, pxs)

        roi, rep, pxs = self._updateROIAndPixelSize(roi, pxs)
        # update repetition without going through the checks
        self.repetition._value = rep
        self.repetition.notify(rep)
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        return roi

    def _setPixelSize(self, pxs):
        """
        Ensures pixel size is within the current allowed range, and updates
         ROI and repetition.
        return (float): new pixel size
        """
        roi, rep, pxs = self._updateROIAndPixelSize(self.roi.value, pxs)

        # update roi and rep without going through the checks
        self.roi._value = roi
        self.roi.notify(roi)
        self.repetition._value = rep
        self.repetition.notify(rep)

        return pxs

    def _setRepetition(self, repetition):
        """
        Find a fitting repetition and update pixel size and ROI, using the
         current ROI making sure that the repetition is ints (pixelSize and roi
        changes are notified but the setter is not called).
        repetition (tuple of 2 ints): new repetition wanted (might be clamped)
        returns (tuple of 2 ints): new (valid) repetition
        """
        roi = self.roi.value
        spxs = self._scanner.pixelSize.value
        sshape = self._scanner.shape
        phy_size = (spxs[0] * sshape[0], spxs[1] * sshape[1])  # max physical ROI

        # clamp repetition to be sure it's correct (it'll be clipped against
        # the scanner resolution later on, to be sure it's compatible with the
        # hardware)
        rep = self.repetition.clip(repetition)

        # If ROI is undefined => link repetition and pxs as if ROI is full
        if roi == UNDEFINED_ROI:
            # must be square, so only care about one dim
            pxs = phy_size[0] / rep[0]
            roi, rep, pxs = self._updateROIAndPixelSize((0, 0, 1, 1), pxs)
            self.pixelSize._value = pxs
            self.pixelSize.notify(pxs)
            return rep

        # The basic principle is that the center and surface of the ROI stay.
        # We only adjust the X/Y ratio and the pixel size based on the new
        # repetition.

        prev_rep = self.repetition.value
        prev_pxs = self.pixelSize.value

        # keep area and adapt ROI (to the new repetition ratio)
        pxs = prev_pxs * math.sqrt(numpy.prod(prev_rep) / numpy.prod(rep))
        roi = self._adaptROI(roi, rep, pxs)
        logging.debug("Estimating roi = %s, rep = %s, pxs = %g", roi, rep, pxs)

        roi, rep, pxs = self._updateROIAndPixelSize(roi, pxs)
        # update roi and pixel size without going through the checks
        self.roi._value = roi
        self.roi.notify(roi)
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        return rep

    # TODO: instead of caring about the pixel size in m, just use the scale, which
    # is a ratio between pixel size and FoV. The advantage is that it's fixed.
    # In this case, the only moment it's useful to know the current pixelSize
    # is when converting it back in physical units for .pixelSize.
    def _getPixelSizeRange(self):
        """
        return (tuple of 2 floats): min and max value of the pixel size at the
          current magnification, in m.
        """
        # Two things to take care of:
        # * current pixel size of the scanner (which depends on the magnification)
        # * merge horizontal/vertical dimensions into one fits-all

        # The current scanner pixel size is the minimum size
        spxs = self._scanner.pixelSize.value
        min_pxs = max(spxs)
        min_scale = max(self._scanner.scale.range[0])
        if min_scale < 1:
            # Pixel size can be smaller if not scanning the whole FoV
            min_pxs *= min_scale
        shape = self._scanner.shape
        # The maximum pixel size is if we acquire a single pixel for the whole FoV
        max_pxs = min(spxs[0] * shape[0], spxs[1] * shape[1])
        return min_pxs, max_pxs

    @abstractmethod
    def estimateAcquisitionTime(self):
        """
        Estimates the acquisition time for the "live" update of the RepetitionStream.
        To get the acquisition time of the actual stream (ie, the corresponding
        MDStream), you need to ask that stream.
        """
        return self.SETUP_OVERHEAD

    def guessFoV(self):
        """
        Estimate the field-of-view based on the current settings.
        return (float, float): width, height in meters
        """
        shape = self._scanner.shape
        pxs = self._scanner.pixelSize.value
        full_fov = shape[0] * pxs[0], shape[0] * pxs[1]
        roi = self.roi.value
        return full_fov[0] * (roi[2] - roi[0]), full_fov[0] * (roi[3] - roi[1])

class CCDSettingsStream(RepetitionStream):

    def estimateAcquisitionTime(self):
        # Exposure time (of the detector) + readout time + 30ms overhead + 20% overhead
        try:
            ro_rate = self._getDetectorVA("readoutRate").value
        except Exception:
            ro_rate = 100e6  # Hz
        res = self._getDetectorVA("resolution").value
        readout = numpy.prod(res) / ro_rate

        exp = self._getDetectorVA("exposureTime").value
        duration = (exp + readout + 0.03) * 1.20
        # Add the setup time
        duration += self.SETUP_OVERHEAD

        return duration


class PMTSettingsStream(RepetitionStream):
    pass


class SpectrumSettingsStream(CCDSettingsStream):
    """ A Spectrum stream.

    The live view is just the current raw spectrum (wherever the ebeam is).

    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_SPECTRUM
        super(SpectrumSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        # For SPARC: typical user wants density a bit lower than SEM
        self.pixelSize.value *= 6

        # B/C and histogram are meaningless on a spectrum
        del self.auto_bc
        del self.auto_bc_outliers
        del self.histogram
        del self.tint

        # Contains one 1D spectrum (start with an empty array)
        self.image.value = model.DataArray([])

        # TODO: grating/cw as VAs (from the spectrometer)

    # onActive: same as the standard LiveStream (ie, acquire from the dataflow)

    def _updateImage(self):
        if not self.raw:
            return

        # Just copy the raw data into the image, removing useless extra dimensions
        im = self.raw[0][:, 0, 0, 0, 0]
        im.metadata = im.metadata.copy()
        im.metadata[model.MD_DIMS] = "C"
        self.image.value = im

    # No histogram => no need to do anything to update it
    @staticmethod
    def _histogram_thread(wstream):
        pass

    def _onNewData(self, dataflow, data):
        # Convert data to be spectrum-like. It's not needed for the projection,
        # but useful when storing the raw data (eg, with in a snapshot in the GUI)
        # (We expect the original shape is (1, X).
        if data.shape[0] != 1:
            logging.warning("Got a spectrum with multiple lines (shape = %s)", data.shape)
        specdata = data.reshape((data.shape[-1], 1, 1, 1, 1))

        # Set POS and PIXEL_SIZE from the e-beam (which is in spot mode)
        epxs = self.emitter.pixelSize.value
        specdata.metadata[model.MD_PIXEL_SIZE] = epxs
        emd = self.emitter.getMetadata()
        pos = emd.get(model.MD_POS, (0, 0))
        trans = self.emitter.translation.value
        specdata.metadata[model.MD_POS] = (pos[0] + trans[0] * epxs[0],
                                           pos[1] - trans[1] * epxs[1])  # Y is inverted
        super(SpectrumSettingsStream, self)._onNewData(dataflow, specdata)


class TemporalSpectrumSettingsStream(CCDSettingsStream):
    """
    An streak camera stream, for a set of points (on the SEM).
    The live view is just the raw readout camera image.
    """
    def __init__(self, name, detector, dataflow, emitter, streak_unit, streak_delay,
                 streak_unit_vas, **kwargs):

        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_TEMPSPECTRUM

        super(TemporalSpectrumSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)  # init of CCDSettingsStream

        self._active = False  # variable keep track if stream is active/inactive

        # For SPARC: typical user wants density much lower than SEM
        self.pixelSize.value *= 30  # increase default value to decrease default repetition rate

        self.streak_unit = streak_unit
        self.streak_delay = streak_delay

        # the VAs are used in SEMCCDMDStream (_sync.py)
        streak_unit_vas = self._duplicateVAs(streak_unit, "det", streak_unit_vas)
        self._det_vas.update(streak_unit_vas)

        # whenever .streakMode changes
        # -> set .MCPGain = 0 and update .MCPGain.range
        # This is important for HW safety reasons to not destroy the streak unit,
        # when changing on of the VA while using a high MCPGain.
        # While the stream is not active: range of possible values for MCPGain
        # is limited to values <= current value to also prevent HW damage
        # when starting to play the stream again.
        try:
            self.detStreakMode.subscribe(self._OnStreakSettings)
            self.detMCPGain.subscribe(self._OnMCPGain)
        except AttributeError:
            raise ValueError("Necessary HW VAs streakMode and MCPGain for streak camera was not provided")

    # Override Stream.__find_metadata() in _base.py
    def _find_metadata(self, md):
        """
        Find the useful metadata for a 2D spatial projection from the metadata of a raw image.
        :returns: (dict) Metadata dictionary (MD_* -> value).
        """
        simple_md = super(TemporalSpectrumSettingsStream, self)._find_metadata(md)
        if model.MD_TIME_LIST in md:
            simple_md[model.MD_TIME_LIST] = md[model.MD_TIME_LIST]
        if model.MD_WL_LIST in md:
            simple_md[model.MD_WL_LIST] = md[model.MD_WL_LIST]
        return simple_md

    # Override Stream._is_active_setter() in RepetitionStream class and in _base.py
    def _is_active_setter(self, active):
        """
        Called when stream is activated/played. Adapts the MCPGain VA range depending
        on whether the stream is active or not.
        :param active: (boolean) True if stream is playing.
        :returns: (boolean) If stream is playing or not.
        """
        self._active = super(TemporalSpectrumSettingsStream, self)._is_active_setter(active)

        if self.is_active.value != self._active:  # changing from previous value?
            if self._active:
                # make the full MCPGain range available when stream is active
                self.detMCPGain.range = self.streak_unit.MCPGain.range
            else:
                # Set HW MCPGain VA = 0, but keep GUI VA = previous value
                try:
                    self.streak_unit.MCPGain.value = 0
                except Exception:
                    # Can happen if the hardware is not responding. In such case,
                    # let's still pause the stream.
                    logging.exception("Failed to reset the streak unit MCP Gain")

                # only allow values <= current MCPGain value for HW safety reasons when stream inactive
                self.detMCPGain.range = (0, self.detMCPGain.value)
        return self._active

    def _OnStreakSettings(self, value):
        """
        Callback, which sets MCPGain GUI VA = 0,
        if .streakMode VA has changed.
        """
        self.detMCPGain.value = 0  # set GUI VA 0
        self._OnMCPGain(value)  # update the .MCPGain VA

    def _OnMCPGain(self, _=None):
        """
        Callback, which updates the range of possible values for MCPGain GUI VA if stream is inactive:
        only values <= current value are allowed.
        If stream is active the full range is available.
        """
        if not self._active:
            self.detMCPGain.range = (0, self.detMCPGain.value)


class MonochromatorSettingsStream(PMTSettingsStream):
    """
    A stream acquiring a count corresponding to the light at a given wavelength,
    typically with a counting PMT as a detector via a spectrograph.

    The raw data is in count/s.

    It's physically very similar to the Spectrum stream, but as the acquisition
    time is a magnitude shorter (ie, close to the SED), and only one point, the
    live view is different.

    The live view shows the raw data over a period of time, which is the easiest
    to allow configuring the settings correctly. Same as CameraCountStream.
    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        """
        emtvas: don't put resolution or scale, if it will be used with a
          concurrent SEM stream
        """
        super(MonochromatorSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        # Don't change pixel size, as we keep the same as the SEM

        # Fuzzing is not handled for SEM/SEM streams (and doesn't make much
        # sense as it's the same as software-binning
        del self.fuzzing

        # scan stage is not (yet?) handled for SEM/SEM streams
        del self.useScanStage

        # B/C and histogram are meaningless on a chronogram
        del self.auto_bc
        del self.auto_bc_outliers
        del self.histogram
        del self.tint

        # .raw is an array of floats with time on the first dim, and count/date
        # on the second dim.
        self.raw = model.DataArray(numpy.empty((0, 2), dtype=numpy.float64))
        md = {
            model.MD_DIMS: "T",
            model.MD_DET_TYPE: model.MD_DT_NORMAL,
        }
        self.image.value = model.DataArray([], md)  # start with an empty array

        # Time over which to accumulate the data. 0 indicates that only the last
        # value should be included
        self.windowPeriod = model.FloatContinuous(30, range=(0, 1e6), unit="s")

        # TODO: once the semcomedi works with any value, remove this
        if hasattr(self, "emtDwellTime"):
            dt = self.emtDwellTime
            # Recommended > 1ms, but 0.1 ms should work
            dt.value = max(10e-3, dt.value)
            mn, mx = dt.range
            dt.range = (max(0.1e-3, mn), mx)

    def estimateAcquisitionTime(self):
        # 1 pixel => the dwell time (of the emitter)
        duration = self._getScannerVA("dwellTime").value
        # Add the setup time
        duration += self.SETUP_OVERHEAD

        return duration

    # onActive: same as the standard LiveStream (ie, acquire from the dataflow)
    # Note: we assume we are in spot mode, if not the dwell time will be messed up!
    # TODO: if the dwell time is small (eg, < 0.1s), do multiple acquisitions
    # at the same spot (how?)

    def _append(self, count, date):
        """
        Adds a new count and updates the window
        """
        # find first element still part of the window
        oldest = date - self.windowPeriod.value
        first = numpy.searchsorted(self.raw[:, 1], oldest)

        # We must update .raw atomically as _updateImage() can run simultaneously
        new = numpy.array([[count, date]], dtype=numpy.float64)
        self.raw = model.DataArray(numpy.append(self.raw[first:], new, axis=0))

    def _updateImage(self):
        try:
            # convert the list into a DataArray
            raw = self.raw  # read in one shot
            count, date = raw[:, 0], raw[:, 1]
            im = model.DataArray(count)
            # Save the relative time of each point into TIME_LIST, going from
            # negative to 0 (now).
            if len(date) > 0:
                age = date - date[-1]
            else:
                age = date  # empty
            im.metadata[model.MD_TIME_LIST] = age
            im.metadata[model.MD_DIMS] = "T"
            im.metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
            assert len(im) == len(date)
            assert im.ndim == 1

            self.image.value = im
        except Exception:
            logging.exception("Failed to generate chronogram")

    def _onNewData(self, dataflow, data):
        # we absolutely need the acquisition time
        try:
            date = data.metadata[model.MD_ACQ_DATE]
        except KeyError:
            date = time.time()

        # Get one data value
        if data.shape == (1, 1):  # obtained during spot mode?
            d = data[0, 0]
        else:  # obtained during a scan
            logging.debug("Monochromator got %s points instead of 1", data.shape)
            # TODO: cut the data into subparts based on the dwell time
            d = data.view(numpy.ndarray).mean()

        dtyp = data.metadata.get(model.MD_DET_TYPE, model.MD_DT_INTEGRATING)
        if dtyp == model.MD_DT_INTEGRATING:
            # Convert the data from counts to counts/s
            try:
                dt = data.metadata[model.MD_DWELL_TIME]
            except KeyError:
                dt = data.metadata.get(model.MD_EXP_TIME, self.emitter.dwellTime.value)
                logging.warning("No dwell time metadata found in the monochromator data, "
                                "will use %f s", dt)

            d /= dt
            assert isinstance(d, numbers.Real), "%s is not a number" % d

        elif dtyp != model.MD_DT_NORMAL:
            logging.warning("Unknown detector type %s", dtyp)

        self._append(d, date)
        self._shouldUpdateImage()


class PolarizedCCDSettingsStream(CCDSettingsStream):
    """
    A mixin class that is used from ARSettingsStream and AngularSpectrumSettingsStream
    to display a stream with porarization analyzer.
    """
    def __init__(self, name, detector, dataflow, emitter, analyzer=None, **kwargs):
        """
        analyzer (None or Actuator): polarization analyser with a "pol" axis
        """

        super(PolarizedCCDSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        # The attributes are used in SEMCCDMDStream (_sync.py)
        self.analyzer = analyzer
        if analyzer:
            # Hardcode the 6 pol pos + pass-through
            positions = set(POL_POSITIONS) | {MD_POL_NONE}
            # check positions specified in the microscope file are correct
            for pos in positions:
                if pos not in analyzer.axes["pol"].choices:
                    raise ValueError("Polarization analyzer %s misses position '%s'" % (analyzer, pos))
            self.polarization = model.VAEnumerated(MD_POL_NONE, choices=positions)

            # True: acquire all the polarization positions sequentially.
            # False: acquire just the one selected in .polarization .
            self.acquireAllPol = model.BooleanVA(True)

    # onActive & projection: same as the standard LiveStream

    def _linkHwAxes(self):
        """"
        Subscribe polarization VA: link VA to hardware axis.
        Synchronized with stream. Waits until movement is completed.
        """
        super(PolarizedCCDSettingsStream, self)._linkHwAxes()

        if self.analyzer:
            try:
                logging.debug("Moving polarization analyzer to position %s.", self.polarization.value)
                f = self.analyzer.moveAbs({"pol": self.polarization.value})
                f.result()
            except Exception:
                logging.exception("Failed to move polarization analyzer.")
            self.polarization.subscribe(self._onPolarization)
            # TODO: ideally it would also listen to the analyzer.position VA
            # and update the polarization VA whenever the axis has moved

    def _unlinkHwAxes(self):
        """"Unsubscribe polarization VA: unlink VA from hardware axis."""
        super(PolarizedCCDSettingsStream, self)._unlinkHwAxes()

        if self.analyzer:
            self.polarization.unsubscribe(self._onPolarization)

    def _onPolarization(self, pol):
        """
        Move actuator axis for polarization analyzer.
        Not synchronized with stream as stream is already active.
        """
        f = self.analyzer.moveAbs({"pol": pol})
        f.add_done_callback(self._onPolarizationMove)

    def _onPolarizationMove(self, f):
        """
         Callback method, which checks that the move is actually finished.
        :param f: (future)
        """
        try:
            f.result()
        except Exception:
            logging.exception("Failed to move polarization analyzer.")


class ARSettingsStream(PolarizedCCDSettingsStream):
    """
    An angular-resolved stream, for a set of points (on the SEM).
    The live view is just the raw CCD image.
    See StaticARStream for displaying a stream with polar projection.
    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):

        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_AR

        super(ARSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        # Fuzzing doesn't make much sense as it would mostly blur the image
        del self.fuzzing

        # For SPARC: the typical user wants much less pixels than the SEM survey
        # (due to the long exposure time). So make the default pixel size bigger.
        self.pixelSize.value *= 30


class AngularSpectrumSettingsStream(PolarizedCCDSettingsStream):
    """
    An angular-resolved spectrum stream that allows to translate the detector plane to the theta values
    at a given wavelength. This stream is supported by the dimension A in CAZYX.

    The stream uses 2 binnings, a horizontal and a vertical one. They are linked to the camera resolution.

    """

    def __init__(self, name, detector, dataflow, emitter, spectrometer, spectrograph, **kwargs):

        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_EK

        super(AngularSpectrumSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        # Fuzzing is not needed for EK imaging as it would mostly blur the image
        del self.fuzzing

        self.spectrometer = spectrometer
        self.spectrograph = spectrograph

        # For SPARC: typical user wants density much lower than SEM
        self.pixelSize.value *= 30  # increase default value to decrease default repetition rate

        # Instantiates horizontal(spectrum) and vertical(angular) binning
        try:
            hw_choices = detector.binning.choices
            h_choices = {b for b in {1, 2, 4, 8, 16} if any(hb[0] == b for hb in hw_choices)}
            self.spectrum_binning = model.VAEnumerated(1, choices=h_choices)
            v_choices = {b for b in {1, 2, 4, 8, 16} if any(hb[1] == b for hb in hw_choices)}
            self.angular_binning = model.VAEnumerated(1, choices=v_choices)
        except AttributeError:
            logging.info("The VA of the detector.binning doesn't support .choices")
            try:
                hw_range = detector.binning.range
                h_choices = {b for b in {1, 2, 4, 8, 16} if hw_range[0][0] <= b <= hw_range[1][0]}
                self.spectrum_binning = model.VAEnumerated(1, choices=h_choices)
                v_choices = {b for b in {1, 2, 4, 8, 16} if hw_range[0][1] <= b <= hw_range[1][1]}
                self.angular_binning = model.VAEnumerated(1, choices=v_choices)
            except AttributeError:
                logging.info("The VA of the detector.binning doesn't support .range so instantiate read-only VAs "
                             "for both horizontal and vertical binning")
                self.spectrum_binning = model.VigilantAttribute(1, readonly=True)
                self.angular_binning = model.VigilantAttribute(1, readonly=True)

        self.wl_inverted = False  # variable shows if the wavelength list is inverted

        # This is a little tricky: we don't directly need the spectrometer, the
        # 1D image of the CCD, as we are interested in the raw image. However,
        # we care about the wavelengths and the spectrometer might be inverted
        # in order to make sure the wavelength is in the correct direction (ie,
        # lowest pixel = lowest wavelength). So we need to do the same on the
        # raw image. However, there is no "official" way to connect the
        # spectrometer(s) to their raw CCD. So we rely on the fact that
        # typically this is a wrapper, so we can check using the .dependencies.
        try:
            # check transpose in X (1 or -1), and invert if it's inverted (-1)
            self.wl_inverted = (self.spectrometer.transpose[0] == -1)
        except (AttributeError, TypeError) as ex:
            # A very unlikely case where the spectrometer has no .transpose or it's not a tuple
            logging.warning("%s: assuming that the wavelengths are not inverted", ex)

    def _onNewData(self, dataflow, data):
        """
        Stores the dimension order CAZYX in the metadata MD_DIMS. This convention records the data
        in such an order where C is the channel, A is the angle and ZYX the standard axes dimensions.

        Saves the list of angles in the new metadata MD_THETA_LIST, used only when EK imaging
        is applied.

        Calculates the wavelength list and checks whether the highest wavelengths are at the smallest
        indices. In such a case it swaps the wavelength axis of the CCD.
        """
        # Note: we cannot override PIXEL_SIZE as it is needed to compute MD_THETA_LIST
        # during acquisition => Create a new DataArray with a different metadata.
        md = data.metadata.copy()

        md[model.MD_DIMS] = "AC"
        # We compute a basic version of the MD_THETA_LIST corresponding to the
        # center wavelength. This allows the user to confirm that the
        # calibration is still correct (at least roughly). The image is not
        # cropped/corrected (while in the StaticStream only the data with angle
        # is shown, and eventually it'll also be corrected for the chromatic
        # aberration).
        md[model.MD_THETA_LIST] = angleres.ExtractThetaList(data)

        if self.wl_inverted:
            data = data[:,::-1, ...]  # invert C

        # Sets POS and PIXEL_SIZE from the e-beam (which is in spot mode). Useful when taking snapshots.
        epxs = self.emitter.pixelSize.value
        md[model.MD_PIXEL_SIZE] = epxs
        emd = self.emitter.getMetadata()
        pos = emd.get(model.MD_POS, (0, 0))
        trans = self.emitter.translation.value
        md[model.MD_POS] = (pos[0] + trans[0] * epxs[0],
                            pos[1] - trans[1] * epxs[1])  # Y is inverted

        data = model.DataArray(data, metadata=md)
        super(AngularSpectrumSettingsStream, self)._onNewData(dataflow, data)

    def _find_metadata(self, md):
        """
        Finds the useful metadata for a 2D spatial projection from the metadata of a raw image.
        :returns: (dict) Metadata dictionary (MD_* -> value).
        """
        simple_md = super(AngularSpectrumSettingsStream, self)._find_metadata(md)
        if model.MD_THETA_LIST in md:
            simple_md[model.MD_THETA_LIST] = md[model.MD_THETA_LIST]
        if model.MD_WL_LIST in md:
            simple_md[model.MD_WL_LIST] = md[model.MD_WL_LIST]
        return simple_md

    def _is_active_setter(self, active):
        """
        Called when stream is active/playing. Links the angular and spectrum binning VAs to
        the camera resolution VA and the detector binning depending on whether the stream
        is active or not.
        :param active: (boolean) True if stream is playing.
        :returns: (boolean) If stream is playing or not.
        """
        self._active = super(AngularSpectrumSettingsStream, self)._is_active_setter(active)

        if self._active:
            self._linkBin2CamRes()
        else:
            self._unlinkBin2CamRes()
        return self._active

    def _linkBin2CamRes(self):
        """
        Subscribes the detector resolution and binning to the spectrum and angular binning VAs.
        """
        self.angular_binning.subscribe(self._onBinning, init=True)
        self.spectrum_binning.subscribe(self._onBinning, init=True)

    def _unlinkBin2CamRes(self):
        """
        Unsubscribes the detector resolution and binning and update the GUI
        """
        self.angular_binning.unsubscribe(self._onBinning)
        self.spectrum_binning.unsubscribe(self._onBinning)

    def _onBinning(self, _=None):
        """
        Callback, which updates the binning on the detector and calculates spectral resolution
        based on the spectrum and angular binning values.
        Only called when stream is active.
        """
        binning = (self.spectrum_binning.value, self.angular_binning.value)
        try:
            self._detector.binning.value = binning
        except Exception:
            logging.exception("Failed to set the camera binning to %s", binning)

        actual_binning = self._detector.binning.value
        if actual_binning != binning:
            logging.warning("Detector accepted binning %s instead of requested %s",
                            actual_binning, binning)

        try:
            cam_xres = self._detector.shape[0] // actual_binning[0]
            cam_yres = self._detector.shape[1] // actual_binning[1]
            self._detector.resolution.value = (int(cam_xres), int(cam_yres))
        except Exception:
            logging.exception("Failed to set camera resolution on detector %s", self._detector)


class AngularSpectrumAlignmentStream(AngularSpectrumSettingsStream):
    """
    A live stream for EK alignment, which has the same settings as AngularSpectrumSettingsStream
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # No need for the repetition information, as we only do live view
        del self.pixelSize
        del self.repetition

        # Doesn't do acquisition, so cannot acquire multiple polarizations
        if hasattr(self, "acquireAllPol"):
            del self.acquireAllPol

    def _onMagnification(self, mag):
        # Override, to not update the pixelSize (as the parent does)
        pass

    def _onNewData(self, dataflow, data):
        if self.wl_inverted:
            data = data[:,::-1, ...]  # invert C

        super(AngularSpectrumSettingsStream, self)._onNewData(dataflow, data)


class CLSettingsStream(PMTSettingsStream):
    """
    A spatial cathodoluminescense stream, typically with a PMT as a detector.
    It's physically very similar to the AR stream, but as the acquisition time
    is many magnitudes shorter (ie, close to the SED), the live view is the
    entire image.

    In live view, the ROI is not applied, but the pixelSize is.

    Note: It could be possible to acquire an image simultaneously to the
      SED in live view, but they would need to pick one dwell time/resolution.
      That would be tricky to handle when starting/stopping one of the streams.

    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        """
        emtvas: don't put resolution or scale
        """
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_CL
        super(CLSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        # Don't change pixel size, as we keep the same as the SEM

        # Fuzzing is not handled for SEM/SEM streams (and doesn't make much
        # sense as it's the same as software-binning
        del self.fuzzing

        # scan stage is not (yet?) handled for SEM/SEM streams
        del self.useScanStage

        # For the live view, we need a way to define the scale and resolution,
        # but not changing any hardware setting would mean we rely on another
        # stream (bad), requiring local resolution/scale would cause conflicts
        # with repetition/pixelSize, so instead, we just use pixelSize (and the
        # current SEM pixelSize/mag/FoV) to define the scale. The ROI is always
        # full FoV (which is fine for live view).
        self.pixelSize.subscribe(self._onPixelSize)

        try:
            self._getScannerVA("dwellTime").subscribe(self._onDwellTime)
        except AttributeError:
            # if emitter has no dwell time -> no problem
            pass
        try:
            self._getScannerVA("resolution").subscribe(self._onResolution)
        except AttributeError:
            pass

    # projection: same as the standard LiveStream

    def estimateAcquisitionTime(self):
        try:
            # Find out the resolution (it's full FoV, using pixelSize)
            hwpxs = self._emitter.pixelSize.value[0]
            scale = self.pixelSize.value / hwpxs
            res = tuple(int(round(s / scale)) for s in self._emitter.shape[:2])

            # Each pixel x the dwell time (of the emitter) + 20% overhead
            dt = self._getScannerVA("dwellTime").value
            duration = numpy.prod(res) * dt * 1.20
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    def _applyROI(self):
        """
        Update the hardware scale/resolution setting based on the pixelSize
        """
        hwpxs = self._emitter.pixelSize.value[0]
        scale = self.pixelSize.value / hwpxs
        logging.debug("Setting scale to %f, based on pxs = %g m", scale, self.pixelSize.value)
        self._emitter.scale.value = (scale, scale)

        # use full FoV
        res = tuple(int(round(s / scale)) for s in self._emitter.shape[:2])
        self._emitter.resolution.value = res

    def _onPixelSize(self, pxs):
        if self.is_active.value:
            self._applyROI()

    def _onActive(self, active):
        if active:
            self._applyROI()

        super(CLSettingsStream, self)._onActive(active)

    def _onDwellTime(self, value):
        # TODO: restarting the acquisition means also resetting the protection.
        # => don't do anything is protection is active
        self._updateAcquisitionTime()

    def _onResolution(self, value):
        self._updateAcquisitionTime()

    def _onNewData(self, dataflow, data):
        # TODO: read protection status just after acquisition
        # How? Export protection VA from PMT? Have a warning status?
        # protection = self._detector.protection.value
        # And update the stream status if protection was triggered
        super(CLSettingsStream, self)._onNewData(dataflow, data)


# Maximum allowed overlay difference in electron coordinates.
# Above this, the find overlay procedure will consider an error occurred and
# raise an exception
OVRL_MAX_DIFF = 10e-06 # m

class OverlayStream(Stream):
    """
    Fake Stream triggering the fine overlay procedure.

    It's basically a wrapper to the find_overlay function.

    Instead of actually returning an acquired data, it returns an empty DataArray
    with the only metadata being the correction metadata (i.e., MD_*_COR). This
    metadata has to be applied to all the other optical images acquired.
    See img.mergeMetadata() for merging the metadata.
    """

    def __init__(self, name, ccd, emitter, emd, opm=None):
        """
        name (string): user-friendly name of this stream
        ccd (Camera): the ccd
        emitter (Emitter): the emitter (eg: ebeam scanner)
        emd (Detector): the SEM detector (eg: SED)
        """
        self.name = model.StringVA(name)

        # Hardware Components
        self._detector = emd
        self._emitter = emitter
        self._ccd = ccd

        # 0.1s is a bit small, but the algorithm will automatically try with
        # longer dwell times if no spot is visible first.
        self.dwellTime = model.FloatContinuous(0.1,
                                               range=[1e-9, 100],
                                               unit="s")
        # The number of points in the grid
        self.repetition = model.ResolutionVA((4, 4),  # good default
                                             ((2, 2), (16, 16)))

        # Future generated by find_overlay
        self._overlay_future = None

        # Unused, but makes .prepare() happy
        self.is_active = model.BooleanVA(False)
        self._prepared = False
        self._opm = opm

    def estimateAcquisitionTime(self):
        """
        Estimate the time it will take to put through the overlay procedure

        returns (float): approximate time in seconds that overlay will take
        """
        return align.find_overlay.estimateOverlayTime(self.dwellTime.value,
                                                      self.repetition.value)

    def acquire(self):
        """
        Runs the find overlay procedure
        returns Future that will have as a result an empty DataArray with
        the correction metadata
        """
        # Make sure the stream is prepared
        self.prepare().result()

        # Just calls the FindOverlay function and return its future
        ovrl_future = align.FindOverlay(self.repetition.value,
                                        self.dwellTime.value,
                                        OVRL_MAX_DIFF,
                                        self._emitter,
                                        self._ccd,
                                        self._detector,
                                        skew=True,
                                        bgsub=model.hasVA(self._emitter, "blanker"))

        ovrl_future.result = self._result_wrapper(ovrl_future.result)
        return ovrl_future

    def _result_wrapper(self, f):
        """
        Wraps the .result() return value of the Future provided
          by the FindOverlay function to make it return DataArrays, as a normal
          future from a Stream should do.
        """
        @wraps(f)
        def result_as_da(timeout=None):
            trans_val, (opt_md, sem_md) = f(timeout)
            # In case the transformation values are extreme compared to the
            # calibration values just abort them
            f_scale = opt_md[model.MD_PIXEL_SIZE_COR]
            f_rot = -opt_md[model.MD_ROTATION_COR] % (2 * math.pi)
            f_scale_xy = sem_md.get(model.MD_PIXEL_SIZE_COR, (1, 1))
            ccdmd = self._ccd.getMetadata()
            c_scale = ccdmd.get(model.MD_PIXEL_SIZE_COR, (1, 1))
            if model.MD_PIXEL_SIZE_COR in ccdmd:
                max_scale_diff = 1.1
            else:
                max_scale_diff = 3
            c_rot = -ccdmd.get(model.MD_ROTATION_COR, 0) % (2 * math.pi)
            rot_diff = abs(((f_rot - c_rot) + math.pi) % (2 * math.pi) - math.pi)
            scale_diff = max(f_scale[0] / c_scale[0], c_scale[0] / f_scale[0])
            if rot_diff > math.radians(2) or scale_diff > max_scale_diff or any(v > 1.3 for v in f_scale_xy) or any(v < 0.7 for v in f_scale_xy):
                raise ValueError("Overlay failure. There is a significant difference between the calibration "
                                 "and fine alignment values (scale difference: %f, rotation difference: %f, "
                                 "scale ratio xy: %s)"
                                 % (scale_diff, rot_diff, f_scale_xy))

            # The metadata will be used to _update_ the current metadata of the
            # images. We need to be careful on what needs to be left as-is and what
            # needs to be updated. In particular, the fine alignment has some
            # expectation on how the images will be displayed.
            # Optical:
            #  * POS_COR: overridden by fine alignment
            #  * PXS_COR: overridden by fine alignment
            #  * ROT_COR: overridden by fine alignment, see trick below
            #  * SHEAR_COR: fine alignment expects 0 => forced to 0
            # SEM:
            #  * POS_COR: fine alignment expects 0 => forced to 0
            #  * PXS_COR: overridden by fine alignment
            #  * ROT_COR: fine alignment expects 0, see trick below
            #  * SHEAR_COR: overridden by fine alignment
            # For the rotation, normally the SEM has no rotation, and the optical
            # is rotated from fine alignment. However, if the user has manually
            # rotated the SEM scanning, we want to have the rotation on the SEM.
            # So, we first check the SEM rotation (= ROT-ROT_COR), and if it's
            # rotated, rotate the optical image by the same amount.

            # Compensate also for any rotation applied by the user
            emittermd = self._emitter.getMetadata()
            rot_offset = self._emitter.rotation.value - emittermd.get(model.MD_ROTATION_COR, 0)
            if rot_offset != 0:
                logging.warning("The SEM image has been manually rotated by %f", rot_offset)
                opt_md[model.MD_ROTATION_COR] = opt_md[model.MD_ROTATION_COR] - rot_offset

            sem_md[model.MD_POS_COR] = (0, 0)
            opt_md[model.MD_SHEAR_COR] = 0
            # Create an empty DataArray with trans_md as the metadata
            return [model.DataArray([], opt_md), model.DataArray([], sem_md)]

        return result_as_da


class ScannedTCSettingsStream(RepetitionStream):

    def __init__(self, name, detector, emitter, scanner, time_correlator,
                 scanner_extra=None, tc_detector_live=None, **kwargs):
        """
        A helper stream used to define FLIM acquisition settings and run a live setting stream
        that gets a time-series from an APD (tc_detector)

        detector: (model.Detector) a photo-detector, synchronized with the scanner
        emitter: (model.Light) The light (typically a pulsed laser)
        scanner: (model.Emitter) typically laser-mirror
        time_correlator: (model.Detector) typically Symphotime controller
        scanner_extra: (model.Emitter or None) extra scanner that receives the same
           settings as the scanner. Typically, the Symphotime scanner, as it
           needs the information to reconstruct the image.
        tc_detector_live: (model.Detector or None) the detector to use in the
          live mode. Typically a Symphotime Live detector - gets apd counts

        Warning: do not use local .dwellTime, but use the one provided by the stream.
        """
        if tc_detector_live:
            det_live = tc_detector_live
        else:
            det_live = detector
        RepetitionStream.__init__(self, name, det_live, det_live.data, emitter,
                                  scanner, **kwargs)

        # Fuzzing is not handled for FLIM streams (and doesn't make much
        # sense as it's the same as software-binning
        del self.fuzzing

        # scan stage is not (yet?) handled for FLIM streams
        del self.useScanStage

        # B/C and histogram are meaningless on a chronogram
        del self.auto_bc
        del self.auto_bc_outliers
        del self.histogram
        del self.tint

        # Child devices
        self.time_correlator = time_correlator
        self.tc_detector = detector
        self.tc_scanner = scanner_extra

        # VA's
        self.dwellTime = model.FloatContinuous(10e-6, range=(scanner.dwellTime.range[0], 100), unit="s")

        # Raw: series of data (normalized)/acq date (s)
        self.raw = model.DataArray(numpy.empty((0, 2), dtype=numpy.float64))
        md = {
            model.MD_DIMS: "T",
            model.MD_DET_TYPE: model.MD_DT_NORMAL,
        }
        self.image.value = model.DataArray([], md)  # start with an empty array
        # Time over which to accumulate the data. 0 indicates that only the last
        # value should be included
        self.windowPeriod = model.FloatContinuous(30.0, range=(0, 1e6), unit="s")

    def estimateAcquisitionTime(self):
        # 1 pixel => the dwell time (of the emitter)
        duration = self.scanner.dwellTime.value
        # Add the setup time
        duration += self.SETUP_OVERHEAD

        return duration

    # Taken from MonochromatorSettingsStream
    # onActive: same as the standard LiveStream (ie, acquire from the dataflow)
    # Note: we assume we are in spot mode, if not the dwell time will be messed up!
    # TODO: if the dwell time is small (eg, < 0.1s), do multiple acquisitions
    # at the same spot (how?)

    def _append(self, count, date):
        """
        Adds a new count and updates the window
        """
        # find first element still part of the window
        oldest = date - self.windowPeriod.value
        first = numpy.searchsorted(self.raw[:, 1], oldest)

        # We must update .raw atomically as _updateImage() can run simultaneously
        new = numpy.array([[count, date]], dtype=numpy.float64)
        self.raw = model.DataArray(numpy.append(self.raw[first:], new, axis=0))

    def _updateImage(self):
        try:
            # convert the list into a DataArray
            raw = self.raw  # read in one shot
            count, date = raw[:, 0], raw[:, 1]
            im = model.DataArray(count)
            # Save the relative time of each point into TIME_LIST, going from
            # negative to 0 (now).
            if len(date) > 0:
                age = date - date[-1]
            else:
                age = date  # empty
            im.metadata[model.MD_TIME_LIST] = age
            im.metadata[model.MD_DIMS] = "T"
            im.metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
            assert len(im) == len(date)
            assert im.ndim == 1

            self.image.value = im
        except Exception:
            logging.exception("Failed to generate chronogram")

    def _onNewData(self, dataflow, data):
        # we absolutely need the acquisition time
        try:
            date = data.metadata[model.MD_ACQ_DATE]
        except KeyError:
            date = time.time()

        # Get one data value
        if data.shape == (1, 1):  # obtained during spot mode?
            d = data[0, 0]
        else:  # obtained during a scan
            logging.debug("ScannedTCSettingsStream got %s points instead of 1", data.shape)
            d = data.view(numpy.ndarray).mean()

        dtyp = data.metadata.get(model.MD_DET_TYPE, model.MD_DT_INTEGRATING)
        if dtyp == model.MD_DT_INTEGRATING:
            # Convert the data from counts to counts/s
            try:
                dt = data.metadata[model.MD_DWELL_TIME]
            except KeyError:
                dt = data.metadata.get(model.MD_EXP_TIME, self.scanner.dwellTime.value)
                logging.warning("No dwell time metadata found in the ScannedTCSettings data, "
                                "will use %f s", dt)

            d /= dt
            assert isinstance(d, numbers.Real), "%s is not a number" % d

        elif dtyp != model.MD_DT_NORMAL:
            logging.warning("Unknown detector type %s", dtyp)

        self._append(d, date)
        self._shouldUpdateImage()
        
    def _setPower(self, value):
        # set all light power at once to a value
        pw = list(self.emitter.power.range[1])
        pw = [value * p for p in pw]
        self.emitter.power.value = pw

    def _onActive(self, active):
        if active: 
            # set power values
            self._setPower(1)
        else:
            # stop power values
            self._setPower(0)
            
        RepetitionStream._onActive(self, active)


class ScannedTemporalSettingsStream(CCDSettingsStream):
    """
    Stream that allows to acquire a 2D spatial map with the time correlator for lifetime mapping or g(2) mapping.
    """
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_TEMPORAL
        super(ScannedTemporalSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
    
        # typical user wants density much lower than SEM
        self.pixelSize.value *= 30
        
        # Fuzzing not supported (yet)
        del self.fuzzing

        # scan stage is not (yet?) handled by SEMTemporalMDStreams
        del self.useScanStage

        # B/C and histogram are meaningless on a spectrum
        del self.auto_bc
        del self.auto_bc_outliers
        del self.histogram
        del self.tint

        # Contains one 1D spectrum (start with an empty array)
        self.image.value = model.DataArray([])

    def _updateImage(self):
        if not self.raw:
            return

        # Just copy the raw data into the image, removing useless extra dimensions
        # TODO: support data with different shape than XT
        # tindex = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim::])
        im = self.raw[0][0, :]
        im.metadata = im.metadata.copy()
        im.metadata[model.MD_DIMS] = "T"
        self.image.value = im

    # No histogram => no need to do anything to update it
    @staticmethod
    def _histogram_thread(wstream):
        pass

    def _onNewData(self, dataflow, data):
        # Set POS and PIXEL_SIZE from the e-beam (which is in spot mode)
        epxs = self.emitter.pixelSize.value
        data.metadata[model.MD_PIXEL_SIZE] = epxs
        emd = self.emitter.getMetadata()
        pos = emd.get(model.MD_POS, (0, 0))
        trans = self.emitter.translation.value
        data.metadata[model.MD_POS] = (pos[0] + trans[0] * epxs[0],
                                       pos[1] - trans[1] * epxs[1])  # Y is inverted
        super(ScannedTemporalSettingsStream, self)._onNewData(dataflow, data)
