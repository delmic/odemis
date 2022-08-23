# -*- coding: utf-8 -*-
'''
Created on 24 Apr 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import glob
import logging
from odemis.driver import tlaptmf
import os
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

SN = "37848720"  # put the serial number written on the component to test
SN_SIM = "37000001"
CLASS = tlaptmf.MFF

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

KWARGS_SIM = dict(name="test", role="switch", port="/dev/fake", axis="r",
                  inverted=["r"], positions=[[0, "off"], [1, "on"]])
KWARGS = dict(name="test", role="switch", sn=SN, axis="r", inverted=["r"])
if TEST_NOHW:
    KWARGS = KWARGS_SIM

# @skip("simple")
class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_scan(self):
        devices = CLASS.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print("opening", name)
            dev = CLASS(name, "switch", **kwargs)
            self.assertTrue(dev.selfTest(), "self test failed.")
            dev.terminate()

    def test_creation(self):
        """
        Doesn't even try to do anything, just create and delete components
        """
        dev = CLASS(**KWARGS)

        self.assertGreater(len(dev.axes["r"].choices), 0)

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()

    def test_wrong_serial(self):
        """
        Check it correctly fails if the device with the given serial number is
        not a MFF.
        """
        # Look for a device with a serial number not starting with 37
        paths = glob.glob("/sys/bus/usb/devices/*/serial")
        for p in paths:
            try:
                f = open(p)
                snw = f.read().strip()
            except IOError:
                logging.debug("Failed to read %s, skipping device", p)
            if not snw.startswith("37"):
                break
        else:
            self.skipTest("Failed to find any USB device with a serial number")

        kwargsw = dict(KWARGS)
        kwargsw["sn"] = snw
        with self.assertRaises(ValueError):
            dev = CLASS(**kwargsw)

    def test_fake(self):
        """
        Just makes sure we don't (completely) break FakeMFF after an update
        """
        dev = CLASS(**KWARGS_SIM)

        self.assertGreater(len(dev.axes["r"].choices), 0)
        for p in dev.axes["r"].choices:
            dev.moveAbs({"r": p})

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()

class TestMFF(unittest.TestCase):
    """
    Tests which need a component ready
    """

    def setUp(self):
        self.dev = CLASS(**KWARGS)
        self.orig_pos = dict(self.dev.position.value)

    def tearDown(self):
        # move back to the original position
        f = self.dev.moveAbs(self.orig_pos)
        f.result()
        self.dev.terminate()

    def test_simple(self):
        cur_pos = self.dev.position.value
        axis = list(cur_pos.keys())[0]
        apos = cur_pos[axis]
        logging.info("Device is currently at position %s", apos)

        # don't change position
        f = self.dev.moveAbs({axis: apos})
        f.result()

        self.assertEqual(self.dev.position.value[axis], apos)

        # try every other position
        axis_def = self.dev.axes[axis]
        for p in axis_def.choices:
            if p != apos:
                logging.info("Testing move to position %s", p)
                f = self.dev.moveAbs({axis: p})
                f.result()
                self.assertEqual(self.dev.position.value[axis], p)

        if self.dev.position.value[axis] == apos:
            self.fail("Failed to find a position different from %d" % apos)


if __name__ == "__main__":
    unittest.main()
