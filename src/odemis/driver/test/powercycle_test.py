# -*- coding: utf-8 -*-
'''
Created on 2 Apr 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import glob
import logging
from odemis.driver import powercycle
import unittest


logger = logging.getLogger().setLevel(logging.DEBUG)

SN = "0E203C44"  # put the serial number written on the component to test

# Test using the hardware
CLASS = powercycle.Relay
# KWARGS = dict(name="test", role="relay", sn=SN)
# Test using the simulator
KWARGS = dict(name="test", role="relay", port="/dev/fake")

class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_scan(self):
        devices = CLASS.scan()
        self.assertGreater(len(devices), 0)

    def test_creation(self):
        """
        Doesn't even try to do anything, just create and delete components
        """
        dev = CLASS(**KWARGS)

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()

    def test_wrong_serial(self):
        """
        Check it correctly fails if the device with the given serial number is
        not an ARM microcontroller.
        """
        # Look for a device with a serial number not starting with 37
        paths = glob.glob("/sys/bus/usb/devices/*/serial")
        for p in paths:
            try:
                f = open(p)
                snw = f.read().strip()
            except IOError:
                logging.debug("Failed to read %s, skipping device", p)
        else:
            self.skipTest("Failed to find any USB device with a serial number")

        kwargsw = dict(KWARGS)
        kwargsw["sn"] = snw
        with self.assertRaises(ValueError):
            dev = CLASS(**kwargsw)

class TestRelay(unittest.TestCase):
    """
    Tests which need a component ready
    """

    def setUp(self):
        self.dev = CLASS(**KWARGS)

    def tearDown(self):
        self.dev.terminate()

    def test_relay_va(self):
        # Test contact
        contact = False
        self.dev.contact.value = contact
        self.assertEqual(self.dev.contact.value, contact)
        contact = True
        self.dev.contact.value = contact
        self.assertEqual(self.dev.contact.value, contact)

if __name__ == "__main__":
    unittest.main()
