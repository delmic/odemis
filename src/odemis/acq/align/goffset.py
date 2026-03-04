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

def Gaussian(x, amplitude, x0, width):
    """
    x = input coordinates
    amplitude = peak intensity
    x0 = peak's center
    width = standard deviation
    """
    intensity = amplitude*numpy.exp(-0.5*((x-x0)/width)**2)
    return intensity


def find_peak_position(data: numpy.ndarray, window_radius: int = 15) -> float:
    """
    Finds the peak position in the given spectrum data.
    It can handle both 1D and 2D data (in which case it averages over the first dimension).
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

    if weights.sum() == 0:
        weighted_avg = float(peak_idx)
    else:
        weighted_avg = float(numpy.sum(window_idx * window_data) / numpy.sum(window_data))

    # try Gaussian fit for better accuracy, but fallback to weighted average if it fails or is out of bounds

    try:
        p0 = [window_data.max(), weighted_avg, 2.5]
        popt, pcov = curve_fit(Gaussian, window_idx, window_data, p0=p0)
        peak = popt[1]

        if start <= peak <= end:
            return float(peak)

    except Exception:
        pass

    return weighted_avg


def estimate_goffset_scale(spgr: model.Actuator, detector: model.Detector, delta=5.0) -> float:
    """
    Estimates how many pixels the peak shifts per 1 unit of goffset.
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
    scale = (p1-p0)/actual_delta

    logging.info(
        f"SCALE TRACKING | p0: {p0:.1f} | p1: {p1:.1f} | Delta: {actual_delta} | Shift: {(p1-p0):.1f} | Result Scale: {scale:.4f}")

    if abs(scale) < 1e-3:
        try:
            scale = estimate_goffset_scale(spgr, detector)
        except RuntimeError:
            logging.warning("Scale too small, using default 0.5")
            scale = 0.5

    return scale

def SparcAutoGratingOffset(spgr: model.Actuator,
                           detector: model.Detector,
                           align_grating: bool = True,
                           tolerance_px: float = 0.4,
                           max_it: int = 20,
                           gain: float = 0.4) -> model.ProgressiveFuture:

    est_start = time.time() + 0.05
    est_time = max_it*0.5  # conservative estimate

    f = model.ProgressiveFuture(start=est_start, end=est_start + est_time)
    f._centering_state = RUNNING
    f._centering_lock = threading.Lock()
    f.task_canceller = _CancelSparcAutoGratingOffset

    executeAsyncTask(f, _DoSparcAutoGratingOffset, args=(f, spgr, detector, align_grating, tolerance_px, max_it, gain))

    return f

def _DoSparcAutoGratingOffset(future: model.ProgressiveFuture,
                              spgr: model.Actuator,
                              detector: model.Detector,
                              align_grating: bool,
                              tolerance_px: float,
                              max_it: int,
                              gain: float) -> bool:

    """
    Iteratively adjusts the grating offset to align the spectral peak to the center of the detector.
    The algorithm estimates the current peak position, calculates the error from the center, and moves the grating offset proportionally to reduce this error.
    It continues until the peak is within the specified tolerance or the maximum number of iterations is reached.
    """

    success = False

    logging.info("Running alignment | detector=%s | align_grating=%s",detector.name, align_grating,)

    try:
        scale = estimate_goffset_scale(spgr, detector)
        center_target = detector.resolution.value[0]/2 # adjust if 0 is not the center
        total_goffset_displacement = 0.0

        for i in range(max_it):
            with future._centering_lock:
                if future._centering_state == CANCELLED:
                    raise CancelledError()

            # find peak and calculate error
            peak_px = find_peak_position(detector.data.get(asap=False))
            error_px = peak_px - center_target

            if abs(error_px) <= tolerance_px:
                logging.info("Spectral peak aligned to 0 px after %d iterations", i + 1)
                success = True
                return True

            delta_goffset = -gain*(error_px/scale)

            # clamp move to safe fraction of axis range
            axis = spgr.axes["goffset"]
            minv, maxv = axis.range
            max_step = 0.1*(maxv-minv)  # max 10% of range

            delta_goffset = max(-max_step, min(max_step, delta_goffset))
            total_goffset_displacement += delta_goffset

            print(f"DEBUG | Iter: {i} | Peak: {peak_px:.1f} | Error: {error_px:.1f} | Move: {delta_goffset:.4f} | Total Change: {total_goffset_displacement:.4f}")
            spgr.moveRelSync({"goffset": delta_goffset})
            time.sleep(2)

            future.set_progress(
                end=time.time() + (max_it-i-1)*0.5)  # update estimated end time

        logging.warning("SparcAutoGratingOffset did not converge")
        return False

    except CancelledError:
        logging.debug("SparcAutoGratingOffset cancelled")
        raise
    except Exception as e:
        logging.error(f"Alignment error: {e}")
        raise

def _CancelSparcAutoGratingOffset(future: model.ProgressiveFuture):
    with future._centering_lock:
        future._centering_state = CANCELLED