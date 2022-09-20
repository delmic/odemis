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

# Some helper functions to convert/manipulate images

import cairo
import logging
import math
import numbers
import numpy
from odemis import model
from odemis.acq.stream import RGBProjection, RGBSpatialProjection, \
    SinglePointTemporalProjection, SinglePointAngularProjection, DataProjection
from odemis.gui import BLEND_SCREEN, BLEND_DEFAULT
from odemis.gui.comp.overlay.base import Label
from odemis.model import DataArrayShadow
from odemis.model import TINT_FIT_TO_RGB, TINT_RGB_AS_IS
from odemis.util import intersect, fluo, img, units
from odemis.util import spectrum
from past.builtins import basestring
import threading
import time
from typing import Tuple, Optional
import wx

import odemis.acq.stream as acqstream
import odemis.gui.img as guiimg


BAR_PLOT_COLOUR = (0.5, 0.5, 0.5)
CROP_RES_LIMIT = 1024
MAX_RES_FACTOR = 5  # upper limit resolution factor to exported image
MIN_AR_SIZE = 800  # px, minimum size the AR image is exported
TICKS_PER_AXIS = 10  # rough number of ticks to show on axes
SPEC_PLOT_SIZE = 1024
SPEC_SCALE_WIDTH = 150  # ticks + text vertically
SPEC_SCALE_HEIGHT = 100  # ticks + text horizontally
SMALL_SCALE_WIDTH = 10  # just ticks
SPEC_FONT_SIZE = 0.035  # Ratio of the whole output width
AR_FONT_SIZE = 0.03
# legend ratios
CELL_WIDTH = 0.2
MAIN_LAYER = 0.05
SUB_LAYER = 0.035
CELL_MARGIN = 0.02
LARGE_FONT = 0.0162
MEDIUM_FONT = 0.0131
SMALL_FONT = 0.0118
MAIN_UPPER = 0.0229
MAIN_LOWER = 0.0389
MAIN_MIDDLE = 0.0311
SUB_UPPER = 0.0147
SUB_LOWER = 0.029
BAR_HEIGHT = 0.015
BAR_THICKNESS = 0.0026
LINE_THICKNESS = 0.0016
ARC_RADIUS = 0.002
ARC_LEFT_MARGIN = 0.01
ARC_TOP_MARGIN = 0.0104
TINT_SIZE = 0.0155
COLORBAR_WIDTH_RATIO = 0.6  # the fraction of the two cells to make the colorbar


# TODO: rename to *_bgra_*
def format_rgba_darray(im_darray, alpha=None):
    """ Reshape the given numpy.ndarray from RGB to BGRA format
    im_darray (DataArray of shape Y,X,{3,4}): input image
    alpha (0 <= int <= 255 or None): If an alpha value is provided it will be
      set in the '4th' byte and used to scale the other RGB values within the array.
    return (DataArray of shape Y,X,4): The return type is the same of im_darray
    """
    if im_darray.shape[-1] == 3:
        h, w, _ = im_darray.shape
        rgba_shape = (h, w, 4)
        rgba = numpy.empty(rgba_shape, dtype=numpy.uint8)
        # Copy the data over with bytes 0 and 2 being swapped (RGB becomes BGR through the -1)
        rgba[:, :, 0:3] = im_darray[:, :, ::-1]
        if alpha is not None:
            rgba[:, :, 3] = alpha
            if alpha != 255:
                scale_to_alpha(rgba)
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
    else:
        raise ValueError("Unsupported colour depth!")

    new_darray = model.DataArray(rgba)
    new_darray.metadata['byteswapped'] = True
    return new_darray


def format_bgra_to_rgb(im_darray, keepalpha=True, inplace=False):
    """ Reshape the given numpy.ndarray from BGR(A) to RGB(A) format
    im_darray (DataArray of shape Y,X,{3,4}): input image
    keepalpha (bool): If an alpha is present, keep it. IOW, if there are 4 channels,
      the 4th channel will be kept as is.
    inplace (bool): directly modify im_darray. If True, keepalpha must also be
       True (as the array cannot change shape).
    return (DataArray of shape Y,X,{3,4}): The return type is the same of im_darray
    """
    assert im_darray.ndim == 3

    if hasattr(im_darray, 'metadata'):
        if not im_darray.metadata.get('byteswapped', True):
            logging.warning("Trying to convert to RGB an array already in RGB")
            return im_darray

    if im_darray.shape[-1] == 3:
        shape = im_darray.shape
    elif im_darray.shape[-1] == 4:
        if keepalpha:
            shape = im_darray.shape
        else:
            shape = im_darray.shape[:2] + (3,)
            if inplace:
                raise ValueError("Cannot drop alpha channel in-place")
    else:
        raise ValueError("Unsupported colour depth!")

    if inplace:
        rgba = im_darray
        rgba[:,:, [2, 0]] = rgba[:,:, [0, 2]]  # Just switch R <> B
    else:
        rgba = numpy.empty(im_darray.shape, dtype=numpy.uint8)
        # Copy the data over with bytes 0 and 2 being swapped (RGB becomes BGR through the -1)
        rgba[:,:, 0] = im_darray[:,:, 2]
        rgba[:,:, 1] = im_darray[:,:, 1]
        rgba[:,:, 2] = im_darray[:,:, 0]
        if shape[-1] == 4:
            rgba[:,:, 3] = im_darray[:,:, 3]

    # Add metadata to detect if the function is called twice on the same array
    if not hasattr(rgba, 'metadata'):
        rgba = model.DataArray(rgba)
    rgba.metadata['byteswapped'] = False

    return rgba


def min_type(data):
    """Find the minimum type code needed to represent the elements in `data`.
    """

    if numpy.issubdtype(data.dtype, numpy.integer):
        types = (numpy.uint8, numpy.int8, numpy.uint16, numpy.int16, numpy.uint32,
                 numpy.int32, numpy.uint64, numpy.int64)

        data_min, data_max = data.min(), data.max()
        for t in types:
            if numpy.all(data_min >= numpy.iinfo(t).min) and numpy.all(data_max <= numpy.iinfo(t).max):
                return t
        else:
            raise ValueError("Could not find suitable dtype.")
    else:
        # TODO: for floats, be more clever, and if all the float could have a
        # smaller precision, return that smaller floats (eg, the data contains
        # only 0's)
        return data.dtype


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


def ar_create_tick_labels(client_size, ticksize, num_ticks, margin=0):
    """
    Create list of tick labels for AR polar representation

    client_size (int, int)
    ticksize (int): size of tick in pixels
    num_ticks (int): number of ticks
    returns (list of Labels)
            (tuple of floats): center
            (float): inner radius
            (float): radius
    """

    # Calculate the characteristic values
    center_x = client_size[0] / 2
    center_y = client_size[1] / 2
    font_size = max(3, client_size[0] * AR_FONT_SIZE)
    inner_radius = min(center_x, center_y)
    radius = inner_radius + 1
    ticks = []

    # Top middle
    for i in range(num_ticks):
        # phi needs to be rotated 90 degrees counter clockwise, otherwise
        # 0 degrees will be at the right side of the circle
        phi = (2 * math.pi / num_ticks * i) - (math.pi / 2)
        deg = round(math.degrees(phi))

        cos = math.cos(phi)
        sin = math.sin(phi)

        # Tick start and end point (outer and inner)
        ox = center_x + radius * cos + margin
        oy = center_y + radius * sin + margin
        ix = center_x + (radius - ticksize) * cos + margin
        iy = center_y + (radius - ticksize) * sin + margin

        # Tick label positions
        lx = center_x + (radius + 5) * cos + margin
        ly = center_y + (radius + 5) * sin + margin

        label = Label(
            text=u"%d°" % (deg + 90),
            pos=(lx, ly),
            font_size=font_size,
            flip=True,
            align=wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_BOTTOM,
            colour=(0, 0, 0),
            opacity=1.0,
            deg=deg - 90
        )

        ticks.append((ox, oy, ix, iy, label))
    return ticks, (center_x + margin, center_y + margin), inner_radius, radius


def write_label(ctx, l, font_name, canvas_padding=None, view_width=None, view_height=None):
    """
    Draws label to given context

    ctx: (cairo.Context) Cairo context to draw on
    l: (Label) label to draw
    font_name (string): font name
    canvas_padding (int): canvas padding if exists
    view_width (int): window view width
    view_height (int): window view height
    """

    # No text? Do nothing
    if not l.text:
        return

    # Cache the current context settings
    ctx.save()

    # TODO: Look at ScaledFont for additional caching
    ctx.select_font_face(font_name, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)

    # For some reason, fonts look a little bit smaller when Cairo
    # plots them at an angle. We compensate for that by increasing the size
    # by 1 point in that case, so the size visually resembles that of
    # straight text.
    if l.deg not in (0.0, 180.0, None):
        ctx.set_font_size(l.font_size + 1)
    else:
        ctx.set_font_size(l.font_size)

    # Rotation always happens at the plot coordinates
    if l.deg is not None:
        phi = math.radians(l.deg)
        rx, ry = l.pos

        if l.flip:
            phi -= math.pi

        ctx.translate(rx, ry)
        ctx.rotate(phi)
        ctx.translate(-rx, -ry)

    # Take care of newline characters
    parts = l.text.split("\n")

    # Calculate the rendering position
    if not l.render_pos:
        x, y = l.pos

        lw, lh = 0, 0
        plh = l.font_size  # default to font size, but should always get updated
        for p in parts:
            plw, plh = ctx.text_extents(p)[2:4]
            lw = max(lw, plw)
            lh += plh

        # Cairo renders text from the bottom left, but we want to treat
        # the top left as the origin. So we need to add the height (lower the
        # render point), to make the given position align with the top left.
        y += plh

        if canvas_padding is not None:
            # Apply padding
            x = max(min(x, view_width - canvas_padding), canvas_padding)
            y = max(min(y, view_height - canvas_padding), canvas_padding)

        # Horizontally align the label
        if l.align & wx.ALIGN_RIGHT:
            x -= lw
        elif l.align & wx.ALIGN_CENTRE_HORIZONTAL:
            x -= lw / 2.0

        # Vertically align the label
        if l.align & wx.ALIGN_BOTTOM:
            y -= lh
        elif l.align & wx.ALIGN_CENTER_VERTICAL:
            y -= lh / 2.0

        # When we rotate text, flip gets a different meaning
        if l.deg is None and l.flip:
            if canvas_padding is not None:
                width = view_width
                height = view_height

                # Prevent the text from running off screen
                if x + lw + canvas_padding > width:
                    x = width - lw
                elif x < canvas_padding:
                    x = canvas_padding
                if y + lh + canvas_padding > height:
                    y = height - lh
                elif y < lh:
                    y = lh

        l.render_pos = x, y
        l.text_size = lw, lh
    else:
        x, y = l.render_pos

    # Draw Shadow
    if l.colour:
        if l.colour == (0, 0, 0):
            # FIXME: For now we just use "white" shadow if the text is totally black
            ctx.set_source_rgba(1, 1, 1, 0.7 * l.opacity)
        else:
            ctx.set_source_rgba(0.0, 0.0, 0.0, 0.7 * l.opacity)
        ofst = 0
        for part in parts:
            ctx.move_to(x + 1, y + 1 + ofst)
            ofst += l.font_size
            ctx.show_text(part)

    # Draw Text
    if l.colour:
        if len(l.colour) == 3:
            ctx.set_source_rgba(*(l.colour + (l.opacity,)))
        else:
            ctx.set_source_rgba(*l.colour)

    ofst = 0
    for part in parts:
        ctx.move_to(x, y + ofst)
        ofst += l.font_size + 3
        ctx.show_text(part)

    ctx.restore()


def draw_ar_frame(ctx, client_size, ticks, font_name, center_x, center_y, inner_radius, radius):
    """
    Draws AR frame on the given context

    ctx (cairo.Context): Cairo context to draw on
    client_size (wx._core.Size): client window size
    ticks (list of Labels): list of tick labels to draw
    font_name (string): font name
    center_x (float): center x axis
    center_y (float): center y axis
    inner_radius (float): inner radius
    radius (float): radius
    """
    # Draw frame that covers everything outside the center circle
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    ctx.set_source_rgb(1, 1, 1)

    ctx.rectangle(0, 0, client_size[0], client_size[1])
    ctx.arc(center_x, center_y, inner_radius, 0, 2 * math.pi)
    ctx.fill()

    # Draw Azimuth degree circle
    ctx.set_line_width(2)
    ctx.set_source_rgb(0.5, 0.5, 0.5)
    ctx.arc(center_x, center_y, radius, 0, 2 * math.pi)
    ctx.stroke()

    # Draw Azimuth degree ticks
    ctx.set_line_width(1)
    for sx, sy, lx, ly, _ in ticks:
        ctx.move_to(sx, sy)
        ctx.line_to(lx, ly)
    ctx.stroke()

    # Draw tick labels
    for _, _, _, _, label in ticks:
        write_label(ctx, label, font_name)


def draw_ar_spiderweb(ctx, center_x, center_y, radius):
    """
    Draws AR spiderweb on the given context

    ctx (cairo.Context): Cairo context to draw on
    center_x (float): center x axis
    center_y (float): center y axis
    radius (float): radius
    """

    # First draw a dark semi-transparent "shadow" then a grey line
    lines = ((2.5, (0, 0, 0, 0.5)), (1.25, (0.5, 0.5, 0.5, 1.0)))
    for lw, lc in lines:
        ctx.set_line_width(lw)
        ctx.set_source_rgba(*lc)

        # Draw inner degree circles, we assume the exterior one is already there as
        # part of the frame
        ctx.new_path()
        ctx.arc(center_x, center_y, (2 / 3) * radius, 0, 2 * math.pi)
        ctx.stroke()
        ctx.arc(center_x, center_y, (1 / 3) * radius, 0, 2 * math.pi)
        ctx.stroke()

        # Finds lines ending points
        n_ends = 12
        ends = []
        for i in range(n_ends):
            phi = (2 * math.pi / n_ends * i) - (math.pi / 2)
            cos = math.cos(phi)
            sin = math.sin(phi)

            # Tick start and end point (outer and inner)
            ox = center_x + radius * cos
            oy = center_y + radius * sin
            ends.append((ox, oy))
        # Draw lines
        n_lines = n_ends // 2
        for i in range(n_lines):
            ctx.move_to(ends[i][0], ends[i][1])
            ctx.line_to(ends[i + n_lines][0], ends[i + n_lines][1])
        ctx.stroke()


def set_images(im_args):
    """ Set (or update) image

    im_args: (list of tuples): Each element is either None or
        (im, p_pos, scale, keepalpha, rotation, name, blend_mode)

        0. im (DataArray of shape YXC): the image
        1. p_pos (2-tuple of float): position of the center of the image (in physical coordinates)
        2. scale (float, float): scale of the image
        3. keepalpha (boolean): whether the alpha channel must be used to draw
        4. rotation (float): clockwise rotation in radians on the center of the image
        5. shear (float): horizontal shear relative to the center of the image
        6. flip (int): Image horz or vert flipping. 0 for no flip, wx.HORZ and wx.VERT otherwise
        7. blend_mode (int): blend mode to use for the image. Defaults to `source` which
                just overrides underlying layers.
        8. name (str): name of the stream that the image originated from
        9. date (int): seconds since epoch
        10. stream (Stream): the stream from which the image corresponds to
        11. metadata (dict): the metadata of the raw data

    returns (list of DataArray)
    """

    images = []

    for args in im_args:
        if args is None:
            images.append(None)
        else:
            im, p_pos, scale, keepalpha, rotation, shear, flip, blend_mode, name, date, stream, md = args

            if not blend_mode:
                blend_mode = BLEND_DEFAULT

            try:
                depth = im.shape[2]

                if depth == 3:
                    im = add_alpha_byte(im)
                elif depth != 4:  # Both ARGB32 and RGB24 need 4 bytes
                    raise ValueError("Unsupported colour byte size (%s)!" % depth)
            except IndexError:
                # Handle grayscale images pretending they are rgb
                pass

            im.metadata['dc_center'] = p_pos
            im.metadata['dc_scale'] = scale
            im.metadata['dc_rotation'] = rotation
            im.metadata['dc_shear'] = shear
            im.metadata['dc_flip'] = flip
            im.metadata['dc_keepalpha'] = keepalpha
            im.metadata['blend_mode'] = blend_mode
            im.metadata['name'] = name
            im.metadata['date'] = date
            im.metadata['stream'] = stream
            im.metadata['metadata'] = md

            images.append(im)

    return images


def calc_img_buffer_rect(im_data, im_scale, p_im_center, buffer_center, buffer_scale, buffer_size):
    """ Compute the rectangle containing the image in buffer coordinates

    im_data (DataArray): image data
    im_scale (float, float): The x and y scales of the image
    p_im_center (float, float): The center of the image in phys coordinates
    buffer_center (float, float): The buffer center (in phys coordinates)
    buffer_scale (float, float): The buffer scale
    buffer_size (int, int): The buffer shape in pixels (X, Y)

    returns (float, float, float, float) top, left, width, height

    """
    # TODO: get im_scale and im_center from the metadata of im_data
    # TODO: also use shear and rotation to computer BBox. At least, add 10%
    # in case there are not both null.

    # Scale the image
    im_h, im_w = im_data.shape[:2]
    scale_x, scale_y = im_scale[:2]
    scaled_im_size = (im_w * scale_x, im_h * scale_y)

    # Calculate the top left (in buffer coordinates, so bottom left in phys)
    p_topleft = (p_im_center[0] - (scaled_im_size[0] / 2),
                 p_im_center[1] + (scaled_im_size[1] / 2))

    b_topleft = (((p_topleft[0] - buffer_center[0]) / buffer_scale[0]) + (buffer_size[0] / 2),
                 -((p_topleft[1] - buffer_center[1]) / buffer_scale[1]) + (buffer_size[1] / 2))

    final_size = (scaled_im_size[0] / buffer_scale[0], scaled_im_size[1] / buffer_scale[1])
    return b_topleft + final_size


def draw_image(ctx, im_data, p_im_center, buffer_center, buffer_scale,
               buffer_size, opacity=1.0, im_scale=(1.0, 1.0), rotation=None,
               shear=None, flip=None, blend_mode=BLEND_DEFAULT, interpolate_data=False):
    """ Draw the given image to the Cairo context

    ctx (cairo.Context): Cario context to draw on
    im_data (DataArray): Image to draw
    p_im_center (2-tuple float)
    buffer_center (float, float): The buffer center
    buffer_scale (float, float): The buffer scale
    buffer_size (float, float): The buffer size
    opacity (float) [0..1] => [transparent..opaque]
    im_scale (float, float)
    rotation (float): Clock-wise rotation around the image center in radians
    shear (float): Horizontal shearing of the image data (around it's center)
    flip (wx.HORIZONTAL | wx.VERTICAL): If and how to flip the image
    blend_mode (int): Graphical blending type used for transparency
    interpolate_data (boolean): apply interpolation if True

    """

    # Fully transparent image does not need to be drawn
    if opacity < 1e-8:
        logging.debug("Skipping draw: image fully transparent")
        return

    # Determine the rectangle the image would occupy in the buffer
    # TODO: check why it works when there is rotation or skew
    b_im_rect = calc_img_buffer_rect(im_data, im_scale, p_im_center, buffer_center, buffer_scale, buffer_size)

    # To small to see, so no need to draw
    if b_im_rect[2] < 1 or b_im_rect[3] < 1:
        # TODO: compute the mean, and display one pixel with it
        logging.debug("Skipping draw: too small")
        return

    # Get the intersection with the actual buffer
    buffer_rect = (0, 0) + buffer_size

    intersection = intersect(buffer_rect, b_im_rect)

    # No intersection means nothing to draw
    if not intersection:
        logging.debug("Skipping draw: no intersection with buffer")
        return

    # print b_im_rect
    x, y, w, h = b_im_rect
    # Rotate if needed
    ctx.save()

    # apply transformations if needed
    apply_rotation(ctx, rotation, b_im_rect)
    apply_shear(ctx, shear, b_im_rect)
    apply_flip(ctx, flip, b_im_rect)

    width_ratio = float(im_scale[0]) / float(buffer_scale[0])
    height_ratio = float(im_scale[1]) / float(buffer_scale[1])
    total_scale = total_scale_x, total_scale_y = (width_ratio, height_ratio)

    if total_scale_x > 1.0 or total_scale_y > 1.0:
        logging.debug("Up scaling required")

        # If very little data is trimmed, it's better to scale the entire image than to create
        # a slightly smaller copy first.
        if b_im_rect[2] > intersection[2] * 1.1 or b_im_rect[3] > intersection[3] * 1.1:
            # This is just to make sure there are no blank parts when cropping and
            # then rotating
            # TODO: move these 10% into calc_img_buffer_rect()
            intersection = (intersection[0] - 0.1 * intersection[2],
                            intersection[1] - 0.1 * intersection[3],
                            1.2 * intersection[2],
                            1.2 * intersection[3])
            im_data, tl = get_sub_img(intersection, b_im_rect, im_data, total_scale)
            b_im_rect = (tl[0], tl[1], b_im_rect[2], b_im_rect[3],)
            x, y, _, _ = b_im_rect

    if im_data.metadata.get('dc_keepalpha', True):
        im_format = cairo.FORMAT_ARGB32
    else:
        im_format = cairo.FORMAT_RGB24

    height, width, _ = im_data.shape

    # Note: Stride calculation is done automatically when no stride parameter is provided.
    stride = cairo.ImageSurface.format_stride_for_width(im_format, width)

    imgsurface = cairo.ImageSurface.create_for_data(im_data, im_format, width, height, stride)

    # In Cairo a pattern is the 'paint' that it uses to draw
    surfpat = cairo.SurfacePattern(imgsurface)

    if interpolate_data:
        # Since cairo v1.14, FILTER_BEST is different from BILINEAR.
        # Downscaling and upscaling < 2x is nice, but above that, it just
        # makes the pixels big (and antialiased)
        if total_scale_x > 2:
            surfpat.set_filter(cairo.FILTER_BILINEAR)
        else:
            surfpat.set_filter(cairo.FILTER_BEST)
    else:
        surfpat.set_filter(cairo.FILTER_NEAREST)  # FAST

    ctx.translate(x, y)
    ctx.scale(total_scale_x, total_scale_y)

    ctx.set_source(surfpat)
    ctx.set_operator(blend_mode)

    if opacity < 1.0:
        ctx.paint_with_alpha(opacity)
    else:
        ctx.paint()

    # Restore the cached transformation matrix
    ctx.restore()


def ar_to_export_data(projections, raw=False):
    """
    Creates either raw or WYSIWYG representation for the AR projection.
    :param projections: (list of projection objects) projections displayed in the current view.
    :param raw: (boolean) If True returns raw representation of the data.
    :returns: (model.DataArray or dict of images)
            If raw, returns a 2D array with axes phi/theta -> intensity (equi-rectangular projection).
            Otherwise, returns a 3D DataArray corresponding to a greyscale RGBA view of the polar projection,
            with the axes drawn over it. If polarization or polarimetry VAs present, all images for
            the requested ebeam position will be returned in a dictionary. If only one polarization position
            is available, a 3D DataArray will be returned.
    """
    # Logo for legend
    logo = "legend_logo_delmic_black.png"

    # we expect just one stream
    if len(projections) == 0:
        raise LookupError("No stream to export")
    elif len(projections) > 1:
        logging.warning("More than one stream exported to AR, will only use the first one.")

    projection = projections[0]

    if raw:  # csv
        # single image for raw AR data (phi/theta representation) for one ebeam pos
        # if multiple images per ebeam pos (e.g. polarization or polarimetry data): batch export
        return projection.projectAsRaw()

    else:  # png, tiff
        # single image for visualized AR data for one ebeam pos
        # batch export for polarization analyzer raw data and polarimetry results for one ebeam pos
        data_dict = projection.projectAsVis()

        # create the image with web overlay, legend etc.
        for pol_mode, stream_im in data_dict.items():
            if stream_im is None:
                raise LookupError("Stream %s has no data selected" % (projection.name.value,))

            # Image is always centered, fitting the whole canvas (+ a margin of 10 %)
            if stream_im.shape[0] < MIN_AR_SIZE or stream_im.shape[1] < MIN_AR_SIZE:
                # Increase the size of the image to fit the minimum size
                scale = MIN_AR_SIZE / min(stream_im.shape[:2])
                im_scale = (scale, scale)
            else:  # Use the image as-is, and adjust the overlay/legend size to fit
                im_scale = (1.0, 1.0)

            im_size = int(round(stream_im.shape[0] * im_scale[0])), int(round(stream_im.shape[1] * im_scale[1]))
            ar_margin = int(0.2 * im_size[0])
            buffer_size = im_size[0] + ar_margin, im_size[1] + ar_margin

            # Create a cairo surface to draw the final image
            data_to_draw = numpy.zeros((buffer_size[1], buffer_size[0], 4), dtype=numpy.uint8)
            surface = cairo.ImageSurface.create_for_data(
                data_to_draw, cairo.FORMAT_ARGB32, buffer_size[0], buffer_size[1])
            ctx = cairo.Context(surface)

            plot_im = format_rgba_darray(stream_im)  # RGB -> BGRA for cairo
            plot_im.metadata['dc_keepalpha'] = False  # The alpha channel is garbage => don't use it
            draw_image(
                ctx,
                plot_im,
                p_im_center=(0, 0),
                buffer_center=(0, 0),
                buffer_scale=(1.0, 1.0),
                buffer_size=buffer_size,
                im_scale=im_scale,
                interpolate_data=True
            )

            font_name = "Sans"
            ticksize = 10
            num_ticks = 6
            ticks_info = ar_create_tick_labels(im_size, ticksize, num_ticks, ar_margin / 2)
            ticks, (center_x, center_y), inner_radius, radius = ticks_info
            draw_ar_frame(ctx, buffer_size, ticks, font_name, center_x, center_y, inner_radius, radius)
            draw_ar_spiderweb(ctx, center_x, center_y, radius)

            # Draw legend
            date = stream_im.metadata.get(model.MD_ACQ_DATE)
            legend_rgb = draw_legend_simple(stream_im, buffer_size, date,
                                            img_file=logo, bg_color=(1, 1, 1), text_color=(0, 0, 0))
            data_with_legend = numpy.append(data_to_draw, legend_rgb, axis=0)
            format_bgra_to_rgb(data_with_legend, inplace=True)
            ar_plot_final = model.DataArray(data_with_legend, metadata={model.MD_DIMS: "YXC"})
            data_dict[pol_mode] = ar_plot_final

        # TODO this needs to be redone when we can handle batch export in export.py
        # as we now distinguish between a dict and array export for handling the data
        if len(data_dict) > 1:
            return data_dict
        else:  # only return the array
            return next(iter(data_dict.values()))


def guess_sig_num_rng(rng: list, v: numbers.Real=None) -> Optional[int]:
    """
    Guess a significant numbers to keep in values to display for being user-friendly
    rng (None or list of float with len>=2): the range of values to be shown.
      Only the first and last are cared of. In case of a list, it may contain NaNs,
      in which case they are omitted.
    v (Real): if passed, and the range is not informative, the type will be used
      to guess the significant numbers
    return: the number of figures to keep, or None if all should be kept
    """
    if rng is None:
        rng = [0, 0]

    minr = numpy.nanmin(rng)
    maxr = numpy.nanmax(rng)
    if minr == maxr:
        # Can't use range => rely on the type
        return None if isinstance(v, numbers.Integral) else 3
    else:
        # Compare the biggest value displayed (with or without the "-" sign) to
        # the range of values. If they are very different, we want to include
        # more significant numbers. (ex, rng = [17999, 18003], we need at least 5
        # significant numbers, and even more if floating point => return 7)
        max_abs = max(abs(minr), abs(maxr))
        ratio_rng = max_abs / (maxr - minr)
        return 2 + math.ceil(math.log10(ratio_rng) + 0.5)


def value_to_pixel(value, pixel_space, value_range, orientation):
    """
    Map value within a range to pixel position on an axis

    value (float): value to map
    pixel_space (int): length of space available to draw, in pixels
    value_range (list of floats of len>=2): values at linearly spread intervals
      The values have to be either in increasing order or decreasing order.
      It may contain a series of NaN at the beginning and at the end. In this case,
      the NaN are considered empty space.
    orientation (wx.VERTICAL or wx.HORIZONTAL): legend orientation
       When vertical, going from bottom to top.
       When horizontal, going from left to right.

    returns (0<=int<pixel_space): pixel position
    """
    if pixel_space is None:
        return None

    if None in value_range:
        return None

    # Handle NaN before and after by taking them away, and running the interpolation
    # only within the "finite" part of value_range. Then, we have to just shift
    # the position by the space taken by the NaNs at the beginning.
    # Note that another way to handle it would be to replace the NaNs by the same
    # value as the first finite value (left) and last one (right), then numpy.interp()
    # would almost return the correct pixel... we'd just need to handle explicitly
    # the cases of the min or max are requested. Eventually, this is about as
    # complicated, and would required creating new arrays.
    i_first, i_last = find_first_last_finite_indices(value_range)
    value_range_finite = value_range[i_first:i_last + 1]
    assert value_range_finite[0] <= value <= value_range_finite[-1] or value_range_finite[-1] <= value <= value_range_finite[0]

    # If no NaNs, pixel_space_finite == pixel_space
    pixel_space_finite = pixel_space * (1 + i_last - i_first) / len(value_range)

    # if going from big to small, reverse temporarily from small to big and we'll
    # reverse the result at the end
    reverse = value_range_finite[0] > value_range_finite[-1]
    if reverse:
        value_range_finite = value_range_finite[::-1]
        # nan_shift is 0 if i_last is the last value
        nan_shift = pixel_space * (len(value_range) - (i_last + 1)) / len(value_range)
    else:
        # nan_shift is 0 if i_first is the first value
        nan_shift = pixel_space * i_first / len(value_range)

    # Find the two points in the range which are the closest value, then linearly interpolate
    # Example       |----------------|-----------------|
    # value range:  1                10                100
    # value:                                 ^50
    # pixel space:  0                                  1000
    # pixel pos:                             722
    pos_px = numpy.interp(value,
                          value_range_finite,
                          numpy.linspace(0, pixel_space_finite - 1, len(value_range_finite)))
    pos_px += nan_shift

    # For historical reasons, vertically, we report 0 at the end / bottom, unless
    # of course, it's reversed
    if orientation == wx.VERTICAL:
        reverse = not reverse

    if reverse:
        pos_px = (pixel_space - 1) - pos_px

    return int(round(pos_px))


def pixel_to_value(pos_px, pixel_space, value_range, orientation):
    """
    Map a position along an axis representing a range to its value.
    It does the opposite of value_to_pixel()

    pos_px (0<=int<pixel_space): position
    pixel_space (int): length of space available to draw, in pixels
    value_range (list of floats of len>=2): values at linearly spread intervals
      The values have to be either in increasing order or decreasing order.
      There shouldn't be any NaN or inf.
    orientation (wx.VERTICAL or wx.HORIZONTAL): orientation
       When vertical, going from bottom to top.
       When horizontal, going from left to right.

    returns (float): value
    """
    if pixel_space is None:
        return None

    if None in value_range:
        return None

    if math.nan in value_range or math.inf in value_range:
        raise ValueError("value_range contains NaN or inf, which is not supported")

    assert 0 <= pos_px <= pixel_space - 1

    # if going from big to small, reverse temporarily from small to big and we'll
    # reverse the result at the end
    reverse = value_range[0] > value_range[-1]
    if reverse:
        value_range = value_range[::-1]

    # For historical reasons, vertically, we report 0 at the end / bottom, unless
    # of course, it's reversed
    if orientation == wx.VERTICAL:
        reverse = not reverse

    if reverse:
        pos_px = (pixel_space - 1) - pos_px

    # Find the two values in the range which are the nearest to the proportional
    # position of the pixel in the pixel space.

    # Find the two points in the range which are the closest value, then linearly interpolate
    # Example       |----------------|-----------------|
    # pixel space:  0                                  1000
    # pixel pos:                             ^722
    # value range:  1                10                100
    # value:                                 50
    val = numpy.interp(pos_px, numpy.linspace(0, pixel_space - 1, len(value_range)), value_range)

    return val


def calculate_ticks(value_range, client_size, orientation, tick_spacing):
    """
    Calculate which values in the range to represent as ticks on the axis

    value_range (list of floats of len>=2): values at linearly spread intervals.
      It must be ordered, either increasing or decreasing.
      It may contain a series of NaN at the beginning and at the end. In this case,
      the NaN are considered empty space.
    client_size (int > 0, int > 0): number of pixels in X,Y
    orientation (wx.HORIZONTAL or wx.VERTICAL): legend orientation
    tick_spacing (float > 0): approximate space between ticks (number of pixels)

    returns (list of tuples of floats): list of pixel position and value pairs
    """

    if value_range is None:
        logging.info("Trying to compute legend tick without range")
        return None

    # Get the horizontal/vertical space in pixels
    if orientation == wx.HORIZONTAL:
        pixel_space = client_size[0]
        # Don't display ticks too close from the left border
        min_pixel = 10
    else:
        # Don't display ticks too close from the border
        pixel_space = client_size[1]
        min_pixel = 10

    # Skip the NaNs. We don't check that the NaNs are just on the border. We
    # just assume it's so, and if not, the output will be incorrect.
    min_val = numpy.nanmin(value_range)
    max_val = numpy.nanmax(value_range)

    # Range width
    value_space = abs(max_val - min_val)
    if value_space == 0:
        logging.info("Trying to compute legend tick with empty range %s", value_range)
        # Just one tick, at the origin
        pixel = max(min_pixel, value_to_pixel(min_val, pixel_space, value_range, orientation))
        tick_list = [(pixel, min_val)]
        return tick_list

    epsilon = (value_space / pixel_space) / 10  # "tiny" value: a 10th of a pixel

    # Find the ticks to show so that it looks good for the user. It must have a
    # constant spacing between the ticks, and this spacing should be a "easy
    # number": 1, 2, 5, 7.5, or any of these values multiplied by a power of 10.
    # For example, 150 is not good, but 200 is fine.

    # minimum number of ticks that we want
    num_ticks = max(2, pixel_space // tick_spacing + 1)

    # minimum tick spacing for getting the number of ticks
    min_value_step = abs(value_space) / (num_ticks - 1)

    # Start with the power of 10 just below
    power = math.floor(math.log10(min_value_step))

    for step in [1, 2, 5, 7.5, 10]:
        value_step = step * 10 ** power
        if value_step > min_value_step:
            break
    logging.debug("Value step is %s with range %s and requested spacing %s px",
                  value_step, value_space, tick_spacing)

    first_val = (int(min_val / value_step) + 1) * value_step
    # logging.debug("Setting first tick at value %s", first_val)

    tick_values = [min_val] if min_val == 0 else []
    cur_val = first_val
    while cur_val < max_val:
        tick_values.append(cur_val)
        cur_val += value_step
    if len(tick_values) < 2:
        logging.debug("Only got ticks %s, while wanted at least %d, will fallback to min/max as axis ticks",
                      tick_values, num_ticks)
        # Fallback to something that work always:
        tick_values = [min_val, max_val]

    ticks = []
    min_margin = (tick_spacing / 4)
    prev_pixel = 0
    for tick_value in tick_values:
        pixel = value_to_pixel(tick_value, pixel_space, value_range, orientation)
        # Round "almost 0" (due to floating point errors) to 0
        if abs(tick_value) < epsilon:
            tick_value = 0

        pix_val = (pixel, tick_value)
        if pix_val not in ticks:
            if (tick_value not in (numpy.min(tick_values), numpy.max(tick_values)) and
                    abs(pixel - prev_pixel) < min_margin):
                # keep a min distance between ticks
                continue
            if min_pixel <= pixel <= pixel_space:
                ticks.append(pix_val)
                prev_pixel = pixel

    tick_list = ticks

    return tick_list


def find_first_last_finite_indices(l: list) -> Tuple[int, int]:
    """
    l (list of floats)
    return int, int: the indices of the first value non NaN and last value non NaN
    raise IndexError: if all values are NaNs
    """
    first_finite = 0
    for i, v in enumerate(l):
        if math.isfinite(v):
            first_finite = i
            break
    else:
        raise IndexError(f"All {len(l)} values are NaN")

    for i, v in enumerate(l[::-1]):
        if math.isfinite(v):
            last_finite = len(l) - i - 1
            break

    return first_finite, last_finite


def draw_scale(value_range, client_size, orientation, tick_spacing,
               fill_colour, unit, font_size, scale_label=None, mirror=False):
    """
    Draws horizontal or vertical scale bar

    value_range (tuple of floats): value range
    client_size (int, int): number of pixels in X/Y
    orientation (int): legend orientation
    tick_spacing (float): space between ticks
    fill_colour (tuple of floats): colour to draw bar and tick
    unit (str): scale unit
    font_size (float)
    scale_label (str or None): label to be attached
    mirror (boolean): if True: in case of horizontal bar means scale bar goes to the
        top side of the plot and in case of vertical scale bar goes to the right side
        of the plot. The tick values is not written.
    return (numpy array of shape YXC with uint8): BGRA image containing the scale bar
    """
    if value_range is None:
        logging.info("Not drawing scale bar, as range is None")
        return

    # TODO: Instead of just the min/max value, support a whole list of values,
    # so that if they are not linearly distributed, the ticks are shown at the
    # right place.

    im = numpy.full((client_size[1], client_size[0], 4), 255, dtype=numpy.uint8)
    surface = cairo.ImageSurface.create_for_data(im, cairo.FORMAT_ARGB32,
                                                 client_size[0], client_size[1])
    ctx = cairo.Context(surface)

    tick_list = calculate_ticks(value_range, client_size, orientation, tick_spacing)

    # Set Font
    font_name = "Sans"
    ctx.select_font_face(font_name, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(font_size)

    ctx.set_source_rgb(*fill_colour)
    ctx.set_line_width(3)
    ctx.set_line_join(cairo.LINE_JOIN_MITER)

    if orientation == wx.VERTICAL:
        if mirror:
            ctx.move_to(0, 0)
            ctx.line_to(0, client_size[1])
            ctx.stroke()
        else:
            ctx.move_to(client_size[0], 0)
            ctx.line_to(client_size[0], client_size[1])
            ctx.stroke()
    else:
        if mirror:
            ctx.move_to(0, client_size[1])
            ctx.line_to(client_size[0], client_size[1])
            ctx.stroke()
        else:
            ctx.move_to(0, 0)
            ctx.line_to(client_size[0], 0)
            ctx.stroke()

    max_width = 0
    prev_lpos = 0 if orientation == wx.HORIZONTAL else client_size[1]


    if scale_label:
        ctx.save()
        prefix = ""
        if unit not in units.IGNORE_UNITS:
            # Find the best unit prefix
            absv = sorted(abs(v) for p, v in tick_list)
            midv = absv[(len(absv) - 1) // 2]
            divisor, prefix = units.get_si_scale(midv)
            tick_list = [(p, v / divisor) for p, v in tick_list]
        scale_label += u" (%s%s)" % (prefix, unit)
        _, _, lbl_width, _, _, _ = ctx.text_extents(scale_label)
        # TODO: probably not correctly placed in case of mirror (but no one cares)
        if orientation == wx.HORIZONTAL:
            ctx.move_to(((client_size[0] - client_size[1]) / 2) - lbl_width / 2,
                        client_size[1] - int(font_size * 0.3))
        else:
            ctx.move_to(int(font_size * 1.2),
                        ((client_size[1] - client_size[0]) / 2) + lbl_width / 2)
            ctx.rotate(-math.pi / 2)
        ctx.show_text(scale_label)
        ctx.restore()
        # Don't write the unit next to each tick, label is enough
        unit = None

    for i, (pos, val) in enumerate(tick_list):
        label = units.readable_str(val, unit, 3)  # units.to_string_pretty(v, sig)
        _, _, lbl_width, lbl_height, _, _ = ctx.text_extents(label)

        if orientation == wx.HORIZONTAL:
            lpos = pos - (lbl_width // 2)
            lpos = max(min(lpos, client_size[0] - lbl_width - 2), 2)
            if prev_lpos < lpos:
                if mirror:
                    # ctx.move_to(lpos, client_size[1] - (lbl_height - 3))
                    # ctx.show_text(label)
                    ctx.move_to(pos, client_size[1] - 5)
                    ctx.line_to(pos, client_size[1])
                else:
                    ctx.move_to(lpos, lbl_height + 17)
                    ctx.show_text(label)
                    ctx.move_to(pos, 5)
                    ctx.line_to(pos, 0)

                prev_lpos = lpos + lbl_width
        else:
            max_width = max(max_width, lbl_width)
            lpos = pos + (lbl_height // 2)
            lpos = max(min(lpos, client_size[1]), lbl_height)

            if abs(prev_lpos - lpos) > 20 or i == 0 or i == len(tick_list):
                if mirror:
#                     ctx.move_to(client_size[0] - lbl_width - 9, client_size[1] - lpos)
#                     ctx.show_text(label)
                    ctx.move_to(client_size[0] - 8, pos)
                    ctx.line_to(client_size[0] - 4, pos)
                else:
                    ctx.move_to(client_size[0] - lbl_width - 17, lpos)
                    ctx.show_text(label)
                    ctx.move_to(client_size[0] - 5, pos)
                    ctx.line_to(client_size[0], pos)

                prev_lpos = lpos + lbl_height

        ctx.stroke()

    return im


def val_x_to_pos_x(val_x, client_size, range_x):
    """ Translate an x value to an x position in pixels
    The minimum x value is considered to be pixel 0 and the maximum is the canvas width. The
    parameter will be clipped if it's out of range.
    val_x (float): The value to map
    client_size (int, int)
    returns (float)
    """
    data_width = range_x[1] - range_x[0]

    if data_width:
        # Clip val_x
        x = min(max(range_x[0], val_x), range_x[1])
        perc_x = (x - range_x[0]) / data_width
        return perc_x * client_size[0]
    else:
        return 0


def val_y_to_pos_y(val_y, client_size, range_y):
    """ Translate an y value to an y position in pixels
    The minimum y value is considered to be pixel 0 and the maximum is the canvas width. The
    parameter will be clipped if it's out of range.
    val_y (float): The value to map
    client_size (int, int)
    returns (float)
    """
    data_height = range_y[1] - range_y[0]
    if data_height == 0:
        data_height = range_y[1]

    if data_height:
        y = min(max(range_y[0], val_y), range_y[1])
        perc_y = (range_y[1] - y) / data_height
        return perc_y * client_size[1]
    else:
        return 0


def bar_plot(ctx, data, range_x, range_y, client_size, fill_colour):
    """ Do a bar plot of the current `_data` 
    data needs to be a list or ndarray, not an iterator
    """

    if len(data) < 2:
        return

    line_to = ctx.line_to
    ctx.set_source_rgb(*fill_colour)

    diff = (data[1][0] - data[0][0]) / 2
    px = val_x_to_pos_x(data[0][0] - diff, client_size, range_x)
    py = val_y_to_pos_y(0, client_size, range_y)

    ctx.move_to(px, py)
    # print "-", px, py

    for i, (vx, vy) in enumerate(data[:-1]):
        py = val_y_to_pos_y(vy, client_size, range_y)
        # print "-", px, py
        line_to(px, py)
        px = val_x_to_pos_x((data[i + 1][0] + vx) / 2, client_size, range_x)
        # print "-", px, py
        line_to(px, py)

    py = val_y_to_pos_y(data[-1][1], client_size, range_y)
    # print "-", px, py
    line_to(px, py)

    diff = (data[-1][0] - data[-2][0]) / 2
    px = val_x_to_pos_x(data[-1][0] + diff, client_size, range_x)
    # print "-", px, py
    line_to(px, py)

    py = val_y_to_pos_y(0, client_size, range_y)
    # print "-", px, py
    line_to(px, py)

    ctx.close_path()
    ctx.fill()


def clip_data_window(hrange, vrange, xd, yd):
    """
    Clip  a window from data values (xd, yd) using two range tuples
    hrange and vrange
    returns: xs, ys (the clipped dataset as a tuple)
    """

    # Using the selected horizontal range, define a window
    # for display of the data.
    lo, hi = hrange

    # Find the index closest to the range extremes
    lox = numpy.searchsorted(xd, lo, side="left")
    hix = numpy.searchsorted(xd, hi, side="right")

    # normal case
    if lox != hix:
        xs = xd[lox:hix]
        ys = yd[lox:hix]
    # otherwise we are zoomed in so much that only a single point is visible.
    # Therefore just display a single bar that fills the the panel
    else:
        xs = [(hi + lo) / 2]
        ys = [yd[lox]]

    # Add a few points onto the beginning and end of the array
    # to prevent gaps in the data from appearing
    if lox > 0 and xs[0] != lo:
        xs = numpy.insert(xs, 0, xd[lox - 1])
        ys = numpy.insert(ys, 0, yd[lox - 1])

    if hix < len(xd) and xs[-1] != hi:
        xs = numpy.append(xs, xd[hix])
        ys = numpy.append(ys, yd[hix])

    # Clip y
    ys = numpy.clip(ys, vrange[0], vrange[1])

    # Redefine the data to export with the clipped data
    return xs, ys


def spectrum_to_export_data(proj, raw, vp=None):
    """
    Creates either the raw or the representation as shown in the viewport in the GUI
    (WYSIWYG) of the spectrum data plot for export.
    :param proj: (SinglePointSpectrumProjection) A spectrum projection.
    :param raw: (boolean) If True, returns raw representation.
    :param vp: (Viewport or None) The viewport selected for export to get the data displayed
               in the viewport.
    :returns: (model.DataArray) The data array to export.
    TODO: Instead of a viewport, pass a gui.model.StreamView. That (special) view
    would have the corresponding VAs hrange and vrange.
    The NavigablePlotViewport would just read/write them,
    whenever the user moves or zooms around.
    """
    if raw:  # csv
        data = proj.projectAsRaw()
        if data is None:
            raise LookupError("No pixel selected to pick a spectrum")
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
        return data
    else:  # tiff, png
        spec = proj.image.value
        if spec is None:
            raise LookupError("No pixel selected to pick a spectrum")
        spectrum_range, unit = spectrum.get_spectrum_range(spec)

        # Draw spectrum bar plot
        fill_colour = BAR_PLOT_COLOUR
        client_size = (SPEC_PLOT_SIZE, SPEC_PLOT_SIZE)
        data_to_draw = numpy.full((client_size[1], client_size[0], 4), 255, dtype=numpy.uint8)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, client_size[0], client_size[1])
        ctx = cairo.Context(surface)

        if vp is not None:
            # Limit to the displayed ranges
            range_x = vp.hrange.value
            range_y = vp.vrange.value
            # Check if the range is near 0, and if so, extend it slightly to include it.
            # We define "near" as 10% of the range.
            ext = (range_y[1] - range_y[0]) * 0.1
            if range_y[0] - ext <= 0 <= range_y[1] + ext:
                range_y_and_0 = sorted([range_y[0], 0, range_y[1]])
                range_y = range_y_and_0[0], range_y_and_0[-1]
            spectrum_range, spec = clip_data_window(range_x, range_y, spectrum_range, spec)
        else:
            # calculate data characteristics
            min_x = min(spectrum_range)
            max_x = max(spectrum_range)
            min_y = min(spec)
            max_y = max(spec)
            range_x = (min_x, max_x)
            range_y = (min_y, max_y)

        data = list(zip(spectrum_range, spec))
        bar_plot(ctx, data, range_x, range_y, client_size, fill_colour)

        # Differentiate the scale bar colour so the user later on
        # can easily change the bar plot or the scale bar colour
        text_colour = (0, 0, 0)

        # Draw bottom horizontal scale legend
        tick_spacing = client_size[0] // TICKS_PER_AXIS
        font_size = client_size[0] * SPEC_FONT_SIZE
        scale_x_draw = draw_scale(range_x, (client_size[0], SPEC_SCALE_HEIGHT), wx.HORIZONTAL,
                              tick_spacing, text_colour, unit, font_size, "Wavelength")
        data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

        # Draw top horizontal scale legend
        scale_x_draw = draw_scale(range_x, (client_size[0], SMALL_SCALE_WIDTH), wx.HORIZONTAL,
                              tick_spacing, text_colour, unit, font_size, None,
                              mirror=True)
        data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

        # Draw left vertical scale legend
        tick_spacing = client_size[1] // 6
        scale_y_draw = draw_scale(range_y, (SPEC_SCALE_WIDTH, client_size[1]), wx.VERTICAL,
                              tick_spacing, text_colour, "cts", font_size, "Intensity")

        # Extend y scale bar to fit the height of the bar plot with the x scale bars attached
        extend = numpy.full((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), 255, dtype=numpy.uint8)
        scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
        data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

        # Draw right vertical scale legend
        scale_y_draw = draw_scale(range_y, (SMALL_SCALE_WIDTH, client_size[1]), wx.VERTICAL,
                      tick_spacing, text_colour, "cts", font_size, None,
                      mirror=True)

        # Extend y scale bar to fit the height of the bar plot with the x scale bars attached
        scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
        data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

        spec_plot = model.DataArray(data_with_legend)
        spec_plot.metadata[model.MD_DIMS] = 'YXC'
        return spec_plot


def chronogram_to_export_data(proj, raw, vp=None):
    """
    Creates either the raw or the representation as shown in the viewport in the GUI
    (WYSIWYG) of the time data plot for export.
    :param proj: (SinglePointTemporalProjection) A chronogram projection.
    :param raw: (boolean) If True, returns raw representation.
    :param vp: (Viewport or None) The viewport selected for export to get the data displayed
               in the viewport.
    :returns: (model.DataArray) The data array to export.
    """
    # TODO check if this needs to be done, and if yes implement for other exports as well
    if not isinstance(proj, SinglePointTemporalProjection):
        raise ValueError("Trying to export a time spectrum of an invalid projection")

    if raw:  # csv
        data = proj.projectAsRaw()
        if data is None:
            raise LookupError("No pixel selected to pick a chronogram")
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
        return data
    else:  # tiff, png
        spec = proj.image.value
        if spec is None:
            raise LookupError("No pixel selected to pick a chronogram")
        time_range, unit = spectrum.get_time_range(spec)

        # Draw spectrum bar plot
        fill_colour = BAR_PLOT_COLOUR
        client_size = (SPEC_PLOT_SIZE, SPEC_PLOT_SIZE)
        data_to_draw = numpy.full((client_size[1], client_size[0], 4), 255, dtype=numpy.uint8)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, client_size[0], client_size[1])
        ctx = cairo.Context(surface)

        if vp is not None:
            # Limit to the displayed ranges
            range_x = vp.hrange.value
            range_y = vp.vrange.value
            time_range, spec = clip_data_window(range_x, range_y, time_range, spec)
        else:
            # calculate data characteristics
            min_x = min(time_range)
            max_x = max(time_range)
            min_y = min(spec)
            max_y = max(spec)
            range_x = (min_x, max_x)
            range_y = (min_y, max_y)

        data = list(zip(time_range, spec))
        bar_plot(ctx, data, range_x, range_y, client_size, fill_colour)

        # Differentiate the scale bar colour so the user later on
        # can easily change the bar plot or the scale bar colour
        text_colour = (0, 0, 0)

        # Draw bottom horizontal scale legend
        tick_spacing = client_size[0] // TICKS_PER_AXIS
        font_size = client_size[0] * SPEC_FONT_SIZE
        scale_x_draw = draw_scale(range_x, (client_size[0], SPEC_SCALE_HEIGHT), wx.HORIZONTAL,
                              tick_spacing, text_colour, unit, font_size, "Time")
        data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

        # Draw top horizontal scale legend
        scale_x_draw = draw_scale(range_x, (client_size[0], SMALL_SCALE_WIDTH), wx.HORIZONTAL,
                              tick_spacing, text_colour, unit, font_size, None,
                              mirror=True)
        data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

        # Draw left vertical scale legend
        tick_spacing = client_size[1] // TICKS_PER_AXIS
        scale_y_draw = draw_scale(range_y, (SPEC_SCALE_WIDTH, client_size[1]), wx.VERTICAL,
                              tick_spacing, text_colour, "cts", font_size, "Intensity")

        # Extend y scale bar to fit the height of the bar plot with the x scale bars attached
        extend = numpy.full((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), 255, dtype=numpy.uint8)
        scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
        data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

        # Draw right vertical scale legend
        scale_y_draw = draw_scale(range_y, (SMALL_SCALE_WIDTH, client_size[1]), wx.VERTICAL,
                              tick_spacing, text_colour, "cts", font_size, None,
                              mirror=True)

        # Extend y scale bar to fit the height of the bar pl
        # ot with the x scale bars attached
        scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
        data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

        spec_plot = model.DataArray(data_with_legend)
        spec_plot.metadata[model.MD_DIMS] = 'YXC'
        return spec_plot


def theta_to_export_data(proj, raw, vp=None):
    """
    Creates either the raw or the representation as shown in the viewport in the GUI of the theta data plot for export.
    :param proj: (SinglePointAngularProjection) A theta projection.
    :param raw: (boolean) If True, returns raw representation.
    :param vp: (Viewport or None) The viewport selected for export to get the data displayed
               in the viewport.
    :returns: (model.DataArray) The data array to export.
    """
    if not isinstance(proj, SinglePointAngularProjection):
        raise ValueError("Trying to export an angle spectrum of an invalid projection")

    if raw:  # csv
        data = proj.projectAsRaw()
        if data is None:
            raise LookupError("No pixel selected to pick a angle")
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_EK
        return data
    else:  # tiff, png
        spec = proj.image.value
        if spec is None:
            raise LookupError("No pixel selected to pick a angle")
        angle_range, unit_a = spectrum.get_angle_range(spec)
        if unit_a == "rad":
            unit_a = "°"
            angle_range = [math.degrees(angle) for angle in angle_range]  # Converts radians to degrees

        # Draw spectrum bar plot
        fill_colour = BAR_PLOT_COLOUR
        client_size = (SPEC_PLOT_SIZE, SPEC_PLOT_SIZE)
        data_to_draw = numpy.full((client_size[1], client_size[0], 4), 255, dtype=numpy.uint8)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, client_size[0], client_size[1])
        ctx = cairo.Context(surface)

        if vp is not None:
            # Limit to the displayed ranges
            range_x = vp.hrange.value
            range_y = vp.vrange.value
            angle_range, spec = clip_data_window(range_x, range_y, angle_range, spec)
        else:
            # calculate data characteristics
            min_x = min(angle_range)
            max_x = max(angle_range)
            min_y = min(spec)
            max_y = max(spec)
            range_x = (min_x, max_x)
            range_y = (min_y, max_y)

        data = list(zip(angle_range, spec))
        bar_plot(ctx, data, range_x, range_y, client_size, fill_colour)

        # Differentiate the scale bar colour so the user later on
        # can easily change the bar plot or the scale bar colour
        text_colour = (0, 0, 0)

        # Draw bottom horizontal scale legend
        tick_spacing = client_size[0] // TICKS_PER_AXIS
        font_size = client_size[0] * SPEC_FONT_SIZE
        scale_x_draw = draw_scale(range_x, (client_size[0], SPEC_SCALE_HEIGHT), wx.HORIZONTAL,
                              tick_spacing, text_colour, unit_a, font_size, "Angle")
        data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

        # Draw top horizontal scale legend
        scale_x_draw = draw_scale(range_x, (client_size[0], SMALL_SCALE_WIDTH), wx.HORIZONTAL,
                              tick_spacing, text_colour, unit_a, font_size, None,
                              mirror=True)
        data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

        # Draw left vertical scale legend
        tick_spacing = client_size[1] // TICKS_PER_AXIS
        scale_y_draw = draw_scale(range_y, (SPEC_SCALE_WIDTH, client_size[1]), wx.VERTICAL,
                              tick_spacing, text_colour, "cts", font_size, "Intensity")

        # Extend y scale bar to fit the height of the bar plot with the x scale bars attached
        extend = numpy.full((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), 255, dtype=numpy.uint8)
        scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
        data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

        # Draw right vertical scale legend
        scale_y_draw = draw_scale(range_y, (SMALL_SCALE_WIDTH, client_size[1]), wx.VERTICAL,
                              tick_spacing, text_colour, "cts", font_size, None,
                              mirror=True)

        # Extend y scale bar to fit the height of the bar plot with the x scale bars attached
        scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
        data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

        spec_plot = model.DataArray(data_with_legend)
        spec_plot.metadata[model.MD_DIMS] = 'YXC'
        return spec_plot


def line_to_export_data(proj, raw):
    """
    Creates either the raw or the representation as shown in the viewport in the GUI
    (WYSIWYG) of the spectrum line data for export.
    :param proj: (LineSpectrumProjection) A line spectrum projection.
    :param raw: (boolean) If True, returns raw representation.
    :returns: (model.DataArray) The data array to export.
    """
    if raw:  # csv
        data = proj.projectAsRaw()
        if data is None:
            raise LookupError("No line selected to pick a spectrum")
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
        return data
    else:  # tiff, png
        spec = proj.image.value
        if spec is None:
            raise LookupError("No line selected to pick a spectrum")
        spectrum_range, unit = spectrum.get_spectrum_range(spec)
        line_length = spec.shape[0] * spec.metadata[model.MD_PIXEL_SIZE][1]

        return _draw_image_graph(spec,
                                 size=(SPEC_PLOT_SIZE, SPEC_PLOT_SIZE),
                                 xrange=spectrum_range,
                                 xunit=unit,
                                 xtitle="Wavelength",
                                 yrange=(0, line_length),
                                 yunit="m",
                                 ytitle="Distance from origin",
                                 flip=wx.VERTICAL,  # X should go from bottom (0) to top (line length)
                                 )

def temporal_spectrum_to_export_data(proj, raw):
    """
    Creates either the raw or the representation as shown in the viewport in the GUI
    (WYSIWYG) of the temporal spectrum data for export.
    :param proj: (RGBSpatialSpectrumProjection) A temporal spectrum projection.
    :param raw: (boolean) If True, returns raw representation.
    :returns: (model.DataArray) The data array to export.
    """

    if raw:  # csv
        data = proj.projectAsRaw()
        if data is None:
            raise LookupError("No pixel selected to pick a temporal-spectrum")
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_TEMPSPECTRUM
        return data
    else:  # tiff, png
        spec = proj.image.value
        if spec is None:
            raise LookupError("No pixel selected to pick a temporal-spectrum")
        spectrum_range, wl_unit = spectrum.get_spectrum_range(spec)
        time_range, t_unit = spectrum.get_time_range(spec)

        return _draw_image_graph(spec,
                                 size=(SPEC_PLOT_SIZE, SPEC_PLOT_SIZE),
                                 xrange=spectrum_range,
                                 xunit=wl_unit,
                                 xtitle="Wavelength",
                                 yrange=time_range[::-1],  # 0 at the top
                                 yunit=t_unit,
                                 ytitle="Time",
                                 )


def angular_spectrum_to_export_data(proj, raw):
    """
    Creates either the raw or the representation as shown in the viewport in the GUI
    of the angular spectrum data for export.
    :param proj: (RGBSpatialSpectrumProjection) An angular spectrum projection.
    :param raw: (boolean) If True, returns raw representation.
    :returns: (model.DataArray) The data array to export.
    """
    if raw:  # csv0
        data = proj.projectAsRaw()
        if data is None:
            raise LookupError("No pixel selected to pick an angular-spectrum")
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_EK
        return data
    else:  # tiff, png
        spec = proj.image.value
        if spec is None:
            raise LookupError("No pixel selected to pick an angular-spectrum")
        spectrum_range, wl_unit = spectrum.get_spectrum_range(spec)
        angle_range, unit_a = spectrum.get_angle_range(spec)
        if unit_a == "rad":
            unit_a = "°"
            angle_range = [math.degrees(angle) for angle in angle_range]  # Convert radians to degrees

        return _draw_image_graph(spec,
                                 size=(SPEC_PLOT_SIZE, SPEC_PLOT_SIZE),
                                 xrange=spectrum_range,
                                 xunit=wl_unit,
                                 xtitle="Wavelength",
                                 yrange=angle_range[::-1],  # reversed to show the low angles at the top
                                 yunit=unit_a,
                                 ytitle="Angle",
                                 )


def _draw_image_graph(im, size, xrange, xunit, xtitle, yrange, yunit, ytitle, flip=0):
    """
    Draw the given RGB image into a X/Y plot
    im (DataArray YXC of uint8): RGB(A) data to be displayed
    size (int, int): number of pixels in X/Y in the generated image
    xrange (float, float): left, right values
    xunit (str): unit of the x axis
    xtitle (str or None): text to describe the x axis
    yrange (float, float): bottom, top values
    yunit (str): unit of the x axis
    ytitle (str or None): text to describe the x axis
    """
    # adjust to viewport size
    scale = (size[0] / im.shape[1], size[1] / im.shape[0])
    # Make surface based on the maximum resolution, with white background
    data_to_draw = numpy.full((size[1], size[0], 4), 255, dtype=numpy.uint8)
    surface = cairo.ImageSurface.create_for_data(data_to_draw, cairo.FORMAT_ARGB32, size[0], size[1])
    ctx = cairo.Context(surface)

    plot_im = format_rgba_darray(im)  # RGB -> BGRA for cairo
    plot_im.metadata['dc_keepalpha'] = False  # The alpha channel is garbage => don't use it
    draw_image(
        ctx,
        plot_im,
        p_im_center=(0, 0),
        buffer_center=(0, 0),
        buffer_scale=(1, 1),
        buffer_size=size,
        im_scale=scale,
        flip=flip,
    )

    # Draw top/bottom horizontal (wavelength) legend
    text_colour = (0, 0, 0)  # black
    tick_spacing = size[0] // TICKS_PER_AXIS  # px, rough goal
    font_size = size[0] * SPEC_FONT_SIZE
    scale_x_draw = draw_scale(xrange, (size[0], SPEC_SCALE_HEIGHT), wx.HORIZONTAL,
                              tick_spacing, text_colour, xunit, font_size, xtitle)
    data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

    # Top
    scale_x_draw = draw_scale(xrange, (size[0], SMALL_SCALE_WIDTH), wx.HORIZONTAL,
                              tick_spacing, text_colour, xunit, font_size, None,
                              mirror=True)
    data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

    # Draw left vertical (distance) legend
    tick_spacing = size[1] // TICKS_PER_AXIS
    scale_y_draw = draw_scale(yrange, (SPEC_SCALE_WIDTH, size[1]), wx.VERTICAL,
                              tick_spacing, text_colour, yunit, font_size, ytitle)
    # Extend y scale bar to fit the height of the bar plot with the x scale bar attached
    extend = numpy.full((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), 255, dtype=numpy.uint8)
    scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
    scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
    data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

    # Right
    scale_y_draw = draw_scale(yrange, (SMALL_SCALE_WIDTH, size[1]), wx.VERTICAL,
                              tick_spacing, text_colour, yunit, font_size, None,
                              mirror=True)
    # Extend y scale bar to fit the height of the bar plot with the x scale bars attached
    scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
    scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
    data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

    data_with_legend = model.DataArray(data_with_legend)
    data_with_legend.metadata[model.MD_DIMS] = 'YXC'
    format_bgra_to_rgb(data_with_legend, inplace=True)
    return data_with_legend


def _draw_file(file, legend_ctx, buffer_size, margin_x, legend_height, cell_x_step, cell_factor=1):
    """
    :param file: (str or None) Name of an image file, that should be displayed in the legend.
    :param legend_ctx: (cairo.Context) The legend object.
    :param buffer_size: (int, int) Size of the output image (original size plus some frame for the legend).
    :param margin_x: (float) The initial x pos to start drawing/writing in the legend.
    :param legend_height: (float) Heights of the legend containing the general information.
    :param cell_x_step: (float) Step in x direction.
    :param cell_factor: (0 < int < 6) Factor to specify in which cell to write the image. A cell is 20% of the
                        full width of the legend.
    """
    img_surface = cairo.ImageSurface.create_from_png(guiimg.getStream(file))
    img_scale_x = ((cell_x_step / 2) - margin_x) / img_surface.get_width()
    legend_ctx.save()
    # Note: Goal of antialiasing & interpolation is to smooth the edges when
    # downscaling the image. It only works with cairo v1.14 or newer.
    surfpat = cairo.SurfacePattern(img_surface)
    # Since cairo v1.14, FILTER_BEST is different from BILINEAR.
    # Downscaling and upscaling < 2x is nice, but above that, it just
    # makes the pixels big (and antialiased)
    if img_scale_x > 2:
        surfpat.set_filter(cairo.FILTER_BILINEAR)
    else:
        surfpat.set_filter(cairo.FILTER_BEST)

    img_h_height = (img_scale_x * img_surface.get_height()) / 2
    # move origin of translation matrix
    legend_ctx.translate(buffer_size[0] - cell_x_step * cell_factor / 2, (legend_height / 2) - img_h_height)
    legend_ctx.scale(img_scale_x, img_scale_x)
    legend_ctx.set_source(surfpat)
    legend_ctx.paint()  # draw the image (paints the current source everywhere within the current clip region)
    legend_ctx.restore()


def _draw_acq_date(date, legend_ctx, buffer_size, legend_x_pos):
    """
    Draws the acquisition date into the legend.
    :param date: (float) Acquisition date to be written to the legend.
    :param legend_ctx: (cairo.Context) The legend object.
    :param buffer_size: (int, int) Size of the output image (original size plus some frame for the legend).
    :param legend_x_pos: (float) The x position to start drawing the acquisition date.
    """
    label = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(date))
    date_split = label.split()
    legend_ctx.show_text(date_split[0])  # write date
    legend_y_pos = MAIN_LOWER * buffer_size[0]  # specify y pos of next row
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_ctx.show_text(date_split[1])  # write time


def draw_legend_simple(image, buffer_size, date, img_file=None, bg_color=(0, 0, 0), text_color=(1, 1, 1)):
    """
    Draws simple legend to be attached to the exported image.
    :param image: (DataArray) Image to draw a legend for.
    :param buffer_size: (int, int) Size of the output image (original size plus some frame for the legend).
    :param date: (float or None) Acquisition date to be attached to the legend.
    :param img_file: (str or None) An image file, that should be displayed in the legend.
    :param bg_color: (int, int, int) The background color of the legend. Default is black.
    :param text_color: (int, int, int) The text color in the legend. Default is white.
    :returns: (ndarray of 3 dims Y,X,4) The legend in RGB.
    """
    # create the legend canvas
    full_shape = (int(buffer_size[0] * MAIN_LAYER), buffer_size[0], 4)
    legend_rgb = numpy.zeros(full_shape, dtype=numpy.uint8)
    legend_surface = cairo.ImageSurface.create_for_data(
                        legend_rgb, cairo.FORMAT_ARGB32,
                        legend_rgb.shape[1], legend_rgb.shape[0])
    legend_ctx = cairo.Context(legend_surface)

    margin_x = buffer_size[0] * CELL_MARGIN  # a bit of space to the canvas edge
    medium_font = buffer_size[0] * MEDIUM_FONT  # used for acquisition date and pol mode

    # Just make cell dimensions analog to the image buffer dimensions
    legend_height = buffer_size[0] * MAIN_LAYER  # big cell containing the general info in the legend
    cell_x_step = buffer_size[0] * CELL_WIDTH  # step in x direction

    # fills the legend canvas with bg color
    legend_ctx.set_source_rgb(*bg_color)
    legend_ctx.rectangle(0, 0, buffer_size[0], legend_height)
    legend_ctx.fill()

    legend_ctx.set_source_rgb(*text_color)
    legend_ctx.set_font_size(medium_font)

    # move to the next position in the legend to write the acquisition date to
    legend_x_pos = cell_x_step/2  # x pos to start writing the acq date at (start where the actual plot starts)
    legend_y_pos = MAIN_UPPER * buffer_size[0]  # y pos in legend to write acq date to
    legend_ctx.move_to(legend_x_pos, legend_y_pos)  # move to pos of acq date

    # write acquisition date
    if date:
        _draw_acq_date(date, legend_ctx, buffer_size, legend_x_pos)

    # move to the next position in the legend to write the polarization mode to
    legend_x_pos += cell_x_step  # x pos in legend to write pol mode to
    legend_y_pos = MAIN_UPPER * buffer_size[0]  # y pos in legend to write pol mode to
    legend_ctx.move_to(legend_x_pos, legend_y_pos)  # move to pos of pol mode

    # write the polarization mode info (polarization analyzer position or polarimetry result)
    if model.MD_POL_MODE in image.metadata:
        legend_ctx.show_text(model.MD_POL_MODE)  # write text for polarization mode
        legend_y_pos = MAIN_LOWER * buffer_size[0]  # specify y pos of next row
        legend_ctx.move_to(legend_x_pos, legend_y_pos)  # move to next row
        legend_ctx.show_text(acqstream.POL_POSITIONS_2_DISPLAY[image.metadata[model.MD_POL_MODE]])  # write pol mode

    # write delmic logo
    if img_file:
        _draw_file(img_file, legend_ctx, buffer_size, margin_x, legend_height, cell_x_step, cell_factor=2)

    return legend_rgb


def draw_legend_multi_streams(images, buffer_size, buffer_scale,
                              hfw, date, stream=None, img_file=None,
                              bg_color=(0, 0, 0), text_color=(1, 1, 1)):
    """
    Draws legend to be attached to the exported image.
    :param images: (list) List of images (dataArray) to draw a legend for.
    :param buffer_size: (int, int) Size of the output image (original size plus some frame for the legend).
    :param buffer_scale: (0<float, 0<float) Scaling factor for size of output image.  TODO please check!
    :param hfw: (numpy.float64 or None) horizontal field width
    :param date: (float or None) Acquisition date to be attached to the legend.
    :param stream: (None or Stream) If provided, the text corresponding to this stream
                   will be indicated by a bullet before the name.
    :param img_file: (str or None) An image file, that should be displayed in the legend.
    :param bg_color: (tuple) The background color of the legend. Default is black.
    :param text_color: (tuple) The text color in the legend. Default is white.
    :returns: (ndarray of 3 dims Y,X,4) The legend in RGB.
    """
    # TODO: get a "raw" parameter to know whether to display in RGB or greyscale
    # TODO: get a better argument (name) than "stream"
    n = len(images)
    full_shape = (n * int(buffer_size[0] * SUB_LAYER) + int(buffer_size[0] * MAIN_LAYER), buffer_size[0], 4)
    legend_rgb = numpy.zeros(full_shape, dtype=numpy.uint8)
    legend_surface = cairo.ImageSurface.create_for_data(
                        legend_rgb, cairo.FORMAT_ARGB32,
                        legend_rgb.shape[1], legend_rgb.shape[0])
    legend_ctx = cairo.Context(legend_surface)

    init_x_pos = buffer_size[0] * CELL_MARGIN
    large_font = buffer_size[0] * LARGE_FONT  # used for general data
    medium_font = buffer_size[0] * MEDIUM_FONT  # used for acquisition date
    small_font = buffer_size[0] * SMALL_FONT  # used for stream data

    arc_radius = buffer_size[0] * ARC_RADIUS
    tint_box_size = buffer_size[0] * TINT_SIZE

    # Just make cell dimensions analog to the image buffer dimensions
    legend_height = buffer_size[0] * MAIN_LAYER  # big cell containing the general info in the legend
    legend_height_stream = buffer_size[0] * SUB_LAYER  # smaller cell containing the stream info in the legend
    cell_x_step = buffer_size[0] * CELL_WIDTH

    # fills the legend canvas with bg color
    legend_ctx.set_source_rgb(*bg_color)
    legend_ctx.rectangle(0, 0, buffer_size[0], n * legend_height_stream + legend_height)  # 1 big + n*stream small cells
    legend_ctx.fill()

    # draw separation lines
    legend_ctx.set_line_width(buffer_size[0] * LINE_THICKNESS)
    legend_ctx.set_source_rgb(*text_color)
    legend_y_pos = legend_height
    legend_ctx.move_to(0, legend_y_pos)
    legend_ctx.line_to(buffer_size[0], legend_y_pos)
    legend_ctx.stroke()
    for i in range(n - 1):
        legend_y_pos += legend_height_stream
        legend_ctx.move_to(0, legend_y_pos)
        legend_ctx.line_to(buffer_size[0], legend_y_pos)
        legend_ctx.stroke()

    legend_x_pos = init_x_pos
    max_bar_width = 2 * cell_x_step - 2 * init_x_pos
    max_actual_width = max_bar_width * buffer_scale[0]
    actual_width = units.round_down_significant(max_actual_width, 1)
    bar_width = int(round(actual_width / buffer_scale[0]))

    # Write: HFW | Scale bar | date | logos
    legend_ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL)
    legend_ctx.set_font_size(large_font)
    legend_y_pos = MAIN_MIDDLE * buffer_size[0]

    # write HFW
    legend_x_pos += cell_x_step
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    hfw = units.round_significant(hfw, 4)
    label = u"HFW: %s" % units.readable_str(hfw, "m", sig=3)
    legend_ctx.show_text(label)

    # Draw scale bar
    legend_ctx.set_line_width(buffer_size[0] * BAR_THICKNESS)
    legend_x_pos += cell_x_step
    legend_y_pos = (legend_height / 2) - (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_y_pos = (legend_height / 2) + (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.line_to(legend_x_pos, legend_y_pos)
    bar_line = bar_width * 0.375
    legend_y_pos = legend_height / 2
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_x_pos += bar_line
    legend_ctx.line_to(legend_x_pos, legend_y_pos)

    label = units.readable_str(actual_width, "m", sig=2)
    plw, _ = legend_ctx.text_extents(label)[2:4]
    # just leave a 10% of the text width as margin
    bar_margin = 0.1 * plw
    legend_x_pos += bar_margin
    legend_y_pos = MAIN_MIDDLE * buffer_size[0]
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_ctx.show_text(label)

    legend_y_pos = legend_height / 2
    legend_x_pos += 1.1 * plw
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_x_pos += bar_line
    legend_ctx.line_to(legend_x_pos, legend_y_pos)
    legend_y_pos = (legend_height / 2) - (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_y_pos = (legend_height / 2) + (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.line_to(legend_x_pos, legend_y_pos)
    legend_ctx.stroke()

    legend_ctx.set_font_size(medium_font)

    # move to the next position in the legend to write the acquisition date to
    legend_x_pos += 2 * cell_x_step - (bar_width + init_x_pos)  # x pos in legend to write acq date to
    legend_y_pos = MAIN_UPPER * buffer_size[0]  # y pos in legend to write acq date to
    legend_ctx.move_to(legend_x_pos, legend_y_pos)  # move to pos of acq date

    # write acquisition date
    if date:
        _draw_acq_date(date, legend_ctx, buffer_size, legend_x_pos)

    # TODO: allow to insert another logo or text
    # => pass a string (= text) or a 2D or 3D numpy array (image)

    # write delmic logo
    if img_file:
        _draw_file(img_file, legend_ctx, buffer_size, init_x_pos, legend_height, cell_x_step)

    # Write stream data, sorted by acquisition date (and fallback on stable order)
    legend_ctx.set_font_size(small_font)
    legend_y_pos = legend_height

    sorted_im = sorted(images, key=lambda im: im.metadata['date'] if im.metadata['date'] else 0)
    for im in sorted_im:
        s = im.metadata['stream']
        md = im.metadata['metadata']
        if s is stream and stream is not None:
            # in case of multifile/raw, spot this particular stream with a
            # circle next to the stream name
            legend_ctx.arc(ARC_LEFT_MARGIN * buffer_size[0],
                           legend_y_pos + ARC_TOP_MARGIN * buffer_size[0],
                           arc_radius, 0, 2 * math.pi)
            legend_ctx.fill()
            legend_ctx.stroke()

        legend_x_pos = init_x_pos
        legend_y_pos += SUB_UPPER * buffer_size[0]
        legend_ctx.move_to(legend_x_pos, legend_y_pos)
        if s:
            legend_ctx.show_text(s.name.value)

        # Handle the stream colormap
        if stream is None and hasattr(s, "tint"):
            tint = s.tint.value
            
            if tint != TINT_RGB_AS_IS:

                tint = img.tintToColormap(tint)
            
                # Draw a gradient of the colormap
                width = int(cell_x_step * 2 * COLORBAR_WIDTH_RATIO)
                height = int(tint_box_size)

                colorbar_start_x = legend_x_pos + cell_x_step * 2 * 0.12
                colorbar_start_y = legend_y_pos + 3

                # draw colorbar scale
                if s.tint.value != TINT_FIT_TO_RGB:
                    legend_ctx.move_to(legend_x_pos, legend_y_pos + SUB_UPPER * buffer_size[0])
                    legend_ctx.show_text(str(s.intensityRange.value[0]))
                    legend_ctx.move_to(legend_x_pos + colorbar_start_x + width - 10, legend_y_pos + SUB_UPPER * buffer_size[0])
                    legend_ctx.show_text(str(s.intensityRange.value[1]))

                legend_ctx.rectangle(colorbar_start_x,
                                 colorbar_start_y,
                                 width, height)
                legend_ctx.fill()

                gradient = img.getColorbar(tint, width - 2, height - 2, alpha=True)
                gradient = format_rgba_darray(gradient)
                surface = cairo.ImageSurface.create_for_data(
                    gradient, cairo.FORMAT_RGB24, gradient.shape[1], gradient.shape[0])

                legend_ctx.set_source_surface(surface, colorbar_start_x + 1,
                                 colorbar_start_y + 1)
                legend_ctx.paint()

                legend_x_pos += cell_x_step
                
            legend_ctx.set_source_rgb(*text_color)

        legend_x_pos += cell_x_step
        legend_y_pos_store = legend_y_pos
        for i, d in enumerate(_get_stream_legend_text(md)):
            legend_ctx.move_to(legend_x_pos, legend_y_pos)
            legend_ctx.show_text(d)
            if i % 2 == 1:
                legend_x_pos += cell_x_step
                legend_y_pos = legend_y_pos_store
            else:
                legend_y_pos += (SUB_LOWER - SUB_UPPER) * buffer_size[0]
        legend_y_pos = (legend_y_pos_store + (legend_height_stream - SUB_UPPER * buffer_size[0]))

    return legend_rgb


def _get_stream_legend_text(md):
    """
    md (dict): the metadata of the (raw) data
    return (list of str): Small pieces of text to display, ordered
    """
    captions = []

    try:
        if model.MD_EXP_TIME in md:
            captions.append(u"Exposure time: %s" % units.readable_str(md[model.MD_EXP_TIME], "s", sig=3))
        if model.MD_DWELL_TIME in md:
            captions.append(u"Dwell time: %s" % units.readable_str(md[model.MD_DWELL_TIME], "s", sig=3))
        if model.MD_LENS_MAG in md:
            captions.append(u"Magnification: %s x" % units.readable_str(md[model.MD_LENS_MAG], sig=2))
        if model.MD_FILTER_NAME in md:
            captions.append(md[model.MD_FILTER_NAME])
        if model.MD_LIGHT_POWER in md:
            captions.append("Power: %s" % units.readable_str(md[model.MD_LIGHT_POWER], "W", sig=3))
        if model.MD_EBEAM_VOLTAGE in md:
            captions.append("Accel: %s" % units.readable_str(abs(md[model.MD_EBEAM_VOLTAGE]), "V", sig=3))
        if model.MD_EBEAM_CURRENT in md:
            captions.append("Current: %s" % units.readable_str(md[model.MD_EBEAM_CURRENT], "A", sig=3))
        if model.MD_IN_WL in md:
            captions.append(u"Excitation: %s" % units.readable_str(numpy.average(md[model.MD_IN_WL]), "m", sig=3))
        if model.MD_POS in md:
            pos = md[model.MD_POS]
            if len(pos) == 3:   # 3D Z stack data
                captions.append(u"Z Position: %s" % units.readable_str(pos[2], "m", sig=3))
        if model.MD_OUT_WL in md:
            out_wl = md[model.MD_OUT_WL]
            if isinstance(out_wl, basestring):
                captions.append(u"Emission: %s" % (out_wl,))
            else:
                captions.append(u"Emission: %s" % units.readable_str(numpy.average(out_wl), "m", sig=3))
        if model.MD_WL_LIST in md:
            wll = md[model.MD_WL_LIST]
            captions.append(u"Wavelength: %s" % fluo.to_readable_band((wll[0], wll[-1])))
    except Exception:
        logging.exception("Failed to export metadata fully")

    return captions


def get_ordered_images(streams, raw=False):
    """ Return the list of images to display, ordered bottom to top (=last to draw)

    The last image of the list will have the merge ratio applied (as opacity)
    """
    images_opt = []
    images_spc = []
    images_std = []

    im_min_type = numpy.uint8
    for s in streams:
        if not s:
            # should not happen, but let's not completely fail on this
            logging.error("StreamTree has a None stream")
            continue

        if not raw:
            if not hasattr(s, "image") or s.image.value is None:
                continue
            data = s.image.value
            if isinstance(data, tuple): # 2D tuple = tiles
                data = img.mergeTiles(data)
        else:
            if isinstance(s, RGBProjection):
                data = s.projectAsRaw()
            elif not s.raw: # Nothing to export
                logging.info("Skipping %s which has no raw data", s)
                continue
            else:
                # For now we only export the first data (typically, there is only one)
                data = s.raw[0]
                if isinstance(data, DataArrayShadow):
                    data = data.getData()
                if model.hasVA(s, "zIndex"):
                    data = img.getYXFromZYX(data, s.zIndex.value)

            # Pretend to be RGB for the drawing by cairo
            if numpy.can_cast(im_min_type, min_type(data)):
                im_min_type = min_type(data)

        if isinstance(s.raw, tuple):  # s.raw has tiles
            md = s.raw[0][0].metadata.copy()
        else:
            md = s.raw[0].metadata.copy()

        if model.hasVA(s, "zIndex") and not raw:
            # Make sure we keep the correct Pos[Z] (from the projection)
            try:
                md[model.MD_POS] = data.metadata[model.MD_POS]
            except KeyError:
                pass

        ostream = s.stream if isinstance(s, DataProjection) else s

        # Sometimes SEM streams contain the dt value as exposure time metadata.
        # In that case handle it in special way
        if (isinstance(ostream, acqstream.EMStream) and
            model.MD_EXP_TIME in md and model.MD_DWELL_TIME not in md):
            md[model.MD_DWELL_TIME] = md[model.MD_EXP_TIME]
            del md[model.MD_EXP_TIME]
        elif isinstance(ostream, acqstream.SpectrumStream):
            # The spectrum stream projection is limited to the selected bandwidth
            # => update the metadata (note that we are subverting this metadata
            # as it should have as many entries as C dim, but the C dim has been
            # flatten, so we put two, to convey center/width info)
            md[model.MD_WL_LIST] = ostream.spectrumBandwidth.value

        # FluoStreams are merged using the "Screen" method that handles colour
        # merging without decreasing the intensity.
        if isinstance(ostream, (acqstream.FluoStream, acqstream.StaticFluoStream, acqstream.CLStream)):
            images_opt.append((data, BLEND_SCREEN, ostream, md))
        elif isinstance(ostream, acqstream.SpectrumStream):
            images_spc.append((data, BLEND_DEFAULT, ostream, md))
        else:
            images_std.append((data, BLEND_DEFAULT, ostream, md))

    # Sort by size, so that the biggest picture is first drawn (no opacity)
    def get_area(d):
        return numpy.prod(d[0].shape[0:2]) * d[0].metadata[model.MD_PIXEL_SIZE][0]

    images_opt.sort(key=get_area, reverse=True)
    images_spc.sort(key=get_area, reverse=True)
    images_std.sort(key=get_area, reverse=True)

    return images_opt + images_std + images_spc, im_min_type


# Similar to miccanvas, but without cache, and with trick to support raw export
def convert_streams_to_images(streams, raw=False):
    """ Temporary function to convert the StreamTree to a list of images as
    the export function currently expects.

    returns:
        images (list of DataArray)
        stream_data (dict Stream -> tuple (float, list of str/values)): For each stream,
          associate the acquisition date, stuff to display in the legend, and baseline value
        im_min_type (numpy.dtype): data type for the output data (common for all the
          DataArrays)
    """
    images, im_min_type = get_ordered_images(streams, raw)

    # add the images in order
    ims = []
    for data, blend_mode, stream, md in images:
        try:
            rgba_im = _convert_to_bgra(data)
        except TypeError:
            if not raw:
                raise  # Something is very wrong, as we should have RGB data from the projection
            logging.warning("Data of %s cannot be packed so will export very raw", stream.name.value)
            rgba_im = data  # The caller will have to handle it in a special way
        keepalpha = False
        date = data.metadata.get(model.MD_ACQ_DATE, None)
        scale = data.metadata[model.MD_PIXEL_SIZE]
        pos = data.metadata[model.MD_POS]
        rot = data.metadata.get(model.MD_ROTATION, 0)
        shear = data.metadata.get(model.MD_SHEAR, 0)
        flip = data.metadata.get(model.MD_FLIP, 0)

        # TODO: directly put the metadata as set_images do?
        ims.append((rgba_im, pos, scale, keepalpha, rot, shear, flip, blend_mode,
                    stream.name.value, date, stream, md))

    images = set_images(ims)

    # TODO: just return an OderedDict of image->stream
    return images, im_min_type


def get_sub_img(b_intersect, b_im_rect, im_data, total_scale):
    """ Return the minimal image data that will cover the intersection

    :param b_intersect: (ltbr px = 4 float) Intersection of the full image and the buffer

    :param b_im_rect: (ltbr px = 4 float) The area the full image would occupy in the
        buffer
    :param im_data: (DataArray) The original image data
    :param total_scale: (float, float) The scale used to convert the image data to
        buffer pixels. (= image scale * buffer scale)

    :return: (DataArray, (float, float)): cropped image and left-top coordinate

    Since trimming the image will possibly change the top left buffer
    coordinates it should be drawn at, an adjusted (x, y) tuple will be
    returned as well.
    """
    # TODO: Test if scaling a sub image really has performance benefits
    # while rendering with Cairo (i.e. Maybe Cairo is smart enough to render
    # big images without calculating the pixels that are not visible.)
    # Although, it seems not, at least with Cairo 1.0.

    im_h, im_w = im_data.shape[:2]

    # where is this intersection in the original image?
    unsc_rect = (
        (b_intersect[0] - b_im_rect[0]) / total_scale[0],
        (b_intersect[1] - b_im_rect[1]) / total_scale[1],
        b_intersect[2] / total_scale[0],
        b_intersect[3] / total_scale[1]
    )

    # Round the rectangle values to whole pixel values
    # Note that the width and length get "double rounded":
    # The bottom left gets rounded up to match complete pixels and that
    # value is adjusted by a rounded down top/left.
    unsc_rnd_rect = [
        int(unsc_rect[0]),  # rounding down origin
        int(unsc_rect[1]),  # rounding down origin
        int(math.ceil(unsc_rect[0] + unsc_rect[2])) - int(unsc_rect[0]),
        int(math.ceil(unsc_rect[1] + unsc_rect[3])) - int(unsc_rect[1])
    ]

    # Make sure that the rectangle fits inside the image
    l = max(0, unsc_rnd_rect[0])
    t = max(0, unsc_rnd_rect[1])
    r = min(max(0, unsc_rnd_rect[0] + unsc_rnd_rect[2]), im_w - 1)
    b = min(max(0, unsc_rnd_rect[1] + unsc_rnd_rect[3]), im_h - 1)

    # New top left origin in buffer coordinates to account for the clipping
    b_new = ((l * total_scale[0]) + b_im_rect[0],
             (t * total_scale[1]) + b_im_rect[1])

    # We need to copy the data, since cairo.ImageSurface.create_for_data expects a single
    # segment buffer object (i.e. the data must be contiguous)
    im_data = im_data[t:b + 1, l:r + 1].copy()

    return im_data, b_new


class FakeCanvas(object):
    """Fake canvas for drawing purposes. It is currently used to export images with printed rulers
    in print-ready export. We ask the overlay to draw on this fake canvas"""

    def __init__(self, ctx, buffer_size, buffer_center, buffer_scale):
        """
        ctx (cairo context): the view context on which to draw
        buffer_size (0<int, 0<int) : buffer width and height in pixels
        buffer_center (float, float) : center position X, Y in meters
        buffer_scale (float, float) : buffer scale position in pixels/meter
        """
        self.ctx = ctx
        self.buffer_size = buffer_size
        self.buffer_center = buffer_center
        self.buffer_scale = buffer_scale

    def get_half_buffer_size(self):
        """Return half the size of the current buffer"""
        return tuple(v // 2 for v in self.buffer_size)

    def phys_to_buffer(self, pos, offset=(0, 0)):
        """Convert a position in physical coordinates into buffer coordinates"""
        return ((pos[0] - self.buffer_center[0]) * self.buffer_scale[0] + offset[0],
                -(pos[1] - self.buffer_center[1]) * self.buffer_scale[1] + offset[1])

    def draw_overlay(self, overlay):
        font_size = self.buffer_size[0] * LARGE_FONT
        """Pass the fake canvas to the draw function of the overlay"""
        overlay.draw(self.ctx, canvas=self, font_size=font_size)


def images_to_export_data(streams, view_hfw, view_pos,
                          draw_merge_ratio, raw=False,
                          orig_canvas=None, interpolate_data=False, logo=None):
    """
    streams (Streams or DataProjection): the data to be exported
    view_hfw (tuple of float): X (width), Y (height) in m
    view_pos (tuple of float): center position X, Y in m
    raw (bool): if False, generates one RGB image out of all the streams, otherwise
      generates one image per stream using the raw data
    orig_canvas: if the passed canvas has a ruler overlay, a fake canvas is used
      for the ruler overlay to draw on it.
    logo (RGBA DataArray): Image to display in the legend
    return (list of DataArray)
    raise LookupError: if no data visible in the selected FoV
    """
    max_res = (MAX_RES_FACTOR * CROP_RES_LIMIT) ** 2
    # min_mpp = the minimum meters per pixels resulting in the maximum pixels size on based on the requested
    # field-of-view, and the maximum number of pixels we are willing to export (independent of the image ratio)
    min_mpp = math.sqrt((view_hfw[0]*view_hfw[1]) / max_res)# Area = [meters per pixel]^2 * number_of_pixels

    def _ensure_proj_mpp(projection, min_mpp):
        img_received = threading.Event()
        new_proj = RGBSpatialProjection(projection.stream)
        new_proj.rect.value = projection.rect.value
        new_proj.mpp.value = new_proj.mpp.clip(min_mpp)  # set pixel size to desired number
        streams[stream_idx] = new_proj

        #Callback function needed to pass to subscribe method
        def img_callback(da):
            img_received.set()

        # Wait until the projection has been updated
        new_proj.image.subscribe(img_callback)
        img_received.wait()
        new_proj.image.unsubscribe(img_callback)

    for stream_idx, projection in enumerate(streams):
        #Check if a projection resolution can be increased begore exporting
        if hasattr(projection, 'mpp'):
            _ensure_proj_mpp(projection, min_mpp)

    images, im_min_type = convert_streams_to_images(streams, raw)

    if not images:
        raise LookupError("There is no stream data to be exported")

    if interpolate_data and im_min_type != numpy.uint8:
        # TODO: make interpolation work also with 16 bits and higher data type
        # For now, as Cairo is convinced it's RGB, it computes wrong data.
        # cf util.img.rescale_hq() before casting to RGB?
        logging.debug("Disabling interpolation as data is not 8 bits")
        interpolate_data = False

    # Find min pixel size
    min_pxs = min(im.metadata['dc_scale'] for im in images)

    # TODO: first crop the view_hfw + view_pos to the data, and then compute
    # the maximum resolution. Currently, it might be made very small just
    # because the data is shown at low mag.

    # Check that resolution of all images remains within limits if we use
    # the smallest pixel size, otherwise adjust it
    min_res = CROP_RES_LIMIT, CROP_RES_LIMIT * view_hfw[1] / view_hfw[0]
    new_res = view_hfw[0] // min_pxs[0], view_hfw[1] // min_pxs[1]
    max_res = MAX_RES_FACTOR * min_res[0], MAX_RES_FACTOR * min_res[1]
    buffer_size = tuple(numpy.clip(new_res, min_res, max_res))
    if buffer_size != new_res:
        min_pxs = view_hfw[0] / buffer_size[0], view_hfw[1] / buffer_size[1]

    buffer_size = int(buffer_size[0]), int(buffer_size[1])

    # The buffer center is the same as the view window's center
    buffer_center = tuple(view_pos)
    buffer_scale = min_pxs

    # Check if we need to crop in order to only keep the stream data and get
    # rid of the blank parts of the canvas
    crop_pos = buffer_size
    crop_shape = (0, 0)
    intersection_found = False
    for im in images:
        b_im_rect = calc_img_buffer_rect(im, im.metadata['dc_scale'], im.metadata['dc_center'],
                                         buffer_center, buffer_scale, buffer_size)
        buffer_rect = (0, 0) + buffer_size
        intersection = intersect(buffer_rect, b_im_rect)
        if intersection:
            # Keep the min roi that contains all the stream data
            crop_pos = tuple(min(a, b) for a, b in zip(crop_pos, intersection[:2]))
            crop_shape = tuple(max(a, b) for a, b in zip(crop_shape, intersection[2:]))
            intersection_found = True

    # if there is no intersection of any stream data with the viewport, then
    # raise LookupError
    if not intersection_found:
        raise LookupError("There is no visible stream data to be exported")

    if crop_pos != (0, 0) or crop_shape != buffer_size:
        logging.debug("Need to crop the data from %s to %s", crop_shape, buffer_size)
        new_size = crop_shape
        if new_size[0] < min_res[0]:
            new_size = min_res[0], (min_res[0] / new_size[0]) * new_size[1]
        new_size = int(new_size[0]), int(new_size[1])
        crop_factor = new_size[0] / crop_shape[0], new_size[1] / crop_shape[1]
        # we also need to adjust the hfw displayed on legend
        hfw_factor = crop_shape[0] / buffer_size[0], crop_shape[1] / buffer_size[1]
        view_hfw = view_hfw[0] * hfw_factor[0], view_hfw[1] * hfw_factor[1]

        crop_center = crop_pos[0] + (crop_shape[0] / 2) - (buffer_size[0] / 2), crop_pos[1] + (crop_shape[1] / 2) - (buffer_size[1] / 2)
        buffer_size = new_size
        buffer_center = (buffer_center[0] + crop_center[0] * buffer_scale[0], buffer_center[1] - crop_center[1] * buffer_scale[1])
        buffer_scale = (buffer_scale[0] / crop_factor[0], buffer_scale[1] / crop_factor[1])

    # TODO: make sure that Y dim of the buffer_size is not crazy high

    # The list of images to export
    data_to_export = []
    fake_canvas = None
    n = len(images)
    bm_last = images[-1].metadata["blend_mode"]
    for i, im in enumerate(images):
        if raw and not (im.ndim == 3 and im.shape[-1] == 4):
            # Non BGRA data type => we'll pass it completely as-is
            data_to_export.append(im)
            continue

        if raw or i == 0:  # when print-ready, share the surface to draw
            # Make surface based on the maximum resolution
            data_to_draw = numpy.zeros((buffer_size[1], buffer_size[0], 4), dtype=numpy.uint8)
            surface = cairo.ImageSurface.create_for_data(
                data_to_draw, cairo.FORMAT_ARGB32, buffer_size[0], buffer_size[1])
            ctx = cairo.Context(surface)
            # The ruler overlay needs a canvas to draw itself, so use a fake canvas
            fake_canvas = FakeCanvas(ctx, buffer_size, buffer_center, (1 / buffer_scale[0], 1 / buffer_scale[1]))

        blend_mode = im.metadata['blend_mode']
        if n == 1 or raw:
            # For single image, don't use merge ratio
            # For raw, each image is a "single image"
            merge_ratio = 1.0
        else:
            # If there are all "screen" (= last one is screen):
            # merge ratio   im0   im1
            #     0         1      0
            #    0.25       1      0.5
            #    0.5        1      1
            #    0.75       0.5    1
            #     1         0      1
            if bm_last == BLEND_SCREEN:
                if ((draw_merge_ratio < 0.5 and i < n - 1) or
                    (draw_merge_ratio >= 0.5 and i == n - 1)):
                    merge_ratio = 1
                else:
                    merge_ratio = (0.5 - abs(draw_merge_ratio - 0.5)) * 2
            else:  # bm_last == BLEND_DEFAULT
                # Average all the first images
                if i < n - 1:
                    if blend_mode == BLEND_SCREEN:
                        merge_ratio = 1.0
                    else:
                        merge_ratio = 1 - i / n
                else:  # last image
                    merge_ratio = draw_merge_ratio

        # Reset the first image to be drawn to the default blend operator to be
        # drawn full opacity (only useful if the background is not full black)
        if i == 0:
            blend_mode = BLEND_DEFAULT

        draw_image(
            ctx,
            im,
            im.metadata['dc_center'],
            buffer_center,
            buffer_scale,
            buffer_size,
            merge_ratio,
            im_scale=im.metadata['dc_scale'],
            rotation=im.metadata['dc_rotation'],
            shear=im.metadata['dc_shear'],
            flip=im.metadata['dc_flip'],
            blend_mode=blend_mode,
            interpolate_data=interpolate_data
        )

        # Create legend for each raw image
        if raw:
            legend_rgb = draw_legend_multi_streams(images, buffer_size, buffer_scale,
                                                   view_hfw[0], im.metadata['date'],
                                                   im.metadata['stream'], img_file=logo)

            new_data_to_draw = _unpack_raw_data(data_to_draw, im_min_type)
            legend_as_raw = _adapt_rgb_to_raw(legend_rgb, new_data_to_draw)
            data_with_legend = numpy.append(new_data_to_draw, legend_as_raw, axis=0)

            md = {model.MD_DESCRIPTION: im.metadata['name']}
            data_to_export.append(model.DataArray(data_with_legend, md))

    # Create legend for print-ready
    if not raw:  # png, tiff
        # In print-ready export, a fake canvas is used by the ruler overlay
        if orig_canvas and orig_canvas.gadget_overlay:
            fake_canvas.draw_overlay(orig_canvas.gadget_overlay)
        dates = [im.metadata['date'] if im.metadata['date'] else 0 for im in images]
        date = max(dates)
        legend_rgb = draw_legend_multi_streams(images, buffer_size, buffer_scale,
                                               view_hfw[0], date, img_file=logo)
        data_with_legend = numpy.append(data_to_draw, legend_rgb, axis=0)
        data_with_legend[:, :, [2, 0]] = data_with_legend[:, :, [0, 2]]
        md = {model.MD_DIMS: 'YXC'}
        data_to_export.append(model.DataArray(data_with_legend, md))

    return data_to_export


def _adapt_rgb_to_raw(imrgb, data_raw):
    """
    Converts a RGB(A) image to greyscale.
    Note: for now the implementation is very crude and just takes the first channel.
    imrgb (ndarray Y,X,{3,4}): RGB image to convert to a greyscale.
    data_raw (DataArray): Raw image (to know the dtype and min/max)
    return (ndarray Y,X)
    """
    dtype = data_raw.dtype.type
    blkval = numpy.min(data_raw)
    a = (numpy.max(data_raw) - blkval) / 255
    im_as_raw = imrgb[:, :, 0].astype(dtype)
    numpy.multiply(im_as_raw, a, out=im_as_raw, casting="unsafe")
    im_as_raw += dtype(blkval)

    return im_as_raw


def _convert_to_bgra(data):
    """
    Convert an image data to BGRA format, to make it compatible with Cairo.
    data (ndarray of shape YX or YX3 or YX4): If the data is already BGRA, nothing happens.
      If the data is a large int (eg int16), which can fit on 24 bits then it's
      packed into the BGR bytes.
    return (ndarray of shape YX4): BGRA data
    raise TypeError: if the data cannot be converted (losslessly)
    """
    if data.ndim == 3 and data.shape[2] in (3, 4):
        # RGB image => normal conversion
        return format_rgba_darray(data)
    elif (data.ndim == 2 and
          numpy.issubdtype(data.dtype, numpy.integer) and data.dtype.itemsize <= 4):
        # Split the bits in B,G,R,A
        return _pack_data_into_bgra(data)

    raise TypeError("Data of type %s and shape %s cannot be packed in BGRA" % (data.dtype, data.shape))


def _pack_data_into_bgra(data_raw):
    """
    Convert a "raw" data (as in "greyscale") into data pretending to be BGRA 8-bit
    data_raw (ndarray Y,X)
    return (ndrarray Y,X,4)
    """
    data = numpy.empty((data_raw.shape[0], data_raw.shape[1], 4), dtype=numpy.uint8)
    if numpy.any(numpy.right_shift(data_raw[:, :], 24) & 255):
        raise TypeError("Data contains information that would be packed on the alpha byte")

    # Note: the order doesn't really matter. We just need to use the same one
    # when unpacking
    data[:, :, 0] = data_raw[:, :] & 255
    data[:, :, 1] = numpy.right_shift(data_raw[:, :], 8) & 255
    data[:, :, 2] = numpy.right_shift(data_raw[:, :], 16) & 255
    data[:, :, 3] = 255

    data = model.DataArray(data, data_raw.metadata)
    data.metadata['byteswapped'] = True
    return data


def _unpack_raw_data(imrgb, dtype):
    """
    Convert back the "raw" (as in "greyscale with lots of bits") data from data
      pretending to be BGRA 8-bit

    imrgb (ndarray Y,X,4)
    dtype: type of the output data (should be some uint <= 64 bits)
    return (ndrarray Y,X)
    """
    imraw = (imrgb[:, :, 0]
             | numpy.left_shift(imrgb[:, :, 1], 8, dtype=numpy.uint32)
             | numpy.left_shift(imrgb[:, :, 2], 16, dtype=numpy.uint32))
    return imraw.astype(dtype)


def add_alpha_byte(im_darray, alpha=255):
    # if im_darray is a tuple of tuple of tiles, return a tuple of tuple of processed tiles
    if isinstance(im_darray, tuple):
        new_array = []
        for tuple_col in im_darray:
            new_array_col = []
            for tile in tuple_col:
                tile = add_alpha_byte(tile, alpha)
                new_array_col.append(tile)

            new_array.append(tuple(new_array_col))
        return tuple(new_array)

    height, width, depth = im_darray.shape

    if depth == 4:
        return im_darray
    elif depth == 3:
        new_im = numpy.empty((height, width, 4), dtype=numpy.uint8)
        new_im[:, :, -1] = alpha
        new_im[:, :, :-1] = im_darray

        if alpha != 255:
            scale_to_alpha(new_im)

        if isinstance(im_darray, model.DataArray):
            return model.DataArray(new_im, im_darray.metadata)
        else:
            return new_im
    else:
        raise ValueError("Unexpected colour depth of %d bytes!" % depth)


def scale_to_alpha(im_darray):
    """
    Scale the R, G and B values to the alpha value present.

    im_darray (numpy.array of shape Y, X, 4, and dtype uint8). Alpha channel
    is the fourth element of the last dimension. It is modified in place.
    return im_darray (numpy.array): the input
    """

    if im_darray.shape[2] != 4:
        raise ValueError("DataArray needs to have 4 byte RGBA values!")

    alphar = im_darray[:, :, 3] / 255
    numpy.multiply(im_darray[:, :, 0], alphar, out=im_darray[:, :, 0], casting="unsafe")
    numpy.multiply(im_darray[:, :, 1], alphar, out=im_darray[:, :, 1], casting="unsafe")
    numpy.multiply(im_darray[:, :, 2], alphar, out=im_darray[:, :, 2], casting="unsafe")

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
        # 1 copy
        return wx.Image(*size, data=numpy.ascontiguousarray(image[:, :, 0:3]),
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
        # Note that creating a empty Bitmap and then using CopyFromBuffer()
        # doesn't work on Windows (with wxPython 4.0.7).
        bim = wx.Bitmap.FromBuffer(size[0], size[1], image)
    elif image.shape[2] == 4: # RGBA
        bim = wx.Bitmap.FromBufferRGBA(size[0], size[1], image)
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

    return numpy.ndarray(buffer=image.GetData(), shape=shape, dtype=numpy.uint8)


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


def insert_tile_to_image(tile, ovv):
    """ 
    Inserts a tile into a larger (overview) image. The entire tile is inserted into the
    corresponding part of the ovv and the previous content at this part of the ovv is
    deleted. If the tile reaches beyond the borders of the ovv, it is cropped.
    tile: 3D DataArray (RGB or RGBA) with MD_PIXEL_SIZE and MD_POS metadata
    ovv: 3D DataArray (RGB or RGBA) with MD_PIXEL_SIZE metadata
    Returns 3D DataArray with the same shape as ovv (updated ovv)
    """
    # TODO: allow a 'blend_screen' mode, for multiple fluo images?

    # Tile parameters
    tile_pos = tile.metadata[model.MD_POS]
    tile_mpp = tile.metadata[model.MD_PIXEL_SIZE]

    # Ovv parameters
    ovv_mpp = ovv.metadata[model.MD_PIXEL_SIZE]
    ovv_sz = ovv.shape[1], ovv.shape[0]
    ovv_pos = ovv.metadata[model.MD_POS]

    # Convert to Cairo format (BGRA)
    ovv_bgra = format_rgba_darray(ovv, 255)
    tile_bgra = format_rgba_darray(tile, 255)

    surface = cairo.ImageSurface.create_for_data(ovv_bgra, cairo.FORMAT_ARGB32,
                                                 ovv_bgra.shape[1], ovv_bgra.shape[0])
    ctx = cairo.Context(surface)

    draw_image(
        ctx,
        tile_bgra,
        tile_pos,
        ovv_pos,
        ovv_mpp,
        ovv_sz,
        opacity=1.0,
        im_scale=tile_mpp,
        blend_mode=BLEND_DEFAULT,
        interpolate_data=True
    )

    # Copy back to original image and convert back to RGB, at once
    ovv[:, :, 0] = ovv_bgra[:, :, 2]
    ovv[:, :, 1] = ovv_bgra[:, :, 1]
    ovv[:, :, 2] = ovv_bgra[:, :, 0]
    return ovv


def merge_screen(ima, imb):
    """ 
    Merges two images into one using the "screen" operator. Roughly, it's a
    "soft plus", which only reaches the maximum when both values are at the maximum.
    Precisely, it's defined as: f(xA,xB) = xA + xB − xA·xB (with values between
    0 and 1, with each channel independent).
    ima, imb: DataArray with ima.shape = imb.shape (YXC, either RGB or RGBA)
    returns RGBA DataArray (of the same YX as ima, but with always depth=4)
    """
    if ima.shape[-1] != 3 and ima.shape[-1] != 4:
        raise ValueError("Ovv images have an invalid number of channels: %d" % (ima.shape[-1]))
    if ima.shape[:-1] != imb.shape[:-1]:
        raise ValueError("Images have different shapes: %s != %s" % (ima.shape, imb.shape))

    md = imb.metadata.copy()
    ima = format_rgba_darray(ima, 255)  # convert to BGRA
    ima.metadata["dc_keepalpha"] = True
    out = format_rgba_darray(imb, 255)

    height, width, _ = ima.shape
    buffer_size = (width, height)
    buffer_top_left = (0, 0)
    buffer_scale = (1, 1)

    # Combine images
    surface = cairo.ImageSurface.create_for_data(
        out, cairo.FORMAT_ARGB32, buffer_size[0], buffer_size[1])
    ctx = cairo.Context(surface)

    draw_image(ctx, ima, buffer_top_left, buffer_top_left, buffer_scale,
               buffer_size, 1, blend_mode=BLEND_SCREEN)

    # Convert back to RGB
    format_bgra_to_rgb(out, inplace=True)
    out.metadata = md
    return out
