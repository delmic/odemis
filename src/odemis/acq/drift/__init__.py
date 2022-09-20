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

import itertools
import logging
import math
import numpy
from scipy import misc
import threading
import cv2

from odemis.acq.align.shift import MeasureShift

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

        # Latest drift vector from the previous acquisition
        self.drift = (0, 0)  # in sem px
        # Total drift vector from the first acquisition
        self.tot_drift = (0, 0)  # in sem px
        # Maximum distance drifted from the first acquisition
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

        # Compute margin so that it's always possible to place the ROI within the
        # scanner FoV: half the ROI resolution (at scale 1).
        margin = ((self._res[0] * self._scale[0]) // 2,
                  (self._res[1] * self._scale[1]) // 2)
        trans_rng = scanner.translation.range
        self._trans_range = ((trans_rng[0][0] + margin[0], trans_rng[0][1] + margin[1]),
                             (trans_rng[1][0] - margin[0], trans_rng[1][1] - margin[1]))

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
        To read the value again, use .drift.
        To read the total drift since the first acquisition, use .tot_drift
        return (float, float): estimated extra drift in X/Y SEM px since last
          estimation.
        """
        # Calculate the drift between the last two frames and
        # between the last and first frame
        if len(self.raw) > 1:
            # Note: prev_drift and drift, don't represent exactly the same
            # value as the previous image also had drifted. So we need to
            # include also the drift of the previous image.
            # Also, MeasureShift return the shift in image pixels, which is
            # different (usually bigger) from the SEM px.
            prev_drift = MeasureShift(self.raw[-2], self.raw[-1], 10)
            prev_drift = (prev_drift[0] * self._scale[0] + self.drift[0],
                          prev_drift[1] * self._scale[1] + self.drift[1])

            orig_drift = MeasureShift(self.raw[0], self.raw[-1], 10)
            self.drift = (orig_drift[0] * self._scale[0],
                          orig_drift[1] * self._scale[1])

            logging.debug("Current drift: %s", self.drift)
            logging.debug("Previous frame diff: %s", prev_drift)
            if (abs(self.drift[0] - prev_drift[0]) > 5 * self._scale[0] or
                    abs(self.drift[1] - prev_drift[1]) > 5 * self._scale[1]):
                # TODO: in such case, add the previous and current image to .raw
                logging.warning("Drift cannot be measured precisely, "
                                "hesitating between %s and %s px",
                                self.drift, prev_drift)

            # Update drift since the original position
            self.tot_drift = (self.tot_drift[0] + self.drift[0],
                              self.tot_drift[1] + self.drift[1])

            # Update maximum drift
            if math.hypot(*self.tot_drift) > math.hypot(*self.max_drift):
                self.max_drift = self.tot_drift

        return self.drift

    def estimateAcquisitionTime(self):
        """
        return (float): estimated time to acquire 1 anchor area
        """
        anchor_time = numpy.prod(self._res) * self._dwell_time + 0.01

        return anchor_time

    @staticmethod
    def estimateCorrectionPeriod(period, t, repetitions):
        """
        Convert the correction period (as a time) into a number of acquisitions.
        :param period: (float) Maximum time between acquisition of the anchor region in seconds.
        :param t: (float) Time spend for acquiring one data at the fastest dimension.
        :param repetitions: (tuple of 2 ints) Number of iterations for the two fastest axes in the
                                              entire drift-corrected acquisition. First value is the
                                              fastest dimension scanned. Usually it is the
                                              number of px (ebeam) positions. Sometimes fastest dimension
                                              can be e.g. number of images to integrate.
        :returns (iterator yielding 0<int): Iterator which yields number of acquisitions until
                                            next correction.
        """
        # TODO: implement more clever calculation
        acq_dc_period = []
        acq = int(period // t)  # number of acquisitions per period
        acq_per_fastest_dim = repetitions[0]
        if acq >= acq_per_fastest_dim:
            # Correct every (acq // acq_per_fastest_dim) acquisitions
            acq_dc_period.append((acq // acq_per_fastest_dim) * acq_per_fastest_dim)
        elif acq <= 1:  # also catches cases that would be 1,1,2,1,...
            # Correct every pixel
            acq_dc_period.append(1)
        else:
            # Correct every X or X+1 pixel
            # number of acquisition per line
            nacq = int((acq_per_fastest_dim * t) // period)
            # average duration of a period when fitted to the fastest dimension of total acquisition
            avgp = acq_per_fastest_dim / nacq
            tot_acq = 0  # total acquisitions rounded down
            for i in range(1, nacq):
                prev_tot_acq = tot_acq
                tot_acq = int(avgp * i)
                acq_dc_period.append(tot_acq - prev_tot_acq)
            else:
                # last one explicit, to avoid floating point errors
                acq_dc_period.append(acq_per_fastest_dim - tot_acq)

        logging.debug("Drift correction will be performed every %s sub acquisitions", acq_dc_period)

        return itertools.cycle(acq_dc_period)

    def _updateSEMSettings(self):
        """
        Update the scanning area of the SEM according to the anchor region
        for drift correction.
        """
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        # we clip translation inside of bounds in case of huge drift
        trans = (self._trans[0] - self.drift[0],
                 self._trans[1] - self.drift[1])
        self._trans = (max(self._trans_range[0][0], min(trans[0], self._trans_range[1][0])),
                       max(self._trans_range[0][1], min(trans[1], self._trans_range[1][1])))
        if trans != self._trans:
            logging.warning("Generated image may be incorrect due to extensive "
                            "drift of %s clipped to %s", trans, self._trans)

        # always in this order
        self._emitter.scale.value = self._scale
        self._emitter.resolution.value = self._res
        self._emitter.translation.value = self._trans
        self._emitter.dwellTime.value = self._dwell_time


def GuessAnchorRegion(whole_img, sample_region):
    """
    It detects a region with clean edges, proper for drift measurements. This region
    must not overlap with the sample that is to be scanned due to the danger of
    contamination.
    whole_img (ndarray): 2d array with the whole SEM image
    sample_region (tuple of 4 floats): roi of the sample in order to avoid overlap
    returns (tuple of 4 floats): roi of the anchor region
    """
    # Drift correction region shape
    dc_shape = (50, 50)

    # Scale the image between 0 and 255 to have it properly modified for cv2.Canny
    uint8_img = (255 - 0) * ((whole_img - numpy.min(whole_img)) / (numpy.max(whole_img) - numpy.min(whole_img))) + 0
    uint8_img = numpy.round(uint8_img).astype(numpy.uint8)

    # Generates black/white image that contains only the edges
    cannied_img = cv2.Canny(uint8_img, 100, 200)

    # Mask the sample_region plus a margin equal to the half of dc region and
    # a margin along the edges of the whole image again equal to the half of
    # the anchor region. Thus we keep pixels that we can use as center of our
    # anchor region knowing that it will not overlap with the sample region
    # and it will not be outside of bounds
    masked_img = cannied_img

    # Clip between the bounds
    l = sorted((0, int(sample_region[0] * whole_img.shape[0] - dc_shape[0] / 2),
                whole_img.shape[0]))[1]
    r = sorted((0, int(sample_region[2] * whole_img.shape[0] + dc_shape[0] / 2),
                whole_img.shape[0]))[1]
    t = sorted((0, int(sample_region[1] * whole_img.shape[1] - dc_shape[1] / 2),
                whole_img.shape[1]))[1]
    b = sorted((0, int(sample_region[3] * whole_img.shape[1] + dc_shape[1] / 2),
                whole_img.shape[1]))[1]
    masked_img[l:r, t:b] = 0
    masked_img[0:(dc_shape[0] // 2), :] = 0
    masked_img[:, 0:(dc_shape[1] // 2)] = 0
    masked_img[masked_img.shape[0] - (dc_shape[0] // 2):masked_img.shape[0], :] = 0
    masked_img[:, masked_img.shape[1] - (dc_shape[1] // 2):masked_img.shape[1]] = 0

    # Find indices of edge pixels
    occurrences_indices = numpy.where(masked_img == 255)
    X = numpy.expand_dims(occurrences_indices[0], axis=1)
    Y = numpy.expand_dims(occurrences_indices[1], axis=1)
    occurrences = numpy.hstack([X, Y])

    # If there is such a pixel outside of the sample region and there is enough
    # space according to dc_shape, use the masked image and calculate the anchor
    # region roi
    if len(occurrences) > 0:
        # Enough space outside of the sample region
        anchor_roi = ((occurrences[0, 0] - (dc_shape[0] / 2)) / whole_img.shape[0],
                      (occurrences[0, 1] - (dc_shape[1] / 2)) / whole_img.shape[1],
                      (occurrences[0, 0] + (dc_shape[0] / 2)) / whole_img.shape[0],
                      (occurrences[0, 1] + (dc_shape[1] / 2)) / whole_img.shape[1])

    else:
        # Not enough space outside of the sample region
        # Pick a random pixel
        cannied_img = cv2.Canny(uint8_img, 100, 200)
        # Find indices of edge pixels
        occurrences_indices = numpy.where(cannied_img == 255)
        X = numpy.expand_dims(occurrences_indices[0], axis=1)
        Y = numpy.expand_dims(occurrences_indices[1], axis=1)
        occurrences = numpy.hstack([X, Y])
        anchor_roi = ((occurrences[0, 0] - (dc_shape[0] / 2)) / whole_img.shape[0],
                      (occurrences[0, 1] - (dc_shape[1] / 2)) / whole_img.shape[1],
                      (occurrences[0, 0] + (dc_shape[0] / 2)) / whole_img.shape[0],
                      (occurrences[0, 1] + (dc_shape[1] / 2)) / whole_img.shape[1])

    return anchor_roi
