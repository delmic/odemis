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
import logging

# Inspired by code from:
# http://codingmess.blogspot.nl/2009/05/conversion-of-wavelength-in-nanometers.html
# based on:
# http://www.physics.sfasu.edu/astro/color/spectra.html
def wave2rgb(wavelength):
    """
    Convert a wavelength into a (r,g,b) value
    wavelength (0<float): wavelength in m
    return (3-tupe int in 0..255): RGB value
    """

    w = wavelength * 1e9
    # outside of the visible spectrum, use fixed colour
    if w < 350: # ultraviolet
        w = 350
    elif w > 780: # infrared
        w = 780

    # colour
    if w >= 350 and w < 440:
        r = -(w - 440) / (440 - 350)
        g = 0.0
        b = 1.0
    elif w >= 440 and w < 490:
        r = 0.0
        g = (w - 440) / (490 - 440)
        b = 1.0
    elif w >= 490 and w < 510:
        r = 0.0
        g = 1.0
        b = -(w - 510) / (510 - 490)
    elif w >= 510 and w < 580:
        r = (w - 510) / (580 - 510)
        g = 1.0
        b = 0.0
    elif w >= 580 and w < 645:
        r = 1.0
        g = -(w - 645) / (645 - 580)
        b = 0.0
    elif w >= 645 and w <= 780:
        r = 1.0
        g = 0.0
        b = 0.0
    else:
        logging.warning("Unable to compute RGB for wavelength %d", w)

    return int(255*r), int(255*g), int(255*b)

def hex_to_rgb(hex_str):
    """
    Convert a Hexadecimal color representation into an 3-tuple of floats
    return (tuple of 3 (0<float<1): R, G, and B
    """
    hex_str = hex_str[-6:]
    return tuple(int(hex_str[i:i+2], 16) / 255 for i in [0, 2, 4])

def hex_to_rgba(hex_str, af=1.0):
    """ Convert a Hexadecimal color representation into an 4-tuple of floats """
    return hex_to_rgb(hex_str) + (af,)

def wxcol_to_rgb(wxcol):
    return (wxcol.Red() / 255.0, wxcol.Green() / 255.0, wxcol.Blue() / 255.0)

def change_brightness(col_tup, step):
    col_list = []
    f, lim = (min, 1.0) if step > 0 else (max, 0.0)

    for c in col_tup[:3]:
        col_list.append(f(c + step, lim))

    return tuple(col_list + list(col_tup[3:]))

def formats_to_wildcards(formats2ext, include_all=False, include_any=False):
    """Convert formats into wildcards string compatible with wx.FileDialog()

    formats2ext (dict (string -> list of strings)): format names and lists of
        their possible extensions.
    include_all (boolean): If True, also include as first wildcards for all the formats 
    include_any (boolean): If True, also include as last the *.* wildcards 

    returns (tuple (string, list of strings)): wildcards, name of the format
        in the same order as in the wildcards (or None if all/any format)
    """
    formats = []
    wildcards = []
    for fmt, extensions in formats2ext.items():
        ext_wildcards = ";".join(["*" + e for e in extensions])
        wildcard = "%s files (%s)|%s" % (fmt, ext_wildcards, ext_wildcards)
        formats.append(fmt)
        wildcards.append(wildcard)

    if include_all:
        fmt_wildcards = []
        for extensions in formats2ext.values():
            fmt_wildcards.append(";".join(["*" + e for e in extensions]))
        ext_wildcards = ";".join(fmt_wildcards)
        wildcard = "All supported files (%s)|%s" % (ext_wildcards, ext_wildcards)
        wildcards.insert(0, wildcard)
        formats.insert(0, None)

    if include_any:
        wildcards.append("Any file (*.*)|*.*")
        formats.append(None)

    # the whole importance is that they are in the same order
    return "|".join(wildcards), formats
