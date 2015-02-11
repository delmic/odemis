# -*- coding: utf-8 -*-
"""
Created on 10 Jan 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

# Some helper functions to convert/manipulate images (DataArray and wxImage)

from __future__ import division

import logging
import numpy
import odemis.model
import wx


# @profile
# TODO: rename to *_bgra_*
from odemis.model._dataflow import DataArray


def format_rgba_darray(im_darray, alpha=None):
    """ Reshape the given numpy.ndarray from RGB to BGRA format

    If an alpha value is provided it will be set in the '4th' byte and used to scale the other RGB
    values within the array.

    """

    if im_darray.shape[-1] == 3:
        h, w, _ = im_darray.shape
        rgba_shape = (h, w, 4)
        rgba = numpy.empty(rgba_shape, dtype=numpy.uint8)
        # Copy the data over with bytes 0 and 2 being swapped (RGB becomes BGR through the -1)
        rgba[:, :, 0:3] = im_darray[:, :, ::-1]
        if alpha is not None:
            rgba[:, :, 3] = alpha
            rgba = scale_to_alpha(rgba)
        new_darray = odemis.model.DataArray(rgba)

        return new_darray

    elif im_darray.shape[-1] == 4:
        if hasattr(im_darray, 'metadata'):
            if im_darray.metadata.get('byteswapped', False):
                logging.warning("Trying to convert to BGRA an array already in BGRA")
                return im_darray

        rgba = numpy.empty(im_darray.shape, dtype=numpy.uint8)
        rgba[:, :, 0] = im_darray[:, :, 2]
        rgba[:, :, 1] = im_darray[:, :, 1]
        rgba[:, :, 2] = im_darray[:, :, 0]
        rgba[:, :, 3] = im_darray[:, :, 3]
        new_darray = odemis.model.DataArray(rgba)
        new_darray.metadata['byteswapped'] = True
        return new_darray
    else:
        raise ValueError("Unsupported colour depth!")


def add_alpha_byte(im_darray, alpha=255):

    height, width, depth = im_darray.shape

    if depth == 4:
        return im_darray
    elif depth == 3:
        new_im = numpy.empty((height, width, 4), dtype=numpy.uint8)
        new_im[:, :, -1] = alpha
        new_im[:, :, :-1] = im_darray

        if alpha != 255:
            new_im = scale_to_alpha(new_im)

        if isinstance(im_darray, DataArray):
            return DataArray(new_im, im_darray.metadata)
        else:
            return new_im
    else:
        raise ValueError("Unexpected colour depth of %d bytes!" % depth)


def scale_to_alpha(im_darray):
    """ Scale the R, G and B values to the alpha value present """

    if im_darray.shape[2] != 4:
        raise ValueError("DataArray needs to have 4 byte RGBA values!")

    im_darray[:, :, 0] *= im_darray[:, :, 3] / 255
    im_darray[:, :, 1] *= im_darray[:, :, 3] / 255
    im_darray[:, :, 2] *= im_darray[:, :, 3] / 255

    return im_darray


# Note: it's also possible to directly generate a wx.Bitmap from a buffer, but
# always implies a memory copy.
def NDImage2wxImage(image):
    """
    Converts a NDImage into a wxImage.
    Note, the copy of the data will be avoided whenever possible.
    image (ndarray of uint8 with shape YX3 or YX4): original image,
     order of last dimension is RGB(A)
    return (wxImage)
    """
    assert(len(image.shape) == 3)
    size = image.shape[1::-1]
    if image.shape[2] == 3: # RGB
        wim = wx.ImageFromBuffer(*size, dataBuffer=image) # 0 copy
        return wim
    elif image.shape[2] == 4: # RGBA
        # 2 copies
        return wx.ImageFromDataWithAlpha(*size,
                             data=numpy.ascontiguousarray(image[:, :, 0:3]),
                             alpha=numpy.ascontiguousarray(image[:, :, 3]))
    else:
        raise ValueError("image is of shape %s" % (image.shape,))

# Untested
def NDImage2wxBitmap(image):
    """
    Converts a NDImage into a wxBitmap.
    Note, the copy of the data will be avoided whenever possible.
    image (ndarray of uint8 with shape YX3 or YX4): original image,
     order of last dimension is RGB(A)
    return (wxImage)
    """
    assert(len(image.shape) == 3)
    size = image.shape[1::-1]
    if image.shape[2] == 3: # RGB
        bim = wx.EmptyBitmap(size[0], size[1], 24)
        bim.CopyFromBuffer(image, wx.BitmapBufferFormat_RGB)
        # bim = wx.BitmapFromBuffer(size[0], size[1], image)
    elif image.shape[2] == 4: # RGBA
        bim = wx.BitmapFromBufferRGBA(size[0], size[1], image)
    else:
        raise ValueError("image is of shape %s" % (image.shape,))

    return bim



def wxImage2NDImage(image, keep_alpha=True):
    """
    Converts a wx.Image into a numpy array.
    image (wx.Image): the image to convert of size MxN
    keep_alpha (boolean): keep the alpha channel when converted
    returns (numpy.ndarray): a numpy array of shape NxMx3 (RGB) or NxMx4 (RGBA)
    Note: Alpha not yet supported.
    """
    if keep_alpha and image.HasAlpha():
        shape = image.Height, image.Width, 4
        raise NotImplementedError()
    else:
        shape = image.Height, image.Width, 3

    return numpy.ndarray(buffer=image.DataBuffer, shape=shape, dtype=numpy.uint8)
