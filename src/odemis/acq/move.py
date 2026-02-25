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
import functools
import logging
import math
import threading
from abc import abstractmethod
from concurrent.futures import CancelledError, Future
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING
from typing import Dict, Union, Iterable, Optional, Sequence, List, Tuple

import numpy
import scipy

from odemis import model, util
from odemis.model import isasync
from odemis.util import executeAsyncTask
from odemis.util.driver import ATOL_ROTATION_POS, isInRange, isNearPosition
from odemis.util.transform import get_rotation_transforms

MAX_SUBMOVE_DURATION = 90  # s

UNKNOWN, LOADING, IMAGING, ALIGNMENT, COATING, LOADING_PATH, MILLING, SEM_IMAGING, \
    FM_IMAGING, GRID_1, GRID_2, THREE_BEAMS, FIB_IMAGING = -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
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
    THREE_BEAMS: "THREE BEAMS",
    FIB_IMAGING: "FIB_IMAGING"
}

RTOL_PROGRESS = 0.3
# Compensation factor for a rotational move to take the same amount of time as a linear move
ROT_DIST_SCALING_FACTOR = 0.06  # m/rad, 1° ~ 1mm
SAFETY_MARGIN_5DOF = 100e-6  # m
SAFETY_MARGIN_3DOF = 200e-6  # m

# The possible axes for linear and rotational movements
LINEAR_AXES = {'x', 'y', 'z', 'm'}
ROTATION_AXES = {'rx', 'ry', 'rz', 'rm'}

# Tolerance for the difference between the current position and the target position
# these should only be used for TFS1MeteorPostureManager _transformFromSEMToMeteor / _transformFromMeteorToSEM
ATOL_ROTATION_TRANSFORM = 0.04  # rad ~2.5 deg
ATOL_LINEAR_TRANSFORM = 5e-6  # 5 um

# roles that are affected by sample stage transformation
COMPS_AFFECTED_ROLES = ["ccd", "e-beam", "ion-beam"]

# fib column tilts, relative to sem column
TFS_FIB_COLUMN_TILT = math.radians(52)
TESCAN_FIB_COLUMN_TILT = math.radians(55)
ZEISS_FIB_COLUMN_TILT = math.radians(54)

# These values might differ per system and would then require a configuration option per system.
# Hardcoded for now. Note that these values correspond to the milling angle, and not the actual stage tilt.
MILLING_RANGE = (math.radians(5), math.radians(30))

def filter_dict(keys: set, d: Dict[str, float]) -> Dict[str, float]:
    """
    Filter a dictionary to only keep the given keys
    :param keys: keys to keep
    :param d: complete dict
    :return: filtered dict
    """
    return {key: d[key] for key in keys if key in d}


class MicroscopePostureManager:
    def __new__(cls, microscope):
        role = microscope.role
        if role == "meteor":
            stage = model.getComponent(role='stage-bare')
            stage_md = stage.getMetadata()
            md_calib = stage_md.get(model.MD_CALIB, None)
            # Check the version in MD_CALIB, defaults to tfs_1
            stage_version = md_calib.get("version", "tfs_1") if md_calib else "tfs_1"
            if stage_version == "zeiss_1":
                return super().__new__(MeteorZeiss1PostureManager)
            elif stage_version == "tfs_1":
                return super().__new__(MeteorTFS1PostureManager)
            elif stage_version == "tfs_3":
                return super().__new__(MeteorTFS3PostureManager)
            elif stage_version == "tescan_1":
                return super().__new__(MeteorTescan1PostureManager)
            elif stage_version == "jeol_1":
                return super().__new__(MeteorJeol1PostureManager)
            else:
                raise ValueError(f"Stage version {stage_version} is not supported")
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
        lin_axes = axes & LINEAR_AXES  # only the axes found on both points
        rot_axes = axes & ROTATION_AXES  # only the axes found on both points
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
            logging.debug(f"required {required_keys} md_calib {calibrated_md.keys()}")
            missing_keys = required_keys - calibrated_md.keys()
            raise ValueError(f"Stage metadata {model.MD_CALIB} is missing the following required keys: {missing_keys}.")

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

    def _update_posture(self, position: Dict[str, float]):
        """
        Update the current posture of the microscope
        """
        self.current_posture.value = self.getCurrentPostureLabel(position)


class MeteorPostureManager(MicroscopePostureManager):
    def __init__(self, microscope):
        # Load components
        self.stage = model.getComponent(role='stage-bare')
        self.focus = model.getComponent(role='focus')
        # set linear axes and rotational axes used
        self.axes = self.stage.axes
        self.linear_axes = set(key for key in self.axes.keys() if key in LINEAR_AXES)
        self.rotational_axes = set(key for key in self.axes.keys() if key in ROTATION_AXES)
        # required keys that must be present in the stage metadata
        self.required_keys = {
            model.MD_FAV_POS_DEACTIVE, model.MD_FAV_SEM_POS_ACTIVE, model.MD_FAV_FM_POS_ACTIVE,
            model.MD_SAMPLE_CENTERS}
        # Supporting parameter to convert between sample and stage positions
        self._transforms: Dict[int, numpy.ndarray] = {}  # transforms (to-sample-stage)
        self._inv_transforms: Dict[int, numpy.ndarray] = {}  # inverse transforms (from-sample-stage)
        self._metadata = {}
        self._axes_dep = {}  # axes dependencies between different planes

        # Assumed SEM scan rotation if it cannot be read from the hardware (e-beam)
        # Used for displaying the features and moving the stage in SEM imaging posture in the right orientation.
        self._default_scan_rotation = 0  # rad

        # pre-tilt is required for milling posture, but not all systems have it
        stage_md = self.stage.getMetadata()
        md_calib = stage_md.get(model.MD_CALIB, {})
        self.pre_tilt = md_calib.get(model.MD_SAMPLE_PRE_TILT, None)
        self.fib_column_tilt = TFS_FIB_COLUMN_TILT
        # use_linked_sem_focus_compensation: when True, the SEM focus is restored to the eucentric
        # focus when moving the stage. This is done on TFS systems to compensate
        # for the SEM focus changing when the stage is moved in Z (due to stage linking).
        # In this case, MD_CALIB["SEM-Eucentric-Focus"] specifies the fixed focus position to use.
        self.use_linked_sem_focus_compensation: bool = md_calib.get("use_linked_sem_focus_compensation", False)

        # Upgrade path MD_FAV_MILL_POS_ACTIVE["rx"] -> ["mill_angle"]
        # Can be deleted once all installations microscope files have been updated.
        # In Odemis v3.7, MD_FAV_MILL_POS_ACTIVE used the "rx" key to refer to the milling angle.
        # From Odemis v3.8 onwards, the "mill_angle" key is used instead.
        # As now, the "rx" angle is not used, we know the precense of a "rx" key indicates it's a
        # legacy config. So we automatically convert it here, and it will stay as-is for as long as the
        # back-end runs.
        if model.MD_FAV_MILL_POS_ACTIVE in stage_md:
            mill_md = stage_md[model.MD_FAV_MILL_POS_ACTIVE]
            if "rx" in mill_md:
                if "mill_angle" in mill_md:
                    raise ValueError("stage-bare metadata FAV_MILL_POS_ACTIVE contains both 'rx' and "
                                     "'mill_angle' keys. Only 'mill_angle' should be provided.")
                logging.info("Upgrading stage metadata %s: converting 'rx' to 'mill_angle'", model.MD_FAV_MILL_POS_ACTIVE)
                mill_md["mill_angle"] = mill_md.pop("rx")
                self.stage.updateMetadata({model.MD_FAV_MILL_POS_ACTIVE: mill_md})

            milling_angle = mill_md["mill_angle"]
        else:
            milling_angle = MILLING_RANGE[0]

        self.milling_angle = model.FloatContinuous(
            milling_angle, (MILLING_RANGE[0], MILLING_RANGE[1]), unit="rad", setter=self._set_milling_angle
        )

        # current posture va
        self.current_posture = model.VigilantAttribute(UNKNOWN)
        self.stage.position.subscribe(self._update_posture, init=True)

        # Supported postures for sample stage (can be extended by the subclass)
        self.postures = (SEM_IMAGING, FM_IMAGING)

        # set the transforms between different postures
        self._posture_transforms = {
            FM_IMAGING: {
                SEM_IMAGING: self._transformFromMeteorToSEM,
                MILLING: self._transformFromMeteorToMilling,
                FIB_IMAGING: self._transformFromMeteorToFIB,
            },
            SEM_IMAGING: {
                FM_IMAGING: self._transformFromSEMToMeteor,
                MILLING: self._transformFromSEMToMilling,
                FIB_IMAGING: self._transformFromSEMToFIB,
            },
            MILLING: {
                SEM_IMAGING: self._transformFromMillingToSEM,
                FM_IMAGING: self._transformFromMillingToFM,
                # milling position can be dynamically updated, so we need to support this recalculation
                MILLING: self._transformFromSEMToMilling,
                FIB_IMAGING: self._transformFromMillingToFIB,
            },
            FIB_IMAGING: {
                SEM_IMAGING: self._transformFromFIBToSEM,
                FM_IMAGING: self._transformFromFIBToMeteor,
                MILLING: self._transformFromFIBToMilling,
            },
            UNKNOWN: {
                UNKNOWN: lambda x: x
         }
        }

    def create_sample_stage(self):
        self.sample_stage = SampleStage(name="Sample Stage",
                                        role="stage",
                                        stage_bare=self.stage,
                                        posture_manager=self)

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
            if self.at_fib_posture(pos, stage_md):
                return FIB_IMAGING
            if self.at_milling_posture(pos, stage_md):
                return MILLING
            return SEM_IMAGING
        # None of the above -> unknown position
        return UNKNOWN

    def getCurrentGridLabel(self) -> Optional[int]:
        """
        Detects which grid on the sample shuttle of meteor being viewed
        :return: (GRID_* or None) the guessed grid. If current posture doesn't allow to distinguish,
        for instance because it's in LOADING posture, None is returned.
        """
        current_pos = self.stage.position.value
        current_posture = self.getCurrentPostureLabel(current_pos)
        if current_posture not in self.postures:
            logging.warning("Cannot detect current grid in posture %s",
                            POSITION_NAMES[current_posture])
            return None

        stage_md = self.stage.getMetadata()

        # Grid positions are defined in the stage bare coordinates, on the SEM_IMAGING posture
        # They only contain the linear axes (x, y, z, m).
        # The rotation axes are defined on MD_FAV_SEM_POS_ACTIVE.
        sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
        sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
        sem_grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]]
        sem_grid2_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])

        try:
            grid1_pos = self.to_posture(sem_grid1_pos, current_posture)
            grid2_pos = self.to_posture(sem_grid2_pos, current_posture)
        except ValueError as ex:
            logging.warning("Cannot detect current grid in posture %s: %s",
                            POSITION_NAMES[current_posture], ex)
            return None

        distance_to_grid1 = self._getDistance(current_pos, grid1_pos)
        distance_to_grid2 = self._getDistance(current_pos, grid2_pos)

        return GRID_1 if distance_to_grid2 > distance_to_grid1 else GRID_2

    def at_milling_posture(self, pos: Dict[str, float], stage_md: Dict[str, float]) -> bool:
        """Milling posture is not required for all meteor systems, so we need to
        first check it's available
        :param pos the stage position
        :param stage_md the stage metadata
        :param return True if the stage is at the milling posture, False if not (or not available)"""
        if model.MD_FAV_MILL_POS_ACTIVE in stage_md:
            stage_milling = self.get_posture_orientation(MILLING)
            if isNearPosition(pos,
                            stage_milling,
                            self.rotational_axes,
                            atol_rotation=math.radians(3)):
                return True
        return False

    def at_fib_posture(self, pos: Dict[str, float], stage_md: Dict[str, float]) -> bool:
        """FIB posture is not required for all meteor systems, so we need to
        first check it's available
        :param pos the stage position
        :param stage_md the stage metadata
        :param return True if the stage is at the fib posture, False if not (or not available)"""
        if model.MD_FAV_FIB_POS_ACTIVE in stage_md:
            stage_fib = self.get_posture_orientation(FIB_IMAGING)
            if isNearPosition(pos,
                            stage_fib,
                            self.rotational_axes,
                            atol_rotation=math.radians(3)):
                return True
        return False

    def get_posture_orientation(self, posture: int) -> Dict[str, float]:
        """Get the orientation of the stage for the given posture
        :param posture: the posture to get the orientation for
        :return: a dict with the orientation of the stage for the given posture"""
        stage_md = self.stage.getMetadata()
        if posture == SEM_IMAGING:
            return stage_md[model.MD_FAV_SEM_POS_ACTIVE]
        elif posture == FM_IMAGING:
            return stage_md[model.MD_FAV_FM_POS_ACTIVE]
        elif posture == LOADING:
            return stage_md[model.MD_FAV_POS_DEACTIVE]
        elif posture == FIB_IMAGING:
            return stage_md[model.MD_FAV_FIB_POS_ACTIVE]
        elif posture == MILLING:
            md = stage_md[model.MD_FAV_MILL_POS_ACTIVE]
            rx = calculate_stage_tilt_from_milling_angle(milling_angle=md["mill_angle"],
                                                        pre_tilt=self.pre_tilt,
                                                        column_tilt=self.fib_column_tilt)
            return {"rx": rx, "rz": md["rz"]}
        else:
            raise KeyError(f"posture {POSITION_NAMES.get(posture, posture)} not supported for orientation retrieval")

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        target_pos_lbl (int): a label representing a position (SEM_IMAGING, FM_IMAGING, GRID_1 or GRID_2)
        :return: (dict str->float) the target position of the stage
        :raises ValueError: if the target position is not supported
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

    def _set_milling_angle(self, angle: float) -> float:
        """
        Set the milling angle of the stage
        :param angle: (float) milling angle in radians
        """
        if model.MD_FAV_MILL_POS_ACTIVE not in self.stage.getMetadata():
            logging.warning("Trying to set a milling angle on a system that was not configured for milling")
            return angle
        rotations = {'mill_angle': angle, 'rz': self.stage.getMetadata()[model.MD_FAV_MILL_POS_ACTIVE]["rz"]}
        self.stage.updateMetadata({model.MD_FAV_MILL_POS_ACTIVE: rotations})
        return angle

    def _transformFromMeteorToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area
            to the SEM imaging area.
        :param pos: (dict str->float) the current stage position
        :param stage: (Actuator) the stage component
        :return: (dict str->float) the transformed stage position.
        """
        pass

    def _initialise_transformation(
            self,
            axes: Sequence[str],
            rotation: float = 0,
    ):
        """
        Initializes the transformation parameters that allows conversion between stage-bare and sample plane.
        :param axes: stage axes which are used to calculate transformation parameters
        :param rotation: rotation in radians from sample plane to stage
        """
        self._axes_dep = {"x": axes[0], "y": axes[1]}  # TODO: Should be called y, z... or even better: also take x as first axis
        self._update_conversion(rotation)
        self._initialise_offset()

    def _update_conversion(self,
                           pre_tilt: float = 0,
                           ) -> None:
        """
        Computes transformation parameters based on the given metadata to allow conversion
        stage-bare and sample plane.
        This version assumes the FM imaging posture is 180° rotated in rx compared to the SEM imaging posture.
        NOTE: transformations are defined as sample stage -> stage bare
        the inverse transformation is used for stage bare -> sample stage
        :param pre_tilt: rotation in radians from sample plane to stage (pre-tilt)
        """
        tf_id = numpy.eye(3)  # identity transform for the UNKNOWN posture, to make it clear it's not handled
        tf_rz_180, _ = get_rotation_transforms(rz=math.pi)

        # Note that the tilt of the stage doesn't matter here, because the XYZ of the stage is
        # "after" the tilt axis.

        # Compensate for the scan rotation (around Z)
        sr = self._get_scan_rotation()   # Fails if ion-beam and e-beam have different scan rotations
        # Make sure the SEM image is shown without rotation in the UI. This works by setting
        # MD_ROTATION_COR as the same value as MD_ROTATION (automatically set on the image).
        self._set_scanner_rotation_cor(sr)
        tf_sr, _ = get_rotation_transforms(rz=sr)

        # We assume that FM & SEM are rotate by 180°. Let's warn if that's not the case.
        try:
            stage_md = self.stage.getMetadata()
            rz_fm = stage_md[model.MD_FAV_FM_POS_ACTIVE]["rz"]
            rz_sem = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rz"]
            rot_fm_sem = rz_sem - rz_fm
            if not util.rot_almost_equal(rot_fm_sem, math.pi, atol=math.radians(5)):
                logging.warning("FM and SEM imaging postures are expected to be 180° rotated in rz, but got %s°.",
                                math.degrees(rot_fm_sem))
        except Exception:
            logging.exception("Failed to check the rotation between FM and SEM imaging postures")

        # FM imaging: compensate for the pre-tilt
        tf_tilt, _ = get_rotation_transforms(rx=pre_tilt)
        # Rotate by 180° to compensate for the stage being rotated in rz compared to the SEM imaging posture
        # Also rotate by the scan rotation, as the CCD image is aligned with the SEM image
        tf_fm = tf_tilt @ tf_sr @ tf_rz_180
        tf_fm_inv = numpy.linalg.inv(tf_fm)

        # SEM imaging + milling: stage 180° rotated along rz compared to FM IMAGING + opposite pre-tilt.
        tf_tilt, _ = get_rotation_transforms(rx=-pre_tilt)
        tf_sem = tf_tilt @ tf_sr
        tf_sem_inv = numpy.linalg.inv(tf_sem)

        logging.debug(f"tf_fm: {tf_fm}, tf_sem: {tf_sem}")

        # FIB IMAGING: stage is 180° rotated compared to the SEM IMAGING
        # TODO: test once FIB IMAGING is supported

        tf_fib_im = tf_sem @ tf_rz_180
        tf_fib_im_inv = numpy.linalg.inv(tf_fib_im)

        # From sample-stage to stage-bare
        self._transforms = {
            FM_IMAGING: tf_fm,
            SEM_IMAGING: tf_sem,
            FIB_IMAGING: tf_fib_im,
            MILLING: tf_sem,
            UNKNOWN: tf_id
        }
        # From stage-bare to sample-stage
        self._inv_transforms = {
            FM_IMAGING: tf_fm_inv,
            SEM_IMAGING: tf_sem_inv,
            FIB_IMAGING: tf_fib_im_inv,
            MILLING: tf_sem_inv,
            UNKNOWN: tf_id
        }

    def _initialise_offset(self):
        stage_md = self.stage.getMetadata()

        # Grid positions are defined in the stage bare coordinates, on the SEM_IMAGING posture
        # They only contain the linear axes (x, y, z, m).
        # The rotation axes are defined on MD_FAV_SEM_POS_ACTIVE.
        sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
        sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])

        # Shift (x,y,z) from sample-stage to stage-bare coordinates
        self._offset = {p: numpy.array([0, 0, 0]) for p in self.postures}
        self._offset[UNKNOWN] = numpy.array([0, 0, 0])

        # TODO: update MILLING offset when changing milling angle

        # We define the position in SEM_IMAGING as the reference, it's arbitrary, but using the SEM
        # coordinates system means the positions will look similar to the one on the SEM software,
        # which is handy.
        ref_p1 = numpy.array([sem_grid1_pos["x"],
                              sem_grid1_pos[self._axes_dep["x"]],
                              sem_grid1_pos[self._axes_dep["y"]]])
        for p in self.postures:
            grid1_pos = self.to_posture(sem_grid1_pos, p)
            p1_sample = self.to_sample_stage_from_stage_position(grid1_pos, p)
            p1_sample = numpy.array([p1_sample["x"], p1_sample["y"], p1_sample["z"]])
            self._offset[p] = p1_sample - ref_p1

        logging.debug("Sample stage transformation offsets: %s", self._offset)

    def _get_scan_rotation(self) -> float:
        """
        Get the scan rotation value for SEM/FIB, and ensure they match.
        :return: the scan rotation value in radians
        """
        try:
            ebeam = model.getComponent(role='e-beam')
        except LookupError:
            logging.info("e-beam and/or ion-beam not available, scan rotation assumed to %s°",
                         round(math.degrees(self._default_scan_rotation)))
            return self._default_scan_rotation

        # check if e-beam and ion-beam have the same rotation
        sr = ebeam.rotation.value

        try:
            ion_beam = model.getComponent(role='ion-beam')
        except LookupError:
            logging.info("ion-beam not available, scan rotation assumed to be the same as e-beam: %s°", math.degrees(sr))
            return sr

        ion_sr = ion_beam.rotation.value
        if not numpy.isclose(sr, ion_sr, atol=ATOL_ROTATION_POS):
            raise ValueError(f"The SEM and FIB rotations do not match {sr} != {ion_sr}")

        return sr

    def _set_scanner_rotation_cor(self, rotation: float):
        """
        Set the scanners' MD_ROTATION_COR metadata field to the provided rotation value.
        :param rotation: rotation in radians
        """
        for scanner_name in ["e-beam", "ion-beam"]:
            scanner = None
            try:
                scanner = model.getComponent(role=scanner_name)
            except LookupError:
                pass

            if scanner is not None:
                scanner.updateMetadata({model.MD_ROTATION_COR: rotation})

    def from_sample_stage_to_stage_movement(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Get the stage movement coordinates from the sample stage movement coordinates.
        (sample-stage -> stage-bare, for relative movements)
        :param pos: move in sample-stage coordinates (not all axes are required)
        :return: move in the stage-bare coordinates
        """
        q = numpy.array([pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)])
        posture = self.current_posture.value
        pinv = self._transforms[posture] @ q

        ppos = {"x": pinv[0], self._axes_dep["x"]: pinv[1], self._axes_dep["y"]: pinv[2]}
        return ppos

    def from_sample_stage_to_stage_position(self, pos: Dict[str, float], posture: Optional[int] = None) -> Dict[str, float]:
        """
        Get stage position coordinates from sample stage coordinates (sample-stage -> stage-bare).
        :param pos: position in the sample-stage coordinates
        :param posture: The posture to use for the transformation. If None, uses the current posture.
            Valid values include SEM_IMAGING, FM_IMAGING, MILLING, FIB_IMAGING, etc.
        :return: position in the stage-bare coordinates
        """
        q = numpy.array([pos["x"], pos["y"], pos["z"]])
        if posture is None:
            posture = self.current_posture.value
        q += self._offset[posture]
        pinv = self._transforms[posture] @ q

        # add orientation (rx, rz)
        orientation = self.get_posture_orientation(posture)

        ppos = {"x": pinv[0], self._axes_dep["x"]: pinv[1], self._axes_dep["y"]: pinv[2]}
        ppos.update(orientation)
        return ppos

    def to_sample_stage_from_stage_position(self, pos: Dict[str, float], posture: Optional[int] = None) -> Dict[str, float]:
        """
        Get sample stage coordinates from stage coordinates. (stage-bare -> sample stage)
        :param pos: position in the stage-bare coordinates
        :param posture: The posture to use for the transformation. If None, uses the current posture.
            Valid values include SEM_IMAGING, FM_IMAGING, MILLING, FIB_IMAGING, etc.
        :return: position in the sample-stage coordinates
        """
        p = numpy.array([pos["x"], pos[self._axes_dep["x"]], pos[self._axes_dep["y"]]])
        if posture is None:
            posture = self.current_posture.value
        q = self._inv_transforms[posture] @ p
        q -= self._offset[posture]

        qpos = {"x": q[0], "y": q[1], "z": q[2]}
        return qpos

    def to_posture(self, pos: Dict[str, float], posture: int) -> Dict[str, float]:
        """Convert a stage-bare position to a position in the target posture.
        :param pos: stage position in the stage-bare coordinates
        :param posture: (int) the target posture of the stage
        :return: stage-bare position in the target posture"""

        position_posture = self.getCurrentPostureLabel(pos)

        logging.info(f"Position Posture: {POSITION_NAMES[position_posture]}, Target Posture: {POSITION_NAMES[posture]}")

        # The milling angle can change, so we should handle the MILLING --> MILLING posture switch differently.
        if posture != MILLING and position_posture == posture:
            return pos

        # validate the transformation
        if position_posture not in self._posture_transforms:
            raise ValueError(f"Position posture {position_posture} not supported")

        if posture not in self._posture_transforms[position_posture]:
            raise ValueError(f"Posture {posture} not supported for position posture {position_posture}")

        tf = self._posture_transforms[position_posture][posture]

        return tf(pos)

    def _transformFromSEMToMilling(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from sem imaging to milling position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        # the only difference is the tilt axes assuming eucentricity
        position = pos.copy()
        position.update(self.get_posture_orientation(MILLING))

        return position

    def _transformFromMillingToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from milling to sem imaging position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        # the only difference is the tilt axes assuming eucentricity
        position = pos.copy()
        position.update(self.get_posture_orientation(SEM_IMAGING))

        return position

    def _transformFromMeteorToMilling(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from fm imaging to milling position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        # simple chain of fm->sem->milling
        sem_pos = self._transformFromMeteorToSEM(pos)
        return self._transformFromSEMToMilling(sem_pos)

    def _transformFromMillingToFM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from milling to fm imaging position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        # simple chain of milling->sem->fm
        sem_pos = self._transformFromMillingToSEM(pos)
        return self._transformFromSEMToMeteor(sem_pos)

    def _transformFromSEMToFIB(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from SEM imaging to FIB imaging position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        raise NotImplementedError()

    def _transformFromFIBToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from FIB imaging to SEM imaging position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        raise NotImplementedError()

    def _transformFromMeteorToFIB(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from meteor to FIB imaging position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        raise NotImplementedError()

    def _transformFromFIBToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from FIB imaging to meteor position
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        raise NotImplementedError()

    def _transformFromMillingToFIB(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from milling to fib imaging position"
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        # simple chain of milling->sem->fib
        sem_pos = self._transformFromMillingToSEM(pos)
        return self._transformFromSEMToFIB(sem_pos)

    def _transformFromFIBToMilling(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the stage position from fib imaging to milling position"
        :param pos: (dict str->float) the current stage position
        :return: (dict str->float) the transformed stage position.
        """
        # simple chain of fib->sem->milling
        sem_pos = self._transformFromFIBToSEM(pos)
        return self._transformFromSEMToMilling(sem_pos)


# Minimum stage bare z height required to switch between FM and SEM postures
Z_LOW = 0.028  # m, safe value provided by TFS on a Hydra Bio CX system which should also be compatible on other systems


class MeteorTFS1PostureManager(MeteorPostureManager):
    def __init__(self, microscope):
        super().__init__(microscope)
        # Check required metadata used during switching
        self.required_keys.add(model.MD_POS_COR)
        self.check_stage_metadata(required_keys=self.required_keys)
        if not {"x", "y", "rz", "rx"}.issubset(self.stage.axes):
            raise KeyError("The stage misses 'x', 'y', 'rx' or 'rz' axes")

        # On TFS, the default scan orientation makes the FIB image in MILLING posture display the
        # closest points at the top, which is not intuitive. So typically, the users apply a scan
        # rotation of 180° (on e-beam and ion-beam)
        self._default_scan_rotation = math.pi  # rad

        # forced conversion to sample-stage axes
        comp = model.getComponent(name="Linked YZ")
        self.pre_tilt = comp.getMetadata()[model.MD_ROTATION_COR]
        self.postures = [SEM_IMAGING, FM_IMAGING]
        self._initialise_transformation(axes=["y", "z"], rotation=self.pre_tilt)
        self.create_sample_stage()

    def getTargetPosition(self, target_pos_lbl: int) -> Dict[str, float]:
        """
        Returns the position that the stage would go to.
        :param target_pos_lbl: (int) a label representing a position (SEM_IMAGING, FM_IMAGING, GRID_1 or GRID_2)
        :return: (dict str->float) the end position of the stage
        :raises ValueError: if the target position is not supported
        """
        stage_md = self.stage.getMetadata()
        stage_position = self.stage.position.value
        current_posture = self.getCurrentPostureLabel(stage_position)
        end_pos = None

        if target_pos_lbl in (GRID_1, GRID_2):
            # Go to grid center: only works if in a supported sample stage posture (ie, not LOADING, UNKNOWN...)
            if current_posture not in self.postures:
                raise ValueError(f"Cannot go to grid position from current posture "
                                 f"{POSITION_NAMES.get(current_posture, current_posture)}")
            sem_grid_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[target_pos_lbl]]
            sem_grid_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
            end_pos = self.to_posture(pos=sem_grid_pos, posture=current_posture)
        elif target_pos_lbl == LOADING:  # Always accept going to loading position from any posture
            end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
        elif current_posture == LOADING:
            # If at loading, go to GRID 1 by default
            # Note: all grid positions need to have rx, rz axes to be able to transform
            # this is not the case by default, and needs to be added in the metadata
            sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
            sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
            end_pos = self.to_posture(pos=sem_grid1_pos, posture=target_pos_lbl)
        elif current_posture == FM_IMAGING:
            if target_pos_lbl in [SEM_IMAGING, MILLING, FIB_IMAGING]:
                # Revert to the same Z height as before going to FM (if known)
                deactive_fm_position = stage_md.get(model.MD_FM_POS_SAMPLE_DEACTIVE)
                if deactive_fm_position and "z" in deactive_fm_position:
                    sample_stage_pos = self.to_sample_stage_from_stage_position(stage_position, posture=FM_IMAGING)
                    sample_stage_pos["z"] = deactive_fm_position["z"]
                    stage_position = self.from_sample_stage_to_stage_position(sample_stage_pos, posture=FM_IMAGING)
                end_pos = self.to_posture(pos=stage_position, posture=target_pos_lbl)
        elif current_posture in (SEM_IMAGING, MILLING, FIB_IMAGING):
            if target_pos_lbl in self.postures:
                end_pos = self.to_posture(pos=stage_position, posture=target_pos_lbl)

        if end_pos is None:
            raise ValueError("Unknown target position {} when in {}".format(
                POSITION_NAMES.get(target_pos_lbl, target_pos_lbl),
                POSITION_NAMES.get(current_posture, current_posture))
            )

        return end_pos

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
        if not ("rz" in pos and "rz" in fm_pos_active):
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
        if not ("rz" in pos and "rz" in sem_pos_active):
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

            # get the metadata
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
            current_pos = self.stage.position.value
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
            elif target in (LOADING, SEM_IMAGING, FM_IMAGING, MILLING, FIB_IMAGING):
                # Park the focuser for safety
                if not isNearPosition(self.focus.position.value, focus_deactive, self.focus.axes):
                    sub_moves.append((self.focus, focus_deactive))

                if (type(self) == MeteorTFS1PostureManager):
                    if current_label == SEM_IMAGING and target == FM_IMAGING:
                        # NOTE: with TFS1, no distinction was made between SEM and MILL positions, and these
                        # were dynamically updated based on the current SEM position when switching to FM,
                        # and used to restore the same position when switching back from FM -> SEM.
                        # From TFS3, there is a separate MILLING position, so the SEM position has really a
                        # fixed rotation and tilt.
                        self.stage.updateMetadata({model.MD_FAV_SEM_POS_ACTIVE: {'rx': current_pos['rx'],
                                                                                 'rz': current_pos['rz']}})
                    # Move translation axes, then rotational ones
                    sub_moves.append((self.stage, filter_dict({'x', 'y', 'z'}, target_pos)))
                    sub_moves.append((self.stage, filter_dict({'rx', 'rz'}, target_pos)))

                elif (type(self) == MeteorTFS3PostureManager):
                    stage_md = self.stage.getMetadata()
                    md_calib = stage_md[model.MD_CALIB]
                    z_low = md_calib.get("z_low", Z_LOW)  # safe z to achieve before switching to SEM posture

                    if (current_label in [SEM_IMAGING, MILLING, FIB_IMAGING]
                      and target == FM_IMAGING
                     ):
                        # Store the Z position, for recovery when going back to SEM.
                        # We record it by computing its projection in FM sample coordinates, without the fixed plane
                        # correction. As Z is the same for SEM, Milling or FIB, and it's fine to just use SEM to Meteor
                        # for all occasions.
                        target_pos_unfixed = self._transformFromSEMToMeteor(current_pos, fix_fm_plane=False)
                        sample_stage_pos = self.to_sample_stage_from_stage_position(target_pos_unfixed, posture=FM_IMAGING)
                        sample_stage_pos = {"z": sample_stage_pos["z"]}  # Drop x and y, to make clear only z is used
                        self.stage.updateMetadata({model.MD_FM_POS_SAMPLE_DEACTIVE: sample_stage_pos})
                        # Stage switching based on Hydra Bio TFS assessment
                        # move lower than 28 mm (z_low)
                        # move r and t
                        # move x and y
                        # move all to final position (outside if else block to take other switching postures combinations)
                        sub_moves.append((self.stage, {"z": z_low}))
                        sub_moves.append((self.stage, filter_dict({'rx', 'rz'}, target_pos)))
                        sub_moves.append((self.stage, filter_dict({'x', 'y'}, target_pos)))

                    elif (current_label == FM_IMAGING
                        and target in [SEM_IMAGING, MILLING, FIB_IMAGING]):
                        # Stage switching based on Hydra Bio TFS assessment
                        # If current z is more than safe value, move it to safe z value else skip it.
                        # move x and y
                        # move all to final position (outside if else block to take other switching postures combinations)
                        if current_pos["z"] > z_low:
                            sub_moves.append((self.stage, {"z": z_low}))
                        sub_moves.append((self.stage, filter_dict({'x', 'y'}, target_pos)))

                    sub_moves.append((self.stage, target_pos))

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


class MeteorTFS3PostureManager(MeteorTFS1PostureManager):
    def __init__(self, microscope):
        MeteorPostureManager.__init__(self, microscope)
        # Check required metadata used during switching
        self.required_keys.add(model.MD_CALIB)
        self.check_stage_metadata(required_keys=self.required_keys)
        required_calib = {model.MD_SAMPLE_PRE_TILT, "dx", "dy"}
        # Note: when use_linked_sem_focus_compensation is set, "SEM-Eucentric-Focus" can be defined,
        # but it has a default value, so it is never required.
        self.check_calib_data(required_calib)
        if not {"x", "y", "rz", "rx"}.issubset(self.stage.axes):
            raise KeyError("The stage misses 'x', 'y', 'rx' or 'rz' axes")

        # On TFS, the default scan orientation makes the FIB image in MILLING posture display the
        # closest points at the top, which is not intuitive. So typically, the users apply a scan
        # rotation of 180° (on e-beam and ion-beam)
        self._default_scan_rotation = math.pi  # rad

        self.postures = [SEM_IMAGING, FM_IMAGING]
        # These positions are "optional", and only used with Odemis advanced
        stage_md = self.stage.getMetadata()
        if model.MD_FAV_MILL_POS_ACTIVE in stage_md:
            self.postures.append(MILLING)
        if model.MD_FAV_FIB_POS_ACTIVE in stage_md:
            self.postures.append(FIB_IMAGING)

        # Hack warning: during initialization of the transforms, the offset is computed by calling
        # to_posture(), which calls _transformFromSEMToMeteor() for the SEM->FM transform. If
        # MD_FM_POS_SAMPLE_ACTIVE is already present, the fix_fm_plane logic is triggered, which
        # uses the sample plane transformations, which aren't fully initialized yet. This can cause
        # an incorrect offset computation, and eventually an incorrect movement to FM.
        # To avoid this, we temporarily set to_posture() to not use the fix_fm_plane logic.
        _transformFromSEMToMeteorNoFixFM = functools.partial(self._transformFromSEMToMeteor, fix_fm_plane=False)
        self._posture_transforms[SEM_IMAGING][FM_IMAGING] = _transformFromSEMToMeteorNoFixFM
        self._initialise_transformation(axes=["y", "z"], rotation=self.pre_tilt)
        self.create_sample_stage()
        # Reset to the standard function (with fixed FM plane)
        self._posture_transforms[SEM_IMAGING][FM_IMAGING] = self._transformFromSEMToMeteor

        # If there is no known fixed sample z for FM, compute it here and store it for later use
        if not stage_md.get(model.MD_FM_POS_SAMPLE_ACTIVE):
            # For FM imaging, fix the imaging plane so that all features always lie on the same plane.
            # Pick a sane default for the sample stage z using grid 1.
            sem_grid1_pos = dict(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]])
            sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
            sem_grid1_pos_fm = self._transformFromSEMToMeteor(sem_grid1_pos, fix_fm_plane=False)
            fixed_fm_sample = self.to_sample_stage_from_stage_position(sem_grid1_pos_fm, posture=FM_IMAGING)
            fixed_fm_sample = {"z": fixed_fm_sample["z"]}  # Drop x and y, to make clear only z is used
            self.stage.updateMetadata({model.MD_FM_POS_SAMPLE_ACTIVE: fixed_fm_sample})

    def _transformFromSEMToMeteor(self, pos: Dict[str, float], fix_fm_plane: bool = True) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the meteor/FM imaging area.
        :param pos: the initial stage position.
        :param fix_fm_plane: If True, maintains a consistent FM imaging plane by keeping the z-coordinate
            in sample-stage space constant. It uses the z value stored in MD_FM_POS_SAMPLE_ACTIVE.
            If False, performs direct transformation without z-adjustment.
        :return: the transformed position.
        """
        # NOTE: this transform now always rotates around the z axis (180deg)
        # for pure translation, use FIB -> FM transform
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()
        md_calib = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]
        fm_sample_pos_active = stage_md.get(model.MD_FM_POS_SAMPLE_ACTIVE)

        # check if the stage positions have rz axes
        if not ("rz" in pos and "rz" in fm_pos_active):
            raise ValueError(f"The stage position does not have rz axis pos={pos}, fm_pos_active={fm_pos_active}")

        transformed_pos["x"] = md_calib["dx"] - pos["x"]
        transformed_pos["y"] = md_calib["dy"] - pos["y"]
        transformed_pos.update(fm_pos_active)

        if fix_fm_plane and fm_sample_pos_active and "z" in fm_sample_pos_active:
            sample_stage_pos = self.to_sample_stage_from_stage_position(transformed_pos, posture=FM_IMAGING)
            sample_stage_pos["z"] = fm_sample_pos_active["z"]
            transformed_pos = self.from_sample_stage_to_stage_position(sample_stage_pos, posture=FM_IMAGING)

        return transformed_pos

    def _transformFromMeteorToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area
        to the SEM imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed stage position.
        """
        # NOTE: this transform now always rotates around the z axis (180deg)
        # for pure translation, use FM -> FIB transform
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()
        md_calib = stage_md[model.MD_CALIB]
        sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

        # check if the stage positions have rz axes
        if not ("rz" in pos and "rz" in sem_pos_active):
            raise ValueError(f"The stage position does not have rz axis. pos={pos}, sem_pos_active={sem_pos_active}")

        transformed_pos["x"] = md_calib["dx"] - pos["x"]
        transformed_pos["y"] = md_calib["dy"] - pos["y"]
        transformed_pos.update(sem_pos_active)

        return transformed_pos

    def _transformFromFIBToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the FIB imaging area to the
        meteor/FM imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed position.
        """
        # TODO: check this is correct
        return self._transformFromSEMToMeteor(self._transformFromFIBToSEM(pos))

    def _transformFromMeteorToFIB(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area to the FIB imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed stage position.
        """
        # TODO: check this is correct
        return self._transformFromSEMToFIB(self._transformFromMeteorToSEM(pos))

    def _transformFromSEMToFIB(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from SEM imaging to the FIB imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed stage position.
        """
        # NOTE: This should be a compucentric rotation. need to translate around rotation centre
        transformed_pos = pos.copy()
        fib_pos_active = self.get_posture_orientation(FIB_IMAGING)
        transformed_pos.update(fib_pos_active)

        # invert x,y for compucentric rotation (rotation centered at 0,0)
        transformed_pos["x"] = -transformed_pos["x"]
        transformed_pos["y"] = -transformed_pos["y"]
        return transformed_pos

    def _transformFromFIBToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from FIB imaging to the SEM imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed stage position.
        """
        # NOTE: This should be a compucentric rotation. need to translate around rotation centre
        transformed_pos = pos.copy()
        fib_pos_active = self.get_posture_orientation(SEM_IMAGING)
        transformed_pos.update(fib_pos_active)

        # invert x,y for compucentric rotation (rotation centered at 0,0)
        transformed_pos["x"] = -transformed_pos["x"]
        transformed_pos["y"] = -transformed_pos["y"]

        return transformed_pos

    def _transformFromChamberToStage(self, shift: Dict[str, float]) -> Dict[str, float]:
        """Transform the shift from chamber to stage bare coordinates.
        Used for moving the stage vertically in the chamber.
        :param shift: The shift to be transformed
        :return: The transformed shift
        """
        # get the shift values
        dx = shift.get("x", 0)
        pdz = shift.get("z", 0)

        # calculate axis components
        theta = self.stage.position.value["rx"] # tilt, in radians
        dy = pdz * math.sin(theta)
        dz = pdz / math.cos(theta)
        vshift = {"x": dx, "y": dy, "z": dz}
        logging.debug(f"transforming from chamber to stage-bare, vshift: {vshift}, theta: {theta}, initial shift: {shift}")
        return vshift


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
        self.fib_column_tilt = ZEISS_FIB_COLUMN_TILT

        self._default_scan_rotation = math.pi  # rad

        if self.pre_tilt is None: # pre-tilt not available in the stage calib metadata
            # First version of the microscope file had it hard-coded on the Linked YM wrapper component
            comp = model.getComponent(name="Linked YM")
            self.pre_tilt = comp.getMetadata()[model.MD_ROTATION_COR]

        self.postures = [SEM_IMAGING, FM_IMAGING]
        # Automatic conversion to sample-stage axes
        self._initialise_transformation(axes=["y", "m"], rotation=self.pre_tilt)
        self.create_sample_stage()

    def from_sample_stage_to_stage_position(self, pos: Dict[str, float]) -> Dict[str, float]:
        new_pos = super().from_sample_stage_to_stage_position(pos)
        # No knowledge about "z", so just copy it. As it's the same posture, it should be correct
        new_pos["z"] = self.stage.position.value["z"]
        return new_pos

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
        try:
            rx_sem = pos["rx"]  # Current tilt angle (can differ per point of interest)
            z = pos["z"]
        except KeyError:
            raise KeyError(f"The stage position does not have rx or z axis. pos={pos}")

        rx_fm = fm_pos_active["rx"]  # Calibrated tilt angle, for imaging perpendicular to objective
        b_0 = z - calibrated_values["z_ct"]
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
                    sub_moves.append((stage, filter_dict({'x', 'y', 'rm'}, target_pos)))
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

# Extra Z margin, to be certain no intermediary move can cause a collision during posture switch
TESCAN_SAFETY_Z_MARGIN = 7e-3  # m


class MeteorTescan1PostureManager(MeteorPostureManager):
    def __init__(self, microscope):
        super().__init__(microscope)
        # Check required metadata used during switching
        required_keys_tescan1 = {"x_0", "y_0", "z_ct", "dx", "dy", "b_y"}
        self.required_keys.add(model.MD_CALIB)
        self.check_stage_metadata(self.required_keys)
        self.check_calib_data(required_keys_tescan1)
        if not {"x", "y", "z", "rx", "rz"}.issubset(self.stage.axes):
            missed_axes = {'x', 'y', 'z', 'rx', 'rz'} - self.stage.axes.keys()
            raise KeyError("The stage misses %s axes" % missed_axes)
        self.fib_column_tilt = TESCAN_FIB_COLUMN_TILT

        if self.pre_tilt is None: # pre-tilt not available in the stage calib metadata
            # First version of the microscope file had it hard-coded on the Linked YM wrapper component
            comp = model.getComponent(name="Linked YZ")
            self.pre_tilt = comp.getMetadata()[model.MD_ROTATION_COR]

        # Get shutter VA if available on the e-beam scanner
        self.shutter = None
        try:
            scanner = model.getComponent(role='e-beam')
            self.shutter = getattr(scanner, 'shutter', None)
        except LookupError:
            pass

        # Y/Z axes are not perpendicular. The angle depends on rx (if rx==0°, they are perpendicular)
        # To compensate for this, we use shear and scale.
        stage_md = self.stage.getMetadata()

        # Automatic conversion to sample-stage axes
        self.postures = [SEM_IMAGING, FM_IMAGING]
        if model.MD_FAV_MILL_POS_ACTIVE in stage_md:
            self.postures.append(MILLING)

        self.linked_axes = ["y", "z"]
        # The setter of self.milling_angle sets the milling angle metadata, which is used to update the transformation parameters.
        self.milling_angle.subscribe(self._initialise_transformation, init=True)
        self.create_sample_stage()

        # Update the posture based on the actual metadata
        self._update_posture(self.stage.position.value)

    def _initialise_transformation(self, angle):
        """
        Initializes the transformation parameters that allows conversion between stage-bare and sample plane.
        """
        self._axes_dep = {"x": self.linked_axes[0], "y": self.linked_axes[1]}  # TODO: Should be called y, z... or even better: also take x as first axis
        self._update_conversion(self.pre_tilt)
        self._initialise_offset()

    def _update_conversion(self,
                           pre_tilt: float,
                           ):
        """
        Computes transformation parameters based on the given metadata to allow conversion
        stage-bare and sample plane.
        NOTE: transformations are defined as sample stage -> stage bare
        the inverse transformation is used for stage bare -> sample stage
        :param pre_tilt: pre-tilt in radians, rotation from sample plane to stage
        """
        tf_id = numpy.eye(3)
        tf_reverse = -tf_id  # The Tescan stage convention is opposite of Odemis so inverse the direction of the XYZ axis.

        # Compensate for the scan rotation (around Z)
        sr = self._get_scan_rotation()  # Fails if ion-beam and e-beam have different scan rotations
        self._set_scanner_rotation_cor(sr)  # Makes sure total image rotation is 0
        tf_sr, _ = get_rotation_transforms(rz=-sr)

        # FM imaging
        stage_md = self.stage.getMetadata()
        rx_fm = stage_md[model.MD_FAV_FM_POS_ACTIVE]["rx"]
        tf_tilt = self._get_tilt_transformation(pre_tilt, rx_fm)
        # The stage rz is 180° opposite of the SEM imaging, which compensates for the stage convention to
        # be opposite of Odemis. So no need to "reverse" the XYZ axes.
        # Only the other hand, although the scan rotation has no direct effect on the FM image, in
        # practice, the camera image is also "transposed" to match the SEM image orientation, in the
        # microscope file. So also need to apply the scan rotation here.
        tf_fm = tf_tilt @ tf_sr
        tf_fm_inv = numpy.linalg.inv(tf_fm)

        rx_sem = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rx"]
        # The stage rz is 180° opposite of the FM imaging => -pre_tilt
        tf_tilt = self._get_tilt_transformation(-pre_tilt, rx_sem)
        tf_sem = tf_reverse @ tf_tilt @ tf_sr
        tf_sem_inv = numpy.linalg.inv(tf_sem)

        if model.MD_FAV_MILL_POS_ACTIVE in stage_md:
            mill_pos_active = stage_md[model.MD_FAV_MILL_POS_ACTIVE]
            rx_mill = calculate_stage_tilt_from_milling_angle(milling_angle=mill_pos_active["mill_angle"],
                                                              pre_tilt=self.pre_tilt,
                                                              column_tilt=self.fib_column_tilt)
            # Scan rotation and pre-tilt are the same as in SEM imaging, so can reuse tf_sr
            tf_tilt = self._get_tilt_transformation(-pre_tilt, rx_mill)
            tf_mill = tf_reverse @ tf_tilt @ tf_sr
        else:
            tf_mill = tf_id

        tf_mill_inv = numpy.linalg.inv(tf_mill)
        logging.debug(f"tf_matrix: {tf_fm}, tf_sem: {tf_sem}, tf_mill: {tf_mill}")

        # From sample-stage to stage-bare
        self._transforms = {FM_IMAGING: tf_fm,
                            SEM_IMAGING: tf_sem,
                            MILLING: tf_mill,
                            UNKNOWN: tf_id}
        # From stage-bare to sample-stage
        self._inv_transforms = {FM_IMAGING: tf_fm_inv,
                                SEM_IMAGING: tf_sem_inv,
                                MILLING: tf_mill_inv,
                                UNKNOWN: tf_id}

    def _get_tilt_transformation(self, pre_tilt: float, rx: float) -> numpy.ndarray:
        """
        Computes the transformation matrix based on the given tilt (rx) angle, on the Tescan stage.
        Note that this is special on Tescan because the Z axis is not perpendicular to Y when the
        stage is tilted.
        :param pre_tilt: stage pre-tilt angle (ie, angle between sample and stage XY plane) in radians
        :param rx: stage-bare tilt angle (ie, angle between x and z axes) in radians
        :return: tilt transformation matrix
        """
        tf_pre_tilt, _ = get_rotation_transforms(rx=pre_tilt)
        shear = (-math.tan(rx), 0)
        scale = (1, 1 / math.cos(rx))  # rx is always < 90°, so no division by zero
        # The shear & scale parameters are for the 2nd and 3rd axes (Y and Z) because Z stays always
        # parallel to the e-beam column, while Y changes with the stage tilt.
        shear_matrix_3d = numpy.array([
            [1, 0, 0],         # x-axis remains unaffected
            [0, 1, shear[0]],  # y-axis shear
            [0, shear[1], 1],  # z-axis shear
        ])

        scale_matrix_3d = numpy.array([
            [1, 0, 0],  # x-axis remains unaffected
            [0, scale[0], 0],  # y-axis scale
            [0, 0, scale[1]],  # z-axis scale
        ])

        tf_tilt = scale_matrix_3d @ shear_matrix_3d @ tf_pre_tilt
        return tf_tilt

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
        stage_position = self.stage.position.value
        current_posture = self.getCurrentPostureLabel(stage_position)

        if target_pos_lbl in (GRID_1, GRID_2):
            # Go to grid center: only works if in a supported sample stage posture
            if current_posture not in self.postures:
                raise ValueError(f"Cannot go to grid position from current posture {current_posture}")
            sem_grid_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[target_pos_lbl]]
            sem_grid_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
            end_pos = self.to_posture(pos=sem_grid_pos, posture=current_posture)
        elif target_pos_lbl == LOADING:
            end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
        elif current_posture == LOADING:
            # Go always to GRID 1 center, in the posture requested
            sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
            sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
            end_pos = self.to_posture(pos=sem_grid1_pos, posture=target_pos_lbl)
        elif current_posture in self.postures:
            end_pos = self.to_posture(pos=stage_position, posture=target_pos_lbl)
        else:
            raise ValueError("Unknown target position {} when in {}".format(
                POSITION_NAMES.get(target_pos_lbl, target_pos_lbl),
                POSITION_NAMES.get(current_posture, current_posture))
            )

        return end_pos

    def _transformFromSEMToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
        meteor/FM imaging area.
        :param pos: the current stage position.
        :return: the transformed position.
        """
        if "rx" not in pos:
            raise ValueError(f"The stage-bare position does not have rx axis. pos={pos}")

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
        b_y = calibrated_values["b_y"]
        b_z = (pos["z"] - z_ct) * math.cos(rx_sem) + b_y * math.sin(rx_sem)

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position,
        # at the SEM position stage tilt
        sem_current_pos_x = x_0
        sem_current_pos_y = y_0 - b_y * (1 - 1 / math.cos(rx_sem)) - b_z * math.tan(rx_sem)
        sem_current_pos_z = 0 - b_y * math.tan(rx_sem) - b_z * (1 - 1 / math.cos(rx_sem))

        # Calculate the equivalent coordinates of the calibrated position, at the FM position
        fm_target_pos_x = x_0 + calibrated_values["dx"]
        fm_target_pos_y = y_0 + calibrated_values["dy"] - b_y * (1 - 1 / math.cos(rx_fm)) - b_z * math.tan(rx_fm)
        fm_target_pos_z = 0 - b_y * math.tan(rx_fm) - b_z * (1 - 1 / math.cos(rx_fm))

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the FM position.
        # Note that the 180-degree rotation is taken care of by swapping the +/- signs for x and y (wrt the m equation).
        transformed_pos["x"] = fm_target_pos_x + (sem_current_pos_x - pos["x"])
        transformed_pos["y"] = fm_target_pos_y + (sem_current_pos_y - pos["y"])
        transformed_pos["z"] = fm_target_pos_z + (pos["z"] - sem_current_pos_z)

        # Update the angles to the FM position angles
        transformed_pos.update(fm_pos_active)

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
        :param pos: the current stage position
        :return: the transformed stage position.
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
        b_y = calibrated_values["b_y"]
        b_z = (pos["z"] - z_ct) * math.cos(rx_fm) + b_y * math.sin(rx_fm)

        # Calculate the equivalent coordinates of the calibrated position, at the FM position
        fm_current_pos_x = x_0 + calibrated_values["dx"]
        fm_current_pos_y = y_0 + calibrated_values["dy"] - b_y * (1 - 1 / math.cos(rx_fm)) - b_z * math.tan(rx_fm)
        fm_current_pos_z = 0 - b_y * math.tan(rx_fm) - b_z * (1 - 1 / math.cos(rx_fm))

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position, at the SEM position stage tilt
        sem_target_pos_x = x_0
        sem_target_pos_y = y_0 - b_y * (1 - 1 / math.cos(rx_sem)) - b_z * math.tan(rx_sem)
        sem_target_pos_z = 0 - b_y * math.tan(rx_sem) - b_z * (1 - 1 / math.cos(rx_sem))

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the FM position.
        # Note that the 180-degree rotation is taken care of by swapping the +/- signs for x and y (wrt the m equation).
        transformed_pos["x"] = sem_target_pos_x + (fm_current_pos_x - pos["x"])
        transformed_pos["y"] = sem_target_pos_y + (fm_current_pos_y - pos["y"])
        transformed_pos["z"] = sem_target_pos_z + (pos["z"] - fm_current_pos_z)

        # Update the angles to the FM position angles
        transformed_pos.update(sem_pos_active)

        return transformed_pos

    def _transformFromSEMToMilling(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
        milling imaging area.
        :param pos: the current stage position.
        :return: the transformed position.
        """
        if "rx" not in pos:
            raise ValueError(f"The stage-bare position does not have rx axis. pos={pos}")

        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Call out calibrated values and stage tilt and rotation angles
        calibrated_values = stage_md[model.MD_CALIB]
        mill_pos_active = stage_md[model.MD_FAV_MILL_POS_ACTIVE].copy()

        # Define values that are used more than once
        rx_sem = pos["rx"]  # Current tilt angle (can differ per point of interest)
        mill_angle = mill_pos_active.pop("mill_angle")
        rx_mill = calculate_stage_tilt_from_milling_angle(mill_angle, pre_tilt=self.pre_tilt, column_tilt=self.fib_column_tilt)
        mill_pos_active["rx"] = rx_mill  # update the computed rx based on the milling angle

        z_ct = calibrated_values["z_ct"]
        b_y = calibrated_values["b_y"]
        b_z = (pos["z"] - z_ct) * math.cos(rx_sem) + b_y * math.sin(rx_sem)

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position,
        # at the SEM position stage tilt
        sem_current_pos_y = 0 - b_y * (1 - 1 / math.cos(rx_sem)) - b_z * math.tan(rx_sem)
        sem_current_pos_z = 0 - b_y * math.tan(rx_sem) - b_z * (1 - 1 / math.cos(rx_sem))

        # Calculate the equivalent coordinates of the calibrated position, at the milling position
        mill_target_pos_y = 0 - b_y * (1 - 1 / math.cos(rx_mill)) - b_z * math.tan(rx_mill)
        mill_target_pos_z = 0 - b_y * math.tan(rx_mill) - b_z * (1 - 1 / math.cos(rx_mill))

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the milling position.
        # X stays the same as when switching from SEM to MILL only Y and Z change.
        transformed_pos["x"] = pos["x"]
        transformed_pos["y"] = mill_target_pos_y + (pos["y"] - sem_current_pos_y)
        transformed_pos["z"] = mill_target_pos_z + (pos["z"] - sem_current_pos_z)

        # Update the angles to the MILL position angles
        transformed_pos.update(mill_pos_active)

        return transformed_pos

    def _transformFromMillingToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the milling imaging area
        to the SEM imaging area.
        :param pos: the current stage position
        :return: the transformed stage position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Call out calibrated values and stage tilt and rotation angles
        calibrated_values = stage_md[model.MD_CALIB]
        mill_pos_active = stage_md[model.MD_FAV_MILL_POS_ACTIVE]
        sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

        # Define values that are used more than once
        rx_sem = sem_pos_active["rx"]
        rx_mill = calculate_stage_tilt_from_milling_angle(milling_angle=mill_pos_active["mill_angle"],
                                                          pre_tilt=self.pre_tilt, column_tilt=self.fib_column_tilt)

        z_ct = calibrated_values["z_ct"]
        b_y = calibrated_values["b_y"]
        b_z = (pos["z"] - z_ct) * math.cos(rx_mill) + b_y * math.sin(rx_mill)

        # Calculate the equivalent coordinates of the calibrated position, at the milling position
        mill_current_pos_y = 0 - b_y * (1 - 1 / math.cos(rx_mill)) - b_z * math.tan(rx_mill)
        mill_current_pos_z = 0 - b_y * math.tan(rx_mill) - b_z * (1 - 1 / math.cos(rx_mill))

        # Calculate the equivalent coordinates of the (0-degree tilt) calibrated position, at the SEM position stage tilt
        sem_target_pos_y = 0 - b_y * (1 - 1 / math.cos(rx_sem)) - b_z * math.tan(rx_sem)
        sem_target_pos_z = 0 - b_y * math.tan(rx_sem) - b_z * (1 - 1 / math.cos(rx_sem))

        # Use the above reference positions to calculate the equivalent coordinates of the point of interest,
        # at the milling position.
        # X stays the same as when switching from SEM to MILL only Y and Z change.
        transformed_pos["x"] = pos["x"]
        transformed_pos["y"] = sem_target_pos_y + (pos["y"] - mill_current_pos_y)
        transformed_pos["z"] = sem_target_pos_z + (pos["z"] - mill_current_pos_z)

        # Update the angles to the milling position angles
        transformed_pos.update(sem_pos_active)

        return transformed_pos

    def _doCryoSwitchSamplePosition(self, future, target):
        try:
            target_name = POSITION_NAMES[target]
        except KeyError:
            raise ValueError(f"Unknown target '{target}'")

        try:
            # get the meta data
            focus_md = self.focus.getMetadata()
            focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
            focus_active = focus_md[model.MD_FAV_POS_ACTIVE]
            # To hold the ordered sub moves list
            sub_moves = []  # list of tuples (component, position)

            # get the current label
            current_pos = self.stage.position.value
            current_posture = self.getCurrentPostureLabel(current_pos)
            current_name = POSITION_NAMES[current_posture]

            if current_posture == target:
                logging.warning(f"Requested move to the same position as current: {target_name}")

            # get the set point position
            target_pos = self.getTargetPosition(target)

            # In many cases, to move safely, we force the stage Z to go down first + extra margin,
            # do the actual moves, and then move back up. But on Tescan (stage-bare), the Z axis
            # *increases* when going down.
            lowest_z = max(target_pos["z"], current_pos["z"])
            safety_z = lowest_z + TESCAN_SAFETY_Z_MARGIN
            # Handle the (very unlikely) case where we would ask to go too low
            z_range = self.stage.axes["z"].range
            if safety_z > z_range[1]:
                logging.warning("Limiting Z safety position from %s m to %s m due to axis range", safety_z, z_range[1])
                safety_z = z_range[1]

            # If at some "weird" position, it's quite unsafe. We consider the targets
            # LOADING and SEM_IMAGING safe to go. So if not going there, first pass
            # by SEM_IMAGING and then go to the actual requested position.
            if current_posture == UNKNOWN:
                logging.warning("Moving stage while current position is unknown (at %s).", current_pos)
                if target not in (LOADING, SEM_IMAGING):
                    logging.debug("Moving first to SEM_IMAGING position")
                    target_pos_sem = self.getTargetPosition(SEM_IMAGING)
                    if not isNearPosition(self.focus.position.value, focus_deactive, self.focus.axes):
                        sub_moves.append((self.focus, focus_deactive))

                    sub_moves.append((self.stage, {'z': safety_z}))
                    sub_moves.append((self.stage, filter_dict({'x', 'y', 'rx', 'rz'}, target_pos_sem)))
                    # Don't move in Z of SEM_IMAGING, as it'll move down first to safety_z later

            if target in (GRID_1, GRID_2):
                # The current posture doesn't change.
                # Moving should mostly consist in a move in X (and Y+Z to go back to the center of the grid)
                pos_rotation = filter_dict({'rx', 'rz'}, current_pos)
                target_rotation = filter_dict({'rx', 'rz'}, target_pos)
                if not isNearPosition(pos_rotation, target_rotation, {'rx', 'rz'}):
                    raise ValueError(f"Unexpected change of stage rotation/tilt when moving to grid "
                                     f"position: {pos_rotation} vs {target_rotation}. Aborting move.")
                sub_moves.append((self.stage, filter_dict({'x'}, target_pos)))
                sub_moves.append((self.stage, filter_dict({'y', 'z'}, target_pos)))

            elif target in (LOADING, SEM_IMAGING, FM_IMAGING, MILLING):
                # Park the focuser for safety
                if not isNearPosition(self.focus.position.value, focus_deactive, self.focus.axes):
                    sub_moves.append((self.focus, focus_deactive))

                if current_posture == MILLING:
                    # Store current milling angle, to go back to that same position next time
                    mill_angle = calculate_milling_angle_from_stage_tilt(current_pos["rx"],
                                            pre_tilt=self.pre_tilt,
                                            column_tilt=self.fib_column_tilt)
                    mill_pos_active = self.stage.getMetadata()[model.MD_FAV_MILL_POS_ACTIVE]
                    mill_pos_active["mill_angle"] = mill_angle
                    self.stage.updateMetadata({model.MD_FAV_MILL_POS_ACTIVE: mill_pos_active})
                    # TODO: update transformation matrices for milling (at the moment the milling angle is changed)

                # In the Odemis Standard case, which doesn't distinguish between SEM_IMAGING and MILLING,
                # the user might have changed the tilt/rotation of the stage while in SEM mode,
                # to change posture. So store them so that when going back to SEM_IMAGING,
                # we can go back to the same posture.
                # TODO: if there is a MILLING posture, should we still save rx & rz?
                if current_posture == SEM_IMAGING and target == FM_IMAGING:
                    pos_rotation = filter_dict({'rx', 'rz'}, current_pos)
                    sem_pos_active = self.stage.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
                    if not isNearPosition(pos_rotation, sem_pos_active, {"rx", "rz"}):
                        logging.info("Updating SEM posture from %s to %s", sem_pos_active, pos_rotation)
                        self.stage.updateMetadata({model.MD_FAV_SEM_POS_ACTIVE: pos_rotation})

                # Same order in every case:
                sub_moves.append((self.stage, {'z': safety_z}))  # Move Z to really low position
                sub_moves.append((self.stage, filter_dict({'x', 'y', 'rx', 'rz'}, target_pos)))  # Do all moves simultaneously
                sub_moves.append((self.stage, filter_dict({'z'}, target_pos)))  # Move the final Z

                if target == FM_IMAGING:
                    if self.shutter is not None:
                        logging.info("Retracting shutter before engaging the objective for FM imaging")
                        self.shutter.value = False  # False = retracted (open), blocking call
                    # Engage the focuser as last move
                    sub_moves.append((self.focus, focus_active))
            else:
                raise ValueError(f"Unsupported move to target {target_name}")

            # run the moves
            logging.info("Moving from position %s to position %s.",current_name, target_name)
            for component, sub_move in sub_moves:
                self._run_sub_move(future, component, sub_move)

            # Handle shutter when transitioning to MILLING and SEM imaging positions, coming from FM.
            # We rely on Tescan's automatic shutter control, that acts when the imaging mode changes.
            # TODO: later extend for FIB_IMAGING
            if target in [MILLING, SEM_IMAGING] and current_posture == FM_IMAGING and self.shutter is not None:
                if self.shutter.value is False:
                    logging.info("Setting shutter to automatic for transition from FM imaging")
                    self.shutter.value = None  # None = automatic

        except CancelledError:
            logging.info("CryoSwitchSamplePosition cancelled.")
        except Exception:
            logging.exception("Failure to move to %s position.", target_name)
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED

    def _transformFromChamberToStage(self, shift: Dict[str, float]) -> Dict[str, float]:
        """Transform the shift from stage bare to chamber coordinates.
        Used for moving the stage vertically in the chamber.
        For tescan, the z-axis is already aligned with the chamber axis,
        so this function returns the input.
        :param shift: The shift to be transformed
        :return: The transformed shift
        """
        vshift = {"x": shift.get("x", 0), "z": shift.get("z", 0)}
        return vshift


class MeteorJeol1PostureManager(MeteorPostureManager):
    def __init__(self, microscope):
        super().__init__(microscope)
        # Check required metadata used during switching
        required_keys_jeol1 = {"x_0", "y_0", "z_0", "dx", "dy", "dz"}
        self.required_keys.add(model.MD_CALIB)
        self.check_stage_metadata(self.required_keys)
        self.check_calib_data(required_keys_jeol1)

        # Jeol microscopes typically use x, y, z, rx, rz axes
        if not {"x", "y", "z", "rx", "rz"}.issubset(self.stage.axes):
            missed_axes = {'x', 'y', 'z', 'rx', 'rz'} - self.stage.axes.keys()
            raise KeyError(f"The stage misses {missed_axes} axes")

        if self.pre_tilt is None:
            self.pre_tilt = 0  # Standard on JEOL is no pre-tilt (ie, 0°)

        if self.pre_tilt != 0:
            raise ValueError("JEOL Posture Manager only supports pre-tilt of 0°")

        self.postures = [SEM_IMAGING, FM_IMAGING]
        # Automatic conversion to sample-stage axes
        self._initialise_transformation(axes=["y", "z"])
        self.create_sample_stage()

    def _update_conversion(self,
                           rotation: float = 0,
                           ):
        """
        Computes transformation parameters based on the given metadata to allow conversion
        between the stage-bare and sample plane.
        NOTE: transformations are defined as sample stage -> stage bare
        the inverse transformation is used for stage bare -> sample stage
        :param rotation: rotation in radians from sample plane to stage (rx)
        """
        tf_id = numpy.eye(3)
        # The JEOL stage convention is opposite of Odemis so invert the direction of the XYZ axes.
        tf_reverse = -tf_id

        # Otherwise, nothing to do: there is no pre-tilt, and the stage X/Y is above the Rx axis,
        # so no need to compensate for the tilt.
        tf_fm = tf_reverse
        tf_fm_inv = numpy.linalg.inv(tf_fm)

        # get the scan rotation value
        sr = self._get_scan_rotation()

        # get scan rotation matrix (rz -> rx)
        tf_sr, _ = get_rotation_transforms(rz=-sr)
        tf_sem = tf_reverse @ tf_sr
        tf_sem_inv = numpy.linalg.inv(tf_sem)

        logging.debug(f"Sample stage transformation matrices, FM: {tf_fm}, SEM: {tf_sem}")

        # From sample-stage to stage-bare
        self._transforms = {FM_IMAGING: tf_fm,
                            SEM_IMAGING: tf_sem,
                            UNKNOWN: tf_id}
        # From stage-bare to sample-stage
        self._inv_transforms = {FM_IMAGING: tf_fm_inv,
                                SEM_IMAGING: tf_sem_inv,
                                UNKNOWN: tf_id}

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

        # SEM posture + grid center
        def get_sem_grid(grid_label):
            pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE].copy()
            pos.update(stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[grid_label]])
            return pos

        if target_pos_lbl == LOADING:
            end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
        if current_position in [LOADING, SEM_IMAGING]:
            if target_pos_lbl in [SEM_IMAGING, GRID_1]:
                end_pos = get_sem_grid(GRID_1)
            elif target_pos_lbl == GRID_2:
                end_pos = get_sem_grid(GRID_2)
            elif target_pos_lbl == FM_IMAGING:
                if current_position == LOADING:
                    # if at loading and fm is pressed, choose grid1 by default
                    end_pos = self._transformFromSEMToMeteor(get_sem_grid(GRID_1))
                else:
                    end_pos = self._transformFromSEMToMeteor(self.stage.position.value)
        elif current_position == FM_IMAGING:
            if target_pos_lbl == GRID_1:
                end_pos = self._transformFromSEMToMeteor(get_sem_grid(GRID_1))
            elif target_pos_lbl == GRID_2:
                end_pos = self._transformFromSEMToMeteor(get_sem_grid(GRID_2))
            elif target_pos_lbl == SEM_IMAGING:
                end_pos = self._transformFromMeteorToSEM(self.stage.position.value)

        if end_pos is None:
            raise ValueError(
                f"Unknown target position {POSITION_NAMES.get(target_pos_lbl, target_pos_lbl)} "
                f"when in {POSITION_NAMES.get(current_position, current_position)}")

        return end_pos

    def _transformFromSEMToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
        meteor/FM imaging area.
        :param pos: the current stage position.
        :return: the transformed position.
        """
        if "rx" not in pos:
            raise ValueError(f"The stage-bare position does not have rx axis. pos={pos}")

        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Get calibrated values and stage angles
        calibrated_values = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]

        # Define reference positions and current tilt
        rx_sem = pos["rx"]  # Current tilt angle
        rx_fm = fm_pos_active["rx"]  # FM tilt angle
        z_0 = calibrated_values["z_0"]

        # Calculate position transformation considering tilt compensation
        # For Jeol systems, typically use simple translation with tilt correction
        dx = calibrated_values["dx"]
        dy = calibrated_values["dy"]
        dz = calibrated_values.get("dz", 0)

        # Apply tilt-dependent correction (similar to Tescan approach)
        z_offset = pos["z"] - z_0
        tilt_correction_y = z_offset * (math.sin(rx_fm) - math.sin(rx_sem))
        tilt_correction_z = z_offset * (math.cos(rx_sem) - math.cos(rx_fm))

        # Calculate transformed position
        transformed_pos["x"] = pos["x"] + dx
        transformed_pos["y"] = pos["y"] + dy + tilt_correction_y
        transformed_pos["z"] = pos["z"] + dz + tilt_correction_z

        # Update angles to FM position
        transformed_pos.update(fm_pos_active)

        return transformed_pos

    def _transformFromMeteorToSEM(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the meteor/FM imaging area
        to the SEM imaging area.
        :param pos: the current stage position
        :return: the transformed stage position.
        """
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()

        # Get calibrated values and stage angles
        calibrated_values = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]
        sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

        # Define reference positions and angles
        rx_sem = sem_pos_active["rx"]
        rx_fm = fm_pos_active["rx"]
        z_0 = calibrated_values["z_0"]

        # Calculate reverse transformation
        dx = calibrated_values["dx"]
        dy = calibrated_values["dy"]
        dz = calibrated_values.get("dz", 0)

        # Apply reverse tilt-dependent correction
        z_offset = pos["z"] - z_0
        tilt_correction_y = z_offset * (math.sin(rx_sem) - math.sin(rx_fm))
        tilt_correction_z = z_offset * (math.cos(rx_fm) - math.cos(rx_sem))

        # Calculate transformed position (reverse transformation)
        transformed_pos["x"] = pos["x"] - dx
        transformed_pos["y"] = pos["y"] - dy + tilt_correction_y
        transformed_pos["z"] = pos["z"] - dz + tilt_correction_z

        # Update angles to SEM position
        transformed_pos.update(sem_pos_active)

        return transformed_pos

    def _doCryoSwitchSamplePosition(self, future, target_posture: int):
        """
        Do the actual switching procedure for cryoSwitchSamplePosition
        :param future: cancellable future of the move
        :param target_posture: target posture
        """
        try:
            target_name = POSITION_NAMES[target_posture]
        except KeyError:
            raise ValueError(f"Unknown target '{target_posture}'")

        try:
            # To hold the ordered sub moves list
            sub_moves: List[Tuple[model.Actuator, Dict[str, float]]] = []  # series of component + position
            current_name = POSITION_NAMES[self.current_posture.value]

            target_position = self.getTargetPosition(target_posture)  # raises an exception if move is unsupported

            if target_posture in (GRID_1, GRID_2):
                sub_moves.append((self.stage, target_position))
            else:  # posture switch (LOADING, SEM_IMAGING, FM_IMAGING)
                focus_md = self.focus.getMetadata()
                focus_deactive = focus_md[model.MD_FAV_POS_DEACTIVE]
                focus_active = focus_md[model.MD_FAV_POS_ACTIVE]

                # Park the focuser for safety
                if not isNearPosition(self.focus.position.value, focus_deactive, self.focus.axes):
                    sub_moves.append((self.focus, focus_deactive))

                # Move stage directly (the JEOL software takes care of the safety aspects)
                sub_moves.append((self.stage, target_position))

                if target_posture == FM_IMAGING:
                    # Engage the focuser as last move
                    sub_moves.append((self.focus, focus_active))

            # run the moves
            logging.info("Moving from position %s to position %s.", current_name, target_name)
            for component, sub_move in sub_moves:
                self._run_sub_move(future, component, sub_move)

        except CancelledError:
            logging.info("CryoSwitchSamplePosition cancelled.")
        except Exception:
            logging.exception("Failure to move to %s position.", target_name)
            raise
        finally:
            with future._task_lock:
                if future._task_state == CANCELLED:
                    raise CancelledError()
                future._task_state = FINISHED

    def _transformFromChamberToStage(self, shift: Dict[str, float]) -> Dict[str, float]:
        """Transform the shift from stage bare to chamber coordinates.
        Used for moving the stage vertically in the chamber.
        Should not be used in JEOL1 configuration.
        :param shift: The shift to be transformed
        :return: The transformed shift
        """
        raise NotImplementedError("_transformFromChamberToStage is not used in JEOL1 configuration")


class SampleStage(model.Actuator):
    """
    Stage wrapper component which converts the stage position to the sample stage position.
    The sample stage coordinates system is along the sample-plane which is adjusted
    according to the pre-tilt and other factors.
    """

    def __init__(self, name: str, role: str, stage_bare: model.Actuator,
                 posture_manager: MeteorPostureManager, **kwargs):
        """
        :param name: the name of the component (usually "Sample Stage")
        :param role: the role of the component (usually "stage")
        :param stage_bare: the stage component to be wrapped
        :param posture_manager: the posture manager to be used for conversion
        :param **kwargs: additional arguments to be passed to the parent class
        """

        self._stage_bare = stage_bare
        sample_stage_axes = copy.deepcopy({"x": self._stage_bare.axes["x"],
                                           "y": self._stage_bare.axes["y"],
                                           "z": self._stage_bare.axes["z"],})

        model.Actuator.__init__(self, name=name, role=role, dependencies={"under": stage_bare},
                                axes=sample_stage_axes, **kwargs)

        # update related MDs
        self._affected_components = []
        for role in COMPS_AFFECTED_ROLES:
            try:
                self._affected_components.append(model.getComponent(role=role))
            except Exception:
                pass

        # get the ebeam focus component, for the SEM focus compensation (not always available)
        self.ebeam_focus = None
        try:
            self.ebeam_focus = model.getComponent(role="ebeam-focus")
        except Exception:
            pass

        # posture manager to convert the positions
        self._pm = posture_manager

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({"x": 0, "y": 0, "z": 0},
                                                unit=("m", "m", "m"),  readonly=True)
        # it's just a conversion from the dep's position
        self._stage_bare.position.subscribe(self._updatePosition, init=True)

        if model.hasVA(self._stage_bare, "speed"):
            speed_axes = set(sample_stage_axes.keys())
            if set(self.axes) <= speed_axes:
                self.speed = model.VigilantAttribute({}, readonly=True)
                self._stage_bare.speed.subscribe(self._updateSpeed, init=True)
            else:
                logging.info("Axes %s of dependency are missing from .speed, so not providing it",
                             set(self.axes) - speed_axes)

    def _updatePosition(self, pos_dep):
        """
        update the position VA when the dep's position is updated
        """
        # Explicitly computes the posture, as .current_posture is updated by a subscriber that might be
        # called after this one.
        posture = self._pm.getCurrentPostureLabel(pos_dep)
        if posture not in self._pm.postures:
            logging.info("Not updating stage sample position for unsupported posture %s", posture)
            return
        pos = self._pm.to_sample_stage_from_stage_position(pos_dep, posture)
        # it's read-only, so we change it via _value
        self.position._set_value(pos, force_write=True)
        logging.debug("Sample stage position updated to %s (posture = %s)", pos, POSITION_NAMES[posture])

        # update related mds
        for comp in self._affected_components:
            try:
                md_pos = pos.get("x", 0), pos.get("y", 0)
                comp.updateMetadata({
                    model.MD_POS: md_pos,
                    model.MD_STAGE_POSITION_RAW: pos_dep,
                })
            except Exception as e:
                logging.error("Failed to update %s with new position: %s", comp, e)

        # Update the SEM focus position when the stage is moved to compensate for linked behavior.
        # Note: normally, only useful on SEM_IMAGING posture, but sometimes the user might want to
        # do SEM imaging also in MILLING posture (as it's the same as SEM IMAGING, but with some tilt).
        # So also update the e-beam focus in that posture.
        # TODO: update the self.sem_eucentric_focus when the user manually focuses.
        if self._pm.use_linked_sem_focus_compensation and posture in (SEM_IMAGING, MILLING):
            try:
                # get the eucentric focus position from the metadata
                self.sem_eucentric_focus = self._stage_bare.getMetadata()[model.MD_CALIB].get("SEM-Eucentric-Focus", 7.0e-3)
                f = self.ebeam_focus.moveAbs({"z": self.sem_eucentric_focus})
                f.result()
            except Exception as e:
                logging.error(f"Failed to update ebeam-focus with new position: 'z': {self.sem_eucentric_focus}, {e}")

    def _updateSpeed(self, dep_speed):
        """
        update the speed VA based on the dependency's speed
        """
        self.speed._set_value(dep_speed, force_write=True)

    def moveRel(self, shift: Dict[str, float], **kwargs) -> Future:
        """
        :param shift: The relative shift to be made
        :param **kwargs: Mostly there to support "update" argument
        """
        # missing values are assumed to be zero
        shift_stage = self._pm.from_sample_stage_to_stage_movement(shift)
        # Only the linear axes are necessary for the move, so drop the others (to avoid extraneous moves)
        shift_stage = filter_dict(LINEAR_AXES, shift_stage)
        logging.debug("converted relative move from %s to %s", shift, shift_stage)
        return self._stage_bare.moveRel(shift_stage, **kwargs)

    def moveAbs(self, pos: Dict[str, float], **kwargs) -> Future:
        """
        :param pos: The absolute position to be moved to
        :param **kwargs: Mostly there to support "update" argument
        """

        # if key is missing from pos, fill it with the current position
        for key in self.axes.keys():
            if key not in pos:
                pos[key] = self.position.value[key]

        # pos is a position, so absolute conversion
        pos_stage = self._pm.from_sample_stage_to_stage_position(pos)
        # Only the linear axes are necessary for the move, so drop the others (to avoid extraneous moves)
        pos_stage = filter_dict(LINEAR_AXES, pos_stage)
        logging.debug("converted absolute move from %s to %s", pos, pos_stage)
        return self._stage_bare.moveAbs(pos_stage, **kwargs)

    def moveRelChamberCoordinates(self, shift: Dict[str, float]) -> Future:
        """Move the stage vertically in the chamber. This is non-blocking. From fibsemOS.
        The desired input shift (x, z) is transformed to x, y, z axis components such that the
        the stage moves in the vertical direction in the chamber.
        The input shift is expected in the sample-stage coordinates. A feature in the FIB FoV is
        expected to be moved by dx in the x-image coordinates, and dz in the y-image coordinates. The
        feature will not be moved in the SEM FoV. To achieve this, we compute the stage-bare movement such
        that the stage moves vertically in the chamber, resulting in the desired behavior in the SEM/FIB FoV.
        For TFS, the z-axis is attached to the tilt, so the tilt angle must be taken into account.
        This is used to correct coincidence between the SEM and FIB FoV.
        For Tescan systems, the z-axis is always vertical in the chamber, so conversion is not required.
        This functionality works at any posture.
        :param shift: The relative shift to be made (x, z).
        :return: A cancellable future
        """
        # TODO: account for scan rotation
        # transform the shift from stage bare to chamber coordinates
        vshift = self._pm._transformFromChamberToStage(shift)
        return self._stage_bare.moveRel(vshift)

    def stop(self, axes=None):
        self._stage_bare.stop()


def calculate_stage_tilt_from_milling_angle(milling_angle: float, pre_tilt: float, column_tilt: float) -> float:
    """Calculate the stage tilt from the milling angle and the pre-tilt.
    :param milling_angle: the milling angle in radians
    :param pre_tilt: the pre-tilt in radians
    :param column_tilt: the column tilt in radians (default TFS = 52deg, Tescan = 55deg)
    :return: the stage tilt in radians
    """
    # Equation:
    # MillingAngle = 90 - ColumnTilt + StageTilt - PreTilt
    # StageTilt = MillingAngle + PreTilt + ColumnTilt - 90

    # calculate the stage tilt from the milling angle and the pre-tilt
    stage_tilt = milling_angle + pre_tilt + column_tilt - math.radians(90)
    return stage_tilt


def calculate_milling_angle_from_stage_tilt(rx: float, pre_tilt: float, column_tilt: float) -> float:
    """
    Calculate the milling angle from the stage tilt, pre-tilt, and column tilt.
    :param rx: the stage tilt angle in radians
    :param pre_tilt: the pre-tilt in radians
    :param column_tilt: the column tilt in radians
    :return: the milling angle in radians
    """
    # calculate the stage tilt from the milling angle and the pre-tilt
    milling_angle = rx - pre_tilt - column_tilt + math.radians(90)
    return milling_angle
