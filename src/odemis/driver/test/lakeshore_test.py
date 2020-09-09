#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on August 31, 2020

@author: Anders Muskens

Copyright © 2020 Anders Muskens, Delmic

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

from __future__ import division

import logging
from odemis.driver import lakeshore
import odemis.model as model
from odemis.util import test
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG = {"name": "Lakeshore Test",
          "role": "temperature-controller",
          "port": "/dev/fake",
          "sensor_input": 'b',
          "output_channel": 2,
}

# arguments used for the creation of basic components
if TEST_NOHW:
    CONFIG["port"] = "/dev/fake"


class TestLakeshore(unittest.TestCase):
    """
    Tests cases for the Lakeshore 310 Temperature PID
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = lakeshore.Lakeshore(**CONFIG)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()  # free up socket.

    def test_simple(self):
        """
        Test some simple functions
        """

        self.assertNotEqual(self.dev.temperature.value, 0)

        temps = range(1, 25)
        for temp in temps:
            self.dev.targetTemperature.value = temp
            self.assertEqual(self.dev.targetTemperature.value, temp)

        # set heating range
        self.dev.heating.value = 0
        self.assertEqual(self.dev.heating.value, 0)
        self.dev.heating.value = 1
        self.assertEqual(self.dev.heating.value, 1)
        self.dev.heating.value = 2
        self.assertEqual(self.dev.heating.value, 2)
        with self.assertRaises(IndexError):
            self.dev.heating.value = 5

    def test_temperature_poll(self):
        # Test that the temperature polls.
        # Designed to work with the simulator - the temperature will not be exactly the same
        temp1 = self.dev.temperature.value
        time.sleep(3)
        temp2 = self.dev.temperature.value
        self.assertNotEqual(temp1, temp2)


if __name__ == "__main__":
    unittest.main()

