# -*- coding: utf-8 -*-
"""
Created on 12 Feb 2025

Copyright © Delmic

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
import os
import unittest

import numpy
from odemis import model
from odemis.acq.align import tdct

RESULTS_PATH = os.path.join(os.getcwd(), "correlation_data.yaml")

class TestTDCT(unittest.TestCase):

    def tearDown(self):
        if os.path.exists(RESULTS_PATH):
            os.remove(RESULTS_PATH)

    def test_convert_das_to_numpy_stack(self):
        """Test the conversion of DataArrays to numpy stack"""
        nc, nz, ny, nx = 3, 10, 1000, 1000
        data_2d = numpy.random.random((ny, nx))
        data_3d = numpy.random.random((nz, ny, nx))
        data_5d = numpy.random.random((1, 1, nz, ny, nx))

        # Test 2D input
        da_2d = model.DataArray(data_2d)
        result_2d = tdct._convert_das_to_numpy_stack([da_2d])
        self.assertEqual(result_2d.shape, (1, 1, ny, nx))
        self.assertEqual(result_2d.ndim, 4)

        # Test 3D input
        da_3d = model.DataArray(data_3d)
        result_3d = tdct._convert_das_to_numpy_stack([da_3d])
        self.assertEqual(result_3d.shape, (1, nz, ny, nx))
        self.assertEqual(result_3d.ndim, 4)

        # Test 5D input
        da_5d = model.DataArray(data_5d)
        result_5d = tdct._convert_das_to_numpy_stack([da_5d])
        self.assertEqual(result_5d.shape, (1, nz, ny, nx))
        self.assertEqual(result_5d.ndim, 4)

        # Test multiple channels
        result_multi_3d = tdct._convert_das_to_numpy_stack([da_3d, da_3d, da_3d])
        self.assertEqual(result_multi_3d.shape, (nc, nz, ny, nx))
        self.assertEqual(result_multi_3d.ndim, 4)

        result_multi_5d = tdct._convert_das_to_numpy_stack([da_5d, da_5d, da_5d])
        self.assertEqual(result_multi_5d.shape, (nc, nz, ny, nx))
        self.assertEqual(result_multi_5d.ndim, 4)

        # Test invalid dimensions
        data_1d = numpy.random.random(nx)
        da_1d = model.DataArray(data_1d)
        with self.assertRaises(AssertionError):
            tdct._convert_das_to_numpy_stack([da_1d])

    def test_run_tdct_correlation(self):
        """Run the TDCT correlation and validate the output"""
        fib_coords = numpy.array(
            [[100, 100],
             [900, 100],
            [900, 900],
            [100, 900]], dtype=numpy.float32)
        fm_coords = numpy.array(
            [[100, 100, 3],
             [1000, 100, 4],
            [900, 1000, 8],
            [100, 1000, 9]], dtype=numpy.float32)
        poi_coords = numpy.array([[500, 500, 5]], dtype=numpy.float32)
        fib_image = model.DataArray(numpy.zeros(shape=(1024, 1536)),
                                    metadata={
                                        model.MD_PIXEL_SIZE: (100e-9, 100e-9)})
        fm_image = numpy.zeros(shape=(10, 1024, 1024))
        path = os.getcwd()

        # run the correlation
        ret = tdct.run_tdct_correlation(fib_coords=fib_coords,
                                        fm_coords=fm_coords,
                                        poi_coords=poi_coords,
                                        fib_image=fib_image,
                                        fm_image=fm_image,
                                        path=path)

        self.assertTrue(isinstance(ret, dict))
        self.assertTrue(RESULTS_PATH)

        # extract the poi coordinate from the correlation results
        poi = tdct.get_poi_coordinate(ret)

        # check the poi coordinate match the expected value
        poi_um = ret["output"]["poi"][0]["px_um"]

        self.assertEqual(len(poi), 2)
        self.assertAlmostEqual(poi[0], poi_um[0] * 1e-6)
        self.assertAlmostEqual(poi[1], poi_um[1] * 1e-6)
