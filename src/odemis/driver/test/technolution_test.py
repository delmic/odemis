#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 11 May 2020

@author: Sabrina Rossberger, Kornee Kleijwegt

Copyright © 2019-2021 Kornee Kleijwegt, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

These test cases can only be done using the simulator for the ASM.
After installing the simulator it can be starting using the following commands in the terminal:
    sudo su
    echo 134217728 > /proc/sys/net/core/rmem_max;
    systemctl restart vsftpd.service; systemctl restart asm_service; systemctl restart sam_simulator;
    systemctl status asm_service;
"""
import logging
import math
import queue

import numpy
from odemis import model
from odemis.util import almost_equal
import os
import pickle
import threading
import time
import unittest
from urllib.parse import urlparse

import matplotlib.pyplot as plt
from technolution_asm.models import CalibrationLoopParameters
from technolution_asm.models.mega_field_meta_data import MegaFieldMetaData

from odemis.driver.technolution import AcquisitionServer, convert2Bits, convertRange, AsmApiException, DATA_CONTENT_TO_ASM

# Set logger level to debug to observe all the output (useful when a test fails)
logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW = 1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

URL = "http://localhost:8080/v2"

# Configuration of the children of the AcquisitionServer object
CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}
CHILDREN_ASM = {"EBeamScanner"   : CONFIG_SCANNER,
                "MirrorDescanner": CONFIG_DESCANNER,
                "MPPC"           : CONFIG_MPPC}
EXTRNAL_STORAGE = {"host"     : "localhost",
                   "username" : "username",
                   "password" : "password",
                   "directory": "asm_service"}


class TestAuxilaryFunc(unittest.TestCase):
    def test_convertRange(self):
        # Test input value of zero on even range
        out = tuple(convertRange((0, 0), (-1, 1), (-100, 100)))
        self.assertEqual((0, 0), out)

        # Test input value of zero on uneven range
        out = tuple(convertRange((0, 0), (-1, 1), (-101, 100)))
        # Due to uneven scaling 0.0 is mapped to 0.5
        # (uneven scaling means that 0.0 is not mapped to zero but to 0.5 due to the uneven range of INT16)
        self.assertEqual((-0.5, -0.5), out)

        # Test for negative to negative range
        out = convertRange(-5, (-2, -8), (-2, -18))
        self.assertEqual(out, -10)

        # Test for negative to positive range
        out = convertRange(-5, (-2, -8), (2, 18))
        self.assertEqual(out, 10)

        # Test for positive to positive range
        out = convertRange(7, (5, 10), (10, 20))
        self.assertEqual(out, 14)

        # Test for positive to negative range
        out = convertRange(7, (5, 10), (-10, -20))
        self.assertEqual(out, -14)

    def test_convertBits(self):
        # Test input value of zero
        out = tuple(convert2Bits((0, 0), (-1, 1)))
        # Due to uneven scaling 0.0 is mapped to 0.5
        # (uneven scaling means that 0.0 is not mapped to zero but to 0.5 due to the uneven range of INT16)
        self.assertEqual((-0.5, -0.5), out)

        # Use floor for rounding because the convert2Bits method returns floats and does not round.
        out = numpy.floor(convert2Bits((-10, 0, 10), (-10, 10)))
        self.assertEqual(tuple(out), (-2 ** 15, -1, 2 ** 15 - 1))

        # Use floor for rounding because the convert2Bits method returns floats and does not round.
        out = numpy.floor(convert2Bits((0, 0.5, 1), (0, 1)))
        self.assertEqual(tuple(out), (-2 ** 15, -1, 2 ** 15 - 1))

class TestAcquisitionServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwComponents present. Skipping tests.')

        cls.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTRNAL_STORAGE)
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        cls.ASM_manager.terminate()
        time.sleep(0.2)  # wait a bit so that termination calls to the ASM are completed and session is properly closed.

    def setUp(self):
        numpy.random.seed(0)  # Reset seed to have reproducibility of testcases.

        # Change megafield id to prevent testing on existing images/overwriting issues.
        self.MPPC.filename.value = time.strftime("test_images/testing_megafield_id-%Y-%m-%d-%H-%M-%S")

    def tearDown(self):
        pass

    def test_exception_pickling(self):
        """
        Check the exception can be pickled and unpickled (for Pyro4)
        """
        # Get an execption by expecting a wrong status
        try:
            resp = self.ASM_manager.asmApiGetCall("/scan/clock_frequency", 666, raw_response=True)
        except AsmApiException as e:
            ex = e
        else:
            raise self.fail("Failed to get an exception")

        p = pickle.dumps(ex)
        ep = pickle.loads(p)
        self.assertIsInstance(ep, AsmApiException)
        self.assertEqual(str(ex), str(ep))

    def test_get_API_call(self):
        expected_status_code = 200
        clockFrequencyResponse = self.ASM_manager.asmApiGetCall("/scan/clock_frequency", 200, raw_response=True)
        self.assertEqual(clockFrequencyResponse.status_code, expected_status_code)

    def test_post_API_call(self):
        # Tests most basic post call to see if making a post call works correctly.
        # finish_mega_field (can be called multiple times without causing a problem)
        expected_status_code = 204
        status_code = self.ASM_manager.asmApiPostCall("/scan/finish_mega_field", expected_status_code)
        self.assertEqual(status_code, expected_status_code)

    def test_clockVA(self):
        clockFrequencyData = self.ASM_manager.asmApiGetCall("/scan/clock_frequency", 200)
        # Check if clockFrequencyData contains the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not contain the proper key. Expected "
                          "key: 'frequency'.")
        clock_freq = clockFrequencyData['frequency']

        self.assertIsInstance(clock_freq, int)

        self.assertEqual(
                self.ASM_manager.clockPeriod.value,
                1 / clock_freq)

    def test_externalStorageURL_VA(self):
        """Test the external storage URL VA.
        Note: Try to choose examples that are only triggering one of the checks and none of the others!"""

        test_url = 'ftp://username:password@127.0.0.1:5000/asm_service'
        self.ASM_manager.externalStorageURL._set_value(test_url, force_write=True)
        self.assertEqual(self.ASM_manager.externalStorageURL.value, test_url)

        # Special cases that parser does not handle correctly - incorrect splits (1st if statement)
        # Test incorrect characters in host
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL._set_value('ftp://username:password@127.0.0.?1:5000/directory',
                                                           force_write=True)
        # Test '@' duplicated in url
        with self.assertRaises(ValueError):
            # the '@' at this very specific position triggers the url.hostname to be None
            self.ASM_manager.externalStorageURL._set_value('ftp://username:password@127.0.0.1:5000@/directory',
                                                           force_write=True)
        with self.assertRaises(ValueError):
            # the '@' at this very specific position triggers the url.username to be None
            self.ASM_manager.externalStorageURL._set_value('ftp:@//username:password@127.0.0.1:5000/directory',
                                                           force_write=True)
        with self.assertRaises(ValueError):
            # the '@' at this very specific position triggers the url.scheme to be an empty string
            self.ASM_manager.externalStorageURL._set_value('ftp@://username:password@127.0.0.1:5000/directory',
                                                           force_write=True)
        # TODO: example below not captured yet! There is no check yet, that verifies the host being 4 numbers
        # with self.assertRaises(ValueError):
        #     self.ASM_manager.externalStorageURL._set_value('ftp://username:password@127.0.0:.1:5000/directory',
        #                                                            force_write=True)
        # Test additional '/' in host (captured by 6th if statement)
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL._set_value('ftp://username:password@127.0.0/.1:5000/directory',
                                                           force_write=True)

        # Test incorrect scheme (2nd if statement)
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL._set_value('incorscheme://username:password@127.0.0.1:5000/directory',
                                                           force_write=True)

        # Test incorrect character in user name (3rd  if statement)
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL._set_value('ftp://incorrect()user:password@127.0.0.1:5000/directory',
                                                           force_write=True)

        # Test incorrect character in password (4th  if statement)
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL._set_value('ftp://username:incor()password@127.0.0.1:5000/directory',
                                                           force_write=True)

        # Test incorrect character in host (5th  if statement)
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL._set_value('ftp://username:password@incorrecthost.().0.0/directory',
                                                           force_write=True)

        # Test incorrect character in path (6th  if statement)
        with self.assertRaises(ValueError):
            self.ASM_manager.externalStorageURL._set_value('ftp://username:password@127.0.0.1:5000/incorrect:path',
                                                           force_write=True)

    def test_assembleCalibrationMetadata(self):
        MAX_NMBR_POINTS = 4000  # Constant maximum number of setpoints
        # TODO MAX_NMBR_POINT value of 4000 is sufficient for the entire range of the dwell time because the maximum
        #  dwell_time is decreased. However, for the original maximum dwell time of 1e-4 seconds, this value
        #  needs to be increased on the ASM HW to a value above 9000.

        descanner = self.MirrorDescanner
        scanner = self.EBeamScanner
        ASM = self.ASM_manager
        mppc = self.MPPC

        # Test repeatably and randomly over the range so possible floating point error are found.
        for test_repetition in range(0, 1000):
            # Check randomly all the options of the dwell time allowed.
            minimum_dwell_time = scanner.dwellTime.range[0]
            random_dwell_time = numpy.round(numpy.random.random() * scanner.dwellTime.range[1], 9)
            scanner.dwellTime.value = max(random_dwell_time, minimum_dwell_time)

            line_scan_time = scanner.dwellTime.value * mppc.cellCompleteResolution.value[0]
            remainder_scanning_time = line_scan_time % descanner.clockPeriod.value
            if remainder_scanning_time is not 0:
                # Adjusted the flyback time if there is a remainder of scanning time by adding one setpoint to ensure the
                # line scan time is equal to a equal to a whole multiple of the descan clock period
                flyback_time = descanner.physicalFlybackTime + (descanner.clockPeriod.value - remainder_scanning_time)

            # Total line scan time is equal to period of the calibration signal, the frequency is the inverse
            total_line_scan_time = line_scan_time + flyback_time

            calibration_parameters = ASM._assembleCalibrationMetadata()

            # Check types of calibration parameters (output send to the ASM)
            self.assertIsInstance(calibration_parameters, CalibrationLoopParameters)
            self.assertIsInstance(calibration_parameters.descan_rotation, float)
            self.assertIsInstance(calibration_parameters.x_descan_offset, int)
            self.assertIsInstance(calibration_parameters.y_descan_offset, int)
            self.assertIsInstance(calibration_parameters.dwell_time, int)
            self.assertIsInstance(calibration_parameters.scan_rotation, float)
            self.assertIsInstance(calibration_parameters.x_scan_delay, int)
            self.assertIsInstance(calibration_parameters.x_scan_offset, float)
            self.assertIsInstance(calibration_parameters.y_scan_offset, float)

            # Check descan setpoints
            self.assertIsInstance(calibration_parameters.x_descan_setpoints, list)
            self.assertIsInstance(calibration_parameters.y_descan_setpoints, list)
            for x_setpoint, y_setpoint in zip(calibration_parameters.x_descan_setpoints,
                                              calibration_parameters.y_descan_setpoints):
                self.assertIsInstance(x_setpoint, int)
                self.assertIsInstance(y_setpoint, int)

            # Check if the time interval used for the calculation of the descanner setpoints is equal to the
            # descanner clock period.
            x_descan_setpoints_time_interval = total_line_scan_time / len(calibration_parameters.x_descan_setpoints)
            self.assertEqual(numpy.round(x_descan_setpoints_time_interval, 10) % descanner.clockPeriod.value, 0,
                             "Time interval used for the descanner setpoints is not equal to the descanner clock "
                             "period")

            # Check if the total scanning time is equal to the total scanning time send to the ASM
            descan_derived_total_scanning_time = descanner.clockPeriod.value * \
                                                 len(calibration_parameters.x_descan_setpoints)
            if not almost_equal(descan_derived_total_scanning_time, total_line_scan_time, rtol=0, atol=1e-9):
                raise ValueError("Total descan time implied by the setpoints send to the ASM is not equal to the "
                                 "scanning time set by the VA's.")

            # Check scan setpoints
            self.assertLessEqual(len(calibration_parameters.x_scan_setpoints), MAX_NMBR_POINTS)
            self.assertLessEqual(len(calibration_parameters.y_scan_setpoints), MAX_NMBR_POINTS)
            self.assertEqual(len(calibration_parameters.x_scan_setpoints), len(calibration_parameters.y_scan_setpoints))
            self.assertIsInstance(calibration_parameters.x_scan_setpoints, list)
            self.assertIsInstance(calibration_parameters.y_scan_setpoints, list)
            for x_setpoint, y_setpoint in zip(calibration_parameters.x_scan_setpoints,
                                              calibration_parameters.y_scan_setpoints):
                self.assertIsInstance(x_setpoint, float)
                self.assertIsInstance(y_setpoint, float)

            x_scan_setpoints_time_interval = total_line_scan_time / len(calibration_parameters.x_scan_setpoints)
            # Check if the a whole number of clock periods fits in the time interval
            self.assertEqual(numpy.round(x_scan_setpoints_time_interval / ASM.clockPeriod.value, 10) % 1, 0,
                             "Implied sampling period is not a whole multiple of the scanner clock period.")

            if not almost_equal(calibration_parameters.dwell_time % ASM.clockPeriod.value, 0, rtol=0, atol=1e-9):
                raise ValueError("Total scanning time implied by the setpoints send to the ASM is not equal to the "
                                 "scanning time set by the VA's.")

            # Check if the total scanning time is equal to the total scanning time send to the ASM
            scan_total_scanning_time = len(calibration_parameters.x_scan_setpoints) * calibration_parameters.dwell_time\
                                                                                    * ASM.clockPeriod.value

            if not almost_equal(scan_total_scanning_time, total_line_scan_time, rtol=0, atol=1e-9):
                raise ValueError("Total scanning time is not equal to the defined total scanning time.")

    @unittest.skip  # Skip plotting of calibration setpoints, these plots are made for debugging.
    def test_plot_calibration_setpoints(self):
        """
        Test case for inspecting global behaviour of the scan and descan calibration setpoint profiles.
        """
        import matplotlib.pyplot as plt
        self.MirrorDescanner.scanGain.value = (0.5, 0.5)
        self.EBeamScanner.scanGain.value = (0.5, 0.5)
        self.ASM_manager.calibrationMode.value = False
        self.ASM_manager.calibrationMode.value = True

        calibration_parameters = self.ASM_manager._calibrationParameters
        total_line_scan_time = calibration_parameters.dwell_time * self.ASM_manager.clockPeriod.value * \
                               len(calibration_parameters.x_scan_setpoints)

        x_descan_setpoints = numpy.array(calibration_parameters.x_descan_setpoints)
        y_descan_setpoints = numpy.array(calibration_parameters.y_descan_setpoints)
        x_scan_setpoints = numpy.array(calibration_parameters.x_scan_setpoints)
        y_scan_setpoints = numpy.array(calibration_parameters.y_scan_setpoints)

        time_points_descanner = numpy.arange(0, total_line_scan_time, self.MirrorDescanner.clockPeriod.value)
        time_points_scanner = numpy.arange(0, total_line_scan_time,
                                           total_line_scan_time / len(calibration_parameters.x_scan_setpoints))

        fig, axs = plt.subplots(2)
        # Shift scanner values up by 20% of max to make both visible in the same plot.
        upwards_shift = 0.2 * max(x_descan_setpoints)
        axs[0].plot(time_points_descanner, upwards_shift + x_descan_setpoints, "ro", label="Descanner x setpoints")
        axs[1].plot(time_points_descanner, upwards_shift + y_descan_setpoints, "bo", label="Descanner y setpoints")
        axs[0].plot(time_points_scanner, x_scan_setpoints, "rx", label="Scanner x setpoints")
        axs[1].plot(time_points_scanner, y_scan_setpoints, "bx", label="Scanner y setpoints")
        axs[0].legend(loc="upper left")
        axs[1].legend(loc="upper left")
        plt.show()
        self.ASM_manager.calibrationMode.value = False

    def test_checkMegaFieldExists(self):
        """Check if a megafield exists on the external storage or not."""

        # Set dwell time to minimum so scanning of an image is fast.
        self.EBeamScanner.dwellTime.value = self.EBeamScanner.dwellTime.range[0]

        filename = self.MPPC.filename.value  # sub-directories and file name (megafield id)

        dataflow = self.MPPC.data
        image = dataflow.get()  # acquire a small megafield (it is just a single field)
        time.sleep(1)  # wait a bit until image is offloaded to the external storage

        # check if just acquired megafield exists on external storage
        image_received = self.ASM_manager.checkMegaFieldExists(filename)
        self.assertTrue(image_received)

        # request filename containing invalid characters
        with self.assertRaises(ValueError):
            self.ASM_manager.checkMegaFieldExists("sub-dir/wrong_mega_field_id_#@$")

        # request path on external storage containing invalid characters
        with self.assertRaises(ValueError):
            self.ASM_manager.checkMegaFieldExists("wrong_storage_dir_@#$/megafield_id")

        # check for a megafield which should not be on external storage
        image_received = self.ASM_manager.checkMegaFieldExists("test-from-last-century/test/1900-13-40-100-1000-10")
        self.assertFalse(image_received)

    def test_AsmApiException(self):
        # Test if get call raises exceptions properly
        with self.assertRaises(AsmApiException):
            self.ASM_manager.asmApiGetCall("/fake/function/error", 200, raw_response=True)

        # Test if post call raises exceptions properly
        with self.assertRaises(AsmApiException):
            self.ASM_manager.asmApiPostCall("/fake/function/error", 200, raw_response=True)

class TestEBeamScanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwCompetents present. Skipping tests.')

        cls.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTRNAL_STORAGE)
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        cls.ASM_manager.terminate()
        time.sleep(0.2)

    def setUp(self):
        # Change megafield id to prevent testing on existing images/overwriting issues.
        self.MPPC.filename.value = time.strftime("test_images/project/testing_megafield_id-%Y-%m-%d-%H-%M-%S")

    def tearDown(self):
        pass

    def test_resolution_VA(self):
        """
        The setter allows only to enter resolutions with an effective cell size which are a whole multiple of 4.
        """
        min_res = self.EBeamScanner.resolution.range[0][0]
        max_res = self.EBeamScanner.resolution.range[1][0]

        # Check if small resolution values are allowed
        self.EBeamScanner.resolution.value = (min_res, min_res)
        self.assertEqual(self.EBeamScanner.resolution.value, (min_res, min_res))

        # Check if max resolutions can be set
        self.EBeamScanner.resolution.value = (max_res, max_res)
        self.assertEqual(self.EBeamScanner.resolution.value, (max_res, max_res))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.resolution.value = (max_res + 10, max_res + 10)
        # Check if value remains unchanged
        self.assertEqual(self.EBeamScanner.resolution.value, (max_res, max_res))

        with self.assertRaises(IndexError):
            self.EBeamScanner.resolution.value = (min_res - 1, min_res - 1)
        # Check if value remains unchanged
        self.assertEqual(self.EBeamScanner.resolution.value, (max_res, max_res))

        # Check if it is allowed to have non-square resolutions
        self.EBeamScanner.resolution.value = (7 * min_res, 3 * min_res)
        self.assertEqual(self.EBeamScanner.resolution.value, (7 * min_res, 3 * min_res))

        # Check if for requested resolution values, where the effective cell size is not a multiple of 4, the closest
        # correct resolution is returned
        self.EBeamScanner.resolution.value = (3207, 3207)
        self.assertEqual(self.EBeamScanner.resolution.value, (3200, 3200))

        self.EBeamScanner.resolution.value = (6403, 6403)
        self.assertEqual(self.EBeamScanner.resolution.value, (6400, 6400))

        self.EBeamScanner.resolution.value = (6385, 6385)
        self.assertEqual(self.EBeamScanner.resolution.value, (6400, 6400))

    def test_dwellTimeVA(self):
        min_dwellTime = self.EBeamScanner.dwellTime.range[0]
        max_dwellTime = self.EBeamScanner.dwellTime.range[1]

        self.EBeamScanner.dwellTime.value = 0.9 * max_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, 0.9 * max_dwellTime)

        self.EBeamScanner.dwellTime.value = min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 1.2 * max_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 0.5 * min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

    def test_getTicksDwellTime(self):
        dwellTime = 0.9 * self.EBeamScanner.dwellTime.range[1]
        self.EBeamScanner.dwellTime.value = dwellTime
        self.assertIsInstance(self.EBeamScanner.getTicksDwellTime(), int)
        self.assertEqual(self.EBeamScanner.getTicksDwellTime(), int(dwellTime / self.ASM_manager.clockPeriod.value))

    def test_pixelSizeVA(self):
        min_pixelSize = self.EBeamScanner.pixelSize.range[0][0]
        max_pixelSize = self.EBeamScanner.pixelSize.range[1][0]

        # Check if small pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (min_pixelSize * 1.2, min_pixelSize * 1.2)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (min_pixelSize * 1.2, min_pixelSize * 1.2))

        # Check if big pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (max_pixelSize * 0.8, max_pixelSize * 0.8)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize * 0.8, max_pixelSize * 0.8))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.pixelSize.value = (max_pixelSize * 1.6, max_pixelSize * 1.6)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize * 0.8, max_pixelSize * 0.8))

        with self.assertRaises(IndexError):
            self.EBeamScanner.pixelSize.value = (min_pixelSize * 0.6, min_pixelSize * 0.6)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize * 0.8, max_pixelSize * 0.8))

        # Check if setter prevents settings of non-square pixelSize
        self.EBeamScanner.pixelSize.value = (6e-7, 5e-7)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (6e-7, 6e-7))

    def test_rotationVA(self):
        max_rotation = self.EBeamScanner.rotation.range[1]

        # Check if small rotation values are allowed
        self.EBeamScanner.rotation.value = 0.1 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.1 * max_rotation)

        # Check if big rotation values are allowed
        self.EBeamScanner.rotation.value = 0.9 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.rotation.value = 1.1 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

        with self.assertRaises(IndexError):
            self.EBeamScanner.rotation.value = (-0.1 * max_rotation)
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

    def test_scanOffsetVA(self):
        min_scanOffset = self.EBeamScanner.scanOffset.range[0][0]
        max_scanOffset = self.EBeamScanner.scanOffset.range[1][0]

        # Check if small scanOffset values are allowed
        self.EBeamScanner.scanOffset.value = (0.9 * min_scanOffset, 0.9 * min_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * min_scanOffset, 0.9 * min_scanOffset))

        # Check if big scanOffset values are allowed
        self.EBeamScanner.scanOffset.value = (0.9 * max_scanOffset, 0.9 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanOffset.value = (1.2 * max_scanOffset, 1.2 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanOffset.value = (1.2 * min_scanOffset, 1.2 * min_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

    def test_scanGainVA(self):
        min_scanGain = self.EBeamScanner.scanGain.range[0][0]
        max_scanGain = self.EBeamScanner.scanGain.range[1][0]

        # Check if small scanGain values are allowed
        self.EBeamScanner.scanGain.value = (0.9 * min_scanGain, 0.9 * min_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * min_scanGain, 0.9 * min_scanGain))

        # Check if big scanGain values are allowed
        self.EBeamScanner.scanGain.value = (0.9 * max_scanGain, 0.9 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanGain.value = (1.2 * max_scanGain, 1.2 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanGain.value = (1.2 * min_scanGain, 1.2 * min_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

    def test_scanDelayVA(self):
        min_scanDelay = self.EBeamScanner.scanDelay.range[0][0]
        max_scanDelay = self.EBeamScanner.scanDelay.range[1][0]
        min_y_prescan_lines = self.EBeamScanner.scanDelay.range[0][1]
        max_y_prescan_lines = self.EBeamScanner.scanDelay.range[1][1]

        # Set acquisition delay on detector to maximum value as scanner delay needs to be always smaller than the
        # acquisition delay
        self.MPPC.acqDelay.value = self.MPPC.acqDelay.range[1]

        # Check if small scanDelay values are allowed
        self.EBeamScanner.scanDelay.value = (0.1 * max_scanDelay, 0.1 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.1 * max_scanDelay, 0.1 * max_y_prescan_lines))

        # Check if big scanDelay values are allowed
        self.EBeamScanner.scanDelay.value = (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (1.2 * max_scanDelay, 1.2 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (-0.2 * max_scanDelay, -0.2 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        # Check that the scanner delay cannot be greater than the acquisition delay.
        self.EBeamScanner.scanDelay.value = (min_scanDelay, min_y_prescan_lines)
        self.EBeamScanner.parent._mppc.acqDelay.value = 0.5 * max_scanDelay
        self.EBeamScanner.scanDelay.value = (0.6 * max_scanDelay, 0.6 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (min_scanDelay, min_y_prescan_lines))


class TestMirrorDescanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwComponents present. Skipping tests.')

        cls.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTRNAL_STORAGE)
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        cls.ASM_manager.terminate()
        time.sleep(0.2)  # wait a bit so that termination calls to the ASM are completed and session is properly closed.

    def setUp(self):
        numpy.random.seed(0)  # Reset seed to have reproducibility of testcases.

        # Change megafield id to prevent testing on existing images/overwriting issues.
        self.MPPC.filename.value = time.strftime("test_images/project/testing_megafield_id-%Y-%m-%d-%H-%M-%S")

    def tearDown(self):
        pass

    def test_rotation_VA(self):
        max_rotation = self.MirrorDescanner.rotation.range[1]

        # Check if small rotation values are allowed
        self.MirrorDescanner.rotation.value = 0.1 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.1 * max_rotation)

        # Check if big rotation values are allowed
        self.MirrorDescanner.rotation.value = 0.9 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.rotation.value = 1.1 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

        with self.assertRaises(IndexError):
            self.MirrorDescanner.rotation.value = (-0.1 * max_rotation)
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

    def test_scanOffset_VA(self):
        min_scanOffset = self.MirrorDescanner.scanOffset.range[0][0]
        max_scanOffset = self.MirrorDescanner.scanOffset.range[1][0]

        # Check if small scanOffset values are allowed
        self.MirrorDescanner.scanOffset.value = (0.9 * min_scanOffset, 0.9 * min_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.9 * min_scanOffset, 0.9 * min_scanOffset))

        # Check if big scanOffset values are allowed
        self.MirrorDescanner.scanOffset.value = (0.9 * max_scanOffset, 0.9 * max_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanOffset.value = (1.2 * max_scanOffset, 1.2 * max_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanOffset.value = (1.2 * min_scanOffset, 1.2 * min_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

    def test_scanGain_VA(self):
        min_scanGain = self.MirrorDescanner.scanGain.range[0][0]
        max_scanGain = self.MirrorDescanner.scanGain.range[1][0]

        # Check if small scanGain values are allowed
        self.MirrorDescanner.scanGain.value = (0.9 * min_scanGain, 0.9 * min_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * min_scanGain, 0.9 * min_scanGain))

        # Check if big scanGain values are allowed
        self.MirrorDescanner.scanGain.value = (0.9 * max_scanGain, 0.9 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanGain.value = (1.2 * max_scanGain, 1.2 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanGain.value = (1.2 * min_scanGain, 1.2 * min_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

    def test_getXAcqSetpoints(self):
        """
        For multiple settings the x acquisition setpoints are checked on total number of setpoints (length) and the
        expected range of the setpoints.
        """
        descanner = self.MirrorDescanner
        scanner = self.EBeamScanner
        mppc = self.MPPC
        # Change values such that it is easy to follow the calculation by head. (The setpoint have an increase of
        # 20 bits per setpoint in the scanning ramp)
        descanner.scanOffset.value = (0.09767299916075389, 0.09767299916075389)
        descanner.scanGain.value = (0.646387426565957, 0.646387426565957)
        scanner.dwellTime.value = descanner.clockPeriod.value
        def expected_setpoint_length(dwellTime, physcicalFlybackTime, X_cell_size, descan_period):
            # Ceil round the number of scanning points so that if a half descan period is left at least a full extra
            # setpoint is added to allow the scan to be properly finished
            scanning_setpoints = math.ceil(numpy.round((dwellTime * X_cell_size) / descan_period, 10))
            flyback_setpoints = math.ceil(physcicalFlybackTime / descan_period)
            return scanning_setpoints + flyback_setpoints

        # Check default values
        X_descan_setpoints = descanner.getXAcqSetpoints()
        self.assertEqual(len(X_descan_setpoints),
                         expected_setpoint_length(scanner.dwellTime.value,
                                                  descanner.physicalFlybackTime,
                                                  mppc.cellCompleteResolution.value[0],
                                                  descanner.clockPeriod.value))

        self.assertEqual(min(X_descan_setpoints),
                         numpy.floor(convert2Bits(descanner.scanOffset.value,
                                                  numpy.array(descanner.scanOffset.range)[:, 1])[0]))
        self.assertEqual(max(X_descan_setpoints),
                         numpy.floor(convert2Bits(descanner.scanGain.value,
                                                  numpy.array(descanner.scanGain.range)[:, 1])[0]))

        # Check with randomly changing the dwell_time to also catch floating point errors.
        for test_repetition in range(0, 1000):
            minimum_dwell_time = scanner.dwellTime.range[0]
            random_dwell_time = numpy.round(numpy.random.random() * scanner.dwellTime.range[1], 6)
            scanner.dwellTime.value = max(random_dwell_time, minimum_dwell_time)

            X_descan_setpoints = descanner.getXAcqSetpoints()
            self.assertEqual(len(X_descan_setpoints),
                             expected_setpoint_length(scanner.dwellTime.value,
                                                      descanner.physicalFlybackTime,
                                                      mppc.cellCompleteResolution.value[0],
                                                      descanner.clockPeriod.value))

            self.assertEqual(min(X_descan_setpoints),
                             numpy.floor(convert2Bits(descanner.scanOffset.value,
                                                      numpy.array(descanner.scanOffset.range)[:, 1])[0]))
            self.assertEqual(max(X_descan_setpoints),
                             numpy.floor(convert2Bits(descanner.scanGain.value,
                                                      numpy.array(descanner.scanGain.range)[:, 1])[0]))

        # Check values when descan offset equals descan gain meaning a flat line is found for the descan points.
        descanner.scanOffset.value = (0.5, 0.5)
        descanner.scanGain.value = (0.5, 0.5)
        X_descan_setpoints = descanner.getXAcqSetpoints()
        self.assertEqual(len(numpy.unique(numpy.round(X_descan_setpoints))), 1)  # Check if all values are the same
        self.assertEqual(numpy.unique(numpy.round(X_descan_setpoints)),
                         numpy.round(convert2Bits(descanner.scanGain.value,
                                                  numpy.array(descanner.scanGain.range)[:, 1])[0]))
        self.assertEqual(len(X_descan_setpoints),
                         expected_setpoint_length(scanner.dwellTime.value,
                                                  descanner.physicalFlybackTime,
                                                  mppc.cellCompleteResolution.value[0],
                                                  descanner.clockPeriod.value))

        self.assertEqual(min(X_descan_setpoints),
                         numpy.floor(convert2Bits(descanner.scanOffset.value,
                                                  numpy.array(descanner.scanOffset.range)[:, 1])[0]))
        self.assertEqual(max(X_descan_setpoints),
                         numpy.floor(convert2Bits(descanner.scanGain.value,
                                                  numpy.array(descanner.scanGain.range)[:, 1])[0]))

    def test_getYAcqSetpoints(self):
        """
        For multiple settings the y acquisition setpoints are checked on total number of setpoints (length) and the
        expected range of the setpoints.
        """
        descanner = self.MirrorDescanner
        mppc = self.MPPC
        # Change values such that it is easy to follow the calculation
        descanner.scanOffset.value = (0.09767299916075389, 0.09767299916075389)
        descanner.scanGain.value = (0.646387426565957, 0.646387426565957)

        # Check default values
        Y_descan_setpoints = descanner.getYAcqSetpoints()
        self.assertEqual(min(Y_descan_setpoints),
                         math.floor(convert2Bits(descanner.scanOffset.value,
                                                 numpy.array(descanner.scanOffset.range)[:, 1])[1])
                         )
        self.assertEqual(max(Y_descan_setpoints),
                         math.floor(convert2Bits(descanner.scanGain.value[1],
                                                 numpy.array(descanner.scanGain.range)[:, 1]))
                         )
        self.assertEqual(len(Y_descan_setpoints), mppc.cellCompleteResolution.value[1])

        # Check with randomly changing the dwell_time to also catch floating point errors.
        for test_repetition in range(0, 1000):
            minimum_dwell_time = self.EBeamScanner.dwellTime.range[0]
            random_dwell_time = numpy.round(numpy.random.random() * self.EBeamScanner.dwellTime.range[1], 6)
            self.EBeamScanner.dwellTime.value = max(random_dwell_time, minimum_dwell_time)

            # Check with changing gain
            # Change y value to a value which has an irregular difference between the value of thesetpoints which
            # makes it an interesting test case.
            descanner.scanGain.value = (0.1, 0.7)
            Y_descan_setpoints = descanner.getYAcqSetpoints()
            self.assertEqual(min(Y_descan_setpoints),
                             math.floor(convert2Bits(descanner.scanOffset.value,
                                                     numpy.array(descanner.scanOffset.range)[:, 1])[1])
                             )
            self.assertEqual(max(Y_descan_setpoints),
                             math.floor(convert2Bits(descanner.scanGain.value[1],
                                                     numpy.array(descanner.scanGain.range)[:, 1]))
                             )
            self.assertEqual(len(Y_descan_setpoints), mppc.cellCompleteResolution.value[1])

            # Change the cell_size and check if number of setpoints change accordingly.
            mppc.cellCompleteResolution.value = (777, 777)
            Y_descan_setpoints = descanner.getYAcqSetpoints()
            self.assertEqual(min(Y_descan_setpoints),
                             math.floor(convert2Bits(descanner.scanOffset.value,
                                                     numpy.array(descanner.scanOffset.range)[:, 1])[1])
                             )
            self.assertEqual(max(Y_descan_setpoints),
                             math.floor(convert2Bits(descanner.scanGain.value[1],
                                                     numpy.array(descanner.scanGain.range)[:, 1]))
                             )
            self.assertEqual(len(Y_descan_setpoints), mppc.cellCompleteResolution.value[1])

            # Check values when descan offset equals descan gain, meaning a flat line is created for the descan points.
            descanner.scanOffset.value = (0.5, 0.5)
            descanner.scanGain.value = (0.5, 0.5)
            Y_descan_setpoints = descanner.getYAcqSetpoints()
            self.assertEqual(len(numpy.unique(numpy.round(Y_descan_setpoints))), 1)  # Check if all values are the same
            self.assertEqual(numpy.unique(numpy.round(Y_descan_setpoints)),
                             numpy.round(convert2Bits(descanner.scanGain.value,
                                                      numpy.array(descanner.scanGain.range)[:, 1])[1]))
            self.assertEqual(min(Y_descan_setpoints),
                             math.floor(convert2Bits(descanner.scanOffset.value,
                                                     numpy.array(descanner.scanOffset.range)[:, 1])[1])
                             )
            self.assertEqual(max(Y_descan_setpoints),
                             math.floor(convert2Bits(descanner.scanGain.value[1],
                                                     numpy.array(descanner.scanGain.range)[:, 1]))
                             )
            self.assertEqual(len(Y_descan_setpoints), mppc.cellCompleteResolution.value[1])

    @unittest.skip  # Skip plotting of acq setpoints, these plots are made for debugging.
    def test_plot_getAcqSetpoints(self):
        """
        Test case for inspecting global behaviour of the acquistion descan setpoint profiles.
        """
        self.EBeamScanner.dwellTime.value = 4e-6  # Increase dwell time to see steps in the profile better
        self.MirrorDescanner.physicalFlybackTime = 25e-4  # Increase flybacktime to see its effect in the profile better

        X_descan_setpoints = self.MirrorDescanner.getXAcqSetpoints()
        Y_descan_setpoints = self.MirrorDescanner.getYAcqSetpoints()

        fig, axs = plt.subplots(2)
        axs[0].plot(numpy.tile(X_descan_setpoints, 4), "xb", label="x descan setpoints (scanning of 4 rows)")
        axs[1].plot(Y_descan_setpoints[::], "or", label="y descan setpoints (scanning of an entire field image)")
        axs[0].legend(loc="upper left")
        axs[1].legend(loc="upper left")
        plt.show()


class TestMPPC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwCompetents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTRNAL_STORAGE)
        for child in self.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                self.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                self.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                self.MirrorDescanner = child

        # Change megafield id to prevent testing on existing images/overwriting issues.
        self.MPPC.filename.value = time.strftime("test_images/project/testing_megafield_id-%Y-%m-%d-%H-%M-%S")

    def tearDown(self):
        self.ASM_manager.terminate()
        time.sleep(0.2)  # wait a bit so that termination calls to the ASM are completed and session is properly closed.

    def test_filenameVA(self):
        """Testing the filename VA, which contains the path to the image data on the external storage
        (sub-directories) and the filename, which represents the megafield id."""
        self.MPPC.filename.value = "date/project/megafield_id"
        self.assertEqual(self.MPPC.filename.value, "date/project/megafield_id")

        # Raise an error if invalid filename is provided
        with self.assertRaises(ValueError):
            self.MPPC.filename.value = "@testing_file_name"

    def test_acqDelay_VA(self):
        max_acqDelay = self.MPPC.acqDelay.range[1]
        # Set scanner delay to minimum as the detector acquisition delay needs to be always bigger than the
        # scanner delay
        self.EBeamScanner.scanDelay.value = self.EBeamScanner.scanDelay.range[0]
        self.assertEqual(self.EBeamScanner.scanDelay.value, (self.EBeamScanner.scanDelay.range[0]))

        # Check if big acqDelay values are allowed
        self.MPPC.acqDelay.value = max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, max_acqDelay)

        # Check if small acqDelay values are allowed
        self.MPPC.acqDelay.value = 0.1 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, 0.1 * max_acqDelay)

        # Check that the scanner delay cannot be greater than the detector acquisition delay. If the detector
        # acquisition delay is too small it should be automatically changed so its value matches the scanner delay.
        self.MPPC.acqDelay.value = max_acqDelay
        self.EBeamScanner.scanDelay.value = self.EBeamScanner.scanDelay.range[1]
        self.MPPC.acqDelay.value = 0.2 * self.EBeamScanner.scanDelay.range[1][0]
        # Check if acquisition delay is not updated to asked value but to the required value instead.
        self.assertEqual(self.MPPC.acqDelay.value, self.EBeamScanner.scanDelay.range[1][0])
        # Check if the scanner delay remains unchanged.
        self.assertEqual(self.EBeamScanner.scanDelay.value, self.EBeamScanner.scanDelay.range[1])

    def test_overVoltageVA(self):
        max_overVoltage = self.MPPC.overVoltage.range[1]

        # Check if small sensor over voltage values are allowed
        self.MPPC.overVoltage.value = 0.1 * max_overVoltage
        self.assertEqual(self.MPPC.overVoltage.value, 0.1 * max_overVoltage)

        # Check if big sensor over voltage values are allowed
        self.MPPC.overVoltage.value = 0.9 * max_overVoltage
        self.assertEqual(self.MPPC.overVoltage.value, 0.9 * max_overVoltage)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MPPC.overVoltage.value = 1.1 * max_overVoltage
        # Check that previous value is still set
        self.assertEqual(self.MPPC.overVoltage.value, 0.9 * max_overVoltage)

        with self.assertRaises(IndexError):
            self.MPPC.overVoltage.value = (-0.1 * max_overVoltage)
        # Check that previous value is still set
        self.assertEqual(self.MPPC.overVoltage.value, 0.9 * max_overVoltage)

    def test_dataContentVA(self):
        for key in DATA_CONTENT_TO_ASM:
            self.MPPC.dataContent.value = key
            self.assertEqual(self.MPPC.dataContent.value, key)

        # Test incorrect input
        with self.assertRaises(IndexError):
            self.MPPC.dataContent.value = "Incorrect input"
        self.assertEqual(self.MPPC.dataContent.value, key)  # Check if variable remains unchanged

    def test_cellTranslationVA(self):
        """ Testing the cell translation VA (position of the cell image within the overscanned cell image)"""
        # The test values below are also handy for debugging, these values are chosen such that their number corresponds
        # to a row based numbering with the first row of x values being from 10 to 17, and for y values from 100 to
        # 107. This allows to have a human readable check on the tuple structure created while testing input values.
        self.MPPC.cellTranslation.value = \
            tuple(tuple((10 + j, 100 + j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.assertEqual(self.MPPC.cellTranslation.value,
                tuple(tuple((10 + j, 100 + j) for j in range(i, i + self.MPPC.shape[0]))
                      for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0])))

        # Changing the digital translation back to something simple
        self.MPPC.cellTranslation.value = tuple(
                tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))

        # Test missing rows
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0] - 1))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        # Test missing column
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1] - 1))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        # Test wrong number of coordinates
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(
                    tuple((50) for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        # Test wrong type
        # Float for x instead of an int
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50.0, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0])) for i in
                               range(0, self.MPPC.shape[1])))

        # Float for y instead of an int
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, 50.0) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0]))
                               for i in range(0, self.MPPC.shape[1])))

        # Negative number for x
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((-1, 50) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0]))
                               for i in range(0, self.MPPC.shape[1])))

        # Negative number for y
        with self.assertRaises(ValueError):
            self.MPPC.cellTranslation.value = tuple(tuple((50, -1) for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellTranslation.value,
                         tuple(tuple((50, 50) for i in range(0, self.MPPC.shape[0]))
                               for i in range(0, self.MPPC.shape[1])))

    def test_cellDarkOffsetVA(self):
        """ Testing the dark offset VA (background noise per cell)"""
        # The test values below are also handy for debugging, these values are chosen such that their number corresponds
        # to a row based numbering with the first row of values being from 0 to 7. This allows to have a human
        # readable check on the tuple structure created while testing input values.
        self.MPPC.cellDarkOffset.value = \
            tuple(tuple(j for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(j for j in range(i, i + self.MPPC.shape[0]))
                               for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0])))

        # Changing the dark offset back to something simple
        self.MPPC.cellDarkOffset.value = \
            tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))

        # Test missing rows
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(0 for i in range(0, self.MPPC.shape[0] - 1))
                                                   for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test missing column
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(0 for i in range(0, self.MPPC.shape[0]))
                                                   for i in range(0, self.MPPC.shape[1] - 1))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test wrong type, use a float instead of an int
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0]))
                                                   for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test if setter returns the correct error for a negative value
        with self.assertRaises(ValueError):
            self.MPPC.cellDarkOffset.value = tuple(tuple(-1 for i in range(0, self.MPPC.shape[0]))
                                                   for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDarkOffset.value,
                         tuple(tuple(0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

    def test_cellDigitalGainVA(self):
        """ Testing the digital gain VA (amplification value per cell)"""
        # The test values below are also handy for debugging, these values are chosen such that their number corresponds
        # to a row based numbering with the first row of values being from 0.0 to 7.0. This allows to have a human
        # readable check on the tuple structure created while testing input values.
        self.MPPC.cellDigitalGain.value = \
            tuple(tuple(float(j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(float(j) for j in range(i, i + self.MPPC.shape[0]))
                               for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0])))

        # Changing the digital gain back to something simple
        self.MPPC.cellDigitalGain.value = \
            tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1]))

        # Test missing rows
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0] - 1))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test missing column
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1] - 1))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test invalid type, use a string instead of an int or a float
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple('string_type' for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

        # Test if setter returns the correct error for a negative value
        with self.assertRaises(ValueError):
            self.MPPC.cellDigitalGain.value = tuple(tuple(-1.0 for i in range(0, self.MPPC.shape[0]))
                                                    for i in range(0, self.MPPC.shape[1]))
        self.assertEqual(self.MPPC.cellDigitalGain.value,
                         tuple(tuple(0.0 for i in range(0, self.MPPC.shape[0])) for i in range(0, self.MPPC.shape[1])))

    def test_cellCompleteResolutionVA(self):
        min_res = self.MPPC.cellCompleteResolution.range[0][0]
        max_res = self.MPPC.cellCompleteResolution.range[1][0]

        # Check if small resolution values are allowed
        self.MPPC.cellCompleteResolution.value = (min_res + 5, min_res + 5)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (min_res + 5, min_res + 5))

        # Check if big resolutions values are allowed
        self.MPPC.cellCompleteResolution.value = (max_res - 200, max_res - 200)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MPPC.cellCompleteResolution.value = (max_res + 10, max_res + 10)

        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        with self.assertRaises(IndexError):
            self.MPPC.cellCompleteResolution.value = (min_res - 1, min_res - 1)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        # Check if setter allows setting of non-square resolutions.
        self.MPPC.cellCompleteResolution.value = (int(0.2 * max_res), int(0.5 * max_res))
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (int(0.2 * max_res), int(0.5 * max_res)))

    def test_assemble_megafield_metadata(self):
        """
        Test which checks the MegaFieldMetadata object and the correctly ordering (row/column conversions) from the
        VA's to the MegaFieldMetadata object which is passed to the ASM
        """
        megafield_metadata = self.MPPC._assembleMegafieldMetadata()
        self.assertIsInstance(megafield_metadata, MegaFieldMetaData)

        # Test attributes megafield_metadata which contiain only primitive datatypes (int, float, string but not lists)
        self.assertIsInstance(megafield_metadata.mega_field_id, str)
        self.assertIsInstance(megafield_metadata.storage_directory, str)
        self.assertIsInstance(megafield_metadata.custom_data, str)
        self.assertIsInstance(megafield_metadata.stage_position_x, float)
        self.assertIsInstance(megafield_metadata.stage_position_x, float)
        self.assertIsInstance(megafield_metadata.pixel_size, int)
        self.assertIsInstance(megafield_metadata.dwell_time, int)
        self.assertIsInstance(megafield_metadata.x_scan_to_acq_delay, int)
        self.assertIsInstance(megafield_metadata.x_cell_size, int)
        self.assertIsInstance(megafield_metadata.x_eff_cell_size, int)
        self.assertIsInstance(megafield_metadata.x_scan_gain, float)
        self.assertIsInstance(megafield_metadata.x_scan_offset, float)
        self.assertIsInstance(megafield_metadata.x_descan_offset, int)
        self.assertIsInstance(megafield_metadata.y_cell_size, int)
        self.assertIsInstance(megafield_metadata.y_eff_cell_size, int)
        self.assertIsInstance(megafield_metadata.y_scan_gain, float)
        self.assertIsInstance(megafield_metadata.y_scan_offset, float)
        self.assertIsInstance(megafield_metadata.y_descan_offset, int)
        self.assertIsInstance(megafield_metadata.y_prescan_lines, int)
        self.assertIsInstance(megafield_metadata.x_scan_delay, int)
        self.assertIsInstance(megafield_metadata.scan_rotation, float)
        self.assertIsInstance(megafield_metadata.descan_rotation, float)
        self.assertIsInstance(megafield_metadata.sensor_over_voltage, float)

        # Check descan setpoints types
        self.assertIsInstance(megafield_metadata.x_descan_setpoints, list)
        self.assertIsInstance(megafield_metadata.y_descan_setpoints, list)
        for x_setpoint, y_setpoint in zip(megafield_metadata.x_descan_setpoints, megafield_metadata.y_descan_setpoints):
            self.assertIsInstance(x_setpoint, int)
            self.assertIsInstance(y_setpoint, int)

        # Test changing stage position and cell parameters and if these values are correctly represented in the
        # MegaFieldMetaData object.
        # Change stage position to an arbitrary other place.
        new_stage_position = (0.7 * megafield_metadata.stage_position_x + 1.3,
                              0.3 * megafield_metadata.stage_position_y + 1.7)
        self.MPPC._metadata[model.MD_POS] = new_stage_position

        self.MPPC.cellTranslation.value = \
            tuple(tuple((10 + j, 20 + j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.MPPC.cellDarkOffset.value = \
            tuple(tuple(j for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        self.MPPC.cellDigitalGain.value = \
            tuple(tuple(float(j) for j in range(i, i + self.MPPC.shape[0]))
                  for i in range(0, self.MPPC.shape[1] * self.MPPC.shape[0], self.MPPC.shape[0]))

        # Get megafield metadata with changed stage position and new cell parameters and test if values match those in
        # the megafield metadata object.
        megafield_metadata = self.MPPC._assembleMegafieldMetadata()
        self.assertEqual(megafield_metadata.stage_position_x, new_stage_position[0])
        self.assertEqual(megafield_metadata.stage_position_y, new_stage_position[1])
        self.assertEqual(len(megafield_metadata.cell_parameters), self.MPPC.shape[0] * self.MPPC.shape[1])

        for cell_number, individual_cell in enumerate(megafield_metadata.cell_parameters):
            self.assertEqual(individual_cell.digital_gain, cell_number)
            self.assertEqual(individual_cell.x_eff_orig, 10 + cell_number)
            self.assertEqual(individual_cell.y_eff_orig, 20 + cell_number)
            self.assertIsInstance(individual_cell.digital_gain, float)
            self.assertIsInstance(individual_cell.x_eff_orig, int)
            self.assertIsInstance(individual_cell.y_eff_orig, int)


class Test_ASMDataFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwComponents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTRNAL_STORAGE)
        for child in self.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                self.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                self.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                self.MirrorDescanner = child

        # Specify receiving only empty images as default, to speed up the testcases and save memory.
        # Full and thumbnail pictures are only received when explicitly specified in a test.
        self.MPPC.dataContent.value = "empty"

        # Change megafield id to prevent testing on existing images/overwriting issues.
        self.MPPC.filename.value = time.strftime("test_images/project/testing_megafield_id-%Y-%m-%d-%H-%M-%S")

        self._data_received = threading.Event()
        time.sleep(5)  # give the ASM some extra time to empty the offload queue

    def tearDown(self):
        self.MPPC.data.unsubscribe(self.image_received)
        self.MPPC.data.unsubscribe(self.image_2_received)
        if len(self.MPPC.data._listeners) > 0:
            raise IOError("Listeners are not correctly unsubscribed")
        self.ASM_manager.terminate()
        time.sleep(0.2)  # wait a bit so that termination calls to the ASM are completed and session is properly closed.

    def image_received(self, *args):
        """
        Subscriber for test cases which counts the number of times it is notified.
        *args contains the image/data which is received from the subscriber.
        """
        image = args[1]
        # Check resolution of received image
        if image.shape != self.dataContent2Resolution(self.MPPC.dataContent.value):
            raise ValueError("Received the wrong resolution")

        # Check acquisition date of received image. First acquired image determines the acquisition date for all the
        # following cell images hence the acquisition data value is not checked.
        if not isinstance(image.metadata[model.MD_ACQ_DATE], float):
            raise ValueError("Found wrong acquisition date in the metadata of the received image.")

        self.counter += 1
        self._data_received.set()
        logging.info("image received")

    def image_2_received(self, *args):
        """
        Subscriber for test cases which counts the number of times it is notified.
        *args contains the image/data which is received from the subscriber.
        """
        image = args[1]
        # Check resolution of received image
        if image.shape != self.dataContent2Resolution(self.MPPC.dataContent.value):
            raise ValueError("Received the wrong resolution")

        # Check acquisition date of received image. First acquired image determines the acquisition date for all the
        # following cell images hence the acquisition data value is not checked.
        if not isinstance(image.metadata[model.MD_ACQ_DATE], float):
            raise ValueError("Found wrong acquisition date in the metadata of the received image.")

        self.counter2 += 1
        logging.info("image two received")

    def dataContent2Resolution(self, dataContentString):
        """
        Returns the expected resolution for a given data content.
        :param dataContentString (str): with empty, thumbnail or full
        :return: (tuple of two ints): resolution
        """
        data_content_size = {"empty": (1, 1), "thumbnail": (100, 100), "full": self.EBeamScanner.resolution.value}
        return data_content_size[dataContentString]

    def test_get_field(self):
        """Test acquiring a single field image."""
        dataflow = self.MPPC.data

        image = dataflow.get()
        self.assertIsInstance(image, model.DataArray)

    def test_dataContent_get_field(self):
        """
        Tests if the appropriate image size is returned after calling with empty, thumbnail or full image as
        datacontent by using the get field image method.
        """
        for key, value in DATA_CONTENT_TO_ASM.items():
            dataflow = self.MPPC.data
            image = dataflow.get(dataContent=key)
            self.assertIsInstance(image, model.DataArray)
            self.assertEqual(image.shape, self.dataContent2Resolution(key))

    def test_subscribe_mega_field(self):
        field_images = (3, 4)
        self.counter = 0
        self.MPPC.dataContent.value = "thumbnail"

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        # Iterate over each field, and wait for it to be acquired before acquiring the next one
        for x in range(field_images[0]):
            for y in range(field_images[1]):
                self._data_received.clear()
                # Here the stage would move to the right position
                dataflow.next((x, y))
                # Allow 3 seconds per field image to be acquired
                if not self._data_received.wait(3):
                    self.fail("No data received after 3s for field %d, %d" % (x, y))

        # Wait a bit to allow some processing and receive images.
        dataflow.unsubscribe(self.image_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)

        time.sleep(1.5 * field_images[0] * field_images[1])  # Allow 1 second per field image to offload.

    def test_subscribe_mega_field_queued_next(self):
        """ Test acquiring a megafield by queueing all next's"""
        # The implementation supports calling next() multiple times in a row, and they
        # are sent to the ASM one at a time. It's probably not really useful
        # as typically we'd always want to wait for the stage to move before
        # acquiring the next field.
        field_images = (3, 4)
        self.counter = 0
        self.MPPC.dataContent.value = "empty"

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        # Check it's fine to pass numpy ints
        for x, y in numpy.ndindex(field_images[::-1]):
            dataflow.next((x, y))

        # Wait a bit to allow some processing and receive images.
        time.sleep(1.5 * field_images[0] * field_images[1])  # Allow 1.5 seconds per field image to offload.
        dataflow.unsubscribe(self.image_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)

    def test_next_error(self):
        """ Test passing wrong value to next()"""
        self.counter = 0
        self.MPPC.dataContent.value = "empty"

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        with self.assertRaises(ValueError):
            dataflow.next((0, 0.5))

        with self.assertRaises(ValueError):
            dataflow.next((-1, 0))

        dataflow.unsubscribe(self.image_received)

    def test_termination(self):
        """ Terminate detector and acquisition thread during acquisition and test if acquisition does not continue."""
        field_images = (3, 4)
        termination_point = (1, 3)
        self.counter = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                if x == termination_point[0] and y == termination_point[1]:
                    logging.debug("Send terminating command")
                    self.MPPC.terminate()
                    time.sleep(1.5)
                    self.assertEqual(self.MPPC.acq_queue.qsize(), 0,
                                     "Queue was not cleared properly and is not empty")
                    time.sleep(0.5)
                    break

                dataflow.next((x, y))
                time.sleep(1.5)

        self.assertFalse(self.MPPC._acq_thread.is_alive())
        self.assertEqual((termination_point[0] * field_images[1]) + termination_point[1], self.counter)
        dataflow.unsubscribe(self.image_received)

    def test_multiple_subscriptions(self):
        field_images = (3, 4)
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)
        dataflow.subscribe(self.image_2_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        time.sleep(1.5 * field_images[0] * field_images[1])  # Wait a bit to allow some processing and receive images.
        dataflow.unsubscribe(self.image_received)
        dataflow.unsubscribe(self.image_2_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)
        self.assertEqual(self.counter, self.counter2)

    def test_late_second_subscription(self):
        field_images = (3, 4)
        add_second_subscription = (1, 3)
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                if x == add_second_subscription[0] and y == add_second_subscription[1]:
                    # Wait until all the old items in the queue are handled so the outcome of the first counter is known
                    logging.debug("Adding second subscription")
                    dataflow.subscribe(self.image_2_received)
                dataflow.next((x, y))
                time.sleep(1.5)

        dataflow.unsubscribe(self.image_received)
        dataflow.unsubscribe(self.image_2_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)  # Check early subscriber
        self.assertEqual(
                ((field_images[1] - add_second_subscription[1]) * field_images[0])
                + field_images[0] - add_second_subscription[0],
                self.counter2)  # Check late subscriber

    def test_get_and_subscribe(self):
        """
        Check it's not possible to do a .get() during a megafield acquisition
        """
        field_images = (3, 4)
        self.counter = 0
        self.counter2 = 0

        dataflow = self.MPPC.data
        dataflow.subscribe(self.image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        # Acquire single field without unsubscribing listener (expect error)
        with self.assertRaises(Exception):
            image = dataflow.get()

        time.sleep(1.5 * field_images[0] * field_images[1])  # Wait a bit to allow some processing and receive images.
        self.assertEqual(field_images[0] * field_images[1], self.counter)
        dataflow.unsubscribe(self.image_received)

        # Acquire single field after unsubscribing listener
        image = dataflow.get()
        self.assertIsInstance(image, model.DataArray)

        # Start acquiring second mega field
        dataflow.subscribe(self.image_2_received)
        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))

        time.sleep(1.5 * field_images[0] * field_images[1])  # Wait a bit to allow some processing and receive images.
        dataflow.unsubscribe(self.image_2_received)
        time.sleep(0.5)
        self.assertEqual(field_images[0] * field_images[1], self.counter)
        self.assertEqual(field_images[0] * field_images[1], self.counter2)

    def test_error_get(self):
        """Test that exceptions are raised if wrong settings are not caught in the wrapper, but on the ASM."""

        self.MPPC.filename.value = time.strftime("testing_megafield_id-%Y-%m-%d-%H-%M-%S")
        dataflow = self.MPPC.data

        init_cellCompleteRes = self.MPPC.cellCompleteResolution.value
        # force a value on a VA which will raise an error on the ASM
        # use ._value in order to circumvent the setter checks
        self.MPPC.cellCompleteResolution._value = (1100, 1100)  # max value allowed on HW is 1000; force it!

        # raise exception due to wrong value in settings uploaded to ASM during the start of the acquisition
        with self.assertRaises(AsmApiException):
            image = dataflow.get()

        # now check that with a correct value an acquisition can be performed again
        self.MPPC.cellCompleteResolution.value = init_cellCompleteRes
        image = dataflow.get()
        self.assertIsInstance(image, model.DataArray)

    def test_error_subscribe(self):
        """Test that exceptions are raised if wrong settings are not caught in the wrapper, but on the ASM.
        Additionally, check that subscribers are properly unsubscribed after an exception occurred."""

        self.MPPC.filename.value = time.strftime("testing_megafield_id-%Y-%m-%d-%H-%M-%S")
        dataflow = self.MPPC.data

        img_queue_1 = queue.Queue()
        img_queue_2 = queue.Queue()

        def image_received_1(dataflow, da):
            img_queue_1.put(da)
            logging.debug("Image received by subscriber 1.")

        def image_received_2(dataflow, da):
            img_queue_2.put(da)
            logging.debug("Image received by subscriber 2.")

        init_cellCompleteRes = self.MPPC.cellCompleteResolution.value
        # force a value on a VA which will raise an error on the ASM
        # use ._value in order to circumvent the setter checks
        self.MPPC.cellCompleteResolution._value = (1100, 1100)  # max value allowed on HW is 1000; force it!

        # raise exception due to wrong value in settings uploaded to ASM during the start of the acquisition
        with self.assertRaises(AsmApiException):
            dataflow.subscribe(image_received_1)

        # put back allowed value
        self.MPPC.cellCompleteResolution.value = init_cellCompleteRes

        # add new subscriber
        dataflow.subscribe(image_received_2)
        dataflow.next((0, 0))
        img = img_queue_2.get(timeout=3)  # read the image
        # check queue is empty after reading the image (no further images..)
        assert img_queue_2.empty()
        # check that the previous subscriber did unsubscribe properly (image_received_1)
        assert img_queue_1.empty()
        dataflow.unsubscribe(image_received_2)

        # check it is not possible to request an image when there is no subscriber
        with self.assertRaises(ValueError):
            dataflow.next((0, 1))
        time.sleep(2)
        assert img_queue_1.empty()
        assert img_queue_2.empty()


if __name__ == '__main__':
    unittest.main()
