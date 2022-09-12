# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2020 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from past.builtins import basestring, long
from collections.abc import Iterable
import cv2
import json
import logging
import math
import numpy
from odemis import model
import re
import yaml


def wavelength2rgb(wavelength):
    """
    Convert a wavelength into a (r, g, b) value
    wavelength (0 < float): wavelength in m
    return (3-tuple int in 0..255): RGB value

    Notes:
    Inspired by code from:
    http://codingmess.blogspot.nl/2009/05/conversion-of-wavelength-in-nanometers.html
    based on:
    http://www.physics.sfasu.edu/astro/color/spectra.html
    """
    wavelength *= 1e9  # Convert the wavelength from [m] to [nm]

    # maps w to a single color value between 0 and 255 for r1 > r2 and between 255 and 0 for r1 < r2
    w2color = lambda w, r1, r2: abs(-round((w - r2) / abs((r2 - r1)) * 255))

    if wavelength < 440:
        # min for clipping below 350; outside the visible spectrum use purple as a fixed colour
        red = min(w2color(wavelength, 350, 440), 255)
        return red, 0, 255  # red changes from 255 to 0 with blue 255 (purple to blue)
    elif wavelength < 490:
        return 0, w2color(wavelength, 490, 440), 255  # green changes from 0 to 255 with blue 255 (blue to turquoise)
    elif wavelength < 510:
        return 0, 255, w2color(wavelength, 490, 510)  # blue changes from 255 to 0 with green 255 (turquoise to green)
    elif wavelength < 580:
        return w2color(wavelength, 580, 510), 255, 0  # red changes from 0 to 255 with green 255 (green to yellow)
    elif wavelength < 645:
        return 255, w2color(wavelength, 580, 645), 0  # green changes from 255 to 0 with red 255 (yellow to red)
    else:
        return 255, 0, 0  # outside the visible spectrum use red as a fixed colour


def hex_to_rgb(hex_str):
    """  Convert a Hexadecimal colour representation into a 3-tuple of RGB integers

    :param hex_str: str  Colour value of the form '#FFFFFF'
    :rtype : (int, int int)

    """

    if len(hex_str) != 7:
        raise ValueError("Invalid HEX colour %s" % hex_str)
    hex_str = hex_str[-6:]
    return tuple(int(hex_str[i:i + 2], 16) for i in [0, 2, 4])


def hex_to_rgba(hex_str, af=255):
    """ Convert a Hexadecimal colour representation into a 4-tuple of RGBA ints

    :param hex_str: str  Colour value of the form '#FFFFFF'
    :param af: int  Alpha value in the range [0..255]
    :rtype : (int, int int, int)

    """

    if len(hex_str) != 7:
        raise ValueError("Invalid HEX colour %s" % hex_str)
    return hex_to_rgb(hex_str) + (af,)


def rgb_to_frgb(rgb):
    """ Convert an integer RGB value into a float RGB value

    :param rgb: (int, int, int) RGB values in the range [0..255]
    :return: (float, float, float)

    """

    if len(rgb) != 3:
        raise ValueError("Illegal RGB colour %s" % rgb)
    return tuple(v / 255.0 for v in rgb)


def rgba_to_frgba(rgba):
    """ Convert an integer RGBA value into a float RGBA value

    :param rgba: (int, int, int, int) RGBA values in the range [0..255]
    :return: (float, float, float, float)

    """

    if len(rgba) != 4:
        raise ValueError("Illegal RGB colour %s" % rgba)
    return tuple(v / 255.0 for v in rgba)


def frgb_to_rgb(frgb):
    """ Convert an float RGB value into an integer RGB value

    :param frgb: (float, float, float) RGB values in the range [0..1]
    :return: (int, int, int)

    """

    if len(frgb) != 3:
        raise ValueError("Illegal RGB colour %s" % frgb)
    return tuple(int(v * 255) for v in frgb)


def frgba_to_rgba(frgba):
    """ Convert an float RGBA value into an integer RGBA value

    :param rgba: (float, float, float, float) RGBA values in the range [0..1]
    :return: (int, int, int, int)

    """

    if len(frgba) != 4:
        raise ValueError("Illegal RGB colour %s" % frgba)
    return tuple(int(v * 255) for v in frgba)


def hex_to_frgb(hex_str):
    """ Convert a Hexadecimal colour representation into a 3-tuple of floats
    :rtype : (float, float, float)
    """
    return rgb_to_frgb(hex_to_rgb(hex_str))


def hex_to_frgba(hex_str, af=1.0):
    """ Convert a Hexadecimal colour representation into a 4-tuple of floats
    :rtype : (float, float, float, float)
    """
    return rgba_to_frgba(hex_to_rgba(hex_str, int(af * 255)))


# String -> VA conversion helper
def convert_to_object(s):
    """
    Tries to convert a string to a (simple) object.
    s (str): string that will be converted
    return (object) the value contained in the string with the type of the real value
    raises
      ValueError() if not possible to convert
    """
    try:
        # be nice and accept list and dict without [] or {}
        fixed = s.strip()
        if re.match(
                r"([-.a-zA-Z0-9_]+\s*:\s+[-.a-zA-Z0-9_]+)(\s*,\s*([-.a-zA-Z0-9_]+\s*:\s+[-.a-zA-Z0-9_]+))*$",
                fixed):  # a dict?
            fixed = "{" + fixed + "}"
        elif re.match(r"[-.a-zA-Z0-9_]+(\s*,\s*[-.a-zA-Z0-9_]+)+$", fixed):  # a list?
            fixed = "[" + fixed + "]"
        # We could also use ast.literal_eval() to accept Python syntax instead,
        # but as the microscope file is in YAML, it might be easier for the user
        # that this follows the same syntax.
        return yaml.safe_load(fixed)
    except yaml.YAMLError as exc:
        logging.error("Syntax error: %s", exc)
        # TODO: with Python3: raise from?
        raise ValueError("Failed to parse %s" % s)


def boolify(s):
    if s == 'True' or s == 'true':
        return True
    if s == 'False' or s == 'false':
        return False
    raise ValueError('Not a boolean value: %s' % s)


def reproduce_typed_value(typed_value, str_val):
    """ Convert a string to the type of the given typed value

    Args:
        typed_value: (object) Example value with the type that must be converted to
        str_val: (string) String to be converted

    Returns:
        (object) The converted string value:

    Raises:
        ValueError: if not possible to convert
        TypeError: if type of real value is not supported

    """

    if isinstance(typed_value, bool):
        return boolify(str_val)
    elif isinstance(typed_value, int):
        return int(str_val)
    elif isinstance(typed_value, float):
        return float(str_val)
    elif isinstance(typed_value, basestring):
        return str_val
    # Process dictionaries before matching against Iterables
    elif isinstance(typed_value, dict):
        # Grab the first key/value pair, to determine their types
        if typed_value:
            key_typed_val = list(typed_value.keys())[0]
            value_typed_val = typed_value[key_typed_val]
        else:
            logging.warning("Type of attribute is unknown, using string")
            key_typed_val = ""
            value_typed_val = ""

        dict_val = {}

        for sub_str in str_val.split(','):
            item = sub_str.split(':')
            if len(item) != 2:
                raise ValueError("Cannot convert '%s' to a dictionary item" % item)
            key = reproduce_typed_value(key_typed_val, item[0])
            value = reproduce_typed_value(value_typed_val, item[1])
            dict_val[key] = value

        return dict_val
    elif isinstance(typed_value, Iterable):
        if typed_value:
            typed_val_elm = typed_value[0]
        else:
            logging.warning("Type of attribute is unknown, using string")
            typed_val_elm = ""

        # Try to be open-minded if the sub-type is a number (so that things like
        # " 3 x 5 px" returns (3, 5)
        if isinstance(typed_val_elm, (int, long)):
            pattern = r"[+-]?[\d]+"  # ex: -15
        elif isinstance(typed_val_elm, float):
            pattern = r"[+-]?[\d.]+(?:[eE][+-]?[\d]+)?"  # ex: -156.41e-9
        else:
            pattern = "[^,]+"

        iter_val = []

        for sub_str in re.findall(pattern, str_val):
            iter_val.append(reproduce_typed_value(typed_val_elm, sub_str))

        # Cast to detected type
        final_val = type(typed_value)(iter_val)

        return final_val

    raise TypeError("Type %r is not supported to convert %s" % (type(typed_value), str_val))


def ensure_tuple(v):
    """
    Recursively convert an iterable object into a tuple
    v (iterable or object): If it is an iterable, it will be converted into a tuple, and
      otherwise it will be returned as is
    return (tuple or object): same a v, but a tuple if v was iterable
    """
    if isinstance(v, Iterable) and not isinstance(v, basestring):
        # convert to a tuple, with each object contained also converted
        return tuple(ensure_tuple(i) for i in v)
    else:
        return v


def get_img_transformation_matrix(md):
    """
    Computes the 2D transformation matrix based on the given metadata.
    md (dict str -> value): the metadata (of the DataArray) containing MD_PIXEL_SIZE 
        and possibly also MD_ROTATION and MD_SHEAR.
    return (numpy.array of 2,2 floats): the 2D transformation matrix
    """

    if model.MD_PIXEL_SIZE not in md:
        raise ValueError("MD_PIXEL_SIZE must be set")
    ps = md[model.MD_PIXEL_SIZE]
    rotation = md.get(model.MD_ROTATION, 0.0)
    shear = md.get(model.MD_SHEAR, 0.0)

    # Y pixel coordinates goes down, but Y coordinates in world goes up
    # The '-' before ps[1] is there to make this conversion
    ps_mat = numpy.array([[ps[0], 0], [0, -ps[1]]])
    rcos, rsin = math.cos(rotation), math.sin(rotation)
    rot_mat = numpy.array([[rcos, -rsin], [rsin, rcos]])
    shear_mat = numpy.array([[1, 0], [-shear, 1]])
    return rot_mat @ shear_mat @ ps_mat


def get_tile_md_pos(i, tile_size, tileda, origda):
    """
    Compute the position of the center of the tile, aka MD_POS.
    i (int, int): the tile index (X, Y)
    tile_size (int>0, int>0): the standard size of a tile in the (X, Y)
    tileda (DataArray): the tile data, with MD_PIXEL_SIZE in its metadata.
        It can be smaller than the tile_size in case
    origda (DataArray or DataArrayShadow): the original/raw DataArray. If
        no MD_POS is provided, the image is considered located at (0,0).
    return (float, float): the center position
    """
    md = origda.metadata
    tile_md = tileda.metadata
    md_pos = numpy.asarray(md.get(model.MD_POS, (0.0, 0.0)))
    if model.MD_PIXEL_SIZE not in md or model.MD_PIXEL_SIZE not in tile_md:
        raise ValueError("MD_PIXEL_SIZE must be set")
    orig_ps = numpy.asarray(md[model.MD_PIXEL_SIZE])
    tile_ps = numpy.asarray(tile_md[model.MD_PIXEL_SIZE])

    dims = md.get(model.MD_DIMS, "CTZYX"[-origda.ndim::])
    img_shape = [origda.shape[dims.index('X')], origda.shape[dims.index('Y')]]
    img_shape = numpy.array(img_shape, numpy.float)
    # center of the image in pixels
    img_center = img_shape / 2

    tile_shape = [tileda.shape[dims.index('X')], tileda.shape[dims.index('Y')]]
    # center of the tile in pixels
    tile_center_pixels = numpy.array([
        i[0] * tile_size[0] + tile_shape[0] / 2,
        i[1] * tile_size[1] + tile_shape[1] / 2]
    )
    # convert to the original image coordinates
    tile_center_pixels *= tile_ps / orig_ps
    # center of the tile relative to the center of the image
    tile_rel_to_img_center_pixels = tile_center_pixels - img_center

    # calculate the transformation matrix
    tmat = get_img_transformation_matrix(md)

    # Converts the tile_rel_to_img_center_pixels array of coordinates to a 2 x 1 matrix
    # The numpy.array(array) function returns a 1 x 2 matrix, so .transpose() is called
    # to transpose the matrix
    tile_rel_to_img_center_pixels = numpy.array(tile_rel_to_img_center_pixels).transpose()
    # calculate the new position of the tile, relative to the center of the image,
    # in world coordinates
    new_tile_pos_rel = tmat @ tile_rel_to_img_center_pixels
    new_tile_pos_rel = numpy.ravel(new_tile_pos_rel)
    # calculate the final position of the tile, in world coordinates
    tile_pos_world_final = md_pos + new_tile_pos_rel
    return tuple(tile_pos_world_final)


def get_img_transformation_md(mat, timage, src_img):
    """
    Computes the metadata of the transformations from the transformation matrix
    It is an approximation, as a 3 x 3 matrix cannot be fully represented only
    with translation, scale, rotation and shear (eg, no "keystone" shape possible).
    mat (ndarray of shape 3,3): transformation matrix (the OpenCV format).
    timage (numpy.array): Transformed image
    src_image (numpy.array): Source image. It should at least contain MD_PIXEL_SIZE
    return (dict str value): metadata with MD_POS, MD_PIXEL_SIZE, MD_ROTATION, MD_SHEAR.
    raise ValueError: If the transformation matrix is incorrect
    """
    # Check the scale is not null (mathematically, it's allowed, meaning that the
    # other image is just a single point, but it's very unlikely what the user
    # would want to do, and the rest of the code doesn't deal with this corner
    # case for now).
    if mat[0, 0] * mat[1, 1] * mat[2, 2] == 0:
        raise ValueError("Transformation matrix has null scale")

    # TODO: for now we use rather convoluted (and reliable) way to convert from
    # the transformation matrix to the values, passing by OpenCV. There should
    # be a more straightforward mathematical path to achieve the same.

    half_size = (timage.shape[1] / 2, timage.shape[0] / 2)
    img_src_center = (src_img.shape[1] / 2, src_img.shape[0] / 2)

    # project some key points from the original image on the transformed image
    points = [
        [half_size[0], half_size[1]],
        [0.0, 0.0],
        [timage.shape[1], 0.0],
        [0.0, timage.shape[0]],
    ]
    converted_points = cv2.perspectiveTransform(numpy.array([points]), mat)[0]

    center_point = converted_points[0]
    top_left_point = converted_points[1]
    top_right_point = converted_points[2]
    bottom_left_point = converted_points[3]

    def length(p1, p2):
        dif_x = p2[0] - p1[0]
        dif_y = p2[1] - p1[1]
        return math.hypot(dif_x, dif_y)

    top_length = length(top_left_point, top_right_point)
    scale_x = top_length / timage.shape[1]

    left_length = length(top_left_point, bottom_left_point)
    scale_y = left_length / timage.shape[0]

    diag_length = length(bottom_left_point, top_right_point)
    # using the law of cosines
    corner_ang = math.acos((left_length ** 2 + top_length ** 2 - diag_length ** 2) /
                           (2 * left_length * top_length))
    shear = math.tan(corner_ang - math.pi / 2)

    b = mat[0, 1]
    d = mat[1, 1]
    sin_full = -b / scale_y
    cos_full = d / scale_y
    rot = math.atan2(sin_full, cos_full)

    translation_x = center_point[0] - img_src_center[0]
    translation_y = center_point[1] - img_src_center[1]

    # TODO: if no MD_PIXEL_SIZE, just provide MD_PIXEL_SIZE_COR?
    # The new pixel size
    src_img_ps = src_img.metadata.get(model.MD_PIXEL_SIZE)
    ps_cor = (scale_x, scale_y)
    new_pixel_size = (src_img_ps[0] * ps_cor[0], src_img_ps[1] * ps_cor[1])

    # Position in physical coordinates
    src_img_pos = src_img.metadata.get(model.MD_POS, (0.0, 0.0))
    pos_cor = (translation_x, -translation_y)
    pos_cor_phys = (pos_cor[0] * src_img_ps[0], pos_cor[1] * src_img_ps[1])

    src_img_rot = src_img.metadata.get(model.MD_ROTATION, 0.0)
    src_img_shear = src_img.metadata.get(model.MD_SHEAR, 0.0)

    metadata = {
        model.MD_POS: (src_img_pos[0] + pos_cor_phys[0],
                       src_img_pos[1] + pos_cor_phys[1]),
        model.MD_PIXEL_SIZE: new_pixel_size,
        model.MD_ROTATION: src_img_rot - rot,
        model.MD_SHEAR: src_img_shear + shear,
    }

    return metadata


class JsonExtraEncoder(json.JSONEncoder):
    """Support for data types that JSON default encoder
    does not do.
    This includes:
        * Numpy array or number
        * Complex number
        * Set
        * Bytes

    Based on astropy.utils.misc.JsonCustomEncoder.
    Use as: json.dumps(obj, cls=JsonExtraEncoder)
    """

    def default(self, obj):
        if isinstance(obj, (numpy.number, numpy.ndarray)):
            return obj.tolist()
        elif isinstance(obj, complex):
            return [obj.real, obj.imag]
        elif isinstance(obj, set):
            return list(obj)
        elif isinstance(obj, bytes):
            return obj.decode()

        return json.JSONEncoder.default(self, obj)
