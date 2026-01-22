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
import time
import unittest

import numpy

import odemis
from odemis import model
from odemis.acq.move import (FM_IMAGING, GRID_1, MILLING, SEM_IMAGING, UNKNOWN, POSITION_NAMES,
                             MeteorTFS3PostureManager, LOADING)
from odemis.acq.move import MicroscopePostureManager
from odemis.util import testing
from odemis.util.driver import isNearPosition

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TFS3_CONFIG = CONFIG_PATH + "sim/meteor-tfs3-sim.odm.yaml"
METEOR_TFS3_FIBSEM_CONFIG = CONFIG_PATH + "sim/meteor-fibsem-sim.odm.yaml"


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
        cls.stage_md = cls.stage_bare.getMetadata()
        cls.stage_grid_centers = cls.stage_md[model.MD_SAMPLE_CENTERS]
        cls.stage_loading = cls.stage_md[model.MD_FAV_POS_DEACTIVE]

    def setUp(self):
        # reset to a known posture before each test
        if self.pm.current_posture.value == UNKNOWN:
            logging.info("Test setup: posture is UNKNOWN, resetting to SEM_IMAGING")
            # Reset to loading position before each test
            f = self.pm.cryoSwitchSamplePosition(LOADING)
            f.result()
            # From loading, going to SEM IMAGING will use GRID 1 as base position
            f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
            f.result()

    def test_switching_movements(self):
        """Test switching between different postures and check that the 3D transformations work as expected"""
        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()

        self.assertEqual(self.pm.current_posture.value, SEM_IMAGING)

        if model.MD_FAV_MILL_POS_ACTIVE in self.stage_md:
            f = self.pm.cryoSwitchSamplePosition(MILLING)
            f.result()
            self.assertEqual(self.pm.current_posture.value, MILLING)

        f = self.pm.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()

        self.assertEqual(self.pm.current_posture.value, FM_IMAGING)

    def test_to_posture(self):
        """Test that posture projection is the same as moving to the posture"""

        # move to SEM imaging posture
        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()

        # first move back to grid-1 to make sure we are in a known position
        f = self.stage_bare.moveAbs(self.stage_grid_centers[POSITION_NAMES[GRID_1]])
        f.result()

        # Check that getCurrentPostureLabel() with a given stage-bare position returns the expected posture
        pos = self.stage_bare.position.value
        self.assertEqual(self.pm.getCurrentPostureLabel(pos), SEM_IMAGING)

        fm_pos = self.pm.to_posture(pos, FM_IMAGING)
        self.assertEqual(self.pm.getCurrentPostureLabel(fm_pos), FM_IMAGING)

        if model.MD_FAV_MILL_POS_ACTIVE in self.stage_md:
            milling_pos = self.pm.to_posture(pos, MILLING)
            self.assertEqual(self.pm.getCurrentPostureLabel(milling_pos), MILLING)

        # Move to the postures and check that the position is close to the expected positions
        f = self.pm.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        fm_pos_after_move = self.stage_bare.position.value
        self.assertTrue(isNearPosition(fm_pos_after_move, fm_pos,
                                       axes={"x", "y", "z", "rx", "rz"}))

        if model.MD_FAV_MILL_POS_ACTIVE in self.stage_md:
            f = self.pm.cryoSwitchSamplePosition(MILLING)
            f.result()
            milling_pos_after_move = self.stage_bare.position.value
            self.assertTrue(isNearPosition(milling_pos_after_move, milling_pos,
                                           axes={"x", "y", "z", "rx", "rz"}))

    def test_sample_stage_movement(self):
        """Test sample stage movements in different postures match the expected movements"""
        # move to SEM/GRID 1
        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        f = self.stage_bare.moveAbs(self.stage_grid_centers[POSITION_NAMES[GRID_1]])
        f.result()

        dx, dy = 50e-6, 50e-6
        for posture in [SEM_IMAGING, FM_IMAGING]:

            if self.pm.current_posture.value != posture:
                f = self.pm.cryoSwitchSamplePosition(posture)
                f.result()

            f = self.pm.cryoSwitchSamplePosition(GRID_1)
            f.result()
            time.sleep(0.1) # simulated stage moves too fast, needs time to update

            # test relative movement
            init_ss_pos = self.stage.position.value
            init_sb_pos = self.stage_bare.position.value

            f = self.stage.moveRel({"x": dx, "y": dy})
            f.result()
            time.sleep(0.1)

            new_pos = self.stage.position.value
            new_sb_pos = self.stage_bare.position.value

            # expected movement is along the x, y axes
            self.assertAlmostEqual(new_pos["x"], init_ss_pos["x"] + dx, places=5)
            self.assertAlmostEqual(new_pos["y"], init_ss_pos["y"] + dy, places=5)

            # test absolute movement
            f = self.pm.cryoSwitchSamplePosition(GRID_1)
            f.result()
            time.sleep(0.1) # simulated stage moves too fast, needs time to update

            abs_pos = init_ss_pos.copy()
            abs_pos["x"] += dx
            abs_pos["y"] += dy

            f = self.stage.moveAbs(abs_pos)
            f.result()
            time.sleep(0.1)

            new_pos = self.stage.position.value
            new_sb_pos = self.stage_bare.position.value

            self.assertTrue(isNearPosition(new_pos, abs_pos,
                                                  axes={"x", "y", "z"}))


    def test_scan_rotation(self):
        # TODO: implement once completed
        pass

    def test_stage_to_chamber(self):
        """Test the sample-stage to chamber transformation used for vertical movements."""

        # go to sem imaging
        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        time.sleep(0.1)

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

    def test_fixed_fm_z(self):
        """
        With the fixed fm plane feature, the fm imaging distance should always be the same, independent of the previous
        stage position. In this test we switch to the fm posture, coming from different stage positions.
        We check that when requesting a fixed fm plane, the fm imaging distance stays the same. No matter the previous
        stage position. Furthermore, we also check that for the same posture switches, the fm imaging distance changes
        when explicitly not requesting the fixed fm imaging plane.
        """
        translational = self.stage_grid_centers[POSITION_NAMES[GRID_1]]
        rotational = self.stage_bare.getMetadata().get(model.MD_FAV_SEM_POS_ACTIVE)
        position_base = {**translational, **rotational}

        position_a = {**position_base, "z": 0.020}
        position_b = {**position_base, "z": 0.032}

        # Test when fixing the fixed fm z, the fm sample z does not change between different positions
        # with distinct z.
        self.stage_bare.moveAbs(position_a).result()
        self.pm.cryoSwitchSamplePosition(FM_IMAGING).result()
        position_fm_a = self.stage_bare.position.value
        sample_z_a = self.pm.to_sample_stage_from_stage_position(position_fm_a, posture=FM_IMAGING)["z"]

        self.stage_bare.moveAbs(position_b).result()
        self.pm.cryoSwitchSamplePosition(FM_IMAGING).result()
        position_fm_b = self.stage_bare.position.value
        sample_z_b = self.pm.to_sample_stage_from_stage_position(position_fm_b, posture=FM_IMAGING)["z"]

        self.assertAlmostEqual(sample_z_a, sample_z_b, places=6)

        # Now clear fixed fm position to see if sample z actually changes for same scenario.
        self.stage_bare.updateMetadata({model.MD_FM_POS_SAMPLE_ACTIVE: None})
        self.stage_bare.moveAbs(position_a).result()
        self.pm.cryoSwitchSamplePosition(FM_IMAGING).result()
        position_fm_a = self.stage_bare.position.value
        sample_z_a = self.pm.to_sample_stage_from_stage_position(position_fm_a, posture=FM_IMAGING)["z"]

        self.stage_bare.moveAbs(position_b).result()
        self.pm.cryoSwitchSamplePosition(FM_IMAGING).result()
        position_fm_b = self.stage_bare.position.value
        sample_z_b = self.pm.to_sample_stage_from_stage_position(position_fm_b, posture=FM_IMAGING)["z"]

        self.assertNotAlmostEqual(sample_z_a, sample_z_b, places=6)

    def test_revert_from_fixed_fm_z(self):
        """
        Test that when requesting a fixed fm plane posture switch, we also revert to the previous stage bare position.
        """
        translational = self.stage_grid_centers[POSITION_NAMES[GRID_1]]
        rotational = self.stage_bare.getMetadata().get(model.MD_FAV_SEM_POS_ACTIVE)
        position_base = {**translational, **rotational}
        position_requested = {**position_base, "z": 0.025}
        self.stage_bare.moveAbs(position_requested).result()
        position_initial = self.stage_bare.position.value
        # Go from SEM posture to METEOR, and back. Check that we end up at the same spot as before
        self.pm.cryoSwitchSamplePosition(FM_IMAGING).result()
        self.pm.cryoSwitchSamplePosition(SEM_IMAGING).result()
        position_reverted = self.stage_bare.position.value
        testing.assert_pos_almost_equal(position_reverted, position_initial, atol=1e-9)
        # Also for milling to METEOR
        self.pm.cryoSwitchSamplePosition(MILLING).result()
        position_milling_initial = self.stage_bare.position.value
        self.pm.cryoSwitchSamplePosition(FM_IMAGING).result()
        self.pm.cryoSwitchSamplePosition(MILLING).result()
        position_reverted = self.stage_bare.position.value
        testing.assert_pos_almost_equal(position_reverted, position_milling_initial, atol=1e-9)
        self.pm.cryoSwitchSamplePosition(SEM_IMAGING).result()
        position_reverted = self.stage_bare.position.value
        testing.assert_pos_almost_equal(position_reverted, position_initial, atol=1e-9)


class TestMeteorTFS3FIBSEMMove(TestMeteorTFS3Move):
    """
    Test the MeteorPostureManager functions for TFS 3 with FIBSEM options
    """
    MIC_CONFIG = METEOR_TFS3_FIBSEM_CONFIG


if __name__ == "__main__":
    unittest.main()
