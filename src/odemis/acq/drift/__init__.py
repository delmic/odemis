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
from .calculation import CalculateDrift
from .dc_region import GuessAnchorRegion
from odemis.util import TimeoutError
from itertools import cycle

import logging
import numpy
import threading

# to identify a ROI which must still be defined by the user
UNDEFINED_ROI = (0, 0, 0, 0)
MIN_RESOLUTION = (20, 20) # seems 10x10 sometimes work, but let's not tent it

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
        self._scale = (1, 1) # TODO: allow the user to select different scale
        self.orig_drift = (0, 0)
        self.max_drift = (0, 0)
        self.raw = [] # all the anchor areas acquired (in order)
        self._acq_sem_complete = threading.Event()
        
        # Calculate initial translation for anchor region acquisition
        self._roi = region
        center = ((self._roi[0] + self._roi[2]) / 2,
                  (self._roi[1] + self._roi[3]) / 2)
        width = (self._roi[2] - self._roi[0], self._roi[3] - self._roi[1])
        shape = self._emitter.shape

        # translation is distance from center (situated at 0.5, 0.5), can be floats
        self._trans = (shape[0] * (center[0] - 0.5) - self.orig_drift[1],
                       shape[1] * (center[1] - 0.5) - self.orig_drift[0])
        # self._transl = (shape[0] * (center[0] - 0.5) - drift[1],
        #                 shape[1] * (center[1] - 0.5) - drift[0])

        # resolution is the maximum resolution at the scale in proportion of the width
        self._res = (max(1, int(round(shape[0] * width[0]))),
                     max(1, int(round(shape[1] * width[1]))))

        # Demand large enough anchor region for drift calculation
        if self._res[0] < 50:
            self._res = (MIN_RESOLUTION[0], self._res[1])
        elif self._res[1] < 50:
            self._res[1] = (self._res[1], MIN_RESOLUTION[1])

        self._safety_bounds = (-0.99 * (shape[0] / 2), 0.99 * (shape[1] / 2))
        self._min_bound = self._safety_bounds[0] + (max(self._res[0],
                                                        self._res[1]) / 2)
        self._max_bound = self._safety_bounds[1] - (max(self._res[0],
                                                        self._res[1]) / 2)

    def acquire(self):
        """
        Scan the anchor area
        """
        if self._roi != UNDEFINED_ROI:
            # Save current SEM settings
            cur_dwell_time = self._emitter.dwellTime.value
            cur_scale = self._emitter.scale.value
            cur_resolution = self._emitter.resolution.value

            self._updateSEMSettings()
            logging.debug("E-beam spot to anchor region: %s",
                          self._emitter.translation.value)
            logging.debug("Scanning anchor region with resolution "
                          "%s and dwelltime %s and scale %s",
                          self._emitter.resolution.value,
                          self._emitter.dwellTime.value,
                          self._emitter.scale.value)
            data = self._semd.data.get()
            if data.shape == (1, 1):
                data = self._semd.data.get()
            self.raw.append(data)

            # Restore SEM settings
            self._emitter.dwellTime.value = cur_dwell_time
            self._emitter.scale.value = cur_scale
            self._emitter.resolution.value = cur_resolution


    def estimate(self):
        """
        return (float, float): estimated current drift in X/Y SEM px
        """
        # Calculate the drift between the last two frames and
        # between the last and first frame
        if len(self.raw) > 1:
            prev_drift = CalculateDrift(self.raw[-2],
                                        self.raw[-1], 10)
            self.orig_drift = CalculateDrift(self.raw[0],
                                             self.raw[-1], 10)

            logging.debug("Current drift: %s", self.orig_drift)
            logging.debug("Previous frame diff: %s", prev_drift)
            if (abs(self.orig_drift[0] - prev_drift[0]) > 5 or
                abs(self.orig_drift[1] - prev_drift[1]) > 5):
                logging.warning("Drift cannot be measured precisely, "
                                "hesitating between %s and %s px",
                                 self.orig_drift, prev_drift)

            # Update max_drift
            if abs(numpy.prod(self.orig_drift)) > abs(numpy.prod(self.max_drift)):
                self.max_drift = self.orig_drift
        return self.orig_drift
    
    def estimateAcquisitionTime(self):
        """
        return (float): estimated time to acquire 1 anchor area
        """
        roi = self._roi
        if roi == UNDEFINED_ROI:
            return 0

        width = (roi[2] - roi[0], roi[3] - roi[1])
        shape = self._emitter.shape
        res = (max(1, int(round(shape[0] * width[0]))),
               max(1, int(round(shape[1] * width[1]))))
        anchor_time = numpy.prod(res) * self._dwell_time + 0.01

        return anchor_time

    def estimateCorrectionPeriod(self, period, dwell_time, repetitions):
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
        pxs = period // dwell_time # number of pixels per period
        pxs_per_line = repetitions[0]
        if pxs > pxs_per_line:
            # Correct every (pxs // pxs_per_line) lines
            pxs_dc_period.append((pxs // pxs_per_line) * pxs_per_line)
        else:
            half_rep = pxs_per_line // 2
            if pxs < half_rep:
                # Correct every pixel
                pxs_dc_period.append(1)
            else:
                # Correct every half a line
                pxs_dc_period.append(half_rep)
                pxs_dc_period.append(pxs_per_line - half_rep)

        logging.debug("Drift correction will be being performed every %s pixels",
                        pxs_dc_period)
        return cycle(pxs_dc_period)

    def _updateSEMSettings(self):
        """
        Update the scanning area of the SEM according to the anchor region
        for drift correction.
        """
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        # we clip translation inside of bounds in case of huge drift
        new_translation = (self._trans[0] - self.orig_drift[0],
                           self._trans[1] - self.orig_drift[1])

        if (abs(new_translation[0]) > abs(self._safety_bounds[0])
            or abs(new_translation[1]) > abs(self._safety_bounds[1])):
            logging.warning("Generated image may be incorrect due to extensive "
                            "drift of %s", new_translation)

        self._trans = (numpy.clip(new_translation[0], self._min_bound, self._max_bound),
                       numpy.clip(new_translation[1], self._min_bound, self._max_bound))

        # always in this order
        self._emitter.scale.value = self._scale
        self._emitter.resolution.value = self._res
        self._emitter.translation.value = self._trans
        self._emitter.dwellTime.value = self._dwell_time

