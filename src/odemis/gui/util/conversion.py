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
    """
    Convert a Hexadecimal color representation into an 3-tuple of floats
    return (tuple of 3 (0<float<1): R, G, and B
    """
    hex_str = hex_str[-6:]
    return tuple(int(hex_str[i:i + 2], 16) / 255 for i in [0, 2, 4])

def hex_to_rgba(hex_str, af=1.0):
    """ Convert a Hexadecimal color representation into an 4-tuple of floats """
    return hex_to_rgb(hex_str) + (af,)

def wxcol_to_rgb(wxcol):
    return (wxcol.Red() / 255, wxcol.Green() / 255, wxcol.Blue() / 255)

def change_brightness(colf, weight):
    """
    Brighten (or darken) a given colour
    See also wx.lib.agw.aui.aui_utilities.StepColour()
    colf (tuple of 3+ 0<float<1): RGB colour (and alpha)
    weight (-1<float<1): how much to brighten (>0) or darken (<0) 
    return (tuple of 3+ 0<float<1): new RGB colour
    """
    if weight > 0:
        # blend towards white
        f, lim = min, 1.0
    else:
        # blend towards black
        f, lim = max, 0.0
        weight = -weight

    new_col = tuple(f(c * (1 - weight) + lim * weight, lim) for c in colf[:3])

    return new_col + colf[3:]

