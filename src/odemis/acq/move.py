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
UNKNOWN, LOADING, IMAGING, ALIGNMENT, COATING, LOADING_PATH, MILLING, SEM_IMAGING, FM_IMAGING, GRID_1, GRID_2 = -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
target_pos_str = {LOADING: "LOADING", IMAGING: "IMAGING", COATING: "COATING", ALIGNMENT: "ALIGNMENT",
                  LOADING_PATH: "LOADING PATH", UNKNOWN: "UNKNOWN", SEM_IMAGING: "SEM IMAGING"}
meteor_labels = {SEM_IMAGING: "SEM IMAGING", FM_IMAGING: "FM IMAGING", GRID_1: "GRID 1", GRID_2: "GRID 2", LOADING: "LOADING", UNKNOWN: "UNKNOWN"}
ATOL_LINEAR_POS = 100e-6  # m
ATOL_ROTATION_POS = 1e-3  # rad (~0.5°)
RTOL_PROGRESS = 0.3
SCALING_FACTOR = 0.03 # m (based on fine tuning)


def getTargetPosition(target_pos_lbl, stage):
    """
    Returns the position that the stage would go to. It might return None in case the target
        position has not been determined. 
    target_pos_lbl (int): a label representing a position (SEM_IMAGING, FM_IMAGING, GRID_1 or GRID_2)
    stage (Actuator): a stage component 
    returns (dict->float): the end position of the stage
    """
    stage_md = stage.getMetadata()
    current_position = getCurrentPositionLabel(stage.position.value, stage)
    if target_pos_lbl == LOADING:
        end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
    elif current_position in [LOADING, SEM_IMAGING]: 
        if target_pos_lbl in [SEM_IMAGING, GRID_1]:
            # if at loading, and sem is pressed, choose grid1 by default
            sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][meteor_labels[GRID_1]]
            sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
            end_pos = sem_grid1_pos
        elif target_pos_lbl == GRID_2:
            sem_grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][meteor_labels[GRID_2]]
            sem_grid2_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
            end_pos = sem_grid2_pos
        elif target_pos_lbl == FM_IMAGING:
            if current_position == LOADING:
                # if at loading and fm is pressed, choose grid1 by default
                sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][meteor_labels[GRID_1]]
                fm_target_pos = transformFromSEMToMeteor(sem_grid1_pos, stage)
            elif current_position == SEM_IMAGING:
                fm_target_pos = transformFromSEMToMeteor(stage.position.value, stage)
            end_pos = fm_target_pos
        else:
            raise ValueError("Unkown target position")
    elif current_position == FM_IMAGING:
        if target_pos_lbl == GRID_1:
            sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][meteor_labels[GRID_1]]
            end_pos = transformFromSEMToMeteor(sem_grid1_pos, stage)
        elif target_pos_lbl == GRID_2:
            sem_grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][meteor_labels[GRID_2]]
            end_pos = transformFromSEMToMeteor(sem_grid2_pos, stage)
        elif target_pos_lbl == SEM_IMAGING:
            end_pos = transformFromMeteorToSEM(stage.position.value, stage)                    
        else:
            raise ValueError("Unkown target position")
    else:
        raise ValueError("Unkown target position")
    return end_pos


def getCurrentGridLabel(current_pos, stage):
    """
    Detects which grid on the sample shuttle of meteor being viewed
    current_pos (dict str->float): position of the stage
    stage (Actuator): the stage component  
    return (GRID_1 or GRID_2): the guessed grid. If current position is not SEM
        or FM, None would be returned.
    """
    current_pos_label = getCurrentPositionLabel(current_pos, stage)
    stage_md = stage.getMetadata()
    grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][meteor_labels[GRID_1]]
    grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][meteor_labels[GRID_2]]
    if current_pos_label == SEM_IMAGING:
        distance_to_grid1 = _getDistance(current_pos, grid1_pos)
        distance_to_grid2 = _getDistance(current_pos, grid2_pos)
        return GRID_2 if distance_to_grid1 > distance_to_grid2 else GRID_1 
    elif current_pos_label == FM_IMAGING:
        distance_to_grid1 = _getDistance(current_pos, transformFromSEMToMeteor(grid1_pos, stage))
        distance_to_grid2 = _getDistance(current_pos, transformFromSEMToMeteor(grid2_pos, stage))
        return GRID_1 if distance_to_grid2 > distance_to_grid1 else GRID_2
    else: 
        logging.warning("Cannot guess between grid 1 and grid2 in %s position" % meteor_labels[current_pos_label])
        return None


def _getCurrentEnzelPositionLabel(current_pos, stage):
    """
    Detects the current stage position of enzel. 
    current_pos (dict str->float): position of the stage
    stage (Actuator): the stage component 
    returns a label UNKNOWN, COATING, IMAGING, MILLING, LOADING or LOADING_PATH
    """
    stage_md = stage.getMetadata()
    stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
    stage_active = stage_md[model.MD_FAV_POS_ACTIVE]
    stage_active_range = stage_md[model.MD_POS_ACTIVE_RANGE]
    stage_coating = stage_md[model.MD_FAV_POS_COATING]
    stage_alignment = stage_md[model.MD_FAV_POS_ALIGN]
    stage_sem_imaging = stage_md[model.MD_FAV_POS_SEM_IMAGING]
    # If stage is not referenced, set position as unknown (to only allow loading position)
    if not all(stage.referenced.value.values()):
        return UNKNOWN
    # If stage is not referenced, set position as unknown (to only allow loading position)
    # Check the stage is near the coating position
    if _isNearPosition(current_pos, stage_coating, stage.axes):
        return COATING
    # Check the stage is near the alignment position
    if _isNearPosition(current_pos, stage_alignment, stage.axes):
        return ALIGNMENT
    # Check the stage X,Y,Z are within the active range and on the tilted plane -> imaging position
    if _isInRange(
        current_pos, stage_active_range, {'x', 'y', 'z'}
    ):
        if _isNearPosition(current_pos, {'rx': stage_active['rx']}, {'rx'}):
            return IMAGING
        elif _isNearPosition(current_pos, {'rx': stage_sem_imaging['rx']}, {'rx'}):
            return SEM_IMAGING
    # Check the stage is near the loading position
    if _isNearPosition(current_pos, stage_deactive, stage.axes):
        return LOADING

    # TODO: refine loading path to be between any move from loading to active range?
    # Check the current position is near the line between DEACTIVE and ACTIVE
    imaging_progress = getMovementProgress(current_pos, stage_deactive, stage_active)
    if imaging_progress is not None:
        return LOADING_PATH

    # Check the current position is near the line between DEACTIVE and COATING
    coating_progress = getMovementProgress(current_pos, stage_deactive, stage_coating)
    if coating_progress is not None:
        return LOADING_PATH

    # Check the current position is near the line between DEACTIVE and COATING
    alignment_path = getMovementProgress(current_pos, stage_deactive, stage_alignment)
    if alignment_path is not None:
        return LOADING_PATH
    # None of the above -> unknown position
    return UNKNOWN


def _getCurrentMeteorPositionLabel(current_pos, stage):
    """
    Detects the current stage position of meteor 
    current_pos (dict str->float): position of the stage 
    stage (Actuator): the stage component 
    returns a label LOADING, SEM_IMAGIN, FM_IMAGING or UNKNOWN
    """
    # meta data of meteor stage positions 
    stage_md = stage.getMetadata()
    stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
    stage_fm_imaging_rng = stage_md[model.MD_FM_IMAGING_RANGE] 
    stage_sem_imaging_rng = stage_md[model.MD_SEM_IMAGING_RANGE] 
    # Check the stage is near the loading position
    if _isNearPosition(current_pos, stage_deactive, stage.axes):
        return LOADING
    if _isInRange(current_pos, stage_fm_imaging_rng, {'x', 'y', 'z'}):
        return FM_IMAGING
    if _isInRange(current_pos, stage_sem_imaging_rng, {'x', 'y', 'z'}):
        return SEM_IMAGING
    # None of the above -> unknown position
    return UNKNOWN


def getCurrentPositionLabel(current_pos, stage):
    """
    Determine where lies the current stage position
    :param current_pos: (dict str->float) Current position of the stage
    :param stage: (Actuator) the stage component 
    :return: (int) a value representing stage position from the constants LOADING, IMAGING, TILTED, COATING..etc
    """
    role = model.getMicroscope().role
    if role == 'enzel':
        return _getCurrentEnzelPositionLabel(current_pos, stage)
    elif role == 'meteor':
        return _getCurrentMeteorPositionLabel(current_pos, stage)
    else:
        raise LookupError("Unhandled microscope role %s" % role)

def getCurrentAlignerPositionLabel(current_pos, align):
    """
    Determine the current aligner position
    :param current_pos: (dict str->float) Current position of the stage
    :param align: Lens stage (aligner) that's being controlled
    :return: (int) a value representing stage position from the constants LOADING, IMAGING, TILTED, COATING..etc
    """
    align_md = align.getMetadata()
    align_deactive = align_md[model.MD_FAV_POS_DEACTIVE]
    align_active = align_md[model.MD_FAV_POS_ACTIVE]
    align_alignment = align_md[model.MD_FAV_POS_ALIGN]
    # If align is not referenced, set position as unknown (to only allow loading position)
    if not all(align.referenced.value.values()):
        return UNKNOWN

    if _isNearPosition(current_pos, align_alignment, align.axes):
        return ALIGNMENT

    if _isNearPosition(current_pos, align_active, align.axes):
        return IMAGING

    # Check the stage is near the loading position
    if _isNearPosition(current_pos, align_deactive, align.axes):
        return LOADING

    # Check the current position is near the line between DEACTIVE and ACTIVE
    imaging_progress = getMovementProgress(current_pos, align_deactive, align_active)
    if imaging_progress is not None:
        return LOADING_PATH

    # Check the current position is near the line between DEACTIVE and ALIGNMENT
    alignment_path = getMovementProgress(current_pos, align_deactive, align_alignment)
    if alignment_path is not None:
        return LOADING_PATH
    # None of the above -> unknown position
    return UNKNOWN


# TODO for now this function is hardcoded to work only for rz and rx. Handle
# also the ry axis to make the function generic
def _getDistance(start, end):
    """
    Calculate the error/difference between two 3D postures with x, y, z, rx, rz axes
        or a subset of these axes. If there are no common axes between the two passed
        postures, an error would be raised. The scaling factor of the rotation error is in meter.
    start, end (dict -> float): a 3D posture
    return (float >= 0): the difference between two 3D postures.
    """
    axes = start.keys() & end.keys()
    lin_axes = axes & {'x', 'y', 'z'}  # only the axes found on both points
    rot_axes = axes & {'rx', 'rz'}  # only the axes found on both points
    if not lin_axes and not rot_axes:
        raise ValueError("No common axes found between the two postures")
    lin_error = rot_error = 0
    # for the linear error
    if lin_axes:
        sp = numpy.array([start[a] for a in sorted(lin_axes)])
        ep = numpy.array([end[a] for a in sorted(lin_axes)])
        lin_error = scipy.spatial.distance.euclidean(ep, sp)
    # for the rotation error
    if rot_axes:
        Rx_start = Rx_end = Rz_start = Rz_end = numpy.eye(3)
        for a in rot_axes:
            if a == "rx":
                # find the elemental rotation around x axis
                Rx_start = getRotationMatrix(a, start["rx"])
                Rx_end = getRotationMatrix(a, end["rx"])
            elif a == "rz":
                # find the elemental rotation around z axis
                Rz_end = getRotationMatrix(a, end["rz"])
                Rz_start = getRotationMatrix(a, start["rz"])
        # find the total rotations
        R_start = numpy.matmul(Rz_start, Rx_start)
        R_end = numpy.matmul(Rz_end, Rx_end)
        # find the rotation error matrix
        R_diff = numpy.matmul(numpy.transpose(R_start), R_end)
        # map to scalar error
        rot_error = SCALING_FACTOR * abs(numpy.trace(numpy.eye(3) - R_diff))
    return lin_error + rot_error
    

def getRotationMatrix(axis, angle):
    """
    Computes the rotation matrix of the given angle around the given axis. 
    axis (str): the axis around which the rotation matrix is to be calculated. The axis can be 'rx', 'ry' or 'rz'.
    angle (float): the angle of rotation. The angle must be in radians.
    return (numpy.ndarray): the rotation matrix. It is 3x3 matrix of floats.
    """
    if axis == "rx":
        Rx = numpy.array([[1, 0, 0], [0, numpy.cos(angle), -numpy.sin(angle)], [0, numpy.sin(angle), numpy.cos(angle)]])
        return Rx
    elif axis == "ry":
        Ry = numpy.array([[numpy.cos(angle), 0, numpy.sin(angle)], [0, 1, 0], [-numpy.sin(angle), 0, numpy.cos(angle)]])
        return Ry
    elif axis == "rz":
        Rz = numpy.array([[numpy.cos(angle), -numpy.sin(angle), 0], [numpy.sin(angle), numpy.cos(angle), 0], [0, 0, 1]])
        return Rz
    else:
        raise ValueError(f"Unknown axis name {axis}")


def getMovementProgress(current_pos, start_pos, end_pos):
    """
    Compute the position on the path between start and end positions of a stage movement (such as LOADING to IMAGING)
    If it’s too far from the line between the start and end positions, then it’s considered out of the path.
    :param current_pos: (dict str->float) Current position of the stage
    :param start_pos: (dict str->float) A position to start the movement from
    :param end_pos: (dict str->float) A position to end the movement to
    :return:(0<=float<=1, or None) Ratio of the progress, None if it's far away from of the path
    """
    # Get distance for current point in respect to start and end
    from_start = _getDistance(start_pos, current_pos)
    to_end = _getDistance(current_pos, end_pos)
    total_length = _getDistance(start_pos, end_pos)
    if total_length == 0:  # same value
        return 1
    # Check if current position is on the line from start to end position
    # That would happen if start_to_current +  current_to_start = total_distance from start to end
    if util.almost_equal((from_start + to_end), total_length, rtol=RTOL_PROGRESS):
        return min(from_start / total_length, 1.0)  # Clip in case from_start slightly > total_length
    else:
        return None


def _isInRange(current_pos, active_range, axes):
    """
    Check if current position is within active range
    :param current_pos: (dict) current position dict (axis -> value)
    :param active_range: (dict) imaging  active range (axis name → (min,max))
    :param axes: (set) axes to check values
    :return: True if position in active range, False otherwise
    """
    if not axes:
        logging.warning("Empty axes given.")
        return False
    for axis in axes:
        pos = current_pos[axis]
        axis_active_range = [r for r in active_range[axis]]
        # Add 1% margin for hardware slight errors
        margin = (axis_active_range[1] - axis_active_range[0]) * 0.01
        if not((axis_active_range[0] - margin) <= pos <= (axis_active_range[1] + margin)):
            return False
    return True


def _isNearPosition(current_pos, target_position, axes):
    """
    Check whether given axis is near stage target position
    :param current_pos: (dict) current position dict (axis -> value)
    :param target_position: (dict) target position dict (axis -> value)
    :param axes: (set) axes to compare values
    :return: True if the axis is near position, False otherwise
    :raises ValueError if axis is unknown
    """
    if not axes:
        logging.warning("Empty axes given.")
        return False
    for axis in axes:
        current_value = current_pos[axis]
        target_value = target_position[axis]
        if axis in {'x', 'y', 'z'}:
            is_near = abs(target_value - current_value) < ATOL_LINEAR_POS
        elif axis in {'rx', 'rz'}:
            is_near = util.rot_almost_equal(current_value, target_value, atol=ATOL_ROTATION_POS)
        else:
            raise ValueError("Unknown axis value %s." % axis)
        if not is_near:
            return False
    return True

def cryoSwitchAlignPosition(target):
    """
    Provide the ability to switch between loading, imaging and alignment position, without bumping into anything.
    :param target: (int) target position either one of the constants LOADING, IMAGING or ALIGNMENT
    :return (CancellableFuture -> None): cancellable future of the move to observe the progress, and control the raise
    ValueError exception
    """
    # Get the aligner from backend components
    align = model.getComponent(role='align')

    f = model.CancellableFuture()
    f.task_canceller = _cancelCryoMoveSample
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f._running_subf = model.InstantaneousFuture()
    # Run in separate thread
    executeAsyncTask(f, _doCryoSwitchAlignPosition, args=(f, align, target))
    return f

def _doCryoSwitchAlignPosition(future, align, target):
    """
    Do the actual switching procedure for the Cryo lens stage (align) between loading, imaging and alignment positions
    :param future: cancellable future of the move
    :param align: wrapper for optical objective (lens aligner)
    :param target: target position either one of the constants LOADING, IMAGING and ALIGNMENT
    """
    try:
        align_md = align.getMetadata()
        target_pos = {LOADING: align_md[model.MD_FAV_POS_DEACTIVE],
                      IMAGING: align_md[model.MD_FAV_POS_ACTIVE],
                      ALIGNMENT: align_md[model.MD_FAV_POS_ALIGN]
                      }
        align_referenced = all(align.referenced.value.values())
        # Fail early when required axes are not found on the positions metadata
        required_axes = {'x', 'y', 'z'}
        for align_position in target_pos.values():
            if not required_axes.issubset(align_position.keys()):
                raise ValueError("Aligner %s metadata does not have all required axes %s." % (list(align_md.keys())[list(align_md.values()).index(align_position)], required_axes))
        current_pos = align.position.value
        # To hold the ordered sub moves list
        sub_moves = []
        # Create axis->pos dict from target position given smaller number of axes
        filter_dict = lambda keys, d: {key: d[key] for key in keys}

        current_label = getCurrentAlignerPositionLabel(current_pos, align)
        if target == LOADING:
            if current_label is UNKNOWN:
                logging.warning("Parking aligner while current position is unknown.")

            # reference align if not already referenced
            if not align_referenced:
                run_reference(future, align)

            # Add the sub moves to perform the loading move
            sub_moves.append((align, filter_dict({'y'}, target_pos[LOADING])))
            sub_moves.append((align, filter_dict({'x', 'z'}, target_pos[LOADING])))

        elif target in (ALIGNMENT, IMAGING):
            if current_label is UNKNOWN:
                raise ValueError("Unable to move aligner to {} while current position is unknown.".format(
                    target_pos_str.get(target, lambda: "unknown")))

            # Add the sub moves to perform the imaging/alignment move
            sub_moves.append((align, filter_dict({'x', 'z'}, target_pos[target])))
            sub_moves.append((align, filter_dict({'y'}, target_pos[target])))
        else:
            raise ValueError("Unknown target value %s." % target)

        for component, sub_move in sub_moves:
            logging.info("Starting aligner movement from {} -> {}...".format(target_pos_str[current_label], target_pos_str[target]))
            run_sub_move(future, component, sub_move)
    except CancelledError:
        logging.info("_doCryoSwitchAlignPosition cancelled.")
    except Exception as exp:
        logging.exception("Failure to move to {} position.".format(target_pos_str.get(target, lambda: "unknown")))
        raise
    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


def cryoSwitchSamplePosition(target):
    """
    Provide the ability to switch between loading, imaging and coating position, without bumping into anything.
    :param target: (int) target position either one of the constants LOADING, IMAGING, COATING AND ALIGNMENT
    :return (CancellableFuture -> None): cancellable future of the move to observe the progress, and control raising the
    ValueError exception
    """
    f = model.CancellableFuture()
    f.task_canceller = _cancelCryoMoveSample
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f._running_subf = model.InstantaneousFuture()
    # Run in separate thread
    executeAsyncTask(f, _doCryoSwitchSamplePosition, args=(f, target))
    return f


def _doCryoSwitchSamplePosition(future, target):
    """
    Do the actual switching procedure for the Cryo sample stage between loading, imaging and coating positions
    :param future: cancellable future of the move
    :param stage: sample stage that's being controlled
    :param align: focus for optical lens
    :param target: target position either one of the constants LOADING, IMAGING, ALIGNMENT and COATING
    """
    role = model.getMicroscope().role
    if role == "enzel":
        try:
            # get the stage and aligner objects
            stage = model.getComponent(role='stage')
            align = model.getComponent(role='align')
            stage_md = stage.getMetadata()
            align_md = align.getMetadata()
            target_pos = {LOADING: stage_md[model.MD_FAV_POS_DEACTIVE],
                        IMAGING: stage_md[model.MD_FAV_POS_ACTIVE],
                        COATING: stage_md[model.MD_FAV_POS_COATING],
                        ALIGNMENT: stage_md[model.MD_FAV_POS_ALIGN],
                        SEM_IMAGING: stage_md[model.MD_FAV_POS_SEM_IMAGING]
                        }
            align_deactive = align_md[model.MD_FAV_POS_DEACTIVE]
            stage_referenced = all(stage.referenced.value.values())
            # Fail early when required axes are not found on the positions metadata
            required_axes = {'x', 'y', 'z', 'rx', 'rz'}
            for stage_position in target_pos.values():
                if not required_axes.issubset(stage_position.keys()):
                    raise ValueError("Stage %s metadata does not have all required axes %s." % (list(stage_md.keys())[list(stage_md.values()).index(stage_position)], required_axes))
            current_pos = stage.position.value
            # To hold the ordered sub moves list
            sub_moves = []
            # To hold the sub moves to run if the ordered sub moves failed
            fallback_submoves = []
            # Create axis->pos dict from target position given smaller number of axes
            filter_dict = lambda keys, d: {key: d[key] for key in keys}

            current_label = getCurrentPositionLabel(current_pos, stage)
            if target == LOADING:
                if current_label is UNKNOWN and stage_referenced:
                    logging.warning("Moving stage to loading while current position is unknown.")
                if abs(target_pos[LOADING]['rx']) > ATOL_ROTATION_POS:
                    raise ValueError(
                        "Absolute value of rx for FAV_POS_DEACTIVE is greater than {}".format(ATOL_ROTATION_POS))

                # Check if stage is not referenced:
                # park aligner (move it to loading position) then reference the stage
                if not stage_referenced:
                    cryoSwitchAlignPosition(LOADING).result()
                    run_reference(future, stage)

                fallback_submoves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos[LOADING])))
                fallback_submoves.append((stage, filter_dict({'rx', 'rz'}, target_pos[LOADING])))
                # Add the sub moves to perform the loading move
                sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos[LOADING])))
                if current_label is UNKNOWN and not stage_referenced:
                    # After referencing the stage could move near the maximum axes range,
                    # and moving single axes may result in an invalid/reachable position error,
                    # so all axes will moved together for this special case.
                    sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos[LOADING])))
                else:
                    sub_moves.append((stage, filter_dict({'x', 'y'}, target_pos[LOADING])))
                    sub_moves.append((stage, filter_dict({'z'}, target_pos[LOADING])))

            elif target in (ALIGNMENT, IMAGING, SEM_IMAGING, COATING):
                if current_label is LOADING:
                    # Automatically run the referencing procedure as part of the
                    # first step of the movement loading → imaging/coating position
                    run_reference(future, stage)
                elif current_label is UNKNOWN:
                    raise ValueError("Unable to move to {} while current position is unknown.".format(
                        target_pos_str.get(target, lambda: "unknown")))

                fallback_submoves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos[target])))
                fallback_submoves.append((stage, filter_dict({'rx', 'rz'}, target_pos[target])))
                # Add the sub moves to perform the imaging/coating move
                if current_label == LOADING:
                    # As moving from loading position requires re-referencing the stage, move all axes together to
                    # prevent invalid/reachable position error
                    sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos[target])))
                else:
                    sub_moves.append((stage, filter_dict({'z'}, target_pos[target])))
                    sub_moves.append((stage, filter_dict({'x', 'y'}, target_pos[target])))
                sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos[target])))
            else:
                raise ValueError("Unknown target value %s." % target)

            try:
                logging.info("Starting sample movement from {} -> {}...".format(target_pos_str[current_label], target_pos_str[target]))
                # Park aligner to safe position before any movement
                if not _isNearPosition(align.position.value, align_deactive, align.axes):
                    cryoSwitchAlignPosition(LOADING).result()
                for component, sub_move in sub_moves:
                    run_sub_move(future, component, sub_move)
                if target in (IMAGING, ALIGNMENT):
                    cryoSwitchAlignPosition(target).result()
            except IndexError:
                # In case the required movement is invalid/unreachable with the smaract 5dof stage
                # Move all linear axes first then rotational ones using the fallback_submoves
                logging.debug("This move {} is unreachable, trying to move all axes at once...".format(sub_move))
                for component, sub_move in fallback_submoves:
                    run_sub_move(future, component, sub_move)

        except CancelledError:
            logging.info("_doCryoSwitchSamplePosition cancelled.")
        except Exception as exp:
            logging.exception("Failure to move to {} position.".format(target_pos_str.get(target, lambda: "unknown")))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED

    elif role == "meteor":
        try:
            # get the focus and stage components 
            focus = model.getComponent(role='focus')
            stage = model.getComponent(role='stage-bare')
            # get the meta data 
            focus_md = focus.getMetadata()
            focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
            focus_active = focus_md[model.MD_FAV_POS_ACTIVE]
            # To hold the ordered sub moves list
            sub_moves = []
            # Create axis->pos dict from target position given smaller number of axes
            filter_dict = lambda keys, d: {key: d[key] for key in keys}
            # get the current label 
            current_pos_label = getCurrentPositionLabel(stage.position.value, stage)
            # get the set point position
            target_pos = getTargetPosition(target, stage)
            # determine the order of moves for the stage and focuser
            if target == LOADING:
                # park the focuser for safety 
                if not _isNearPosition(focus.position.value, focus_deactive, focus.axes):
                    sub_moves.append((focus, focus_deactive))
                sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos)))
            elif current_pos_label in [LOADING, SEM_IMAGING]:
                if target in [SEM_IMAGING, GRID_1]:  
                    # park the focuser for safety 
                    if not _isNearPosition(focus.position.value, focus_deactive, focus.axes):
                        sub_moves.append((focus, focus_deactive))
                    sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos)))
                elif target == FM_IMAGING:
                    # park focus for safety 
                    if not _isNearPosition(focus.position.value, focus_deactive, focus.axes):
                        sub_moves.append((focus, focus_deactive))
                    if current_pos_label == LOADING:
                        sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos)))
                        sub_moves.append((focus, focus_active))
                    elif current_pos_label == SEM_IMAGING:
                        sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos)))
                        sub_moves.append((focus, focus_active))
                elif target == GRID_2:
                    sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos)))
            elif current_pos_label == FM_IMAGING:
                if target in (GRID_1, GRID_2):
                    sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                elif target == SEM_IMAGING:
                    # park focus for safety 
                    if not _isNearPosition(focus.position.value, focus_deactive, focus.axes):
                        sub_moves.append((focus, focus_deactive))
                    sub_moves.append((stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx', 'rz'}, target_pos)))
            else:
                return
            # run the moves 
            try:
                for component, sub_move in sub_moves:
                    logging.info("Moving from position {} to position {}.".format(meteor_labels[current_pos_label], meteor_labels[target]))
                    run_sub_move(future, component, sub_move)
            except Exception:
                raise
        except CancelledError:
            logging.info("The movement was cancelled.")
        except Exception as exp:
            logging.exception("Failure to move to {} position.".format(meteor_labels.get(target)))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED

# Note: this transformation consists of translation of along x and y 
# axes, and 7 degrees rotation around rx, and 180 degree rotation around rz.
# The rotation angles are constant existing in "FM_POS_ACTIVE" metadata,
# but the translation are calculated based on the current position and some
# correction/shifting parameters existing in metadata "FM_POS_ACTIVE". 
# This correction parameters can change every session. They are calibrated
# at the beginning of each run.
def transformFromSEMToMeteor(pos, stage):
    """
    Transforms the current stage position from the SEM imaging area to the 
        meteor/FM imaging area.
    pos (dict str->float): the current stage position. The position has to have x, y, rz, and rx axes,
        otherwise error would be raised.
    stage (Actuator): the stage component. The stage has to have metadata "MD_POS_COR" and "MD_FAV_FM_POS_ACTIVE"
    return (dict str->float): the transformed position. It returns the updated axes x, y, rx, rz. The axis z is same as the input.
    """
    if not {"x", "y", "rz", "rx"}.issubset(stage.axes):
        raise KeyError("The stage misses 'x', 'y', 'rx' or 'rz' axes")
    stage_md = stage.getMetadata()
    transformed_pos = pos.copy()
    pos_cor = stage_md[model.MD_POS_COR]
    fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]
    transformed_pos["x"] = 2 * pos_cor["x"] - pos["x"]
    transformed_pos['y'] = 2 * pos_cor["y"] - pos["y"]
    transformed_pos.update(fm_pos_active)
    return transformed_pos


# Note: this transformation also consists of translation and rotation. 
# The translation is along x and y axes. They are calculated based on
# the current position and correction parameters which are calibrated every session.
# The rotation angles are 180 degree around rz axis, and a rotation angle
# around rx axis which should also be calibrated at the beginning of the run. 
# The rx angle is actually the same as the milling angle. 
def transformFromMeteorToSEM(pos, stage):
    """
    Transforms the current stage position from the meteor/FM imaging area
        to the SEM imaging area.
    pos (dict str->float): the current stage position
    stage (Actuator): the stage component
    returns (dict str->float): the transformed stage position. 
    """
    if not {"x", "y", "rx", "rz"}.issubset(stage.axes):
        raise KeyError("The stage misses 'x', 'y', 'rx' or 'rz' axes")
    stage_md = stage.getMetadata()
    transformed_pos = pos.copy()
    pos_cor = stage_md[model.MD_POS_COR]
    sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
    transformed_pos["x"] = 2*pos_cor["x"]-pos["x"]
    transformed_pos['y'] = 2*pos_cor["y"]-pos["y"]
    transformed_pos.update(sem_pos_active)
    return transformed_pos


def cryoTiltSample(rx, rz=0):
    """
    Provide the ability to switch between imaging and tilted position, withing bumping into anything.
    Imaging position is considered when rx and rz are equal 0, otherwise it's considered tilting
    :param rx: (float) rotation movement in x axis
    :param rz: (float) rotation movement in z axis
    :return (CancellableFuture -> None): cancellable future of the move to observe the progress, and control raising the ValueError exception
    """
    # Get the stage and align components from the backend components
    stage = model.getComponent(role='stage')
    align = model.getComponent(role='align')

    f = model.CancellableFuture()
    f.task_canceller = _cancelCryoMoveSample
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f._running_subf = model.InstantaneousFuture()
    # Run in separate thread
    executeAsyncTask(f, _doCryoTiltSample, args=(f, stage, align, rx, rz,))
    return f


def _doCryoTiltSample(future, stage, align, rx, rz):
    """
    Do the actual tilting procedure for the Cryo sample stage
    :param future: cancellable future of the move
    :param stage: sample stage that's being controlled
    :param align: aligner for optical lens
    :param rx: (float) rotation movement in x axis
    :param rz: (float) rotation movement in z axis
    """
    try:
        stage_md = stage.getMetadata()
        align_md = align.getMetadata()
        stage_active_range = stage_md[model.MD_POS_ACTIVE_RANGE]
        align_deactive = align_md[model.MD_FAV_POS_DEACTIVE]
        current_pos = stage.position.value

        # Check that the stage X,Y,Z are within the limits
        if not _isInRange(current_pos, stage_active_range, {'x', 'y', 'z'}):
            raise ValueError("Current position is out of active range.")
        # To hold the ordered sub moves list to perform the tilting/imaging move
        sub_moves = []
        # Park aligner to safe position before any movement
        if not _isNearPosition(align.position.value, align_deactive, align.axes):
            cryoSwitchAlignPosition(LOADING).result()

        sub_moves.append((stage, {'rz': rz}))
        sub_moves.append((stage, {'rx': rx}))

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
    Canceller of _doCryoTiltSample and _doCryoSwitchSamplePosition tasks
    """
    logging.debug("Cancelling CryoMoveSample...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future._running_subf.cancel()
        logging.debug("CryoMoveSample cancellation requested.")

    return True

def run_reference(future, component):
    """
    Perform the stage reference procedure
    :param future: cancellable future of the reference procedure
    :param component: Either the stage or the align component
    :raises CancelledError: if the reference is cancelled
    """
    try:
        with future._task_lock:
            if future._task_state == CANCELLED:
                logging.info("Reference procedure is cancelled.")
                raise CancelledError()
            logging.debug("Performing stage referencing.")
            future._running_subf = component.reference(set(component.axes.keys()))
        future._running_subf.result()
    except Exception as error:
        logging.exception(error)
    if future._task_state == CANCELLED:
        logging.info("Reference procedure is cancelled.")
        raise CancelledError()


def run_sub_move(future, component, sub_move):
    """
    Perform the sub moveAbs using the given component and axis->pos dict
    :param future: cancellable future of the whole move
    :param component: Either the stage or the align component
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
