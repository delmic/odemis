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
from typing import Callable, Optional
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
from odemis.acq.milling import system_state
from odemis.driver import simulated

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

        alignment_area = (0.1, 0.2, 0.4, 0.45)
        stage = convert_task_to_milling_stage(dummy_task, alignment_area=alignment_area)
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
        self.assertEqual(stage.alignment.rect.left, alignment_area[0])
        self.assertEqual(stage.alignment.rect.top, alignment_area[1])
        self.assertAlmostEqual(stage.alignment.rect.width, 0.3)
        self.assertAlmostEqual(stage.alignment.rect.height, 0.25)


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
        alignment_area = (0.2, 0.15, 0.6, 0.45)
        stages = convert_milling_tasks_to_milling_stages(tasks, alignment_area=alignment_area)
        self.assertEqual(len(stages), 2)
        # Check names and basic settings of each stage
        self.assertEqual(stages[0].name, task1.name)
        self.assertEqual(stages[1].name, task2.name)
        # Check that each stage has a valid pattern conversion
        self.assertIsInstance(stages[0].pattern, BasePattern)
        self.assertIsInstance(stages[1].pattern, BasePattern)
        for stage in stages:
            self.assertEqual(stage.alignment.rect.left, alignment_area[0])
            self.assertEqual(stage.alignment.rect.top, alignment_area[1])
            self.assertAlmostEqual(stage.alignment.rect.width, 0.4)
            self.assertAlmostEqual(stage.alignment.rect.height, 0.3)

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
        self.assertAlmostEqual(stage.alignment.rect.left, 0.7)
        self.assertAlmostEqual(stage.alignment.rect.top, 0.375)
        self.assertAlmostEqual(stage.alignment.rect.width, 0.25)
        self.assertAlmostEqual(stage.alignment.rect.height, 0.25)
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
        "e-beam": deepcopy(beam),
        "ion-beam": deepcopy(beam),
        "ebeam-focus": {"position": [{"z": 5e-3}, "m"]},
        "se-detector": {"type": ["SE", ""]},
        "se-detector-ion": {"type": ["SE", ""]},
        "stage-bare": {"position": [{"x": 0.0, "y": 0.0, "z": 30e-3, "rx": 0.0, "rz": 0.0}, "m"]},
    }
    metadata["e-beam"]["probeCurrent"] = [100e-12, "A"]
    metadata["ion-beam"]["beamPreset"] = ["30 keV; 150 pA", None]
    return metadata


class TestTescanMetadataDefaults(unittest.TestCase):
    """Check metadata normalization without acquiring microscope settings."""

    def setUp(self):
        self.manager = fibsemos.FibsemOSMillingTaskManager.__new__(fibsemos.FibsemOSMillingTaskManager)
        self.manager.microscope = TescanMicroscopeStub()
        patcher = mock.patch.object(fibsemos, "OdemisTescanMicroscope", TescanMicroscopeStub, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_current_values(self):
        metadata = create_tescan_metadata()
        # A measured FIB current must not be copied into the setpoint field.
        metadata["ion-beam"]["probeCurrent"] = [90e-12, "A"]
        normalized = self.manager._normalize_system_state(metadata)
        self.assertEqual(normalized["e-beam"]["beamCurrent"], [100e-12, "A"])
        self.assertIsNone(normalized["ion-beam"]["beamCurrent"][0])

    def test_preserve_recorded_values(self):
        metadata = create_tescan_metadata()
        for beam_key in ("e-beam", "ion-beam"):
            metadata[beam_key]["beamCurrent"] = [150e-12, "A"]
            metadata[beam_key]["shift"] = [[1e-6, 2e-6], "m"]
            metadata[beam_key]["stigmator"] = [[0.1, 0.2], ""]
        metadata["ion-focus"] = {"position": [{"z": 10e-3}, "m"]}
        metadata["se-detector-ion"]["brightness"] = [0.7, ""]
        metadata["se-detector-ion"]["contrast"] = [0.0, ""]
        original = deepcopy(metadata)
        normalized = self.manager._normalize_system_state(metadata)
        self.assertEqual(normalized["ion-beam"], original["ion-beam"])
        self.assertEqual(normalized["ion-focus"], original["ion-focus"])
        self.assertEqual(normalized["se-detector-ion"]["contrast"], [0.0, ""])
        normalized["ion-beam"]["beamCurrent"][0] = 50e-12
        self.assertEqual(metadata, original)

    def test_missing_beam_settings(self):
        metadata = create_tescan_metadata()
        del metadata["e-beam"]
        with self.assertRaisesRegex(ValueError, "missing e-beam"):
            self.manager._normalize_system_state(metadata)

    def test_invalid_acquisition_settings(self):
        for field, value in (("dwellTime", 0.0), ("horizontalFoV", math.nan)):
            with self.subTest(field=field):
                metadata = create_tescan_metadata()
                metadata["ion-beam"][field][0] = value
                with self.assertRaisesRegex(ValueError, rf"invalid ion-beam\.{field}"):
                    self.manager._normalize_system_state(metadata)

    def test_missing_stage_position(self):
        metadata = create_tescan_metadata()
        del metadata["stage-bare"]["position"][0]["z"]
        with self.assertRaisesRegex(ValueError, r"stage-bare\.position\.z"):
            self.manager._normalize_system_state(metadata)

    @unittest.skipUnless(fibsemos.FIBSEMOS_INSTALLED, "fibsemOS is not available")
    def test_image_conversion(self):
        """Preserve acquisition provenance through real Tescan and Thermo image conversion."""
        for tescan, current in ((True, None), (False, 20e-12)):
            with self.subTest(tescan=tescan):
                self.manager.microscope = TescanMicroscopeStub() if tescan else SimpleNamespace()
                recorded = create_tescan_metadata()
                recorded["ion-beam"]["probeCurrent"] = [90e-12 if tescan else current, "A"]
                if not tescan:
                    del recorded["ion-beam"]["beamPreset"]
                metadata = self.manager._normalize_system_state(recorded)
                image = model.DataArray(numpy.zeros((6, 8), dtype=numpy.uint8), metadata={
                    model.MD_PIXEL_SIZE: (12.5e-6, 12.5e-6),
                    model.MD_ACQ_TYPE: model.MD_AT_FIB,
                    model.MD_DESCRIPTION: "Custom reference stream",
                    model.MD_ACQ_DATE: 1000.0,
                })
                converted = fibsemos._convert_reference_image(image, metadata, path="/tmp/reference.ome.tiff")
                state = converted.metadata.microscope_state
                self.assertIn("microscope_state", converted.metadata.to_dict())
                self.assertEqual(state.stage_position.z, 30e-3)
                self.assertEqual(state.timestamp, 1000.0)
                self.assertEqual(state.electron_beam.beam_current, 100e-12)
                self.assertEqual(state.ion_beam.beam_current, current)
                self.assertIsNone(state.ion_beam.shift)
                self.assertIsNone(state.ion_beam.stigmation)
                self.assertIsNone(state.ion_beam.working_distance)
                self.assertIsNone(state.ion_detector.brightness)
                self.assertEqual(converted.metadata.image_settings.dwell_time, 1e-6)
                self.assertEqual(converted.metadata.image_settings.hfw, 100e-6)
                self.assertEqual(converted.metadata.image_settings.path, "/tmp")
                self.assertEqual(converted.metadata.image_settings.filename, "reference.ome.tiff")


class TestThermoMetadataNormalization(unittest.TestCase):
    """Check preservation of Thermo's configured beam currents."""

    def setUp(self):
        self.manager = fibsemos.FibsemOSMillingTaskManager.__new__(fibsemos.FibsemOSMillingTaskManager)
        self.manager.microscope = SimpleNamespace()
        patcher = mock.patch.object(fibsemos, "OdemisTescanMicroscope", TescanMicroscopeStub, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.metadata = create_tescan_metadata()
        self.metadata["ion-beam"]["probeCurrent"] = [20e-12, "A"]

    def test_preserve_setpoints(self):
        normalized = self.manager._normalize_system_state(self.metadata)
        self.assertEqual(normalized["e-beam"]["beamCurrent"], [100e-12, "A"])
        self.assertEqual(normalized["ion-beam"]["beamCurrent"], [20e-12, "A"])
        self.assertEqual(normalized["ion-beam"]["probeCurrent"], [20e-12, "A"])
        self.assertIsNot(normalized["ion-beam"]["beamCurrent"], normalized["ion-beam"]["probeCurrent"])

    def test_missing_setpoint(self):
        for beam_key in ("e-beam", "ion-beam"):
            with self.subTest(beam=beam_key):
                metadata = deepcopy(self.metadata)
                del metadata[beam_key]["probeCurrent"]
                with self.assertRaisesRegex(ValueError, rf"{beam_key}\.probeCurrent"):
                    self.manager._normalize_system_state(metadata)

class TescanMicroscopeStub:
    """Identify the Tescan backend in manager tests without importing fibsemOS."""


class TestSystemState(unittest.TestCase):
    """Use real VAs to check mapping, snapshot isolation, and restoration."""

    def setUp(self):
        self.beam = simulated.GenericComponent("Custom FIB scanner", "ion-beam", vas={
            "beamPreset": {"value": "imaging"},
            "horizontalFoV": {"value": 100e-6, "unit": "m"},
            "rotation": {"value": 0.5, "unit": "rad"},
            "accelVoltage": {"value": 30e3, "readonly": True},
            "temperature": {"value": 25.0},
        })
        self.beam.resolution = model.ResolutionVA((8, 6), ((1, 1), (100, 100)))
        self.stage = simulated.Stage("Custom stage", "stage-bare", axes={"x"})
        self.addCleanup(self.stage.terminate)
        self.stage.moveAbs({"x": 1e-3}).result()
        decoy = simulated.GenericComponent("Ion-Beam", None)
        # VA-only GenericComponent instances have no actuator executor to terminate.
        for component in (self.beam, decoy):
            self.addCleanup(model.HwComponent.terminate, component)
        patcher = mock.patch.object(model, "getComponents", return_value=[self.beam, self.stage, decoy])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_capture_and_restore(self):
        snapshot = system_state.capture_system_state()
        self.assertEqual(set(snapshot), {"ion-beam", "stage-bare"})
        self.assertEqual(snapshot["ion-beam"]["horizontalFoV"], [100e-6, "m"])
        self.assertNotIn("temperature", snapshot["ion-beam"])
        self.assertNotIn(self.beam.name, snapshot)
        self.stage.moveAbs({"x": 2e-3}).result()
        self.assertEqual(snapshot["stage-bare"]["position"][0], {"x": 1e-3})
        self.beam.beamPreset.value = "milling"
        self.beam.horizontalFoV.value = 400e-6
        self.beam.rotation.value = 1.0
        self.beam.resolution.value = (10, 10)

        # Activating a hardware preset can reset scan settings.
        def activate_preset(value: str) -> str:
            """Simulate scan settings reset by preset activation."""
            self.beam.horizontalFoV.value = 400e-6
            return value
        self.beam.beamPreset = model.StringVA("milling", setter=activate_preset)
        # JSON turns tuples into lists.
        system_state.restore_system_state(json.loads(json.dumps(snapshot)))
        self.assertEqual(self.beam.beamPreset.value, "imaging")
        self.assertEqual(self.beam.horizontalFoV.value, 100e-6)
        self.assertEqual(self.beam.rotation.value, 0.5)
        self.assertEqual(self.beam.resolution.value, (8, 6))
        self.assertEqual(self.beam.accelVoltage.value, 30e3)
        self.assertEqual(self.stage.position.value, {"x": 2e-3})

    def test_focus_restoration(self):
        focus = simulated.Stage("Custom focus", "ion-focus", axes={"z"})
        self.addCleanup(focus.terminate)
        focus.moveAbs({"z": 5e-3}).result()
        with mock.patch.object(model, "getComponents", return_value=[focus, self.stage]):
            snapshot = system_state.capture_system_state()
            focus.moveAbs({"z": 10e-3}).result()
            system_state.restore_system_state(snapshot)
            self.assertEqual(focus.position.value, {"z": 5e-3})
            snapshot["ion-focus"]["position"][0]["z"] = None
            focus.moveAbs({"z": 10e-3}).result()
            system_state.restore_system_state(snapshot)
        self.assertEqual(focus.position.value, {"z": 10e-3})
        self.assertEqual(self.stage.position.value, {"x": 1e-3})

    def test_restoration_continues_after_failure(self):
        snapshot = system_state.capture_system_state()
        self.beam.horizontalFoV.value = 400e-6
        # Simulate a preset that the hardware can no longer activate.
        self.beam.beamPreset = model.StringEnumerated("milling", {"milling"})
        with self.assertLogs(level=logging.ERROR):
            with self.assertRaisesRegex(RuntimeError, "ion-beam.beamPreset"):
                system_state.restore_system_state(snapshot)
        self.assertEqual(self.beam.horizontalFoV.value, 100e-6)
        self.assertEqual(self.stage.position.value, {"x": 1e-3})

    def test_legacy_migration(self):
        for naming in ("roles", "legacy", "custom"):
            with self.subTest(naming=naming):
                expected = create_tescan_metadata()
                legacy = deepcopy(expected)
                if naming == "legacy":
                    legacy = {name: legacy[role] for name, role in system_state.FIBSEMOS_COMPONENT_ROLES.items()
                              if role in legacy}
                elif naming == "custom":
                    legacy[self.beam.name] = legacy.pop("ion-beam")
                snapshot = system_state.migrate_system_state(json.dumps(legacy))
                self.assertEqual(snapshot, expected)
        self.assertIsNone(system_state.migrate_system_state({}))

    def test_reference_state_uses_acquisition_record(self):
        """Resolve custom names and preserve recorded values after live settings change."""
        expected = create_tescan_metadata()
        detector = simulated.GenericComponent("Custom ion detector", "se-detector-ion")
        self.addCleanup(model.HwComponent.terminate, detector)
        recorded = deepcopy(expected)
        for component in (self.beam, self.stage, detector):
            recorded[component.name] = recorded.pop(component.role)
        recorded[self.beam.name]["temperature"] = [25.0, "C"]
        original = deepcopy(recorded)
        self.beam.horizontalFoV.value = 400e-6
        self.stage.moveAbs({"x": 2e-3}).result()
        with mock.patch.object(model, "getComponents", return_value=[self.beam, self.stage, detector]):
            state = system_state.system_state_from_settings(recorded, system_state.get_milling_components())
        self.assertEqual(state, expected)
        state["ion-beam"]["horizontalFoV"][0] = 200e-6
        self.assertEqual(recorded, original)


class TestMillingAPICompatibility(unittest.TestCase):
    """Check both strategy and converter APIs without hardware."""

    def setUp(self):
        self.microscope = TescanMicroscopeStub()
        for name in ("get", "set", "run_milling", "stop_milling"):
            setattr(self.microscope, name, mock.Mock())
        self.microscope.get.return_value = (1e-6, 2e-6)
        self.microscope.connection = mock.Mock()
        self.rect = SimpleNamespace(left=0.25, top=0.25, width=0.5, height=0.5)
        self.stage = SimpleNamespace(name="Test stage", alignment=SimpleNamespace(rect=self.rect),
                                     milling=SimpleNamespace(milling_channel="ION"), imaging=SimpleNamespace())
        self.feature = CryoFeature(name="f1", stage_position={}, fm_focus_position={})
        self.feature.reference_image = model.DataArray(numpy.arange(48).reshape(6, 8), metadata={
            model.MD_EXTRA_SETTINGS: {"user annotation": ["original", None]},
            model.MD_ACQ_TYPE: model.MD_AT_FIB,
            model.MD_DESCRIPTION: "Custom reference stream",
        })
        self.feature.system_state = create_tescan_metadata()
        self.restoration_state = create_tescan_metadata()
        self.restoration_state["ion-beam"]["beamPreset"][0] = "30 keV; 20 pA"
        self.restoration_state["ion-beam"]["probeCurrent"] = [20e-12, "A"]
        self.restoration_state["ion-beam"]["horizontalFoV"][0] = 200e-6
        self.recorded_microscope_state = SimpleNamespace(electron_beam=None, ion_beam=None)
        self.ref_image = SimpleNamespace(data=self.feature.reference_image.copy(),
                                        metadata=SimpleNamespace(image_settings=SimpleNamespace(),
                                                                 microscope_state=self.recorded_microscope_state))

    def _legacy_strategy(self, microscope: TescanMicroscopeStub, stage: SimpleNamespace,
                         asynch: bool = False, parent_ui: object = None) -> None:
        """Emulate the legacy fibsemOS strategy signature."""
        self.assertIs(stage.reference_image, self.ref_image)
        self.assertFalse(asynch)
        self.assertIsNone(parent_ui)
        microscope.run_milling()

    def _new_strategy(self, microscope: TescanMicroscopeStub, stage: SimpleNamespace,
                      asynch: bool = False, parent_ui: object = None, stop_event: object = None) -> None:
        """Emulate the newer strategy with its optional stop event."""
        self.assertIsNone(stop_event)
        self._legacy_strategy(microscope, stage, asynch, parent_ui)

    def _local_converter(self, image: model.DataArray, system_state: dict,
                         path: Optional[str] = None) -> SimpleNamespace:
        """Emulate the converter accepting a separate system snapshot."""
        self.assertEqual(system_state["ion-beam"]["beamPreset"][0], "30 keV; 150 pA")
        self.assertEqual(image.metadata[model.MD_EXTRA_SETTINGS],
                         self.feature.reference_image.metadata[model.MD_EXTRA_SETTINGS])
        return self.ref_image

    def _upstream_converter(self, image: model.DataArray, path: Optional[str] = None) -> SimpleNamespace:
        """Emulate the converter reading settings from image metadata."""
        state = image.metadata[model.MD_EXTRA_SETTINGS]
        self.assertEqual(state["Ion-Beam"]["beamPreset"][0], "30 keV; 150 pA")
        self.assertNotIn("ion-beam", state)
        self.assertEqual(image.metadata[model.MD_DESCRIPTION], "FIB")
        return self.ref_image

    def _run_with_api(self, strategy: Callable, converter: Optional[Callable] = None,
                      restoration_error: Optional[Exception] = None, stages: int = 1) -> None:
        """Run the real manager with an external API signature or failure."""
        converter = converter or self._local_converter
        self.convert = mock.create_autospec(converter, side_effect=converter)
        self.stage.strategy = SimpleNamespace(run=strategy)
        self.ref_image.data = self.feature.reference_image.copy()
        with mock.patch.object(fibsemos, "OdemisTescanMicroscope", TescanMicroscopeStub, create=True), \
             mock.patch.object(fibsemos, "create_fibsemos_microscope", return_value=self.microscope), \
             mock.patch.object(fibsemos, "capture_system_state", return_value=deepcopy(self.restoration_state)) as capture, \
             mock.patch.object(fibsemos, "restore_system_state", side_effect=restoration_error) as restore, \
             mock.patch.object(fibsemos, "from_odemis_image", self.convert, create=True):
            self.manager = fibsemos.FibsemOSMillingTaskManager()
            self.manager._future = futures.Future()
            self.manager._active = True
            self.manager.milling_stages = [self.stage] * stages
            self.manager.feature = self.feature
            self.manager.path = "/tmp/milling"
            try:
                self.manager._run()
            finally:
                self.restore = restore
                self.capture = capture

    def _check_reference_image(self) -> None:
        """Check the cropped reference and completed manager state."""
        self.assertIs(self.stage.reference_image, self.ref_image)
        numpy.testing.assert_array_equal(self.ref_image.data, self.feature.reference_image[1:4, 2:6])
        self.assertEqual(self.ref_image.metadata.image_settings.path, "/tmp/milling")
        self.assertIs(self.ref_image.metadata.image_settings.reduced_area, self.rect)
        self.assertEqual(self.feature.reference_image.shape, (6, 8))
        self.assertIs(self.ref_image.metadata.microscope_state, self.recorded_microscope_state)
        self.assertFalse(self.manager._active)

    def test_strategy_and_converter_compatibility(self):
        cases = ((self._legacy_strategy, self._upstream_converter), (self._new_strategy, self._local_converter))
        for api, converter in cases:
            with self.subTest(strategy=api.__name__, converter=converter.__name__):
                original_state = deepcopy(self.feature.system_state)
                original_metadata = deepcopy(self.feature.reference_image.metadata)
                strategy = mock.create_autospec(api, side_effect=api)
                self._run_with_api(strategy, converter)
                self.assertEqual(strategy.call_count, 1)
                self.assertEqual(self.feature.reference_image.metadata, original_metadata)
                self.assertEqual(self.feature.system_state, original_state)
                image = self.convert.call_args.args[0]
                self.assertIsNot(image.metadata, self.feature.reference_image.metadata)
                self.assertTrue(numpy.shares_memory(image, self.feature.reference_image))
                self.capture.assert_called_once_with()
                self.restore.assert_called_once_with(self.restoration_state)
                self._check_reference_image()
                self.microscope.connection.DrawBeam.UnloadLayer.assert_called()
                self.microscope.connection.FIB.Preset.Activate.assert_not_called()
                self.microscope.set.assert_called_with("shift", value=(1e-6, 2e-6), beam_type="ION")

    def test_metadata_and_snapshot_are_independent(self):
        """Converter edits must not change the feature or its restoration snapshot."""
        original_state = deepcopy(self.feature.system_state)
        original_metadata = deepcopy(self.feature.reference_image.metadata)

        def converter(image: model.DataArray, system_state: dict,
                      path: Optional[str] = None) -> SimpleNamespace:
            """Simulate an external converter modifying its inputs."""
            image.metadata[model.MD_EXTRA_SETTINGS]["user annotation"][0] = "changed"
            system_state["ion-beam"]["dwellTime"][0] = 5e-6
            return self.ref_image

        strategy = mock.create_autospec(self._new_strategy)
        self._run_with_api(strategy, converter)
        self.assertEqual(self.feature.reference_image.metadata, original_metadata)
        self.assertEqual(self.feature.system_state, original_state)
        self.restore.assert_called_once_with(self.restoration_state)

    def test_missing_state_or_preset_prevents_milling(self):
        for state in (None, create_tescan_metadata()):
            with self.subTest(state=state):
                self.feature.system_state = state
                if state is not None:
                    del state["ion-beam"]["beamPreset"]
                strategy = mock.create_autospec(self._new_strategy)
                with self.assertRaisesRegex(ValueError, "acquire a new reference image"):
                    self._run_with_api(strategy)
                strategy.assert_not_called()
                self.restore.assert_not_called()
                self.assertFalse(self.manager._active)

    def test_thermo_current_normalization(self):
        self.microscope = mock.Mock()
        self.feature.system_state["ion-beam"]["probeCurrent"] = [20e-12, "A"]
        strategy = mock.create_autospec(self._new_strategy, side_effect=self._new_strategy)
        self._run_with_api(strategy)
        self.microscope.finish_milling.assert_called_once_with(imaging_current=20e-12, imaging_voltage=30e3)
        self.assertNotIn("beamCurrent", self.feature.system_state["ion-beam"])
        self._check_reference_image()

    def test_errors_are_not_retried(self):
        for failure in ("conversion", "milling"):
            with self.subTest(failure=failure):
                strategy = mock.create_autospec(self._new_strategy)
                converter = self._local_converter
                if failure == "conversion":
                    def converter(image: model.DataArray, system_state: dict,
                                  path: Optional[str] = None) -> SimpleNamespace:
                        """Simulate an internal converter failure."""
                        raise TypeError("Conversion failed")
                else:
                    strategy.side_effect = TypeError("Milling failed")
                with self.assertRaisesRegex(TypeError, "failed"):
                    self._run_with_api(strategy, converter, stages=2)
                self.assertEqual(self.convert.call_count, 1)
                self.assertEqual(strategy.call_count, int(failure == "milling"))
                self.assertEqual(self.restore.call_count, int(failure == "milling"))
                self.assertFalse(self.manager._active)

    def test_cleanup_failure_preserves_milling_error(self):
        """A pattern cleanup error must not replace the original milling error."""
        self.microscope.connection.DrawBeam.UnloadLayer.side_effect = RuntimeError("Cleanup failed")
        strategy = mock.create_autospec(self._new_strategy, side_effect=TypeError("Milling failed"))
        with self.assertRaisesRegex(TypeError, "Milling failed"):
            self._run_with_api(strategy)
        self.restore.assert_called_once_with(self.restoration_state)
        self.microscope.set.assert_called_once_with("shift", value=(1e-6, 2e-6), beam_type="ION")
        self.assertFalse(self.manager._active)

    def test_restoration_failure_is_reported(self):
        """Report failed restoration after an otherwise successful milling run."""
        strategy = mock.create_autospec(self._new_strategy, side_effect=self._new_strategy)
        with self.assertRaisesRegex(RuntimeError, "Restoration failed"):
            self._run_with_api(strategy, restoration_error=RuntimeError("Restoration failed"))
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
