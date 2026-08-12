# -*- coding: utf-8 -*-
"""
Created on Feb 2025

@author: Patrick Cleeve, Alexéy Ilyushkin

Copyright © 2025-2026 Patrick Cleeve, Alexéy Ilyushkin, Delmic

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
import math
import unittest
from concurrent import futures
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock

import numpy

from odemis.acq.milling import fibsemos  # to load the fibsemOS module

try:
    from fibsem.milling import MillingAlignment
    from fibsem.milling.patterning.patterns2 import (
        BasePattern,
        MicroExpansionPattern,
        RectanglePattern,
        TrenchPattern,
    )
    from fibsem.structures import Point
    from odemis.acq.milling.fibsemos import (
        convert_milling_settings,
        convert_milling_tasks_to_milling_stages,
        convert_pattern_to_fibsemos,
        convert_task_to_milling_stage,
    )
except ImportError:
    pass

from odemis.acq.milling.patterns import (
    CompositeRectanglePatternParameters,
    CorrelationPatternParameters,
    MicroexpansionPatternParameters,
    NotchPatternParameters,
    RectanglePatternParameters,
    RulerPatternParameters,
    TrenchPatternParameters,
    WaffleTrenchPatternParameters,
)
from odemis.acq.milling.tasks import MillingSettings, MillingTaskSettings
from odemis import model
from odemis.acq.feature import CryoFeature
from odemis.acq.milling.fibsemos import _format_preset, _get_reference_image

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

# Create dummy parameter objects to pass into converter functions.
def create_rectangle_pattern_params(spot_size_correction=0.0):
    return RectanglePatternParameters(
        name="Rectangle-1",
        width=10e-6,
        height=15e-6,
        depth=5e-6,
        rotation=0,
        center=(100, 150),
        scan_direction="TopToBottom",
        spot_size_correction=spot_size_correction,
    )

def create_trench_pattern_params(spot_size_correction=0.0):
    return TrenchPatternParameters(
        name="Trench-1",
        width=12e-6,
        height=8e-6,
        depth=4e-6,
        spacing=3e-6,
        center=(50, 75),
        spot_size_correction=spot_size_correction,
    )

def create_microexpansion_pattern_params():
    return MicroexpansionPatternParameters(
        name="Microexpansion-1",
        width=5e-6,
        height=10e-6,
        depth=3e-6,
        spacing=7e-6,
        center=(25, 35),
    )

class TestConvertPatterns(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            if not fibsemos.FIBSEMOS_INSTALLED:
                raise ImportError("fibsemOS package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the fibsemOS tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")

    def test_convert_rectangle_pattern(self):
        pattern_param = create_rectangle_pattern_params()
        converted = convert_pattern_to_fibsemos(pattern_param)
        self.assertIsInstance(converted, RectanglePattern)
        self.assertAlmostEqual(converted.width, pattern_param.width.value)
        self.assertAlmostEqual(converted.height, pattern_param.height.value)
        self.assertAlmostEqual(converted.depth, pattern_param.depth.value)
        self.assertAlmostEqual(converted.rotation, pattern_param.rotation.value)
        self.assertEqual(converted.scan_direction, pattern_param.scan_direction.value)
        self.assertEqual(converted.point, Point(x=pattern_param.center.value[0],
                                                y=pattern_param.center.value[1]))

    def test_convert_trench_pattern(self):
        pattern_param = create_trench_pattern_params()
        converted = convert_pattern_to_fibsemos(pattern_param)
        self.assertIsInstance(converted, TrenchPattern)
        self.assertAlmostEqual(converted.width, pattern_param.width.value)
        # Both upper and lower trench heights should be equal to pattern_param.height.value
        self.assertAlmostEqual(converted.upper_trench_height, pattern_param.height.value)
        self.assertAlmostEqual(converted.lower_trench_height, pattern_param.height.value)
        self.assertAlmostEqual(converted.depth, pattern_param.depth.value)
        self.assertAlmostEqual(converted.spacing, pattern_param.spacing.value)
        self.assertEqual(converted.point, Point(x=pattern_param.center.value[0],
                                                y=pattern_param.center.value[1]))

    def test_convert_microexpansion_pattern(self):
        pattern_param = create_microexpansion_pattern_params()
        converted = convert_pattern_to_fibsemos(pattern_param)
        self.assertIsInstance(converted, MicroExpansionPattern)
        self.assertAlmostEqual(converted.width, pattern_param.width.value)
        self.assertAlmostEqual(converted.height, pattern_param.height.value)
        self.assertAlmostEqual(converted.depth, pattern_param.depth.value)
        self.assertAlmostEqual(converted.distance, pattern_param.spacing.value)
        self.assertEqual(converted.point, Point(x=pattern_param.center.value[0],
                                                y=pattern_param.center.value[1]))


class _FakeFibsemPattern:
    def __init__(self, **kwargs):
        vars(self).update(kwargs)


class _FakePoint:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class TestSpotSizeCorrectionConversion(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.multiple(
            fibsemos,
            RectanglePattern=_FakeFibsemPattern,
            TrenchPattern=_FakeFibsemPattern,
            Point=_FakePoint,
            create=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_spot_size_correction_changes_sent_dimensions_only(self):
        correction = 1e-6
        rectangle_param = create_rectangle_pattern_params(correction)
        rectangle = fibsemos.convert_pattern_to_fibsemos(rectangle_param)
        self.assertAlmostEqual(rectangle.width, rectangle_param.width.value - correction)
        self.assertAlmostEqual(rectangle.height, rectangle_param.height.value - correction)

    def test_trench_centers_stay_on_displayed_positions(self):
        correction = 0.1e-6
        trench_param = create_trench_pattern_params(correction)
        trench = fibsemos.convert_pattern_to_fibsemos(trench_param)
        sent_offset = (trench.spacing + trench.upper_trench_height) / 2
        displayed_offset = (trench_param.spacing.value + trench_param.height.value) / 2
        self.assertAlmostEqual(sent_offset, displayed_offset)

class TestConvertMillingSettings(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            if not fibsemos.FIBSEMOS_INSTALLED:
                raise ImportError("fibsemOS package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the fibsemOS tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")

    def test_convert_milling_settings(self):
        dummy_settings = MillingSettings(
            current=1e-9,
            voltage=30000,
            mode="Serial",
            field_of_view=80e-6,
            align=True
        )
        converted = convert_milling_settings(dummy_settings)
        # Validate that the converted settings match
        self.assertAlmostEqual(converted.milling_current, dummy_settings.current.value)
        self.assertAlmostEqual(converted.milling_voltage, dummy_settings.voltage.value)
        self.assertEqual(converted.patterning_mode, dummy_settings.mode.value)
        self.assertAlmostEqual(converted.hfw, dummy_settings.field_of_view.value)
        # preset string must be set and match voltage/current formatting
        self.assertEqual(converted.preset, "30 keV; 1 nA")

class TestConvertTaskToMillingStage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            if not fibsemos.FIBSEMOS_INSTALLED:
                raise ImportError("fibsemOS package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the fibsemOS tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")

    def test_convert_task_to_milling_stage(self):
        dummy_milling = MillingSettings(
            current=1e-9,
            voltage=30000,
            mode="Serial",
            field_of_view=80e-6,
            align=True
        )
        # Create a rectangle pattern parameter instance
        pattern_param = create_rectangle_pattern_params()

        # Create a dummy task with a name, milling settings, and a single pattern.
        dummy_task = MillingTaskSettings(
            name="Task-1",
            milling=dummy_milling,
            patterns=[pattern_param],
        )

        stage = convert_task_to_milling_stage(dummy_task)
        # Check that stage has been constructed correctly.
        self.assertEqual(stage.name, dummy_task.name)
        # Check milling settings conversion
        self.assertAlmostEqual(stage.milling.milling_current, dummy_milling.current.value)
        self.assertAlmostEqual(stage.milling.milling_voltage, dummy_milling.voltage.value)
        # preset must propagate through task to stage conversion
        self.assertEqual(stage.milling.preset, "30 keV; 1 nA")

        # Check pattern conversion: since we passed a rectangle, expect RectanglePattern output.
        self.assertIsInstance(stage.pattern, RectanglePattern)

        # Check alignment conversion; alignment.enabled should reflect dummy_milling.align.value.
        self.assertIsInstance(stage.alignment, MillingAlignment)
        self.assertEqual(stage.alignment.enabled, dummy_milling.align.value)

class TestConvertMillingTasksToMillingStages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            if not fibsemos.FIBSEMOS_INSTALLED:
                raise ImportError("fibsemOS package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the fibsemOS tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")

    def test_convert_milling_tasks_to_milling_stages(self):
        # Create two dummy tasks.
        dummy_milling1 = MillingSettings(
            current=1e-9,
            voltage=30000,
            mode="Serial",
            field_of_view=80e-6,
            align=False
        )
        dummy_milling2 = MillingSettings(
            current=2e-9,
            voltage=5000,
            mode="Parallel",
            field_of_view=150e-6,
            align=True
        )
        pattern_param1 = create_trench_pattern_params()
        pattern_param2 = create_microexpansion_pattern_params()

        task1 = MillingTaskSettings(
            name="Task-1",
            milling=dummy_milling1,
            patterns=[pattern_param1],
        )
        task2 = MillingTaskSettings(
            name="Task-2",
            milling=dummy_milling2,
            patterns=[pattern_param2],
        )
        tasks = [task1, task2]
        stages = convert_milling_tasks_to_milling_stages(tasks)
        self.assertEqual(len(stages), 2)
        # Check names and basic settings of each stage
        self.assertEqual(stages[0].name, task1.name)
        self.assertEqual(stages[1].name, task2.name)
        # Check that each stage has a valid pattern conversion
        self.assertIsInstance(stages[0].pattern, BasePattern)
        self.assertIsInstance(stages[1].pattern, BasePattern)

    def assert_composite_pattern_uses_one_stage(
            self, pattern: CompositeRectanglePatternParameters) -> 'FibsemMillingStage':
        """Assert that all generated rectangles share one milling stage."""
        milling = MillingSettings(
            current=60e-12, voltage=30000, field_of_view=80e-6, align=True)
        task = MillingTaskSettings(
            name=pattern.name.value, milling=milling, patterns=[pattern])

        stages = convert_milling_tasks_to_milling_stages([task])

        self.assertEqual(len(stages), 1)
        stage = stages[0]
        expected_count = len(pattern.generate())
        self.assertIsInstance(stage.pattern, RectanglePattern)
        self.assertEqual(len(stage.patterns), expected_count)
        self.assertEqual(len(stage.pattern.define()), expected_count)
        self.assertTrue(all(isinstance(rectangle, RectanglePattern)
                            for rectangle in stage.patterns))
        self.assertEqual(stage.milling.milling_current, milling.current.value)
        self.assertEqual(stage.alignment.enabled, milling.align.value)
        return stage

    def test_ruler_uses_one_milling_stage(self) -> None:
        """Send all ruler rectangles in one milling stage."""
        pattern = RulerPatternParameters(
            width=2e-6, height=10e-6, depth=0.5e-6, spacing=10e-6,
            num_graduations=6, center=(3e-6, -4e-6))

        self.assert_composite_pattern_uses_one_stage(pattern)

    def test_ruler_in_task_with_multiple_patterns(self) -> None:
        """Convert a ruler within a task that contains other pattern types."""
        milling = MillingSettings(current=60e-12, voltage=30000, field_of_view=80e-6, align=False)
        ruler = RulerPatternParameters(width=2e-6, height=10e-6, depth=0.5e-6,
                                       spacing=10e-6, num_graduations=6)
        task = MillingTaskSettings(name="Mixed", milling=milling,
                                   patterns=[create_trench_pattern_params(), ruler,
                                             create_rectangle_pattern_params()])
        stages = convert_milling_tasks_to_milling_stages([task])
        self.assertEqual(len(stages), 3)
        self.assertIsInstance(stages[0].pattern, TrenchPattern)
        self.assertEqual(len(stages[1].patterns), 12)
        self.assertTrue(all(isinstance(stage.pattern, RectanglePattern)
                            for stage in stages[1:]))
        self.assertTrue(all(not stage.alignment.enabled for stage in stages))
        task.selected = False
        self.assertEqual(convert_milling_tasks_to_milling_stages([task]), [])

    def test_notch_uses_one_milling_stage(self) -> None:
        """Send all notch rectangles in one milling stage."""
        pattern = NotchPatternParameters(
            width=3.5e-6, height=8.1e-6, depth=0.5e-6, gap=1.1e-6,
            thickness=0.2e-6, offset=-0.2e-6)

        self.assert_composite_pattern_uses_one_stage(pattern)

    def test_correlation_pattern_uses_one_milling_stage(self) -> None:
        """Send all correlation markers in one milling stage."""
        pattern = CorrelationPatternParameters(
            width=900e-6, height=700e-6, marker_length=75e-6,
            thickness=4e-6, depth=3e-6)

        stage = self.assert_composite_pattern_uses_one_stage(pattern)
        self.assertEqual(stage.milling.hfw, 960e-6)
        rotated_rectangles = [rectangle for rectangle in stage.pattern.define()
                              if rectangle.rotation]
        self.assertEqual(len(rotated_rectangles), 1)
        self.assertAlmostEqual(rotated_rectangles[0].rotation, math.pi / 4)

    def test_waffle_trench_uses_one_milling_stage(self) -> None:
        """Send both waffle trench rectangles in one milling stage."""
        pattern = WaffleTrenchPatternParameters(
            top_width=22e-6, top_height=37e-6,
            bottom_width=20e-6, bottom_height=17e-6,
            depth=1e-6, spacing=3e-6)

        self.assert_composite_pattern_uses_one_stage(pattern)


@unittest.skipUnless(fibsemos.FIBSEMOS_INSTALLED, "fibsemOS is not installed")
class TestCompositeSpotSizeCorrection(unittest.TestCase):
    """Test composite-pattern spot size correction during fibsemOS conversion."""

    def setUp(self) -> None:
        """Create a ruler for the pattern-specific boundary tests."""
        self.ruler = RulerPatternParameters(
            width=2e-6, height=6e-6, depth=0.5e-6, spacing=10e-6,
            num_graduations=2, center=(3e-6, -4e-6), spot_size_correction=20e-9)
        milling = MillingSettings(current=60e-12, voltage=30000, field_of_view=80e-6)
        self.task = MillingTaskSettings(name="Ruler", milling=milling, patterns=[self.ruler])

    def assert_spot_size_correction_applied(
            self, pattern: CompositeRectanglePatternParameters, correction: float) -> None:
        """Assert correction of every generated rectangle's lateral dimensions."""
        pattern.spot_size_correction.value = correction
        milling = MillingSettings(current=60e-12, voltage=30000, field_of_view=80e-6)
        task = MillingTaskSettings(name=pattern.name.value, milling=milling, patterns=[pattern])
        stage = convert_milling_tasks_to_milling_stages([task])[0]
        rectangles = pattern.generate()

        self.assertEqual(len(stage.patterns), len(rectangles))
        for converted, rectangle in zip(stage.patterns, rectangles):
            self.assertAlmostEqual(converted.width, rectangle.width.value - correction, places=15)
            self.assertAlmostEqual(converted.height, rectangle.height.value - correction, places=15)
            self.assertEqual(converted.depth, rectangle.depth.value)
            self.assertEqual((converted.point.x, converted.point.y), rectangle.center.value)

    def test_correction_is_applied_to_composite_patterns(self) -> None:
        """Apply positive correction to every supported composite pattern."""
        patterns = (
            ("ruler", RulerPatternParameters(
                width=2e-6, height=6e-6, depth=0.5e-6, spacing=10e-6,
                num_graduations=2, center=(3e-6, -4e-6))),
            ("notch", NotchPatternParameters(
                width=3.5e-6, height=8.1e-6, depth=0.5e-6, gap=1.1e-6,
                thickness=0.2e-6, offset=-0.2e-6)),
            ("waffle trench", WaffleTrenchPatternParameters(
                top_width=22e-6, top_height=37e-6, bottom_width=20e-6,
                bottom_height=17e-6, depth=1e-6, spacing=3e-6,
                center=(2e-6, -3e-6))),
            ("correlation", CorrelationPatternParameters(
                width=900e-6, height=700e-6, marker_length=75e-6,
                thickness=4e-6, depth=3e-6, center=(1e-6, -2e-6))),
        )
        for name, pattern in patterns:
            with self.subTest(pattern=name):
                self.assert_spot_size_correction_applied(pattern, 20e-9)

    def test_correction_equal_to_graduation_height_is_rejected(self) -> None:
        """Reject a correction equal to the graduation height."""
        self.ruler.spot_size_correction.value = self.ruler.generate()[0].height.value
        with self.assertRaises(ValueError):
            convert_milling_tasks_to_milling_stages([self.task])

    def test_correction_exceeding_graduation_height_is_rejected(self) -> None:
        """Reject a correction greater than the graduation height."""
        self.ruler.spot_size_correction.value = 1.25 * self.ruler.generate()[0].height.value
        with self.assertRaises(ValueError):
            convert_milling_tasks_to_milling_stages([self.task])

    def test_correction_exceeding_short_graduation_width_is_rejected(self) -> None:
        """Reject a correction greater than a short graduation width."""
        self.ruler.width.value = 20e-9  # Long ticks are 20 nm, short ticks are 15 nm.
        self.ruler.spot_size_correction.value = 16e-9
        with self.assertRaises(ValueError):
            convert_milling_tasks_to_milling_stages([self.task])


def create_tescan_metadata() -> dict:
    """Return recorded Tescan settings without unsupported optional fields."""
    beam = {
        "dwellTime": [1e-6, "s"],
        "horizontalFoV": [100e-6, "m"],
        "accelVoltage": [30e3, "V"],
        "resolution": [[8, 6], ""],
        "rotation": [0.0, "rad"],
    }
    metadata = {
        "Electron-Beam": deepcopy(beam),
        "Ion-Beam": deepcopy(beam),
        "Electron-Focus": {"position": [{"z": 5e-3}, "m"]},
        "Electron-Detector": {"type": ["SE", ""]},
        "Ion-Detector": {"type": ["SE", ""]},
        "Stage": {"position": [{"x": 0.0, "y": 0.0, "z": 30e-3, "rx": 0.0, "rz": 0.0}, "m"]},
    }
    metadata["Electron-Beam"]["probeCurrent"] = [100e-12, "A"]
    return metadata


class TestTescanMetadataDefaults(unittest.TestCase):
    """Check metadata normalization without acquiring microscope settings."""

    def test_current_values(self):
        metadata = create_tescan_metadata()
        # A measured FIB current must not be copied into the setpoint field.
        metadata["Ion-Beam"]["probeCurrent"] = [90e-12, "A"]
        normalized = fibsemos._populate_tescan_metadata_defaults(metadata)
        self.assertEqual(normalized["Electron-Beam"]["beamCurrent"], [100e-12, "A"])
        self.assertIsNot(normalized["Electron-Beam"]["beamCurrent"], metadata["Electron-Beam"]["probeCurrent"])
        self.assertIsNone(normalized["Ion-Beam"]["beamCurrent"][0])

    def test_preserve_recorded_values(self):
        metadata = create_tescan_metadata()
        for beam_key in ("Electron-Beam", "Ion-Beam"):
            metadata[beam_key]["beamCurrent"] = [150e-12, "A"]
            metadata[beam_key]["shift"] = [[1e-6, 2e-6], "m"]
            metadata[beam_key]["stigmator"] = [[0.1, 0.2], ""]
        metadata["Ion-Focus"] = {"position": [{"z": 10e-3}, "m"]}
        metadata["Ion-Detector"]["brightness"] = [0.7, ""]
        metadata["Ion-Detector"]["contrast"] = [0.0, ""]
        original = deepcopy(metadata)
        normalized = fibsemos._populate_tescan_metadata_defaults(metadata)
        self.assertIs(normalized, metadata)
        for component, settings in original.items():
            for field, entry in settings.items():
                with self.subTest(component=component, field=field):
                    self.assertEqual(normalized[component][field], entry)

    def test_optional_defaults(self):
        normalized = fibsemos._populate_tescan_metadata_defaults(create_tescan_metadata())
        self.assertIsNone(normalized["Ion-Focus"]["position"][0]["z"])
        self.assertIsNone(normalized["Ion-Detector"]["mode"][0])
        self.assertIsNone(normalized["Ion-Detector"]["brightness"][0])
        self.assertEqual(normalized["Ion-Beam"]["shift"][0], [0.0, 0.0])

    def test_missing_beam_settings(self):
        for beam_key in ("Electron-Beam", "Ion-Beam"):
            for field in ("dwellTime", "horizontalFoV", "accelVoltage", "resolution", "rotation"):
                for entry in (None, [], [None]):
                    with self.subTest(beam=beam_key, field=field, entry=entry):
                        metadata = create_tescan_metadata()
                        metadata[beam_key][field] = entry
                        with self.assertRaisesRegex(ValueError, rf"{beam_key}\.{field}"):
                            fibsemos._populate_tescan_metadata_defaults(metadata)

    def test_invalid_acquisition_settings(self):
        for field in ("dwellTime", "horizontalFoV"):
            for value in (0.0, -1.0):
                with self.subTest(field=field, value=value):
                    metadata = create_tescan_metadata()
                    metadata["Ion-Beam"][field][0] = value
                    with self.assertRaisesRegex(ValueError, rf"invalid Ion-Beam\.{field}"):
                        fibsemos._populate_tescan_metadata_defaults(metadata)

    def test_missing_stage_position(self):
        for entry in (None, [], [{}]):
            with self.subTest(entry=entry):
                metadata = create_tescan_metadata()
                metadata["Stage"]["position"] = entry
                with self.assertRaisesRegex(ValueError, r"Stage\.position"):
                    fibsemos._populate_tescan_metadata_defaults(metadata)
        for axis in ("x", "y", "z", "rx", "rz"):
            with self.subTest(axis=axis):
                metadata = create_tescan_metadata()
                del metadata["Stage"]["position"][0][axis]
                with self.assertRaisesRegex(ValueError, rf"Stage\.position\.{axis}"):
                    fibsemos._populate_tescan_metadata_defaults(metadata)

    @unittest.skipUnless(fibsemos.FIBSEMOS_INSTALLED, "fibsemOS is not available")
    def test_image_conversion(self):
        """Convert normalized metadata through the installed fibsemOS adapter."""
        metadata = fibsemos._populate_tescan_metadata_defaults(create_tescan_metadata())
        image = model.DataArray(numpy.zeros((6, 8)), metadata={
            model.MD_EXTRA_SETTINGS: metadata,
            model.MD_PIXEL_SIZE: (12.5e-6, 12.5e-6),
            model.MD_DESCRIPTION: "FIB",
        })
        converted = fibsemos.from_odemis_image(image)
        self.assertIsNone(converted.metadata.microscope_state.ion_beam.beam_current)
        self.assertEqual(converted.metadata.microscope_state.electron_beam.beam_current, 100e-12)
        self.assertEqual(converted.metadata.image_settings.dwell_time, 1e-6)
        self.assertEqual(converted.metadata.image_settings.hfw, 100e-6)


class TestMillingAPICompatibility(unittest.TestCase):
    """Check reference image handling with both fibsemOS APIs without hardware."""

    def setUp(self):
        self.microscope = mock.Mock()
        self.rect = SimpleNamespace(left=0.25, top=0.25, width=0.5, height=0.5)
        self.stage = SimpleNamespace(name="Test stage", alignment=SimpleNamespace(rect=self.rect))
        self.feature = CryoFeature(name="f1", stage_position={}, fm_focus_position={})
        self.feature.reference_image = model.DataArray(numpy.arange(48).reshape(6, 8), metadata={
            model.MD_EXTRA_SETTINGS: create_tescan_metadata(),
        })
        self.ref_image = SimpleNamespace(data=self.feature.reference_image.copy(),
                                        metadata=SimpleNamespace(image_settings=SimpleNamespace()))

    def _legacy_mill_stages(self, microscope: object, stages: list, reference_image: object = None,
                            parent_ui: object = None) -> None:
        """Model the reference-image argument's fallback to stage metadata."""
        self.assertIsNone(reference_image)
        self.assertIsNone(parent_ui)
        self.assertIs(stages[0].reference_image, self.ref_image)

    def _new_mill_stages(self, microscope: object, stages: list, parent_ui: object = None) -> None:
        """Model the API that reads reference images from stage metadata."""
        self.assertIsNone(parent_ui)
        self.assertIs(stages[0].reference_image, self.ref_image)

    def _run_with_api(self, milling_api: mock.Mock, version: str = "0.5.0.dev0") -> None:
        """Run the manager synchronously with a mocked fibsemOS API."""
        self.ref_image.data = self.feature.reference_image.copy()
        with mock.patch.object(fibsemos, "mill_stages", milling_api, create=True), \
             mock.patch.object(fibsemos, "fibsem", SimpleNamespace(__version__=version), create=True), \
             mock.patch.object(fibsemos, "create_fibsemos_microscope", return_value=self.microscope), \
             mock.patch.object(fibsemos, "from_odemis_image", return_value=self.ref_image, create=True):
            self.manager = fibsemos.FibsemOSMillingTaskManager()
            self.manager._future = futures.Future()
            self.manager._active = True
            self.manager.milling_stages = [self.stage]
            self.manager.feature = self.feature
            self.manager.path = "/tmp/milling"
            self.manager._run()

    def _check_reference_image(self) -> None:
        """Check that the API receives the cropped image and alignment metadata."""
        self.assertIs(self.stage.reference_image, self.ref_image)
        numpy.testing.assert_array_equal(self.ref_image.data, self.feature.reference_image[1:4, 2:6])
        self.assertEqual(self.ref_image.metadata.image_settings.path, "/tmp/milling")
        self.assertIs(self.ref_image.metadata.image_settings.reduced_area, self.rect)
        self.assertEqual(self.feature.reference_image.shape, (6, 8))
        self.assertFalse(self.manager._active)

    def test_legacy_api(self):
        milling_api = mock.create_autospec(self._legacy_mill_stages, side_effect=self._legacy_mill_stages)
        with self.assertLogs(level=logging.WARNING) as logs:
            self._run_with_api(milling_api, version="0.4.1a1")

        self.assertIn("fibsemOS 0.4.1a1: passing reference images via stage metadata.", logs.output[0])
        milling_api.assert_called_once_with(self.microscope, [self.stage])
        self._check_reference_image()

    def test_new_api(self):
        for version in ("0.5.0.dev0", "0.5.0a0", "0.5.0", "0.5.2.dev0", "0.10.0"):
            with self.subTest(version=version):
                milling_api = mock.create_autospec(self._new_mill_stages, side_effect=self._new_mill_stages)
                with mock.patch.object(fibsemos.logging, "warning") as warning:
                    self._run_with_api(milling_api, version=version)

                warning.assert_not_called()
                milling_api.assert_called_once_with(self.microscope, [self.stage])
                self._check_reference_image()

    def test_unknown_version(self):
        """Unknown version metadata must not prevent milling through stage metadata."""
        milling_api = mock.create_autospec(self._new_mill_stages, side_effect=self._new_mill_stages)
        with mock.patch.object(fibsemos.logging, "warning") as warning:
            self._run_with_api(milling_api, version="unknown")
        warning.assert_not_called()
        milling_api.assert_called_once_with(self.microscope, [self.stage])
        self._check_reference_image()

    def test_serialized_metadata(self):
        """Normalize settings loaded from an image with JSON metadata."""
        self.feature.reference_image.metadata[model.MD_EXTRA_SETTINGS] = json.dumps(create_tescan_metadata())
        milling_api = mock.create_autospec(self._new_mill_stages, side_effect=self._new_mill_stages)
        self._run_with_api(milling_api)
        normalized = self.feature.reference_image.metadata[model.MD_EXTRA_SETTINGS]
        self.assertEqual(normalized["Electron-Beam"]["beamCurrent"], [100e-12, "A"])
        self.assertIsNone(normalized["Ion-Beam"]["beamCurrent"][0])
        self._check_reference_image()

    def test_missing_metadata_prevents_milling(self):
        del self.feature.reference_image.metadata[model.MD_EXTRA_SETTINGS]
        milling_api = mock.create_autospec(self._new_mill_stages)
        with self.assertRaisesRegex(ValueError, "missing extra settings"):
            self._run_with_api(milling_api)
        milling_api.assert_not_called()
        self.assertFalse(self.manager._active)

    def test_milling_error_is_not_retried(self):
        """A TypeError inside milling must propagate without a second milling attempt."""
        for api in (self._legacy_mill_stages, self._new_mill_stages):
            with self.subTest(api=api.__name__):
                milling_api = mock.create_autospec(api)
                milling_api.side_effect = TypeError("Milling failed")
                with self.assertRaisesRegex(TypeError, "Milling failed"):
                    self._run_with_api(milling_api)
                self.assertEqual(milling_api.call_count, 1)
                self.assertFalse(self.manager._active)


class TestResolveFeatureReferenceImage(unittest.TestCase):
    def test_returns_in_memory_reference_image(self):
        feature = CryoFeature(name="f1", stage_position={}, fm_focus_position={})
        da = model.DataArray(numpy.zeros((10, 12), dtype=numpy.uint16), metadata={model.MD_DIMS: "YX"})
        feature.reference_image = da

        out = _get_reference_image(feature)
        self.assertIs(out, da)

    def test_raises_if_missing_in_memory(self):
        feature = CryoFeature(name="f1", stage_position={}, fm_focus_position={})
        setattr(feature, "reference_image", None)

        with self.assertRaises(ValueError):
            _get_reference_image(feature)

if __name__ == "__main__":
    unittest.main()
