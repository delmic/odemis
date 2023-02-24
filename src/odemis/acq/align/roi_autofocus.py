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

import numpy

from odemis import model
from odemis.acq import align
from odemis.acq.align.autofocus import estimateAutoFocusTime
from odemis.util import executeAsyncTask


def do_autofocus_in_roi(
        f: model.ProgressiveFuture,
        bbox: tuple,
        stage: model.Component,
        ccd: model.Component,
        focus: model.Component,
        focus_range: tuple,
        nx: int = 3,
        ny: int = 3,
        conf_level: float = 0
):
    """
    Run autofocus in a given roi. The roi is divided in nx * ny positions and autofocus is run at each position.

    :param f: future of autofocus in roi
    :param bbox: bounding box of the roi, tuple of (xmin, ymin, xmax, ymax) in meters
    :param stage: stage component
    :param ccd: ccd component
    :param focus: focus component
    :param focus_range: focus range, tuple of (zmin, zmax) in meters
    :param nx: (int) the number of positions in x where to focus
    :param ny: (int) the number of positions in y where to focus
    :param conf_level: (float) confidence level of the focus metric, default 0 takes all values.
        For values above 0 only the positions with confidence higher than conf_level are taken into account.
    :return:
    """
    try:
        init_pos = stage.position.value
        xmin, ymin, xmax, ymax = bbox

        focus_positions = []
        for y in numpy.linspace(ymin, ymax, ny):
            for x in numpy.linspace(xmin, xmax, nx):
                if f._autofocus_roi_state == CANCELLED:
                    raise CancelledError()
                stage.moveAbsSync({"x": x, "y": y})
                # run autofocus
                f._running_subf = align.AutoFocus(ccd, None, focus, rng_focus=focus_range)
                foc_pos, foc_lev, conf = f._running_subf.result(timeout=900)
                if foc_lev >= conf_level:
                    focus_positions.append([stage.position.value["x"],
                                            stage.position.value["y"],
                                            focus.position.value["z"]])
                    logging.debug(f"Added focus with confidence of {foc_lev} at position: {focus_positions[-1]}")
        return focus_positions
    except TimeoutError:
        f._running_subf.cancel()
        logging.exception(f"Timed out during autofocus at position {x, y} .")
        raise
    except CancelledError:
        logging.info(f"Autofocus in roi cancelled at position {x, y}.")
    finally:
        logging.debug(f"Moving back to initial stage position {init_pos}")
        stage.moveAbsSync(init_pos)
        with f._autofocus_roi_lock:
            if f._autofocus_roi_state == CANCELLED:
                raise CancelledError()
            f._autofocus_state = FINISHED


def estimate_autofocus_in_roi_time(n_focus_points, detector):
    """
    Estimate the time it will take to run autofocus in a roi with nx * ny positions.
    :param n_focus_points: (tuple) number of focus points in x and y direction
    :param detector: component of the detector
    :return:
    """
    # add 0.5 second for stage movement
    return n_focus_points[0] * n_focus_points[1] * (estimateAutoFocusTime(detector, None) + 0.5)


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
        n_focus_points: tuple = (3, 3),
        conf_level: int = 0
):
    """
    Wrapper for do_autofocus_in_roi. It provides the ability to check the progress of autofocus
    procedure and cancel it.
    :param bbox: bounding box of the roi, tuple of (xmin, ymin, xmax, ymax) in meters
    :param stage: stage component
    :param ccd: ccd component
    :param focus: focus component
    :param focus_range: focus range, tuple of (zmin, zmax) in meters
    :param n_focus_points: (tuple) number of focus points in x and y direction
    :param conf_level: (float) confidence level of the focus metric, default 0 takes all values.
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimate_autofocus_in_roi_time(n_focus_points,
                                                                               ccd))
    f._autofocus_roi_state = RUNNING
    f._autofocus_roi_lock = threading.Lock()
    f.task_canceller = _cancel_autofocus_bbox

    executeAsyncTask(f, do_autofocus_in_roi,
                     args=(f, bbox, stage, ccd, focus, focus_range, n_focus_points[0], n_focus_points[1], conf_level))
    return f
