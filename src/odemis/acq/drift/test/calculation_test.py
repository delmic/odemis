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
from __future__ import division
import logging
import numpy
import unittest
import math

from odemis.dataio import hdf5
from odemis.acq.drift import calculation
from numpy import fft
from numpy import random

# @unittest.skip("skip")
class TestDriftCalculation(unittest.TestCase):
    """
    Test CalculateDrift
    """
    # @unittest.skip("skip")
    def setUp(self):
        # Input
        self.data = hdf5.read_data("example_input.h5")
        C, T, Z, Y, X = self.data[0].shape
        self.data[0].shape = Y, X
        self.small_data = self.data[0][350:400, 325:375]

        # Input drifted by known value
        self.data_drifted = hdf5.read_data("example_drifted.h5")
        C, T, Z, Y, X = self.data_drifted[0].shape
        self.data_drifted[0].shape = Y, X

        # Input drifted by random value
        z = 1j  # imaginary unit
        self.deltar = numpy.random.uniform(-100, 100)
        self.deltac = numpy.random.uniform(-100, 100)
        nr, nc = self.data[0].shape
        array_nr = numpy.arange(-numpy.fix(nr / 2), numpy.ceil(nr / 2))
        array_nc = numpy.arange(-numpy.fix(nc / 2), numpy.ceil(nc / 2))
        Nr = fft.ifftshift(array_nr)
        Nc = fft.ifftshift(array_nc)
        [Nc, Nr] = numpy.meshgrid(Nc, Nr)
        self.data_random_drifted = fft.ifft2(fft.fft2(self.data[0]) * numpy.power(math.e,
        				z * 2 * math.pi * (self.deltar * Nr / nr + self.deltac * Nc / nc)))

        # Noisy inputs
        noise = random.normal(0, 3000, self.data[0].size)
        noise_array = noise.reshape(self.data[0].shape[0], self.data[0].shape[1])

        self.data_noisy = self.data[0] + noise_array
        self.data_drifted_noisy = self.data_drifted[0] + noise_array
        self.data_random_drifted_noisy = self.data_random_drifted + noise_array

        # Small input drifted by random value
        self.small_deltar = numpy.random.uniform(-10, 10)
        self.small_deltac = numpy.random.uniform(-10, 10)
        nr, nc = self.small_data.shape
        array_nr = numpy.arange(-numpy.fix(nr / 2), numpy.ceil(nr / 2))
        array_nc = numpy.arange(-numpy.fix(nc / 2), numpy.ceil(nc / 2))
        Nr = fft.ifftshift(array_nr)
        Nc = fft.ifftshift(array_nc)
        [Nc, Nr] = numpy.meshgrid(Nc, Nr)
        self.small_data_random_drifted = fft.ifft2(fft.fft2(self.small_data) * numpy.power(math.e,
        				z * 2 * math.pi * (self.small_deltar * Nr / nr + self.small_deltac * Nc / nc)))

        # Small noisy inputs
        small_noise = random.normal(0, 3000, self.small_data.size)
        small_noise_array = small_noise.reshape(self.small_data.shape[0], self.small_data.shape[1])

        self.small_data_noisy = self.small_data + small_noise_array
        self.small_data_random_drifted_noisy = self.small_data_random_drifted + small_noise_array


    # @unittest.skip("skip")
    def test_identical_inputs(self):
        """
        Tests for input of identical images.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data[0], 1)
        numpy.testing.assert_almost_equal(drift, (0, 0), 1)

    # @unittest.skip("skip")
    def test_known_drift(self):
        """
        Tests for image drifted by known drift value.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_drifted[0], 1)
        numpy.testing.assert_almost_equal(drift, (-3, 5), 1)

    # @unittest.skip("skip")
    def test_random_drift(self):
        """
        Tests for image drifted by random drift value.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 10)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 1)

    # @unittest.skip("skip")
    def test_different_precisions(self):
        """
        Tests for image drifted by random drift value using different precisions.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 1)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 0)

        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 10)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 1)

        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 100)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 2)

        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted, 1000)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 3)

    def test_identical_inputs_noisy(self):
        """
        Tests for input of identical images after noise is added.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_noisy, 1)
        numpy.testing.assert_almost_equal(drift, (0, 0), 1)

    # @unittest.skip("skip")
    def test_known_drift_noisy(self):
        """
        Tests for image drifted by known drift value after noise is added.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_drifted_noisy, 1)
        numpy.testing.assert_almost_equal(drift, (-3, 5), 1)

    # @unittest.skip("skip")
    def test_random_drift_noisy(self):
        """
        Tests for image drifted by random drift value after noise is added.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted_noisy, 10)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 1)

    # @unittest.skip("skip")
    def test_different_precisions_noisy(self):
        """
        Tests for image drifted by random drift value using different precisions after noise is added.
        """
        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted_noisy, 1)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 0)

        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted_noisy, 10)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 1)

        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted_noisy, 100)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 2)

        drift = calculation.CalculateDrift(self.data[0], self.data_random_drifted_noisy, 1000)
        numpy.testing.assert_almost_equal(drift, (self.deltac, self.deltar), 2)

    # @unittest.skip("skip")
    def test_small_identical_inputs(self):
        """
        Tests for input of identical images.
        """
        drift = calculation.CalculateDrift(self.small_data, self.small_data, 1)
        numpy.testing.assert_almost_equal(drift, (0, 0), 0)

    # @unittest.skip("skip")
    def test_small_random_drift(self):
        """
        Tests for image drifted by random drift value.
        """
        drift = calculation.CalculateDrift(self.small_data, self.small_data_random_drifted, 10)
        numpy.testing.assert_almost_equal(drift, (self.small_deltac, self.small_deltar), 0)

    # @unittest.skip("skip")
    def test_small_identical_inputs_noisy(self):
        """
        Tests for input of identical images after noise is added.
        """
        drift = calculation.CalculateDrift(self.small_data, self.small_data_noisy, 1)
        numpy.testing.assert_almost_equal(drift, (0, 0), 0)

    # @unittest.skip("skip")
    def test_small_random_drift_noisy(self):
        """
        Tests for image drifted by random drift value after noise is added.
        """
        drift = calculation.CalculateDrift(self.small_data, self.small_data_random_drifted_noisy, 10)
        numpy.testing.assert_almost_equal(drift, (self.small_deltac, self.small_deltar), 0)

if __name__ == '__main__':
    unittest.main()
