import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import TimeoutError, CancelledError
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING
from typing import Any, Dict, List, Optional, Tuple, Union

from odemis import model
from odemis.model import InstantaneousFuture
from odemis.util import executeAsyncTask

from odemis.acq.align.autofocus import _mapDetectorToSelector
from odemis.acq.align.goffset import sparc_auto_grating_offset


def _checkCancelled(future: "model.ProgressiveFuture"):

    """
    Check if the future has been cancelled, and if so raise CancelledError.
    """

    with future._function_lock:
        if future._function_state == CANCELLED:
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
    move_time = ((n_gratings-1) * MOVE_TIME_GRATING + (n_detectors - 1) * MOVE_TIME_DETECTOR)

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
    f.task_canceller = _cancel_auto_align_grating_detector_offsets

    f._task_lock = threading.Lock()
    f._task_state = RUNNING
    f._subfuture = InstantaneousFuture()
    executeAsyncTask(f, _do_auto_align_grating_detector_offsets, args=(f, spectrograph, detectors, selector, streams))
    return f

MOVE_TIME_GRATING = 20 #s
MOVE_TIME_DETECTOR = 5 #s
EST_ALIGN_TIME = 30 #s

def _do_auto_align_grating_detector_offsets(future: model.ProgressiveFuture,
                                       spectrograph: model.Actuator,
                                       detectors: List[model.Detector],
                                       selector: Optional[model.Actuator],
                                       streams: List['Stream'],
                                       stabilization_time: float = 15.0) -> Optional[Dict[Any, Any]]:

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

    def is_current_detector(d):
        if selector is None:
            return True
        return detector_to_selector[d] == selector.position.value[selector_axes]

    try:
        g0 = gratings[0]
        logging.info(f"Starting alignment for initial grating: {g0}")

        spectrograph.moveAbsSync({"grating": g0, "wavelength": 0})
        time.sleep(stabilization_time)

        detectors_sorted = sorted(detectors, key=is_current_detector, reverse=True)

        # align each detector for the first grating
        for d in detectors_sorted:
            _checkCancelled(future)
            logging.info(f"Starting alignment | Detector: {d.name} | Grating: {g0}")

            if selector:
                selector.moveAbsSync({selector_axes: detector_to_selector[d]})
                future._subfuture = sparc_auto_grating_offset(spectrograph, d)
                success = future._subfuture.result()
                results[(g0, d.name)] = success

                logging.info(f"Finished alignment | Detector: {d.name} | Grating: {g0} | Success: {success}")

        if selector:
            selector.moveAbsSync({selector_axes: detector_to_selector[first_detector]})

        # align remaining gratings using the first detector
        for g in gratings[1:]:
            _checkCancelled(future)
            logging.info(f"Switching to grating: {g}")

            spectrograph.moveAbsSync({"grating": g, "wavelength": 0})
            time.sleep(stabilization_time)
            logging.info(f"Starting alignment | Detector: {first_detector.name} | Grating: {g}")

            future._subfuture = sparc_auto_grating_offset(spectrograph, first_detector)
            success = future._subfuture.result()
            results[(g, first_detector.name)] = success

            logging.info(f"Finished alignment | Detector: {first_detector.name} | Grating: {g} | Success: {success}")

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
    logging.debug("Cancelling autoalignment...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future._subfuture.cancel()
        logging.debug("Auto-alignment cancellation requested")

    return True
