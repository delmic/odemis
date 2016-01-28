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
from __future__ import division

import logging
from odemis.driver import omicronxx
import os
import time
import unittest
from unittest.case import skip
from odemis.driver.omicronxx import HubxX

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing


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


class TestGenericxX(object):
    def tearDown(self):
        self.dev.terminate()

    def test_simple(self):
        # should start off
        self.assertEqual(self.dev.power.value, 0)

        # turn on first source to 10%
        self.dev.power.value = self.dev.power.range[1]
        em = self.dev.emissions.value
        em[0] = 0.1
        self.dev.emissions.value = em
        self.assertGreater(self.dev.emissions.value[0], 0)

    def test_cycle(self):
        """
        Test each emission source for 2 seconds at maximum intensity and then 1s
        at 10%.
        """
        em = self.dev.emissions.value
        em = [0 for v in em]
        self.dev.power.value = self.dev.power.range[1]

        # can fully checked only by looking what the hardware is doing
        print "Starting emission source cycle..."
        for i in range(len(em)):
            print "Turning on wavelength %g" % self.dev.spectra.value[i][2]
            em[i] = 0.1
            self.dev.emissions.value = em
            time.sleep(5)
            self.assertEqual(self.dev.emissions.value, em)
            em[i] = 0
            self.dev.emissions.value = em
            self.assertEqual(self.dev.emissions.value, em)


class TestMultixX(TestGenericxX, unittest.TestCase):
    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No simulator for MultixX")
        self.dev = omicronxx.MultixX("test", "light", MXXPORTS)


class TestHubxX(TestGenericxX, unittest.TestCase):
    def setUp(self):
        self.dev = omicronxx.HubxX("test", "light", HUBPORT)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
