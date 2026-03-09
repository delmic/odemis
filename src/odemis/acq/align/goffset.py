import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import TimeoutError, CancelledError
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING
from typing import Any, Dict, List, Optional, Tuple, Union, Callable

import numpy

from odemis import model
from odemis.acq.align import light
from odemis.model import InstantaneousFuture
from odemis.util import executeAsyncTask, almost_equal
from odemis.util.driver import guessActuatorMoveDuration
from odemis.util.focus import MeasureSEMFocus, Measure1d, MeasureSpotsFocus, AssessFocus
from odemis.util.img import Subtract
from scipy.optimize import curve_fit

def gaussian(x, amplitude, x0, width):
    """
    Gaussian function (for curve fitting).

    :param x: input coordinates
    :param amplitude:  peak intensity
    :param x0 = peak's center
    :param width = standard deviation
    :return: Gaussian function evaluated at x
    """
    intensity = amplitude*numpy.exp(-0.5 * ((x - x0) / width)**2)
    return intensity


def find_peak_position(data: numpy.ndarray, window_radius: int = 15) -> float:
    """
    Finds the peak position in the given spectrum data.
    It can handle both 1D and 2D data (in which case it averages over the first dimension).

    The function first identifies the absolute peak, then creates a window around it to minimize noise influence.
    It then calculates a weighted average of the positions within this window, using the intensity values as weights.
    For improved accuracy, it attempts to fit a Gaussian curve to the windowed data, estimating the peak's center, width
    and amplitude.

    However, the curve fit can fail to converge or produce unreasonable results if the initial guess is poor, if there are
    outliers, baseline trends, or if the data is too noisy. In such cases, the function falls back to using the weighted
    average as the peak position estimate. The weighted average is more robust as it does not run an iterative optimizer,
    so it has no convergence or numerical-optimization failure modes, but it may be less accurate if the peak is not well-defined
    or if there are multiple peaks within the window.
    """

    if data.ndim == 2:
        spectrum = data.mean(axis=0)  # squash data into a 1D array
    else:
        spectrum = data

    x = numpy.arange(len(spectrum))
    peak_idx = numpy.argmax(spectrum)  # find the absolute highest point

    # create a window around the peak
    start = max(0, peak_idx - window_radius)
    end = min(len(spectrum), peak_idx + window_radius + 1)
    window_data = spectrum[start:end]
    window_idx = numpy.arange(start, end)

    # calculate the weighted averages
    weights = window_data.clip(min=0)

    # Corner case: window has no positive signal. Use the maximum index itself
    # as the best estimate of the peak, since weighted average would be undefined
    if weights.sum() == 0:
        weighted_avg = float(peak_idx)
        logging.info("Weighted average fallback: all window data <= 0, using peak_idx=%d as estimate",
            peak_idx)
    else:
        weighted_avg = float(numpy.sum(window_idx * window_data) / numpy.sum(window_data))

    # try Gaussian fit for better accuracy, but fallback to weighted average if it fails or is out of bounds

    try:
        p0 = [window_data.max(), weighted_avg, 2.5] # intial guess: [amplitude, center, width]
        popt, pcov = curve_fit(gaussian, window_idx, window_data, p0=p0)
        peak = popt[1]

        if start <= peak <= end:
            return float(peak)

    except RuntimeError:
        logging.info("Gaussian peak fit did not converge, falling back to weighted average")

    except ValueError:
        logging.exception("Gaussian peak fit failed due to invalid input")

    return weighted_avg


def estimate_goffset_scale(spgr: model.Actuator, detector: model.Detector, delta=5.0) -> float:
    """
    Estimate the scale factor between a change in the grating offset ('goffset') 
    and the resulting shift of the spectral peak on the detector.

    The function moves the actuator by a small step (delta) and measures the peak position before and after the move.
    It calculates the ratio of pixel shift per unit of goffset. The actuator is returned to its original position
    after measurement.

    If the measured scale is unreasonably small or large, the function retries recursively
    and falls back to a default value of 0.5 if necessary.

    :param spgr: spectrograph
    :param detector: detector
    :param delta: The relative goffset step size to apply when measuring the scale (default: 5.0).
                  The actual step may be negated to avoid exceeding hardware limits.
                  
    :return: Tuple (scale, p0, p1)
         scale: estimated pixels per unit of goffset
         p0: peak position at the initial goffset
         p1: peak position after applying the test delta
    """
    # get initial state
    data0 = detector.data.get(asap=False)
    p0 = find_peak_position(data0)

    # check limits before moving
    current_pos = spgr.position.value["goffset"]
    goffset_max = spgr.axes["goffset"].range[1]

    # ensure that max limit isn't violated
    move_direction = 1 if (current_pos + delta < goffset_max) else -1
    actual_delta = delta*move_direction

    # move and measure
    spgr.moveRelSync({"goffset": actual_delta})
    data1 = detector.data.get(asap=False)
    p1 = find_peak_position(data1)

    # return back to start
    spgr.moveRelSync({"goffset": -actual_delta})

    # calculate goffset scale
    scale = (p1 - p0) / actual_delta

    logging.info(
        f"SCALE TRACKING | p0: {p0:.1f} | p1: {p1:.1f} | Delta: {actual_delta} | Shift: {(p1-p0):.1f} | Result Scale: {scale:.4f}")


    # If the estimated scale is extremely small, the measurement is likely unreliable
    # (e.g., due to noise or a flat spectrum).
    # In that case we retry the estimation once by calling the function recursively.
    # If the retry still fails (raising a RuntimeError), we fall back to a default
    # scale value to ensure the algorithm can continue and avoid infinite recursion.

    if abs(scale) < 1e-3 or abs(scale)>10.0:
        try:
            scale, p0, p1 = estimate_goffset_scale(spgr, detector)
        except RuntimeError:
            logging.warning("Scale too small, using default 0.5")
            scale = 0.5

    return scale, p0, p1

def sparc_auto_grating_offset(spgr: model.Actuator,
                           detector: model.Detector,
                           tolerance_px: float = 0.4,
                           max_it: int = 20,
                           gain: float = 0.4) -> model.ProgressiveFuture:
    """
    Start an asynchronous task that centers the spectral peak by adjusting the
    grating offset (goffset).

    :param spgr: spectrograph
    :param detector: detector
    :param tolerance_px: the acceptable displacement of the peak from the center in pixels (default: 0.4)
    :param max_it: maximum number of iterations to attempt (default: 20)
    :param gain: proportional gain factor for adjusting the goffset (default: 0.4)
    :return: A ``ProgressiveFuture`` representing the asynchronous alignment
             task. The future can be used to monitor progress, retrieve the
             result, or cancel the alignment.
    """

    est_start = time.time() + 0.05
    est_time = max_it*0.5  # conservative estimate

    f = model.ProgressiveFuture(start=est_start, end=est_start + est_time)

    f._task_lock = threading.Lock()
    f._task_state = RUNNING
    f.task_canceller = _cancel_sparc_auto_grating_offset

    executeAsyncTask(
        f,
        _do_sparc_auto_grating_offset,
        args=(f, spgr, detector, tolerance_px, max_it, gain),
    )

    return f

def _do_sparc_auto_grating_offset(future: model.ProgressiveFuture,
                              spgr: model.Actuator,
                              detector: model.Detector,
                              tolerance_px: float,
                              max_it: int,
                              gain: float) -> bool:

    """
    Iteratively adjusts the grating offset to align the spectral peak to the center of the detector.
    The algorithm estimates the current peak position, calculates the error from the center, and moves the grating offset
    proportionally to reduce this error.
    It continues until the peak is within the specified tolerance or the maximum number of iterations is reached.
    """

    logging.info("Running alignment | detector=%s |",
                 detector.name)

    try:
        scale, p0, p1 = estimate_goffset_scale(spgr, detector)
        center_target = detector.resolution.value[0] / 2 # adjust if 0 is not the center
        total_goffset_displacement = 0.0

        # clamp move to safe fraction of axis range
        axis = spgr.axes["goffset"]
        minv, maxv = axis.range
        max_step = 0.1 * (maxv - minv)  # max 10% of range

        for i in range(max_it):
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()

            if i == 0:
                peak_px = p0
            else:
                data = detector.data.get(asap=False)
                peak_px = find_peak_position(data)

            error_px = peak_px - center_target

            if abs(error_px) <= tolerance_px:
                logging.info(
                    "Spectral peak aligned after %d iterations | error_px=%.2f | total_goffset_change=%.4f",
                    i + 1,
                    error_px,
                    total_goffset_displacement
                )
                return True

            delta_goffset = -gain*(error_px/scale)

            delta_goffset = max(-max_step, min(max_step, delta_goffset))
            total_goffset_displacement += delta_goffset

            logging.debug(f"DEBUG | Iter: {i} | Peak: {peak_px:.1f} | Error: {error_px:.1f} | Move: {delta_goffset:.4f} | Total Change: {total_goffset_displacement:.4f}")
            spgr.moveRelSync({"goffset": delta_goffset})

            future.set_progress(
                end=time.time() + (max_it - i - 1) * 0.5)  # update estimated end time

        logging.warning("SparcAutoGratingOffset did not converge")
        return False

    except CancelledError:
        logging.debug("SparcAutoGratingOffset cancelled")
        raise
    except Exception as e:
        logging.error(f"Alignment error: {e}")
        raise

def _cancel_sparc_auto_grating_offset(future: model.ProgressiveFuture):

    """
    Canceller of _do_sparc_auto_grating_offset task.
    """
    with future._task_lock:
        future._task_state = CANCELLED