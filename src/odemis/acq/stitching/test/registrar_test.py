# -*- coding: utf-8 -*-
'''
Created on 26 Jul 2017

@author: Éric Piel, Philip Winkler

Copyright © 2017 Éric Piel, Philip Winkler, Delmic

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
import logging
from odemis import model
import numpy
import unittest
import random
import copy
import os

from odemis.acq.stitching import IdentityRegistrar, ShiftRegistrar
from odemis.dataio import hdf5
import odemis

from stitching_test import decompose_image

logging.getLogger().setLevel(logging.DEBUG)

# Find path for test images
IMG_PATH = os.path.dirname(odemis.__file__) + "/driver/"
IMGS = [IMG_PATH + "songbird-sim-sem.h5"]  # IMG_PATH + "songbird-sim-ccd.h5" fails. It is
# decomposed into tiles that are almost entirely homogeneously black, so stitching does not
# work particularly well.


# @unittest.skip("skip")
class TestIdentityRegistrar(unittest.TestCase):

    def setUp(self):
        random.seed(1)

    def test_synthetic_images(self):
        """ Test on synthetic images with rectangles """

        # Image with large rectangle, different shifts, datatypes
        # and tile sizes
        shifts = [(0, 0), (10, 10), (0, 10), (8, 5), (-6, 5)]
        t_sizes = [(500, 500), (300, 400), (500, 200)]
        dtypes = [numpy.int8, numpy.int16]

        for shift in shifts:
            for size in t_sizes:
                for dtype in dtypes:
                    img = 255 * numpy.ones(size, dtype=dtype)

                    # Rectangle
                    l = int(0.9 * size[1])
                    h = int(0.9 * size[0])
                    idx1 = int(0.05 * size[0])
                    idx2 = int(0.05 * size[1])
                    img[idx1:idx1 + h, idx2:idx2 + l] = \
                            numpy.zeros((h, l), dtype=dtype)

                    # Crop two tiles with different shifts
                    tile1 = img[0.3 * size[0]:0.6 * size[0], 0:0.6 * size[1]]
                    tile2 = img[0.5 * size[0] + shift[0]:0.8 * size[0] + shift[0],
                                shift[1]:0.6 * size[1] + shift[1]]

                    px_size = (1e-6, 2e-5)
                    md1 = {
                        model.MD_POS: (1e-3, -30e-3),
                        model.MD_PIXEL_SIZE: px_size
                    }
                    md2 = {
                        model.MD_POS: (1e-3 + 0.6 * size[0] * px_size[0], -30e-3),
                        model.MD_PIXEL_SIZE: px_size
                    }

                    tiles = [model.DataArray(
                        tile1, md1), model.DataArray(tile2, md2)]
                    registrar = IdentityRegistrar()
                    for t in tiles:
                        registrar.addTile(t)

                    diff = numpy.subtract(registrar.getPositions()[
                                          0][0], md1[model.MD_POS])
                    # one pixel difference allowed
                    self.assertLessEqual(diff[0] * px_size[0], 1)
                    self.assertLessEqual(diff[1] * px_size[1], 1)

                    # Shift is ignored by IdentityRegistrar
                    diff = (registrar.getPositions()[0][1][0] - (md2[model.MD_POS][0]),
                            registrar.getPositions()[0][1][1] - (md2[model.MD_POS][1]))
                    # one pixel difference allowed
                    self.assertLessEqual(diff[0], 1)
                    self.assertLessEqual(diff[1], 1)

    def test_white_image(self):
        """ Position should be left as-is in case of white images """
        tile1 = 255 * numpy.ones((200, 200))
        size_m = 200 * 1.3e-6

        md1 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
        }

        tile2 = 255 * numpy.ones((200, 200))
        md2 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3 + size_m / 2 - size_m * 0.2),  # m
        }

        tile3 = 255 * numpy.ones((200, 200))
        md3 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3 + size_m / 2 - size_m * 0.2, 30e-3),  # m
        }

        tile4 = 255 * numpy.ones((200, 200))
        md4 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            # m
            model.MD_POS: (10e-3 + size_m / 2 - size_m * 0.2, 30e-3 + size_m / 2 - size_m * 0.2),
        }

        pos = [md1[model.MD_POS], md2[model.MD_POS],
               md3[model.MD_POS], md4[model.MD_POS]]

        tiles = [model.DataArray(tile1, md1), model.DataArray(tile2, md2),
                 model.DataArray(tile3, md3), model.DataArray(tile4, md4)]

        registrar = IdentityRegistrar()
        calculatedPositions, _ = registrar.getPositions()
        for t, cp, p in zip(tiles, calculatedPositions, pos):
            registrar.addTile(t)
            diff1 = abs(cp[0] - p[0])
            diff2 = abs(cp[1] - p[1])

            # should return initial position value, small error of 0.01 allowed
            self.assertLessEqual(diff1, 1.3e-6)
            self.assertLessEqual(diff2, 1.3e-6)

    def test_shift_real(self):
        """ Test on decomposed image with known shift """
        numTiles = [2, 3]
        overlap = [0.2, 0.3, 0.4]
        acq = ["horizontalLines", "verticalLines", "horizontalZigzag"]

        for img in IMGS:
            data = numpy.array(hdf5.read_data(img)[0])
            for num in numTiles:
                for o in overlap:
                    for a in acq:
                        [tiles, pos] = decompose_image(data, o, num, a, False)
                        registrar = IdentityRegistrar()
                        for i in range(len(pos)):
                            registrar.addTile(tiles[i])
                            calculatedPositions = registrar.getPositions()[0]
                            diff1 = abs(calculatedPositions[i][0] - pos[i][0])
                            diff2 = abs(calculatedPositions[i][1] - pos[i][1])
                            # allow difference of 10% of overlap
                            px_size = tiles[i].metadata[model.MD_PIXEL_SIZE]
                            # allow error of 1% of tileSize
                            margin1 = 0.01 * tiles[i].shape[0] * px_size[0]
                            margin2 = 0.01 * tiles[i].shape[1] * px_size[1]

                            self.assertLessEqual(diff1, margin1,
                                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                                 " %f != %f" % (calculatedPositions[i][0], pos[i][0]))
                            self.assertLessEqual(diff2, margin2,
                                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                                 " %f != %f" % (calculatedPositions[i][1], pos[i][1]))

    def test_shift_real_manual(self):
        """ Test case not generated by decompose.py file and manually cropped """

        img = hdf5.read_data(IMGS[0])[0]
        cropped1 = img[0:400, 0:400]
        cropped2 = img[4:404, 322:722]

        registrar = IdentityRegistrar()
        tile1 = model.DataArray(numpy.array(cropped1), {
            model.MD_PIXEL_SIZE: [1 / 20, 1 / 20],  # m/px
            model.MD_POS: (200 / 20, img.shape[1] / 20 - 200 / 20),  # m
        })
        tile2 = model.DataArray(numpy.array(cropped2), {
            model.MD_PIXEL_SIZE: [1 / 20, 1 / 20],  # m/px
            model.MD_POS: (520 / 20, img.shape[1] / 20 - 200 / 20),  # m
        })
        registrar.addTile(tile1)
        registrar.addTile(tile2)
        calculatedPositions = registrar.getPositions()[0]
        self.assertAlmostEqual(calculatedPositions[1][0], 520 / 20, places=1)
        self.assertAlmostEqual(
            calculatedPositions[1][1], img.shape[1] / 20 - 200 / 20, places=1)

    def test_dependent_tiles(self):
        """ Tests functionality for dependent tiles """

        # Test on 3 layers of the same image create by decompose_image
        img = hdf5.read_data(IMGS[0])[0][0][0][0]
        num = 2
        o = 0.2
        a = "horizontalZigzag"
        [tiles, pos] = decompose_image(img, o, num, a, False)
        registrar = IdentityRegistrar()
        for i in range(len(pos)):
            registrar.addTile(tiles[i], (tiles[i], tiles[i]))
            tile_pos, dep_tile_pos = registrar.getPositions()

            diff1 = abs(tile_pos[i][0] - pos[i][0])
            diff2 = abs(tile_pos[i][1] - pos[i][1])
            # allow difference of 10% of overlap
            px_size = tiles[i].metadata[model.MD_PIXEL_SIZE]
            margin = 0.01 * tiles[i].shape[0] * px_size[0]
            self.assertLessEqual(diff1, margin,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[i][0], pos[i][0]))
            self.assertLessEqual(diff2, margin,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[i][1], pos[i][1]))

            for j in range(len(dep_tile_pos[0])):
                diff1 = abs(dep_tile_pos[i][j][0] - pos[i][0])
                self.assertLessEqual(diff1, margin,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_tile_pos[i][j][0], pos[i][0]))

                diff2 = abs(dep_tile_pos[i][j][1] - pos[i][1])
                self.assertLessEqual(diff2, margin,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_tile_pos[i][j][1], pos[i][1]))

        # Test with shifted dependent tiles
        [tiles, pos] = decompose_image(img, o, num, a, False)
        registrar = IdentityRegistrar()

        # Add shift
        dep_tiles = copy.deepcopy(tiles)
        rnd1 = [random.randrange(-20, 20) for _ in range(len(pos))]
        rnd2 = [random.randrange(-20, 20) for _ in range(len(pos))]
        for i in range(len(dep_tiles)):
            p = (dep_tiles[i].metadata[model.MD_POS][0] + rnd1[i],
                 dep_tiles[i].metadata[model.MD_POS][1] + rnd2[i])
            dep_tiles[i].metadata[model.MD_POS] = p

        for i in range(len(pos)):
            registrar.addTile(tiles[i], (dep_tiles[i], dep_tiles[i]))
            tile_pos, dep_tile_pos = registrar.getPositions()

            diff1 = abs(tile_pos[i][0] - pos[i][0])
            # allow difference of 10% of overlap
            margin = 0.3 * o * tiles[i].shape[0]
            self.assertLessEqual(diff1, margin,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[i][0], pos[i][0]))

            for j in range(2):
                diff1 = abs(dep_tile_pos[i][j][0] - pos[i][0])
                self.assertLessEqual(diff1, margin,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_tile_pos[i][j][0], pos[i][0] + rnd1[i]))

                diff2 = abs(dep_tile_pos[i][j][1] - pos[i][1])
                self.assertLessEqual(diff2, margin,
                                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                     " %f != %f" % (dep_tile_pos[i][j][1], pos[i][1] + rnd2[i]))


class TestShiftRegistrar(unittest.TestCase):
    """
    Tests ShiftRegistrar on synthetic and real images (simulated with decompose_image function)
    with known positions
    """

    def setUp(self):
        random.seed(1)

    def test_synthetic_images(self):
        """ Test on synthetic images with rectangles """

        # Image with large rectangle, different shifts, datatypes and tile
        # sizes
        shifts = [(0, 0), (10, 10), (0, 10), (8, 5), (-6, 5)]
        t_sizes = [(500, 500), (300, 400), (500, 200)]
        dtypes = [numpy.int8, numpy.int16]

        for shift in shifts:
            for size in t_sizes:
                for dtype in dtypes:
                    img = 255 * numpy.ones(size, dtype=dtype)

                    # Rectangle
                    l = int(0.9 * size[1])
                    h = int(0.9 * size[0])
                    idx1 = int(0.05 * size[0])
                    idx2 = int(0.05 * size[1])
                    img[idx1:idx1 + h, idx2:idx2 + l] = \
                            numpy.zeros((h, l), dtype=dtype)

                    # Crop two tiles with different shifts
                    tile1 = img[:0.3 * size[0], 0:0.6 * size[1]]
                    tile2 = img[0.2 * size[0] + shift[0]:0.5 * size[0] +
                                shift[0], shift[1]:0.6 * size[1] + shift[1]]

                    px_size = (1e-6, 2e-5)
                    md1 = {
                        model.MD_POS: (0.15 * size[0] * px_size[0], 30e-3),
                        model.MD_PIXEL_SIZE: px_size
                    }
                    md2 = {
                        model.MD_POS: (0.35 * size[0] * px_size[0], 30e-3),
                        model.MD_PIXEL_SIZE: px_size
                    }

                    tiles = [model.DataArray(tile1, md1),
                             model.DataArray(tile2, md2)]
                    registrar = ShiftRegistrar()
                    for t in tiles:
                        registrar.addTile(t)

                    diff = numpy.subtract(registrar.getPositions()[0][0],
                                          md1[model.MD_POS])
                    # one pixel difference allowed
                    self.assertLessEqual(diff[0], 1)
                    self.assertLessEqual(diff[1], 1)

                    diff = (registrar.getPositions()[0][1][0] - (md2[model.MD_POS][0] + shift[0] * px_size[0]),
                            registrar.getPositions()[0][1][1] - (md2[model.MD_POS][1] + shift[1] * px_size[1]))
                    # one pixel difference allowed
                    self.assertLessEqual(diff[0], 1)
                    self.assertLessEqual(diff[1], 1)

    def test_white_image(self):
        """ Position should be left as-is in case of white images """
        tile1 = 255 * numpy.ones((200, 200))
        size_m = 200 * 1.3e-6

        md1 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 300e-3),  # m
        }

        tile2 = 255 * numpy.ones((200, 200))
        md2 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 300e-3 - size_m + size_m * 0.2),  # m
        }

        tile3 = 255 * numpy.ones((200, 200))
        md3 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3 + size_m - size_m * 0.2, 300e-3),  # m
        }

        tile4 = 255 * numpy.ones((200, 200))
        md4 = {
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            # m
            model.MD_POS: (10e-3 + size_m - size_m * 0.2, 300e-3 - size_m + size_m * 0.2),
        }

        pos = [md1[model.MD_POS], md2[model.MD_POS],
               md3[model.MD_POS], md4[model.MD_POS]]

        tiles = [model.DataArray(tile1, md1), model.DataArray(tile2, md2),
                 model.DataArray(tile3, md3), model.DataArray(tile4, md4)]

        registrar = ShiftRegistrar()
        for i in range(len(tiles)):
            registrar.addTile(tiles[i])
            calculatedPositions = registrar.getPositions()[0]
            diff1 = abs(calculatedPositions[i][0] - pos[i][0])
            diff2 = abs(calculatedPositions[i][1] - pos[i][1])

            # should return initial position value, small error of 0.01 allowed
            self.assertLessEqual(diff1, 1.3e-6)
            self.assertLessEqual(diff2, 1.3e-6)

    def test_shift_real(self):
        """ Test on decomposed image with known shift """
        numTiles = [2, 3]
        overlap = [0.5, 0.3, 0.2]
        acq = ["horizontalLines", "horizontalZigzag", "verticalLines"]

        for img in IMGS:
            data = numpy.array(hdf5.read_data(img)[0][0][0][0])
            for num in numTiles:
                for o in overlap:
                    for a in acq:
                        [tiles, pos] = decompose_image(data, o, num, a)
                        registrar = ShiftRegistrar()
                        for i in range(len(pos)):
                            registrar.addTile(tiles[i])
                            calculatedPositions = registrar.getPositions()[0]
                            diff1 = abs(calculatedPositions[i][0] - pos[i][0])
                            diff2 = abs(calculatedPositions[i][1] - pos[i][1])
                            # allow difference of 10% of overlap
                            px_size = tiles[i].metadata[model.MD_PIXEL_SIZE]
                            # allow error of 3% of tileSize
                            margin1 = 0.03 * tiles[i].shape[0] * px_size[0]
                            margin2 = 0.03 * tiles[i].shape[1] * px_size[1]

                            self.assertLessEqual(diff1, margin1,
                                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                                 " %f != %f" % (calculatedPositions[i][0], pos[i][0]))
                            self.assertLessEqual(diff2, margin2,
                                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                                 " %f != %f" % (calculatedPositions[i][1], pos[i][1]))

    def test_shift_real_manual(self):
        """ Test case not generated by decompose.py file and manually cropped """

        img = hdf5.read_data(IMGS[0])[0][0][0][0]
        cropped1 = img[0:400, 0:400]
        cropped2 = img[4:404, 322:722]

        registrar = ShiftRegistrar()
        tile1 = model.DataArray(numpy.array(cropped1), {
            model.MD_PIXEL_SIZE: [1 / 20, 1 / 20],  # m/px
            model.MD_POS: (200 / 20, img.shape[1] / 20 - 200 / 20),  # m
        })
        tile2 = model.DataArray(numpy.array(cropped2), {
            model.MD_PIXEL_SIZE: [1 / 20, 1 / 20],  # m/px
            model.MD_POS: (520 / 20, img.shape[1] / 20 - 200 / 20),  # m
        })
        registrar.addTile(tile1)
        registrar.addTile(tile2)
        calculatedPositions = registrar.getPositions()[0]
        diff1 = calculatedPositions[1][0] - 522 / 20
        self.assertLessEqual(diff1, 1 / 20)
        diff2 = calculatedPositions[1][1] - img.shape[1] / 20 - 204 / 20
        self.assertLessEqual(diff2, 1 / 20)

    def test_dependent_tiles(self):
        """ Tests functionality for dependent tiles """

        # Test on 3 layers of the same image create by decompose_image
        img = hdf5.read_data(IMGS[0])[0][0][0][0]
        num = 4
        o = 0.2
        a = "horizontalZigzag"
        [tiles, pos] = decompose_image(img, o, num, a)
        registrar = ShiftRegistrar()
        for i in range(len(pos)):
            registrar.addTile(tiles[i], (tiles[i], tiles[i]))
            tile_pos, dep_tile_pos = registrar.getPositions()

            diff1 = abs(tile_pos[i][0] - pos[i][0])
            diff2 = abs(tile_pos[i][1] - pos[i][1])
            # allow difference of 10% of overlap
            px_size = tiles[i].metadata[model.MD_PIXEL_SIZE]
            margin1 = int(1 + 0.01 * tiles[i].shape[0]) * px_size[0]
            margin2 = int(1 + 0.01 * tiles[i].shape[1]) * px_size[1]
            self.assertLessEqual(diff1, margin1,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[i][0], pos[i][0]))
            self.assertLessEqual(diff2, margin2,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[i][1], pos[i][1]))

        for p, tile in zip(pos, dep_tile_pos):
            for dep_tile in tile:
                diff1 = abs(dep_tile[0] - p[0])
                self.assertLessEqual(diff1, margin1,
                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                     " %f != %f" % (dep_tile[0], p[0]))

                diff2 = abs(dep_tile[1] - p[1])
                self.assertLessEqual(diff2, margin2,
                     "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                     " %f != %f" % (dep_tile[1], p[1]))

        # Test with shifted dependent tiles
        [tiles, pos] = decompose_image(img, o, num, a)

        registrar = ShiftRegistrar()

        # Add different shift for every dependent tile
        dep_tiles = copy.deepcopy(tiles)
        # x-shift for each dependent tile in px
        rnd1 = [random.randrange(-1000, 1000) for _ in range(len(pos))]
        # y-shift for each dependent tile in px
        rnd2 = [random.randrange(-1000, 1000) for _ in range(len(pos))]
        # Change metadata of dependent tiles
        for i in range(len(dep_tiles)):
            p = (dep_tiles[i].metadata[model.MD_POS][0] + rnd1[i] * px_size[0],
                 dep_tiles[i].metadata[model.MD_POS][1] + rnd2[i] * px_size[1])
            dep_tiles[i].metadata[model.MD_POS] = p

        for i in range(len(pos)):
            # Register tiles
            # 2 layers of dependent tiles with the same pos
            registrar.addTile(tiles[i], (dep_tiles[i], dep_tiles[i]))
            tile_pos, dep_tile_pos = registrar.getPositions()

            # Test main tile
            diff1 = abs(tile_pos[i][0] - pos[i][0])
            diff2 = abs(tile_pos[i][1] - pos[i][1])

            # allow difference of 10% of overlap
            margin1 = 0.1 * tiles[i].shape[0] * px_size[0]
            margin2 = 0.1 * tiles[i].shape[1] * px_size[1]
            self.assertLessEqual(diff1, margin1,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[i][0], pos[i][0]))
            self.assertLessEqual(diff2, margin2,
                                 "Failed for %s tiles, %s overlap and %s method," % (num, o, a) +
                                 " %f != %f" % (tile_pos[i][0], pos[i][0]))

        for p, tile, r1, r2 in zip(tile_pos, dep_tile_pos, rnd1, rnd2):
            for dep_tile in tile:
                self.assertAlmostEqual(dep_tile[0], p[0] + r1 * px_size[0])
                self.assertAlmostEqual(dep_tile[1], p[1] + r2 * px_size[1])


if __name__ == '__main__':
    unittest.main()
