#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 16 Aug 2019

@author: Thera Pals

Copyright © 2019 Thera Pals, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see
http://www.gnu.org/licenses/.
"""
from __future__ import division, print_function

import logging
import os
import time
import unittest

from odemis.driver import xt_client
from odemis.driver.xt_client import DETECTOR2CHANNELNAME
from odemis.model import ProgressiveFuture

logging.basicConfig(level=logging.INFO)

TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)

# arguments used for the creation of basic components
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "hfw_nomag": 1}
CONFIG_STAGE = {"name": "stage", "role": "stage",
                "inverted": ["x"],
                }
CONFIG_FOCUS = {"name": "focuser", "role": "ebeam-focus"}
CONFIG_SEM = {"name": "sem", "role": "sem", "address": "PYRO:Microscope@localhost:4242",
              "children": {"scanner": CONFIG_SCANNER,
                           "focus": CONFIG_FOCUS,
                           "stage": CONFIG_STAGE,
                           }
              }


class TestMicroscope(unittest.TestCase):
    """
    Test communication with the server using the Microscope client class.
    """

    @classmethod
    def setUpClass(cls):

        cls.microscope = xt_client.SEM(**CONFIG_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child
            elif child.name == CONFIG_STAGE["name"]:
                cls.stage = child

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No hardware available.")
        if self.microscope.get_vacuum_state() != 'vacuum':
            self.skipTest("Chamber needs to be in vacuum, please pump.")

    def test_acquisition(self):
        """Test acquiring an image."""
        image = self.microscope.acquire_image(channel_name='electron1')
        self.assertEqual(len(image.shape), 2)

    @unittest.skip("Skip because the microscope stage is not used, an external stage is used.")
    def test_move_stage(self):
        """
        Test that moving the microscope stage to a certain x, y, z position moves it to that position.
        """
        pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"x": 2e-6, "y": 3e-6})
        f.result()
        self.assertNotEqual(self.stage.position.value, pos)

        init_pos = self.stage.position.value.copy()  # returns [x, y, z, r, t]  in [m]
        # move stage to a different position
        position = {'x': init_pos['x'] - 1e-6, 'y': init_pos['y'] - 2e-6, 'z': 10e-6}  # [m]
        f = self.stage.moveAbs(position)
        f.result()
        self.assertNotEqual(self.stage.position.value, pos)

        stage_position = self.stage.position.value.copy()
        # test move_stage method actually moves HW by the requested value
        self.assertAlmostEqual(stage_position['x'], position['x'])
        self.assertAlmostEqual(stage_position['y'], position['y'])
        self.assertAlmostEqual(stage_position['z'], position['z'])
        # test relative movement
        init_pos = self.stage.position.value.copy()  # returns [x, y, z, r, t]  in [m]
        relative_position = {'x': 100e-6, 'y': 200e-6}  # [m]
        f = self.stage.moveRel(relative_position)
        f.result()

        stage_position = self.stage.position.value.copy()
        # test move_stage method actually moves HW by the requested value
        self.assertAlmostEqual(stage_position['x'], relative_position['x'] + init_pos['x'])
        self.assertAlmostEqual(stage_position['y'], relative_position['y'] + init_pos['y'])

        # move to a position out of range -> should be impossible
        position = {'x': 1, 'y': 300}  # [m]
        with self.assertRaises(Exception):
            f = self.stage.moveAbs(position)
            f.result()

    def test_set_scan_field_size(self):
        """
        Test setting the field of view (aka the size, which can be scanned with the current settings).
        """
        scanfield_range = self.microscope.scanning_size_info()['range']
        new_scanfield_x = scanfield_range['x'][1]
        new_scanfield_y = scanfield_range['y'][1]
        self.microscope.set_scanning_size(new_scanfield_x, new_scanfield_y)
        self.assertEqual(self.microscope.get_scanning_size()[0], new_scanfield_x)
        self.assertEqual(self.microscope.get_scanning_size()[1], new_scanfield_y)
        # Test it still works for different values.
        new_scanfield_x = scanfield_range['x'][0]
        new_scanfield_y = scanfield_range['y'][0]
        self.microscope.set_scanning_size(new_scanfield_x, new_scanfield_y)
        self.assertEqual(self.microscope.get_scanning_size()[0], new_scanfield_x)
        self.assertEqual(self.microscope.get_scanning_size()[1], new_scanfield_y)
        # set value out of range
        x = 1000000  # [m]
        y = 100
        with self.assertRaises(Exception):
            self.microscope.set_scanning_size(x, y)

    def test_hfov(self):
        """
        Test setting the horizontal field of view (aka the size, which can be scanned with the current settings).
        """
        ebeam = self.scanner
        orig_mag = ebeam.magnification.value
        orig_fov = ebeam.horizontalFoV.value

        ebeam.horizontalFoV.value = orig_fov / 2
        # time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(orig_mag * 2, ebeam.magnification.value)
        self.assertAlmostEqual(orig_fov / 2, ebeam.horizontalFoV.value)

        # Test setting the min and max
        fov_min = ebeam._hfw_nomag / ebeam.magnification.range[1]
        fov_max = ebeam._hfw_nomag / ebeam.magnification.range[0]
        ebeam.horizontalFoV.value = fov_min
        # time.sleep(6)
        self.assertAlmostEqual(fov_min, ebeam.horizontalFoV.value)

        ebeam.horizontalFoV.value = fov_max
        # time.sleep(6)
        self.assertAlmostEqual(fov_max, ebeam.horizontalFoV.value)

        # Reset
        ebeam.horizontalFoV.value = orig_fov
        self.assertAlmostEqual(orig_fov, ebeam.horizontalFoV.value)

    def test_set_selected_area(self):
        """Test setting a selected area in the field of view."""
        start_pos = (0, 0)
        size = (200, 200)
        self.microscope.set_selected_area(start_pos, size)
        x, y, w, h = self.microscope.get_selected_area()
        self.assertEqual(start_pos + size, (x, y, w, h))
        # set value out of range
        size = (20000, 200)
        with self.assertRaises(Exception):
            self.microscope.set_selected_area(start_pos, size)

    def test_reset_selected_area(self):
        """Test resetting the selected area to select the entire image.."""
        start_pos = (0, 0)
        size = (200, 200)
        self.microscope.set_selected_area(start_pos, size)
        self.microscope.reset_selected_area()

    def test_set_ebeam_spotsize(self):
        """Setting the ebeam spot size."""
        spotsize_range = self.scanner.spotSize.range
        new_spotsize = spotsize_range[1] - 1
        self.scanner.spotSize.value = new_spotsize
        self.assertAlmostEqual(new_spotsize, self.scanner.spotSize.value)
        # Test it still works for different values.
        new_spotsize = spotsize_range[0] + 1
        self.scanner.spotSize.value = new_spotsize
        self.assertAlmostEqual(new_spotsize, self.scanner.spotSize.value)

    def test_set_dwell_time(self):
        """Setting the dwell time."""
        dwell_time_range = self.scanner.dwellTime.range
        new_dwell_time = dwell_time_range[0] + 1.5e-6
        self.scanner.dwellTime.value = new_dwell_time
        self.assertAlmostEqual(new_dwell_time, self.scanner.dwellTime.value)
        # Test it still works for different values.
        new_dwell_time = dwell_time_range[1] - 1.5e-6
        self.scanner.dwellTime.value = new_dwell_time
        self.assertAlmostEqual(new_dwell_time, self.scanner.dwellTime.value)

    @unittest.skip("do not test setting voltage on the hardware.")
    def test_set_ht_voltage(self):
        """Setting the HT Voltage."""
        ht_voltage_range = self.scanner.accelVoltage.range
        new_voltage = ht_voltage_range[1] - 1e3
        self.scanner.accelVoltage.value = new_voltage
        self.assertAlmostEqual(new_voltage, self.scanner.accelVoltage.value)
        # Test it still works for different values.
        new_voltage = ht_voltage_range[0] + 1e3
        self.scanner.accelVoltage.value = new_voltage
        self.assertAlmostEqual(new_voltage, self.scanner.accelVoltage.value)

    def test_blank_beam(self):
        """Test that the beam is blanked after blank beam is called."""
        self.scanner.blanker.value = True
        self.assertTrue(self.scanner.blanker.value)

    @unittest.skip("slow, takes about 3 or 4 minutes. When not skipped, increase the timeout in the init.")
    def test_vent_and_pump(self):
        """Test venting and then pumping."""
        self.microscope.vent()
        self.assertEqual(self.microscope.get_vacuum_state(), 'vented')
        self.microscope.pump()
        self.assertEqual(self.microscope.get_vacuum_state(), 'vacuum')

    @unittest.skip("Skip because the microscope stage is not used, an external stage is used.")
    def test_home_stage(self):
        """Test that the stage is homed after home_stage is called."""
        self.microscope.home_stage()
        tstart = time.time()
        while self.microscope.stage_is_moving() and time.time() < tstart + 5:
            continue
        self.assertTrue(self.microscope.is_homed())

    def test_change_channel_state(self):
        """Test changing the channel state and waiting for the channel state to change."""
        self.microscope.set_channel_state('electron1', xt_client.XT_RUN)
        self.microscope.wait_for_state_changed(xt_client.XT_RUN, 'electron1')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state('electron1'), xt_client.XT_RUN)
        self.microscope.set_channel_state('electron1', xt_client.XT_STOP)
        self.microscope.wait_for_state_changed(xt_client.XT_STOP, 'electron1')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state('electron1'), xt_client.XT_STOP)

    def test_change_ccd_channel_state(self):
        """Test changing the channel state and waiting for the channel state to change for the optical channel."""
        self.microscope.set_channel_state(name='optical4', state=xt_client.XT_RUN)
        self.microscope.wait_for_state_changed(xt_client.XT_RUN,
                                               name='optical4')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state(name='optical4'), xt_client.XT_RUN)
        self.microscope.set_channel_state(name='optical4', state=xt_client.XT_STOP)
        self.microscope.wait_for_state_changed(xt_client.XT_STOP,
                                               name='optical4')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state(name='optical4'), xt_client.XT_STOP)

    def test_acquire_ccd_image(self):
        """Test acquiring an image from the optical channel."""
        self.microscope.set_channel_state(name='optical4', state=xt_client.XT_RUN)
        self.microscope.wait_for_state_changed(xt_client.XT_RUN,
                                               name='optical4')  # timeout is handled on the server side
        image = self.microscope.acquire_image(channel_name='optical4')
        self.assertEqual(len(image.shape), 2)

    def test_set_beam_shift(self):
        """Setting the beam shift."""
        beam_shift_range = self.scanner.beamShift.range
        new_beam_shift_x = beam_shift_range[1][0] - 1e-6
        new_beam_shift_y = beam_shift_range[0][1] + 1e-6
        self.scanner.beamShift.value = (new_beam_shift_x, new_beam_shift_y)
        self.assertAlmostEqual((new_beam_shift_x, new_beam_shift_y), self.scanner.beamShift.value)
        # Test it still works for different values.
        new_beam_shift_x = beam_shift_range[0][0] + 1e-6
        new_beam_shift_y = beam_shift_range[1][1] - 1e-6
        self.scanner.beamShift.value = (new_beam_shift_x, new_beam_shift_y)
        self.assertAlmostEqual((new_beam_shift_x, new_beam_shift_y), self.scanner.beamShift.value)

    @unittest.skip("Currently the auto stigmator functionality is not supported by the XT client and the code is "
                   "commented out")
    # TODO Update test_apply_autostigmator code and make test work on XT client when auto stigmator functionality
    #  works again.
    def test_apply_autostigmator(self):
        """
        Test for the auto stigmation functionality.
        """
        detector = 'se-detector'  # autostigmator only works for the se-detector with channel=electron1
        channel = DETECTOR2CHANNELNAME[detector]

        # Start auto stigmation and check if it is running.
        autostigmator_future = self.scanner.applyAutoStigmator(detector)
        time.sleep(2.5)  # Give microscope/simulator the time to update the state
        autostigmator_state = self.microscope.is_autostigmating(channel)
        self.assertEqual(autostigmator_state, True)
        self.assertIsInstance(autostigmator_future, ProgressiveFuture)

        # Stop auto stigmation and check if it stopped running.
        autostigmator_future.cancel()
        time.sleep(5.0)  # Give microscope/simulator the time to update the state
        autostigmator_state = self.microscope.is_autostigmating(channel)
        self.assertEqual(autostigmator_state, False)

        max_execution_time = 30  # Approximately 3 times the normal expected execution time (s)
        for i in range(0, 5):
            starting_time = time.time()
            autostigmator_future = self.scanner.applyAutoStigmator(detector)
            time.sleep(0.5)  # Give microscope/simulator the time to update the state
            autostigmator_state = self.microscope.is_autostigmating(channel)
            self.assertEqual(autostigmator_state, True)
            self.assertIsInstance(autostigmator_future, ProgressiveFuture)

            autostigmator_future.result(timeout=max_execution_time)

            # Check if the time to perform the auto stigmation is not too long
            self.assertLess(time.time() - starting_time, 30,
                            "Execution of auto stigmation was stopped because it took more than %s seconds." %
                            max_execution_time)
            # Line to inspect the execution time to update the expected time
            print("Execution time was %s seconds " % (time.time() - starting_time))

        # Test if an error is raised when an invalid detector name is provided
        with self.assertRaises(KeyError):
            self.scanner.applyAutoStigmator("error_expected")
        time.sleep(2.5)  # Give microscope/simulator the time to update the state
        autostigmator_state = self.microscope.is_autostigmating(channel)
        self.assertEqual(autostigmator_state, False)  # Check if state remained unchanged

    def test_apply_auto_contrast_brightness(self):
        """
        Test for the auto contrast brightness functionality.
        """
        # Check all the different detector types by looping over them
        for role, channel in DETECTOR2CHANNELNAME.items():
            # Start auto contrast brightness and check if it is running.
            auto_contrast_brightness_future = self.scanner.applyAutoContrastBrightness(role)
            auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(channel)
            time.sleep(0.01)
            self.assertEqual(auto_contrast_brightness_state, True)
            self.assertIsInstance(auto_contrast_brightness_future, ProgressiveFuture)

            # Stop auto auto contrast brightness and check if it stopped running.
            auto_contrast_brightness_future.cancel()
            time.sleep(5.0)  # Give microscope/simulator the time to update the state
            auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(channel)
            self.assertEqual(auto_contrast_brightness_state, False)
            self.assertIsInstance(auto_contrast_brightness_future, ProgressiveFuture)

            # Test starting auto contrast brightness and cancelling directly
            auto_contrast_brightness_future = self.scanner.applyAutoContrastBrightness(role)
            auto_contrast_brightness_future.cancel()
            time.sleep(5.0)  # Give microscope/simulator the time to update the state
            auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(channel)
            self.assertEqual(auto_contrast_brightness_state, False)
            self.assertIsInstance(auto_contrast_brightness_future, ProgressiveFuture)

            # Start auto contrast brightness
            max_execution_time = 60  # Approximately 3 times the normal expected execution time (s)
            starting_time = time.time()
            auto_contrast_brightness_future = self.scanner.applyAutoContrastBrightness(role)
            time.sleep(0.5)  # Give microscope/simulator the time to update the state
            # Wait until the auto contrast brightness is finished
            auto_contrast_brightness_future.result(timeout=max_execution_time)

            # Check if the time to perform the auto contrast brightness is not too long
            self.assertLess(time.time() - starting_time, max_execution_time,
                            "Execution of auto contrast brightness was stopped because it took more than %s seconds."
                            % max_execution_time)

            auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(channel)
            self.assertEqual(auto_contrast_brightness_state, False)

        # Test if an error is raised when an invalid detector is provided
        with self.assertRaises(KeyError):
            self.scanner.applyAutoContrastBrightness("error_expected")
        time.sleep(2.5)  # Give microscope/simulator the time to update the state
        autofocus_state = self.microscope.is_autofocusing(channel)
        self.assertEqual(autofocus_state, False)

    def test_apply_autofocus(self):
        """
        Test for the auto functionality of the autofocus.
        """
        # Check all the different detector types by looping over them
        for role, channel in DETECTOR2CHANNELNAME.items():
            # Start auto focus and check if it is running.
            autofocus_future = self.efocus.applyAutofocus(role)
            autofocus_state = self.microscope.is_autofocusing(channel)
            time.sleep(0.01)
            self.assertEqual(autofocus_state, True)
            self.assertIsInstance(autofocus_future, ProgressiveFuture)

            # Stop auto focus and check if it stopped running.
            autofocus_future.cancel()
            time.sleep(5.0)  # Give microscope/simulator the time to update the state
            autofocus_state = self.microscope.is_autofocusing(channel)
            self.assertEqual(autofocus_state, False)
            self.assertIsInstance(autofocus_future, ProgressiveFuture)

            # Test starting auto focus and cancelling directly
            autofocus_future = self.efocus.applyAutofocus(role)
            autofocus_future.cancel()
            time.sleep(5.0)  # Give microscope/simulator the time to update the state
            autofocus_state = self.microscope.is_autofocusing(channel)
            self.assertEqual(autofocus_state, False)
            self.assertIsInstance(autofocus_future, ProgressiveFuture)

            # Start autofocus
            max_execution_time = 40  # Approximately 3 times the normal expected execution time (s)
            starting_time = time.time()
            autofocus_future = self.efocus.applyAutofocus(role)
            time.sleep(0.5)  # Give microscope/simulator the time to update the state
            autofocus_future.result(timeout=max_execution_time)  # Wait until the autofocus is finished

            # Check if the time to perform the autofocus is not too long
            self.assertLess(time.time() - starting_time, max_execution_time,
                            "Execution autofocus was stopped because it took more than %s seconds."
                            % max_execution_time)

            autofocus_state = self.microscope.is_autofocusing(channel)
            self.assertEqual(autofocus_state, False)

        # Test if an error is raised when an invalid detector is provided
        with self.assertRaises(KeyError):
            self.efocus.applyAutofocus("error_expected")
        time.sleep(2.5)  # Give microscope/simulator the time to update the state
        autofocus_state = self.microscope.is_autofocusing(channel)
        self.assertEqual(autofocus_state, False)


if __name__ == '__main__':
    unittest.main()
