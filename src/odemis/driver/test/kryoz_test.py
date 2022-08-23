#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on Apr 17, 2019

@author: Anders Muskens
Copyright © 2019 Anders Muskens, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import os

import unittest
from odemis.driver import kryoz

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing


class TestKryozCooler(unittest.TestCase):
    """
    Tests cases for the Kryoz Cooler driver
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            # Until we have a simulator...
            raise unittest.SkipTest("No simulator available")

        cls.dev = kryoz.Cryolab("Test", "cooler", "localhost")

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def test_basic(self):
        # Test canceling referencing
        sensors = self.dev.getSensorValues()
        self.assertEqual(len(sensors), 6)
        status = self.dev.getStatus()
        self.assertEqual(len(status), 5)
        # set setPoint
        self.dev.disconnect()


if __name__ == "__main__":
    unittest.main()
