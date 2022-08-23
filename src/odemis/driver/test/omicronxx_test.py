# -*- coding: utf-8 -*-
'''
Created on 6 Nov 2013

@author: Éric Piel

Copyright © 2013, 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
from odemis.driver import omicronxx
import os
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

if TEST_NOHW:
    MXXPORTS = "/dev/fakeone"  # TODO: no simulator
    HUBPORT = "/dev/fakehub"
elif os.name == "nt":
    MXXPORTS = "COM*"
    HUBPORT = "COM*"
else:
    MXXPORTS = "/dev/ttyFTDI*" # "/dev/ttyUSB*"
    HUBPORT = "/dev/ttyFTDI*" # "/dev/ttyUSB*"


class TestStatic(unittest.TestCase):

    def test_scan_hub(self):
        devices = omicronxx.HubxX.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            logging.debug("opening %s", name)
            dev = omicronxx.HubxX(name, "test", **kwargs)
            dev.terminate()

    def test_scan_multi(self):
        devices = omicronxx.MultixX.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            logging.debug("opening %s", name)
            dev = omicronxx.MultixX(name, "test", **kwargs)
            dev.terminate()


class BaseGenericxX:
    def tearDown(self):
        self.dev.terminate()

    def test_simple(self):
        # should start off
        self.assertEqual(self.dev.power.value, [0.0, 0.0])

        # turn on first source to 10%
        self.dev.power.value[0] = self.dev.power.range[1][0] * 0.1
        self.assertGreater(self.dev.power.value[0], 0)

        logging.debug("Found hardware %s", self.dev.hwVersion)

    def test_cycle(self):
        """
        Test each power source for 2 seconds at maximum intensity and then 1s
        at 10%.
        """
        self.dev.power.value = self.dev.power.range[0]

        # can fully checked only by looking what the hardware is doing
        print("Starting power source cycle...")
        for i in range(len(self.dev.power.value)):
            print("Turning on wavelength %g" % self.dev.spectra.value[i][2])
            self.dev.power.value[i] = self.dev.power.range[1][i] * 0.1
            self.assertEqual(self.dev.power.value[i], self.dev.power.range[1][i] * 0.1)
            time.sleep(5)

            self.dev.power.value[i] = 0.0
            self.assertEqual(self.dev.power.value[i], 0.0)


class TestMultixX(BaseGenericxX, unittest.TestCase):
    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No simulator for MultixX")
        self.dev = omicronxx.MultixX("test", "light", MXXPORTS)


class TestHubxX(BaseGenericxX, unittest.TestCase):
    def setUp(self):
        self.dev = omicronxx.HubxX("test", "light", HUBPORT)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
