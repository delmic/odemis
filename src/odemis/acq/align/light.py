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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
from odemis.model import CancellableFuture, logging
import numpy
from odemis.util import executeAsyncTask, img
import threading


def turnOnLight(bl, ccd):
    """
    Turn on a light and wait until it detects a signal change on the ccd
    bl (Light): the light, which should initially be turned off.
    ccd (DigitalCamera): detector to use to check for the signal
    returns (CancellableFuture): a future that will finish when a significant
      signal increase is detected. Can also be used to stop the procedure. 
    """
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
    Canceller of turnLight task.
    """
    logging.debug("Cancelling turnLight...")
    with f._task_lock:
        if f._task_state == FINISHED:
            logging.debug("The task already finished")
            return False
        f._task_state = CANCELLED
        logging.debug("Task cancelled.")
    return True


def _doTurnOnLight(f, bl, ccd):
    try:
        # We need the light to be off, so that we can notice a difference when
        # it turns on.
        # In case it's already turned on, just assume everything is fine.
        if sum(bl.power.value) > 0:
            logging.debug("The light is already on")
            return
        if f._task_state == CANCELLED:
            raise CancelledError()

        # Light turned off, if indeed it's all "black", the avg intensity should
        # roughly correspond to the maximum noise level.
        img_light_off = ccd.data.get(asap=False)
        avg_intensity_off = numpy.average(img_light_off)
        # Intensity which is for sure not caused by noise: +150% of the noise level
        intensity_min_on = avg_intensity_off * 1.5 + 0.1
        logging.debug("Looking for intensity of %s in an %s image", intensity_min_on, img_light_off.shape)
        # Turn the light on, full power!
        bl.power.value = bl.power.range[1]
        while True:
            img2 = ccd.data.get()
            try:
                new_img = img.Subtract(img2, img_light_off)
            except ValueError: # could happen if CCD changed resolution
                new_img = img2 - avg_intensity_off
            if f._task_state == CANCELLED:
                raise CancelledError()
            # number of pixels with higher intensity than the avg minimum
            pixels_high_intensity = numpy.sum(new_img > intensity_min_on)
            # the percent of pixels that have intensity higher than the avg minimum
            a = pixels_high_intensity / new_img.size
            # check whether this percent is larger than 0.5% which indicates that the light is on
            if a > 0.005:
                logging.debug("Detected light on (%f %% pixels above %f)", a * 100, intensity_min_on)
                break
            logging.debug("No light detected (%f %% pixels above %f)", a * 100, intensity_min_on)

    except CancelledError:
        raise  # Just don't log the exception
    except Exception:
        logging.exception("Failure while turning on light %s", bl.name)
        raise
    finally:
        with f._task_lock:
            if f._task_state == CANCELLED:
                raise CancelledError()
            f._task_state = FINISHED
