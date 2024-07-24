# -*- coding: utf-8 -*-
"""
Created on 24 Jan 2024

@author: Karishma Kumar

Copyright © 2024 Karishma Kumar, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import os
import unittest

import odemis
from odemis import model
from odemis.acq.stream import FluoStream
from odemis.acq.stream_settings import StreamSettingsConfig, get_settings_order
from odemis.util import testing

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"


class TestAcquiredStreamSettings(unittest.TestCase):
    """
    Test the json file for reading and writing the most recently used acquired stream settings
    """
    # 11 entries and two duplication of the key "name"
    test_data = [{'name': 'Filtered Colour 1', 'excitation': [0, 1, 2],
                  'power': 0.0000000012, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Filtered Colour 2', 'excitation': [0, 1, 2],
                  'power': 0.0000000045, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Filtered Colour 3', 'excitation': [0, 1, 2],
                  'power': 0.0000000056, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Filtered Colour 4', 'excitation': [0, 1, 2],
                  'power': 0.0000000067, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Few settings', 'detExposureTime': 0.89},  # Only exposure time should be applied
                 {'name': 'Filtered Colour 6', 'excitation': [0, 1, 2],
                  'power': 0.0000000078, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Filtered Colour 7', 'excitation': [0, 1, 2],
                  'power': 0.0000000091, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Filtered Colour 8', 'excitation': [0, 1, 2],
                  'power': 0.0000000092, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Filtered Colour 9', 'excitation': [0, 1, 2],
                  'power': 0.0000000093, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 {'name': 'Filtered Colour 10', 'excitation': [0, 1, 2],
                  'power': 0.0000000094, 'emission': [0.00001, 1.234e-07],
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345},
                 # Add tint
                 {'name': 'Filtered Colour 1', 'excitation': [0, 1, 2],
                  'power': 0.0000000095, 'emission': [0.00001, 1.234e-07], "tint": "red",
                  'detExposureTime': 0.00123456789, 'auto_bc_outliers': 0.12345}]

    @classmethod
    def setUpClass(cls):
        testing.start_backend(METEOR_CONFIG)
        cls.stage = model.getComponent(role="stage")
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.focus = model.getComponent(role="focus")
        cls.filter = model.getComponent(role="filter")

    def setUp(self):
        file_path = os.path.abspath(os.path.join("", "settings.json"))
        # Always start from scratch
        try:
            os.remove(file_path)
        except OSError:
            pass

        self.acq_settings = StreamSettingsConfig(file_path, 10)
        # Use the test_data as config_data
        self.acq_settings.update_data(self.test_data)

    def test_entries(self):
        """The name of entries in the json file is unique"""
        # Only one stream should have Filtered Colour 1 as the name
        index = next((i for i, k in enumerate(self.acq_settings.config_data) if k["name"] == "Filtered Colour 1"),
                     None)
        # Find other streams having the same stream name
        # by leaving the first entry with the same name
        is_duplicate = next((False for k in self.acq_settings.config_data[index + 1] if k != "Filtered Colour 1"), True)
        # Assert that there are no duplicates
        self.assertFalse(is_duplicate, "Duplicate stream names found")

        # the length of extracted data to save should be less or equal to set maximum
        self.assertLessEqual(len(self.acq_settings.config_data), self.acq_settings.max_entries)
        # the length of saved data after reading should be less or equal to set maximum
        # self.acq_settings.read()
        self.assertLessEqual(len(self.acq_settings.config_data), self.acq_settings.max_entries,
                             "entries are more than maximum entries")

        # the last entry in test_data i.e. the latest should be the first entry of the json file
        self.assertEqual(self.acq_settings.config_data[0], self.test_data[-1])
        # the first entry in test_data should no longer be in the json file as it will be the 11th entry in the json
        if len(self.test_data) > self.acq_settings.max_entries:
            is_existing = next((True for k in self.acq_settings.config_data if k == self.test_data[0]), False)
            self.assertFalse(is_existing, "JSON file is not updated correctly with the new entries")

    def test_get_streams_settings(self):
        """Get the stream settings from the input stream and save the settings in the JSON file"""
        fms = FluoStream("fluo", self.ccd, self.ccd.data,
                         self.light, self.filter, focuser=self.focus, detvas={"exposureTime"})
        fms.excitation.value = (3.9e-07, 3.97e-07, 4.0000000000000003e-07, 4.0300000000000005e-07,
                                4.1000000000000004e-07)
        fms.power.value = 0.24
        fms.emission.value = (5.795e-07, 6.105e-07)
        fms.tint.value = (12, 13, 14)
        fms.detExposureTime.value = 0.12
        fms.auto_bc_outliers.value = 0.45
        # save the fluo stream settings in the JSON file
        self.acq_settings.update_entries([fms])
        # find the index of the list for the recently saved fluo stream
        index = self.acq_settings._get_config_index(self.acq_settings.config_data, "fluo")
        # get the saved data from the JSON file
        data = self.acq_settings.config_data[index]
        # check the values of the given stream with the values saved in the JSON file
        settings_order = get_settings_order(fms)
        for key in settings_order:
            self.assertEqual(data[key], getattr(fms, key).value)

    def test_set_streams_settings(self):
        """Load the stream from the JSON file and set the input stream with loaded settings"""
        # save a stream setting in the JSON file
        s = FluoStream("fluo", self.ccd, self.ccd.data,
                       self.light, self.filter, focuser=self.focus, detvas={"exposureTime"})
        s.excitation.value = (3.9e-07, 3.97e-07, 4.0000000000000003e-07, 4.0300000000000005e-07,
                              4.1000000000000004e-07)
        s.power.value = 0.24
        s.emission.value = (5.795e-07, 6.105e-07)
        s.tint.value = (12, 13, 14)
        s.detExposureTime.value = 0.12
        s.auto_bc_outliers.value = 0.45
        self.acq_settings.update_entries([s])
        # Set the values of test_fluo stream from the values of saved fluo stream
        fms = FluoStream("fluo", self.ccd, self.ccd.data,
                         self.light, self.filter, focuser=self.focus, detvas={"exposureTime"})
        self.acq_settings.apply_settings(fms, "fluo")
        index = self.acq_settings._get_config_index(self.acq_settings.config_data, "fluo")
        data = self.acq_settings.config_data[index]
        settings_order = get_settings_order(fms)
        for key in settings_order:
            self.assertEqual(getattr(fms, key).value, data[key])

    def test_set_streams_settings_missing_keys(self):
        s = FluoStream("fluo", self.ccd, self.ccd.data,
                       self.light, self.filter, focuser=self.focus, detvas={"exposureTime"})

        s.excitation.value = (3.9e-07, 3.97e-07, 4.0000000000000003e-07, 4.0300000000000005e-07,
                              4.1000000000000004e-07)
        s.power.value = 0.24
        s.emission.value = (5.795e-07, 6.105e-07)
        s.tint.value = (12, 13, 14)
        s.detExposureTime.value = 0.12
        s.auto_bc_outliers.value = 0.45

        self.acq_settings.apply_settings(s, "Few settings")
        self.assertEqual(s.detExposureTime.value, 0.89)  # As defined in the "Few settings" entry
        # The rest shouldn't have changed
        self.assertEqual(s.power.value, 0.24)
        self.assertEqual(s.tint.value, (12, 13, 14))

    def test_get_settings_order(self):
        """Test the list of required setting parameters used for loading/saving an old stream"""
        required_setting_keys = ["excitation", "power", "emission", "auto_bc_outliers", "tint", "name",
                                 "detExposureTime"]
        fms = FluoStream("fluo", self.ccd, self.ccd.data,
                         self.light, self.filter, focuser=self.focus, detvas={"exposureTime"})
        settings_order = get_settings_order(fms)
        is_missing_settings = set(required_setting_keys).difference(settings_order)
        # Check if all required settings are present
        self.assertTrue(is_missing_settings == set())
        # Check if tint is present after excitation, power and emission
        self.assertTrue(
            settings_order.index("excitation") < settings_order.index("emission") < settings_order.index("tint"))


if __name__ == "__main__":
    unittest.main()
