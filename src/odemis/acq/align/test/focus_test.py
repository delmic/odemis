# -*- coding: utf-8 -*-
'''
Created on 11 Apr 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

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
import unittest
from odemis.dataio import hdf5
from odemis.acq.align import settings
from scipy import ndimage

logging.getLogger().setLevel(logging.DEBUG)

class TestSettingsAdjustment(unittest.TestCase):
    """
    Test settings functions
    """
    def test_measure_focus(self):
        """
        Test MeasureFocus
        """
        grid_data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X
        input = grid_data[0]
        
        prev_res = settings.MeasureFocus(input)
        for i in range(1, 10, 1):
            input = ndimage.gaussian_filter(input, sigma=i)
            res = settings.MeasureFocus(input)
            self.assertGreater(prev_res, res)
            prev_res = res


if __name__ == '__main__':
    unittest.main()
