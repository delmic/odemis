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
import logging
import unittest
import json
import numpy
from odemis.acq.milling.patterns import RectanglePatternParameters, TrenchPatternParameters, MicroexpansionPatternParameters
from odemis.acq.milling.tasks import MillingTaskSettings, MillingSettings2

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

class MillingTaskTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        pass

    def test_milling_settings2(self):

        current = 100e-9
        voltage = 30e3
        field_of_view = 400e-6
        mode = "Serial"
        channel = "ion"
        milling_settings = MillingSettings2(current, voltage, field_of_view, mode, channel)

        self.assertEqual(milling_settings.current.value, current)
        self.assertEqual(milling_settings.voltage.value, voltage)
        self.assertEqual(milling_settings.field_of_view.value, field_of_view)
        self.assertEqual(milling_settings.mode.value, mode)
        self.assertEqual(milling_settings.channel.value, channel)

        json_data = milling_settings.to_json()
        self.assertEqual(json_data["current"], current)
        self.assertEqual(json_data["voltage"], voltage)
        self.assertEqual(json_data["field_of_view"], field_of_view)
        self.assertEqual(json_data["mode"], mode)
        self.assertEqual(json_data["channel"], channel)

        milling_settings_from_json = MillingSettings2.from_json(json_data)
        self.assertEqual(milling_settings_from_json.current.value, current)
        self.assertEqual(milling_settings_from_json.voltage.value, voltage)
        self.assertEqual(milling_settings_from_json.field_of_view.value, field_of_view)
        self.assertEqual(milling_settings_from_json.mode.value, mode)
        self.assertEqual(milling_settings_from_json.channel.value, channel)

    def test_milling_task_settings(self):

        milling_settings = MillingSettings2(100e-9, 30e3, 400e-6, "Serial", "ion")
        trench_pattern = TrenchPatternParameters(1e-6, 1e-6, 100e-9, 1e-6, (0, 0))

        milling_task_settings = MillingTaskSettings(milling_settings, [trench_pattern])

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

        json_data = milling_task_settings.to_json()
        self.assertEqual(json_data["name"], "Milling Task")
        self.assertEqual(json_data["milling"], milling_settings.to_json())
        self.assertEqual(json_data["patterns"][0], trench_pattern.to_json())

        milling_task_settings_from_json = MillingTaskSettings.from_json(json_data)
        self.assertEqual(milling_task_settings_from_json.milling.current.value, milling_settings.current.value)
        self.assertEqual(milling_task_settings_from_json.milling.voltage.value, milling_settings.voltage.value)
        self.assertEqual(milling_task_settings_from_json.milling.field_of_view.value, milling_settings.field_of_view.value)
        self.assertEqual(milling_task_settings_from_json.milling.mode.value, milling_settings.mode.value)
        self.assertEqual(milling_task_settings_from_json.milling.channel.value, milling_settings.channel.value)
        self.assertEqual(milling_task_settings_from_json.patterns[0].width.value, trench_pattern.width.value)
        self.assertEqual(milling_task_settings_from_json.patterns[0].height.value, trench_pattern.height.value)
        self.assertEqual(milling_task_settings_from_json.patterns[0].depth.value, trench_pattern.depth.value)
        self.assertEqual(milling_task_settings_from_json.patterns[0].spacing.value, trench_pattern.spacing.value)
        self.assertEqual(milling_task_settings_from_json.patterns[0].center.value, trench_pattern.center.value)

    def test_save_load_milling_tasks(self):
        pass

        # TODO: millmng mill tasks
