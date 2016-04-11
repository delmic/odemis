# -*- coding: utf-8 -*-
"""
Created on 11 Apr 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import cv2
import logging
import numpy
from odemis import model
from odemis.acq._futures import executeTask
from odemis.util.img import Subtract
from scipy import ndimage
import threading
import time


MAX_STEPS_NUMBER = 100  # Max steps to perform autofocus
MAX_BS_NUMBER = 1  # Maximum number of applying binary search with a smaller max_step


def MeasureSEMFocus(image):
    """
    Given an image, focus measure is calculated using the standard deviation of
    the raw data.
    image (model.DataArray): SEM image
    returns (float):    The focus level of the SEM image
    """
    # Handle RGB image
    if len(image.shape) == 3:
        # TODO find faster solution
        r, g, b = image[:, :, 0], image[:, :, 1], image[:, :, 2]
        gray = numpy.empty(image.shape[0:2], dtype="uint16")
        gray[...] = r
        gray += g
        gray += b
    else:
        gray = image

    return ndimage.standard_deviation(gray)


def MeasureOpticalFocus(image):
    """
    Given an image, focus measure is calculated using the variance of Laplacian
    of the raw data.
    image (model.DataArray): Optical image
    returns (float):    The focus level of the optical image
    """
    # Handle RGB image
    if len(image.shape) == 3:
        # TODO find faster solution
        r, g, b = image[:, :, 0], image[:, :, 1], image[:, :, 2]
        gray = numpy.empty(image.shape[0:2], dtype="uint16")
        gray[...] = r
        gray += g
        gray += b
    else:
        gray = image

    return cv2.Laplacian(gray, cv2.CV_64F).var()


def AcquireNoBackground(ccd, dfbkg=None):
    """
    Performs optical acquisition with background subtraction if possible.
    Particularly used in order to eliminate the e-beam source background in the
    Delphi.
    ccd (model.DigitalCamera): detector from which to acquire an image
    dfbkg (model.DataFlow or None): dataflow of se- or bs- detector to
    start/stop the source. If None, a standard acquisition is performed (without
    background subtraction)
    returns (model.DataArray):
        Image (with subtracted background if requested)
    """
    if dfbkg is not None:
        bg_image = ccd.data.get(asap=False)
        dfbkg.subscribe(_discard_data)
        image = ccd.data.get(asap=False)
        dfbkg.unsubscribe(_discard_data)
        ret_data = Subtract(image, bg_image)
        return ret_data
    else:
        image = ccd.data.get(asap=False)
        return image


def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass


def _DoAutoFocus(future, detector, min_step, et, focus, dfbkg, good_focus):
    """
    Iteratively acquires an optical image, measures its focus level and adjusts
    the optical focus with respect to the focus level.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector: model.DigitalCamera or model.Detector
    min_step (float): minimum step size used, equal to depth of field (m)
    thres_factor: threshold factor depending on type of detector and binning
    et (float): acquisition time (s) of one image exposure time if detector is a ccd,
        dwellTime*prod(resolution) if detector is an SEM
    focus (model.Actuator): The optical focus
    dfbkg (model.DataFlow): dataflow of se- or bs- detector
    good_focus (float): if provided, an already known good focus position to be
      taken into consideration while autofocusing
    returns (float):    Focus position (m)
                        Focus level
    raises:
            CancelledError if cancelled
            IOError if procedure failed
    """
    # It does a dichotomy search on the focus level. In practice, it means it
    # will start going into the direction that increase the focus with big steps
    # until the focus decreases again. Then it'll bounce back and forth with
    # smaller and smaller steps.
    # The tricky parts are:
    # * it's hard to estimate the focus level (on a random image)
    # * two acquisitions at the same focus position can have (slightly) different
    #   focus levels (due to noise and sample degradation)
    # * if the focus actuator is not precise (eg, open loop), it's hard to
    #   even go back to the same focus position when wanted
    logging.debug("Starting Autofocus...")

    try:
        max_reached = False  # True once we've passed the maximum level (ie, start bouncing)
        # It's used to cache the focus level, to avoid reacquiring at the same
        # position. We do it only for the 'rough' max search because for the fine
        # search, the actuator and acquisition delta are likely to play a role
        focus_levels = {}  # focus pos (float) -> focus level (float)

        best_pos = focus.position.value['z']
        best_fm = 0

        rng = focus.axes["z"].range
        # Pick measurement method
        if detector.role == "ccd":
            Measure = MeasureOpticalFocus
        else:
            Measure = MeasureSEMFocus

        step_factor = 2 ** 7
        if good_focus is not None:
            current_pos = focus.position.value['z']
            image = AcquireNoBackground(detector, dfbkg)
            fm_current = Measure(image)
            logging.debug("Focus level at %f is %f", current_pos, fm_current)
            f = focus.moveAbs({"z": good_focus})
            f.result()
            image = AcquireNoBackground(detector, dfbkg)
            fm_good = Measure(image)
            logging.debug("Focus level at %f is %f", good_focus, fm_good)
            if fm_good < fm_current:
                # Move back to current position if good_pos is not that good
                # after all
                f = focus.moveAbs({"z": current_pos})
                # it also means we are pretty close
            step_factor = 2 ** 4

        logging.debug("Step factor used for autofocus: %d", step_factor)
        step_cntr = 1
        while step_factor >= 1 and step_cntr <= MAX_STEPS_NUMBER:
            # Start at the current focus position
            center = focus.position.value['z']
            if not max_reached and center in focus_levels:
                fm_center = focus_levels[center]
            else:
                image = AcquireNoBackground(detector, dfbkg)
                fm_center = Measure(image)
                logging.debug("Focus level at %f is %f", center, fm_center)
                focus_levels[center] = fm_center

            if fm_center > best_fm:
                best_pos = center
                best_fm = fm_center

            # Move to right position
            right = center + step_factor * min_step
            if not max_reached and right in focus_levels:
                fm_right = focus_levels[right]
            else:
                right = _ClippedMove(rng, focus, step_factor * min_step)
                image = AcquireNoBackground(detector, dfbkg)
                fm_right = Measure(image)
                logging.debug("Focus level at %f is %f", right, fm_right)
                focus_levels[right] = fm_right

            if fm_right > best_fm:
                best_pos = right
                best_fm = fm_right

            # Move to left position
            left = center - step_factor * min_step
            if not max_reached and left in focus_levels:
                fm_left = focus_levels[left]
            else:
                left = _ClippedMove(rng, focus, -2 * step_factor * min_step)
                image = AcquireNoBackground(detector, dfbkg)
                fm_left = Measure(image)
                logging.debug("Focus level at %f is %f", left, fm_left)
                focus_levels[left] = fm_left

            if fm_left > best_fm:
                best_pos = left
                best_fm = fm_left

            fm_range = [fm_left, fm_center, fm_right]
            pos_range = [left, center, right]
            if future._autofocus_state == CANCELLED:
                raise CancelledError()

            fm_max = max(fm_range)
            i_max = fm_range.index(fm_max)
            pos_max = pos_range[i_max]

            # if best focus was found at the center
            if i_max == 1:
                step_factor = step_factor // 2

            focus.moveAbsSync({"z": pos_max})
            step_cntr += 1

        if step_cntr == MAX_STEPS_NUMBER:
            logging.info("Auto focus gave up after %d steps @ %g m", step_cntr, best_pos)
        else:
            logging.info("Auto focus found best level %g @ %g m", best_fm, best_pos)

        focus.moveAbsSync({"z": best_pos})
        return best_pos, best_fm

    except CancelledError:
        # Go to the best position known so far
        focus.moveAbsSync({"z": best_pos})
    finally:
        with future._autofocus_lock:
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            future._autofocus_state = FINISHED


def _ClippedMove(rng, focus, shift):
    """
    Clips the focus move requested within the range
    """
    cur_pos = focus.position.value['z']
    test_pos = cur_pos + shift
    if rng[0] >= test_pos:
        diff = rng[0] - cur_pos
        f = focus.moveRel({"z": diff})
    elif test_pos >= rng[1]:
        diff = rng[1] - cur_pos
        f = focus.moveRel({"z": diff})
    else:
        f = focus.moveRel({"z": shift})
    f.result()
    final_pos = focus.position.value.get('z')
    return final_pos


def _CancelAutoFocus(future):
    """
    Canceller of _DoAutoFocus task.
    """
    logging.debug("Cancelling autofocus...")

    with future._autofocus_lock:
        if future._autofocus_state == FINISHED:
            return False
        future._autofocus_state = CANCELLED
        logging.debug("Autofocus cancelled.")

    return True


def estimateAutoFocusTime(exposure_time, steps=MAX_STEPS_NUMBER):
    """
    Estimates overlay procedure duration
    """
    return steps * exposure_time


def AutoFocus(detector, emt, focus, dfbkg=None, good_focus=None):
    """
    Wrapper for DoAutoFocus. It provides the ability to check the progress of autofocus 
    procedure or even cancel it.
    detector (model.DigitalCamera or model.Detector): Detector on which to
      improve the focus quality
    emt (None or model.Emitter): In case of a SED this is the scanner used
    focus (model.Actuator): The focus actuator
    dfbkg (model.DataFlow or None): If provided, will be used to start/stop
     the e-beam emission (it must be the dataflow of se- or bs-detector) in
     order to do background subtraction. If None, no background subtraction is
     performed.
    good_focus (float): if provided, an already known good focus position to be
      taken into consideration while autofocusing
    returns (model.ProgressiveFuture):  Progress of DoAutoFocus, whose result() will return:
            Focus position (m)
            Focus level
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    # Check if the emitter is a scanner (focusing = SEM)
    if hasattr(emt, "dwellTime") and isinstance(emt.dwellTime, model.VigilantAttributeBase):
        et = emt.dwellTime.value * numpy.prod(emt.resolution.value)
    else:
        et = detector.exposureTime.value

    # use the .depthOfField on detector or emitter as maximum stepsize
    avail_depths = (detector, emt)
    if focus.role == "ebeam-focus":
        avail_depths = (emt, detector)
    for c in avail_depths:
        if hasattr(c, "depthOfField") and isinstance(c.depthOfField, model.VigilantAttributeBase):
            dof = c.depthOfField.value
            break
    else:
        logging.debug("No depth of field info found")
        dof = 1e-6  # m, not too bad value
    logging.debug("Depth of field is %f", dof)
    min_stp_sz = dof / 2

    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAutoFocusTime(et))
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()
    f.task_canceller = _CancelAutoFocus

    # Run in separate thread
    autofocus_thread = threading.Thread(target=executeTask,
                                        name="Autofocus",
                                        args=(f, _DoAutoFocus, f, detector, min_stp_sz,
                                              et, focus, dfbkg, good_focus))

    autofocus_thread.start()
    return f
