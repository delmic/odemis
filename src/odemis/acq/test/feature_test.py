# -*- coding: utf-8 -*-
"""
Created on Oct 2021

Copyright © Delmic

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
import random
import unittest
from unittest import mock

import numpy

from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    resolve_fm_focus_position,
    resolve_stage_bare_position,
    load_milling_tasks,
    FEATURE_READY_TO_MILL,
    REFERENCE_IMAGE_FILENAME,
)
from odemis.acq.move import Posture
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH

logging.getLogger().setLevel(logging.DEBUG)

# store the test-features as json for easier editting
TEST_FEATURES_PATH = os.path.join(os.path.dirname(__file__), "test-features.json")
with open(TEST_FEATURES_PATH, "r") as f:
    TEST_FEATURES_STR = f.read()

class TestFeatureEncoderDecoder(unittest.TestCase):
    """
    Test the json encoder and decoder of the CryoFeature class
    """
    path = ""

    def test_posture_conversion_uses_stored_source_posture(self):
        """Test conversion without inferring posture from feature coordinates."""
        source_position = {"x": 1.0, "y": 2.0, "z": 3.0}
        target_position = {"x": 4.0, "y": 5.0, "z": 6.0}
        feature = CryoFeature("Feature-1")
        feature.set_stage_bare_position(Posture.FM_IMAGING, source_position)
        posture_manager = mock.Mock()
        posture_manager.to_posture.return_value = target_position

        position = resolve_stage_bare_position(
            posture_manager,
            feature,
            Posture.SEM_IMAGING,
        )

        self.assertEqual(position, target_position)
        posture_manager.to_posture.assert_called_once_with(
            source_position,
            Posture.SEM_IMAGING,
            source_posture=Posture.FM_IMAGING,
        )

    def test_posture_conversion_requires_source_position(self):
        """Test that conversion fails when the feature has no stage position."""
        feature = CryoFeature("Feature-1")

        with self.assertRaisesRegex(ValueError, "has no stage position to convert"):
            resolve_stage_bare_position(
                mock.Mock(),
                feature,
                Posture.SEM_IMAGING,
            )

    def test_resolve_fm_focus_position(self):
        """Test that resolving an FM focus position stores the fallback."""
        feature = CryoFeature("Feature-1")
        fallback_position = {"z": 1.7e-3}

        position = resolve_fm_focus_position(
            feature,
            Posture.FM_IMAGING,
            fallback_position,
        )
        fallback_position["z"] = 0

        self.assertEqual(position, {"z": 1.7e-3})
        self.assertEqual(
            feature.get_focus_position(Posture.FM_IMAGING),
            {"z": 1.7e-3},
        )

    def tearDown(self):
        if os.path.exists(self.path):
            filename = os.path.join(self.path, f"TestFeature-1-{REFERENCE_IMAGE_FILENAME}")
            if os.path.exists(filename):
                os.remove(filename)
            os.rmdir(self.path)

    def test_feature_milling_tasks(self):
        fm_focus_position = {"z": 1.7e-3}
        feature = CryoFeature(name="TestFeature-1")
        feature.set_focus_position(Posture.FM_IMAGING, fm_focus_position)
        fm_focus_position["z"] = 0
        focus_changes = []
        feature.focus_position_changed.subscribe(focus_changes.append)
        feature.set_focus_position(Posture.FIB_VIEW_FM, {"z": 2.7e-3})
        self.assertIn(Posture.FM_IMAGING, feature.posture_positions)
        self.assertEqual(feature.get_focus_position(Posture.FM_IMAGING), {"z": 1.7e-3})
        self.assertEqual(feature.get_focus_position(Posture.FIB_VIEW_FM), {"z": 2.7e-3})
        self.assertEqual(
            focus_changes,
            [
                (Posture.FIB_VIEW_FM, {"z": 2.7e-3}),
            ],
        )

        stage_position = {"x": 25e-6, "y": 40e-6, "z": 32e-3, "rx": 0.31, "rz": 0}
        self.path = os.path.join(os.getcwd(), feature.name.value)
        reference_image = model.DataArray(numpy.zeros(shape=(1024, 1536)), metadata={})
        milling_tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)

        # randomly remove some milling tasks (to simulate user choice)
        task_name = random.choice(list(milling_tasks.keys()))
        del milling_tasks[task_name]

        # save milling task data
        feature.save_milling_task_data(
            stage_position=stage_position,
            path=self.path,
            reference_image=reference_image,
            milling_tasks=milling_tasks
        )

        self.assertEqual(feature.path, self.path)
        self.assertEqual(feature.reference_image.shape, reference_image.shape)
        self.assertEqual(feature.get_stage_bare_position(Posture.MILLING), stage_position)
        self.assertEqual(feature.status.value, FEATURE_READY_TO_MILL)
        self.assertEqual(set(feature.milling_tasks.keys()), set(milling_tasks.keys()))

        # assert directory and file is created
        self.assertTrue(os.path.exists(feature.path))

        filename = os.path.join(feature.path, f"{feature.name.value}-{REFERENCE_IMAGE_FILENAME}")
        self.assertTrue(os.path.exists(filename))

if __name__ == "__main__":
    unittest.main()
