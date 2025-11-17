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
import logging
import os
import unittest

import odemis
from odemis import model
from odemis.acq.move import (FM_IMAGING, GRID_1, GRID_2,
                             LOADING, SEM_IMAGING, UNKNOWN, POSITION_NAMES,
                             MeteorPostureManager)
from odemis.acq.move import MicroscopePostureManager
from odemis.util import testing


logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TFS1_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"

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


if __name__ == "__main__":
    unittest.main()
