# -*- coding: utf-8 -*-
"""
Created on 18 April 2014

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

import logging
from odemis.model import MD_PIXEL_SIZE
from .autofocus import AutoFocus, AutoFocusSpectrometer
from .find_overlay import FindOverlay
from .spot import AlignSpot, FindSpot
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
from odemis.model import CancellableFuture
import numpy
from odemis.util import executeAsyncTask
import threading
from odemis.util.img import Subtract


def FindEbeamCenter(ccd, detector, escan):
    """
    Locate the center of the SEM image by setting the SEM to spot mode and
    measuring the position of the spot on the CCD. It is mostly targeted at
    doing it fast. In particular it doesn’t do any focusing or multiple
    iterations with feedback loop.
    ccd (model.DigitalCamera):    The ccd
    detector (model.Detector):    The se-detector
    escan (model.Emitter):    The e-beam scanner
    return (tuple of 2 floats): x, y position of the spot relative to the
     center of the CCD.
    raise:
        LookupError: if the spot cannot be found
    """
    logging.debug("Starting ebeam spot detection...")
    # save the hw settings
    prev_exp = ccd.exposureTime.value
    prev_bin = ccd.binning.value
    prev_res = ccd.resolution.value

    try:
        # set the CCD to maximum resolution
        ccd.binning.value = (1, 1)
        ccd.resolution.value = ccd.resolution.range[1]

        # set ebeam to spot mode
        # resolution -> translation: order matters
        escan.resolution.value = (1, 1)

        # put a not too short dwell time to avoid acquisition to keep repeating,
        # and not too long to avoid using too much memory for acquiring one point.
        escan.dwellTime.value = escan.dwellTime.range[1]  # s
        # Subscribe to actually set the spot mode
        detector.data.subscribe(discard_data)

        exp = 0.1  # start value
        prev_img = None
        while exp < 2:  # above 2 s it means something went wrong
            ccd.exposureTime.value = exp

            img = ccd.data.get(asap=False)
            if prev_img is not None:
                img += prev_img  # accumulate, to increase the signal

            try:
                coord = FindSpot(img, sensitivity_limit=10)
            except LookupError:
                # spot was not found, subtract background and try again
                logging.debug("Subtracting background image")
                detector.data.unsubscribe(discard_data)
                bg_image = ccd.data.get(asap=False)
                detector.data.subscribe(discard_data)
                img = Subtract(img, bg_image)
                try:
                    coord = FindSpot(img, sensitivity_limit=10)
                except LookupError:
                    # try again with longer exposure time
                    prev_img = img
                    exp *= 2
                    continue

            # found a spot! => convert position to meters from center
            return _ConvertCoordinates(coord, img)

    finally:
        detector.data.unsubscribe(discard_data)
        ccd.exposureTime.value = prev_exp
        ccd.binning.value = prev_bin
        ccd.resolution.value = prev_res

    raise LookupError("Failed to locate spot after exposure time %g s" % exp)


def _ConvertCoordinates(coord, img):
    """
    Converts position to meters from center
    """
    pxs = img.metadata[MD_PIXEL_SIZE]
    center = (img.shape[1] / 2, img.shape[0] / 2)  # shape is Y,X
    pos = (-(coord[0] - center[0]) * pxs[0],
            (coord[1] - center[1]) * pxs[1])  # physical Y is opposite direction
    return pos


def discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass


def turnOnLightAndCheck(bl, ccd):
    f = CancellableFuture()
    f.task_canceller = _cancelTurnOnLightAndCheck
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f._was_stopped = False  # if cancel was successful
    # Run in separate thread
    executeAsyncTask(f, _doTurnOnLightAndCheck, args=(f, bl, ccd))
    return f


def _cancelTurnOnLightAndCheck(f):
    """
    Canceller of turnLightAndCheck task.
    """
    logging.debug("Cancelling turnLightAndCheck...")
    with f._task_lock:
        if f._task_state == FINISHED:
            logging.debug("The task already finished")
            return False
        f._task_state = CANCELLED
        logging.debug("Task cancelled.")
    return True


def _doTurnOnLightAndCheck(f, bl, ccd):
    try:
        # check if the light is already turned on. The light should normally be turned off.
        # In case it's already turned on return and start the procedure of autofocus
        if bl.emissions.value[0] * bl.power.value != 0:
            logging.debug("The light is already on")
            return
        if f._task_state == CANCELLED:
            raise CancelledError()

        # Light turned off, take the avg intensity of the image
        img_light_off = ccd.data.get(asap=False)
        avg_intensity_off = numpy.average(img_light_off)
        # set the avg minimum intensity - a significant change (+50%)
        avg_intensity_min_on = avg_intensity_off * 1.5 + 0.1
        # Turn the light on
        bl.power.value = bl.power.range[1]
        bl.emissions.value = [1] * len(bl.emissions.value)
        while True:
            img2 = ccd.data.get()
            # number of pixels with higher intensity than the avg minimum
            pixels_high_intensity = numpy.sum(img2 > avg_intensity_min_on)
            img_size = img2.shape[0]*img2.shape[1]
            # the percent of pixels that have intensity higher than the avg minimum
            a = pixels_high_intensity/img_size
            if f._task_state == CANCELLED:
                raise CancelledError()
            # check whether this percent is larger than 0.5% which indicates that the light is on
            if (a > 0.005):
                logging.debug("The light is on")
                break


    except CancelledError:
        raise  # Just don't log the exception
    except Exception:
        logging.exception("Failure")
        raise
    finally:
        with f._task_lock:
            if f._task_state == CANCELLED:
                raise CancelledError()
            f._task_state = FINISHED