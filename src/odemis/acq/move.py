# -*- coding: utf-8 -*-
"""
Created on 27 July 2020

@author: Éric Piel, Bassim Lazem

Copyright © 2020 Delmic

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

import logging
import threading
from asyncio import CancelledError
from concurrent.futures._base import CANCELLED, RUNNING, FINISHED

import numpy
import scipy

from odemis import model, util
from odemis.util import executeAsyncTask

MAX_SUBMOVE_DURATION = 60  # s
LOADING, IMAGING, TILTED, LOADING_PATH, UNKNOWN = 0, 1, 2, 3, 5
ATOL_LINEAR_POS = 100e-6  # m
ATOL_ROTATION_POS = 1e-3  # rad (~0.5°)
RTOL_PROGRESS = 0.1


def getCurrentPositionLabel(current_pos, stage):
    """
    Determine where lies the current stage position
    :param current_pos: (dict str->float) Current position of the stage
    :param stage: Sample stage that's being controlled
    :return: (int) a value from LOADING, IMAGING, TILTED, LOADING_PATH, UNKNOWN
    """
    stage_md = stage.getMetadata()
    stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
    stage_active = stage_md[model.MD_FAV_POS_ACTIVE]
    active_range = stage_md[model.MD_POS_ACTIVE_RANGE]
    # Check that the stage X,Y,Z are within the active range
    for axis in {'x', 'y', 'z'}:
        if not _isInRange(current_pos, active_range, axis):
            break
    else:
        if current_pos['rx'] == 0 and current_pos['rz'] == 0:
            return IMAGING
        else:
            return TILTED
    # Check the stage is new the loading position
    for axis in stage.axes:
        if not _isNearDeactive(current_pos, stage_deactive, axis):
            break
    else:
        return LOADING

    # Check the current position is near the line between ACTIVE and DEACTIVE
    loading_progress = getMovementProgress(current_pos, stage_active, stage_deactive)
    if loading_progress is not None:
        return LOADING_PATH

    # None of the above -> unknown position
    return UNKNOWN


def getMovementProgress(current_pos, start_pos, end_pos):
    """
    Compute the position on the path between start and end positions of a stage movement (such as LOADING to IMAGING)
    If it’s too far from the line between the start and end positions, then it’s considered out of the path.
    :param current_pos: (dict str->float) Current position of the stage
    :param start_pos: (dict str->float) A position to start the movement from
    :param end_pos: (dict str->float) A position to end the movement to
    :return:(0<=float<=1, or None) Ratio of the progress, None if it's far away from of the path
    """

    def get_distance(start, end):
        # Calculate the euclidean distance between two 3D points
        sp = numpy.array([start['x'], start['y'], start['z']])
        ep = numpy.array([end['x'], end['y'], end['z']])
        return scipy.spatial.distance.euclidean(ep, sp)

    def check_axes(pos):
        if not {'x', 'y', 'z'}.issubset(set(pos.keys())):
            raise ValueError("Missing x,y,z axes in {} for correct distance measurement.".format(pos))

    # Check we have the x,y,z axes in all points
    check_axes(current_pos)
    check_axes(start_pos)
    check_axes(end_pos)
    # Get distance for current point in respect to start and end
    from_start = get_distance(start_pos, current_pos)
    to_end = get_distance(current_pos, end_pos)
    total_length = get_distance(start_pos, end_pos)
    # Check if current position is on the line from start to end position
    # That would happen if start_to_current +  current_to_start = total_distance from start to end
    if util.almost_equal((from_start + to_end), total_length, rtol=RTOL_PROGRESS):
        return min(from_start / total_length, 1.0)  # Clip in case from_start slightly > total_length
    else:
        return None


def _isInRange(current_pos, active_range, axis):
    """
    Check if current position is within active range
    :return: True if position in active range, False otherwise
    """
    pos = current_pos[axis]
    axis_active_range = [r for r in active_range[axis]]
    # Add 1% margin for hardware slight errors
    margin = (axis_active_range[1] - axis_active_range[0]) * 0.01
    return (axis_active_range[0] - margin) <= pos <= (axis_active_range[1] + margin)


def _isNearDeactive(current_pos, stage_deactive, axis):
    """
    Check whether given axis is near stage's FAV_POS_DEACTIVE position
    :return: True if the axis is near (within 1 mm or 0.1 rad) FAV_POS_DEACTIVE, False otherwise
    :raises ValueError if axis is unknown
    """
    current_value = current_pos[axis]
    deactive_value = stage_deactive[axis]
    if axis in {'x', 'y', 'z'}:
        return abs(deactive_value - current_value) < ATOL_LINEAR_POS
    elif axis in {'rx', 'rz'}:
        return util.rot_almost_equal(current_value, deactive_value, atol=ATOL_ROTATION_POS)
    else:
        raise ValueError("Unknown axis value %s.", axis)


def cryoLoadSample(target):
    """
    Provide the ability to switch between loading position and imaging position, without bumping into anything.
    :param target: (int) target position either one of the constants LOADING or IMAGING
    :return (CancellableFuture -> None): cancellable future of the move to observe the progress, and control the
   raise ValueError exception
    """
    # Get the stage and focus components from the backend components
    stage = model.getComponent(role='stage')
    focus = model.getComponent(role='focus')

    f = model.CancellableFuture()
    f.task_canceller = _cancelCryoMoveSample
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f._running_subf = model.InstantaneousFuture()
    # Run in separate thread
    executeAsyncTask(f, _doCryoLoadSample, args=(f, stage, focus, target))
    return f


def _doCryoLoadSample(future, stage, focus, target):
    """
    Do the actual switching procedure for the Cryo sample stage between loading and imaging
    :param future: cancellable future of the move
    :param stage: sample stage that's being controlled
    :param focus: focus for optical lens
    :param target: target position either one of the constants LOADING or IMAGING
    """
    try:
        stage_md = stage.getMetadata()
        focus_md = focus.getMetadata()
        stage_active = stage_md[model.MD_FAV_POS_ACTIVE]
        stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
        active_range = stage_md[model.MD_POS_ACTIVE_RANGE]
        focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
        current_pos = stage.position.value
        # Create axis->pos dict from target position given smaller number of axes
        filter_dict = lambda keys, d: {key: d[key] for key in keys}

        if target == LOADING:
            # Check that the stage X,Y,Z are within the limits
            for axis in {'x', 'y', 'z'}:
                if not _isInRange(current_pos, active_range, axis):
                    logging.warning("Axis %s position is out of active range.", axis)
            # Check that current position is near the ACTIVE to DEACTIVE line
            # (ie, in case it was previously stopped half-way through loading/unloading)
            if getMovementProgress(stage.position.value, stage_active, stage_deactive) is None:
                logging.warning("Stage position is not near the ACTIVE to DEACTIVE line.")

            if abs(stage_deactive['rx']) > ATOL_ROTATION_POS:
                raise ValueError(
                    "Absolute value of rx for FAV_POS_DEACTIVE is greater than {}".format(ATOL_ROTATION_POS))

            # Create an ordered sub moves list to perform the loading move
            sub_moves = [
                (focus, focus_deactive),
                (stage, filter_dict({'rx', 'rz'}, stage_deactive)),
                (stage, filter_dict({'x', 'y'}, stage_deactive)),
                (stage, filter_dict({'z'}, stage_deactive)),
            ]
        elif target == IMAGING:
            for axis in stage.axes:
                # Check if the current position is near DEACTIVE position (within 1 mm or 0.1 rad)
                if not _isNearDeactive(current_pos, stage_deactive, axis):
                    raise ValueError("Axis %s position is not near FAV_POS_DEACTIVE position.", axis)

            focus_active = focus_md[model.MD_FAV_POS_ACTIVE]
            # Create an ordered sub moves list to perform the imaging move
            sub_moves = [
                (focus, focus_deactive),
                (stage, filter_dict({'z'}, stage_active)),
                (stage, filter_dict({'x', 'y'}, stage_active)),
                (stage, filter_dict({'rx', 'rz'}, stage_active)),
                # TODO: check if the following movement is necessary
                (focus, focus_active),
            ]
        else:
            raise ValueError("Unknown target value %s.", target)

        for component, sub_move in sub_moves:
            run_sub_move(future, component, sub_move)
    except CancelledError:
        logging.info("_doCryoLoadSample cancelled.")
    except Exception as exp:
        target_str = {LOADING: "loading", IMAGING: "imaging"}
        logging.exception("Failure to move to {} position.".format(target_str.get(target, lambda: "invalid")))
        raise
    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


def cryoTiltSample(rx, rz=None):
    """
    Provide the ability to switch between imaging and tilted position, withing bumping into anything.
    Imaging position is considered when rx and rz are equal 0, otherwise it's considered tilting
    :param rx: (float) rotation movement in x axis
    :param rz: (float) rotation movement in z axis
    :return (CancellableFuture -> None): cancellable future of the move to observe the progress, and control the
   raise ValueError exception
    """
    # Get the stage and focus components from the backend components
    stage = model.getComponent(role='stage')
    focus = model.getComponent(role='focus')

    f = model.CancellableFuture()
    f.task_canceller = _cancelCryoMoveSample
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f._running_subf = model.InstantaneousFuture()
    # Run in separate thread
    executeAsyncTask(f, _doCryoTiltSample, args=(f, stage, focus, rx, rz,))
    return f


def _doCryoTiltSample(future, stage, focus, rx, rz):
    """
    Do the actual switching procedure for the Cryo sample stage between imaging and tilting
    :param future: cancellable future of the move
    :param stage: sample stage that's being controlled
    :param focus: focus for optical lens
    :param rx: (float) rotation movement in x axis
    :param rz: (float) rotation movement in z axis
    """
    try:
        stage_md = stage.getMetadata()
        focus_md = focus.getMetadata()
        stage_active = stage_md[model.MD_FAV_POS_ACTIVE]
        focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
        active_range = stage_md[model.MD_POS_ACTIVE_RANGE]
        current_pos = stage.position.value
        # Check that the stage X,Y,Z are within the limits
        for axis in {'x', 'y', 'z'}:
            if not _isInRange(current_pos, active_range, axis):
                raise ValueError("Axis %s position is out of active range.", axis)
        # To hold the ordered sub moves list to perform the tilting/imaging move
        sub_moves = []
        # Move focus to FAV_POS_DEACTIVE only if stage rx and rz are equal to 0
        # Otherwise stop if focus is's not already near FAV_POS_DEACTIVE
        if (util.almost_equal(current_pos['rx'], 0, atol=ATOL_ROTATION_POS) and
                util.almost_equal(current_pos['rz'], 0, atol=ATOL_ROTATION_POS)):
            sub_moves.append((focus, focus_deactive))
        elif not util.almost_equal(focus.position.value, focus_deactive, atol=ATOL_LINEAR_POS):
            raise ValueError("Cannot proceed while focus is not near FAV_POS_DEACTIVE position.")

        if rx == 0 and rz == 0:  # Imaging
            # Get the actual Imaging position (which should be ~ 0 as well)
            rx = stage_active['rx']
            rz = stage_active['rz']
            sub_moves.append((stage, {'rz': rz}))
            sub_moves.append((stage, {'rx': rx}))
        else:
            sub_moves.append((stage, {'rx': rx}))
            if rz is not None:
                sub_moves.append((stage, {'rz': rz}))

        for component, sub_move in sub_moves:
            run_sub_move(future, component, sub_move)
    except CancelledError:
        logging.info("_doCryoTiltSample cancelled.")
    except Exception as exp:
        logging.exception("Failure to move to position rx={}, rz={}.".format(rx, rz))
        raise
    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


def _cancelCryoMoveSample(future):
    """
    Canceller of _doCryoTiltSample and _doCryoLoadSample tasks
    """
    logging.debug("Cancelling CryoMoveSample...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future._running_subf.cancel()
        logging.debug("CryoMoveSample cancellation requested.")

    return True


def run_sub_move(future, component, sub_move):
    """
    Perform the sub moveAbs using the given component and axis->pos dict
    :param future: cancellable future of the whole move
    :param component: Either the stage of the focus component
    :param sub_move: the sub_move axis->pos dict
    :raises TimeoutError: if the sub move timed out
    :raises CancelledError: if the sub move is cancelled
    """
    try:
        with future._task_lock:
            if future._task_state == CANCELLED:
                logging.info("Move procedure is cancelled before moving {} -> {}.".format(component.name, sub_move))
                raise CancelledError()
            logging.debug("Performing sub move {} -> {}".format(component.name, sub_move))
            future._running_subf = component.moveAbs(sub_move)
        future._running_subf.result(timeout=MAX_SUBMOVE_DURATION)
    except TimeoutError:
        future._running_subf.cancel()
        logging.exception("Timed out during moving {} -> {}.".format(component.name, sub_move))
        raise
    if future._task_state == CANCELLED:
        logging.info("Move procedure is cancelled after moving {} -> {}.".format(component.name, sub_move))
        raise CancelledError()
