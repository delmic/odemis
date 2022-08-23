# -*- coding: utf-8 -*-
'''
Created on 21 Oct 2015

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import numpy
from odemis.dataio import hdf5
from odemis.util import peak
import os
import unittest
import matplotlib.pyplot as plt


logging.getLogger().setLevel(logging.DEBUG)
PATH = os.path.dirname(__file__)


class TestPeak(unittest.TestCase):
    """
    Test peak fitting in both energy and space domain for all the available fitting types
    """
    def setUp(self):
        data = hdf5.read_data(os.path.join(PATH, "spectrum_fitting.h5"))[1]
        data = numpy.squeeze(data)
        self.data = data
        self.wl_in_meters = numpy.linspace(470e-9, 1030e-9, 167)
        max_bw = data.shape[0] // 2
        min_bw = (max_bw - data.shape[0]) + 1
        self.wl_in_pixels = list(range(min_bw, max_bw + 1))
        self._peak_fitter = peak.PeakFitter()

    def test_peakfitting_energy(self):
        data = self.data
        wl = self.wl_in_meters
        spec = data[:, 20, 20]

        # Try gaussian
        f = self._peak_fitter.Fit(spec, wl, type='gaussian_energy')
        params, offset, curve_type = f.result()
        self.assertTrue(1 <= len(params) < 20)
        # Parameters should be positive
        for pos, width, amplitude in params:
            self.assertGreater(pos, 0)
            self.assertGreater(width, 0)
            self.assertGreater(amplitude, 0)

        # Create curve
        curve = peak.Curve(wl, params, offset)
        self.assertEqual(len(curve), len(wl))
        # TODO: find peaks on curve, and see we about the same peaks
        wlhr = numpy.linspace(470e-9, 1030e-9, 512)
        curve = peak.Curve(wlhr, params, offset, type='gaussian_energy')
        self.assertEqual(len(curve), len(wlhr))
        #plt.figure()
        #plt.plot(wl, spec, 'r', wl, curve, 'r', linewidth=2)

        # Try lorentzian
        f = self._peak_fitter.Fit(spec, wl, type='lorentzian_energy')
        params, offset, curve_type = f.result()
        self.assertTrue(1 <= len(params) < 20)
        # Parameters should be positive
        for pos, width, amplitude in params:
            self.assertGreater(pos, 0)
            self.assertGreater(width, 0)
            self.assertGreater(amplitude, 0)

        curve = peak.Curve(wl, params, offset, type='lorentzian_energy')
        self.assertEqual(len(curve), len(wl))
        wlhr = numpy.linspace(470e-9, 1030e-9, 512)
        curve = peak.Curve(wlhr, params, offset, type='gaussian_energy')
        self.assertEqual(len(curve), len(wlhr))
        #plt.figure()
        #plt.plot(wl, spec, 'r', wl, curve, 'r', linewidth=2)
        #plt.show(block=False)

        # Assert wrong fitting type
        self.assertRaises(KeyError, peak.Curve, wl, params, offset, type='wrongType')

    def test_peakfitting_space(self):
        data = self.data
        wl = self.wl_in_pixels
        spec = data[:, 20, 20]

        # Try gaussian
        f = self._peak_fitter.Fit(spec, wl, type='gaussian_space')
        params, offset, curve_type = f.result()
        self.assertTrue(1 <= len(params) < 20)
        # pos parameter can be negative
        for pos, width, amplitude in params:
            self.assertGreater(pos, -1000)
            self.assertGreater(width, 0)
            self.assertGreater(amplitude, 0)

        # Create curve
        curve = peak.Curve(wl, params, offset)
        self.assertEqual(len(curve), len(wl))
        # TODO: find peaks on curve, and see we about the same peaks
        wlhr = numpy.linspace(-125, 125, 180)
        curve = peak.Curve(wlhr, params, offset, type='gaussian_space')
        self.assertEqual(len(curve), len(wlhr))

        # Try lorentzian
        f = self._peak_fitter.Fit(spec, wl, type='lorentzian_space')
        params, offset, curve_type = f.result()
        self.assertTrue(1 <= len(params) < 20)
        # pos parameter can be negative
        for pos, width, amplitude in params:
            self.assertGreater(pos, -1000)
            self.assertGreater(width, 0)
            self.assertGreater(amplitude, 0)

        curve = peak.Curve(wl, params, offset, type='lorentzian_space')
        self.assertEqual(len(curve), len(wl))
        wlhr = numpy.linspace(-125, 125, 180)
        curve = peak.Curve(wlhr, params, offset, type='lorentzian_space')
        self.assertEqual(len(curve), len(wlhr))

        # Assert wrong fitting type
        self.assertRaises(KeyError, peak.Curve, wl, params, offset, type='wrongType')


if __name__ == "__main__":
    unittest.main()

