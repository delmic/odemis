#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 10 Dec 2024

Copyright © 2024 Delmic

This file is part of Odemis

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
import math

import numpy
from odemis.driver.autoscript_client import (
    IMAGING_STATE_IDLE,
    IMAGING_STATE_RUNNING,
    MILLING_STATE_IDLE,
    MILLING_STATE_PAUSED,
    MILLING_STATE_RUNNING,
    SEM, Scanner, Stage, Detector, Focus
)
from odemis import model

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Accept three values for TEST_NOHW
# * TEST_NOHW = 1: not connected to anything => skip most of the tests
# * TEST_NOHW = sim: xtadapter/server_autoscript.py sim running on localhost
# * TEST_NOHW = 0 (or anything else): connected to the real hardware
TEST_NOHW = os.environ.get("TEST_NOHW", "sim")  # Default to sim testing

if TEST_NOHW == "sim":
    pass
elif TEST_NOHW == "0":
    TEST_NOHW = False
elif TEST_NOHW == "1":
    TEST_NOHW = True
else:
    raise ValueError("Unknown value of environment variable TEST_NOHW=%s" % TEST_NOHW)


# arguments used for the creation of basic components
CONFIG_SEM_SCANNER = {"name": "Electron-Beam", "role": "e-beam", "hfw_nomag": 0.25}
CONFIG_SEM_DETECTOR = {"name": "Electron-Detector", "role": "se-detector"}
CONFIG_SEM_FOCUS = {"name": "Electron-Focus", "role": "ebeam-focus"}
CONFIG_FIB_SCANNER = {"name": "Ion-Beam", "role": "ion-beam", "hfw_nomag": 0.25}
CONFIG_FIB_DETECTOR = {"name": "Ion-Detector", "role": "se-detector-ion"}
CONFIG_FIB_FOCUS = {"name": "Ion-Focus", "role": "ion-focus"}
CONFIG_STAGE = {"name": "Stage", "role": "stage-bare"} # MD?

CONFIG_FIBSEM = {"name": "FIBSEM", "role": "fibsem",
                 "address": "192.168.6.5",
                 "port": 4242,
                 "children": {
                    "sem-scanner": CONFIG_SEM_SCANNER,
                    "sem-detector": CONFIG_SEM_DETECTOR,
                    "sem-focus": CONFIG_SEM_FOCUS,
                    "fib-scanner": CONFIG_FIB_SCANNER,
                    "fib-detector": CONFIG_FIB_DETECTOR,
                    "fib-focus": CONFIG_FIB_FOCUS,
                    "stage": CONFIG_STAGE,
                 }}

if TEST_NOHW == "sim":
    CONFIG_FIBSEM["address"] = "localhost"

CHANNELS = ["electron", "ion"]

class TestMicroscope(unittest.TestCase):
    """
    Test communication with the server using the Microscope client class.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No simulator available.")

        cls.microscope = SEM(**CONFIG_FIBSEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_SEM_SCANNER["name"]:
                cls.scanner: Scanner = child
            elif child.name == CONFIG_SEM_DETECTOR["name"]:
                cls.detector: Detector = child
            elif child.name == CONFIG_SEM_FOCUS["name"]:
                cls.efocus: Focus = child
            elif child.name == CONFIG_STAGE["name"]:
                cls.stage: Stage = child

    @classmethod
    def tearDownClass(cls):
        cls.microscope.terminate()

    def setUp(self):
        pass

    def test_software_version(self):
        sw_version = self.microscope.get_software_version()
        self.assertTrue(isinstance(sw_version, str))
        self.assertTrue("autoscript" in sw_version.lower())

    def test_hardware_version(self):
        hw_version = self.microscope.get_hardware_version()
        self.assertTrue(isinstance(hw_version, str))
        self.assertTrue("delmic" in hw_version.lower())

### STAGE
    def test_stage_movement(self):
        """Test the stage movement functions."""
        pos = self.microscope.get_stage_position()
        self.assertTrue(isinstance(pos, dict))
        # assert all keys
        required_keys = ["x", "y", "z", "r", "t"]
        self.assertTrue(all(k in pos for k in required_keys))

        # get stage info/limits
        stage_info = self.microscope.stage_info()
        self.assertTrue(isinstance(stage_info, dict))
        # assert all keys
        required_keys = ["unit", "range"]
        self.assertTrue(all(k in stage_info for k in required_keys))
        # assert all range is dict with required keys and tuple with two floats
        for k, v in stage_info["range"].items():
            self.assertTrue(len(v) == 2)
            self.assertTrue(isinstance(v[0], float))
            self.assertTrue(isinstance(v[1], float))

        # small relative movement (x, y)
        move = {"x": 5e-6, "y": 5e-6}
        pos1 = self.microscope.get_stage_position()
        self.microscope.move_stage_relative(move)
        pos2 = self.microscope.get_stage_position()
        for k in ["x", "y"]:
            self.assertAlmostEqual(pos1[k] + move[k], pos2[k], delta=1e-6)

        # absolute movement
        self.microscope.move_stage_absolute(pos1)
        pos3 = self.microscope.get_stage_position()
        # assert all keys
        required_keys = ["x", "y", "z", "r", "t"]
        for k in required_keys:
            self.assertAlmostEqual(pos1[k], pos3[k], delta=1e-6)

        # stop movement
        self.microscope.move_stage_relative(move)
        # check no error is raised
        # Note: no way to check if stage is_moving...
        self.microscope.stop_stage_movement()

    @unittest.skipIf(not TEST_NOHW, "Unsafe to test on real hardware.")
    def test_advanced_stage_movement(self):
        # test link/unlink
        self.microscope.link(False)
        self.assertFalse(self.microscope.is_linked())
        self.microscope.link(True)
        self.assertTrue(self.microscope.is_linked())

        # test home
        self.microscope.home_stage()
        self.assertTrue(self.microscope.is_homed())

        # test coordinate system
        self.microscope.set_default_stage_coordinate_system("Specimen")
        cs = self.microscope.get_stage_coordinate_system()
        self.assertEqual(cs, "Specimen".upper())
        self.microscope.set_default_stage_coordinate_system("Raw")
        cs = self.microscope.get_stage_coordinate_system()
        self.assertEqual(cs, "Raw".upper())

    def test_scan_mode(self):
        # test scan modes for both channels

        for ch in CHANNELS:
            mode = "crossover"
            self.microscope.set_scan_mode(mode=mode, channel=ch)
            self.assertEqual(self.microscope.get_scan_mode(channel=ch), mode)

            mode = "external"
            self.microscope.set_scan_mode(mode=mode, channel=ch)
            self.assertEqual(self.microscope.get_scan_mode(channel=ch), mode)

            mode = "full_frame"
            self.microscope.set_scan_mode(mode=mode, channel=ch)
            self.assertEqual(self.microscope.get_scan_mode(channel=ch), mode)

            mode = "line"
            self.microscope.set_scan_mode(mode=mode, value=50, channel=ch)
            self.assertEqual(self.microscope.get_scan_mode(channel=ch), mode)

            mode = "reduced_area"
            self.microscope.set_scan_mode(
                mode=mode,
                value={"top": 0, "left": 0, "width": 0.5, "height": 0.5},
                channel=ch,
            )
            self.assertEqual(self.microscope.get_scan_mode(channel=ch), mode)

            mode = "spot"
            self.microscope.set_scan_mode(
                mode=mode, value={"x": 0.5, "y": 0.5}, channel=ch
            )
            self.assertEqual(self.microscope.get_scan_mode(channel=ch), mode)

            with self.assertRaises(IOError):
                mode = "invalid"
                self.microscope.set_scan_mode(mode, channel=ch)

            info = self.microscope.scan_mode_info()
            self.assertTrue(isinstance(info, list))

            # reset to full frame
            self.microscope.set_scan_mode(mode="full_frame", channel=ch)

    def test_spot_size(self):
        # test spot size for both channels

        for ch in CHANNELS:
            size = self.microscope.get_spotsize(channel=ch)  # get current size
            self.microscope.set_spotsize(spotsize=size, channel=ch)
            self.assertEqual(self.microscope.get_spotsize(channel=ch), size)

            info = self.microscope.spotsize_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range[0], (int, float)))

            # check setting at range
            self.microscope.set_spotsize(spotsize=_range[0], channel=ch)
            self.assertAlmostEqual(self.microscope.get_spotsize(channel=ch), _range[0])
            self.microscope.set_spotsize(spotsize=_range[1], channel=ch)
            # self.assertAlmostEqual(self.microscope.get_spotsize(channel=ch), _range[1])

            # reset to original size
            self.microscope.set_spotsize(spotsize=size, channel=ch)

    def test_dwell_time(self):
        # test dwell time for both channels

        for ch in CHANNELS:
            dwell_time = (
                self.microscope.get_dwell_time(channel=ch) + 0.2e-9
            )  # get current time
            self.microscope.set_dwell_time(dwell_time=dwell_time, channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_dwell_time(channel=ch), dwell_time
            )

            info = self.microscope.dwell_time_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range[0], (int, float)))
            self.assertTrue(isinstance(_range[1], (int, float)))

            # check setting at range
            self.microscope.set_dwell_time(dwell_time=_range[0], channel=ch)
            self.assertAlmostEqual(self.microscope.get_dwell_time(channel=ch), _range[0])
            self.microscope.set_dwell_time(dwell_time=_range[1], channel=ch)
            self.assertAlmostEqual(self.microscope.get_dwell_time(channel=ch), _range[1])

            # reset to original time
            self.microscope.set_dwell_time(dwell_time=dwell_time, channel=ch)

    def test_field_of_view(self):
        # test field of view for both channels

        for ch in CHANNELS:
            fov = self.microscope.get_field_of_view(channel=ch)
            self.microscope.set_field_of_view(field_of_view=fov, channel=ch)
            self.assertAlmostEqual(self.microscope.get_field_of_view(channel=ch), fov)

            info = self.microscope.field_of_view_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range[0], float))
            self.assertTrue(isinstance(_range[1], float))

            # check setting at range
            self.microscope.set_field_of_view(field_of_view=_range[0], channel=ch)
            self.assertAlmostEqual(self.microscope.get_field_of_view(channel=ch), _range[0])
            self.microscope.set_field_of_view(field_of_view=_range[1], channel=ch)
            self.assertAlmostEqual(self.microscope.get_field_of_view(channel=ch), _range[1])

            # reset to original fov
            self.microscope.set_field_of_view(field_of_view=fov, channel=ch)

    def test_high_voltage(self):
        # test high voltage for both channels

        for ch in CHANNELS:
            hv = self.microscope.get_high_voltage(channel=ch)
            self.microscope.set_high_voltage(voltage=hv, channel=ch)
            self.assertAlmostEqual(self.microscope.get_high_voltage(channel=ch), hv)

            info = self.microscope.high_voltage_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range[0], (int, float)))
            self.assertTrue(isinstance(_range[1], (int, float)))

            # check setting at range
            self.microscope.set_high_voltage(voltage=_range[0], channel=ch)
            self.assertAlmostEqual(self.microscope.get_high_voltage(channel=ch), _range[0])
            self.microscope.set_high_voltage(voltage=_range[1], channel=ch)
            self.assertAlmostEqual(self.microscope.get_high_voltage(channel=ch), _range[1])

            # reset to original voltage
            self.microscope.set_high_voltage(voltage=hv, channel=ch)

    def test_beam_current(self):
        # test beam current for both channels

        for ch in CHANNELS:
            current = self.microscope.get_beam_current(channel=ch)
            self.microscope.set_beam_current(current=current, channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_beam_current(channel=ch), current
            )

            info = self.microscope.beam_current_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range[0], (int, float)))
            self.assertTrue(isinstance(_range[1], (int, float)))

            # check setting at range
            self.microscope.set_beam_current(current=_range[0], channel=ch)
            self.assertAlmostEqual(self.microscope.get_beam_current(channel=ch), _range[0])
            self.microscope.set_beam_current(current=_range[1], channel=ch)
            self.assertAlmostEqual(self.microscope.get_beam_current(channel=ch), _range[1])

            # reset to original current
            self.microscope.set_beam_current(current=current, channel=ch)

    def test_working_distance(self):
        # test working distance for both channels

        for ch in CHANNELS:
            wd = self.microscope.get_working_distance(channel=ch)
            self.microscope.set_working_distance(working_distance=wd, channel=ch)
            self.assertAlmostEqual(self.microscope.get_working_distance(channel=ch), wd)

            info = self.microscope.working_distance_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range[0], (int, float)))
            self.assertTrue(isinstance(_range[1], (int, float)))

            # check setting at range
            self.microscope.set_working_distance(working_distance=_range[0], channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_working_distance(channel=ch), _range[0]
            )
            self.microscope.set_working_distance(working_distance=_range[1], channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_working_distance(channel=ch), _range[1]
            )

            # reset to original working distance
            self.microscope.set_working_distance(working_distance=wd, channel=ch)


    def test_stigmator(self):
        # test stigmator for both channels

        for ch in CHANNELS:
            stig = self.microscope.get_stigmator(channel=ch)
            self.microscope.set_stigmator(x=stig[0], y=stig[1], channel=ch)
            self.assertEqual(self.microscope.get_stigmator(channel=ch), stig)

            info = self.microscope.stigmator_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range, dict))
            xrange = _range["x"]
            yrange = _range["y"]

            self.assertTrue(isinstance(xrange[0], float))
            self.assertTrue(isinstance(yrange[0], float))

            # check setting at range
            self.microscope.set_stigmator(x=xrange[0], y=yrange[0], channel=ch)
            self.assertAlmostEqual(self.microscope.get_stigmator(channel=ch), (xrange[0], yrange[0]))
            self.microscope.set_stigmator(x=xrange[1], y=yrange[1], channel=ch)
            self.assertAlmostEqual(self.microscope.get_stigmator(channel=ch), (xrange[1], yrange[1]))

            # reset to original stigmator
            self.microscope.set_stigmator(x=stig[0], y=stig[1], channel=ch)

    def test_beam_shift(self):
        # test beam shift for both channels

        for ch in CHANNELS:
            shift = self.microscope.get_beam_shift(channel=ch)
            self.microscope.set_beam_shift(
                x=shift[0], y=shift[1], channel=ch
            )
            self.assertEqual(self.microscope.get_beam_shift(channel=ch), shift)

            # test move_beam_shift
            new_shift = (0.1e-6, 0.1e-6)
            # set to zero
            self.microscope.set_beam_shift(x=0, y=0, channel=ch)
            self.microscope.move_beam_shift(
                x=new_shift[0], y=new_shift[1], channel=ch
            )
            self.assertEqual(self.microscope.get_beam_shift(channel=ch), new_shift)

            info = self.microscope.beam_shift_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            xrange, yrange = _range["x"], _range["y"]
            self.assertTrue(isinstance(_range, dict))
            self.assertTrue(isinstance(xrange[0], float))
            self.assertTrue(isinstance(yrange[0], float))

            # check setting at range
            self.microscope.set_beam_shift(x=xrange[0], y=yrange[0], channel=ch)
            self.assertEqual(self.microscope.get_beam_shift(channel=ch), (xrange[0], yrange[0]))
            self.microscope.set_beam_shift(x=xrange[1], y=yrange[1], channel=ch)
            self.assertEqual(self.microscope.get_beam_shift(channel=ch), (xrange[1], yrange[1]))

            # reset to original beam shift
            self.microscope.set_beam_shift(x=shift[0], y=shift[1], channel=ch)

    def test_scan_rotation(self):
        # test scan rotation for both channels

        for ch in CHANNELS:
            angle = self.microscope.get_scan_rotation(channel=ch)
            self.microscope.set_scan_rotation(rotation=math.radians(0), channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_scan_rotation(channel=ch), math.radians(0)
            )
            # restore to angle
            self.microscope.set_scan_rotation(rotation=angle, channel=ch)

            info = self.microscope.scan_rotation_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range[0], (int, float)))
            self.assertTrue(isinstance(_range[1], (int, float)))

            # check setting at range
            self.microscope.set_scan_rotation(rotation=math.radians(_range[0]), channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_scan_rotation(channel=ch), math.radians(_range[0])
            )
            self.microscope.set_scan_rotation(rotation=math.radians(_range[1]), channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_scan_rotation(channel=ch), math.radians(_range[1])
            )

            # reset to original rotation
            self.microscope.set_scan_rotation(rotation=angle, channel=ch)

    def test_resolution(self):
        # test resolution for both channels

        for ch in CHANNELS:
            res = self.microscope.get_resolution(channel=ch)
            self.microscope.set_resolution(resolution=res, channel=ch)
            self.assertAlmostEqual(self.microscope.get_resolution(channel=ch), res)

            info = self.microscope.resolution_info(channel=ch)
            self.assertTrue(isinstance(info, dict))
            _range = info["range"]
            self.assertTrue(isinstance(_range, list))

            # check setting at range
            self.microscope.set_resolution(resolution=_range[0], channel=ch)
            self.assertEqual(self.microscope.get_resolution(channel=ch), tuple(_range[0]))
            self.microscope.set_resolution(resolution=_range[1], channel=ch)
            self.assertEqual(self.microscope.get_resolution(channel=ch), tuple(_range[1]))

            # reset to original resolution
            self.microscope.set_resolution(resolution=res, channel=ch)

    def test_beam_on(self):
        # test beam on/off for both channels

        for ch in CHANNELS:
            self.microscope.set_beam_power(state=False, channel=ch)
            self.assertFalse(self.microscope.get_beam_is_on(channel=ch))
            self.microscope.set_beam_power(state=True, channel=ch)
            self.assertTrue(self.microscope.get_beam_is_on(channel=ch))

    def test_beam_blanking(self):
        # test beam blanking for both channels
        for ch in CHANNELS:
            self.microscope.blank_beam(channel=ch)
            self.assertTrue(self.microscope.beam_is_blanked(channel=ch))
            self.microscope.unblank_beam(channel=ch)
            self.assertFalse(self.microscope.beam_is_blanked(channel=ch))

    ### DETECTOR

    def test_detectors(self):
        mode = "SecondaryElectrons"
        _type = "ETD"
        brightness = 0.5
        contrast = 0.5

        for ch in CHANNELS:
            self.microscope.set_detector_mode(mode=mode, channel=ch)
            self.microscope.set_detector_type(detector_type=_type, channel=ch)
            self.microscope.set_brightness(brightness=brightness, channel=ch)
            self.microscope.set_contrast(contrast=contrast, channel=ch)
            self.assertEqual(self.microscope.get_detector_mode(channel=ch), mode)
            self.assertEqual(self.microscope.get_detector_type(channel=ch), _type)
            self.assertAlmostEqual(
                self.microscope.get_brightness(channel=ch), brightness
            )
            self.assertAlmostEqual(self.microscope.get_contrast(channel=ch), contrast)

            contrast_info = self.microscope.contrast_info(channel=ch)
            self.assertTrue(isinstance(contrast_info, dict))
            self.assertTrue(isinstance(contrast_info["range"], list))

            # check setting at range
            self.microscope.set_contrast(contrast=contrast_info["range"][0], channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_contrast(channel=ch), contrast_info["range"][0]
            )
            self.microscope.set_contrast(contrast=contrast_info["range"][1], channel=ch)
            self.assertAlmostEqual(
                self.microscope.get_contrast(channel=ch), contrast_info["range"][1]
            )

            brightness_info = self.microscope.brightness_info(channel=ch)
            self.assertTrue(isinstance(brightness_info, dict))
            self.assertTrue(isinstance(brightness_info["range"], list))

            # check setting at range
            self.microscope.set_brightness(
                brightness=brightness_info["range"][0], channel=ch
            )
            self.assertAlmostEqual(
                self.microscope.get_brightness(channel=ch), brightness_info["range"][0]
            )
            self.microscope.set_brightness(
                brightness=brightness_info["range"][1], channel=ch
            )
            self.assertAlmostEqual(
                self.microscope.get_brightness(channel=ch), brightness_info["range"][1]
            )

            # reset to original values
            self.microscope.set_detector_mode(mode=mode, channel=ch)
            self.microscope.set_detector_type(detector_type=_type, channel=ch)
            self.microscope.set_brightness(brightness=brightness, channel=ch)
            self.microscope.set_contrast(contrast=contrast, channel=ch)


    def test_scanning_filter(self):
        scan_filter = 2  # averaging
        n_frames = 3
        # test scanning filter for both channels
        for ch in CHANNELS:
            self.microscope.set_scanning_filter(
                filter_type=scan_filter, n_frames=n_frames, channel=ch
            )
            filt = self.microscope.get_scanning_filter(channel=ch)
            self.assertEqual(filt["filter_type"], scan_filter)
            self.assertEqual(filt["n_frames"], n_frames)

            # get info
            info = self.microscope.get_scanning_filter_info(channel=ch)
            self.assertTrue(isinstance(info, list))
            self.assertTrue(len(info) > 0)

            for v in info:
                n_frames = 1 if v == 1 else 3
                self.microscope.set_scanning_filter(channel=ch, filter_type=v, n_frames=1)

            # restore to 1 (None)
            self.microscope.set_scanning_filter(filter_type=1, n_frames=1, channel=ch)

    ### IMAGING

    def test_active_view(self):
        for view in [1, 2]:
            self.microscope.set_active_view(view=view)
            self.assertEqual(self.microscope.get_active_view(), view)

    def test_active_device(self):
        for dev in [1, 2]:
            self.microscope.set_active_device(device=dev)
            self.assertEqual(self.microscope.get_active_device(), dev)

    def test_acquire_image(self):
        # test acquire image for both channels
        for ch in CHANNELS:
            # set resolution
            res = (3072, 2048)
            self.microscope.set_resolution(resolution=res, channel=ch)
            image, md = self.microscope.acquire_image(channel=ch)
            self.assertTrue(isinstance(image, numpy.ndarray))
            self.assertTrue(isinstance(md, dict))
            self.assertEqual(image.shape, res[::-1])

            # get last image
            image = self.microscope.get_last_image(channel=ch)
            self.assertTrue(isinstance(image, numpy.ndarray))
            self.assertEqual(image.shape, res[::-1])

    def test_acquisition(self):
        for ch in CHANNELS:
            # start acquisition
            self.microscope.start_acquisition(channel=ch)

            # check imaging state
            imaging_state = self.microscope.get_imaging_state(channel=ch)
            self.assertEqual(imaging_state, IMAGING_STATE_RUNNING)

            # stop acquisition
            self.microscope.stop_acquisition(channel=ch)

            # check imaging state
            imaging_state = self.microscope.get_imaging_state(channel=ch)
            self.assertEqual(imaging_state, IMAGING_STATE_IDLE)

    ### AUTOFUNCTIONS

    def test_autofunctions(self):
        # test autofunctions for both channels
        for ch in CHANNELS:
            # check no error is raised
            self.microscope.run_auto_contrast_brightness(channel=ch, parameters={})

    ### PATTERNING

    def test_set_default_patterning_parameters(self):
        # test setting default patterning parameters
        self.microscope.set_default_patterning_beam_type("ion")
        self.microscope.set_default_application_file("Si")
        app_files = self.microscope.get_available_application_files()
        self.assertTrue("Si" in app_files)

    def test_rectangle(self):
        parameters = {
            "center_x": 0,
            "center_y": 0,
            "width": 10e-6,
            "height": 10e-6,
            "depth": 1e-6,
        }
        self.microscope.create_rectangle(parameters=parameters)

    def test_cleaning_cross_section(self):
        parameters = {
            "center_x": 0,
            "center_y": 0,
            "width": 10e-6,
            "height": 10e-6,
            "depth": 1e-6,
        }
        self.microscope.create_cleaning_cross_section(parameters=parameters)

    def test_regular_cross_section(self):
        parameters = {
            "center_x": 0,
            "center_y": 0,
            "width": 10e-6,
            "height": 10e-6,
            "depth": 1e-6,
        }
        self.microscope.create_regular_cross_section(parameters=parameters)

    def test_line(self):
        parameters = {
            "start_x": 0,
            "start_y": 0,
            "end_x": 10e-6,
            "end_y": 10e-6,
            "depth": 1e-6,
        }
        self.microscope.create_line(parameters=parameters)

    def test_circle(self):
        parameters = {
            "center_x": 0,
            "center_y": 0,
            "inner_diameter": 0,
            "outer_diameter": 10e-6,
            "depth": 1e-6,
        }
        self.microscope.create_circle(parameters=parameters)

    def test_milling_workflow(self):
        # draw two rectangles
        parameters = {
            "center_x": 0,
            "center_y": 0,
            "width": 10e-6,
            "height": 10e-6,
            "depth": 1e-6,
        }
        self.microscope.create_rectangle(parameters=parameters)
        parameters = {
            "center_x": 0,
            "center_y": 0,
            "width": 5e-6,
            "height": 5e-6,
            "depth": 1e-6,
        }
        self.microscope.create_rectangle(parameters=parameters)

        # start milling
        self.microscope.start_milling()
        time.sleep(5)  # wait for milling to start
        self.assertEqual(self.microscope.get_patterning_state(), MILLING_STATE_RUNNING)

        # pause milling
        self.microscope.pause_milling()
        self.assertEqual(self.microscope.get_patterning_state(), MILLING_STATE_PAUSED)

        # resume milling
        self.microscope.resume_milling()
        self.assertEqual(self.microscope.get_patterning_state(), MILLING_STATE_RUNNING)

        # stop milling
        self.microscope.stop_milling()
        self.assertEqual(self.microscope.get_patterning_state(), MILLING_STATE_IDLE)

        # clear all patterns
        self.microscope.clear_patterns()

    def test_milling_time_estimate(self):
        self.microscope.clear_patterns()
        preset_time = 15
        parameters = {
            "center_x": 0,
            "center_y": 0,
            "width": 10e-6,
            "height": 10e-6,
            "depth": 1e-6,
            "time-real": preset_time,
            "time": preset_time,
        }
        self.microscope.create_rectangle(parameters=parameters)

        # get estimated time
        estimated_time = self.microscope.estimate_milling_time()
        self.assertAlmostEqual(estimated_time, preset_time)

        # clear patterns, check estimated time is zero
        self.microscope.clear_patterns()
        estimated_time = self.microscope.estimate_milling_time()
        self.assertAlmostEqual(estimated_time, 0)

#### SCANNER
    def test_scanner_component(self):
        # get initial values
        fov = self.scanner.horizontalFoV.value
        dwell_time = self.scanner.dwellTime.value
        beam_current = self.scanner.probeCurrent.value
        voltage = self.scanner.accelVoltage.value
        scan_rotation = self.scanner.rotation.value
        resolution = self.scanner.resolution.value

        # get new values (at range limit)
        fov_range = self.scanner.horizontalFoV.range
        dwell_time_range = self.scanner.dwellTime.range
        beam_current_range = self.scanner.probeCurrent.range
        voltage_range = self.scanner.accelVoltage.range
        new_fov = fov_range[0]
        new_dwell_time = dwell_time_range[0]
        new_beam_current = beam_current_range[0]
        new_voltage = voltage_range[0]
        new_scan_rotation = math.radians(180)
        new_resolution = (3072, 2048)
        new_pixel_size = (new_fov / new_resolution[0], new_fov / new_resolution[0])

        # set new values
        self.scanner.horizontalFoV.value = new_fov
        self.scanner.dwellTime.value = new_dwell_time
        self.scanner.probeCurrent.value = new_beam_current
        self.scanner.accelVoltage.value = new_voltage
        self.scanner.rotation.value = new_scan_rotation
        self.scanner.resolution.value = new_resolution

        # assert values
        self.assertAlmostEqual(self.scanner.horizontalFoV.value, new_fov)
        self.assertAlmostEqual(self.scanner.dwellTime.value, new_dwell_time)
        self.assertAlmostEqual(self.scanner.probeCurrent.value, new_beam_current)
        self.assertAlmostEqual(self.scanner.accelVoltage.value, new_voltage)
        self.assertAlmostEqual(self.scanner.rotation.value, new_scan_rotation)
        self.assertEqual(self.scanner.resolution.value, new_resolution)
        pixelsize = self.scanner.pixelSize.value
        scale = self.scanner.scale.value
        scaled_pixelsize = (scale[0] * pixelsize[0], scale[1] * pixelsize[1])
        self.assertEqual(scaled_pixelsize, new_pixel_size)
        self.assertEqual(self.scanner._metadata[model.MD_PIXEL_SIZE], new_pixel_size)

        # reset to original values
        self.scanner.horizontalFoV.value = fov
        self.scanner.dwellTime.value = dwell_time
        self.scanner.probeCurrent.value = beam_current
        self.scanner.accelVoltage.value = voltage
        self.scanner.rotation.value = scan_rotation
        self.scanner.resolution.value = resolution

        self.assertAlmostEqual(self.scanner.horizontalFoV.value, fov)
        self.assertAlmostEqual(self.scanner.dwellTime.value, dwell_time)
        self.assertAlmostEqual(self.scanner.probeCurrent.value, beam_current)
        self.assertAlmostEqual(self.scanner.accelVoltage.value, voltage)
        self.assertAlmostEqual(self.scanner.rotation.value, scan_rotation)
        self.assertEqual(self.scanner.resolution.value, resolution)

### DETECTOR
    def test_detector_component(self):
        # set median filter value
        median_filter = 3
        self.detector.medianFilter.value = median_filter

        # image acquisition
        image = self.detector.data.get()
        md = image.metadata
        self.assertEqual(md[model.MD_DATA_FILTER], f"median-filter:{median_filter}")

        req_keys = [model.MD_BEAM_DWELL_TIME, model.MD_BEAM_SCAN_ROTATION,
                    model.MD_BEAM_VOLTAGE, model.MD_BEAM_CURRENT, model.MD_BEAM_SHIFT,
                    model.MD_BEAM_FIELD_OF_VIEW, model.MD_ACQ_TYPE, model.MD_ACQ_DATE]
        self.assertTrue(all(k in md for k in req_keys))

        # no median filter
        self.detector.medianFilter.value = 0
        image = self.detector.data.get()
        md = image.metadata
        self.assertEqual(md[model.MD_DATA_FILTER], None)

### STAGE
    def test_stage_component(self):
        """Test the stage component functions."""
        pos = self.stage.position.value
        self.assertTrue(isinstance(pos, dict))
        # assert all keys
        required_keys = ["x", "y", "z", "rx", "rz"]
        self.assertTrue(all(k in pos for k in required_keys))

        # get stage info/limits
        stage_info = self.stage.axes
        self.assertTrue(isinstance(stage_info, dict))

        # assert all keys
        ax: model.Axis
        for k, ax in stage_info.items():
            self.assertTrue(isinstance(ax, model.Axis))
            self.assertTrue(isinstance(ax.unit, str))
            self.assertTrue(isinstance(ax.range, tuple))
            self.assertTrue(len(ax.range) == 2)
            self.assertTrue(isinstance(ax.range[0], float))
            self.assertTrue(isinstance(ax.range[1], float))

        # small relative movement (x, y)
        move = {"x": 5e-6, "y": 5e-6}
        pos1 = self.stage.position.value
        f = self.stage.moveRel(move)
        f.result()
        pos2 = self.stage.position.value
        for k in ["x", "y"]:
            self.assertAlmostEqual(pos1[k] + move[k], pos2[k], delta=1e-6)

        # absolute movement
        f =  self.stage.moveAbs(pos1)
        f.result()
        pos3 = self.stage.position.value
        # assert all keys
        for k in self.stage.axes.keys():
            self.assertAlmostEqual(pos1[k], pos3[k], delta=1e-6)

        # stop movement
        f = self.stage.moveRel(move)
        # check no error is raised
        # Note: no way to check if stage is_moving...
        self.stage.stop()

### FOCUS
    def test_focuser_component(self):

        # abs move
        wd = self.efocus.position.value
        f = self.efocus.moveAbs(wd)
        f.result()
        self.assertAlmostEqual(self.efocus.position.value, wd)

        # rel move
        wd = self.efocus.position.value
        move = {"z": 100e-6}
        f = self.efocus.moveRel(move)
        f.result()
        self.assertAlmostEqual(self.efocus.position.value["z"], wd["z"] + move["z"])

        # range
        info = self.efocus.axes
        self.assertTrue("z" in info) # only z-axes
        self.assertTrue(isinstance(info["z"], model.Axis))
        self.assertTrue(isinstance(info["z"].unit, str))
        self.assertTrue(isinstance(info["z"].range, tuple)) # (min, max)
        self.assertTrue(len(info["z"].range) == 2)
        self.assertTrue(isinstance(info["z"].range[0], (int,float)))
        self.assertTrue(isinstance(info["z"].range[1], (int,float)))
