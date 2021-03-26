#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2 Jul 2019

@author: Thera Pals

Copyright © 2012-2019 Thera Pals, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
from __future__ import division

import logging
from odemis import model
from odemis.driver.focustracker import FocusTrackerCO
from odemis.model import NotSettableError
import os
import time
import unittest


logging.basicConfig(level=logging.DEBUG)

TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

KWARGS = {"name": "Focus Tracker",
          "role": "focus-position",
          "node_idx": 2,
          "channel": 'can0',
          # "inverted": ["z"]
}
if TEST_NOHW:
    KWARGS["channel"] = "fake"


class TestFocusTrackerCO(unittest.TestCase):
    """Test the focus tracker functionality with the CANopen network."""

    @classmethod
    def setUpClass(cls):
        cls.focus_tracker = FocusTrackerCO(**KWARGS)

    @classmethod
    def tearDownClass(cls):
        cls.focus_tracker.terminate()

    def test_connection(self):
        """Test that connection to the network is unsuccessful when disconnected."""
        if not TEST_NOHW:
            # If the node_idx is not found, maybe the device is disconnected => raise HwError
            with self.assertRaises(model.HwError):
                FocusTrackerCO(name="Focus Tracker", role="focus_tracker", channel=KWARGS['channel'],
                               node_idx=0x12)

        # If channel not fount, maybe the CAN adapter is disconnected => raise HwError
        with self.assertRaises(model.HwError):
            FocusTrackerCO(name="Focus Tracker", role="focus_tracker", channel='not a channel',
                           node_idx=KWARGS['node_idx'])

    def test_position(self):
        """Verify that the current position can be read and not written."""
        pos = self.focus_tracker.position.value["z"]
        self.assertIsInstance(pos, float)
        self.assertGreaterEqual(pos, 0)
        # It should be in m. The distance is typically < 1mm. So let's say always < 10cm
        self.assertLess(pos, 0.1)

        with self.assertRaises(NotSettableError):
            self.focus_tracker.position.value = {"z": 10}

        # After a while the position should change (at least, just due to noise):
        positions = [pos]
        for i in range(10):
            time.sleep(0.1)
            positions.append(self.focus_tracker.position.value["z"])

        # At least 2 positions different
        self.assertGreaterEqual(len(set(positions)), 2, "All positions reported are %s" % (pos,))

    def test_position_sub(self):
        """Verify that the position is automatically updated from the hardware"""
        self._positions = []
        self.focus_tracker.position.subscribe(self.on_position)

        time.sleep(2)  # Wait for the positions to come in

        # Check we did receive some positions, and they are not identical
        self.focus_tracker.position.unsubscribe(self.on_position)
        lpos = len(self._positions)
        self.assertGreater(lpos, 4)  # at least 2Hz (normally, it's 50Hz)
        self.assertGreaterEqual(len(set(self._positions)), 2, "All positions reported are %s" % (self._positions[0],))

        # Check we didn't receive more positions after unsubscribing
        time.sleep(1)
        self.assertEqual(lpos, len(self._positions), "Positions got updated after unsubscribing")

    def on_position(self, pos):
        self._positions.append(pos["z"])


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
