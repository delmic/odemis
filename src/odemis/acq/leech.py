# -*- coding: utf-8 -*-
'''
Created on 28 Sep 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# This contains LeechAcquirers. The basic idea of these objects is that they
# can do some small extra acquisition in parallel to a standard acquisition
# by a Stream. One specific feature is that they can also be aware of the
# acquisition of the whole series of streams, done by the acquisition manager.

from __future__ import division

import logging
import math
import numpy
from odemis import model
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
        logging.debug("Returning a %d lines, as %d pixels requested", ny, n)
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
    def start(self, dt, shape):
        """
        Called before an acquisition starts.
        Note: it can access the hardware, but if it modifies the settings of the
          hardware, it should put them back afterwards so that the main
          acquisition runs as expected.
        dt (0 < float): The expected duration between two pixels (assuming there
           is no "leech"). IOW, the integration time.
        shape (tuple of 0<int): The number of pixels to acquire. When there are
          several dimensions, the last ones are scanned fastest.
        return (1<=int or None): how many pixels before .next() should be called
        """
        self._dt = dt
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

    def series_completed(self, das):
        """
        Called after the last acquisition
        das (list of DataArrays): the data which has just been acquired. It
          might be modified by this function.
        """
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
