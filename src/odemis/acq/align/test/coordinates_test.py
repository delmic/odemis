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

from numpy import random
from numpy import reshape
from odemis import model
from odemis.dataio import hdf5
from odemis.acq.align import coordinates

logging.getLogger().setLevel(logging.DEBUG)

class TestSpotCoordinates(unittest.TestCase):
    """
    Test SpotCoordinates functions
    """
    @unittest.skip("skip")
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

    @unittest.skip("skip")
    def test_devide_neighborhoods_spot(self):
        """
        Test DivideInNeighborhoods for white spot in black image
        """
        spot_image = numpy.zeros(shape=(256, 256))
        spot_image[112:120, 114:122].fill(255)

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(spot_image, (1, 1))
        self.assertEqual(subimages.__len__(), 1)

    @unittest.skip("skip")
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

    @unittest.skip("skip")
    def test_devide_neighborhoods_real_sample(self):
        """
        Test DivideInNeighborhoods for one spot real image
        """
        spot_image = hdf5.read_data("single_part.h5")
        C, T, Z, Y, X = spot_image[0].shape
        spot_image[0].shape = Y, X

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(spot_image[0], (1, 1))
        self.assertEqual(subimages.__len__(), 1)

    @unittest.skip("skip")
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
        expected_coordinates = [(23, 18)]
        numpy.testing.assert_almost_equal(optical_coordinates, expected_coordinates, 0)

    def test_devide_and_find_center_grid(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates
        """
        grid_data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(grid_data[0], (10, 10))
        print subimage_coordinates.__len__()

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

        for i, (a, b) in enumerate(optical_coordinates):
            grid_data[0][b, a] = 1797693134862315700000
        # a = numpy.array([tuple(i) for i in optical_coordinates], dtype=(float, 2))
        # numpy.savetxt("optical_coordinates.csv", a, delimiter=",")
        hdf5.export("centers_grid.h5", grid_data, thumbnail=None)
        print optical_coordinates

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

    def test_match_coordinates(self):
        """
        Test MatchCoordinates
        """
        optical_coordinates = [(2, 3), (3.0, 2.0)]
        electron_coordinates = [(3, 0), (-4, -1)]

        index = coordinates.KNNsearch(optical_coordinates, electron_coordinates)

        print index
        print coordinates.TransfromCoordinates(optical_coordinates, (-2.9999999999999938, 16.999999999999982), -126.869897646, 5.0)
        # print coordinates.TransfromCoordinates(optical_coordinates, (0, 0), 90, 1)

