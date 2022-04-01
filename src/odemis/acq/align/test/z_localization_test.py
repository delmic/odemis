#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 23 March 2022

@author: Kornee Kleijwegt, Daan Boltje

Copyright © 2022 Kornee Kleijwegt, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import unittest
from odemis.util import almost_equal
from odemis.dataio.tiff import read_data
from odemis.acq.align.z_localization import determine_z_position


# Images and calibration data from the z-stack: 2021-06-28-17-20-07zstack_-28.432deg_step50nm_4.80884319rad
CALIB_DATA = {
                'x': {'a': -0.24759672307261632, 'b': 1.0063089478825507, 'c': 653.0753677001792,  'd': 638.8463397122532,  'w0': 11.560179003062268},
                'y': {'a': 0.5893174060828265, 'b': 0.23950839318911246, 'c': 1202.1980639514566,  'd': 425.6030263781317, 'w0': 11.332043010740446},
                'feature_angle': -3.1416,
                'upsample_factor': 5,
                'z_least_confusion': 9.418563712742548e-07,
                'z_calibration_range': [-9.418563712742548e-07, 8.781436287257452e-07],
              }

z_stack_step_size = 50*10-9  # m
PRECISION = z_stack_step_size * 0.45  # Precision should be better than the step within a z stack

class TestDetermineZPosition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from skimage import io, exposure
            from scipy.optimize import fmin_cg
            import psf_extractor

        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the z localization tests, correct libraries to perform the tests are not available.\n"
                                    f"Got the error: %s" % err)

    def test_determine_z_position(self):
        """
        Test for known data the outcome of the function determine_z_position
        """
        # Test on an image below focus
        image = read_data("images/super_z_single_beed_aprox_500nm_under_focus.tif")[0]
        expected_outcome_image_1 = -592.5e-9  # Value determined using the function determine_z_position
        z, warning = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warning, None)
        self.assertAlmostEqual(expected_outcome_image_1, z, delta=PRECISION)

        # Test on an image which is roughly in focus/point of least confusion
        image = read_data("images/super_z_single_beed_semi_in_focus.tif")[0]
        expected_outcome_image_2 = -62.8e-9  # Value determined using the function determine_z_position
        z, warning = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warning, None)
        self.assertAlmostEqual(expected_outcome_image_2, z, delta=PRECISION)

        # Test on an image which is above focus
        image = read_data("images/super_z_single_beed_aprox_500nm_above_focus.tif")[0]
        expected_outcome_image_3 = 420.6e-9  # Value determined using the function determine_z_position
        z, warning = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warning, None)
        self.assertAlmostEqual(expected_outcome_image_3, z, delta=PRECISION)

        # Test on an image where no feature visible because it is just white noise
        image = read_data("images/super_z_no_beed_just_noise.tif")[0]
        _, warning = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warning, 5)  # Since the entire image is noise the warning raised should be 5

        # Test on an image where no feature visible because it is entirely white
        image = read_data("images/super_z_no_beed_just_white.tif")[0]
        _, warning = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warning, 6)  # Since the entire image is white the warning raised should be 5

        # Change the range so warning 6 is raised with an image which is just above focus
        calib_data_limited_range = CALIB_DATA.copy()
        calib_data_limited_range["z_calibration_range"] = (-1e-10, 1e-10)
        image = read_data("images/super_z_single_beed_aprox_500nm_above_focus.tif")[0]
        expected_outcome_image_3 = 420.6e-9  # Value determined using the function determine_z_position
        z, warning = determine_z_position(image, calib_data_limited_range)
        # Since the range is set to small the detected feature is to big and warning raised should be 4
        self.assertEqual(warning, 4)
        self.assertAlmostEqual(expected_outcome_image_3, z, delta=PRECISION)

    def test_key_error(self):
        image = read_data("images/super_z_single_beed_semi_in_focus.tif")[0]

        # Check if the key error is raised when the key 'x' is missing
        calib_data_missing_key = CALIB_DATA.copy()
        _ = calib_data_missing_key.pop("x")
        with self.assertRaises(KeyError):
            _, _ = determine_z_position(image, calib_data_missing_key)

        # Check if the key error is raised when the key 'z_calibration_range' is missing
        calib_data_missing_key = CALIB_DATA.copy()
        _ = calib_data_missing_key.pop("z_calibration_range")
        with self.assertRaises(KeyError):
            _, _ = determine_z_position(image, calib_data_missing_key)

    # TODO Add a test to find that indeed the module not found error is raised when a module is missing.
    # Maybe use something with MOCK?
    # def test_module_not_found_error(self):
    #     image = read_data("images/super_z_single_beed_semi_in_focus.tif")[0]
    #
    #     with self.assertRaises(ModuleNotFoundError):
    #         _, _ = determine_z_position(image, CALIB_DATA)


if __name__ == '__main__':
    unittest.main()
