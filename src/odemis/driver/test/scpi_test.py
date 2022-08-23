#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 21 Apr 2016

Copyright © 2016 Éric Piel, Delmic

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
from odemis import model
from odemis.driver import scpi
import os
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG_K6485 = {"name": "Keithley 6485", "role": "pc-detector",
                "port": "/dev/ttyUSB*",
                "baudrate": 9600,
                "idn": "KEITHLEY.+MODEL 6485.+4126216"
}

if TEST_NOHW:
   CONFIG_K6485["port"] = "/dev/fake"


class TestK6485(unittest.TestCase):
    """
    Tests which can share one Keitheley 6485 device
    """
    @classmethod
    def setUpClass(cls):
        cls.dev = scpi.Ammeter(**CONFIG_K6485)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()
        time.sleep(1)

    def test_acquire_get(self):
        dt = self.dev.dwellTime.range[0]
        self.dev.dwellTime.value = dt
        df = self.dev.data
        for i in range(4):
            data = df.get()
            self.assertEqual(data.shape, (1,))
            self.assertEqual(data.metadata[model.MD_DWELL_TIME], dt)
            self.dev.dwellTime.value = self.dev.dwellTime.clip(dt * 10)
            dt = self.dev.dwellTime.value

    def test_acquire_sub(self):
        """Test the subscription"""
        dt = 0.1  # s
        df = self.dev.data
        self.dev.dwellTime.value = dt

        self._cnt = 0
        self._lastdata = None
        df.subscribe(self._on_det)
        time.sleep(3)
        df.unsubscribe(self._on_det)
        self.assertGreater(self._cnt, 3)
        self.assertEqual(self._lastdata.shape, (1,))

    def _on_det(self, df, data):
        self._cnt += 1
        self._lastdata = data


if __name__ == "__main__":
    unittest.main()
