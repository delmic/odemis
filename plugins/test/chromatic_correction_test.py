# -*- coding: utf-8 -*-
"""
Copyright © 2024 Delmic

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

import numpy

from odemis import model
from odemis.dataio.tiff import read_data
from odemis.util.spot import MaximaFind
from odemis.util.transform import AffineTransform, alt_transformation_matrix_to_implicit, _rotation_matrix_from_angle
from plugins.chromatic_correction import (BEAD_QUANTITY, TOLERANCE,
                                          find_corresponding_points, get_chromatic_correction_dict)

PIXEL_SIZE = (1e-6, 1e-6)  # m
# Default values when there the datasets are similar
DEFAULT_SHEAR = 0
DEFAULT_SCALE = [1, 1]
DEFAULT_ROTATION = 0
DEFAULT_TRANSLATION = [0, 0]


class TestChromaticAberrationCorrection(unittest.TestCase):
    """
    Test the Chromatic Aberration Correction functions used in the plugin
    """

    @classmethod
    def setUpClass(cls):
        cls.das = read_data('FOV1_green.tif')

    def test_affine_transformation_with_fake_data(self):
        """Test the affine transformation values between the point indices of reference and modified data"""
        # Affine parameters that are used on reference image
        # Translation can be any value. Scale is fixed to one if there is some rotation and shear else both scale, shear
        # can be changed by keeping the rotation to zero
        # Set 1: change scale and sheer, keeping the rotation unchanged
        # Set 2: change the rotation and sheer, keeping the scale unchanged
        affine_sets = {"set_1": {"shear": 0.9, "scale": [2, 3], "rotation": numpy.radians(0), "translation": [2, 1]},
                       "set_2": {"shear": 0.4, "scale": [1, 1], "rotation": numpy.radians(5), "translation": [2, 3]}}
        # Make sure the given points indices are not in one line
        points_ref = numpy.array([[5, 5], [5, 10], [10, 8], [11, 11]])

        for affine_set in affine_sets.values():
            shear = affine_set["shear"]
            scale = affine_set["scale"]
            rotation = affine_set["rotation"]
            translation = affine_set["translation"]
            # Modify the reference points based on provided affine set
            r_m = _rotation_matrix_from_angle(rotation)
            shear_m = numpy.array([[1, shear], [0, 1]])
            points_mod = translation + scale * ((r_m @ (shear_m @ points_ref.transpose())).transpose())
            # Compute the transformation values between the reference and the modified points
            affine = AffineTransform.from_pointset(points_ref, points_mod)
            scale_u, rotation_u, shear_u = alt_transformation_matrix_to_implicit(affine.matrix, "RSU")
            scale_cor = [1 / scale_u[0], 1 / scale_u[1]]
            # Test that the computed transformation values are same as given affine parameters
            self.assertAlmostEqual(rotation_u, rotation)
            self.assertAlmostEqual(shear_u, shear)
            self.assertAlmostEqual(scale_cor[0] * scale[0], 1)
            self.assertAlmostEqual(scale_cor[1] * scale[1], 1)

    def test_chromatic_correction_same_data(self):
        """Check if the value are equal to default when same two datasets are same i.e.
        without any chromatic aberration"""
        im = self.das[0]
        points_ref = MaximaFind(im, BEAD_QUANTITY, 14)
        points_im = MaximaFind(im, BEAD_QUANTITY, 14)
        corresponding_pairs = find_corresponding_points(points_ref, points_im, TOLERANCE * 14)
        # Extract the matching points
        points1 = points_ref[corresponding_pairs[:, 0]]
        points2 = points_im[corresponding_pairs[:, 1]]
        correction_dict = get_chromatic_correction_dict(im, points1, points2)
        im.metadata.update(correction_dict)
        # Assert that the values are equal to default
        self.assertAlmostEqual(im.metadata[model.MD_ROTATION_COR], DEFAULT_ROTATION)
        self.assertAlmostEqual(im.metadata[model.MD_SHEAR_COR], DEFAULT_SHEAR)
        self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE_COR][0], DEFAULT_SCALE[0])
        self.assertAlmostEqual(im.metadata[model.MD_PIXEL_SIZE_COR][1], DEFAULT_SCALE[1])
        numpy.testing.assert_almost_equal(im.metadata[model.MD_POS_COR], DEFAULT_TRANSLATION)
