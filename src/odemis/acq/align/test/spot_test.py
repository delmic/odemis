# -*- coding: utf-8 -*-
'''
Created on 15 Apr 2014

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
import numpy
import unittest
from odemis.dataio import hdf5
from odemis.acq.align import spot

logging.getLogger().setLevel(logging.DEBUG)

class TestSpotAlignment(unittest.TestCase):
    """
    Test spot alignment functions
    """
    def test_find_spot(self):
        """
        Test FindSpot
        """
        data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        input = data[0]
        avg = numpy.mean(input)
        input[0:251, :].fill(avg)
        input[290:, : ].fill(avg)
        input[:, 0:329].fill(avg)
        input[:, 374:].fill(avg)
        
        res = spot.FindSpot(input)
        self.assertAlmostEqual(res, (351.68593111619668, 272.68443611130562), 3)


if __name__ == '__main__':
    unittest.main()
