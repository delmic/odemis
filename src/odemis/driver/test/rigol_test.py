#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 22 Feb 2018

Copyright © 2018 Anders Muskens, Delmic

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
from odemis.driver import rigol
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG_DG1000Z = {"name": "Rigol Wave Gen", "role": "pc-emitter",
                  "host": "192.168.1.191",
                  "port": 5555,
                  "channel": 1,
                  "limits": [0, 5.0]
}

# arguments used for the creation of basic components
if TEST_NOHW:
    CONFIG_DG1000Z["host"] = "fake"


class TestWaveGenerator(unittest.TestCase):
    """
    Tests
    """
    @classmethod
    def setUpClass(cls):
        cls.wg = rigol.WaveGenerator(**CONFIG_DG1000Z)    # specify IP of actual device

    @classmethod
    def tearDownClass(cls):
        cls.wg.terminate()  # free up socket.

    def test_power(self):
        self.wg.power.value = 1
        self.assertEqual(self.wg.power.value, 1)
        time.sleep(0.2)
        self.wg.power.value = 0
        self.assertEqual(self.wg.power.value, 0)

    def test_period(self):
        self.wg.power.value = 0
        self.assertEqual(self.wg.power.value, 0)

        for i in range(1000, 10000, 1000):    # specify range of frequencies to increment
            self.wg.period.value = 1 / i
            self.assertEqual(self.wg.period.value, 1 / i)
            self.wg.power.value = 1
            time.sleep(0.1)
            self.wg.power.value = 0

    def test_period_range(self):
        # test limits of range
        for p in self.wg.period.range:
            self.wg.period.value = p
            self.assertEqual(self.wg.period.value, p)

        # test boundary cases
        with self.assertRaises(IndexError):
            self.wg.period.value = 0


if __name__ == "__main__":
    unittest.main()
