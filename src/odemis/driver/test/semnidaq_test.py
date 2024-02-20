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
from odemis.driver import semnidaq
from odemis.driver.semnidaq import Acquirer

matplotlib.use("Gtk3Agg")


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
    "limits": [3, -3]  # Data inverted
}

CONFIG_BSD = {
    "name": "bsd",
    "role": "bsd",
    "channel": 2,
    # "channel": "ao1",  # Loopback from the AO1, for testing
    "limits": [-4, 4]  # Data inverted
}

CONFIG_CNT = {
    "name": "counter",
    "role": "counter",
    "source": 8,  # PFI8
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
    "pixel_ttl": [0, 7],
    "line_ttl": [1],
    "frame_ttl": [6],
}

# For loop-back testing (currently on the breadboard):
# AO0 -> AI0
# DO0.0 (aka 0) -> AI1

CONFIG_SEM = {
    "name": "sem",
    "role": "sem",
    "device": "Dev1",
    "children": {
        "scanner": CONFIG_SCANNER,
        "detector0": CONFIG_SED,
        "detector1": CONFIG_CLD,
        "detector2": CONFIG_BSD,
        "counter0": CONFIG_CNT,
    }
}


class TestAnalogSEM(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
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
        cls.pixel_bit = sum(1 << c for c in CONFIG_SCANNER["pixel_ttl"])
        cls.line_bit = sum(1 << c for c in CONFIG_SCANNER["line_ttl"])
        cls.frame_bit = sum(1 << c for c in CONFIG_SCANNER["frame_ttl"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.sem.terminate()

    def setUp(self) -> None:
        # Start with basic good default values
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]  # s
        self.scanner.scale.value = (8, 8)  # => res is max
        self.scanner.resolution.value = self.scanner.resolution.range[1]  # max res, limited to the scale (so, max / 4)

        # for receive_image()
        self.expected_shape = tuple(self.scanner.resolution.value[::-1])
        self.left = 1
        self.acq_dates = []  # floats
        self.acq_done = threading.Event()
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

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin = self.scanner._get_scan_waveforms(1)

        plt.plot(scan_array[0], label="X")  # X voltage
        plt.plot(scan_array[1], label="Y")  # Y voltage
        ttl_clock = numpy.arange(ttl_array.shape[-1]) * 0.5  # The clock for the TTL is twice faster than the analog out

        ttl_pixel = (ttl_array & self.pixel_bit) != 0
        ttl_line = (ttl_array & self.line_bit) != 0
        ttl_frame = (ttl_array & self.frame_bit) != 0
        plt.plot(ttl_clock, ttl_pixel * 10000 - 51000, label="pixel TTL")
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

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin = self.scanner._get_scan_waveforms(1)

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
        nb_high = numpy.sum((ttl_array & self.pixel_bit).astype(bool))
        self.assertEqual(nb_high, res[0] * res[1])
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

        scan_array, ttl_array, act_dt, ao_osr, ai_osr, act_res, margin = self.scanner._get_scan_waveforms(1)

        self.assertEqual(margin, 0)  # No need for margin when scanning a spot
        self.assertEqual(res, act_res)
        self.assertEqual(dt, act_dt)
        self.assertEqual(ao_osr, 1)  # less than 42s should always be ao_osr == 1

        exp_length = (res[0] + margin) * res[1]  # 1

        # Check the analog voltages (XY position)
        self.assertEqual(scan_array[0].size, exp_length)
        self.assertEqual(scan_array[1].size, exp_length)

        # Check the TTL signals
        self.assertEqual(ttl_array.shape, (exp_length * 2,))

        # There should be one high tick per pixel position
        nb_high = numpy.sum((ttl_array & self.pixel_bit).astype(bool))
        self.assertEqual(nb_high, res[0] * res[1])
        # No margin, so first value of line and frame should immediately be high,
        # but (as a special case), the very last value should be low
        self.assertEqual(bool(ttl_array[0] & self.line_bit), True)
        self.assertEqual(bool(ttl_array[-1] & self.line_bit), False)
        self.assertEqual(bool(ttl_array[0] & self.frame_bit), True)
        self.assertEqual(bool(ttl_array[-1] & self.frame_bit), False)
        # frame TTL should contain only one transition (from low to high), corresponding to the beginning of the frame
        nb_transitions = numpy.sum(numpy.diff((ttl_array & self.frame_bit).astype(bool)))
        self.assertEqual(nb_transitions, 1)

    def test_find_best_dwell_time(self):

        # For small dwell times, it should essentially be rounded to 100ns
        # Also, it should be a monotonic function: it would be odd that
        # 1000 -> 1000, 1100 -> 1500, 1200 -> 1400.
        prev_accepted_dt = None
        # In practice, the hardware is rounded to 10ns, so we test with values multiple of 9ns,
        # to make the live of the driver a little hard.
        for dt in numpy.arange(500e-9, 2000e-9, 9e-9):
            logging.info("Testing dt = %s", dt)
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 1)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            precision = ai_osr * 20e-9
            assert dt - precision <= accepted_dt <= dt + precision
            if prev_accepted_dt is not None:
                self.assertGreaterEqual(accepted_dt, prev_accepted_dt)
            prev_accepted_dt = accepted_dt

        for dt in numpy.arange(2000e-9, 5000e-9, 99e-9):
            logging.info("Testing dt = %s", dt)
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 1)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            precision = ai_osr * 20e-9
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
            self.assertGreaterEqual(ao_osr, 50)
            precision = 0.1
            assert dt - precision <= accepted_dt <= dt + precision
            if prev_accepted_dt is not None:
                self.assertGreaterEqual(accepted_dt, prev_accepted_dt)
            prev_accepted_dt = accepted_dt

        # Test with 2 detectors
        # Minimum dwell time is 1000ns
        prev_accepted_dt = None
        for dt in numpy.arange(500e-9, 1000e-9, 9e-9):
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 2)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            self.assertEqual(accepted_dt, 1000e-9)
            prev_accepted_dt = accepted_dt

        for dt in numpy.arange(1000e-9, 5000e-9, 99e-9):
            accepted_dt, ao_osr, ai_osr = self.sem.find_best_dwell_time(dt, 2)
            self.assertGreaterEqual(ai_osr, 1)
            self.assertEqual(ao_osr, 1)
            precision = ai_osr * 20e-9
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
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        # Very tiny (single sample)
        self.scanner.scale.value = 64, 64
        self.scanner.resolution.value = 1, 1
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        exp_shape, exp_pxs, _ = self.compute_expected_metadata()
        da = self.sed.data.get()
        self.assertEqual(da.shape, exp_shape)
        self.assertIn(model.MD_DWELL_TIME, da.metadata)
        self.assertAlmostEqual(da.metadata[model.MD_PIXEL_SIZE], exp_pxs)

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
        orig_dwell_time = self.scanner.dwellTime.value
        exp_shape, exp_pxs1, exp_duration = self.compute_expected_metadata()
        number = 3 * 4  # Counts for all the detectors

        self.left = number
        self.expected_shape = exp_shape
        self.sed.data.subscribe(self.receive_image)
        self.cld.data.subscribe(self.receive_image)
        self.bsd.data.subscribe(self.receive_image)

        time.sleep(number * (exp_duration + 0.1))

        self.assertLessEqual(self.left, 0)

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

        # start acquiring one image
        self.sed.softwareTrigger.notify()
        self.counter.softwareTrigger.notify()

        # wait enough for the 2 acquisitions
        time.sleep(1 * (2 + exp_duration * 1.1))  # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

        # wait for last acq
        self.acq_done.wait(2 + exp_duration * 1.1)

        # remove synchronisation, should do nothing as they are stopped
        self.sed.data.synchronizedOn(None)
        self.counter.data.synchronizedOn(None)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.expected_shape)
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates.append(image.metadata[model.MD_ACQ_DATE])
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)
            self.acq_done.set()

    def receive_image2(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.expected_shape)
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates2.append(image.metadata[model.MD_ACQ_DATE])
        #        print "Received an image"
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
