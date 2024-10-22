# -*- coding: utf-8 -*-
"""
Created on 2 Feb 2023

@author: Thera Pals

Copyright © 2023 Thera Pals, Delmic

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

import logging
import threading
import time
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError
from typing import Iterable, Dict, List, Optional

import numpy

from odemis import model
from odemis.acq import align
from odemis.acq.align.autofocus import estimateAutoFocusTime
from odemis.util import executeAsyncTask


def correct_roi_autofocus_time(time_per_action: Dict[str, List[float]]) -> Optional[float]:
    """
    Compute average time taken to run autofocus and move stage for one focus position.
    :param time_per_action: (Dict[str, List[float]) (Dict[str, List[float]) time taken per action (move and focus)
        for each focus position
    :return: computed time in seconds
    """
    observed_time = 0
    for k, v in time_per_action.items():
        if len(v) > 0:
            observed_time += numpy.mean(v)
        else:
            # Time is not recorded for each action yet
            return None
    else:
        observed_time = None
    return observed_time


def do_autofocus_in_roi(
        f: model.ProgressiveFuture,
        bbox: tuple,
        stage: model.Component,
        ccd: model.Component,
        focus: model.Component,
        focus_range: tuple,
        focus_points: Iterable[tuple],
        conf_level: float = 0
) -> list:
    """
    Run autofocus in a given roi. The roi is divided in nx * ny positions and autofocus is run at each position.

    :param f: future of autofocus in roi
    :param bbox: bounding box of the roi, tuple of (xmin, ymin, xmax, ymax) in meters
    :param stage: stage component
    :param ccd: ccd component
    :param focus: focus component
    :param focus_range: focus range, tuple of (zmin, zmax) in meters
    :param focus_points : (x,y) positions in the given bbox where autofocus will be run
    :param conf_level: (0<=float<=1) :param conf_level: (float) cut-off value for confidence level of the focus metric,
        only focus points with a confidence above the cut-off will be saved. Default 0 saves all values.
    :return: (list) list of focus positions in x, y, z
    """
    focus_positions = []
    try:
        init_pos = stage.position.value
        time_observers = {"focus", "move"}
        time_per_action = {k: [] for k in time_observers}
        for i, (x, y) in enumerate(focus_points):
            # Update the time progress
            f.set_progress(end=estimate_autofocus_in_roi_time(len(focus_points) - i, ccd, time_per_action) + time.time())
            with f._autofocus_roi_lock:
                if f._autofocus_roi_state == CANCELLED:
                    raise CancelledError()

                logging.debug(f"Moving the stage to autofocus at position: {x, y}")
                move_to_pos_start = time.time()
                stage.moveAbsSync({"x": x, "y": y})
                time_per_action["move"].append(time.time() - move_to_pos_start)
                # run autofocus
                focus_start = time.time()
                f._running_subf = align.AutoFocus(ccd, None, focus, rng_focus=focus_range)

            foc_pos, foc_lev, conf = f._running_subf.result(timeout=900)
            time_per_action["focus"].append(time.time() - focus_start)
            if conf >= conf_level:
                focus_positions.append([stage.position.value["x"],
                                        stage.position.value["y"],
                                        focus.position.value["z"]])
                logging.debug(f"Added focus with confidence of {conf} at position: {focus_positions[-1]}")
            else:
                logging.debug(f"Focus level is not added due to low confidence of {conf} at position: {x, y}.")

    except TimeoutError:
        logging.debug(f"Timed out during autofocus at position {x, y} .")
        f._running_subf.cancel()
        raise

    except CancelledError:
        logging.info(f"Autofocus in roi cancelled at position {x, y}.")
        raise

    finally:
        logging.info(f"The actual time taken per focus position for each action is {time_per_action}")
        logging.debug(f"Moving back to initial stage position {init_pos}")
        stage.moveAbsSync(init_pos)
        with f._autofocus_roi_lock:
            if f._autofocus_roi_state == CANCELLED:
                raise CancelledError()
            f._autofocus_state = FINISHED

    return focus_positions


def estimate_autofocus_in_roi_time(n_focus_points, detector, time_per_action=None):
    """
    Estimate the time it will take to run autofocus in a roi with nx * ny positions.
    :param n_focus_points: (tuple) number of focus points in x and y direction
    :param detector: component of the detector
    :param time_per_action: (Dict[str, List[float]) time taken per action (move and focus) for each focus position
    :return: time in seconds to complete autofocus procedure at given focus points
    """
    # After the autofocus on first focus point, correct the time taken for subsequent focus positions
    if time_per_action is None:
        time_per_action = {}
    time_per_focus_position = correct_roi_autofocus_time(time_per_action)
    if time_per_focus_position:
        return time_per_focus_position * n_focus_points
    # add 10 seconds to account for stage movement between focus points
    focus_time = n_focus_points * (estimateAutoFocusTime(detector, None))
    move_time = n_focus_points * 10
    logging.info(f"The computed time in seconds for autofocus for {n_focus_points} focus positions for move is {move_time}, "
                 f"focus is {focus_time}")
    return focus_time + move_time


def _cancel_autofocus_bbox(future):
    """
    Canceller of autofocus bbox task.
    """
    logging.debug("Cancelling autofocus in roi...")

    with future._autofocus_roi_lock:
        if future._autofocus_roi_state == FINISHED:
            return False
        future._autofocus_roi_state = CANCELLED
        future._running_subf.cancel()
        logging.debug("Autofocus in roi cancellation requested.")

    return True


def autofocus_in_roi(
        bbox: tuple,
        stage: model.Component,
        ccd: model.Component,
        focus: model.Component,
        focus_range: tuple,
        focus_points: Iterable[tuple],
        conf_level: float = 0
):
    """
    Wrapper for do_autofocus_in_roi. It provides the ability to check the progress of autofocus
    procedure and cancel it. The current position of the focus component is used as the starting position.
    :param bbox: bounding box of the roi, tuple of (xmin, ymin, xmax, ymax) in meters
    :param stage: stage component
    :param ccd: ccd component
    :param focus: focus component
    :param focus_range: focus range, tuple of (zmin, zmax) in meters
    :param focus_points : (x,y) positions in the given bbox where autofocus will be run
    :param conf_level: :param conf_level: (0<float<1) cut-off value for confidence level of the focus metric, only focus
        points with a confidence above the cut-off will be saved. Default 0 saves all values.
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    n_focus_points = len(focus_points)
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimate_autofocus_in_roi_time(n_focus_points,
                                                                               ccd))
    f._autofocus_roi_state = RUNNING
    f._autofocus_roi_lock = threading.Lock()
    f.task_canceller = _cancel_autofocus_bbox

    executeAsyncTask(f, do_autofocus_in_roi,
                     args=(f, bbox, stage, ccd, focus, focus_range, focus_points, conf_level))
    return f
