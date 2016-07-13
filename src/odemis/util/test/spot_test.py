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
import unittest


class TestMomentOfInertia(unittest.TestCase):
    """
    Test MomentOfInertia()
    """
    def setUp(self):
        # These are example data (computer generated)
        data = tiff.read_data("moi_input.tif")[0]
        background = tiff.read_data("moi_background.tif")[0]
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


TEST_IMAGE_PATH = "./"
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
            data = hdf5.read_data(TEST_IMAGE_PATH + "image" + str(i + 1) + ".h5")[0]
            C, T, Z, Y, X = data.shape
            data.shape = Y, X
            spot_coordinates = spot.FindCenterCoordinates(data)
            numpy.testing.assert_almost_equal(spot_coordinates, expected_coordinates[i], 3)


if __name__ == "__main__":
    unittest.main()

