# -*- coding: utf-8 -*-
'''
Created on 18 Mar 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Test cases for the spectrum functions

import logging
import math
import time
import unittest

import numpy
from odemis import model
from odemis.util import spectrum

logging.getLogger().setLevel(logging.DEBUG)


class TestGetWavelength(unittest.TestCase):
    
    def test_wl_list(self):
        shape = (220, 1, 1, 50, 400)
        dtype = numpy.dtype("uint16")
        wl_orig = (400e-9 + numpy.arange(shape[0]) * 10e-9).tolist()
        metadata = {model.MD_SW_VERSION: "1.0-test",
                 model.MD_HW_NAME: "fake spec",
                 model.MD_DESCRIPTION: "test3d",
                 model.MD_ACQ_DATE: time.time(),
                 model.MD_BPP: 12,
                 model.MD_BINNING: (1, 1), # px, px
                 model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                 model.MD_WL_LIST: wl_orig,
                 model.MD_POS: (1e-3, -30e-3), # m
                 model.MD_EXP_TIME: 1.2, # s
                }
        da = model.DataArray(numpy.zeros(shape, dtype), metadata)

        wl = spectrum.get_wavelength_per_pixel(da)
        self.assertEqual(len(wl), shape[0])
        self.assertEqual(wl, wl_orig)


class TestGetAngle(unittest.TestCase):

    def test_angle_per_pixel_simple(self):
        """
        Test get_angle_per_pixel when all metadata is good
        """
        shape = (512, 256, 1, 5, 4)
        dtype = numpy.dtype("uint16")
        wl_orig = (400e-9 + numpy.arange(shape[0]) * 10e-9).tolist()
        thetal_orig = numpy.linspace(-1.5, 1.4, shape[1])  # rad
        # Typically the top and bottom angles do no exists, they are replaced by NaN
        thetal_orig[:12] = math.nan
        thetal_orig[-10:] = math.nan
        thetal_orig = thetal_orig.tolist()
        metadata = {
            model.MD_DIMS: "CAZYX",
            model.MD_HW_NAME: "fake AR spec",
            model.MD_DESCRIPTION: "test3d",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_WL_LIST: wl_orig,
            model.MD_THETA_LIST: thetal_orig,
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
        }
        da = model.DataArray(numpy.zeros(shape, dtype), metadata)

        thetal = spectrum.get_angle_per_pixel(da)
        self.assertEqual(len(thetal), shape[1])
        self.assertEqual(thetal, thetal_orig)

        # get_wavelength_per_pixel should also work
        wl = spectrum.get_wavelength_per_pixel(da)
        self.assertEqual(len(wl), shape[0])
        self.assertEqual(wl, wl_orig)

    def test_angle_per_pixel_recreate(self):
        """
        get_angle_per_pixel() should recreate the THETA_LIST based on other metadata if it's not present
        """
        shape = (512, 256, 1, 5, 4)
        dtype = numpy.dtype("uint16")
        wl_orig = (400e-9 + numpy.arange(shape[0]) * 10e-9).tolist()
        metadata = {
            model.MD_DIMS: "CAZYX",
            model.MD_HW_NAME: "fake AR spec",
            model.MD_DESCRIPTION: "test3d",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            # All metadata needed for the Theta creation
            model.MD_WL_LIST: wl_orig,
            model.MD_AR_MIRROR_TOP: [220, 0],
            model.MD_AR_MIRROR_BOTTOM: [30, 0],
            model.MD_AR_FOCUS_DISTANCE: 0.5e-3,
            model.MD_AR_XMAX: 3.8e-3,
            model.MD_AR_PARABOLA_F: 0.8e-3,
        }
        da = model.DataArray(numpy.zeros(shape, dtype), metadata)

        thetal = spectrum.get_angle_per_pixel(da)
        self.assertEqual(len(thetal), shape[1])
        self.assertTrue(all(-3.15 < t < 3.15 for t in thetal if math.isfinite(t)))

        # get_wavelength_per_pixel should also work
        wl = spectrum.get_wavelength_per_pixel(da)
        self.assertEqual(len(wl), shape[0])
        self.assertEqual(wl, wl_orig)


class TestCoefToDA(unittest.TestCase):
    
    def test_simple(self):
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 0.1, 6, 9.1], dtype=float)
        wl_calib = (400 + numpy.arange(len(dcalib)))
        coef = numpy.vstack([wl_calib, dcalib]).T
        self.assertEqual(coef.shape[1], 2)

        da = spectrum.coefficients_to_dataarray(coef)
        self.assertEqual(da.shape, (dcalib.shape[0], 1, 1, 1, 1))
        numpy.testing.assert_equal(da[:, 0, 0, 0, 0], dcalib)
        numpy.testing.assert_equal(da.metadata[model.MD_WL_LIST], wl_calib * 1e-9)

if __name__ == "__main__":
    unittest.main()
