# -*- coding: utf-8 -*-
"""
Created on 27 Feb 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Éric Piel & Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from __future__ import division

import itertools
import logging
import numpy
import threading
import math

from .calculation import CalculateDrift
from .dc_region import GuessAnchorRegion


MIN_RESOLUTION = (20, 20) # seems 10x10 sometimes work, but let's not tent it
MAX_PIXELS = 128 ** 2  # px

class AnchoredEstimator(object):
    """
    Drift estimator based on an "anchor" area. Periodically, a small region
    (the anchor) is scanned. By comparing the images of the anchor area over
    time, an estimation of the drift is computed.

    To use, call .acquire() periodically (and preferably at specific places of
    the global acquire, such as at the beginning of a line), and call .estimate()
    to measure the drift.
    """
    def __init__(self, scanner, detector, region, dwell_time):
        """
        scanner (Emitter)
        detector (Detector)
        region (4 floats)
        dwell_time (float)
        """
        self._emitter = scanner
        self._semd = detector
        self._dwell_time = dwell_time
        self.orig_drift = (0, 0) # in sem px
        self.max_drift = (0, 0) # in sem px
        self.raw = []  # first 2 and last 2 anchor areas acquired (in order)
        self._acq_sem_complete = threading.Event()

        # Calculate initial translation for anchor region acquisition
        self._roi = region
        center = ((self._roi[0] + self._roi[2]) / 2,
                  (self._roi[1] + self._roi[3]) / 2)
        width = (self._roi[2] - self._roi[0], self._roi[3] - self._roi[1])
        shape = self._emitter.shape

        # translation is distance from center (situated at 0.5, 0.5), can be floats
        self._trans = (shape[0] * (center[0] - 0.5),
                       shape[1] * (center[1] - 0.5))

        # resolution is the maximum resolution at the scale in proportion of the width
        # First, try the finest scale (=1)
        self._res = (max(1, int(round(shape[0] * width[0] / 1))),
                     max(1, int(round(shape[1] * width[1] / 1))))

        # Demand large enough anchor region for drift calculation
        if self._res[0] < MIN_RESOLUTION[0] or self._res[1] < MIN_RESOLUTION[1]:
            old_res = tuple(self._res)
            self._res = tuple(max(a, b) for a, b in zip(self._res, MIN_RESOLUTION))
            logging.warning("Anchor region too small %s, will be set to %s",
                            old_res, self._res)

        # Adjust the scale to the anchor region so the image has at the
        # maximum MAX_PIXELS. This way we guarantee that the pixels density of
        # the anchor region is enough to calculate the drift and at the same
        # time to avoid prolonged exposure times that extremely increase the
        # acquisition time.
        ratio = math.sqrt(numpy.prod(self._res) / MAX_PIXELS)
        self._scale = scanner.scale.clip((ratio, ratio))

        # adjust resolution based on the new scale
        self._res = (max(MIN_RESOLUTION[0], int(round(shape[0] * width[0] / self._scale[0]))),
                     max(MIN_RESOLUTION[1], int(round(shape[1] * width[1] / self._scale[1]))))

        logging.info("Anchor region defined with scale=%s, res=%s, trans=%s",
                      self._scale, self._res, self._trans)

        self._safety_bounds = (0.99 * (shape[0] / 2), 0.99 * (shape[1] / 2))
        self._min_bound = -self._safety_bounds[0] + (max(self._res[0],
                                                        self._res[1]) / 2)
        self._max_bound = self._safety_bounds[1] - (max(self._res[0],
                                                        self._res[1]) / 2)

    def acquire(self):
        """
        Scan the anchor area
        """
        # Save current SEM settings
        cur_dwell_time = self._emitter.dwellTime.value
        cur_scale = self._emitter.scale.value
        cur_resolution = self._emitter.resolution.value
        cur_trans = self._emitter.translation.value

        try:
            self._updateSEMSettings()
            logging.debug("E-beam spot to anchor region: %s",
                          self._emitter.translation.value)
            logging.debug("Scanning anchor region with resolution "
                          "%s and dwelltime %s and scale %s",
                          self._emitter.resolution.value,
                          self._emitter.dwellTime.value,
                          self._emitter.scale.value)
            data = self._semd.data.get(asap=False)
            if data.shape[::-1] != self._res:
                logging.warning("Shape of data is %s instead of %s", data.shape[::-1], self._res)

            # TODO: allow to record just every Nth image, and separately record the
            # drift after every measurement
            # In the mean time, we only save the 1st, 2nd and last two images
            if len(self.raw) > 2:
                self.raw = self.raw[0:2] + self.raw[-1:]
            else:
                self.raw = self.raw[0:2]
            self.raw.append(data)
        finally:
            # Restore SEM settings
            self._emitter.dwellTime.value = cur_dwell_time
            self._emitter.scale.value = cur_scale
            self._emitter.resolution.value = cur_resolution
            self._emitter.translation.value = cur_trans

    def estimate(self):
        """
        Estimate the additional drift since previous acquisition+estimation.
        Note: It should be only called once after every acquisition.
        To read the value again, use .orig_drift.
        return (float, float): estimated current drift in X/Y SEM px
        """
        # Calculate the drift between the last two frames and
        # between the last and first frame
        if len(self.raw) > 1:
            # Note: prev_drift and orig_drift, don't represent exactly the same
            # value as the previous image also had drifted. So we need to
            # include also the drift of the previous image.
            # Also, CalculateDrift return the shift in image pixels, which is
            # different (usually bigger) from the SEM px.
            prev_drift = CalculateDrift(self.raw[-2], self.raw[-1], 10)
            prev_drift = (prev_drift[0] * self._scale[0] + self.orig_drift[0],
                          prev_drift[1] * self._scale[1] + self.orig_drift[1])

            orig_drift = CalculateDrift(self.raw[0], self.raw[-1], 10)
            self.orig_drift = (orig_drift[0] * self._scale[0],
                               orig_drift[1] * self._scale[1])

            logging.debug("Current drift: %s", self.orig_drift)
            logging.debug("Previous frame diff: %s", prev_drift)
            if (abs(self.orig_drift[0] - prev_drift[0]) > 5 or
                abs(self.orig_drift[1] - prev_drift[1]) > 5):
                logging.warning("Drift cannot be measured precisely, "
                                "hesitating between %s and %s px",
                                 self.orig_drift, prev_drift)
            # Update max_drift
            if math.hypot(*self.orig_drift) > math.hypot(*self.max_drift):
                self.max_drift = self.orig_drift

        return self.orig_drift

    def estimateAcquisitionTime(self):
        """
        return (float): estimated time to acquire 1 anchor area
        """
        roi = self._roi

        width = (roi[2] - roi[0], roi[3] - roi[1])
        shape = self._emitter.shape
        res = (max(1, int(round(shape[0] * width[0]))),
               max(1, int(round(shape[1] * width[1]))))
        anchor_time = min(numpy.prod(res), MAX_PIXELS) * self._dwell_time + 0.01

        return anchor_time

    @staticmethod
    def estimateCorrectionPeriod(period, dwell_time, repetitions):
        """
        Convert the correction period (as a time) into a number of pixel
        period (float): maximum time between acquisition of the anchor region
          in seconds.
        dwell_time (float): integration time of each pixel in the drift-
          corrected acquisition.
        repetitions (tuple of 2 ints): number of pixel in the entire drift-
          corrected acquisition.
          First value is the fastest dimension scanned (X).
        return (iterator yielding 0<int): iterator which yields number of pixels
          until next correction
        """
        # TODO: implement more clever calculation
        pxs_dc_period = []
        pxs = int(period // dwell_time) # number of pixels per period
        pxs_per_line = repetitions[0]
        if pxs >= pxs_per_line:
            # Correct every (pxs // pxs_per_line) lines
            pxs_dc_period.append((pxs // pxs_per_line) * pxs_per_line)
        elif pxs <= 1: # also catches cases that would be 1,1,2,1,...
            # Correct every pixel
            pxs_dc_period.append(1)
        else:
            # Correct every X or X+1 pixel
            # number of acquisition per line
            nacq = int((pxs_per_line * dwell_time) // period)
            # average duration of a period when fitted to the line
            avgp = pxs_per_line / nacq
            tot_pxi = 0 # total pixels rounded down
            for i in range(1, nacq):
                prev_tot_pxi = tot_pxi
                tot_pxi = int(avgp * i)
                pxs_dc_period.append(tot_pxi - prev_tot_pxi)
            else:
                # last one explicit, to avoid floating point errors
                pxs_dc_period.append(pxs_per_line - tot_pxi)

        logging.debug("Drift correction will be performed every %s pixels",
                      pxs_dc_period)
        return itertools.cycle(pxs_dc_period)

    def _updateSEMSettings(self):
        """
        Update the scanning area of the SEM according to the anchor region
        for drift correction.
        """
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        # we clip translation inside of bounds in case of huge drift
        new_translation = (self._trans[0] - self.orig_drift[0],
                           self._trans[1] - self.orig_drift[1])

        if (abs(new_translation[0]) > self._safety_bounds[0]
            or abs(new_translation[1]) > self._safety_bounds[1]):
            logging.warning("Generated image may be incorrect due to extensive "
                            "drift of %s", new_translation)

        self._trans = (numpy.clip(new_translation[0], self._min_bound, self._max_bound),
                       numpy.clip(new_translation[1], self._min_bound, self._max_bound))

        # always in this order
        self._emitter.scale.value = self._scale
        self._emitter.resolution.value = self._res
        self._emitter.translation.value = self._trans
        self._emitter.dwellTime.value = self._dwell_time

