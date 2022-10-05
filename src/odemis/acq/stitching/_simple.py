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
import copy
from odemis import model
from odemis.acq.stitching._constants import REGISTER_GLOBAL_SHIFT, REGISTER_SHIFT, \
    REGISTER_IDENTITY, WEAVER_MEAN, WEAVER_COLLAGE, WEAVER_COLLAGE_REVERSE
from odemis.acq.stitching._registrar import ShiftRegistrar, IdentityRegistrar, GlobalShiftRegistrar
from odemis.acq.stitching._weaver import MeanWeaver, CollageWeaver, CollageWeaverReverse


def register(tiles, method=REGISTER_GLOBAL_SHIFT):
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
    elif method == REGISTER_GLOBAL_SHIFT:
        registrar = GlobalShiftRegistrar()
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

    # Compute the positions
    positions, dep_positions = registrar.getPositions()

    # Update positions, by creating DataArrays with the same data, but different MD_POS
    for i, ts in enumerate(tiles):
        # Return tuple of positions if dependent tiles are present
        if isinstance(ts, tuple):
            tile = ts[0]
            dep_tiles = ts[1:]

            # Update main tile
            md = copy.deepcopy(tile.metadata)
            md[model.MD_POS] = positions[i]
            tileUpd = model.DataArray(tile, md)

            # Update dependent tiles
            tilesNew = [tileUpd]
            for j, t in enumerate(dep_tiles):
                md = copy.deepcopy(t.metadata)
                md[model.MD_POS] = dep_positions[i][j]
                tilesNew.append(model.DataArray(t, md))
            tileUpd = tuple(tilesNew)

        else:
            md = copy.deepcopy(ts.metadata)
            md[model.MD_POS] = positions[i]
            tileUpd = model.DataArray(ts, md)

        updatedTiles.append(tileUpd)

    return updatedTiles


def weave(tiles, method=WEAVER_MEAN, adjust_brightness=False):
    """
    tiles (list of DataArray of shape YX): The tiles to draw
    method (WEAVER_*): WEAVER_MEAN → MeanWeaver, WEAVER_COLLAGE → CollageWeaver
    return:
        image (DataArray of shape Y'X'): A large image containing all the tiles
    """

    if method == WEAVER_MEAN:
        weaver = MeanWeaver(adjust_brightness)
    elif method == WEAVER_COLLAGE:
        weaver = CollageWeaver(adjust_brightness)
    elif method == WEAVER_COLLAGE_REVERSE:
        weaver = CollageWeaverReverse(adjust_brightness)
    else:
        raise ValueError("Invalid weaver %s" % (method,))

    for t in tiles:
        weaver.addTile(t)
    stitched_image = weaver.getFullImage()

    return stitched_image


