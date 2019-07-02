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
import logging
import numpy
import os
import unittest

from canopen import SdoCommunicationError

import odemis
from odemis import model
from odemis.driver.focustracker import FocusTrackerCOSimulator, FocusTrackerCO
from odemis.model import NotSettableError
from odemis.util import test

logging.basicConfig(level=logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
FAKE_FOCUS_TRACKER_CONFIG = CONFIG_PATH + "sim/focustracker-sim.odm.yaml"
FOCUS_TRACKER_CONFIG = CONFIG_PATH + "hwtest/focustracker.odm.yaml"


class TestFocusTrackerCOSim(unittest.TestCase):
    """Test the focus tracker functionality."""
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(FAKE_FOCUS_TRACKER_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.fake_focus_tracker = model.getComponent(role="focus-tracker")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def test_connection(self):
        """Test an error is raised when trying to connect to can0 when the focus tracker is not connected."""
        with self.assertRaises(IOError):
            # should raise an error, because the focus tracker is not actually connected.
            FocusTrackerCOSimulator(name="Focus Tracker", role="focus_tracker", channel='can0')

    def test_object_dictionary(self):
        """Verify that all relevant objects are in the object dictionary."""
        object_names = ["AI Input PV", "CO Set Point W", "CO Proportional Band Xp1",
                        "CO Integral Action Time Tn1", "CO Derivative Action Time Tv1"]
        object_dictionary = self.fake_focus_tracker.get_object_dictionary()
        self.assertTrue(all(n in object_dictionary.names for n in object_names))

    def test_current_pos(self):
        """Verify that the current position can be read and not written."""
        self.assertAlmostEqual(self.fake_focus_tracker.current_pos.value, 1e-5)

    def test_target_pos(self):
        """Verify that the target position can be read and not written."""
        self.assertAlmostEqual(self.fake_focus_tracker.target_pos.value, 2e-5)

    def test_switch_tracking(self):
        """Verify that the focus tracker can switch between tracking and untracking."""
        self.fake_focus_tracker.tracking.value = True
        self.assertTrue(self.fake_focus_tracker.tracking.value)
        self.assertTrue(self.fake_focus_tracker.get_tracking())
        self.fake_focus_tracker.tracking.value = False
        self.assertFalse(self.fake_focus_tracker.tracking.value)
        self.assertFalse(self.fake_focus_tracker.get_tracking())

    def test_pid_gains(self):
        """Test that PID gains are set when within range and remain unchanged when new gain is out of range."""
        new_p = 0.5
        new_i = 15
        new_d = 3
        self.fake_focus_tracker.updateMetadata({model.MD_GAIN_P: new_p, model.MD_GAIN_I: new_i, model.MD_GAIN_D: new_d})
        set_p = self.fake_focus_tracker.get_proportional()
        self.assertEqual(new_p, set_p)
        set_i = self.fake_focus_tracker.get_integral()
        self.assertEqual(new_i, set_i)
        set_d = self.fake_focus_tracker.get_derivative()
        self.assertEqual(new_d, set_d)


class TestFocusTrackerCO(unittest.TestCase):
    """Test the focus tracker functionality with the CANopen network."""
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(FOCUS_TRACKER_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.focus_tracker = model.getComponent(role="focus-tracker")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def test_connection(self):
        """Test that connection to the network is successful when connected, and unsuccessful when disconnected."""
        # If the network scanner does not see a node at index 0x10, the focus tracker is not connected.
        self.assertTrue(0x10 in self.focus_tracker.get_available_nodes())
        # If a fake channel name is entered, an IOError should be raised.
        with self.assertRaises(IOError):
            FocusTrackerCO(name="Focus Tracker", role="focus_tracker", channel='not a channel', node_idx=0x10)
        # When the node_idx is incorrect an empty node is added to the network, this then results in an
        # SDOCommunicationError when we try to send a message to an object in the object dictionary.
        with self.assertRaises(SdoCommunicationError):
            FocusTrackerCO(name="Focus Tracker", role="focus_tracker", channel='can0', node_idx=0x12)

    def test_object_dictionary(self):
        """Verify that all relevant objects are in the object dictionary."""
        object_names = ["AI Input PV", "CO Set Point W", "CO Proportional Band Xp1",
                        "CO Integral Action Time Tn1", "CO Derivative Action Time Tv1"]
        object_dictionary = self.focus_tracker.get_object_dictionary()
        self.assertTrue(all(n in object_dictionary.names for n in object_names))

    def test_current_pos(self):
        """Verify that the current position can be read and not written."""
        self.assertIsInstance(self.focus_tracker.current_pos.value, float)
        with self.assertRaises(NotSettableError):
            self.focus_tracker.current_pos.value = 10

    def test_target_pos(self):
        """Verify that the target position can be read and not written."""
        self.assertIsInstance(self.focus_tracker.target_pos.value, float)
        with self.assertRaises(NotSettableError):
            self.focus_tracker.target_pos.value = 10

    def test_switch_tracking(self):
        """Verify that the focus tracker can switch between tracking and untracking."""
        self.focus_tracker.tracking.value = True
        self.assertTrue(self.focus_tracker.tracking.value)
        self.assertTrue(self.focus_tracker.get_tracking())
        self.focus_tracker.tracking.value = False
        self.assertFalse(self.focus_tracker.tracking.value)
        self.assertFalse(self.focus_tracker.get_tracking())

    def test_pid_gains(self):
        """Test that PID gains are set when within range and remain unchanged when new gain is out of range."""
        new_p = 0
        new_i = 15.8
        new_d = 3
        self.focus_tracker.updateMetadata({model.MD_GAIN_P: new_p, model.MD_GAIN_I: new_i, model.MD_GAIN_D: new_d})
        set_p = self.focus_tracker.get_proportional()
        self.assertEqual(numpy.floor(new_p), set_p)
        set_i = self.focus_tracker.get_integral()
        self.assertEqual(numpy.floor(new_i), set_i)
        set_d = self.focus_tracker.get_derivative()
        self.assertEqual(numpy.floor(new_d), set_d)
