# -*- coding: utf-8 -*-
'''
Created on 2 Sep 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Very rough converter to simple 8-bit PNG
import logging
import numpy
from odemis import model
from odemis.util import img
import os

from PIL import Image

FORMAT = "PNG"
# list of file-name extensions possible, the first one is the default when saving a file
EXTENSIONS = [u".png"]

# TODO: support 16-bits? But then it looses the point to have a "simple" format?
LOSSY = True # because it doesn't support 16 bits
CAN_SAVE_PYRAMID = False


def _saveAsPNG(filename, data):

    # TODO: store metadata

    # Already RGB 8 bit?
    if (data.metadata.get(model.MD_DIMS) == 'YXC'
        and data.dtype in (numpy.uint8, numpy.int8)
        and data.shape[2] in (3, 4)
       ):
        rgb8 = data
    else:
        data = img.ensure2DImage(data)

        # TODO: it currently fails with large data, use gdal instead?
    #     tempdriver = gdal.GetDriverByName('MEM')
    #     tmp = tempdriver.Create('', rgb8.shape[1], rgb8.shape[0], 1, gdal.GDT_Byte)
    #     tiledriver = gdal.GetDriverByName("png")
    #     tmp.GetRasterBand(1).WriteArray(rgb8[:, :, 0])
    #     tiledriver.CreateCopy("testgdal.png", tmp, strict=0)


        # TODO: support greyscale png?
        # TODO: skip if already 8 bits
        # Convert to 8 bit RGB
        hist, edges = img.histogram(data)
        irange = img.findOptimalRange(hist, edges, 1 / 256)
        rgb8 = img.DataArray2RGB(data, irange)

    # save to file
    im = Image.fromarray(rgb8)
    im.save(filename, "PNG")


def export(filename, data, thumbnail=None):
    '''
    Write a PNG file with the given image
    filename (unicode): filename of the file to create (including path). If more
      than one data is passed, a number will be appended.
    data (list of model.DataArray, or model.DataArray): the data to export.
       Metadata is taken directly from the DA object. If it's a list, a multiple
       page file is created. It must have 5 dimensions in this order: Channel,
       Time, Z, Y, X. However, all the first dimensions of size 1 can be omitted
       (ex: an array of 111YX can be given just as YX, but RGB images are 311YX,
       so must always be 5 dimensions).
    thumbnail (None or numpy.array): Image used as thumbnail for the file. Can be of any
      (reasonable) size. Must be either 2D array (greyscale) or 3D with last
      dimension of length 3 (RGB). As png doesn't support it, it will
      be dropped silently.
    '''
    if thumbnail is not None:
        logging.info("Dropping thumbnail, not supported in PNG")
    if isinstance(data, list):
        if len(data) > 1:
            # Name the files aaa-XXX.png
            base, ext = os.path.splitext(filename)
            for i, d in enumerate(data):
                fn = "%s-%03d%s" % (base, i, ext)
                _saveAsPNG(fn, d)
        else:
            _saveAsPNG(filename, data[0])
    else:
        _saveAsPNG(filename, data)
