# -*- coding: utf-8 -*-
'''
Created on 10 Jan 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import numpy
from odemis.dataio import hdf5
from odemis.util import peak
import unittest
import matplotlib.pyplot as plt


class TestPeak(unittest.TestCase):
    """
    Test peak fitting
    """
    def setUp(self):
        data = hdf5.read_data("spectrum_fitting.h5")[1]
        data = numpy.squeeze(data)
        self.data = data
        self.wl = numpy.linspace(470, 1030, 167)

    def test_precomputed(self):
        data = self.data
        wl = self.wl
        spec = data[:, 20, 20]

        # Try gaussian
        f = peak.Fit(spec, wl)
        params = f.result()
        curve = peak.Curve(wl, params)
        plt.figure()
        plt.plot(wl, spec, 'r', wl, curve, 'r', linewidth=2)

        # Try lorentzian
        f = peak.Fit(spec, wl, type='lorentzian')
        params = f.result()
        curve = peak.Curve(wl, params, type='lorentzian')
        plt.figure()
        plt.plot(wl, spec, 'r', wl, curve, 'r', linewidth=2)
        plt.show(block=False)

        # Assert wrong fitting type
        self.assertRaises(KeyError, peak.Curve, wl, params, type='wrongType')


if __name__ == "__main__":
    unittest.main()

