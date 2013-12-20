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
import logging
import numpy
import unittest
from odemis import model
from odemis.acq import fine_overlay

# logging.getLogger().setLevel(logging.DEBUG)

class TestFineOverlay(unittest.TestCase):
    """
    Test FineOverlay functions
    """
    def test_do_fine_overlay(self):
        """
        Test DoFineOverlay
        """
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "se-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = fine_overlay.DoFineOverlay((9, 9), 1e-06, 1e-07, escan, ccd, detector)
        numpy.testing.assert_almost_equal((calc_translation_x, calc_translation_y, calc_scaling, calc_rotation), (-280.91827079065121, -195.55748765461769, 13.9363892133, -1.47833441067), 1)

    def test_do_fine_overlay_failure(self):
        """
        Test DoFineOverlay failure due to low maximum allowed difference
        """
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "se-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = fine_overlay.DoFineOverlay((9, 9), 1e-06, 1e-08, escan, ccd, detector)
        numpy.testing.assert_equal(((calc_translation_x, calc_translation_y), calc_scaling, calc_rotation), ((None, None), None, None))

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestFineOverlay)
    unittest.TextTestRunner(verbosity=2).run(suite)

