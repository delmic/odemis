#-*- coding: utf-8 -*-
'''
@author: Rinze de Laat 

Copyright © 2012 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

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
        r = -(w - 440.0) / (440.0 - 350.0)
        g = 0.0
        b = 1.0
    elif w >= 440 and w < 490:
        r = 0.0
        g = (w - 440.0) / (490.0 - 440.0)
        b = 1.0
    elif w >= 490 and w < 510:
        r = 0.0
        g = 1.0
        b = -(w - 510.0) / (510.0 - 490.0)
    elif w >= 510 and w < 580:
        r = (w - 510.0) / (580.0 - 510.0)
        g = 1.0
        b = 0.0
    elif w >= 580 and w < 645:
        r = 1.0
        g = -(w - 645.0) / (645.0 - 580.0)
        b = 0.0
    elif w >= 645 and w <= 780:
        r = 1.0
        g = 0.0
        b = 0.0
    else:
        logging.warning("Unable to compute RGB for wavelength %d", w)

    return int(255*r), int(255*g), int(255*b)

