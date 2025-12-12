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
import sys
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
METEOR_JEOL1_CONFIG = CONFIG_PATH + "sim/meteor-jeol-sim.odm.yaml"


# Check using the python version, because that's easier than checking the OS version
@unittest.skipIf(sys.version_info < (3, 10), "odemis-jeol driver does not work for Ubuntu 20.04 or lower")
class TestMeteorJeol1Move(unittest.TestCase):
    """
    Test the MeteorPostureManager functions for JEOL 1
    """
    # Note: similar to the TFS1 test, but using a sample stage instead of a "linked stage".
    MIC_CONFIG = METEOR_JEOL1_CONFIG
    ROTATION_AXES = {'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.posture_manager = MicroscopePostureManager(microscope=cls.microscope)

        # get the stage components
        cls.stage_bare = model.getComponent(role="stage-bare")
        cls.stage = cls.posture_manager.sample_stage
        # get the focus component used by the optical path (sim config provides role 'focus')
        cls.focus = model.getComponent(role="focus")

    def _check_focus_position(self, fav_pos):
        """Check that the focus actuator is at the favourite position specified by fav_key.

        :param fav_pos: either model.MD_FAV_POS_ACTIVE or model.MD_FAV_POS_DEACTIVE.
        This raises a unittest failure if the metadata is missing or the z position
        does not match within 1e-4 (places=4).
        """
        focus_md = self.focus.getMetadata()
        fav = focus_md.get(fav_pos)
        self.assertIsNotNone(fav, f"Missing metadata {fav_pos} on focus component")
        # Ensure metadata contains z
        self.assertIn("z", fav, f"Favourite metadata {fav_pos} missing 'z' entry")
        self.assertAlmostEqual(self.focus.position.value["z"], fav["z"], places=4)

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
        sem_angles = self.stage_bare.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], sem_angles[axis], places=4)
        # focus should be parked (deactivated) when in SEM imaging
        self._check_focus_position(model.MD_FAV_POS_DEACTIVE)

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
        fm_angles = self.stage_bare.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], fm_angles[axis], places=4)

        # focus should be at the active favourite position when in FM imaging
        self._check_focus_position(model.MD_FAV_POS_ACTIVE)

        # move using sample stage: it should be directly passed on to stage-bare, as X&Y are always
        # in the sample stage plane on the JEOL stage, with only being inverted.
        old_raw_pos = self.stage_bare.position.value
        old_sample_pos = self.stage.position.value
        self.stage.moveRel({"y": 1e-3}).result()
        new_raw_pos = self.stage_bare.position.value
        new_sample_pos = self.stage.position.value

        self.assertAlmostEqual(old_sample_pos["y"] + 1e-3, new_sample_pos["y"], places=4)
        self.assertAlmostEqual(old_raw_pos["y"] - 1e-3, new_raw_pos["y"], places=4)

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
        fm_angles = self.stage_bare.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], fm_angles[axis], places=4)
        # focus should be at the active favourite position when in FM imaging
        self._check_focus_position(model.MD_FAV_POS_ACTIVE)

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
        sem_angles = self.stage_bare.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], sem_angles[axis], places=4)

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
        sem_angles = self.stage_bare.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], sem_angles[axis], places=4)

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
        fm_angles = self.stage_bare.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], fm_angles[axis], places=4)
        # focus should be at the active favourite position when in FM imaging
        self._check_focus_position(model.MD_FAV_POS_ACTIVE)

    def test_moving_from_grid1_to_grid2_in_fm_imaging_area(self):
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
        fm_angles = self.stage_bare.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], fm_angles[axis], places=4)

    def test_moving_from_grid2_to_grid1_in_fm_imaging_area(self):
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
        fm_angles = self.stage_bare.getMetadata()[model.MD_FAV_FM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], fm_angles[axis], places=4)

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
        sem_angles = self.stage_bare.getMetadata()[model.MD_FAV_SEM_POS_ACTIVE]
        for axis in self.ROTATION_AXES:
            self.assertAlmostEqual(self.stage_bare.position.value[axis], sem_angles[axis], places=4)
        # focus should be parked (deactivated) when in SEM imaging
        self._check_focus_position(model.MD_FAV_POS_DEACTIVE)

    def test_unknown_label_at_initialization(self):
        arbitrary_position = {"x": 0.0, "y": 0.01, "z": 0.85e-3}
        self.stage_bare.moveAbs(arbitrary_position).result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(UNKNOWN, current_imaging_mode)
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(current_grid, None)

    def test_transformFromSEMToMeteor(self):
        # assert that raises value error when no rz
        stage_md = self.stage_bare.getMetadata()
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
