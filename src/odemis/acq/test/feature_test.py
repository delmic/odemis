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
import tempfile
import unittest
from unittest import mock

import numpy

from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    FeaturesDecoder,
    create_feature_acquisition_filename,
    feature_storage_dirname,
    get_features_dict,
    load_feature_streams_from_disk,
    FEATURE_READY_TO_MILL,
    MILLING,
    REFERENCE_IMAGE_FILENAME,
    load_milling_tasks,
    read_features,
    save_features,
)
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH
from odemis.acq.project_state import (
    STREAM_ORIGIN_FILENAME_MD,
    STREAM_ORIGIN_INDEX_MD,
    get_stream_origin,
    set_stream_origin_from_raw,
)

logging.getLogger().setLevel(logging.DEBUG)

# store the test-features as json for easier editting
TEST_FEATURES_PATH = os.path.join(os.path.dirname(__file__), "test-features.json")
with open(TEST_FEATURES_PATH, "r") as f:
    TEST_FEATURES_STR = f.read()

LEGACY_TEST_FEATURES_STR = (
    '{"feature_list": ['
    '{"name": "Feature-1", "status": "Active", "stage_position": {"x": 0, "y": 0, "z": 0}, '
    '"fm_focus_position": {"z": 0}, "posture_positions": {}, "milling_tasks": {}, '
    '"correlation_data": {}, "superz_stream_name": null, "superz_focused": null}, '
    '{"name": "Feature-2", "status": "Active", "stage_position": {"x": 0.001, "y": 0.001, "z": 0.001}, '
    '"fm_focus_position": {"z": 0.002}, "posture_positions": {}, "milling_tasks": {}, '
    '"correlation_data": {}, "superz_stream_name": null, "superz_focused": null}]}'
)

class TestFeatureEncoderDecoder(unittest.TestCase):
    """
    Test the json encoder and decoder of the CryoFeature class
    """
    path = ""

    def tearDown(self):
        if os.path.exists(self.path):
            filenames = [
                os.path.join(self.path, REFERENCE_IMAGE_FILENAME),
                os.path.join(self.path, f"TestFeature-1-{REFERENCE_IMAGE_FILENAME}"),
            ]
            for filename in filenames:
                if os.path.exists(filename):
                    os.remove(filename)
            os.rmdir(self.path)

    def test_feature_encoder(self):
        feature1 = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        feature2 = CryoFeature("Feature-2", stage_position={"x": 1e-3, "y": 1e-3, "z": 1e-3}, fm_focus_position={"z": 2e-3})
        feature1.id = "feature-id-1"
        feature2.id = "feature-id-2"
        feature1.milling_tasks = {}
        feature2.milling_tasks = {}
        features = [feature1, feature2]
        json_str = json.dumps(get_features_dict(features))
        self.assertEqual(json.loads(json_str), json.loads(TEST_FEATURES_STR))

    def test_feature_decoder(self):
        features = json.loads(TEST_FEATURES_STR, cls=FeaturesDecoder)
        self.assertEqual(len(features), 2)
        self.assertEqual(features[0].name.value, "Feature-1")
        self.assertEqual(features[0].status.value, "Active")
        self.assertEqual(features[0].id, "feature-id-1")
        self.assertEqual(features[0].stream_records, [])
        self.assertEqual(features[1].stage_position.value, {"x": 1e-3, "y": 1e-3, "z": 1e-3})
        self.assertEqual(features[1].fm_focus_position.value, {"z": 2e-3})
        self.assertEqual(features[1].id, "feature-id-2")

    def test_feature_decoder_legacy_features_json(self):
        features = json.loads(LEGACY_TEST_FEATURES_STR, cls=FeaturesDecoder)
        self.assertEqual(len(features), 2)
        self.assertTrue(features[0].id)
        self.assertTrue(features[1].id)
        self.assertEqual(features[0].stream_records, [])
        self.assertEqual(features[1].stream_records, [])

    def test_set_stream_origin_from_raw_sets_origin(self):
        stream = mock.Mock()
        stream.raw = [mock.Mock(metadata={
            STREAM_ORIGIN_FILENAME_MD: "Feature-1/acq-001.ome.tiff",
            STREAM_ORIGIN_INDEX_MD: 2,
        })]

        set_stream_origin_from_raw(stream)

        self.assertEqual(get_stream_origin(stream), ("Feature-1/acq-001.ome.tiff", 2))

    def test_set_stream_origin_from_raw_ignores_missing_metadata(self):
        stream = mock.Mock()
        stream.raw = [mock.Mock(metadata={STREAM_ORIGIN_FILENAME_MD: "Feature-1/acq-001.ome.tiff"})]

        set_stream_origin_from_raw(stream)

        self.assertEqual(get_stream_origin(stream), (None, None))

    def test_save_read_features(self):
        feature1 = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        feature2 = CryoFeature("Feature-2", stage_position={"x": 1e-3, "y": 1e-3, "z": 1e-3}, fm_focus_position={"z": 2e-3})

        features = [feature1, feature2]
        with tempfile.TemporaryDirectory() as project_dir:
            save_features(project_dir, features)
            self.assertTrue(os.path.exists(os.path.join(project_dir, "project_state.json")))
            self.assertFalse(os.path.exists(os.path.join(project_dir, "features.json")))
            r_features = read_features(project_dir)
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

        filename = os.path.join(feature.path, REFERENCE_IMAGE_FILENAME)
        self.assertTrue(os.path.exists(filename))

    def test_load_feature_streams_does_not_fallback_with_project_state(self):
        feature = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        with tempfile.TemporaryDirectory() as project_dir:
            save_features(project_dir, [feature])
            legacy_filename = os.path.join(project_dir, "legacy-Feature-1-001.ome.tiff")
            # Create a sentinel file so legacy filename discovery finds a match.
            # File content is irrelevant because stream loading is mocked in this test.
            with open(legacy_filename, "w", encoding="utf-8"):
                pass

            with mock.patch("odemis.acq.feature.open_acquisition") as open_acquisition_mock:
                with mock.patch("odemis.acq.feature.data_to_static_streams") as streams_conv_mock:
                    migrated = load_feature_streams_from_disk(feature, project_dir)

            self.assertFalse(migrated)
            self.assertEqual(feature.stream_records, [])
            self.assertEqual(feature.streams.value, [])
            open_acquisition_mock.assert_not_called()
            streams_conv_mock.assert_not_called()

    def test_load_feature_streams_legacy_name_fallback(self):
        feature = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        with tempfile.TemporaryDirectory() as project_dir:
            legacy_filename = os.path.join(project_dir, "legacy-Feature-1-001.ome.tiff")
            # Create a sentinel file so legacy filename discovery finds a match.
            # File content is irrelevant because open_acquisition is mocked.
            with open(legacy_filename, "w", encoding="utf-8"):
                pass

            with mock.patch("odemis.acq.feature.get_available_formats", return_value={"TIFF": [".ome.tiff"]}):
                with mock.patch("odemis.acq.feature.open_acquisition", return_value=["data"]):
                    with mock.patch("odemis.acq.feature.data_to_static_streams", return_value=[mock.MagicMock()]):
                        migrated = load_feature_streams_from_disk(feature, project_dir)

            self.assertTrue(migrated)
            self.assertEqual(len(feature.stream_records), 1)
            self.assertEqual(len(feature.streams.value), 1)

    def test_create_feature_acquisition_filename_uses_feature_folder(self):
        feature = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        with tempfile.TemporaryDirectory() as project_dir:
            filename = create_feature_acquisition_filename(feature, os.path.join(project_dir, "acq.ome.tiff"))
            rel = os.path.relpath(filename, project_dir)
            self.assertTrue(rel.startswith(feature_storage_dirname("Feature-1") + os.sep))
            self.assertNotIn("Feature-1-", os.path.basename(filename))

    def test_feature_storage_dirname_drops_dot_segments(self):
        self.assertEqual(feature_storage_dirname("foo/../bar"), "foo_bar")
        self.assertEqual(feature_storage_dirname("./.."), "Feature")

    def test_feature_decoder_loads_legacy_reference_filename(self):
        feature_name = "Feature-1"
        with tempfile.TemporaryDirectory() as project_dir:
            feature_path = os.path.join(project_dir, feature_name)
            os.makedirs(feature_path, exist_ok=True)
            payload = {
                "feature_list": [{
                    "name": feature_name,
                    "status": "Active",
                    "stage_position": {"x": 0, "y": 0, "z": 0},
                    "fm_focus_position": {"z": 0},
                    "posture_positions": {},
                    "milling_tasks": {},
                    "correlation_data": {},
                    "path": feature_path,
                    "superz_stream_name": None,
                    "superz_focused": None,
                }]
            }
            legacy_filename = os.path.join(feature_path, f"{feature_name}-{REFERENCE_IMAGE_FILENAME}")
            modern_filename = os.path.join(feature_path, REFERENCE_IMAGE_FILENAME)
            fake_data = mock.Mock()
            fake_data.getData.return_value = "legacy-data"
            with mock.patch("odemis.acq.feature.os.path.exists", side_effect=lambda p: p == legacy_filename):
                with mock.patch("odemis.acq.feature.open_acquisition", return_value=[fake_data]) as open_mock:
                    features = json.loads(json.dumps(payload), cls=FeaturesDecoder)
            self.assertEqual(features[0].reference_image, "legacy-data")
            open_mock.assert_called_once_with(legacy_filename)
            self.assertNotEqual(open_mock.call_args[0][0], modern_filename)

if __name__ == "__main__":
    unittest.main()
