# -*- coding: utf-8 -*-
"""
Created on 10 Jan 2014

@author: Kimon Tsitsikas, Sabrina Rossberger

Copyright © 2014-2019 Kimon Tsitsikas, Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis.
If not, see http://www.gnu.org/licenses/.
"""

import numpy

from odemis.model import MD_POL_MODE, MD_POL_S1

from odemis.util.img import ensure2DImage

from odemis import model
from odemis.dataio import hdf5
from odemis.util import angleres
import unittest

from odemis.util.img import RGB2Greyscale


class TestAngleResolvedDataConversion(unittest.TestCase):
    """
    Test AngleResolved2Polar, AngleResolved2Rectangular and Rectangular2Polar.
    """
    def setUp(self):
        data = hdf5.read_data("ar-example-input.h5")
        self.data = data

        data_inverted_mirror = hdf5.read_data("ar-inverted-mirror.h5")
        self.data_invMir = data_inverted_mirror

        # test also for different polar parameters
        data_mini = hdf5.read_data("ar-example-minimirror-input.h5")
        self.data_mini = data_mini

        white_data_512 = model.DataArray(numpy.empty((512, 512), dtype="uint16"))
        white_data_512[...] = 255
        white_mag_512 = 0.4917
        white_spxs_512 = (13e-6, 13e-6)
        white_binning_512 = (2, 2)
        white_data_512.metadata[model.MD_AR_POLE] = (283, 259)
        white_data_512.metadata[model.MD_AR_XMAX] = 13.25e-3
        white_data_512.metadata[model.MD_AR_HOLE_DIAMETER] = 0.6e-3
        white_data_512.metadata[model.MD_AR_FOCUS_DISTANCE] = 0.5e-3
        white_data_512.metadata[model.MD_AR_PARABOLA_F] = 2.5e-3
        white_pxs_512 = (white_spxs_512[0] * white_binning_512[0] / white_mag_512,
               white_spxs_512[1] * white_binning_512[1] / white_mag_512)
        white_data_512.metadata[model.MD_PIXEL_SIZE] = white_pxs_512
        self.white_data_512 = white_data_512

        white_data_1024 = model.DataArray(numpy.empty((1024, 1024), dtype="uint16"))
        white_data_1024[...] = 255
        white_mag_1024 = 0.4917
        white_spxs_1024 = (13e-6, 13e-6)
        white_binning_1024 = (2, 2)
        white_data_1024.metadata[model.MD_AR_POLE] = (283, 259)
        white_data_1024.metadata[model.MD_AR_XMAX] = 13.25e-3
        white_data_1024.metadata[model.MD_AR_HOLE_DIAMETER] = 0.6e-3
        white_data_1024.metadata[model.MD_AR_FOCUS_DISTANCE] = 0.5e-3
        white_data_1024.metadata[model.MD_AR_PARABOLA_F] = 2.5e-3
        white_pxs_1024 = (white_spxs_1024[0] * white_binning_1024[0] / white_mag_1024,
               white_spxs_1024[1] * white_binning_1024[1] / white_mag_1024)
        white_data_1024.metadata[model.MD_PIXEL_SIZE] = white_pxs_1024
        self.white_data_1024 = white_data_1024

        white_data_2500 = model.DataArray(numpy.empty((2560, 2160), dtype="uint16"))
        white_data_2500[...] = 255
        white_mag_2500 = 0.4917
        white_spxs_2500 = (13e-6, 13e-6)
        white_binning_2500 = (2, 2)
        white_data_2500.metadata[model.MD_AR_POLE] = (283, 259)
        white_data_2500.metadata[model.MD_AR_XMAX] = 13.25e-3
        white_data_2500.metadata[model.MD_AR_HOLE_DIAMETER] = 0.6e-3
        white_data_2500.metadata[model.MD_AR_FOCUS_DISTANCE] = 0.5e-3
        white_data_2500.metadata[model.MD_AR_PARABOLA_F] = 2.5e-3
        # These values makes the computation much harder:
#         white_mag_2500 = 0.53
#         white_spxs_2500 = (6.5e-6, 6.5e-6)
#         white_binning_2500 = (1, 1)
#         white_data_2500.metadata[model.MD_AR_POLE] = (1480, 1129)
        white_pxs_2500 = (white_spxs_2500[0] * white_binning_2500[0] / white_mag_2500,
               white_spxs_2500[1] * white_binning_2500[1] / white_mag_2500)
        white_data_2500.metadata[model.MD_PIXEL_SIZE] = white_pxs_2500
        self.white_data_2500 = white_data_2500

    def test_inverted_mirror_ar2polar(self):
        data_invMirror = ensure2DImage(self.data_invMir[0])
        result_invMirror = angleres.AngleResolved2Polar(data_invMirror, 1134)

        # get the inverted image of the one that corresponds to the flipped mirror
        data = data_invMirror[::-1, :]
        data.metadata[model.MD_AR_FOCUS_DISTANCE] *= -1
        arpole = data.metadata[model.MD_AR_POLE]
        data.metadata[model.MD_AR_POLE] = (arpole[0], data_invMirror.shape[0] - 1 - arpole[1])

        result_standardMirror = angleres.AngleResolved2Polar(data, 1134)

        numpy.testing.assert_allclose(result_invMirror, result_standardMirror, atol=1e-7)

    def test_precomputed(self):
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = angleres.AngleResolved2Polar(data[0], 201)

        desired_output = hdf5.read_data("desired201x201image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_precomputed_mini(self):
        data_mini = self.data_mini
        C, T, Z, Y, X = data_mini[0].shape
        data_mini[0].shape = Y, X
        result = angleres.AngleResolved2Polar(data_mini[0], 201)

        desired_output = hdf5.read_data("desired201x201imagemini.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], atol=1e-07)

    def test_uint16_input(self):
        """
        Tests for input of DataArray with uint16 ndarray.
        """
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        result = angleres.AngleResolved2Polar(data[0], 201)

        desired_output = hdf5.read_data("desired201x201image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_uint16_input_rect(self):
        """
        Tests for input of DataArray with uint16 ndarray to rectangular projection
        """
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = angleres.AngleResolved2Rectangular(data[0], (100, 400))

        self.assertEqual(result.shape, (100, 400))

    def test_inverted_mirror_ar2rectangular(self):
        data_invMirror = ensure2DImage(self.data_invMir[0])
        result_invMirror = angleres.AngleResolved2Rectangular(data_invMirror, (90, 360))

        # get the inverted image of the one that corresponds to the flipped mirror
        data = data_invMirror[::-1, :]
        data.metadata[model.MD_AR_FOCUS_DISTANCE] *= -1
        arpole = data.metadata[model.MD_AR_POLE]
        data.metadata[model.MD_AR_POLE] = (arpole[0], data_invMirror.shape[0] - 1 - arpole[1])

        result_standardMirror = angleres.AngleResolved2Rectangular(data, (90, 360))

        numpy.testing.assert_allclose(result_invMirror, result_standardMirror, atol=1e-7)

    def test_uint16_input_rect_intensity(self):
        """
        Tests for input of DataArray with uint16 ndarray to rectangular projection checking that the
        expected intensity values are at the correct theta/phi position in the rectangular and polar representation.
        This test case is optimized for the current input image. If ever changed, this test case might fail!
        """
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        data[0] = data[0][0:256, 0:241]

        result_rectangular = angleres.AngleResolved2Rectangular(data[0], (90, 360))

        self.assertEqual(result_rectangular.shape, (90, 360))

        # get theta/phi corresponding to x/y in data
        theta_data, phi_data, intensity_data, circle_mask_dilated = \
            angleres._ExtractAngleInformation(data[0], hole=False)

        # find the position in the raw data with max intensity
        pos_max = numpy.where(data[0] == numpy.max(data[0]))
        x = pos_max[0][0]
        y = pos_max[1][0]

        # find the intensity value for the same position but corrected for photon collection efficiency
        intensity_data_I = intensity_data[x][y]

        # find the corresponding theta/phi angles
        phi = phi_data[x][y]
        theta = theta_data[x][y]

        # find the position in the rectangular representation
        px_phi = int(numpy.round(result_rectangular.shape[1] / (2 * numpy.pi) * phi))
        px_theta = int(numpy.round(result_rectangular.shape[0] / (numpy.pi / 2) * theta))
        result_I_rectangular = result_rectangular[px_theta][px_phi]

        # test values are close due to rounding the px pos and interpolation
        numpy.testing.assert_allclose(intensity_data_I, result_I_rectangular, atol=10e6)

        # find a second characteristic position in raw image
        x1 = 97
        y1 = 77

        # find the intensity value for the same position but corrected for photon collection efficiency
        intensity_data_I_rectangular_2 = intensity_data[x1][y1]

        # find the corresponding theta/phi angles
        phi2 = phi_data[x1][y1]
        theta2 = theta_data[x1][y1]

        px_phi2 = int(numpy.round(result_rectangular.shape[1] / (2 * numpy.pi) * phi2))
        px_theta2 = int(numpy.round(result_rectangular.shape[0] / (numpy.pi / 2) * theta2))

        result_I_rectangular_2 = result_rectangular[px_theta2-1][px_phi2-1]  # Note rounding problems

        # test values are close due to rounding the px pos and interpolation
        numpy.testing.assert_allclose(intensity_data_I_rectangular_2, result_I_rectangular_2, atol=10e6)

    def test_int8_input(self):
        """
        Tests for input of DataArray with int8 ndarray.
        """
        data = self.data
        # scipy.misc.bytescale(data)  # for debug
        data[0] = data[0].astype(numpy.int64)
        data[0] = numpy.right_shift(data[0], 8)
        data[0] = data[0].astype(numpy.int8)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = angleres.AngleResolved2Polar(data[0], 201)

        desired_output = angleres.AngleResolved2Polar(data[0].astype(float), 201)

        numpy.testing.assert_allclose(result, desired_output, rtol=1e-04)

    def test_float_input(self):
        """
        Tests for input of DataArray with float ndarray.
        """
        data = self.data
        data[0] = data[0].astype(numpy.float)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = angleres.AngleResolved2Polar(data[0], 201)

        desired_output = hdf5.read_data("desired201x201image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_100x100(self):
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        result = angleres.AngleResolved2Polar(data[0], 101)

        desired_output = hdf5.read_data("desired100x100image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_1000x1000(self):
        data = self.data
        data[0] = data[0].astype(numpy.int64)
        data[0] = numpy.right_shift(data[0], 8)
        data[0] = data[0].astype(numpy.int8)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        result = angleres.AngleResolved2Polar(data[0], 1001)

        desired_output = hdf5.read_data("desired1000x1000image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1)

    def test_2000x2000(self):
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        result = angleres.AngleResolved2Polar(data[0], 2001)

        desired_output = hdf5.read_data("desired2000x2000image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_512x512(self):
        """
        Test for 512x512 white image input
        """
        white_data_512 = self.white_data_512
        result = angleres.AngleResolved2Polar(white_data_512, 201)

        desired_output = hdf5.read_data("desired_white_512.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_1024x1024(self):
        """
        Test for 1024x1024 white image input
        """
        white_data_1024 = self.white_data_1024
        result = angleres.AngleResolved2Polar(white_data_1024, 201)

        desired_output = hdf5.read_data("desired_white_1024.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_2560x2160(self):
        """
        Test for 2560x2160 white image input
        """
        white_data_2500 = self.white_data_2500

        result = angleres.AngleResolved2Polar(white_data_2500, 2000)

        desired_output = hdf5.read_data("desired_white_2500.h5")

        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_background_substraction_precomputed(self):
        """
        Test clean up before polar conversion
        """
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = angleres.ARBackgroundSubtract(data[0])
        result = angleres.AngleResolved2Polar(clean_data, 201)

        desired_output = hdf5.read_data("substracted_background_image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_background_substraction_uint16_input(self):
        """
        Tests for input of DataArray with uint16 ndarray.
        """
        data = self.data
        data[0] = data[0].astype(numpy.uint16)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = angleres.ARBackgroundSubtract(data[0])
        result = angleres.AngleResolved2Polar(clean_data, 201)

        desired_output = hdf5.read_data("substracted_background_image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_background_substraction_int8_input(self):
        """
        Tests for input of DataArray with int8 ndarray.
        """
        data = self.data
        # scipy.misc.bytescale(data)
        data[0] = data[0].astype(numpy.int64)
        data[0] = numpy.right_shift(data[0], 8)
        data[0] = data[0].astype(numpy.int8)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = angleres.ARBackgroundSubtract(data[0])
        result = angleres.AngleResolved2Polar(clean_data, 201)

        desired_output = angleres.AngleResolved2Polar(data[0].astype(float), 201)

        numpy.testing.assert_allclose(result, desired_output, rtol=1)

    def test_background_substraction_float_input(self):
        """
        Tests for input of DataArray with float ndarray.
        """
        data = self.data
        data[0] = data[0].astype(numpy.float)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = angleres.ARBackgroundSubtract(data[0])
        result = angleres.AngleResolved2Polar(clean_data, 201)

        desired_output = hdf5.read_data("substracted_background_image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_uint16_input_rect2polar(self):
        """
        Tests for input of DataArray with uint16 ndarray from rectangular projection to polar projection.
        """
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        data[0].metadata[MD_POL_MODE] = MD_POL_S1  # add necessary metadata

        # convert to rectangular
        data_rect = angleres.AngleResolved2Rectangular(data[0], (100, 400))
        # convert from rectangular to polar
        result = angleres.Rectangular2Polar(data_rect, 201)
        result_polar_1 = RGB2Greyscale(result)

        # convert input data directly to polar representation
        result_polar_2 = angleres.AngleResolved2Polar(data[0], 201)

        self.assertEqual(result.shape, (201, 201, 3))
        self.assertEqual(result_polar_2.shape, result_polar_1.shape)


if __name__ == "__main__":
    # for debug:
    # import sys;sys.argv = ['', 'TestPolarConversionOutput.test_2000x2000']
    unittest.main()
    # suite = unittest.TestLoader().loadTestsFromTestCase(TestPolarConversionOutput)
    # unittest.TextTestRunner(verbosity=2).run(suite)

