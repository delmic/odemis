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
import os
import pickle
import queue
import threading
import time
import unittest

import matplotlib

matplotlib.use("TkAgg")  # GUI-backend
import matplotlib.pyplot as plt
import numpy

from odemis import model
from odemis.util import testing

try:
    from odemis.driver.technolution import AcquisitionServer, convertRange, AsmApiException, DATA_CONTENT_TO_ASM, \
        VOLT_RANGE, I16_SYM_RANGE
    from technolution_asm.models import CalibrationLoopParameters
    from technolution_asm.models.mega_field_meta_data import MegaFieldMetaData
    technolution_available = True
except ImportError as err:
    logging.info("technolution_asm package not found with error: {}".format(err))
    technolution_available = False

# Set logger level to debug to observe all the output (useful when a test fails)
logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW = 1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

URL = "http://localhost:8080/v2"

# Configuration of the children of the AcquisitionServer object
CONFIG_SCANNER = {"name": "MultiBeam Scanner", "role": "multibeam"}
CONFIG_DESCANNER = {"name": "Mirror Descanner", "role": "descanner"}
CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}
CHILDREN_ASM = {"EBeamScanner"   : CONFIG_SCANNER,
                "MirrorDescanner": CONFIG_DESCANNER,
                "MPPC"           : CONFIG_MPPC}
EXTERNAL_STORAGE = {"host"     : "localhost",
                   "username" : "username",
                   "password" : "password",
                   "directory": "asm_service"}


class TestAuxiliaryFunc(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not technolution_available:
            raise unittest.SkipTest(f"Skipping the technolution tests, correct libraries to perform the tests"
                                    f"are not available.")

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


class TestAcquisitionServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not technolution_available:
            raise unittest.SkipTest(f"Skipping the technolution tests, correct libraries to perform the tests"
                                    f"are not available.")

        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwComponents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTERNAL_STORAGE)
        for child in self.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                self.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                self.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                self.MirrorDescanner = child

        numpy.random.seed(0)  # Reset seed to have reproducibility of testcases.

        # Change megafield id to prevent testing on existing images/overwriting issues.
        self.MPPC.filename.value = time.strftime("test_images/testing_megafield_id-%Y-%m-%d-%H-%M-%S")

    def tearDown(self):
        self.ASM_manager.terminate()
        time.sleep(0.2)  # wait a bit so that termination calls to the ASM are completed and session is properly closed.

    def test_exception_pickling(self):
        """Check the exception can be pickled and unpickled (for Pyro4)."""
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
        """Testing get call to ASM API."""
        expected_status_code = 200
        clockFrequencyResponse = self.ASM_manager.asmApiGetCall("/scan/clock_frequency", 200, raw_response=True)
        self.assertEqual(clockFrequencyResponse.status_code, expected_status_code)

    def test_post_API_call(self):
        """Testing post call to ASM API."""
        # finish_mega_field (can be called multiple times without causing a problem)
        expected_status_code = 204
        status_code = self.ASM_manager.asmApiPostCall("/scan/finish_mega_field", expected_status_code)
        self.assertEqual(status_code, expected_status_code)

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
        """Check that the calibration metadata is correctly assembled and of correct type."""
        scanner = self.EBeamScanner
        ASM = self.ASM_manager

        scanner.dwellTime.value = 1.e-06

        calibration_parameters = ASM._assembleCalibrationMetadata()

        # Check types of calibration parameters
        self.assertIsInstance(calibration_parameters, CalibrationLoopParameters)
        self.assertIsInstance(calibration_parameters.descan_rotation, float)
        self.assertIsInstance(calibration_parameters.x_descan_offset, int)
        self.assertIsInstance(calibration_parameters.y_descan_offset, int)
        self.assertIsInstance(calibration_parameters.dwell_time, int)
        self.assertIsInstance(calibration_parameters.scan_rotation, float)
        self.assertIsInstance(calibration_parameters.x_scan_delay, int)
        self.assertIsInstance(calibration_parameters.x_scan_offset, float)
        self.assertIsInstance(calibration_parameters.y_scan_offset, float)

        self.assertIsInstance(calibration_parameters.x_descan_setpoints, list)
        self.assertIsInstance(calibration_parameters.y_descan_setpoints, list)
        for x_setpoint, y_setpoint in zip(calibration_parameters.x_descan_setpoints,
                                          calibration_parameters.y_descan_setpoints):
            self.assertIsInstance(x_setpoint, int)
            self.assertIsInstance(y_setpoint, int)

        self.assertIsInstance(calibration_parameters.x_scan_setpoints, list)
        self.assertIsInstance(calibration_parameters.y_scan_setpoints, list)
        for x_setpoint, y_setpoint in zip(calibration_parameters.x_scan_setpoints,
                                          calibration_parameters.y_scan_setpoints):
            self.assertIsInstance(x_setpoint, float)
            self.assertIsInstance(y_setpoint, float)

    @unittest.skip  # for debugging only
    def test_plot_calibration_setpoints(self):
        """Plot the calibration scanner and descanner setpoint profiles.
        x scanner: sine
        y scanner: sawtooth
        x descanner: sine
        y descanner: flat line
        """
        self.EBeamScanner.dwellTime.value = 5.e-06
        self.MirrorDescanner.scanOffset.value = (0.1, 0.0)
        self.MirrorDescanner.scanAmplitude.value = (0.5, 0.0)
        self.EBeamScanner.scanOffset.value = (0.2, 0.1)
        self.EBeamScanner.scanAmplitude.value = (0.4, 0.5)
        self.ASM_manager.calibrationMode.value = True

        calibration_parameters = self.ASM_manager._calibrationParameters
        total_line_scan_time = calibration_parameters.dwell_time * self.EBeamScanner.clockPeriod.value * \
                               len(calibration_parameters.x_scan_setpoints)

        x_descan_setpoints = numpy.array(calibration_parameters.x_descan_setpoints)
        y_descan_setpoints = numpy.array(calibration_parameters.y_descan_setpoints)
        x_scan_setpoints = numpy.array(calibration_parameters.x_scan_setpoints)
        y_scan_setpoints = numpy.array(calibration_parameters.y_scan_setpoints)

        timestamps_descanner = numpy.arange(0, total_line_scan_time, self.MirrorDescanner.clockPeriod.value)
        timestamps_scanner = numpy.arange(0, total_line_scan_time,
                                          total_line_scan_time / len(calibration_parameters.x_scan_setpoints))

        fig, axs = plt.subplots(2)
        fig.tight_layout(pad=3.0)  # add some space between subplots so that the axes labels are not hidden
        # Shift scanner values up by 20% of max to make both visible in the same plot.
        upwards_shift = 0.2 * max(x_descan_setpoints)
        axs[0].plot(timestamps_descanner, upwards_shift + x_descan_setpoints, "ro", markersize=0.5,
                    label="Descanner x setpoints")
        axs[0].plot(timestamps_descanner, upwards_shift + y_descan_setpoints, "bo", markersize=0.5,
                    label="Descanner y setpoints")
        axs[0].set_xlabel("line scanning time [sec]")
        axs[0].set_ylabel("setpoints [bits]")

        axs[1].plot(timestamps_scanner, x_scan_setpoints, "rx", markersize=0.5, label="Scanner x setpoints")
        axs[1].plot(timestamps_scanner, y_scan_setpoints, "bx", markersize=0.5, label="Scanner y setpoints")
        axs[1].set_xlabel("line scanning time [sec]")
        axs[1].set_ylabel("setpoints [V]")

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
        """Test exceptions are raised for incorrect ASM API calls."""
        # Test if get call raises exceptions properly
        with self.assertRaises(AsmApiException):
            self.ASM_manager.asmApiGetCall("/fake/function/error", 200, raw_response=True)

        # Test if post call raises exceptions properly
        with self.assertRaises(AsmApiException):
            self.ASM_manager.asmApiPostCall("/fake/function/error", 200, raw_response=True)


class TestEBeamScanner(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not technolution_available:
            raise unittest.SkipTest(f"Skipping the technolution tests, correct libraries to perform the tests"
                                    f"are not available.")

        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or Hw components present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTERNAL_STORAGE)
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
        time.sleep(0.2)

    def test_clockPeriod_VA(self):
        """Testing the clock period VA. It reads the clock period from the ASM."""
        clockFrequencyData = self.ASM_manager.asmApiGetCall("/scan/clock_frequency", 200)
        # Check if clockFrequencyData contains the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not contain the proper key. Expected "
                          "key: 'frequency'.")
        clock_freq = clockFrequencyData['frequency']

        self.assertIsInstance(clock_freq, int)
        self.assertEqual(self.EBeamScanner.clockPeriod.value, 1 / clock_freq)

    def test_resolution_VA(self):
        """Testing the resolution VA. It is the resolution of a single field image. A single field is resembled by
        the cell images. The number of the cell images is defined by the shape of the mppc detector. The resolution
        (effective cell size) does not reflect overscanned pixels. The setter only allows to enter resolutions with
        an effective cell size which are a whole multiple of 4."""
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

    def test_dwellTime_VA(self):
        """Testing the dwell time VA. It is the time for acquiring one pixel."""
        min_dwellTime = self.EBeamScanner.dwellTime.range[0]
        max_dwellTime = self.EBeamScanner.dwellTime.range[1]

        self.EBeamScanner.dwellTime.value = 0.9 * max_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, 0.9 * max_dwellTime)
        # check dwell time on VA is same as on metadata on component
        self.assertEqual(self.EBeamScanner.getMetadata()[model.MD_DWELL_TIME], self.EBeamScanner.dwellTime.value)

        self.EBeamScanner.dwellTime.value = min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)
        # check dwell time on VA is same as on metadata on component
        self.assertEqual(self.EBeamScanner.getMetadata()[model.MD_DWELL_TIME], self.EBeamScanner.dwellTime.value)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 1.2 * max_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 0.5 * min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

    def test_getTicksDwellTime(self):
        """Check that the dwell time as specified on the corresponding VA is correctly translated into system
        clock period ticks."""
        dwellTime = 0.9 * self.EBeamScanner.dwellTime.range[1]
        self.EBeamScanner.dwellTime.value = dwellTime
        self.assertIsInstance(self.EBeamScanner.getTicksDwellTime(), int)
        self.assertEqual(self.EBeamScanner.getTicksDwellTime(), int(dwellTime / self.EBeamScanner.clockPeriod.value))

    def test_getCenterScanVolt(self):
        """Check that center of the scanning ramp is correctly calculated based on the
        scan offset and scan amplitude. It also includes a conversion from arbitrary units to volt."""
        # TODO test for other combinations - to +, + to -, - to - etc.
        scan_start = (0.5, 0.5)
        scan_amp = (0.2, 0.2)
        self.EBeamScanner.scanOffset.value = scan_start
        self.EBeamScanner.scanAmplitude.value = scan_amp

        center = self.EBeamScanner.getCenterScanVolt()
        exp_scan_start = (0.5, 0.5)
        exp_scan_end = (0.7, 0.7)  # offset + amplitude
        exp_center = tuple(convertRange((numpy.array(exp_scan_start) + numpy.array(exp_scan_end)) / 2,
                                        numpy.array(self.EBeamScanner.scanAmplitude.range)[:, 1],
                                        VOLT_RANGE))  # [V])

        self.assertIsInstance(center, tuple)
        self.assertIsInstance(center[0], float)
        self.assertEqual(len(center), 2)  # x and y
        testing.assert_tuple_almost_equal(center, exp_center, places=10)

    def test_getGradientScanVolt(self):
        """Check that gradient of the scanning ramp is correctly calculated based on the
        scan amplitude. It also includes a conversion from arbitrary units to volt."""
        # TODO test for other combinations - to +, + to -, - to - etc.
        scan_amp = (0.2, 0.2)
        self.EBeamScanner.scanAmplitude.value = scan_amp
        self.MPPC.cellCompleteResolution.value = (800, 800)

        gradient = self.EBeamScanner.getGradientScanVolt()
        exp_scan_amp = (0.2, 0.2)
        resolution = numpy.array(self.MPPC.cellCompleteResolution.value)
        steps = resolution - 1  # number of steps to go from start to end of scanning ramp
        exp_gradient = tuple(convertRange(exp_scan_amp / steps,
                                          numpy.array(self.EBeamScanner.scanAmplitude.range)[:, 1],
                                          VOLT_RANGE))  # [V])

        self.assertIsInstance(gradient, tuple)
        self.assertIsInstance(gradient[0], float)
        self.assertEqual(len(gradient), 2)  # x and y
        testing.assert_tuple_almost_equal(gradient, exp_gradient, places=10)

    def test_pixelSize_VA(self):
        """Testing the pixel size VA. Physical size of one pixel."""
        min_pixelSize = self.EBeamScanner.pixelSize.range[0][0]
        max_pixelSize = self.EBeamScanner.pixelSize.range[1][0]

        # Check if small pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (min_pixelSize * 1.2, min_pixelSize * 1.2)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (min_pixelSize * 1.2, min_pixelSize * 1.2))
        # check pixel size on VA is same as on metadata on component
        self.assertEqual(self.EBeamScanner.getMetadata()[model.MD_DWELL_TIME], self.EBeamScanner.dwellTime.value)

        # Check if big pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (max_pixelSize * 0.8, max_pixelSize * 0.8)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize * 0.8, max_pixelSize * 0.8))
        # check pixel size on VA is same as on metadata on component
        self.assertEqual(self.EBeamScanner.getMetadata()[model.MD_PIXEL_SIZE], self.EBeamScanner.pixelSize.value)

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

    def test_rotation_VA(self):
        """Testing the rotation VA. Reflects the rotation of the scanning direction of the multibeam scanner."""
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

    def test_scanOffset_VA(self):
        """Testing the scanner offset VA. It defines the start of the sawtooth scanning signal for the scanner."""
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

        # Check if int value is allowed
        self.EBeamScanner.scanOffset.value = (0, 0)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0, 0))

    def test_scanAmplitude_VA(self):
        """Testing the scanner gain VA. It defines the heights of the sawtooth scanning signal for the scanner."""
        min_scan_amplitude = self.EBeamScanner.scanAmplitude.range[0][0]
        max_scan_amplitude = self.EBeamScanner.scanAmplitude.range[1][0]

        # Check if small scan amplitude values are allowed
        self.EBeamScanner.scanAmplitude.value = (0.9 * min_scan_amplitude, 0.9 * min_scan_amplitude)
        self.assertEqual(self.EBeamScanner.scanAmplitude.value, (0.9 * min_scan_amplitude, 0.9 * min_scan_amplitude))

        # Check if big scan amplitude values are allowed
        self.EBeamScanner.scanAmplitude.value = (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude)
        self.assertEqual(self.EBeamScanner.scanAmplitude.value, (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanAmplitude.value = (1.2 * max_scan_amplitude, 1.2 * max_scan_amplitude)
        self.assertEqual(self.EBeamScanner.scanAmplitude.value, (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanAmplitude.value = (1.2 * min_scan_amplitude, 1.2 * min_scan_amplitude)
        self.assertEqual(self.EBeamScanner.scanAmplitude.value, (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude))

        # Check if int value is allowed
        self.EBeamScanner.scanAmplitude.value = (0, 0)
        self.assertEqual(self.EBeamScanner.scanAmplitude.value, (0, 0))

    def test_scanDelay_VA(self):
        """Testing of the scanner delay VA. It is the delay between the start the acquisition trigger and the
        start of scanner to start scanning via the ebeam scanner."""
        min_scanDelay = self.EBeamScanner.scanDelay.range[0][0]
        max_scanDelay = self.EBeamScanner.scanDelay.range[1][0]
        min_y_prescan_lines = self.EBeamScanner.scanDelay.range[0][1]
        max_y_prescan_lines = self.EBeamScanner.scanDelay.range[1][1]

        self.MPPC.acqDelay.value = self.MPPC.acqDelay.range[1]  # set to max

        # Check if small delay values are allowed
        self.EBeamScanner.scanDelay.value = (0.1 * max_scanDelay, 0.1 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.1 * max_scanDelay, 0.1 * max_y_prescan_lines))

        # Check if big delay values are allowed
        self.EBeamScanner.scanDelay.value = (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (1.2 * max_scanDelay, 1.2 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (-0.2 * max_scanDelay, -0.2 * max_y_prescan_lines)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_y_prescan_lines))

        # Check scanner delay equal to acquisition delay is allowed
        self.MPPC.acqDelay.value = self.MPPC.acqDelay.range[1]  # set to max
        self.EBeamScanner.scanDelay.value = self.EBeamScanner.scanDelay.range[0]  # set to min
        self.MPPC.acqDelay.value = 0.5 * self.EBeamScanner.scanDelay.range[1][0]
        self.EBeamScanner.scanDelay.value = (0.5 * self.EBeamScanner.scanDelay.range[1][0], 0)  # set same value

        # Check that a scanner delay greater than the acquisition delay raises an error
        self.EBeamScanner.scanDelay.value = (min_scanDelay, min_y_prescan_lines)
        self.EBeamScanner.parent._mppc.acqDelay.value = 0.5 * max_scanDelay
        # try scanner delay > acquisition delay
        with self.assertRaises(ValueError):
            self.EBeamScanner.scanDelay.value = (0.6 * max_scanDelay, 0.6 * max_y_prescan_lines)
        # Check that the scan delay remains unchanged.
        self.assertEqual(self.EBeamScanner.scanDelay.value, (min_scanDelay, min_y_prescan_lines))

    def test_getCalibrationSetpoints(self):
        """Check that the calibration setpoints are correctly assembled."""
        scanner = self.EBeamScanner

        # sine for x, sawtooth for y
        scanner.scanOffset.value = (0.1, 0)  # offset of the sine on x; offset of the sawtooth on y
        scanner.scanAmplitude.value = (0.5, 0)  # amplitude of sine on x; heigths of the sawtooth on y
        scanner.dwellTime.value = 5e-06

        # Total line scan time is equal to period of the calibration signal, the frequency is the inverse
        total_line_scan_time = self.MPPC.getTotalLineScanTime()
        # TODO Do we need the flyback included for calibration of the scan delay?

        x_scan_setpoints, y_scan_setpoints, calib_dwell_time_ticks = \
            scanner.getCalibrationSetpoints(total_line_scan_time)

        # use almost equal as the max/min setpoints can be equal or smaller than the absolute amplitude
        # Note: There is not necessarily a setpoint at the max/min amplitude of the sine.
        #
        # *               *  *
        #   *           *      *
        # -----------------------------------
        #     *      *           *
        #       *  *
        #
        # TODO evaluate what accuracy is needed for the calibration
        # check that the minimum x setpoint of the sine equals offset - amplitude in [V]
        self.assertAlmostEqual(min(x_scan_setpoints),
                               convertRange(scanner.scanOffset.value[0] - scanner.scanAmplitude.value[0],
                                            numpy.array(scanner.scanAmplitude.range)[:, 1],
                                            VOLT_RANGE), 5)

        # check that the maximum setpoint of the sine equals offset + amplitude in [V]
        self.assertAlmostEqual(max(x_scan_setpoints),
                               convertRange(scanner.scanOffset.value[0] + scanner.scanAmplitude.value[0],
                                            numpy.array(scanner.scanAmplitude.range)[:, 1],
                                            VOLT_RANGE), 5)

        # check that the minimum x setpoint of the sawtooth equals the offset in [V]
        self.assertAlmostEqual(min(y_scan_setpoints),
                               convertRange(scanner.scanOffset.value[1],
                                            numpy.array(scanner.scanOffset.range)[:, 1],
                                            VOLT_RANGE), 5)

        # check that the maximum setpoint of the sawtooth equals offset + amplitude in [V]
        self.assertAlmostEqual(max(y_scan_setpoints),
                               convertRange(scanner.scanOffset.value[1] + scanner.scanAmplitude.value[1],
                                            numpy.array(scanner.scanAmplitude.range)[:, 1],
                                            VOLT_RANGE), 5)

        # check that the calibration dwell time in seconds is an integer multiple of the scanner clock period
        calib_dwell_time = numpy.round(total_line_scan_time / len(x_scan_setpoints), 10)  # [sec]
        self.assertAlmostEqual(calib_dwell_time % scanner.clockPeriod.value, 0, 10)  # floating point errors

        # check if the total line scan time matches with the time given by the calculated setpoints
        total_time_setpoints = len(x_scan_setpoints) * calib_dwell_time_ticks * scanner.clockPeriod.value  # [sec]
        self.assertAlmostEqual(total_time_setpoints, total_line_scan_time, 10)  # floating point errors

        # check that not more than max possible total number of setpoints
        MAX_NMBR_POINTS = 4000  # maximum number of setpoints possible
        self.assertLessEqual(len(x_scan_setpoints), MAX_NMBR_POINTS)
        self.assertLessEqual(len(y_scan_setpoints), MAX_NMBR_POINTS)
        # check that same number of setpoints in x in y
        self.assertEqual(len(x_scan_setpoints), len(y_scan_setpoints))

    def test_getCalibrationDwellTime(self):
        """Check that the calibration dwell time is correctly calculated."""
        scanner = self.EBeamScanner

        # sine for x, sawtooth for y
        scanner.scanOffset.value = (0.1, 0)  # offset of the sine on x; offset of the sawtooth on y
        scanner.scanAmplitude.value = (0.5, 0)  # amplitude of sine on x; heigths of the sawtooth on y
        scanner.dwellTime.value = 5e-06  # acquisition dwell time (integer multiple of scanner clock period)

        # get the total line scan time, which is equal to the period of the calibration signal
        total_line_scan_time = self.MPPC.getTotalLineScanTime()
        # TODO Do we need the flyback included for calibration of the scan delay?

        # Calculate the total number of setpoints and the calibration dwell time (update frequency of the setpoints).
        calib_dwell_time_ticks, number_setpoints = scanner.getCalibrationDwellTime(total_line_scan_time)

        # check that the calibration time * the clock period * number of setpoints matches the line scan time
        self.assertAlmostEqual(calib_dwell_time_ticks * scanner.clockPeriod.value * number_setpoints,
                               total_line_scan_time, 10)  # floating point errors

        # check that the calibration dwell time in seconds is an integer multiple of the scanner clock period
        calib_dwell_time = numpy.round(total_line_scan_time / number_setpoints, 10)  # [sec]
        self.assertAlmostEqual(calib_dwell_time % scanner.clockPeriod.value, 0, 10)  # floating point errors

        # TODO Do we need the flyback included for calibration of the scan delay?
        #   However, the total line scan time is already always an integer multiple of the descan clock period
        #   independent of the dwell time.
        # Check that, if it is not possible to find a calibration dwell time for a given acquisition dwell time,
        # the descanner clock period is returned as calibration dwell time in ticks.
        # check that the calibration dwell time [ticks] * scanner clock period [sec] is equal to the
        # descanner clock period [sec]
        # self.assertAlmostEqual(calib_dwell_time_ticks * scanner.clockPeriod.value,
        #                        descanner.clockPeriod.value, 10)  # floating point errors

    @unittest.skip  # for debugging only
    def test_plot_calibration_setpoints(self):
        """Plot the calibration scanner setpoint profiles.
        x scanner: sine
        y scanner: sawtooth
        """
        self.EBeamScanner.dwellTime.value = 5e-6
        self.EBeamScanner.scanOffset.value = (0.1, 0.2)  # center of the sine on x; start of the sawtooth on y
        self.EBeamScanner.scanAmplitude.value = (0.5, 0.3)  # amplitude of the sine on x; heights of the sawtooth on y

        # Total line scan time is equal to period of the calibration signal, the frequency is the inverse
        total_line_scan_time = self.MPPC.getTotalLineScanTime()
        # TODO Do we need the flyback included for calibration of the scan delay?

        x_scan_setpoints, y_scan_setpoints, calib_dwell_time_ticks = \
            self.EBeamScanner.getCalibrationSetpoints(total_line_scan_time)

        x_scan_setpoints = numpy.array(x_scan_setpoints)
        y_scan_setpoints = numpy.array(y_scan_setpoints)

        timestamps_scanner = numpy.arange(0, total_line_scan_time, total_line_scan_time / len(x_scan_setpoints))

        fig, axs = plt.subplots(1)

        axs.plot(timestamps_scanner, x_scan_setpoints, "rx", markersize=0.5, label="Scanner x setpoints")
        axs.plot(timestamps_scanner, y_scan_setpoints, "bx", markersize=0.5, label="Scanner y setpoints")
        axs.set_xlabel("scanning time [sec]")
        axs.set_ylabel("setpoints [V]")

        axs.legend(loc="upper left")
        plt.show()


class TestMirrorDescanner(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not technolution_available:
            raise unittest.SkipTest(f"Skipping the technolution tests, correct libraries to perform the tests"
                                    f"are not available.")

        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwComponents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTERNAL_STORAGE)
        for child in self.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                self.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                self.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                self.MirrorDescanner = child
        numpy.random.seed(0)  # Reset seed to have reproducibility of testcases.

        # Change megafield id to prevent testing on existing images/overwriting issues.
        self.MPPC.filename.value = time.strftime("test_images/project/testing_megafield_id-%Y-%m-%d-%H-%M-%S")

    def tearDown(self):
        self.ASM_manager.terminate()
        time.sleep(0.2)  # wait a bit so that termination calls to the ASM are completed and session is properly closed.

    def test_clockPeriod_VA(self):
        """Testing the clock period VA. It reads the clock period for the descanner mirrors."""
        clockFrequencyData = self.ASM_manager.asmApiGetCall("/scan/descan_control_frequency", 200)
        # Check if clockFrequencyData contains the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not contain the proper key. Expected "
                          "key: 'frequency'.")
        clock_freq = clockFrequencyData['frequency']

        self.assertIsInstance(clock_freq, int)
        self.assertEqual(self.MirrorDescanner.clockPeriod.value, 1 / clock_freq)

    def test_rotation_VA(self):
        """Testing the rotation VA. Reflects the rotation of the scanning direction of the descan mirror."""
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
        """Testing the descanner offset VA. It defines the start of the sawtooth scanning signal for the descanner."""
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

        # Check if int value is allowed
        self.EBeamScanner.scanOffset.value = (0, 0)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0, 0))

    def test_scanAmplitude_VA(self):
        """Testing the descanner gain VA. It defines the heights of the sawtooth scanning signal for the descanner."""
        min_scan_amplitude = self.MirrorDescanner.scanAmplitude.range[0][0]
        max_scan_amplitude = self.MirrorDescanner.scanAmplitude.range[1][0]

        # Check if small scan amplitude values are allowed
        self.MirrorDescanner.scanAmplitude.value = (0.9 * min_scan_amplitude, 0.9 * min_scan_amplitude)
        self.assertEqual(self.MirrorDescanner.scanAmplitude.value, (0.9 * min_scan_amplitude, 0.9 * min_scan_amplitude))

        # Check if big scan amplitude values are allowed
        self.MirrorDescanner.scanAmplitude.value = (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude)
        self.assertEqual(self.MirrorDescanner.scanAmplitude.value, (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanAmplitude.value = (1.2 * max_scan_amplitude, 1.2 * max_scan_amplitude)
        self.assertEqual(self.MirrorDescanner.scanAmplitude.value, (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude))

        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanAmplitude.value = (1.2 * min_scan_amplitude, 1.2 * min_scan_amplitude)
        self.assertEqual(self.MirrorDescanner.scanAmplitude.value, (0.9 * max_scan_amplitude, 0.9 * max_scan_amplitude))

        # Check if int value is allowed
        self.EBeamScanner.scanAmplitude.value = (0, 0)
        self.assertEqual(self.EBeamScanner.scanAmplitude.value, (0, 0))

    def test_physicalFlybackTime_VA(self):
        """Testing the physical flyback time VA. The physical flyback time is the time the descanner has to
        move back to the starting position for a new line scan."""
        max_flyback = self.MirrorDescanner.physicalFlybackTime.range[1]

        # Check if small sensor over voltage values are allowed
        self.MirrorDescanner.physicalFlybackTime.value = 0.1 * max_flyback
        self.assertEqual(self.MirrorDescanner.physicalFlybackTime.value, 0.1 * max_flyback)

        # Check if big sensor over voltage values are allowed
        self.MirrorDescanner.physicalFlybackTime.value = 0.9 * max_flyback
        self.assertEqual(self.MirrorDescanner.physicalFlybackTime.value, 0.9 * max_flyback)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.physicalFlybackTime.value = 1.1 * max_flyback
        # Check that previous value is still set
        self.assertEqual(self.MirrorDescanner.physicalFlybackTime.value, 0.9 * max_flyback)

        with self.assertRaises(IndexError):
            self.MirrorDescanner.physicalFlybackTime.value = (-0.1 * max_flyback)
        # Check that previous value is still set
        self.assertEqual(self.MirrorDescanner.physicalFlybackTime.value, 0.9 * max_flyback)

    def test_getXAcqSetpoints(self):
        """Check that the setpoints in y are calculated correctly."""
        scanner = self.EBeamScanner
        descanner = self.MirrorDescanner
        mppc = self.MPPC

        # TODO have example - to +, + to -. + to +, - to - for both offset and amplitude (8 combinations)

        # example here: setpoints have an increase of 20 bits per setpoint in the scanning ramp.
        descanner.scanOffset.value = (0.09767299916075389, 0.09767299916075389)
        descanner.scanAmplitude.value = (0.646387426565957, 0.646387426565957)
        scanner.dwellTime.value = descanner.clockPeriod.value

        x_descan_setpoints = descanner.getXAcqSetpoints()

        # check that the number of setpoints in x equals the size of an overscanned cell image + flyback
        self.assertEqual(len(x_descan_setpoints), self.number_expected_setpoints_x(scanner.dwellTime.value,
                                                                                   descanner.physicalFlybackTime.value,
                                                                                   mppc.cellCompleteResolution.value[0],
                                                                                   descanner.clockPeriod.value))

        # check that the minimum setpoint (start of the scanning ramp) equals the offset in [bits]
        self.assertEqual(min(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanOffset.range)[:, 0],
                                                  I16_SYM_RANGE)))

        # check that the maximum setpoint (end of scanning ramp) equals offset + amplitude in [bits]
        self.assertEqual(max(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanAmplitude.value[0] + descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanAmplitude.range)[:, 0],
                                                  I16_SYM_RANGE)))

    def test_setpoints_x_floating_point_error(self):
        """Check that floating point errors in the calculation of the scanning time are handled when
        calculating the setpoints."""
        scanner = self.EBeamScanner
        descanner = self.MirrorDescanner
        mppc = self.MPPC

        # dwell time * cell complete size / descan period = scanning time (3.e-05 * 900 / 1e-05 = 2699.9999999999995)
        scanner.dwellTime.value = 3.e-5
        mppc.cellCompleteResolution.value = (900, 900)

        x_descan_setpoints = descanner.getXAcqSetpoints()

        # check that the number of setpoints in x equals the size of an overscanned cell image + flyback
        self.assertEqual(len(x_descan_setpoints), self.number_expected_setpoints_x(scanner.dwellTime.value,
                                                                                   descanner.physicalFlybackTime.value,
                                                                                   mppc.cellCompleteResolution.value[0],
                                                                                   descanner.clockPeriod.value))

        # check that the minimum setpoint (start of the scanning ramp) equals the offset in [bits]
        self.assertEqual(min(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanOffset.range)[:, 0],
                                                  I16_SYM_RANGE)))

        # check that the maximum setpoint (end of scanning ramp) equals offset + amplitude in [bits]
        self.assertEqual(max(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanAmplitude.value[0] + descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanAmplitude.range)[:, 0],
                                                  I16_SYM_RANGE)))

    def test_setpoints_x_remainder_scanning_time(self):
        """Check that the setpoints are correctly calculated also when the scanning time
        (dwell time * number overscanned pixels) is not an integer multiple of the descan period (update rate)."""
        scanner = self.EBeamScanner
        descanner = self.MirrorDescanner
        mppc = self.MPPC

        # dwell time * cell complete size / descan period = scanning time (2.2e-05*900/1.e-05 = 1979.9999999999995)
        scanner.dwellTime.value = 2.2e-5

        x_descan_setpoints = descanner.getXAcqSetpoints()

        # check that the number of setpoints in x equals the size of an overscanned cell image + flyback
        self.assertEqual(len(x_descan_setpoints), self.number_expected_setpoints_x(scanner.dwellTime.value,
                                                                                   descanner.physicalFlybackTime.value,
                                                                                   mppc.cellCompleteResolution.value[0],
                                                                                   descanner.clockPeriod.value))

        # check that the minimum setpoint (start of the scanning ramp) equals the offset in [bits]
        self.assertEqual(min(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanOffset.range)[:, 0],
                                                  I16_SYM_RANGE)))

        # check that the maximum setpoint (end of scanning ramp) equals offset + amplitude in [bits]
        self.assertEqual(max(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanAmplitude.value[0] + descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanAmplitude.range)[:, 0],
                                                  I16_SYM_RANGE)))

    def test_flat_line_x(self):
        """Check that if the amplitude is 0, a flat line of acquisition setpoints is returned."""
        scanner = self.EBeamScanner
        descanner = self.MirrorDescanner
        mppc = self.MPPC

        # no descanning = scanning amplitude equals 0 (= flat line of setpoints at offset level)
        descanner.scanOffset.value = (0.5, 0.5)  # random offset
        descanner.scanAmplitude.value = (0.0, 0.0)  # no amplitude

        x_descan_setpoints = descanner.getXAcqSetpoints()

        # check that the setpoints are all of the same value (flat line)
        self.assertEqual(len(numpy.unique(numpy.round(x_descan_setpoints))), 1)
        # check that the setpoints have the correct value (offset level)
        self.assertEqual(numpy.unique(numpy.round(x_descan_setpoints)),
                         numpy.round(convertRange(descanner.scanAmplitude.value[0] + descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanAmplitude.range)[:, 0],
                                                  I16_SYM_RANGE)))

        # check that the number of setpoints in x equals the size of an overscanned cell image + flyback
        self.assertEqual(len(x_descan_setpoints), self.number_expected_setpoints_x(scanner.dwellTime.value,
                                                                                   descanner.physicalFlybackTime.value,
                                                                                   mppc.cellCompleteResolution.value[0],
                                                                                   descanner.clockPeriod.value))

        # check that the minimum setpoint (start of the scanning ramp) equals the offset in [bits]
        self.assertEqual(min(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanOffset.range)[:, 0],
                                                  I16_SYM_RANGE)))

        # check that the maximum setpoint (end of scanning ramp) equals offset + amplitude in [bits]
        self.assertEqual(max(x_descan_setpoints),
                         numpy.floor(convertRange(descanner.scanAmplitude.value[0] + descanner.scanOffset.value[0],
                                                  numpy.array(descanner.scanAmplitude.range)[:, 0],
                                                  I16_SYM_RANGE)))

    def test_max_amp_x(self):
        """Check that if the amplitude is maximal (=1) that the returned max setpoint is of value 2**15 - 1."""
        descanner = self.MirrorDescanner

        # amplitude = 1 is mapped to 2**15, however, ASM only accepts 2**15 - 1
        # check that amplitude = 1 is mapped to 2**15 - 1 before sending it to the ASM
        descanner.scanOffset.value = (0.0, 0.0)
        descanner.scanAmplitude.value = (1.0, 1.0)  # max amplitude

        x_descan_setpoints = descanner.getXAcqSetpoints()

        # check that the maximum setpoint (end of scanning ramp) is reduced by one bit 2**15 -1 = 32767
        # as this is the maximum value the ASM accepts
        self.assertEqual(max(x_descan_setpoints), 32767)

    def number_expected_setpoints_x(self, dwellTime, physcicalFlybackTime, X_cell_size, descan_period):
        """Calculate the number of setpoints in x for an overscanned cell image including flyback."""
        # Ceil round the number of scanning points so that if a half descan period is left at least a full extra
        # setpoint is added to allow the scan to be properly finished
        scanning_setpoints = math.ceil(numpy.round((dwellTime * X_cell_size) / descan_period, 10))
        flyback_setpoints = math.ceil(physcicalFlybackTime / descan_period)

        return scanning_setpoints + flyback_setpoints

    def test_getYAcqSetpoints(self):
        """Check that the setpoints in y are calculated correctly."""
        scanner = self.EBeamScanner
        descanner = self.MirrorDescanner
        mppc = self.MPPC

        # TODO have example - to +, + to -. + to +, - to - for both offset and amplitude (8 combinations)

        # example here: setpoints have an increase of 20 bits per setpoint in the scanning ramp.
        descanner.scanOffset.value = (0.09767299916075389, 0.09767299916075389)
        descanner.scanAmplitude.value = (0.646387426565957, 0.646387426565957)
        scanner.dwellTime.value = descanner.clockPeriod.value  # dwell time = descanner period

        y_descan_setpoints = descanner.getYAcqSetpoints()

        # check that the minimum setpoint (start of the scanning ramp) equals the offset in [bits]
        self.assertEqual(min(y_descan_setpoints),
                         math.floor(convertRange(descanner.scanOffset.value[1],
                                                 numpy.array(descanner.scanOffset.range)[:, 1],
                                                 I16_SYM_RANGE)))

        # check that the maximum setpoint (end of scanning ramp) equals offset + amplitude in [bits]
        self.assertEqual(max(y_descan_setpoints),
                         math.floor(convertRange(descanner.scanAmplitude.value[1] + descanner.scanOffset.value[1],
                                                 numpy.array(descanner.scanAmplitude.range)[:, 1],
                                                 I16_SYM_RANGE)))

        # check that the number of setpoints in y equals the size of an overscanned cell image
        self.assertEqual(len(y_descan_setpoints), mppc.cellCompleteResolution.value[1])

    def test_flat_line_y(self):
        """Check that if the amplitude is 0, a flat line of acquisition setpoints is returned."""
        descanner = self.MirrorDescanner
        mppc = self.MPPC

        # no descanning = scanning amplitude equals 0 (= flat line of setpoints at offset level)
        descanner.scanOffset.value = (0.5, 0.5)  # random offset
        descanner.scanAmplitude.value = (0.0, 0.0)  # no amplitude

        y_descan_setpoints = descanner.getYAcqSetpoints()

        # check that the setpoints are all of the same value (flat line)
        self.assertEqual(len(numpy.unique(numpy.round(y_descan_setpoints))), 1)
        # check that the setpoints have the correct value (offset level)
        self.assertEqual(numpy.unique(numpy.round(y_descan_setpoints)),
                         numpy.round(convertRange(descanner.scanAmplitude.value[1] + descanner.scanOffset.value[1],
                                                  numpy.array(descanner.scanAmplitude.range)[:, 1],
                                                  I16_SYM_RANGE)))

        # check that the minimum setpoint (start of the scanning ramp) equals the offset in [bits]
        self.assertEqual(min(y_descan_setpoints),
                         math.floor(convertRange(descanner.scanOffset.value[1],
                                                 numpy.array(descanner.scanOffset.range)[:, 1],
                                                 I16_SYM_RANGE)))

        # check that the maximum setpoint (end of scanning ramp) equals offset + amplitude in [bits]
        self.assertEqual(max(y_descan_setpoints),
                         math.floor(convertRange(descanner.scanAmplitude.value[1] + descanner.scanOffset.value[1],
                                                 numpy.array(descanner.scanAmplitude.range)[:, 1],
                                                 I16_SYM_RANGE)))

        # check that the number of setpoints in y equals the size of an overscanned cell image
        self.assertEqual(len(y_descan_setpoints), mppc.cellCompleteResolution.value[1])

    def test_max_amp_y(self):
        """Check that if the amplitude is maximal (=1) that the returned max setpoint is of value 2**15 - 1."""
        descanner = self.MirrorDescanner

        # amplitude = 1 is mapped to 2**15, however, ASM only accepts 2**15 - 1
        # check that amplitude = 1 is mapped to 2**15 - 1 before sending it to the ASM
        descanner.scanOffset.value = (0.0, 0.0)
        descanner.scanAmplitude.value = (1.0, 1.0)  # max amplitude

        y_descan_setpoints = descanner.getXAcqSetpoints()

        # check that the maximum setpoint (end of scanning ramp) is reduced by one bit 2**15 -1 = 32767
        # as this is the maximum value the ASM accepts
        self.assertEqual(max(y_descan_setpoints), I16_SYM_RANGE[1] - 1)

    @unittest.skip  # for debugging only
    def test_plot_getAcqSetpoints(self):
        """Plot the acquisition descan setpoint profiles."""
        self.EBeamScanner.dwellTime.value = 5e-6  # Increase dwell time to see steps in the profile better
        self.MirrorDescanner.physicalFlybackTime.value = 25e-4  # Increase to see its effect in the profile better

        x_descan_setpoints = self.MirrorDescanner.getXAcqSetpoints()
        y_descan_setpoints = self.MirrorDescanner.getYAcqSetpoints()

        fig, axs = plt.subplots(2)
        fig.tight_layout(pad=3.0)  # add some space between subplots so that the axes labels are not hidden
        axs[0].plot(x_descan_setpoints, "xb", markersize=0.5,
                    label="x descan setpoints (scanning of one row within a cell image)")
        axs[0].set_xlabel("overscanned cell image row plus flyback [us]")
        axs[0].set_ylabel("x setpoints [bits]")
        axs[1].plot(y_descan_setpoints[::], "or", markersize=0.5,
                    label="y descan setpoints (scanning of one column within a cell image)")
        axs[1].set_xlabel("overscanned cell image column [px]")
        axs[1].set_ylabel("y setpoints [bits]")
        axs[0].legend(loc="upper left")
        axs[1].legend(loc="upper left")
        plt.show()

    def test_getCalibrationSetpoints(self):
        """Check that the calibration setpoints are correctly assembled."""

        descanner = self.MirrorDescanner
        scanner = self.EBeamScanner

        # sine for x, flat line at 0 in y
        descanner.scanOffset.value = (0.1, 0)  # offset of the sine on x
        descanner.scanAmplitude.value = (0.5, 0)  # amplitude of sine on x
        scanner.dwellTime.value = 5e-06

        # Total line scan time is equal to period of the calibration signal, the frequency is the inverse
        total_line_scan_time = self.MPPC.getTotalLineScanTime()
        # TODO Do we need the flyback included for calibration of the scan delay?

        x_descan_setpoints, y_descan_setpoints = descanner.getCalibrationSetpoints(total_line_scan_time)

        # check that the minimum x setpoint of the sine equals offset - amplitude in [bits]
        # use almost equal as the max/min setpoints can be equal or smaller than the absolute amplitude
        # Note: There is not necessarily a setpoint at the max/min amplitude of the sine.
        #
        # *               *  *
        #   *           *      *
        # -----------------------------------
        #     *      *           *
        #       *  *
        #
        self.assertAlmostEqual(min(x_descan_setpoints),
                               math.floor(convertRange(descanner.scanOffset.value[0] - descanner.scanAmplitude.value[0],
                                                       numpy.array(descanner.scanOffset.range)[:, 1],
                                                       I16_SYM_RANGE)), -10)

        # check that the maximum setpoint of the sine equals offset + amplitude in [bits]
        self.assertAlmostEqual(max(x_descan_setpoints),
                              math.floor(convertRange(descanner.scanOffset.value[0] + descanner.scanAmplitude.value[0],
                                                      numpy.array(descanner.scanAmplitude.range)[:, 1],
                                                      I16_SYM_RANGE)), -10)

        # check that the y setpoints are all of the same value (flat line)
        self.assertEqual(len(numpy.unique(numpy.round(y_descan_setpoints))), 1)
        # check that the setpoints have the correct value (offset level)
        self.assertEqual(numpy.unique(numpy.round(y_descan_setpoints)),
                         numpy.round(convertRange(descanner.scanAmplitude.value[1] + descanner.scanOffset.value[1],
                                                  numpy.array(descanner.scanAmplitude.range)[:, 1],
                                                  I16_SYM_RANGE)))

        # check that time interval between two setpoints is equal to the descanner clock period
        x_setpoints_time_interval = total_line_scan_time / len(x_descan_setpoints)
        self.assertAlmostEqual(x_setpoints_time_interval, descanner.clockPeriod.value, 10)  # floating point errors

        # check that same number of setpoints in x in y
        self.assertEqual(len(x_descan_setpoints), len(y_descan_setpoints))

    @unittest.skip  # for debugging only
    def test_plot_calibration_setpoints(self):
        """Plot the calibration descanner setpoint profiles.
        x descanner: sine
        y descanner: flat line
        """
        self.EBeamScanner.dwellTime.value = 5e-6
        self.MirrorDescanner.scanOffset.value = (0.1, 0.0)  # center of the sine on x; y flat line
        self.MirrorDescanner.scanAmplitude.value = (0.5, 0.0)  # amplitude of the sine on x; y flat line

        # Total line scan time is equal to period of the calibration signal, the frequency is the inverse
        total_line_scan_time = self.MPPC.getTotalLineScanTime()
        # TODO Do we need the flyback included for calibration of the scan delay?

        x_descan_setpoints, y_descan_setpoints = self.MirrorDescanner.getCalibrationSetpoints(total_line_scan_time)

        x_descan_setpoints = numpy.array(x_descan_setpoints)
        y_descan_setpoints = numpy.array(y_descan_setpoints)

        timestamps_descanner = numpy.arange(0, total_line_scan_time, self.MirrorDescanner.clockPeriod.value)

        fig, axs = plt.subplots(1)
        axs.plot(timestamps_descanner, x_descan_setpoints, "ro", markersize=0.5,
                    label="Descanner x setpoints")
        axs.plot(timestamps_descanner, y_descan_setpoints, "bo", markersize=0.5,
                    label="Descanner y setpoints")
        axs.set_xlabel("scanning time [sec]")
        axs.set_ylabel("setpoints [bits]")

        axs.legend(loc="upper left")
        plt.show()


class TestMPPC(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not technolution_available:
            raise unittest.SkipTest(f"Skipping the technolution tests, correct libraries to perform the tests"
                                    f"are not available.")

        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwCompetents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTERNAL_STORAGE)
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

    def test_filename_VA(self):
        """Testing the filename VA, which contains the path to the image data on the external storage
        (sub-directories) and the filename, which represents the megafield id."""
        self.MPPC.filename.value = "date/project/megafield_id"
        self.assertEqual(self.MPPC.filename.value, "date/project/megafield_id")

        # Raise an error if invalid filename is provided
        with self.assertRaises(ValueError):
            self.MPPC.filename.value = "@testing_file_name"

        # Raise an error if filename is longer than 50 characters
        with self.assertRaises(ValueError):
            self.MPPC.filename.value = "a-very-very-very-very-very-very-very-very-long-filename"

    def test_acqDelay_VA(self):
        """Testing the acquisition delay VA, which defines the delay between the trigger signal to start the
        acquisition, and the start of the recording with the mppc detector."""
        max_acqDelay = self.MPPC.acqDelay.range[1]

        self.EBeamScanner.scanDelay.value = self.EBeamScanner.scanDelay.range[0]  # set to min
        self.assertEqual(self.EBeamScanner.scanDelay.value, (self.EBeamScanner.scanDelay.range[0]))

        # Check if big delay values are allowed
        self.MPPC.acqDelay.value = max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, max_acqDelay)

        # Check if small delay values are allowed
        self.MPPC.acqDelay.value = 0.1 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, 0.1 * max_acqDelay)

        # Check acquisition delay equals scanner delay is allowed
        self.EBeamScanner.scanDelay.value = (self.MPPC.acqDelay.range[0], self.MPPC.acqDelay.range[0])
        self.MPPC.acqDelay.value = self.MPPC.acqDelay.range[0]

        # Check that a acquisition delay smaller than the scanner delay raises an error
        self.MPPC.acqDelay.value = max_acqDelay  # set to max
        self.EBeamScanner.scanDelay.value = self.EBeamScanner.scanDelay.range[1]  # set to max
        # try acquisition delay < scanner delay
        with self.assertRaises(ValueError):
            self.MPPC.acqDelay.value = 0.2 * self.EBeamScanner.scanDelay.value[0]
        # Check that the acquisition delay remains unchanged.
        self.assertEqual(self.MPPC.acqDelay.value, max_acqDelay)

    def test_overVoltage_VA(self):
        """Testing the overvoltage VA. It regulates the sensitivity of the mppc sensor. The ASM then adds over voltage
        to the breakdown voltage which increases the gain of the sensor output per photon received."""
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

    def test_dataContent_VA(self):
        """Testing the data content VA. It defines the size of the returned DataArray."""
        for key in DATA_CONTENT_TO_ASM:
            self.MPPC.dataContent.value = key
            self.assertEqual(self.MPPC.dataContent.value, key)

        # Test incorrect input
        with self.assertRaises(IndexError):
            self.MPPC.dataContent.value = "Incorrect input"
        self.assertEqual(self.MPPC.dataContent.value, key)  # Check if variable remains unchanged

    def test_cellTranslation_VA(self):
        """Testing the cell translation VA (position of the cell image within the overscanned cell image)."""
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

    def test_cellDarkOffset_VA(self):
        """Testing the dark offset VA (background noise per cell)."""
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

    def test_cellDigitalGain_VA(self):
        """Testing the digital gain VA (amplification value per cell)."""
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

    def test_cellCompleteResolution_VA(self):
        """Testing the cell complete resolution VA (size of the cell image + overscanned pixels)."""
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

    def test_getTotalLineScanTime(self):
        """Check that the time for scanning one line of pixels in a single field image is calculated correctly."""

        # choose example value so that line scan time is not integer multiple of descanner clock period
        self.EBeamScanner.dwellTime.value = 430e-09
        acq_dwell_time = self.EBeamScanner.dwellTime.value
        resolution = self.MPPC.cellCompleteResolution.value[0]  # including overscanned pixels
        flyback_time = self.MirrorDescanner.physicalFlybackTime.value

        # calculate the expected time for one line scan with the above HW settings
        line_scan_time = acq_dwell_time * resolution
        # check if line scan time is integer multiple of descanner clock period
        remainder_scanning_time = line_scan_time % self.MirrorDescanner.clockPeriod.value
        if remainder_scanning_time != 0:
            # make line scan time an integer multiple of the clock period by adding the extra time needed
            flyback_time = flyback_time + (self.MirrorDescanner.clockPeriod.value - remainder_scanning_time)
        exp_line_scan_time = numpy.round(line_scan_time + flyback_time, 9)  # round to prevent floating point errors

        # get the time for one line scan for the current HW settings
        line_scan_time = self.MPPC.getTotalLineScanTime()

        self.assertEqual(exp_line_scan_time, line_scan_time)

    def test_frameDurationVA(self):
        """Testing the frame duration (single field acquisition time) VA."""

        # check that the frame duration changes, when the cell complete resolution changes
        # set the cell complete resolution to some good value
        self.MPPC.cellCompleteResolution.value = (self.MPPC.cellCompleteResolution.range[1][0],
                                                  self.MPPC.cellCompleteResolution.range[1][1])
        # get the frame duration
        orig_frame_dur = self.MPPC.frameDuration.value
        curr_cell_complete_res = self.MPPC.cellCompleteResolution.value
        # change the cell complete resolution, which should trigger the frame duration to be adjusted
        self.MPPC.cellCompleteResolution.value = (curr_cell_complete_res[0] - 100, curr_cell_complete_res[1] - 100)
        # get the new frame duration
        new_frame_dur = self.MPPC.frameDuration.value
        # check that the previous frame duration is greater than the new frame duration
        self.assertGreater(orig_frame_dur, new_frame_dur)

        # check that the frame duration changes, when dwell time changes
        orig_frame_dur = self.MPPC.frameDuration.value
        # set the dwell time to minimum
        self.EBeamScanner.dwellTime.value = self.EBeamScanner.dwellTime.range[0]
        # set dwell time to maximum
        self.EBeamScanner.dwellTime.value = self.EBeamScanner.dwellTime.range[1]
        # get the new value
        new_frame_dur = self.MPPC.frameDuration.value
        # check that the new frame duration is greater than the previous frame duration
        self.assertGreater(new_frame_dur, orig_frame_dur)

    def test_assemble_megafield_metadata(self):
        """Test which checks the MegaFieldMetadata object and the correctly ordering (row/column conversions) from the
        VA's to the MegaFieldMetadata object which is passed to the ASM."""
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
        if not technolution_available:
            raise unittest.SkipTest(f"Skipping the technolution tests, correct libraries to perform the tests"
                                    f"are not available.")

        if TEST_NOHW:
            raise unittest.SkipTest('No simulator for the ASM or HwComponents present. Skipping tests.')

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        self.ASM_manager = AcquisitionServer("ASM", "asm", URL, CHILDREN_ASM, EXTERNAL_STORAGE)
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
        """Tests if the correct image size is returned after requesting images with size "empty", "thumbnail"
        or "full" image on the .dataContent VA."""
        for key, value in DATA_CONTENT_TO_ASM.items():
            dataflow = self.MPPC.data
            image = dataflow.get(dataContent=key)
            self.assertIsInstance(image, model.DataArray)
            self.assertEqual(image.shape, self.dataContent2Resolution(key))

    def test_subscribe_mega_field(self):
        """Test acquiring a small megafield image."""
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
        """Test acquiring a megafield by queueing all next's."""
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
        """Test passing incorrect field numbers in next() call."""
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
        """Terminate detector and acquisition thread during acquisition and test if acquisition does not continue."""
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
        """Test acquiring a small megafield image with multiple listeners."""
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
        """Test acquiring a small megafield image with second listener starting to listen while the acquisition
        is already on-going."""
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
        """Check it's not possible to do a .get() call during an on-going megafield acquisition. It should be not
        possible to call get() and subscribe() at the same time."""
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
