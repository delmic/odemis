#!/usr/bin/env python3
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
import math
import os
import time
import unittest

from odemis.driver import xt_client
from odemis.driver.xt_client import DETECTOR2CHANNELNAME
from odemis.model import ProgressiveFuture
from odemis.util import test

logging.basicConfig(level=logging.INFO)

TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)

# arguments used for the creation of basic components
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "hfw_nomag": 1}
CONFIG_STAGE = {"name": "stage", "role": "stage",
                "inverted": ["x"],
                }
CONFIG_FOCUS = {"name": "focuser", "role": "ebeam-focus"}
CONFIG_SEM = {"name": "sem", "role": "sem", "address": "PYRO:Microscope@192.168.31.130:4242",
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
        self.xt_type = "xttoolkit" if "xttoolkit" in self.microscope.swVersion.lower() else "xtlib"

    def test_move_stage(self):
        """
        Test that moving the microscope stage to a certain x, y, z position moves it to that position.
        """
        if self.xt_type == 'xttoolkit':
            self.skipTest("Microscope stage not tested, too dangerous.")
        init_pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"x": 2e-6, "y": 3e-6})
        f.result()
        self.assertNotEqual(self.stage.position.value, init_pos)

        new_pos = self.stage.position.value.copy()  # returns [x, y, z, r, t]  in [m]
        # move stage to a different position
        position = {'x': new_pos['x'] - 1e-6, 'y': new_pos['y'] - 2e-6, 'z': 10e-6}  # [m]
        f = self.stage.moveAbs(position)
        f.result()
        self.assertNotEqual(self.stage.position.value, new_pos)

        stage_position = self.stage.position.value.copy()
        # test move_stage method actually moves HW by the requested value
        self.assertAlmostEqual(stage_position['x'], position['x'])
        self.assertAlmostEqual(stage_position['y'], position['y'])
        self.assertAlmostEqual(stage_position['z'], position['z'])
        # test relative movement
        new_pos = self.stage.position.value.copy()  # returns [x, y, z, r, t]  in [m]
        relative_position = {'x': 100e-6, 'y': 200e-6}  # [m]
        f = self.stage.moveRel(relative_position)
        f.result()

        stage_position = self.stage.position.value.copy()
        # test move_stage method actually moves HW by the requested value
        self.assertAlmostEqual(stage_position['x'], relative_position['x'] + new_pos['x'])
        self.assertAlmostEqual(stage_position['y'], relative_position['y'] + new_pos['y'])

        # move to a position out of range -> should be impossible
        position = {'x': 1, 'y': 300}  # [m]
        with self.assertRaises(Exception):
            f = self.stage.moveAbs(position)
            f.result()
        # Move stage back to initial position
        self.stage.moveAbs(init_pos)

    def test_move_stage_rot_tilt(self):
        """
        Test that moving the microscope stage to a certain t, r position moves it to that position.
        """
        if self.xt_type == 'xttoolkit':
            self.skipTest("Microscope stage not tested, too dangerous.")
        init_pos = self.stage.position.value.copy()
        # Test absolute movement
        abs_pos = {"y": 2e-6, "rz": 0.5, "rx": 0.2}
        f = self.stage.moveAbs(abs_pos)
        f.result()
        self.assertAlmostEqual(self.stage.position.value["y"], abs_pos["y"])
        self.assertAlmostEqual(self.stage.position.value["rz"], abs_pos["rz"], places=4)
        self.assertAlmostEqual(self.stage.position.value["rx"], abs_pos["rx"], places=4)
        # Test relative movement
        rel_pos = {"y": 2e-6, "rz": 6, "rx": 0.2}
        f = self.stage.moveRel(rel_pos)
        f.result()
        self.assertAlmostEqual(self.stage.position.value["y"], abs_pos["y"] + rel_pos["y"])
        self.assertAlmostEqual(self.stage.position.value["rz"] % (2 * math.pi),
                               (abs_pos["rz"] + rel_pos["rz"]) % (2 * math.pi), places=4)
        self.assertAlmostEqual(self.stage.position.value["rx"],
                               (abs_pos["rx"] + rel_pos["rx"]), places=4)
        self.stage.moveAbs(init_pos)

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

    def test_set_ebeam_spotsize(self):
        """Setting the ebeam spot size."""
        init_spotsize = self.scanner.spotSize.value
        spotsize_range = self.scanner.spotSize.range
        new_spotsize = spotsize_range[1] - 1
        self.scanner.spotSize.value = new_spotsize
        self.assertAlmostEqual(new_spotsize, self.scanner.spotSize.value)
        # Test it still works for different values.
        new_spotsize = spotsize_range[0] + 1
        self.scanner.spotSize.value = new_spotsize
        self.assertAlmostEqual(new_spotsize, self.scanner.spotSize.value)
        # set spotSize back to initial value
        self.scanner.spotSize.value = init_spotsize

    def test_set_dwell_time(self):
        """Setting the dwell time."""
        init_dwell_time = self.scanner.dwellTime.value
        dwell_time_range = self.scanner.dwellTime.range
        new_dwell_time = dwell_time_range[0] + 1.5e-6
        self.scanner.dwellTime.value = new_dwell_time
        self.assertAlmostEqual(new_dwell_time, self.scanner.dwellTime.value)
        # Test it still works for different values.
        new_dwell_time = dwell_time_range[1] - 1.5e-6
        self.scanner.dwellTime.value = new_dwell_time
        self.assertAlmostEqual(new_dwell_time, self.scanner.dwellTime.value)
        # set dwellTime back to initial value
        self.scanner.dwellTime.value = init_dwell_time

    @unittest.skip("Do not test setting voltage on the hardware.")
    def test_set_ht_voltage(self):
        """Setting the HT Voltage."""
        init_voltage = self.scanner.accelVoltage.value
        ht_voltage_range = self.scanner.accelVoltage.range
        new_voltage = ht_voltage_range[1] - 1e3
        self.scanner.accelVoltage.value = new_voltage
        self.assertAlmostEqual(new_voltage, self.scanner.accelVoltage.value)
        # Test it still works for different values.
        new_voltage = ht_voltage_range[0] + 1e3
        self.scanner.accelVoltage.value = new_voltage
        self.assertAlmostEqual(new_voltage, self.scanner.accelVoltage.value)
        # set accelVoltage back to initial value
        self.scanner.accelVoltage.value = init_voltage

    def test_blank_beam(self):
        """Test that the beam is unblanked after unblank beam is called, and blanked after blank is called."""
        self.scanner.blanker.value = False
        # NOTE: it is only possible to check if the beam is unblanked when the stream in XT is running. If the stream is
        # not running it will always return True. Therefore this test only uses a weak check for unblanking the beam.
        self.assertIsInstance(self.scanner.blanker.value, bool)
        self.scanner.blanker.value = True
        self.assertTrue(self.scanner.blanker.value)

    def test_rotation(self):
        """Test setting the rotation."""
        init_rotation = self.scanner.rotation.value
        self.scanner.rotation.value += 0.01
        self.assertEqual(self.scanner.rotation.value, init_rotation + 0.01)
        self.scanner.rotation.value = init_rotation


class TestMicroscopeInternal(unittest.TestCase):
    """
    Test calling the internal of the Microscope client class directly.
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
        self.xt_type = "xttoolkit" if "xttoolkit" in self.microscope.swVersion.lower() else "xtlib"

    def test_acquisition(self):
        """Test acquiring an image."""
        image = self.microscope.get_latest_image(channel_name='electron1')
        self.assertEqual(len(image.shape), 2)

    def test_stop_stage_movement(self):
        """Test that stopping stage movement, stops moving the stage."""
        if self.xt_type == 'xttoolkit':
            self.skipTest("Microscope stage not tested, too dangerous.")
        init_pos = self.microscope.get_stage_position()  # returns [x, y, z, r, t]  in [m]
        # move stage to a different position
        position = {'x': init_pos['x'] - 1e-6, 'y': init_pos['y'] - 2e-6, 'z': 10e-6}  # [m]
        self.microscope.move_stage(position)
        self.microscope.stop_stage_movement()
        time.sleep(1)
        self.assertFalse(self.microscope.stage_is_moving())
        # Move stage back to initial position
        self.microscope.move_stage(init_pos)

    def test_set_scan_field_size(self):
        """
        Test setting the field of view (aka the size, which can be scanned with the current settings).
        """
        scanfield_range = self.microscope.scanning_size_info()['range']
        init_scan_size = self.microscope.get_scanning_size()[0]
        new_scanfield_x = scanfield_range['x'][1]
        self.microscope.set_scanning_size(new_scanfield_x)
        self.assertEqual(self.microscope.get_scanning_size()[0], new_scanfield_x)
        # Test it still works for different values.
        new_scanfield_x = scanfield_range['x'][0]
        self.microscope.set_scanning_size(new_scanfield_x)
        self.assertEqual(self.microscope.get_scanning_size()[0], new_scanfield_x)
        # set value out of range
        x = 1000000  # [m]
        with self.assertRaises(Exception):
            self.microscope.set_scanning_size(x)
        # set scanfield back to initial value
        self.microscope.set_scanning_size(init_scan_size)

    def test_set_selected_area(self):
        """Test setting a selected area in the field of view."""
        start_pos = (0, 0)
        area_range = self.microscope.selected_area_info()["range"]
        size = (area_range[0][1] - 100, area_range[1][1] - 100)
        self.microscope.set_selected_area(start_pos, size)
        x, y, w, h = self.microscope.get_selected_area()
        self.assertEqual(start_pos + size, (x, y, w, h))
        # set value out of range
        size = (20000, 200)
        with self.assertRaises(Exception):
            self.microscope.set_selected_area(start_pos, size)
        self.microscope.reset_selected_area()

    def test_reset_selected_area(self):
        """Test resetting the selected area to select the entire image.."""
        start_pos = (0, 0)
        size = (200, 200)
        self.microscope.set_selected_area(start_pos, size)
        self.microscope.reset_selected_area()

    @unittest.skip("slow, takes about 3 or 4 minutes. When not skipped, increase the timeout in the init.")
    def test_vent_and_pump(self):
        """Test venting and then pumping."""
        self.microscope.vent()
        self.assertEqual(self.microscope.get_vacuum_state(), 'vented')
        self.microscope.pump()
        self.assertEqual(self.microscope.get_vacuum_state(), 'vacuum')

    def test_home_stage(self):
        """Test that the stage is homed after home_stage is called."""
        if self.xt_type == 'xttoolkit':
            self.skipTest("Microscope stage not tested, too dangerous.")
        self.microscope.home_stage()
        tstart = time.time()
        while self.microscope.stage_is_moving() and time.time() < tstart + 5:
            continue
        self.assertTrue(self.microscope.is_homed())

    def test_change_channel_state(self):
        """Test changing the channel state and waiting for the channel state to change."""
        self.microscope.set_channel_state('electron1', True)
        self.microscope.wait_for_state_changed(xt_client.XT_RUN, 'electron1')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state('electron1'), xt_client.XT_RUN)
        self.microscope.set_channel_state('electron1', False)
        self.microscope.wait_for_state_changed(xt_client.XT_STOP, 'electron1')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state('electron1'), xt_client.XT_STOP)

    def test_change_ccd_channel_state(self):
        """Test changing the channel state and waiting for the channel state to change for the optical channel."""
        self.microscope.set_channel_state(name='optical4', state=True)
        self.microscope.wait_for_state_changed(xt_client.XT_RUN,
                                               name='optical4')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state(name='optical4'), xt_client.XT_RUN)
        self.microscope.set_channel_state(name='optical4', state=False)
        self.microscope.wait_for_state_changed(xt_client.XT_STOP,
                                               name='optical4')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state(name='optical4'), xt_client.XT_STOP)

    def test_acquire_ccd_image(self):
        """Test acquiring an image from the optical channel."""
        self.microscope.set_channel_state(name='optical4', state=True)
        self.microscope.wait_for_state_changed(xt_client.XT_RUN,
                                               name='optical4')  # timeout is handled on the server side
        image = self.microscope.get_latest_image(channel_name='optical4')
        self.assertEqual(len(image.shape), 2)

    def test_set_beam_shift(self):
        """Setting the beam shift."""
        init_beam_shift = self.scanner.beamShift.value
        beam_shift_range = self.scanner.beamShift.range
        new_beam_shift_x = beam_shift_range[1][0] - 1e-6
        new_beam_shift_y = beam_shift_range[0][1] + 1e-6
        self.scanner.beamShift.value = (new_beam_shift_x, new_beam_shift_y)
        test.assert_tuple_almost_equal((new_beam_shift_x, new_beam_shift_y), self.scanner.beamShift.value)
        # Test it still works for different values.
        new_beam_shift_x = beam_shift_range[0][0] + 1e-6
        new_beam_shift_y = beam_shift_range[1][1] - 1e-6
        self.scanner.beamShift.value = (new_beam_shift_x, new_beam_shift_y)
        test.assert_tuple_almost_equal((new_beam_shift_x, new_beam_shift_y), self.scanner.beamShift.value)
        # set beamShift back to initial value
        self.scanner.beamShift.value = init_beam_shift

    @unittest.skip("Before running this test make sure it is safe to turn on the beam.")
    def test_beam_power(self):
        """Test turning the beam on and off."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        self.microscope.set_beam_power(True)
        beam_on = self.microscope.get_beam_is_on()
        self.assertTrue(beam_on)
        self.microscope.set_beam_power(False)
        beam_on = self.microscope.get_beam_is_on()
        self.assertFalse(beam_on)

    @unittest.skip("Before running this test make sure it is safe to turn on the beam.")
    def test_autofocus(self):
        """Test running and stopping autofocus."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        self.microscope.set_beam_power(True)
        self.microscope.unblank_beam()
        self.microscope.set_scan_mode("full_frame")
        self.microscope.set_autofocusing("electron1", "run")
        self.assertTrue(self.microscope.is_autofocusing("electron1"))
        time.sleep(0.5)  # wait for the system to register it started running autofocus.
        t = time.time()
        while self.microscope.is_autofocusing("electron1"):
            if time.time() - t > 20:
                self.microscope.set_autofocusing("electron1", "stop")
                raise ValueError("Stopping autofocus, taking too long. ")
            time.sleep(0.01)
            continue
        self.assertFalse(self.microscope.is_autofocusing("electron1"))
        self.microscope.set_autofocusing("electron1", "run")
        self.microscope.set_autofocusing("electron1", "stop")
        self.assertFalse(self.microscope.is_autofocusing("electron1"))
        self.microscope.set_beam_power(False)

    @unittest.skip("Autostigmation not working. And: Before running this test "
                   "make sure it is safe to turn on the beam.")
    def test_autostigmator(self):
        """Test running and stopping autostigmator."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        self.microscope.set_beam_power(True)
        self.microscope.unblank_beam()
        self.microscope.set_scan_mode("full_frame")
        self.microscope.set_autostigmator("electron1", "run")
        self.assertTrue(self.microscope.is_autostigmating("electron1"))
        self.microscope.set_autostigmator("electron1", "stop")
        self.assertFalse(self.microscope.is_autostigmating("electron1"))
        self.microscope.set_beam_power(False)

    @unittest.skip("Before running this test make sure it is safe to turn on the beam.")
    def test_auto_contrast_brightness(self):
        """Test running and stopping auto contrast brightness."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        self.microscope.set_beam_power(True)
        self.microscope.set_scan_mode("full_frame")
        self.microscope.set_auto_contrast_brightness("electron1", "run")
        auto_cb_running = self.microscope.is_running_auto_contrast_brightness("electron1")
        self.assertTrue(auto_cb_running)
        time.sleep(0.5)  # wait for the system to register it started running auto contrast brightness.
        t = time.time()
        while self.microscope.is_running_auto_contrast_brightness("electron1"):
            if time.time() - t > 20:
                self.microscope.set_auto_contrast_brightness("electron1", "stop")
                raise ValueError("Stopping auto contrast brightness, taking too long. ")
            time.sleep(0.01)
        self.microscope.set_auto_contrast_brightness("electron1", "run")
        self.microscope.set_auto_contrast_brightness("electron1", "stop")
        auto_cb_running = self.microscope.is_running_auto_contrast_brightness("electron1")
        self.assertFalse(auto_cb_running)
        self.microscope.set_beam_power(False)

    def test_scan_mode(self):
        """Test setting the scan mode to a different mode."""
        init_scan_mode = self.microscope.get_scan_mode()
        self.microscope.set_scan_mode("external")
        self.assertEqual(self.microscope.get_scan_mode(), "external")
        self.microscope.set_scan_mode("full_frame")
        self.assertEqual(self.microscope.get_scan_mode(), "full_frame")
        # Set scan mode back to initial value
        self.microscope.set_scan_mode(init_scan_mode)

    def test_get_pressure(self):
        pressure = self.microscope.get_pressure()
        self.assertTrue(isinstance(pressure, (int, float)))

    def test_free_working_distance(self):
        """Test setting and getting the free working distance."""
        fwd_range = self.microscope.fwd_info()["range"]
        init_fwd = self.microscope.get_free_working_distance()
        new_fwd = init_fwd + 1e-6
        new_fwd = new_fwd if fwd_range[0] < new_fwd < fwd_range[1] else init_fwd - 1e-6
        self.microscope.set_free_working_distance(new_fwd)
        self.assertEqual(new_fwd, self.microscope.get_free_working_distance())
        # Set free working distance back to initial value
        self.microscope.set_free_working_distance(init_fwd)

    def test_fwd_follows_z(self):
        """Test setting fwd follows z."""
        init_fwd_follows_z = self.microscope.get_fwd_follows_z()
        self.microscope.set_fwd_follows_z(True)
        fwd_follows_z = self.microscope.get_fwd_follows_z()
        self.assertTrue(fwd_follows_z)
        self.microscope.set_fwd_follows_z(False)
        fwd_follows_z = self.microscope.get_fwd_follows_z()
        self.assertFalse(fwd_follows_z)
        # Set fwd follows z back to initial value
        self.microscope.set_fwd_follows_z(init_fwd_follows_z)

    def test_stigmator(self):
        """Test setting and getting stigmator values."""
        stig_range = self.microscope.stigmator_info()["range"]
        init_stig = self.microscope.get_stigmator()
        stig_x, stig_y = (init_stig[0] + 1e-3, init_stig[1] + 1e-3)
        stig_x = stig_x if stig_range['x'][0] < stig_x < stig_range['x'][1] else init_stig[0] - 1e-6
        stig_y = stig_y if stig_range['y'][0] < stig_y < stig_range['y'][1] else init_stig[1] - 1e-6
        new_stig = (stig_x, stig_y)
        self.microscope.set_stigmator(*new_stig)
        self.assertEqual(self.microscope.get_stigmator()[0], new_stig[0])
        self.assertEqual(self.microscope.get_stigmator()[1], new_stig[1])
        self.microscope.set_stigmator(*init_stig)

    def test_pitch(self):
        """Test setting and getting the pitch between two beams within the multiprobe pattern."""
        pitch_range = self.microscope.pitch_info()["range"]
        current_pitch = self.microscope.get_pitch()
        new_pitch = current_pitch + 1
        new_pitch = new_pitch if pitch_range[0] < new_pitch < pitch_range[1] else current_pitch - 1
        self.microscope.set_pitch(new_pitch)
        self.assertAlmostEqual(new_pitch, self.microscope.get_pitch())
        self.microscope.set_pitch(current_pitch)

    def test_primary_stigmator(self):
        """Test getting and setting the primary stigmator."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        stig_range = self.microscope.primary_stigmator_info()["range"]
        init_stig = self.microscope.get_primary_stigmator()
        stig_x, stig_y = (init_stig[0] + 1e-3, init_stig[1] + 1e-3)
        stig_x = stig_x if stig_range['x'][0] < stig_x < stig_range['x'][1] else init_stig[0] - 1e-3
        stig_y = stig_y if stig_range['y'][0] < stig_y < stig_range['y'][1] else init_stig[1] - 1e-3
        self.microscope.set_primary_stigmator(stig_x, stig_y)
        self.assertEqual(self.microscope.get_primary_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_primary_stigmator()[1], stig_y)
        # Set back to the initial stigmator value, so the system is not misaligned after finishing the test.
        self.microscope.set_primary_stigmator(*init_stig)

    def test_secondary_stigmator(self):
        """Test getting and setting the secondary stigmator."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        stig_range = self.microscope.secondary_stigmator_info()["range"]
        init_stig = self.microscope.get_secondary_stigmator()
        stig_x, stig_y = (init_stig[0] + 1e-3, init_stig[1] + 1e-3)
        stig_x = stig_x if stig_range['x'][0] < stig_x < stig_range['x'][1] else init_stig[0] - 1e-3
        stig_y = stig_y if stig_range['y'][0] < stig_y < stig_range['y'][1] else init_stig[1] - 1e-3
        self.microscope.set_secondary_stigmator(stig_x, stig_y)
        self.assertEqual(self.microscope.get_secondary_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_secondary_stigmator()[1], stig_y)
        # Set back to the initial stigmator value, so the system is not misaligned after finishing the test.
        self.microscope.set_secondary_stigmator(*init_stig)

    def test_dc_coils(self):
        """Test getting the four values of the dc coils."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        dc_coils = self.microscope.get_dc_coils()
        self.assertEqual(len(dc_coils), 4)

    def test_use_case(self):
        """Test switching the use case between the single and multibeam case."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        init_use_case = self.microscope.get_use_case()
        self.microscope.set_use_case("MultiBeamTile")
        use_case = self.microscope.get_use_case()
        self.assertEqual(use_case, "MultiBeamTile")
        self.microscope.set_use_case("SingleBeamlet")
        use_case = self.microscope.get_use_case()
        self.assertEqual(use_case, "SingleBeamlet")
        self.microscope.set_use_case(init_use_case)

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
        auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(channel)
        self.assertEqual(auto_contrast_brightness_state, False)

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
