#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 20 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from odemis.driver import lle
from unittest.case import skipIf
import logging
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyFTDI*" #"/dev/ttyLLE"

KWARGS = {"name": "test", "role": "light", "port": PORT, "sources": lle.DEFAULT_SOURCES}
CLASS = lle.LLE
if TEST_NOHW:
    CLASS = lle.FakeLLE


class TestStatic(unittest.TestCase):
    """
    Test everything that doesn't need a actual device created
    """
    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        devices = CLASS.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print("opening %s" % (name,))
            dev = CLASS(name, "light", **kwargs)
            self.assertTrue(dev.selfTest(), "Device self-test failed.")
            dev.terminate()

    def test_few_sources(self):
        """
        same as simple, but using just two sources
        """
        kwargs = dict(KWARGS)
        sources = {"red": lle.DEFAULT_SOURCES["red"],
                   "teal": lle.DEFAULT_SOURCES["teal"]
                   }
        kwargs["sources"] = sources
        dev = CLASS(**KWARGS)

        # should start off
        self.assertEqual(dev.power.value, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        # turn on first source to 50%
        dev.power.value[0] = dev.power.range[1][0] * 0.5
        self.assertGreater(dev.power.value[0], 0)

        dev.terminate()

    def test_fake(self):
        """Ensures that FakeLLE also keeps working"""
        dev = lle.FakeLLE(**KWARGS)
        self.assertTrue(dev.selfTest(), "Device self-test failed.")

        # should start off
        self.assertEqual(dev.power.value, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        # turn on first source to 50%
        dev.power.value[1] = dev.power.range[1][1] * 0.5
        self.assertGreater(dev.power.value[1], 0)

        dev.terminate()


class TestLLE(unittest.TestCase):

    def setUp(self):
        self.dev = CLASS(**KWARGS)

    def tearDown(self):
        self.dev.terminate()

    def test_simple(self):
        self.assertTrue(self.dev.selfTest(), "Device self-test failed.")

        # should start off
        self.assertEqual(self.dev.power.value, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        # turn on first source to 50%
        self.dev.power.value[0] = self.dev.power.range[1][0] * 0.5
        self.assertGreater(self.dev.power.value[0], 0)

    def test_multi(self):
        """simultaneous source activation
        Test the very specific behaviour of LLE which can not activate Yellow/Green
        simultaneously as other sources.
        """
        # should start off
        self.assertEqual(self.dev.power.value, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        # Easiest way is to depend on internal attribute, but we could also check
        # the peak wavelength of .spectra.value and find out which id is which colour

        # turn on 3 sources at the same time (which are possible)
        pw = self.dev.power.value
        for i in self.dev._rcubt[0:3]:
            pw[i] = 0.1 + 0.01 * i
            self.dev.power.value[i] = pw[i]
        self.assertEqual(self.dev.power.value, pw)

        # turn on yellow source very strong => all the other ones should be shut
        yellow_i = self.dev._source_id.index(4)
        pw[yellow_i] = self.dev.power.range[1][yellow_i]
        self.dev.power.value = pw
        exp_em = [0.] * len(pw)
        exp_em[yellow_i] = self.dev.power.range[1][yellow_i]
        self.assertEqual(self.dev.power.value, exp_em)

        # turn on all the sources => at least one should be on
        self.dev.power.value = self.dev.power.range[1]
        self.assertTrue(any(self.dev.power.value))

    def test_cycle(self):
        """
        Test each power source for 2 seconds at maximum intensity and then 1s
        at 30%.
        """
        self.dev.power.value = self.dev.power.range[0]

        # can fully checked only by looking what the hardware is doing
        print("Starting power source cycle...")
        for i in range(len(self.dev.power.value)):
            print("Turning on wavelength %g" % self.dev.spectra.value[i][2])
            self.dev.power.value[i] = self.dev.power.range[1][i]
            time.sleep(1)
            self.assertEqual(self.dev.power.value[i], self.dev.power.range[1][i])
            self.dev.power.value[i] = self.dev.power.range[1][i] * 0.3
            self.assertEqual(self.dev.power.value[i], self.dev.power.range[1][i] * 0.3)
            time.sleep(1)
            # value so small that it's == 0 for the hardware
            self.dev.power.value[i] = self.dev.power.range[1][i] * 1e-8
            self.assertEqual(self.dev.power.value[i], 0.0)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
