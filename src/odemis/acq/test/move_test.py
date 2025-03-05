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
                             MimasPostureManager, MeteorPostureManager, EnzelPostureManager, MeteorTFS3PostureManager)
from odemis.acq.move import MicroscopePostureManager
from odemis.util import testing
from odemis.util.driver import ATOL_LINEAR_POS, isNearPosition
from odemis.util.transform import _get_transforms

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"
METEOR_TFS1_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"
METEOR_TFS2_CONFIG = CONFIG_PATH + "sim/meteor-tfs2-sim.odm.yaml"
METEOR_TFS3_CONFIG = CONFIG_PATH + "sim/meteor-tfs3-sim.odm.yaml"
METEOR_ZEISS1_CONFIG = CONFIG_PATH + "sim/meteor-zeiss-sim.odm.yaml"
METEOR_TESCAN1_CONFIG = CONFIG_PATH + "sim/meteor-tescan-sim.odm.yaml"
MIMAS_CONFIG = CONFIG_PATH + "sim/mimas-sim.odm.yaml"


class TestEnzelMove(unittest.TestCase):
    """
    Test EnzelPostureManager functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        testing.start_backend(ENZEL_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.posture_manager = MicroscopePostureManager(microscope=cls.microscope)

        # find components by their role
        cls.stage = model.getComponent(role="stage")
        cls.aligner = model.getComponent(role="align")

        cls.stage_active = cls.stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        cls.stage_deactive = cls.stage.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.stage_coating = cls.stage.getMetadata()[model.MD_FAV_POS_COATING]
        cls.stage_alignment = cls.stage.getMetadata()[model.MD_FAV_POS_ALIGN]
        cls.stage_sem_imaging = cls.stage.getMetadata()[model.MD_FAV_POS_SEM_IMAGING]
        cls.stage_3beams = cls.posture_manager.get3beamsSafePos(cls.stage.getMetadata()[model.MD_FAV_POS_ACTIVE],
                                                                SAFETY_MARGIN_5DOF)
        cls.align_deactive = cls.aligner.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.align_alignment = cls.aligner.getMetadata()[model.MD_FAV_POS_ALIGN]
        cls.align_active = cls.aligner.getMetadata()[model.MD_FAV_POS_ACTIVE]
        cls.align_3beams = cls.posture_manager.get3beamsSafePos(cls.aligner.getMetadata()[model.MD_FAV_POS_ACTIVE],
                                                                SAFETY_MARGIN_3DOF)

        # Make sure the lens is referenced too (small move will only complete after the referencing)
        cls.aligner.moveRelSync({"x": 1e-6})

        # The 5DoF stage is not referenced automatically, so let's do it now
        if not all(cls.stage.referenced.value.values()):
            stage_axes = set(cls.stage.axes.keys())
            cls.stage.reference(stage_axes).result()

        # Set custom value that works well within the simulator range
        cls.rx_angle = 0.3
        cls.rz_angle = 0.1

    def test_sample_switch_procedures(self):
        """
        Test moving the sample stage from loading position to both imaging, alignment and coating, then back to loading
        """
        stage = self.stage
        align = self.aligner
        # Check the instantiation of correct posture manager
        self.assertIsInstance(self.posture_manager, EnzelPostureManager)
        # Get the stage to loading position
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_deactive,
                                        atol=ATOL_LINEAR_POS)
        # Align should be parked
        testing.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)

        # Get the stage to coating position
        f = self.posture_manager.cryoSwitchSamplePosition(COATING)
        f.result()
        filter_dict = lambda keys, d: {key: d[key] for key in keys}
        testing.assert_pos_almost_equal(filter_dict({'x', 'y', 'z'}, stage.position.value),
                                        filter_dict({'x', 'y', 'z'}, self.stage_coating), atol=ATOL_LINEAR_POS)
        testing.assert_pos_almost_equal(filter_dict({'rx', 'rz'}, stage.position.value),
                                        filter_dict({'rx', 'rz'}, self.stage_coating), atol=ATOL_LINEAR_POS)
        # align should be in deactive position
        testing.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)

        # Get the stage to alignment position
        f = self.posture_manager.cryoSwitchSamplePosition(ALIGNMENT)
        f.result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_alignment, atol=ATOL_LINEAR_POS,
                                        match_all=False)

        # Get the stage to 3beams position
        f = self.posture_manager.cryoSwitchSamplePosition(THREE_BEAMS)
        f.result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_3beams, atol=ATOL_LINEAR_POS, match_all=False)
        testing.assert_pos_almost_equal(align.position.value, self.align_3beams, atol=ATOL_LINEAR_POS)

        # Get the stage to alignment position
        f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_sem_imaging, atol=ATOL_LINEAR_POS,
                                        match_all=False)

        # Switch back to loading position
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_deactive, atol=ATOL_LINEAR_POS)

    def test_align_switch_procedures(self):
        """
        Test moving the sample stage from loading position to both imaging, alignment and coating, then back to loading
        """
        align = self.aligner
        # Get the stage to loading position
        f = self.posture_manager._cryoSwitchAlignPosition(LOADING)
        f.result()
        testing.assert_pos_almost_equal(align.position.value, self.align_deactive,
                                        atol=ATOL_LINEAR_POS)

        # Get the stage to imaging position
        f = self.posture_manager._cryoSwitchAlignPosition(THREE_BEAMS)
        f.result()
        testing.assert_pos_almost_equal(align.position.value, self.align_3beams,
                                        atol=ATOL_LINEAR_POS)

        # Get the stage to imaging position
        f = self.posture_manager._cryoSwitchAlignPosition(ALIGNMENT)
        f.result()
        testing.assert_pos_almost_equal(align.position.value, self.align_alignment,
                                        atol=ATOL_LINEAR_POS)

    def test_cancel_loading(self):
        """
        Test cryoSwitchSamplePosition movement cancellation is handled correctly
        """
        stage = self.stage
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        f = self.posture_manager.cryoSwitchSamplePosition(THREE_BEAMS)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        testing.assert_pos_not_almost_equal(stage.position.value, self.stage_deactive,
                                            atol=ATOL_LINEAR_POS)

        stage = self.stage
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        f = self.posture_manager.cryoSwitchSamplePosition(COATING)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        testing.assert_pos_not_almost_equal(stage.position.value, self.stage_coating,
                                            atol=ATOL_LINEAR_POS)

    def test_get_current_aligner_position(self):
        """
        Test _getCurrentAlignerPositionLabel() function behaves as expected
        """
        aligner = self.aligner
        # Move to loading position
        self.check_move_aligner_to_target(LOADING)

        # Move to imaging position and cancel the movement before reaching there
        f = self.posture_manager._cryoSwitchAlignPosition(THREE_BEAMS)
        time.sleep(5)
        f.cancel()
        pos_label = self.posture_manager._getCurrentAlignerPositionLabel()
        self.assertEqual(pos_label, LOADING_PATH)

        # simulate moving to unknown position by moving in opposite to deactive-active line
        unknown_pos = copy.copy(self.align_active)
        unknown_pos['y'] += 0.005
        unknown_pos['z'] += 0.005
        self.aligner.moveAbs(unknown_pos).result()
        pos_label = self.posture_manager._getCurrentAlignerPositionLabel()
        self.assertEqual(pos_label, UNKNOWN)
        # moving to either imaging/alignment positions shouldn't be allowed
        with self.assertRaises(ValueError):
            f = self.posture_manager._cryoSwitchAlignPosition(THREE_BEAMS)
            f.result()

        with self.assertRaises(ValueError):
            f = self.posture_manager._cryoSwitchAlignPosition(ALIGNMENT)
            f.result()

        # Move to alignment position: the aligner actually reports "three beams"
        # as everything near the optical active position reports this.
        self.posture_manager._cryoSwitchAlignPosition(LOADING).result()  # First move to LOADING to allow next move
        f = self.posture_manager._cryoSwitchAlignPosition(ALIGNMENT)
        f.result()
        pos_label = self.posture_manager._getCurrentAlignerPositionLabel()
        self.assertEqual(pos_label, THREE_BEAMS)

        # Move from loading to imaging position
        self.posture_manager._cryoSwitchAlignPosition(LOADING).result()
        self.check_move_aligner_to_target(THREE_BEAMS)

    def check_move_aligner_to_target(self, target):
        f = self.posture_manager._cryoSwitchAlignPosition(target)
        f.result()
        pos_label = self.posture_manager._getCurrentAlignerPositionLabel()
        self.assertEqual(pos_label, target)

    def test_get_current_position(self):
        """
        Test getCurrentPositionLabel function behaves as expected
        """
        stage = self.stage
        # Move to loading position
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, LOADING)

        # Move to imaging position and cancel the movement before reaching there
        f = self.posture_manager.cryoSwitchSamplePosition(THREE_BEAMS)
        # wait just long enough for the referencing to complete
        time.sleep(7)
        f.cancel()
        pos_label = self.posture_manager.getCurrentPostureLabel()
        # It's really hard to get the timing right, so also allow to be at loading
        # or three-beams
        self.assertIn(pos_label, (THREE_BEAMS, LOADING, LOADING_PATH))

        # Move to imaging position
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        self.posture_manager.cryoSwitchSamplePosition(THREE_BEAMS).result()
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, THREE_BEAMS)

        # Move away the align (only), and check that this is now considered an UNKNOWN position
        f = self.posture_manager._cryoSwitchAlignPosition(LOADING)
        f.result(30)
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, UNKNOWN)

        # Test disabled, because typically ALIGNMENT is the same as
        # THREE_BEAMS, so it's not possible to differentiate them.
        # Move to alignment
        # f = cryoSwitchSamplePosition(ALIGNMENT)
        # f.result()
        # pos_label = getCurrentPostureLabel(stage.position.value, stage)
        # self.assertEqual(pos_label, ALIGNMENT)

        # Move to SEM imaging
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        f = self.posture_manager.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, SEM_IMAGING)

        # Move to coating position
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        f = self.posture_manager.cryoSwitchSamplePosition(COATING)
        f.result()
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, COATING)

        # Return to loading and cancel before reaching
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        time.sleep(4)
        f.cancel()
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, LOADING_PATH)

    def test_smaract_stage_fallback_movement(self):
        """
        Test behaviour of smaract 5dof stage when the linear axes are near the maximum range
        """
        # 1. Move to imaging position
        self.posture_manager.cryoSwitchSamplePosition(THREE_BEAMS).result()
        # 2. Move the stage linear axes to their max range + move rx from 0
        self.posture_manager._cryoSwitchAlignPosition(LOADING).result()
        self.stage.moveAbs(
            {'x': self.stage.axes['x'].range[1], 'y': self.stage.axes['y'].range[1], 'z': self.stage.axes['z'].range[1],
             'rx': 0.15}).result()
        # 3. Move to loading where the ordered submoves would start from rx/rx, resulting in an invalid move
        # exception if it's not handled
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        testing.assert_pos_almost_equal(self.stage.position.value, self.stage_deactive,
                                        atol=ATOL_LINEAR_POS)


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
        """Check if the stage moves in the right direction when moving in the fm imaging grid 1 area."""
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


class TestMeteorTFS2Move(TestMeteorTFS1Move):
    """
    Test the MeteorPostureManager functions for TFS 2
    """
    MIC_CONFIG = METEOR_TFS2_CONFIG
    ROTATION_AXES = {'rx', 'rz'}

    def test_moving_in_grid1_fm_imaging_area_after_loading(self):
        """Check if the stage moves in the right direction when moving in the fm imaging grid 1 area."""
        super().test_moving_in_grid1_fm_imaging_area_after_loading()
        linked_stage_before = self.linked_stage.position.value

        # move in the same imaging mode using linked YM stage
        old_stage_pos = self.stage.position.value
        self.linked_stage.moveRel({"y": 1e-3}).result()
        new_stage_pos = self.stage.position.value

        # Check if stage moves in a constant FM imaging plane
        self.assertAlmostEqual(self.linked_stage.position.value["z"], linked_stage_before["z"], places=5)

        # check if the stage moves in the right direction with the given pre-tilt
        beta = self.stage.getMetadata()[model.MD_CALIB][model.MD_SAMPLE_PRE_TILT]

        estimated_beta = math.atan2(new_stage_pos["z"] - old_stage_pos["z"], new_stage_pos["y"] - old_stage_pos["y"])
        # estimated beta depends on the flatness of the grid and how the shuttle was loaded in the grid
        self.assertAlmostEqual(beta, estimated_beta, places=2, msg="The stage moved in the wrong direction in "
                                                                   "the FM imaging grid 1 area.")

    def test_log_linked_stage_positions(self):
        # In simulator the Z of Meteor stage values are stable but not in hardware
        # log values in hardware to check the stability of z in meteor stage
        # Ideally the z of meteor stage should be same for different values in FM imaging
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result()
        # move to the fm imaging area
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(FM_IMAGING, current_imaging_mode)
        # move to the fm imaging area GRID 1
        f = self.posture_manager.cryoSwitchSamplePosition(GRID_1)
        f.result()
        current_imaging_mode = self.posture_manager.getCurrentGridLabel()
        self.assertEqual(GRID_1, current_imaging_mode)

        logging.debug("Stage position in GRID 1 x: %s y:%s, z:%s", self.linked_stage.position.value["x"],
                      self.linked_stage.position.value["y"], self.linked_stage.position.value["z"])
        # 0.6 mm radius is TFS millable area of one grid
        for n in range(1, 7):
            logging.debug("Moving y in Stage position in GRID 1 y: %s",
                          self.linked_stage.position.value["y"] + 0.6e-03 / n)
            self.linked_stage.moveAbs({"y": self.linked_stage.position.value["y"] + 0.6e-03 / n}).result()
            logging.debug("Stage position in GRID 1 x: %s y:%s, z:%s", self.linked_stage.position.value["x"],
                          self.linked_stage.position.value["y"], self.linked_stage.position.value["z"])

    def test_unknown_label_at_initialization(self):
        pass

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
        self.assertTrue(isNearPosition(ssp, ssp2, axes={"x", "y", "z", "rx", "rz"}))
        self.assertTrue(isNearPosition(ssp, ssp3, axes={"x", "y", "z", "rx", "rz"}))

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
                                                  axes={"x", "y", "z", "rx", "rz"}))
            self.assertTrue(isNearPosition(new_sb_pos, exp_sb_pos,
                                                  axes={"x", "y", "z", "rx", "rz"}))

        return

    def test_transformation_calculation(self):
        """Simple tests for 3D transform calculations"""

        tf, tf_inv = _get_transforms(0)
        self.assertEqual(tf.shape, (3, 3))
        self.assertEqual(tf_inv.shape, (3, 3))
        numpy.testing.assert_array_almost_equal(tf, numpy.eye(3))
        numpy.testing.assert_array_almost_equal(tf_inv, numpy.eye(3))

        tf, tf_inv = _get_transforms(r=math.radians(45))
        r = math.radians(45)
        tf2 = numpy.array(
                [[1, 0, 0],
                [0, numpy.cos(r), -numpy.sin(r)],
                [0, numpy.sin(r), numpy.cos(r)]])
        numpy.testing.assert_array_almost_equal(tf, tf2)
        numpy.testing.assert_array_almost_equal(tf_inv, numpy.linalg.inv(tf2))

    def test_scan_rotation(self):
        # TODO: implement once completed
        pass

    def test_component_metadata_update(self):
        pass # TODO:

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
                                                    "z_ct": 4.774e-03, "dx": -40.1e-03, "dy": 0.157e-03}})
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


class TestMimasMove(unittest.TestCase):
    """
    Test the MimasPostureManager functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        testing.start_backend(MIMAS_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.posture_manager = MicroscopePostureManager(microscope=cls.microscope)

        # find components by their role
        cls.stage = model.getComponent(role="stage")
        cls.aligner = model.getComponent(role="align")
        cls.gis = model.getComponent(role="gis")

        cls.stage_active = cls.stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        cls.stage_deactive = cls.stage.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.align_deactive = cls.aligner.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.align_active = cls.aligner.getMetadata()[model.MD_FAV_POS_ACTIVE]

        # The 5DoF stage is not referenced automatically, so let's do it now
        if not all(cls.stage.referenced.value.values()):
            stage_axes = set(cls.stage.axes.keys())
            cls.stage.reference(stage_axes).result()

        # Make sure the lens is referenced too (small move will only complete after the referencing)
        cls.aligner.moveRelSync({"z": 1e-6})

    def test_sample_switch_procedures(self):
        """
        Test moving the sample stage from loading position to both imaging, alignment and coating, then back to loading
        """
        stage = self.stage
        align = self.aligner
        gis_choices = self.gis.axes["arm"].choices

        # Check the instantiation of correct posture manager
        self.assertIsInstance(self.posture_manager, MimasPostureManager)

        # Get the stage to loading position
        self.posture_manager.cryoSwitchSamplePosition(LOADING).result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_deactive,
                                        atol=ATOL_LINEAR_POS)
        # Align should be parked
        testing.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)
        # GIS should be parked
        self.assertEqual(gis_choices[self.gis.position.value["arm"]], "parked")

        # Get the stage to coating position
        f = self.posture_manager.cryoSwitchSamplePosition(COATING)
        f.result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_active, atol=ATOL_LINEAR_POS)
        # GIS engaged
        self.assertEqual(gis_choices[self.gis.position.value["arm"]], "engaged")
        # Align should be parked
        testing.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, COATING)

        # Go to FLM
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        f.result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_active, atol=ATOL_LINEAR_POS, match_all=False)
        # Align should be engaged
        testing.assert_pos_almost_equal(align.position.value, self.align_active, atol=ATOL_LINEAR_POS)
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, FM_IMAGING)

        # Move a little bit around => still in FM_IMAGING
        stage.moveRelSync({"x": 100e-6, "y": -100e-6, "z": 1e-6})
        current_pos = self.stage.position.value
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, FM_IMAGING)

        # Get the stage to FIB
        f = self.posture_manager.cryoSwitchSamplePosition(MILLING)
        f.result()
        # The stage shouldn't have moved
        testing.assert_pos_almost_equal(stage.position.value, current_pos)
        # Align should be parked
        testing.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, MILLING)

        # Test the progress update
        # Note: it hasn't started yet, but it can be already more than 0%, as by moving
        # around the stage in imaging mode, it might have gotten closer to the DEACTIVE
        # position
        progress_before = self.posture_manager.getMovementProgress(stage.position.value, self.stage_active,
                                                                   self.stage_deactive)
        self.assertLess(progress_before, 0.2)

        # Switch back to loading position
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)

        # Progress should be just a little bit more than before
        progress_start = self.posture_manager.getMovementProgress(stage.position.value, self.stage_active,
                                                                  self.stage_deactive)
        self.assertTrue(0 <= progress_before <= progress_start < 0.5)

        f.result()
        testing.assert_pos_almost_equal(stage.position.value, self.stage_deactive, atol=ATOL_LINEAR_POS)

        # Progress should now be arrived => 100%
        progress_end = self.posture_manager.getMovementProgress(stage.position.value, self.stage_active,
                                                                self.stage_deactive)
        self.assertAlmostEqual(progress_end, 1)

    def test_cancel_loading(self):
        """
        Test cryoSwitchSamplePosition movement cancellation is handled correctly
        """
        stage = self.stage
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result(30)

        logging.debug("Switching to FM IMAGING")
        f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)

        # It shouldn't be in LOADING position anymore, and not in FM_IMAGING yet
        # For now, we don't test it, because the stage simulator of the MIMAS is
        # very crude and doesn't simulate cancellation and move duration properly.
        # testing.assert_pos_not_almost_equal(stage.position.value, self.stage_deactive,
        #                                     atol=ATOL_LINEAR_POS)
        pos_label = self.posture_manager.getCurrentPostureLabel()
        # self.assertNotEqual(pos_label, LOADING)
        self.assertNotEqual(pos_label, FM_IMAGING)
        # Should report UNKNOWN if cancelled early, and IMAGING if cancelled later
        # self.assertEqual(pos_label, (UNKNOWN, IMAGING))

        # It should be allowed to go back to LOADING
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result(30)
        self.assertTrue(f.done())
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, LOADING)

    def test_unknown(self):
        """
        When in UNKNOWN position, it's only allowed to go to LOADING
        """
        stage = self.stage
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result(30)

        # Move a little away to be "nowhere" known
        stage.moveRelSync({"x": 1e-3})

        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, UNKNOWN)

        # Moving to FM_IMAGING should not be allowed from UNKNOWN
        with self.assertRaises(ValueError):
            f = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
            f.result()

        with self.assertRaises(ValueError):
            f = self.posture_manager.cryoSwitchSamplePosition(MILLING)
            f.result()

        # Going to LOADING is fine
        f = self.posture_manager.cryoSwitchSamplePosition(LOADING)
        f.result(30)
        pos_label = self.posture_manager.getCurrentPostureLabel()
        self.assertEqual(pos_label, LOADING)


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
