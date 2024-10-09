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

from odemis import model, dataio
from odemis.dataio.tiff import read_data
from odemis.util.spot import MaximaFind
from odemis.util.transform import AffineTransform, alt_transformation_matrix_to_implicit, _rotation_matrix_from_angle
from plugins.chromatic_correction import (BEAD_QUANTITY, SCALE, TOLERANCE,
                                          find_corresponding_points, get_chromatic_correction_dict)

PIXEL_SIZE = (1e-6, 1e-6)  # m
# Affine parameters that are used on reference image
# Translation can be any value. Scale is fixed to one if there is some rotation and shear else both scale, shear
# can be changed by keeping the rotation to zero
SHEAR = 0.9
SCALE = [2, 3]
ROTATION = numpy.radians(0)
TRANSLATION = [2, 1]
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

    def test_chromatic_correction_with_fake_data_compare_methods(self):
        """Given two images in which one is reference image and other is affine transformed image of reference image.
         The calculated affine parameters should correct the transformed image parameters back to the reference image
         Two methods namely RSU and RSL from odemis.util.transform.py are compared to show which method yields
         good results"""
        # reference image
        ref = self.das[0]
        ref.metadata.update({model.MD_ROTATION: 0})
        ref.metadata.update({model.MD_PIXEL_SIZE: PIXEL_SIZE})
        ref.metadata.update({model.MD_SHEAR: 0})
        ref.metadata.update({model.MD_POS: [0, 0]})
        # modified reference image
        modified_im = self.das[0]
        modified_im.metadata.update({model.MD_ROTATION: ROTATION})
        modified_im.metadata.update({model.MD_POS: [TRANSLATION[0] * PIXEL_SIZE[0], -TRANSLATION[1] * PIXEL_SIZE[1]]})
        modified_im.metadata.update(
            {model.MD_PIXEL_SIZE: [SCALE[0] * PIXEL_SIZE[0], SCALE[1] * PIXEL_SIZE[1]]})
        modified_im.metadata.update({model.MD_SHEAR: SHEAR})

        # Generate synthetic keypoints of reference and modified images based on given affine parameters
        r_m = _rotation_matrix_from_angle(ROTATION)
        # Make sure the given points are not in one line
        points1 = numpy.array([[5, 5], [5, 10], [10, 8], [11, 11]])
        shear_m = numpy.array([[1, SHEAR], [0, 1]])
        points2 = TRANSLATION + SCALE * ((r_m @ (shear_m @ points1.transpose())).transpose())
        print(f"points 1 {points1}")
        print(f"points 2 {points2}")

        affine = AffineTransform.from_pointset(points1, points2)
        mat = numpy.eye(3)
        mat[:2, :2] = affine.matrix
        mat[:2, 2] = affine.translation
        tx = mat[0, 2]
        ty = mat[1, 2]
        # Projection image y-axis is upside down, add negative sign
        translation = [tx * PIXEL_SIZE[0], -ty * PIXEL_SIZE[1]]

        # The images are saved to compare the result of affine correction visually
        # the images with filenames ref and the modified_rsu should overlap
        dataio.tiff.export("sample_ref.tiff", ref)
        dataio.tiff.export("sample_modified_ref.tiff", modified_im)
        # Method 1
        scale_u, rotation_u, shear_u = alt_transformation_matrix_to_implicit(affine.matrix, "RSU")
        scale_cor = [1 / scale_u[0], 1 / scale_u[1]]
        modified_im.metadata.update({model.MD_ROTATION_COR: rotation_u})  # rotation corr
        modified_im.metadata.update({model.MD_POS_COR: translation})  # translation correction
        modified_im.metadata.update(
            {model.MD_PIXEL_SIZE_COR: scale_cor})  # scale correction
        modified_im.metadata.update({model.MD_SHEAR_COR: shear_u})  # shear corr
        dataio.tiff.export("sample_modified_rsu.tiff", modified_im)
        # the correction metadata should be same as given affine parameters
        self.assertAlmostEqual(modified_im.metadata[model.MD_ROTATION_COR], ROTATION)
        self.assertAlmostEqual(modified_im.metadata[model.MD_SHEAR_COR], SHEAR)
        self.assertAlmostEqual(
            modified_im.metadata[model.MD_PIXEL_SIZE_COR][0] * SCALE[0], 1)
        self.assertAlmostEqual(
            modified_im.metadata[model.MD_PIXEL_SIZE_COR][1] * SCALE[1], 1)
        numpy.testing.assert_almost_equal(modified_im.metadata[model.MD_POS_COR], modified_im.metadata[model.MD_POS])
        # Method 2
        scale_l, rotation_l, shear_l = alt_transformation_matrix_to_implicit(affine.matrix, "RSL")
        scale_cor = [1 / scale_l[0], 1 / scale_l[1]]
        modified_im.metadata.update({model.MD_ROTATION_COR: rotation_l})  # rotation corr
        modified_im.metadata.update({model.MD_POS_COR: translation})  # translation correction
        modified_im.metadata.update(
            {model.MD_PIXEL_SIZE_COR: scale_cor})  # scale correction
        modified_im.metadata.update({model.MD_SHEAR_COR: shear_l})  # shear corr
        dataio.tiff.export("sample_modified_rsl.tiff", modified_im)
        # if shear is present in the given affine parameters then RSU and RSL have different results otherwise
        # these methods provide similar results in the absence of shear
        if SHEAR > 0:
            self.assertNotAlmostEqual(modified_im.metadata[model.MD_SHEAR_COR], SHEAR)

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
