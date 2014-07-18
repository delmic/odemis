# -*- coding: utf-8 -*-
'''
Created on 18 Jul 2014

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
from numpy import random
import numpy
from odemis.acq.align import delphi_calibration
from odemis.dataio import hdf5
import unittest


logging.getLogger().setLevel(logging.DEBUG)

# @unittest.skip("skip")
class TestHoleDetection(unittest.TestCase):
    """
    Test HoleDetection
    """
    # @unittest.skip("skip")
    def test_find_hole_center(self):
        """
        Test FindHoleCenter
        """
        data = hdf5.read_data("sem_hole.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        hole_coordinates = delphi_calibration.FindHoleCenter(data[0])
        expected_coordinates = (385.0, 267.5)
        numpy.testing.assert_almost_equal(hole_coordinates, expected_coordinates)
    # @unittest.skip("skip")
    def test_no_hole(self):
        """
        Test FindHoleCenter raises exception
        """
        data = hdf5.read_data("blank_image.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        self.assertRaises(IOError, delphi_calibration.FindHoleCenter, data[0])

if __name__ == '__main__':
    unittest.main()
