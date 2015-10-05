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


def _DoAutoFocus(future, detector, min_step, et, focus, dfbkg):
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
    returns (float):    Focus position (m)
                        Focus level
    raises:
            CancelledError if cancelled
            IOError if procedure failed
    """
    logging.debug("Starting Autofocus...")

    try:
        rng = focus.axes["z"].range
        # Pick measurement method
        if detector.role == "ccd":
            Measure = MeasureOpticalFocus
        else:
            Measure = MeasureSEMFocus

        best_pos = 0
        best_fm = 0
        step_factor = 80
        step_cntr = 1

        while step_factor >= 1 and step_cntr <= MAX_STEPS_NUMBER:
            # Keep the initial focus position
            center = focus.position.value.get('z')
            image = AcquireNoBackground(detector, dfbkg)
            fm_center = Measure(image)
            logging.debug("Focus level at %f is %f", center, fm_center)
            if fm_center > best_fm:
                best_pos = center
                best_fm = fm_center

            # Move to right position
            right = _ClippedMove(rng, focus, step_factor * min_step)
            image = AcquireNoBackground(detector, dfbkg)
            fm_right = Measure(image)
            logging.debug("Focus level at %f is %f", right, fm_right)
            if fm_right > best_fm:
                best_pos = right
                best_fm = fm_right

            # Move to left position
            left = _ClippedMove(rng, focus, -2 * step_factor * min_step)
            image = AcquireNoBackground(detector, dfbkg)
            fm_left = Measure(image)
            logging.debug("Focus level at %f is %f", left, fm_left)
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

            f = focus.moveAbs({"z": pos_max})
            f.result()
            step_cntr += 1

    except CancelledError:
        pos = focus.position.value.get('z')
        shift = best_pos - pos
        new_pos = _ClippedMove(rng, focus, shift)
    finally:
        with future._autofocus_lock:
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            future._autofocus_state = FINISHED


def _ClippedMove(rng, focus, shift):
    """
    Clips the focus move requested within the range
    """
    cur_pos = focus.position.value.get('z')
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


def AutoFocus(detector, emt, focus, dfbkg=None):
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
    for c in (detector, emt):
        if hasattr(c, "depthOfField") and isinstance(c.depthOfField, model.VigilantAttributeBase):
            dof = c.depthOfField.value
            break
    else:
        logging.debug("No depth of field info found")
        dof = 1e-6  # m, not too bad value
    logging.debug("Depth of field is %f", dof)
    min_stp_sz = dof

    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAutoFocusTime(et))
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()
    f.task_canceller = _CancelAutoFocus

    # Run in separate thread
    autofocus_thread = threading.Thread(target=executeTask,
                                        name="Autofocus",
                                        args=(f, _DoAutoFocus, f, detector, min_stp_sz,
                                              et, focus, dfbkg))

    autofocus_thread.start()
    return f
