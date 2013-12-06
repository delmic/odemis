# -*- coding: utf-8 -*-
'''
Created on 19 Sep 2012

@author: piel

Copyright © 2012-2013 Éric Piel & Kimon Tsitsikas, Delmic

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

from odemis import model
from odemis.dataio import hdf5
from odemis.gui.util import img
from odemis.gui.util.img import DataArray2wxImage, wxImage2NDImage, \
    FindOptimalBC, DataArray2RGB


logging.getLogger().setLevel(logging.DEBUG)

def GetRGB(im, x, y):
    """
    return the r,g,b tuple corresponding to a pixel
    """
    r = im.GetRed(x, y)
    g = im.GetGreen(x, y)
    b = im.GetBlue(x, y)
    
    return (r, g, b)


class TestPolarConversion(unittest.TestCase):
    """
    Test AngleResolved2Polar
    """
    def setUp(self):
        data = hdf5.read_data("ar-example-input.h5")
        mag = 0.4917
        spxs = (13e-6, 13e-6)
        binning = (4, 4)
        # data[0].metadata[model.MD_BASELINE] = 820
        data[0].metadata[model.MD_BINNING] = binning
        data[0].metadata[model.MD_SENSOR_PIXEL_SIZE] = spxs
        data[0].metadata[model.MD_LENS_MAG] = mag
        data[0].metadata[model.MD_AR_POLE] = (141, 255 - 139.449038462)
        mag = data[0].metadata[model.MD_LENS_MAG]
        pxs = (spxs[0] * binning[0] / mag,
               spxs[1] * binning[1] / mag)
        data[0].metadata[model.MD_PIXEL_SIZE] = pxs
        self.data = data 

        white_data_512 = hdf5.read_data("white_data_512.h5")
        white_mag_512 = 0.4917
        white_spxs_512 = (13e-6, 13e-6)
        white_binning_512 = (2, 2)
        white_data_512[0].metadata[model.MD_BINNING] = white_binning_512
        white_data_512[0].metadata[model.MD_SENSOR_PIXEL_SIZE] = white_spxs_512
        white_data_512[0].metadata[model.MD_LENS_MAG] = white_mag_512
        white_data_512[0].metadata[model.MD_AR_POLE] = (283, 259)
        white_mag_512 = white_data_512[0].metadata[model.MD_LENS_MAG]
        white_pxs_512 = (white_spxs_512[0] * white_binning_512[0] / white_mag_512,
               white_spxs_512[1] * white_binning_512[1] / white_mag_512)
        white_data_512[0].metadata[model.MD_PIXEL_SIZE] = white_pxs_512
        self.white_data_512 = white_data_512

        white_data_1024 = hdf5.read_data("white_data_1024.h5")
        white_mag_1024 = 0.4917
        white_spxs_1024 = (13e-6, 13e-6)
        white_binning_1024 = (2, 2)
        white_data_1024[0].metadata[model.MD_BINNING] = white_binning_1024
        white_data_1024[0].metadata[model.MD_SENSOR_PIXEL_SIZE] = white_spxs_1024
        white_data_1024[0].metadata[model.MD_LENS_MAG] = white_mag_1024
        white_data_1024[0].metadata[model.MD_AR_POLE] = (283, 259)
        white_mag_1024 = white_data_1024[0].metadata[model.MD_LENS_MAG]
        white_pxs_1024 = (white_spxs_1024[0] * white_binning_1024[0] / white_mag_1024,
               white_spxs_1024[1] * white_binning_1024[1] / white_mag_1024)
        white_data_1024[0].metadata[model.MD_PIXEL_SIZE] = white_pxs_1024
        self.white_data_1024 = white_data_1024
    
    def test_precomputed(self):
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = img.AngleResolved2Polar(data[0], 201)

        desired_output = hdf5.read_data("desired201x201image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_uint16_input(self):
        """
        Tests for input of DataArray with uint16 ndarray.
        """
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = img.AngleResolved2Polar(data[0], 201)

        desired_output = hdf5.read_data("desired201x201image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_int8_input(self):
        """
        Tests for input of DataArray with int8 ndarray.
        """
        data = self.data
        # scipy.misc.bytescale(data)
        data[0] = data[0].astype(numpy.int64)
        data[0] = numpy.right_shift(data[0], 8)
        data[0] = data[0].astype(numpy.int8)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = img.AngleResolved2Polar(data[0], 201)

        desired_output = img.AngleResolved2Polar(data[0].astype(float), 201)

        numpy.testing.assert_allclose(result, desired_output, rtol=1e-04)

    def test_float_input(self):
        """
        Tests for input of DataArray with float ndarray.
        """
        data = self.data
        data[0] = data[0].astype(numpy.float)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        result = img.AngleResolved2Polar(data[0], 201)

        desired_output = hdf5.read_data("desired201x201image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_100x100(self):
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        result = img.AngleResolved2Polar(data[0], 101)

        desired_output = hdf5.read_data("desired100x100image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_1000x1000(self):
        data = self.data
        data[0] = data[0].astype(numpy.int64)
        data[0] = numpy.right_shift(data[0], 8)
        data[0] = data[0].astype(numpy.int8)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        result = img.AngleResolved2Polar(data[0], 1001)

        desired_output = hdf5.read_data("desired1000x1000image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1)

    def test_2000x2000(self):
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        result = img.AngleResolved2Polar(data[0], 2001)

        desired_output = hdf5.read_data("desired2000x2000image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_512x512(self):
        """
        Test for 512x512 white image input
        """
        white_data_512 = self.white_data_512
        C, T, Z, Y, X = white_data_512[0].shape
        white_data_512[0].shape = Y, X
        result = img.AngleResolved2Polar(white_data_512[0], 201)

        desired_output = hdf5.read_data("desired_white_512.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_1024x1024(self):
        """
        Test for 1024x1024 white image input
        """
        white_data_1024 = self.white_data_1024
        C, T, Z, Y, X = white_data_1024[0].shape
        white_data_1024[0].shape = Y, X
        result = img.AngleResolved2Polar(white_data_1024[0], 201)

        desired_output = hdf5.read_data("desired_white_1024.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_background_substraction_precomputed(self):
        """
        Test clean up before polar conversion
        """
        data = self.data
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = img.AR_BackgroundSubtract(data[0])
        result = img.AngleResolved2Polar(clean_data, 201)

        desired_output = hdf5.read_data("substracted_background_image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_background_substraction_uint16_input(self):
        """
        Tests for input of DataArray with uint16 ndarray.
        """
        data = self.data
        data[0] = data[0].astype(numpy.uint16)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = img.AR_BackgroundSubtract(data[0])
        result = img.AngleResolved2Polar(clean_data, 201)

        desired_output = hdf5.read_data("substracted_background_image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

    def test_background_substraction_int8_input(self):
        """
        Tests for input of DataArray with int8 ndarray.
        """
        data = self.data
        # scipy.misc.bytescale(data)
        data[0] = data[0].astype(numpy.int64)
        data[0] = numpy.right_shift(data[0], 8)
        data[0] = data[0].astype(numpy.int8)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = img.AR_BackgroundSubtract(data[0])
        result = img.AngleResolved2Polar(clean_data, 201)

        desired_output = img.AngleResolved2Polar(data[0].astype(float), 201)

        numpy.testing.assert_allclose(result, desired_output, rtol=1)

    def test_background_substraction_float_input(self):
        """
        Tests for input of DataArray with float ndarray.
        """
        data = self.data
        data[0] = data[0].astype(numpy.float)
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        clean_data = img.AR_BackgroundSubtract(data[0])
        result = img.AngleResolved2Polar(clean_data, 201)

        desired_output = hdf5.read_data("substracted_background_image.h5")
        C, T, Z, Y, X = desired_output[0].shape
        desired_output[0].shape = Y, X

        numpy.testing.assert_allclose(result, desired_output[0], rtol=1e-04)

class TestFindOptimalBC(unittest.TestCase):
    def test_simple(self):
        size = (1024, 512)
        depth = 2**8
        img8 = numpy.zeros(size[-1:-3:-1], dtype="uint8")
        img8[0,0] = depth-1
        
        b, c = FindOptimalBC(img8, depth)
        self.assertEqual((0,0), (b,c))
        
        depth = 2**16
        img16 = numpy.zeros(size[-1:-3:-1], dtype="uint16")
        img16[0,0] = depth-1
        
        b, c = FindOptimalBC(img16, depth)
        self.assertEqual((0,0), (b,c))

        # almost grey
        imggr = numpy.zeros(size[-1:-3:-1], dtype="uint16") + (depth/2-1)
        imggr[0,0] = depth/2
        b, c = FindOptimalBC(imggr, depth)
        #self.assertEqual(0, b)
        self.assertLessEqual(b, 0.01)
        self.assertGreater(c, 0)
        self.assertLessEqual(c, 1)
        
        # very dark grey
        imggr = numpy.zeros(size[-1:-3:-1], dtype="uint16") 
        imggr[0,0] = 1
        b, c = FindOptimalBC(imggr, depth)
        self.assertGreater(b, 0)
        self.assertLessEqual(b, 1)
        self.assertGreater(c, 0)
        self.assertLessEqual(c, 1)
        
        # All Black: => brightness should be up, contrast too
        imgbl = numpy.zeros(size[-1:-3:-1], dtype="uint16")
        b, c = FindOptimalBC(imgbl, depth)
        self.assertGreater(b, 0)
        self.assertLessEqual(b, 1)
        self.assertGreater(c, 0)
        self.assertLessEqual(c, 1)

        
    def test_auto_vs_manual(self):
        """
        Checks that conversion with auto BC is the same as optimal BC + manual
        conversion.
        """
        size = (1024, 512)
        depth = 2**12
        img12 = numpy.zeros(size[-1:-3:-1], dtype="uint16") + 420
        img12[0,0] = depth-1-240
        
        # automatic
        out_auto = DataArray2wxImage(img12)
        img_auto = wxImage2NDImage(out_auto)
        
        # manual
        b, c = FindOptimalBC(img12, depth)
        out_manu = DataArray2wxImage(img12, depth, b, c)
        img_manu = wxImage2NDImage(out_manu)
        
        self.assertTrue(numpy.all(img_auto==img_manu))
        
        # second try
        img12 = numpy.zeros(size[-1:-3:-1], dtype="uint16") + 4000
        img12[0,0] = depth-1-40
        
        # automatic
        out_auto = DataArray2wxImage(img12)
        img_auto = wxImage2NDImage(out_auto)
        
        # manual
        b, c = FindOptimalBC(img12, depth)
        out_manu = DataArray2wxImage(img12, depth, b, c)
        img_manu = wxImage2NDImage(out_manu)
        
        self.assertTrue(numpy.all(img_auto==img_manu))


class TestFindOptimalRange(unittest.TestCase):
    """
    Test findOptimalRange
    """

    def test_no_outliers(self):
        # just one value (middle)
        hist = numpy.zeros(256, dtype="int32")
        hist[128] = 4564
        irange = img.findOptimalRange(hist, (0, 255))
        self.assertEqual(irange, (128, 128))

        # first
        hist = numpy.zeros(256, dtype="int32")
        hist[0] = 4564
        irange = img.findOptimalRange(hist, (0, 255))
        self.assertEqual(irange, (0, 0))

        # last
        hist = numpy.zeros(256, dtype="int32")
        hist[255] = 4564
        irange = img.findOptimalRange(hist, (0, 255))
        self.assertEqual(irange, (255, 255))

        # first + last
        hist = numpy.zeros(256, dtype="int32")
        hist[0] = 456
        hist[255] = 4564
        irange = img.findOptimalRange(hist, (0, 255))
        self.assertEqual(irange, (0, 255))

        # average
        hist = numpy.zeros(256, dtype="int32") + 125
        irange = img.findOptimalRange(hist, (0, 255))
        self.assertEqual(irange, (0, 255))

    def test_with_outliers(self):
        # almost nothing, but more than 0
        hist = numpy.zeros(256, dtype="int32")
        hist[128] = 4564
        irange = img.findOptimalRange(hist, (0, 255), 1e-6)
        self.assertEqual(irange, (128, 128))

        # 1%
        hist = numpy.zeros(256, dtype="int32")
        hist[2] = 1
        hist[5] = 99
        hist[135] = 99
        hist[199] = 1

        irange = img.findOptimalRange(hist, (0, 255), 0.01)
        self.assertEqual(irange, (5, 135))

        # 5% -> same
        irange = img.findOptimalRange(hist, (0, 255), 0.05)
        self.assertEqual(irange, (5, 135))

        # 0.1 % -> include everything
        irange = img.findOptimalRange(hist, (0, 255), 0.001)
        self.assertEqual(irange, (2, 199))

    def test_speed(self):
        for depth in [16, 256, 4096]:
            # Check the shortcut when outliers = 0 is indeed faster
            hist = numpy.zeros(depth, dtype="int32")
            p1, p2 = depth // 2 - 4, depth // 2 + 3
            hist[p1] = 99
            hist[p2] = 99

            tstart = time.time()
            for i in range(10000):
                irange = img.findOptimalRange(hist, (0, depth - 1))
            dur_sc = time.time() - tstart
            self.assertEqual(irange, (p1, p2))

            # outliers is some small, it's same behaviour as with 0
            tstart = time.time()
            for i in range(10000):
                irange = img.findOptimalRange(hist, (0, depth - 1), 1e-6)
            dur_full = time.time() - tstart
            self.assertEqual(irange, (p1, p2))

            logging.info("shortcut took %g s, while full took %g s", dur_sc, dur_full)
            self.assertLessEqual(dur_sc, dur_full)
        

    def test_auto_vs_manual(self):
        """
        Checks that conversion with auto BC is the same as optimal BC + manual
        conversion.
        """
        size = (1024, 512)
        depth = 2 ** 12
        img12 = numpy.zeros(size, dtype="uint16") + depth // 2
        img12[0, 0] = depth - 1 - 240

        # automatic
        img_auto = DataArray2RGB(img12)

        # manual
        hist, edges = img.histogram(img12, (0, depth - 1))
        self.assertEqual(edges, (0, depth - 1))
        irange = img.findOptimalRange(hist, edges)
        img_manu = DataArray2RGB(img12, irange)

        numpy.testing.assert_equal(img_auto, img_manu)

        # second try
        img12 = numpy.zeros(size, dtype="uint16") + 4000
        img12[0, 0] = depth - 1 - 40
        img12[12, 12] = 50

        # automatic
        img_auto = DataArray2RGB(img12)

        # manual
        hist, edges = img.histogram(img12, (0, depth - 1))
        irange = img.findOptimalRange(hist, edges)
        img_manu = DataArray2RGB(img12, irange)

        numpy.testing.assert_equal(img_auto, img_manu)


class TestHistogram(unittest.TestCase):
    # 8 and 16 bit short-cuts test
    def test_uint8(self):
        # 8 bits
        depth = 256
        size = (1024, 512)
        grey_img = numpy.zeros(size, dtype="uint8") + depth // 2
        grey_img[0, 0] = 10
        grey_img[0, 1] = depth - 10
        hist, edges = img.histogram(grey_img, (0, depth - 1))
        self.assertEqual(len(hist), depth)
        self.assertEqual(edges, (0, depth - 1))
        self.assertEqual(hist[grey_img[0, 0]], 1)
        self.assertEqual(hist[grey_img[0, 1]], 1)
        self.assertEqual(hist[depth // 2], grey_img.size - 2)
        hist_auto, edges = img.histogram(grey_img)
        numpy.testing.assert_array_equal(hist, hist_auto)
        self.assertEqual(edges, (0, depth - 1))

    def test_uint16(self):
        # 16 bits
        depth = 4096 # limited depth
        size = (1024, 965)
        grey_img = numpy.zeros(size, dtype="uint16") + 1500
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1
        hist, edges = img.histogram(grey_img, (0, depth - 1))
        self.assertEqual(len(hist), depth)
        self.assertEqual(edges, (0, depth - 1))
        self.assertEqual(hist[0], 1)
        self.assertEqual(hist[-1], 1)
        u = numpy.unique(hist[1:-1])
        self.assertEqual(sorted(u.tolist()), [0, grey_img.size - 2])

        hist_auto, edges = img.histogram(grey_img)
        self.assertGreaterEqual(edges[1], depth - 1)
        numpy.testing.assert_array_equal(hist, hist_auto[:depth])

    def test_float(self):
        size = (102, 965)
        grey_img = numpy.zeros(size, dtype="float") + 15.05
        grey_img[0, 0] = -15.6
        grey_img[0, 1] = 500.6
        hist, edges = img.histogram(grey_img)
        self.assertGreaterEqual(len(hist), 256)
        self.assertEqual(numpy.sum(hist), numpy.prod(size))
        self.assertEqual(hist[0], 1)
        self.assertEqual(hist[-1], 1)
        u = numpy.unique(hist[1:-1])
        self.assertEqual(sorted(u.tolist()), [0, grey_img.size - 2])
        hist_forced, edges = img.histogram(grey_img, edges)
        numpy.testing.assert_array_equal(hist, hist_forced)

    def test_compact(self):
        """
        test the compactHistogram()
        """
        depth = 4096 # limited depth
        size = (1024, 965)
        grey_img = numpy.zeros(size, dtype="uint16") + 1500
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1
        hist, edges = img.histogram(grey_img, (0, depth - 1))
        # make it compact
        chist = img.compactHistogram(hist, 256)
        self.assertEqual(len(chist), 256)
        self.assertEqual(numpy.sum(chist), numpy.prod(size))

        # make it really compact
        vchist = img.compactHistogram(hist, 1)
        self.assertEqual(vchist[0], numpy.prod(size))

        # keep it the same length
        nchist = img.compactHistogram(hist, depth)
        numpy.testing.assert_array_equal(hist, nchist)


class TestDataArray2RGB(unittest.TestCase):
    @staticmethod
    def CountValues(array):
        return len(numpy.unique(array))

    def test_simple(self):
        # test with everything auto
        size = (1024, 512)
        grey_img = numpy.zeros(size, dtype="uint16") + 1500

        # one colour
        out = DataArray2RGB(grey_img)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 1)

        # add black
        grey_img[0, 0] = 0
        out = DataArray2RGB(grey_img)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 2)

        # add white
        grey_img[0, 1] = 4095
        out = DataArray2RGB(grey_img)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        pixel0 = out[0, 0]
        pixel1 = out[0, 1]
        pixelg = out[0, 2]
        numpy.testing.assert_array_less(pixel0, pixel1)
        numpy.testing.assert_array_less(pixel0, pixelg)
        numpy.testing.assert_array_less(pixelg, pixel1)

    def test_direct_mapping(self):
        """test with irange fitting the whole depth"""
        # first 8 bit => no change (and test the short-cut)
        size = (1024, 1024)
        depth = 256
        grey_img = numpy.zeros(size, dtype="uint8") + depth // 2
        grey_img[0, 0] = 10
        grey_img[0, 1] = depth - 10

        # should keep the grey
        out = DataArray2RGB(grey_img, irange=(0, depth))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [128, 128, 128])

        # 16 bits
        depth = 4096
        grey_img = numpy.zeros(size, dtype="uint16") + depth // 2
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100

        # should keep the grey
        out = DataArray2RGB(grey_img, irange=(0, depth - 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [128, 128, 128])

    def test_irange(self):
        """test with specific corner values of irange"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size, dtype="uint16") + depth // 2
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100

        # slightly smaller range than everything => still 3 colours
        out = DataArray2RGB(grey_img, irange=(50, depth - 51))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        pixel0 = out[0, 0]
        pixel1 = out[0, 1]
        pixelg = out[0, 2]
        numpy.testing.assert_array_less(pixel0, pixel1)
        numpy.testing.assert_array_less(pixel0, pixelg)
        numpy.testing.assert_array_less(pixelg, pixel1)

        # irange at the lowest value => all white (but the blacks)
        out = DataArray2RGB(grey_img, irange=(0, 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 1)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [255, 255, 255])

        # irange at the highest value => all blacks (but the whites)
        out = DataArray2RGB(grey_img, irange=(depth - 2 , depth - 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 1)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [0, 0, 0])

        # irange at the middle value => black/white/grey (max)
        out = DataArray2RGB(grey_img, irange=(depth // 2 - 1 , depth // 2 + 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        hist, edges = img.histogram(out[:, :, 0]) # just use one RGB channel
        self.assertGreater(hist[0], 0)
        self.assertEqual(hist[1], 0)
        self.assertGreater(hist[-1], 0)
        self.assertEqual(hist[-2], 0)

    def test_tint(self):
        """test with tint"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size, dtype="uint16") + depth // 2
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1

        # white should become same as the tint
        tint = (0, 73, 255)
        out = DataArray2RGB(grey_img, tint=tint)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out[:, :, 0]), 1) # R
        self.assertEqual(self.CountValues(out[:, :, 1]), 3) # G
        self.assertEqual(self.CountValues(out[:, :, 2]), 3) # B

        pixel0 = out[0, 0]
        pixel1 = out[0, 1]
        pixelg = out[0, 2]
        numpy.testing.assert_array_equal(pixel1, list(tint))
        self.assertTrue(numpy.all(pixel0 <= pixel1))
        self.assertTrue(numpy.all(pixel0 <= pixelg))
        self.assertTrue(numpy.all(pixelg <= pixel1))


class TestDataArray2wxImage(unittest.TestCase):
    def test_simple(self):
        # test with everything auto
        size = (1024, 512)
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + 1500 
        
        # one colour
        out = DataArray2wxImage(grey_img)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 1)
        
        # add black
        grey_img[0, 0] = 0
        out = DataArray2wxImage(grey_img)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 2)
        
        # add white
        grey_img[0, 1] = 4095
        out = DataArray2wxImage(grey_img)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel0 = GetRGB(out, 0, 0)
        pixel1 = GetRGB(out, 1, 0)
        pixelg = GetRGB(out, 2, 0)
        self.assertGreater(pixel1, pixel0)
        self.assertGreater(pixelg, pixel0)
        self.assertGreater(pixel1, pixelg)
        
    
    def test_bc_0(self):
        """test with fixed brightness and contrast to 0"""
        # first 8 bit => no change
        size = (1024, 1024)
        depth = 256
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint8") + depth/2
        grey_img[0, 0] = 10
        grey_img[0, 1] = depth - 10
        
        # should keep the grey 
        out = DataArray2wxImage(grey_img, depth, 0, 0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (128, 128, 128))
        
        # 16 bits
        depth = 4096
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + depth/2 
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100
        
        # should keep the grey
        out = DataArray2wxImage(grey_img, depth, 0, 0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (128, 128, 128)) 
        
    def test_bc_forced(self):
        """test with brightness and contrast to specific corner values"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + depth/2 
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100
        
        # little change in brightness and contrast => still 3 colours
        out = DataArray2wxImage(grey_img, depth, brightness=0.1, contrast=0.1)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel0 = GetRGB(out, 0, 0)
        pixel1 = GetRGB(out, 1, 0)
        pixelg = GetRGB(out, 2, 0)
        self.assertGreater(pixel1, pixel0)
        self.assertGreater(pixelg, pixel0)
        self.assertGreater(pixel1, pixelg)
                
        # brightness == 1 => all white
        out = DataArray2wxImage(grey_img, depth, brightness=1, contrast=0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 1)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (255, 255, 255)) 
        
        # brightness == -1 => all black
        out = DataArray2wxImage(grey_img, depth, brightness=-1, contrast=0)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 1)
        pixel = GetRGB(out, 2, 2)
        self.assertTrue(pixel == (0, 0, 0))

        # contrast == -1 => all grey
        out = DataArray2wxImage(grey_img, depth, brightness=0, contrast=-1)
        self.assertEqual(out.GetSize(), size)
        # can be 2 colours : 127 and 128, depending on rounding 
        hist = wx.ImageHistogram()
        numcol = out.ComputeHistogram(hist)
        self.assertLessEqual(numcol, 2)
        if numcol == 1:
            self.assertTrue(hist.GetCountRGB(127, 127, 127) > 0 or
                            hist.GetCountRGB(128, 128, 128) > 0)
        elif numcol == 2:
            self.assertTrue(hist.GetCountRGB(127, 127, 127) > 0 and
                            hist.GetCountRGB(128, 128, 128) > 0)
        # contrast == 1 => black/white/grey (max)
        out = DataArray2wxImage(grey_img, depth, brightness=0, contrast=1)
        self.assertEqual(out.GetSize(), size)
        self.assertLessEqual(out.CountColours(), 3)


    def test_tint(self):
        """test with tint"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size[-1:-3:-1], dtype="uint16") + depth/2 
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1
        
        # white should become same as the tint
        tint = (0, 127, 255)
        out = DataArray2wxImage(grey_img, depth, brightness=0, contrast=0, tint=tint)
        self.assertEqual(out.GetSize(), size)
        self.assertEqual(out.CountColours(), 3)
        pixel0 = GetRGB(out, 0, 0)
        pixel1 = GetRGB(out, 1, 0)
        pixelg = GetRGB(out, 2, 0)
        self.assertEqual(pixel1, tint)
        self.assertGreater(pixel1, pixel0)
        self.assertGreater(pixelg, pixel0)
        self.assertGreater(pixel1, pixelg)


class TestWxImage2NDImage(unittest.TestCase):
    
    def test_simple(self):
        size = (32, 64)
        wximage = wx.EmptyImage(*size) # black RGB
        ndimage = wxImage2NDImage(wximage)
        self.assertEqual(ndimage.shape[0:2], size[-1:-3:-1])
        self.assertEqual(ndimage.shape[2], 3) # RGB
        self.assertTrue((ndimage[0,0] == [0, 0, 0]).all())
    
    # TODO alpha channel

if __name__ == "__main__":
#     import sys;sys.argv = ['', 'TestPolarConversionOutput.test_2000x2000']
    unittest.main()
#    suite = unittest.TestLoader().loadTestsFromTestCase(TestPolarConversionOutput)
#    unittest.TextTestRunner(verbosity=2).run(suite)

