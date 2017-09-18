# -*- coding: utf-8 -*-
'''
Created on 19 Jul 2017

@author: Éric Piel, Philip Winkler

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

from ._registrar import *
from ._weaver import *

import copy


def register(tiles, method="REGISTER_SHIFT"):
    """ 
    tiles (list of DataArray of shape YX or tuples of DataArrays): The tiles to compute the registration. 
    If it's tuples, the first tile of each tuple is the “main tile”, and the following ones are 
    dependent tiles.
    method (REGISTER_*): REGISTER_SHIFT → ShiftRegistrar, REGISTER_IDENTITY → IdentityRegistrar
    returns:
        tiles (list of DataArray of shape YX or tuples of DataArrays): The tiles as passed, but with updated 
        MD_POS metadata
    """
    # TODO: implement functionality for dependent tiles. 
    if method == "REGISTER_SHIFT":
        registrar = ShiftRegistrar()
        
    elif method == "REGISTER_IDENTITY":
        registrar = IdentityRegistrar()
    
    else:
        raise ValueError("Invalid registrar") 

    updatedTiles=[]
    for i in range(len(tiles)):
        if isinstance(tiles[i],tuple):
            tile = tiles[i][0]
            dep_tiles = tiles[i][1:]
        else:
            tile = tiles[i]
            dep_tiles = None
        registrar.addTile(tile,dep_tiles)

        md=copy.deepcopy(tile.metadata)

        md[model.MD_POS] = registrar.getPositions()[i]
        tileUpd = model.DataArray(tile,md)
        updatedTiles.append(tileUpd)
        
    return updatedTiles

def weave(tiles, method="WEAVER_MEAN"):
    pass

