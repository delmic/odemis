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


logging.getLogger().setLevel(logging.DEBUG)

@unittest.skip("skip")
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

        f = find_overlay.FindOverlay((5, 5), 0.1, 1e-06, escan, ccd, detector)

        ((calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation) = f.result()
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation),
                                          (-280.91827079065121, -195.55748765461769, 13.9363892133, 13.9363892133, -1.47833441067),
                                          decimal=1)

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

