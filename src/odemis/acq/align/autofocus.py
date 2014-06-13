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
from odemis.dataio import hdf5
from scipy import signal
import threading
import time


FINE_SPOTMODE_ACCURACY = 10e-6  # fine focus accuracy in spot mode #m
ROUGH_SPOTMODE_ACCURACY = 15e-6  # rough focus accuracy in spot mode #m

MAX_STEPS_NUMBER = 3  # Max steps to perform autofocus
MAX_TRIALS_NUMBER = 2


def MeasureFocus(image):
    """
    Given an image, focus measure is calculated using the modified Laplacian
    method. See http://www.sayonics.com/publications/pertuz_PR2013.pdf
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

    m = numpy.array([[-1], [2], [-1]])
    Lx = signal.correlate(gray, m, 'valid')
    Ly = signal.correlate(gray, m.reshape(-1, 1), 'valid')
    Fm = numpy.abs(Lx) + abs(Ly)
    Fm = numpy.mean(Fm)

    return Fm


def _DoAutoFocus(future, device, et, focus, accuracy):
    """
    Iteratively acquires an optical image, measures its focus level and adjusts 
    the optical focus with respect to the focus level.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper 
    device: model.DigitalCamera or model.Detector
    escan: e-beam
    et: exposure time if device is a ccd, 
        dwellTime*prod(resolution) if device is an SEM
    focus (model.CombinedActuator): The optical focus
    accuracy (float): Focus precision #m
    returns (float):    Focus position #m
                        Focus level
    raises:    
            CancelledError if cancelled
            IOError if procedure failed
    """
    logging.debug("Starting Autofocus...")

    try:
        rng = focus.axes["z"].range

        # Keep the initial focus position
        init_pos = focus.position.value.get('z')

        # Determine focus direction
        # TODO check if out of bounds
        step = 20e-6
        image = device.data.get()
        fm_cur = MeasureFocus(image)
        hdf5.export("firstimg.h5", model.DataArray(image))
        init_fm = fm_cur
        f = focus.moveRel({"z": step})
        f.result()
        image = device.data.get()
        fm_test = MeasureFocus(image)
        hdf5.export("secimg.h5", model.DataArray(image))

        if future._autofocus_state == CANCELLED:
            raise CancelledError()
        print fm_test
        # Check if we our completely out of focus
        if abs(fm_cur - fm_test) < (0.05 * fm_cur):
            logging.warning("Completely out of focus, retrying...")
            fm_new = 0
            sign = 1
            factor = 1
            new_step = step
            cur_pos = focus.position.value.get('z')

            steps = 0
            while fm_new < 1.02 * fm_test:
                if steps >= MAX_STEPS_NUMBER:
                    break
                sign = -sign
                cur_pos = cur_pos + sign * new_step
                if sign == 1:
                    factor += 1
                new_step += factor * step
                # Increase factor every 2 times
                print cur_pos, rng
                if rng[0] <= cur_pos <= rng[1]:
                    pos = focus.position.value.get('z')
                    shift = cur_pos - pos
                    f = focus.moveRel({"z":shift})
                    f.result()
                    image = device.data.get()
                    fm_new = MeasureFocus(image)
                    print fm_new
                    if future._autofocus_state == CANCELLED:
                        raise CancelledError()
                steps += 1

            image = device.data.get()
            fm_cur = MeasureFocus(image)
            print fm_cur
            f = focus.moveRel({"z": step})
            f.result()
            image = device.data.get()
            fm_test = MeasureFocus(image)
            if future._autofocus_state == CANCELLED:
                raise CancelledError()

        # Update progress of the future (approx. 10 steps less)
        future.set_end_time(time.time() +
                            estimateAutoFocusTime(et,
                                                MAX_STEPS_NUMBER - 10))

        if fm_cur > fm_test:
            sign = -1
            f = focus.moveRel({"z":-step})
            f.result()
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            fm_test = fm_cur
        else:
            sign = 1
        print sign

        # Move the lens in the correct direction until focus measure is decreased
        step = accuracy
        fm_old, fm_new = fm_test, fm_test
        steps = 0
        while (0.98 * fm_old) <= fm_new:
            if steps >= MAX_STEPS_NUMBER:
                break
            fm_old = fm_new
            f = focus.moveRel({"z":sign * step})
            f.result()
            image = device.data.get()
            fm_new = MeasureFocus(image)
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            print "step"
            print fm_new, focus.position.value.get('z')
            steps += 1
        f = focus.moveRel({"z":-sign * step})
        f.result()

        if future._autofocus_state == CANCELLED:
            raise CancelledError()
        fm_final = fm_old
        if steps == 1:
            print "Already well focused."
            logging.info("Already well focused.")
            print fm_final
            return focus.position.value.get('z'), fm_final

        print init_fm, fm_final
        if init_fm < 0.95 * fm_final:
            return focus.position.value.get('z'), fm_final
        else:
            # Return to initial position
            pos = focus.position.value.get('z')
            shift = init_pos - pos
            f = focus.moveRel({"z":shift})
            f.result()
            raise IOError("Autofocus failure")

        return focus.position.value.get('z'), fm_final
    finally:
        with future._autofocus_lock:
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            future._autofocus_state = FINISHED

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
    return steps * (exposure_time + 3)  # 3 sec approx. for focus actuator to move


def AutoFocus(detector, scanner, focus, accuracy):
    """
    Wrapper for DoAutoFocus. It provides the ability to check the progress of autofocus 
    procedure or even cancel it.
    detector (model.DigitalCamera or model.Detector):
    scanner (None or model.Scanner): In case of a SED this is the scanner used
    focus (model.CombinedActuator): The optical focus
    accuracy (float): Focus precision #m
    returns (model.ProgressiveFuture):    Progress of DoAutoFocus, whose result() will return:
            Focus position #m
            Focus level
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    # Check if you focus the SEM or a digital camera
    if scanner is not None:
        et = scanner.dwellTime.value * numpy.prod(scanner.resolution.value)
    else:
        et = detector.exposureTime.value
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAutoFocusTime(et))
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()

    # Task to run
    doAutoFocus = _DoAutoFocus
    f.task_canceller = _CancelAutoFocus

    # Run in separate thread
    autofocus_thread = threading.Thread(target=executeTask,
                  name="Autofocus",
                  args=(f, doAutoFocus, f, detector, et, focus, accuracy))

    autofocus_thread.start()
    return f
