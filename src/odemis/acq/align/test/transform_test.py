# -*- coding: utf-8 -*-
'''
Created on 29 Nov 2013

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
from odemis.dataio import hdf5
from odemis.acq.align import transform

logging.getLogger().setLevel(logging.DEBUG)

class TestTransformationParams(unittest.TestCase):
    """
    Test TransformationParams functions
    """
    def test_calculate_transform(self):
        """
        Test CalculateTransform
        """
        optical_coordinates = [(1.01, 1.01), (1.01, 2.01), (2.01, 1.01), (2.01, 2.01)]
        electron_coordinates = [(1, 1), (1, 2), (2, 1), (2, 2)]

        transform.CalculateTransform(optical_coordinates, electron_coordinates)
        # translation, scaling, rotation = transform.CalculateTransform(optical_coordinates, electron_coordinates)
        # print translation, scaling, rotation
