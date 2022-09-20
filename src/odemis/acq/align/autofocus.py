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

from collections.abc import Iterable
from concurrent.futures import TimeoutError, CancelledError
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING
import cv2
import logging
import numpy
from odemis import model
from odemis.acq.align import light
from odemis.model import InstantaneousFuture
from odemis.util import executeAsyncTask, almost_equal
from odemis.util.img import Subtract
from scipy import ndimage
from scipy.optimize import curve_fit
from scipy.signal import medfilt
import threading
import time


MTD_BINARY = 0
MTD_EXHAUSTIVE = 1

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


def AssessFocus(levels, min_ratio=15):
    """
    Given a list of focus levels, it decides if there is any significant value
    or it only contains noise.
    levels (list of floats): List of focus levels
    min_ratio (0 < float): minimum ratio between the focus level max-mean and
      the standard deviation to be considered "significant".
    returns (boolean): True if there is significant deviation
    """
    std_l = numpy.std(levels)

    levels_nomax = list(levels)
    max_l = max(levels)
    levels_nomax.remove(max_l)
    avg_l = numpy.mean(levels_nomax)
    l_diff = max_l - avg_l

    logging.debug("Focus level std dev: %f, avg: %f, diff max: %f", std_l, avg_l, l_diff)
    if std_l > 0 and l_diff >= min_ratio * std_l:
        logging.debug("Significant focus level deviation was found")
        return True
    return False


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


def Measure1d(image):
    """
    Given an image of a 1 line ccd, measure the focus based on the inverse of the width of a gaussian fit of the data.
    It is assumed that the signal is in focus when the width of the signal is smallest and therefore sigma is smallest.
    image (model.DataArray): 1D image from 1 line ccd.
    returns (float): The focus level of the image, based on the inverse of the width of a gaussian fitted on the image.
    """
    # Use the gauss function to fit a gaussian to the 1 line image.
    def gauss(x, amplitude, pos, width, base):
        y = amplitude * numpy.exp(-(x - pos) ** 2 / (2 * width ** 2)) + base
        return y
    # squeeze to make sure the image array is 1d.
    signal = numpy.squeeze(image)
    # Apply a median filter with a kernel of 5, to handle noise with up to 2 neighbouring pixels with a very high value,
    # resembling a peak, which sometimes happens on CCDs.
    signal = medfilt(signal, 5)
    x = numpy.arange(len(signal))
    width = max(3.0, 0.01 * len(signal))
    # determine the indices and the values of the 1% highest points in the signal.
    max_ids = signal.argsort()[-int(width):]
    max_sig = signal[max_ids]
    med_sig = numpy.median(signal)
    # give an initial estimate for the parameters of the gaussian fit: [amplitude, expected position, width, base]
    p_initial = [numpy.median(max_sig) - med_sig, numpy.median(max_ids), width, med_sig]
    # Use curve_fit to fit the gauss function to the data. Use p_initial as our initial guess.
    try:
        popt, pcov = curve_fit(gauss, x, signal, p0=p_initial)
    except RuntimeError as ex:
        # No fitting can be found => the focus is really bad
        logging.debug("Failed to estimate focus level, assuming a very bad level: %s", ex)
        return 0

    # The focus metric is the inverse of width of the gaussian fit (a smaller width is a higher focus level).
    return 1 / abs(popt[2])


def MeasureSpotsFocus(image):
    """
    Focus measurement metric based on Tenengrad variance:
        Pech, J.; Cristobal, G.; Chamorro, J. & Fernandez, J. Diatom autofocusing in brightfield microscopy: a
        comparative study. 2000.

    Given an image, the focus measure is calculated using the variance of a Sobel filter applied in the
    x and y directions of the raw data.
    image (model.DataArray): Optical image
    returns (float): The focus level of the image (higher is better)
    """
    sobelx = cv2.Sobel(image, cv2.CV_64F, 1, 0, ksize=5)
    sobely = cv2.Sobel(image, cv2.CV_64F, 0, 1, ksize=5)
    sobel_image = sobelx ** 2 + sobely ** 2
    return sobel_image.var()


def getNextImage(det, timeout=None):
    """
    Acquire one image from the given detector
    det (model.Detector): detector from which to acquire an image
    timeout (None or 0<float): maximum time to wait
    returns (model.DataArray):
        Image (with subtracted background if requested)
    raise:
        IOError: if it timed out
    """
    # Code based on Dataflow.get(), to support timeout
    min_time = time.time()  # asap=False
    is_received = threading.Event()
    data_shared = [None]  # in python2 we need to create a new container object

    def receive_one_image(df, data):
        if data.metadata.get(model.MD_ACQ_DATE, float("inf")) >= min_time:
            df.unsubscribe(receive_one_image)
            data_shared[0] = data
            is_received.set()

    det.data.subscribe(receive_one_image)
    if not is_received.wait(timeout):
        det.data.unsubscribe(receive_one_image)
        raise IOError("No data received after %g s" % (timeout,))
    return data_shared[0]


def AcquireNoBackground(det, dfbkg=None, timeout=None):
    """
    Performs optical acquisition with background subtraction if possible.
    Particularly used in order to eliminate the e-beam source background in the
    Delphi.
    det (model.Detector): detector from which to acquire an image
    dfbkg (model.DataFlow or None): dataflow of se- or bs- detector to
    start/stop the source. If None, a standard acquisition is performed (without
    background subtraction)
    timeout (None or 0<float): maximum time to wait
    returns (model.DataArray):
        Image (with subtracted background if requested)
    raise:
        IOError: if it timed out
    """
    if dfbkg is not None:
        # acquire background
        bg_image = getNextImage(det, timeout)

        # acquire with signal
        dfbkg.subscribe(_discard_data)
        try:
            data = getNextImage(det, timeout)
        finally:
            dfbkg.unsubscribe(_discard_data)

        return Subtract(data, bg_image)
    else:
        return getNextImage(det, timeout)


def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass


def _DoBinaryFocus(future, detector, emt, focus, dfbkg, good_focus, rng_focus):
    """
    Iteratively acquires an optical image, measures its focus level and adjusts
    the optical focus with respect to the focus level.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector: model.DigitalCamera or model.Detector
    emt (None or model.Emitter): In case of a SED this is the scanner used
    focus (model.Actuator): The focus actuator (with a "z" axis)
    dfbkg (model.DataFlow): dataflow of se- or bs- detector
    good_focus (float): if provided, an already known good focus position to be
      taken into consideration while autofocusing
    rng_focus (tuple of floats): if provided, the search of the best focus position is limited
      within this range
    returns:
        (float): Focus position (m)
        (float): Focus level
    raises:
            CancelledError if cancelled
            IOError if procedure failed
    """
    # TODO: dfbkg is mis-named, as it's the dataflow to use to _activate_ the
    # emitter. It's necessary to acquire the background, as otherwise we assume
    # the emitter is always active, but during background acquisition, that
    # emitter is explicitly _disabled_.
    # => change emt to "scanner", and "dfbkg" to "emitter". Or pass a stream?
    # Note: the emt is almost not used, only to estimate completion time,
    # and read the depthOfField.

    # It does a dichotomy search on the focus level. In practice, it means it
    # will start going into the direction that increase the focus with big steps
    # until the focus decreases again. Then it'll bounce back and forth with
    # smaller and smaller steps.
    # The tricky parts are:
    # * it's hard to estimate the focus level (on an arbitrary image)
    # * two acquisitions at the same focus position can have (slightly) different
    #   focus levels (due to noise and sample degradation)
    # * if the focus actuator is not precise (eg, open loop), it's hard to
    #   even go back to the same focus position when wanted
    logging.debug("Starting binary autofocus on detector %s...", detector.name)

    try:
        # Big timeout, most important being that it's shorter than eternity
        timeout = 3 + 2 * estimateAcquisitionTime(detector, emt)

        # use the .depthOfField on detector or emitter as maximum stepsize
        avail_depths = (detector, emt)
        if model.hasVA(emt, "dwellTime"):
            # Hack in case of using the e-beam with a DigitalCamera detector.
            # All the digital cameras have a depthOfField, which is updated based
            # on the optical lens properties... but the depthOfField in this
            # case depends on the e-beam lens.
            # TODO: or better rely on which component the focuser affects? If it
            # affects (also) the emitter, use this one first? (but in the
            # current models the focusers affects nothing)
            avail_depths = (emt, detector)
        for c in avail_depths:
            if model.hasVA(c, "depthOfField"):
                dof = c.depthOfField.value
                break
        else:
            logging.debug("No depth of field info found")
            dof = 1e-6  # m, not too bad value
        logging.debug("Depth of field is %.7g", dof)
        min_step = dof / 2

        # adjust to rng_focus if provided
        rng = focus.axes["z"].range
        if rng_focus:
            rng = (max(rng[0], rng_focus[0]), min(rng[1], rng_focus[1]))

        max_step = (rng[1] - rng[0]) / 2
        if max_step <= 0:
            raise ValueError("Unexpected focus range %s" % (rng,))

        rough_search = True  # False once we've passed the maximum level (ie, start bouncing)
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
            if detector.role == 'diagnostic-ccd':
                logging.debug("Using Spot method to estimate focus")
                Measure = MeasureSpotsFocus
            elif detector.resolution.value[1] == 1:
                logging.debug("Using 1d method to estimate focus")
                Measure = Measure1d
            else:
                logging.debug("Using Optical method to estimate focus")
                Measure = MeasureOpticalFocus
        else:
            logging.debug("Using SEM method to estimate focus")
            Measure = MeasureSEMFocus

        step_factor = 2 ** 7
        if good_focus is not None:
            current_pos = focus.position.value['z']
            image = AcquireNoBackground(detector, dfbkg, timeout)
            fm_current = Measure(image)
            logging.debug("Focus level at %.7g is %.7g", current_pos, fm_current)
            focus_levels[current_pos] = fm_current

            focus.moveAbsSync({"z": good_focus})
            good_focus = focus.position.value["z"]
            image = AcquireNoBackground(detector, dfbkg, timeout)
            fm_good = Measure(image)
            logging.debug("Focus level at %.7g is %.7g", good_focus, fm_good)
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
            if (rough_search or last_pos == center) and center in focus_levels:
                fm_center = focus_levels[center]
            else:
                image = AcquireNoBackground(detector, dfbkg, timeout)
                fm_center = Measure(image)
                logging.debug("Focus level (center) at %.7g is %.7g", center, fm_center)
                focus_levels[center] = fm_center

            last_pos = center

            # Move to right position
            right = center + step_factor * min_step
            right = max(rng[0], min(right, rng[1]))  # clip
            if rough_search and right in focus_levels:
                fm_right = focus_levels[right]
            else:
                focus.moveAbsSync({"z": right})
                right = focus.position.value["z"]
                last_pos = right
                image = AcquireNoBackground(detector, dfbkg, timeout)
                fm_right = Measure(image)
                logging.debug("Focus level (right) at %.7g is %.7g", right, fm_right)
                focus_levels[right] = fm_right

            # Move to left position
            left = center - step_factor * min_step
            left = max(rng[0], min(left, rng[1]))  # clip
            if rough_search and left in focus_levels:
                fm_left = focus_levels[left]
            else:
                focus.moveAbsSync({"z": left})
                left = focus.position.value["z"]
                last_pos = left
                image = AcquireNoBackground(detector, dfbkg, timeout)
                fm_left = Measure(image)
                logging.debug("Focus level (left) at %.7g is %.7g", left, fm_left)
                focus_levels[left] = fm_left

            fm_range = (fm_left, fm_center, fm_right)
            if all(almost_equal(fm_left, fm, rtol=1e-6) for fm in fm_range[1:]):
                logging.debug("All focus levels identical, picking the middle one")
                # Most probably the images are all noise, or they are not affected
                # by the focus. In any case, the best is to not move the focus,
                # so let's "center" on it. That's better than the default behaviour
                # which would tend to pick "left" because that's the first one.
                i_max = 1
                best_pos, best_fm = center, fm_center
            else:
                pos_range = (left, center, right)
                best_fm = max(fm_range)
                i_max = fm_range.index(best_fm)
                best_pos = pos_range[i_max]

            if future._autofocus_state == CANCELLED:
                raise CancelledError()

            if left == right:
                logging.info("Seems to have reached minimum step size (at %g m)", 2 * step_factor * min_step)
                break

            # if best focus was found at the center
            if i_max == 1:
                step_factor /= 2
                if rough_search:
                    logging.debug("Now zooming in on improved focus")
                rough_search = False
            elif (rng[0] > best_pos - step_factor * min_step or
                  rng[1] < best_pos + step_factor * min_step):
                step_factor /= 1.5
                logging.debug("Reducing step factor to %g because the focus (%g) is near range limit %s",
                              step_factor, best_pos, rng)
                if step_factor <= 8:
                    rough_search = False  # Force re-checking data

            if last_pos != best_pos:
                # Clip best_pos in case the hardware reports a position outside of the range.
                best_pos = max(rng[0], min(best_pos, rng[1]))
                focus.moveAbsSync({"z": best_pos})
            step_cntr += 1

        worst_fm = min(focus_levels.values())
        if step_cntr == MAX_STEPS_NUMBER:
            logging.info("Auto focus gave up after %d steps @ %g m", step_cntr, best_pos)
        elif (best_fm - worst_fm) < best_fm * 0.5:
            # We can be confident of the data if there is a "big" (50%) difference
            # between the focus levels.
            logging.info("Auto focus indecisive but picking level %g @ %g m (lowest = %g)",
                         best_fm, best_pos, worst_fm)
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


def _DoExhaustiveFocus(future, detector, emt, focus, dfbkg, good_focus, rng_focus):
    """
    Moves the optical focus through the whole given range, measures the focus
    level on each position and ends up where the best focus level was found. In
    case a significant deviation was found while going through the range, it
    stops and limits the search within a smaller range around this position.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector: model.DigitalCamera or model.Detector
    emt (None or model.Emitter): In case of a SED this is the scanner used
    focus (model.Actuator): The optical focus
    dfbkg (model.DataFlow): dataflow of se- or bs- detector
    good_focus (float): if provided, an already known good focus position to be
      taken into consideration while autofocusing
    rng_focus (tuple): if provided, the search of the best focus position is limited
      within this range
    returns:
        (float): Focus position (m)
        (float): Focus level
    raises:
            CancelledError if cancelled
            IOError if procedure failed
    """
    logging.debug("Starting exhaustive autofocus on detector %s...", detector.name)

    try:
        # Big timeout, most important being that it's shorter than eternity
        timeout = 3 + 2 * estimateAcquisitionTime(detector, emt)

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
        logging.debug("Depth of field is %.7g", dof)

        # Pick measurement method based on the heuristics that SEM detectors
        # are typically just a point (ie, shape == data depth).
        # TODO: is this working as expected? Alternatively, we could check
        # MD_DET_TYPE.
        if len(detector.shape) > 1:
            if detector.role == 'diagnostic-ccd':
                logging.debug("Using Spot method to estimate focus")
                Measure = MeasureSpotsFocus
            elif detector.resolution.value[1] == 1:
                logging.debug("Using 1d method to estimate focus")
                Measure = Measure1d
            else:
                logging.debug("Using Optical method to estimate focus")
                Measure = MeasureOpticalFocus
        else:
            logging.debug("Using SEM method to estimate focus")
            Measure = MeasureSEMFocus

        # adjust to rng_focus if provided
        rng = focus.axes["z"].range
        if rng_focus:
            rng = (max(rng[0], rng_focus[0]), min(rng[1], rng_focus[1]))

        if good_focus:
            focus.moveAbsSync({"z": good_focus})

        focus_levels = []  # list with focus levels measured so far
        best_pos = orig_pos = focus.position.value['z']
        best_fm = 0

        if future._autofocus_state == CANCELLED:
            raise CancelledError()

        # Based on our measurements on spot detection, a spot is visible within
        # a margin of ~30microns around its best focus position. Such a step
        # (i.e. ~ 6microns) ensures that we will eventually be able to notice a
        # difference compared to the focus levels measured so far.
        step = 8 * dof
        lower_bound, upper_bound = rng
        # start moving upwards until we reach the upper bound or we find some
        # significant deviation in focus level
        # The number of steps is the distance to the upper bound divided by the step size.
        for next_pos in numpy.linspace(orig_pos, upper_bound, int((upper_bound - orig_pos) / step)):
            focus.moveAbsSync({"z": next_pos})
            image = AcquireNoBackground(detector, dfbkg, timeout)
            new_fm = Measure(image)
            focus_levels.append(new_fm)
            logging.debug("Focus level at %.7g is %.7g", next_pos, new_fm)
            if new_fm >= best_fm:
                best_fm = new_fm
                best_pos = next_pos
            if len(focus_levels) >= 10 and AssessFocus(focus_levels):
                # trigger binary search on if significant deviation was
                # found in current position
                return _DoBinaryFocus(future, detector, emt, focus, dfbkg, best_pos, (best_pos - 2 * step, best_pos + 2 * step))

        if future._autofocus_state == CANCELLED:
            raise CancelledError()

        # if nothing was found go downwards, starting one step below the original position
        num = max(int((orig_pos - lower_bound) / step), 0)  # Take 0 steps if orig_pos is too close to lower_bound
        for next_pos in numpy.linspace(orig_pos - step, lower_bound, num):
            focus.moveAbsSync({"z": next_pos})
            image = AcquireNoBackground(detector, dfbkg, timeout)
            new_fm = Measure(image)
            focus_levels.append(new_fm)
            logging.debug("Focus level at %.7g is %.7g", next_pos, new_fm)
            if new_fm >= best_fm:
                best_fm = new_fm
                best_pos = next_pos
            if len(focus_levels) >= 10 and AssessFocus(focus_levels):
                # trigger binary search on if significant deviation was
                # found in current position
                return _DoBinaryFocus(future, detector, emt, focus, dfbkg, best_pos, (best_pos - 2 * step, best_pos + 2 * step))

        if future._autofocus_state == CANCELLED:
            raise CancelledError()

        logging.debug("No significant focus level was found so far, thus we just move to the best position found %.7g", best_pos)
        focus.moveAbsSync({"z": best_pos})
        return _DoBinaryFocus(future, detector, emt, focus, dfbkg, best_pos, (best_pos - 2 * step, best_pos + 2 * step))

    except CancelledError:
        # Go to the best position known so far
        focus.moveAbsSync({"z": best_pos})
    finally:
        # Only used if for some reason the binary focus is not called (e.g. cancellation)
        with future._autofocus_lock:
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            future._autofocus_state = FINISHED


def _CancelAutoFocus(future):
    """
    Canceller of AutoFocus task.
    """
    logging.debug("Cancelling autofocus...")

    with future._autofocus_lock:
        if future._autofocus_state == FINISHED:
            return False
        future._autofocus_state = CANCELLED
        logging.debug("Autofocus cancellation requested.")

    return True


def estimateAcquisitionTime(detector, scanner=None):
    """
    Estimate how long one acquisition will take
    detector (model.DigitalCamera or model.Detector): Detector on which to
      improve the focus quality
    scanner (None or model.Emitter): In case of a SED this is the scanner used
    return (0<float): time in s
    """
    # Check if there is a scanner (focusing = SEM)
    if model.hasVA(scanner, "dwellTime"):
        et = scanner.dwellTime.value * numpy.prod(scanner.resolution.value)
    elif model.hasVA(detector, "exposureTime"):
        et = detector.exposureTime.value
        # TODO: also add readoutRate * resolution if present
    else:
        # Completely random... but we are in a case where probably that's the last
        # thing the caller will care about.
        et = 1

    return et


# TODO: drop steps, which is unused, or use it
def estimateAutoFocusTime(detector, scanner=None, steps=MAX_STEPS_NUMBER):
    """
    detector (model.DigitalCamera or model.Detector): Detector on which to
      improve the focus quality
    scanner (None or model.Emitter): In case of a SED this is the scanner used
    Estimates overlay procedure duration
    """
    return steps * estimateAcquisitionTime(detector, scanner)


def Sparc2AutoFocus(align_mode, opm, streams=None, start_autofocus=True):

    """
    It provides the ability to check the progress of the complete Sparc2 autofocus
    procedure in a Future or even cancel it.
        Pick the hardware components
        Turn on the light and wait for it to be complete
        Change the optical path (closing the slit)
        Run AutoFocusSpectrometer
        Acquire one last image
        Turn off the light
    align_mode (str): OPM mode, spec-focus or spec-fiber-focus, streak-focus
    opm: OpticalPathManager
    streams: list of streams. The first stream is used for displaying the last
       image with the slit closed.
    return (ProgressiveFuture -> dict((grating, detector)->focus position)): a progressive future
          which will eventually return a map of grating/detector -> focus position, the same as AutoFocusSpectrometer
    raises:
            CancelledError if cancelled
            LookupError if procedure failed
    """
    focuser = None
    if align_mode == "spec-focus":
        focuser = model.getComponent(role='focus')
    elif align_mode == "spec-fiber-focus":
        # The "right" focuser is the one which affects the same detectors as the fiber-aligner
        aligner = model.getComponent(role='fiber-aligner')
        aligner_affected = aligner.affects.value  # List of component names
        for f in ("spec-ded-focus", "focus"):
            try:
                focus = model.getComponent(role=f)
            except LookupError:
                logging.debug("No focus component %s found", f)
                continue
            focuser_affected = focus.affects.value
            # Does the focus affects _at least_ one component also affected by the fiber-aligner?
            if set(focuser_affected) & set(aligner_affected):
                focuser = focus
                break
    else:
        raise ValueError("Unknown align_mode %s" % (align_mode,))

    if focuser is None:
        raise LookupError("Failed to find the focuser for align mode %s" % (align_mode,))

    if streams is None:
        streams = []

    for s in streams:
        if s.focuser is None:
            logging.debug("Stream %s has no focuser, will assume it's fine", s)
        elif s.focuser != focuser:
            logging.warning("Stream %s has focuser %s, while expected %s", s, s.focuser, focuser)

    # Get all the detectors, spectrograph and selectors affected by the focuser
    try:
        spgr, dets, selector = _getSpectrometerFocusingComponents(focuser)  # type: (object, List[Any], Optional[Any])
    except LookupError as ex:
        # TODO: just run the standard autofocus procedure instead?
        raise LookupError("Failed to focus in mode %s: %s" % (align_mode, ex))

    for s in streams:
        if s.detector.role not in (d.role for d in dets):
            logging.warning("The detector of the stream is not found to be one of the picked detectors %s")

    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1

    # Rough approximation of the times of each action:
    # * 5 s to turn on the light
    # * 5 s to close the slit
    # * af_time s for the AutoFocusSpectrometer procedure to be completed
    # * 0.2 s to acquire one last image
    # * 0.1 s to turn off the light
    if start_autofocus:
        # calculate the time needed for the AutoFocusSpectrometer procedure to be completed
        af_time = _totalAutoFocusTime(spgr, dets)
        autofocus_loading_times = (5, 5, af_time, 0.2, 5) # a list with the time that each action needs
    else:
        autofocus_loading_times = (5, 5)

    f = model.ProgressiveFuture(start=est_start, end=est_start + sum(autofocus_loading_times))
    f._autofocus_state = RUNNING
    # Time for each action left
    f._actions_time = list(autofocus_loading_times)
    f.task_canceller = _CancelSparc2AutoFocus
    f._autofocus_lock = threading.Lock()
    f._running_subf = model.InstantaneousFuture()

    # Run in separate thread
    executeAsyncTask(f, _DoSparc2AutoFocus, args=(f, streams, align_mode, opm, dets, spgr, selector, focuser, start_autofocus))
    return f


def _cancelSparc2ManualFocus(future):
    """
    Canceller of _DoSparc2ManualFocus task.
    """
    logging.debug("Cancelling manual focus...")
    if future._state == FINISHED:
        return False
    future._state = CANCELLED
    return True


def Sparc2ManualFocus(opm, bl, align_mode, toggled=True):
    """
    Provides the ability to check the progress of the Sparc2 manual focus
    procedure in a Future or even cancel it.
    :param opm: OpticalPathManager object
    :param bl: bright light object
    :param align_mode (str): OPM mode, spec-focus or spec-fiber-focus, streak-focus
    :param mf_toggled (bool): Toggle the manual focus button on/off
    :return (ProgressiveFuture -> for the _DoSparc2ManualFocus function)
    """
    est_start = time.time() + 0.1
    manual_focus_loading_time = 10  # Rough estimation of the slit movement
    f = model.ProgressiveFuture(start=est_start, end=est_start + manual_focus_loading_time)
    # The only goal for using a canceller is to make the progress bar stop
    # as soon as it's cancelled.
    f.task_canceller = _cancelSparc2ManualFocus
    executeAsyncTask(f, _DoSparc2ManualFocus, args=(opm, bl, align_mode, toggled))
    return f


def _DoSparc2ManualFocus(opm, bl, align_mode, toggled=True):
    """
    The actual implementation of the manual focus procedure, run asynchronously
    When the manual focus button is toggled:
            - Turn on the light
            - Change the optical path (closing the slit)
    :param future: the future object that is used to represent the task
    :param opm: OpticalPathManager object
    :param bl: brightlight object
    :param align_mode: OPM mode, spec-focus or spec-fiber-focus, streak-focus
    :param mf_toggled (bool): Toggle the manual focus button on/off
    """
    # First close slit, then switch on calibration light
    # Go to the special focus mode (=> close the slit)
    f = opm.setPath(align_mode)
    bl.power.value = bl.power.range[(1 * toggled)]  # When mf_toggled = False 1 will be 0
    f.result()


def GetSpectrometerFocusingDetectors(focuser):
    """
    Public wrapper around _getSpectrometerFocusingComponents to return detectors only
    :param focuser: (Actuator) the focuser that will be used to change focus
    :return: detectors (list of Detectors): the detectors attached on the
          spectrograph, which can be used for focusing
    """
    dets = []
    for n in focuser.affects.value:
        try:
            d = model.getComponent(name=n)
        except LookupError:
            logging.info("Focuser affects non-existing component %s", n)
            continue
        if d.role.startswith("ccd") or d.role.startswith("sp-ccd"):  # catches ccd*, sp-ccd*
            dets.append(d)
    return dets


def _getSpectrometerFocusingComponents(focuser):
    """
    Finds the different components needed to run auto-focusing with the
    given focuser.
    focuser (Actuator): the focuser that will be used to change focus
    return:
        * spectrograph (Actuator): component to move the grating and wavelength
        * detectors (list of Detectors): the detectors attached on the
          spectrograph, which can be used for focusing
        * selector (Actuator or None): the component to switch detectors
    raise LookupError: if not all the components could be found
    """

    dets = GetSpectrometerFocusingDetectors(focuser)
    if not dets:
        raise LookupError("Failed to find any detector for the spectrometer focusing")

    # The order doesn't matter for SpectrometerAutofocus, but the first detector
    # is used for detecting the light is on. In addition it's nice to be reproducible.
    # => Use alphabetical order of the roles
    dets.sort(key=lambda c: c.role)

    # Get the spectrograph and selector based on the fact they affect the
    # same detectors.
    spgr = _findSameAffects(["spectrograph", "spectrograph-dedicated"], dets)

    # Only need the selector if there are several detectors
    if len(dets) <= 1:
        selector = None  # we can keep it simple
    else:
        selector = _findSameAffects(["spec-det-selector", "spec-ded-det-selector"], dets)

    return spgr, dets, selector


def _findSameAffects(roles, affected):
    """
    Find a component that affects all the given components
    comps (list of str): set of component's roles in which to look for the "affecter"
    affected (list of Component): set of affected components
    return (Component): the first component that affects all the affected
    raise LookupError: if no component found
    """
    naffected = set(c.name for c in affected)
    for r in roles:
        try:
            c = model.getComponent(role=r)
        except LookupError:
            logging.debug("No component with role %s found", r)
            continue
        if naffected <= set(c.affects.value):
            return c
    else:
        raise LookupError("Failed to find a component that affects all %s" % (naffected,))


def _DoSparc2AutoFocus(future, streams, align_mode, opm, dets, spgr, selector, focuser, start_autofocus=True):
    """
        cf Sparc2AutoFocus
        return dict((grating, detector) -> focus pos)
    """
    def updateProgress(subf, start, end):
        """
        Updates the time progress when the current subfuture updates its progress
        """
        # if the future is complete, the standard progress update will work better
        if not subf.done():
            future.set_progress(end=end + sum(future._actions_time))

    try:
        if future._autofocus_state == CANCELLED:
            logging.info("Autofocus procedure cancelled before the light is on")
            raise CancelledError()

        logging.debug("Turning on the light")
        bl = model.getComponent(role="brightlight")
        _playStream(dets[0], streams)
        future._running_subf = light.turnOnLight(bl, dets[0])
        try:
            future._running_subf.result(timeout=60)
        except TimeoutError:
            future._running_subf.cancel()
            logging.warning("Light doesn't appear to have turned on after 60s, will try focusing anyway")
        if future._autofocus_state == CANCELLED:
            logging.info("Autofocus procedure cancelled after turning on the light")
            raise CancelledError()
        future._actions_time.pop(0)
        future.set_progress(end=time.time() + sum(future._actions_time))

        # Configure the optical path to the specific focus mode, for the detector
        # (so that the path manager knows which component matters). In case of
        # multiple detectors, any of them should be fine, as the only difference
        # should be the selector, which AutoFocusSpectrometer() takes care of.
        logging.debug("Adjusting the optical path to %s", align_mode)
        future._running_subf = opm.setPath(align_mode, detector=dets[0])
        future._running_subf.result()
        if future._autofocus_state == CANCELLED:
            logging.info("Autofocus procedure cancelled after closing the slit")
            raise CancelledError()
        future._actions_time.pop(0)
        future.set_progress(end=time.time() + sum(future._actions_time))

        # In case autofocus is manual return
        if not start_autofocus:
            return None

        # Configure each detector with good settings
        for d in dets:
            # The stream takes care of configuring its detector, so no need
            # In case there is no streams for the detector, take the binning and exposureTime values as far as they exist
            if not any(s.detector.role == d.role for s in streams):
                binning = 1, 1
                if model.hasVA(d, "binning"):
                    d.binning.value = d.binning.clip((2, 2))
                    binning = d.binning.value
                if model.hasVA(d, "exposureTime"):
                    # 0.2 s tends to be good for most cameras, but need to compensate
                    # if binning is smaller
                    exp = 0.2 * ((2 * 2) / numpy.prod(binning))
                    d.exposureTime.value = d.exposureTime.clip(exp)
        ret = {}
        logging.debug("Running AutoFocusSpectrometer on %s, using %s, for the detectors %s, and using selector %s",
                      spgr, focuser, dets, selector)
        try:
            future._running_subf = AutoFocusSpectrometer(spgr, focuser, dets, selector, streams)
            et = future._actions_time.pop(0)
            future._running_subf.add_update_callback(updateProgress)
            ret = future._running_subf.result(timeout=3 * et + 10)
        except TimeoutError:
            future._running_subf.cancel()
            logging.error("Timeout for autofocus spectrometer after %g s", et)
        except IOError:
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            raise
        if future._autofocus_state == CANCELLED:
            logging.info("Autofocus procedure cancelled after the completion of the autofocus")
            raise CancelledError()
        future.set_progress(end=time.time() + sum(future._actions_time))

        logging.debug("Acquiring the last image")
        if streams:
            _playStream(streams[0].detector, streams)
            # Ensure the latest image shows the slit focused
            streams[0].detector.data.get(asap=False)
            # pause the streams
            streams[0].is_active.value = False
        if future._autofocus_state == CANCELLED:
            logging.info("Autofocus procedure cancelled after acquiring the last image")
            raise CancelledError()
        future._actions_time.pop(0)
        future.set_progress(end=time.time() + sum(future._actions_time))

        logging.debug("Turning off the light")
        bl.power.value = bl.power.range[0]
        if future._autofocus_state == CANCELLED:
            logging.warning("Autofocus procedure is cancelled after turning off the light")
            raise CancelledError()
        future._actions_time.pop(0)
        future.set_progress(end=time.time() + sum(future._actions_time))

        return ret

    except CancelledError:
        logging.debug("DoSparc2AutoFocus cancelled")
    finally:
        # Make sure the light is always turned off, even if cancelled/error half-way
        if start_autofocus:
            try:
                bl.power.value = bl.power.range[0]
            except:
                logging.exception("Failed to turn off the light")

        with future._autofocus_lock:
            if future._autofocus_state == CANCELLED:
                raise CancelledError()
            future._autofocus_state = FINISHED


def _CancelSparc2AutoFocus(future):
    """
    Canceller of _DoSparc2AutoFocus task.
    """
    logging.debug("Cancelling autofocus...")

    with future._autofocus_lock:
        if future._autofocus_state == FINISHED:
            return False
        future._autofocus_state = CANCELLED
        future._running_subf.cancel()
        logging.debug("Sparc2AutoFocus cancellation requested.")

    return True


def AutoFocus(detector, emt, focus, dfbkg=None, good_focus=None, rng_focus=None, method=MTD_BINARY):
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
    rng_focus (tuple): if provided, the search of the best focus position is limited
      within this range
    method (MTD_*): focusing method, if BINARY we follow a dichotomic method while in
      case of EXHAUSTIVE we iterate through the whole provided range
    returns (model.ProgressiveFuture):  Progress of DoAutoFocus, whose result() will return:
            Focus position (m)
            Focus level
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAutoFocusTime(detector, emt))
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()
    f.task_canceller = _CancelAutoFocus

    # Run in separate thread
    if method == MTD_EXHAUSTIVE:
        autofocus_fn = _DoExhaustiveFocus
    elif method == MTD_BINARY:
        autofocus_fn = _DoBinaryFocus
    else:
        raise ValueError("Unknown autofocus method")

    executeAsyncTask(f, autofocus_fn,
                     args=(f, detector, emt, focus, dfbkg, good_focus, rng_focus))
    return f


def AutoFocusSpectrometer(spectrograph, focuser, detectors, selector=None, streams=None):
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
      the spectrometer.
    selector (Actuator or None): must have a rx axis with each position corresponding
     to one of the detectors. If there is only one detector, selector can be None.
    return (ProgressiveFuture -> dict((grating, detector)->focus position)): a progressive future
      which will eventually return a map of grating/detector -> focus position.
    """
    if not isinstance(detectors, Iterable):
        detectors = [detectors]
    if not detectors:
        raise ValueError("At least one detector must be provided")
    if len(detectors) > 1 and selector is None:
        raise ValueError("No selector provided, but multiple detectors")

    if streams is None:
        streams=[]

    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    #calculate the time for the AutoFocusSpectrometer procedure to be completed
    a_time = _totalAutoFocusTime(spectrograph, detectors)
    f = model.ProgressiveFuture(start=est_start, end=est_start + a_time)
    f.task_canceller = _CancelAutoFocusSpectrometer
    # Extra info for the canceller
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()
    f._subfuture = InstantaneousFuture()
    # Run in separate thread
    executeAsyncTask(f, _DoAutoFocusSpectrometer, args=(f, spectrograph, focuser, detectors, selector, streams))
    return f


# Rough time estimation for movements
MOVE_TIME_GRATING = 20  # s
MOVE_TIME_DETECTOR = 5  # , for the detector selector


def _totalAutoFocusTime(spgr, dets):
    ngs = len(spgr.axes["grating"].choices)
    nds = len(dets)
    et = estimateAutoFocusTime(dets[0], None)

    # 1 time for each grating/detector combination, with the gratings changing slowly
    move_et = ngs * MOVE_TIME_GRATING if ngs > 1 else 0
    move_et += (ngs * (nds - 1) + (1 if nds > 1 else 0)) * MOVE_TIME_DETECTOR

    return (ngs * nds) * et + move_et


def _updateAFSProgress(future, af_dur, grating_moves, detector_moves):
    """
    Update the progress of the future based on duration of the previous autofocus
    future (ProgressiveFuture)
    af_dur (0< float): total duration of the next autofocusing actions
    grating_moves (0<= int): number of grating moves left to do
    detector_moves (0<= int): number of detector moves left to do
    """
    tleft = af_dur + grating_moves * MOVE_TIME_GRATING + detector_moves * MOVE_TIME_DETECTOR
    future.set_progress(end=time.time() + tleft)


def CLSpotsAutoFocus(detector, focus, good_focus=None, rng_focus=None, method=MTD_EXHAUSTIVE):
    """
    Wrapper for do auto focus for CL spots. It provides the ability to check the progress of the CL spots auto focus
    procedure in a Future or even cancel it.

    detector (model.DigitalCamera or model.Detector): Detector on which to improve the focus quality. Should have the
            role diagnostic-ccd.
    focus (model.Actuator): The focus actuator.
    good_focus (float): if provided, an already known good focus position to be
            taken into consideration while autofocusing.
    rng_focus (tuple): if provided, the search of the best focus position is limited within this range.
    method: if provided, the search of the best focus position is limited within this range.
    returns (model.ProgressiveFuture):  Progress of DoAutoFocus, whose result() will return:
        Focus position (m)
        Focus level
    """
    detector.exposureTime.value = 0.01
    return AutoFocus(detector, None, focus, good_focus=good_focus, rng_focus=rng_focus, method=method)


def _mapDetectorToSelector(selector, detectors):
    """
    Maps detector to selector positions
    returns:
       axis (str): the selector axis to use
       position_map (dict (str -> value)): detector name -> selector position
    """
    # We pick the right axis by assuming that it's the only one which has
    # choices, and the choices are a dict pos -> detector name.
    # TODO: handle every way of indicating affect position in acq.path? -> move to odemis.util
    det_2_sel = {}
    sel_axis = None
    for an, ad in selector.axes.items():
        if hasattr(ad, "choices") and isinstance(ad.choices, dict):
            sel_axis = an
            for pos, value in ad.choices.items():
                for d in detectors:
                    if d.name in value:
                        # set the position so it points to the target
                        det_2_sel[d] = pos

            if det_2_sel:
                # Found an axis with names of detectors, that should be the
                # right one!
                break

    if len(det_2_sel) < len(detectors):
        raise ValueError("Failed to find all detectors (%s) in positions of selector axes %s" %
                  (", ".join(d.name for d in detectors), list(selector.axes.keys())))

    return sel_axis, det_2_sel


def _playStream(detector, streams):
    """
    It first pauses the streams and then plays only the stream related to the corresponding detector
    detector : (model.DigitalCamera or model.Detector): detector from which the image is acquired
    streams : list of streams
    """
    # First pause all the streams
    for s in streams:
        if s.detector.role != detector.role:
            s.is_active.value = False
            s.should_update.value = False

    # After all the streams are paused, play only the steam that is related to the detector
    for s in streams:
        if s.detector.role == detector.role:
            s.should_update.value = True
            s.is_active.value = True
            break


def _DoAutoFocusSpectrometer(future, spectrograph, focuser, detectors, selector, streams):
    """
    cf AutoFocusSpectrometer
    return dict((grating, detector) -> focus pos)
    """
    ret = {}
    # Record the wavelength and grating position
    pos_orig = {k: v for k, v in spectrograph.position.value.items()
                              if k in ("wavelength", "grating")}
    gratings = list(spectrograph.axes["grating"].choices.keys())
    if selector:
        sel_orig = selector.position.value
        sel_axis, det_2_sel = _mapDetectorToSelector(selector, detectors)


    def is_current_det(d):
        """
        return bool: True if the given detector is the current one selected by
          the selector.
        """
        if selector is None:
            return True
        return det_2_sel[d] == selector.position.value[sel_axis]

    # Note: this procedure works well with the SR-193i. In particular, it
    # records the focus position for each grating and detector.
    # It needs to be double checked if used with other spectrographs.
    if "Shamrock" not in spectrograph.hwVersion:
        logging.warning("Spectrometer autofocusing has not been tested on"
                        "this type of spectrograph (%s)", spectrograph.hwVersion)

    # In theory, it should be "safe" to only find the right focus once for each
    # grating (for a given detector), and once for each detector (for a given
    # grating). The focus for the other combinations grating/ detectors should
    # be grating + detector offset. However, currently the spectrograph API
    # doesn't allow to explicitly set these values. As in the worse case so far,
    # the spectrograph has only 2 gratings and 2 detectors, it's simpler to just
    # run the autofocus a 4th time.

    # For progress update
    ngs = len(gratings)
    nds = len(detectors)
    cnts = ngs * nds
    ngs_moves = ngs if ngs > 1 else 0
    nds_moves = (ngs * (nds - 1) + (1 if nds > 1 else 0))
    try:
        if future._autofocus_state == CANCELLED:
            raise CancelledError()

        # We "scan" in two dimensions: grating + detector. Grating is the "slow"
        # dimension, as it's typically the move that takes the most time (eg, 20s).

        # Start with the current grating, to save time
        gratings.sort(key=lambda g: 0 if g == pos_orig["grating"] else 1)
        for g in gratings:
            # Start with the current detector
            dets = sorted(detectors, key=is_current_det, reverse=True)
            for d in dets:
                logging.debug("Autofocusing on grating %s, detector %s", g, d.name)
                if selector:
                    if selector.position.value[sel_axis] != det_2_sel[d]:
                        nds_moves = max(0, nds_moves - 1)
                    selector.moveAbsSync({sel_axis: det_2_sel[d]})
                try:
                    if spectrograph.position.value["grating"] != g:
                        ngs_moves = max(0, ngs_moves - 1)
                    # 0th order is not absolutely necessary for focusing, but it
                    # typically gives the best results
                    spectrograph.moveAbsSync({"wavelength": 0, "grating": g})
                except Exception:
                    logging.exception("Failed to move to 0th order for grating %s", g)

                if future._autofocus_state == CANCELLED:
                    raise CancelledError()

                tstart = time.time()
                # Note: we could try to reuse the focus position from the previous
                # grating or detector, and pass it as good_focus, to save a bit
                # of time. However, if for some reason the previous value was
                # way off (eg, because it's a simulated detector, or there is
                # something wrong with the grating), it might prevent this run
                # from finding the correct value.
                _playStream(d, streams)
                future._subfuture = AutoFocus(d, None, focuser)
                fp, flvl = future._subfuture.result()
                ret[(g, d)] = fp
                cnts -= 1
                _updateAFSProgress(future, (time.time() - tstart) * cnts, ngs_moves, nds_moves)

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

