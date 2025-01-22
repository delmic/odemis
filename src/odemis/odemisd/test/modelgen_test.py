#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 16 Dec 2020

@author: Kornee Kleijwegt

Copyright © 2019-2021 Kornee Kleijwegt, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see
http://www.gnu.org/licenses/.
"""
import logging
import os
import unittest

import yaml
from odemis.odemisd.modelgen import ParseError, SafeLoader

TEST_FILES_PATH = os.path.dirname(__file__)

logging.getLogger().setLevel(logging.DEBUG)


class SafeLoaderExtensionsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(
                os.path.join(TEST_FILES_PATH,
                             "yaml-merger-combination-include-extend-expected-result.yaml"), "r") as f:
            cls.expected_full_result = yaml.load(f, SafeLoader)

    def test_include(self):
        """
        Basic test for the include keyword in a yaml file to include the content of a file as the value of a key.
        """
        # __init__ of the CL-detector
        with open(
                os.path.join(TEST_FILES_PATH,
                             "yaml-merger-include-complete-init-CL-Detector-test.odm.yaml"), "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = self.expected_full_result["CL Detector"]["init"]
        self.assertEqual(expected_result, data_found)

        # Full CL-detector component
        with open(
                os.path.join(TEST_FILES_PATH,
                             "yaml-merger-include-full-CL-Detector-component-test.odm.yaml"), "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = {"CL Detector": self.expected_full_result["CL Detector"]}
        self.assertEqual(expected_result, data_found)

        # __init__ of the CL-detector
        # Contains "settle_time" which is overwritten by the second !include
        with open(
                os.path.join(TEST_FILES_PATH,
                             "yaml-merger-include-complete-overwrite-init-CL-Detector-test.odm.yaml"), "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = self.expected_full_result["CL Detector"]["init"].copy()  # Copy because a value is adjusted
        expected_result["settle_time"] = ["overwritten", "values", "are", "correctly", "stored"]
        self.assertEqual(expected_result, data_found)

        # __init__ of the CL-detector using a relative reference in the !include
        with open(os.path.join(TEST_FILES_PATH,
                               "yaml-merger-relative-path-test",
                               "yaml-merger-include-complete-init-relative-path-CL-Detector-test.odm.yaml"),
                  "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = self.expected_full_result["CL Detector"]["init"]
        self.assertEqual(expected_result, data_found)

        with self.assertRaises(FileNotFoundError):
            with open(os.path.join(TEST_FILES_PATH,
                                   "yaml-merger-include-error-non-exist-file-in-init-CL-Detector-test.odm.yaml"),
                      'r') as f:
                data_found = yaml.load(f, SafeLoader)

        with self.assertRaises(ParseError):
            with open(os.path.join(TEST_FILES_PATH,
                                   "yaml-merger-include-error-reference-to-typo-in-init-CL-Detector-test.odm.yaml"),
                      'r') as f:
                data_found = yaml.load(f, SafeLoader)

    def test_extend_double(self):
        """
        Test for 2 extend keywords in row
        """
        # __init__ of the SEM Scan Interface
        with open(os.path.join(TEST_FILES_PATH,
                               "two-extends-in-a-row.odm.yaml"), "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        self.assertEqual(len(data_found["init"]), 5)

    def test_extend(self):
        """
        Basic test for the extend keyword in a yaml file to extend a dictionary with the content of a file.
        """
        # __init__ of the SEM Scan Interface
        with open(os.path.join(TEST_FILES_PATH,
                               "yaml-merger-extend-complete-init-SEM-Scan-Interface-test.odm.yaml"),
                              "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = {"init": self.expected_full_result["SEM Scan Interface"]["init"]}
        self.assertEqual(expected_result, data_found)

        # Full SEM Scan Interface component
        with open(os.path.join(TEST_FILES_PATH,
                               "yaml-merger-extend-full-SEM-Scan-Interface-component-test.odm.yaml"), "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = {"SEM Scan Interface": self.expected_full_result["SEM Scan Interface"]}
        self.assertEqual(expected_result, data_found)

        # __init__ of the SEM Scan Interface using a relative reference in the !include
        with open(os.path.join(TEST_FILES_PATH,
                               "yaml-merger-relative-path-test",
                               "yaml-merger-extend-complete-init-relative-path-SEM-Scan-Interface-test.odm.yaml"),
                  "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = {"init": self.expected_full_result["SEM Scan Interface"]["init"]}
        self.assertEqual(expected_result, data_found)

        with self.assertRaises(FileNotFoundError):
            with open(os.path.join(TEST_FILES_PATH,
                                   "yaml-merger-extend-error-non-exist-file-in-init-SEM-Scan-interface-test.odm.yaml"),
                      'r') as f:
                data_found = yaml.load(f, SafeLoader)

        with self.assertRaises(ParseError):
            with open(os.path.join(TEST_FILES_PATH,
                                   "yaml-merger-extend-error-reference-to-typo-in-init-SEM-Scan-Interface.odm.yaml"),
                      'r') as f:
                data_found = yaml.load(f, SafeLoader)

    def test_extend_override(self):
        """
        Check behaviour of extend when the key already exists (it's overriden)
        """
        # TODO: for now, the latest value is used... however the plan is to eventually
        # change this behaviour and raise an error, because it's typically a sign
        # of a mistake if a key is written twice, and there is no use case for that.

        # __init__ of the SEM Scan Interface
        # Contains the entries "username" and "password" which are overwritten by the second !extend
        with open(os.path.join(TEST_FILES_PATH,
                               "yaml-merger-extend-complete-init-overwrite-SEM-Scan-Interface-test.odm.yaml"),
                  "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = {"init": self.expected_full_result["SEM Scan Interface"]["init"].copy()}
        expected_result["init"]["username"] = 'user1_overwritten'
        expected_result["init"]["password"] = 'complicated_and_overwritten'

        self.assertEqual(expected_result, data_found)

    def test_combination_include_extend_two_components_dict(self):
        """
        Extended test combining the extend and include keywords in a file to a single dict.
        """
        # CL Detector and SEM Scan Interface component
        with open(os.path.join(TEST_FILES_PATH,
                               "yaml-merger-combination-two-components.yaml"), "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        expected_result = {"SEM Scan Interface": self.expected_full_result["SEM Scan Interface"],
                           "CL Detector": self.expected_full_result["CL Detector"]}
        self.assertEqual(expected_result, data_found)

    def test_multiple_layered_include_extend_realistic_startup_file_combination(self):
        """
        Extended test combining the extend and include keywords in a file to a complete microscope startup file
        """
        # Full startup setting defined in yaml-merger-combination-include-extend-expected-result.yaml
        with open(os.path.join(TEST_FILES_PATH,
                               "yaml-merger-combination-realistic-startup-file.yaml"), "r") as f:
            data_found = yaml.load(f, SafeLoader)

        # Compare with expected results
        self.assertEqual(self.expected_full_result, data_found)

if __name__ == '__main__':
    unittest.main()
