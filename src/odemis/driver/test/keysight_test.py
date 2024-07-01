#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on May 05, 2024

@author: Canberk Akin

Copyright © 2024 Canberk Akin, Delmic

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

from odemis import util
from odemis.driver import keysight
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to HW testing

# arguments used for the creation of basic components to test via IP connection
CONFIG_AWG = {
    "name": "Keysight AWG",
    "role": "blanker",
    "address": "192.168.5.11",
    "channel": 1,
    "tracking": {2: "INV"},
    "limits": [[-4.0, 4.0], [5.0, -5.0]],
}

# arguments used for the creation of basic components
if TEST_NOHW:
    CONFIG_AWG["address"] = "fake"

class TestKeysight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dev = keysight.TrueForm(**CONFIG_AWG)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def setUp(self):
        self.dev.period.value = 25e-9
        self.dev.power.value = True

    def test_duty_cycle(self):
        """
        Test duty cycle
        """
        old_val = self.dev.dutyCycle.value
        self.dev.dutyCycle.value = 0.68
        self.assertEqual(self.dev.dutyCycle.value, 0.68)
        self.dev.dutyCycle.value = 0.32
        self.assertEqual(self.dev.dutyCycle.value, 0.32)

        with self.assertRaises(IndexError):
            self.dev.dutyCycle.value = 0.95
        with self.assertRaises(IndexError):
            self.dev.dutyCycle.value = 0.15

        self.dev.dutyCycle.value = old_val

    def test_period(self):
        """
        Test period setting which is 1/frequency
        """
        logging.debug("Test period(frequency) setting")

        self.assertNotEqual(self.dev.period.value, 0)

        old_val = self.dev.period.value
        self.dev.period.value = 1e-4
        self.assertEqual(self.dev.period.value, 1e-4)
        self.dev.period.value = 25e-7
        self.assertEqual(self.dev.period.value, 25e-7)

        with self.assertRaises(IndexError):
            self.dev.period.value = 1e7
        with self.assertRaises(IndexError):
            self.dev.period.value = 1e-9

        self.dev.period.value = old_val

    def test_delay(self):
        """
        Test delay
        """
        self.dev.period.value = 1e-4
        self.dev.delay.value = 1e-5
        self.assertEqual(self.dev.delay.value, 1e-5)
        self.dev.delay.value = 1e-6
        self.assertEqual(self.dev.delay.value, 1e-6)

        with self.assertRaises(IndexError):
            self.dev.delay.value = self.dev.delay.range[1] + 1
        with self.assertRaises(IndexError):
            self.dev.delay.value = -1

        with self.assertRaises(ValueError):
            self.dev.delay.value = self.dev.period.value + 1e-6

    def test_power(self):
        """
        Test power
        """
        self.dev.period.value = 25e-9
        self.dev.power.value = False
        self.assertEqual(self.dev.power.value, False)
        time.sleep(0.01)
        self.dev.power.value = True
        self.assertEqual(self.dev.power.value, True)


if __name__ == "__main__":
    unittest.main()
