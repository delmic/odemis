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

from odemis.dataio import tiff
from odemis.util import inertia
import unittest
from odemis.util import img

class TestPolarConversion(unittest.TestCase):
    """
    Test AngleResolved2Polar
    """
    def setUp(self):
        data = tiff.read_data("inertia_precomputed.tif")
        background = tiff.read_data("background.tif")
        self.data = data
        self.background = background

    def test_precomputed(self):
        data = self.data
        background = self.background
        drange = img.guessDRange(data[0])
        mi, valid = inertia.CalculateMomentOfInertia(data[0], background[0], drange)
        self.assertAlmostEqual(mi, 112.005654085)
        self.assertEqual(valid, True)


if __name__ == "__main__":
#     import sys;sys.argv = ['', 'TestPolarConversionOutput.test_2000x2000']
    unittest.main()
#    suite = unittest.TestLoader().loadTestsFromTestCase(TestPolarConversionOutput)
#    unittest.TextTestRunner(verbosity=2).run(suite)

