#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 31 Jun 2016

@author: Éric Piel
Testing class for driver.ueye .

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''

import logging
from odemis.driver import ueye
from odemis import model
import os
import unittest
from unittest.case import skip

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, VirtualTestSynchronized


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CLASS = ueye.Camera
KWARGS = dict(name="camera", role="ccd", device=None, max_res=None, transp=[2, -1])


class StaticTestUEye(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = KWARGS


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestUEye(VirtualTestCam, unittest.TestCase):
    """
    Test directly the UEye class.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No camera HW present. Skipping tests.')

        super(TestUEye, cls).setUpClass()

    @classmethod
    def tearDownClass(cls):
        if TEST_NOHW:
            return
        super(TestUEye, cls).tearDownClass()

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No simulator available")
        VirtualTestCam.setUp(self)


# @skip("simple")
class TestSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    camera_type = CLASS
    camera_kwargs = KWARGS

    @classmethod
    def setUpClass(cls):
        if not TEST_NOHW:
            VirtualTestCam.setUpClass()

    @classmethod
    def tearDownClass(self):
        if not TEST_NOHW:
            VirtualTestCam.tearDown(self)

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No simulator available")
        VirtualTestCam.setUp(self)


class TestUEyeCroppingTranspose(VirtualTestCam, unittest.TestCase):
    """
    Test setting up the UEye class with a transposed image and a set resolution to force cropping.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No camera HW present. Skipping tests.')

        # set a fixed max_res smaller than the maximum supported camera resolution
        cls.camera_kwargs['max_res'] = (768, 1000)

        super(TestUEyeCroppingTranspose, cls).setUpClass()

    def test_set_resolution(self):
        """
        Test if the max resolution and the transpose argument are passed and applied.
        Also test if an acquired image is of the correct dimensions and the axes are right also with binning active.
        """
        # assign the right resolutions after initialization of the camera class
        sensor_res_raw = self.camera._sensor_res
        expected_res = self.camera_kwargs['max_res']
        sensor_res_user = self.camera.getMetadata()[model.MD_SENSOR_SIZE]

        # check if the set camera resolution matches the expected resolution
        self.assertEqual(expected_res, self.camera.resolution.value)

        # check if the resolution is not the same, own cam res should be higher
        self.assertNotEqual(self.camera_kwargs['max_res'], sensor_res_raw)

        # check if the transpose parameter is applied and axes are switched
        self.assertEqual(sensor_res_raw, sensor_res_user[::-1])

        # be sure to have binning set to base value
        self.camera.binning.value = (1, 1)
        # check if the acquired image is of the right size
        im = self.camera.data.get()
        self.assertEqual(im.shape, expected_res[::-1])

        # check if the acquired image is of the right size with higher binning
        self.camera.binning.value = (2, 4)
        binned_resolution = (expected_res[0] // 2, expected_res[1] / 4)
        im = self.camera.data.get()
        self.assertEqual(im.shape, binned_resolution[::-1])


if __name__ == '__main__':
    unittest.main()
