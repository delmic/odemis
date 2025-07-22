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

import json
import logging
import os
import random
import unittest

import numpy

from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    FeaturesDecoder,
    get_features_dict,
    read_features,
    save_features,
    load_milling_tasks,
    FEATURE_READY_TO_MILL,
    MILLING,
    REFERENCE_IMAGE_FILENAME,
)
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

    def tearDown(self):
        if os.path.exists(self.path):
            filename = os.path.join(self.path, f"TestFeature-1-{REFERENCE_IMAGE_FILENAME}")
            if os.path.exists(filename):
                os.remove(filename)
            os.rmdir(self.path)

    def test_feature_encoder(self):
        feature1 = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        feature2 = CryoFeature("Feature-2", stage_position={"x": 1e-3, "y": 1e-3, "z": 1e-3}, fm_focus_position={"z": 2e-3})
        feature1.milling_tasks = {}
        feature2.milling_tasks = {}
        features = [feature1, feature2]
        json_str = json.dumps(get_features_dict(features))
        self.assertEqual(json_str, TEST_FEATURES_STR)

    def test_feature_decoder(self):
        features = json.loads(TEST_FEATURES_STR, cls=FeaturesDecoder)
        self.assertEqual(len(features), 2)
        self.assertEqual(features[0].name.value, "Feature-1")
        self.assertEqual(features[0].status.value, "Active")
        self.assertEqual(features[1].stage_position.value, {"x": 1e-3, "y": 1e-3, "z": 1e-3})
        self.assertEqual(features[1].fm_focus_position.value, {"z": 2e-3})

    def test_save_read_features(self):
        feature1 = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        feature2 = CryoFeature("Feature-2", stage_position={"x": 1e-3, "y": 1e-3, "z": 1e-3}, fm_focus_position={"z": 2e-3})

        features = [feature1, feature2]
        save_features("", features)
        r_features = read_features("")
        self.assertEqual(len(features), len(r_features))
        self.assertEqual(features[0].name.value, r_features[0].name.value)

    def test_feature_milling_tasks(self):
        feature = CryoFeature(
            name="TestFeature-1",
            stage_position={"x": 50e-6, "y": 25e-6, "z": 32e-3, "rx": 0.61, "rz": 0},
            fm_focus_position={"z": 1.69e-3}
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
        self.assertEqual(feature.get_posture_position(MILLING), stage_position)
        self.assertEqual(feature.status.value, FEATURE_READY_TO_MILL)
        self.assertEqual(set(feature.milling_tasks.keys()), set(milling_tasks.keys()))

        # assert directory and file is created
        self.assertTrue(os.path.exists(feature.path))

        filename = os.path.join(feature.path, f"{feature.name.value}-{REFERENCE_IMAGE_FILENAME}")
        self.assertTrue(os.path.exists(filename))

if __name__ == "__main__":
    unittest.main()
