# -*- coding: utf-8 -*-
"""
Copyright © 2020 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import copy
import logging
import math
import os
import time
import unittest

import numpy
import scipy

import odemis
from odemis import model
from odemis import util
from odemis.acq.move import (FM_IMAGING, GRID_1, GRID_2,
                             LOADING, ALIGNMENT, COATING, MILLING, LOADING_PATH,
                             RTOL_PROGRESS, SEM_IMAGING, UNKNOWN, POSITION_NAMES,
                             SAFETY_MARGIN_5DOF, SAFETY_MARGIN_3DOF, THREE_BEAMS, ROT_DIST_SCALING_FACTOR,
                             ATOL_LINEAR_TRANSFORM, ATOL_ROTATION_TRANSFORM,
                             MeteorPostureManager, MeteorTFS3PostureManager)
from odemis.acq.move import MicroscopePostureManager
from odemis.util import testing
from odemis.util.driver import ATOL_LINEAR_POS, isNearPosition
from odemis.util.transform import get_rotation_transforms

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TFS1_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"
METEOR_TFS3_CONFIG = CONFIG_PATH + "sim/meteor-tfs3-sim.odm.yaml"
METEOR_ZEISS1_CONFIG = CONFIG_PATH + "sim/meteor-zeiss-sim.odm.yaml"
METEOR_TESCAN1_CONFIG = CONFIG_PATH + "sim/meteor-tescan-sim.odm.yaml"


class TestMeteorTFS1Move(unittest.TestCase):
    """
    Test the MeteorPostureManager functions for Thermofisher 1
    """
    MIC_CONFIG = METEOR_TFS1_CONFIG
    ROTATION_AXES = {'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.posture_manager = MicroscopePostureManager(microscope=cls.microscope)

        # get the stage components
        cls.stage = model.getComponent(role="stage-bare")
        cls.linked_stage = model.getComponent(role="stage")

        # get the metadata
        stage_md = cls.stage.getMetadata()
        cls.stage_grid_centers = stage_md[model.MD_SAMPLE_CENTERS]
        cls.stage_loading = stage_md[model.MD_FAV_POS_DEACTIVE]

    def test_moving_to_grid1_in_sem_imaging_area_after_loading_1st_method(self):
        # Check the instantiation of correct posture manager
        self.assertIsInstance(self.posture_manager, MeteorPostureManager)
        # move the stage to the loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to the sem imaging area, and grid1 will be chosen by default.
        f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        position_label = self.posture_manager.getCurrentPostureLabel()
        grid_label = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(position_label, SEM_IMAGING)
        self.assertEqual(grid_label, GRID_1)
        # check the values of tilt and rotation
        sem_angles = self.stage.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], sem_angles[axis], places=4)

    # test linked axes movement when in fm imaging area
    def test_moving_in_grid1_fm_imaging_area_after_loading(self):
        """Check if the stage moves in the right direction when moving in the fm imaging grid 1 area."""
        # move the stage to the loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to the fm imaging area, and grid1 will be chosen by default
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        position_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(position_label, FM_IMAGING)
        # check the values of tilt and rotation
        fm_angles = self.stage.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], fm_angles[axis], places=4)

        # move in the same imaging mode using linked YZ stage
        old_stage_pos = self.stage.position.value
        old_linked_pos = self.linked_stage.position.value
        self.linked_stage.moveRel({"y": 1e-3}).result()
        new_stage_pos = self.stage.position.value
        new_linked_pos = self.linked_stage.position.value

        self.assertAlmostEqual(old_linked_pos["y"] + 1e-3, new_linked_pos["y"], places=4)
        self.assertTrue(old_stage_pos["y"] < new_stage_pos["y"])

    def test_moving_to_grid1_in_sem_imaging_area_after_loading_2nd_method(self):
        # move the stage to the loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to grid1, and sem imaging area will be chosen by default.
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_1)
        f.result()
        position_label = self.posture_manager.getCurrentPostureLabel()
        grid_label = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(position_label, SEM_IMAGING)
        self.assertEqual(grid_label, GRID_1)
        sem_angles = self.stage.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], sem_angles[axis], places=4)

    def test_moving_to_grid1_in_fm_imaging_area_after_loading(self):
        # move the stage to the loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to the fm imaging area, and grid1 will be chosen by default
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        position_label = self.posture_manager.getCurrentPostureLabel()
        grid_label = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(position_label, FM_IMAGING)
        self.assertEqual(grid_label, GRID_1)
        # check the values of tilt and rotation
        fm_angles = self.stage.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], fm_angles[axis], places=4)

    def test_moving_to_grid2_in_sem_imaging_area_after_loading(self):
        # move the stage to the loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to grid2
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_2)
        f.result()
        position_label = self.posture_manager.getCurrentPostureLabel()
        grid_label = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(position_label, SEM_IMAGING)
        self.assertEqual(grid_label, GRID_2)
        sem_angles = self.stage.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], sem_angles[axis], places=4)

    def test_moving_from_grid1_to_grid2_in_sem_imaging_area(self):
        # move to loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to the sem imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(SEM_IMAGING, current_imaging_mode)
        # now the selected grid is already the grid1
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_1, current_grid)
        # move the stage to grid2
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_2)
        f.result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_2, current_grid)
        # make sure we are still in sem  imaging area
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(SEM_IMAGING, current_imaging_mode)
        sem_angles = self.stage.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], sem_angles[axis], places=4)

    def test_moving_from_grid2_to_grid1_in_sem_imaging_area(self):
        # move to loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to the sem imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(SEM_IMAGING, current_imaging_mode)
        # move the stage to grid2
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_2)
        f.result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_2, current_grid)
        # move the stage back to grid1
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_1)
        f.result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_1, current_grid)
        # make sure we are still in the sem imaging area
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(SEM_IMAGING, current_imaging_mode)
        sem_angles = self.stage.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], sem_angles[axis], places=4)

    def test_moving_from_sem_to_fm(self):
        # move to loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move the stage to the sem imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(SEM_IMAGING, current_imaging_mode)
        # move to the fm imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)
        # check the values of tilt and rotation
        fm_angles = self.stage.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], fm_angles[axis], places=4)

    def test_moving_from_grid1_to_grid2_in_fm_imaging_Area(self):
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move to the fm imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)
        # now the grid is grid1 by default
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_1, current_grid)
        # move to the grid2
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_2)
        f.result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_2, current_grid)
        # make sure we are still in fm imaging area
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)
        # check the values of tilt and rotation
        fm_angles = self.stage.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], fm_angles[axis], places=4)

    def test_moving_from_grid2_to_grid1_in_fm_imaging_Area(self):
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move to the fm imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)
        # move to the grid2
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_2)
        f.result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_2, current_grid)
        # move back to the grid1
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_1)
        f.result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_1, current_grid)
        # make sure we are still in fm imaging area
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)
        # check the values of tilt and rotation
        fm_angles = self.stage.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], fm_angles[axis], places=4)

    def test_moving_to_sem_from_fm(self):
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move to the fm imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)
        # move to sem
        f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(SEM_IMAGING, current_imaging_mode)
        sem_angles = self.stage.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage.position.value[axis], sem_angles[axis], places=4)

    def test_unknown_label_at_initialization(self):
        arbitrary_position = {"x": 0.0, "y": 0.0, "z": -3.0e-3}
        self.stage.moveAbs(arbitrary_position).result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(UNKNOWN, current_imaging_mode)
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(current_grid, None)

    def test_transformFromSEMToMeteor(self):

        # previously, the transformFromSEMToMeteor function accepted positions without rz axes.
        # now, it checks compares the current and target rz axes for the required transformation.
        # the function will now raise an error if both positions don't have rz axes.
        #
        # the grid positions in the metadata of the stage component are defined without rz axes.
        # and were previously used to check which grid the stage is in (in the flm) (_get_CurrentGridLabel).
        # if these are passed without adding the rz axes from the active sem pos, it should raise
        # an error

        # assert that raises value error when no rz
        stage_md = self.stage.getMetadata()
        grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
        grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]]
        with self.assertRaises(ValueError):
            self.posture_manager._transformFromSEMToMeteor(grid1_pos)
        with self.assertRaises(ValueError):
            self.posture_manager._transformFromSEMToMeteor(grid2_pos)

        # assert that it doesn't raise error when rz is added
        grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
        grid2_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])

        # check if no error is raised (test fails if error is raised)
        try:
            self.posture_manager._transformFromSEMToMeteor(grid1_pos)
            self.posture_manager._transformFromSEMToMeteor(grid2_pos)
        except Exception as e:
            self.fail(f"_transformFromSEMToMeteor raised error when it shouldn't: {e}")


class TestMeteorZeiss1Move(TestMeteorTFS1Move):
    """
    Test the MeteorPostureManager functions for Zeiss 1
    """
    MIC_CONFIG = METEOR_ZEISS1_CONFIG
    ROTATION_AXES = {'rx', 'rm'}

    def test_moving_in_grid1_fm_imaging_area_after_loading(self):
        """
        Check if the stage moves in the right direction when moving in the fm imaging grid 1 area.
        """
        super().test_moving_in_grid1_fm_imaging_area_after_loading()

        # move in the same imaging mode using linked YM stage
        old_stage_pos = self.stage.position.value
        self.linked_stage.moveRel({"y": 1e-3}).result()
        new_stage_pos = self.stage.position.value

        # the stage moved in the right direction if the pre-tilt angle was maintained at 26-degrees
        beta = 0.4537856055185  # 26-degrees in radians
        estimated_beta = math.atan2(new_stage_pos["m"] - old_stage_pos["m"], new_stage_pos["y"] - old_stage_pos["y"])
        self.assertAlmostEqual(beta, estimated_beta, places=5, msg="The stage moved in the wrong direction in "
                                                                   "the FM imaging grid 1 area.")

    def test_transformFromSEMToMeteor(self):
        """
        Test the keys in stage position for _transformFromSEMToMeteor.
        """
        stage_md = self.stage.getMetadata()
        grid1_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
        grid2_pos = stage_md[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_2]]
        # Above position are updated in linear axes and do not have rotation axes,
        # so should raise KeyError when rotation axes are accessed
        with self.assertRaises(KeyError):
            self.posture_manager._transformFromSEMToMeteor(grid1_pos)
        with self.assertRaises(KeyError):
            self.posture_manager._transformFromSEMToMeteor(grid2_pos)

        # update the stage with rotation axes
        grid1_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])
        grid2_pos.update(stage_md[model.MD_FAV_SEM_POS_ACTIVE])

        # check if no error is raised (test fails if error is raised)
        try:
            self.posture_manager._transformFromSEMToMeteor(grid1_pos)
            self.posture_manager._transformFromSEMToMeteor(grid2_pos)
        except Exception as e:
            self.fail(f"_transformFromSEMToMeteor raised error when it shouldn't: {e}")

    def test_unknown_label_at_initialization(self):
        arbitrary_position = {"x": 0.0, "y": 0.0, "z": 0.0e-3}
        self.stage.moveAbs(arbitrary_position).result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(UNKNOWN, current_imaging_mode)
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(current_grid, None)


class TestMeteorTFS3Move(unittest.TestCase):
    """
    Test the MeteorPostureManager functions for TFS 3
    """
    MIC_CONFIG = METEOR_TFS3_CONFIG
    ROTATION_AXES = {'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.pm: MeteorTFS3PostureManager = MicroscopePostureManager(microscope=cls.microscope)

        # get the stage components
        cls.stage_bare = model.getComponent(role="stage-bare")
        cls.stage = cls.pm.sample_stage

        # get the metadata
        stage_md = cls.stage_bare.getMetadata()
        cls.stage_grid_centers = stage_md[model.MD_SAMPLE_CENTERS]
        cls.stage_loading = stage_md[model.MD_FAV_POS_DEACTIVE]

    def test_switching_movements(self):
        """Test switching between different postures and check that the 3D transformations work as expected"""
        if self.pm.current_posture.value == UNKNOWN:
            f = self.stage_bare.moveAbs(self.stage_grid_centers[POSITION_NAMES[GRID_1]])
            f.result()

        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()

        self.assertEqual(self.pm.current_posture.value, SEM_IMAGING)
        self._test_3d_transformations()

        f = self.pm.cryoSwitchSamplePosition(MILLING)
        f.result()
        self.assertEqual(self.pm.current_posture.value, MILLING)
        self._test_3d_transformations()

        f = self.pm.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()

        self.assertEqual(self.pm.current_posture.value, FM_IMAGING)

        self._test_3d_transformations()

    def _test_3d_transformations(self):
        """Test that the 3D transforms work the same as the 2D transforms for 0 scan rotation"""
        # 3d transforms should produce the same result as the 2d transforms
        self.pm.use_3d_transforms = False # make sure we're using 2D transforms
        stage_pos = self.stage_bare.position.value
        ssp = self.pm.to_sample_stage_from_stage_position(stage_pos)    # new 2D method
        ssp2 = self.pm.to_sample_stage_from_stage_position2(stage_pos)  # new 3D method
        ssp3 = self.pm._get_sample_pos(stage_pos)                       # old 2D method

        # assert near Position
        self.assertTrue(isNearPosition(ssp, ssp2, axes={"x", "y", "z"}))
        self.assertTrue(isNearPosition(ssp, ssp3, axes={"x", "y", "z"}))

    def test_to_posture(self):
        """Test that posture projection is the same as moving to the posture"""

        # first move back to grid-1 to make sure we are in a known position
        f = self.stage_bare.moveAbs(self.stage_grid_centers[POSITION_NAMES[GRID_1]])
        f.result()

        # move to SEM imaging posture
        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()

        pos = self.stage_bare.position.value
        milling_pos = self.pm.to_posture(pos, MILLING)
        fm_pos = self.pm.to_posture(pos, FM_IMAGING)

        self.assertEqual(self.pm.getCurrentPostureLabel(pos), SEM_IMAGING)
        self.assertEqual(self.pm.getCurrentPostureLabel(milling_pos), MILLING)
        self.assertEqual(self.pm.getCurrentPostureLabel(fm_pos), FM_IMAGING)

        # move to positions and check that they are close to the expected positions
        # milling
        f = self.pm.cryoSwitchSamplePosition(MILLING)
        f.result()

        milling_pos_after_move = self.stage_bare.position.value
        self.assertTrue(isNearPosition(milling_pos_after_move, milling_pos,
                                       axes={"x", "y", "z", "rx", "rz"}))

        # fm
        f = self.pm.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()

        fm_pos_after_move = self.stage_bare.position.value
        self.assertTrue(isNearPosition(fm_pos_after_move, fm_pos,
                                       axes={"x", "y", "z", "rx", "rz"}))

    def test_sample_stage_movement(self):
        """Test sample stage movements in different postures match the expected movements"""

        f = self.stage_bare.moveAbs(self.stage_grid_centers[POSITION_NAMES[GRID_1]])
        f.result()

        dx, dy = 50e-6, 50e-6
        self.pm.use_3d_transforms = True
        for posture in [FM_IMAGING, SEM_IMAGING]:

            if self.pm.current_posture.value is not posture:
                f = self.pm.cryoSwitchSamplePosition(posture)
                f.result()

            f = self.pm.cryoSwitchSamplePosition(GRID_1)
            f.result()
            time.sleep(2) # simulated stage moves too fast, needs time to update

            # test relative movement
            init_ss_pos = self.stage.position.value
            init_sb_pos = self.stage_bare.position.value

            f = self.stage.moveRel({"x": dx, "y": dy})
            f.result()
            time.sleep(2)

            new_pos = self.stage.position.value
            new_sb_pos = self.stage_bare.position.value

            # expected movement is along the x, y axes
            self.assertAlmostEqual(new_pos["x"], init_ss_pos["x"] + dx, places=5)
            self.assertAlmostEqual(new_pos["y"], init_ss_pos["y"] + dy, places=5)

            # manually calculate the expected stage bare position
            p = [dx, dy, 0]

            tf = self.pm._inv_transforms2[posture] # to-stage bare

            q = numpy.dot(tf, p)
            exp_sb_pos = {
                "x": init_sb_pos["x"] + q[0],
                "y": init_sb_pos["y"] + q[1],
                "z": init_sb_pos["z"] + q[2],
                "rx": init_sb_pos["rx"],
                "rz": init_sb_pos["rz"]}

            # expected movement is projection of the movement along the x, y axes
            self.assertTrue(isNearPosition(new_sb_pos, exp_sb_pos,
                                        axes={"x", "y", "z", "rx", "rz"}))

            # test absolute movement
            f = self.pm.cryoSwitchSamplePosition(GRID_1)
            f.result()
            time.sleep(2) # simulated stage moves too fast, needs time to update

            abs_pos = init_ss_pos.copy()
            abs_pos["x"] += dx
            abs_pos["y"] += dy

            f = self.stage.moveAbs(abs_pos)
            f.result()
            time.sleep(2)

            new_pos = self.stage.position.value
            new_sb_pos = self.stage_bare.position.value

            self.assertTrue(isNearPosition(new_pos, abs_pos,
                                                  axes={"x", "y", "z"}))
            self.assertTrue(isNearPosition(new_sb_pos, exp_sb_pos,
                                                  axes={"x", "y", "z", "rx", "rz"}))

        return

    def test_transformation_calculation(self):
        """Simple tests for 3D transform calculations"""

        tf, tf_inv = get_rotation_transforms(rx=0)
        self.assertEqual(tf.shape, (3, 3))
        self.assertEqual(tf_inv.shape, (3, 3))
        numpy.testing.assert_array_almost_equal(tf, numpy.eye(3))
        numpy.testing.assert_array_almost_equal(tf_inv, numpy.eye(3))

        # rotation around x-axis
        rx = math.radians(45)
        tf, tf_inv = get_rotation_transforms(rx=rx)
        tf_rx = numpy.array(
                [[1, 0, 0],
                [0, numpy.cos(rx), -numpy.sin(rx)],
                [0, numpy.sin(rx), numpy.cos(rx)]])
        numpy.testing.assert_array_almost_equal(tf, tf_rx)
        numpy.testing.assert_array_almost_equal(tf_inv, numpy.linalg.inv(tf_rx))

        # rotation around z-axis
        rz = math.radians(180)
        tf, tf_inv = get_rotation_transforms(rz=rz)
        tf_rz = numpy.array([
            [numpy.cos(rz), -numpy.sin(rz), 0],
            [numpy.sin(rz), numpy.cos(rz), 0],
            [0, 0, 1]])
        numpy.testing.assert_array_almost_equal(tf, tf_rz)
        numpy.testing.assert_array_almost_equal(tf_inv, numpy.linalg.inv(tf_rz))

        # multiply two rotations (rz, rx)
        tf, tf_inv = get_rotation_transforms(rx=rx, rz=rz)
        tf_2 = numpy.dot(tf_rz, tf_rx)
        numpy.testing.assert_array_almost_equal(tf, tf_2)
        numpy.testing.assert_array_almost_equal(tf_inv, numpy.linalg.inv(tf_2))

    def test_scan_rotation(self):
        # TODO: implement once completed
        pass

    def test_stage_to_chamber(self):
        """Test the sample-stage to chamber transformation used for vertical movements."""

        # go to sem imaging
        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        time.sleep(2)

        # calculate the vertical shift in chamber coordinates
        shift = {"x": 100e-6, "z": 50e-6}
        zshift = self.pm._transformFromChamberToStage(shift)

        # calculate axis components
        theta = self.stage_bare.position.value["rx"] # tilt, in radians (stage-bare)
        dy = shift["z"] * math.sin(theta)
        dz = shift["z"] / math.cos(theta)
        expected_vshift = {"x": shift["x"], "y": dy, "z": dz}

        # check that the transformation is correct
        for axis in expected_vshift.keys():
            self.assertAlmostEqual(zshift[axis], expected_vshift[axis], places=5)


class TestMeteorTescan1Move(TestMeteorTFS1Move):
    """
    Test the MeteorPostureManager functions for Tescan 1
    """
    MIC_CONFIG = METEOR_TESCAN1_CONFIG
    ROTATION_AXES = {'rx', 'rz'}

    def test_switching_consistency(self):
        """Test if switching to and from sem results in the same stage coordinates"""
        # Update the stage metadata according to the example
        self.stage.updateMetadata({model.MD_CALIB: {"x_0": 1.77472e-03, "y_0": -0.05993e-03, "b_y": -0.297e-03,
                                                    "z_ct": 4.774e-03, "dx": -40.1e-03, "dy": 0.157e-03,
                                                    "version": "tescan_1"}})
        self.stage.updateMetadata({model.MD_FAV_SEM_POS_ACTIVE: {"rx": 0.349065850, "rz": 0.523598775}})  # 20°, 30°
        self.stage.updateMetadata(
            {model.MD_FAV_FM_POS_ACTIVE: {"rx": 0.261799, "rz": -2.6179938779914944}})  # 15°, -150°
        self.linked_stage.updateMetadata({model.MD_ROTATION: 0.6981317})  # pre-tilt 40°
        sem_positions = [{"x": -4.413e-03, "y": -2.13888e-03, "z": 29.95268e-03, "rx": 0.349065850, "rz": 0.523598775},
                         {"x": -0.413e-03, "y": -3.139e-03, "z": 30.637e-03, "rx": 0.349065850, "rz": 0.523598775},
                         {"x": -5.413e-03, "y": -2.139e-03, "z": 29.953e-03, "rx": 0.349065850, "rz": 0.523598775}
                         ]
        # corresponding fm positions
        fm_positions = [
            {"x": -32.137e-03, "y": -12.741e-03, "z": 29.243e-03, "rx": 0.261799, "rz": -2.6179938779914944},
            {"x": -36.137e-03, "y": -12.147e-03, "z": 29.909e-03, "rx": 0.261799, "rz": -2.6179938779914944},
            {"x": -31.137e-03, "y": -12.745e-03, "z": 29.243e-03, "rx": 0.261799, "rz": -2.6179938779914944}
            ]
        for i in range(len(sem_positions)):
            # move to sem
            sem_position = sem_positions[i]
            self.stage.moveAbs(sem_position).result()
            current_stage_position = self.stage.position.value
            current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
            self.assertEqual(SEM_IMAGING, current_imaging_mode)
            for axis in sem_position.keys():
                self.assertAlmostEqual(sem_position[axis], current_stage_position[axis], places=4)
            # move to fm
            f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
            f.result()
            current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
            self.assertEqual(FM_IMAGING, current_imaging_mode)
            fm_position = fm_positions[i]
            current_stage_position = self.stage.position.value
            for axis in fm_position.keys():
                self.assertAlmostEqual(fm_position[axis], current_stage_position[axis], places=4)
            # move back to sem
            f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
            f.result()
            current_stage_position = self.stage.position.value
            current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
            self.assertEqual(SEM_IMAGING, current_imaging_mode)
            for axis in sem_position.keys():
                self.assertAlmostEqual(sem_position[axis], current_stage_position[axis], places=4)

    def test_moving_in_grid1_fm_imaging_area_after_loading(self):
        """Check if the stage moves in the right direction when moving in the fm imaging grid 1 area."""
        super().test_moving_in_grid1_fm_imaging_area_after_loading()

        # move in the same imaging mode using linked YZ stage
        old_stage_pos = self.stage.position.value
        old_linked_yz_pos = self.linked_stage.position.value
        self.linked_stage.moveRel({"y": 1.0e-3}).result()
        new_stage_pos = self.stage.position.value
        new_linked_yz_pos = self.linked_stage.position.value

        self.assertAlmostEqual(old_linked_yz_pos["y"] + 1.0e-3, new_linked_yz_pos["y"], places=3)
        self.assertTrue(old_stage_pos["y"] < new_stage_pos["y"])

        # the stage moved in the right direction if the pre-tilt and tilt angles were maintained
        beta = math.radians(40)
        alpha = math.radians(15)
        ratio = math.cos(alpha + beta) / math.sin(beta)
        estimated_ratio = (old_stage_pos["y"] - new_stage_pos["y"]) / (
                old_stage_pos["z"] - new_stage_pos["z"])  # delta y/ delta z
        self.assertAlmostEqual(ratio, estimated_ratio, places=3)

    def test_unknown_label_at_initialization(self):
        arbitrary_position = {'rx': 0.0, 'rz': math.radians(-60), 'x': 0, 'y': 0, 'z': 40.e-3}
        self.stage.moveAbs(arbitrary_position).result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(UNKNOWN, current_imaging_mode)
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(current_grid, None)

    def test_stage_to_chamber(self):
        shift = {"x": 100e-6, "z": 50e-6}
        zshift = self.posture_manager._transformFromChamberToStage(shift)
        self.assertAlmostEqual(zshift["x"], shift["x"], places=5)
        self.assertAlmostEqual(zshift["z"], shift["z"], places=5)


class TestGetDifferenceFunction(unittest.TestCase):
    """
    This class is to test _getDistance() function in the move module
    """

    @classmethod
    def setUpClass(cls):
        # Backend can be any of these : Meteor/Enzel/Mimas
        testing.start_backend(METEOR_TFS1_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.posture_manager = MicroscopePostureManager(microscope=cls.microscope)

    def test_only_linear_axes(self):
        point1 = {'x': 0.023, 'y': 0.032, 'z': 0.01}
        point2 = {'x': 0.082, 'y': 0.01, 'z': 0.028}
        pos1 = numpy.array([point1[a] for a in list(point1.keys())])
        pos2 = numpy.array([point2[a] for a in list(point2.keys())])
        expected_distance = scipy.spatial.distance.euclidean(pos1, pos2)
        actual_distance = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(expected_distance, actual_distance)

    def test_only_linear_axes_but_without_difference(self):
        point1 = {'x': 0.082, 'y': 0.01, 'z': 0.028}
        point2 = {'x': 0.082, 'y': 0.01, 'z': 0.028}
        expected_distance = 0
        actual_distance = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(expected_distance, actual_distance)

    def test_only_linear_axes_but_without_common_axes(self):
        point1 = {'x': 0.023, 'y': 0.032}
        point2 = {'x': 0.023, 'y': 0.032, 'z': 1}
        expected_distance = 0
        actual_distance = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(expected_distance, actual_distance)

    def test_only_rotation_axes(self):
        point1 = {'rx': numpy.radians(30), 'rz': 0}  # 30 degree
        point2 = {'rx': numpy.radians(60), 'rz': 0}  # 60 degree
        # the rotation difference is 30 degree
        exp_rot_dist = ROT_DIST_SCALING_FACTOR * numpy.radians(30)
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

        # Same in the other direction
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

    def test_rotation_axes_no_difference(self):
        point1 = {'rx': 0, 'rz': numpy.radians(30)}  # 30 degree
        point2 = {'rx': 0, 'rz': numpy.radians(30)}  # 30 degree
        # the rotation difference is 0 degree
        exp_rot_error = 0
        act_rot_error = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_error, act_rot_error)

        # Same in the other direction
        act_rot_error = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(exp_rot_error, act_rot_error)

    def test_rotation_axes_missing_axis(self):
        point1 = {'rx': numpy.radians(30), 'rz': numpy.radians(30)}  # 30 degree
        # No rx => doesn't count it
        point2 = {'rz': numpy.radians(60)}  # 60 degree
        exp_rot_dist = ROT_DIST_SCALING_FACTOR * numpy.radians(30)
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

        # Same in the other direction
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

    def test_no_common_axes(self):
        point1 = {'rx': numpy.radians(30), 'rz': numpy.radians(30)}
        point2 = {'x': 0.082, 'y': 0.01}
        with self.assertRaises(ValueError):
            self.posture_manager._getDistance(point1, point2)

    def test_lin_rot_axes(self):
        point1 = {'rx': 0, 'rz': numpy.radians(30), 'x': -0.02, 'y': 0.05, 'z': 0.019}
        point2 = {'rx': 0, 'rz': numpy.radians(60), 'x': -0.01, 'y': 0.05, 'z': 0.019}
        # The rotation difference is 30 degree
        # The linear difference is 0.01
        exp_dist = ROT_DIST_SCALING_FACTOR * numpy.radians(30) + 0.01
        act_dist = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(exp_dist, act_dist)

        # Same in the other direction
        act_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_dist, act_dist)

    def test_get_progress(self):
        """
        Test getMovementProgress function behaves as expected
        """
        start_point = {'x': 0, 'y': 0, 'z': 0}
        end_point = {'x': 2, 'y': 2, 'z': 2}
        current_point = {'x': 1, 'y': 1, 'z': 1}
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))

        current_point = {'x': .998, 'y': .999, 'z': .999}  # slightly off the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))

        current_point = {'x': 3, 'y': 3, 'z': 3}  # away from the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)

        current_point = {'x': 1, 'y': 1, 'z': 3}  # away from the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)

        current_point = {'x': -1, 'y': 0, 'z': 0}  # away from the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)

    def test_get_progress_lin_rot(self):
        """
        Test getMovementProgress return sorted values along a path with linear and
        rotational axes.
        """
        # Test also rotations
        start_point = {'x': 0, 'rx': 0, 'rz': 0}
        point_1 = {'x': 0.5, 'rx': 0.1, 'rz': -0.1}
        point_2 = {'x': 1, 'rx': 0.1, 'rz': -0.1}  # middle
        point_3 = {'x': 1.5, 'rx': 0.18, 'rz': -0.19}
        end_point = {'x': 2, 'rx': 0.2, 'rz': -0.2}

        # start_point = 0 < Point 1 < Point 2 < Point 3 < 1 = end_point
        progress_0 = self.posture_manager.getMovementProgress(start_point, start_point, end_point)
        self.assertAlmostEqual(progress_0, 0)

        progress_1 = self.posture_manager.getMovementProgress(point_1, start_point, end_point)

        # Point 2 should be in the middle
        progress_2 = self.posture_manager.getMovementProgress(point_2, start_point, end_point)
        self.assertTrue(util.almost_equal(progress_2, 0.5, rtol=RTOL_PROGRESS))

        progress_3 = self.posture_manager.getMovementProgress(point_3, start_point, end_point)

        progress_end = self.posture_manager.getMovementProgress(end_point, start_point, end_point)
        self.assertAlmostEqual(progress_end, 1)

        assert progress_0 < progress_1 < progress_2 < progress_3 < progress_end


class TestMoveUtil(unittest.TestCase):
    """
    This class is to test movement utilities in the move module
    """

    def test_isNearPosition(self):
        """
        Test isNearPosition function behaves as expected
        """

        # negative tests (not near)
        start = {'x': 0.023, 'y': 0.032, 'z': 0.01, "rx": 0, "rz": 0}
        end = {'x': 0.024, 'y': 0.033, 'z': 0.015, "rx": 0.12213888553625313, "rz": 5.06145}

        self.assertFalse(isNearPosition(start, end, {'x'}))
        self.assertFalse(isNearPosition(start, end, {'y'}))
        self.assertFalse(isNearPosition(start, end, {'z'}))
        self.assertFalse(isNearPosition(start, end, {'rx'}))
        self.assertFalse(isNearPosition(start, end, {'rz'}))

        # positive tests (is near)
        start = {'x': 0.023, 'y': 0.32, 'z': 0.01, "rx": 0, "rz": 0}
        end = {'x': 0.023 + 0.09e-6, 'y': 0.32 + 0.09e-6, 'z': 0.01, "rx": 0 + 0.5e-3, "rz": 0 + 0.5e-3}

        self.assertTrue(isNearPosition(start, end, {'x'}))
        self.assertTrue(isNearPosition(start, end, {'y'}))
        self.assertTrue(isNearPosition(start, end, {'z'}))
        self.assertTrue(isNearPosition(start, end, {'rx'}))
        self.assertTrue(isNearPosition(start, end, {'rz'}))

        # test user defined tolerance
        start = {'x': 20e-6, 'y': 0.032, 'z': 0.01, "rx": 0, "rz": 5.043996}
        end = {'x': 22e-6, 'y': 0.06, 'z': 0.015, "rx": 0.12213888553625313, "rz": 5.06145}

        # true
        self.assertTrue(isNearPosition(start, end, {'x', 'rz'},
                                       atol_linear=ATOL_LINEAR_TRANSFORM,
                                       atol_rotation=ATOL_ROTATION_TRANSFORM))

        # false
        self.assertFalse(isNearPosition(start, end, {'y', 'rx'},
                                        atol_linear=ATOL_LINEAR_TRANSFORM,
                                        atol_rotation=ATOL_ROTATION_TRANSFORM))


if __name__ == "__main__":
    unittest.main()
