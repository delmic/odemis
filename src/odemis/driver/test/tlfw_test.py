# -*- coding: utf-8 -*-
'''
Created on 15 Jan 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from collections.abc import Iterable
import logging
from odemis.driver import tlfw
import os
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyFTDI*" #"/dev/ttyUSB0"

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

if TEST_NOHW:
    CLASS = tlfw.FakeFW102c
else:
    CLASS = tlfw.FW102c

KWARGS = {"name": "test", "role": "filter", "port": PORT,
          "bands": {1: (100e-9, 150e-9),
                   2: (200e-9, 250e-9),
                   3: (100e-9, 300e-9),
                   4: [(200e-9, 250e-9), (500e-9, 600e-9)],
                   #5: (600e-9, 2500e-9),
                   6: (600e-9, 2500e-9),
                   # one filter not present
                   }
          }


#@skip("simple")
class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_scan(self):
        devices = CLASS.scan()
        self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print("opening ", name)
            sem = CLASS(name, "filter", **kwargs)
            self.assertTrue(sem.selfTest(), "self test failed.")

    def test_creation(self):
        """
        Doesn't even try to do anything, just create and delete components
        """
        dev = CLASS(**KWARGS)

        self.assertGreater(len(dev.axes["band"].choices), 0)

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()

    def test_fake(self):
        """
        Just makes sure we don't (completely) break FakeFW102c after an update
        """
        dev = tlfw.FakeFW102c(**KWARGS)

        self.assertGreater(len(dev.axes["band"].choices), 0)
        for p, b in dev.axes["band"].choices.items():
            self.assertIsInstance(b, Iterable)
            dev.moveAbs({"band": p})

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()


class TestFW102c(unittest.TestCase):
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
        cur_pos = self.dev.position.value["band"]

        # don't change position
        f = self.dev.moveAbs({"band": cur_pos})
        f.result()

        self.assertEqual(self.dev.position.value["band"], cur_pos)

        # find a different position
        bands = self.dev.axes["band"]
        for p in bands.choices:
            if p != cur_pos:
                new_pos = p
                break
        else:
            self.fail("Failed to find a position different from %d" % cur_pos)

        f = self.dev.moveAbs({"band": new_pos})
        f.result()
        self.assertEqual(self.dev.position.value["band"], new_pos)

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
