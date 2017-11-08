# -*- coding: utf-8 -*-
'''
Created on 26 Jul 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

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
from odemis import model
import time
import unittest
import os
import random

from odemis.acq.stitching._weaver import CollageWeaver, MeanWeaver
from odemis.dataio import tiff

from stitching_test import decompose_image

logging.getLogger().setLevel(logging.DEBUG)

# Find path for test images
path = os.path.relpath("acq/align/test/images", "acq/stitching/test")
imgs = [path + "/Slice69_stretched.tif"]
img = tiff.read_data(imgs[0])[0]

# @unittest.skip("skip")


class TestCollageWeaver(unittest.TestCase):

    def setUp(self):
        random.seed(1)

    # @unittest.skip("skip")
    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = numpy.zeros((2048, 1937), dtype=numpy.uint16) + 4000
        md = {
            model.MD_SW_VERSION: "1.0-test",
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        intile = model.DataArray(img12, md)

        weaver = CollageWeaver()
        weaver.addTile(intile)
        outd = weaver.getFullImage()

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

        # Same thing but with a typical SEM data
        img8 = numpy.zeros((256, 356), dtype=numpy.uint8) + 40
        md8 = {
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test sem",
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
        }
        intile = model.DataArray(img8, md8)

        weaver = MeanWeaver()
        weaver.addTile(intile)
        outd = weaver.getFullImage()

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

    def test_no_seam(self):
        """
        Test on decomposed image
        """

        numTiles = [2, 3, 4]
        overlap = [0.2]

        for n in numTiles:
            for o in overlap:
                [tiles, _] = decompose_image(
                    img, o, n, "horizontalZigzag", False)

                weaver = CollageWeaver()
                for t in tiles:
                    weaver.addTile(t)

                sz = len(weaver.getFullImage())
                w = weaver.getFullImage()
                im = img[:sz, :sz]

                for i in range(len(img[:sz, :sz])):
                    for j in range(len(img[:sz, :sz])):
                        self.assertLessEqual(
                            abs(w[i][j] - im[i][j]), 1, "%s %s" % (o, n))


# @unittest.skip("skip")
class TestMeanWeaver(unittest.TestCase):

    def setUp(self):
        random.seed(1)

    # @unittest.skip("skip")
    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = numpy.zeros((2048, 1937), dtype=numpy.uint16) + 4000
        md = {
            model.MD_SW_VERSION: "1.0-test",
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        intile = model.DataArray(img12, md)

        weaver = MeanWeaver()
        weaver.addTile(intile)
        outd = weaver.getFullImage()

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

        # Same thing but with a typical SEM data
        img8 = numpy.zeros((256, 356), dtype=numpy.uint8) + 40
        md8 = {
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test sem",
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
        }
        intile = model.DataArray(img8, md8)

        weaver = MeanWeaver()
        weaver.addTile(intile)
        outd = weaver.getFullImage()

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

    def test_real_perfect_overlap(self):
        """
        Test on decomposed image
        """

        numTiles = [2, 3, 4]
        overlap = [0.2]

        for n in numTiles:
            for o in overlap:
                [tiles, _] = decompose_image(
                    img, o, n, "horizontalZigzag", False)

                weaver = MeanWeaver()
                for t in tiles:
                    weaver.addTile(t)

                sz = len(weaver.getFullImage())
                w = weaver.getFullImage()
                im = img[:sz, :sz]

                for i in range(len(img[:sz, :sz])):
                    for j in range(len(img[:sz, :sz][0])):
                        self.assertLessEqual(
                            abs(int(w[i][j]) - int(im[i][j])), 2, "%s %s" % (w[i][j], im[i][j]))

    def test_synthetic_perfect_overlap(self):
        """
        Test on synthetic image with exactly matching overlap, weaved image should be equal to original image
        """

        img0 = numpy.zeros((100, 100))
        img1 = numpy.zeros((100, 100))

        img0[:, 80:90] = 1
        img1[:, 10:20] = 1

        exp_out = numpy.zeros((100, 170))
        exp_out[:, 80:90] = 1

        md0 = {
            model.MD_SW_VERSION: "1.0-test",
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1, 1),  # m/px
            model.MD_POS: (50, 50),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        in0 = model.DataArray(img0, md0)

        md1 = {
            model.MD_SW_VERSION: "1.0-test",
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1, 1),  # m/px
            model.MD_POS: (120, 50),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        in1 = model.DataArray(img1, md1)

        weaver = MeanWeaver()
        weaver.addTile(in0)
        weaver.addTile(in1)
        outd = weaver.getFullImage()

        self.assertTrue((numpy.array(outd) == exp_out).all())

    def test_gradient(self):
        """
        Test if gradient appears in two images with different constant values
        """

        img0 = numpy.ones((100, 100)) * 256
        img1 = numpy.zeros((100, 100))

        md0 = {
            model.MD_SW_VERSION: "1.0-test",
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1, 1),  # m/px
            model.MD_POS: (50, 50),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        in0 = model.DataArray(img0, md0)

        md1 = {
            model.MD_SW_VERSION: "1.0-test",
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1, 1),  # m/px
            model.MD_POS: (120, 50),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        in1 = model.DataArray(img1, md1)

        weaver = MeanWeaver()
        weaver.addTile(in0)
        weaver.addTile(in1)
        outd = weaver.getFullImage()

        # Visualize overlap mask
        #from PIL import Image
        # Image.fromarray(outd).show()

        # Test that values in overlapping region decrease from left to right
        col_prev = 100000
        o = outd[:, 70:100]
        for row in o:
            col_prev = 10000
            for col in row:
                self.assertLess(col, col_prev)
                col_prev = col


"""
# Visualize weaved result after bad stitching
img = path + "/g_009_cropped.tif"
img = tiff.read_data(img)[0]
numTiles = 10
overlap = 0.2
[tiles, pos] = decompose_image(
    img, overlap, numTiles, "horizontalZigzag")

from odemis.acq.stitching import register, REGISTER_SHIFT
updatedTiles = register(tiles, REGISTER_SHIFT)

weaver = MeanWeaver()

for i in range(len(updatedTiles)):
    weaver.addTile(updatedTiles[i])

image = weaver.getFullImage()

from PIL import Image
im = Image.fromarray(image)
im.show()

weaver = CollageWeaver()

for i in range(len(updatedTiles)):
    weaver.addTile(updatedTiles[i])

image = weaver.getFullImage()

from PIL import Image
im = Image.fromarray(image)
im.show()
"""

if __name__ == '__main__':
    unittest.main()
