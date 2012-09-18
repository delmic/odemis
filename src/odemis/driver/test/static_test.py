# -*- coding: utf-8 -*-
'''
Created on 18 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.driver import static
import unittest

# Simple test cases, for the very simple static components

class TestLightFilter(unittest.TestCase):
    def test_simple(self):
        band = [(480e-9, 651e-9), (700e-9, 800e-9)]
        comp = static.LightFilter("test", "filter", band)
        self.assertEqual(set(band), comp.band.value)
        comp.terminate()

    def test_one_band(self):
        band = (480e-9, 651e-9)
        comp = static.LightFilter("test", "filter", band)
        self.assertEqual(set([band]), comp.band.value)
        comp.terminate()

class TestOpticalLens(unittest.TestCase):
    def test_simple(self):
        mag = 10.
        comp = static.OpticalLens("test", "lens", mag)
        self.assertEqual(mag, comp.magnification.value)
        comp.terminate()
        
if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()