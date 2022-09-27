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

import logging
from odemis import model
import numpy
import unittest
import random
import copy
import os
import itertools

from odemis.acq.stitching import IdentityRegistrar, ShiftRegistrar, GlobalShiftRegistrar
from odemis.dataio import find_fittest_converter
from odemis.util.img import ensure2DImage
import odemis

from stitching_test import decompose_image

logging.getLogger().setLevel(logging.DEBUG)

# Find path for test images
IMG_PATH = os.path.dirname(odemis.__file__)
IMGS = [IMG_PATH + "/driver/songbird-sim-sem.h5",
        IMG_PATH + "/acq/align/test/images/Slice69_stretched.tif"]
# "simsem-fake-output.h5" and "songbird-sim-ccd.h5" fail. (simsem-fake-output works with global alignment)


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
                    tsize = (int(0.3 * size[0]), int(0.6 * size[1]))
                    start = (int(0.3 * size[0]), 0)
                    end = (start[0] + tsize[0], start[1] + tsize[1])
                    tile1 = img[start[0]:end[0], start[1]:end[1]]

                    start = numpy.add((int(0.5 * size[0]), 0), shift)
                    end = (start[0] + tsize[0], start[1] + tsize[1])
                    tile2 = img[start[0]:end[0], start[1]:end[1]]

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
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            data = ensure2DImage(data)
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

        for img in IMGS:
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            img = ensure2DImage(data)
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
        for img in IMGS:
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            img = ensure2DImage(data)
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
                    tsize = (int(0.3 * size[0]), int(0.6 * size[1]))
                    start = (0, 0)
                    end = (start[0] + tsize[0], start[1] + tsize[1])
                    tile1 = img[start[0]:end[0], start[1]:end[1]]

                    start = numpy.add((int(0.2 * size[0]), 0), shift)
                    end = (start[0] + tsize[0], start[1] + tsize[1])
                    tile2 = img[start[0]:end[0], start[1]:end[1]]

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
        numTiles = [2, 3, 4]
        overlap = [0.5, 0.4, 0.3, 0.2]
        acq = ["horizontalLines", "horizontalZigzag", "verticalLines"]

        for img, num, o, a in itertools.product(IMGS, numTiles, overlap, acq):
            _, img_name = os.path.split(img)
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            data = ensure2DImage(data)

            # Create artificial tiled image
            [tiles, real_pos] = decompose_image(data, o, num, a)
            px_size = tiles[0].metadata[model.MD_PIXEL_SIZE]
            registrar = ShiftRegistrar()

            # Register tiles
            for tile in tiles:
                registrar.addTile(tile)
            # Compare positions to real positions, allow 5 px offset
            registered_pos = registrar.getPositions()[0]
            diff = numpy.absolute(numpy.subtract(registered_pos, real_pos))
            allowed_px_offset = numpy.repeat(numpy.multiply(px_size, 5), len(diff))
            numpy.testing.assert_array_less(diff.flatten(), allowed_px_offset.flatten(),
                        "Position %s pxs off for image '%s', " % (max(diff.flatten()) / px_size[0], img_name) +
                        "%s x %s tiles, %s ovlp, %s method." % (num, num, o, a))

    def test_shift_real_manual(self):
        """ Test case not generated by decompose.py file and manually cropped """

        for img in IMGS:
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            img = ensure2DImage(data)
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
        for img in IMGS:
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            img = ensure2DImage(data)
            num = 4
            o = 0.3  # fails for 0.2
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

                # Check if position is not completely wrong. The margins are given
                # by the extreme value calculation in the registrar and provide
                # a very generous upper limit for the error that should never be exceeded
                # because of the fallback method.
                # Unfortunately, many tests don't pass stricter limits yet.
                margin1 = px_size[0] * 5
                margin2 = px_size[1] * 5

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
                margin1 = px_size[0] * 5
                margin2 = px_size[1] * 5
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


class TestGlobalShiftRegistrar(unittest.TestCase):
    """
    Tests GlobalShiftRegistrar on synthetic and real images (simulated with decompose_image function)
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
                    tsize = (int(0.3 * size[0]), int(0.6 * size[1]))
                    start = (0, 0)
                    end = (start[0] + tsize[0], start[1] + tsize[1])
                    tile1 = img[start[0]:end[0], start[1]:end[1]]

                    start = numpy.add((int(0.2 * size[0]), 0), shift)
                    end = (start[0] + tsize[0], start[1] + tsize[1])
                    tile2 = img[start[0]:end[0], start[1]:end[1]]

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
                    registrar = GlobalShiftRegistrar()
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

        registrar = GlobalShiftRegistrar()
        for i in range(len(tiles)):
            registrar.addTile(tiles[i])

        for i in range(len(tiles)):
            calculatedPositions = registrar.getPositions()[0]
            diff1 = abs(calculatedPositions[i][0] - pos[i][0])
            diff2 = abs(calculatedPositions[i][1] - pos[i][1])

            # should return initial position value, small error of 0.01 allowed
            self.assertLessEqual(diff1, 1.3e-6)
            self.assertLessEqual(diff2, 1.3e-6)

    def test_shift_real(self):
        """ Test on decomposed image with known shift """
        numTiles = [2, 3, 4]
        overlap = [0.5, 0.4, 0.3, 0.2]
        acq = ["horizontalLines", "horizontalZigzag", "verticalLines"]

        for img, num, o, a in itertools.product(IMGS, numTiles, overlap, acq):
            _, img_name = os.path.split(img)
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            data = ensure2DImage(data)

            # Create artificial tiled image
            [tiles, real_pos] = decompose_image(data, o, num, a)
            px_size = tiles[0].metadata[model.MD_PIXEL_SIZE]
            registrar = GlobalShiftRegistrar()

            # Register tiles
            for tile in tiles:
                registrar.addTile(tile)
            # Compare positions to real positions, allow 5 px offset
            registered_pos = registrar.getPositions()[0]
            diff = numpy.absolute(numpy.subtract(registered_pos, real_pos))
            allowed_px_offset = numpy.repeat(numpy.multiply(px_size, 5), len(diff))
            numpy.testing.assert_array_less(diff.flatten(), allowed_px_offset.flatten(),
                        "Position %s pxs off for image '%s', " % (max(diff.flatten()) / px_size[0], img_name) +
                        "%s x %s tiles, %s ovlp, %s method." % (num, num, o, a))

    def test_shift_real_manual(self):
        """ Test case not generated by decompose.py file and manually cropped """

        for img in IMGS:
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            img = ensure2DImage(data)
            cropped1 = img[0:400, 0:400]
            cropped2 = img[4:404, 322:722]

            registrar = GlobalShiftRegistrar()
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
        for img in IMGS:
            conv = find_fittest_converter(img)
            data = conv.read_data(img)[0]
            img = ensure2DImage(data)
            num = 4
            o = 0.3  # fails for 0.2
            a = "horizontalZigzag"
            [tiles, pos] = decompose_image(img, o, num, a)
            registrar = GlobalShiftRegistrar()
            for i in range(len(pos)):
                registrar.addTile(tiles[i], (tiles[i], tiles[i]))
            tile_pos, dep_tile_pos = registrar.getPositions()
            for i in range(len(pos)):
                diff1 = abs(tile_pos[i][0] - pos[i][0])
                diff2 = abs(tile_pos[i][1] - pos[i][1])
                # allow difference of 10% of overlap
                px_size = tiles[i].metadata[model.MD_PIXEL_SIZE]

                # Check if position is not completely wrong. The margins are given
                # by the extreme value calculation in the registrar and provide
                # a very generous upper limit for the error that should never be exceeded
                # because of the fallback method.
                # Unfortunately, many tests don't pass stricter limits yet.
                margin1 = px_size[0] * 5
                margin2 = px_size[1] * 5

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

            registrar = GlobalShiftRegistrar()

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
            for i in range(len(pos)):
                # Test main tile
                diff1 = abs(tile_pos[i][0] - pos[i][0])
                diff2 = abs(tile_pos[i][1] - pos[i][1])
                margin1 = px_size[0] * 5
                margin2 = px_size[1] * 5
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
