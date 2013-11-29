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

from numpy import genfromtxt
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
        optical_coordinates = [(4.8241, 3.2631), (5.7418, 4.5738), (5.2170, 1.0348), (8.8879, 6.2774)]
        electron_coordinates = [(0, 1), (0, 2), (1, 0), (1, 4)]

        # optical_coordinates = genfromtxt('doc1.csv', delimiter=',')
        # electron_coordinates = genfromtxt('doc2.csv', delimiter=',')

        transform.CalculateTransform(optical_coordinates, electron_coordinates)
        # translation, scaling, rotation = transform.CalculateTransform(optical_coordinates, electron_coordinates)
        # print translation, scaling, rotation
