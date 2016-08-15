# -*- coding: utf-8 -*-
'''
Created on 10 Jan 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import math
import numpy
from odemis import model
from odemis.dataio import tiff, hdf5
from odemis.util import spot
import os
import unittest


TEST_IMAGE_PATH = os.path.dirname(__file__)

class TestMomentOfInertia(unittest.TestCase):
    """
    Test MomentOfInertia()
    """
    def setUp(self):
        # These are example data (computer generated)
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "moi_input.tif"))[0]
        background = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "moi_background.tif"))[0]
        self.data = data
        self.background = background

    def test_precomputed(self):
        data = self.data
        background = self.background
        mi = spot.MomentOfInertia(data, background)
        self.assertAlmostEqual(mi, 112.005654085)

    def test_no_bkg(self):
        data = self.data
        # No info at all
        mi = spot.MomentOfInertia(data)
        self.assertAlmostEqual(mi, 112.005654085, delta=10)

        # now with MD_BASELINE
        data.metadata[model.MD_BASELINE] = 100
        mi = spot.MomentOfInertia(data)
        self.assertAlmostEqual(mi, 112.005654085, delta=5)

        # TODO: test without background subtraction (just use Baseline)

#        self.assertEqual(valid, True)

    def test_black(self):
        data = numpy.zeros((480, 640), dtype=numpy.uint16)
        mi = spot.MomentOfInertia(data)
        self.assertTrue(math.isnan(mi))

    def test_spot(self):
        data = numpy.zeros((480, 640), dtype=numpy.uint16)
        data[240, 360] = 5000
        mi = spot.MomentOfInertia(data)
        self.assertTrue(math.isnan(mi) or mi > 0)


class TestSpotIntensity(unittest.TestCase):
    """
    Test SpotIntensity()
    """

    def test_precomputed(self):
        # These are example data (computer generated)
        data = hdf5.read_data("image1.h5")[0]
        data.shape = data.shape[-2:]
        si = spot.SpotIntensity(data)  # guessed background
        self.assertAlmostEqual(si, 0.713582339927869)

        # Same thing, with some static background
        background = numpy.zeros(data.shape, dtype=data.dtype)
        background += 50
        si = spot.SpotIntensity(data, background)
        self.assertAlmostEqual(si, 0.8621370728816907)


class TestFindCenterCoordinates(unittest.TestCase):
    """
    Test FindCenterCoordinates()
    """
    def test_find_center(self):
        """
        Test FindCenterCoordinates
        """
        expected_coordinates = [(-0.00019439548586790034, -0.023174120210179554),
                                (0.41813787193469681, -0.77556146879261101),
                                (0.05418032832973009, -0.046573726263258203),
                                (0.15117173005078957, 0.20813259555303279),
                                (0.15372338817998937, -0.071307409462406962),
                                (0.22214464176322843, 1.5448851668913044),
                                (-1.3567379189595801, 0.20634334863259929),
                                (-0.068717256379618827, 0.76902400758882417),
                                (-0.064496044288789064, 0.14000630665134439),
                                (0.020941736978718473, -0.0071056828496776324)]

        for i in range(10):
            data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "image" + str(i + 1) + ".h5"))[0]
            C, T, Z, Y, X = data.shape
            data.shape = Y, X
            spot_coordinates = spot.FindCenterCoordinates(data)
            numpy.testing.assert_almost_equal(spot_coordinates, expected_coordinates[i], 3)

    def test_find_center_big(self):
        """
        Test FindCenterCoordinates on large data
        """
        # Note: it's not very clear why, but the center is not exactly the same
        # as with the original data.
        expected_coordinates = [(-0.0003367114224783442, -0.022941682748052378),
                                (0.4213370004373016, -0.25313267150174318),
                                (0.054153514028894206, -0.046475569488448026),
                                (0.15117193581594143, 0.20813363301021551),
                                (0.1963834856403108, -0.18329597166583256),
                                (0.23159684275306583, 1.3670166271550004),
                                (-1.3363782613242998, 0.20192181693837058),
                                (-0.15084662336702331, 0.65850157367975504),
                                (-0.058984235874285897, 0.13071737132569164),
                                (0.021009283646695891, -0.007037802630523865)]

        for i in range(10):
            data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "image" + str(i + 1) + ".h5"))[0]
            data.shape = data.shape[-2:]
            Y, X = data.shape
            databig = numpy.zeros((200 + Y, 200 + X), data.dtype)
            databig += numpy.min(data)
            # We put it right at the center, so shouldn't change expected coordinates
            databig[100:100 + Y:, 100: 100 + X] = data
            spot_coordinates = spot.FindCenterCoordinates(databig)
            numpy.testing.assert_almost_equal(spot_coordinates, expected_coordinates[i], 3)

    def test_find_center_syn(self):
        """
        Test FindCenterCoordinates on synthetic data
        """
        offsets = [(0,0),
                   (-1, -1),
                   (3, 2),
                   ]
        for ofs in offsets:
            data = numpy.zeros((201, 201), numpy.uint16)
            # Just one point, so it should be easy to find
            data[100 + ofs[1], 100 + ofs[0]] = 500
            spot_coordinates = spot.FindCenterCoordinates(data)
            numpy.testing.assert_almost_equal(spot_coordinates, ofs, 3)

if __name__ == "__main__":
    unittest.main()

