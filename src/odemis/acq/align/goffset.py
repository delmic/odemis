import logging
import numpy
import threading
import time

from collections.abc import Iterable
from concurrent.futures import CancelledError
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING
from odemis import model
from odemis.acq.align.autofocus import _mapDetectorToSelector
from odemis.model import InstantaneousFuture
from odemis.util import executeAsyncTask
from scipy.optimize import curve_fit
from typing import Any, Dict, List, Optional, Tuple, Union
from scipy.ndimage import gaussian_filter1d


MOVE_TIME_GRATING = 20  # s
MOVE_TIME_DETECTOR = 5  # s
EST_ALIGN_TIME = 30  # s

def gaussian(x: numpy.ndarray, amplitude: float, x0: float, width: float) -> numpy.ndarray:
    """
    Gaussian function (for curve fitting).

    :param x: input coordinates
    :param amplitude:  peak intensity
    :param x0 = peak's center
    :param width = standard deviation
    :return: Gaussian function evaluated at x (numpy array)
    """

    intensity = amplitude * numpy.exp(-0.5 * ((x - x0) / width) ** 2)
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

    :param data: 1D or 2D array containing the spectrum (if 2D, it will be averaged to 1D)
    :param window_radius: number of pixels on either side of the peak to include in the window for fitting (default: 15)
    :return: estimated peak position in pixels (float)
    :raises RuntimeError: if no significant peak is detected (SNR too low)
    """

    if data.ndim == 2:
        spectrum = data.mean(axis=0)  # squash data into a 1D array
    else:
        spectrum = data

    # compute SNR by comparing the peak height above the median background to the background magnitude and returns
    # False if that SNR is below the threshold. This helps reject cases with no real peak,
    # where the Gaussian fit would fail or produce nonsense results.

    # peak_value = float(spectrum.max())
    # background = float(numpy.median(spectrum))
    # snr = (peak_value - background) / (abs(background) + 1e-6)

    spectrum = spectrum - numpy.median(spectrum)
    noise_std = numpy.std(spectrum)
    peak_height = spectrum.max()
    snr = peak_height / (noise_std + 1e-6)

    if snr < 10.0:  # tune threshold
        raise RuntimeError("No peak detected (SNR too low)")

    spectrum_smooth = gaussian_filter1d(spectrum, sigma=2)
    peak_idx = int(numpy.argmax(spectrum_smooth))
    # peak_idx = numpy.argmax(spectrum)  # find the absolute highest point

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
        weighted_avg = float(numpy.sum(window_idx * window_data) / numpy.sum(weights))

    # try Gaussian fit for better accuracy, but fallback to weighted average if it fails or is out of bounds

    try:
        p0 = [window_data.max(), weighted_avg, 2.5]  # intial guess: [amplitude, center, width]
        popt, pcov = curve_fit(gaussian, window_idx, window_data, p0=p0)
        peak = popt[1]

        if start <= peak <= end:
            return float(peak)

    except RuntimeError:
        logging.info("Gaussian peak fit did not converge, falling back to weighted average")

    except ValueError:
        logging.exception("Gaussian peak fit failed due to invalid input")

    return weighted_avg

def peak_is_present(spectrum: numpy.ndarray,
                    snr_threshold: float=5.0,
                    width_range: Tuple[float, float]=(0.5, 12.0)) -> bool:
    """
       Test to decide whether spectral peak is present.

        The test uses:
          - a simple SNR threshold comparing the maximum to the median background,
          - a local width estimate computed from a small window around the peak to
            reject hot pixels and extremely broad features.

    :param spectrum: 1D array containing the spectrum (intensity vs pixel)
    :param snr_threshold: minimum required signal-to-noise ratio for a peak to be considered present (default: 1)
    :param width_range: acceptable range of estimated peak widths in pixels (default: (0.5, 12.0))
    :return: True if a peak meeting the criteria is present, False otherwise
    """

    # basic stats
    # peak_value = float(spectrum.max())
    # background = float(numpy.median(spectrum))
    # snr = (peak_value - background) / (abs(background) + 1e-6)

    spectrum = spectrum - numpy.median(spectrum)
    noise_std = numpy.std(spectrum)
    peak_value = spectrum.max()
    snr = peak_value / (noise_std + 1e-6)

    if snr < snr_threshold:
        return False

    # estimate width around the peak
    spectrum_smooth = gaussian_filter1d(spectrum, sigma=2)
    peak_idx = int(numpy.argmax(spectrum_smooth))
    # peak_idx = int(numpy.argmax(spectrum))
    if peak_idx < 1 or peak_idx > len(spectrum) - 2:
        logging.debug("Peak too close to edge idx=%d len=%d", peak_idx, len(spectrum))
        return False

    window = spectrum[peak_idx-2 : peak_idx+3]
    x = numpy.arange(len(window))
    w = window - window.min()
    if w.sum() == 0:
        return False

    mean = numpy.sum(x * w) / numpy.sum(w)
    var = numpy.sum(w * (x - mean)**2) / numpy.sum(w)
    width = numpy.sqrt(var)
    present = width_range[0] <= width <= width_range[1]
    logging.debug("snr=%.2f width=%.2f present=%s", snr, width, present)

    return present


def coarse_scan_goffset_for_peak(spgr, detector, future: model.ProgressiveFuture, step: int=2000) -> Tuple[float, float]:
    """
    Coarse scan across the goffset axis until a real peak becomes visible.

    The function performs absolute moves across the configured goffset axis
    from min to max in steps of `step`. After each move it reads the detector,
    computes a 1D spectrum (mean across the first axis), and applies
    `peak_is_present` to decide whether a real peak is on the detector.
    When a peak is found, `find_peak_position` is used to estimate its pixel
    position and the function returns the actual goffset (as reported by the
    actuator) and the peak pixel.

    :param spgr: spectrograph
    :param detector: detector
    :param step: step size in goffset units for the coarse scan (default: 2000)
    :return: tuple (actual_goffset, peak_pixel)
    :raises RuntimeError: if no peak is found across the full goffset range
    """

    current = float(spgr.position.value["goffset"])
    step = abs(step) if step != 0 else 2000.0
    max_span = 200000.0  # limit how far we wander from the current valid position

    logging.debug(
        "Coarse local scan around goffset %.1f with step %.1f and max span %.1f",
        current, step, max_span)

    # Build a sequence of positions: current, +step, -step, +2*step, -2*step, ...
    positions = [current]
    k = 1
    while k * step <= max_span:
        positions.append(current + k * step)
        positions.append(current - k * step)
        k += 1

    tried = set()

    for g in positions:
        _checkCancelled(future)

        # avoid duplicate moves due to symmetry or float rounding
        key = round(g, 3)
        if key in tried:
            continue
        tried.add(key)

        logging.debug("Coarse scan move attempt to goffset %.3f", g)
        try:
            spgr.moveAbsSync({"goffset": g})
        except ValueError:
            logging.warning("Skipping invalid goffset %.3f (%s)", g, ValueError)
            continue

        data = detector.data.get(asap=False)
        spectrum = data.mean(axis=0)

        if peak_is_present(spectrum):
            peak = find_peak_position(data)
            return g, peak

    raise RuntimeError("Peak not found in local goffset scan around current position")

def estimate_goffset_scale(spgr: model.Actuator,
                           detector: model.Detector,
                           delta: float=5.0,
                           retries: int=1) -> Tuple[float, float, float]:
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
    :param retries: number of retries allowed if the estimated scale is unreliable (default: 1).

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
    actual_delta = delta * move_direction

    # move and measure
    spgr.moveRelSync({"goffset": actual_delta})
    data1 = detector.data.get(asap=False)
    p1 = find_peak_position(data1)

    # return back to start
    spgr.moveRelSync({"goffset": -actual_delta})

    # calculate goffset scale
    scale = (p1 - p0) / actual_delta

    logging.info(
        "SCALE TRACKING | p0: %.1f | p1: %.1f | Delta: %.1f | Shift: %.1f | Result Scale: %.4f",
        p0, p1, actual_delta, (p1 - p0), scale)

    # If the estimated scale is extremely small, the measurement is likely unreliable
    # (e.g., due to noise or a flat spectrum).
    # In that case we retry the estimation once by calling the function recursively.
    # If the retry still fails (raising a RuntimeError), we fall back to a default
    # scale value to ensure the algorithm can continue and avoid infinite recursion.

    if abs(scale) < 1e-3 or abs(scale) > 10.0:
        logging.warning(
            "Unreliable scale estimate (%.4f). Retries left: %d",
            scale, retries)

        if retries > 0:
            return estimate_goffset_scale(spgr, detector, delta, retries - 1)

        logging.warning("Scale estimation failed after retries, using default 0.5")
        scale = 0.5

    return scale, p0, p1

def log_detector_state(caller: str, stage: str, detector: model.Detector, data: numpy.ndarray):
    shape = data.shape

    # Keep consistent with detection pipeline
    spectrum = data.mean(axis=0) if data.ndim == 2 else data

    max_val = float(spectrum.max())
    min_val = float(spectrum.min())
    mean_val = float(spectrum.mean())

    background = float(numpy.median(spectrum))
    signal = spectrum - background
    peak_height = float(signal.max())

    # robust noise estimate
    noise_region = signal[signal < 0]
    if len(noise_region) > 10:
        noise_std = float(numpy.std(noise_region))
    else:
        noise_std = float(numpy.std(signal))

    snr = peak_height / (noise_std + 1e-6)

    logging.warning(
        "%s: goffset: [%s] Detector=%s | shape=%s | min=%.2f max=%.2f mean=%.2f | bg=%.2f | noise=%.2f | snr=%.2f",
        caller, stage, detector.name, shape, min_val, max_val, mean_val, background, noise_std, snr)

def sparc_auto_grating_offset(spgr: model.Actuator,
                              detector: model.Detector,
                              tolerance_px: float = 0.4,
                              max_it: int = 60,
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
    est_time = max_it * 0.5  # rough estimated time

    f = model.ProgressiveFuture(start=est_start, end=est_start + est_time)

    f._task_lock = threading.Lock()
    f._task_state = RUNNING
    f.task_canceller = _cancel_sparc_auto_grating_offset

    executeAsyncTask(f, _do_sparc_auto_grating_offset, args=(f, spgr, detector, tolerance_px, max_it, gain))

    return f

def _do_sparc_auto_grating_offset(future: model.ProgressiveFuture,
                                  spgr: model.Actuator,
                                  detector: model.Detector,
                                  tolerance_px: float,
                                  max_it: int,
                                  gain: float) -> bool:
    """
    Core alignment routine that iteratively adjusts the grating offset to center the peak.

    Behavior:
    - Attempts to acquire a peak on the detector (coarse scan) if none is present.
    - Measures the local goffset-to-pixel scale only when a peak is present and not centered.
    - Runs a centering loop until the peak is within `tolerance_px` or the max #iterations is reached.
    - Respects cancellation via the provided ProgressiveFuture.

    :param future: model.ProgressiveFuture
    :param spgr: spectrograph
    :param detector: detector
    :param tolerance_px: pixel tolerance for successful alignment
    :param max_it: maximum number of centering iterations
    :param gain: proportional gain for converting pixel error to goffset correction
    :return: True if alignment succeeded (peak within tolerance), False otherwise (bool)
    :raises CancelledError: if the future was cancelled during execution
    """

    logging.info("Running alignment | detector=%s |", detector.name)

    try:
        center_target = detector.resolution.value[0] / 2
        data0 = detector.data.get(asap=False)
        spectrum0 = data0.mean(axis=0) if data0.ndim == 2 else data0

        if peak_is_present(spectrum0):
            peak0 = find_peak_position(data0)
            logging.debug("Initial read: peak0=%.2f px", peak0)
            peak_present = True
        else:
            logging.debug("Initial read: no significant peak detected, scanning the axes")
            peak_present = False
            peak0 = None

        # # initial read: try to get a valid peak without moving the grating
        # try:
        #     data0 = detector.data.get(asap=False)
        #     peak0 = find_peak_position(data0)   # raises RuntimeError if no peak present
        #     logging.debug("Initial read: peak0=%.2f px", peak0)
        #     peak_present = True
        # except RuntimeError:
        #     logging.debug("Initial read: no significant peak detected, scanning the axes")
        #     peak_present = False
        #     peak0 = None

        # if peak present and already centered, do nothing
        if peak_present:
            initial_error_px = peak0 - center_target
            logging.debug("Initial error_px=%.3f px (tolerance=%.3f)", initial_error_px, tolerance_px)
            if abs(initial_error_px) <= tolerance_px:
                logging.info("Peak already centered | peak=%.2f | center=%.2f | error=%.3f",
                             peak0, center_target, initial_error_px)
                return True

        # if no peak present, run acquisition (this will move the grating)
        else:
            try:
                g_acq, p_acq = coarse_scan_goffset_for_peak(spgr, detector, future, step=2000)
                logging.info("Peak acquired at goffset=%d pixel=%.2f", g_acq, p_acq)
                peak0 = p_acq
            except RuntimeError:
                logging.error("Peak acquisition failed — aborting alignment")
                return False

            # after acquisition, check if acquisition already placed peak within tolerance
            post_acq_error_px = peak0 - center_target
            logging.debug("Post-acquisition error_px=%.3f px", post_acq_error_px)
            if abs(post_acq_error_px) <= tolerance_px:
                logging.info("Peak centered by acquisition | peak=%.2f | center=%.2f | error=%.3f",
                             peak0, center_target, post_acq_error_px)
                return True

        # peak is present and not centered -> estimate scale and center
        # scale, p0, p1 = estimate_goffset_scale(spgr, detector)
        # logging.info("Scale estimated: %.4f px/goffset | p0=%.2f p1=%.2f", scale, p0, p1)

        try:
            scale, p0, p1 = estimate_goffset_scale(spgr, detector)
            logging.info("Scale estimated: %.4f px/goffset | p0=%.2f p1=%.2f", scale, p0, p1)
        except RuntimeError:
            logging.warning("Peak lost during scale estimation. Forcing re-acquisition.")
            try:
                g_acq, p_acq = coarse_scan_goffset_for_peak(spgr, detector, future, step=2000)
                scale, p0, p1 = estimate_goffset_scale(spgr, detector)  # try one more time
            except RuntimeError:
                logging.error("Peak re-acquisition failed — aborting alignment")
                return False

        # prefer p1 (after probe move) if available, else p0
        # p1 is preferred because it reflects the peak position after a controlled probe move,
        # making it more up‑to‑date and reliable than the initial p0 measurement.
        start_peak = p1 if p1 is not None else p0

        total_goffset_displacement = 0.0
        axis = spgr.axes["goffset"]
        minv, maxv = axis.range

        for i in range(max_it):
            _checkCancelled(future)

            if i == 0:
                peak_px = start_peak
            else:
                data = detector.data.get(asap=False)
                try:
                    peak_px = find_peak_position(data)
                except RuntimeError:
                    logging.error("Peak re-acquisition failed during iteration %d — aborting", i)
                    return False

            error_px = peak_px - center_target

            if abs(error_px) <= tolerance_px:
                return True

            # Calculate required adjustment based on pixel error and scaling factor
            delta_goffset = -gain * (error_px / scale)
            current = spgr.position.value["goffset"]

            # Clamp move to stay within the allowed goffset range.
            # Ensures that we don't command a step that would exceed axis limits.
            delta_goffset = max(minv - current, min(maxv - current, delta_goffset))

            # Accumulate the actual displacement for total movement tracking
            total_goffset_displacement += delta_goffset

            logging.debug(
                "Iter: %d | Peak: %.2f | Error: %.2f | Move: %.6f | Total Change: %.6f",
                i, peak_px, error_px, delta_goffset, total_goffset_displacement)

            try:
                spgr.moveRelSync({"goffset": delta_goffset})
            except ValueError:
                logging.warning("Hardware offset limit reached: %s. Keeping max allowed move.", ValueError)
                break

            future.set_progress(end=time.time() + (max_it - i - 1) * 0.5)

        logging.warning("SparcAutoGratingOffset did not converge within max iterations")
        return False

    except CancelledError:
        logging.debug("SparcAutoGratingOffset cancelled")
        raise
    except Exception as e:
        logging.error("Alignment error: %s", e)
        raise


def _cancel_sparc_auto_grating_offset(future: model.ProgressiveFuture) -> bool:
    """
    Canceller of _do_sparc_auto_grating_offset task.
    """
    with future._task_lock:
        future._task_state = CANCELLED


def _checkCancelled(future: "model.ProgressiveFuture") -> None:
    """
    Check if the future has been cancelled, and if so raise CancelledError.
    """

    with future._task_lock:
        if future._task_state == CANCELLED:
            raise CancelledError()


def _total_alignment_time(n_gratings: int,
                          n_detectors: int) -> float:
    """
    Estimate total time for aligning all grating-detector combinations.

    :param n_gratings: number of gratings to align
    :param n_detectors: number of detectors to align
    :return: estimated total time in seconds
    """

    runs = n_detectors + max(0, n_gratings - 1)
    move_time = ((n_gratings - 1) * MOVE_TIME_GRATING + (n_detectors - 1) * MOVE_TIME_DETECTOR)

    # total time = time spent running alignment algorithms + time spent moving hardware
    return runs * EST_ALIGN_TIME + move_time

def auto_align_grating_detector_offsets(spectrograph: model.Actuator,
                                        detectors: Union[model.Detector, List[model.Detector]],
                                        selector: Optional[model.Actuator] = None,
                                        streams: Optional[List['Stream']] = None) -> model.ProgressiveFuture:
    """
    Automatically align grating-detector offsets for all combinations of gratings and detectors.
     - If a selector is provided, it will be used to switch between detectors for the first grating, then the first detector
     will be used for all subsequent gratings.
     - For multiple detectors, the grating alignment will only be adjusted for the first detector; subsequent detectors will
     be aligned by adjusting the detector offset with the grating alignment fixed.

    :param spectrograph: spectrograph
    :param detectors: list of detectors
    :param selector: optional selector to switch between detectors
    :param streams: optional list of streams to update with progress
    :return: ProgressiveFuture that will resolve to a dict mapping (grating, detector)
    :raises ValueError: if no detectors provided, or if multiple detectors provided without a selector
    :raises CancelledError: if the operation is cancelled
    """

    if not isinstance(detectors, Iterable):
        detectors = [detectors]
    if not detectors:
        raise ValueError("At least one detector must be provided")
    if len(detectors) > 1 and selector is None:
        raise ValueError("No selector provided, but multiple detectors")

    if streams is None:
        streams = []

    est_start = time.time() + 0.1
    n_gratings = len(spectrograph.axes["grating"].choices)
    n_detectors = len(detectors)
    a_time = _total_alignment_time(n_gratings, n_detectors)
    f = model.ProgressiveFuture(start=est_start, end=est_start + a_time)
    f._progress = 0.0
    f.task_canceller = _cancel_auto_align_grating_detector_offsets

    f._task_lock = threading.Lock()
    f._task_state = RUNNING
    f._subfuture = InstantaneousFuture()
    executeAsyncTask(f, _do_auto_align_grating_detector_offsets, args=(f, spectrograph, detectors, selector, streams))
    return f

def _do_auto_align_grating_detector_offsets(future: model.ProgressiveFuture,
                                            spectrograph: model.Actuator,
                                            detectors: List[model.Detector],
                                            selector: Optional[model.Actuator],
                                            streams: List['Stream'],
                                            stabilization_time: float = 10.0) -> Optional[Dict[Any, Any]]:
    """
    Iterate through each grating and detector combination, adjusting the selector if provided, and run the auto-alignment algorithm.
     - If a selector is provided, it will be used to switch between detectors for the first grating, then the first detector
     will be used for all subsequent gratings.
     - For multiple detectors, the grating alignment will only be adjusted for the first detector; subsequent detectors will
     be aligned by adjusting the detector offset with the grating alignment fixed.

    :param future: ProgressiveFuture to update with progress and results
    :param spectrograph: spectrograph
    :param detectors: list of detectors
    :param selector: optional selector to switch between detectors
    :param streams: optional list of streams to update with progress
    :param stabilization_time: time to wait after moving hardware before starting alignment (default: 15s)

    :return: dict mapping (grating, detector) to alignment success boolean
    :raises CancelledError: if the operation is cancelled
    """

    results: dict[tuple, bool] = {}
    original_pos = {k: v for k, v in spectrograph.position.value.items()
                    if k in ("wavelength", "grating")}

    gratings = sorted(list(spectrograph.axes["grating"].choices.keys()))
    logging.info(f"Available gratings: {list(spectrograph.axes['grating'].choices.keys())}")

    first_detector = detectors[0]

    if selector:
        original_selector = selector.position.value
        selector_axes, detector_to_selector = _mapDetectorToSelector(selector, detectors)
        logging.debug("selector_axes=%s detector_to_selector keys=%s",
                      selector_axes, list(detector_to_selector.keys()))

    def is_current_detector(d):
        if selector is None:
            return True
        return detector_to_selector[d] == selector.position.value[selector_axes]

    # Calculate total steps for a simple progress bar
    total_steps = len(detectors) + (len(gratings) - 1)
    current_step = 0
    future._progress = 0.0

    # Start alignment for the first grating
    try:
        g0 = gratings[0]
        logging.info("Starting alignment for initial grating: %s", g0)

        spectrograph.moveAbsSync({"grating": g0, "wavelength": 0})
        time.sleep(stabilization_time)

        detectors_sorted = sorted(detectors, key=is_current_detector, reverse=True)

        # align each detector for the first grating
        for d in detectors_sorted:
            _checkCancelled(future)
            logging.info("Starting alignment | Detector: %s | Grating: %s", d.name, g0)

            if selector:
                selector.moveAbsSync({selector_axes: detector_to_selector[d]})
                time.sleep(stabilization_time)

            gui_data = d.data.get(asap=False)
            log_detector_state("GUI", "INITIAL", d, gui_data)

            future._subfuture = sparc_auto_grating_offset(spectrograph, d)
            success = future._subfuture.result()
            results[(g0, d.name)] = success

            # Update progress
            current_step += 1
            future._progress = current_step / total_steps

            logging.info("Finished alignment | Detector: %s | Grating: %s", d.name, g0)

        if selector:
            selector.moveAbsSync({selector_axes: detector_to_selector[first_detector]})
            time.sleep(stabilization_time)

        # align remaining gratings using the first detector
        for g in gratings[1:]:
            _checkCancelled(future)
            logging.info("Switching to grating: %s", g)

            spectrograph.moveAbsSync({"grating": g, "wavelength": 0})
            time.sleep(stabilization_time)
            logging.info("Starting alignment | Detector: %s | Grating: %s", first_detector.name, g)

            future._subfuture = sparc_auto_grating_offset(spectrograph, first_detector)
            success = future._subfuture.result()
            results[(g, first_detector.name)] = success

            # Update progress
            current_step += 1
            future._progress = current_step / total_steps

            logging.info("Finished alignment | Detector: %s | Grating: %s", first_detector.name, g)

        future._progress = 1.0
        return results

    except CancelledError:
        logging.info("Auto-alignment cancelled")
        raise

    finally:
        spectrograph.moveAbsSync(original_pos)
        if selector:
            selector.moveAbsSync(original_selector)

        with future._task_lock:
            future._task_state = FINISHED

def _cancel_auto_align_grating_detector_offsets(future: model.ProgressiveFuture) -> bool:
    """
    Canceller for _do_auto_align_grating_detector_offsets task.
    """
    logging.debug("Cancelling autoalignment...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future._subfuture.cancel()
        logging.debug("Auto-alignment cancellation requested")

    return True
