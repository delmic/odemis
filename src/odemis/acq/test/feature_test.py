# -*- coding: utf-8 -*-
"""
Created on Oct 2021

@author: Alexéy Ilyushkin

Copyright © 2021-2026 Alexéy Ilyushkin, Delmic

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
from unittest.mock import Mock, patch

import numpy

from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    DEFAULT_MILLING_ALIGNMENT_AREA,
    FEATURE_READY_TO_MILL,
    MIN_MILLING_ALIGNMENT_AREA_PIXELS,
    MillingAlignmentAreaTooSmallError,
    REFERENCE_IMAGE_FILENAME,
    constrain_milling_alignment_area,
    feature_decoder,
    _stream_overlaps_position,
    collect_feature_data,
    load_milling_tasks,
)
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH
from odemis.acq.move import Posture
from odemis.acq.stream import StaticFluoStream

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

    def test_notch_feature_offset(self):
        """Keep the notch right of the centered polishing pattern."""
        tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        feature = CryoFeature(
            name="TestFeature-1",
            stage_position={"x": 0, "y": 0},
            fm_focus_position={Posture.FM_IMAGING: {"z": 0}},
            milling_tasks={
                "Notch": tasks["Notch"],
                "Polishing 02": tasks["Polishing 02"],
            },
        )
        feature_position = (12e-6, -8e-6)

        feature.set_milling_feature_offset(feature_position)

        notch = feature.milling_tasks["Notch"].patterns[0]
        polishing = feature.milling_tasks["Polishing 02"].patterns[0]
        for actual, expected in zip(notch.center.value, (15e-6, -8e-6)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(polishing.center.value, feature_position)
        notch.mirrored.value = True
        feature.set_milling_feature_offset(feature_position)
        for actual, expected in zip(notch.center.value, (15e-6, -8e-6)):
            self.assertAlmostEqual(actual, expected)

    def test_decoder_ignores_unsupported_milling_tasks(self):
        """Ignore saved tasks that contain no patterns supported by this version."""
        feature = feature_decoder({
            "name": "Future feature",
            "status": FEATURE_READY_TO_MILL,
            "stage_position": {"x": 0, "y": 0},
            "fm_focus_position": {"FM IMAGING": 0},
            "milling_tasks": {
                "Future task": {
                    "name": "Future task",
                    "milling": {
                        "current": 100e-9,
                        "voltage": 30e3,
                        "field_of_view": 400e-6,
                        "mode": "Serial",
                        "channel": "ion",
                    },
                    "patterns": [{"pattern": "future_pattern"}],
                },
            },
        })

        self.assertEqual(feature.milling_tasks, {})

    def test_feature_milling_tasks(self):
        feature = CryoFeature(
            name="TestFeature-1",
            stage_position={"x": 50e-6, "y": 25e-6, "z": 32e-3, "rx": 0.61, "rz": 0},
            fm_focus_position={Posture.FM_IMAGING: {"z": 1.69e-3}}
        )
        stage_position = {"x": 25e-6, "y": 40e-6, "z": 32e-3, "rx": 0.31, "rz": 0}
        self.path = os.path.join(os.getcwd(), feature.name.value)
        reference_image = model.DataArray(numpy.zeros(shape=(1024, 1536)), metadata={})
        milling_tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        milling_feature_offset = (12e-6, -8e-6)

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
        feature.set_milling_feature_offset(milling_feature_offset)

        self.assertEqual(feature.path, self.path)
        self.assertEqual(feature.reference_image.shape, reference_image.shape)
        self.assertEqual(feature.get_posture_position(Posture.MILLING), stage_position)
        self.assertEqual(feature.milling_feature_offset.value, milling_feature_offset)
        for task in feature.milling_tasks.values():
            for pattern in task.patterns:
                self.assertEqual(
                    pattern.center.value,
                    pattern.get_center_at_feature(milling_feature_offset),
                )
        self.assertEqual(feature.status.value, FEATURE_READY_TO_MILL)
        self.assertEqual(set(feature.milling_tasks.keys()), set(milling_tasks.keys()))

        # assert directory and file is created
        self.assertTrue(os.path.exists(feature.path))

        filename = os.path.join(feature.path, f"{feature.name.value}-{REFERENCE_IMAGE_FILENAME}")
        self.assertTrue(os.path.exists(filename))


class TestCollectFlag(unittest.TestCase):
    """Tests for the CryoFeature.is_collectible flag and its persistence."""

    def test_collect_flag_is_bool(self):
        """CryoFeature.is_collectible must be False by default."""
        f = CryoFeature("F", {"x": 0, "y": 0, "z": 0}, {"z": 0})
        self.assertFalse(f.is_collectible)


class TestCollectFeatureData(unittest.TestCase):
    """Tests for collect_feature_data()."""

    def _make_feature(self, is_collectible: bool = True, pos=None) -> CryoFeature:
        if pos is None:
            pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        return CryoFeature("TestFeature", pos, {"z": 0.0}, is_collectible=is_collectible)

    def _make_fluo_stream(self):
        """Return a minimal StaticFluoStream with a 2-D DataArray."""
        arr = numpy.zeros((64, 64), dtype=numpy.uint16)
        da = model.DataArray(arr, metadata={
            model.MD_POS: (0.0, 0.0),
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        })
        return StaticFluoStream("ch0", da)

    def _make_feature_with_stream(self, is_collectible: bool = True) -> CryoFeature:
        """Return a feature with one FM stream attached."""
        f = self._make_feature(is_collectible=is_collectible)
        f.streams.value.append(self._make_fluo_stream())
        return f

    def test_skips_when_collect_false(self):
        """collect_feature_data must not call record() when feature.is_collectible is False."""
        f = self._make_feature(is_collectible=False)
        with patch("odemis.util.datacollector.get_data_collector") as MockDC:
            collect_feature_data(f)
            MockDC.return_value.get_consent.assert_not_called()

    def test_skips_when_no_consent(self):
        """collect_feature_data must not call record() when consent is not granted."""
        f = self._make_feature_with_stream(is_collectible=True)
        with patch("odemis.util.datacollector.get_data_collector") as MockDC:
            MockDC.return_value.get_consent.return_value = False
            collect_feature_data(f)
            MockDC.return_value.record.assert_not_called()

    def test_no_record_without_images(self):
        """record() must NOT be called when the feature has no image streams."""
        f = self._make_feature(is_collectible=True)  # no streams attached
        with patch("odemis.util.datacollector.get_data_collector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            collect_feature_data(f)
            MockDC.return_value.record.assert_not_called()

    def test_sets_collect_false_after_collection(self):
        """feature.is_collectible must be False after successful collection with images."""
        f = self._make_feature_with_stream(is_collectible=True)
        with patch("odemis.util.datacollector.get_data_collector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            collect_feature_data(f)
        self.assertFalse(f.is_collectible)

    def test_payload_contains_status_positions_and_image(self):
        """Payload must contain status, stage_position, fm_focus_position, and at least one image."""
        f = self._make_feature_with_stream(is_collectible=True)
        f.status.value = "Active"
        captured = {}

        def fake_record(event_name, schema_version, payload, **kwargs):
            captured.update(payload)

        with patch("odemis.util.datacollector.get_data_collector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            MockDC.return_value.record.side_effect = fake_record
            collect_feature_data(f)

        self.assertIn("status", captured)
        self.assertIn("stage_position", captured)
        self.assertIn("fm_focus_position", captured)
        image_keys = [k for k in captured if k.startswith(("channel_", "overview_fm_", "overview_sem_"))]
        self.assertTrue(len(image_keys) >= 1, "Payload must contain at least one image")

    def test_payload_has_no_feature_name(self):
        """Payload must not contain the feature name string as a key or value."""
        f = self._make_feature_with_stream(is_collectible=True)
        f.name.value = "my_secret_feature_name"
        captured = {}

        def fake_record(event_name, schema_version, payload, **kwargs):
            captured.update(payload)

        with patch("odemis.util.datacollector.get_data_collector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            MockDC.return_value.record.side_effect = fake_record
            collect_feature_data(f)

        self.assertNotIn("my_secret_feature_name", captured)
        self.assertNotIn("my_secret_feature_name", str(captured.keys()))

    def test_payload_channel_keys_are_generic(self):
        """Image payload keys must be generic (channel_N), not derived from feature or stream name.

        A StaticFluoStream named 'test_stream' is attached to the feature.
        After collection the payload key for the image must be 'channel_0',
        not 'test_stream' or the feature name — ensuring data privacy.
        """
        f = self._make_feature_with_stream(is_collectible=True)
        captured = {}

        def fake_record(event_name, schema_version, payload, **kwargs):
            captured.update(payload)

        with patch("odemis.util.datacollector.get_data_collector") as MockDC:
            MockDC.return_value.get_consent.return_value = True
            MockDC.return_value.record.side_effect = fake_record
            collect_feature_data(f)

        image_keys = [k for k in captured if k.startswith("channel_")]
        self.assertTrue(len(image_keys) >= 1, "Expected at least one channel_N key in payload")
        for k in image_keys:
            self.assertRegex(k, r"^channel_\d+$")

    def test_collects_on_status_change(self):
        """Subscribing to feature.status and calling collect_feature_data on change must call record().

        This simulates the controller's _on_feature_status subscriber: when
        the feature status VA changes, collect_feature_data is invoked and record()
        is called exactly once (consent granted, images present, is_collectible=True).
        """
        f = self._make_feature_with_stream(is_collectible=True)
        record_calls = []

        def fake_record(event_name, schema_version, payload, **kwargs):
            record_calls.append((event_name, schema_version))

        def _on_status_changed(_status):
            if f.is_collectible:
                collect_feature_data(f)

        f.status.subscribe(_on_status_changed, init=False)
        try:
            with patch("odemis.util.datacollector.get_data_collector") as MockDC:
                MockDC.return_value.get_consent.return_value = True
                MockDC.return_value.record.side_effect = fake_record
                f.status.value = FEATURE_READY_TO_MILL
        finally:
            f.status.unsubscribe(_on_status_changed)

        self.assertEqual(len(record_calls), 1)
        self.assertEqual(record_calls[0][0], "feature_collected")


class TestStreamHelpers(unittest.TestCase):
    """Tests for stream_overlaps_position."""

    def _make_static_fluo_stream(self, shape=(64, 64), pos=(0.0, 0.0), pixel_size=(1e-6, 1e-6)):
        """Return a minimal StaticFluoStream."""
        from odemis.acq.stream import StaticFluoStream
        arr = numpy.zeros(shape, dtype=numpy.uint16)
        da = model.DataArray(arr, metadata={
            model.MD_POS: pos,
            model.MD_PIXEL_SIZE: pixel_size,
        })
        return StaticFluoStream("test_stream", da)

    def _make_zstack_stream(self, pos=(0.0, 0.0), pixel_size=(1e-6, 1e-6)):
        """Return a minimal StaticFluoStream that looks like a z-stack (has zIndex)."""
        s = self._make_static_fluo_stream(pos=pos, pixel_size=pixel_size)
        s.zIndex = model.IntContinuous(0, (0, 3))
        return s

    def test_overlaps_centre(self):
        """Position at the stream centre must overlap."""
        # 64 x 64 pixels at 1 µm/pixel centred at (0, 0) → bbox ±32 µm.
        s = self._make_static_fluo_stream()
        self.assertTrue(_stream_overlaps_position(s, 0.0, 0.0))

    def test_overlaps_edge(self):
        """Position exactly on the bounding-box edge must still overlap."""
        s = self._make_static_fluo_stream(pos=(0.0, 0.0), pixel_size=(2e-6, 2e-6))
        # half-width = 64/2 * 2e-6 = 64e-6 m → right edge at +64e-6
        self.assertTrue(_stream_overlaps_position(s, 64e-6, 0.0))

    def test_no_overlap_outside(self):
        """Position clearly outside the bounding box must not overlap."""
        s = self._make_static_fluo_stream()
        # bbox is ±32 µm; 100 µm is well outside.
        self.assertFalse(_stream_overlaps_position(s, 100e-6, 0.0))

    def test_no_overlap_bad_stream(self):
        """_stream_overlaps_position returns False when getBoundingBox() raises."""
        from unittest.mock import MagicMock
        bad_stream = MagicMock()
        bad_stream.getBoundingBox.side_effect = AttributeError("no bbox")
        self.assertFalse(_stream_overlaps_position(bad_stream, 0.0, 0.0))


class TestMillingAlignmentPersistence(unittest.TestCase):
    """Tests for saving and restoring milling alignment areas."""

    path = ""

    def tearDown(self):
        if os.path.exists(self.path):
            filename = os.path.join(self.path, f"TestFeature-1-{REFERENCE_IMAGE_FILENAME}")
            if os.path.exists(filename):
                os.remove(filename)
            os.rmdir(self.path)

    def test_too_small_reference_image_is_not_saved(self):
        feature = CryoFeature(
            name="TestFeature-1",
            stage_position={"x": 0, "y": 0},
            fm_focus_position={"z": 0},
        )
        self.path = os.path.join(os.getcwd(), feature.name.value)
        reference_image = model.DataArray(numpy.zeros(shape=(200, 300)), metadata={})

        with self.assertRaises(MillingAlignmentAreaTooSmallError):
            feature.save_milling_task_data(
                stage_position={"x": 0, "y": 0},
                path=self.path,
                reference_image=reference_image,
            )

        self.assertFalse(os.path.exists(self.path))
        self.assertIsNone(feature.reference_image)

    def test_decoder_loads_feature_with_legacy_small_reference_image(self):
        """Keep an old project loadable when its saved reference is too small."""
        reference_image = model.DataArray(numpy.zeros(shape=(200, 300)), metadata={})
        acquisition = Mock()
        acquisition.getData.return_value = reference_image
        feature_raw = {
            "name": "Legacy feature",
            "status": FEATURE_READY_TO_MILL,
            "stage_position": {"x": 0, "y": 0},
            "fm_focus_position": {Posture.FM_IMAGING.value: {"z": 0}},
            "path": "/legacy-project",
            "milling_alignment_area": DEFAULT_MILLING_ALIGNMENT_AREA,
        }

        with patch("odemis.acq.feature.os.path.exists", return_value=True), patch(
                "odemis.acq.feature.open_acquisition", return_value=[acquisition]):
            with self.assertLogs(level=logging.WARNING):
                feature = feature_decoder(feature_raw)

        self.assertIs(feature.reference_image, reference_image)
        self.assertEqual(
            feature.millingAlignmentArea.value,
            DEFAULT_MILLING_ALIGNMENT_AREA,
        )

    def test_decoder_ignores_invalid_saved_alignment_area(self):
        """Keep the default area when saved coordinates extend beyond the image."""
        feature_raw = {
            "name": "Invalid alignment feature",
            "status": FEATURE_READY_TO_MILL,
            "stage_position": {"x": 0, "y": 0},
            "fm_focus_position": {Posture.FM_IMAGING.value: {"z": 0}},
            "milling_alignment_area": (0.8, 0.8, 0.3, 0.3),
        }

        with self.assertLogs(level=logging.WARNING):
            feature = feature_decoder(feature_raw)

        self.assertEqual(
            feature.millingAlignmentArea.value,
            DEFAULT_MILLING_ALIGNMENT_AREA,
        )


class TestMillingAlignmentArea(unittest.TestCase):
    def test_default_area(self):
        feature = CryoFeature("Feature-1", {"x": 0, "y": 0, "z": 0}, {"z": 0})

        self.assertEqual(feature.millingAlignmentArea.value, DEFAULT_MILLING_ALIGNMENT_AREA)

    def test_area_is_clamped_to_minimum_pixel_count_and_image_bounds(self):
        area = constrain_milling_alignment_area((0.9, 0.9, 0.1, 0.1), (1024, 2048))

        expected = (0.8125, 0.75, 0.1875, 0.25)
        for actual_value, expected_value in zip(area, expected):
            self.assertAlmostEqual(actual_value, expected_value)

    def test_square_reference_image_can_contain_minimum_area(self):
        area = constrain_milling_alignment_area(DEFAULT_MILLING_ALIGNMENT_AREA, (512, 512))

        pixel_width = area[2] * 512
        pixel_height = area[3] * 512
        self.assertAlmostEqual(pixel_width * pixel_height, MIN_MILLING_ALIGNMENT_AREA_PIXELS)
        self.assertAlmostEqual(pixel_width, pixel_height)

    def test_minimum_area_accepts_both_extreme_aspect_ratios(self):
        for area in ((0.0, 0.0, 0.75, 0.5), (0.0, 0.0, 0.5, 0.75)):
            with self.subTest(area=area):
                self.assertEqual(constrain_milling_alignment_area(area, (512, 512)), area)

    def test_area_constrains_narrow_shape_even_with_enough_pixels(self):
        cases = (
            ((0.0, 0.0, 0.75, 0.125), (0.0, 0.0, 0.75, 0.5)),
            ((0.0, 0.0, 0.125, 0.75), (0.0, 0.0, 0.5, 0.75)),
        )
        for area, expected in cases:
            with self.subTest(area=area):
                self.assertEqual(constrain_milling_alignment_area(area, (1024, 1024)), expected)

    def test_reference_image_must_contain_minimum_area(self):
        with self.assertRaises(ValueError):
            constrain_milling_alignment_area((0.1, 0.2, 0.25, 0.25), (200, 300))

    def test_feature_accepts_free_aspect_ratio(self):
        feature = CryoFeature("Feature-1", {"x": 0, "y": 0, "z": 0}, {"z": 0})

        feature.millingAlignmentArea.value = (0.1, 0.1, 0.3, 0.2)

        self.assertEqual(feature.millingAlignmentArea.value, (0.1, 0.1, 0.3, 0.2))


if __name__ == "__main__":
    unittest.main()
