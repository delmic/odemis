# -*- coding: utf-8 -*-
"""
Created on 10 Jan 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
import math
import numpy
from odemis import model
from odemis.dataio import tiff, hdf5
from odemis.util import spot, synthetic
from odemis.util.peak_local_max import ensure_spacing
import os
import scipy.stats
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
    Unit test class to test the behavior of FindCenterCoordinates in
    odemis.util.spot.
    """

    def setUp(self):
        self.imgdata = tiff.read_data('spotdata.tif')
        self.coords0 = numpy.genfromtxt('spotdata.csv', delimiter=',')

    def test_find_center(self):
        """
        Test FindCenterCoordinates
        """
        expected_coordinates = [(-0.00019439548586790034, -0.023174120210179554),
                                (0.47957790544657719, -0.82786251901339769),
                                (0.05418032832973009, -0.046573726263258203),
                                (0.15117173005078957, 0.20813259555303279),
                                (-0.16400161706684502, 0.12399078936095265),
                                (0.21457123595635252, 1.682698104874774),
                                (-1.3480442345004007, 0.19789183664083154),
                                (-0.13424061744712734, 0.73739434108133217),
                                (-0.063230444692135013, 0.14718269387805094),
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
                                (0.42179351215760619, -0.25668360673638801),
                                (0.054153514028894206, -0.046475569488448026),
                                (0.15117193581594143, 0.20813363301021551),
                                (0.1963834856403108, -0.18329597166583256),
                                (0.23159684275306583, 1.3670166271550004),
                                (-1.3363782613242998, 0.20192181693837058),
                                (-0.14978764151902624, 0.66067572281822606),
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
        offsets = [(0, 0),
                   (-1, -1),
                   (3, 2),
                   ]
        for ofs in offsets:
            data = numpy.zeros((201, 201), numpy.uint16)
            # Just one point, so it should be easy to find
            data[100 + ofs[1], 100 + ofs[0]] = 500
            spot_coordinates = spot.FindCenterCoordinates(data)
            numpy.testing.assert_almost_equal(spot_coordinates, ofs, 3)

    def test_zero_bias(self):
        """
        FindCenterCoordinates should estimate the center position without any
        bias; i.e. the expectation value of the difference between the actual
        position and the estimated position should be zero.
        """
        n = len(self.imgdata)
        coords = numpy.array(list(map(spot.FindCenterCoordinates, self.imgdata)))
        delta = coords - self.coords0
        bias = numpy.average(delta, axis=0)
        stdev = numpy.std(delta, axis=0)
        self.assertTrue(all(numpy.abs(bias) < (stdev / numpy.sqrt(n))))
        self.assertTrue(all(numpy.abs(bias) < 5.0e-4))

    def test_anisotropic_accuracy(self):
        """
        FindCenterCoordinates should have equal accuracy in all directions.
        Perform Bartlett's test for equal variances on the residuals in x and
        y at a significance level of 5%.
        """
        coords = numpy.array(list(map(spot.FindCenterCoordinates, self.imgdata)))
        delta = coords - self.coords0
        _, pvalue = scipy.stats.bartlett(delta[:, 0], delta[:, 1])
        self.assertTrue(pvalue > 0.05)

    def test_accuracy(self):
        """
        FindCenterCoordinates should have an accuracy of better than 0.05 px.
        """
        coords = numpy.array(list(map(spot.FindCenterCoordinates, self.imgdata)))
        delta = coords - self.coords0
        stdev = numpy.std(delta.ravel())
        self.assertLess(stdev, 0.05)

    def test_sanity(self):
        """
        Create an image consisting of all zeros and a single pixel with value
        one. FindCenterCoordinates should return the coordinates of this one
        pixel.
        """
        for n in range(5, 12):
            for m in range(5, 12):
                for i in range(2, n - 2):
                    for j in range(2, m - 2):
                        img = numpy.zeros((n, m))
                        img[i, j] = 1
                        xc, yc = spot.FindCenterCoordinates(img)
                        self.assertAlmostEqual(j, xc + 0.5 * (m - 1))
                        self.assertAlmostEqual(i, yc + 0.5 * (n - 1))


class TestRadialSymmetryCenter(unittest.TestCase):
    """
    Unit test class to test the behavior of radial_symmetry_center in
    odemis.util.spot.
    """

    @classmethod
    def setUpClass(cls):
        """Create a synthetic dataset of 1000 spot images."""
        n = 1000
        shape = (9, 9)
        refractive_index = 1
        numerical_aperture = 0.95
        wavelength = 550e-9  # [m]
        magnification = 40
        pixel_size = 6.5e-6  # [m]

        # Ensure that each time when the test case is run we have the same
        # 'random' numbers.
        numpy.random.seed(0)

        sigma = (magnification / pixel_size) * synthetic.psf_sigma_wffm(
            refractive_index, numerical_aperture, wavelength
        )
        coords0 = 0.5 * numpy.asarray(shape) - numpy.random.random_sample((n, 2))
        imgdata = numpy.empty((n,) + shape)
        for i in range(n):
            imgdata[i] = synthetic.psf_gaussian(shape, coords0[i], sigma)
        coords = numpy.array(list(map(spot.radial_symmetry_center, imgdata)))

        # Make the dataset available as an immutable array
        cls.delta = coords - coords0
        cls.delta.flags.writeable = False

    def test_zero_bias(self):
        """
        radial_symmetry_center should estimate the center position without any
        bias; i.e. the expectation value of the difference between the actual
        position and the estimated position should be zero.
        """
        n = len(self.delta)
        bias = numpy.average(self.delta, axis=0)
        stdev = numpy.std(self.delta, axis=0)
        self.assertTrue(all(numpy.abs(bias) < (stdev / numpy.sqrt(n))))
        self.assertTrue(all(numpy.abs(bias) < 5.0e-4))

    def test_anisotropic_accuracy(self):
        """
        radial_symmetry_center should have equal accuracy in all directions.
        Perform Bartlett's test for equal variances on the residuals in `j` and
        `i` at a significance level of 5%.
        """
        _, pvalue = scipy.stats.bartlett(self.delta[:, 0], self.delta[:, 1])
        self.assertTrue(pvalue > 0.05)

    def test_accuracy(self):
        """
        radial_symmetry_center should have an accuracy of better than 0.05 px.
        """
        stdev = numpy.std(self.delta.ravel())
        self.assertLess(stdev, 0.05)

    def test_sanity(self):
        """
        Create an image consisting of all zeros and a single pixel with value
        one. radial_symmetry_center should return the pixel index of this pixel.
        """
        for n in range(5, 12):
            for m in range(5, 12):
                for j in range(2, n - 2):
                    for i in range(2, m - 2):
                        img = numpy.zeros((n, m))
                        img[j, i] = 1
                        jc, ic = spot.radial_symmetry_center(img)
                        self.assertAlmostEqual(j, jc)
                        self.assertAlmostEqual(i, ic)


class TestFindSpotPositions(unittest.TestCase):
    def test_multiple(self):
        """
        `find_spot_positions` should find all spot positions in a generated
        test image.

        """
        n = 100
        shape = (256, 256)
        refractive_index = 1
        numerical_aperture = 0.95
        wavelength = 0.55  # [µm]
        magnification = 40
        pixel_size = 3.45  # [µm]

        # Ensure that each time when the test case is run we have the same
        # 'random' numbers.
        numpy.random.seed(0)

        sigma = (magnification / pixel_size) * synthetic.psf_sigma_wffm(
            refractive_index, numerical_aperture, wavelength
        )

        # Generate a test image containing at most 100 randomly distributed
        # spots with a guaranteed minimal spacing.
        spacing = int(round(10 * sigma))
        border = numpy.asarray((spacing, spacing))
        srange = numpy.asarray(shape) - 2 * border - numpy.array((1, 1))
        loc = border + srange * numpy.random.random_sample((n, 2))
        loc = ensure_spacing(loc, spacing=spacing, p_norm=2)
        image = synthetic.psf_gaussian(shape, loc, sigma)

        ji = spot.find_spot_positions(image, sigma)

        # Map the found spot locations to the known spot locations.
        tree = scipy.spatial.cKDTree(loc)
        # NOTE: Starting SciPy v1.6.0 the `n_jobs` argument will be renamed `workers`
        distances, indices = tree.query(ji, k=1, n_jobs=-1)

        # Check that all spot positions have been found within the required
        # accuracy.
        numpy.testing.assert_array_equal(sorted(indices), range(len(loc)))
        numpy.testing.assert_array_less(distances, 0.05)


if __name__ == "__main__":
    unittest.main()
