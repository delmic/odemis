# -*- coding: utf-8 -*-
'''
Created on 26 Jul 2017

@author: Éric Piel, Philip Winkler

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

import unittest
from odemis.acq.stitching import register, weave, REGISTER_IDENTITY, REGISTER_SHIFT, WEAVER_COLLAGE, WEAVER_MEAN, decompose_image
from odemis import model
from odemis.dataio import tiff
import os
import random
import copy
import numpy

# Find path for test images
path = os.path.relpath("acq/align/test/images", "acq/stitching/test")
imgs = [path + "/Slice69_stretched.tif"]
img = tiff.read_data(imgs[0])[0]


class TestRegister(unittest.TestCase):

    # @unittest.skip("skip")
    def test_real_images_shift(self):
        """
        Test register wrapper function
        """
        img = tiff.read_data(imgs[0])[0]
        num = 2
        o = 0.2
        a = "horizontalZigzag"
        [tiles, pos] = decompose_image(img, o, num, a)

        upd_tiles = register(tiles, method=REGISTER_SHIFT)

        for i in range(len(upd_tiles)):
            calculatedPosition = upd_tiles[i].metadata[model.MD_POS]
            self.assertAlmostEqual(calculatedPosition[0], pos[i][0], places=1)
            self.assertAlmostEqual(calculatedPosition[1], pos[i][1], places=1)

    def test_real_images_identity(self):
        """
        Test register wrapper function
        """
        img = tiff.read_data(imgs[0])[0]
        num = 2
        o = 0.2
        a = "horizontalZigzag"
        [tiles, pos] = decompose_image(img, o, num, a, False)

        upd_tiles = register(tiles, method=REGISTER_IDENTITY)

        for i in range(len(upd_tiles)):
            calculatedPosition = upd_tiles[i].metadata[model.MD_POS]
            self.assertAlmostEqual(calculatedPosition[0], pos[i][0], places=1)
            self.assertAlmostEqual(calculatedPosition[1], pos[i][1], places=1)

    # @unittest.skip("skip")
    def test_dep_tiles(self):
        """
        Test register wrapper function, when dependent tiles are present
        """
        # Test on 3 layers of the same image create by decompose_image
        img = tiff.read_data(imgs[0])[0]
        num = 3
        o = 0.2
        a = "horizontalZigzag"
        [tiles, pos] = decompose_image(img, o, num, a)

        all_tiles = []
        for i in range(len(pos)):
            all_tiles.append((tiles[i], tiles[i], tiles[i]))
        all_tiles_new = register(all_tiles)

        for i in range(len(pos)):
            tile_pos = all_tiles_new[i][0].metadata[model.MD_POS]
            dep_pos = (all_tiles_new[i][1].metadata[model.MD_POS],
                       all_tiles_new[i][2].metadata[model.MD_POS])

            diff1 = abs(tile_pos[0] - pos[i][0])
            diff2 = abs(tile_pos[1] - pos[i][1])
            # allow difference of 1% of tile
            px_size = tiles[i].metadata[model.MD_PIXEL_SIZE]
            margin = 0.01 * tiles[i].shape[0] * px_size[0]
            self.assertLessEqual(diff1, margin,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[0], pos[i][0]))
            self.assertLessEqual(diff2, margin,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[1], pos[i][1]))

            for j in range(2):
                diff1 = abs(dep_pos[j][0] - pos[i][0])
                self.assertLessEqual(diff1, margin,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_pos[j][0], pos[i][0]))

                diff2 = abs(dep_pos[j][1] - pos[i][1])
                self.assertLessEqual(diff2, margin,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_pos[j][1], pos[i][1]))

        # Test with shifted dependent tiles
        [tiles, pos] = decompose_image(img, o, num, a)

        # Add shift
        dep_tiles = copy.deepcopy(tiles)
        rnd1 = [random.randrange(-1000, 1000) for _ in range(len(pos))]
        rnd2 = [random.randrange(-1000, 1000) for _ in range(len(pos))]
        for i in range(len(dep_tiles)):
            p = (dep_tiles[i].metadata[model.MD_POS][0] + rnd1[i] * px_size[0],
                 dep_tiles[i].metadata[model.MD_POS][1] + rnd2[i] * px_size[1])
            dep_tiles[i].metadata[model.MD_POS] = p

        all_tiles = []
        for i in range(len(pos)):
            all_tiles.append((tiles[i], dep_tiles[i], dep_tiles[i]))
        all_tiles_new = register(all_tiles)

        for i in range(len(pos)):
            tile_pos = all_tiles_new[i][0].metadata[model.MD_POS]
            dep_pos = (all_tiles_new[i][1].metadata[model.MD_POS],
                       all_tiles_new[i][2].metadata[model.MD_POS])

            diff1 = abs(tile_pos[0] - pos[i][0])
            diff2 = abs(tile_pos[1] - pos[i][1])
            # allow difference of 1% of tile
            px_size = tiles[i].metadata[model.MD_PIXEL_SIZE]
            margin1 = 0.01 * tiles[i].shape[0] * px_size[0]
            margin2 = 0.01 * tiles[i].shape[1] * px_size[1]
            self.assertLessEqual(diff1, margin1,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[0], pos[i][0]))
            self.assertLessEqual(diff2, margin2,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[1], pos[i][1]))

            for j in range(2):
                diff1 = abs(dep_pos[j][0] - rnd1[i] * px_size[0] - pos[i][0])
                self.assertLessEqual(diff1, margin1,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_pos[j][0], pos[i][0] + rnd1[i] * px_size[0]))

                diff2 = abs(dep_pos[j][1] - rnd2[i] * px_size[1] - pos[i][1])
                self.assertLessEqual(diff2, margin2,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_pos[j][1], pos[i][1] + rnd2[i] * px_size[1]))


class TestWeave(unittest.TestCase):

    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = numpy.zeros((2048, 1937), dtype=numpy.uint16) + 4000
        md = {
            model.MD_SW_VERSION: "1.0-test",
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test",
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        intile = model.DataArray(img12, md)

        outd = weave([intile], WEAVER_COLLAGE)

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

        # Same thing but with a typical SEM data
        img8 = numpy.zeros((256, 356), dtype=numpy.uint8) + 40
        md8 = {
            # tiff doesn't support É (but XML does)
            model.MD_DESCRIPTION: u"test sem",
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
        }
        intile = model.DataArray(img8, md8)

        outd = weave([intile], WEAVER_MEAN)

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

                w = weave(tiles, WEAVER_MEAN)
                sz = len(w)

                im = img[:sz, :sz]

                for i in range(len(img[:sz, :sz])):
                    for j in range(len(img[:sz, :sz])):
                        self.assertLessEqual(
                            abs(int(w[i][j]) - int(im[i][j])), 2)


if __name__ == '__main__':
    unittest.main()
