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
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

import numpy

from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    FEATURE_COLLECT_PROBABILITY,
    FeaturesDecoder,
    _is_zstack_stream,
    collect_feature_data,
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
        feature1 = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0}, collect=False)
        feature2 = CryoFeature("Feature-2", stage_position={"x": 1e-3, "y": 1e-3, "z": 1e-3}, fm_focus_position={"z": 2e-3}, collect=False)
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


class TestCollectFlag(unittest.TestCase):
    """Tests for the CryoFeature.collect flag and its persistence."""

    def test_collect_flag_is_bool(self):
        """CryoFeature.collect must be a bool when not explicitly provided."""
        f = CryoFeature("F", {"x": 0, "y": 0, "z": 0}, {"z": 0})
        self.assertIsInstance(f.collect, bool)

    def test_collect_flag_explicit_true(self):
        """Passing collect=True must set the attribute to True."""
        f = CryoFeature("F", {"x": 0, "y": 0, "z": 0}, {"z": 0}, collect=True)
        self.assertTrue(f.collect)

    def test_collect_flag_explicit_false(self):
        """Passing collect=False must set the attribute to False."""
        f = CryoFeature("F", {"x": 0, "y": 0, "z": 0}, {"z": 0}, collect=False)
        self.assertFalse(f.collect)

    def test_collect_flag_random_probability(self):
        """With enough samples, roughly FEATURE_COLLECT_PROBABILITY fraction should be True."""
        n = 500
        trues = sum(
            CryoFeature(f"F{i}", {"x": 0, "y": 0, "z": 0}, {"z": 0}).collect
            for i in range(n)
        )
        ratio = trues / n
        # Allow ±10 percentage points tolerance.
        self.assertAlmostEqual(ratio, FEATURE_COLLECT_PROBABILITY, delta=0.10)

    def test_collect_flag_persisted_in_dict(self):
        """get_features_dict must include 'collect' in each feature entry."""
        f = CryoFeature("F", {"x": 0, "y": 0, "z": 0}, {"z": 0}, collect=True)
        d = get_features_dict([f])
        self.assertIn("collect", d["feature_list"][0])
        self.assertTrue(d["feature_list"][0]["collect"])

    def test_collect_flag_round_trip_json(self):
        """collect flag must survive JSON serialise / deserialise round-trip."""
        for value in (True, False):
            f = CryoFeature("F", {"x": 0, "y": 0, "z": 0}, {"z": 0}, collect=value)
            j = json.dumps(get_features_dict([f]))
            loaded = json.loads(j, cls=FeaturesDecoder)
            self.assertEqual(loaded[0].collect, value)

    def test_collect_flag_missing_in_json_defaults_to_random(self):
        """When collect key is absent in loaded JSON, the flag is randomly assigned."""
        f = CryoFeature("F", {"x": 0, "y": 0, "z": 0}, {"z": 0}, collect=True)
        d = get_features_dict([f])
        del d["feature_list"][0]["collect"]
        j = json.dumps(d)
        loaded = json.loads(j, cls=FeaturesDecoder)
        self.assertIsInstance(loaded[0].collect, bool)


class TestCollectFeatureData(unittest.TestCase):
    """Tests for collect_feature_data()."""

    def _make_feature(self, collect: bool = True, pos=None) -> CryoFeature:
        if pos is None:
            pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        return CryoFeature("TestFeature", pos, {"z": 0.0}, collect=collect)

    def test_skips_when_collect_false(self):
        """collect_feature_data must not call record() when feature.collect is False."""
        f = self._make_feature(collect=False)
        with patch("odemis.acq.feature.DataCollector") as MockDC:
            collect_feature_data(f)
            MockDC.return_value.get_consent.assert_not_called()

    def test_skips_when_no_consent(self):
        """collect_feature_data must not call record() when consent is not granted."""
        f = self._make_feature(collect=True)
        with patch("odemis.acq.feature.DataCollector") as MockDC:
            MockDC.return_value.get_consent.return_value = False
            collect_feature_data(f)
            MockDC.return_value.record.assert_not_called()

    def test_calls_record_when_consent_given(self):
        """collect_feature_data must call record() once when consent is True."""
        f = self._make_feature(collect=True)
        with patch("odemis.acq.feature.DataCollector") as MockDC:
            mock_instance = MockDC.return_value
            mock_instance.get_consent.return_value = True
            collect_feature_data(f)
            mock_instance.record.assert_called_once()

    def test_sets_collect_false_after_collection(self):
        """feature.collect must be False after collect_feature_data is called."""
        f = self._make_feature(collect=True)
        with patch("odemis.acq.feature.DataCollector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            collect_feature_data(f)
        self.assertFalse(f.collect)

    def test_collect_false_not_changed_when_skipped(self):
        """feature.collect remains True when collection is skipped due to no consent."""
        f = self._make_feature(collect=True)
        with patch("odemis.acq.feature.DataCollector") as MockDC:
            MockDC.return_value.get_consent.return_value = False
            collect_feature_data(f)
        self.assertTrue(f.collect)

    def test_payload_contains_status_and_positions(self):
        """Payload must contain status, stage_position and fm_focus_position."""
        f = self._make_feature(collect=True)
        f.status.value = "Active"
        captured = {}

        def fake_record(event_name, schema_version, payload, **kwargs):
            captured.update(payload)

        with patch("odemis.acq.feature.DataCollector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            MockDC.return_value.record.side_effect = fake_record
            collect_feature_data(f)

        self.assertIn("status", captured)
        self.assertIn("stage_position", captured)
        self.assertIn("fm_focus_position", captured)

    def test_payload_has_no_feature_name(self):
        """Payload must not contain the feature name string as a key or value."""
        f = self._make_feature(collect=True)
        f.name.value = "my_secret_feature_name"
        captured = {}

        def fake_record(event_name, schema_version, payload, **kwargs):
            captured.update(payload)

        with patch("odemis.acq.feature.DataCollector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            MockDC.return_value.record.side_effect = fake_record
            collect_feature_data(f)

        self.assertNotIn("my_secret_feature_name", captured)
        self.assertNotIn("my_secret_feature_name", str(captured.keys()))

    def test_payload_channel_keys_are_generic(self):
        """Image payload keys must be generic (channel_N) not derived from feature name."""
        from odemis.acq.stream import StaticFluoStream
        arr = numpy.zeros((64, 64), dtype=numpy.uint16)
        da = model.DataArray(arr, metadata={
            model.MD_POS: (0.0, 0.0),
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        })
        stream = StaticFluoStream("test_stream", da)
        f = self._make_feature(collect=True)
        f.streams.value.append(stream)
        captured = {}

        def fake_record(event_name, schema_version, payload, **kwargs):
            captured.update(payload)

        with patch("odemis.acq.feature.DataCollector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            MockDC.return_value.record.side_effect = fake_record
            collect_feature_data(f)

        image_keys = [k for k in captured if k.startswith("channel_")]
        self.assertTrue(len(image_keys) >= 1, "Expected at least one channel_N key in payload")
        for k in image_keys:
            self.assertRegex(k, r"^channel_\d+$")

    def test_never_raises(self):
        """collect_feature_data must never raise an exception."""
        f = self._make_feature(collect=True)
        with patch("odemis.acq.feature.DataCollector", side_effect=RuntimeError("boom")):
            try:
                collect_feature_data(f)
            except Exception as exc:
                self.fail(f"collect_feature_data raised unexpectedly: {exc}")


if __name__ == "__main__":
    unittest.main()
