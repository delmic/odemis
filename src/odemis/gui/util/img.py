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

from __future__ import division

import cairo
import logging
import math
import numpy
from odemis import model
from odemis.acq.stream import DataProjection
from odemis.gui import BLEND_SCREEN, BLEND_DEFAULT
from odemis.gui.comp.overlay.base import Label
from odemis.util import intersect, fluo, conversion, polar, img, units
import time
import wx

import odemis.acq.stream as acqstream
import odemis.gui.img as guiimg


BAR_PLOT_COLOUR = (0.5, 0.5, 0.5)
CROP_RES_LIMIT = 1024
MAX_RES_FACTOR = 5  # upper limit resolution factor to exported image
SPEC_PLOT_SIZE = 1024
SPEC_SCALE_WIDTH = 150  # ticks + text vertically
SPEC_SCALE_HEIGHT = 100  # ticks + text horizontally
SMALL_SCALE_WIDTH = 10  # just ticks
SPEC_FONT_SIZE = 0.03  # Ratio of the whole output width
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


# TODO: rename to *_bgra_*
def format_rgba_darray(im_darray, alpha=None):
    """ Reshape the given numpy.ndarray from RGB to BGRA format
    im_darray (DataArray or tuple of tuple of DataArray): input image
    alpha (0 <= int <= 255 or None): If an alpha value is provided it will be
      set in the '4th' byte and used to scale the other RGB values within the array.
    return (DataArray or tuple of tuple of DataArray): The return type is the same of im_darray
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
                rgba = scale_to_alpha(rgba)
        new_darray = model.DataArray(rgba)

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
        new_darray = model.DataArray(rgba)
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
        # FIXME: doesn't work with floats
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


def ar_create_tick_labels(client_size, ticksize, num_ticks, margin=0):
    """
    Create list of tick labels for AR polar representation

    client_size (wx._core.Size)
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
    font_size = max(3, client_size[0] * SPEC_FONT_SIZE)
    inner_radius = min(center_x, center_y)
    radius = inner_radius + (ticksize / 1.5)
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
        lw, lh = l.text_size

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
        ofst += l.font_size + 1
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

    The (top, left) value are relative to the 0,0 top left of the buffer.

    im_data (DataArray): image data
    im_scale (float, float): The x and y scales of the image
    p_im_center (float, float): The center of the image in phys coordinates
    buffer_center (float, float): The buffer center (in phys coordinates)
    buffer_scale (float, float): The buffer scale
    buffer_size (float, float): The buffer size

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

    b_topleft = (round(((p_topleft[0] - buffer_center[0]) // buffer_scale[0]) + (buffer_size[0] // 2)),
                 round((-(p_topleft[1] - buffer_center[1]) // buffer_scale[1]) + (buffer_size[1] // 2)))

    final_size = (scaled_im_size[0] // buffer_scale[0], scaled_im_size[1] // buffer_scale[1])
    return b_topleft + final_size


def draw_image(ctx, im_data, p_im_center, buffer_center, buffer_scale,
               buffer_size, opacity=1.0, im_scale=(1.0, 1.0), rotation=None,
               shear=None, flip=None, blend_mode=BLEND_DEFAULT, interpolate_data=False):
    """ Draw the given image to the Cairo context

    The buffer is considered to have it's 0,0 origin at the top left

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


def calculate_raw_ar(data, bg_data):
    """
    Project the AR data to equirectangular
    return (DataArray of shape phi/theta): phi/theta -> intensity
    """
    # FIXME: This code is a duplicate of part of _project2Polar
    if numpy.prod(data.shape) > (800 * 800):
        logging.info("AR image is very large %s, will convert to "
                     "equirectangular projection in reduced precision.",
                     data.shape)
        y, x = data.shape
        if y > x:
            small_shape = 768, int(round(768 * x / y))
        else:
            small_shape = int(round(768 * y / x)), 768
        # resize
        data = img.rescale_hq(data, small_shape)
        dtype = numpy.float16
    else:
        dtype = None  # just let the function use the best one

    size = (90, 360)  # TODO: increase if data is high def

    if bg_data is None:
        # Simple version: remove the background value
        data0 = polar.ARBackgroundSubtract(data)
    else:
        data0 = img.Subtract(data, bg_data)  # metadata from data

    # calculate raw polar representation
    polard = polar.AngleResolved2Rectangular(data0, size, hole=False, dtype=dtype)

    return polard


def ar_to_export_data(streams, raw=False):
    """
    Creates either raw or WYSIWYG representation for the AR projection

    streams (list of Stream objects): streams displayed in the current view
    client_size (wx._core.Size)
    raw (boolean): if True returns raw representation

    returns (model.DataArray): if raw, returns a 2D array with axes phi/theta ->
       intensity (equirectangular projection). Otherwise, returns a 3D DataArray
       corresponding to a greyscale RGBA view of the polar projection, with the
       axes drawn over it.
    """
    # we expect just one stream
    if len(streams) == 0:
        raise LookupError("No stream to export")
    elif len(streams) > 1:
        logging.warning("More than one stream exported to AR, will only use the first one.")

    s = streams[0]

    if raw:  # csv
        pos = s.point.value

        # find positions of each acquisition
        # tuple of 2 floats -> DataArray: position on SEM -> data
        sempos = {}
        for d in s.raw:
            try:
                sempos[d.metadata[model.MD_POS]] = img.ensure2DImage(d)
            except KeyError:
                logging.info("Skipping DataArray without known position")
        raw_ar = calculate_raw_ar(sempos[pos], s.background.value)
        raw_ar.metadata[model.MD_ACQ_TYPE] = model.MD_AT_AR
        return raw_ar
    else:
        sim = s.image.value  # TODO: check that it's up to date (and not None)
        if sim is None:
            raise LookupError("Stream %s has no data selected" % (s.name.value,))
        wim = format_rgba_darray(sim)
        # image is always centered, fitting the whole canvas, with scale 1
        images = set_images([(wim, (0, 0), (1, 1), False, None, None, None, None, s.name.value, None, None, {})])
        ar_margin = int(0.2 * sim.shape[0])
        ar_size = sim.shape[0] + ar_margin, sim.shape[1] + ar_margin

        # Make surface based on the maximum resolution
        data_to_draw = numpy.zeros((ar_size[1], ar_size[0], 4), dtype=numpy.uint8)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, ar_size[0], ar_size[1])
        ctx = cairo.Context(surface)

        im = images[0]
        buffer_center = (-ar_margin / 2, ar_margin / 2)
        buffer_scale = (1.0, 1.0)
        buffer_size = sim.shape[0], sim.shape[1]

        draw_image(
            ctx,
            im,
            im.metadata['dc_center'],
            buffer_center,
            buffer_scale,
            buffer_size,
            1.0,
            # im_scale=(1.0, 1.0),
            rotation=im.metadata['dc_rotation'],
            shear=im.metadata['dc_shear'],
            flip=im.metadata['dc_flip'],
            blend_mode=im.metadata['blend_mode'],
            interpolate_data=False
        )

        font_name = "Sans"
        ticksize = 10
        num_ticks = 6
        ticks_info = ar_create_tick_labels(streams[0].image.value.shape, ticksize, num_ticks, ar_margin / 2)
        ticks, (center_x, center_y), inner_radius, radius = ticks_info
        draw_ar_frame(ctx, ar_size, ticks, font_name, center_x, center_y, inner_radius, radius)
        draw_ar_spiderweb(ctx, center_x, center_y, radius)
        ar_plot = model.DataArray(data_to_draw)
        ar_plot.metadata[model.MD_DIMS] = 'YXC'
        return ar_plot


def value_to_pixel(value, pixel_space, vtp_ratio, value_range, orientation):
    """
    Map range value to legend pixel position

    value (float): value to map
    pixel_space (int): pixel space
    vtp_ratio (float):  value to pixel ratio
    value_range (tuple of floats): value range
    orientation (int): legend orientation

    returns (float): pixel position
    """
    if pixel_space is None:
        return None
    elif None not in (vtp_ratio, value_range):
        pixel = (value - value_range[0]) * vtp_ratio
        pixel = int(round(pixel))
    else:
        pixel = 0
    return pixel if orientation == wx.HORIZONTAL else pixel_space - pixel


def calculate_ticks(value_range, client_size, orientation, tick_spacing):
    """
    Calculate which values in the range to represent as ticks on the axis

    value_range (tuple of floats): value range
    client_size (wx._core.Size)
    orientation (int): legend orientation
    tick_spacing (float): space between ticks

    returns (list of tuples of floats): list of pixel position and value pairs
            (float): value to pixel ratio

    """

    if value_range is None:
        return

    min_val, max_val = value_range

    # Get the horizontal/vertical space in pixels
    if orientation == wx.HORIZONTAL:
        pixel_space = client_size[0]
        min_pixel = 0
    else:
        pixel_space = client_size[1]
        # Don't display ticks too close from the left border
        min_pixel = 10

    # Range width
    value_space = max_val - min_val
    if value_space == 0:
        logging.info("Trying to compute legend tick with empty range %s", value_range)
        vtp_ratio = None
        # Just one tick, at the origin
        pixel = max(min_pixel, value_to_pixel(min_val, pixel_space, vtp_ratio,
                                              value_range, orientation))
        tick_list = [(pixel, min_val)]
        return tick_list, vtp_ratio

    vtp_ratio = pixel_space / value_space

    num_ticks = pixel_space // tick_spacing
    # Calculate the best step size in powers of 10, so it will cover at
    # least the distance `val_dist`
    value_step = 1e-12

    # Increase the value step tenfold while it fits more than num_ticks times
    # in the range
    while value_step and value_space / value_step > num_ticks:
        value_step *= 10
    # logging.debug("Value step is %s after first iteration with range %s",
    #               value_step, value_space)

    # Divide the value step by two,
    while value_step and value_space / value_step < num_ticks:
        value_step /= 2
    # logging.debug("Value step is %s after second iteration with range %s",
    #               value_step, value_space)

    first_val = (int(min_val / value_step) + 1) * value_step if value_step else 0
    # logging.debug("Setting first tick at value %s", first_val)

    tick_values = [min_val]
    cur_val = first_val

    while cur_val < max_val:
        tick_values.append(cur_val)
        cur_val += value_step

    ticks = []
    min_margin = (tick_spacing / 4)
    prev_pixel = 0
    for tick_value in tick_values:
        pixel = value_to_pixel(tick_value, pixel_space, vtp_ratio, value_range,
                               orientation)
        pix_val = (pixel, tick_value)
        if pix_val not in ticks:
            if (tick_value not in [numpy.min(tick_values), numpy.max(tick_values)] and
                    abs(pixel - prev_pixel) < min_margin):
                # keep a min distance between ticks
                continue
            if min_pixel <= pixel <= pixel_space:
                ticks.append(pix_val)
                prev_pixel = pixel

    tick_list = ticks

    return tick_list, vtp_ratio


def draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
               fill_colour, unit, scale_width, font_size, scale_label=None, mirror=False):
    """
    Draws horizontal and vertical scale bars

    value_range (tuple of floats): value range
    client_size (wx._core.Size)
    orientation (int): legend orientation
    tick_spacing (float): space between ticks
    fill_colour (tuple of floats): colour to fill bars
    unit (string): scale unit
    scale_width (float): scale bar width
    font_size (float)
    scale_label (string): label to be attached
    mirror (boolean): if True: in case of horizontal bar means scale bar goes to the
        top side of the plot and in case of vertical scale bar goes to the right side
        of the plot. The tick values is not written.
    """

    if value_range is None:
        return

    tick_list, _ = calculate_ticks(value_range, client_size, orientation, tick_spacing)
    csize = client_size
    # TODO use always client_size instead of scale_width

    # Set Font
    font_name = "Sans"
    ctx.select_font_face(font_name, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(font_size)

    ctx.set_source_rgb(*fill_colour)
    ctx.set_line_width(2)
    ctx.set_line_join(cairo.LINE_JOIN_MITER)

    if orientation == wx.VERTICAL:
        if mirror:
            ctx.move_to(0, 0)
            ctx.line_to(0, csize.y)
            ctx.stroke()
        else:
            ctx.move_to(scale_width, 0)
            ctx.line_to(scale_width, csize.y)
            ctx.stroke()
    else:
        if mirror:
            ctx.move_to(0, scale_width)
            ctx.line_to(csize.x, scale_width)
            ctx.stroke()
        else:
            ctx.move_to(0, 0)
            ctx.line_to(csize.x, 0)
            ctx.stroke()

    max_width = 0
    prev_lpos = 0 if orientation == wx.HORIZONTAL else csize.y

    if scale_label:
        ctx.save()
        prefix = ""
        if unit is None:
            # assume it is in counts if there is no info
            unit = "cts"
        elif unit not in units.IGNORE_UNITS:
            # Find the best unit prefix
            absv = sorted(abs(v) for p, v in tick_list)
            midv = absv[(len(absv) - 1) // 2]
            divisor, prefix = units.get_si_scale(midv)
            tick_list = [(p, v / divisor) for p, v in tick_list]
        scale_label += u" (%s%s)" % (prefix, unit)
        _, _, lbl_width, _, _, _ = ctx.text_extents(scale_label)
        # TODO: probably not correctly placed in case of mirror (but no one cases)
        if orientation == wx.HORIZONTAL:
            ctx.move_to(((csize.x - scale_width) / 2) - lbl_width / 2,
                        scale_width - int(font_size * 0.4))
        else:
            ctx.move_to(int(font_size * 1.2),
                        ((csize.y - scale_width) / 2) + lbl_width / 2)
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
            lpos = max(min(lpos, csize.x - lbl_width - 2), 2)
            if prev_lpos < lpos:
                if mirror:
                    # ctx.move_to(lpos, scale_width - (lbl_height - 3))
                    # ctx.show_text(label)
                    ctx.move_to(pos, scale_width - 5)
                    ctx.line_to(pos, scale_width)
                else:
                    ctx.move_to(lpos, lbl_height + 8)
                    ctx.show_text(label)
                    ctx.move_to(pos, 5)
                    ctx.line_to(pos, 0)
            prev_lpos = lpos + lbl_width
        else:
            max_width = max(max_width, lbl_width)
            lpos = pos + (lbl_height // 2)
            lpos = max(min(lpos, csize.y), 2)

            if prev_lpos >= lpos + 20 or i == 0:
                if mirror:
                    # ctx.move_to(9, lpos)
                    # ctx.show_text(label)
                    ctx.move_to(5, pos)
                    ctx.line_to(0, pos)
                else:
                    ctx.move_to(scale_width - lbl_width - 9, lpos)
                    ctx.show_text(label)
                    ctx.move_to(scale_width - 5, pos)
                    ctx.line_to(scale_width, pos)
            prev_lpos = lpos + lbl_height

        ctx.stroke()


def val_x_to_pos_x(val_x, client_size, data_width=None, range_x=None, data_prop=None):
    """ Translate an x value to an x position in pixels
    The minimum x value is considered to be pixel 0 and the maximum is the canvas width. The
    parameter will be clipped if it's out of range.
    val_x (float): The value to map
    client_size (wx._core.Size)
    data_prop (int, int, int, int)
    returns (float)
    """
    range_x = range_x or data_prop[1]
    data_width = data_width or data_prop[0]

    if data_width:
        # Clip val_x
        x = min(max(range_x[0], val_x), range_x[1])
        perc_x = (x - range_x[0]) / data_width
        return perc_x * client_size.x
    else:
        return 0


def val_y_to_pos_y(val_y, client_size, data_height=None, range_y=None, data_prop=None):
    """ Translate an y value to an y position in pixels
    The minimum y value is considered to be pixel 0 and the maximum is the canvas width. The
    parameter will be clipped if it's out of range.
    val_y (float): The value to map
    client_size (wx._core.Size)
    data_prop (int, int, int, int)
    returns (float)
    """
    range_y = range_y or data_prop[3]
    data_height = data_height or data_prop[2]

    if data_height:
        y = min(max(range_y[0], val_y), range_y[1])
        perc_y = (range_y[1] - y) / data_height
        return perc_y * client_size.y
    else:
        return 0


def bar_plot(ctx, data, data_width, range_x, data_height, range_y, client_size, fill_colour):
    """ Do a bar plot of the current `_data` """

    if len(data) < 2:
        return

    vx_to_px = val_x_to_pos_x
    vy_to_py = val_y_to_pos_y

    line_to = ctx.line_to
    ctx.set_source_rgb(*fill_colour)

    diff = (data[1][0] - data[0][0]) / 2.0
    px = vx_to_px(data[0][0] - diff, client_size, data_width, range_x)
    py = vy_to_py(0, client_size, data_height, range_y)

    ctx.move_to(px, py)
    # print "-", px, py

    for i, (vx, vy) in enumerate(data[:-1]):
        py = vy_to_py(vy, client_size, data_height, range_y)
        # print "-", px, py
        line_to(px, py)
        px = vx_to_px((data[i + 1][0] + vx) / 2.0, client_size, data_width, range_x)
        # print "-", px, py
        line_to(px, py)

    py = vy_to_py(data[-1][1], client_size, data_height, range_y)
    # print "-", px, py
    line_to(px, py)

    diff = (data[-1][0] - data[-2][0]) / 2.0
    px = vx_to_px(data[-1][0] + diff, client_size, data_width, range_x)
    # print "-", px, py
    line_to(px, py)

    py = vy_to_py(0, client_size, data_height, range_y)
    # print "-", px, py
    line_to(px, py)

    ctx.close_path()
    ctx.fill()


def spectrum_to_export_data(stream, raw):
    """
    Creates either raw or WYSIWYG representation for the spectrum data plot

    stream (SpectrumStream): spectrum stream
    raw (boolean): if True returns raw representation

    returns (model.DataArray)
    """
    spectrum = stream.get_pixel_spectrum()
    if spectrum is None:
        raise LookupError("No pixel selected to pick a spectrum")
    spectrum_range, unit = stream.get_spectrum_range()

    if raw:
        if unit == "m":
            spectrum.metadata[model.MD_WL_LIST] = spectrum_range
        spectrum.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
        return spectrum
    else:
        # Draw spectrum bar plot
        data = zip(spectrum_range, spectrum)
        fill_colour = BAR_PLOT_COLOUR
        client_size = wx.Size(SPEC_PLOT_SIZE, SPEC_PLOT_SIZE)
        data_to_draw = numpy.empty((client_size.y, client_size.x, 4), dtype=numpy.uint8)
        data_to_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, client_size.x, client_size.y)
        ctx = cairo.Context(surface)
        # calculate data characteristics
        horz, vert = zip(*data)
        min_x = min(horz)
        max_x = max(horz)
        min_y = min(vert)
        max_y = max(vert)
        range_x = (min_x, max_x)
        data_width = max_x - min_x
        range_y = (min_y, max_y)
        data_height = max_y - min_y
        if data_height == 0:
            data_height = max_y
        bar_plot(ctx, data, data_width, range_x, data_height, range_y, client_size, fill_colour)

        # Differentiate the scale bar colour so the user later on
        # can easily change the bar plot or the scale bar colour
        fill_colour = (0, 0, 0)

        # Draw bottom horizontal scale legend
        value_range = (spectrum_range[0], spectrum_range[-1])
        orientation = wx.HORIZONTAL
        tick_spacing = SPEC_PLOT_SIZE // 4
        font_size = SPEC_PLOT_SIZE * SPEC_FONT_SIZE
        scale_x_draw = numpy.empty((SPEC_SCALE_HEIGHT, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SPEC_SCALE_HEIGHT)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, unit, SPEC_SCALE_HEIGHT, font_size, "Wavelength")
        data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

        # Draw top horizontal scale legend
        scale_x_draw = numpy.empty((SMALL_SCALE_WIDTH, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SMALL_SCALE_WIDTH)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, unit, SMALL_SCALE_WIDTH, font_size, mirror=True)
        data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

        # Draw left vertical scale legend
        orientation = wx.VERTICAL
        tick_spacing = SPEC_PLOT_SIZE // 6
        value_range = (min(spectrum), max(spectrum))
        scale_y_draw = numpy.empty((client_size.y, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SPEC_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, None, SPEC_SCALE_WIDTH, font_size, "Intensity")

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bars attached
        extend = numpy.empty((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        extend.fill(255)
        scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
        data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

        # Draw right vertical scale legend
        scale_y_draw = numpy.empty((client_size.y, SMALL_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SMALL_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, None, SMALL_SCALE_WIDTH, font_size, mirror=True)

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bars attached
        scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
        data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

        spec_plot = model.DataArray(data_with_legend)
        spec_plot.metadata[model.MD_DIMS] = 'YXC'
        return spec_plot


def time_spectrum_to_export_data(stream, raw):
    """
    Creates either raw or WYSIWYG representation for the time spectrum data plot

    stream (SpectrumStream): spectrum stream
    raw (boolean): if True returns raw representation

    returns (model.DataArray)
    """
    spectrum = stream.get_pixel_time()
    if spectrum is None:
        raise LookupError("No pixel selected to pick a spectrum")
    time_range, unit = stream.get_time_values()

    if raw:
        if unit == "s":
            spectrum.metadata[model.MD_TIME_LIST] = time_range
        spectrum.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
        return spectrum
    else:
        # Draw spectrum bar plot
        data = zip(time_range, spectrum)
        fill_colour = BAR_PLOT_COLOUR
        client_size = wx.Size(SPEC_PLOT_SIZE, SPEC_PLOT_SIZE)
        data_to_draw = numpy.empty((client_size.y, client_size.x, 4), dtype=numpy.uint8)
        data_to_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, client_size.x, client_size.y)
        ctx = cairo.Context(surface)
        # calculate data characteristics
        horz, vert = zip(*data)
        min_x = min(horz)
        max_x = max(horz)
        min_y = min(vert)
        max_y = max(vert)
        range_x = (min_x, max_x)
        data_width = max_x - min_x
        range_y = (min_y, max_y)
        data_height = max_y - min_y
        if data_height == 0:
            data_height = max_y
        bar_plot(ctx, data, data_width, range_x, data_height, range_y, client_size, fill_colour)

        # Differentiate the scale bar colour so the user later on
        # can easily change the bar plot or the scale bar colour
        fill_colour = (0, 0, 0)

        # Draw bottom horizontal scale legend
        value_range = (time_range[0], time_range[-1])
        orientation = wx.HORIZONTAL
        tick_spacing = SPEC_PLOT_SIZE // 4
        font_size = SPEC_PLOT_SIZE * SPEC_FONT_SIZE
        scale_x_draw = numpy.empty((SPEC_SCALE_HEIGHT, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SPEC_SCALE_HEIGHT)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, unit, SPEC_SCALE_HEIGHT, font_size, "Time")
        data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

        # Draw top horizontal scale legend
        scale_x_draw = numpy.empty((SMALL_SCALE_WIDTH, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SMALL_SCALE_WIDTH)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, unit, SMALL_SCALE_WIDTH, font_size, mirror=True)
        data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

        # Draw left vertical scale legend
        orientation = wx.VERTICAL
        tick_spacing = SPEC_PLOT_SIZE // 6
        value_range = (min(spectrum), max(spectrum))
        scale_y_draw = numpy.empty((client_size.y, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SPEC_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, None, SPEC_SCALE_WIDTH, font_size, "Intensity")

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bars attached
        extend = numpy.empty((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        extend.fill(255)
        scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
        data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

        # Draw right vertical scale legend
        scale_y_draw = numpy.empty((client_size.y, SMALL_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SMALL_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   fill_colour, None, SMALL_SCALE_WIDTH, font_size, mirror=True)

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bars attached
        scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
        data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

        spec_plot = model.DataArray(data_with_legend)
        spec_plot.metadata[model.MD_DIMS] = 'YXC'
        return spec_plot


def line_to_export_data(stream, raw):
    """
    Creates either raw or WYSIWYG representation for the spectrum line data

    stream (SpectrumStream)
    raw (boolean): if True returns raw representation

    returns (model.DataArray)
    """
    spectrum = stream.get_line_spectrum(raw)
    if spectrum is None:
        raise LookupError("No line selected to pick a spectrum")
    spectrum_range, unit = stream.get_spectrum_range()

    if raw:
        data = spectrum.T  # switch axes
        if unit == "m":
            data.metadata[model.MD_WL_LIST] = spectrum_range
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
        return data
    else:
        images = set_images([(spectrum, (0, 0), (1, 1), True, None, None, None, None, "Spatial Spectrum", None, None, {})])
        # TODO: just use a standard tuple, instead of wx.Size
        client_size = wx.Size(SPEC_PLOT_SIZE, SPEC_PLOT_SIZE)
        im = images[0]  # just one image
        # adjust to viewport size
        scale = (im.shape[1] / client_size.x, im.shape[0] / client_size.y)
        # Make surface based on the maximum resolution
        data_to_draw = numpy.zeros((client_size.y, client_size.x, 4), dtype=numpy.uint8)
        data_to_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, client_size.x, client_size.y)
        ctx = cairo.Context(surface)

        buffer_center = (0, 0)
        buffer_scale = scale
        buffer_size = client_size.x, client_size.y

        draw_image(
            ctx,
            im,
            im.metadata['dc_center'],
            buffer_center,
            buffer_scale,
            buffer_size,
            1.0,
            im_scale=im.metadata['dc_scale'],
            rotation=im.metadata['dc_rotation'],
            shear=im.metadata['dc_shear'],
            flip=im.metadata['dc_flip'],
            blend_mode=im.metadata['blend_mode'],
            interpolate_data=False
        )

        # Draw top/bottom horizontal (wavelength) legend
        text_colour = (0, 0, 0)  # black
        value_range = (spectrum_range[0], spectrum_range[-1])
        orientation = wx.HORIZONTAL
        tick_spacing = SPEC_PLOT_SIZE // 4
        font_size = SPEC_PLOT_SIZE * SPEC_FONT_SIZE
        scale_x_draw = numpy.empty((SPEC_SCALE_HEIGHT, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SPEC_SCALE_HEIGHT)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit, SPEC_SCALE_HEIGHT, font_size, "Wavelength")
        data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

        # Top
        scale_x_draw = numpy.empty((SMALL_SCALE_WIDTH, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SMALL_SCALE_WIDTH)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit, SMALL_SCALE_WIDTH, font_size, mirror=True)
        data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

        # Draw left vertical (distance) legend
        orientation = wx.VERTICAL
        tick_spacing = SPEC_PLOT_SIZE // 6
        line_length = spectrum.shape[0] * spectrum.metadata[model.MD_PIXEL_SIZE][1]
        value_range = (0, line_length)
        scale_y_draw = numpy.empty((client_size.y, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SPEC_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        unit = "m"
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit, SPEC_SCALE_WIDTH, font_size, "Distance from origin")

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bar attached
        extend = numpy.empty((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        extend.fill(255)
        scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
        data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

        # Right
        scale_y_draw = numpy.empty((client_size.y, SMALL_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SMALL_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit, SMALL_SCALE_WIDTH, font_size, mirror=True)

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bars attached
        scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
        data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

        line_img = model.DataArray(data_with_legend)
        line_img.metadata[model.MD_DIMS] = 'YXC'
        return line_img


def temporal_spectrum_to_export_data(stream, raw):
    """
    Creates either raw or WYSIWYG representation for the temporal spectrum data

    stream (SpectrumStream)
    raw (boolean): if True returns raw representation

    returns (model.DataArray)
    """

    spectrum = stream.get_pixel_time_spectrum()

    md = spectrum.metadata
    spectrum = numpy.swapaxes(spectrum, 0, 1)  # Make sure the order is T vs C
    spectrum = model.DataArray(img.DataArray2RGB(spectrum), md)

    if spectrum is None:
        raise LookupError("No pixel selected to pick a spectrum")
    spectrum_range, unit_wl = stream.get_spectrum_range()
    time_range, unit_tl = stream.get_time_values()

    if raw:
        data = spectrum.C  # switch axes
        if unit_wl == "m":
            data.metadata[model.MD_WL_LIST] = spectrum_range
        if unit_tl == "s":
            data.metadata[model.MD_TIME_LIST] = time_range
        data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_STREAK
        return data
    else:
        images = set_images([(spectrum, (0, 0), (1, 1), True, None, None, None, None, "Spatial Spectrum", None, None, {})])
        # TODO: just use a standard tuple, instead of wx.Size
        client_size = wx.Size(SPEC_PLOT_SIZE, SPEC_PLOT_SIZE)
        im = images[0]  # just one image
        # adjust to viewport size
        scale = (im.shape[1] / client_size.x, im.shape[0] / client_size.y)
        # Make surface based on the maximum resolution
        data_to_draw = numpy.zeros((client_size.y, client_size.x, 4), dtype=numpy.uint8)
        data_to_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, client_size.x, client_size.y)
        ctx = cairo.Context(surface)

        buffer_center = (0, 0)
        buffer_scale = scale
        buffer_size = client_size.x, client_size.y

        draw_image(
            ctx,
            im,
            im.metadata['dc_center'],
            buffer_center,
            buffer_scale,
            buffer_size,
            1.0,
            im_scale=im.metadata['dc_scale'],
            rotation=im.metadata['dc_rotation'],
            shear=im.metadata['dc_shear'],
            flip=im.metadata['dc_flip'],
            blend_mode=im.metadata['blend_mode'],
            interpolate_data=False
        )

        # Draw top/bottom horizontal (wavelength) legend
        text_colour = (0, 0, 0)  # black
        value_range = (spectrum_range[0], spectrum_range[-1])
        orientation = wx.HORIZONTAL
        tick_spacing = SPEC_PLOT_SIZE // 4
        font_size = SPEC_PLOT_SIZE * SPEC_FONT_SIZE
        scale_x_draw = numpy.empty((SPEC_SCALE_HEIGHT, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SPEC_SCALE_HEIGHT)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit_wl, SPEC_SCALE_HEIGHT, font_size, "Wavelength")
        data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

        # Top
        scale_x_draw = numpy.empty((SMALL_SCALE_WIDTH, client_size.x, 4), dtype=numpy.uint8)
        scale_x_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_x_draw, cairo.FORMAT_ARGB32, client_size.x, SMALL_SCALE_WIDTH)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit_wl, SMALL_SCALE_WIDTH, font_size, mirror=True)
        data_with_legend = numpy.append(scale_x_draw, data_with_legend, axis=0)

        # Draw left vertical (time) legend
        orientation = wx.VERTICAL
        tick_spacing = SPEC_PLOT_SIZE // 6
        line_length = spectrum.shape[0] * spectrum.metadata[model.MD_PIXEL_SIZE][1]
        value_range = (0, line_length)
        scale_y_draw = numpy.empty((client_size.y, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SPEC_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit_tl, SPEC_SCALE_WIDTH, font_size, "Time")

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bar attached
        extend = numpy.empty((SPEC_SCALE_HEIGHT, SPEC_SCALE_WIDTH, 4), dtype=numpy.uint8)
        extend.fill(255)
        scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :], scale_y_draw, axis=0)
        data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

        # Right
        scale_y_draw = numpy.empty((client_size.y, SMALL_SCALE_WIDTH, 4), dtype=numpy.uint8)
        scale_y_draw.fill(255)
        surface = cairo.ImageSurface.create_for_data(
            scale_y_draw, cairo.FORMAT_ARGB32, SMALL_SCALE_WIDTH, client_size.y)
        ctx = cairo.Context(surface)
        draw_scale(ctx, value_range, client_size, orientation, tick_spacing,
                   text_colour, unit_tl, SMALL_SCALE_WIDTH, font_size, mirror=True)

        # Extend y scale bar to fit the height of the bar plot with the x
        # scale bars attached
        scale_y_draw = numpy.append(scale_y_draw, extend[:, :SMALL_SCALE_WIDTH], axis=0)
        scale_y_draw = numpy.append(extend[:SMALL_SCALE_WIDTH, :SMALL_SCALE_WIDTH], scale_y_draw, axis=0)
        data_with_legend = numpy.append(data_with_legend, scale_y_draw, axis=1)

        line_img = model.DataArray(data_with_legend)
        line_img.metadata[model.MD_DIMS] = 'YXC'
        return line_img


def draw_export_legend(images, buffer_size, buffer_scale,
                       hfw, date, stream=None, logo=None):
    """
    Draws legend to be attached to the exported image
    stream (None or Stream): if provided, the text corresponding to this stream
      will be indicated by a bullet before the name
    return (ndarray of 3 dims Y,X,4) : the legend in RGB
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
    medium_font = buffer_size[0] * MEDIUM_FONT
    small_font = buffer_size[0] * SMALL_FONT  # used for stream data
    arc_radius = buffer_size[0] * ARC_RADIUS
    tint_box_size = buffer_size[0] * TINT_SIZE
    # Just make cell dimensions analog to the image buffer dimensions
    big_cell_height = buffer_size[0] * MAIN_LAYER
    small_cell_height = buffer_size[0] * SUB_LAYER
    cell_x_step = buffer_size[0] * CELL_WIDTH
    legend_ctx.set_source_rgb(0, 0, 0)
    legend_ctx.rectangle(0, 0, buffer_size[0], n * small_cell_height + big_cell_height)
    legend_ctx.fill()
    legend_ctx.set_source_rgb(1, 1, 1)
    legend_ctx.set_line_width(buffer_size[0] * LINE_THICKNESS)

    # draw separation lines
    legend_y_pos = big_cell_height
    legend_ctx.move_to(0, legend_y_pos)
    legend_ctx.line_to(buffer_size[0], legend_y_pos)
    legend_ctx.stroke()
    for i in range(n - 1):
        legend_y_pos += small_cell_height
        legend_ctx.move_to(0, legend_y_pos)
        legend_ctx.line_to(buffer_size[0], legend_y_pos)
        legend_ctx.stroke()

    # Write: HFW | Scale bar | date | logos
    legend_ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL)
    legend_ctx.set_font_size(large_font)
    legend_x_pos = init_x_pos
    legend_y_pos = MAIN_MIDDLE * buffer_size[0]
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    # TODO: not done => remove

    # write HFW
    legend_x_pos += cell_x_step
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    hfw = units.round_significant(hfw, 4)
    label = u"HFW: %s" % units.readable_str(hfw, "m", sig=3)
    legend_ctx.show_text(label)

    # Draw scale bar
    # min_bar_width = cell_x_step - init_x_pos
    max_bar_width = 2 * cell_x_step - 2 * init_x_pos
    max_actual_width = max_bar_width * buffer_scale[0]
    actual_width = units.round_down_significant(max_actual_width, 1)
    bar_width = int(round(actual_width / buffer_scale[0]))

    legend_ctx.set_line_width(buffer_size[0] * BAR_THICKNESS)
    legend_x_pos += cell_x_step
    legend_y_pos = (big_cell_height / 2) - (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_y_pos = (big_cell_height / 2) + (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.line_to(legend_x_pos, legend_y_pos)
    bar_line = bar_width * 0.375
    legend_y_pos = big_cell_height / 2
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

    legend_y_pos = big_cell_height / 2
    legend_x_pos += 1.1 * plw
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_x_pos += bar_line
    legend_ctx.line_to(legend_x_pos, legend_y_pos)
    legend_y_pos = (big_cell_height / 2) - (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    legend_y_pos = (big_cell_height / 2) + (BAR_HEIGHT * buffer_size[0] / 2)
    legend_ctx.line_to(legend_x_pos, legend_y_pos)
    legend_ctx.stroke()

    # write acquisition date
    legend_ctx.set_font_size(medium_font)
    legend_x_pos += 2 * cell_x_step - (bar_width + init_x_pos)
    legend_y_pos = MAIN_UPPER * buffer_size[0]
    legend_ctx.move_to(legend_x_pos, legend_y_pos)
    if date is not None:
        label = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(date))
        date_split = label.split()
        legend_ctx.show_text(date_split[0])
        legend_y_pos = MAIN_LOWER * buffer_size[0]
        legend_ctx.move_to(legend_x_pos, legend_y_pos)
        legend_ctx.show_text(date_split[1])

    # TODO: allow to insert another logo or text
    # => pass a string (= text) or a 2D or 3D numpy array (image)

    # write delmic logo
    if logo is not None:
        logo_surface = cairo.ImageSurface.create_from_png(guiimg.getStream(logo))
        logo_scale_x = ((cell_x_step / 2) - init_x_pos) / logo_surface.get_width()
        legend_ctx.save()
        # Note: Goal of antialiasing & interpolation is to smooth the edges when
        # downscaling the logo. It only works with cairo v1.14 or newer.
        surfpat = cairo.SurfacePattern(logo_surface)
        # Since cairo v1.14, FILTER_BEST is different from BILINEAR.
        # Downscaling and upscaling < 2x is nice, but above that, it just
        # makes the pixels big (and antialiased)
        if logo_scale_x > 2:
            surfpat.set_filter(cairo.FILTER_BILINEAR)
        else:
            surfpat.set_filter(cairo.FILTER_BEST)

        logo_h_height = (logo_scale_x * logo_surface.get_height()) / 2
        legend_ctx.translate(buffer_size[0] - (cell_x_step / 2), (big_cell_height / 2) - logo_h_height)
        legend_ctx.scale(logo_scale_x, logo_scale_x)
        legend_ctx.set_source(surfpat)
        legend_ctx.paint()
        legend_ctx.restore()

    # Write stream data, sorted by acquisition date (and fallback on stable order)
    legend_ctx.set_font_size(small_font)
    legend_y_pos = big_cell_height

    sorted_im = sorted(images, key=lambda im: im.metadata['date'])
    for im in sorted_im:
        s = im.metadata['stream']
        md = im.metadata['metadata']
        if s is stream:
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
        legend_ctx.show_text(s.name.value)

        # If stream has tint, draw the colour in a little square next to the name
        if stream is None and isinstance(s, (acqstream.FluoStream, acqstream.StaticFluoStream)):
            tint = s.tint.value
            legend_ctx.set_source_rgb(*conversion.rgb_to_frgb(tint))
            legend_ctx.rectangle(legend_x_pos + cell_x_step - tint_box_size - small_font,
                                 legend_y_pos - small_font,
                                 tint_box_size, tint_box_size)
            legend_ctx.fill()
            legend_ctx.set_source_rgb(1, 1, 1)

        legend_x_pos += cell_x_step
        legend_y_pos_store = legend_y_pos
        for i, d in enumerate(_get_stream_legend_text(md)):
            legend_ctx.move_to(legend_x_pos, legend_y_pos)
            legend_ctx.show_text(d)
            if (i % 2 == 1):
                legend_x_pos += cell_x_step
                legend_y_pos = legend_y_pos_store
            else:
                legend_y_pos += (SUB_LOWER - SUB_UPPER) * buffer_size[0]
        legend_y_pos = (legend_y_pos_store + (small_cell_height - SUB_UPPER * buffer_size[0]))

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
            captions.append(u"%s x" % units.readable_str(md[model.MD_LENS_MAG], sig=2))
        if model.MD_FILTER_NAME in md:
            captions.append(md[model.MD_FILTER_NAME])
        if model.MD_LIGHT_POWER in md:
            captions.append(units.readable_str(md[model.MD_LIGHT_POWER], "W", sig=3))
        if model.MD_EBEAM_VOLTAGE in md:
            captions.append(units.readable_str(abs(md[model.MD_EBEAM_VOLTAGE]), "V", sig=3))
        if model.MD_EBEAM_CURRENT in md:
            captions.append(units.readable_str(md[model.MD_EBEAM_CURRENT], "A", sig=3))
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
            if isinstance(s.raw, tuple): # 2D tuple = tiles
                data_raw = img.mergeTiles(s.raw)
            else:
                data_raw = s.raw[0]

            if model.hasVA(s, "zIndex"):
                data_raw = img.getYXFromZYX(s.raw[0], s.zIndex.value)

            # Pretend to be RGB for the drawing by cairo
            if numpy.can_cast(im_min_type, min_type(data_raw)):
                im_min_type = min_type(data_raw)

            if data_raw.ndim > 2:
                # It's not (just) spatial => need to project it
                if isinstance(s, acqstream.SpectrumStream):
                    data_raw = s.get_spatial_spectrum(raw=raw)
                else:
                    logging.warning("Doesn't know how to export data of %s spatial raw", s.name.value)
                    continue

            # Split the bits in R,G,B,A
            data = _pack_data_into_rgba(data_raw)

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
        if isinstance(ostream, acqstream.OpticalStream):
            images_opt.append((data, BLEND_SCREEN, ostream, md))
        elif isinstance(ostream, (acqstream.SpectrumStream, acqstream.CLStream)):
            images_spc.append((data, BLEND_DEFAULT, ostream, md))
        else:
            images_std.append((data, BLEND_DEFAULT, ostream, md))

    # Sort by size, so that the biggest picture is first drawn (no opacity)
    def get_area(d):
        return numpy.prod(d[0].shape[0:2]) * d[0].metadata[model.MD_PIXEL_SIZE][0]

    images_opt.sort(key=get_area, reverse=True)
    images_spc.sort(key=get_area, reverse=True)
    images_std.sort(key=get_area, reverse=True)

    # Reset the first image to be drawn to the default blend operator to be
    # drawn full opacity (only useful if the background is not full black)
    if images_opt:
        images_opt[0] = (images_opt[0][0], BLEND_DEFAULT, images_opt[0][2], images_opt[0][3])

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
    for rgbim, blend_mode, stream, md in images:
        rgba_im = format_rgba_darray(rgbim)
        keepalpha = False
        date = rgbim.metadata.get(model.MD_ACQ_DATE, None)
        scale = rgbim.metadata[model.MD_PIXEL_SIZE]
        pos = rgbim.metadata[model.MD_POS]
        rot = rgbim.metadata.get(model.MD_ROTATION, 0)
        shear = rgbim.metadata.get(model.MD_SHEAR, 0)
        flip = rgbim.metadata.get(model.MD_FLIP, 0)

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


def images_to_export_data(streams, view_hfw, view_pos,
                          draw_merge_ratio, raw=False,
                          interpolate_data=False, logo=None):
    """
    streams (Streams or DataProjection): the data to be exported
    view_hfw (tuple of float): X (width), Y (height) in m
    view_pos (tuple of float): center position X, Y in m
    raw (bool): if False, generates one RGB image out of all the streams, otherwise
      generates one image per stream using the raw data
    logo (RGBA DataArray): Image to display in the legend
    return (list of DataArray)
    raise LookupError: if no data visible in the selected FoV
    """
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

    n = len(images)
    for i, im in enumerate(images):
        if raw or i == 0:  # when print-ready, share the surface to draw
            # Make surface based on the maximum resolution
            data_to_draw = numpy.zeros((buffer_size[1], buffer_size[0], 4), dtype=numpy.uint8)
            surface = cairo.ImageSurface.create_for_data(
                data_to_draw, cairo.FORMAT_ARGB32, buffer_size[0], buffer_size[1])
            ctx = cairo.Context(surface)

        if im.metadata['blend_mode'] == BLEND_SCREEN or raw:
            # No transparency in case of "raw" export
            merge_ratio = 1.0
        elif i == n - 1: # last image
            if n == 1:
                merge_ratio = 1.0
            else:
                merge_ratio = draw_merge_ratio
        else:
            merge_ratio = 1 - i / n

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
            blend_mode=im.metadata['blend_mode'],
            interpolate_data=interpolate_data
        )

        # Create legend for each raw image
        if raw:
            legend_rgb = draw_export_legend(images, buffer_size, buffer_scale,
                                            view_hfw[0], im.metadata['date'],
                                            im.metadata['stream'], logo)

            new_data_to_draw = _unpack_raw_data(data_to_draw, im_min_type)
            legend_as_raw = _adapt_rgb_to_raw(legend_rgb, new_data_to_draw)
            data_with_legend = numpy.append(new_data_to_draw, legend_as_raw, axis=0)

            md = {model.MD_DESCRIPTION: im.metadata['name']}
            data_to_export.append(model.DataArray(data_with_legend, md))

    # Create legend for print-ready
    if not raw:
        date = max(im.metadata['date'] for im in images)
        legend_rgb = draw_export_legend(images, buffer_size, buffer_scale,
                                        view_hfw[0], date, logo=logo)
        data_with_legend = numpy.append(data_to_draw, legend_rgb, axis=0)
        data_with_legend[:, :, [2, 0]] = data_with_legend[:, :, [0, 2]]
        md = {model.MD_DIMS: 'YXC'}
        data_to_export.append(model.DataArray(data_with_legend, md))

    return data_to_export


def _adapt_rgb_to_raw(imrgb, data_raw):
    """
    imrgb (ndarray Y,X,4): RGB image to convert to a greyscale
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


def _pack_data_into_rgba(data_raw):
    """
    Convert a "raw" data (as in "greyscale") into data pretending to be RGBA 8-bit
    data_raw (ndarray Y,X)
    return (ndrarray Y,X,4)
    """
    data = numpy.empty((data_raw.shape[0], data_raw.shape[1], 4), dtype=numpy.uint8)
    # TODO: why in this order?? due to RGBA -> BGRA after?
    data[:, :, 0] = numpy.right_shift(data_raw[:, :], 8) & 255
    data[:, :, 1] = data_raw[:, :] & 255
    data[:, :, 2] = numpy.right_shift(data_raw[:, :], 16) & 255
    data[:, :, 3] = numpy.right_shift(data_raw[:, :], 24) & 255
    return model.DataArray(data, data_raw.metadata)


def _unpack_raw_data(imrgb, dtype):
    """
    Convert back the "raw" (as in "greyscale with lots of bits") data from data
      pretending to be RGBA 8-bit

    imrgb (ndarray Y,X,4)
    dtype: type of the output data (should be some uint <= 64 bits)
    return (ndrarray Y,X)
    """
    imraw = (numpy.left_shift(imrgb[:, :, 2], 8, dtype=numpy.uint32)
             | imrgb[:, :, 1]
             | numpy.left_shift(imrgb[:, :, 0], 16, dtype=numpy.uint32)
             | numpy.left_shift(imrgb[:, :, 3], 24, dtype=numpy.uint32))
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
            new_im = scale_to_alpha(new_im)

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
        bim = wx.Bitmap(size[0], size[1], 24)
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
    Returns 3D DataArray with the same shape as tile (updated ovv)
    """
    # TODO: use cairo to insert images. Not required for current specification, but
    # 'blend_screen' mode insertion might be necessary to overlay multiple optical images
    # in future implementations.

    # Tile parameters
    tile_pos_m = tile.metadata[model.MD_POS]
    tile_mpp = tile.metadata[model.MD_PIXEL_SIZE]
    x, y, c = tile.shape
    tile_size_m = (x * tile_mpp[0], y * tile_mpp[1])

    # Ovv parameters
    ovv_mpp = ovv.metadata[model.MD_PIXEL_SIZE]
    ovv_sz = ovv.shape[1], ovv.shape[0]

    # Tile size, pos in ovv image (px)
    tile_sz_px = (int(round(tile_size_m[0] / ovv_mpp[0])),
                  int(round(tile_size_m[1] / ovv_mpp[1])),
                  c)
    if 0 in tile_sz_px:
        logging.debug("Not drawing tile which would be too small %s", tile_sz_px)
        return ovv
    tile_pos_px = (int(tile_pos_m[0] / ovv_mpp[0]), int(tile_pos_m[1] / ovv_mpp[1]))
    # Change origin of coordinates from center of the image to top left of ovv image,
    # the position indicates the top left part of the tile, not its center.
    pos_top_left = (int(tile_pos_px[0] + ovv_sz[0] / 2 - tile_sz_px[0] / 2),
                    int(ovv_sz[1] / 2 - tile_pos_px[1] - tile_sz_px[1] / 2))

    # Crop tile on edges of overview image
    if pos_top_left[0] + tile_sz_px[0] > ovv_sz[0]:
        diff_x2 = pos_top_left[0] + tile_sz_px[0] - ovv_sz[0]
        x_right = tile_sz_px[0] - diff_x2
    else:
        diff_x2 = 0
        x_right = tile_sz_px[0]

    if pos_top_left[0] < 0:
        diff_x = -pos_top_left[0]
        pos_top_left = (0, pos_top_left[1])
    else:
        diff_x = 0

    if pos_top_left[1] + tile_sz_px[1] > ovv_sz[1]:
        diff_y2 = pos_top_left[1] + tile_sz_px[1] - ovv_sz[1]
        y_right = tile_sz_px[1] - diff_y2
    else:
        diff_y2 = 0
        y_right = tile_sz_px[1]

    if pos_top_left[1] < 0:
        diff_y = -pos_top_left[1]
        pos_top_left = (pos_top_left[0], 0)
    else:
        diff_y = 0

    ovv[pos_top_left[1]: pos_top_left[1] + tile_sz_px[1] - diff_y - diff_y2,
        pos_top_left[0]: pos_top_left[0] + tile_sz_px[0] - diff_x - diff_x2] = \
        img.rescale_hq(tile, (tile_sz_px[1], tile_sz_px[0], c))[diff_y:y_right, diff_x:x_right]
    return ovv

def merge_screen(im, background):
    """ 
    Merges two images (im and background) into one using the "screen" operator:
    f(xA,xB) = xA + xB − xA·xB (with values between 0 and 1, with each channel independent)
    im, background: DataArray with im.shape = background.shape (either RGB or RGBA)
    returns RGB DataArray (of the same shape as im)
    """
    assert im.shape == background.shape, "Images have different shapes."
    if im.shape[-1] != 3 and im.shape[-1] != 4:
        raise ValueError("Ovv images have an invalid number of channels: %d" % (im.shape[-1]))

    md = im.metadata.copy()
    md["dc_keepalpha"] = True
    im = format_rgba_darray(im, 255)  # convert to BGRA
    im = model.DataArray(im, md)
    background = format_rgba_darray(background, 255)

    height, width, _ = im.shape
    buffer_size = (width, height)
    buffer_top_left = (0, 0)
    buffer_scale = (1, 1)

    # Combine images
    surface = cairo.ImageSurface.create_for_data(
        background, cairo.FORMAT_ARGB32, buffer_size[0], buffer_size[1])
    ctx = cairo.Context(surface)

    draw_image(ctx, im, buffer_top_left, buffer_top_left, buffer_scale,
               buffer_size, 1, blend_mode=BLEND_SCREEN)

    # Convert back to RGB
    rgb = numpy.empty((im.shape[0], im.shape[1], 3), dtype=numpy.uint8)
    rgb[:, :, 0] = background[:, :, 2]
    rgb[:, :, 1] = background[:, :, 1]
    rgb[:, :, 2] = background[:, :, 0]
    rgb = model.DataArray(rgb, md)

    return rgb

