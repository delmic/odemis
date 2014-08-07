# -*- coding: utf-8 -*-
'''
Created on 6 Aug 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# various functions to help with computations related to fluorescence microscopy

from __future__ import division

import collections

# constants to indicate how well a emission/excitation setting fits a dye
# emission/excitation (peak)
FIT_GOOD = 2 # Should be fine
FIT_BAD = 1 # Might work, but not at its best
FIT_IMPOSSIBLE = 0 # Unlikely to work

def estimate_fit_to_dye(wl, band):
    """
    Estimate how well the light settings of the hardware fit for a given dye
    emission or excitation wavelength.
    wl (float): the wavelength of peak of the dye
    band ((list of) tuple of 2 or 5 floats): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return (FIT_*): how well it fits (the higher the better)
    """
    # TODO: support (multiple) peak/band/curve for the dye

    # if multi-band: get the best of all
    if isinstance(band[0], collections.Iterable):
        return max(estimate_fit_to_dye(b, wl) for b in band)

    if band[0] < wl < band[-1]: # within the hardware range
        return FIT_GOOD
    elif band[0] - 20e-9 < wl < band[-1] + 20e-9: # give ± 20 nm to the peak
        return FIT_BAD
    else: # unlikely to fit
        return FIT_IMPOSSIBLE

def quantify_fit_to_dye(wl, band):
    """
    Quantifies how well the given wavelength matches the given
      band: the better the match, the higher the return value will be.
    wl (float): the wavelength of peak of the dye
    band ((list of) tuple of 2 or 5 floats): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return (0<float): the more, the merrier
    """
    # if multi-band: get the best of all
    if isinstance(band[0], collections.Iterable):
        return max(quantify_fit_to_dye(wl, b) for b in band)

    if len(band) % 2:
        center = sum(band) / len(band) # works well at least with 2 values
    else:
        center = band[len(band) // 2]
    width = band[-1] - band[0]
    distance = abs(wl - center)

    if band[0] < wl < band[-1]:
        # ensure it cannot get infinite score for being in the center
        return 1 / (max(distance, 1e-9) * max(width, 1e-9))
    elif band[0] - 20e-9 < wl < band[-1] + 20e-9:
        # almost? => 100x less good
        return 0.01 / (max(distance, 1e-9) * max(width, 1e-9))
    else:
        # No match
        return 0

def find_best_band_for_dye(wl, bands):
    """
    Pick the best band for the given dye emission or excitation.
    wl (float): the wavelength of peak of the dye
    bands (set of (list of) tuple of 2 or 5 floats): set of either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return ((list of) tuple of 2 or 5 floats): the best fitting bands
    """
    # The most fitting band: narrowest band centered around the wavelength
    return max([b for b in bands], key=lambda x: quantify_fit_to_dye(wl, x))
