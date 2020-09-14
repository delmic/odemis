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
import os
import time
import unittest

from odemis.driver import xt_client

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
        self.xt_type = "xttoolkit" if "xttoolkit" in self.microscope.swVersion.lower() else "xtlib"

    def test_acquisition(self):
        """Test acquiring an image."""
        image = self.microscope.get_latest_image(channel_name='electron1')
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
        image = self.microscope.get_latest_image(channel_name='optical4')
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


if __name__ == '__main__':
    unittest.main()
