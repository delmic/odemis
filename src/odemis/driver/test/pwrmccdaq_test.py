#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 21 Feb 2024

@author: Stefan Sneep

Copyright © 2024 Stefan Sneep, Delmic

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
import copy
import logging
import os
import time
import unittest

from odemis import model
from odemis.driver import pwrmccdaq

logging.getLogger().setLevel(logging.DEBUG)
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CONFIG_LIGHT_DEVICE = {
                "name": "Laser Hub", "role": "light",
                "mcc_device": None,
                "ao_channels": [0, 1],
                "do_channels": [5, 6], # pins 26 & 27
                "spectra": [
                    [592.e-9, 593.e-9, 594.e-9, 595.e-9, 596.e-9],
                    [592.e-9, 593.e-9, 594.e-9, 595.e-9, 596.e-9]],
                "pwr_curve": [
                    {
                        0: 0,
                        5: 0.02,  # 20mW light emitter
                    },
                    {
                        0: 0,
                        5: 0.06,  # 60mW light emitter
                    }],
                # Simulator connects to the first 8 channels <-> last 8 channels.
                # Setting the power to 0.0 will change the DO ports 5 or 6, which will affect
                # the DI ports 13 or 14.
                # For hardware testing, connect pins 26 and 27 with 37 and 38 respectively on the board.
                "di_channels": {13: ["interlockTriggered", False], 14: ["mirrorParked", True]},
}

CONFIG_CONTROLLER_DEVICE = {
    "name": "ND Filter",
    "role": "nd-filter",
    "mcc_device": "fake",
    # DO channel 0 (Port A, bit 0) controls the filter
    "do_axes": {0: ["nd-filter", "on", "off", 0.1]},
    # Simulator mirrors Port A bit 0 -> Port B bit 0, which is DI channel 8
    "di_feedback": {"nd-filter": [8, True]},
    # DI channel 9 (Port B, bit 1) monitors the interlock.
    # Simulator mirrors DO channel 1 (Port A, bit 1) -> DI channel 9 (Port B, bit 1).
    "di_channels": {9: ["interlockTriggered", True]},
}



class TestMCCDeviceLight(unittest.TestCase):
    """
    All test cases from this class are executed with a simulated device.
    """
    def setUp(self):
        if TEST_NOHW:
            CONFIG_LIGHT_DEVICE["mcc_device"] = "fake"
        self.mcc_device = None

    def test_simple(self):
        """
        Simple test to test the power control functionality of the MCCDeviceLight class.
        """
        self._create_device()

        # power should be set on 0.0 after instantiating the component
        self.assertEqual(self.mcc_device.power.value, [0.0, 0.0])
        # if the power values are 0.0 the DO port bit values should also be 0
        pb_lst = list(map(self.mcc_device.channel_to_port, CONFIG_LIGHT_DEVICE["do_channels"]))
        l_func = lambda pb: self.mcc_device.device.DBitIn(pb[0], pb[1])
        do_port_vals = list(map(l_func, pb_lst))
        self.assertFalse(any(do_port_vals))

        # check if the VA lists are of equal length
        self.assertEqual(len(self.mcc_device.power.value), len(self.mcc_device.spectra.value))

        # turn on first source to 50%
        self.mcc_device.power.value[0] = self.mcc_device.power.range[1][0] * 0.5
        self.assertGreater(self.mcc_device.power.value[0], 0)

        # turn on second source to 90%
        self.mcc_device.power.value[1] = self.mcc_device.power.range[1][1] * 0.9
        self.assertGreater(self.mcc_device.power.value[1], 0)

        # test over and under limits with absolute values
        with self.assertRaises(IndexError):
            self.mcc_device.power.value[0] = self.mcc_device.power.range[1][0] + 0.001
            self.mcc_device.power.value[1] = self.mcc_device.power.range[0][0] - 0.001

    def test_interlock(self):
        """
        Test for the instantiation of a basic MCC device with a DI status activated.
        Checks for registered VA's, polling status thread and channel selection.
        Tests trigger of VA status by using a TTL signal from a DO to a specific DI.
        For this test to work with real HW, connect pins 26 and 27 with pins 37 and 38
        respectively on the board.
        """
        self._create_device()

        # check if the interlockTriggered VA is registered properly
        self.assertTrue(model.hasVA(self.mcc_device, "interlockTriggered"))
        self.assertTrue(model.hasVA(self.mcc_device, "mirrorParked"))

        # check if polling of the DI port bits has started properly
        self.assertTrue(self.mcc_device._status_thread.is_alive())

        # test a wrong channel num
        with self.assertRaises(ValueError):
            self.mcc_device.channel_to_port(16)

        # check if the mcc_device instance picked the simulator
        self.assertTrue(isinstance(self.mcc_device.device, pwrmccdaq.MCCDeviceSimulator))

        # first increase the power of the laser to anything above 0, this should
        # set the DO to HIGH which will in turn set the DI to the right state
        self.mcc_device.power.value[0] = self.mcc_device.power.range[1][0] * 0.1
        self.mcc_device.power.value[1] = self.mcc_device.power.range[1][1] * 0.1
        time.sleep(0.15)  # wait a little longer than the tread interval

        # check the status of the DI channels before triggering
        self.assertFalse(self.mcc_device.interlockTriggered.value)
        self.assertTrue(self.mcc_device.mirrorParked.value)

        # check the old bit status
        old_bit_status = self.mcc_device.device.DIn(0x04)

        # set the power back to zero, this should trigger both of the DI channels
        self.mcc_device.power.value = self.mcc_device.power.range[0]
        time.sleep(0.15)  # wait a little longer than the tread interval

        # check the status of the DI channels after triggering
        self.assertTrue(self.mcc_device.interlockTriggered.value)
        self.assertFalse(self.mcc_device.mirrorParked.value)

        # check if the bit status is now the new bit status
        new_bit_status = self.mcc_device.device.DIn(0x04)
        self.assertNotEqual(old_bit_status, new_bit_status)

    def test_cycle(self):
        """
        Test each emission source for 2 seconds at maximum intensity and then 1s at 30%.
        """
        self._create_device()

        self.mcc_device.power.value = list(self.mcc_device.power.range[0])

        # can be fully checked only by looking what the hardware is doing
        logging.info("Starting emission source cycle...")
        for i in range(len(self.mcc_device.power.value)):
            logging.info("Turning on wavelength %g", self.mcc_device.spectra.value[i][2])
            self.mcc_device.power.value[i] = self.mcc_device.power.range[1][i]
            time.sleep(1)
            self.assertGreater(self.mcc_device.power.value[i], 0)  # Can't check for equality due to clamping

            self.mcc_device.power.value[i] *= 0.3
            time.sleep(1)
            self.assertGreater(self.mcc_device.power.value[i], 0)

            # a value so small that it's considered equal to 0 for the hardware
            self.mcc_device.power.value[i] *= 1e-8
            time.sleep(1)
            self.assertGreater(self.mcc_device.power.value[i], 0)

    def test_incorrect_parameters(self):
        """
        Simple test for testing the creation of a MCCDeviceLight class with incorrect parameters.
        """
        MOD_CONFIG_LIGHT_DEVICE = copy.deepcopy(CONFIG_LIGHT_DEVICE)
        # too few ao_channels
        MOD_CONFIG_LIGHT_DEVICE["ao_channels"] = [1,]

        with self.assertRaises(ValueError):
            pwrmccdaq.MCCDeviceLight(**MOD_CONFIG_LIGHT_DEVICE)

        MOD_CONFIG_LIGHT_DEVICE = copy.deepcopy(CONFIG_LIGHT_DEVICE)
        # spectra with unexpected wavelength
        MOD_CONFIG_LIGHT_DEVICE["spectra"] = [[572.e-8, 593.e-9, 594.e-9, 595.e-9, 596.e-9],
                                              [592.e-9, 593.e-9, 594.e-9, 595.e-9, 596.e-9]]

        with self.assertRaises(ValueError):
            pwrmccdaq.MCCDeviceLight(**MOD_CONFIG_LIGHT_DEVICE)

        MOD_CONFIG_LIGHT_DEVICE = copy.deepcopy(CONFIG_LIGHT_DEVICE)
        # power curve with negative power
        MOD_CONFIG_LIGHT_DEVICE["pwr_curve"][1] = {0: -0.0001,  5: 0.06}

        with self.assertRaises(ValueError):
            pwrmccdaq.MCCDeviceLight(**MOD_CONFIG_LIGHT_DEVICE)

    def test_incorrect_di_list(self):
        """
        Test for the instantiation of a basic MCC device with incorrect DI list values or structure.
        Also does a small check for the poll thread to not have started when there are no status
        DI's to check.
        """
        # test for an invalid DI channel number
        MOD_CONFIG_LIGHT_DEVICE = copy.deepcopy(CONFIG_LIGHT_DEVICE)
        MOD_CONFIG_LIGHT_DEVICE["di_channels"] = {12: ["interlockTriggered", False], 99: ["testProperty", True]}
        with self.assertRaises(ValueError):
            pwrmccdaq.MCCDeviceLight(**MOD_CONFIG_LIGHT_DEVICE)

        # test for an invalid structure of the channel dict
        MOD_CONFIG_LIGHT_DEVICE = copy.deepcopy(CONFIG_LIGHT_DEVICE)
        MOD_CONFIG_LIGHT_DEVICE["di_channels"] = {11: [True, "testProperty"]}
        with self.assertRaises(TypeError):
            pwrmccdaq.MCCDeviceLight(**MOD_CONFIG_LIGHT_DEVICE)

        # check list with no channel values given
        MOD_CONFIG_LIGHT_DEVICE = copy.deepcopy(CONFIG_LIGHT_DEVICE)
        MOD_CONFIG_LIGHT_DEVICE["di_channels"] = {}
        light_device = pwrmccdaq.MCCDeviceLight(**MOD_CONFIG_LIGHT_DEVICE)

        # check if there are no extra Boolean VA's
        for va in model.getVAs(self.mcc_device):
            self.assertFalse(isinstance(va, model.BooleanVA))

        # be sure that there is no poll tread started as there are no Boolean VA's to check
        self.assertFalse(light_device._status_thread)

    def test_terminate_di_status(self):
        """
        Check if the polling thread for the status changes of the DI's is working properly.
        """
        self._create_device()

        # check if polling of the DI port bits has started
        self.assertTrue(self.mcc_device._status_thread.is_alive())
        self.assertFalse(self.mcc_device._status_thread.terminated)

        self.mcc_device.terminate()
        time.sleep(0.5)  # give the thread some time to suspend

        # check if polling of the DI port bits has suspended
        self.assertTrue(self.mcc_device._status_thread.terminated)
        self.assertFalse(self.mcc_device._status_thread.is_alive())

    def _create_device(self):
        self.mcc_device = pwrmccdaq.MCCDeviceLight(**CONFIG_LIGHT_DEVICE)
        time.sleep(1)  # wait long enough until everything is set in the MCCDeviceDIStatus thread

    def tearDown(self):
        if self.mcc_device:
            self.mcc_device.terminate()


class TestMCCDeviceDIOActuator(unittest.TestCase):
    """
    All test cases from this class are executed with a simulated device.
    The simulator mirrors DO channels 0-7 (Port A) to DI channels 8-15 (Port B).
    """
    def setUp(self):
        self.controller = pwrmccdaq.MCCDeviceDIOActuator(**CONFIG_CONTROLLER_DEVICE)

    def tearDown(self):
        if self.controller:
            self.controller.terminate()

    def test_initial_state(self):
        """Check that all axes start in the low (off) position."""
        self.assertEqual(self.controller.position.value["nd-filter"], "off")

    def test_move_abs(self):
        """Test moving the axis to both positions."""
        # Move to "on" (high)
        f = self.controller.moveAbs({"nd-filter": "on"})
        f.result(timeout=5)
        self.assertEqual(self.controller.position.value["nd-filter"], "on")

        # Move back to "off" (low)
        f = self.controller.moveAbs({"nd-filter": "off"})
        f.result(timeout=5)
        self.assertEqual(self.controller.position.value["nd-filter"], "off")

    def test_move_abs_invalid(self):
        """Test that moving to an invalid position raises ValueError."""
        with self.assertRaises(ValueError):
            self.controller.moveAbs({"nd-filter": "invalid"})

    def test_interlock(self):
        """
        Test that the interlockTriggered VA updates when the interlock DI channel changes.
        The simulator mirrors DO channel 1 (Port A, bit 1) to DI channel 9 (Port B, bit 1).
        """
        # Check the VA is registered and polling thread is running
        self.assertTrue(model.hasVA(self.controller, "interlockTriggered"))
        self.assertTrue(self.controller._status_thread.is_alive())

        # Initially the interlock should not be triggered
        time.sleep(0.15)  # let the polling thread do one cycle
        self.assertFalse(self.controller.interlockTriggered.value)

        # Simulate an interlock signal by writing to DO channel 1 (Port A, bit 1),
        # which the simulator mirrors to DI channel 9 (Port B, bit 1)
        port, bit = self.controller.channel_to_port(1)
        with self.controller._connection_lock:
            self.controller.device.DBitOut(port, bit, 1)
        time.sleep(0.15)  # wait longer than the poll interval

        self.assertTrue(self.controller.interlockTriggered.value)

        # Release the interlock
        with self.controller._connection_lock:
            self.controller.device.DBitOut(port, bit, 0)
        time.sleep(0.15)

        self.assertFalse(self.controller.interlockTriggered.value)

    def test_terminate(self):
        """Test that terminate() resets all outputs to low."""
        f = self.controller.moveAbs({"nd-filter": "on"})
        f.result(timeout=5)
        self.assertEqual(self.controller.position.value["nd-filter"], "on")

        self.controller.terminate()
        # After terminate, the DO pin should be low
        port, bit = self.controller.channel_to_port(0)
        val = self.controller.device.DBitIn(port, bit)
        self.assertEqual(val, 0)
        self.controller = None  # prevent tearDown from calling terminate() again


if __name__ == "__main__":
    unittest.main()
