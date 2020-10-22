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
    KWARGS = {"name": "Focus Tracker", "role": "focus-tracker", "node_idx": 0x10, "channel": 'fake'}
else:
    KWARGS = {"name": "Focus Tracker", "role": "focus-tracker", "node_idx": 0x10, "channel": 'can0'}


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

    def test_position(self):
        """Verify that the current position can be read and not written."""
        self.assertIsInstance(self.focus_tracker.position.value, float)
        with self.assertRaises(NotSettableError):
            self.focus_tracker.position.value = 10

    def test_target_pos(self):
        """Verify that the target position can be read and not written."""
        self.assertIsInstance(self.focus_tracker.targetPosition.value, float)
        self.focus_tracker.targetPosition.value = 10e-6
        self.assertAlmostEqual(self.focus_tracker.targetPosition.value, 10e-6)
        with self.assertRaises(IndexError):
            self.focus_tracker.targetPosition.value = -10e-6

    def test_switch_tracking(self):
        """Verify that the focus tracker can switch between tracking and untracking."""
        self.focus_tracker.tracking.value = True
        self.assertTrue(self.focus_tracker.tracking.value)
        self.focus_tracker.tracking.value = False
        self.assertFalse(self.focus_tracker.tracking.value)

    def test_pid_gains(self):
        """Test that PID gains are set when within range and remain unchanged when new gain is out of range."""
        # Test setting all 3 values at the same time.
        new_p = 0
        new_i = 15.8
        new_d = 3
        self.focus_tracker.updateMetadata({model.MD_GAIN_P: new_p, model.MD_GAIN_I: new_i, model.MD_GAIN_D: new_d})
        set_p = self.focus_tracker._get_proportional()
        self.assertEqual(numpy.floor(new_p), numpy.floor(set_p))
        set_i = self.focus_tracker._get_integral()
        self.assertEqual(numpy.floor(new_i), numpy.floor(set_i))
        set_d = self.focus_tracker._get_derivative()
        self.assertEqual(numpy.floor(new_d), numpy.floor(set_d))
        # Test setting a single value.
        self.focus_tracker.updateMetadata({model.MD_GAIN_I: 10})
        self.assertEqual(10, self.focus_tracker._get_integral())
        # check that a ValueError is raised when trying to set a negative value.
        with self.assertRaises(ValueError):
            self.focus_tracker.updateMetadata({model.MD_GAIN_P: -10})


if __name__ == "__main__":
    # import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
