# -*- coding: utf-8 -*-
'''
Created on 8 Mar 2013

@author: piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division
from numpy.polynomial import polynomial
from odemis import model
from odemis.driver import spectrometer, spectrapro, pvcam
from unittest.case import skip, skipIf
import logging
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

if os.name == "nt":
    PORT_SPG = "COM1"
else:
    PORT_SPG = "/dev/ttySP"

# Simulated device
CLASS_SPG = spectrapro.FakeSpectraPro
KWARGS_SPG = {"name": "spg", "role": "spectrograph", "port": PORT_SPG}

# Real device: PI PIXIS
CLASS_CCD = pvcam.PVCam
KWARGS_CCD = {"name": "pixis", "role": "ccd", "device": 0}

CLASS = spectrometer.CompositedSpectrometer

class TestCompositedSpectrometer(unittest.TestCase):
    """
    Test the CompositedSpectrometer class
    """

    @classmethod
    def setUpClass(cls):
        cls.detector = CLASS_CCD(**KWARGS_CCD)
        cls.spectrograph = CLASS_SPG(**KWARGS_SPG)
        cls.spectrometer = CLASS(name="test", role="spectrometer",
                                 children={"detector": cls.detector,
                                           "spectrograph": cls.spectrograph})
        #save position
        cls._orig_pos = cls.spectrograph.position.value
    
    @classmethod
    def tearDownClass(cls):
        # restore position
        f = cls.spectrograph.moveAbs(cls._orig_pos)
        f.result() # wait for the move to finish
        
        cls.spectrometer.terminate()
        cls.detector.terminate()
        cls.spectrograph.terminate()
    
    def setUp(self):
        # put a meaningful wavelength
        f = self.spectrograph.moveAbs({"wavelength": 500e-9})
        
        # save basic VA
        self._orig_binning = self.spectrometer.binning.value
        self._orig_res = self.spectrometer.resolution.value
        
        f.result() # wait for the position to be set
    
    def tearDown(self):
        # put back VAs
        self.spectrometer.binning.value = self._orig_binning
        self.spectrometer.resolution.value = self._orig_res
    
    def test_simple(self):
        """
        Just ensures that the device has all the VA it should
        """
        self.assertTrue(isinstance(self.spectrometer.binning.value, tuple))
        self.assertEqual(self.spectrometer.resolution.value[1], 1)
        self.assertGreaterEqual(self.spectrometer.shape[0], self.spectrometer.shape[1])
        self.assertGreater(self.spectrometer.exposureTime, 0)
        
    def test_acquisition(self):
        exp = 0.1 #s
        self.spectrometer.exposureTime.value = exp
        
        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        
        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        
        
    def test_vbinning(self):
        """
        Test vertical binning (use less than the whole detector)
        """
        if (self.spectrometer.binning.range[1][1] == 1):
            self.skipTest("Spectrometer doesn't support vertical binning")
        
        # normally vertical binning is by default the maximum, so it's not going
        # to change much
        binning = [self.spectrometer.binning.value[0],    # as-is
                   self.spectrometer.binning.range[1][1]] # max
        self.spectrometer.binning.value = binning
        self.spectrometer.resolution.value = self.spectrometer.resolution.range[1]
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))
        
        # reduce the binning (v resolution stays 1)
        binning[1] //= 2
        self.spectrometer.binning.value = binning
        self.assertEqual(self.spectrometer.resolution.value[1], 1)
        
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))
        
    def test_hbinning(self):
        """
        Test horizontal binning (large horizontal pixels)
        """
        if (self.spectrometer.binning.range[1][0] == 1):
            self.skipTest("Spectrometer doesn't support horizontal binning")

        # start with minimum binning
        binning = [self.spectrometer.binning.range[0][0], # min
                   self.spectrometer.binning.range[1][1]] # max
        self.spectrometer.binning.value = binning
        self.spectrometer.resolution.value = self.spectrometer.resolution.range[1]
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))

        # increase the binning (h resolution decreases)
        prev_hbinning = binning[0]
        prev_hres = self.spectrometer.resolution.value[0]
        binning[0] *= min(2, self.spectrometer.binning.range[1][0])
        self.spectrometer.binning.value = binning
        exp_hresolution = int(round(prev_hres / (binning[0] / prev_hbinning)))
        self.assertEqual(self.spectrometer.resolution.value[0], exp_hresolution)

        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))
        
    
    def test_resolution(self):
        """
        Check the (unusual) behaviour of the resolution 
        """
        if (self.spectrometer.resolution.range[0] == self.spectrometer.resolution.range[1]):
            self.skipTest("Spectrometer doesn't support changing the resolution, boring")
        
        # horizontally, resolution behaves pretty normally
        res = self.spectrometer.resolution.range[1] # max
        self.spectrometer.resolution.value = res
        res = self.spectrometer.resolution.value # the actual value
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        
        res = self.spectrometer.resolution.range[0] # min
        self.spectrometer.resolution.value = res
        res = self.spectrometer.resolution.value # the actual value
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        
        # vertically, it's fixed to one
        new_res = (self.spectrometer.resolution.value[0], 2)
        try:
            self.spectrometer.resolution.value = new_res
        except Exception:
            pass
        else:
            self.fail("vertical resolution should not be allowed above 1, got %r" % new_res)
    
    def test_spec_calib(self):
        """
        Check that the calibration of the wavelength make _some_ sense
        It's not expected that the calibration is correct, but it should be at
        least some how logical.
        """
        # the wavelength bandwidth across the CCD should be pretty much constant
        # independent of the resolution (not exactly, as the wavelength is for
        # the center of the pixel, so the bigger are the pixels, the closer are
        # the centers) 
        
        # horizontal maximum res/min binning
        binning = (self.spectrometer.binning.range[0][0], # min
                   self.spectrometer.binning.range[1][1]) # max
        self.spectrometer.binning.value = binning
        res = self.spectrometer.resolution.range[1] # max
        self.spectrometer.resolution.value = res
        res = self.spectrometer.resolution.value # actual value
        
        # read calibration
        data = self.spectrometer.data.get()
        pn = data.metadata[model.MD_WL_POLYNOMIAL]
        if len(pn) <= 1:
            logging.warning("Wavelength polynomial is of very low quality: length = %d", len(pn))
        # pixel 0 to pixel N +1 => whole CCD
        wl_bw_max_res = polynomial.polyval(0, pn) - polynomial.polyval(res[0], pn)
        cwl_max_res = (polynomial.polyval(0, pn) + polynomial.polyval(res[0]-1, pn)) / 2
        logging.info("Wl bw = %f nm, center = %f nm", 
                     wl_bw_max_res * 1e9, cwl_max_res * 1e9)
        
        # do they make any sense?
        # centre wavelength should about (~30%) the same as the wavelength position
        exp_cwl = self.spectrograph.position.value["wavelength"] 
        self.assertTrue(exp_cwl / 1.3 < cwl_max_res and cwl_max_res < exp_cwl * 1.3)
        # never heard of bandwidth higher than a few 1000 nm
        self.assertGreater(wl_bw_max_res, 0)
        self.assertLess(wl_bw_max_res, 10000e-9)
        
        # 8 times smaller resolution
        binning = (min(binning[0] * 8, self.spectrometer.binning.range[1][0]),
                   binning[1])
        self.spectrometer.binning.value = binning
        res = self.spectrometer.resolution.value # new resolution
        
        # read calibration
        data = self.spectrometer.data.get()
        pn = data.metadata[model.MD_WL_POLYNOMIAL]
        # pixel 0 to pixel N +1 => whole CCD
        wl_bw_low_res = polynomial.polyval(0, pn) - polynomial.polyval(res[0], pn)
        cwl_low_res = (polynomial.polyval(0, pn) + polynomial.polyval(res[0]-1, pn)) / 2
        
        self.assertAlmostEqual(wl_bw_low_res, wl_bw_max_res)
        self.assertAlmostEqual(cwl_low_res, cwl_max_res)
        
if __name__ == '__main__':
    unittest.main()