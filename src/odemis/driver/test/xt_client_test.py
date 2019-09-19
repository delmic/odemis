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

logging.basicConfig(level=logging.INFO)

TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)


class TestMicroscope(unittest.TestCase):
    """
    Test communication with the server using the Microscope client class.
    """

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No hardware available.")
        self.microscope = xt_client.MicroscopeClient(timeout=30)
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
        init_pos = self.microscope.get_stage_position()  # returns [x, y, z, r, t]  in [m]
        # move stage to a different position
        position = {'x': init_pos['x'] - 1e-6, 'y': init_pos['y'] - 2e-6, 'z': 10e-6}  # [m]
        self.microscope.move_stage(position)
        tstart = time.time()
        while self.microscope.stage_is_moving() and time.time() < tstart + 5:
            continue
        stage_position = self.microscope.get_stage_position()
        # test move_stage method actually moves HW by the requested value
        self.assertAlmostEqual(stage_position['x'], position['x'])
        self.assertAlmostEqual(stage_position['y'], position['y'])
        self.assertAlmostEqual(stage_position['z'], position['z'])
        # test relative movement
        init_pos = self.microscope.get_stage_position()  # returns [x, y, z, r, t]  in [m]
        relative_position = {'x': 100e-6, 'y': 200e-6}  # [m]
        self.microscope.move_stage(relative_position, rel=True)
        tstart = time.time()
        while self.microscope.stage_is_moving() and time.time() < tstart + 5:
            continue
        stage_position = self.microscope.get_stage_position()
        # test move_stage method actually moves HW by the requested value
        self.assertAlmostEqual(stage_position['x'], relative_position['x'] + init_pos['x'])
        self.assertAlmostEqual(stage_position['y'], relative_position['y'] + init_pos['y'])

        # move to a position out of range -> should be impossible
        position = {'x': 1, 'y': 300}  # [m]
        with self.assertRaises(Exception):
            self.microscope.move_stage(position)

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
        spotsize_range = self.microscope.spotsize_info()['range']
        new_spotsize = spotsize_range[1] - 1
        self.microscope.set_ebeam_spotsize(new_spotsize)
        self.assertAlmostEqual(new_spotsize, self.microscope.get_ebeam_spotsize())
        # Test it still works for different values.
        new_spotsize = spotsize_range[0] + 1
        self.microscope.set_ebeam_spotsize(new_spotsize)
        self.assertAlmostEqual(new_spotsize, self.microscope.get_ebeam_spotsize())

    def test_set_dwell_time(self):
        """Setting the dwell time."""
        dwell_time_range = self.microscope.dwell_time_info()['range']
        new_dwell_time = dwell_time_range[0] + 1.5e-6
        self.microscope.set_dwell_time(new_dwell_time)
        self.assertAlmostEqual(new_dwell_time, self.microscope.get_dwell_time())
        # Test it still works for different values.
        new_dwell_time = dwell_time_range[1] - 1.5e-6
        self.microscope.set_dwell_time(new_dwell_time)
        self.assertAlmostEqual(new_dwell_time, self.microscope.get_dwell_time())

    @unittest.skip("do not test setting voltage on the hardware.")
    def test_set_ht_voltage(self):
        """Setting the HT Voltage."""
        ht_voltage_range = self.microscope.ht_voltage_info()['range']
        new_voltage = ht_voltage_range[1] - 1e3
        self.microscope.set_ht_voltage(new_voltage)
        self.assertAlmostEqual(new_voltage, self.microscope.get_ht_voltage())
        # Test it still works for different values.
        new_voltage = ht_voltage_range[0] + 1e3
        self.microscope.set_ht_voltage(new_voltage)
        self.assertAlmostEqual(new_voltage, self.microscope.get_ht_voltage())

    def test_blank_beam(self):
        """Test that the beam is blanked after blank beam is called."""
        self.microscope.blank_beam()
        self.assertTrue(self.microscope.beam_is_blanked())

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
        self.microscope.wait_for_state_changed(xt_client.XT_RUN, name='optical4')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state(name='optical4'), xt_client.XT_RUN)
        self.microscope.set_channel_state(name='optical4', state=xt_client.XT_STOP)
        self.microscope.wait_for_state_changed(xt_client.XT_STOP, name='optical4')  # timeout is handled on the server side
        self.assertEqual(self.microscope.get_channel_state(name='optical4'), xt_client.XT_STOP)

    def test_acquire_ccd_image(self):
        """Test acquiring an image from the optical channel."""
        self.microscope.set_channel_state(name='optical4', state=xt_client.XT_RUN)
        self.microscope.wait_for_state_changed(xt_client.XT_RUN, name='optical4')  # timeout is handled on the server side
        image = self.microscope.acquire_image(channel_name='optical4')
        self.assertEqual(len(image.shape), 2)

    def test_set_beam_shift(self):
        """Setting the beam shift."""
        beam_shift_range = self.microscope.beam_shift_info()['range']
        new_beam_shift_x = beam_shift_range['x'][1] - 1e-6
        new_beam_shift_y = beam_shift_range['y'][0] + 1e-6
        self.microscope.set_beam_shift(new_beam_shift_x, new_beam_shift_y)
        self.assertAlmostEqual((new_beam_shift_x, new_beam_shift_y), self.microscope.get_beam_shift())
        # Test it still works for different values.
        new_beam_shift_x = beam_shift_range['x'][0] + 1e-6
        new_beam_shift_y = beam_shift_range['y'][1] - 1e-6
        self.microscope.set_beam_shift(new_beam_shift_x, new_beam_shift_y)
        self.assertAlmostEqual((new_beam_shift_x, new_beam_shift_y), self.microscope.get_beam_shift())


if __name__ == '__main__':
    unittest.main()
