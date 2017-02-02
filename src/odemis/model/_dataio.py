#!/usr/bin/env python
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
from abc import ABCMeta, abstractmethod


class DataArrayShadow(object):
    """
    This class contains information about a DataArray.
    It has all the useful attributes of a DataArray, but not the actual data.
    """
    def __init__(self, shape, dtype, metadata=None, maxzoom=None):
        """
        Constructor
        shape (tuple of int): The shape of the corresponding DataArray
        dtype (numpy.dtype): The data type
        metadata (dict str->val): The metadata
        maxzoom (0<=int): the maximum zoom level possible. If the data isn't
            encoded in pyramidal format, the attribute is not present.
            The shape of the images in each zoom level is as following:
            (shape of full image) // (2**z)
            where z is the index of the zoom level
        """
        self.shape = shape
        self.ndim = len(shape)
        self.dtype = dtype
        self.metadata = metadata if metadata else {}
        if maxzoom is not None:
            self.maxzoom = maxzoom

class AcquisitionData(object):
    """
    It's an abstract class to represent an opened file. It allows
    to have random access to a sub-part of any image in the file. It's extended by
    each dataio converter to actually support the specific file format.
    """
    __metaclass__ = ABCMeta

    def __init__(self, content, thumbnails=None):
        self.content = content
        self.thumbnails = thumbnails if thumbnails else ()

    @abstractmethod
    def getData(self, n):
        """
        Fetches the whole data (at full resolution) of image at index n.
        n (0<=int): index of the image
        return DataArray: the data, with its metadata (ie, identical to .content[n] but
            with the actual data)
        """
        pass

    @abstractmethod
    def getSubData(self, n, z, rect):
        """
        Fetches a part of the data, for a given zoom. If the (complete) data has more
        than two dimensions, all the extra dimensions (ie, non-spatial) are always fully
        returned for the given part.
        n (int): index of the image
        z (0 <= int) : zoom level. The data returned will be with MD_PIXEL_SIZE * 2^z.
            So 0 means to use the highest zoom, with the original pixel size. 1 will
            return data half the width and heigh (The maximum possible value depends
            on the data).
        rect (4 ints): left, top, right, bottom coordinates (in px, at zoom=0) of the
            area of interest.
        return (tuple of tuple of DataArray): all the tiles in X&Y dimension, so that
            the area of interest is fully covered (so the area can be larger than requested).
            The first dimension is X, and second is Y. For example, if returning 3x7 tiles,
            the most bottom-right tile will be accessed as ret[2][6]. For each
            DataArray.metadata, MD_POS and MD_PIXEL_SIZE are updated appropriately
            (if MD_POS is not present, (0,0) is used as default for the entire image, and if
            MD_PIXEL_SIZE is not present, it will not be updated).
        raise ValueError: if the area or z is out of range, or if the raw data is not pyramidal.
        """
        pass

    @abstractmethod
    def getThumbnail(self, n):
        """
        Fetches the whole data (at full resolution) of a thumbnail image at index n.
        n (0<=int): index of the image
        return DataArray: the data, with its metadata (ie, identical to .thumbnail[n] but
            with the actual data)
        """
        pass
