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
import numpy
import os
import unittest

from canopen import SdoCommunicationError

from odemis import model
from odemis.driver.focustracker import FocusTrackerCO
from odemis.model import NotSettableError

logging.basicConfig(level=logging.DEBUG)

TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

if TEST_NOHW:
    KWARGS = {"name": "Focus Tracker", "role": "focus-tracker", "node_id": 0x10, "channel": 'fake',
              "datasheet": "../FocusTracker.eds"}
else:
    KWARGS = {"name": "Focus Tracker", "role": "focus-tracker", "node_id": 0x10, "channel": 'can0',
              "datasheet": "../FocusTracker.eds"}


class TestFocusTrackerCO(unittest.TestCase):
    """Test the focus tracker functionality with the CANopen network."""

    def setUp(self):
        self.focus_tracker = FocusTrackerCO(**KWARGS)
        self.kwargs = KWARGS

    def test_connection(self):
        """Test that connection to the network is successful when connected, and unsuccessful when disconnected."""
        if not TEST_NOHW:
            # If the network scanner sees a node at index 0x10, the focus tracker is connected.
            self.assertTrue(0x10 in self.focus_tracker.network.scanner.nodes)
            # When the node_idx is incorrect an empty node is added to the network, this then results in an
            # SDOCommunicationError when we try to send a message to an object in the object dictionary.
            with self.assertRaises(SdoCommunicationError):
                FocusTrackerCO(name="Focus Tracker", role="focus_tracker", channel=self.kwargs['channel'],
                               node_idx=0x12)
        # If a fake channel name is entered, an IOError should be raised.
        with self.assertRaises(IOError):
            FocusTrackerCO(name="Focus Tracker", role="focus_tracker", channel='not a channel',
                           node_idx=self.kwargs['node_idx'])

    def test_output(self):
        """Verify that the current position can be read and not written."""
        self.assertIsInstance(self.focus_tracker.ccd_output.value, int)
        with self.assertRaises(NotSettableError):
            self.focus_tracker.ccd_output.value = 10



if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
