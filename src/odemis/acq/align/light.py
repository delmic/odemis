# -*- coding: utf-8 -*-
"""
This file is part of Odemis.

Created on 25 February 2019

@author: Victoria Mavrikopoulou

Copyright © 2019 Victoria Mavrikopoulou, Delmic

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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
from odemis.model import CancellableFuture, logging
import numpy
from odemis.util import executeAsyncTask, img
import threading


def turnOnLight(bl, ccd):
    f = CancellableFuture()
    f.task_canceller = _cancelTurnOnLight
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f._was_stopped = False  # if cancel was successful
    # Run in separate thread
    executeAsyncTask(f, _doTurnOnLight, args=(f, bl, ccd))
    return f


def _cancelTurnOnLight(f):
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


def _doTurnOnLight(f, bl, ccd):
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
        logging.debug("Minimum avg intensity of %s in an %s image", avg_intensity_min_on, img_light_off.shape)
        # Turn the light on
        bl.power.value = bl.power.range[1]
        bl.emissions.value = [1] * len(bl.emissions.value)
        while True:
            img2 = ccd.data.get()
            try:
                new_img = img.Subtract(img2, img_light_off)
            except ValueError: # could happen if CCD changed resolution
                new_img = img2 - avg_intensity_off
            if f._task_state == CANCELLED:
                raise CancelledError()
            # number of pixels with higher intensity than the avg minimum
            pixels_high_intensity = numpy.sum(new_img > avg_intensity_min_on)
            # the percent of pixels that have intensity higher than the avg minimum
            a = pixels_high_intensity / new_img.size
            # check whether this percent is larger than 0.5% which indicates that the light is on
            if a > 0.005:
                logging.debug("Detected light on (%f %% pixels above %f)", a * 100, avg_intensity_min_on)
                break
            logging.debug("No light detected (%f %% pixels above %f)", a * 100, avg_intensity_min_on)

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