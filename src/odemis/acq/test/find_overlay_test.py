# -*- coding: utf-8 -*-
'''
Created on 19 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from concurrent import futures
import logging
import numpy
import time
import unittest

from odemis import model
from odemis.acq import find_overlay
from odemis.dataio import hdf5


logging.getLogger().setLevel(logging.DEBUG)


############## TO BE REMOVED ON TESTING##############
grid_data = hdf5.read_data("spots_img.h5")
C, T, Z, Y, X = grid_data[0].shape
grid_data[0].shape = Y, X
fake_input = grid_data[0]

grid_data = hdf5.read_data("overlay_image1.h5")
C, T, Z, Y, X = grid_data[0].shape
grid_data[0].shape = Y, X
fake_input2 = grid_data[0]

grid_data = hdf5.read_data("38.h5")
C, T, Z, Y, X = grid_data[1].shape
grid_data[1].shape = Y, X
fake_input3 = grid_data[1]
#####################################################

class TestOverlay(unittest.TestCase):
    """
    Test Overlay functions
    """
    def setUp(self):
        self._escan = None
        self._detector = None
        self._ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                self._escan = c
            elif c.role == "se-detector":
                self._detector = c
            elif c.role == "ccd":
                self._ccd = c
        if not all([self._escan, self._detector, self._ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        # self._overlay = find_overlay.Overlay()

    #@unittest.skip("skip")
    def test_find_overlay(self):
        """
        Test FindOverlay
        """
        escan = self._escan
        detector = self._detector
        ccd = self._ccd
        # overlay = self._overlay

        f = find_overlay.FindOverlay((4, 4), 0.1, 1e-06, escan, ccd, detector)

        # opt_im = fake_input
        transformed_image = fake_input
        ((calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation), transformed_data = f.result()
        electron_grid = hdf5.read_data("electron_grid.h5")[0]
        print "PXS"
        print escan.pixelSize.value
        sem_width = [r * p for r, p in zip(escan.shape, escan.pixelSize.value)]
        eg_pxs = [w / s for w, s in zip(sem_width, electron_grid.shape[-1:-3:-1])]
        electron_grid.metadata[model.MD_PIXEL_SIZE] = eg_pxs
        print eg_pxs
        electron_grid.metadata[model.MD_POS] = fake_input.metadata[model.MD_POS]
        # electron_grid.metadata[model.MD_POS] = (0, 0)
        transformed_image.metadata = transformed_data
        print fake_input2.metadata
        hdf5.export("overlay_image.h5", [fake_input2, fake_input3])
        # hdf5.export("transformed_image.h5", [opt_im, transformed_image])
        print ((calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation), transformed_data
        # print transformed_image
        #numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation),
#                                           (-280.91827079065121, -195.55748765461769, 13.9363892133, 13.9363892133, -1.47833441067),
#                                           decimal=1)

    @unittest.skip("skip")
    def test_find_overlay_failure(self):
        """
        Test FindOverlay failure due to low maximum allowed difference
        """
        escan = self._escan
        detector = self._detector
        ccd = self._ccd
        # overlay = self._overlay

        f = find_overlay.FindOverlay((9, 9), 1e-06, 1e-08, escan, ccd, detector)

        self.assertRaises(ValueError, f.result)

    @unittest.skip("skip")
    def test_find_overlay_cancelled(self):
        """
        Test FindOverlay cancellation
        """
        escan = self._escan
        detector = self._detector
        ccd = self._ccd
        # overlay = self._overlay

        f = find_overlay.FindOverlay((9, 9), 1e-06, 1e-07, escan, ccd, detector)
        time.sleep(0.04)  # Cancel almost after the half grid is scanned

        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        self.assertRaises(futures.CancelledError, f.result)

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestOverlay)
    unittest.TextTestRunner(verbosity=2).run(suite)

