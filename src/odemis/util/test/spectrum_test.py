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
import numpy
from odemis import model
from odemis.util import spectrum
import time
import unittest
from builtins import range

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
        

class TestCoefToDA(unittest.TestCase):
    
    def test_simple(self):
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 0.1, 6, 9.1], dtype=numpy.float)
        wl_calib = (400 + numpy.arange(len(dcalib)))
        coef = numpy.vstack([wl_calib, dcalib]).T
        self.assertEqual(coef.shape[1], 2)

        da = spectrum.coefficients_to_dataarray(coef)
        self.assertEqual(da.shape, (dcalib.shape[0], 1, 1, 1, 1))
        numpy.testing.assert_equal(da[:, 0, 0, 0, 0], dcalib)
        numpy.testing.assert_equal(da.metadata[model.MD_WL_LIST], wl_calib * 1e-9)

if __name__ == "__main__":
    unittest.main()
