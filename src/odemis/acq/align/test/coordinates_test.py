# -*- coding: utf-8 -*-
'''
Created on 28 Nov 2013

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
import unittest

from odemis import model
from odemis.dataio import hdf5
from odemis.acq.align import coordinates

logging.getLogger().setLevel(logging.DEBUG)

class TestSpotCoordinates(unittest.TestCase):
    """
    Test SpotCoordinates functions
    """
    def test_find_center(self):
        """
        Test FindCenterCoordinates
        """
        data = hdf5.read_data("single_part.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        subimages = []
        subimages.append(model.DataArray(data[0]))

        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
