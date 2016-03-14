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
from __future__ import division

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
    Test peak fitting
    """
    def setUp(self):
        data = hdf5.read_data(os.path.join(PATH, "spectrum_fitting.h5"))[1]
        data = numpy.squeeze(data)
        self.data = data
        self.wl = numpy.linspace(470, 1030, 167)
        self._peak_fitter = peak.PeakFitter()

    def test_precomputed(self):
        data = self.data
        wl = self.wl
        spec = data[:, 20, 20]

        # Try gaussian
        f = self._peak_fitter.Fit(spec, wl)
        params, offset = f.result()
        self.assertTrue(1 <= len(params) < 20)
        # Parameters should be positive
        for pos, width, amplitude in params:
            self.assertGreater(pos, 0)
            self.assertGreater(width, 0)
            self.assertGreater(amplitude, 0)
        # offset doesn't officially needs to be positive
        # self.assertTrue(offset >= 0)

        # Create curve
        curve = peak.Curve(wl, params, offset)
        self.assertEqual(len(curve), len(wl))
        # TODO: find peaks on curve, and see we about the same peaks
        wlhr = numpy.linspace(470, 1030, 512)
        curve = peak.Curve(wlhr, params, offset)
        self.assertEqual(len(curve), len(wlhr))
        #plt.figure()
        #plt.plot(wl, spec, 'r', wl, curve, 'r', linewidth=2)

        # Try lorentzian
        f = self._peak_fitter.Fit(spec, wl, type='lorentzian')
        params, offset = f.result()
        self.assertTrue(1 <= len(params) < 20)
        # Parameters should be positive
        for pos, width, amplitude in params:
            self.assertGreater(pos, 0)
            self.assertGreater(width, 0)
            self.assertGreater(amplitude, 0)

        curve = peak.Curve(wl, params, offset, type='lorentzian')
        #plt.figure()
        #plt.plot(wl, spec, 'r', wl, curve, 'r', linewidth=2)
        #plt.show(block=False)

        # Assert wrong fitting type
        self.assertRaises(KeyError, peak.Curve, wl, params, offset, type='wrongType')


if __name__ == "__main__":
    unittest.main()

