# -*- coding: utf-8 -*-
"""
Created on 11 Apr 2014

@author: Kimon Tsitsikas

Copyright © 2013-2016 Kimon Tsitsikas and Éric Piel, Delmic

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

import collections
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import numpy
from odemis import model
from odemis.acq._futures import executeTask
from odemis.model import InstantaneousFuture
from odemis.util.img import Subtract
from scipy import ndimage
import threading
import time

import cv2


MAX_STEPS_NUMBER = 100  # Max steps to perform autofocus
MAX_BS_NUMBER = 1  # Maximum number of applying binary search with a smaller max_step


def _convertRBGToGrayscale(image):
    """
    Quick and dirty convertion of RGB data to grayscale
    image (numpy array of shape YX3)
    return (numpy array of shape YX)
    """
    r, g, b = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    gray = numpy.empty(image.shape[0:2], dtype="uint16")
    gray[...] = r
    gray += g
    gray += b

    return gray


def MeasureSEMFocus(image):
    """
    Given an image, focus measure is calculated using the standard deviation of
    the raw data.
    image (model.DataArray): SEM image
    returns (float): The focus level of the SEM image (higher is better)
    """
    # Handle RGB image
    if len(image.shape) == 3:
        # TODO find faster/better solution
        image = _convertRBGToGrayscale(image)

    return ndimage.standard_deviation(image)


def MeasureOpticalFocus(image):
    """
    Given an image, focus measure is calculated using the variance of Laplacian
    of the raw data.
    image (model.DataArray): Optical image
    returns (float): The focus level of the optical image (higher is better)
    """
    # Handle RGB image
    if len(image.shape) == 3:
        # TODO find faster/better solution
        image = _convertRBGToGrayscale(image)

    return cv2.Laplacian(image, cv2.CV_64F).var()


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


def _DoAutoFocus(future, detector, emt, focus, dfbkg, good_focus):
    """
    Iteratively acquires an optical image, measures its focus level and adjusts
    the optical focus with respect to the focus level.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector: model.DigitalCamera or model.Detector
    emt (None or model.Emitter): In case of a SED this is the scanner used
    focus (model.Actuator): The optical focus
    dfbkg (model.DataFlow): dataflow of se- or bs- detector
    good_focus (float): if provided, an already known good focus position to be
      taken into consideration while autofocusing
    returns:
        (float): Focus position (m)
        (float): Focus level
    raises:
            CancelledError if cancelled
            IOError if procedure failed
    """
    # TODO: dfbkg is mis-named, as it's the dataflow to use to _activate_ the
    # emitter. To acquire the background, it's specifically not used.

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
        # use the .depthOfField on detector or emitter as maximum stepsize
        avail_depths = (detector, emt)
        if model.hasVA(emt, "dwellTime"):
            # Hack in case of using the e-beam with a DigitalCamera detector.
            # All the digital cameras have a depthOfField, which is updated based
            # on the optical lens properties... but the depthOfField in this
            # case depends on the e-beam lens.
            avail_depths = (emt, detector)
        for c in avail_depths:
            if model.hasVA(c, "depthOfField"):
                dof = c.depthOfField.value
                break
        else:
            logging.debug("No depth of field info found")
            dof = 1e-6  # m, not too bad value
        logging.debug("Depth of field is %f", dof)
        min_step = dof / 2

        rng = focus.axes["z"].range
        max_step = (rng[1] - rng[0]) / 2
        if max_step <= 0:
            raise ValueError("Unexpected focus range %s" % (rng,))

        max_reached = False  # True once we've passed the maximum level (ie, start bouncing)
        # It's used to cache the focus level, to avoid reacquiring at the same
        # position. We do it only for the 'rough' max search because for the fine
        # search, the actuator and acquisition delta are likely to play a role
        focus_levels = {}  # focus pos (float) -> focus level (float)

        best_pos = focus.position.value['z']
        best_fm = 0
        last_pos = None

        # Pick measurement method based on the heuristics that SEM detectors
        # are typically just a point (ie, shape == data depth).
        # TODO: is this working as expected? Alternatively, we could check
        # MD_DET_TYPE.
        if len(detector.shape) > 1:
            logging.debug("Using Optical method to estimate focus")
            Measure = MeasureOpticalFocus
        else:
            logging.debug("Using SEM method to estimate focus")
            Measure = MeasureSEMFocus

        step_factor = 2 ** 7
        if good_focus is not None:
            current_pos = focus.position.value['z']
            image = AcquireNoBackground(detector, dfbkg)
            fm_current = Measure(image)
            logging.debug("Focus level at %f is %f", current_pos, fm_current)
            focus_levels[current_pos] = fm_current

            focus.moveAbsSync({"z": good_focus})
            image = AcquireNoBackground(detector, dfbkg)
            fm_good = Measure(image)
            logging.debug("Focus level at %f is %f", good_focus, fm_good)
            focus_levels[good_focus] = fm_good
            last_pos = good_focus

            if fm_good < fm_current:
                # Move back to current position if good_pos is not that good
                # after all
                focus.moveAbsSync({"z": current_pos})
                # it also means we are pretty close
            step_factor = 2 ** 4

        if step_factor * min_step > max_step:
            # Large steps would be too big. We can reduce step_factor and/or
            # min_step. => let's take our time, and maybe find finer focus
            min_step = max_step / step_factor
            logging.debug("Reducing min step to %g", min_step)

        # TODO: to go a bit faster, we could use synchronised acquisition on
        # the detector (if it supports it)
        # TODO: we could estimate the quality of the autofocus by looking at the
        # standard deviation of the the focus levels (and the standard deviation
        # of the focus levels measured for the same focus position)
        logging.debug("Step factor used for autofocus: %g", step_factor)
        step_cntr = 1
        while step_factor >= 1 and step_cntr <= MAX_STEPS_NUMBER:
            # TODO: update the estimated time (based on how long it takes to
            # move + acquire, and how many steps are approximately left)

            # Start at the current focus position
            center = focus.position.value['z']
            # Don't redo the acquisition either if we've just done it, or if it
            # was already done and we are still doing a rough search
            if (not max_reached or last_pos == center) and center in focus_levels:
                fm_center = focus_levels[center]
            else:
                image = AcquireNoBackground(detector, dfbkg)
                fm_center = Measure(image)
                logging.debug("Focus level (center) at %f is %f", center, fm_center)
                focus_levels[center] = fm_center

            # Move to right position
            right = center + step_factor * min_step
            right = max(rng[0], min(right, rng[1]))  # clip
            if not max_reached and right in focus_levels:
                fm_right = focus_levels[right]
            else:
                focus.moveAbsSync({"z": right})
                right = focus.position.value["z"]
                image = AcquireNoBackground(detector, dfbkg)
                fm_right = Measure(image)
                logging.debug("Focus level (right) at %f is %f", right, fm_right)
                focus_levels[right] = fm_right

            # Move to left position
            left = center - step_factor * min_step
            left = max(rng[0], min(left, rng[1]))  # clip
            if not max_reached and left in focus_levels:
                fm_left = focus_levels[left]
            else:
                focus.moveAbsSync({"z": left})
                left = focus.position.value["z"]
                image = AcquireNoBackground(detector, dfbkg)
                fm_left = Measure(image)
                logging.debug("Focus level (left) at %f is %f", left, fm_left)
                focus_levels[left] = fm_left
                last_pos = left

            fm_range = (fm_left, fm_center, fm_right)
            pos_range = (left, center, right)
            best_fm = max(fm_range)
            i_max = fm_range.index(best_fm)
            best_pos = pos_range[i_max]

            if future._autofocus_state == CANCELLED:
                raise CancelledError()

            # if best focus was found at the center
            if i_max == 1:
                step_factor /= 2
                if not max_reached:
                    logging.debug("Now zooming in on improved focus")
                max_reached = True
            elif (rng[0] > best_pos - step_factor * min_step or
                  rng[1] < best_pos + step_factor * min_step):
                step_factor /= 1.5
                logging.debug("Reducing step factor to %g because the focus (%g) is near range limit %s",
                              step_factor, best_pos, rng)
                if step_factor <= 8:
                    max_reached = True  # Force re-checking data

            focus.moveAbsSync({"z": best_pos})
            step_cntr += 1

        if step_cntr == MAX_STEPS_NUMBER:
            logging.info("Auto focus gave up after %d steps @ %g m", step_cntr, best_pos)
        else:
            logging.info("Auto focus found best level %g @ %g m", best_fm, best_pos)

        return best_pos, best_fm

    except CancelledError:
        # Go to the best position known so far
        focus.moveAbsSync({"z": best_pos})
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
        logging.debug("Autofocus cancellation requested.")

    return True


# TODO: drop steps, which is unused, or use it
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
    if model.hasVA(emt, "dwellTime"):
        et = emt.dwellTime.value * numpy.prod(emt.resolution.value)
    elif model.hasVA(detector, "exposureTime"):
        et = detector.exposureTime.value
    else:
        # Completely random... but we are in a case where probably that's the last
        # thing the caller will care about.
        et = 1

    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAutoFocusTime(et))
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()
    f.task_canceller = _CancelAutoFocus

    # Run in separate thread
    autofocus_thread = threading.Thread(target=executeTask,
                                        name="Autofocus",
                                        args=(f, _DoAutoFocus, f, detector, emt,
                                              focus, dfbkg, good_focus))

    autofocus_thread.start()
    return f


def AutoFocusSpectrometer(spectrograph, focuser, detectors, selector=None):
    """
    Run autofocus for a spectrograph. It will actually run autofocus on each
    gratings, and for each detectors. The input slit should already be in a
    good position (typically, almost closed), and a light source should be
    active.
    Note: it's currently tailored to the Andor Shamrock SR-193i. It's recommended
    to put the detector on the "direct" output as first detector.
    spectrograph (Actuator): should have grating and wavelength.
    focuser (Actuator): should have a z axis
    detectors (Detector or list of Detectors): all the detectors available on
      the spectrometer. The first detector will be used to autofocus all the
      gratings, and each other detector will be focused with the original
      grating.
    selector (Actuator or None): must have a rx axis with each position corresponding
     to one of the detectors. If there is only one detector, selector can be None.
    return (ProgressiveFuture -> dict((grating, detector)->focus position)): a progressive future
      which will eventually return a map of grating/detector -> focus position.
    """
    if not isinstance(detectors, collections.Iterable):
        detectors = [detectors]
    if not detectors:
        raise ValueError("At least one detector must be provided")

    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    detector = detectors[0]
    if model.hasVA(detector, "exposureTime"):
        et = detector.exposureTime.value
    else:
        # Completely random... but we are in a case where probably that's the last
        # thing the caller will care about.
        et = 1

    # 1 time / grating + 1 time / extra detector
    cnts = len(spectrograph.axes["grating"].choices) + (len(detectors) - 1)
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + cnts * estimateAutoFocusTime(et))
    f.task_canceller = _CancelAutoFocusSpectrometer
    # Extra info for the canceller
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()
    f._subfuture = InstantaneousFuture()

    # Run in separate thread
    autofocus_thread = threading.Thread(target=executeTask,
                                        name="Spectrometer Autofocus",
                                        args=(f, _DoAutoFocusSpectrometer, f,
                                              spectrograph, focuser, detectors, selector))

    autofocus_thread.start()
    return f


def _moveSelectorToDetector(selector, detector):
    """
    Move the selector to have the given detector receive light
    selector (Actuator): a rx axis with a position
    detector (Component): the component to receive light
    return (position): the new position of the selector
    raise LookupError: if no position on the selector affects the detector
    """
    # TODO: handle every way of indicating affect position? -> move to odemis.util
    choices = selector.axes["rx"].choices
    for p, affected in choices.items():
        if detector.name in affected:
            selector.moveAbsSync({"rx": p})
            return {"rx": p}

    raise LookupError("Failed to find detector '%s' in selector positions %s" %
                      (detector.name, choices.values()))


def _updateAFSProgress(future, last_dur, left):
    """
    Update the progress of the future based on duration of the previous autofocus
    future (ProgressiveFuture)
    last_dur (0< float): duration of the latest autofocusing action
    left (0<= int): number of autofocus actions still left
    """
    # Estimate that all the other autofocusing will take the same amount of time
    tleft = left * last_dur + 5  # 5 s to go back to original pos
    future.set_progress(end=time.time() + tleft)


def _DoAutoFocusSpectrometer(future, spectrograph, focuser, detectors, selector):
    """
    cf AutoFocusSpectrometer
    return dict((grating, detector) -> focus pos)
    """
    ret = {}
    # Record the wavelength and grating position
    pos_orig = {k: v for k, v in spectrograph.position.value.items()
                              if k in ("wavelength", "grating")}
    gratings = spectrograph.axes["grating"].choices.keys()
    if selector:
        sel_orig = selector.position.value

    # For progress update
    cnts = len(gratings) + (len(detectors) - 1)

    # Note: this procedure works well with the SR-193i. In particular, it
    # records the focus position for each grating (in absolute) and each
    # detector (as an offset). It needs to be double checked if used with
    # other detectors.
    if "Shamrock" not in spectrograph.hwVersion:
        logging.warning("Spectrometer autofocusing has not been tested on"
                        "this type of spectrograph (%s)", spectrograph.hwVersion)

    try:
        # Autofocus each grating, using the first detector
        detector = detectors[0]
        if selector:
            _moveSelectorToDetector(selector, detector)

        if future._autofocus_state == CANCELLED:
            raise CancelledError()

        # start with the current grating, to save the move time
        gratings.sort(key=lambda g: 0 if g == pos_orig["grating"] else 1)
        for g in gratings:
            logging.debug("Autofocusing on grating %s", g)
            tstart = time.time()
            try:
                # 0th order is not absolutely necessary for focusing, but it
                # typically gives the best results
                spectrograph.moveAbsSync({"wavelength": 0, "grating": g})
            except Exception:
                logging.exception("Failed to move to 0th order for grating %s", g)

            future._subfuture = AutoFocus(detector, None, focuser)
            fp, flvl = future._subfuture.result()
            ret[(g, detector)] = fp
            cnts -= 1
            _updateAFSProgress(future, time.time() - tstart, cnts)

            if future._autofocus_state == CANCELLED:
                raise CancelledError()

        # Autofocus each additional detector
        grating = pos_orig["grating"]
        for d in detectors[1:]:
            logging.debug("Autofocusing on detector %s", d)
            tstart = time.time()
            _moveSelectorToDetector(selector, d)
            try:
                # 0th order + original grating
                # TODO: instead of using original grating, use mirror grating if
                # available
                spectrograph.moveAbsSync({"wavelength": 0, "grating": grating})
            except Exception:
                logging.exception("Failed to move to 0th order and grating %s", grating)

            future._subfuture = AutoFocus(detector, None, focuser)
            fp, flvl = future._subfuture.result()
            ret[(grating, d)] = fp
            cnts -= 1
            _updateAFSProgress(future, time.time() - tstart, cnts)

            if future._autofocus_state == CANCELLED:
                raise CancelledError()

        return ret
    except CancelledError:
        logging.debug("AutofocusSpectrometer cancelled")
    finally:
        spectrograph.moveAbsSync(pos_orig)
        if selector:
            selector.moveAbsSync(sel_orig)
        with future._autofocus_lock:
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            future._autofocus_state = FINISHED


def _CancelAutoFocusSpectrometer(future):
    """
    Canceller of _DoAutoFocus task.
    """
    logging.debug("Cancelling autofocus...")

    with future._autofocus_lock:
        if future._autofocus_state == FINISHED:
            return False
        future._autofocus_state = CANCELLED
        future._subfuture.cancel()
        logging.debug("AutofocusSpectrometer cancellation requested.")

    return True
