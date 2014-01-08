# -*- coding: utf-8 -*-
'''
Created on 3 Jan 2014

@author: kimon

Copyright © 2013-2014 Éric Piel & Kimon Tsitsikas, Delmic

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
import time
import unittest
import wx
import math
import random

from odemis import model
from odemis.dataio import hdf5
from odemis.acq.drift import calculation
from numpy import fft

class TestDriftCalculation(unittest.TestCase):
    """
    Test CalculateDrift
    """
    #@unittest.skip("skip")
    def setUp(self):
		# Input
    	self.data = hdf5.read_data("example_input.h5")
        C, T, Z, Y, X = self.data[0].shape
        self.data[0].shape = Y, X

		# Input drifted by known value
        self.data_drifted = hdf5.read_data("example_drifted.h5")
        C, T, Z, Y, X = self.data_drifted[0].shape
        self.data_drifted[0].shape = Y, X

		# Input drifted by random value
        z = 1j  # imaginary unit
        numpy.random.seed(0)
        self.deltar = random.uniform(-100, 100)
        self.deltac = random.uniform(-100, 100)
        nr, nc = self.data[0].shape
        array_nr = numpy.arange(-numpy.fix(nr / 2), numpy.ceil(nr / 2))
        array_nc = numpy.arange(-numpy.fix(nc / 2), numpy.ceil(nc / 2))
        Nr = fft.ifftshift(array_nr)
        Nc = fft.ifftshift(array_nc)
        [Nc, Nr] = numpy.meshgrid(Nc, Nr)
        self.data_random_drifted = fft.ifft2(fft.fft2(self.data[0]) * numpy.power(math.e,
						z * 2 * math.pi * (self.deltar * Nr / nr + self.deltac * Nc / nc)))

    # @unittest.skip("skip")
    def test_identical_inputs(self):
        """
        Tests for input of identical images.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data[0], 1)
        numpy.testing.assert_almost_equal(drift, (0,0), 1)
    
    # @unittest.skip("skip")
    def test_known_drift(self):
        """
        Tests for image drifted by known drift value.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_drifted[0], 1)
        numpy.testing.assert_almost_equal(drift, (5,-3), 1)
        
    # @unittest.skip("skip")
    def test_random_drift(self):
        """
        Tests for image drifted by random drift value.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 10)
        numpy.testing.assert_almost_equal(drift, (self.deltar, self.deltac), 1)

    # @unittest.skip("skip")
    def test_different_precisions(self):
		"""
		Tests for image drifted by random drift value using different precisions.
		"""
		drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 1)
		numpy.testing.assert_almost_equal(drift, (self.deltar, self.deltac), 0)

		drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 10)
		numpy.testing.assert_almost_equal(drift, (self.deltar, self.deltac), 1)

		drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 100)
		numpy.testing.assert_almost_equal(drift, (self.deltar, self.deltac), 2)
		
		drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 1000)
		numpy.testing.assert_almost_equal(drift, (self.deltar, self.deltac), 3)
