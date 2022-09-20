#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 16 Aug 2019

@author: Thera Pals, Kornee Kleijwegt

Copyright © 2019-2021 Thera Pals, Delmic

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
import logging
import math
import os
import time
import unittest

import numpy
from odemis import model

from odemis.driver import xt_client
from odemis.model import ProgressiveFuture, NotSettableError
from odemis.util import testing

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Accept three values for TEST_NOHW
# * TEST_NOHW = 1: not connected to anything => skip most of the tests
# * TEST_NOHW = sim: xtadapter/server_sim.py running on localhost
# * TEST_NOHW = 0 (or anything else): connected to the real hardware
TEST_NOHW = os.environ.get("TEST_NOHW", "0")  # Default to Hw testing

if TEST_NOHW == "sim":
    pass
elif TEST_NOHW == "0":
    TEST_NOHW = False
elif TEST_NOHW == "1":
    TEST_NOHW = True
else:
    raise ValueError("Unknown value of environment variable TEST_NOHW=%s" % TEST_NOHW)

# arguments used for the creation of basic components
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "hfw_nomag": 1, "channel": "electron1"}
CONFIG_STAGE = {"name": "stage", "role": "stage",
                "inverted": ["x"],
                }
CONFIG_FOCUS = {"name": "focuser", "role": "ebeam-focus"}
CONFIG_DETECTOR = {"name": "detector", "role": "se-detector"}
CONFIG_CHAMBER = {"name": "Chamber", "role": "chamber"}
CONFIG_SEM = {"name": "sem", "role": "sem", "address": "PYRO:Microscope@192.168.31.162:4242",
              "children": {"scanner": CONFIG_SCANNER,
                           "focus": CONFIG_FOCUS,
                           "stage": CONFIG_STAGE,
                           "detector": CONFIG_DETECTOR,
                           "chamber": CONFIG_CHAMBER,
                           }
              }

CONFIG_FIB_SCANNER = {"name": "fib-scanner", "role": "ion", "channel": "ion2"}
CONFIG_FIB_SEM = {"name": "sem", "role": "sem", "address": "PYRO:Microscope@192.168.31.162:4242",
                  "children": {"fib-scanner": CONFIG_FIB_SCANNER,
                               "stage": CONFIG_STAGE,
                               "detector": CONFIG_DETECTOR,
                               }
                  }

CONFIG_DUAL_MODE_SEM = {"name": "sem", "role": "sem", "address": "PYRO:Microscope@192.168.31.162:4242",
                        "children": {"scanner": CONFIG_SCANNER,
                                     "fib-scanner": CONFIG_FIB_SCANNER,
                                     "focus": CONFIG_FOCUS,
                                     "stage": CONFIG_STAGE,
                                     "detector": CONFIG_DETECTOR,
                                     }
                        }

CONFIG_MB_SCANNER = {"name": "mb-scanner", "role": "ebeam", "hfw_nomag": 1}
CONFIG_MB_SEM = {"name": "sem", "role": "sem", "address": "PYRO:Microscope@192.168.31.138:4242",
                 "children": {"mb-scanner": CONFIG_MB_SCANNER,
                              "focus": CONFIG_FOCUS,
                              "detector": CONFIG_DETECTOR,
                              }
                 }

if TEST_NOHW == "sim":
    CONFIG_SEM["address"] = "PYRO:Microscope@localhost:4242"
    CONFIG_FIB_SEM["address"] = "PYRO:Microscope@localhost:4242"
    CONFIG_DUAL_MODE_SEM["address"] = "PYRO:Microscope@localhost:4242"
    CONFIG_MB_SEM["address"] = "PYRO:Microscope@localhost:4242"


class TestMicroscope(unittest.TestCase):
    """
    Test communication with the server using the Microscope client class.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No simulator available.")

        cls.microscope = xt_client.SEM(**CONFIG_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child
            elif child.name == CONFIG_STAGE["name"]:
                cls.stage = child
            elif child.name == CONFIG_DETECTOR["name"]:
                cls.detector = child
            elif child.name == CONFIG_CHAMBER["name"]:
                cls.chamber = child

    @classmethod
    def tearDownClass(cls):
        cls.detector.terminate()

    def setUp(self):
        # if self.chamber.position.value["vacuum"] > 100e-3:
        if self.microscope.get_vacuum_state() != 'vacuum':
            self.skipTest("Chamber needs to be in vacuum, please pump.")
        self.xt_type = "xttoolkit" if "xttoolkit" in self.microscope.swVersion.lower() else "xtlib"

    @unittest.skipIf(not TEST_NOHW, "Microscope stage not tested, too dangerous.")
    def test_move_stage(self):
        """
        Test that moving the microscope stage to a certain x, y, z position moves it to that position.
        """
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

    @unittest.skipIf(not TEST_NOHW, "Microscope stage not tested, too dangerous.")
    def test_move_stage_rot_tilt(self):
        """
        Test that moving the microscope stage to a certain t, r position moves it to that position.
        """
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

        # Relative move in rz < 0 => it should "wrap around" and report a value near 2pi.
        # (rx doesn't support full rotation, so we don't use it)
        f = self.stage.moveAbs({"rz": 0})
        rel_pos = {"rz": -0.5} # rad
        f = self.stage.moveRel(rel_pos)
        f.result()
        self.assertAlmostEqual(self.stage.position.value["rz"] % (2 * math.pi),
                               rel_pos["rz"] % (2 * math.pi), places=4)

        self.stage.moveAbs(init_pos)

    def test_hfov(self):
        """
        Test setting the horizontal field of view (aka the size, which can be scanned with the current settings).
        """
        ebeam = self.scanner
        orig_mag = ebeam.magnification.value
        orig_fov = ebeam.horizontalFoV.value

        ebeam.horizontalFoV.value = orig_fov / 2
        self.assertAlmostEqual(orig_mag * 2, ebeam.magnification.value)
        self.assertAlmostEqual(orig_fov / 2, ebeam.horizontalFoV.value, places=10)

        # Test setting the min and max
        fov_min = ebeam.horizontalFoV.range[0]
        fov_max = ebeam.horizontalFoV.range[1]
        ebeam.horizontalFoV.value = fov_min
        self.assertAlmostEqual(fov_min, ebeam.horizontalFoV.value, places=10)

        ebeam.horizontalFoV.value = fov_max
        self.assertAlmostEqual(fov_max, ebeam.horizontalFoV.value, places=10)

        # Reset
        ebeam.horizontalFoV.value = orig_fov
        self.assertAlmostEqual(orig_fov, ebeam.horizontalFoV.value, places=10)

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
        self.assertAlmostEqual(new_dwell_time, self.scanner.dwellTime.value, places=10)
        # Test it still works for different values.
        new_dwell_time = dwell_time_range[1] - 1.5e-6
        self.scanner.dwellTime.value = new_dwell_time
        self.assertAlmostEqual(new_dwell_time, self.scanner.dwellTime.value, places=10)
        # set dwellTime back to initial value
        self.scanner.dwellTime.value = init_dwell_time

    @unittest.skipIf(not TEST_NOHW, "Do not test setting voltage on the hardware.")
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

    def test_autoblanking(self):
        """Test that scanner automatically blanks after acquisition if the .blanker VA is set to None."""
        self.scanner.blanker.value = None
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        im = self.detector.data.get()
        self.assertEqual(self.scanner.parent.beam_is_blanked(), True)
        time.sleep(5)  # give settings updater time to update new blanker value
        self.assertEqual(self.scanner.blanker.value, None)  # .blanker.value should not change

    def test_rotation(self):
        """Test setting the rotation."""
        init_rotation = self.scanner.rotation.value
        self.scanner.rotation.value += 0.01
        self.assertEqual(self.scanner.rotation.value, init_rotation + 0.01)
        self.scanner.rotation.value = init_rotation

    def test_external(self):
        """Test setting the scan mode to external."""
        init_external = self.scanner.external.value
        self.scanner.external.value = True
        self.assertEqual(self.microscope.get_scan_mode(), "external")
        self.scanner.external.value = False
        self.assertEqual(self.microscope.get_scan_mode(), "full_frame")
        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_scan_mode("external")
        time.sleep(6)
        self.assertTrue(self.scanner.external.value)
        # set the value back to its initial value.
        self.scanner.external.value = init_external

    def test_scale(self):
        scale_orig = self.scanner.scale.value

        # Pixel size should always be square
        pxs = self.scanner.pixelSize.value
        self.assertEqual(pxs[0], pxs[1])

        # scale * n => res / n
        self.scanner.scale.value = (1, 1)
        res_1 = self.scanner.resolution.value
        for s in self.scanner.scale.choices:
            self.assertEqual(s[0], s[1])
            self.scanner.scale.value = s
            self.assertEqual(self.scanner.scale.value, s)
            self.assertAlmostEqual(res_1[0] / s[0], self.scanner.resolution.value[0])
            self.assertIn(self.scanner.resolution.value, self.scanner.resolution.choices)

        # Check that if the server has the resolution changed, the resolution and scale VAs are updated
        self.scanner.scale.value = (1, 1)
        res_2 = tuple(r // 2 for r in res_1)
        self.microscope.set_resolution(res_2)
        time.sleep(6)  # Long enough for the settings to be updated
        self.assertEqual(res_2, self.scanner.resolution.value)
        self.assertEqual((2, 2), self.scanner.scale.value)

        with self.assertRaises(IndexError):
            self.scanner.scale.value = (1.123, 1.234)

        self.scanner.scale.value = scale_orig

    def _compute_expected_duration(self):
        """Computes the expected duration of a single image acquisition."""
        dwell = self.scanner.dwellTime.value
        settle = 5.e-6
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

    def test_acquire(self):
        """Test acquiring an image using the Detector."""
        init_dwell_time = self.scanner.dwellTime.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        expected_duration = self._compute_expected_duration()
        start = time.time()
        im = self.detector.data.get()
        duration = time.time() - start
        self.assertEqual(im.shape, self.scanner.resolution.value[::-1])

        if TEST_NOHW != 'sim':
            # The basic simulator does not simulate the right execution time.
            self.assertGreaterEqual(
                duration,
                expected_duration,
                "Error execution took %f s, less than exposure time %d." % (duration, expected_duration)
            )
        self.assertIn(model.MD_DWELL_TIME, im.metadata)
        # Set back dwell time to initial value
        self.scanner.dwellTime.value = init_dwell_time

    def _get_scale(self, res):
        """
        return (float, float): the scale needed to get the given resolution
        """
        max_res = self.scanner.shape
        return (max_res[0] / res[0],) * 2

    def test_stop_acquisition(self):
        """Test stopping the acquisition of an image using the Detector."""
        init_dwell_time = self.scanner.dwellTime.value
        init_scale = self.scanner.scale.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        # Set resolution to a high value for a long acquisition that will be stopped.
        self.scanner.scale.value = self._get_scale((3072, 2048))
        self.assertEqual(self.scanner.resolution.value, (3072, 2048))
        self.detector.data.subscribe(self.receive_data)
        self.detector.data.unsubscribe(self.receive_data)
        # Set resolution to a low value for a quick acquisition.
        self.scanner.scale.value = self._get_scale((768, 512))
        self.assertEqual(self.scanner.resolution.value, (768, 512))
        im = self.detector.data.get()
        # Check that the acquired image has the last set resolution.
        self.assertEqual(im.shape, self.scanner.resolution.value[::-1])
        self.assertIn(model.MD_DWELL_TIME, im.metadata)
        # Set back scale/resolution and dwell time to initial values
        self.scanner.dwellTime.value = init_dwell_time
        self.scanner.scale.value = init_scale

    def test_live_change(self):
        """Test changing the resolution while the acquisition is running."""
        init_dwell_time = self.scanner.dwellTime.value
        init_scale = self.scanner.scale.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        # Set resolution, acquire an image and check it has the correct resolution.
        self.scanner.scale.value = self._get_scale((1536, 1024))
        self.assertEqual(self.scanner.resolution.value, (1536, 1024))
        self.detector.data.subscribe(self.receive_data)
        im = self.detector.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[::-1])
        # Change resolution and check if the acquired image has the new resolution.
        self.scanner.scale.value = self._get_scale((768, 512))
        self.assertEqual(self.scanner.resolution.value, (768, 512))
        im = self.detector.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[::-1])
        self.detector.data.unsubscribe(self.receive_data)
        # Set back scale/resolution and dwell time to initial values
        self.scanner.dwellTime.value = init_dwell_time
        self.scanner.scale.value = init_scale

    def receive_data(self, dataflow, image):
        """Callback for dataflow of acquisition tests."""
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        dataflow.unsubscribe(self.receive_data)

    @unittest.skipIf(not TEST_NOHW, "Microscope chamber not tested, too dangerous.")
    def test_chamber(self):
        # Only via XTToolKit (MBScanner)
        # self.scanner.power.value = False

        self.chamber.stop()

        prev_press = None
        for vac in self.chamber.axes["vacuum"].choices.keys():
            f = self.chamber.moveAbs({"vacuum": vac})
            f.result()
            self.assertEqual(self.chamber.position.value["vacuum"], vac)
            press = self.chamber.pressure.value
            self.assertNotEqual(prev_press, press)

    def test_apply_auto_contrast_brightness(self):
        """
        Test for the auto contrast brightness functionality.
        """
        self.scanner.blanker.value = False
        # Start auto contrast brightness and check if it is running.
        auto_contrast_brightness_future = self.detector.applyAutoContrastBrightness()
        auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(self.scanner.channel)
        time.sleep(0.01)
        self.assertEqual(auto_contrast_brightness_state, True)
        self.assertIsInstance(auto_contrast_brightness_future, ProgressiveFuture)

        # Stop auto auto contrast brightness and check if it stopped running.
        auto_contrast_brightness_future.cancel()
        time.sleep(1.0)  # Give microscope/simulator the time to update the state
        auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(self.scanner.channel)
        self.assertEqual(auto_contrast_brightness_state, False)
        self.assertIsInstance(auto_contrast_brightness_future, ProgressiveFuture)

        # Test starting auto contrast brightness and cancelling directly
        auto_contrast_brightness_future = self.detector.applyAutoContrastBrightness()
        auto_contrast_brightness_future.cancel()
        time.sleep(1.0)  # Give microscope/simulator the time to update the state
        auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(self.scanner.channel)
        self.assertEqual(auto_contrast_brightness_state, False)
        self.assertIsInstance(auto_contrast_brightness_future, ProgressiveFuture)

        # Start auto contrast brightness
        max_execution_time = 60  # Approximately 3 times the normal expected execution time (s)
        starting_time = time.time()
        auto_contrast_brightness_future = self.detector.applyAutoContrastBrightness()
        time.sleep(0.5)  # Give microscope/simulator the time to update the state
        # Wait until the auto contrast brightness is finished
        auto_contrast_brightness_future.result(timeout=max_execution_time)

        # Check if the time to perform the auto contrast brightness is not too long
        self.assertLess(time.time() - starting_time, max_execution_time,
                        "Execution of auto contrast brightness was stopped because it took more than %s seconds."
                        % max_execution_time)

        auto_contrast_brightness_state = self.microscope.is_running_auto_contrast_brightness(self.scanner.channel)
        self.assertEqual(auto_contrast_brightness_state, False)
        self.scanner.blanker.value = True

    def test_apply_autofocus(self):
        """
        Test for the auto functionality of the autofocus.
        """
        self.scanner.blanker.value = False
        # Start auto focus and check if it is running.
        autofocus_future = self.efocus.applyAutofocus(self.detector)
        autofocus_state = self.microscope.is_autofocusing(self.scanner.channel)
        self.assertEqual(autofocus_state, True)
        self.assertIsInstance(autofocus_future, ProgressiveFuture)

        # Stop auto focus and check if it stopped running.
        autofocus_future.cancel()
        time.sleep(1.0)  # Give microscope/simulator the time to update the state
        autofocus_state = self.microscope.is_autofocusing(self.scanner.channel)
        self.assertEqual(autofocus_state, False)
        self.assertIsInstance(autofocus_future, ProgressiveFuture)

        # Test starting auto focus and cancelling directly
        autofocus_future = self.efocus.applyAutofocus(self.detector)
        autofocus_future.cancel()
        time.sleep(1.0)  # Give microscope/simulator the time to update the state
        autofocus_state = self.microscope.is_autofocusing(self.scanner.channel)
        self.assertEqual(autofocus_state, False)
        self.assertIsInstance(autofocus_future, ProgressiveFuture)

        # Start autofocus
        max_execution_time = 40  # Approximately 3 times the normal expected execution time (s)
        starting_time = time.time()
        autofocus_future = self.efocus.applyAutofocus(self.detector)
        time.sleep(0.5)  # Give microscope/simulator the time to update the state
        autofocus_future.result(timeout=max_execution_time)  # Wait until the autofocus is finished

        # Check if the time to perform the autofocus is not too long
        self.assertLess(time.time() - starting_time, max_execution_time,
                        "Execution autofocus was stopped because it took more than %s seconds."
                        % max_execution_time)

        autofocus_state = self.microscope.is_autofocusing(self.scanner.channel)
        self.assertEqual(autofocus_state, False)
        self.scanner.blanker.value = True

    def test_set_beam_shift(self):
        """Setting the beam shift."""
        init_beam_shift = self.scanner.beamShift.value
        beam_shift_range = self.scanner.beamShift.range
        new_beam_shift_x = beam_shift_range[1][0] - 1e-6
        new_beam_shift_y = beam_shift_range[0][1] + 1e-6
        self.scanner.beamShift.value = (new_beam_shift_x, new_beam_shift_y)
        time.sleep(0.1)
        testing.assert_tuple_almost_equal((new_beam_shift_x, new_beam_shift_y), self.scanner.beamShift.value)
        # Test it still works for different values.
        new_beam_shift_x = beam_shift_range[0][0] + 1e-6
        new_beam_shift_y = beam_shift_range[1][1] - 1e-6
        self.scanner.beamShift.value = (new_beam_shift_x, new_beam_shift_y)
        testing.assert_tuple_almost_equal((new_beam_shift_x, new_beam_shift_y), self.scanner.beamShift.value)
        # set beamShift back to initial value
        self.scanner.beamShift.value = init_beam_shift

    def test_contrast(self):
        """Test setting the contrast."""
        init_contrast = self.detector.contrast.value
        self.detector.contrast.value += 0.01
        self.assertEqual(self.detector.contrast.value, init_contrast + 0.01)
        self.detector.contrast.value = init_contrast

    def test_brightness(self):
        """Test setting the brightness."""
        init_brightness = self.detector.brightness.value
        self.detector.brightness.value += 0.01
        self.assertEqual(self.detector.brightness.value, init_brightness + 0.01)
        self.detector.brightness.value = init_brightness

    @unittest.skipIf(TEST_NOHW, 'Contrast and brightness do not change on the simulated hardware.')
    def test_brightness_contrast(self):
        """Test that changing the contrast and brightness updates the image correctly."""
        # Test that for brightness and contrast 1 all values in the image are equal to 255
        init_brightness = self.detector.brightness.value
        init_contrast = self.detector.contrast.value
        self.detector.brightness = 1
        self.detector.contrast = 1
        image = self.microscope.get_latest_image(self.scanner.channel)
        numpy.testing.assert_array_equal(image, 255)

        # Test that decreasing the brightness results in a darker image
        self.detector.brightness = 0.5
        darker_image = self.microscope.get_latest_image(self.scanner.channel)
        self.assertGreater(numpy.sum(image), numpy.sum(darker_image))

        # Test that for brightness and contrast 0 all values in the image are equal to 0
        self.detector.brightness = 0
        self.detector.contrast = 0
        image = self.microscope.get_latest_image(self.scanner.channel)
        numpy.testing.assert_array_equal(image, 0)

        # Test that increasing the brightness and contrast results in a lighter image
        self.detector.brightness = 0.5
        self.detector.contrast = 0.5
        lighter_image = self.microscope.get_latest_image(self.scanner.channel)
        self.assertGreater(numpy.sum(lighter_image), numpy.sum(image))

        # Set back initial values
        self.detector.brightness.value = init_brightness
        self.detector.contrast.value = init_contrast


class TestMicroscopeInternal(unittest.TestCase):
    """
    Test calling the internal of the Microscope client class directly.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No simulator available.")

        cls.microscope = xt_client.SEM(**CONFIG_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    def setUp(self):
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
        self.assertAlmostEqual(self.microscope.get_scanning_size()[0], new_scanfield_x, places=10)
        # Test it still works for different values.
        new_scanfield_x = scanfield_range['x'][0]
        self.microscope.set_scanning_size(new_scanfield_x)
        self.assertAlmostEqual(self.microscope.get_scanning_size()[0], new_scanfield_x, places=10)
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
        with self.assertRaises((OSError, ValueError)):
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

    @unittest.skipIf(not TEST_NOHW, "Microscope stage not tested, too dangerous.")
    def test_home_stage(self):
        """Test that the stage is homed after home_stage is called."""
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
        self.microscope.set_channel_state(name='optical4', state=False)
        self.microscope.wait_for_state_changed(xt_client.XT_STOP,
                                               name='optical4')  # timeout is handled on the server side
        image = self.microscope.get_latest_image(channel_name='optical4')
        self.assertEqual(len(image.shape), 2)

    def test_set_beam_shift(self):
        """Setting the beam shift."""
        init_beam_shift = self.microscope.get_beam_shift()
        new_beam_shift_x = self.microscope.get_beam_shift()[0] - 20e-6
        beam_shift_range = self.microscope.beam_shift_info()['range']
        if new_beam_shift_x < beam_shift_range['x'][0]:
            new_beam_shift_x = beam_shift_range['x'][0] + 20e-6
        elif new_beam_shift_x > beam_shift_range['x'][1]:
            new_beam_shift_x = beam_shift_range['x'][1] - 20e-6

        new_beam_shift_y = self.microscope.get_beam_shift()[1] - 20e-6
        if new_beam_shift_y < beam_shift_range['y'][0]:
            new_beam_shift_y = beam_shift_range['y'][0] + 20e-6
        elif new_beam_shift_y > beam_shift_range['y'][1]:
            new_beam_shift_y = beam_shift_range['y'][1] - 20e-6
        self.microscope.set_beam_shift(new_beam_shift_x, new_beam_shift_y)
        current_shift = self.microscope.get_beam_shift()
        self.assertAlmostEqual(new_beam_shift_x, current_shift[0])
        self.assertAlmostEqual(new_beam_shift_y, current_shift[1])
        # Set beam shift back to initial value
        self.microscope.set_beam_shift(*init_beam_shift)

    @unittest.skipIf(not TEST_NOHW, "Before running this test make sure it is safe to turn on the beam.")
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

    @unittest.skipIf(not TEST_NOHW, "Before running this test make sure it is safe to turn on the beam.")
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

    @unittest.skipIf(not TEST_NOHW, "Before running this test make sure it is safe to turn on the beam.")
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

    def test_delta_pitch(self):
        """Test setting and getting the delta pitch."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        delta_pitch_range = self.microscope.delta_pitch_info()["range"]
        current_delta_pitch = self.microscope.get_delta_pitch()
        new_delta_pitch = current_delta_pitch + 1
        new_delta_pitch = new_delta_pitch if delta_pitch_range[0] < new_delta_pitch < delta_pitch_range[1] else \
            current_delta_pitch - 1
        self.microscope.set_delta_pitch(new_delta_pitch)
        self.assertAlmostEqual(new_delta_pitch, self.microscope.get_delta_pitch())
        self.microscope.set_delta_pitch(current_delta_pitch)

    def test_stigmator(self):
        """Test setting and getting stigmator values."""
        stig_range = self.microscope.stigmator_info()["range"]
        init_stig = self.microscope.get_stigmator()
        self.assertIsInstance(init_stig, tuple)
        stig_x, stig_y = (init_stig[0] + 1e-3, init_stig[1] + 1e-3)
        stig_x = stig_x if stig_range['x'][0] < stig_x <= stig_range['x'][1] else init_stig[0] - 1e-6
        stig_y = stig_y if stig_range['y'][0] < stig_y <= stig_range['y'][1] else init_stig[1] - 1e-6
        self.microscope.set_stigmator(stig_x, stig_y)
        self.assertEqual(self.microscope.get_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_stigmator()[1], stig_y)
        # Try to set value outside of range
        with self.assertRaises((OSError, ValueError)):
            self.microscope.set_stigmator(stig_range['x'][1] + 1e-3, stig_range['y'][1] + 1e-3)
        self.assertEqual(self.microscope.get_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_stigmator()[1], stig_y)
        # Set back to the initial stigmator value, so the system is not misaligned after finishing the test.
        self.microscope.set_stigmator(*init_stig)

    def test_secondary_stigmator(self):
        """Test getting and setting the secondary stigmator."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        stig_range = self.microscope.secondary_stigmator_info()["range"]
        init_stig = self.microscope.get_secondary_stigmator()
        self.assertIsInstance(init_stig, tuple)
        stig_x, stig_y = (init_stig[0] + 1e-3, init_stig[1] + 1e-3)
        stig_x = stig_x if stig_range['x'][0] < stig_x <= stig_range['x'][1] else init_stig[0] - 1e-6
        stig_y = stig_y if stig_range['y'][0] < stig_y <= stig_range['y'][1] else init_stig[1] - 1e-6
        self.microscope.set_secondary_stigmator(stig_x, stig_y)
        self.assertEqual(self.microscope.get_secondary_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_secondary_stigmator()[1], stig_y)
        # Try to set value outside of range
        with self.assertRaises((OSError, ValueError)):
            self.microscope.set_secondary_stigmator(stig_range['x'][1] + 1e-3, stig_range['y'][1] + 1e-3)
        self.assertEqual(self.microscope.get_secondary_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_secondary_stigmator()[1], stig_y)
        # Set back to the initial stigmator value, so the system is not misaligned after finishing the test.
        self.microscope.set_secondary_stigmator(*init_stig)

    def test_pattern_stigmator(self):
        """Test getting and setting the secondary stigmator."""
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")
        stig_range = self.microscope.pattern_stigmator_info()["range"]
        init_stig = self.microscope.get_pattern_stigmator()
        self.assertIsInstance(init_stig, tuple)
        stig_x, stig_y = (init_stig[0] + 1e-3, init_stig[1] + 1e-3)
        stig_x = stig_x if stig_range['x'][0] < stig_x <= stig_range['x'][1] else init_stig[0] - 1e-6
        stig_y = stig_y if stig_range['y'][0] < stig_y <= stig_range['y'][1] else init_stig[1] - 1e-6
        self.microscope.set_pattern_stigmator(stig_x, stig_y)
        self.assertEqual(self.microscope.get_pattern_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_pattern_stigmator()[1], stig_y)
        # Try to set value outside of range
        with self.assertRaises((OSError, ValueError)):
            self.microscope.set_pattern_stigmator(stig_range['x'][1] + 1e-3, stig_range['y'][1] + 1e-3)
        self.assertEqual(self.microscope.get_pattern_stigmator()[0], stig_x)
        self.assertEqual(self.microscope.get_pattern_stigmator()[1], stig_y)
        # Set back to the initial stigmator value, so the system is not misaligned after finishing the test.
        self.microscope.set_pattern_stigmator(*init_stig)

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

    def test_set_resolution(self):
        """Test changing the scale and resolution"""
        init_resolution = self.microscope.get_resolution()
        for res in self.scanner.resolution.choices:
            self.microscope.set_resolution(res)
            self.assertEqual(res, self.microscope.get_resolution())

        # set resolution back to initial value
        self.microscope.set_resolution(init_resolution)

    def test_get_mpp_orientation(self):
        """
        Test getting the multiprobe pattern orientation.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        mpp_orientation = self.microscope.get_mpp_orientation()
        self.assertIsInstance(mpp_orientation, float)
        self.assertTrue(-90 < mpp_orientation < 90)

    def test_mpp_orientation_info(self):
        """
        Test getting the multiprobe pattern orientation info.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        mpp_orientation_info = self.microscope.mpp_orientation_info()
        self.assertIsInstance(mpp_orientation_info, dict)
        self.assertTrue("unit" in mpp_orientation_info)
        self.assertEqual(len(mpp_orientation_info["range"]), 2)

    def test_get_aperture_index(self):
        """
        Test getting the aperture index.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        aperture_index = self.microscope.get_aperture_index()
        self.assertIsInstance(aperture_index, int)
        self.assertTrue(0 <= aperture_index <= 14)

    def test_set_aperture_index(self):
        """
        Test setting the aperture index.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        current_aperture_index = self.microscope.get_aperture_index()
        aperture_range = tuple(self.microscope.aperture_index_info()["range"])
        new_aperture_index = current_aperture_index + 1 if current_aperture_index < aperture_range[1] else \
            current_aperture_index - 1

        self.microscope.set_aperture_index(new_aperture_index)
        aperture_index = self.microscope.get_aperture_index()
        self.assertEqual(aperture_index, new_aperture_index)

        self.microscope.set_aperture_index(current_aperture_index)
        aperture_index = self.microscope.get_aperture_index()
        self.assertEqual(aperture_index, current_aperture_index)

    def test_aperture_index_info(self):
        """
        Test getting the aperture index info.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        aperture_index_info = self.microscope.aperture_index_info()
        self.assertIsInstance(aperture_index_info, dict)
        self.assertEqual(len(aperture_index_info["range"]), 2)

    def test_get_beamlet_index(self):
        """
        Test getting the beamlet index.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        beamlet_index = self.microscope.get_beamlet_index()
        self.assertIsInstance(beamlet_index, tuple)
        self.assertEqual(len(beamlet_index), 2)
        self.assertTrue(1 <= beamlet_index[0] <= 8)
        self.assertTrue(1 <= beamlet_index[1] <= 8)

    def test_set_beamlet_index(self):
        """
        Test setting the beamlet index.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        current_index = self.microscope.get_beamlet_index()
        # Make sure both the x and the y value are changed.
        new_x_beamlet_index = 3 if current_index[0] != 3 else 6
        new_y_beamlet_index = 5 if current_index[1] != 5 else 7
        new_beamlet_index = (new_x_beamlet_index, new_y_beamlet_index)

        self.microscope.set_beamlet_index(new_beamlet_index)
        beamlet_index = self.microscope.get_beamlet_index()
        self.assertEqual(beamlet_index, new_beamlet_index)

        self.microscope.set_beamlet_index(current_index)
        beamlet_index = self.microscope.get_beamlet_index()
        self.assertEqual(beamlet_index, current_index)

    def test_beamlet_index_info(self):
        """
        Test getting the beamlet index info.
        """
        if self.xt_type != 'xttoolkit':
            self.skipTest("This test needs XTToolkit to run.")

        beamlet_index_info = self.microscope.beamlet_index_info()
        self.assertIsInstance(beamlet_index_info, dict)
        self.assertIsInstance(beamlet_index_info["range"], dict)
        self.assertIsInstance(beamlet_index_info["range"]["x"], list)
        self.assertIsInstance(beamlet_index_info["range"]["y"], list)
        self.assertEqual(len(beamlet_index_info["range"]["x"]), 2)
        self.assertEqual(len(beamlet_index_info["range"]["y"]), 2)

    def test_contrast(self):
        """Test setting and getting the contrast."""
        init_contrast = self.microscope.get_contrast()
        new_contrast = init_contrast + 0.01
        contrast_range = self.microscope.contrast_info()["range"]
        if new_contrast < contrast_range[0]:
            new_contrast = contrast_range[0] + 0.01
        elif new_contrast > contrast_range[1]:
            new_contrast = contrast_range[1] - 0.01
        self.microscope.set_contrast(new_contrast)
        self.assertAlmostEqual(new_contrast, self.microscope.get_contrast())
        # Set dwell time back to initial value
        self.microscope.set_contrast(init_contrast)

    def test_brightness(self):
        """Test setting and getting the brigthness."""
        init_brightness = self.microscope.get_brightness()
        new_brightness = init_brightness + 0.01
        brightness_range = self.microscope.brightness_info()["range"]
        if new_brightness < brightness_range[0]:
            new_brightness = brightness_range[0] + 0.01
        elif new_brightness > brightness_range[1]:
            new_brightness = brightness_range[1] - 0.01
        self.microscope.set_brightness(new_brightness)
        self.assertAlmostEqual(new_brightness, self.microscope.get_brightness())
        # Set dwell time back to initial value
        self.microscope.set_brightness(init_brightness)


class TestFIBScanner(unittest.TestCase):
    """
    Test the FIB Scanner class.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No simulator available.")

        cls.microscope = xt_client.SEM(**CONFIG_FIB_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_FIB_SCANNER["name"]:
                cls.fib_scanner = child
            elif child.name == CONFIG_DETECTOR["name"]:
                cls.detector = child

    def setUp(self):
        if self.microscope.get_vacuum_state() != 'vacuum':
            self.skipTest("Chamber needs to be in vacuum, please pump.")

    def test_acquire(self):
        """Test acquiring an image from the FIB/ion2 channel."""
        image = self.detector.data.get()
        self.assertEqual(image.ndim, 2)
        # Check that image size is at least 200*200 pixels
        self.assertGreaterEqual(image.shape[0], 200)
        self.assertGreaterEqual(image.shape[1], 200)


class TestDualModeMicroscope(unittest.TestCase):
    """
    Test the SEM using both the FIB and the ebeam.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No simulator available.")

        cls.microscope = xt_client.SEM(**CONFIG_DUAL_MODE_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FIB_SCANNER["name"]:
                cls.fib_scanner = child
            elif child.name == CONFIG_DETECTOR["name"]:
                cls.detector = child

    def setUp(self):
        if self.microscope.get_vacuum_state() != 'vacuum':
            self.skipTest("Chamber needs to be in vacuum, please pump.")

    def _compute_expected_duration(self):
        """Computes the expected duration of a single image acquisition."""
        dwell = self.scanner.dwellTime.value
        settle = 5.e-6
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

    def _callback_counter(self, dataflow, image):
        self.assertEqual(image.ndim, 2)
        # Check that image size is at least 200*200 pixels
        self.assertGreaterEqual(image.shape[0], 200)
        self.assertGreaterEqual(image.shape[1], 200)
        self.images_received += 1

    def test_scanner_VA(self):
        """Tests the scanner VA and its corresponding setter"""
        scanner_modes = {self.scanner.name: self.scanner, self.fib_scanner.name: self.fib_scanner}
        self.assertEqual(set(self.detector.scanner.choices), set(scanner_modes))

        # Loop over scan modes twice to make sure also the switch from fib to ebeam work properly
        for _ in range(2):
            for scanner_mode in scanner_modes:
                # Switch active scanner mode
                self.detector.scanner.value = scanner_mode
                self.assertEqual(self.detector.scanner.value, scanner_mode)

        # Check if mode remains unchanged if any non existing mode is tried to be set
        with self.assertRaises(IndexError):
            self.detector.scanner.value = "non existing mode"
        # The mode should be equal to the last set correct mode.
        self.assertEqual(self.detector.scanner.value, scanner_mode)

    def test_subscribing_while_changing_scanner(self):
        """
        Subscribes to the dataflow then switches between both scanner types and then checks if an image is received
        while subscribing to that type of scanner (FIB <--> ebeam)
        """
        self.images_received = 0
        self.detector.data.subscribe(self._callback_counter)

        scanner_modes = {self.scanner.name: self.scanner,
                         self.fib_scanner.name: self.fib_scanner}

        # Loop over scan modes twice to make sure also the switch from fib to ebeam work properly.
        for _ in range(2):
            for scanner_mode in scanner_modes:
                # Switch active scanner mode
                self.detector.scanner.value = scanner_mode
                # Set image counter to zero to check that new images are received after switching
                self.images_received = 0
                self.assertEqual(self.detector.scanner.value, scanner_mode)
                self.assertIsInstance(self.detector._scanner, type(scanner_modes[scanner_mode]))
                time.sleep(2)
                self.assertGreaterEqual(self.images_received, 1)

        self.detector.data.unsubscribe(self._callback_counter)  # Unsubscribe to stop refreshing the image

    def test_acquire_FIB_image(self):
        """Test acquiring an image from the FIB/ion channel."""
        # Switch to ion mode
        self.detector.scanner.value = self.fib_scanner.name
        image = self.detector.data.get()
        self.assertEqual(image.ndim, 2)
        # Check that image size is at least 200*200 pixels
        self.assertGreaterEqual(image.shape[0], 200)
        self.assertGreaterEqual(image.shape[1], 200)

    def test_acquire_ebeam_image(self):
        """Test acquiring an image using the Detector."""
        # Switch to electron mode
        self.detector.scanner.value = self.scanner.name
        init_dwell_time = self.scanner.dwellTime.value
        self.scanner.dwellTime.value = 25e-9  # s
        expected_duration = self._compute_expected_duration()
        start = time.time()
        im = self.detector.data.get()
        duration = time.time() - start
        self.assertEqual(im.shape, self.scanner.resolution.value[::-1])

        if TEST_NOHW != 'sim':
            # The basic simulator does not simulate the right execution time.
            self.assertGreaterEqual(
                duration,
                expected_duration,
                "Error execution took %f s, less than exposure time %d." % (duration, expected_duration)
            )
        self.assertIn(model.MD_DWELL_TIME, im.metadata)
        self.assertEqual(im.metadata[model.MD_DWELL_TIME], self.scanner.dwellTime.value)
        expected_pixel_size = numpy.array(self.scanner.pixelSize.value) * numpy.array(self.scanner.scale.value)
        self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][0], expected_pixel_size[0])
        self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE][1], expected_pixel_size[1])
        # Set back dwell time to initial value
        self.scanner.dwellTime.value = init_dwell_time


class TestMBScanner(unittest.TestCase):
    """
    Test the Scanner class, its methods, and the VA's it has.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No simulator available.")

        cls.microscope = xt_client.SEM(**CONFIG_MB_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_MB_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child

    def setUp(self):
        if self.microscope.get_vacuum_state() != 'vacuum':
            self.skipTest("Chamber needs to be in vacuum, please pump.")

        if "xttoolkit" in self.microscope.swVersion.lower():
            self.xt_type = "xttoolkit"
        else:
            raise TypeError("Xttoolkit must be running for this test.")

    def test_type_scanner_child(self):
        # Check if the scanner class is of the correct type
        self.assertIsInstance(self.scanner, xt_client.MultiBeamScanner)

        # Check if the xt_client scanner class is correctly overwritten and does not exist as a child anymore.
        for child in self.microscope.children.value:
            self.assertIsNot(type(child), xt_client.Scanner)

    def test_delta_pitch_VA(self):
        current_value = self.scanner.deltaPitch.value
        # Test if directly changing the value via the VA works. Not always will the entirety of the range be
        # allowed. Negative delta pitch is limited by the voltage it can apply. Therefore the max range and the 0
        # value is tested.
        for test_delta_pitch in (0.0, self.scanner.deltaPitch.range[1]):
            self.scanner.deltaPitch.value = test_delta_pitch
            self.assertEqual(test_delta_pitch, self.scanner.deltaPitch.value)
            self.assertEqual(test_delta_pitch, self.microscope.get_delta_pitch() * 1e-6)

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.deltaPitch.value = 1.2 * self.scanner.deltaPitch.range[1]
        self.assertEqual(test_delta_pitch, self.microscope.get_delta_pitch() * 1e-6)

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_delta_pitch(0.5 * self.scanner.deltaPitch.range[1] * 1e6)
        time.sleep(6)
        self.assertEqual(0.5 * self.scanner.deltaPitch.range[1], self.scanner.deltaPitch.value)

        self.scanner.deltaPitch.value = current_value

    def test_beam_stigmator_VA(self):
        current_value = self.scanner.beamStigmator.value
        # Test if directly changing it via the VA works
        for test_stigmator_value in self.scanner.beamStigmator.range:
            self.scanner.beamStigmator.value = test_stigmator_value
            self.assertEqual(test_stigmator_value, tuple(self.microscope.get_stigmator()))

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.beamStigmator.value = tuple(1.2 * i for i in self.scanner.beamStigmator.range[1])
        self.assertEqual(test_stigmator_value, tuple(self.microscope.get_stigmator()))

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_stigmator(0, 0)
        time.sleep(6)
        self.assertEqual((0, 0), tuple(self.scanner.beamStigmator.value))

        self.scanner.beamStigmator.value = current_value

    def test_pattern_stigmator_VA(self):
        current_value = self.scanner.patternStigmator.value

        # Test if directly changing it via the VA works
        for test_stigmator_value in self.scanner.patternStigmator.range:
            self.scanner.patternStigmator.value = test_stigmator_value
            self.assertEqual(test_stigmator_value, tuple(self.microscope.get_pattern_stigmator()))

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.patternStigmator.value = tuple(1.2 * i for i in self.scanner.patternStigmator.range[1])
        self.assertEqual(test_stigmator_value, tuple(self.microscope.get_pattern_stigmator()))

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_pattern_stigmator(0, 0)
        time.sleep(6)
        self.assertEqual((0, 0), tuple(self.scanner.patternStigmator.value))

        self.scanner.patternStigmator.value = current_value

    def test_beam_shift_transformation_matrix_VA(self):
        beamShiftTransformationMatrix = self.scanner.beamShiftTransformationMatrix
        self.assertIsInstance(beamShiftTransformationMatrix.value, list)
        self.assertEqual(len(beamShiftTransformationMatrix.value), 4)
        for row_transformation_matrix in beamShiftTransformationMatrix.value:
            self.assertIsInstance(row_transformation_matrix, list)
            self.assertEqual(len(row_transformation_matrix), 2)
            self.assertIsInstance(row_transformation_matrix[0], float)
            self.assertIsInstance(row_transformation_matrix[1], float)

        # Check if VA is read only
        with self.assertRaises(NotSettableError):
            self.scanner.beamShiftTransformationMatrix.value = beamShiftTransformationMatrix

    def test_multiprobe_rotation_VA(self):
        mpp_rotation = self.scanner.multiprobeRotation.value
        self.assertIsInstance(mpp_rotation, float)
        # Currently the range of the value can be quite big due to different designs for microscopes.
        self.assertGreaterEqual(mpp_rotation, - math.radians(90))
        self.assertLessEqual(mpp_rotation, math.radians(90))

        # Check if VA is read only
        with self.assertRaises(NotSettableError):
            self.scanner.multiprobeRotation.value = mpp_rotation

    def test_aperture_index_VA(self):
        current_value = self.scanner.apertureIndex.value

        # Test if directly changing it via the VA works
        for test_aperture_index in self.scanner.apertureIndex.range:
            self.scanner.apertureIndex.value = test_aperture_index
            self.assertEqual(test_aperture_index, self.microscope.get_aperture_index())

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.apertureIndex.value = 1.2 * self.scanner.apertureIndex.range[1]
        self.assertEqual(test_aperture_index, self.microscope.get_aperture_index())

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_aperture_index(0)
        time.sleep(6)
        self.assertEqual(0, self.scanner.apertureIndex.value)

        self.scanner.apertureIndex.value = current_value

    def test_beamlet_index_VA(self):
        current_value = self.scanner.beamletIndex.value

        # Test if directly changing it via the VA works
        for test_beamlet_index in self.scanner.beamletIndex.range:
            self.scanner.beamletIndex.value = test_beamlet_index
            self.assertEqual(test_beamlet_index, self.microscope.get_beamlet_index())

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.beamletIndex.value = tuple(int(2 * i) for i in self.scanner.beamletIndex.range[1])
        self.assertEqual(test_beamlet_index, self.microscope.get_beamlet_index())

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_beamlet_index(self.scanner.beamletIndex.range[0])
        time.sleep(6)
        self.assertEqual(self.scanner.beamletIndex.range[0], self.scanner.beamletIndex.value)

        self.scanner.beamletIndex.value = current_value
        self.assertEqual(current_value, self.microscope.get_beamlet_index())
        self.assertEqual(current_value, self.scanner.beamletIndex.value)

    def test_immersion_VA(self):
        current_value = self.scanner.immersion.value  # type: bool

        # Set current_value as last, so that it goes back to current value after the test
        for new_val in (not current_value, current_value):
            fov_range_prev = self.scanner.horizontalFoV.range[1]

            # Test if directly changing it via the VA works
            self.scanner.immersion.value = new_val
            self.assertEqual(self.scanner.immersion.value, new_val)
            # FoV should change
            self.assertNotEqual(fov_range_prev, self.scanner.horizontalFoV.range[1])

            # Check it's still correct after updating the settings
            time.sleep(6)
            self.assertEqual(self.scanner.immersion.value, new_val)
            self.assertNotEqual(fov_range_prev, self.scanner.horizontalFoV.range[1])

    def test_multiprobe_mode_VA(self):
        current_beam_mode = self.scanner.multiBeamMode.value
        current_aperture_index = self.scanner.apertureIndex.value
        current_beamlet_index = self.scanner.beamletIndex.value

        # Test multiple time to see if the change from True --> False and the other way around succeeds also if it is
        # preceded by switch in beam mode via the VA.
        for multi_beam_boolean in [True, False, True, False, True]:
            self.scanner.multiBeamMode.value = multi_beam_boolean
            self.assertEqual(self.scanner.multiBeamMode.value, multi_beam_boolean)
            self.assertEqual(self.microscope.get_use_case(), 'MultiBeamTile' if multi_beam_boolean else 'SingleBeamlet')
            # Check if aperture and beamlet index do not change while switching beam modes.
            self.assertEqual(self.microscope.get_aperture_index(), current_aperture_index)
            self.assertEqual(self.microscope.get_beamlet_index(), current_beamlet_index)

        # Test if the value is automatically updated when the value is not changed via the VA (and end the test with
        # the beam turned off)
        self.microscope.set_use_case('SingleBeamlet')
        time.sleep(8)
        self.assertEqual(False, self.scanner.multiBeamMode.value)

        self.scanner.multiBeamMode.value = current_beam_mode

    @unittest.skipIf(not TEST_NOHW, "Before running this test make sure it is safe to turn on the beam.")
    def test_beam_powerVA(self):
        """Test getting and setting the beam power through the VA."""
        init_beam_power = self.scanner.power.value
        self.scanner.power.value = True
        self.assertTrue(self.microscope.get_beam_is_on())
        self.scanner.power.value = False
        self.assertFalse(self.microscope.get_beam_is_on())
        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_beam_power(True)
        time.sleep(6)
        self.assertTrue(self.scanner.power.value)
        self.scanner.power.value = init_beam_power

    def test_apply_autostigmator(self):
        """
        Test for the auto stigmation functionality.
        """
        autostigmator_future = self.scanner.applyAutoStigmator()
        self.assertIsInstance(autostigmator_future, ProgressiveFuture)
        autostigmator_future.result(timeout=30)


if __name__ == '__main__':
    unittest.main()
