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
from __future__ import division

from odemis.driver import static, simcam
from odemis.util import timeout
import unittest


# Simple test cases, for the very simple static components
class TestLightFilter(unittest.TestCase):
    @timeout(1)
    def test_simple(self):
        band = ((480e-9, 651e-9), (700e-9, 800e-9))
        comp = static.LightFilter("test", "filter", band)
        self.assertEqual({0: band}, comp.axes["band"].choices)

        cur_pos = comp.position.value["band"]
        self.assertEqual(band, comp.axes["band"].choices[cur_pos])

        f = comp.moveAbs({"band": 0})
        f.result()
        cur_pos = comp.position.value["band"]
        self.assertEqual(band, comp.axes["band"].choices[cur_pos])

        comp.terminate()

    def test_one_band(self):
        band = (480e-9, 651e-9)
        comp = static.LightFilter("test", "filter", band)
        self.assertEqual({0: (band,)}, comp.axes["band"].choices)
        comp.terminate()


class TestOpticalLens(unittest.TestCase):
    def test_simple(self):
        mag = 10.
        comp = static.OpticalLens("test", "lens", mag, pole_pos=(512.3, 400))
        self.assertEqual(mag, comp.magnification.value)
        comp.magnification.value = 1.5  # should be allowed
        comp.terminate()

    def test_mag_choices(self):
        mag_choices = [1, 1.5, 2.5]
        comp = static.OpticalLens("test", "lens", 1, mag_choices=mag_choices)
        self.assertEqual(1, comp.magnification.value)
        self.assertEqual(comp.magnification.choices, set(mag_choices))
        comp.magnification.value = 1.5  # should be allowed
        with self.assertRaises(IndexError):
            comp.magnification.value = 2.0
        comp.terminate()

    def test_configurations(self):
        configurations = {"Mirror up": {pole_pos: [458, 519], focus_dist: 0.5e-3},
                          "Mirror down": {pole_pos: [634, 652], focus_dist: -0.5e-3}}
        comp = static.OpticalLens("test", "lens", 1, configurations_choices=configurations)

class TestSpectrograph(unittest.TestCase):
    @timeout(3)
    def test_fake(self):
        """
        Just makes sure we more or less follow the behaviour of a spectrograph
        """
        wlp = [500e-9, 1/1e6]
        ccd = simcam.Camera("testcam", "ccd", image="andorcam2-fake-clara.tiff")
        sp = static.Spectrograph("test", "spectrograph", wlp=wlp, dependencies={"ccd": ccd})
        ptw = sp.getPixelToWavelength(ccd.shape[0], ccd.pixelSize.value[0])
        self.assertGreater(wlp[0], ptw[0])
        self.assertLess(wlp[0], ptw[-1])

        f = sp.moveAbs({"wavelength":300e-9})
        f.result()
        self.assertAlmostEqual(sp.position.value["wavelength"], 300e-9)

        wlp[0] = 300e-9
        ptw = sp.getPixelToWavelength(ccd.shape[0], ccd.pixelSize.value[0])
        self.assertGreater(wlp[0], ptw[0])
        self.assertLess(wlp[0], ptw[-1])

        sp.stop()

        self.assertTrue(sp.selfTest(), "self test failed.")
        sp.terminate()

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
