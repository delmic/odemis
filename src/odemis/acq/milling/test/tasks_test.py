# -*- coding: utf-8 -*-
"""
@author: Patrick Cleeve

Copyright © 2024, Delmic

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
import os
import logging
import unittest
from odemis.acq.milling.patterns import TrenchPatternParameters, MicroexpansionPatternParameters
from odemis.acq.milling.tasks import MillingTaskSettings, MillingSettings, load_milling_tasks, save_milling_tasks

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

TASKS_PATH = os.path.join(os.path.dirname(__file__), "milling_tasks.yaml")

class MillingTaskTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(TASKS_PATH):
            os.remove(TASKS_PATH)

    def setUp(self):
        pass

    def test_milling_settings(self):
        current = 100e-9
        voltage = 30e3
        field_of_view = 400e-6
        mode = "Serial"
        channel = "ion"
        milling_settings = MillingSettings(current, voltage, field_of_view, mode, channel)

        self.assertEqual(milling_settings.current.value, current)
        self.assertEqual(milling_settings.voltage.value, voltage)
        self.assertEqual(milling_settings.field_of_view.value, field_of_view)
        self.assertEqual(milling_settings.mode.value, mode)
        self.assertEqual(milling_settings.channel.value, channel)

        dict_data = milling_settings.to_dict()
        self.assertEqual(dict_data["current"], current)
        self.assertEqual(dict_data["voltage"], voltage)
        self.assertEqual(dict_data["field_of_view"], field_of_view)
        self.assertEqual(dict_data["mode"], mode)
        self.assertEqual(dict_data["channel"], channel)

        milling_settings_from_dict = MillingSettings.from_dict(dict_data)
        self.assertEqual(milling_settings_from_dict.current.value, current)
        self.assertEqual(milling_settings_from_dict.voltage.value, voltage)
        self.assertEqual(milling_settings_from_dict.field_of_view.value, field_of_view)
        self.assertEqual(milling_settings_from_dict.mode.value, mode)
        self.assertEqual(milling_settings_from_dict.channel.value, channel)

    def test_milling_task_settings(self):
        milling_settings = MillingSettings(100e-9, 30e3, 400e-6, "Serial", "ion")
        trench_pattern = TrenchPatternParameters(1e-6, 1e-6, 100e-9, 1e-6, (0, 0))

        milling_task_settings = MillingTaskSettings(milling_settings, [trench_pattern], "Milling Task")

        self.assertEqual(milling_task_settings.milling.current.value, milling_settings.current.value)
        self.assertEqual(milling_task_settings.milling.voltage.value, milling_settings.voltage.value)
        self.assertEqual(milling_task_settings.milling.field_of_view.value, milling_settings.field_of_view.value)
        self.assertEqual(milling_task_settings.milling.mode.value, milling_settings.mode.value)
        self.assertEqual(milling_task_settings.milling.channel.value, milling_settings.channel.value)
        self.assertEqual(milling_task_settings.patterns[0].width.value, trench_pattern.width.value)
        self.assertEqual(milling_task_settings.patterns[0].height.value, trench_pattern.height.value)
        self.assertEqual(milling_task_settings.patterns[0].depth.value, trench_pattern.depth.value)
        self.assertEqual(milling_task_settings.patterns[0].spacing.value, trench_pattern.spacing.value)
        self.assertEqual(milling_task_settings.patterns[0].center.value, trench_pattern.center.value)

        dict_data = milling_task_settings.to_dict()
        self.assertEqual(dict_data["name"], "Milling Task")
        self.assertEqual(dict_data["selected"], True)
        self.assertEqual(dict_data["milling"], milling_settings.to_dict())
        self.assertEqual(dict_data["patterns"][0], trench_pattern.to_dict())

        milling_task_settings_from_dict = MillingTaskSettings.from_dict(dict_data)
        self.assertEqual(milling_task_settings_from_dict.milling.current.value, milling_settings.current.value)
        self.assertEqual(milling_task_settings_from_dict.milling.voltage.value, milling_settings.voltage.value)
        self.assertEqual(milling_task_settings_from_dict.milling.field_of_view.value, milling_settings.field_of_view.value)
        self.assertEqual(milling_task_settings_from_dict.milling.mode.value, milling_settings.mode.value)
        self.assertEqual(milling_task_settings_from_dict.milling.channel.value, milling_settings.channel.value)
        self.assertEqual(milling_task_settings_from_dict.patterns[0].width.value, trench_pattern.width.value)
        self.assertEqual(milling_task_settings_from_dict.patterns[0].height.value, trench_pattern.height.value)
        self.assertEqual(milling_task_settings_from_dict.patterns[0].depth.value, trench_pattern.depth.value)
        self.assertEqual(milling_task_settings_from_dict.patterns[0].spacing.value, trench_pattern.spacing.value)
        self.assertEqual(milling_task_settings_from_dict.patterns[0].center.value, trench_pattern.center.value)

    def test_save_load_task_settings(self):
        milling_settings = MillingSettings(100e-9, 30e3, 400e-6, "Serial", "ion")
        trench_pattern = TrenchPatternParameters(10e-6, 3e-6, 100e-9, 2e-6, (0, 0))
        trench_task_settings = MillingTaskSettings(milling_settings, [trench_pattern], "Trench")

        milling_settings = MillingSettings(100e-9, 30e3, 400e-6, "Serial", "ion")
        microexpansion_pattern = MicroexpansionPatternParameters(1e-6, 10e-6, 100e-9, 10e-6, (0, 0))
        microexpansion_task_settings = MillingTaskSettings(milling_settings, [microexpansion_pattern], "Microexpansion")

        tasks = {"Trench": trench_task_settings, "Microexpansion": microexpansion_task_settings}

        # save and load the tasks
        save_milling_tasks(path=TASKS_PATH, milling_tasks=tasks)
        loaded_tasks = load_milling_tasks(TASKS_PATH)

        self.assertTrue("Trench" in loaded_tasks)
        self.assertTrue("Microexpansion" in loaded_tasks)

        self.assertEqual(loaded_tasks["Trench"].milling.current.value, trench_task_settings.milling.current.value)
        self.assertEqual(loaded_tasks["Trench"].milling.voltage.value, trench_task_settings.milling.voltage.value)
        self.assertEqual(loaded_tasks["Trench"].milling.field_of_view.value, trench_task_settings.milling.field_of_view.value)
        self.assertEqual(loaded_tasks["Trench"].milling.mode.value, trench_task_settings.milling.mode.value)
        self.assertEqual(loaded_tasks["Trench"].milling.channel.value, trench_task_settings.milling.channel.value)
        self.assertEqual(loaded_tasks["Trench"].patterns[0].width.value, trench_task_settings.patterns[0].width.value)
        self.assertEqual(loaded_tasks["Trench"].patterns[0].height.value, trench_task_settings.patterns[0].height.value)
        self.assertEqual(loaded_tasks["Trench"].patterns[0].depth.value, trench_task_settings.patterns[0].depth.value)
        self.assertEqual(loaded_tasks["Trench"].patterns[0].spacing.value, trench_task_settings.patterns[0].spacing.value)
        self.assertEqual(loaded_tasks["Trench"].patterns[0].center.value, trench_task_settings.patterns[0].center.value)

        self.assertEqual(loaded_tasks["Microexpansion"].milling.current.value, microexpansion_task_settings.milling.current.value)
        self.assertEqual(loaded_tasks["Microexpansion"].milling.voltage.value, microexpansion_task_settings.milling.voltage.value)
        self.assertEqual(loaded_tasks["Microexpansion"].milling.field_of_view.value, microexpansion_task_settings.milling.field_of_view.value)
        self.assertEqual(loaded_tasks["Microexpansion"].milling.mode.value, microexpansion_task_settings.milling.mode.value)
        self.assertEqual(loaded_tasks["Microexpansion"].milling.channel.value, microexpansion_task_settings.milling.channel.value)
        self.assertEqual(loaded_tasks["Microexpansion"].patterns[0].width.value, microexpansion_task_settings.patterns[0].width.value)
        self.assertEqual(loaded_tasks["Microexpansion"].patterns[0].height.value, microexpansion_task_settings.patterns[0].height.value)
        self.assertEqual(loaded_tasks["Microexpansion"].patterns[0].depth.value, microexpansion_task_settings.patterns[0].depth.value)
        self.assertEqual(loaded_tasks["Microexpansion"].patterns[0].spacing.value, microexpansion_task_settings.patterns[0].spacing.value)
        self.assertEqual(loaded_tasks["Microexpansion"].patterns[0].center.value, microexpansion_task_settings.patterns[0].center.value)


if __name__ == "__main__":
    unittest.main()
