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
from concurrent.futures import CancelledError, Future
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING
from typing import Dict, Union, Iterable, Optional

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
        self.linear_axes = set(key for key in self.axes.keys() if key in {'x', 'y', 'z', 'm'})
        self.rotational_axes = set(key for key in self.axes.keys() if key in {'rx', 'ry', 'rz', 'rm'})
        # required keys that must be present in the stage metadata
        self.required_keys = {
            model.MD_FAV_POS_DEACTIVE, model.MD_FAV_SEM_POS_ACTIVE, model.MD_FAV_FM_POS_ACTIVE,
            model.MD_SAMPLE_CENTERS}
        # Supporting parameter to convert between sample and stage positions
        self._transforms: Dict[int, numpy.ndarray] = {}  # transforms (to-sample-stage)
        self._inv_transforms: Dict[int, numpy.ndarray] = {}  # inverse transforms (from-sample-stage)
        self._metadata = {}
        self._axes_dep = {}  # axes dependencies between different planes

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
            rx = calculate_stage_tilt_from_milling_angle(milling_angle=md["rx"],
                                                        pre_tilt=self.pre_tilt,
                                                        column_tilt=self.fib_column_tilt)
            return {"rx": rx, "rz": md["rz"]}

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
            axes: Iterable[str],
            rotation: float = 0,
            scale: tuple = (1, 1),
            translation: tuple = (0, 0),
            shear: tuple = (0, 0),
    ):
        """
        Initializes the transformation parameters that allows conversion between stage-bare and sample plane.
        :param axes: stage axes which are used to calculate transformation parameters
        :param rotation: rotation in radians from sample plane to stage
        :param scale: scale from sample to stage
        :param translation: translation from sample to stage
        :param shear: shear from sample to stage
        """
        self._axes_dep = {"x": axes[0], "y": axes[1]}  # Should be called y, z... or even better: also take x as first axis
        self._metadata[model.MD_POS_COR] = translation
        self._metadata[model.MD_ROTATION_COR] = rotation
        self._metadata[model.MD_PIXEL_SIZE_COR] = scale
        self._metadata[model.MD_SHEAR_COR] = shear
        self._update_conversion()

    def _update_conversion(self):
        """
        Computes transformation parameters based on the given metadata to allow conversion
        stage-bare and sample plane.
        """
        # NOTE: transformations are defined as sample stage -> stage bare
        # the inverse transformation is used for stage bare -> sample stage
        translation = self._metadata[model.MD_POS_COR]
        scale = self._metadata[model.MD_PIXEL_SIZE_COR]
        shear = self._metadata[model.MD_SHEAR_COR]

        # The shear & scale parameters are for the 2nd and 3rd axes (y and z) in FM imaging
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

        # pre-tilt is rotation around the stage-bare x axis
        rx = self.pre_tilt

        # FM imaging
        # Scaling*Shearing*Rotation for convert back/forth between exposed and dep
        rot_matrix_3d, rot_matrix_3d_inv = get_rotation_transforms(rx=rx)
        tf = scale_matrix_3d @ shear_matrix_3d @ rot_matrix_3d
        tf_inv = numpy.linalg.inv(tf)

        # get the scan rotation value
        sr = self._get_scan_rotation()

        # TODO: also need shear and scale for SEM_IMAGING and MILLING postures, each different.
        # get scan rotation matrix (rz -> rx)
        tf_sr, tf_inv_sr = get_rotation_transforms(rx=rx, rz=sr)
        logging.debug(f"tf_matrix: {tf}, tf_sr: {tf_sr}")

        self._transforms = {FM_IMAGING: tf,
                            FIB_IMAGING: tf_sr,
                             SEM_IMAGING: tf_inv_sr,
                             MILLING: tf_inv_sr,
                             UNKNOWN: tf_inv_sr}
        self._inv_transforms = {FM_IMAGING: tf_inv,
                                 FIB_IMAGING: tf_inv_sr,
                                 SEM_IMAGING: tf_sr,
                                 MILLING: tf_sr,
                                 UNKNOWN: tf_sr}

    def _get_scan_rotation(self) -> float:
        """
        Get the scan rotation value for SEM/FIB, and ensure they match.
        :return: the scan rotation value in radians
        """

        # need to check if e-beam and ion-beam are available
        comps = model.getComponents()
        roles = [comp.role for comp in comps]
        if not ("e-beam" in roles and "ion-beam" in roles):
            logging.warning("e-beam and/or ion-beam not available, scan rotation will be set to 0")
            return 0

        # check if e-beam and ion-beam have the same rotation
        ebeam = model.getComponent(role='e-beam')
        ion_beam = model.getComponent(role='ion-beam')
        sr = ebeam.rotation.value
        ion_sr = ion_beam.rotation.value
        if not numpy.isclose(sr, ion_sr, atol=ATOL_ROTATION_POS):
            raise ValueError(f"The SEM and FIB rotations do not match {sr} != {ion_sr}")

        return sr

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

        ppos = {}
        ppos["x"] = pinv[0]
        ppos[self._axes_dep["x"]] = pinv[1]
        ppos[self._axes_dep["y"]] = pinv[2]
        return ppos

    def from_sample_stage_to_stage_position(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Get stage position coordinates from sample stage coordinates (sample-stage -> stage-bare).
        :param pos: position in the sample-stage coordinates
        :return: position in the stage-bare coordinates
        """
        q = numpy.array([pos["x"], pos["y"], pos["z"]])
        posture = self.current_posture.value
        pinv = self._transforms[posture] @ q

        # add orientation (rx, rz)
        orientation = self.get_posture_orientation(posture)

        ppos = {}
        ppos["x"] = pinv[0]
        ppos[self._axes_dep["x"]] = pinv[1]
        ppos[self._axes_dep["y"]] = pinv[2]
        ppos.update(orientation)
        return ppos

    def to_sample_stage_from_stage_position(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Get sample stage coordinates from stage coordinates. (stage-bare -> sample stage)
        :param pos: position in the stage-bare coordinates
        :return: position in the sample-stage coordinates
        """
        p = numpy.array([pos["x"], pos[self._axes_dep["x"]], pos[self._axes_dep["y"]]])
        posture = self.current_posture.value
        q = self._inv_transforms[posture] @ p

        qpos = {}
        qpos["x"] = q[0]
        qpos["y"] = q[1]
        qpos["z"] = q[2]

        return qpos

    def to_posture(self, pos: Dict[str, float], posture: int) -> Dict[str, float]:
        """Convert a stage-bare position to a position in the target posture.
        :param pos: stage position in the stage-bare coordinates
        :param posture: (int) the target posture of the stage
        :return: stage-bare position in the target posture"""

        position_posture = self.getCurrentPostureLabel(pos)

        logging.info(f"Position Posture: {POSITION_NAMES[position_posture]}, Target Posture: {POSITION_NAMES[posture]}")

        if position_posture == posture:
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
        Transforms the stage position from sem imaging to milling position"
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


class MeteorTFS1PostureManager(MeteorPostureManager):
    def __init__(self, microscope):
        super().__init__(microscope)
        # Check required metadata used during switching
        self.required_keys.add(model.MD_POS_COR)
        self.check_stage_metadata(required_keys=self.required_keys)
        if not {"x", "y", "rz", "rx"}.issubset(self.stage.axes):
            raise KeyError("The stage misses 'x', 'y', 'rx' or 'rz' axes")

        # forced conversion to sample-stage axes
        comp = model.getComponent(name="Linked YZ")
        self.pre_tilt = comp.getMetadata()[model.MD_ROTATION_COR]
        self._initialise_transformation(axes=["y", "z"], rotation=self.pre_tilt)
        self.postures = [SEM_IMAGING, FM_IMAGING]

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

        # Note: all grid positions need to have rx, rz axes to be able to transform
        # this is not the case by default, and needs to be added in the metadata
        sem_grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
        sem_grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
        sem_grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]]
        sem_grid2_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])

        stage_position = self.stage.position.value

        if target_pos_lbl == LOADING:
            end_pos = stage_md[model.MD_FAV_POS_DEACTIVE]
        elif current_position in [LOADING, SEM_IMAGING]:
            if target_pos_lbl in [SEM_IMAGING, GRID_1]:
                # if at loading, and sem is pressed, choose grid1 by default
                end_pos = sem_grid1_pos
            elif target_pos_lbl == GRID_2:
                end_pos = sem_grid2_pos
            elif target_pos_lbl == FM_IMAGING:
                if current_position == LOADING:
                    # if at loading and fm is pressed, choose grid1 by default
                    fm_target_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
                elif current_position == SEM_IMAGING:
                    fm_target_pos = self._transformFromSEMToMeteor(stage_position)
                end_pos = fm_target_pos
            elif target_pos_lbl == MILLING:
                end_pos = self._transformFromSEMToMilling(stage_position)
            elif target_pos_lbl == FIB_IMAGING:
                end_pos = self._transformFromSEMToFIB(stage_position)
        elif current_position == FM_IMAGING:
            if target_pos_lbl == GRID_1:
                end_pos = self._transformFromSEMToMeteor(sem_grid1_pos)
            elif target_pos_lbl == GRID_2:
                end_pos = self._transformFromSEMToMeteor(sem_grid2_pos)
            elif target_pos_lbl == SEM_IMAGING:
                end_pos = self._transformFromMeteorToSEM(stage_position)
            elif target_pos_lbl == MILLING:
                end_pos = self._transformFromMeteorToMilling(stage_position)
            elif target_pos_lbl == FIB_IMAGING:
                end_pos = self._transformFromMeteorToFIB(stage_position)
        elif current_position == MILLING:
            if target_pos_lbl in [SEM_IMAGING, FM_IMAGING, MILLING, FIB_IMAGING]:
                end_pos = self.to_posture(pos=stage_position, posture=target_pos_lbl)
        elif current_position == FIB_IMAGING:
            if target_pos_lbl in [SEM_IMAGING, FM_IMAGING, MILLING]:
                end_pos = self.to_posture(pos=stage_position, posture=target_pos_lbl)
        if end_pos is None:
            raise ValueError("Unknown target position {} when in {}".format(
                POSITION_NAMES.get(target_pos_lbl, target_pos_lbl),
                POSITION_NAMES.get(current_position, current_position))
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
            elif target in (LOADING, SEM_IMAGING, FM_IMAGING, MILLING, FIB_IMAGING):
                # NOTE: prior to TFS3, no distinction was made between SEM and MILL positions, and these
                # were dynamically updated based on the current SEM position when switching to FM,
                # and used to restore the same position when switching back from FM -> SEM.
                # From TFS3, there is a separate MILLING position, so the SEM position has really a
                # fixed rotation and tilt.
                if (isinstance(self, MeteorTFS1PostureManager)
                    and current_label == SEM_IMAGING and target == FM_IMAGING
                   ):
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

        self._initialise_transformation(axes=["y", "z"], rotation=self.pre_tilt)
        self.create_sample_stage()
        self.postures = [SEM_IMAGING, FM_IMAGING]
        # These positions are "optional", and only used with Odemis advanced
        stage_md = self.stage.getMetadata()
        if model.MD_FAV_MILL_POS_ACTIVE in stage_md:
            self.postures.append(MILLING)
        if model.MD_FAV_FIB_POS_ACTIVE in stage_md:
            self.postures.append(FIB_IMAGING)

    def create_sample_stage(self):
        self.sample_stage = SampleStage(name="Sample Stage",
                                        role="stage",
                                        stage_bare = self.stage,
                                        posture_manager=self)

    def _transformFromSEMToMeteor(self, pos: Dict[str, float]) -> Dict[str, float]:
        """
        Transforms the current stage position from the SEM imaging area to the
        meteor/FM imaging area.
        :param pos: (dict str->float) the initial stage position.
        :return: (dict str->float) the transformed position.
        """
        # NOTE: this transform now always rotates around the z axis (180deg)
        # for pure translation, use FIB -> FM transform
        stage_md = self.stage.getMetadata()
        transformed_pos = pos.copy()
        md_calib = stage_md[model.MD_CALIB]
        fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]

        # check if the stage positions have rz axes
        if not ("rz" in pos and "rz" in fm_pos_active):
            raise ValueError(f"The stage position does not have rz axis pos={pos}, fm_pos_active={fm_pos_active}")

        transformed_pos["x"] = md_calib["dx"] - pos["x"]
        transformed_pos["y"] = md_calib["dy"] - pos["y"]
        transformed_pos.update(fm_pos_active)

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

        if self.pre_tilt is None: # pre-tilt not available in the stage calib metadata
            # First version of the microscope file had it hard-coded on the Linked YM wrapper component
            comp = model.getComponent(name="Linked YM")
            self.pre_tilt = comp.getMetadata()[model.MD_ROTATION_COR]

        # Automatic conversion to sample-stage axes
        self._initialise_transformation(axes=["y", "m"], rotation=self.pre_tilt)
        self.postures = [SEM_IMAGING, FM_IMAGING]

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

        # Y/Z axes are not perpendicular. The angle depends on rx (if rx==0°, they are perpendicular)
        # To compensate for this, we use shear and scale.
        stage_md = self.stage.getMetadata()
        rx_fm = stage_md[model.MD_FAV_FM_POS_ACTIVE]["rx"]
        shear = (-math.tan(rx_fm), 0)
        scale = (1, 1 / math.cos(rx_fm))

        # Automatic conversion to sample-stage axes
        self._initialise_transformation(axes=["y", "z"], rotation=self.pre_tilt, shear=shear, scale=scale)
        self.postures = [SEM_IMAGING, FM_IMAGING]

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
                    sub_moves.append((stage, filter_dict({'x'}, target_pos_sem)))
                    sub_moves.append((stage, filter_dict({'y', 'rz'}, target_pos_sem)))
                    sub_moves.append((stage, filter_dict({'rx'}, target_pos_sem)))
                    sub_moves.append((stage, filter_dict({'z'}, target_pos_sem)))

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


class SampleStage(model.Actuator):
    """
    Stage wrapper component which converts the stage position to the sample stage position.
    The sample stage coordinates system is along the sample-plane which is adjusted
    according to the pre-tilt and other factors.
    """

    def __init__(self, name: str, role: str, stage_bare: model.Actuator , posture_manager: MicroscopePostureManager, **kwargs):
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
        # TODO: this should be posture converted to SEM posture
        # pos_sem = self._pm.to_posture(pos_dep, SEM_IMAGING)
        # pos = self._pm.to_sample_stage_from_stage_position(pos_sem)
        # logging.warning(f"Converted position from {pos_dep} to {pos_sem}, Updating SampleStage position to {pos}")

        pos = self._pm.to_sample_stage_from_stage_position(pos_dep)
        # it's read-only, so we change it via _value
        self.position._set_value(pos, force_write=True)

        # update related mds
        for comp in self._affected_components:
            try:
                if comp:
                    md_pos = pos.get("x", 0), pos.get("y", 0)
                    comp.updateMetadata({
                        model.MD_POS: md_pos,
                        model.MD_STAGE_POSITION_RAW: pos_dep}
                        )
            except Exception as e:
                logging.error("Failed to update %s with new position: %s", comp, e)

        # update the SEM focus position when the stage is moved to compensate for linked behavior
        # TODO: update the self.sem_eucentric_focus when the user manually focuses.
        if self._pm.use_linked_sem_focus_compensation:
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

    @isasync
    def moveRel(self, shift: Dict[str, float], **kwargs) -> Future:
        """
        :param shift: The relative shift to be made
        :param **kwargs: Mostly there to support "update" argument
        """
        # missing values are assumed to be zero
        shift_stage = self._pm.from_sample_stage_to_stage_movement(shift)
        logging.debug("converted relative move from %s to %s", shift, shift_stage)
        return self._stage_bare.moveRel(shift_stage, **kwargs)

    @isasync
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
        logging.debug("converted absolute move from %s to %s", pos, pos_stage)
        return self._stage_bare.moveAbs(pos_stage, **kwargs)

    @isasync
    def moveRelChamberCoordinates(self, shift: Dict[str, float]) -> Future:
        """Move the stage vertically in the chamber. This is non-blocking. From OpenFIBSEM.
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


def calculate_stage_tilt_from_milling_angle(milling_angle: float, pre_tilt: float, column_tilt: int = math.radians(52)) -> float:
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
