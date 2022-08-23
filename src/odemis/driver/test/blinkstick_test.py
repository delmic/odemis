    # -*- coding: utf-8 -*-
'''
Created on 10 Jul 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import numpy
from odemis.driver import blinkstick
import os
import unittest
from unittest.case import skip


logger = logging.getLogger().setLevel(logging.DEBUG)

# Test using the hardware
# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CLASS = blinkstick.WhiteLed
KWARGS = dict(name="test", role="light", max_power=1.0, inversed=True)


class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_creation(self):
        """
        Doesn't even try to do anything, just create and delete components
        """
        if TEST_NOHW:
            self.skipTest("Cannot test without hardware present")
        dev = CLASS(**KWARGS)

        dev.terminate()

    def test_scan(self):
        """
        Test scanning for the device
        """
        devices = CLASS.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print("opening", name)
            d = CLASS(name, "test", **kwargs)
            d.terminate()


class TestWhiteLed(unittest.TestCase):
    """
    Tests which need a component ready
    """

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("Cannot test without hardware present")
        self.dev = CLASS(**KWARGS)

    def tearDown(self):
        self.dev.terminate()

    def test_power_va(self):
        # Set power value min and max and mean
        self.dev.power.value = self.dev.power.range[0]
        self.assertEqual(self.dev.power.value, list(self.dev.power.range[0]))

        self.dev.power.value = self.dev.power.range[1]
        self.assertEqual(self.dev.power.value, list(self.dev.power.range[1]))

        h = numpy.mean(self.dev.power.range)
        self.dev.power.value = [h]
        self.assertAlmostEqual(self.dev.power.value[0], h, delta=1 / 256)


if __name__ == "__main__":
    unittest.main()
