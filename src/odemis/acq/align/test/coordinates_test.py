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
import unittest
import numpy
import operator

from numpy import random
from random import uniform
from numpy import reshape
from odemis import model
from odemis.dataio import hdf5
from odemis.acq.align import coordinates
from odemis.acq.align import transform
from random import shuffle

logging.getLogger().setLevel(logging.DEBUG)

# @unittest.skip("skip")
class TestFindCenterCoordinates(unittest.TestCase):
    """
    Test FindCenterCoordinates
    """
    def test_find_center(self):
        """
        Test FindCenterCoordinates
        """
        data = []
        subimages = []

        for i in xrange(10):
            data.append(hdf5.read_data("image" + str(i+1) + ".h5"))
            C, T, Z, Y, X = data[i][0].shape
            data[i][0].shape = Y, X
            subimages.append(data[i][0])

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        expected_coordinates = [(4.9998, 4.9768), (5.4181, 4.2244), (15.5542, 15.4534),
                                (8.1512, 8.2081), (5.1537, 4.9287), (5.2221, 5.0449),
                                (4.1433, 6.7063), (6.4313, 7.2690), (4.9355, 5.1400), (5.0209, 4.9929)]
        numpy.testing.assert_almost_equal(spot_coordinates, expected_coordinates, 3)

# @unittest.skip("skip")
class TestDivideInNeighborhoods(unittest.TestCase):
    """
    Test DivideInNeighborhoods
    """
    def setUp(self):
        random.seed(0)

    def test_devide_neighborhoods_spot(self):
        """
        Test DivideInNeighborhoods for white spot in black image
        """
        spot_image = numpy.zeros(shape=(256, 256))
        spot_image[112:120, 114:122].fill(255)

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(spot_image, (1, 1))
        self.assertEqual(subimages.__len__(), 1)

    def test_devide_neighborhoods_grid(self):
        """
        Test DivideInNeighborhoods for 3x3 grid of white spots in black image
        """
        grid_image = numpy.zeros(shape=(256, 256))
        x_start, y_start = 70, 64
        for x in xrange(3):
            y_start_in = y_start
            for y in xrange(3):
                grid_image[x_start:x_start + 8, y_start_in:y_start_in + 8].fill(255)
                y_start_in = y_start_in + 40
            x_start = x_start + 40

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(grid_image, (3, 3))
        self.assertEqual(subimages.__len__(), 9)

    # @unittest.skip("skip")
    def test_devide_neighborhoods_real_sample(self):
        """
        Test DivideInNeighborhoods for one spot real image
        """
        spot_image = hdf5.read_data("single_part.h5")
        C, T, Z, Y, X = spot_image[0].shape
        spot_image[0].shape = Y, X

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(spot_image[0], (1, 1))
        self.assertEqual(subimages.__len__(), 1)

    # @unittest.skip("skip")
    def test_devide_and_find_center_spot(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates
        """
        spot_image = hdf5.read_data("single_part.h5")
        C, T, Z, Y, X = spot_image[0].shape
        spot_image[0].shape = Y, X

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(spot_image[0], (1, 1))
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)
        expected_coordinates = [(23, 20)]
        numpy.testing.assert_almost_equal(optical_coordinates, expected_coordinates, 0)

    # @unittest.skip("skip")
    def test_devide_and_find_center_grid(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates
        """
        grid_data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(grid_data[0], (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

        self.assertEqual(subimages.__len__(), 100)

    # @unittest.skip("skip")
    def test_devide_and_find_center_grid_noise(self):
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

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

        self.assertEqual(subimages.__len__(), 100)

    # @unittest.skip("skip")
    def test_devide_and_find_center_grid_missing_point(self):
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

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

        self.assertEqual(subimages.__len__(), 99)

    # @unittest.skip("skip")
    def test_devide_and_find_center_grid_cosmic_ray(self):
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

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

        self.assertEqual(subimages.__len__(), 99)

    # @unittest.skip("skip")
    def test_devide_and_find_center_grid_noise_missing_point_cosmic_ray(self):
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

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

        self.assertEqual(subimages.__len__(), 99)

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
        for i in xrange(10):
            for j in xrange(10):
                self.electron_coordinates_10x10.append((i + 1, j + 1))

        for i in xrange(40):
            for j in xrange(40):
                self.electron_coordinates_40x40.append((i + 1, j + 1))

        # self.translation_x, self.translation_y = 1.3000132631489385, 2.3999740720548788
        self.translation_x, self.translation_y = uniform(-0.5, 0.5), uniform(-0.5, 0.5)
        # self.scale = 4
        self.scale = uniform(4, 4.2)
        # self.rotation = -0.4517
        self.rotation = uniform(-0.4, 0.4)

    def test_match_coordinates_precomputed_output(self):
        """
        Test MatchCoordinates for precomputed output
        """
        optical_coordinates = [(9.1243, 6.7570), (10.7472, 16.8185), (4.7271, 12.6429), (13.9714, 6.0185), (5.6263, 17.5885), (14.8142, 10.9271), (10.0384, 11.8815), (15.5146, 16.0694), (4.4803, 7.5966)]
        electron_coordinates = self.electron_coordinates_3x3

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates)
        numpy.testing.assert_equal(estimated_coordinates, [(2, 1), (2, 3), (1, 2), (3, 1), (1, 3), (3, 2), (2, 2), (3, 3), (1, 1)])

    def test_match_coordinates_single_element(self):
        """
        Test MatchCoordinates for single element lists, warning should be thrown
        """
        optical_coordinates = [(9.1243, 6.7570)]
        electron_coordinates = self.electron_coordinates_1x1

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates)
        numpy.testing.assert_equal(estimated_coordinates, [])

    def test_match_coordinates_precomputed_transformation_3x3(self):
        """
        Test MatchCoordinates for applied transformation
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale = self.scale
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates)
        numpy.testing.assert_equal(estimated_coordinates, electron_coordinates)
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(transformed_coordinates, estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling, calc_rotation), (translation_x, translation_y, scale, rotation))

    def test_match_coordinates_shuffled_3x3(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale = self.scale
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        shuffle(shuffled_coordinates)

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates)
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(shuffled_coordinates, estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling, calc_rotation), (translation_x, translation_y, scale, rotation))

    def test_match_coordinates_shuffled__distorted_3x3(self):
        """
        Test MatchCoordinates for shuffled and distorted optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale = self.scale
        rotation = self.rotation

        optical_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        shuffle(shuffled_coordinates)
        distorted_coordinates = []
        # Add noise to the coordinates
        for i in xrange(shuffled_coordinates.__len__()):
            distortion = tuple((uniform(-0.1, 0.1), uniform(-0.1, 0.1)))
            distorted_coordinates.append(tuple(map(operator.add, shuffled_coordinates[i], distortion)))

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(distorted_coordinates, electron_coordinates)
        optical_order = coordinates._KNNsearch(shuffled_coordinates, optical_coordinates)
        electron_order = coordinates._KNNsearch(estimated_coordinates, electron_coordinates)
        numpy.testing.assert_equal(electron_order, optical_order)

    def test_match_coordinates_precomputed_output_missing_point_3x3(self):
        """
        Test MatchCoordinates if NaN is returned in the corresponding position in case of missing point
        """
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale = self.scale
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        rand = random.randint(0, transformed_coordinates.__len__()-1)
        del transformed_coordinates[rand]
        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates)

        if estimated_coordinates != []:
            numpy.testing.assert_equal(estimated_coordinates[rand], float('nan'))

    def test_match_coordinates_precomputed_transformation_10x10(self):
        """
        Test MatchCoordinates for applied transformation
        """
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale = self.scale
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates)
        numpy.testing.assert_equal(estimated_coordinates, electron_coordinates)
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(transformed_coordinates, estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling, calc_rotation), (translation_x, translation_y, scale, rotation))

    def test_match_coordinates_shuffled_10x10(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale = self.scale
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        shuffle(shuffled_coordinates)

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates)
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(shuffled_coordinates, estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling, calc_rotation), (translation_x, translation_y, scale, rotation))

    def test_match_coordinates_precomputed_transformation_40x40(self):
        """
        Test MatchCoordinates for applied transformation
        """
        electron_coordinates = self.electron_coordinates_40x40
        translation_x, translation_y = 0.3, 0.3
        scale = 4
        rotation = 0.5

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates)
        numpy.testing.assert_equal(electron_coordinates, estimated_coordinates)
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(transformed_coordinates, estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling, calc_rotation), (translation_x, translation_y, scale, rotation))

    def test_match_coordinates_shuffled_40x40(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = 0.3, 0.3
        scale = 4
        rotation = 0.5

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        shuffle(shuffled_coordinates)

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates)
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(shuffled_coordinates, estimated_coordinates)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling, calc_rotation), (translation_x, translation_y, scale, rotation))

    def test_match_coordinates_precomputed_output_missing_point_40x40(self):
        """
        Test MatchCoordinates if NaN is returned in the corresponding position in case of missing point
        """
        electron_coordinates = self.electron_coordinates_40x40
        translation_x, translation_y = 0.3, 0.3
        scale = 4
        rotation = 0.5

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        rand = random.randint(0, transformed_coordinates.__len__()-1)
        del transformed_coordinates[rand]
        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates)

        if estimated_coordinates != []:
            numpy.testing.assert_equal(estimated_coordinates[rand], float('nan'))
            
# @unittest.skip("skip")
class TestOverallComponent(unittest.TestCase):
    """
    Test the interaction of all the functions together
    """
    def setUp(self):
        self.electron_coordinates_3x3 = [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)]
        self.electron_coordinates_10x10 = []
        for i in xrange(10):
            for j in xrange(10):
                self.electron_coordinates_10x10.append((i + 1, j + 1))

        # self.translation_x, self.translation_y = 1.3000132631489385, 2.3999740720548788
        self.translation_x, self.translation_y = uniform(-0.5, 0.5), uniform(-0.5, 0.5)
        # self.scale = 4
        self.scale = uniform(4, 4.2)
        # self.rotation = -0.4517
        self.rotation = uniform(-0.4, 0.4)

    def test_overall_simple(self):
        """
        Test DivideInNeighborhoods, FindCenterCoordinates, MatchCoordinates, CalculateTransform for 3x3 grid of white spots in black image
        """
        electron_coordinates = []
        for i in xrange((self.electron_coordinates_3x3).__len__()):
            mul_10_tuple = tuple(map(operator.mul, self.electron_coordinates_3x3[i], (10, 10)))
            electron_coordinates.append(mul_10_tuple)

        translation_x, translation_y = self.translation_x, self.translation_y
        scale = self.scale
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, scale)
        grid_image = numpy.zeros(shape=(256, 256))

        for x in zip(transformed_coordinates):
            (a, b) = x[0]
            grid_image[a - 1:a + 1, b - 1:b + 1].fill(255)

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(grid_image, (3, 3))
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)
        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates)
        optical_order = coordinates._KNNsearch(optical_coordinates, transformed_coordinates)
        electron_order = coordinates._KNNsearch(estimated_coordinates, electron_coordinates)
        numpy.testing.assert_equal(electron_order, optical_order)

    # @unittest.skip("skip")
    def test_overall_precomputed_output(self):
        """
        Test MatchCoordinates for precomputed output
        """
        electron_coordinates = []
        for i in xrange((self.electron_coordinates_10x10).__len__()):
            mul_10_tuple = tuple(map(operator.mul, self.electron_coordinates_10x10[i], (11, 11)))
            electron_coordinates.append(mul_10_tuple)

        grid_data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(grid_data[0], (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)
        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates)
        print known_optical_coordinates
        # (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(known_optical_coordinates, estimated_coordinates)
        print estimated_coordinates
        # print (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation

