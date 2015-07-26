#-*- coding: utf-8 -*-
'''
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Éric Piel, Delmic

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

from __future__ import division

import collections
import logging
import re
import wx
import yaml


# Inspired by code from:
# http://codingmess.blogspot.nl/2009/05/conversion-of-wavelength-in-nanometers.html
# based on:
# http://www.physics.sfasu.edu/astro/colour/spectra.html
def wave2rgb(wavelength):
    """
    Convert a wavelength into a (r,g,b) value
    wavelength (0<float): wavelength in m
    return (3-tupe int in 0..255): RGB value
    """
    w = wavelength * 1e9
    # outside of the visible spectrum, use fixed colour
    w = min(max(w, 350), 780)

    # colour
    if 350 <= w < 440:
        r = -(w - 440) / (440 - 350)
        g = 0
        b = 1
    elif 440 <= w < 490:
        r = 0
        g = (w - 440) / (490 - 440)
        b = 1
    elif 490 <= w < 510:
        r = 0
        g = 1
        b = -(w - 510) / (510 - 490)
    elif 510 <= w < 580:
        r = (w - 510) / (580 - 510)
        g = 1
        b = 0
    elif 580 <= w < 645:
        r = 1
        g = -(w - 645) / (645 - 580)
        b = 0
    elif 645 <= w <= 780:
        r = 1
        g = 0
        b = 0
    else:
        logging.warning("Unable to compute RGB for wavelength %d", w)

    return int(round(255 * r)), int(round(255 * g)), int(round(255 * b))


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
    return tuple([v / 255.0 for v in rgb])


def rgba_to_frgba(rgba):
    """ Convert an integer RGBA value into a float RGBA value

    :param rgba: (int, int, int, int) RGBA values in the range [0..255]
    :return: (float, float, float, float)

    """

    if len(rgba) != 4:
        raise ValueError("Illegal RGB colour %s" % rgba)
    return tuple([v / 255.0 for v in rgba])


def frgb_to_rgb(frgb):
    """ Convert an float RGB value into an integer RGB value

    :param frgb: (float, float, float) RGB values in the range [0..1]
    :return: (int, int, int)

    """

    if len(frgb) != 3:
        raise ValueError("Illegal RGB colour %s" % frgb)
    return tuple([int(v * 255) for v in frgb])


def frgba_to_rgba(frgba):
    """ Convert an float RGBA value into an integer RGBA value

    :param rgba: (float, float, float, float) RGBA values in the range [0..1]
    :return: (int, int, int, int)

    """

    if len(frgba) != 4:
        raise ValueError("Illegal RGB colour %s" % frgba)
    return tuple([int(v * 255) for v in frgba])


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


def wxcol_to_rgb(wxcol):
    """ Convert a wx.Colour to an RGB int tuple
    :param wxcol:
    :return:
    """
    return wxcol.Red(), wxcol.Green(), wxcol.Blue()


def wxcol_to_rgba(wxcol):
    """ Convert a wx.Colour to an RGBA int tuple
    :param wxcol:
    :return:
    """
    return wxcol.Red(), wxcol.Green(), wxcol.Blue(), wxcol.Alpha()


def rgb_to_wxcol(rgb):
    """
    :param rgb: (int, int, int)
    :return: wx.Colour
    """
    if len(rgb) != 3:
        raise ValueError("Illegal RGB colour %s" % rgb)
    return wx.Colour(*rgb)


def rgba_to_wxcol(rgba):
    """
    :param rgba: (int, int, int, int)
    :return: wx.Colour
    """
    if len(rgba) != 4:
        raise ValueError("Illegal RGB colour %s" % rgba)
    return wx.Colour(*rgba)


def rgb_to_hex(rgb):
    """ Convert a RGB(A) colour to hexadecimal colour representation
    rgb (3 or 4-tuple of ints): actually works with any length
    return (string): in the form "aef1e532"
    """
    hex_str = "".join("%.2x" % c for c in rgb)
    return hex_str


def hex_to_wxcol(hex_str):
    rgb = hex_to_rgb(hex_str)
    return wx.Colour(*rgb)


def wxcol_to_frgb(wxcol):
    return wxcol.Red() / 255.0, wxcol.Green() / 255.0, wxcol.Blue() / 255.0


def frgb_to_wxcol(frgb):
    return rgb_to_wxcol(frgb_to_rgb(frgb))


def change_brightness(colour, weight):
    """ Brighten or darken a given colour

    See also wx.lib.agw.aui.aui_utilities.StepColour() and Colour.ChangeLightness() from 3.0

    colf (tuple of 3+ 0<float<1): RGB colour (and alpha)
    weight (-1<float<1): how much to brighten (>0) or darken (<0)
    return (tuple of 3+ 0<float<1): new RGB colour

    :type colf: tuple
    :type weight: float
    :rtype : tuple
    """

    _alpha = None

    if isinstance(colour, basestring):
        _col = hex_to_frgb(colour)
        _alpha = None
    elif isinstance(colour, tuple):
        if all([isinstance(v, float) for v in colour]):
            _col = colour[:3]
            _alpha = colour[-1] if len(colour) == 4 else None
        elif all([isinstance(v, int) for v in colour]):
            _col = rgb_to_frgb(colour[:3])
            _alpha = colour[-1] if len(colour) == 4 else None
        else:
            raise ValueError("Unknown colour format (%s)" % (colour,))
    elif isinstance(colour, wx.Colour):
        _col = wxcol_to_frgb(colour)
        _alpha = None
    else:
        raise ValueError("Unknown colour format")

    if weight > 0:
        # blend towards white
        f, lim = min, 1.0
    else:
        # blend towards black
        f, lim = max, 0.0
        weight = -weight

    new_fcol = tuple(f(c * (1 - weight) + lim * weight, lim) for c in _col[:3])

    return new_fcol + (_alpha,) if _alpha is not None else new_fcol


# String -> VA conversion helper
def convertToObject(s):
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
        if re.match(r"([-.a-zA-Z0-9_]+\s*:\s+[-.a-zA-Z0-9_]+)(\s*,\s*([-.a-zA-Z0-9_]+\s*:\s+[-.a-zA-Z0-9_]+))*$", fixed): # a dict?
            fixed = "{" + fixed + "}"
        elif re.match(r"[-.a-zA-Z0-9_]+(\s*,\s*[-.a-zA-Z0-9_]+)+$", fixed): # a list?
            fixed = "[" + fixed + "]"
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


def reproduceTypedValue(real_val, str_val):
    """
    Tries to convert a string to the type of the given value
    real_val (object): example value with the type that must be converted to
    str_val (string): string that will be converted
    return the value contained in the string with the type of the real value
    raises
      ValueError() if not possible to convert
      TypeError() if type of real value is not supported
    """
    if isinstance(real_val, bool):
        return boolify(str_val)
    elif isinstance(real_val, int):
        return int(str_val)
    elif isinstance(real_val, float):
        return float(str_val)
    elif isinstance(real_val, basestring):
        return str_val
    elif isinstance(real_val, dict):  # must be before iterable
        if len(real_val) > 0:
            key_real_val = real_val.keys()[0]
            value_real_val = real_val[key_real_val]
        else:
            logging.warning("Type of attribute is unknown, using string")
            sub_real_val = ""
            value_real_val = ""

        dict_val = {}
        for sub_str in str_val.split(','):
            item = sub_str.split(':')
            if len(item) != 2:
                raise ValueError("Cannot convert '%s' to a dictionary item" % item)
            key = reproduceTypedValue(key_real_val, item[0])  # TODO Should warn if len(item) != 2
            value = reproduceTypedValue(value_real_val, item[1])
            dict_val[key] = value
        return dict_val
    elif isinstance(real_val, collections.Iterable):
        if len(real_val) > 0:
            sub_real_val = real_val[0]
        else:
            logging.warning("Type of attribute is unknown, using string")
            sub_real_val = ""

        # Try to be open-minded if the sub-type is a number (so that things like
        # " 3 x 5 px" returns (3, 5)
        if isinstance(sub_real_val, (int, long)):
            pattern = "[+-]?[\d]+"  # ex: -15
        elif isinstance(sub_real_val, float):
            pattern = "[+-]?[\d.]+(?:[eE][+-]?[\d]+)?"  # ex: -156.41e-9
        else:
            pattern = "[^,]+"

        iter_val = []
        for sub_str in re.findall(pattern, str_val):
            iter_val.append(reproduceTypedValue(sub_real_val, sub_str))
        final_val = type(real_val)(iter_val)  # cast to real type
        return final_val

    raise TypeError("Type %r is not supported to convert %s" % (type(real_val), str_val))


def ensureTuple(v):
    """
    Recursively convert an iterable object into a tuple
    v (iterable or object): If it is an iterable, it will be converted into a tuple, and
      otherwise it will be returned as is
    return (tuple or object): same a v, but a tuple if v was iterable
    """
    if isinstance(v, collections.Iterable):
        # convert to a tuple, with each object contained also converted
        return tuple(ensureTuple(i) for i in v)
    else:
        return v
