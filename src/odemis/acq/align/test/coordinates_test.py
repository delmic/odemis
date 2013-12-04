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
        data1 = numpy.genfromtxt('image1.csv', delimiter=',')
        data2 = numpy.genfromtxt('image2.csv', delimiter=',')
        data3 = numpy.genfromtxt('image3.csv', delimiter=',')
        data4 = numpy.genfromtxt('image4.csv', delimiter=',')
        data5 = numpy.genfromtxt('image5.csv', delimiter=',')
        data6 = numpy.genfromtxt('image6.csv', delimiter=',')
        data7 = numpy.genfromtxt('image7.csv', delimiter=',')
        data8 = numpy.genfromtxt('image8.csv', delimiter=',')
        data9 = numpy.genfromtxt('image9.csv', delimiter=',')
        data10 = numpy.genfromtxt('image10.csv', delimiter=',')

        subimages = []
        subimages.append(model.DataArray(data1))
        subimages.append(model.DataArray(data2))
        subimages.append(model.DataArray(data3))
        subimages.append(model.DataArray(data4))
        subimages.append(model.DataArray(data5))
        subimages.append(model.DataArray(data6))
        subimages.append(model.DataArray(data7))
        subimages.append(model.DataArray(data8))
        subimages.append(model.DataArray(data9))
        subimages.append(model.DataArray(data10))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        expected_coordinates = [(4.9998, 4.9768), (5.4181, 4.2244), (15.5542, 15.4534),
                                (8.1512, 8.2081), (5.1537, 4.9287), (5.2221, 5.0449),
                                (4.1433, 6.7063), (6.4313, 7.2690), (4.9355, 5.1400), (5.0209, 4.9929)]
        numpy.testing.assert_almost_equal(spot_coordinates, expected_coordinates, 3)

    @unittest.skip("skip")
    def test_devide_neighborhoods_spot(self):
        """
        Test DivideInNeighborhoods for one spot image
        """
        spot_image = numpy.zeros(shape=(256, 256))
        spot_image[112:120, 114:122].fill(255)

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(spot_image, (1, 1))
        self.assertEqual(subimages.__len__(), 1)

    @unittest.skip("skip")
    def test_devide_neighborhoods_grid(self):
        """
        Test DivideInNeighborhoods for 3x3 grid
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
        # hdf5.export("subimage.h5", subimages[0], thumbnail=None)
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)
        expected_coordinates = [(23, 18)]
        numpy.testing.assert_almost_equal(optical_coordinates, expected_coordinates, 0)

    def test_devide_and_find_center_grid(self):
        """
        Test DivideInNeighborhoods combined with FindCenterCoordinates
        """
        grid_data = numpy.genfromtxt('grid2.csv', delimiter=',')
        # grid_data[33, 113] = 4700
        hdf5.export("input_grid.h5", model.DataArray(grid_data), thumbnail=None)

        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(grid_data, (10, 10))
        print subimage_coordinates.__len__()

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

        for i, (a, b) in enumerate(optical_coordinates):
            grid_data[b, a] = 1797693134862315700000
        # a = numpy.array([tuple(i) for i in optical_coordinates], dtype=(float, 2))
        # numpy.savetxt("optical_coordinates.csv", a, delimiter=",")
        hdf5.export("centers_grid.h5", model.DataArray(grid_data), thumbnail=None)
        # print optical_coordinates

