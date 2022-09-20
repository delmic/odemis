# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2017 Rinze de Laat, Éric Piel, Delmic

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

from past.builtins import basestring
from odemis.util.conversion import hex_to_rgb, frgb_to_rgb, hex_to_frgb, \
    rgb_to_frgb
import wx


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
        if all(isinstance(v, float) for v in colour):
            _col = colour[:3]
            _alpha = colour[-1] if len(colour) == 4 else None
        elif all(isinstance(v, int) for v in colour):
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
