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
import math
import os
import unittest

from odemis.util import testing

import odemis
from odemis import model
from odemis.acq.move import (FM_IMAGING, LOADING, MILLING, SEM_IMAGING, UNKNOWN)
from odemis.acq.test.move_tfs1_test import TestMeteorTFS1Move

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TESCAN1_CONFIG = CONFIG_PATH + "sim/meteor-tescan-sim.odm.yaml"
METEOR_TESCAN1_SHUTTER_CONFIG = CONFIG_PATH + "sim/meteor-tescan-fibsem-stage-sim.odm.yaml"


class TestMeteorTescan1Move(TestMeteorTFS1Move):
    """
    Test the MeteorPostureManager functions for Tescan 1
    """
    MIC_CONFIG = METEOR_TESCAN1_CONFIG
    ROTATION_AXES = {'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        """Set up the test case with Tescan 1 configuration"""
        super().setUpClass()
        cls.stage_bare_md = cls.stage.getMetadata()

    def setUp(self):
        super().setUp()
        # reset stage-bare metadata, so that even if a test modifies it, the next test starts fresh
        self.stage.updateMetadata(self.stage_bare_md)

    def test_switching_consistency(self):
        """Test if switching to and from sem results in the same stage coordinates"""

        # Update the stage metadata according to the example
        # Note: this works for switching posture because the metadata is re-read every time in cryoSwitchSamplePosition()
        # However, for moving along the sample stage, this would not be sufficient, as the transformations are cached.
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

    def test_rel_move_fm_posture(self):
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)

        # relative moves in sample stage coordinates
        sample_stage_moves = [
            {"x": 10e-6, "y": 0},
            {"x": 0, "y": 10e-6},
        ]
        # corresponding stage-bare relative moves (based on "ground truth" tested on hardware)
        stage_bare_moves = [
            {"x": 10e-6, "y": 0, "z": 0},
            {"x": 0, "y": 5.9e-6, "z": 6.7e-6},  # 40° pre-tilt
        ]
        for m_sample, m_bare in zip(sample_stage_moves, stage_bare_moves):
            old_bare_pos = self.stage.position.value
            self.linked_stage.moveRel(m_sample).result()
            new_bare_pos = self.stage.position.value

            exp_bare_pos = old_bare_pos.copy()
            for axis in m_bare.keys():
                exp_bare_pos[axis] += m_bare[axis]
            testing.assert_pos_almost_equal(new_bare_pos, exp_bare_pos, atol=1e-6)

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


class TestMeteorTescan1ShutterMove(TestMeteorTFS1Move):
    """
    Test the MeteorPostureManager functions for the Tescan 1 shutter.
    """
    MIC_CONFIG = METEOR_TESCAN1_SHUTTER_CONFIG
    ROTATION_AXES = {'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        """Set up the test case with Tescan 1 configuration"""
        super().setUpClass()
        cls.stage_bare_md = cls.stage.getMetadata()

    def setUp(self):
        super().setUp()
        # reset stage-bare metadata, so that even if a test modifies it, the next test starts fresh
        self.stage.updateMetadata(self.stage_bare_md)

    def test_fm_shutter_control(self):
        """Test shutter state changes during FM mode transitions."""
        if self.posture_manager.shutter is None:
            self.skipTest("Shutter not available")

        # Move to safe start position
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()

        # Ensure shutter is engaged before engaging the objective, to make it more interesting
        self.posture_manager.shutter.value = True  # True = engaged (closed)
        # Move to FM_IMAGING and check shutter is retracted
        self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING).result()
        self.assertEqual(self.posture_manager.shutter.value, False, "Shutter should be retracted for FM")

        # Move to MILLING and check shutter is in the protecting state
        self.posture_manager.cryoSwitchSamplePosition(MILLING).result()
        self.assertEqual(self.posture_manager.shutter.value, True, "Shutter should be protecting for milling")

        # Move back to SEM_IMAGING and check shutter is retracted
        self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING).result()
        self.assertEqual(self.posture_manager.shutter.value, False, "Shutter should be retracted for SEM")


if __name__ == "__main__":
    unittest.main()
