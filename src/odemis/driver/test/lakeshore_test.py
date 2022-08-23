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

import logging
from odemis.driver import lakeshore
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG = {"name": "Lakeshore Test",
          "role": "temperature-controller",
          "port": "/dev/ttyUSB0",
          "sensor_input": "B",
          "output_channel": 2,
}

# arguments used for the creation of basic components
if TEST_NOHW:
    CONFIG["port"] = "/dev/fake"


class TestLakeshore(unittest.TestCase):
    """
    Tests cases for the Lakeshore 335 Temperature PID
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
        return
        logging.debug("Test target temperature setting")

        self.assertNotEqual(self.dev.temperature.value, 0)
        old_val = self.dev.targetTemperature.value

        temps = (-94.20, -90, 0)  # in C
        for temp in temps:
            self.dev.targetTemperature.value = temp
            self.assertAlmostEqual(self.dev.targetTemperature.value, temp)
            time.sleep(1.0)

        self.dev.targetTemperature.value = old_val

        logging.debug("Test heating range")

        # set heating range
        old_val = self.dev.heating.value
        self.dev.heating.value = 0
        self.assertEqual(self.dev.heating.value, 0)
        self.dev.heating.value = 1
        self.assertEqual(self.dev.heating.value, 1)
        self.dev.heating.value = 2
        self.assertEqual(self.dev.heating.value, 2)
        with self.assertRaises(IndexError):
            self.dev.heating.value = 5
        self.dev.heating.value = old_val

    def test_temperature_poll(self):
        return
        # Test that the temperature polls.
        # Designed to work with the simulator - the temperature will not be exactly the same
        logging.debug("Test stasis temperature polling")
        temp1 = self.dev.temperature.value
        time.sleep(3)
        temp2 = self.dev.temperature.value
        # self.assertNotEqual(temp1, temp2)

    def test_heating_test(self):
        """
        Test heating
        """
        old = self.dev.targetTemperature.value
        # set a target temperature 1 degree higher and start heating
        self.dev.targetTemperature.value = self.dev.temperature.value + 1
        self.dev.heating.value = 3  # fast heating
        # wait to heat up and then check if the temperature rose by a degree
        time.sleep(30)
        self.assertAlmostEqual(self.dev.temperature.value, self.dev.targetTemperature.value, delta=0.5)

        self.dev.targetTemperature.value = old
        self.dev.heating.value = 0  # stop heating


if __name__ == "__main__":
    unittest.main()
