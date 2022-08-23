# -*- coding: utf-8 -*-
'''
Created on 28 Nov 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

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
import math
from numpy import random
import numpy
from numpy.random import shuffle
from numpy.random import uniform
from odemis.acq.align import coordinates
from odemis.acq.align import transform
from odemis.dataio import hdf5
from odemis.util import spot
import operator
import unittest
from builtins import range

logging.getLogger().setLevel(logging.DEBUG)

# @unittest.skip("skip")
class TestDivideInNeighborhoods(unittest.TestCase):
    """
    Test DivideInNeighborhoods
    """
    def setUp(self):
        random.seed(0)

    # @unittest.skip("skip")
    def test_divide_and_find_center_grid(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates
        """
        grid_data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(grid_data[0], (10, 10), 40)

        spot_coordinates = [spot.FindCenterCoordinates(i) for i in subimages]
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        self.assertEqual(len(subimages), 100)

    # @unittest.skip("skip")
    def test_divide_and_find_center_grid_noise(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates for noisy input
        """
        grid_data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        # Add Gaussian noise
        noise = random.normal(0, 40, grid_data[0].size)
        noise_array = noise.reshape(grid_data[0].shape[0], grid_data[0].shape[1])
        noisy_grid_data = grid_data[0] + noise_array

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10), 40)

        spot_coordinates = [spot.FindCenterCoordinates(i) for i in subimages]
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        self.assertEqual(len(subimages), 100)

    # @unittest.skip("skip")
    def test_divide_and_find_center_grid_missing_point(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates for grid that misses one point
        """
        grid_data = hdf5.read_data("grid_missing_point.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        # Add Gaussian noise
        noise = random.normal(0, 40, grid_data[0].size)
        noise_array = noise.reshape(grid_data[0].shape[0], grid_data[0].shape[1])
        noisy_grid_data = grid_data[0] + noise_array

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10), 40)

        spot_coordinates = [spot.FindCenterCoordinates(i) for i in subimages]
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        self.assertEqual(len(subimages), 99)

    # @unittest.skip("skip")
    def test_divide_and_find_center_grid_cosmic_ray(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates for grid that misses one point
        and contains cosmic ray
        """
        grid_data = hdf5.read_data("grid_cosmic_ray.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        # Add Gaussian noise
        noise = random.normal(0, 40, grid_data[0].size)
        noise_array = noise.reshape(grid_data[0].shape[0], grid_data[0].shape[1])
        noisy_grid_data = grid_data[0] + noise_array

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10), 40)

        spot_coordinates = [spot.FindCenterCoordinates(i) for i in subimages]
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        self.assertEqual(len(subimages), 99)

    # @unittest.skip("skip")
    def test_divide_and_find_center_grid_noise_missing_point_cosmic_ray(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates for noisy input that
        misses one point and contains cosmic ray
        """
        grid_data = hdf5.read_data("grid_cosmic_ray.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        # Add Gaussian noise
        noise = random.normal(0, 40, grid_data[0].size)
        noise_array = noise.reshape(grid_data[0].shape[0], grid_data[0].shape[1])
        noisy_grid_data = grid_data[0] + noise_array

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10), 40)

        spot_coordinates = [spot.FindCenterCoordinates(i) for i in subimages]
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        self.assertEqual(len(subimages), 99)

# @unittest.skip("skip")
class TestMatchCoordinates(unittest.TestCase):
    """
    Test MatchCoordinates
    """
    def setUp(self):
        random.seed(0)
        self.electron_coordinates_1x1 = [(1, 1)]
        self.electron_coordinates_3x3 = [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)]
        self.electron_coordinates_10x10 = []
        self.electron_coordinates_40x40 = []
        for i in range(10):
            for j in range(10):
                self.electron_coordinates_10x10.append((i + 1, j + 1))

        for i in range(40):
            for j in range(40):
                self.electron_coordinates_40x40.append((i + 1, j + 1))

        # self.translation_x, self.translation_y = 1.3000132631489385, 2.3999740720548788
        self.translation_x, self.translation_y = uniform(-20, 20), uniform(-20, 20)
        # self.scale = 4
        self.scale = uniform(4, 4.2)
        self.scale_x, self.scale_y = self.scale, self.scale
        # self.rotation = -0.4517
        self.rotation = math.radians(uniform(-2, 2))

    def test_precomputed_output(self):
        """
        Test MatchCoordinates for precomputed output
        """
        optical_coordinates = [(9.1243, 6.7570), (10.7472, 16.8185), (4.7271, 12.6429), (13.9714, 6.0185), (5.6263, 17.5885), (14.8142, 10.9271), (10.0384, 11.8815), (15.5146, 16.0694), (4.4803, 7.5966)]
        electron_coordinates = self.electron_coordinates_3x3

        estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, 0.25, 0.25)
        assert 0 <= max_dist < 0.25
        numpy.testing.assert_equal(estimated_coordinates, [(2, 1), (2, 3), (1, 2), (3, 1), (1, 3), (3, 2), (2, 2), (3, 3), (1, 1)])

    def test_single_element(self):
        """
        Test MatchCoordinates for single element lists, error should be raised
        """
        optical_coordinates = [(9.1243, 6.7570)]
        electron_coordinates = self.electron_coordinates_1x1

        with self.assertRaises(LookupError):
            r = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, 0.25, 0.25)

    def test_precomputed_transformation_3x3(self):
        """
        Test MatchCoordinates for applied transformation
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))

        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)
        assert 0 <= max_dist < 0.25
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_shuffled_3x3(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)

        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates, 0.25, 0.25)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_shuffled_distorted_3x3(self):
        """
        Test MatchCoordinates for shuffled and distorted optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)
        distorted_coordinates = []
        # Add noise to the coordinates
        for c in shuffled_coordinates:
            distortion = (uniform(-0.1, 0.1), uniform(-0.1, 0.1))
            distorted_coordinates.append(tuple(map(operator.add, c, distortion)))
        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(distorted_coordinates, electron_coordinates, 0.25, 0.25)
        # if known_estimated_coordinates != []:
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(shuffled_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_precomputed_output_missing_point_3x3(self):
        """
        Test MatchCoordinates if NaN is returned in the corresponding position in case of missing point
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        rand = random.randint(0, len(transformed_coordinates) - 1)
        del transformed_coordinates[rand]
        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)

        self.assertEqual(len(known_estimated_coordinates), len(electron_coordinates) - 1)

    def test_precomputed_transformation_10x10(self):
        """
        Test MatchCoordinates for applied transformation
        """
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))

        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_shuffled_10x10(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)

        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates, 0.25, 0.25)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_shuffled__distorted_10x10(self):
        """
        Test MatchCoordinates for shuffled and distorted optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)
        distorted_coordinates = []
        # Add noise to the coordinates
        for c in shuffled_coordinates:
            distortion = tuple((uniform(-0.1, 0.1), uniform(-0.1, 0.1)))
            distorted_coordinates.append(tuple(map(operator.add, c, distortion)))

        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(distorted_coordinates, electron_coordinates, 0.25, 0.25)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(shuffled_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_precomputed_transformation_40x40(self):
        """
        Test MatchCoordinates for applied transformation
        """
        electron_coordinates = self.electron_coordinates_40x40
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))

        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 0)

    def test_shuffled_40x40(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_40x40
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)

        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates, 0.25, 0.25)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_precomputed_output_missing_point_40x40(self):
        """
        Test MatchCoordinates if NaN is returned in the corresponding position in case of missing point
        """
        electron_coordinates = self.electron_coordinates_40x40
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        rand = random.randint(0, len(transformed_coordinates) - 1)
        del transformed_coordinates[rand]
        known_estimated_coordinates, known_optical_coordinates, max_dist = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)

        self.assertEqual(len(known_estimated_coordinates), len(electron_coordinates) - 1)


if __name__ == '__main__':
    unittest.main()
