# -*- coding: utf-8 -*-
"""
Created on 5 Dec 2024

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
from odemis.util.interpolation import (
    interpolate_z_stack,
    multi_channel_interpolation,
)


class TestInterpolationUtil(unittest.TestCase):
    """
    Tests the util-acquisition functions for filename suggestions.
    """

    def setUp(self):
        arr = numpy.zeros(shape=(1, 1, 10, 1024, 1024), dtype=numpy.uint16)
        md = {model.MD_PIXEL_SIZE: (1e-7, 1e-7, 1e-6), model.MD_DIMS: "CTZYX"}
        self.z_stack = model.DataArray(arr, metadata=md)
        self.multi_channel_z_stack = (self.z_stack, self.z_stack, self.z_stack)

    def test_interpolation(self):
        """Tests the interpolation of a z-stack."""
        # get expected number of z-slices
        px = self.z_stack.metadata[model.MD_PIXEL_SIZE]
        nz_expected = round(px[2] / px[0]) * self.z_stack.shape[2]

        # Test linear interpolation
        interpolated = interpolate_z_stack(self.z_stack, method="linear")
        assert interpolated.shape == (1, 1, nz_expected, 1024, 1024)
        assert interpolated.metadata[model.MD_PIXEL_SIZE][2] == px[0]
        assert interpolated.metadata[model.MD_PIXEL_SIZE][0] == px[0]

        # Test cubic interpolation
        interpolated = interpolate_z_stack(self.z_stack, method="cubic")
        assert interpolated.shape == (1, 1, nz_expected, 1024, 1024)
        assert interpolated.metadata[model.MD_PIXEL_SIZE][2] == px[0]
        assert interpolated.metadata[model.MD_PIXEL_SIZE][0] == px[0]

        # test input and output pixel sizes
        interpolated = interpolate_z_stack(self.z_stack, pixelsize_out=1e-6)
        assert interpolated.metadata[model.MD_PIXEL_SIZE][2] == 1e-6

    def test_multi_channel_interpolation(self):
        px = self.multi_channel_z_stack[0].metadata[model.MD_PIXEL_SIZE]
        nz_expected = round(px[2] / px[0]) * self.multi_channel_z_stack[0].shape[2]

        interpolated = multi_channel_interpolation(self.multi_channel_z_stack)

        # check shape
        assert len(interpolated) == len(self.multi_channel_z_stack)
        for ch in interpolated:
            assert ch.shape == (1, 1, nz_expected, 1024, 1024)

            assert ch.metadata[model.MD_PIXEL_SIZE][2] == px[0]
            assert ch.metadata[model.MD_PIXEL_SIZE][0] == px[0]

    def test_interpolation_requirements(self):

        # wrong shape (4D)
        four_d = numpy.zeros(shape=(1, 1, 1024, 1024), dtype=numpy.uint16)
        md = {model.MD_PIXEL_SIZE: (1e-7, 1e-7, 1e-6), model.MD_DIMS: "CZYX"}
        with self.assertRaises(ValueError):
            interpolate_z_stack(model.DataArray(four_d, md))

        # wrong shape (multi-channel)
        multi_channel_wrong_shape = numpy.zeros(shape=(2, 1, 10, 1024, 1024), dtype=numpy.uint16)

        with self.assertRaises(ValueError):
            interpolate_z_stack(model.DataArray(multi_channel_wrong_shape, md))

        # bad dims
        bad_dims = numpy.zeros(shape=(1, 1, 10, 1024, 1024), dtype=numpy.uint16)
        bad_md = {model.MD_PIXEL_SIZE: (1e-7, 1e-7, 1e-6), model.MD_DIMS: "CTYX"}

        with self.assertRaises(ValueError):
            interpolate_z_stack(model.DataArray(bad_dims, metadata=bad_md))
