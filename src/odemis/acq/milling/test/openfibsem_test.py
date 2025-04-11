# -*- coding: utf-8 -*-
"""
Created on Feb 2025

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
import unittest

from odemis.acq.milling import openfibsem  # to load the openfibsem module

try:
    from fibsem.milling import MillingAlignment
    from fibsem.milling.patterning.patterns2 import (
        BasePattern,
        MicroExpansionPattern,
        RectanglePattern,
        TrenchPattern,
    )
    from fibsem.structures import Point
    from odemis.acq.milling.openfibsem import (
        convert_milling_settings,
        convert_milling_tasks_to_milling_stages,
        convert_pattern_to_openfibsem,
        convert_task_to_milling_stage,
    )
except ImportError:
    pass

from odemis.acq.milling.patterns import (
    MicroexpansionPatternParameters,
    RectanglePatternParameters,
    TrenchPatternParameters,
)
from odemis.acq.milling.tasks import MillingSettings, MillingTaskSettings

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

# Create dummy parameter objects to pass into converter functions.
def create_rectangle_pattern_params():
    return RectanglePatternParameters(
        name="Rectangle-1",
        width=10e-6,
        height=15e-6,
        depth=5e-6,
        rotation=0,
        center=(100, 150),
        scan_direction="TopToBottom",
    )

def create_trench_pattern_params():
    return TrenchPatternParameters(
        name="Trench-1",
        width=12e-6,
        height=8e-6,
        depth=4e-6,
        spacing=3e-6,
        center=(50, 75),
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
            if not openfibsem.OPENFIBSEM_INSTALLED:
                raise ImportError("OpenFIBSEM package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the openfibsem tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")

    def test_convert_rectangle_pattern(self):
        pattern_param = create_rectangle_pattern_params()
        converted = convert_pattern_to_openfibsem(pattern_param)
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
        converted = convert_pattern_to_openfibsem(pattern_param)
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
        converted = convert_pattern_to_openfibsem(pattern_param)
        self.assertIsInstance(converted, MicroExpansionPattern)
        self.assertAlmostEqual(converted.width, pattern_param.width.value)
        self.assertAlmostEqual(converted.height, pattern_param.height.value)
        self.assertAlmostEqual(converted.depth, pattern_param.depth.value)
        self.assertAlmostEqual(converted.distance, pattern_param.spacing.value)
        self.assertEqual(converted.point, Point(x=pattern_param.center.value[0],
                                                y=pattern_param.center.value[1]))

class TestConvertMillingSettings(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            if not openfibsem.OPENFIBSEM_INSTALLED:
                raise ImportError("OpenFIBSEM package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the openfibsem tests, correct libraries "
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

class TestConvertTaskToMillingStage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            if not openfibsem.OPENFIBSEM_INSTALLED:
                raise ImportError("OpenFIBSEM package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the openfibsem tests, correct libraries "
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

        # Check pattern conversion: since we passed a rectangle, expect RectanglePattern output.
        self.assertIsInstance(stage.pattern, RectanglePattern)

        # Check alignment conversion; alignment.enabled should reflect dummy_milling.align.value.
        self.assertIsInstance(stage.alignment, MillingAlignment)
        self.assertEqual(stage.alignment.enabled, dummy_milling.align.value)

class TestConvertMillingTasksToMillingStages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            if not openfibsem.OPENFIBSEM_INSTALLED:
                raise ImportError("OpenFIBSEM package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the openfibsem tests, correct libraries "
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

if __name__ == "__main__":
    unittest.main()
