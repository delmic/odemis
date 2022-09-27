# -*- coding: utf-8 -*-
'''
Created on 28 Sep 2017

@author: Éric Piel

Copyright © 2017-2018 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# This contains LeechAcquirers. The basic idea of these objects is that they
# can do some small extra acquisition in parallel to a standard acquisition
# by a Stream. One specific feature is that they can also be aware of the
# acquisition of the whole series of streams, done by the acquisition manager.

from past.builtins import long
import logging
import math
import numpy
from odemis import model
from odemis.acq import drift
from odemis.acq.stream import UNDEFINED_ROI
import time


# Helper function for code using the leeches
def get_next_rectangle(shape, current, n):
    """
    Compute the biggest number of pixels that can be acquired next, while keeping
    it either a sub-line of the X axis, or a couple of lines in Y.
    shape (1<=int, 1<=int): Y and X
    current (0<=int): number of pixels acquired so far
    n (1<=int): maximum number of pixels to acquire.
    return (1<=ny, 1<=nx): number of pixels in Y and X to scan. If ny > 1, nx
    is always rep[1]. nx * ny <= n. It also ensures it will not run out of the
    shape.
    """
    if current >= shape[0] * shape[1]:
        raise ValueError("Already %d pixels acquired over %s, and asked for %d more" % (
                         current, shape, n))

    # Position of the first next pixel
    cy, cx = current // shape[1], current % shape[1]

    # Force a sub-line if not starting at the beginning of a line
    if cx > 0:
        logging.debug("Return a sub-line as current pos is at %d,%d", cx, cy)
        return 1, min(n, shape[1] - cx)

    # Acquire a set of lines if number of pixels to acquire is more than a line
    if n < shape[1]:
        logging.debug("Returning a sub-line, as only %d pixels requested", n)
        return 1, min(n, shape[1] - cx)
    else:
        nx = shape[1]
        ny = min(n // shape[1], shape[0] - cy)
        logging.debug("Returning %d lines, as %d pixels requested", ny, n)
        return ny, nx


class LeechAcquirer(object):
    """
    Small acquisition, which depends on an another acquisition to run.
    """
    def __init__(self):
        # The values of the current acquisition
        self._dt = None  # s
        self._shape = None  # tuple of int

    def estimateAcquisitionTime(self, dt, shape):
        """
        Compute an approximation of how long the leech will increase the
        time of an acquisition.
        return (0<float): time in s added to the whole acquisition
        """
        return 0

    # TODO: also pass the stream?
    def start(self, acq_t, shape):
        """
        Called before an acquisition starts.
        Note: it can access the hardware, but if it modifies the settings of the
          hardware, it should put them back afterwards so that the main
          acquisition runs as expected.
        acq_t (0 < float): The expected duration between two acquisitons (assuming there is no "leech").
        shape (tuple of 0<int): The number of pixels to acquire. When there are
          several dimensions, the last ones are scanned fastest.
        return (1<=int or None): how many pixels before .next() should be called
        """
        self._dt = acq_t
        self._shape = shape

        return None

    def next(self, das):
        """
        Called after the Nth pixel has been acquired.
        das (list of DataArrays): the data which has just been acquired. It
          might be modified by this function.
        return (1<=int or None): how many pixels before .next() should be called
         Note: it may return more pixels than what still needs to be acquired.
        """
        return None

    # TODO: return extra das, to add to the current list of das
    def complete(self, das):
        """
        Called after the last pixel has been acquired, and the data processed
        das (list of DataArrays): the data which has just been acquired. It
          might be modified by this function.
        """
        return None

    def series_start(self):
        """
        Called before any acquisition has started
        """
        pass

    def series_complete(self, das):
        """
        Called after the last acquisition
        das (list of DataArrays): the data which has just been acquired. It
          might be modified by this function.
        """
        return None


class AnchorDriftCorrector(LeechAcquirer):
    """
    Acquires regularly a Region-Of-Interest, and detects the position change to
    estimate current drift.
    """

    def __init__(self, scanner, detector):
        """
        :param scanner: (Emitter) A component with a .dwellTime, .translation, .scale.
        :param detector: (Detector) To acquire the signal.
        """
        super(AnchorDriftCorrector, self).__init__()
        self._scanner = scanner
        self._detector = detector
        self._dc_estimator = None
        self._period_acq = None  # number of acq left until next drift correction is performed

        # roi: the anchor region, it must be set to something different from
        #  UNDEFINED_ROI to run.
        # dwellTime: dwell time used when acquiring anchor region
        # period is the (approximate) time between two acquisition of the
        #  anchor (and drift compensation). The exact period is determined so
        #  that it fits with the region of acquisition.
        # Note: the scale used for the acquisition of the anchor region is
        #  selected by the AnchoredEstimator, to be as small as possible while
        #  still not scanning too many pixels.
        self.roi = model.TupleContinuous(UNDEFINED_ROI,
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float),
                                         setter=self._setROI)
        self.dwellTime = model.FloatContinuous(scanner.dwellTime.range[0],
                                               range=scanner.dwellTime.range, unit="s")
        # in seconds, default to "fairly frequent" to work hopefully in most cases
        self.period = model.FloatContinuous(10, range=(0.1, 1e6), unit="s")

    @property
    def drift(self):
        """
        Latest drift vector from the previous acquisition, in sem px
        """
        return self._dc_estimator.drift

    @property
    def tot_drift(self):
        """
        Total drift vector from the first acquisition, in sem px
        """
        return self._dc_estimator.tot_drift

    @property
    def max_drift(self):
        """
        Maximum distance drifted from the first acquisition, in sem px
        """
        return self._dc_estimator.max_drift

    @property
    def raw(self):
        """
        first 2 and last 2 anchor areas acquired (in order)
        """
        return self._dc_estimator.raw

    def _setROI(self, roi):
        """
        Called when the .roi is set
        """
        logging.debug("drift corrector ROI set to %s", roi)
        if roi == UNDEFINED_ROI:
            return roi  # No need to discuss it

        width = (roi[2] - roi[0], roi[3] - roi[1])
        center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        # Ensure the ROI is at least as big as the MIN_RESOLUTION
        # (knowing it always uses scale = 1)
        shape = self._scanner.shape
        min_width = [r / s for r, s in zip(drift.MIN_RESOLUTION, shape)]
        width = [max(a, b) for a, b in zip(width, min_width)]

        # Recompute the ROI so that it fits
        roi = [center[0] - width[0] / 2, center[1] - width[1] / 2,
               center[0] + width[0] / 2, center[1] + width[1] / 2]

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

        return tuple(roi)

    def estimateAcquisitionTime(self, acq_t, shape):
        """
        Compute an approximation of how long the leech will increase the time of an acquisition.
        :param acq_t: (float) Time spend for acquiring one data at the fastest dimension.
        :param shape: (tuple of int): Dimensions are sorted from slowest to fasted axis for acquisition.
                                      It always includes the number of pixel positions to be acquired (y, x).
                                      Other dimensions can be multiple images that are acquired per pixel
                                      (ebeam) position.
        :returns: (0<float) Time in s added to the whole acquisition.
        """
        if self.roi.value == UNDEFINED_ROI:
            return 0

        dce = drift.AnchoredEstimator(self._scanner, self._detector,
                                      self.roi.value, self.dwellTime.value)

        # only pass the two fastest axes of the acquisition to retrieve the period when a leech should be run again
        period = dce.estimateCorrectionPeriod(self.period.value, acq_t, shape[-2:])
        nimages = numpy.prod(shape)
        n_anchor = 1 + nimages // next(period)
        # number of times the anchor will be acquired * anchor acquisition time
        return n_anchor * dce.estimateAcquisitionTime()

    def series_start(self):
        """
        Creates the drift estimator object for the acquisition series and
        runs a first acquisition of the anchor region.
        """
        if self.roi.value == UNDEFINED_ROI:
            raise ValueError("AnchorDriftCorrector.roi is not defined")

        self._dc_estimator = drift.AnchoredEstimator(self._scanner,
                                                     self._detector,
                                                     self.roi.value,
                                                     self.dwellTime.value)

        # First acquisition of anchor area
        self._dc_estimator.acquire()

    def series_complete(self, das):
        """
        Erases the drift estimator, when the acquisition series is completed.
        """
        self._dc_estimator = None

    def start(self, acq_t, shape):
        """
        Called before an acquisition starts.
        Note: It can access the hardware, but if it modifies the settings of the
              hardware, it should put them back afterwards so that the main
              acquisition runs as expected.
        :param acq_t: (0 < float) The expected duration between two acquisitons (assuming there
                      is no "leech").
        :param shape: (tuple of 0<int) Dimensions are sorted from slowest to fasted axis for acquisition.
                      It always includes the number of pixel positions to be acquired (y, x).
                      Other dimensions can be multiple images that are acquired per pixel
                      (ebeam) position.
        :returns: (1<=int or None) How many acquisitions before .next() should be called and
                  the next drift correction needs to be run.
        """
        assert self._dc_estimator is not None
        # TODO: automatically call series_start if it hasn't been called?
        # That would be handy for basic acquisition?
#         if not self._dc_estimator:
#             logging.warning("start() called before series_start(), will call it now")
#             self.series_start()

        super(AnchorDriftCorrector, self).start(acq_t, shape)
        self._period_acq = self._dc_estimator.estimateCorrectionPeriod(self.period.value, acq_t, shape[-2:])

        # Skip if the last acquisition is very recent (compared to the period)
        try:
            last_acq_date = self._dc_estimator.raw[-1].metadata[model.MD_ACQ_DATE]
            if last_acq_date > time.time() - self.period.value:
                logging.debug("Skipping DC estimation at acquisition start, as latest anchor is still fresh")

                return next(self._period_acq)
        except KeyError:  # No MD_ACQ_DATE => no short-cut
            pass

        # Same as a standard acquisition == acquire + estimate
        return self.next([])

    def next(self, das):
        """
        Called after the Nth acquisition has been performed.
        :param das: (list of DataArrays) The data which has just been acquired. It
                    might be modified by this function.
        :returns: (1<=int or None) How many sub-acquisitions before .next() should be called
                  Note: it may return more sub-acquisitions than what still needs to be acquired.
        """
        assert self._period_acq is not None

        # Acquisition of anchor area & estimate drift
        # Cannot cancel during this time, but hopefully it's short
        self._dc_estimator.acquire()
        self._dc_estimator.estimate()

        # TODO: if next() would mean all the acquisitions, skip the last call by returning None
        return next(self._period_acq)

    def complete(self, das):
        """
        Called after the last sub-acquisition has been performed, and the data processed
        :param das: (list of DataArrays) The data which has just been acquired.
                    It might be modified by this function.
        """
        self._period_acq = None
        # TODO: add (a copy of) self.raw to the das? Or in series_complete()
        return None


class ProbeCurrentAcquirer(LeechAcquirer):
    """
    Acquires probe current regularly, and attaches the reading as a metadata to
    the data of each acquisition.
    """
    def __init__(self, detector, selector=None):
        """
        detector (Detector): the probe current detector (which has a data of shape
         (1,)
        selector (Actuator or None): If available, will use it to "activate" the
          probe current detector. It requires a "x" axis with True/False choices,
          and where True is active.
        """
        LeechAcquirer.__init__(self)

        self._detector = detector
        self._selector = selector
        if selector:
            if "x" not in selector.axes:
                raise ValueError("Selector needs a x axis")
            choices = selector.axes["x"].choices
            if isinstance(choices, set) and not {True, False} <= choices:
                raise ValueError("Selector axis x has not True and False positions")
            # TODO: also handle choices as a dict val -> True/False

        # How often the acquisition should be done
        # At least one acquisition at init, and one at the end will run anyway
        self.period = model.FloatContinuous(60, (1e-9, 3600), unit="s")

        # For computing the next pixels
        self._pixels = 0  # How many pixels acquired so far
        self._acqs = 0  # How many (internal) acquisitions done so far
        self._pxs = 1  # (1<=float) number of pixels per period
        self._tot_acqs = 1  # How many acquisitions should be done (including the first one)

        # Ordered measurements done so far
        self._measurements = []  # (float, float) -> time, current (Amp)

    def estimateAcquisitionTime(self, dt, shape):
        # It's pretty easy to know how many times the leech will run, it's a lot
        # harder to know how long it takes to acquire one probe current reading.

        nacqs = 1 + math.ceil(dt * numpy.prod(shape) / self.period.value)
        if model.hasVA(self._detector, "dwellTime"):
            at = self._detector.dwellTime.value
        else:
            at = 0.1
        if self._selector:
            # The time it takes probably depends a lot on the hardware, and
            # there is not much info (maybe the .speed could be used).
            # For now, we just use the time the only hardware we support takes
            at += 3.0 * 2  # doubled as it has go back and forth

        at += 0.1  # for overhead

        return nacqs * at

    def _get_next_pixels(self):
        """
        Computes how many pixels before next period
        return (int or None)
        """
        tot_px = numpy.prod(self._shape)
        if self._pxs > tot_px:
            # Call back a second time, at the end
            return tot_px

        self._acqs += 1
        if self._acqs > self._tot_acqs:
            logging.warning("Unexpected to be called so many times (%d)", self._acqs)

        px_goal = min(int(self._pxs * self._acqs), tot_px)
        np = px_goal - self._pixels
        self._pixels = px_goal

        if np < 1:
            # Normally happens on the last call, and returning anything should
            # be fine, but just to be sure, return something safe
            return 1
        return np

    def _acquire(self):
        logging.debug("Acquiring probe current #%d", self._acqs)
        if self._selector:
            self._selector.moveAbsSync({"x": True})

        # TODO: check that it always works, even if the ebeam is not scanning.
        # (if it's not scanning, the blanker might be active, which might prevent
        # the reading to work). => force a sem scan?

        try:
            d = self._detector.data.get()
            ts = d.metadata.get(model.MD_ACQ_DATE, time.time())
            val = float(d)  # works both if shape is () and (1)
            self._measurements.append((ts, val))
        except Exception:
            logging.exception("Failed to acquire probe current")
            # Don't fail the real acquisition
        finally:
            if self._selector:
                self._selector.moveAbsSync({"x": False})

    def start(self, dt, shape):
        LeechAcquirer.start(self, dt, shape)

        # Re-initialise
        self._pixels = 0
        self._acqs = 0
        self._measurements = []

        # Compute how often and how many times it should run
        tot_px = numpy.prod(self._shape)
        pxs = max(1, self.period.value / self._dt)
        acqs = math.ceil(tot_px / pxs)
        self._pxs = tot_px / acqs  # spread evenly

        # If more than 2 lines at a time, round it to N lines
        if len(shape) >= 2 and self._pxs > 2 * shape[-1]:
            nlines = self._pxs // shape[-1]
            logging.debug("Rounding probe current acquisition to %d lines", nlines)
            self._pxs = nlines * shape[-1]
            acqs = math.ceil(tot_px / self._pxs)

        self._tot_acqs = 1 + acqs

        np = self._get_next_pixels()
        logging.debug("Probe current acquisition every ~%s pixels", np)

        self._acquire()
        return np

    def next(self, das):
        np = self._get_next_pixels()
        self._acquire()
        return np

    def complete(self, das):
        # Store the measurements inside the metadata of each DA
        md_pb = {model.MD_EBEAM_CURRENT_TIME: self._measurements}
        for da in das:
            da.metadata.update(md_pb)

    # Nothing special for series
