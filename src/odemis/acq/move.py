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

import copy
import logging
import math
import threading
from abc import abstractmethod
from concurrent.futures import CancelledError
from concurrent.futures._base import CANCELLED, RUNNING, FINISHED
from typing import Dict, Union

import numpy
import scipy

from odemis import model, util
from odemis.util import executeAsyncTask
from odemis.util.driver import ATOL_ROTATION_POS, isNearPosition, isInRange

MAX_SUBMOVE_DURATION = 90  # s

UNKNOWN, LOADING, IMAGING, ALIGNMENT, COATING, LOADING_PATH, MILLING, SEM_IMAGING, \
    FM_IMAGING, GRID_1, GRID_2, THREE_BEAMS = -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
POSITION_NAMES = {
    UNKNOWN: "UNKNOWN",
    LOADING: "LOADING",
    IMAGING: "IMAGING",
    ALIGNMENT: "ALIGNMENT",
    COATING: "COATING",
    LOADING_PATH: "LOADING PATH",
    MILLING: "MILLING",
    SEM_IMAGING: "SEM IMAGING",
    FM_IMAGING: "FM IMAGING",
    GRID_1: "GRID 1",
    GRID_2: "GRID 2",
    THREE_BEAMS: "THREE BEAMS"
}

RTOL_PROGRESS = 0.3
# Compensation factor for a rotational move to take the same amount of time as a linear move
ROT_DIST_SCALING_FACTOR = 0.06  # m/rad, 1° ~ 1mm
SAFETY_MARGIN_5DOF = 100e-6  # m
SAFETY_MARGIN_3DOF = 200e-6  # m

# Tolerance for the difference between the current position and the target position
# these should only be used for TFS1MeteorPostureManager _transformFromSEMToMeteor / _transformFromMeteorToSEM
ATOL_ROTATION_TRANSFORM = 0.04   # rad ~2.5 deg
ATOL_LINEAR_TRANSFORM = 5e-6    # 5 um

class MicroscopePostureManager:
    def __new__(cls, microscope):
        role = microscope.role
        if role == "enzel":
            return super().__new__(EnzelPostureManager)
        elif role == "meteor":
            stage = model.getComponent(role='stage-bare')
            stage_md = stage.getMetadata()
            md_calib = stage_md.get(model.MD_CALIB, None)
            # Check the version in MD_CALIB, defaults to tfs_1
            stage_version = md_calib.get("version", "tfs_1") if md_calib else "tfs_1"
            if stage_version == "zeiss_1":
                return super().__new__(MeteorZeiss1PostureManager)
            elif stage_version == "tfs_1":
                return super().__new__(MeteorTFS1PostureManager)
            elif stage_version == "tescan_1":
                return super().__new__(MeteorTescan1PostureManager)
            else:
                raise ValueError(f"Stage version {stage_version} is not supported")
        elif role == "mimas":
            return super().__new__(MimasPostureManager)
        else:
            ValueError(f"Microscope {role} is not supported")

    @abstractmethod
    def __init__(self, microscope):
        pass

    @abstractmethod
    def getCurrentPostureLabel(self, pos: Dict[str, float] = None) -> int:
        """
        Determine where lies the current stage position
        :param pos: (dict str->float) the stage position in which the label needs to be found. If None, it uses the
         current position of the stage.
        :return (int): a value representing stage position from the constants LOADING, THREE_BEAMS, COATING, etc.
        """
        pass

    def cryoSwitchSamplePosition(self, target: int):
        """
        Provide the ability to switch between different positions, without bumping into anything.
        :param target: (int) target position either one of the constants: LOADING, IMAGING,
           ALIGNMENT, COATING, LOADING_PATH, MILLING, SEM_IMAGING, FM_IMAGING.
        :return (CancellableFuture -> None): cancellable future of the move to observe the progress, and control raising the
        ValueError exception
        """
        f = model.CancellableFuture()
        f.task_canceller = self._cancelCryoMoveSample
        f._task_state = RUNNING
        f._task_lock = threading.Lock()
        f._running_subf = model.InstantaneousFuture()
        # Run in separate thread
        executeAsyncTask(f, self._doCryoSwitchSamplePosition, args=(f, target))
        return f

    @abstractmethod
    def _doCryoSwitchSamplePosition(self, future, target_pos: int):
        """
        Do the actual switching procedure for cryoSwitchSamplePosition
        :param future: cancellable future of the move
        :param target: (int) target position either one of the constants: LOADING, IMAGING,
           ALIGNMENT, COATING, LOADING_PATH, MILLING, SEM_IMAGING, FM_IMAGING.
        """
        pass

    def getMovementProgress(self, current_pos: Dict[str, float], start_pos: Dict[str, float],
                            end_pos: Dict[str, float]) -> Union[float, None]:
        """
        Compute the position on the path between start and end positions of a stage movement (such as LOADING to IMAGING)
        If it’s too far from the line between the start and end positions, then it’s considered out of the path.
        :param current_pos: (dict str->float) Current position of the stage
        :param start_pos: (dict str->float) A position to start the movement from
        :param end_pos: (dict str->float) A position to end the movement to
        :return: (0<=float<=1, or None) Ratio of the progress, None if it's too far away from of the path
        """
        # Get distance for current point in respect to start and end
        from_start = self._getDistance(start_pos, current_pos)
        to_end = self._getDistance(current_pos, end_pos)
        total_length = self._getDistance(start_pos, end_pos)
        if total_length == 0:  # same value
            return 1
        # Check if current position is on the line from start to end position
        # That would happen if start_to_current +  current_to_start = total_distance from start to end
        if util.almost_equal((from_start + to_end), total_length, rtol=RTOL_PROGRESS):
            return min(from_start / total_length, 1.0)  # Clip in case from_start slightly > total_length
        else:
            return None

    def _getDistance(self, start: dict, end: dict) -> float:
        """
        Calculate the difference between two 3D postures with x, y, z, m, rx, ry, rz, rm axes
        or a subset of these axes. If there are no common axes between the two passed
        postures, an error would be raised. The scaling factor of the rotation error is in meter.
        start, end (dict -> float): a 3D posture
        return (float >= 0): the difference between two 3D postures.
        """
        axes = start.keys() & end.keys()
        lin_axes = axes & {'x', 'y', 'z', 'm'}  # only the axes found on both points
        rot_axes = axes & {'rx', 'ry', 'rz', 'rm'}  # only the axes found on both points
        if not lin_axes and not rot_axes:
            raise ValueError("No common axes found between the two postures")

        lin_error = 0
        # for the linear error
        if lin_axes:
            sp = numpy.array([start[a] for a in sorted(lin_axes)])
            ep = numpy.array([end[a] for a in sorted(lin_axes)])
            lin_error = scipy.spatial.distance.euclidean(ep, sp)

        # for the rotation error: just the sum of all rotations
        rot_dist = sum(abs(util.rot_shortest_move(start[a], end[a])) for a in rot_axes)
        # Convert to a value which has the same order of magnitude as linear distances (in a microscope)
        rot_error = ROT_DIST_SCALING_FACTOR * rot_dist

        return lin_error + rot_error

    def check_stage_metadata(self, required_keys: set):
        """
        Checks the required metadata in the stage metadata.
        :param required_keys: A set of keys that must be present in on the stage metadata. The keys begin with MD_*.
        :raises ValueError: if the metadata does not have all required keys.
        """
        stage_md = self.stage.getMetadata()

        # Check for required keys
        if not required_keys.issubset(stage_md.keys()):
            missing_keys = required_keys - stage_md.keys()
            raise ValueError(f"Stage metadata is missing the following required keys: {missing_keys}.")

    def _cancelCryoMoveSample(self, future):
        """
        Canceller of _doCryoSwitchAlignPosition and _doCryoSwitchSamplePosition tasks
        """
        logging.debug("Cancelling cryo switch move...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future._running_subf.cancel()
            logging.debug("Cryo switch move cancellation requested.")

        return True

    def _run_reference(self, future, component):
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

    def _run_sub_move(self, future, component, sub_move):
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
                    logging.info("Move procedure is cancelled before moving %s -> %s", component.name, sub_move)
                    raise CancelledError()

                logging.debug("Performing sub move %s -> %s", component.name, sub_move)
                future._running_subf = component.moveAbs(sub_move)
            future._running_subf.result(timeout=MAX_SUBMOVE_DURATION)
        except TimeoutError:
            future._running_subf.cancel()
            logging.exception("Timed out while moving %s -> %s", component.name, sub_move)
            raise

        if future._task_state == CANCELLED:
            logging.info("Move procedure is cancelled after moving %s -> %s", component.name, sub_move)
            raise CancelledError()


class MeteorPostureManager(MicroscopePostureManager):
    def __init__(self, microscope):
        # Load components
        self.stage = model.getComponent(role='stage-bare')
        self.focus = model.getComponent(role='focus')
        # set linear axes and rotational axes used
        self.axes = self.stage.axes
        self.linear_axes = set(key for key in self.axes.keys() if key in {'x', 'y', 'z', 'm'})
        self.rotational_axes = set(key for key in self.axes.keys() if key in {'rx', 'ry', 'rz', 'rm'})
        # required keys that must be present in the stage metadata
        self.required_keys = {
            model.MD_FAV_POS_DEACTIVE, model.MD_FAV_SEM_POS_ACTIVE, model.MD_FAV_FM_POS_ACTIVE,
            model.MD_SAMPLE_CENTERS}

    def getCurrentPostureLabel(self, pos: Dict[str, float] = None) -> int:
        """
        Detects the current stage position of meteor
        :param pos: (dict str->float) the stage position in which the label needs to be found. If None, it uses the
         current position of the stage.
        :return: (int) a label LOADING, SEM_IMAGING, FM_IMAGING or UNKNOWN
        """
        stage_md = self.stage.getMetadata()
        stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
        stage_fm_imaging_rng = stage_md[model.MD_FM_IMAGING_RANGE]
        stage_sem_imaging_rng = stage_md[model.MD_SEM_IMAGING_RANGE]
        if pos is None:
            pos = self.stage.position.value
        # Check the stage is near the loading position
        if isNearPosition(pos, stage_deactive, self.stage.axes):
            return LOADING
        if isInRange(pos, stage_fm_imaging_rng, self.linear_axes):
            return FM_IMAGING
        if isInRange(pos, stage_sem_imaging_rng, self.linear_axes):
            return SEM_IMAGING
        # None of the above -> unknown position
        return UNKNOWN

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        target_pos_lbl (int): a label representing a position (SEM_IMAGING, FM_IMAGING, GRID_1 or GRID_2)
        :return: (dict str->float) the target position of the stage
        :raises ValueError: if the target position is not supported
        """
        pass

    def getCurrentGridLabel(self) -> int:
        """
        Detects which grid on the sample shuttle of meteor being viewed
        :return: (GRID_1 or GRID_2) the guessed grid. If current position is not SEM
            or FM, None would be returned.
        """
        pass

    def _transformFromSEMToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
            meteor/FM imaging area.
        :param pos: (dict str->float) the current stage position. The position has to have linear and rotational axes,
         otherwise error would be raised.
        :return: (dict str->float) the transformed position. It returns the updated axes.
        """
        pass

    def _transformFromMeteorToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area
            to the SEM imaging area.
        :param pos: (dict str->float) the current stage position
        :param stage: (Actuator) the stage component
        :return: (dict str->float) the transformed stage position.
        """
        pass


class MeteorTFS1PostureManager(MeteorPostureManager):
    def __init__(self, microscope):
        super().__init__(microscope)
        # Check required metadata used during switching
        self.required_keys.add(model.MD_POS_COR)
        self.check_stage_metadata(required_keys=self.required_keys)
        if not {"x", "y", "rz", "rx"}.issubset(self.stage.axes):
            raise KeyError("The stage misses 'x', 'y', 'rx' or 'rz' axes")

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        :param target_pos_lbl: (int) a label representing a position (SEM_IMAGING, FM_IMAGING, GRID_1 or GRID_2)
        :return: (dict str->float) the end position of the stage
        :raises ValueError: if the target position is not supported
        """
        stage_md = self.stage.getMetadata()
        current_position = self.getCurrentPostureLabel()
        end_pos = None

        if target_pos_lbl == LOADING:
            end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
        elif current_position in [LOADING, SEM_IMAGING]:
            if target_pos_lbl in [SEM_IMAGING, GRID_1]:
                # if at loading, and sem is pressed, choose grid1 by default
                sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
                sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
                end_pos = sem_grid1_pos
            elif target_pos_lbl == GRID_2:
                sem_grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]]
                sem_grid2_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
                end_pos = sem_grid2_pos
            elif target_pos_lbl == FM_IMAGING:
                if current_position == LOADING:
                    # if at loading and fm is pressed, choose grid1 by default
                    sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
                    fm_target_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
                elif current_position == SEM_IMAGING:
                    fm_target_pos = self._transformFromSEMToMeteor(self.stage.position.value)
                end_pos = fm_target_pos
        elif current_position == FM_IMAGING:
            if target_pos_lbl == GRID_1:
                sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
                end_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
            elif target_pos_lbl == GRID_2:
                sem_grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]]
                end_pos = self._transformFromSEMToMeteor(sem_grid2_pos)
            elif target_pos_lbl == SEM_IMAGING:
                end_pos = self._transformFromMeteorToSEM(self.stage.position.value)

        if end_pos is None:
            raise ValueError("Unknown target position {} when in {}".format(
                POSITION_NAMES.get(target_pos_lbl, target_pos_lbl),
                POSITION_NAMES.get(current_position, current_position))
            )

        return end_pos

    def getCurrentGridLabel(self) -> int:
        """
        Detects which grid on the sample shuttle of meteor being viewed
        :return: (GRID_1 or GRID_2) the guessed grid. If current position is not SEM
         or FM, None would be returned.
        """
        current_pos = self.stage.position.value
        current_pos_label = self.getCurrentPostureLabel()
        stage_md = self.stage.getMetadata()
        grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
        grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]]
        if current_pos_label == SEM_IMAGING:
            distance_to_grid1 = self._getDistance(current_pos, grid1_pos)
            distance_to_grid2 = self._getDistance(current_pos, grid2_pos)
            return GRID_2 if distance_to_grid1 > distance_to_grid2 else GRID_1
        elif current_pos_label == FM_IMAGING:
            # add rx, rz from sem fav position, as grid positions do not have rx, rz
            # rz is now required to calculate transform in _transformFromSEMToMeteor
            # TODO: add this outside the if statement, after confirming behaviour is the same @patrick
            sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE] # only rx, rz
            grid1_pos.update(sem_pos_active)   # x, y, z, rx, rz
            grid2_pos.update(sem_pos_active)   # x, y, z, rx, rz

            distance_to_grid1 = self._getDistance(current_pos, self._transformFromSEMToMeteor(grid1_pos))
            distance_to_grid2 = self._getDistance(current_pos, self._transformFromSEMToMeteor(grid2_pos))
            return GRID_1 if distance_to_grid2 > distance_to_grid1 else GRID_2
        else:
            logging.warning("Cannot guess between grid 1 and grid2 in %s position" % POSITION_NAMES[current_pos_label])
            return None

    # Note: this transformation consists of translation of along x and y
    # axes, and 7 degrees rotation around rx, and 180 degree rotation around rz.
    # The rotation angles are constant existing in "FM_POS_ACTIVE" metadata,
    # but the translation are calculated based on the current position and some
    # correction/shifting parameters existing in metadata "FM_POS_ACTIVE".
    # This correction parameters can change every session. They are calibrated
    # at the beginning of each run.
    def _transformFromSEMToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
        meteor/FM imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()
        pos_cor = stage_md[model.MD_POS_COR]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]

        # check if the stage positions have rz axes
        if not ("rz" in pos and"rz" in fm_pos_active):
            raise ValueError(f"The stage position does not have rz axis pos={pos}, fm_pos_active={fm_pos_active}")

        # whether we need to rotate around the z axis (180deg)
        has_rz = not isNearPosition(pos, fm_pos_active, {"rz"},
                                    atol_rotation=ATOL_ROTATION_TRANSFORM)

        # NOTE:
        # if we are rotating around the z axis (180deg), we need to flip the x and y axes
        # if we are not rotating around the z axis, we we only need to translate the x and y axes
        # For the rotation case: pos_cor calibration data is multipled by 2x due to historical reasons
        # it is the radius of rotation -> we need the diameter, therefore 2x
        # TODO: remove the 2x multiplication when the calibration data is updated
        if has_rz:
            transformed_pos["x"] = 2 * pos_cor[0] - pos["x"]
            transformed_pos["y"] = 2 * pos_cor[1] - pos["y"]
        else:
            transformed_pos["x"] = pos["x"] + pos_cor[0]
            transformed_pos["y"] = pos["y"] + pos_cor[1]

        transformed_pos.update(fm_pos_active)

        # check if the transformed position is within the FM imaging range
        if not isInRange(transformed_pos, stage_md[model.MD_FM_IMAGING_RANGE], {'x', 'y'}):
            # only log warning, because transforms are used to get current position too
            logging.warning(f"Transformed position {transformed_pos} is outside FM imaging range")

        return transformed_pos

    # Note: this transformation also consists of translation and rotation.
    # The translation is along x and y axes. They are calculated based on
    # the current position and correction parameters which are calibrated every session.
    # The rotation angles are 180 degree around rz axis, and a rotation angle
    # around rx axis which should also be calibrated at the beginning of the run.
    # The rx angle is actually the same as the milling angle.
    def _transformFromMeteorToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area
        to the SEM imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed stage position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()
        pos_cor = stage_md[model.MD_POS_COR]
        sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

        # check if the stage positions have rz axes
        if not ("rz" in pos and"rz" in sem_pos_active):
            raise ValueError(f"The stage position does not have rz axis. pos={pos}, sem_pos_active={sem_pos_active}")

        # whether we need to rotate around the z axis (180deg)
        has_rz = not isNearPosition(pos, sem_pos_active, {"rz"},
                                    atol_rotation=ATOL_ROTATION_TRANSFORM)

        # NOTE:
        # if we are rotating around the z axis (180deg), we need to flip the x and y axes
        # if we are not rotating around the z axis, we we only need to translate the x and y axes
        # For the rotation case: pos_cor calibration data is multipled by 2x due to historical reasons
        # it is the radius of rotation -> we need the diameter, therefore 2x
        # TODO: remove the 2x multiplication when the calibration data is updated
        if has_rz:
            transformed_pos["x"] = 2 * pos_cor[0] - pos["x"]
            transformed_pos["y"] = 2 * pos_cor[1] - pos["y"]
        else:
            transformed_pos["x"] = pos["x"] - pos_cor[0]
            transformed_pos["y"] = pos["y"] - pos_cor[1]

        transformed_pos.update(sem_pos_active)

        # check if the transformed position is within the SEM imaging range
        if not isInRange(transformed_pos, stage_md[model.MD_SEM_IMAGING_RANGE], {'x', 'y'}):
            # only log warning, because transforms are used to get current position too
            logging.warning(f"Transformed position {transformed_pos} is outside SEM imaging range")

        return transformed_pos

    def _doCryoSwitchSamplePosition(self, future, target):
        """
        Do the actual switching procedure for cryoSwitchSamplePosition
        :param future: cancellable future of the move
        :param target: (int) target position either one of the constants: LOADING, SEM_IMAGING, FM_IMAGING.
        """
        try:
            try:
                target_name = POSITION_NAMES[target]
            except KeyError:
                raise ValueError(f"Unknown target '{target}'")

            # Create axis->pos dict from target position given smaller number of axes
            filter_dict = lambda keys, d: {key: d[key] for key in keys}

            # get the meta data
            focus_md = self.focus.getMetadata()
            focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
            focus_active = focus_md[model.MD_FAV_POS_ACTIVE]
            # To hold the ordered sub moves list
            sub_moves = []  # list of tuples (component, position)

            # get the current label
            current_label = self.getCurrentPostureLabel()
            current_name = POSITION_NAMES[current_label]

            if current_label == target:
                logging.warning(f"Requested move to the same position as current: {target_name}")

            # get the set point position
            target_pos = self.getTargetPosition(target)

            # If at some "weird" position, it's quite unsafe. We consider the targets
            # LOADING and SEM_IMAGING safe to go. So if not going there, first pass
            # by SEM_IMAGING and then go to the actual requested position.
            if current_label == UNKNOWN:
                logging.warning("Moving stage while current position is unknown.")
                if target not in (LOADING, SEM_IMAGING):
                    logging.debug("Moving first to SEM_IMAGING position")
                    target_pos_sem = self.getTargetPosition(SEM_IMAGING)
                    if not isNearPosition(self.focus.position.value, focus_deactive, self.focus.axes):
                        sub_moves.append((self.focus, focus_deactive))
                    sub_moves.append((self.stage, filter_dict({'x', 'y', 'z'}, target_pos_sem)))
                    sub_moves.append((self.stage, filter_dict({'rx', 'rz'}, target_pos_sem)))

            if target in (GRID_1, GRID_2):
                # The current mode doesn't change. Only X/Y/Z should move (typically
                # only X/Y). In the same mode, GRID 1/2, the rx/rz values should not change
                # TODO: probably a better way would be to forbid grid switching if not in SEM/FM imaging posture
                sub_moves.append((self.stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                sub_moves.append((self.stage, filter_dict({'rx', 'rz'}, target_pos)))
            elif target in (LOADING, SEM_IMAGING, FM_IMAGING):
                # save rotation and tilt in SEM before switching to FM imaging
                # to restore rotation and tilt while switching back from FM -> SEM
                if current_label == SEM_IMAGING and target == FM_IMAGING:
                    current_value = self.stage.position.value
                    self.stage.updateMetadata({model.MD_FAV_SEM_POS_ACTIVE: {'rx': current_value['rx'],
                                                                             'rz': current_value['rz']}})
                # Park the focuser for safety
                if not isNearPosition(self.focus.position.value, focus_deactive, self.focus.axes):
                    sub_moves.append((self.focus, focus_deactive))

                # Move translation axes, then rotational ones
                sub_moves.append((self.stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                sub_moves.append((self.stage, filter_dict({'rx', 'rz'}, target_pos)))

                if target == FM_IMAGING:
                    # Engage the focuser
                    sub_moves.append((self.focus, focus_active))
            else:
                raise ValueError(f"Unsupported move to target {target_name}")

            # run the moves
            logging.info("Moving from position {} to position {}.".format(current_name, target_name))
            for component, sub_move in sub_moves:
                self._run_sub_move(future, component, sub_move)

        except CancelledError:
            logging.info("CryoSwitchSamplePosition cancelled.")
        except Exception:
            logging.exception("Failure to move to {} position.".format(target_name))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED


class MeteorZeiss1PostureManager(MeteorPostureManager):
    def __init__(self, microscope):
        super().__init__(microscope)
        # Check required metadata used during switching
        required_keys_zeiss1 = {'x', 'y', 'm', 'z', 'z_ct', 'dx', 'dy'}
        self.required_keys.add(model.MD_CALIB)
        self.check_stage_metadata(self.required_keys)
        self.check_calib_data(required_keys_zeiss1)
        if not {"x", "y", "m", "z", "rx", "rm"}.issubset(self.stage.axes):
            missed_axes = {'x', 'y', 'm', 'z', 'rx', 'rm'} - self.stage.axes.keys()
            raise KeyError("The stage misses %s axes" % missed_axes)

    def check_calib_data(self, required_keys: set):
        """
        Checks the keys in the stage metadata MD_CALIB.
        :param required_keys : A set of keys that must be present in the MD_CALIB metadata.
        :raises ValueError: if the metadata does not have all required keys.
        """
        # Check for unique keys in the given metadata
        stage_md = self.stage.getMetadata()
        calibrated_md = stage_md[model.MD_CALIB]
        if not required_keys.issubset(calibrated_md.keys()):
            missing_keys = required_keys - calibrated_md.keys()
            raise ValueError(f"Stage metadata {model.MD_CALIB} is missing the following required keys: {missing_keys}.")

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        :param target_pos_lbl: (int) a label representing a position (SEM_IMAGING, FM_IMAGING, GRID_1 or GRID_2)
        :return: (dict str->float) the end position of the stage
        :raises ValueError: if the target position is not supported
        """
        stage_md = self.stage.getMetadata()
        current_position = self.getCurrentPostureLabel()
        end_pos = None

        if target_pos_lbl == LOADING:
            end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
        elif current_position in [LOADING, SEM_IMAGING]:
            if target_pos_lbl in [SEM_IMAGING, GRID_1]:
                # if at loading, and sem is pressed, choose grid1 by default
                sem_grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]  # get the base
                sem_grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
                end_pos = sem_grid1_pos
            elif target_pos_lbl == GRID_2:
                sem_grid2_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
                sem_grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
                end_pos = sem_grid2_pos
            elif target_pos_lbl == FM_IMAGING:
                if current_position == LOADING:
                    # if at loading and fm is pressed, choose grid1 by default
                    sem_grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
                    sem_grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
                    fm_target_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
                elif current_position == SEM_IMAGING:
                    fm_target_pos = self._transformFromSEMToMeteor(self.stage.position.value)
                end_pos = fm_target_pos
        elif current_position == FM_IMAGING:
            if target_pos_lbl == GRID_1:
                sem_grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]  # get the base
                sem_grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
                end_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
            elif target_pos_lbl == GRID_2:
                sem_grid2_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
                sem_grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
                end_pos = self._transformFromSEMToMeteor(sem_grid2_pos)
            elif target_pos_lbl == SEM_IMAGING:
                end_pos = self._transformFromMeteorToSEM(self.stage.position.value)

        if end_pos is None:
            raise ValueError("Unknown target position {} when in {}".format(
                POSITION_NAMES.get(target_pos_lbl, target_pos_lbl),
                POSITION_NAMES.get(current_position, current_position))
            )

        return end_pos

    def getCurrentGridLabel(self) -> int:
        """
        Detects which grid on the sample shuttle of meteor being viewed
        :return: (GRID_1 or GRID_2) the guessed grid. If current position is not SEM
         or FM, None would be returned.
        """
        current_pos = self.stage.position.value
        current_pos_label = self.getCurrentPostureLabel()
        stage_md = self.stage.getMetadata()

        if current_pos_label == SEM_IMAGING:
            grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE].copy()
            grid2_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
            grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
            grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
        elif current_pos_label == FM_IMAGING:
            grid1_pos = stage_md[model.MD_FAV_FM_POS_ACTIVE].copy()
            grid2_pos = stage_md[model.MD_FAV_FM_POS_ACTIVE]
            grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
            grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
            grid1_pos = self._transformFromSEMToMeteor(grid1_pos)
            grid2_pos = self._transformFromSEMToMeteor(grid2_pos)
        else:
            logging.warning("Cannot guess between grid 1 and grid2 in %s position" % POSITION_NAMES[current_pos_label])
            return None
        distance_to_grid1 = self._getDistance(current_pos, grid1_pos)
        distance_to_grid2 = self._getDistance(current_pos, grid2_pos)

        return GRID_1 if distance_to_grid2 > distance_to_grid1 else GRID_2

    # Note: this transformation consists of translation and rotation.
    # The translations are along the x, y and m axes. They are calculated based on
    # the current position and some calibrated values existing in metadata "CALIB".
    # The rotations are 180 degree around the rm axis, and a calibrated angle around the rx axis.
    # These angles exist in the metadata "FM_POS_ACTIVE".
    def _transformFromSEMToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
        meteor/FM imaging area.
        :param pos: (dict str->float) the current stage position.
        :return: (dict str->float) the transformed position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Call out calibrated values and stage tilt and rotation angles
        calibrated_values = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]
        sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

        # Define values that are used more than once
        rx_sem = pos["rx"]  # Current tilt angle (can differ per point of interest)
        rx_fm = fm_pos_active["rx"]  # Calibrated tilt angle, for imaging perpendicular to objective
        b_0 = pos["z"] - calibrated_values["z_ct"]
        x_0 = calibrated_values["x"]
        y_0 = calibrated_values["y"]
        m_0 = calibrated_values["m"]

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position, at the SEM position stage tilt
        sem_reference_pos_x = x_0
        sem_reference_pos_y = y_0 + b_0 * math.sin(rx_sem)
        sem_reference_pos_m = m_0 + b_0 * (math.cos(rx_sem) - 1)

        # Calculate the equivalent coordinates of the calibrated position, at the FM position
        fm_reference_pos_x = x_0 + calibrated_values["dx"]
        fm_reference_pos_y = y_0 + calibrated_values["dy"] + b_0 * math.sin(rx_fm)
        fm_reference_pos_m = m_0 + b_0 * (math.cos(rx_fm) - 1)

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the FM position.
        # Note that the 180-degree rotation is taken care of by swapping the +/- signs for x and y (wrt the m equation).
        transformed_pos["x"] = fm_reference_pos_x + (sem_reference_pos_x - pos["x"])
        transformed_pos["y"] = fm_reference_pos_y + (sem_reference_pos_y - pos["y"])
        transformed_pos["m"] = fm_reference_pos_m + (pos["m"] - sem_reference_pos_m)

        # Update the angles to the FM position angles
        transformed_pos.update(fm_pos_active)

        # Return transformed_pos (containing the new x, y, m, rx, rm coordinates, as well as the unchanged z coordinate)
        return transformed_pos

    # Note: this transformation consists of translation and rotation.
    # The translations are along the x, y and m axes. They are calculated based on
    # the current position and some calibrated values existing in metadata "CALIB".
    # The rotations are 180 degree around the rm axis, and a calibrated angle around the rx axis.
    # These angles exist in the metadata "FM_POS_ACTIVE" and "SEM_POS_ACTIVE".
    def _transformFromMeteorToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area
        to the SEM imaging area.
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Call out calibrated values and stage tilt and rotation angles
        calibrated_values = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]
        sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

        # Define values that are used more than once
        rx_sem = sem_pos_active["rx"]
        rx_fm = fm_pos_active["rx"]
        b_0 = pos["z"] - calibrated_values["z_ct"]
        x_0 = calibrated_values["x"]
        y_0 = calibrated_values["y"]
        m_0 = calibrated_values["m"]

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position, at the SEM position stage tilt
        sem_ref_pos_x = x_0
        sem_ref_pos_y = y_0 + b_0 * math.sin(rx_sem)
        sem_ref_pos_m = m_0 + b_0 * (math.cos(rx_sem) - 1)

        # Calculate the equivalent coordinates of the calibrated position, at the FM position
        fm_ref_pos_x = x_0 + calibrated_values["dx"]
        fm_ref_pos_y = y_0 + calibrated_values["dy"] + b_0 * math.sin(rx_fm)
        fm_ref_pos_m = m_0 + b_0 * (math.cos(rx_fm) - 1)

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the FM position.
        # Note that the 180-degree rotation is taken care of by swapping the +/- signs for x and y (wrt the m equation).
        transformed_pos["x"] = sem_ref_pos_x + (fm_ref_pos_x - pos["x"])
        transformed_pos["y"] = sem_ref_pos_y + (fm_ref_pos_y - pos["y"])
        transformed_pos["m"] = sem_ref_pos_m + (pos["m"] - fm_ref_pos_m)

        # Update the angles to the FM position angles
        transformed_pos.update(sem_pos_active)

        # Return transformed_pos (containing the new x, y, m, rx, rm coordinates, as well as the unchanged z coordinate)
        return transformed_pos

    def _doCryoSwitchSamplePosition(self, future, target):
        try:
            try:
                target_name = POSITION_NAMES[target]
            except KeyError:
                raise ValueError(f"Unknown target '{target}'")

            # Create axis->pos dict from target position given smaller number of axes
            filter_dict = lambda keys, d: {key: d[key] for key in keys}

            focus = model.getComponent(role='focus')
            stage = model.getComponent(role='stage-bare')
            # get the meta data
            focus_md = focus.getMetadata()
            focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
            focus_active = focus_md[model.MD_FAV_POS_ACTIVE]
            # To hold the ordered sub moves list
            sub_moves = []  # list of tuples (component, position)

            # get the current label
            current_label = self.getCurrentPostureLabel()
            current_name = POSITION_NAMES[current_label]

            if current_label == target:
                logging.warning(f"Requested move to the same position as current: {target_name}")

            # get the set point position
            target_pos = self.getTargetPosition(target)

            # If at some "weird" position, it's quite unsafe. We consider the targets
            # LOADING and SEM_IMAGING safe to go. So if not going there, first pass
            # by SEM_IMAGING and then go to the actual requested position.
            if current_label == UNKNOWN:
                logging.warning("Moving stage while current position is unknown.")
                if target not in (LOADING, SEM_IMAGING):
                    logging.debug("Moving first to SEM_IMAGING position")
                    target_pos_sem = self.getTargetPosition(SEM_IMAGING)
                    if not isNearPosition(focus.position.value, focus_deactive, focus.axes):
                        sub_moves.append((focus, focus_deactive))
                    sub_moves.append((stage, filter_dict({'z', 'm'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'x', 'y' 'rm'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx'}, target_pos)))

            if target in (GRID_1, GRID_2):
                # The current mode doesn't change.
                sub_moves.append((stage, filter_dict({'x', 'y', 'm', 'z'}, target_pos)))
                sub_moves.append((stage, filter_dict({'rx', 'rm'}, target_pos)))

            elif target in (LOADING, SEM_IMAGING, FM_IMAGING):
                # Park the focuser for safety
                if not isNearPosition(focus.position.value, focus_deactive, focus.axes):
                    sub_moves.append((focus, focus_deactive))

                if target == LOADING:
                    # TODO lower the z position
                    sub_moves.append((stage, filter_dict({'z', 'm'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'x', 'y', 'rm'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx'}, target_pos)))
                    # TODO increase the z position
                if target == SEM_IMAGING:
                    # when switching from FM to SEM
                    # move in the following order
                    sub_moves.append((stage, filter_dict({'rx', 'rm', 'x', 'y', 'm', 'z'}, target_pos)))
                if target == FM_IMAGING:

                    if current_label == LOADING:
                        # In practice, the user will not go directly from LOADING to FM_IMAGING
                        # but will go through SEM_IMAGING first. But just in case, we handle the case
                        # where the current position is LOADING and the target is FM_IMAGING, do the following:
                        # First switch from Loading to SEM_IMAGING
                        sem_int_posit = self.getTargetPosition(SEM_IMAGING)
                        sub_moves.append((stage, filter_dict({'rx'}, sem_int_posit)))
                        sub_moves.append((stage, filter_dict({'rm', 'x', 'y'}, sem_int_posit)))
                        sub_moves.append((stage, filter_dict({'m', 'z'}, sem_int_posit)))
                        # Then switch the stage from SEM_IMAGING to FM_IMAGING
                        sub_moves.append((stage, filter_dict({'m', 'z'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'y', 'x', 'rm'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'rx'}, target_pos)))

                    if current_label == SEM_IMAGING:
                        # save rotation and tilt in SEM before switching to FM imaging
                        # to restore rotation and tilt while switching back from FM -> SEM
                        current_value = self.stage.position.value
                        self.stage.updateMetadata({model.MD_FAV_SEM_POS_ACTIVE: {'rx': current_value['rx'],
                                                                                 'rm': current_value['rm']}})
                        # when switching from SEM to FM
                        # move in the following order :
                        sub_moves.append((stage, filter_dict({'rx', 'rm', 'x', 'y', 'm', 'z'}, target_pos)))

                    # Engage the focuser
                    sub_moves.append((focus, focus_active))
            else:
                raise ValueError(f"Unsupported move to target {target_name}")

            # run the moves
            logging.info("Moving from position {} to position {}.".format(current_name, target_name))
            for component, sub_move in sub_moves:
                self._run_sub_move(future, component, sub_move)

        except CancelledError:
            logging.info("CryoSwitchSamplePosition cancelled.")
        except Exception:
            logging.exception("Failure to move to {} position.".format(target_name))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED


class MeteorTescan1PostureManager(MeteorPostureManager):
    def __init__(self, microscope):
        super().__init__(microscope)
        # Check required metadata used during switching
        required_keys_tescan1 = {'x_0', 'y_0', 'z_ct', 'dx', 'dy'}
        self.required_keys.add(model.MD_CALIB)
        self.check_stage_metadata(self.required_keys)
        self.check_calib_data(required_keys_tescan1)
        if not {"x", "y", "z", "rx", "rz"}.issubset(self.stage.axes):
            missed_axes = {'x', 'y', 'z', 'rx', 'rz'} - self.stage.axes.keys()
            raise KeyError("The stage misses %s axes" % missed_axes)

    def check_calib_data(self, required_keys: set):
        """
        Checks the keys in the stage metadata MD_CALIB.
        :param required_keys : A set of keys that must be present in the MD_CALIB metadata.
        :raises ValueError: if the metadata does not have all required keys.
        """
        # Check for required keys in the given metadata
        stage_md = self.stage.getMetadata()
        calibrated_md = stage_md[model.MD_CALIB]
        if not required_keys.issubset(calibrated_md.keys()):
            missing_keys = required_keys - calibrated_md.keys()
            raise ValueError(f"Stage metadata {model.MD_CALIB} is missing the following required keys: {missing_keys}.")

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        :param target_pos_lbl: (int) a label representing a position (SEM_IMAGING, FM_IMAGING, GRID_1 or GRID_2)
        :return: (dict str->float) the end position of the stage
        :raises ValueError: if the target position is not supported
        """
        stage_md = self.stage.getMetadata()
        current_position = self.getCurrentPostureLabel()
        end_pos = None

        if target_pos_lbl == LOADING:
            end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
        elif current_position in [LOADING, SEM_IMAGING]:
            if target_pos_lbl in [SEM_IMAGING, GRID_1]:
                # if at loading, and sem is pressed, choose grid1 by default
                sem_grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]  # get the base
                sem_grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
                end_pos = sem_grid1_pos
            elif target_pos_lbl == GRID_2:
                sem_grid2_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
                sem_grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
                end_pos = sem_grid2_pos
            elif target_pos_lbl == FM_IMAGING:
                if current_position == LOADING:
                    # if at loading and fm is pressed, choose grid1 by default
                    sem_grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
                    sem_grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
                    fm_target_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
                elif current_position == SEM_IMAGING:
                    fm_target_pos = self._transformFromSEMToMeteor(self.stage.position.value)
                end_pos = fm_target_pos
        elif current_position == FM_IMAGING:
            if target_pos_lbl == GRID_1:
                sem_grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]  # get the base
                sem_grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
                end_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
            elif target_pos_lbl == GRID_2:
                sem_grid2_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
                sem_grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
                end_pos = self._transformFromSEMToMeteor(sem_grid2_pos)
            elif target_pos_lbl == SEM_IMAGING:
                end_pos = self._transformFromMeteorToSEM(self.stage.position.value)

        if end_pos is None:
            raise ValueError("Unknown target position {} when in {}".format(
                POSITION_NAMES.get(target_pos_lbl, target_pos_lbl),
                POSITION_NAMES.get(current_position, current_position))
            )

        return end_pos

    def getCurrentGridLabel(self) -> int:
        """
        Detects which grid on the sample shuttle of meteor being viewed
        :return: (GRID_1 or GRID_2) the guessed grid. If current position is not SEM
        or FM, None would be returned.
        """
        current_pos = self.stage.position.value
        current_pos_label = self.getCurrentPostureLabel()
        stage_md = self.stage.getMetadata()

        if current_pos_label == SEM_IMAGING:
            grid1_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE].copy()
            grid2_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
            grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
            grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
        elif current_pos_label == FM_IMAGING:
            grid1_pos = stage_md[model.MD_FAV_FM_POS_ACTIVE].copy()
            grid2_pos = stage_md[model.MD_FAV_FM_POS_ACTIVE]
            grid1_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
            grid2_pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]])
            grid1_pos = self._transformFromSEMToMeteor(grid1_pos)
            grid2_pos = self._transformFromSEMToMeteor(grid2_pos)
        else:
            logging.warning("Cannot guess between grid 1 and grid2 in %s position" % POSITION_NAMES[current_pos_label])
            return None
        distance_to_grid1 = self._getDistance(current_pos, grid1_pos)
        distance_to_grid2 = self._getDistance(current_pos, grid2_pos)

        return GRID_1 if distance_to_grid2 > distance_to_grid1 else GRID_2

    # Note: this transformation consists of translation and rotation.
    # The translations are along the x, y and m axes. They are calculated based on
    # the current position and some calibrated values existing in metadata "CALIB".
    # The rotations are 180 degree around the rm axis, and a calibrated angle around the rx axis.
    # These angles exist in the metadata "FM_POS_ACTIVE".
    def _transformFromSEMToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
        meteor/FM imaging area.
        :param pos: (dict str->float) the current stage position.
        :return: (dict str->float) the transformed position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Call out calibrated values and stage tilt and rotation angles
        calibrated_values = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]

        # Define values that are used more than once
        rx_sem = pos["rx"]  # Current tilt angle (can differ per point of interest)
        rx_fm = fm_pos_active["rx"]  # Calibrated tilt angle, for imaging perpendicular to objective
        x_0 = calibrated_values["x_0"]
        y_0 = calibrated_values["y_0"]
        z_ct = calibrated_values["z_ct"]
        b_0 = (pos["z"] - z_ct)*math.cos(rx_sem)

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position, at the SEM position stage tilt
        sem_reference_pos_x = x_0
        sem_reference_pos_y = y_0 - b_0 * math.tan(rx_sem)
        sem_reference_pos_z = b_0 * (1/math.cos(rx_sem) - 1)

        # Calculate the equivalent coordinates of the calibrated position, at the FM position
        fm_reference_pos_x = x_0 + calibrated_values["dx"]
        fm_reference_pos_y = y_0 + calibrated_values["dy"] - b_0 * math.tan(rx_fm)
        fm_reference_pos_z = b_0 * (1/math.cos(rx_fm) - 1)

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the FM position.
        # Note that the 180-degree rotation is taken care of by swapping the +/- signs for x and y (wrt the m equation).
        transformed_pos["x"] = fm_reference_pos_x + (sem_reference_pos_x - pos["x"])
        transformed_pos["y"] = fm_reference_pos_y + (sem_reference_pos_y - pos["y"])
        transformed_pos["z"] = fm_reference_pos_z + (pos["z"] - sem_reference_pos_z)

        # Update the angles to the FM position angles
        transformed_pos.update(fm_pos_active)

        # Return transformed_pos (containing the new x, y, z, rx, rz coordinates, as well as the unchanged z coordinate)
        return transformed_pos

    # Note: this transformation consists of translation and rotation.
    # The translations are along the x, y and m axes. They are calculated based on
    # the current position and some calibrated values existing in metadata "CALIB".
    # The rotations are 180 degree around the rm axis, and a calibrated angle around the rx axis.
    # These angles exist in the metadata "FM_POS_ACTIVE" and "SEM_POS_ACTIVE".
    def _transformFromMeteorToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area
        to the SEM imaging area.
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Call out calibrated values and stage tilt and rotation angles
        calibrated_values = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]
        sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

        # Define values that are used more than once
        rx_sem = sem_pos_active["rx"]
        rx_fm = fm_pos_active["rx"]
        x_0 = calibrated_values["x_0"]
        y_0 = calibrated_values["y_0"]
        z_ct = calibrated_values["z_ct"]
        b_0 = (pos["z"] - z_ct) * math.cos(rx_sem)

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position, at the SEM position stage tilt
        sem_ref_pos_x = x_0
        sem_ref_pos_y = y_0 - b_0 * math.tan(rx_sem)
        sem_ref_pos_z = b_0 * (1/math.cos(rx_sem) - 1)

        # Calculate the equivalent coordinates of the calibrated position, at the FM position
        fm_ref_pos_x = x_0 + calibrated_values["dx"]
        fm_ref_pos_y = y_0 + calibrated_values["dy"] - b_0 * math.tan(rx_fm)
        fm_ref_pos_z = b_0 * (1/math.cos(rx_fm) - 1)

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the FM position.
        # Note that the 180-degree rotation is taken care of by swapping the +/- signs for x and y (wrt the m equation).
        transformed_pos["x"] = sem_ref_pos_x + (fm_ref_pos_x - pos["x"])
        transformed_pos["y"] = sem_ref_pos_y + (fm_ref_pos_y - pos["y"])
        transformed_pos["z"] = sem_ref_pos_z + (pos["z"] - fm_ref_pos_z)

        # Update the angles to the FM position angles
        transformed_pos.update(sem_pos_active)

        # Return transformed_pos (containing the new x, y, z, rx, rz coordinates, as well as the unchanged z coordinate)
        return transformed_pos

    def _doCryoSwitchSamplePosition(self, future, target):
        try:
            try:
                target_name = POSITION_NAMES[target]
            except KeyError:
                raise ValueError(f"Unknown target '{target}'")

            # Create axis->pos dict from target position given smaller number of axes
            filter_dict = lambda keys, d: {key: d[key] for key in keys}

            focus = model.getComponent(role='focus')
            stage = model.getComponent(role='stage-bare')
            # get the meta data
            focus_md = focus.getMetadata()
            focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
            focus_active = focus_md[model.MD_FAV_POS_ACTIVE]
            # To hold the ordered sub moves list
            sub_moves = []  # list of tuples (component, position)

            # get the current label
            current_label = self.getCurrentPostureLabel()
            current_name = POSITION_NAMES[current_label]

            if current_label == target:
                logging.warning(f"Requested move to the same position as current: {target_name}")

            # get the set point position
            target_pos = self.getTargetPosition(target)

            # If at some "weird" position, it's quite unsafe. We consider the targets
            # LOADING and SEM_IMAGING safe to go. So if not going there, first pass
            # by SEM_IMAGING and then go to the actual requested position.
            if current_label == UNKNOWN:
                logging.warning("Moving stage while current position is unknown.")
                if target not in (LOADING, SEM_IMAGING):
                    logging.debug("Moving first to SEM_IMAGING position")
                    target_pos_sem = self.getTargetPosition(SEM_IMAGING)
                    if not isNearPosition(focus.position.value, focus_deactive, focus.axes):
                        sub_moves.append((focus, focus_deactive))
                    sub_moves.append((stage, filter_dict({'x'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'y', 'rz'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'z'}, target_pos)))

            if target in (GRID_1, GRID_2):
                # The current mode doesn't change.
                sub_moves.append((stage, filter_dict({'x'}, target_pos)))
                sub_moves.append((stage, filter_dict({'y', 'rz'}, target_pos)))
                sub_moves.append((stage, filter_dict({'rx'}, target_pos)))
                sub_moves.append((stage, filter_dict({'z'}, target_pos)))

            elif target in (LOADING, SEM_IMAGING, FM_IMAGING):
                # Park the focuser for safety
                if not isNearPosition(focus.position.value, focus_deactive, focus.axes):
                    sub_moves.append((focus, focus_deactive))

                if target == LOADING:
                    sub_moves.append((stage, filter_dict({'z'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'x', 'y', 'rz'}, target_pos)))

                if target == SEM_IMAGING:
                    # when switching from FM to SEM
                    # move in the following order
                    sub_moves.append((stage, filter_dict({'x'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'y', 'rz'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'rx'}, target_pos)))
                    sub_moves.append((stage, filter_dict({'z'}, target_pos)))
                if target == FM_IMAGING:

                    if current_label == LOADING:
                        # In practice, the user will not go directly from LOADING to FM_IMAGING
                        sub_moves.append((stage, filter_dict({'x'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'y', 'rz'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'rx'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'z'}, target_pos)))

                    if current_label == SEM_IMAGING:
                        # save rotation and tilt in SEM before switching to FM imaging
                        # to restore rotation and tilt while switching back from FM -> SEM
                        current_value = self.stage.position.value
                        self.stage.updateMetadata({model.MD_FAV_SEM_POS_ACTIVE: {'rx': current_value['rx'],
                                                                                 'rz': current_value['rz']}})
                        # when switching from SEM to FM
                        # move in the following order :
                        sub_moves.append((stage, filter_dict({'z'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'rx'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'rz', 'y'}, target_pos)))
                        sub_moves.append((stage, filter_dict({'x'}, target_pos)))
                    # Engage the focuser
                    sub_moves.append((focus, focus_active))
            else:
                raise ValueError(f"Unsupported move to target {target_name}")

            # run the moves
            logging.info("Moving from position {} to position {}.".format(current_name, target_name))
            for component, sub_move in sub_moves:
                self._run_sub_move(future, component, sub_move)

        except CancelledError:
            logging.info("CryoSwitchSamplePosition cancelled.")
        except Exception:
            logging.exception("Failure to move to {} position.".format(target_name))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED


class MimasPostureManager(MicroscopePostureManager):
    def __init__(self, microscope):
        # Load components
        self.stage = model.getComponent(role="stage")
        self.align = model.getComponent(role="align")
        self.gis = model.getComponent(role="gis")
        # set linear axes and rotational axes used
        self.axes = self.stage.axes
        self.linear_axes = set(key for key in self.axes.keys() if key in {'x', 'y', 'z', 'm'})
        self.rotational_axes = set(key for key in self.axes.keys() if key in {'rx', 'ry', 'rz', 'rm'})
        # required keys that must be present in the stage metadata
        self.required_keys = {
            model.MD_FAV_POS_DEACTIVE, model.MD_POS_ACTIVE_RANGE, model.MD_FAV_POS_ACTIVE,
            model.MD_SAMPLE_CENTERS}
        self.check_stage_metadata(self.required_keys)

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        :param target_pos_lbl: (int) a label representing a position LOADING, MILLING, COATING, FM_IMAGING.
        :return: (dict str->float) the end position of the stage
        :raises ValueError: if the target position is not supported
        """
        stage_md = self.stage.getMetadata()
        # There are actually only 2 positions for the stage: LOADING, and
        # everything else happens at "IMAGING".
        target_pos = {
            LOADING: stage_md[model.MD_FAV_POS_DEACTIVE],
            COATING: stage_md[model.MD_FAV_POS_ACTIVE],
            FM_IMAGING: stage_md[model.MD_FAV_POS_ACTIVE],
            MILLING: stage_md[model.MD_FAV_POS_ACTIVE],
        }
        # Fail early when required axes are not found on the positions metadata
        required_axes = {'x', 'y', 'z', 'rx', 'rz'}  # ry is normally never used so it can be omitted
        for stage_position in target_pos.values():
            if not required_axes.issubset(stage_position.keys()):
                raise ValueError("Stage %s metadata does not have all required axes %s." % (
                    list(stage_md.keys())[list(stage_md.values()).index(stage_position)],
                    required_axes))

        if target_pos_lbl not in target_pos:
            raise ValueError(f"{target_pos_lbl} not in {target_pos.keys()}")
        return target_pos[target_pos_lbl]

    def getCurrentPostureLabel(self, stage_pos: Dict[str, float] = None) -> int:
        """
        Detects the current aligner position of mimas
        :param stage_pos: (dict str->float) the stage position in which the label needs to be found. If None, it uses
         the current position of the stage.
        :return: a label LOADING, FM_IMAGING, MILLING, IMAGING, COATING, or UNKNOWN.
         IMAGING indicates that the stage is in a position compatible with FM_IMAGING and MILLING,
         but the aligner is not in a known position.
         UNKNKOWN is for all other unhandled positions.
        """
        # Firstly, both actuators should be referenced
        stage_referenced = all(self.stage.referenced.value.values())
        aligner_referenced = all(self.align.referenced.value.values())
        if not stage_referenced or not aligner_referenced:
            return UNKNOWN

        # Defined stage positions
        stage_md = self.stage.getMetadata()
        stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
        stage_imaging_rng = stage_md[model.MD_POS_ACTIVE_RANGE]

        if stage_pos is None:
            stage_pos = self.stage.position.value

        current_align_pos = self.align.position.value
        aligner_md = self.align.getMetadata()
        aligner_fib = aligner_md[model.MD_FAV_POS_DEACTIVE]
        aligner_optical = aligner_md[model.MD_FAV_POS_ACTIVE]

        # All MILLING, FM_IMAGING, and COATING are at the same position (ie, somewhere
        # within the IMAGING range). To distinguish we check the position of the optical
        # lens (aligner) and GIS needle.
        # Note that there can be some odd combinations that do not fit any of the
        # known positions, in which case we return "IMAGING" (to indicate it's a bit
        # unclear but not UNKNOWN either).

        if isInRange(stage_pos, stage_imaging_rng, {'x', 'y', 'z', 'rx', 'ry', 'rz'}):
            if isNearPosition(current_align_pos, aligner_fib, self.align.axes):
                try:
                    gis_choices = self.gis.axes["arm"].choices
                    gis_pos = gis_choices[self.gis.position.value["arm"]]  # convert position to a string
                except Exception:
                    logging.exception("Failed to read GIS arm position, assuming it's parked")
                    gis_pos = "parked"
                if gis_pos == "engaged":
                    return COATING
                else:
                    return MILLING
            elif isNearPosition(current_align_pos, aligner_optical, self.align.axes):
                return FM_IMAGING
            return IMAGING
        elif (isNearPosition(stage_pos, stage_deactive, self.stage.axes) and
              isNearPosition(current_align_pos, aligner_fib, self.align.axes)):
            return LOADING

        # None of the above -> unknown position
        return UNKNOWN

    def _doCryoSwitchSamplePosition(self, future, target):
        """
        Do the actual switching procedure for cryoSwitchSamplePosition
        :param future: cancellable future of the move
        :param target: (int) target position either one of the constants: LOADING, MILLING, COATING, FM_IMAGING.
        """
        try:
            try:
                target_name = POSITION_NAMES[target]
            except KeyError:
                raise ValueError(f"Unknown target '{target}'")

            # get the stage and aligner objects
            stage_md = self.stage.getMetadata()
            align_md = self.align.getMetadata()
            stage_imaging_rng = stage_md[model.MD_POS_ACTIVE_RANGE]
            stage_target_pos = self.getTargetPosition(target)
            aligner_fib = align_md[model.MD_FAV_POS_DEACTIVE]
            aligner_optical = align_md[model.MD_FAV_POS_ACTIVE]
            stage_referenced = all(self.stage.referenced.value.values())

            current_pos = self.stage.position.value
            current_label = self.getCurrentPostureLabel()
            current_name = POSITION_NAMES[current_label]

            # If no move to do => skip all
            if target == current_label:
                logging.debug("Position %s requested, while already in that position", target_name)
                return

            # Only loading position is allowed to go to at init, or if something odd happened
            if target != LOADING:
                if not stage_referenced:
                    raise ValueError(f"Unable to move to {target_name} while stage is not referenced.")
                if current_label == UNKNOWN:
                    raise ValueError(f"Unable to move to {target_name} while current position is UNKNOWN.")

            # Find the position of the GIS. The component is expected to have a "arm"
            # axis with choices defining the position to "parked" and "engaged".
            gis = model.getComponent(role="gis")
            gis_parked = None
            gis_engaged = None
            for arm_pos, pos_name in gis.axes["arm"].choices.items():
                if pos_name == "parked":
                    gis_parked = arm_pos
                elif pos_name == "engaged":
                    gis_engaged = arm_pos

            if gis_parked is None or gis_engaged is None:
                raise ValueError("Failed to find the parked & engaged positions on the gis component")

            logging.info("Moving from position %s to position %s.", current_name, target_name)

            # Always park the GIS needle before a move
            self._run_sub_move(future, gis, {"arm": gis_parked})

            if target == LOADING:
                if not stage_referenced:
                    self._run_reference(future, self.stage)
                self._run_sub_move(future, self.align, aligner_fib)  # park the optical lens
                self._run_sub_move(future, self.stage, stage_target_pos)
            elif target in (MILLING, FM_IMAGING, COATING):
                # If not in imaging mode yet, move the stage to the default imaging position
                if not isInRange(current_pos, stage_imaging_rng, {'x', 'y', 'z', 'rx', 'ry', 'rz'}):
                    # move stage to imaging range (with the optical lens retracted)
                    self._run_sub_move(future, self.align, aligner_fib)
                    self._run_sub_move(future, self.stage, stage_target_pos)

                if target == MILLING:
                    # retract the optical lens
                    self._run_sub_move(future, self.align, aligner_fib)
                elif target == FM_IMAGING:
                    # engage the optical lens
                    self._run_sub_move(future, self.align, aligner_optical)
                elif target == COATING:
                    # retract the optical lens
                    self._run_sub_move(future, self.align, aligner_fib)
                    # Engage the GIS needle
                    self._run_sub_move(future, gis, {"arm": gis_engaged})
                    # TODO: Turn on the GIS heater (and turn it off for every other positions?)
                    # At least need to add it to the simulator... and get the driver working
                    # gis_reservoir = model.getComponent(role="gis-reservoir")
                    # gis_reservoir.temperatureRegulation.value = True

        except CancelledError:
            logging.info("CryoSwitchSamplePosition cancelled.")
        except Exception:
            logging.exception("Failure to move to {} position.".format(target_name))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED


class EnzelPostureManager(MicroscopePostureManager):
    def __init__(self, microscope):
        # Load components
        self.stage = model.getComponent(role='stage')
        self.align = model.getComponent(role='align')
        # Set linear axes and rotational axes used
        self.axes = self.stage.axes
        self.linear_axes = set(key for key in self.axes.keys() if key in {'x', 'y', 'z', 'm'})
        self.rotational_axes = set(key for key in self.axes.keys() if key in {'rx', 'ry', 'rz', 'rm'})
        # required keys that must be present in the stage metadata
        self.required_keys = {
            model.MD_POS_ACTIVE_RANGE, model.MD_FAV_POS_ALIGN, model.MD_FAV_POS_ACTIVE, model.MD_FAV_POS_SEM_IMAGING,
            model.MD_FAV_POS_DEACTIVE, model.MD_FAV_POS_COATING, model.MD_ION_BEAM_TO_SAMPLE_ANGLE}
        self.check_stage_metadata(self.required_keys)
        # Check axes in range metadata
        stage_metadata = self.stage.getMetadata()
        if not {'x', 'y', 'z'}.issubset(stage_metadata[model.MD_POS_ACTIVE_RANGE]):
            raise ValueError('POS_ACTIVE_RANGE metadata should have values for x, y, z axes.')

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        :param target_pos_lbl: (int) a label representing a position COATING, SEM_IMAGING, THREE_BEAMS,
         ALIGNMENT, LOADING
        :return: (dict str->float) the end position of the stage
        :raises ValueError: if the target position is not supported
        """
        stage_md = self.stage.getMetadata()
        target_pos = {LOADING: stage_md[model.MD_FAV_POS_DEACTIVE],
                      IMAGING: stage_md[model.MD_FAV_POS_ACTIVE],
                      COATING: stage_md[model.MD_FAV_POS_COATING],
                      ALIGNMENT: stage_md[model.MD_FAV_POS_ALIGN],
                      SEM_IMAGING: stage_md[model.MD_FAV_POS_SEM_IMAGING],
                      THREE_BEAMS: self.get3beamsSafePos(stage_md[model.MD_FAV_POS_ACTIVE], SAFETY_MARGIN_5DOF)
                      }
        # Fail early when required axes are not found on the positions metadata
        required_axes = {'x', 'y', 'z', 'rx', 'rz'}
        for stage_position in target_pos.values():
            if not required_axes.issubset(stage_position.keys()):
                raise ValueError("Stage %s metadata does not have all required axes %s." % (
                    list(stage_md.keys())[list(stage_md.values()).index(stage_position)], required_axes))

        if target_pos_lbl not in target_pos:
            raise ValueError(f"{target_pos_lbl} not in {target_pos.keys()}")
        return target_pos[target_pos_lbl]

    def getCurrentPostureLabel(self, stage_pos: Dict[str, float] = None) -> int:
        """
        Detects the current stage position of enzel.
        :param stage_pos: (dict str->float) the stage position in which the label needs to be found. If None, it uses
         the current position of the stage.
        :return: a label UNKNOWN, COATING, SEM_IMAGING, THREE_BEAMS, ALIGNMENT, LOADING or LOADING_PATH
        """
        stage_posture = self._getCurrentStagePostureLabel(stage_pos)
        if stage_posture == UNKNOWN:
            return UNKNOWN

        align_posture = self._getCurrentAlignerPositionLabel()
        if align_posture == UNKNOWN:
            return UNKNOWN

        if (align_posture == LOADING  # Parked
                and stage_posture in (LOADING, COATING, SEM_IMAGING, LOADING_PATH)):
            return stage_posture
        elif (align_posture == THREE_BEAMS  # Engaged
                and stage_posture in (IMAGING, ALIGNMENT, THREE_BEAMS)):
            return stage_posture
        elif (align_posture == LOADING_PATH
                and stage_posture == LOADING_PATH):
            return stage_posture

        # None of the above -> unknown position
        return UNKNOWN

    def _getCurrentStagePostureLabel(self, stage_pos: Dict[str, float] = None) -> int:
        """
        Detects the current stage position of enzel.
        :param stage_pos: (dict str->float) the stage position in which the label needs to be found. If None, it uses
         the current position of the stage.
        :return: a label UNKNOWN, COATING, SEM_IMAGING, THREE_BEAMS, ALIGNMENT, LOADING or LOADING_PATH
        """
        stage_md = self.stage.getMetadata()
        stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
        stage_active = stage_md[model.MD_FAV_POS_ACTIVE]
        stage_active_range = stage_md[model.MD_POS_ACTIVE_RANGE]
        stage_coating = stage_md[model.MD_FAV_POS_COATING]
        stage_alignment = stage_md[model.MD_FAV_POS_ALIGN]
        stage_sem_imaging = stage_md[model.MD_FAV_POS_SEM_IMAGING]

        if stage_pos is None:
            stage_pos = self.stage.position.value
        # If stage is not referenced, set position as unknown (to only allow loading position)
        if not all(self.stage.referenced.value.values()):
            return UNKNOWN
        # If stage is not referenced, set position as unknown (to only allow loading position)
        # Check the stage is near the coating position
        if isNearPosition(stage_pos, stage_coating, self.stage.axes):
            return COATING
        # Check the stage X,Y,Z are within the active range and on the tilted plane -> imaging position
        if isInRange(stage_pos, stage_active_range, {'x', 'y', 'z'}):
            if isNearPosition(stage_pos, {'rx': stage_active['rx']}, {'rx'}):
                return THREE_BEAMS
            elif isNearPosition(stage_pos, {'rx': stage_sem_imaging['rx']}, {'rx'}):
                return SEM_IMAGING

        # Check the stage is near the loading position
        if isNearPosition(stage_pos, stage_deactive, self.stage.axes):
            return LOADING

        # Check the stage is near the alignment position (= 3 beams but really safe)
        # Only report this position if it's not considered THREE_BEAMS
        if isNearPosition(stage_pos, stage_alignment, self.stage.axes):
            return ALIGNMENT

        # TODO: refine loading path to be between any move from loading to active range?
        # Check the current position is near the line between DEACTIVE and ACTIVE
        imaging_progress = self.getMovementProgress(stage_pos, stage_deactive, stage_active)
        if imaging_progress is not None:
            return LOADING_PATH

        # Check the current position is near the line between DEACTIVE and COATING
        coating_progress = self.getMovementProgress(stage_pos, stage_deactive, stage_coating)
        if coating_progress is not None:
            return LOADING_PATH

        # Check the current position is near the line between DEACTIVE and COATING
        alignment_path = self.getMovementProgress(stage_pos, stage_deactive, stage_alignment)
        if alignment_path is not None:
            return LOADING_PATH
        # None of the above -> unknown position
        return UNKNOWN

    def _doCryoSwitchSamplePosition(self, future, target):
        """
        Do the actual switching procedure for cryoSwitchSamplePosition
        :param future: cancellable future of the move
        :param target: (int) target position either one of the constants: LOADING, IMAGING,
         ALIGNMENT, COATING, MILLING, SEM_IMAGING, FM_IMAGING.
        """
        try:
            try:
                target_name = POSITION_NAMES[target]
            except KeyError:
                raise ValueError(f"Unknown target '{target}'")

            # Create axis->pos dict from target position given smaller number of axes
            filter_dict = lambda keys, d: {key: d[key] for key in keys}
            align_md = self.align.getMetadata()
            align_deactive = align_md[model.MD_FAV_POS_DEACTIVE]
            stage_referenced = all(self.stage.referenced.value.values())
            target_position = self.getTargetPosition(target)
            current_pos = self.stage.position.value
            # To hold the sub moves to run if normal ordering failed
            fallback_submoves = [{'x', 'y', 'z'}, {'rx', 'rz'}]

            current_label = self._getCurrentStagePostureLabel()
            current_name = POSITION_NAMES[current_label]

            if target == LOADING:
                if current_label is UNKNOWN and stage_referenced:
                    logging.warning("Moving stage to loading while current position is unknown.")
                if abs(target_position['rx']) > ATOL_ROTATION_POS:
                    raise ValueError(
                        "Absolute value of rx for FAV_POS_DEACTIVE is greater than {}".format(ATOL_ROTATION_POS))

                # Check if stage is not referenced:
                # park aligner (move it to loading position) then reference the stage
                if not stage_referenced:
                    future._running_subf = self._cryoSwitchAlignPosition(LOADING)
                    try:
                        future._running_subf.result(timeout=60)
                    except TimeoutError:
                        future._running_subf.cancel()
                    if future._task_state == CANCELLED:
                        logging.info("Cancelling aligner movement...")
                        raise CancelledError()
                    self._run_reference(future, self.stage)

                # Add the sub moves to perform the loading move
                if current_label is UNKNOWN and not stage_referenced:
                    # After referencing the stage could move near the maximum axes range,
                    # and moving single axes may result in an invalid/reachable position error,
                    # so all linear axes will be moved together for this special case.
                    sub_moves = [{'x', 'y', 'z'}, {'rx', 'rz'}]
                else:
                    # Notes on the movement on the typical case:
                    # - Moving each linear axis separately to be easily trackable by the user from the chamber cam.
                    # - Moving X first is a way to move it to a safe position, as it's not affected by the rx
                    # (and rz is typically always 0). Moreover, X is the largest move, and so it'll be
                    # "around" the loading position.
                    # - The X/Y/Z movement is in the Rx referential. So if the rx is tilted (eg, we are in IMAGING),
                    # and Y/Z are far from the pivot point, we have a good chance of hitting something.
                    # Moving along X should always be safe (as Rx is not affected by this axis position).
                    sub_moves = [{'x'}, {'y'}, {'z'}, {'rx', 'rz'}]

            elif target in (ALIGNMENT, IMAGING, SEM_IMAGING, COATING, THREE_BEAMS):
                if current_label is LOADING:
                    # Automatically run the referencing procedure as part of the
                    # first step of the movement loading → imaging/coating position
                    self._run_reference(future, self.stage)
                elif current_label is UNKNOWN:
                    raise ValueError(f"Unable to move to {target_name} while current position is unknown.")

                # Add the sub moves to perform the imaging/coating/alignment/sem_imaging moves
                # Essentially the same/reverse as for going to LOADING: do the small movements first near
                # the loading position, and end with the large x move to get close to the pole-piece.
                # TODO: test if coating position needs a different ordering
                if current_label == LOADING:
                    # As moving from loading position requires re-referencing the stage, move linked axes (y & z)
                    # together to prevent invalid/reachable position error
                    sub_moves = [{'y', 'z'}, {'rx', 'rz'}, {'x'}]
                else:
                    sub_moves = [{'z'}, {'y'}, {'rx', 'rz'}, {'x'}]
            else:
                raise ValueError(f"Unsupported move to target {target_name}")

            try:
                logging.info("Starting sample movement from {} -> {}...".format(current_name, target_name))
                # Park aligner to safe position before any movement
                if not isNearPosition(self.align.position.value, align_deactive, self.align.axes):
                    future._running_subf = self._cryoSwitchAlignPosition(LOADING)
                    try:
                        future._running_subf.result(timeout=60)
                    except TimeoutError:
                        future._running_subf.cancel()
                    if future._task_state == CANCELLED:
                        logging.info("Cancelling aligner movement...")
                        raise CancelledError()

                # The movement in Rx is quite odd with the stage (moves a lot of axes).
                # So if any large Rx rotation is needed, we do it far away from
                # the pole-piece. The movement in X is independent of rx, so it
                # should be always safe to go to the LOADING position in rx.
                if abs(current_pos["rx"] - target_position["rx"]) > math.radians(2):
                    target_pos_loading = self.getTargetPosition(LOADING)
                    sub_move_dict = filter_dict({"x"}, target_pos_loading)
                    logging.debug("Moving %s to a safe position in X axis, to %s.", self.stage.name, sub_move_dict)
                    self._run_sub_move(future, self.stage, sub_move_dict)

                    sub_move_dict = filter_dict({"rx"}, target_pos_loading)
                    logging.debug("Moving %s to a safe rotation position in Rx axis, to %s.", self.stage.name,
                                  sub_move_dict)
                    self._run_sub_move(future, self.stage, sub_move_dict)

                for sub_move in sub_moves:
                    sub_move_dict = filter_dict(sub_move, target_position)
                    logging.debug("Moving %s to %s.", self.stage.name, sub_move_dict)
                    self._run_sub_move(future, self.stage, sub_move_dict)
                if target in (IMAGING, ALIGNMENT, THREE_BEAMS):
                    future._running_subf = self._cryoSwitchAlignPosition(target)
                    try:
                        future._running_subf.result(timeout=60)
                    except TimeoutError:
                        future._running_subf.cancel()
                    if future._task_state == CANCELLED:
                        logging.info("Cancelling aligner movement...")
                        raise CancelledError()
            except IndexError:
                # In case the required movement is invalid/unreachable with the smaract 5dof stage
                # Move all linear axes first then rotational ones using the fallback_submoves
                logging.debug("This move %s is unreachable, trying to move all axes at once...",
                              sub_move_dict)
                for sub_move in fallback_submoves:
                    sub_move_dict = filter_dict(sub_move, target_position)
                    logging.debug("Moving %s to %s.", self.stage.name, sub_move)
                    self._run_sub_move(future, self.stage, sub_move_dict)

        except CancelledError:
            logging.info("CryoSwitchSamplePosition cancelled.")
        except Exception:
            logging.exception("Failure to move to {} position.".format(target_name))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED

    def get3beamsSafePos(self, active_pos: Dict[str, float], safety_margin: float) -> Dict[str, float]:
        """
        Get the safe position of 3 beams alignment for either 5dof or 3dof stages
        :param active_pos: (dict str->float) stage active position
        :param safety_margin: (float) amount to lower the stage Z axis
        :return: (dict str->float) safe position for 3 beams alignment
        """
        three_beams_pos = copy.copy(active_pos)
        three_beams_pos['z'] -= safety_margin
        return three_beams_pos

    def _cryoSwitchAlignPosition(self, target: int):
        """
        Provide the ability to switch between loading, imaging and alignment position, without bumping into anything.
        :param target: (int) target position either one of the constants LOADING, IMAGING or ALIGNMENT
        :return (CancellableFuture -> None): cancellable future of the move to observe the progress, and control the raise
        :raises ValueError
        """
        f = model.CancellableFuture()
        f.task_canceller = self._cancelCryoMoveSample
        f._task_state = RUNNING
        f._task_lock = threading.Lock()
        f._running_subf = model.InstantaneousFuture()
        # Run in separate thread
        executeAsyncTask(f, self._doCryoSwitchAlignPosition, args=(f, target))
        return f

    def _doCryoSwitchAlignPosition(self, future, target):
        """
        Do the actual switching procedure for the Cryo lens stage (align) between loading, imaging and alignment positions
        :param future: cancellable future of the move
        :param target: target position either one of the constants LOADING, THREE_BEAMS and ALIGNMENT
        """
        try:
            target_name = POSITION_NAMES[target]
        except KeyError:
            raise ValueError(f"Unknown target '{target}'")

        try:
            align_md = self.align.getMetadata()
            target_pos = {LOADING: align_md[model.MD_FAV_POS_DEACTIVE],
                          IMAGING: align_md[model.MD_FAV_POS_ACTIVE],
                          ALIGNMENT: align_md[model.MD_FAV_POS_ALIGN],
                          THREE_BEAMS: self.get3beamsSafePos(align_md[model.MD_FAV_POS_ACTIVE], SAFETY_MARGIN_3DOF)
                          }
            align_referenced = all(self.align.referenced.value.values())
            # Fail early when required axes are not found on the positions metadata
            required_axes = {'x', 'y', 'z'}
            for align_position in target_pos.values():
                if not required_axes.issubset(align_position.keys()):
                    raise ValueError("Aligner %s metadata does not have all required axes %s." % (
                        list(align_md.keys())[list(align_md.values()).index(align_position)], required_axes))
            # To hold the ordered sub moves list
            sub_moves = []
            # Create axis->pos dict from target position given smaller number of axes
            filter_dict = lambda keys, d: {key: d[key] for key in keys}

            current_label = self._getCurrentAlignerPositionLabel()
            current_name = POSITION_NAMES[current_label]

            if target == LOADING:
                if current_label is UNKNOWN:
                    logging.warning("Parking aligner while current position is unknown.")

                # reference align if not already referenced
                if not align_referenced:
                    self._run_reference(future, self.align)

                # Add the sub moves to perform the loading move
                # NB: moving Z axis downward first so when aligner Y move (
                # compensating 3DOF Y&Z) upwards it doesn't hit the 5DOF
                sub_moves = [{'x'}, {'z'}, {'y'}]

            elif target in (ALIGNMENT, IMAGING, THREE_BEAMS):
                if current_label is UNKNOWN:
                    raise ValueError("Unable to move aligner to {} while current position is unknown.".format(
                        target_name))

                # Add the sub moves to perform the imaging/alignment move
                # Moving Y axis first downwards so Z move upwards it doesn't hit the 5DOF stage
                sub_moves = [{'y'}, {'z'}, {'x'}]
            else:
                raise ValueError("Unknown target value %s." % target)

            logging.info("Starting aligner movement from {} -> {}...".format(current_name, target_name))
            for sub_move in sub_moves:
                self._run_sub_move(future, self.align, filter_dict(sub_move, target_pos[target]))
        except CancelledError:
            logging.info("_doCryoSwitchAlignPosition cancelled.")
        except Exception:
            logging.exception("Failure to move to {} position.".format(target_name))
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED

    def _getCurrentAlignerPositionLabel(self) -> int:
        """
        Determine the current aligner position
        :return: (int) a value representing stage position from the constants LOADING, THREE_BEAMS, etc.
        """
        align_md = self.align.getMetadata()
        align_deactive = align_md[model.MD_FAV_POS_DEACTIVE]
        align_active = align_md[model.MD_FAV_POS_ACTIVE]
        align_alignment = align_md[model.MD_FAV_POS_ALIGN]
        three_beams = self.get3beamsSafePos(align_md[model.MD_FAV_POS_ACTIVE], SAFETY_MARGIN_3DOF)
        current_pos = self.align.position.value

        # If align is not referenced, set position as unknown (to only allow loading position)
        if not all(self.align.referenced.value.values()):
            return UNKNOWN

        # Check the stage is near the loading position
        if isNearPosition(current_pos, align_deactive, self.align.axes):
            return LOADING

        # Anywhere around POS_ACTIVE, is THREE_BEAMS
        # As POS_ACTIVE is updated every time the aligner is moved, it's typically
        # exactly at POS_ACTIVE.
        # TODO: should have a POS_ACTIVE_RANGE to define the whole region
        if (isNearPosition(current_pos, align_active, self.align.axes) or
            isNearPosition(current_pos, align_alignment, self.align.axes) or
                isNearPosition(current_pos, three_beams, self.align.axes)):
            return THREE_BEAMS

        # Check the current position is near the line between DEACTIVE and ACTIVE
        imaging_progress = self.getMovementProgress(current_pos, align_deactive, align_active)
        if imaging_progress is not None:
            return LOADING_PATH

        # Check the current position is near the line between DEACTIVE and ALIGNMENT
        alignment_path = self.getMovementProgress(current_pos, align_deactive, align_alignment)
        if alignment_path is not None:
            return LOADING_PATH
        # None of the above -> unknown position
        return UNKNOWN
