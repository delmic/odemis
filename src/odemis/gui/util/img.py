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

import cairo
import logging
import numpy
import odemis.model
from odemis.model._dataflow import DataArray
import wx


# @profile
# TODO: rename to *_bgra_*
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


def min_type(data):
    """Find the minimum type code needed to represent the elements in `data`.
    """

    if numpy.issubdtype(data.dtype, numpy.integer):
        types = [numpy.int8, numpy.uint8, numpy.int16, numpy.uint16, numpy.int32,
                 numpy.uint32, numpy.int64, numpy.uint64]
    else:
        types = [numpy.float16, numpy.float32, numpy.float64]

    data_min, data_max = data.min(), data.max()

    for t in types:
        if numpy.all(data_min >= numpy.iinfo(t).min) and numpy.all(data_max <= numpy.iinfo(t).max):
            return t
    else:
        raise ValueError("Could not find suitable dtype.")


def apply_rotation(ctx, rotation, b_im_rect):
    """
    Applies rotation to the given cairo context

    ctx: (cairo.Context) Cairo context to draw on
    rotation: (float) in rads
    b_im_rect: (float, float, float, float) top, left, width, height rectangle
        containing the image in buffer coordinates
    """
    if rotation is not None and abs(rotation) >= 0.008:  # > 0.5°
        x, y, w, h = b_im_rect

        rot_x = x + w / 2
        rot_y = y + h / 2
        # Translate to the center of the image (in buffer coordinates)
        ctx.translate(rot_x, rot_y)
        # Rotate
        ctx.rotate(-rotation)
        # Translate back, so the origin is at the top left position of the image
        ctx.translate(-rot_x, -rot_y)


def apply_shear(ctx, shear, b_im_rect):
    """
    Applies shear to the given cairo context

    ctx: (cairo.Context) Cairo context to draw on
    shear: (float) shear to be applied
    b_im_rect: (float, float, float, float) top, left, width, height rectangle
        containing the image in buffer coordinates
    """
    # Shear if needed
    if shear is not None and abs(shear) >= 0.0005:
        # Shear around the center of the image data. Shearing only occurs on the x axis
        x, y, w, h = b_im_rect
        shear_x = x + w / 2
        shear_y = y + h / 2

        # Translate to the center x of the image (in buffer coordinates)
        ctx.translate(shear_x, shear_y)
        shear_matrix = cairo.Matrix(1.0, shear, 0.0, 1.0)
        ctx.transform(shear_matrix)
        ctx.translate(-shear_x, -shear_y)


def apply_flip(ctx, flip, b_im_rect):
    """
    Applies flip to the given cairo context

    ctx: (cairo.Context) Cairo context to draw on
    flip: (boolean) apply flip if True
    b_im_rect: (float, float, float, float) top, left, width, height rectangle
        containing the image in buffer coordinates
    """
    if flip:
        fx = fy = 1.0

        if flip & wx.HORIZONTAL == wx.HORIZONTAL:
            fx = -1.0

        if flip & wx.VERTICAL == wx.VERTICAL:
            fy = -1.0

        x, y, w, h = b_im_rect

        flip_x = x + w / 2
        flip_y = y + h / 2

        flip_matrix = cairo.Matrix(fx, 0.0, 0.0, fy)

        ctx.translate(flip_x, flip_y)

        ctx.transform(flip_matrix)
        ctx.translate(-flip_x, -flip_y)


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


def wxImageScaleKeepRatio(im, size, quality=wx.IMAGE_QUALITY_NORMAL):
    """
    Scales (down) an image so that if fits within a given bounding-box without
      changing the aspect ratio, and filling up with black bands
    im (wxImage): the image to scale
    size (int, int): the size (width, height) of the bounding box
    quality (int): scaling quality, same as image.Scale()
    return (wxImage): an image scaled to fit the size within at least one
      dimension. The other dimension will be of the requested size, but with
      only a subset containing the data.
    """
    ratio = min(size[0] / im.Width, size[1] / im.Height)
    rw = max(1, int(im.Width * ratio))
    rh = max(1, int(im.Height * ratio))
    sim = im.Scale(rw, rh, quality)

    # Add a (black) border on the small dimension
    lt = ((size[0] - rw) // 2, (size[1] - rh) // 2)
    sim.Resize(size, lt, 0, 0, 0)

    return sim
