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
import logging
import numpy
from odemis import model
from odemis.acq._futures import executeTask
from scipy import ndimage
import threading
import time
from odemis.util.img import Subtract
import cv2

INIT_THRES_FACTOR = 4e-3  # initial autofocus threshold factor

MAX_STEPS_NUMBER = 30  # Max steps to perform autofocus
MAX_BS_NUMBER = 1  # Maximum number of applying binary search with a smaller max_step


def MeasureSpotFocus(image):
    """
    Given a CL spot image, focus measure is calculated using the number of
    circles detected. The lower the number of circles detected the more focused
    is the image considered to be.
    image (model.DataArray): Optical image
    returns (float):    The focus level of the optical image
    """
    nd = numpy.array(image, dtype=numpy.uint8)
    nda = model.DataArray(nd, metadata=image.metadata)
    img = cv2.medianBlur(nda, 5)
    circles = cv2.HoughCircles(img, cv2.cv.CV_HOUGH_GRADIENT, dp=1, minDist=20, param1=50,
                               param2=15, minRadius=0, maxRadius=100)
    if circles is None:
        level = 0
    else:
        # Focus level is inverse to the number of circles detected. We also
        # multiply by a factor to make it more distinct.
        level = (1 / circles.shape[1]) * 1e03

    return level


def MeasureImageFocus(image):
    """
    Given an image, focus measure is calculated using the standard deviation of
    the raw data.
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

    return ndimage.standard_deviation(gray)


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


def _DoAutoFocus(future, detector, max_step, thres_factor, et, focus, dfbkg):
    """
    Iteratively acquires an optical image, measures its focus level and adjusts
    the optical focus with respect to the focus level.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector: model.DigitalCamera or model.Detector
    max_step (float): step size (m) used in case we are completely out of focus
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
        if detector.role == "ccd" and focus.role == "ebeam-focus":
            Measure = MeasureSpotFocus
        else:
            Measure = MeasureImageFocus

        for trial in range(MAX_BS_NUMBER):
            # Keep the initial focus position
            init_pos = focus.position.value.get('z')
            best_pos = init_pos
            step = max_step / 2
            cur_pos = focus.position.value.get('z')
            image = AcquireNoBackground(detector, dfbkg)
            fm_cur = Measure(image)
            init_fm = fm_cur
            best_fm = init_fm

            #Clip within range
            new_pos = _ClippedMove(rng, focus, step)
            image = AcquireNoBackground(detector, dfbkg)
            fm_test = Measure(image)
            if fm_test > best_fm:
                best_pos = new_pos
                best_fm = fm_test

            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            cur_pos = focus.position.value.get('z')

            # Check if we our completely out of focus
            if abs(fm_cur - fm_test) < ((thres_factor / (trial + 1)) * fm_cur):
                logging.warning("Completely out of focus, retrying...")
                step = max_step
                fm_new = 0
                sign = 1
                factor = 1
                new_step = step
                cur_pos = focus.position.value.get('z')

                steps = 0
                while fm_new - fm_test < ((thres_factor / (trial + 1)) * 2) * fm_test:
                    if steps >= MAX_STEPS_NUMBER:
                        break
                    sign = -sign
                    cur_pos = cur_pos + sign * new_step
                    #if sign == 1:
                    factor += 1
                    new_step = factor * step
                    #if rng[0] <= cur_pos <= rng[1]:
                    pos = focus.position.value.get('z')
                    shift = cur_pos - pos
                    new_pos = _ClippedMove(rng, focus, shift)
                    image = AcquireNoBackground(detector, dfbkg)
                    fm_new = Measure(image)
                    if fm_new > best_fm:
                        best_pos = new_pos
                        best_fm = fm_new
                    if future._autofocus_state == CANCELLED:
                        raise CancelledError()
                    steps += 1

                image = AcquireNoBackground(detector, dfbkg)
                fm_cur = Measure(image)
                if fm_cur > best_fm:
                    best_pos = new_pos
                    best_fm = fm_cur
                new_pos = _ClippedMove(rng, focus, step)
                image = AcquireNoBackground(detector, dfbkg)
                fm_test = Measure(image)
                if fm_test > best_fm:
                    best_pos = new_pos
                    best_fm = fm_test
                if future._autofocus_state == CANCELLED:
                    raise CancelledError()

            # Update progress of the future
            future.set_end_time(time.time() +
                                estimateAutoFocusTime(et, MAX_STEPS_NUMBER / 2))
            # Determine focus direction
            if fm_cur > fm_test:
                sign = -1
                new_pos = _ClippedMove(rng, focus, -step)
                if future._autofocus_state == CANCELLED:
                    raise CancelledError()
                fm_test = fm_cur
            else:
                sign = 1

            # Move the lens in the correct direction until focus measure is decreased
            step = max_step / 2
            fm_old, fm_new = fm_test, fm_test
            steps = 0
            while fm_old - fm_new <= (thres_factor / (trial + 1)) * fm_old:
                if steps >= MAX_STEPS_NUMBER:
                    break
                fm_old = fm_new
                before_move = focus.position.value.get('z')
                new_pos = _ClippedMove(rng, focus, sign * step)
                after_move = focus.position.value.get('z')
                # Do not stuck to the border
                if before_move == after_move:
                    sign = -sign
                image = AcquireNoBackground(detector, dfbkg)
                fm_new = Measure(image)
                logging.debug("Focus level at %f is %f", focus.position.value.get('z'), fm_new)
                if fm_new > best_fm:
                    best_pos = new_pos
                    best_fm = fm_new
                if future._autofocus_state == CANCELLED:
                    raise CancelledError()
                steps += 1

            # Binary search between the last 2 positions
            new_pos = _ClippedMove(rng, focus, sign * (step / (2 / (trial + 1))))
            max_step = max_step / 8

        if future._autofocus_state == CANCELLED:
            raise CancelledError()

        # Return to best measured focus position anyway
        pos = focus.position.value.get('z')
        shift = best_pos - pos
        new_pos = _ClippedMove(rng, focus, shift)
        return focus.position.value.get('z'), best_fm
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
        f.result()
    elif test_pos >= rng[1]:
        diff = rng[1] - cur_pos
        f = focus.moveRel({"z": diff})
        f.result()
    else:
        f = focus.moveRel({"z": shift})
        f.result()
    return test_pos


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


def AutoFocus(detector, scanner, focus, dfbkg=None):
    """
    Wrapper for DoAutoFocus. It provides the ability to check the progress of autofocus 
    procedure or even cancel it.
    detector (model.DigitalCamera or model.Detector): Detector on which to
      improve the focus quality
    scanner (None or model.Scanner): In case of a SED this is the scanner used
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
    # Check if you focus the SEM or a digital camera
    if scanner is not None:
        et = scanner.dwellTime.value * numpy.prod(scanner.resolution.value)
    else:
        et = detector.exposureTime.value

    # Set proper step and thres factor according to detector type
    thres_factor = INIT_THRES_FACTOR
    role = focus.role
    if role == "focus":  # CCD
        max_stp_sz = 3 * detector.pixelSize.value[0]
        if detector.binning.value[0] > 2:
            thres_factor = 10 * thres_factor  # better snr
        else:
            thres_factor = 5 * thres_factor
    elif role == "overview-focus":  # NAVCAM
        max_stp_sz = 100 * detector.pixelSize.value[0]
    elif role == "ebeam-focus":  # SEM
        if detector.role == "ccd":
            max_stp_sz = 7e04 * scanner.pixelSize.value[0]
            thres_factor = 10 * thres_factor
        else:
            max_stp_sz = 5.5e03 * scanner.pixelSize.value[0]
            thres_factor = 5 * thres_factor
    else:
        logging.warning("Unknown focus %s, will try autofocus anyway.", role)
        max_stp_sz = 3 * detector.pixelSize.value[0]

    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAutoFocusTime(et))
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()
    f.task_canceller = _CancelAutoFocus

    # Run in separate thread
    autofocus_thread = threading.Thread(target=executeTask,
                                        name="Autofocus",
                                        args=(f, _DoAutoFocus, f, detector, max_stp_sz, thres_factor,
                                              et, focus, dfbkg))

    autofocus_thread.start()
    return f
