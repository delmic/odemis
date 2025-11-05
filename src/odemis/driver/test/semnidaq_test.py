#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Jul 1, 2023

@author: Éric Piel

Copyright © 2023 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import sys
from unittest import SkipTest, skip

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

import threading
import time
import unittest
from typing import Tuple

import matplotlib
import matplotlib.pyplot as plt
import numpy
from odemis import model, util

try:
    from odemis.driver import semnidaq
    from odemis.driver.semnidaq import Acquirer
except ModuleNotFoundError:
    # Failure to load the module typically indicate some packages are missing, but let's nicely skip the tests in such case
    semnidaq = None

matplotlib.use("Gtk3Agg")

# Note: if testing on a PCIe-6251, you need to change the value below,
# and disable all "image_ttl".
# The PCIe-6251 has a precision of 50ns
# DWELL_TIME_PRECISION = 50e-9  # s
# The PCIe-6361 has 20ns
DWELL_TIME_PRECISION = 20e-9  # s

CONFIG_SED = {
    "name": "sed",
    "role": "sed",
    "channel": 0,
    # "channel": "ao0",  # Loopback from the AO0, for testing
    "limits": [-3, 6.2]
}

CONFIG_CLD = {
    "name": "cld",
    "role": "cld",
    "channel": 1,
    # "channel": "ao1",  # Loopback from the AO1, for testing
    "limits": [3, -3],  # Data inverted
    # Cannot be used as standard board only has 8 channels, and they are all used
    # "active_ttl": {
    #     5: [True, False, "protection"],  # high when active, low when protection is True
    # },
}

CONFIG_BSD = {
    "name": "bsd",
    "role": "bsd",
    "channel": 2,
    # "channel": "ao1",  # Loopback from the AO1, for testing
    "limits": [-4, 4]
}

CONFIG_CNT = {
    "name": "counter",
    "role": "counter",
    "source": 8,  # PFI8
    "active_ttl": {
        5: [False, True, "protection"],  # low when active, high when protection is True
    },
    "activation_delay": 500e-9,  # s
}

CONFIG_SCANNER = {
    "name": "scanner",
    "role": "ebeam",
    "channels": [0, 1],
    "limits": [[-3, 6], [2, 1.2]],  # Y goes inverted
    "park": [8, -8],
    # "max_res": [4096, 3072],  # 4:3 ratio
    "max_res": [8192, 6144],  # 4:3 ratio
    "settle_time": 10e-6,  # s
    "scan_active_delay": 0.1,  # s
    "hfw_nomag": 0.112,
    "scanning_ttl": {
        4: [True, True, None],
        2: [True, True, "external"],  # High when scanning, High when VA set to True
        3: [False, True, "blanker"],  # Low when scanning, High when VA set to True
    },
    "image_ttl": {
        "pixel": {
             "ports": [0, 7],
             "inverted": [False, True],
             "affects": ["IR Camera", "UV Camera"],
        },
        "line": {
             "ports": [1],
             # "inverted": [True],
        },
        "frame": {
             "ports": [6],
             # "inverted": [True],
        },
    },
}

# For loop-back testing (currently on the breadboard):
# AO0 -> AI0
# DO0.0 (aka 0) -> AI1

CONFIG_SEM = {
    "name": "sem",
    "role": "sem",
    "device": "Dev1",
    "multi_detector_min_period": 1e-6,  # s, smaller is "harder" for the hardware
    "children": {
        "scanner": CONFIG_SCANNER,
        "detector0": CONFIG_SED,
        "detector1": CONFIG_CLD,
        "detector2": CONFIG_BSD,
        "counter0": CONFIG_CNT,
    }
}


class EventReceiver:
    """
    Helper class to receive model.Events
    """
    def __init__(self):
        self.count = 0

    def onEvent(self):
        logging.debug("Received an event")
        self.count += 1


class TestAnalogSEM(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        if not semnidaq:
            raise SkipTest("semnidaq driver is not available. Check if python3-nidaqmx is installed.")

        cls.sem = semnidaq.AnalogSEM(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_CLD["name"]:
                cls.cld = child
            elif child.name == CONFIG_BSD["name"]:
                cls.bsd = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_CNT["name"]:
                cls.counter = child

        # Compute the fast TTL masks, for the waveform checks
        pixel_ttls = CONFIG_SCANNER["image_ttl"]["pixel"]
        pixel_ttls_conf = list(zip(pixel_ttls["ports"], pixel_ttls["inverted"]))
        cls.pixel_bit = sum(1 << c for c, inv in pixel_ttls_conf if not inv)
        cls.pixel_bit_inv = sum(1 << c for c, inv in pixel_ttls_conf if inv)
        cls.line_bit = sum(1 << c for c in CONFIG_SCANNER["image_ttl"]["line"]["ports"])
        cls.frame_bit = sum(1 << c for c in CONFIG_SCANNER["image_ttl"]["frame"]["ports"])


    @classmethod
    def tearDownClass(cls) -> None:
        cls.sem.terminate()

    def setUp(self) -> None:
        # Start with basic good default values
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]  # s
        self.scanner.scale.value = (8, 8)  # => res is 8x8 smaller than max res
        self.scanner.resolution.value = self.scanner.resolution.range[1]  # max res, limited to the scale (so, max / 8)
        self.scanner.scanPath.value = None
        self.scanner.scanPixelTTL.value = None
        self.scanner.scanLineTTL.value = None
        self.scanner.scanFrameTTL.value = None

        # Reset the metadata
        self.scanner.updateMetadata({model.MD_POS: (0, 0)})

        # for receive_image()
        self.expected_shape = tuple(self.scanner.resolution.value[::-1])
        self.left = 1
        self.acq_dates = []  # floats
        self.im_received = threading.Event()  # Set for each image received
        self.acq_done = threading.Event()  # Set once all the images have been received
        self.left2 = 1
        self.acq_dates2 = []  # floats
        self.acq_done2 = threading.Event()
        self.das = []  # DataArrays

    def compute_expected_metadata(self) -> Tuple[Tuple[int, int], Tuple[float, float], float]:
        exp_shape = self.scanner.resolution.value[::-1]
        scanner_pxs = self.scanner.pixelSize.value
        scale = self.scanner.scale.value
        exp_pxs = tuple(p * s for p, s in zip(scanner_pxs, scale))
        exp_duration = self.scanner.dwellTime.value * numpy.prod(exp_shape)
        return exp_shape, exp_pxs, exp_duration

    def test_simple(self):
        self.assertEqual(self.scanner.resolution.range[1], tuple(CONFIG_SCANNER["max_res"]))

    def test_ttl_vas(self):
        time.sleep(1)  # Wait a little bit to make sure the scan state manager is running (usually takes < 0.1s)

        # default value should be None
        self.assertEqual(self.scanner.external.value, None)
        self.assertEqual(self.scanner.blanker.value, None)

        # Manually set to a value
        # We cannot check so much (as it only changes the TTL output... and prints a log message)
        self.scanner.external.value = True
        self.assertEqual(self.scanner.external.value, True)

        self.scanner.blanker.value = False
        self.assertEqual(self.scanner.blanker.value, False)

        logging.debug("Setting the TTL VAs to automatic")
        # Set them back to automatic (None)
        # As we are not scanning, that should put the external (channel 2) to False, and blanker (channel 3) to True
        self.scanner.external.value = None
        self.scanner.blanker.value = None
        self.assertEqual(self.scanner.external.value, None)
        self.assertEqual(self.scanner.blanker.value, None)

    def plot_small_waveform(self):
        """
        Extra debug function to visualise the waveforms
        """
        scanner = self.scanner

        # Dwell time might not be exactly accepted as-is depending on the hardware
        scanner.dwellTime.value = 2e-6  # s

        # Reduce the number of pixels, while keeping the full FoV
        scanner.scale.value = (300, 384)  # => res = 13x8
        res = scanner.resolution.value
        print(res)

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin, is_vector_scan = self.scanner._get_scan_waveforms(1)

        plt.plot(scan_array[0], label="X")  # X voltage
        plt.plot(scan_array[1], label="Y")  # Y voltage
        ttl_clock = numpy.arange(ttl_array.shape[-1]) * 0.5  # The clock for the TTL is twice faster than the analog out

        ttl_pixel = (ttl_array & self.pixel_bit) != 0
        ttl_pixel_inv = (ttl_array & self.pixel_bit_inv) != 0
        ttl_line = (ttl_array & self.line_bit) != 0
        ttl_frame = (ttl_array & self.frame_bit) != 0
        plt.plot(ttl_clock, ttl_pixel * 10000 - 51000, label="pixel TTL")
        plt.plot(ttl_clock, ttl_pixel_inv * 10000 - 51000, label="pixel inv TTL")
        plt.plot(ttl_clock, ttl_line * 10000 - 40000, label="line TTL")
        plt.plot(ttl_clock, ttl_frame * 10000 - 29000, label="frame TTL")
        # plt.plot(scan_array[0].flat, scan_array[1].flat, "ro") # X vs Y -> should show the whole grid of points
        plt.xlabel('pixel')
        plt.ylabel('voltage')
        plt.show()

    def test_waveform_2d(self):
        """
        Check the waveform (analog + TTL) generated for a "standard" 2D scan
        :return:
        """
        scanner = self.scanner

        # Dwell time might not be exactly accepted as-is depending on the hardware
        scanner.dwellTime.value = 1e-6  # s
        dt = scanner.dwellTime.value
        self.assertTrue(0.5e-6 <= dt <= 1.5e-6)

        # Reduce the number of pixels, while keeping the full FoV
        scanner.scale.value = (16, 8)  # 16, 8
        scale = scanner.scale.value
        self.assertEqual(scale, (16, 8))
        scanner.resolution.value = scanner.resolution.range[1]  # Force it to be the largest
        res = scanner.resolution.value

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin, is_vector_scan = self.scanner._get_scan_waveforms(1)

        exp_margin = int(CONFIG_SCANNER["settle_time"] / dt)  # True if the whole FoV is scanned
        self.assertEqual(exp_margin, margin)
        self.assertEqual(res, act_res)
        self.assertEqual(dt, act_dt)
        self.assertEqual(ao_osr, 1)

        exp_length = (res[0] + margin) * res[1]

        # Check the analog voltages (XY position)
        self.assertEqual(scan_array[0].size, exp_length)
        self.assertEqual(scan_array[1].size, exp_length)
        # The beginning in X should correspond to the lowest (that's the flyback of the first line)
        min_x = scan_array[0].min()
        self.assertEqual(scan_array[0][0], min_x)
        # The end in X should correspond to the highest (that's the end of the last line)
        # Note, that assumes there is no margin on the right to smooth the flyback
        max_x = scan_array[0].max()
        self.assertEqual(scan_array[0][-1], max_x)

        # The beginning in Y should correspond to the highest (as the voltage limits are set big->small)
        max_y = scan_array[1].max()
        self.assertEqual(scan_array[1][0], max_y)
        # The end in Y should correspond to the lowest
        min_y = scan_array[1].min()
        self.assertEqual(scan_array[1][-1], min_y)

        # Check the TTL signals
        self.assertEqual(ttl_array.shape, (exp_length * 2,))
        # There should be one high tick per pixel position
        nb_active = numpy.sum((ttl_array & self.pixel_bit).astype(bool))
        self.assertEqual(nb_active, res[0] * res[1])
        nb_active_inv = numpy.sum(~(ttl_array & self.pixel_bit_inv).astype(bool))
        self.assertEqual(nb_active_inv, res[0] * res[1])

        # First value of line and frame should be low, and last one should be high
        self.assertEqual(bool(ttl_array[0] & self.line_bit), False)
        self.assertEqual(bool(ttl_array[-1] & self.line_bit), True)
        self.assertEqual(bool(ttl_array[0] & self.frame_bit), False)
        self.assertEqual(bool(ttl_array[-1] & self.frame_bit), True)
        # frame TTL should contain only one transition (from low to high), corresponding to the beginning of the frame
        nb_transitions = numpy.sum(numpy.diff((ttl_array & self.frame_bit).astype(bool)))
        self.assertEqual(nb_transitions, 1)

    def test_waveform_spot(self):
        """
        Check the waveform generated when scanning a single spot
        """
        scanner = self.scanner

        # Dwell time might not be exactly accepted as-is depending of hardware
        scanner.dwellTime.value = 1e-3  # s
        dt = scanner.dwellTime.value
        self.assertTrue(0.999e-3 <= dt <= 1.001e-3)

        scanner.scale.value = 1, 1
        scale = scanner.scale.value
        self.assertEqual(scale, (1, 1))
        res = 1, 1
        scanner.resolution.value = res
        self.assertEqual(scanner.resolution.value, res)

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin, is_vector_scan = self.scanner._get_scan_waveforms(1)

        self.assertEqual(margin, 0)  # No need for margin when scanning a spot
        self.assertEqual(res, act_res)
        self.assertEqual(dt, act_dt)
        self.assertEqual(ao_osr, 1)  # less than 42s should always be ao_osr == 1

        exp_length = (res[0] + margin) * res[1]  # 1

        # Check the analog voltages (XY position) are the expected shape
        self.assertEqual(scan_array.shape, (2, exp_length))
        self.assertEqual(scan_array[0].size, exp_length)

        # Check the TTL signals
        self.assertEqual(ttl_array.shape, (exp_length * 2,))

        # There should be one high tick per pixel position
        nb_active = numpy.sum((ttl_array & self.pixel_bit).astype(bool))
        self.assertEqual(nb_active, res[0] * res[1])
        nb_active_inv = numpy.sum(~(ttl_array & self.pixel_bit_inv).astype(bool))
        self.assertEqual(nb_active_inv, res[0] * res[1])

        # No margin, so first value of line and frame should immediately be high,
        # but (as a special case), the very last value should be low
        self.assertEqual(bool(ttl_array[0] & self.line_bit), True)
        self.assertEqual(bool(ttl_array[-1] & self.line_bit), False)
        self.assertEqual(bool(ttl_array[0] & self.frame_bit), True)
        self.assertEqual(bool(ttl_array[-1] & self.frame_bit), False)
        # frame TTL should contain only one transition (from low to high), corresponding to the beginning of the frame
        nb_transitions = numpy.sum(numpy.diff((ttl_array & self.frame_bit).astype(bool)))
        self.assertEqual(nb_transitions, 1)

    def test_waveform_scan_path(self):
        dt = 1e-6  # s
        # Basic scan path, which scan the 4 corners of the FoV, and the center
        limits_px = self.scanner.translation.range
        scan_path = numpy.array([
            [limits_px[0][0], limits_px[0][1]],  # Left/top corner
            [limits_px[1][0], limits_px[0][1]],  # Right/top corner
            [limits_px[0][0], limits_px[1][1]],  # Left/bottom corner
            [limits_px[1][0], limits_px[1][1]],  # Right/bottom corner
            [0, 0]     # Center
        ], dtype=float)
        self.scanner.scanPath.value = scan_path
        self.scanner.dwellTime.value = dt

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin, is_vector_scan = self.scanner._get_scan_waveforms(1)

        self.assertEqual(margin, 0)  # No need for margin when scanning a spot
        self.assertEqual(act_res, (5, 1))  # number of points
        self.assertEqual(dt, act_dt)
        self.assertEqual(ao_osr, 1)  # less than 42s should always be ao_osr == 1

        self.assertEqual(scan_array.shape, (2, 5))
        # Compare to the expected limits  (X: -3 -> 6V, Y: 2 -> 1.2 V)
        # => smallest X should be negative, largest X positive,
        # => smallest Y positive, largest Y positive, but smaller
        self.assertLessEqual(scan_array[0, 0], -1000)  # X min
        self.assertGreaterEqual(scan_array[0, 1], 1000)  # X max

        self.assertGreater(scan_array[1, 2], 0)  # Y max > 0
        self.assertGreater(scan_array[1, 0], scan_array[1, 2])  # Y min > Y max

        # Test with a single point
        scan_path = numpy.array([
            [limits_px[0][0], limits_px[0][1]],  # Top left corner
        ], dtype=float)
        self.scanner.scanPath.value = scan_path
        self.scanner.dwellTime.value = dt

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin, is_vector_scan = self.scanner._get_scan_waveforms(1)

        self.assertEqual(margin, 0)  # No need for margin when scanning a spot
        self.assertEqual(act_res, (1, 1))  # number of points
        self.assertEqual(dt, act_dt)
        self.assertEqual(ao_osr, 1)  # less than 42s should always be ao_osr == 1

        self.assertEqual(scan_array.shape, (2, 1))

        # Test with 1 million points
        n = 1_000_000
        scan_path = numpy.empty((n, 2), dtype=float)
        scan_path[:, 0] = numpy.linspace(limits_px[0][0], limits_px[1][0], n)  # X
        scan_path[:, 1] = numpy.linspace(limits_px[0][1], limits_px[1][1], n)  # Y

        self.scanner.scanPath.value = scan_path
        self.scanner.dwellTime.value = dt

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin, is_vector_scan = self.scanner._get_scan_waveforms(1)

        self.assertEqual(margin, 0)  # No need for margin when scanning a spot
        self.assertEqual(act_res, (n, 1))  # number of points
        self.assertEqual(dt, act_dt)
        self.assertEqual(ao_osr, 1)  # less than 42s should always be ao_osr == 1

        self.assertEqual(scan_array.shape, (2, n))

        # Test with a single point for a very long dwell time (duplicated AO samples)
        dt = 50  # s, > 42s => dup == 2
        scan_path = numpy.array([
            [limits_px[0][0], limits_px[0][1]],  # Top left corner
        ], dtype=float)
        self.scanner.scanPath.value = scan_path
        self.scanner.dwellTime.value = dt

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin, is_vector_scan = self.scanner._get_scan_waveforms(1)

        self.assertEqual(margin, 0)  # No need for margin when scanning a spot
        self.assertEqual(act_res, (1, 1))  # number of points
        self.assertEqual(dt, act_dt)
        self.assertEqual(ao_osr, 2)  # > 42 s

        self.assertEqual(scan_array.shape, (2, 2))  # 2 samples for the single point, as ao_osr == 2

    def test_find_best_dwell_time(self):

        # For small dwell times, it should essentially be rounded to 100ns
        # Also, it should be a monotonic function: it would be odd that
        # 1000 -> 1000, 1100 -> 1500, 1200 -> 1400.
        prev_accepted_dt = None
        # In practice, the hardware is rounded to 10ns, so we test with values multiple of 9ns,
        # to make the live of the driver a little hard.
        min_dt = self.scanner.dwellTime.range[0]
        for dt in numpy.arange(min_dt, 2000e-9, 9e-9):
            logging.info("Testing dt = %s", dt)
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 1)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            precision = ai_osr * DWELL_TIME_PRECISION
            assert dt - precision <= accepted_dt <= dt + precision, f"Accepted dt is {accepted_dt} but expected {dt}"
            if prev_accepted_dt is not None:
                self.assertGreaterEqual(accepted_dt, prev_accepted_dt)
            prev_accepted_dt = accepted_dt

        for dt in numpy.arange(2000e-9, 5000e-9, 99e-9):
            logging.info("Testing dt = %s", dt)
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 1)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            precision = ai_osr * DWELL_TIME_PRECISION
            assert dt - precision <= accepted_dt <= dt + precision
            if prev_accepted_dt is not None:
                self.assertGreaterEqual(accepted_dt, prev_accepted_dt)
            prev_accepted_dt = accepted_dt

        # Large dwell times (> 42s), meaning AO OSR > 1
        for dt_extra in numpy.arange(0, 0.1, 1e-3):
            dt = 50 + dt_extra
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 1)
            self.assertGreaterEqual(ai_osr, 1000)
            self.assertGreaterEqual(ao_osr, 2)
            # Check that ai_osr is a multiple of ao_osr
            self.assertEqual((ai_osr / ao_osr) % 1, 0, "ai_osr={ai_osr} not multiple of ao_osr={ao_osr}")

            precision = 0.1
            assert dt - precision <= accepted_dt <= dt + precision
            if prev_accepted_dt is not None:
                self.assertGreaterEqual(accepted_dt, prev_accepted_dt)
            prev_accepted_dt = accepted_dt

        # Really large dwell times
        for dt_extra in numpy.arange(0, 0.1, 1e-3):
            dt = 5000 + dt_extra
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 1)
            self.assertGreaterEqual(ai_osr, 1000)
            self.assertGreaterEqual(ao_osr, 10)
            precision = 0.1
            assert dt - precision <= accepted_dt <= dt + precision
            if prev_accepted_dt is not None:
                self.assertGreaterEqual(accepted_dt, prev_accepted_dt)
            prev_accepted_dt = accepted_dt

        # Test with 2 detectors
        # Minimum dwell time is 1000ns (on the PCIe-6361)
        prev_accepted_dt = None
        for dt in numpy.arange(min_dt, 1000e-9, 9e-9):
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 2)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            self.assertEqual(accepted_dt, 1000e-9)
            prev_accepted_dt = accepted_dt

        for dt in numpy.arange(1000e-9, 5000e-9, 99e-9):
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 2)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            precision = ai_osr * DWELL_TIME_PRECISION
            assert dt - precision <= accepted_dt <= dt + precision
            if prev_accepted_dt is not None:
                self.assertGreaterEqual(accepted_dt, prev_accepted_dt)
            prev_accepted_dt = accepted_dt

    def test_downsample(self):

        # Pass exactly the whole array, with osr == 1 (so as-is)
        res = (20, 10)  # X, Y
        margin = 3
        osr = 1
        data = numpy.zeros(res[::-1], dtype=numpy.int16)
        buffer = numpy.linspace(500, 1500, (res[1] * (res[0] + margin)) * osr, dtype=numpy.int16)
        acc_dtype = util.get_best_dtype_for_acc(buffer.dtype, osr)
        samples_n, samples_sum = Acquirer._downsample_data(data, res, margin, 0, osr,
                                                           buffer, 0, 0,
                                                           acc_dtype)

        self.assertEqual(samples_n, 0)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])
        data_osr1 = data.copy()  # for later

        # Add one sample at a time
        osr = 1
        prev_samples_n = 0
        prev_samples_sum = 0
        data = numpy.zeros(res[::-1], dtype=numpy.int16)  # reset
        acc_dtype = util.get_best_dtype_for_acc(buffer.dtype, osr)
        for i, d in enumerate(buffer):
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         i, osr,
                                                                         numpy.array([d]),
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype)
        self.assertEqual(samples_n, 0)
        numpy.testing.assert_array_equal(data, data_osr1)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

        # Pass the whole buffer, with osr == 13
        osr = 13
        data = numpy.zeros(res[::-1], dtype=numpy.int16)  # reset
        buffer_osr13 = buffer.repeat(osr)
        acc_dtype = util.get_best_dtype_for_acc(buffer_osr13.dtype, osr)
        samples_n, samples_sum = Acquirer._downsample_data(data, res, margin, 0, osr,
                                                           buffer_osr13, 0, 0,
                                                           acc_dtype)

        self.assertEqual(samples_n, 0)
        numpy.testing.assert_array_equal(data, data_osr1)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

        # Add one sample at a time, with osr == 13
        osr = 13
        prev_samples_n = 0
        prev_samples_sum = 0
        data = numpy.zeros(res[::-1], dtype=numpy.int16)  # reset
        acc_dtype = util.get_best_dtype_for_acc(buffer_osr13.dtype, osr)
        for i, d in enumerate(buffer_osr13):
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         i, osr,
                                                                         numpy.array([d]),
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype)
        self.assertEqual(samples_n, 0)
        numpy.testing.assert_array_equal(data, data_osr1)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

        # Same, but pass 14 samples at a time (ie, not the same as osr)
        grain = 14
        osr = 13
        prev_samples_n = 0
        prev_samples_sum = 0
        data = numpy.zeros(res[::-1], dtype=numpy.int16)  # reset
        left_buffer = buffer_osr13[:]
        acc_dtype = util.get_best_dtype_for_acc(buffer_osr13.dtype, osr)
        acquired_n = 0
        while left_buffer.size > 0:
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         acquired_n, osr,
                                                                         left_buffer[:grain],
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype)
            acquired_n += grain
            left_buffer = left_buffer[grain:]

        self.assertEqual(samples_n, 0)
        numpy.testing.assert_array_equal(data, data_osr1)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

        # Same, but pass 2.3 lines at a time
        osr = 13
        prev_samples_n = 0
        prev_samples_sum = 0
        data = numpy.zeros(res[::-1], dtype=numpy.int16)  # reset
        grain = int((res[0] + margin) * osr * 2.3)
        left_buffer = buffer_osr13[:]
        acc_dtype = util.get_best_dtype_for_acc(buffer_osr13.dtype, osr)
        acquired_n = 0
        while left_buffer.size > 0:
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         acquired_n, osr,
                                                                         left_buffer[:grain],
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype)
            acquired_n += grain
            left_buffer = left_buffer[grain:]

        self.assertEqual(samples_n, 0)
        numpy.testing.assert_array_equal(data, data_osr1)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

        # Same, but pass 1 lines at a time
        osr = 13
        prev_samples_n = 0
        prev_samples_sum = 0
        data = numpy.zeros(res[::-1], dtype=numpy.int16)  # reset
        grain = (res[0] + margin) * osr
        left_buffer = buffer_osr13[:]
        acc_dtype = util.get_best_dtype_for_acc(buffer_osr13.dtype, osr)
        acquired_n = 0
        while left_buffer.size > 0:
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         acquired_n, osr,
                                                                         left_buffer[:grain],
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype)
            acquired_n += grain
            left_buffer = left_buffer[grain:]

        self.assertEqual(samples_n, 0)
        numpy.testing.assert_array_equal(data, data_osr1)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

        # Same, but pass a few pixels (less than margin) + the rest a whole line, repeatedly
        osr = 13
        prev_samples_n = 0
        prev_samples_sum = 0
        data = numpy.zeros(res[::-1], dtype=numpy.int16)  # reset
        grain1 = (margin - 1) * osr
        grain2 = (res[0] + margin) * osr - grain1
        left_buffer = buffer_osr13[:]
        acc_dtype = util.get_best_dtype_for_acc(buffer_osr13.dtype, osr)
        acquired_n = 0
        while left_buffer.size > 0:
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         acquired_n, osr,
                                                                         left_buffer[:grain1],
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype)
            acquired_n += grain1
            left_buffer = left_buffer[grain1:]
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         acquired_n, osr,
                                                                         left_buffer[:grain2],
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype)
            acquired_n += grain2
            left_buffer = left_buffer[grain2:]

        self.assertEqual(samples_n, 0)
        numpy.testing.assert_array_equal(data, data_osr1)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

        # Test like CI
        res = (1024, 768)
        margin = 10
        osr = 1
        prev_samples_n = 0
        prev_samples_sum = 0
        data = numpy.zeros(res[::-1], dtype=numpy.uint32)
        grain = 100000
        buffer = numpy.linspace(500, 1500, (res[1] * (res[0] + margin)) * osr, dtype=numpy.uint32)
        left_buffer = buffer[:]
        acc_dtype = numpy.uint32
        acquired_n = 0
        while left_buffer.size > 0:
            logging.debug(f"Passing another {grain} samples, still {left_buffer.size} left")
            prev_samples_n, prev_samples_sum = Acquirer._downsample_data(data, res, margin,
                                                                         acquired_n, osr,
                                                                         left_buffer[:grain],
                                                                         prev_samples_n, prev_samples_sum,
                                                                         acc_dtype,
                                                                         average=False)
            acquired_n += grain
            left_buffer = left_buffer[grain:]

        self.assertEqual(samples_n, 0)
        self.assertEqual(data[0, 0], buffer[margin])
        self.assertEqual(data[-1, -1], buffer[-1])

    def test_acquisition(self):
        # Fast acquisition, using synchronous acquisition
        self.scanner.dwellTime.value = 1.e-6  # s
        self.scanner.scale.value = (300, 384)  # => res = 13x8
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        # Error PDI
        dt = 0.11870046
        self.scanner.dwellTime.value = dt  # s
        self.scanner.scale.value = 8, 8
        self.scanner.resolution.value = (16, 8)
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        # Slightly different settings
        self.scanner.dwellTime.value = 2.1234e-6  # s
        self.scanner.scale.value = (4, 5)
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        # Tricky dwell time with respect to DO period
        # 3570 ns divides nicely into 7 x 510ns, but when divided by 2 (for DO), it's not a multiple of 10ns: 2 x 1785
        self.scanner.dwellTime.value = 3.57e-06  # s
        # Same with 1530ns:
        # self.scanner.dwellTime.value = 1.53e-06  # s
        self.scanner.scale.value = 8, 8  # Doesn't really matter for DO
        self.scanner.resolution.value = (512, 384)
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        # Another tricky dwell time for DO
        for dt in [0.009020000447332858, 0.010080000057816505, 0.011010000233352184, 0.018860000613331793, 0.02920000020414591]:
            self.scanner.dwellTime.value = dt  # s
            self.scanner.scale.value = 8, 8  # Doesn't really matter for DO
            self.scanner.resolution.value = (2, 4)
            exp_shape, exp_pxs, _ = self.compute_expected_metadata()
            da = self.sed.data.get()
            self.assertEqual(da.shape, exp_shape)
            self.assertIn(model.MD_DWELL_TIME, da.metadata)
            self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acquisition_counter(self):
        self.scanner.dwellTime.value = 10e-06  # s
        self.scanner.scale.value = 8, 8
        self.scanner.resolution.value = (512, 384)  # px
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.counter.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(da.metadata[model.MD_INTEGRATION_COUNT], 1)

        # Small acquisition, which fits in less than a standard buffer size
        self.scanner.dwellTime.value = 1e-06  # s
        self.scanner.scale.value = 8, 8
        self.scanner.resolution.value = (1, 384)  # px
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.counter.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(da.metadata[model.MD_INTEGRATION_COUNT], 1)

        # Single pixel tests, which can be more difficult, because the hardware expects always at least 2 samples
        self.scanner.dwellTime.value = 10e-06  # s
        self.scanner.scale.value = 1, 1
        self.scanner.resolution.value = (1, 1)  # px
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.counter.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(da.metadata[model.MD_INTEGRATION_COUNT], 1)

        # Same thing, but with "long" acquisition (AO OSR > 1)
        self.scanner.dwellTime.value = 43  # s
        self.scanner.scale.value = 1, 1
        self.scanner.resolution.value = (1, 1)  # px
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.counter.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(da.metadata[model.MD_INTEGRATION_COUNT], 2)  # AO OSR

    def test_acquisition_fast(self):
        """
        Try acquisition which is too fast for continuous acquisition
        """
        # Default dwell time is the shortest dwell time => that's what we want
        self.scanner.scale.value = 64, 64
        self.scanner.resolution.value = 10, 8

        evt_counter = EventReceiver()
        self.scanner.startScan.subscribe(evt_counter)

        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(evt_counter.count, 1)

        # Very tiny (single sample)
        self.scanner.scale.value = 64, 64
        self.scanner.resolution.value = 1, 1
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(evt_counter.count, 2)
        self.scanner.startScan.unsubscribe(evt_counter)

    def test_acquisition_big_res(self):
        """
        Test acquisitions with large resolution
        """
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] * 2  # 2 samples per point
        self.scanner.scale.value = (1, 1)  # => res is max
        self.scanner.resolution.value = self.scanner.resolution.range[1]
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        # Now, same thing, but fast
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]  # 1 sample per point
        self.scanner.scale.value = (1, 1)  # => res is max
        self.scanner.resolution.value = self.scanner.resolution.range[1]
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acquisition_sparc_hwsync(self):
        """
        Test acquisitions for "fast" SPARC spectrum data (eg, every 0.5ms), with HW pixel trigger
        """
        self.scanner.dwellTime.value = 0.5e-3
        self.scanner.scale.value = (1, 1)  # => res is max
        self.scanner.resolution.value = (128, 50)
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        for i in range(da.shape[0]):
            print("%d th line average: %s" % (i, da[i, :].mean(),))

    def test_acquisition_sparc_slow(self):
        """
        Test acquisitions for "slow" SPARC spectrum data (eg, every 0.1s), without HW trigger synchronization
        """
        start_dt = 0.1
        self.scanner.dwellTime.value = start_dt
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = (1, 1)
        self.scanner.translation.value = (0, 0)
        exp_shape, exp_pxs, exp_duration = self.compute_expected_metadata()

        evt_counter = EventReceiver()
        self.scanner.startScan.subscribe(evt_counter)

        spots_dates = []
        for i in range(50):
            # On the SPARC, the dwell time doesn't actually change for every spot, but let's make it
            # a little harder.
            self.scanner.dwellTime.value = start_dt / (i + 1)
            logging.debug("Will acquire spot %d at dt = %s", i, self.scanner.dwellTime.value)
            self.scanner.translation.value = (0, i)  # New scan parameter every time
            # Typically acquires 1 frame, and runs a second frame until half of it. But let's just let it
            # run a lot, and we'll stop it before it's done.
            self.expected_shape = exp_shape
            self.left = 10
            self.im_received.clear()
            self.sed.data.subscribe(self.receive_image)
            time.sleep(0.2 + exp_duration * 1.5)
            self.sed.data.unsubscribe(self.receive_image)
            self.assertTrue(self.im_received.is_set(), f"No image received on pixel {i}")
            spots_dates.append(self.acq_dates[-1])
            time.sleep(5e-3)  # simulate data processing

        self.scanner.startScan.unsubscribe(evt_counter)
        self.assertEqual(len(spots_dates), 50)
        self.assertEqual(evt_counter.count, 50)

    def test_acquisition_long_dt(self):
        """
        Test image acquisition with a long dwell time (so that AO OSR > 1)
        """
        # The maximum period for AO OSR == 1 is 2**32 * 10ns = 42.9s
        self.scanner.dwellTime.value = 43  # s, AO OSR == 2
        self.scanner.scale.value = (1, 1)  # Not that is matters
        self.scanner.resolution.value = (1, 1)  # px, small so that it's too long
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

    def test_flow(self):
        """
        Check continuous acquisition
        """
        self.scanner.scale.value = 2, 3
        self.scanner.resolution.value = self.scanner.resolution.range[1]  # Clipped to the max possible
        self.scanner.translation.value = 0, 0
        self.scanner.dwellTime.value = 1e-6  # s
        exp_shape, exp_pxs, exp_duration = self.compute_expected_metadata()

        # Also check that the startScan event is properly sent just once after the first acquisition
        evt_counter = EventReceiver()
        self.scanner.startScan.subscribe(evt_counter)

        # Acquire several points/frame in a row, to make sure it can continuously acquire
        number = 7
        self.expected_shape = exp_shape
        exp_tot_dur = exp_duration * number
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        start_t = time.time()
        done = self.acq_done.wait(timeout=exp_tot_dur * 1.3)
        stop_t = time.time()
        logging.info("Acquisitions received at %s", self.acq_dates)
        self.assertTrue(done, f"Acquisition not completed within time. Still running after {exp_tot_dur} * 1.3")
        duration = stop_t - start_t
        self.assertGreaterEqual(duration, exp_tot_dur, f"Acquisition ended too early: {duration}s")

        self.assertEqual(evt_counter.count, 1)
        self.scanner.startScan.unsubscribe(evt_counter)

    def test_flow_change_settings(self):
        """
        Check continuous acquisition while the settings change
        """
        self.scanner.scale.value = 2, 3
        self.scanner.resolution.value = self.scanner.resolution.range[1]  # Clipped to the max possible
        self.scanner.translation.value = 0, 0
        self.scanner.dwellTime.value = 1e-6  # s
        exp_shape1, exp_pxs1, exp_duration1 = self.compute_expected_metadata()

        # Acquire several points/frame in a row, to make sure it can continuously acquire
        number = 5
        self.left = number
        self.sed.data.subscribe(self.receive_and_store_image)
        start_t = time.time()

        # Wait a bit before changing the settings
        time.sleep(exp_duration1 / 2)
        self.scanner.scale.value = 4, 5
        exp_shape2, exp_pxs2, exp_duration2 = self.compute_expected_metadata()

        # wait until the end
        done = self.acq_done.wait(timeout=exp_duration2 * 6 * 1.3)
        stop_t = time.time()
        duration = stop_t - start_t
        self.assertTrue(done, f"Acquisition not completed within time. Still running after {duration}")

        # Check the first image has different res than the rest (image 2 could still be using the old resolution)
        logging.info("Acquisition shapes: %s", [da.shape for da in self.das])
        self.assertEqual(self.das[0].shape, exp_shape1)
        self.assertEqual(self.das[3].shape, exp_shape2)

    def test_flow_goes_too_fast(self):
        """
        Check continuous acquisition while the settings change to a too fast frame rate for the
        continuous acquisition
        """
        # Start with a "slow" framerate
        self.scanner.scale.value = 4, 4
        self.scanner.resolution.value = self.scanner.resolution.range[1]  # Clipped to the max possible
        self.scanner.translation.value = 0, 0
        # Dwell time set to the fastest
        exp_shape1, exp_pxs1, exp_duration1 = self.compute_expected_metadata()

        # Acquire several points/frame in a row, to make sure it can continuously acquire
        number = 500
        self.left = number
        self.sed.data.subscribe(self.receive_and_store_image)
        start_t = time.time()

        # Wait a bit before changing the settings.
        time.sleep(exp_duration1 * 2)
        # We (try to) find settings for a frame rate > 1ms, but still too fast for the hardware,
        # which is the most complex code path
        # Note: it's actually pretty hard to trigger. It helps to add a sleep(0.005) in _acquire_frames()
        self.scanner.scale.value = 64, 64
        self.scanner.resolution.value = 90, 90
        exp_shape2, exp_pxs2, exp_duration2 = self.compute_expected_metadata()

        # wait until the end
        done = self.acq_done.wait(timeout=(exp_duration2 + 30e-3) * number * 1.3)
        stop_t = time.time()
        duration = stop_t - start_t
        self.assertTrue(done, f"Acquisition not completed within time. Still running after {duration}")

        # Check the first image has different res than the rest (image 2 could still be using the old resolution)
        logging.info("Acquisition shapes: %s", [da.shape for da in self.das])
        self.assertEqual(self.das[0].shape, exp_shape1)
        self.assertEqual(self.das[10].shape, exp_shape2)

    def test_spot_mode(self):
        """
        Check acquisition of 1x1 pixels, with long dwell time
        """
        # If the dwell time is longer than the BUFFER_DURATION, the driver will
        # have to receive buffer while no data can be downsampled. This mostly
        # test such corner case.

        # Scale doesn't matter too much, but usually we use 1x1, to make sure the
        # translation accepts to go anywhere.
        self.scanner.scale.value = 1, 1
        self.scanner.resolution.value = 1, 1
        self.scanner.translation.value = -100, 200  # px, arbitrary values
        self.scanner.dwellTime.value = 1.5  # s

        # Acquire several points/frame in a row, to make sure it can continuously acquire
        number = 5
        self.expected_shape = 1, 1
        exp_duration = self.scanner.dwellTime.value * numpy.prod(self.expected_shape) * number
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        start_t = time.time()
        done = self.acq_done.wait(timeout=exp_duration * 1.3)
        stop_t = time.time()
        duration = stop_t - start_t
        self.assertTrue(done, f"Acquisition not completed within time. Still running after {duration}")
        self.assertGreaterEqual(duration, exp_duration, f"Acquisition ended too early: {duration}s")

    def test_spot_mode_fast(self):
        """
        Check acquisition of 1x1 pixels, with short dwell time
        """
        self.scanner.scale.value = 1, 1
        self.scanner.resolution.value = 1, 1
        #fastest dwell time

        # Acquire several points/frame in a row, to make sure it can continuously acquire
        number = 5
        self.expected_shape = 1, 1
        exp_duration = self.scanner.dwellTime.value * numpy.prod(self.expected_shape) * number
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        start_t = time.time()
        done = self.acq_done.wait(timeout=1 + exp_duration * 1.3)
        stop_t = time.time()
        duration = stop_t - start_t
        self.assertTrue(done, f"Acquisition not completed within time. Still running after {duration}")
        self.assertGreaterEqual(duration, exp_duration, f"Acquisition ended too early: {duration}s")

    def test_spot_mode_buffer_size(self):
        """
        Check acquisition of 1x1 pixels, with 0.1s dwell time (which is precisely the default AI buffer duration)
        """
        self.scanner.scale.value = 1, 1
        self.scanner.resolution.value = 1, 1
        self.scanner.dwellTime.value = 0.1

        self.expected_shape = 1, 1
        exp_duration = self.scanner.dwellTime.value * numpy.prod(self.expected_shape)

        # Start/stop between each acquisition (point), to simulate the SPARC acquisition.
        for i in range(5):
            self.left = 100  # We'll stop by ourselves, while the next frame has already started
            self.acq_dates = []
            self.im_received.clear()
            self.sed.data.subscribe(self.receive_image)

            # Wait for 1 acquisition
            start_t = time.time()
            received = self.im_received.wait(timeout=1 + exp_duration * 1.3)
            stop_t = time.time()
            duration = stop_t - start_t
            self.assertTrue(received, f"Acquisition not completed within time. Still running after {duration}")
            self.assertGreaterEqual(duration, exp_duration, f"Acquisition ended too early: {duration}s")
            self.sed.data.unsubscribe(self.receive_image)
            self.assertEqual(len(self.acq_dates), 1)

    def test_acquire_two_flows(self):
        """
        Simple acquisition with two dataflows acquiring (more or less)
        simultaneously
        """
        # Pick a very small dwell time, which is not possible with 2 detectors, so it will have to
        # increase at the moment the acquisition starts
        orig_dwell_time = self.scanner.dwellTime.value
        exp_shape, exp_pxs1, exp_duration = self.compute_expected_metadata()
        number, number2 = 4, 5

        logging.debug("Starting acquisition")

        self.left = number
        self.expected_shape = exp_shape
        self.sed.data.subscribe(self.receive_image)

        time.sleep(exp_duration + 0.1)  # make sure we'll start asynchronously
        self.left2 = number2
        self.cld.data.subscribe(self.receive_image2)

        time.sleep(exp_duration)  # make sure at least the next frame has started
        new_dwell_time = self.scanner.dwellTime.value
        self.assertGreater(new_dwell_time, orig_dwell_time)

        for i in range(number + number2):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + exp_duration * 1.1)  # 2s per image should be more than enough in any case

        # check that at least some images were acquired simultaneously
        common_dates = set(self.acq_dates) & set(self.acq_dates2)
        self.assertGreater(len(common_dates), 0, "No common dates between %r and %r" %
                           (self.acq_dates, self.acq_dates2))

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

    def test_acquire_three_flows(self):
        """
        Simple acquisition with two dataflows acquiring (more or less)
        simultaneously
        """
        # Pick a very small dwell time, which is not possible with 2 detectors, so it will have to
        # increase at the moment the acquisition starts
        self.scanner.scale.value = (16, 16)
        self.scanner.dwellTime.value = 10e-6 # s
        exp_shape, exp_pxs1, exp_duration = self.compute_expected_metadata()
        number = 3 * 4  # Counts for all the detectors

        self.left = number
        self.expected_shape = exp_shape
        self.sed.data.subscribe(self.receive_image)
        self.cld.data.subscribe(self.receive_image)
        self.bsd.data.subscribe(self.receive_image)

        try:
            # acq_done should be set by the 12th frame, which should correspond to the last detector of the 3.
            if not self.acq_done.wait(number * (exp_duration + 0.1)):
                self.fail("Acquisition not completed within time")
        finally:
            # Unsubscribe from the other 2 detectors (but as we don't know which one was last, just
            # unsubscribe from all)
            self.sed.data.unsubscribe(self.receive_image)
            self.cld.data.unsubscribe(self.receive_image)
            self.bsd.data.unsubscribe(self.receive_image)

        time.sleep(1)  # make sure all the images are received

        self.assertLessEqual(self.left, 0)
        logging.debug("Received %d images", len(self.acq_dates))

    def test_sync_flow(self):
        """
        Acquire a dataflow with a softwareTrigger
        """
        self.scanner.dwellTime.value = 1e-6
        exp_shape, exp_pxs1, exp_duration = self.compute_expected_metadata()

        # set softwareTrigger on the first detector to be subscribed
        self.sed.data.synchronizedOn(self.sed.softwareTrigger)

        self.left = 3
        self.expected_shape = exp_shape
        self.sed.data.subscribe(self.receive_image)

        self.sed.softwareTrigger.notify()  # start acquiring
        self.sed.softwareTrigger.notify()  # should be queued up for next acquisition

        # wait enough for the 2 acquisitions
        time.sleep(2 * (2 + exp_duration * 1.1))  # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 1)

        # remove synchronisation
        self.sed.data.synchronizedOn(None)  # => should immediately start another acquisition

        # wait for last acq
        self.acq_done.wait(2 + exp_duration * 1.1)

        self.assertEqual(self.left, 0)

    def test_sync_ai_ci(self):
        """
        Acquire simultaneously one analog detector and one counter detector
        """
        exp_shape, exp_pxs1, exp_duration = self.compute_expected_metadata()

        # set softwareTrigger on the first detector to be subscribed
        self.sed.data.synchronizedOn(self.sed.softwareTrigger)

        self.left = 1
        self.left2 = 1
        self.expected_shape = exp_shape
        self.sed.data.subscribe(self.receive_image)
        self.counter.data.subscribe(self.receive_image2)

        # Shouldn't be acquiring yet, as the trigger hasn't been sent
        logging.debug("Waiting to be certain the acquisition hasn't started yet...")
        time.sleep(1 * (2 + exp_duration * 1.1))
        self.assertEqual(self.left2, 1)

        # start acquiring one image
        self.sed.softwareTrigger.notify()

        # wait enough for the 2 acquisitions
        time.sleep(1 * (2 + exp_duration * 1.1))  # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

        # wait for last acq
        self.acq_done.wait(2 + exp_duration * 1.1)

        # remove synchronisation, should do nothing as they are stopped
        self.sed.data.synchronizedOn(None)

    @skip("Too long (~ 1h20)")
    def test_sync_long(self):
        """
        Acquire one long acquisition, in synchronized mode (ie, one frame at a time)
        """
        # The NI DAQmx has a limit for the number of sample of 8 * 1024**3 (~8 billion).
        # This could be reached with a very long acquisition, on the AI task, as it uses the
        # maximum sampling rate. At a sampling rate of 0.5µs, this is reached when the acquisition
        # lasts ~1h10 min. This is normally correctly handled, but this test checks it.
        self.scanner.dwellTime.value = 0.1  # s
        self.scanner.scale.value = 1, 1
        self.scanner.resolution.value = (192, 30)
        exp_shape, exp_pxs1, exp_duration = self.compute_expected_metadata()

        # set softwareTrigger on the first detector to be subscribed
        self.sed.data.synchronizedOn(self.sed.softwareTrigger)

        self.left = 1
        self.left2 = 1
        self.expected_shape = exp_shape
        self.sed.data.subscribe(self.receive_image)
        self.counter.data.subscribe(self.receive_image2)

        # start acquiring one image
        self.sed.softwareTrigger.notify()
        self.counter.softwareTrigger.notify()

        # wait enough for the acquisition of 1 frame
        time.sleep(1 * (2 + exp_duration * 1.1))

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

        # wait for last acq
        self.acq_done.wait(2 + exp_duration * 1.1)

        # remove synchronisation, should do nothing as they are stopped
        self.sed.data.synchronizedOn(None)
        self.counter.data.synchronizedOn(None)

    def test_acquisition_scan_path(self):
        limits_px = self.scanner.translation.range
        # Basic scan path, which scan the 4 corners of the FoV, and the center
        scan_path = numpy.array([
            [limits_px[0][0], limits_px[0][1]],  # Top left corner
            [limits_px[1][0], limits_px[0][1]],  # Top right corner
            [limits_px[0][0], limits_px[1][1]],  # Bottom right corner
            [limits_px[1][0], limits_px[1][1]],  # Bottom left corner
            [0, 0]     # Center
        ], dtype=float)
        # TTL signal: everything is a pixel, a line and a frame
        all_high = numpy.ones(scan_path.shape[0] * 2, dtype=bool)

        self.scanner.scanPath.value = scan_path
        self.scanner.scanPixelTTL.value = all_high
        self.scanner.scanLineTTL.value = all_high
        self.scanner.scanFrameTTL.value = all_high
        self.scanner.dwellTime.value = 1.e-6  # s

        exp_pxs = self.scanner.pixelSize.value

        da = self.sed.data.get()
        self.assertEqual(da.shape, (5,))
        self.assertAlmostEqual(da.metadata[model.MD_DWELL_TIME], 1.e-6)
        # MD_PIXEL_SIZE should be the same as the pixel size (ie, assume scale == 1)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)
        # MD_POS should be passed as-is
        self.assertAlmostEqual(da.metadata[model.MD_POS], (0, 0))

    def test_acquire_two_flows_scan_path(self):
        """
        Vector scan acquisition with two dataflows acquiring simultaneously
        """
        # Test with 1 million points (as a line going from the top-left to bottom-right corner)
        n = 1_000_000
        limits_px = self.scanner.translation.range
        scan_path = numpy.empty((n, 2), dtype=float)
        scan_path[:, 0] = numpy.linspace(limits_px[0][0], limits_px[1][0], n)  # X
        scan_path[:, 1] = numpy.linspace(limits_px[0][1], limits_px[1][1], n)  # Y

        dt = 2e-6 # s, small dwell time, but large enough to be accepted with 2 detectors
        self.scanner.scanPath.value = scan_path
        self.scanner.dwellTime.value = dt

        exp_duration = n * dt
        number, number2 = 4, 5

        logging.debug("Starting acquisition")

        self.left = number
        self.expected_shape = (n,)
        self.sed.data.subscribe(self.receive_image)

        time.sleep(exp_duration + 0.1)  # make sure we'll start asynchronously
        self.left2 = number2
        self.cld.data.subscribe(self.receive_image2)

        time.sleep(exp_duration)  # make sure at least the next frame has started

        for i in range(number + number2):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + exp_duration * 1.1)  # 2s per image should be more than enough in any case

        # check that at least some images were acquired simultaneously
        common_dates = set(self.acq_dates) & set(self.acq_dates2)
        self.assertGreater(len(common_dates), 0, "No common dates between %r and %r" %
                           (self.acq_dates, self.acq_dates2))

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.expected_shape)
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates.append(image.metadata[model.MD_ACQ_DATE])
        self.im_received.set()
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)
            logging.debug("Stopping acquisition of %s after receiving all expected DataArrays", dataflow)
            self.acq_done.set()

    def receive_image2(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.expected_shape)
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates2.append(image.metadata[model.MD_ACQ_DATE])
        self.left2 -= 1
        if self.left2 <= 0:
            dataflow.unsubscribe(self.receive_image2)
            self.acq_done2.set()

    def receive_and_store_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.acq_dates.append(image.metadata[model.MD_ACQ_DATE])
        self.das.append(image)
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_and_store_image)
            self.acq_done.set()


if __name__ == "__main__":
    unittest.main()
