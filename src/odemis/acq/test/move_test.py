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
import os
import time
import unittest

import odemis
from odemis import model
from odemis import util
from odemis.acq.move import ATOL_LINEAR_POS, ATOL_ROTATION_POS, RTOL_PROGRESS, cryoSwitchAlignPosition, \
    getCurrentAlignerPositionLabel, SEM_IMAGING, UNKNOWN
from odemis.acq.move import LOADING, IMAGING, ALIGNMENT, COATING, LOADING_PATH
from odemis.acq.move import cryoTiltSample, cryoSwitchSamplePosition, getMovementProgress, getCurrentPositionLabel
from odemis.util import test

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"


class TestCryoMove(unittest.TestCase):
    """
    Test cryoSwitchSamplePosition and cryoTiltSample functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(ENZEL_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.stage = model.getComponent(role="stage")
        cls.aligner = model.getComponent(role="align")

        cls.stage_active = cls.stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        cls.stage_deactive = cls.stage.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.stage_coating = cls.stage.getMetadata()[model.MD_FAV_POS_COATING]
        cls.stage_alignment = cls.stage.getMetadata()[model.MD_FAV_POS_ALIGN]
        cls.stage_sem_imaging = cls.stage.getMetadata()[model.MD_FAV_POS_SEM_IMAGING]
        cls.align_deactive = cls.aligner.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.align_alignment = cls.aligner.getMetadata()[model.MD_FAV_POS_ALIGN]
        cls.align_active = cls.aligner.getMetadata()[model.MD_FAV_POS_ACTIVE]

        # Make sure the lens is referenced too (small move will only complete after the referencing)
        cls.aligner.moveRelSync({"x": 1e-6})

        # The 5DoF stage is not referenced automatically, so let's do it now
        if not all(cls.stage.referenced.value.values()):
            stage_axes = set(cls.stage.axes.keys())
            cls.stage.reference(stage_axes).result()

        # Set custom value that works well within the simulator range
        cls.rx_angle = 0.3
        cls.rz_angle = 0.1

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_sample_switch_procedures(self):
        """
        Test moving the sample stage from loading position to both imaging, alignment and coating, then back to loading
        """
        stage = self.stage
        align = self.aligner
        # Get the stage to loading position
        cryoSwitchSamplePosition(LOADING).result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_deactive,
                                     atol=ATOL_LINEAR_POS)
        # Align should be parked
        test.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)

        # Get the stage to imaging position
        cryoSwitchSamplePosition(IMAGING).result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_active, atol=ATOL_LINEAR_POS, match_all=False)
        # align should be in active position
        test.assert_pos_almost_equal(align.position.value, self.align_active, atol=ATOL_LINEAR_POS)

        # Get the stage to coating position
        f = cryoSwitchSamplePosition(COATING)
        f.result()
        filter_dict = lambda keys, d: {key: d[key] for key in keys}
        test.assert_pos_almost_equal(filter_dict({'x', 'y', 'z'}, stage.position.value),
                                     filter_dict({'x', 'y', 'z'}, self.stage_coating), atol=ATOL_LINEAR_POS)
        test.assert_pos_almost_equal(filter_dict({'rx', 'rz'}, stage.position.value),
                                     filter_dict({'rx', 'rz'}, self.stage_coating), atol=ATOL_LINEAR_POS)
        # align should be in deactive position
        test.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)

        # Get the stage to alignment position
        f = cryoSwitchSamplePosition(ALIGNMENT)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_alignment, atol=ATOL_LINEAR_POS, match_all=False)

        # Get the stage to alignment position
        f = cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_sem_imaging, atol=ATOL_LINEAR_POS, match_all=False)

        # Switch back to loading position
        cryoSwitchSamplePosition(LOADING).result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_deactive, atol=ATOL_LINEAR_POS)

    def test_align_switch_procedures(self):
        """
        Test moving the sample stage from loading position to both imaging, alignment and coating, then back to loading
        """
        align = self.aligner
        # Get the stage to loading position
        f = cryoSwitchAlignPosition(LOADING)
        f.result()
        test.assert_pos_almost_equal(align.position.value, self.align_deactive,
                                     atol=ATOL_LINEAR_POS)

        # Get the stage to imaging position
        f = cryoSwitchAlignPosition(IMAGING)
        f.result()
        test.assert_pos_almost_equal(align.position.value, self.align_active,
                                     atol=ATOL_LINEAR_POS)

        # Get the stage to imaging position
        f = cryoSwitchAlignPosition(ALIGNMENT)
        f.result()
        test.assert_pos_almost_equal(align.position.value, self.align_alignment,
                                     atol=ATOL_LINEAR_POS)

    def test_tilting_procedures(self):
        """
        Test moving the sample stage from imaging position to tilting position and back to imaging
        """
        stage = self.stage
        align = self.aligner
        # Test tilting from imaging
        # Get the stage to imaging position
        cryoSwitchSamplePosition(LOADING).result()
        cryoSwitchSamplePosition(IMAGING).result()

        # Tilt the stage on rx only
        f = cryoTiltSample(rx=self.rx_angle)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {'rx': self.rx_angle},
                                     match_all=False,
                                     atol=ATOL_ROTATION_POS)

        # Tilt the stage on rx and rz
        f = cryoTiltSample(rx=self.rx_angle, rz=self.rz_angle)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {'rx': self.rx_angle, 'rz': self.rz_angle},
                                     match_all=False,
                                     atol=ATOL_ROTATION_POS)
        # align should be in deactive position
        test.assert_pos_almost_equal(align.position.value, self.align_deactive, atol=ATOL_LINEAR_POS)

    def test_invalid_switch_movements(self):
        """
        Test it's not possible to do some disallowed switch movements
        """
        # Test tilting from loading
        cryoSwitchSamplePosition(LOADING).result()
        with self.assertRaises(ValueError):
            f = cryoTiltSample(rx=self.rx_angle, rz=self.rz_angle)
            f.result()

    def test_cancel_loading(self):
        """
        Test cryoSwitchSamplePosition movement cancellation is handled correctly
        """
        stage = self.stage
        cryoSwitchSamplePosition(LOADING).result()
        f = cryoSwitchSamplePosition(IMAGING)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        test.assert_pos_not_almost_equal(stage.position.value, self.stage_deactive,
                                         atol=ATOL_LINEAR_POS)

        stage = self.stage
        cryoSwitchSamplePosition(LOADING).result()
        f = cryoSwitchSamplePosition(COATING)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        test.assert_pos_not_almost_equal(stage.position.value, self.stage_coating,
                                         atol=ATOL_LINEAR_POS)

    def test_cancel_tilting(self):
        """
        Test cryoTiltSample movement cancellation is handled correctly
        """
        stage = self.stage
        cryoSwitchSamplePosition(LOADING).result()
        cryoSwitchSamplePosition(IMAGING).result()
        f = cryoTiltSample(rx=self.rx_angle, rz=self.rz_angle)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        test.assert_pos_not_almost_equal(stage.position.value, {'rx': self.rx_angle, 'rz': self.rz_angle},
                                         match_all=False,
                                         atol=ATOL_ROTATION_POS)

    def test_get_progress(self):
        """
        Test getMovementProgress function behaves as expected
        """
        start_point = {'x': 0, 'y': 0, 'z': 0}
        end_point = {'x': 2, 'y': 2, 'z': 2}
        current_point = {'x': 1, 'y': 1, 'z': 1}
        progress = getMovementProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))
        current_point = {'x': .998, 'y': .999, 'z': .999}  # slightly off the line
        progress = getMovementProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))
        current_point = {'x': 3, 'y': 3, 'z': 3}  # away from the line
        progress = getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)
        current_point = {'x': 1, 'y': 1, 'z': 3}  # away from the line
        progress = getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)
        current_point = {'x': -1, 'y': 0, 'z': 0}  # away from the line
        progress = getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)

    def test_get_current_aligner_position(self):
        """
        Test getCurrentPositionLabel function behaves as expected
        """
        aligner = self.aligner
        # at start the aligner wouldn't be in one of the predefined positions
        pos_label = getCurrentAlignerPositionLabel(aligner.position.value, aligner)
        self.assertTrue(pos_label in (LOADING_PATH, UNKNOWN))
        # Move to loading position
        self.test_move_aligner_to_target(LOADING)

        # Move to imaging position and cancel the movement before reaching there
        f = cryoSwitchAlignPosition(IMAGING)
        time.sleep(5)
        f.cancel()
        pos_label = getCurrentAlignerPositionLabel(aligner.position.value, aligner)
        self.assertEqual(pos_label, LOADING_PATH)

        # simulate moving to unknown position by moving in opposite to deactive-active line
        unknown_pos = copy.copy(self.align_active)
        unknown_pos['y'] += 0.005
        unknown_pos['z'] += 0.005
        self.aligner.moveAbs(unknown_pos).result()
        pos_label = getCurrentAlignerPositionLabel(aligner.position.value, aligner)
        self.assertEqual(pos_label, UNKNOWN)
        # moving to either imaging/alignment positions shouldn't be allowed
        with self.assertRaises(ValueError):
            f = cryoSwitchAlignPosition(IMAGING)
            f.result()

        with self.assertRaises(ValueError):
            f = cryoSwitchAlignPosition(ALIGNMENT)
            f.result()

        # Move to alignment position
        cryoSwitchAlignPosition(LOADING).result()
        self.test_move_aligner_to_target(ALIGNMENT)

        # from alignment to loading
        cryoSwitchAlignPosition(LOADING).result()

        # Move to imaging position
        self.test_move_aligner_to_target(IMAGING)

    def test_move_aligner_to_target(self, target):
        f = cryoSwitchAlignPosition(target)
        f.result()
        pos_label = getCurrentAlignerPositionLabel(self.aligner.position.value, self.aligner)
        self.assertEqual(pos_label, target)

    def test_get_current_position(self):
        """
        Test getCurrentPositionLabel function behaves as expected
        """
        stage = self.stage
        # Move to loading position
        cryoSwitchSamplePosition(LOADING).result()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, LOADING)

        # Move to imaging position and cancel the movement before reaching there
        f = cryoSwitchSamplePosition(IMAGING)
        # abit long wait for the loading-imaging referencing to finish
        time.sleep(7)
        f.cancel()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, LOADING_PATH)

        # Move to imaging position
        cryoSwitchSamplePosition(LOADING).result()
        cryoSwitchSamplePosition(IMAGING).result()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, IMAGING)

        # Move to alignment
        f = cryoSwitchSamplePosition(ALIGNMENT)
        f.result()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, ALIGNMENT)

        # Move to SEM imaging
        f = cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, SEM_IMAGING)

        # Move to coating position
        cryoSwitchSamplePosition(LOADING).result()
        f = cryoSwitchSamplePosition(COATING)
        f.result()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, COATING)

        # Return to loading and cancel before reaching
        f = cryoSwitchSamplePosition(LOADING)
        time.sleep(4)
        f.cancel()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, LOADING_PATH)

    def test_smaract_stage_fallback_movement(self):
        """
        Test behaviour of smaract 5dof stage when the linear axes are near the maximum range
        """
        # 1. Move to imaging position
        cryoSwitchSamplePosition(IMAGING).result()
        # 2. Move the stage linear axes to their max range + move rx from 0
        self.focus.moveAbs(self.focus_deactive).result()
        self.stage.moveAbs({'x': self.stage.axes['x'].range[1], 'y': self.stage.axes['y'].range[1], 'z': self.stage.axes['z'].range[1], 'rx': 0.15}).result()
        # 3. Move to loading where the ordered submoves would start from rx/rx, resulting in an invalid move
        # exception if it's not handled
        cryoSwitchSamplePosition(LOADING).result()
        test.assert_pos_almost_equal(self.stage.position.value, self.stage_deactive,
                                     atol=ATOL_LINEAR_POS)


if __name__ == "__main__":
    unittest.main()
