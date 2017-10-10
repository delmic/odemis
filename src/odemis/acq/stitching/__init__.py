# -*- coding: utf-8 -*-
'''
Created on 19 Jul 2017

@author: Éric Piel, Philip Winkler

Copyright © 2017 Éric Piel, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

from ._registrar import *
from ._weaver import *

import copy
import random

REGISTER_IDENTITY = 0
REGISTER_SHIFT = 1
WEAVER_MEAN = 0
WEAVER_COLLAGE = 1


def register(tiles, method=REGISTER_SHIFT):
    """
    tiles (list of DataArray of shape YX or tuples of DataArrays): The tiles to compute the registration. 
    If it's tuples, the first tile of each tuple is the “main tile”, and the following ones are 
    dependent tiles.
    method (REGISTER_*): REGISTER_SHIFT → ShiftRegistrar, REGISTER_IDENTITY → IdentityRegistrar
    returns:
        tiles (list of DataArray of shape YX or tuples of DataArrays): The tiles as passed, but with updated 
        MD_POS metadata
    """

    if method == REGISTER_SHIFT:
        registrar = ShiftRegistrar()
    elif method == REGISTER_IDENTITY:
        registrar = IdentityRegistrar()
    else:
        raise ValueError("Invalid registrar %s" % (method))

    # Register tiles
    updatedTiles = []
    for ts in tiles:
        # Separate tile and dependent_tiles
        if isinstance(ts, tuple):
            tile = ts[0]
            dep_tiles = ts[1:]
        else:
            tile = ts
            dep_tiles = None

        registrar.addTile(tile, dep_tiles)

    # Update positions
    for i in range(len(tiles)):
        # Return tuple of positions if dependent tiles are present
        if isinstance(tiles[i], tuple):
            tile = tiles[i][0]
            dep_tiles = tiles[i][1:]

            # Update main tile
            md = copy.deepcopy(tile.metadata)
            md[model.MD_POS] = registrar.getPositions()[0][i]
            tileUpd = model.DataArray(tile, md)

            # Update dependent tiles
            tilesNew = [tileUpd]
            for j in range(len(dep_tiles)):
                md = copy.deepcopy(dep_tiles[j][i].metadata)
                md[model.MD_POS] = registrar.getPositions()[1][j][i]
                tilesNew.append(model.DataArray(tile, md))
            tileUpd = tuple(tilesNew)

        else:
            tile = tiles[i]
            dep_tiles = None

            md = copy.deepcopy(tile.metadata)
            md[model.MD_POS] = registrar.getPositions()[0][i]
            tileUpd = model.DataArray(tile, md)

        updatedTiles.append(tileUpd)

    return updatedTiles


def weave(tiles, method=WEAVER_MEAN):
    """
    tiles (list of DataArray of shape YX or tuples of DataArrays): The tiles to compute the registration. 
    If it's tuples, the first tile of each tuple is the “main tile”, and the following ones are dependent tiles.
    method (WEAVER_*): WEAVER_MEAN → MeanWeaver, WEAVER_COLLAGE → CollageWeaver
    return:
        tiles (list of DataArray of shape YX or tuples of DataArrays): The tiles as passed, but with updated MD_POS metadata
    """

    if method == WEAVER_MEAN:
        weaver = MeanWeaver()
    elif method == WEAVER_COLLAGE:
        weaver = CollageWeaver()

    for t in tiles:
        weaver.addTile(t)
    stitched_image = weaver.getFullImage()

    return stitched_image


def decompose_image(img, overlap=0.1, numTiles=5, method="horizontalLines", shift=True):
    """
    Decomposes image into tiles for testing. The tiles overlap and their center positions are subject to random noise.
    Returns list of tiles and list of the actual positions. 
    img: 2D numpy array representing gray-scale image
    numTiles: number of desired tiles in each direction
    method: acquisition method, "horizontalLines" scans image by row and starts at the left for each row,
    "verticalLines" scans image by columns starting at the top for each row, and "horizontalZigzag" scans 
    a row, then scans the next row in reverse, etc. mimicking the behaviour of DELMIC microscopes. 
    shift : Boolean variable indicating whether or not to add a shift to the positions
    """

    tileSize = int(min(img.shape[0], img.shape[1]) / numTiles)

    pos = []
    tiles = []
    for i in range(numTiles):
        for j in range(numTiles):
            # Positions top left
            if method == "verticalLines":
                posX = int(i * (1 - overlap) * tileSize)
                posY = int(j * (1 - overlap) * tileSize)
            elif method == "horizontalLines":
                posX = int(j * (1 - overlap) * tileSize)
                posY = int(i * (1 - overlap) * tileSize)
            elif method == "horizontalZigzag":
                if i % 2 == 0:
                    posX = int(j * (1 - overlap) * tileSize)
                else:
                    # reverse direction for every second row
                    posX = int((numTiles - j - 1) * (1 - overlap) * tileSize)
                posY = int(i * (1 - overlap) * tileSize)

            else:
                raise ValueError("%s is not a valid method" % (method))

            yMax = img.shape[0]
            px_size = 100e-9
            md = {
                model.MD_PIXEL_SIZE: [px_size, px_size],  # m/px
                # m
                model.MD_POS: ((posX + tileSize / 2) * px_size, (yMax - posY - tileSize / 2) * px_size),
            }

            if shift:
                # Add noise for all positions except top left
                if i > 0 or j > 0:
                    maxNoise = int(0.05 * tileSize)
                    # the registrar can deal with 5% shift of the tile size, but not with 10%. In this
                    # case an overlap of 0.2 is not sufficient and even for higher overlaps, the
                    # stitching isn't guaranteed to work properly.
                    noise = [random.randrange(-maxNoise, maxNoise)
                             for _ in range(2)]

                    # Avoid negative indices
                    # Indices never exceed image size since tiles include
                    # margin of 1*overlap > noise
                    posX = max(0, int(posX + noise[0]))
                    posY = max(0, int(posY + noise[1]))

            # Crop images
            tile = img[posY:posY + tileSize, posX:posX + tileSize]

            # Create list of tiles and positions
            tile = model.DataArray(tile, md)

            tiles.append(tile)
            pos.append([(posX + tileSize / 2) * px_size,
                        (yMax - posY - tileSize / 2) * px_size])

    return [tiles, pos]
