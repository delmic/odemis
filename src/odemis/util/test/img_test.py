# -*- coding: utf-8 -*-
"""
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
"""
import logging
import math
import os
import statistics
import time
import unittest

from matplotlib import cm, colors
import numpy

from odemis import model
from odemis.dataio import tiff
from odemis.model import TINT_FIT_TO_RGB, TINT_RGB_AS_IS
from odemis.util import img, get_best_dtype_for_acc, testing
from odemis.util.img import Bin, mean_within_circle

logging.getLogger().setLevel(logging.DEBUG)


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
        depth = 2 ** 12    # test for higher depth (worse case)
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
        img_auto = img.DataArray2RGB(img12)

        # manual
        hist, edges = img.histogram(img12, (0, depth - 1))
        self.assertEqual(edges, (0, depth - 1))
        irange = img.findOptimalRange(hist, edges)
        img_manu = img.DataArray2RGB(img12, irange)

        numpy.testing.assert_equal(img_auto, img_manu)

        # second try
        img12 = numpy.zeros(size, dtype="uint16") + 4000
        img12[0, 0] = depth - 1 - 40
        img12[12, 12] = 50

        # automatic
        img_auto = img.DataArray2RGB(img12)

        # manual
        hist, edges = img.histogram(img12, (0, depth - 1))
        irange = img.findOptimalRange(hist, edges)
        img_manu = img.DataArray2RGB(img12, irange)

        numpy.testing.assert_equal(img_auto, img_manu)

    def test_uint32_small(self):
        """
        Test uint32, but with values very close from each other => the histogram
        will look like just one column not null. But we still want the image
        to display between 0->255 in RGB.
        """
        size = (512, 100)
        grey_img = numpy.zeros(size, dtype="uint32") + 3
        grey_img[0, :] = 0
        grey_img[:, 1] = 40
        hist, edges = img.histogram(grey_img)  # , (0, depth - 1))
        irange = img.findOptimalRange(hist, edges, 0)

        rgb = img.DataArray2RGB(grey_img, irange)

        self.assertEqual(rgb[0, 0].tolist(), [0, 0, 0])
        self.assertEqual(rgb[5, 1].tolist(), [255, 255, 255])
        self.assertTrue(0 < rgb[50, 50, 0] < 255)

    def test_empty_hist(self):
        # Empty histogram
        edges = (0, 0)
        irange = img.findOptimalRange(numpy.array([]), edges, 1 / 256)
        self.assertEqual(irange, edges)

        # histogram from an array with a single point
        edges = (10, 10)
        irange = img.findOptimalRange(numpy.array([1]), edges, 1 / 256)
        self.assertEqual(irange, edges)


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
        depth = 4096  # limited depth
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

    def test_uint32(self):
        # 32 bits
        depth = 2 ** 32
        size = (512, 100)
        grey_img = numpy.zeros(size, dtype="uint32") + (depth // 3)
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1
        hist, edges = img.histogram(grey_img, (0, depth - 1))
        self.assertTrue(256 <= len(hist) <= depth)
        self.assertEqual(edges, (0, depth - 1))
        self.assertEqual(hist[0], 1)
        self.assertEqual(hist[-1], 1)
        u = numpy.unique(hist[1:-1])
        self.assertEqual(sorted(u.tolist()), [0, grey_img.size - 2])

        hist_auto, edges = img.histogram(grey_img)
        self.assertGreaterEqual(edges[1], depth - 1)
        numpy.testing.assert_array_equal(hist, hist_auto[:depth])

    def test_uint32_small(self):
        """
        Test uint32, but with values very close from each other => the histogram
        will look like just one column not null.
        """
        depth = 2 ** 32
        size = (512, 100)
        grey_img = numpy.zeros(size, dtype="uint32") + 3
        grey_img[0, 0] = 0
        grey_img[0, 1] = 40
        hist, edges = img.histogram(grey_img, (0, depth - 1))
        self.assertTrue(256 <= len(hist) <= depth)
        self.assertEqual(edges, (0, depth - 1))
        self.assertEqual(hist[0], grey_img.size)
        self.assertEqual(hist[-1], 0)

        # Only between 0 and next power above max data (40 -> 63)
        hist, edges = img.histogram(grey_img, (0, 63))
        self.assertTrue(len(hist) <= depth)
        self.assertEqual(edges, (0, 63))
        self.assertEqual(hist[0], 1)
        self.assertEqual(hist[40], 1)

        hist_auto, edges = img.histogram(grey_img)
        self.assertEqual(edges[1], grey_img.max())
        numpy.testing.assert_array_equal(hist[:len(hist_auto)], hist_auto[:len(hist)])

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
        depth = 4096  # limited depth
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
        out = img.DataArray2RGB(grey_img)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 1)

        # add black
        grey_img[0, 0] = 0
        out = img.DataArray2RGB(grey_img)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 2)

        # add white
        grey_img[0, 1] = 4095
        out = img.DataArray2RGB(grey_img)
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
        grey_img = numpy.zeros(size, dtype="uint8") + depth // 2  # 128
        grey_img[0, 0] = 10
        grey_img[0, 1] = depth - 10

        # should keep the grey
        out = img.DataArray2RGB(grey_img, irange=(0, depth - 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        pixel = out[2, 2]
        numpy.testing.assert_equal(out[:, :, 0], out[:, :, 1])
        numpy.testing.assert_equal(out[:, :, 0], out[:, :, 2])
        numpy.testing.assert_equal(pixel, [128, 128, 128])

        # 16 bits
        depth = 4096
        grey_img = numpy.zeros(size, dtype="uint16") + depth // 2
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100
        grey_img[1, 0] = 0
        grey_img[1, 1] = depth - 1

        # should keep the grey
        out = img.DataArray2RGB(grey_img, irange=(0, depth - 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 5)
        pixel = out[2, 2]
        numpy.testing.assert_equal(out[:, :, 0], out[:, :, 1])
        numpy.testing.assert_equal(out[:, :, 0], out[:, :, 2])
        # In theory, depth//2 should be 128, but due to support for floats (ranges),
        # the function cannot ensure this, so accept slightly less (127).
        assert (numpy.array_equal(pixel, [127, 127, 127]) or
                numpy.array_equal(pixel, [128, 128, 128]))
        numpy.testing.assert_equal(out[1, 0], [0, 0, 0])
        numpy.testing.assert_equal(out[1, 1], [255, 255, 255])

        # 32 bits
        depth = 2 ** 32
        grey_img = numpy.zeros(size, dtype="uint32") + depth // 2
        grey_img[0, 0] = depth // 50
        grey_img[0, 1] = depth - depth // 50
        grey_img[1, 0] = 0
        grey_img[1, 1] = depth - 1

        # should keep the grey
        out = img.DataArray2RGB(grey_img, irange=(0, depth - 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 5)
        pixel = out[2, 2]
        numpy.testing.assert_equal(out[:, :, 0], out[:, :, 1])
        numpy.testing.assert_equal(out[:, :, 0], out[:, :, 2])
        assert (numpy.array_equal(pixel, [127, 127, 127]) or
                numpy.array_equal(pixel, [128, 128, 128]))
        numpy.testing.assert_equal(out[1, 0], [0, 0, 0])
        numpy.testing.assert_equal(out[1, 1], [255, 255, 255])

    def test_irange(self):
        """test with specific corner values of irange"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size, dtype="uint16") + depth // 2
        grey_img[0, 0] = 100
        grey_img[0, 1] = depth - 100

        # slightly smaller range than everything => still 3 colours
        out = img.DataArray2RGB(grey_img, irange=(50, depth - 51))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        pixel0 = out[0, 0]
        pixel1 = out[0, 1]
        pixelg = out[0, 2]
        numpy.testing.assert_array_less(pixel0, pixel1)
        numpy.testing.assert_array_less(pixel0, pixelg)
        numpy.testing.assert_array_less(pixelg, pixel1)

        # irange at the lowest value => all white (but the blacks)
        out = img.DataArray2RGB(grey_img, irange=(0, 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 1)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [255, 255, 255])

        # irange at the highest value => all blacks (but the whites)
        out = img.DataArray2RGB(grey_img, irange=(depth - 2, depth - 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 1)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [0, 0, 0])

        # irange at the middle value => black/white/grey (max)
        out = img.DataArray2RGB(grey_img, irange=(depth // 2 - 1, depth // 2 + 1))
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out), 3)
        hist, edges = img.histogram(out[:, :, 0])  # just use one RGB channel
        self.assertGreater(hist[0], 0)
        self.assertEqual(hist[1], 0)
        self.assertGreater(hist[-1], 0)
        self.assertEqual(hist[-2], 0)

    def test_fast(self):
        """Test the fast conversion"""
        data = numpy.ones((251, 200), dtype="uint16")
        data[:, :] = numpy.arange(200)
        data[2, :] = 56
        data[200, 2] = 3

        data_nc = data.swapaxes(0, 1)  # non-contiguous cannot be treated by fast conversion
        outliners = 1 / 256

        # convert to RGB
        hist, edges = img.histogram(data)
        irange = img.findOptimalRange(hist, edges, outliners)
        tstart = time.time()
        for i in range(10):
            rgb = img.DataArray2RGB(data, irange)
        fast_dur = time.time() - tstart

        hist_nc, edges_nc = img.histogram(data_nc)
        irange_nc = img.findOptimalRange(hist_nc, edges_nc, outliners)
        tstart = time.time()
        for i in range(10):
            rgb_nc = img.DataArray2RGB(data_nc, irange_nc)
        std_dur = time.time() - tstart
        rgb_nc_back = rgb_nc.swapaxes(0, 1)

        print("Time fast conversion = %g s, standard = %g s" % (fast_dur, std_dur))
        self.assertLess(fast_dur, std_dur)
        # ±1, to handle the value shifts by the standard converter to handle floats
        numpy.testing.assert_almost_equal(rgb, rgb_nc_back, decimal=0)

    def test_tint(self):
        """test with tint (on the fast path)"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size, dtype="uint16") + depth // 2
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1

        # white should become same as the tint
        tint = (0, 73, 255)
        out = img.DataArray2RGB(grey_img, tint=tint)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out[:, :, 0]), 1)  # R
        self.assertEqual(self.CountValues(out[:, :, 1]), 3)  # G
        self.assertEqual(self.CountValues(out[:, :, 2]), 3)  # B

        pixel0 = out[0, 0]
        pixel1 = out[0, 1]
        pixelg = out[0, 2]
        numpy.testing.assert_array_equal(pixel1, list(tint))
        self.assertTrue(numpy.all(pixel0 <= pixel1))
        self.assertTrue(numpy.all(pixel0 <= pixelg))
        self.assertTrue(numpy.all(pixelg <= pixel1))

    def test_tint_int16(self):
        """test with tint, with the slow path"""
        size = (1024, 1024)
        depth = 4096
        grey_img = numpy.zeros(size, dtype="int16") + depth // 2
        grey_img[0, 0] = 0
        grey_img[0, 1] = depth - 1

        # white should become same as the tint
        tint = (0, 73, 255)
        out = img.DataArray2RGB(grey_img, tint=tint)
        self.assertEqual(out.shape, size + (3,))
        self.assertEqual(self.CountValues(out[:, :, 0]), 1)  # R
        self.assertEqual(self.CountValues(out[:, :, 1]), 3)  # G
        self.assertEqual(self.CountValues(out[:, :, 2]), 3)  # B

        pixel0 = out[0, 0]
        pixel1 = out[0, 1]
        pixelg = out[0, 2]
        numpy.testing.assert_array_equal(pixel1, list(tint))
        self.assertTrue(numpy.all(pixel0 <= pixel1))
        self.assertTrue(numpy.all(pixel0 <= pixelg))
        self.assertTrue(numpy.all(pixelg <= pixel1))

    def test_uint8(self):
        # uint8 is special because it's so close from the output that bytescale
        # normally does nothing
        irange = (25, 135)
        shape = (1024, 836)
        tint = (0, 73, 255)
        data = numpy.random.randint(irange[0], irange[1] + 1, shape).astype(numpy.uint8)
        # to be really sure there is at least one of the min and max values
        data[0, 0] = irange[0]
        data[0, 1] = irange[1]

        out = img.DataArray2RGB(data, irange, tint=tint)

        pixel1 = out[0, 1]
        numpy.testing.assert_array_equal(pixel1, list(tint))

        self.assertTrue(numpy.all(out[..., 0] == 0))

        self.assertEqual(out[..., 2].min(), 0)
        self.assertEqual(out[..., 2].max(), 255)

        # Same data, but now mapped between 0->255 => no scaling to do (just duplicate)
        irange = (0, 255)
        out = img.DataArray2RGB(data, irange, tint=tint)
        self.assertTrue(numpy.all(out[..., 0] == 0))
        numpy.testing.assert_array_equal(data, out[:, :, 2])

    def test_float(self):
        irange = (0.3, 468.4)
        shape = (102, 965)
        tint = (0, 73, 255)
        grey_img = numpy.zeros(shape, dtype="float") + 15.05
        grey_img[0, 0] = -15.6
        grey_img[0, 1] = 500.6

        out = img.DataArray2RGB(grey_img, irange, tint=tint)
        self.assertTrue(numpy.all(out[..., 0] == 0))
        self.assertEqual(out[..., 2].min(), 0)
        self.assertEqual(out[..., 2].max(), 255)

        # irange at the lowest value => all white (but the blacks)
        out = img.DataArray2RGB(grey_img, irange=(-100, -50))
        self.assertEqual(out.shape, shape + (3,))
        self.assertEqual(self.CountValues(out), 1)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [255, 255, 255])

        # irange at the highest value => all blacks (but the whites)
        out = img.DataArray2RGB(grey_img, irange=(5000, 5000.1))
        self.assertEqual(out.shape, shape + (3,))
        self.assertEqual(self.CountValues(out), 1)
        pixel = out[2, 2]
        numpy.testing.assert_equal(pixel, [0, 0, 0])

        # irange at the middle => B&W only
        out = img.DataArray2RGB(grey_img, irange=(10, 10.1))
        self.assertEqual(out.shape, shape + (3,))
        self.assertEqual(self.CountValues(out), 2)
        hist, edges = img.histogram(out[:, :, 0])  # just use one RGB channel
        self.assertGreater(hist[0], 0)
        self.assertEqual(hist[1], 0)
        self.assertGreater(hist[-1], 0)
        self.assertEqual(hist[-2], 0)


class TestBin(unittest.TestCase):

    def test_simple(self):
        d = model.DataArray(numpy.ones((20, 6), dtype=numpy.uint8))
        db = Bin(d, (3, 5))
        self.assertEqual(d.shape, (20, 6))  # d should stay untouched
        self.assertEqual(db.shape, (4, 2))  # 20 / 5, 6 / 3
        # we are summing 1s 3x5 times, so it should be 15 in all pixels
        self.assertTrue(numpy.all(db == 3 * 5))

        # Metadata is created/updated
        self.assertEqual(db.metadata[model.MD_BINNING], (3, 5))

    def test_no_binning(self):
        d = model.DataArray(numpy.ones((20, 6), dtype=numpy.uint16))
        db = Bin(d, (1, 1))
        self.assertEqual(d.shape, (20, 6))  # d should stay untouched
        self.assertEqual(db.shape, (20, 6))  # db should be identical
        numpy.testing.assert_array_equal(d, db)

        # Metadata is created/updated
        self.assertEqual(db.metadata[model.MD_BINNING], (1, 1))


class TestMergeMetadata(unittest.TestCase):

    def test_simple(self):
        # Try correction is null (ie, identity)
        md = {model.MD_ROTATION: 0,  # °
              model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m
              model.MD_POS: (-5e-3, 2e-3),  # m
              model.MD_ROTATION_COR: 0,  # °
              model.MD_PIXEL_SIZE_COR: (1, 1),  # ratio
              model.MD_POS_COR: (0, 0),  # m
              }
        orig_md = dict(md)
        img.mergeMetadata(md)
        for k in [model.MD_ROTATION, model.MD_PIXEL_SIZE, model.MD_POS]:
            self.assertEqual(orig_md[k], md[k])
        for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
            self.assertNotIn(k, md)

        # Try the same but using a separate correction metadata
        id_cor = {model.MD_ROTATION_COR: 0,  # °
                  model.MD_PIXEL_SIZE_COR: (1, 1),  # ratio
                  model.MD_POS_COR: (0, 0),  # m
                  }

        orig_md = dict(md)
        img.mergeMetadata(md, id_cor)
        for k in [model.MD_ROTATION, model.MD_PIXEL_SIZE, model.MD_POS]:
            self.assertEqual(orig_md[k], md[k])
        for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
            self.assertNotIn(k, md)

        # Check that empty correction metadata is same as identity
        orig_md = dict(md)
        img.mergeMetadata(md, {})
        for k in [model.MD_ROTATION, model.MD_PIXEL_SIZE, model.MD_POS]:
            self.assertEqual(orig_md[k], md[k])
        for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
            self.assertNotIn(k, md)

        # Check that providing a metadata without correction data doesn't change
        # anything
        simpl_md = {model.MD_ROTATION: 90,  # °
                    model.MD_PIXEL_SIZE: (17e-8, 17e-8),  # m
                    model.MD_POS: (5e-3, 2e-3),  # m
                    }
        orig_md = dict(simpl_md)
        img.mergeMetadata(simpl_md)
        for k in [model.MD_ROTATION, model.MD_PIXEL_SIZE, model.MD_POS]:
            self.assertEqual(orig_md[k], simpl_md[k])
        for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
            self.assertNotIn(k, simpl_md)


class TestEnsureYXC(unittest.TestCase):

    def test_simple(self):
        cyxim = numpy.zeros((3, 512, 256), dtype=numpy.uint8)
        cyxim = model.DataArray(cyxim)
        orig_shape = cyxim.shape
        orig_md = cyxim.metadata.copy()
        for i in range(3):
            cyxim[i] = i

        yxcim = img.ensureYXC(cyxim)
        self.assertEqual(yxcim.shape, (512, 256, 3))
        self.assertEqual(yxcim.metadata[model.MD_DIMS], "YXC")

        # check original da was not changed
        self.assertEqual(cyxim.shape, orig_shape)
        self.assertDictEqual(orig_md, cyxim.metadata)

        # try again with explicit metadata
        cyxim.metadata[model.MD_DIMS] = "CYX"
        orig_md = cyxim.metadata.copy()

        yxcim = img.ensureYXC(cyxim)
        self.assertEqual(yxcim.shape, (512, 256, 3))
        self.assertEqual(yxcim.metadata[model.MD_DIMS], "YXC")

        # check no metadata was changed
        self.assertDictEqual(orig_md, cyxim.metadata)

        for i in range(3):
            self.assertEqual(yxcim[0, 0, i], i)

    def test_no_change(self):
        yxcim = numpy.zeros((512, 256, 3), dtype=numpy.uint8)
        yxcim = model.DataArray(yxcim)
        yxcim.metadata[model.MD_DIMS] = "YXC"

        newim = img.ensureYXC(yxcim)
        self.assertEqual(newim.shape, (512, 256, 3))
        self.assertEqual(newim.metadata[model.MD_DIMS], "YXC")


class TestIsClipping(unittest.TestCase):

    def test_no_clip(self):
        im = numpy.zeros((512, 256), dtype=numpy.uint8)
        im = model.DataArray(im)
        self.assertFalse(img.isClipping(im))
        self.assertFalse(img.isClipping(im, (0, 36)))

        im[1, 1] = 254
        self.assertFalse(img.isClipping(im))
        self.assertFalse(img.isClipping(im, (0, 255)))

    def test_clip(self):
        im = numpy.zeros((512, 256), dtype=numpy.uint8)
        im = model.DataArray(im)
        im[1, 1] = 255
        self.assertTrue(img.isClipping(im))

        im[1, 1] = 36
        self.assertTrue(img.isClipping(im, (0, 36)))


class TestRGB2Greyscale(unittest.TestCase):

    def test_simple(self):
        rgbim = numpy.zeros((512, 256, 3), dtype=numpy.uint8)
        rgbim = model.DataArray(rgbim)
        gsim = img.RGB2Greyscale(rgbim)
        self.assertEqual(gsim.shape, rgbim.shape[0:2])
        numpy.testing.assert_array_equal(gsim, rgbim[:, :, 0])

        rgbim[1, 1, 1] = 254
        gsim = img.RGB2Greyscale(rgbim)
        self.assertEqual(gsim.shape, rgbim.shape[0:2])
        self.assertEqual(gsim[1, 1], 254)


class TestRescaleHQ(unittest.TestCase):

    def test_simple(self):
        size = (1024, 512)
        background = 2 ** 12
        img12 = numpy.zeros(size, dtype="uint16") + background
        watermark = 538
        # write a square of watermark
        img12[20:40, 50:70] = watermark

        # rescale
        out = img.rescale_hq(img12, (512, 256))
        self.assertEqual(out.shape, (512, 256))
        self.assertEqual(out.dtype, img12.dtype)
        # test if the watermark is in the right place
        self.assertEqual(out[15, 30], watermark)
        self.assertEqual(out[30, 60], background)

    def test_smoothness(self):
        size = (100, 100)
        img_in = numpy.zeros(size, dtype="uint8")
        # draw an image like a chess board
        for i in range(0, 100):
            for j in range(0, 100):
                img_in[i, j] = ((i + j) % 2) * 255

        # rescale
        out = img.rescale_hq(img_in, (50, 50))
        # if the image is smooth, all the values are the same
        for i in range(10, 20):
            self.assertEqual(128, out[0, i])

    def test_data_array_metadata(self):
        size = (1024, 512)
        depth = 2 ** 12
        img12 = numpy.zeros(size, dtype="uint16") + depth // 2
        watermark = 538
        # write a square of watermark
        img12[20:40, 50:70] = watermark
        metadata = {
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),
            model.MD_BINNING: (1, 1),
            model.MD_AR_POLE: (253.1, 65.1)
        }
        img12da = model.DataArray(img12, metadata)

        # rescale
        out = img.rescale_hq(img12da, (512, 256))
        self.assertEqual(out.shape, (512, 256))
        # test if the watermark is in the right place
        self.assertEqual(out[15, 30], watermark)
        self.assertEqual(out[30, 60], depth // 2)

        # assert metadata
        self.assertEqual(out.metadata[model.MD_PIXEL_SIZE], (2e-06, 4e-05))
        self.assertEqual(out.metadata[model.MD_BINNING], (2.0, 2.0))
        self.assertEqual(out.metadata[model.MD_AR_POLE], (126.55, 32.55))

    def test_5d(self):
        # C=3, T=2, Z=2, Y=1024, X=512
        size = (3, 2, 2, 1024, 512)
        background = 58
        img_in = numpy.zeros(size, dtype="uint8") + background
        img_in = model.DataArray(img_in)
        out = img.rescale_hq(img_in, (3, 2, 2, 512, 256))
        self.assertEqual(out.shape, (3, 2, 2, 512, 256))

    def test_rgb(self):
        """
        Test downscaling an RGB in YXC format
        """
        # X=1024, Y=512
        size = (512, 1024, 3)
        background = 58
        img_in = numpy.zeros(size, dtype="uint8") + background
        # watermark
        img_in[246:266, 502:522, 0] = 50
        img_in[246:266, 502:522, 1] = 100
        img_in[246:266, 502:522, 2] = 150
        img_in = model.DataArray(img_in)
        img_in.metadata[model.MD_DIMS] = "YXC"
        out = img.rescale_hq(img_in, (256, 512, 3))
        self.assertEqual(out.shape, (256, 512, 3))
        self.assertEqual(out.dtype, img_in.dtype)
        # Check watermark. Should be no interpolation between color channels
        self.assertEqual(50, out[128, 256, 0])
        self.assertEqual(100, out[128, 256, 1])
        self.assertEqual(150, out[128, 256, 2])

    def test_25d(self):
        """
        Test downscaling an 2.5D image (YXC, with C=14)
        """
        # X=1024, Y=512
        size = (512, 1024, 14)
        background = 58
        img_in = numpy.zeros(size, dtype=numpy.float) + background
        # watermark
        img_in[246:266, 502:522, 0] = 50
        img_in[246:266, 502:522, 1] = 100
        img_in[246:266, 502:522, 2] = 150
        img_in[246:266, 502:522, 3] = 255  # Alpha
        img_in = model.DataArray(img_in)
        img_in.metadata[model.MD_DIMS] = "YXC"
        out = img.rescale_hq(img_in, (256, 512, 14))
        self.assertEqual(out.shape, (256, 512, 14))
        self.assertEqual(out.dtype, img_in.dtype)
        # Check watermark. Should be no interpolation between color channels
        self.assertEqual(50, out[128, 256, 0])
        self.assertEqual(100, out[128, 256, 1])
        self.assertEqual(150, out[128, 256, 2])
        self.assertEqual(255, out[128, 256, 3])


class TestMeanWithinCircle(unittest.TestCase):

    def test_3d(self):
        """
        Check that the mean of 3D data is 1D
        """
        # X = 40, Y = 30
        data = numpy.zeros((3, 30, 40), dtype=numpy.uint16)
        data[1] = 1
        data[2] = 2
        data[2, 29, 39] = 100

        # Tiny circle of 1px => same as the point
        m = mean_within_circle(data, (35, 10), 1)
        self.assertEqual(m.shape, (3,))
        numpy.testing.assert_equal(m, data[:, 10, 35])  # Y, X are in reverse order

        # Circle of radius 3 on a area where every point is the same value => same as the center
        m = mean_within_circle(data, (15, 10), 3)
        self.assertEqual(m.shape, (3,))
        numpy.testing.assert_almost_equal(m, data[:, 15, 10])  # Y, X are in reverse order

        # Circle of radius 3 on a area where a point is brighter => bigger than the average
        m = mean_within_circle(data, (39, 28), 3)
        self.assertEqual(m.shape, (3,))
        numpy.testing.assert_almost_equal(m[0:2], data[0:2, 28, 39])  # Y, X are in reverse order
        self.assertGreater(m[2], data[2, 28, 39])  # Y, X are in reverse order

        # Very large circle => it's also fine
        m = mean_within_circle(data, (20, 10), 300)
        self.assertEqual(m.shape, (3,))

    def test_4d(self):
        """
        Check that the mean of 4D data is 2D
        """
        # X = 40, Y = 30
        data = numpy.zeros((25, 3, 30, 40), dtype=numpy.uint8)
        data[:, 1] = 1
        data[:, 2] = 2
        data[:, 2, 29, 39] = 100

        # Tiny circle of 1px => same as the point
        m = mean_within_circle(data, (35, 10), 1)
        self.assertEqual(m.shape, (25, 3))
        numpy.testing.assert_equal(m, data[:, :, 10, 35])  # Y, X are in reverse order

        # Circle of radius 3 on a area where every point is the same value => same as the center
        m = mean_within_circle(data, (15, 10), 3)
        self.assertEqual(m.shape, (25, 3,))
        numpy.testing.assert_almost_equal(m, data[:, :, 15, 10])  # Y, X are in reverse order


class TestImageIntegrator(unittest.TestCase):

    def setUp(self):
        dtype = numpy.uint16
        im = numpy.ones((5, 5), dtype=dtype)
        metadata = {model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_BINNING: (1, 1),  # px, px
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
                    model.MD_POS: (1e-3, -30e-3),  # m
                    model.MD_EXP_TIME: 1.2,  # s
                    model.MD_DET_TYPE: model.MD_DT_INTEGRATING,
                    model.MD_DWELL_TIME: 1e-06,  # s
                    }
        self.data = model.DataArray(im, metadata)
        self.integrated_data = None
        self.img_intor = None
        self.integrationCounts = 3

    def test_simple(self):
        self.img_intor = img.ImageIntegrator(self.integrationCounts)
        self.assertEqual(self.img_intor.steps, self.integrationCounts)

        for i in range(self.integrationCounts):
            self.integrated_data = self.img_intor.append(self.data[i])

        numpy.testing.assert_equal(self.integrated_data, (numpy.array([3, 3, 3, 3, 3], dtype='uint32')))
        self.assertEqual(self.img_intor._best_dtype, get_best_dtype_for_acc(self.data[0].dtype, self.integrationCounts))

        # check that the parameters ._img, ._step are reset after all images are integrated
        self.assertEqual(self.img_intor._step, 0)
        self.assertEqual(self.img_intor._img, None)

    def test_2Ddata(self):
        self.img_intor = img.ImageIntegrator(self.integrationCounts)
        self.assertEqual(self.img_intor.steps, self.integrationCounts)

        for i in range(self.integrationCounts):
            self.integrated_data = self.img_intor.append(self.data)

        data_2d = self.integrationCounts * numpy.ones((5, 5), 'uint32')
        numpy.testing.assert_equal(self.integrated_data, data_2d)

    def test_metadata(self):
        """
        Test that the metadata is updated after image integration
        """
        self.img_intor = img.ImageIntegrator(self.integrationCounts)
        dw_time, exp_time = 0, 0

        for i in range(self.integrationCounts):
            self.integrated_data = self.img_intor.append(self.data[i])
            dw_time += self.data[i].metadata[model.MD_DWELL_TIME]
            exp_time += self.data[i].metadata[model.MD_EXP_TIME]

        md_intor = self.integrated_data.metadata  # metadata of the integrated image
        self.assertEqual(md_intor[model.MD_DWELL_TIME], dw_time)
        self.assertEqual(md_intor[model.MD_EXP_TIME], exp_time)
        self.assertIn(model.MD_INTEGRATION_COUNT, md_intor)
        self.assertEqual(md_intor[model.MD_INTEGRATION_COUNT], self.integrationCounts)

    def test_baseline_metadata(self):
        """
        Test in case baseline exists
        """
        self.data.metadata[model.MD_BASELINE] = 2
        self.img_intor = img.ImageIntegrator(self.integrationCounts)

        for i in range(1, self.integrationCounts):
            self.integrated_data = self.img_intor.append(self.data[i])

        md_intor = self.integrated_data.metadata
        self.assertEqual(md_intor[model.MD_BASELINE], 2)

    def test_one_integration(self):
        """
        Test in case of one integration step
        """
        self.integrationCounts = 1
        self.img_intor = img.ImageIntegrator(self.integrationCounts)
        self.integrated_data = self.img_intor.append(self.data[0])

        numpy.testing.assert_equal(self.integrated_data, self.data[0])

    def test_steps_change(self):
        """
        Test in case the steps change while the images are integrated one after another
        """
        self.img_intor = img.ImageIntegrator(self.integrationCounts)

        for i in range(self.integrationCounts):
            self.integrated_data = self.img_intor.append(self.data[i])
            self.img_intor.steps = 1

        numpy.testing.assert_equal(self.integrated_data, (numpy.array([1, 1, 1, 1, 1], dtype='uint16')))

    def test_normal_detector(self):
        """
        Test in case of a normal detector (SEM)
        """
        self.data.metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self.img_intor = img.ImageIntegrator(self.integrationCounts)

        for i in range(self.integrationCounts):
            self.integrated_data = self.img_intor.append(self.data[i])

        numpy.testing.assert_equal(self.integrated_data, (numpy.array([1, 1, 1, 1, 1])))


class TestMergeTiles(unittest.TestCase):

    def test_one_tile(self):

        def getSubData(dast, zoom, rect):
            x1, y1, x2, y2 = rect
            tiles = []
            for x in range(x1, x2 + 1):
                tiles_column = []
                for y in range(y1, y2 + 1):
                    tiles_column.append(dast.getTile(x, y, zoom))
                tiles.append(tiles_column)
            return tiles

        FILENAME = u"test" + tiff.EXTENSIONS[0]
        POS = (5.0, 7.0)
        size = (250, 200)
        md = {
            model.MD_DIMS: 'YX',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr = numpy.arange(size[0] * size[1], dtype=numpy.uint8).reshape(size[::-1])
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        rdata = tiff.open_data(FILENAME)

        tiles = getSubData(rdata.content[0], 0, (0, 0, 0, 0))
        merged_img = img.mergeTiles(tiles)
        self.assertEqual(merged_img.shape, (200, 250))
        self.assertEqual(merged_img.metadata[model.MD_POS], POS)

        del rdata

        os.remove(FILENAME)

    def test_multiple_tiles(self):

        def getSubData(dast, zoom, rect):
            x1, y1, x2, y2 = rect
            tiles = []
            for x in range(x1, x2 + 1):
                tiles_column = []
                for y in range(y1, y2 + 1):
                    tiles_column.append(dast.getTile(x, y, zoom))
                tiles.append(tiles_column)
            return tiles

        FILENAME = u"test" + tiff.EXTENSIONS[0]
        POS = (5.0, 7.0)
        size = (2000, 1000)
        md = {
            model.MD_DIMS: 'YX',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr = numpy.arange(size[0] * size[1], dtype=numpy.uint8).reshape(size[::-1])
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        rdata = tiff.open_data(FILENAME)

        tiles = getSubData(rdata.content[0], 0, (0, 0, 7, 3))
        merged_img = img.mergeTiles(tiles)
        self.assertEqual(merged_img.shape, (1000, 2000))
        self.assertEqual(merged_img.metadata[model.MD_POS], POS)

        tiles = getSubData(rdata.content[0], 0, (0, 0, 3, 1))
        merged_img = img.mergeTiles(tiles)
        self.assertEqual(merged_img.shape, (512, 1024))
        numpy.testing.assert_almost_equal(merged_img.metadata[model.MD_POS], (4.999512, 7.000244))

        del rdata

        os.remove(FILENAME)

    def test_rgb_tiles(self):

        def getSubData(dast, zoom, rect):
            x1, y1, x2, y2 = rect
            tiles = []
            for x in range(x1, x2 + 1):
                tiles_column = []
                for y in range(y1, y2 + 1):
                    tiles_column.append(dast.getTile(x, y, zoom))
                tiles.append(tiles_column)
            return tiles

        FILENAME = u"test" + tiff.EXTENSIONS[0]
        POS = (5.0, 7.0)
        size = (3, 2000, 1000)
        md = {
            model.MD_DIMS: 'YXC',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr = numpy.arange(size[0] * size[1] * size[2], dtype=numpy.uint8).reshape(size[::-1])
        print(arr.shape)
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        rdata = tiff.open_data(FILENAME)

        tiles = getSubData(rdata.content[0], 0, (0, 0, 7, 3))
        merged_img = img.mergeTiles(tiles)
        self.assertEqual(merged_img.shape, (1000, 2000, 3))
        self.assertEqual(merged_img.metadata[model.MD_POS], POS)

        tiles = getSubData(rdata.content[0], 0, (0, 0, 3, 1))
        merged_img = img.mergeTiles(tiles)
        self.assertEqual(merged_img.shape, (512, 1024, 3))
        numpy.testing.assert_almost_equal(merged_img.metadata[model.MD_POS], (4.999512, 7.000244))

        del rdata

        os.remove(FILENAME)


class TestRotateImage(unittest.TestCase):

    def test_rotate_img_metadata(self):
        """
        Verify that rotating an image around a center of rotation results in
        the correct metadata being set on the image.
        """

        # Expected position calculated with the equation:
        # exp_pos_x = cos(alpha) * (pos_x - cor_x) - sin(alpha) * (pos_y - cor_y) + cor_y
        # exp_pos_y = sin(alpha) * (pos_x - cor_x) + sin(alpha) * (pos_y - cor_y) + cor_y
        test_data = [
            # pos:  {center of rot, rotation,  expected position}
            {"pos": (0, 0), "cor": (0, 0), "rot": 10, "exp_pos": (0, 0)},
            {"pos": (0, 1), "cor": (0, 0), "rot": 90, "exp_pos": (-1, 0)},
            {"pos": (0, 1), "cor": (0, 0), "rot": -90, "exp_pos": (1, 0)},
            {"pos": (123e-6, 24e-7), "cor": (123.0e-6, 2.4e-6), "rot": 5.7, "exp_pos": (123.0e-6, 2.4e-6)},
            {"pos": (123e-6, 24e-7), "cor": (10.0e-6, 24e-6), "rot": 5.7, "exp_pos": (1.24586586e-4, 1.37299314e-05)},
            {"pos": (123e-6, 24e-7), "cor": (10.0e-6, 24e-6), "rot": -5.7, "exp_pos": (1.20295973e-4, -8.7163320e-06)},
        ]

        data = numpy.ones((51, 76))
        for t in test_data:
            md = {model.MD_POS: t["pos"]}
            image = model.DataArray(data, md)
            rotation = math.radians(t["rot"])
            center_of_rot = t["cor"]
            rotated_img = img.rotate_img_metadata(image, rotation, center_of_rot)

            self.assertEqual(rotation, rotated_img.metadata[model.MD_ROTATION])
            testing.assert_tuple_almost_equal(t["exp_pos"], rotated_img.metadata[model.MD_POS])
            numpy.testing.assert_array_equal(image, rotated_img)  # The data should not change, only the metadata.


class TestTintToMdFormat(unittest.TestCase):

    def test_tint_to_md_format(self):
        """
        Check if passing a tint in different input types will lead to returning the correct metadata format.
        """
        # test for Tuple tint instance type
        tint_name = img.tint_to_md_format((240, 240, 10))
        self.assertEqual(tint_name, (240, 240, 10))

        # test for Colormap tint instance type
        tint_name = img.tint_to_md_format(cm.get_cmap('summer', 10))
        self.assertEqual(tint_name, 'summer')

        # test for str tint instance type
        tint_name = img.tint_to_md_format(TINT_FIT_TO_RGB)
        self.assertEqual(tint_name, TINT_FIT_TO_RGB)

    def test_tint_to_md_format_unexpected(self):
        """
        Check if passing an unknown tint str will lead to raising a correct exception.
        """
        TINT_RGB_NONE = "norgb"

        with self.assertRaises(ValueError):
            img.tint_to_md_format(TINT_RGB_NONE)


class TestMdFormatToTint(unittest.TestCase):

    def test_md_format_to_tint_tuple(self):
        """
        Check if passing different user_tint types in saved metadata will lead to returning the correct tint format.
        """
        # test for Tuple tint instance type
        tint_obj = img.md_format_to_tint((240, 240, 10))
        self.assertIsInstance(tint_obj, tuple)

        # test for str tint instance type
        tint_obj = img.md_format_to_tint(TINT_FIT_TO_RGB)
        self.assertIsInstance(tint_obj, str)

        # test for Colormap tint instance type
        tint_obj = img.md_format_to_tint('autumn')
        self.assertIsInstance(tint_obj, colors.Colormap)

    def test_md_format_to_tint_unexpected_input(self):
        """
        Check if passing an unknown user_tint str or a wrong type in
        saved metadata will lead to raising the correct exception.
        """
        TINT_RGB_NONE = "norgb"

        with self.assertRaises(ValueError):
            img.md_format_to_tint(TINT_RGB_NONE)

        with self.assertRaises(TypeError):
            img.md_format_to_tint(0.05)


class TestGetOutliers(unittest.TestCase):

    def test_get_outliers_default(self):
        """
        Verify that with the default outlier value (0) the right
        min/max values of the histogram range within a DA are returned.
        It is expected that none of the outliers will be discarded.
        """
        # input will be a DA containing a fake picture
        size = (300, 300)
        depth = 256
        outliers = [200, 50, 40, 2, 9]

        # create a DA with a couple of outliers
        data = numpy.zeros(size, dtype='uint8') + depth // 2
        data[0, 1] = outliers[0]
        data[0, 10] = outliers[1]
        data[0, 60] = outliers[2]
        data[0, 280] = outliers[3]
        data[0, 290] = outliers[4]

        # keep the value for outliers default so force output_range
        # to be between lowest and the highest outlier
        output_range = img.getOutliers(data)

        # min will be the lowest outlier
        self.assertEqual(min(output_range), numpy.amin(data))
        # max will be the highest outlier
        self.assertEqual(max(output_range), numpy.amax(data))
        # check if greatest outlier is found in the output value
        self.assertTrue(min(outliers) in output_range)

    def test_get_outliers_discard_all(self):
        """
        Verify that with a given outlier value the right min/max
        values of the histogram range within a DA are returned.
        It is expected that all outliers will be discarded.
        """
        # input will be a DA containing a fake picture and a value for outliers
        size = (300, 300)
        outlier_num = 0.5  # use the max value, all outliers will be discarded
        outliers = [200, 50, 40, 2, 9]
        depth = 256

        # create a DA with a couple of outliers
        data = numpy.zeros(size, dtype='uint8') + depth // 2
        data[0, 1] = outliers[0]
        data[0, 10] = outliers[1]
        data[0, 60] = outliers[2]
        data[0, 280] = outliers[3]
        data[0, 290] = outliers[4]

        output_range = img.getOutliers(data, outlier_num)

        # both the min and max value of output_range will
        # be the same due to the median being returned
        self.assertEqual(output_range[0], depth // 2)
        self.assertEqual(output_range[1], depth // 2)
        # check if the outliers are discarded and not found in the output value
        self.assertTrue(all(o not in output_range for o in outliers))


class TestGuessDRange(unittest.TestCase):

    def test_guess_drange_zero_bpp(self):
        """
        Test if value of 0 for BPP returns the default min/max for uint8.
        """
        size = (2000, 1000)
        md = {
              model.MD_DIMS: 'YX',
              model.MD_PIXEL_SIZE: (1e-6, 1e-6),
              model.MD_BPP: 0
             }

        # check for dtype uint8
        arr = numpy.arange(size[0] * size[1], dtype=numpy.uint8)
        data = model.DataArray(arr, metadata=md)

        # min/max values should be uint8 min/max
        min_max_type = numpy.iinfo(numpy.uint8)

        # the ValueError raised is handled in the guessDRange method
        min_max_values = img.guessDRange(data)

        self.assertEqual(min_max_values[0], min_max_type.min)
        self.assertEqual(min_max_values[1], min_max_type.max)

    def test_guess_drange_wrong_type(self):
        """
        Test if an unexpected array datatype will raise a TypeError.
        """
        size = (2000, 2000)
        md = {
              model.MD_DIMS: 'YX',
              model.MD_PIXEL_SIZE: (1e-6, 1e-6),
              model.MD_BPP: 8
             }

        # check for dtype float (=wrong type)
        arr = numpy.arange(size[0] * size[1], dtype=numpy.float)
        data = model.DataArray(arr, metadata=md)

        with self.assertRaises(TypeError):
            img.guessDRange(data)

    def test_guess_drange_normal(self):
        """
        Test the data range of the data based on guess method with regular model dict, type and DA values.
        """
        size = (4000, 4000)
        md = {
              model.MD_DIMS: 'YX',
              model.MD_PIXEL_SIZE: (1e-6, 1e-6),
              model.MD_BPP: 8
             }

        # check for dtype int32
        arr = numpy.arange(size[0] * size[1], dtype='int32')
        data = model.DataArray(arr, metadata=md)

        # min/max values are dependent on given BPP
        min_max_values = img.guessDRange(data)

        bpp_conversion_min = -abs(2 ** data.metadata[model.MD_BPP] // 2)
        bpp_conversion_max = (2 ** (data.metadata[model.MD_BPP] - 1)) - 1

        self.assertEqual(min_max_values[0], bpp_conversion_min)
        self.assertEqual(min_max_values[1], bpp_conversion_max)


class TestGetColorBar(unittest.TestCase):

    def test_get_colorbar(self):
        """
        Test if input of a registered matplotlib.colors colormap will return a ndArray of
        the right tuple shape, within ranges of uint8 and with alpha channel included.
        """
        # input will be color_map (obj matplotlib) height (int), width (int) and alpha (bool)
        input_cm = cm.get_cmap('cividis')
        input_height = 16
        input_width = 72

        # test with the alpha channel included
        output_array = img.getColorbar(input_cm, input_height, input_width, alpha=True)

        # test if the returned tuple contains an alpha channel
        self.assertEqual(output_array.shape[2], 4)
        # test for RGB uint8 dtype
        self.assertTrue(output_array.dtype, numpy.uint8)

        # test without the alpha channel
        output_array = img.getColorbar(input_cm, input_height, input_width)

        # test if the returned tuple does not have an alpha channel
        self.assertEqual(output_array.shape[2], 3)

        # test if height input as a float will raise an AssertionError exception
        with self.assertRaises(AssertionError):
            img.getColorbar(None, 15, 162.5)


class TestTintToColormap(unittest.TestCase):

    def test_tint_to_colormap(self):
        """
        Test if different tint input types will return a valid colormap.
        """
        output_colormap = img.tintToColormap(cm.get_cmap('viridis'))
        self.assertEqual(output_colormap, cm.get_cmap('viridis'))

        output_colormap = img.tintToColormap((0, 73, 255))
        self.assertIsInstance(output_colormap, cm.colors.LinearSegmentedColormap)

        output_colormap = img.tintToColormap(TINT_RGB_AS_IS)
        self.assertIsInstance(output_colormap, cm.colors.LinearSegmentedColormap)

        output_colormap = img.tintToColormap(TINT_FIT_TO_RGB)
        self.assertIsInstance(output_colormap, cm.colors.ListedColormap)

    def test_tint_to_colormap_unexpected_input(self):
        """
        Test if a wrong tint type will raise a TypeError and a wrong tint value will raise a ValueError.
        """
        input_tint = "norgb"

        with self.assertRaises(TypeError):
            img.tintToColormap(input_tint)

        input_tint = (0, 300, 255)

        with self.assertRaises(ValueError):
            img.tintToColormap(input_tint)


class TestGetYXFromZYX(unittest.TestCase):

    def setUp(self):
        # create a mock grayscale 3D image array
        size = (200, 399, 399)
        self.md = {
              model.MD_DIMS: 'YXZ',
              model.MD_PIXEL_SIZE: (1e-6, 1e-6),
              model.MD_BPP: 8,
              model.MD_POS: (5.0, 7.0)
             }

        # create a mock 3D image array with white color values
        self.arr1 = numpy.zeros(size, dtype='uint8') + 255

        x_count, y_count = 0, 0
        self.color_num = 160

        # change values of the ndarray in a pyramid shape so that every
        # z-slice contains a different amount of gray color values
        for z in range(self.arr1.shape[0]):
            for y in range(y_count, self.arr1.shape[1] - y_count):
                self.arr1[z, y, x_count:self.arr1.shape[2] - x_count] = self.color_num
            y_count += 1
            x_count += 1

    def test_get_yx_from_zyx_normal(self):
        """
        Verify that using a 3D DA picture will return the right 2D DA picture.
        """
        data = model.DataArray(self.arr1, metadata=self.md)
        z_slice = 160

        output_DA = numpy.asarray(img.getYXFromZYX(data, zIndex=z_slice))

        # check if dimensions of the returned ndarray are smaller than input array (ZYX vs YX)
        self.assertLess(output_DA.ndim, data.ndim)
        # check if the XY slice output is part of input DA
        self.assertTrue(output_DA in data)

        # check if the slice has the same size (x,y) as input
        self.assertEqual(output_DA.shape, data.shape[1:])

        # calculates a checknum value based on predicted values e.g. 3x3 = 9 elements containing value of color_num
        # this value is based on input z_slice of 160 compared to max z value of 199 (array z shape - 1)
        z_num = self.arr1.shape[0] - 1
        checknum = ((1 + (z_num - z_slice)) + z_num - z_slice) * ((1 + (z_num - z_slice)) + z_num - z_slice)

        self.assertEqual(numpy.count_nonzero(output_DA == self.color_num), checknum)

    def test_get_yx_from_zyx_1d(self):
        """
        Verify that using a 1D DA picture will return the right DA slice.
        See if expected input of ZYX DA can handle an X only DA.
        """
        # create a 2D data array using a sliced 3D array
        # keep the z-slice at default
        data = model.DataArray(self.arr1[0, 0, :], metadata=self.md)
        output_DA = numpy.asarray(img.getYXFromZYX(data))

        # check if the XY slice output is part of input DA
        self.assertTrue(output_DA in data)
        # check if the slice has the same size in x as input
        self.assertEqual(numpy.squeeze(output_DA).shape, data.shape)
        # check if all elements are of the set color_num
        self.assertTrue(numpy.all(output_DA == self.color_num))

    def test_get_yx_from_zyx_4d(self):
        """
        Verify that using a 4D DA picture will fail and that a ValueError exception is raised.
        """
        # use a 4D array to force a ValueError when slicing array shape
        data = model.DataArray(self.arr1.reshape(20, 399, 399, 10), metadata=self.md)

        with self.assertRaises(ValueError):
            img.getYXFromZYX(data)


class TestEnsure2DImage(unittest.TestCase):

    def setUp(self):
        y = 4000
        x = 4000
        # size of picture in CTZYX
        size = (1, 1, 1, y, x)
        arr = numpy.ones(size, dtype='uint8')
        self.data = model.DataArray(arr)

    def test_ensure_2d_image(self):
        """
        Test if using different dimensions of DA will be handled appropriately.
        """
        # Verify that using a CTZ=111 5D DA will return the right 2D DA.
        out_arr = img.ensure2DImage(self.data)

        # check if the YX values match
        self.assertEqual(out_arr.shape, self.data.shape[3:])

        # Verify that using a full 5D DA will fail and see if a ValueError exception is raised
        data = self.data.reshape(1, 2, 50, 400, 400)

        with self.assertRaises(ValueError):
            img.ensure2DImage(data)


class TestSubtract(unittest.TestCase):

    def setUp(self):
        self.pic1 = numpy.zeros((600, 600))
        self.pic2 = numpy.zeros((600, 600))

    def test_subtract_uint(self):
        """
        Verify that using an uint8 as dtype will return the right DA.
        """
        pic1 = self.pic1.astype('uint8')
        pic2 = self.pic2.astype('uint8')

        # add white squares in the black pictures
        pic1[200:500, 200:500] = 255
        pic2[300:400, 300:400] = 255

        # subtract the pictures
        out_arr = img.Subtract(pic1, pic2)

        # output shape should be the same as biggest input DA
        self.assertEqual(out_arr.shape, pic1.shape)
        # all values in this square should return false=0
        self.assertTrue(numpy.all(out_arr[300:400, 300:400] == 0))

    def test_subtract_underflow(self):
        """
        Verify that using an uint8 as dtype will return the right DA.
        Also test that underflow does not happen.
        """
        pic1 = self.pic1.astype('uint8')
        pic2 = self.pic2.astype('uint8')

        # 10 -11 == -1, but uint8 doesn't support it, so it should be clipped to 0.
        pic1[:] = 10
        pic2[:] = 11

        # subtract the pictures
        out_arr = img.Subtract(pic1, pic2)

        # output shape should be the same as biggest input DA
        self.assertEqual(out_arr.shape, pic1.shape)
        # see if the return value is of type uint8
        self.assertEqual(out_arr.dtype, numpy.dtype('uint8'))
        # all values in this square should return false=0
        numpy.testing.assert_equal(out_arr, 0)

    def test_subtract_float(self):
        """
        Verify that using a float as dtype will return the right DA.
        """
        pic1 = self.pic1.astype('float32')
        pic2 = self.pic2.astype('float32')

        # add white squares in the black pictures
        pic1[200:500, 200:500] = 0.5
        pic2[300:400, 300:400] = 0.5

        # subtract the pictures
        out_arr = img.Subtract(pic1, pic2)

        # output shape should be the same as biggest input DA
        self.assertEqual(out_arr.shape, pic1.shape)
        # all values in this square should return false=0
        self.assertTrue(numpy.all(out_arr[300:400, 300:400] == 0))

    def test_subtract_uint_float(self):
        """
        Verify that using pictures with different dtypes will return the right DA.
        Check if using 2 different dtype arrays will still work.
        """
        pic1 = self.pic1.astype('uint8')
        pic2 = self.pic2.astype('float32')

        # add white squares in the black pictures
        pic1[200:500, 200:500] = 1
        pic2[300:400, 300:400] = 0.5

        # subtract the pictures
        out_arr = img.Subtract(pic1, pic2)

        # output shape should be the same as biggest input DA
        # FIXME: for now this doesn't pass because Subtract() always return a float
        # self.assertEqual(out_arr.shape, pic1.shape)

        # no subtraction should have happened due to different dtypes
        self.assertTrue(numpy.all(out_arr[300:400, 300:400] == 0.5))

        out_arr = img.Subtract(pic2, pic1)
        self.assertEqual(out_arr.dtype, pic2.dtype)


class TestGetTilesSize(unittest.TestCase):

    def test_get_tiles_size_shape2(self):
        """
        Verify that using a 2D shaped DA tiles list will return the right length and size of DA.
        Also check for only 1 tile.
        """
        # create the 4 DA subtiles
        size = (800, 600)
        tile1 = numpy.zeros(size)
        tile2 = numpy.zeros(size)
        tile3 = numpy.zeros(size)
        tile4 = numpy.zeros(size)

        # start index for y-axis
        y_start = 0

        # create a pattern in the tile
        for x in reversed(range(size[0])):
            for y in range(y_start, size[1]):
                tile1[x, y] = 1
            y_start += 1

        # create the other 3 tiles by swapping axis of tile1 this creates a pattern
        tile2 = numpy.flip(tile1, axis=1)
        tile3 = numpy.flip(tile1, axis=0)
        tile4 = numpy.flip(tile3, axis=1)

        # create a Tuple of 4 tiles
        tile_set = (tile1, tile2, tile3, tile4)
        # create a Tuple containing 4 tile Tuples
        tile_collection = (tile_set, tile_set, tile_set, tile_set)

        output_size = img.getTilesSize(tile_collection)

        # check if the output size is correct (based on input size)
        self.assertEqual(output_size, tuple(len(tile_collection) * elem for elem in size))

        # check single tile input
        tile_set = (tile1,)
        tile_collection = (tile_set,)

        output_size = img.getTilesSize(tile_collection)

        self.assertEqual(output_size, size)

    def test_get_tiles_size_shape3(self):
        """
        Verify that using a 3D shaped DA tiles list will return the right length and size of DA.
        """
        # create the 4 DA subtiles
        size = (800, 600, 10)

        tile1 = numpy.zeros(size)
        tile2 = numpy.zeros(size)
        tile3 = numpy.zeros(size)
        tile4 = numpy.zeros(size)

        # start index for y-axis
        y_start = 0

        # create a pattern in the tile
        for x in reversed(range(size[0])):
            for y in range(y_start, size[1]):
                tile1[x, y] = 1
            y_start += 1

        # create the other 3 tiles by swapping axis of tile1 this creates a pattern
        tile2 = numpy.flip(tile1, axis=1)
        tile3 = numpy.flip(tile1, axis=0)
        tile4 = numpy.flip(tile3, axis=1)

        # create a Tuple of 4 tiles
        tile_set = (tile1, tile2, tile3, tile4)
        # create a Tuple containing 4 tile Tuples
        tile_collection = (tile_set, tile_set, tile_set, tile_set)

        output_size = img.getTilesSize(tile_collection)

        # check if the output length is not the same as input size
        self.assertFalse(len(output_size) == len(size))
        # check if the output sizes are correct (based on input size) skipping the last element of size
        self.assertEqual(output_size[0], len(tile_collection) * size[0])
        self.assertEqual(output_size[1], len(tile_collection) * size[1])


class TestGetCenterOfTiles(unittest.TestCase):

    def setUp(self):
        md = {
              model.MD_DIMS: 'CTZYX',
              model.MD_PIXEL_SIZE: (1e-6, 1e-6),
              model.MD_POS: (0.04, 0.07),
              model.MD_SHEAR: 0.1
             }

        # create rectangular tile sizes of 5 dimensions
        self.size = (1, 1, 1, 600, 400)
        tile1 = model.DataArray(numpy.zeros(self.size), md)
        tile2 = model.DataArray(numpy.zeros(self.size), md)
        tile3 = model.DataArray(numpy.zeros(self.size), md)

        # start index for y-axis
        y_start = 0

        # create a pattern in the tile
        for x in reversed(range(self.size[-2])):
            for y in range(y_start, self.size[-1]):
                tile1[..., x, y] = 1
            y_start += 1

        # create the other 2 tiles by swapping axis of tile1
        tile2 = numpy.flip(tile1, axis=1)
        tile3 = numpy.flip(tile1, axis=0)

        # create a Tuple of 3 tiles
        tile_set = (tile1, tile2, tile3)
        # create a tile collection of 3x3 tiles
        self.tile_collection = (tile_set, tile_set, tile_set)

    def test_get_center_of_tiles(self):
        """
        Verify that using a 5D shaped DA tiles list will return the right
        physical position if compared with input metadata position.
        """
        result_shape = tuple(len(self.tile_collection) * len(self.tile_collection) * elem for elem in self.size[-2:])

        # normal input of CTZYX dimension of DA's in tuple list of tuples
        output_pos = img.getCenterOfTiles(self.tile_collection, result_shape)

        # get the middle of the 3x3 tile info
        x_pos = self.tile_collection[1][1].metadata[model.MD_POS][0]
        y_pos = self.tile_collection[1][1].metadata[model.MD_POS][1]

        x_pos_out = round(output_pos[0], 2)
        y_pos_out = round(output_pos[1], 2)

        # check if the x positions match
        self.assertEqual(x_pos_out, x_pos)
        # check if the y positions match
        self.assertEqual(y_pos_out, y_pos)

    def test_get_center_of_tiles_rotated(self):
        """
        Verify that using a 5D shaped DA tiles list which is rotated 90° will
        return the right physical position if compared with input metadata position.
        """
        # rotate 90°
        for x in range(len(self.tile_collection)):
            for y in range(len(self.tile_collection[x])):
                self.tile_collection[x][y].metadata[model.MD_ROTATION] = math.pi/2

        result_shape = tuple(len(self.tile_collection) * len(self.tile_collection) * elem for elem in self.size[-2:])

        # normal input of CTZYX dimension of DA's in tuple list of tuples
        output_pos = img.getCenterOfTiles(self.tile_collection, result_shape)

        # get the middle of the 3x3 tile info
        x_pos = self.tile_collection[1][1].metadata[model.MD_POS][0]
        y_pos = self.tile_collection[1][1].metadata[model.MD_POS][1]

        x_pos_out = round(output_pos[0], 2)
        y_pos_out = round(output_pos[1], 2)

        # check if the x positions match
        self.assertEqual(x_pos_out, x_pos)
        # check if the y positions match
        self.assertEqual(y_pos_out, y_pos)

    def test_get_center_of_tiles_single(self):
        """
        Verify that using a 5D shaped DA tiles list containing a single tile will
        return the right physical position if compared with input metadata position.
        """
        single_size = [[self.tile_collection[0][0]]]

        # normal input of CTZYX dimension of DA's in tuple list of tuples
        output_pos = img.getCenterOfTiles(single_size, self.tile_collection[0][0].shape)

        x_pos = self.tile_collection[0][0].metadata[model.MD_POS][0]
        y_pos = self.tile_collection[0][0].metadata[model.MD_POS][1]

        # check if the x positions match
        self.assertEqual(output_pos[0], x_pos)
        # check if the y positions match
        self.assertEqual(output_pos[1], y_pos)

    def test_get_center_of_tiles_incorrect_shape(self):
        """
        Verify that using a wrong shaped DA tiles list will raise an IndexError exception.
        """
        new_tiles = []

        for t1 in self.tile_collection:
            new_tile = []
            for t2 in t1:
                # remove the first 2 axis so effectively keep a 3D ZYX array
                new_tile.append(t2[0, 0, :, :, :])
            new_tiles.append(new_tile)

        # using wrong shape forces an IndexError while expected shape is CTZYX
        result_shape = tuple(len(new_tiles) * elem for elem in self.size)

        with self.assertRaises(IndexError):
            img.getCenterOfTiles(new_tiles, result_shape)

    def test_get_center_of_tiles_no_pixelsize(self):
        """
        Verify that leaving out PIXEL_SIZE in the metadate DA tiles list will raise a ValueError exception.
        """
        # force ValueError by deleting PIXEL_SIZE from the MD
        del self.tile_collection[0][0].metadata[model.MD_PIXEL_SIZE]
        result_shape = tuple(len(self.tile_collection) * elem for elem in self.size)

        with self.assertRaises(ValueError):
            img.getCenterOfTiles(self.tile_collection, result_shape)


class TestGetBoundingBox(unittest.TestCase):

    def setUp(self):
        # create a 5D size and metadata with necessary items
        self.size = (9, 10, 13, 400, 600)
        self.md = {
            model.MD_DIMS: 'CTZYX',
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
            model.MD_POS: (0.95, 0.07)
        }

    def test_get_bounding_box_normal(self):
        """
        Verify that using a 5D shaped DA will return the right bounding box.
        """
        # create a DA with random values
        new_da = numpy.random.random(self.size)
        new_da = model.DataArray(new_da, self.md)

        output_pos = img.getBoundingBox(new_da)

        # set the positions to check for
        pos_x = new_da.metadata[model.MD_POS][0]
        pos_y = new_da.metadata[model.MD_POS][1]

        # check if the min position of the bounding box is the same as half the
        # size of the DA calculating from the middle point on the 0-axis downwards
        expected_pos_x_min = pos_x - self.size[-1] * self.md[model.MD_PIXEL_SIZE][0] / 2
        self.assertEqual(output_pos[0], expected_pos_x_min)

        # check if the max position of the bounding box is the same as half the
        # size of the DA calculating from the middle point on the 0-axis upwards
        expected_pos_x_max = pos_x + self.size[-1] * self.md[model.MD_PIXEL_SIZE][0] / 2
        self.assertEqual(output_pos[2], expected_pos_x_max)

        # check if the min position of the bounding box is the same as half the
        # size of the DA calculating from the middle point on the 1-axis downwards
        expected_pos_y_min = pos_y - self.size[-2] * self.md[model.MD_PIXEL_SIZE][1] / 2
        self.assertEqual(output_pos[1], expected_pos_y_min)

        # check if the max position of the bounding box is the same as half the
        # size of the DA calculating from the middle point on the 1-axis upwards
        expected_pos_y_max = pos_y + self.size[-2] * self.md[model.MD_PIXEL_SIZE][1] / 2
        self.assertEqual(output_pos[3], expected_pos_y_max)

    def test_get_bounding_box_incomplete_metadata(self):
        """
        Verify that removing PIXEL_SIZE in the metadate DA will fail and a LookupError exception is raised.
        """
        # create a DA with random values
        new_da = numpy.random.random(self.size)
        new_da = model.DataArray(new_da, self.md)

        # remove the DIMS key and check if a return value is passed and no exception is raised
        del new_da.metadata[model.MD_DIMS]
        bbox = img.getBoundingBox(new_da)
        self.assertTrue(isinstance(bbox, tuple))

        # remove the PIXEL_SIZE key and check if a LookupError is forced
        del self.md[model.MD_PIXEL_SIZE]

        with self.assertRaises(LookupError):
            img.getBoundingBox(new_da)

    def test_get_bounding_box_wrong_arrayshape(self):
        """
        Verify that using an unexpected shaped DA will fail and if an IndexError exception is raised.
        The DA is assumed unexpected due to the MD_DIMS stating the DA is supposed to be 5 dims.
        """
        # use an unexpected 2 dimensional shaped DA with random values
        self.size = (4000, 3500)
        new_da = numpy.random.random(self.size)
        new_da = model.DataArray(new_da, self.md)

        # force an IndexError due to wrong DA shape
        with self.assertRaises(IndexError):
            img.getBoundingBox(new_da)


class TestAssembleZCube(unittest.TestCase):

    def setUp(self):
        # create a 2D size and metadata with necessary items
        self.size = (400, 300)
        self.md = {
            model.MD_DIMS: 'YX',
            model.MD_PIXEL_SIZE: (2e-06, 4e-05),
            model.MD_POS: (0.2, 0.2)
        }

        # create an ordered list of (float) focus positions from 1-8um
        self.z_list = [1e-06, 2e-06, 3e-06, 4e-06, 5e-06, 6e-06, 7e-06, 8e-06]

    def test_assemble_zcube_normal(self):
        """
        Verify that using a 2D shaped image DA's in a list will return the right ZCube assembly.
        """
        img_list = []

        # create a list of DA
        for x in range(len(self.z_list)):
            img_list.append(model.DataArray(numpy.random.randint(0, 220, self.size), self.md))

        output_DA = img.assembleZCube(img_list, self.z_list)

        # check if the shape of the returned DA is a combination of the number of z-slices and the size
        input_shape = (len(img_list),) + self.size
        self.assertEqual(output_DA.shape, input_shape)
        # check if metadata includes the centre position of the z-stack
        self.assertEqual(len(output_DA.metadata[model.MD_POS]), 3)
        # check if metadata includes the pixel size of the z-stack
        self.assertEqual(output_DA.metadata[model.MD_PIXEL_SIZE], (2e-06, 4e-05, 1e-06))
        # check if the metadata contains the z-stack mean position
        self.assertAlmostEqual(output_DA.metadata[model.MD_POS][-1], statistics.mean(self.z_list))

    def test_assemble_zcube_3d(self):
        """
        Verify that using a 3D shaped image DA's in a list will fail the
        assembly of a ZCube and see if a ValueError exception is raised.
        """
        # adjust the size for 3D DA
        size = (10, 700, 700)

        img_list = []

        # create a list of DA
        for x in range(len(self.z_list)):
            img_list.append(model.DataArray(numpy.random.random_integers(0, 220, size), self.md))

        with self.assertRaises(ValueError):
            img.assembleZCube(img_list, self.z_list)

    def test_assemble_zcube_z_order(self):
        """
        Verify that using a 3D shaped image DA's in a list will return a
        different ZCube assembly if the order of the images is changed.
        See if the differences with a list generated before and after reordering are picked up.
        """
        img_list = []
        z_list = [0.1, 0.2, 0.3, 0.4, 0.5]

        # create a list of DA
        for z in z_list:
            img_list.append(model.DataArray(numpy.random.random_integers(0, 220, self.size), self.md))

        output_da_before = img.assembleZCube(img_list, z_list)

        # use a reversed image order to force a different ZCube assembly
        output_da_after = img.assembleZCube(img_list[::-1], z_list)

        # check if the Z dimension has the right size
        self.assertEqual(output_da_before.shape[0], len(z_list))
        self.assertEqual(output_da_after.shape[0], len(z_list))
        # check if the returned DA before order reverse is different after order reverse
        testing.assert_array_not_equal(output_da_before, output_da_after)

        # Passing Z in the reverse order should lead to a cube reversed in Z, with a positive pixel size
        output_rev_z = img.assembleZCube(img_list, z_list[::-1])
        self.assertGreater(output_rev_z.metadata[model.MD_PIXEL_SIZE][2], 0)
        numpy.testing.assert_array_equal(output_da_after, output_rev_z)


class TestFloodFill(unittest.TestCase):

    def test_standard_fill(self):
        """Test the expected use of flood fill"""
        a = numpy.zeros((7, 7), dtype=bool)
        for i in range(min(a.shape)):
            a[i, i] = True
            a[-i - 1, i] = True

        expected_array = numpy.array([
            [1, 0, 0, 0, 0, 0, 1],
            [1, 1, 0, 0, 0, 1, 0],
            [1, 1, 1, 0, 1, 0, 0],
            [1, 1, 1, 1, 0, 0, 0],
            [1, 1, 1, 0, 1, 0, 0],
            [1, 1, 0, 0, 0, 1, 0],
            [1, 0, 0, 0, 0, 0, 1]])

        filled_array = img.apply_flood_fill(a, (3, 0))
        numpy.testing.assert_array_equal(expected_array, filled_array)

    def test_start_on_filled(self):
        """Check that the array stays the same after starting on an already filled position"""
        a = numpy.zeros((7, 7), dtype=int)
        for i in range(min(a.shape)):
            a[i, i] = True
            a[-i - 1, i] = True

        filled_array = img.apply_flood_fill(a, (3, 3))
        numpy.testing.assert_equal(a, filled_array)

    def test_out_of_range(self):
        """Check if the function returns an error when start is out of range"""
        a = numpy.zeros((2, 3))

        with self.assertRaises(ValueError):
            img.apply_flood_fill(a, (2, 4))


if __name__ == "__main__":
    unittest.main()

