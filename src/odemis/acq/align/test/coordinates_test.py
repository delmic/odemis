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
import math
import operator

from scipy.spatial import cKDTree
from numpy import random
from random import uniform
from numpy import reshape
from odemis import model
from odemis.dataio import hdf5
from odemis.acq.align import coordinates
from odemis.acq.align import transform
from random import shuffle
from numpy import genfromtxt
from operator import itemgetter
from scipy import ndimage
from scipy import misc
from scipy import imag

logging.getLogger().setLevel(logging.DEBUG)

@unittest.skip("skip")
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
        expected_coordinates = [(0.00019439548586790034, 0.023174120210179554),
                                (-0.41813787193469681, 0.77556146879261101),
                                (-0.05418032832973009, 0.046573726263258203),
                                (-0.15117173005078957, -0.20813259555303279),
                                (-0.15372338817998937, 0.071307409462406962),
                                (-0.22214464176322843, -1.5448851668913044),
                                (1.3567379189595801, -0.20634334863259929),
                                (0.068717256379618827, -0.76902400758882417),
                                (0.064496044288789064, -0.14000630665134439),
                                (-0.020941736978718473, 0.0071056828496776324)]
        numpy.testing.assert_almost_equal(spot_coordinates, expected_coordinates, 3)

@unittest.skip("skip")
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

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(spot_image, (1, 1))
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

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(grid_image, (3, 3))
        self.assertEqual(subimages.__len__(), 9)

    # @unittest.skip("skip")
    def test_devide_neighborhoods_real_sample(self):
        """
        Test DivideInNeighborhoods for one spot real image
        """
        spot_image = hdf5.read_data("single_part.h5")
        C, T, Z, Y, X = spot_image[0].shape
        spot_image[0].shape = Y, X

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(spot_image[0], (1, 1))
        self.assertEqual(subimages.__len__(), 1)

    # @unittest.skip("skip")
    def test_devide_and_find_center_spot(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates
        """
        spot_image = hdf5.read_data("single_part.h5")
        C, T, Z, Y, X = spot_image[0].shape
        spot_image[0].shape = Y, X

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(spot_image[0], (1, 1))
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)
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

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(grid_data[0], (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

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

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

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

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        self.assertEqual(subimages.__len__(), 100)

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

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

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

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(noisy_grid_data, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        self.assertEqual(subimages.__len__(), 99)

@unittest.skip("skip")
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
        self.translation_x, self.translation_y = uniform(-20, 20), uniform(-20, 20)
        # self.scale = 4
        self.scale = uniform(4, 4.2)
        self.scale_x, self.scale_y = self.scale, self.scale
        # self.rotation = -0.4517
        self.rotation = uniform(-2, 2)

    def test_match_coordinates_precomputed_output(self):
        """
        Test MatchCoordinates for precomputed output
        """
        print "testing precomputed..."
        optical_coordinates = [(9.1243, 6.7570), (10.7472, 16.8185), (4.7271, 12.6429), (13.9714, 6.0185), (5.6263, 17.5885), (14.8142, 10.9271), (10.0384, 11.8815), (15.5146, 16.0694), (4.4803, 7.5966)]
        electron_coordinates = self.electron_coordinates_3x3

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, 0.25, 0.25)
        numpy.testing.assert_equal(estimated_coordinates, [(2, 1), (2, 3), (1, 2), (3, 1), (1, 3), (3, 2), (2, 2), (3, 3), (1, 1)])

    def test_match_coordinates_single_element(self):
        """
        Test MatchCoordinates for single element lists, warning should be thrown
        """
        print "testing single..."
        optical_coordinates = [(9.1243, 6.7570)]
        electron_coordinates = self.electron_coordinates_1x1

        estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, 0.25, 0.25)
        numpy.testing.assert_equal(estimated_coordinates, [])

    def test_match_coordinates_precomputed_transformation_3x3(self):
        """
        Test MatchCoordinates for applied transformation
        """
        print "testing normal 3x3..."
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_match_coordinates_shuffled_3x3(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        print "testing shuffled 3x3..."
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_match_coordinates_shuffled__distorted_3x3(self):
        """
        Test MatchCoordinates for shuffled and distorted optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        print "testing distorted 3x3..."
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)
        distorted_coordinates = []
        # Add noise to the coordinates
        for i in xrange(shuffled_coordinates.__len__()):
            distortion = tuple((uniform(-0.1, 0.1), uniform(-0.1, 0.1)))
            distorted_coordinates.append(tuple(map(operator.add, shuffled_coordinates[i], distortion)))

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(distorted_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(shuffled_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_match_coordinates_precomputed_output_missing_point_3x3(self):
        """
        Test MatchCoordinates if NaN is returned in the corresponding position in case of missing point
        """
        print "testing missing 3x3..."
        electron_coordinates = self.electron_coordinates_3x3
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        rand = random.randint(0, transformed_coordinates.__len__()-1)
        del transformed_coordinates[rand]
        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)

        if known_estimated_coordinates != []:
            numpy.testing.assert_equal(known_estimated_coordinates.__len__(), electron_coordinates.__len__() - 1)

    def test_match_coordinates_precomputed_transformation_10x10(self):
        """
        Test MatchCoordinates for applied transformation
        """
        print "testing normal 10x10..."
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_match_coordinates_shuffled_10x10(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        print "testing shuffled 10x10..."
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_match_coordinates_shuffled__distorted_10x10(self):
        """
        Test MatchCoordinates for shuffled and distorted optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        print "testing distorted 10x10..."
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)
        distorted_coordinates = []
        # Add noise to the coordinates
        for i in xrange(shuffled_coordinates.__len__()):
            distortion = tuple((uniform(-0.1, 0.1), uniform(-0.1, 0.1)))
            distorted_coordinates.append(tuple(map(operator.add, shuffled_coordinates[i], distortion)))

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(distorted_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(shuffled_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_match_coordinates_precomputed_transformation_40x40(self):
        """
        Test MatchCoordinates for applied transformation
        """
        print "testing normal 40x40..."
        electron_coordinates = self.electron_coordinates_40x40
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 0)

    def test_match_coordinates_shuffled_40x40(self):
        """
        Test MatchCoordinates for shuffled optical coordinates, comparing the order of the shuffled optical list and the estimated coordinates
        generated by MatchCoordinates
        """
        print "testing shuffled 40x40..."
        electron_coordinates = self.electron_coordinates_10x10
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        shuffled_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        shuffle(shuffled_coordinates)

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(shuffled_coordinates, electron_coordinates, 0.25, 0.25)
        if known_estimated_coordinates != []:
            (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
            numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (translation_x, translation_y, scale_x, scale_y, rotation), 1)

    def test_match_coordinates_precomputed_output_missing_point_40x40(self):
        """
        Test MatchCoordinates if NaN is returned in the corresponding position in case of missing point
        """
        print "testing missing 40x40..."
        electron_coordinates = self.electron_coordinates_40x40
        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        rand = random.randint(0, transformed_coordinates.__len__()-1)
        del transformed_coordinates[rand]
        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(transformed_coordinates, electron_coordinates, 0.25, 0.25)

        if known_estimated_coordinates != []:
            numpy.testing.assert_equal(known_estimated_coordinates.__len__(), electron_coordinates.__len__() - 1)
            
@unittest.skip("skip")
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
        self.electron_coordinates_real_example = []
        for i in xrange(9):
            for j in xrange(9):
                self.electron_coordinates_real_example.append((i * 249.625, j * 249.625))

        self.translation_x, self.translation_y = uniform(-5, 5), uniform(-5, 5)
        self.scale = uniform(4, 4.2)
        self.scale_x, self.scale_y = self.scale, self.scale
        self.rotation = uniform(-2, 2)

    @unittest.skip("skip")
    def test_overall_simple_shuffled_distorted(self):
        """
        Test DivideInNeighborhoods, FindCenterCoordinates, MatchCoordinates, CalculateTransform for 10x10 shuffled and distorted grid of white spots 
        in black image
        """
        electron_coordinates = []
        for i in xrange((self.electron_coordinates_10x10).__len__()):
            mul_10_tuple = tuple(map(operator.mul, self.electron_coordinates_10x10[i], (10, 10)))
            electron_coordinates.append(mul_10_tuple)

        translation_x, translation_y = self.translation_x, self.translation_y
        scale_x, scale_y = self.scale_x, self.scale_y
        rotation = self.rotation

        transformed_coordinates = coordinates._TransformCoordinates(electron_coordinates, (translation_x, translation_y), rotation, (scale_x, scale_y))
        distorted_coordinates = []

        # Add noise to the coordinates
        for i in xrange(transformed_coordinates.__len__()):
            distortion = tuple((uniform(-0.3, 0.3), uniform(-0.3, 0.3)))
            distorted_coordinates.append(tuple(map(operator.add, transformed_coordinates[i], distortion)))

        grid_optical_image = numpy.zeros(shape=(600, 600))

        for x in zip(distorted_coordinates):
            (a, b) = x[0]
            grid_optical_image[a - 1:a + 1, b - 1:b + 1].fill(255)

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(grid_optical_image, (10, 10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)
        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, 0.25, 0.25)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        overlay_coordinates = coordinates._TransformCoordinates(known_estimated_coordinates, (calc_translation_y, calc_translation_x), -calc_rotation, (calc_scaling_x, calc_scaling_y))

        numpy.testing.assert_almost_equal((calc_translation_y, calc_translation_x, -calc_rotation, calc_scaling_x, calc_scaling_y), (translation_x, translation_y, rotation, scale_x, scale_y), 1)

    # @unittest.skip("skip")
    def test_overall_real_example(self):
        """
        Test DivideInNeighborhoods, FindCenterCoordinates, MatchCoordinates, CalculateTransform for real example of 9x9 grid
        """
        electron_coordinates = self.electron_coordinates_real_example

        grid_data = hdf5.read_data("scanned_image-45.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(grid_data[0], (2, 2))
        print subimage_coordinates
        for i in subimages:
            print i.shape
        print len(subimages)
        hdf5.export("spot_found.h5", subimages, thumbnail=None)
        #hdf5.export("subimages", model.DataArray(subimages[0]))
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        print spot_coordinates

        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)
        print optical_coordinates
        # grid_data[0].reshape(grid_data[0].shape[0], grid_data[0].shape[1], 3)
        for i in optical_coordinates:
            grid_data[0][i[1], i[0]] = 12000
        hdf5.export("found.h5", model.DataArray(grid_data[0]))
        # TODO: Make function for scale calculation
        sorted_coordinates = sorted(optical_coordinates, key=lambda tup: tup[1])
        tab = tuple(map(operator.sub, sorted_coordinates[0], sorted_coordinates[1]))
        optical_scale = math.hypot(tab[0], tab[1])
        print optical_scale
        scale = 249.625 / optical_scale
            
        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, scale, 25)
        print len(known_estimated_coordinates), len(known_optical_coordinates)
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
        final_optical = coordinates._TransformCoordinates(known_estimated_coordinates, (calc_translation_y, calc_translation_x), -calc_rotation, (calc_scaling_x, calc_scaling_y))

        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation), (4218.0607845864843, 3100.907806071255, 0.0653803040366, 0.0653803040366, 1.47833441039), 1)
        
