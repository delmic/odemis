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
        raise ValueError("Invalid registrar %s" % (method,))

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


