# -*- coding: utf-8 -*-
'''
Created on 18 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from odemis.driver import static
from odemis.util import timeout
import unittest

# Simple test cases, for the very simple static components

class TestLightFilter(unittest.TestCase):
    @timeout(1.2) # this is to test timeout
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

class TestSpectrograph(unittest.TestCase):
    @timeout(3)
    def test_fake(self):
        """
        Just makes sure we more or less follow the behaviour of a spectrograph
        """
        wlp = [500e-9, 1/1e6]
        sp = static.Spectrograph("test", "spectrograph", wlp=wlp)
        self.assertEqual(wlp, sp.getPolyToWavelength())
        
        f = sp.moveAbs({"wavelength":300e-9})
        f.result()
        self.assertAlmostEqual(sp.position.value["wavelength"], 300e-9)
        
        wlp[0] = 300e-9
        self.assertEqual(wlp, sp.getPolyToWavelength())

        sp.stop()
        
        self.assertTrue(sp.selfTest(), "self test failed.")
        sp.terminate()
        
if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()