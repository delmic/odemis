# -*- coding: utf-8 -*-
"""
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
"""

import logging
import math
import os
import random
import re
import time
import unittest
import warnings

import numpy

import odemis
from odemis import model
from odemis.acq.stitching import CollageWeaver, MeanWeaver, CollageWeaverReverse, WEAVER_MEAN, WEAVER_COLLAGE_REVERSE, \
    WEAVER_COLLAGE
from odemis.acq.stitching.test.stitching_test import decompose_image
from odemis.dataio import find_fittest_converter
from odemis.util.img import ensure2DImage

logging.getLogger().setLevel(logging.DEBUG)

# Find path for test images
IMG_PATH = os.path.dirname(odemis.__file__)
IMGS = [IMG_PATH + "/driver/songbird-sim-sem.h5",
        IMG_PATH + "/acq/align/test/images/Slice69_stretched.tif"]


class WeaverBaseTest:
    """Base class for testing the different types of weavers."""

    def setUp(self):
        random.seed(1)  # for reproducibility

    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = numpy.zeros((2048, 1937), dtype=numpy.uint16) + 4000
        md = {
            model.MD_SW_VERSION: "1.0-test",
            model.MD_DESCRIPTION: u"test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
            model.MD_DIMS: "YX",
            model.MD_ROTATION: 0,
        }
        intile = model.DataArray(img12, md)

        if self.weaver_type == WEAVER_COLLAGE:
            weaver = CollageWeaver()
        elif self.weaver_type == WEAVER_COLLAGE_REVERSE:
            weaver = CollageWeaverReverse()
        elif self.weaver_type == WEAVER_MEAN:
            weaver = MeanWeaver()

        weaver.addTile(intile)
        outd = weaver.getFullImage()

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

        # Same thing but with a typical SEM data
        img8 = numpy.zeros((256, 356), dtype=numpy.uint8) + 40
        md8 = {
            model.MD_DESCRIPTION: u"test sem",
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
            model.MD_DIMS: "YX",
            model.MD_ROTATION: 0,
        }
        intile = model.DataArray(img8, md8)

        if self.weaver_type == WEAVER_COLLAGE:
            weaver = CollageWeaver()
        elif self.weaver_type == WEAVER_COLLAGE_REVERSE:
            weaver = CollageWeaverReverse()
        elif self.weaver_type == WEAVER_MEAN:
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
        overlap = [0.4]
        for img in IMGS:
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            img = ensure2DImage(data)
            for n in numTiles:
                for o in overlap:
                    [tiles, _] = decompose_image(
                        img, o, n, "horizontalZigzag", False)
                    if self.weaver_type == WEAVER_COLLAGE:
                        weaver = CollageWeaver()
                    elif self.weaver_type == WEAVER_COLLAGE_REVERSE:
                        weaver = CollageWeaverReverse()
                    elif self.weaver_type == WEAVER_MEAN:
                        weaver = MeanWeaver()

                    for t in tiles:
                        weaver.addTile(t)
                    sz = len(weaver.getFullImage())
                    w = weaver.getFullImage()
                    numpy.testing.assert_allclose(w, img[:sz, :sz], rtol=1)

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

        if self.weaver_type == WEAVER_COLLAGE:
            weaver = CollageWeaver()
        elif self.weaver_type == WEAVER_COLLAGE_REVERSE:
            weaver = CollageWeaverReverse()
        elif self.weaver_type == WEAVER_MEAN:
            weaver = MeanWeaver()

        weaver.addTile(in0)
        weaver.addTile(in1)
        outd = weaver.getFullImage()

        numpy.testing.assert_equal(outd, exp_out)

    def test_rotated_tiles(self):
        """Verify that the correct rotation is set on the weaved image."""
        numTiles = [2, 3, 4]
        overlap = 0.4
        rotation = math.radians(5)
        if self.weaver_type == WEAVER_COLLAGE:
            weaver_class = CollageWeaver
        elif self.weaver_type == WEAVER_COLLAGE_REVERSE:
            weaver_class = CollageWeaverReverse
        elif self.weaver_type == WEAVER_MEAN:
            weaver_class = MeanWeaver

        for img in IMGS:
            conv = find_fittest_converter(img)
            img = conv.read_data(img)[0]
            img = ensure2DImage(img)
            for n in numTiles:
                [tiles, _] = decompose_image(img, overlap, n, "horizontalZigzag", False)
                weaver = weaver_class()

                for t in tiles:
                    t.metadata[model.MD_ROTATION] = rotation
                    weaver.addTile(t)

                # The content of the weaved image will be bad looking, because the rotation is not taken into
                # account when decomposing the image into tiles.
                weaved_img = weaver.getFullImage()
                self.assertEqual(weaved_img.metadata[model.MD_ROTATION], rotation)


class TestCollageWeaver(WeaverBaseTest, unittest.TestCase):

    def setUp(self):
        # Ignore RuntimeWarning: numpy.ndarray size changed, may indicate binary incompatibility.
        # Expected 80 from C header, got 88 from PyObject
        # This warning is not caused by the code explicitly changing the array size but rather
        # by an inconsistency between different versions of NumPy.
        warnings.filterwarnings(
            "ignore", category=RuntimeWarning, message=re.escape("numpy.ndarray size changed")
        )

    @classmethod
    def setUpClass(cls):
        cls.weaver_type = WEAVER_COLLAGE

    def test_brightness_adjust(self):
        img0 = numpy.zeros((100, 100))
        img1 = numpy.zeros((100, 100)) + 0.2

        img0[:, 80:90] = 1
        img1[:, 10:20] = 1.2

        # Tile 1 brightness is 0.2 higher than tile 0 brightness. In the weaved image, both
        # will have the mean value.
        exp_out = numpy.zeros((100, 170)) + 0.1
        exp_out[:, 80:90] = 1.1

        md0 = {
            model.MD_SW_VERSION: "1.0-test",
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

        weaver = CollageWeaver(adjust_brightness=True)
        weaver.addTile(in0)
        weaver.addTile(in1)
        outd = weaver.getFullImage()

        numpy.testing.assert_almost_equal(outd, exp_out, decimal=5)


class TestMeanWeaver(WeaverBaseTest, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.weaver_type = WEAVER_COLLAGE

    def test_gradient(self):
        """
        Test if gradient appears in two images with different constant values
        """

        img0 = numpy.ones((100, 100)) * 256
        img1 = numpy.zeros((100, 100))

        md0 = {
            model.MD_SW_VERSION: "1.0-test",
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

        weaver = MeanWeaver(adjust_brightness=False)
        weaver.addTile(in0)
        weaver.addTile(in1)
        outd = weaver.getFullImage()

        # Visualize overlap mask
        # from PIL import Image
        # Image.fromarray(outd).show()
        #
        # The overlap mask should look something like this:
        # ........................@@@@@@@@@@@@@@@
        # ...........|\-----------@@@@@@@@@@@@@@@
        # ...........||\----------@@@@@@@@@@@@@@@
        # ...........|||\---------@@@@@@@@@@@@@@@
        # ...........||||\--------@@@@@@@@@@@@@@@
        # ...........||||||@@@@@@@@@@@@@@@@@@@@@@
        # ...........||||||@@@@@@@@@@@@@@@@@@@@@@
        # ...........||||||@@@@@@@@@@@@@@@@@@@@@@
        # ...........||||||@@@@@@@@@@@@@@@@@@@@@@
        # ...........||||||@@@@@@@@@@@@@@@@@@@@@@
        # ...........||||/--------@@@@@@@@@@@@@@@
        # ...........|||/---------@@@@@@@@@@@@@@@
        # ...........||/----------@@@@@@@@@@@@@@@
        # ...........|/-----------@@@@@@@@@@@@@@@
        # ........................@@@@@@@@@@@@@@@

        # Test that values in overlapping region decrease from left to right
        o = outd[10:-10, 70:100]  # the gradient is going to be small on the edges, so only check middle part
        for row in o:
            # the gradient can be small, so only test that the left pixel has a lower
            # value than the value of the right pixel
            self.assertLess(row[-1], row[0])


class TestCollageWeaverReverse(WeaverBaseTest, unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.weaver_type = WEAVER_COLLAGE

    def test_foreground_tile(self):
        """
        Test if the overlap region contains part of the first tile, not the second one.
        """

        img0 = numpy.ones((100, 100)) * 256
        img1 = numpy.zeros((100, 100))

        md0 = {
            model.MD_SW_VERSION: "1.0-test",
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

        weaver = CollageWeaverReverse(adjust_brightness=False)
        weaver.addTile(in0)
        weaver.addTile(in1)
        outd = weaver.getFullImage()

        # Test that values in overlapping region are all 256 (as in first tile)
        o = outd[10:-10, 70:100]  # the gradient is going to be small on the edges, so only check middle part
        numpy.testing.assert_equal(o, 256 * numpy.ones((80, 30)))


if __name__ == '__main__':
    unittest.main()
