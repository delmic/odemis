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

import odemis
from odemis import model
from odemis.acq.move import GRID_1, GRID_2, UNKNOWN, POSITION_NAMES
from odemis.acq.test import move_tfs1_test

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"

METEOR_ZEISS1_CONFIG = CONFIG_PATH + "sim/meteor-zeiss-sim.odm.yaml"


class TestMeteorZeiss1Move(move_tfs1_test.TestMeteorTFS1Move):
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
        fm_grid_1_pos = self.posture_manager._transformFromSEMToMeteor(grid1_pos)
        self.stage.moveAbs(fm_grid_1_pos).result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_1, current_grid)
        fm_grid_2_pos = self.posture_manager._transformFromSEMToMeteor(grid2_pos)
        self.stage.moveAbs(fm_grid_2_pos).result()
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_2, current_grid)

    def test_unknown_label_at_initialization(self):
        # Find a position that is not in any defined posture i.e. it is outside the imaging range
        arbitrary_position = {"x": 0.0, "y": 0.0, "z": 0.0e-3}
        self.stage.moveAbs(arbitrary_position).result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(UNKNOWN, current_imaging_mode)
        current_grid = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(current_grid, None)


if __name__ == "__main__":
    unittest.main()
