# -*- coding: utf-8 -*-
'''
Created on 24 Jan 2017

@author: Guilherme Stiebler

Copyright © 2017 Guilherme Stiebler, Éric Piel, Delmic

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
from future.utils import with_metaclass
from abc import ABCMeta, abstractmethod


class DataArrayShadow(with_metaclass(ABCMeta, object)):
    """
    This class contains information about a DataArray.
    It has all the useful attributes of a DataArray, but not the actual data.
    If the image represented by an instance of this class is tiled, it should have a
    method called 'getTile()', that fetches one tile from the image.
    """

    def __init__(self, shape, dtype, metadata=None, maxzoom=None, tile_shape=None):
        """
        Constructor
        shape (tuple of 0<=int): The shape of the corresponding DataArray
        dtype (numpy.dtype): The data type
        metadata (dict str->val): The metadata
        maxzoom (0<=int): the maximum zoom level possible. If the data isn't
            encoded in pyramidal format, the attribute is not present.
            The shape of the images in each zoom level is as following:
            (shape of full image) // (2**z)
            where z is the index of the zoom level
        tile_shape (0<int, 0<int): the shape of the tile (X, Y) , if the image is tiled.
           It is only present when maxzoom is also present
        """
        if not all (0 <= v for v in shape):
            raise ValueError("shape should be positives ints, but got %s" % (shape,))
        self.shape = shape
        self.ndim = len(shape)
        self.dtype = dtype
        self.metadata = metadata if metadata else {}
        if maxzoom is not None:
            if not isinstance(maxzoom, int) or maxzoom < 0:
                raise ValueError("Zoom must be 0<int, but got %s" % (maxzoom,))
            self.maxzoom = maxzoom
            if len(tile_shape) != 2 or not all (0 < v for v in tile_shape):
                raise ValueError("tile_shape should be 2 positives ints, but got %s" % (tile_shape,))
            self.tile_shape = tile_shape

    @abstractmethod
    def getData(self):
        """
        Fetches the whole data (at full resolution) of the DataArray.
        return DataArray: the data, with its metadata (ie, identical to .content[n] but
            with the actual data)
        """
        pass

    # Defined if the object supports per tile access.
#     def getTile(self, x, y, zoom):
#         """
#         x (0<=int): X index of the tile.
#         y (0<=int): Y index of the tile
#         zoom (0<=int): zoom level to use. The total shape of the image is shape / 2**zoom.
#             The number of tiles available in an image is ceil((shape//zoom)/tile_shape)
#         return (DataArray): the shape of the DataArray is typically of shape
#         """


class AcquisitionData(with_metaclass(ABCMeta, object)):
    """
    It's an abstract class to represent an opened file. It allows
    to have random access to a sub-part of any image in the file. It's extended by
    each dataio converter to actually support the specific file format.
    """

    def __init__(self, content, thumbnails=None):
        """
        content (tuple of DataArrayShadows))
        thumbnails (tuple of DataArrayShadows)
        """
        self.content = content
        self.thumbnails = thumbnails if thumbnails else ()
