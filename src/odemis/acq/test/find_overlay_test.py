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
import time
import unittest

from odemis import model
from odemis.acq import find_overlay
from odemis.dataio import hdf5
from odemis.util import img


logging.getLogger().setLevel(logging.DEBUG)


############## TO BE REMOVED ON TESTING##############
grid_data = hdf5.read_data("spots_image_m.h5")
C, T, Z, Y, X = grid_data[0].shape
grid_data[0].shape = Y, X
fake_spots = grid_data[0]

grid_data = hdf5.read_data("ele_image_m.h5")
C, T, Z, Y, X = grid_data[0].shape
grid_data[0].shape = Y, X
fake_ele = grid_data[0]

grid_data = hdf5.read_data("opt_image_m.h5")
C, T, Z, Y, X = grid_data[0].shape
grid_data[0].shape = Y, X
fake_opt = grid_data[0]
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

        f = find_overlay.FindOverlay((7, 7), 0.1, 1e-06, escan, ccd, detector)

        # opt_im = fake_input
        transformed_image = fake_opt
        transformation_values, transform_md = f.result()
        print transform_md
        print transformation_values
        current_md = transformed_image.metadata
        merged_md = img.mergeMetadata(current_md, transform_md)
#         rotation = transformed_image.metadata.get(model.MD_ROTATION, 0)
#         pixel_size = transformed_image.metadata.get(model.MD_PIXEL_SIZE, (0, 0))
#         position = transformed_image.metadata.get(model.MD_POS, (0, 0))
#
#         transformed_image.metadata[model.MD_ROTATION] = rotation - rotation_cor
#         transformed_image.metadata[model.MD_PIXEL_SIZE] = (pixel_size[0] * pixel_size_cor[0],
#                                                            pixel_size[1] * pixel_size_cor[1])
#         transformed_image.metadata[model.MD_POS] = (position[0] + position_cor[0],
#                                                     position[1] - position_cor[1])
        print merged_md
        transformed_image.metadata = merged_md
        hdf5.export("overlay_image.h5", [transformed_image, fake_ele])

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

