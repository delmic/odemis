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
import time
import unittest

import odemis
from odemis import model
from odemis import util
from odemis.acq.move import ATOL_LINEAR_POS, ATOL_ROTATION_POS, RTOL_PROGRESS
from odemis.acq.move import LOADING, IMAGING, MILLING, COATING, LOADING_PATH
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
        cls.focus = model.getComponent(role="focus")
        cls.aligner = model.getComponent(role="align")

        cls.stage_active = cls.stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        cls.stage_deactive = cls.stage.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.stage_coating = cls.stage.getMetadata()[model.MD_FAV_POS_COATING]
        cls.focus_deactive = cls.focus.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.align_deactive = cls.aligner.getMetadata()[model.MD_FAV_POS_DEACTIVE]
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

    def test_loading_procedures(self):
        """
        Test moving the sample stage from loading position to both imaging and coating, then back to loading
        """
        stage = self.stage
        focus = self.focus
        align = self.aligner
        # Get the stage to loading position
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_deactive,
                                     atol=ATOL_LINEAR_POS)
        # Focus should be parked
        test.assert_pos_almost_equal(focus.position.value, self.focus_deactive, atol=ATOL_LINEAR_POS)

        # Get the stage to imaging position
        f = cryoSwitchSamplePosition(IMAGING)
        f.result()
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

        # Switch back to loading position
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_deactive, atol=ATOL_LINEAR_POS)

    def test_tilting_procedures(self):
        """
        Test moving the sample stage from imaging position to tilting position and back to imaging
        """
        stage = self.stage
        align = self.aligner
        # Test tilting from imaging
        # Get the stage to imaging position
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        f = cryoSwitchSamplePosition(IMAGING)
        f.result()

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

        # Test imaging from tilting
        f = cryoTiltSample(rx=0)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_active, atol=ATOL_LINEAR_POS, match_all=False)
        # align should be in active position
        test.assert_pos_almost_equal(align.position.value, self.align_active, atol=ATOL_LINEAR_POS)

    def test_invalid_switch_movements(self):
        """
        Test it's not possible to do some disallowed switch movements
        """
        # Test tilting from loading
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        with self.assertRaises(ValueError):
            f = cryoTiltSample(rx=self.rx_angle, rz=self.rz_angle)
            f.result()

    def test_cancel_loading(self):
        """
        Test cryoSwitchSamplePosition movement cancellation is handled correctly
        """
        stage = self.stage
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        f = cryoSwitchSamplePosition(IMAGING)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        test.assert_pos_not_almost_equal(stage.position.value, self.stage_deactive,
                                         atol=ATOL_LINEAR_POS)

        stage = self.stage
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
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
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        f = cryoSwitchSamplePosition(IMAGING)
        f.result()
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

    def test_get_current_position(self):
        """
        Test getCurrentPositionLabel function behaves as expected
        """
        stage = self.stage
        # Move to loading position
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
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
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        f = cryoSwitchSamplePosition(IMAGING)
        f.result()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, IMAGING)

        # Move to tilting
        f = cryoTiltSample(rx=self.rx_angle, rz=self.rz_angle)
        f.result()
        pos_label = getCurrentPositionLabel(stage.position.value, stage)
        self.assertEqual(pos_label, MILLING)

        # Move to coating position
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
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
        f = cryoSwitchSamplePosition(IMAGING)
        f.result()
        # 2. Move the stage linear axes to their max range + move rx from 0
        self.focus.moveAbs(self.focus_deactive).result()
        self.stage.moveAbs({'x': self.stage.axes['x'].range[1], 'y': self.stage.axes['y'].range[1], 'z': self.stage.axes['z'].range[1], 'rx': 0.15}).result()
        # 3. Move to loading where the ordered submoves would start from rx/rx, resulting in an invalid move
        # exception if it's not handled
        f = cryoSwitchSamplePosition(LOADING)
        f.result()
        test.assert_pos_almost_equal(self.stage.position.value, self.stage_deactive,
                                     atol=ATOL_LINEAR_POS)


if __name__ == "__main__":
    unittest.main()
