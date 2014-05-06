# -*- coding: utf-8 -*-
'''
Created on 10 Jan 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Some helper functions to convert/manipulate images (DataArray and wxImage)

from __future__ import division

import logging
import numpy
import wx

from odemis.model import DataArray
from profilehooks import profile

# @profile
def format_rgba_darray(im_darray):
    """ Make sure the data array has the right shape and swap bytes """

    if im_darray.shape[-1] == 3:
        h, w, _ = im_darray.shape
        rgba_shape = (h, w, 4)
        rgba = numpy.empty(rgba_shape, dtype=numpy.uint8)
        rgba[:, :, 0:3] = im_darray[:, :, ::-1]
        rgba[:, :, 3] = 255
        new_darray = DataArray(rgba)
        return new_darray
    elif im_darray.shape[-1] == 4:
        return im_darray
    else:
        raise ValueError("Unsupporeted colour depth!")

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
