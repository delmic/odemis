#!/usr/bin/env python
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
from __future__ import division

from odemis.driver import lle
from unittest.case import skipIf
import logging
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

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
            print "opening %s" % (name,)
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
        self.assertEqual(dev.power.value, 0)

        # turn on first source to 50%
        dev.power.value = dev.power.range[1]
        em = dev.emissions.value
        em[0] = 0.5
        dev.emissions.value = em
        self.assertGreater(dev.emissions.value[0], 0)

        dev.terminate()

    def test_fake(self):
        """Ensures that FakeLLE also keeps working"""
        dev = lle.FakeLLE(**KWARGS)
        self.assertTrue(dev.selfTest(), "Device self-test failed.")

        # should start off
        self.assertEqual(dev.power.value, 0)

        # turn on green (1) to 50%
        dev.power.value = dev.power.range[1]
        em = dev.emissions.value
        em[1] = 0.5
        dev.emissions.value = em
        self.assertGreater(dev.emissions.value[1], 0)

        dev.terminate()


class TestLLE(unittest.TestCase):

    def setUp(self):
        self.dev = CLASS(**KWARGS)

    def tearDown(self):
        self.dev.terminate()

    def test_simple(self):
        self.assertTrue(self.dev.selfTest(), "Device self-test failed.")

        # should start off
        self.assertEqual(self.dev.power.value, 0)

        # turn on first source to 50%
        self.dev.power.value = self.dev.power.range[1]
        em = self.dev.emissions.value
        em[0] = 0.5
        self.dev.emissions.value = em
        self.assertGreater(self.dev.emissions.value[0], 0)

    def test_multi(self):
        """simultaneous source activation
        Test the very specific behaviour of LLE which can not activate Yellow/Green
        simultaneously as other sources.
        """
        # should start off
        self.assertEqual(self.dev.power.value, 0)

        # Easiest way is to depend on internal attribute, but we could also check
        # the peak wavelength of .spectra.value and find out which id is which colour

        # turn on 3 sources at the same time (which are possible)
        self.dev.power.value = self.dev.power.range[1]
        em = [0] * len(self.dev.emissions.value)
        for i in self.dev._rcubt[0:3]:
            em[i] = 0.1 + 0.1 * i
        self.dev.emissions.value = em
        self.assertEqual(self.dev.emissions.value, em)

        # turn on yellow source very strong => all the other ones should be shut
        yellow_i = self.dev._source_id.index(4)
        em[yellow_i] = 1
        self.dev.emissions.value = em
        exp_em = [0] * len(em)
        exp_em[yellow_i] = 1
        self.assertEqual(self.dev.emissions.value, exp_em)

        # turn on all the sources => at least one should be on
        self.dev.emissions.value = [1 for e in em]
        self.assertTrue(any(self.dev.emissions.value))

    def test_cycle(self):
        """
        Test each emission source for 2 seconds at maximum intensity and then 1s
        at 30%.
        """
        em = [0] * len(self.dev.emissions.value)
        self.dev.power.value = self.dev.power.range[1]

        # can fully checked only by looking what the hardware is doing
        print "Starting emission source cycle..."
        for i in range(len(em)):
            print "Turning on wavelength %g" % self.dev.spectra.value[i][2]
            em[i] = 1
            self.dev.emissions.value = em
            time.sleep(1)
            self.assertEqual(self.dev.emissions.value, em)
            em[i] = 0.3
            self.dev.emissions.value = em
            time.sleep(1)
            self.assertEqual(self.dev.emissions.value, em)
            # value so small that it's == 0 for the hardware
            self.dev.emissions.value[i] = 1e-8
            em[i] = 0
            self.assertEqual(self.dev.emissions.value, em)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
