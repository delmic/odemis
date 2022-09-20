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

from collections.abc import Iterable
from odemis.model import BAND_PASS_THROUGH
from past.builtins import basestring  # For Python 2 & 3

# constants to indicate how well a emission/excitation setting fits a dye
# emission/excitation (peak)
FIT_GOOD = 2 # Should be fine
FIT_BAD = 1 # Might work, but not at its best
FIT_IMPOSSIBLE = 0 # Unlikely to work


def get_center(band):
    """
    Return the center wavelength(es) of a emission/excitation band
    band ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return ((tuple of) float): wavelength in m or list of wavelength for each band
    """
    if band == BAND_PASS_THROUGH:
        return 5000e-9  # Something large, so that if it's sorted, it's after every other band

    if isinstance(band, basestring):
        raise TypeError("Band must be a list or a tuple")

    if isinstance(next(iter(band)), Iterable):
        return tuple(get_center(b) for b in band)

    if len(band) % 2 == 0:
        center = sum(band) / len(band) # works well at least with 2 values
    else:
        center = band[len(band) // 2]
    return center


def get_one_band_em(bands, ex_band):
    """
    Return the band given or if it's a multi-band, return just the most likely
    one based on the current excitation band
    bands ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): emission band(s)
    ex_band ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): excitation band(s)
    return (tuple of 2 or 5 floats, or BAND_PASS_THROUGH): emission band
    """
    if bands == BAND_PASS_THROUGH:
        return bands

    if not isinstance(next(iter(bands)), Iterable):
        return bands

    # Need to guess: the closest above the excitation wavelength
    if ex_band == BAND_PASS_THROUGH:
        ex_center = 1e9  # Nothing will match
    elif isinstance(next(iter(ex_band)), Iterable):
        # It's getting tricky, but at least above the smallest one
        ex_center = min(get_center(ex_band))
    else:
        ex_center = get_center(ex_band)

    # Force each band as a tuple to make sure the key is hashable
    em_b2c = {tuple(b): get_center(b) for b in bands}
    bands_above = [b for b, c in em_b2c.items() if c > ex_center]
    if bands_above:
        em_band = min(bands_above, key=em_b2c.get)
    else:
        # excitation and emission don't seem to match, so fallback to the
        # less crazy value
        em_band = max(bands, key=em_b2c.get)

    return em_band


def get_one_center_em(bands, ex_band):
    """
    Return the center of an emission band, and if it's a multi-band, return just
    one of the centers based on the current excitation band
    bands ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): emission band(s)
    ex_band ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): excitation band(s)
    return (float): wavelength in m
    """
    return get_center(get_one_band_em(bands, ex_band))


def get_one_band_ex(bands, em_band):
    """
    Return the excitation band, and if it's a multi-band, return the band
      fitting best the current emission band: the first excitation wavelength
      below the emission.
    bands ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): excitation band(s)
    em_band ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): emission band(s)
    return (float): wavelength in m
    """
    if bands == BAND_PASS_THROUGH:
        return bands

    # FIXME: make it compatible with sets instead of list
    if not isinstance(next(iter(bands)), Iterable):
        return bands

    # Need to guess: the closest below the emission wavelength
    if em_band == BAND_PASS_THROUGH:
        em_center = 0  # Nothing will match
    elif isinstance(next(iter(em_band)), Iterable):
        # It's getting tricky, but at least below the biggest one
        em_center = max(get_center(em_band))
    else:
        em_center = get_center(em_band)

    # Force each band as a tuple to make sure the key is hashable
    ex_b2c = {tuple(b): get_center(b) for b in bands}
    # ex_centers = get_center(bands)
    ex_bands_below = [b for b, c in ex_b2c.items() if c < em_center]
    if ex_bands_below:
        ex_band = max(ex_bands_below, key=ex_b2c.get)
    else:
        # excitation and emission don't seem to match, so fallback to the
        # less crazy value
        ex_band = min(bands, key=ex_b2c.get)

    return ex_band


def get_one_center_ex(bands, em_band):
    """
    Return the center of an excitation band, and if it's a multi-band, return
    just one of the centers based on the current emission band
    bands ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): excitation band(s)
    em_band ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): emission band(s)
    return (float): wavelength in m
    """
    return get_center(get_one_band_ex(bands, em_band))


def get_one_center(band):
    """
    Return the center of a band, and if it's a multi-band, return just one of the centers.
    If possible use get_one_center_ex() or get_one_center_em() to get more
    likely value.

    :return: (float) wavelength in m
    """
    if isinstance(band[0], Iterable) and band != BAND_PASS_THROUGH:
        return get_center(band[0])
    else:
        return get_center(band)


def estimate_fit_to_dye(wl, band):
    """
    Estimate how well the light settings of the hardware fit for a given dye
    emission or excitation wavelength.
    wl (float): the wavelength of peak of the dye
    band ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return (FIT_*): how well it fits (the higher the better)
    """
    if band == BAND_PASS_THROUGH:
        return FIT_IMPOSSIBLE
    # TODO: support multiple-peak/band/curve for the dye

    # if multi-band: get the best of all
    if isinstance(band[0], Iterable):
        return max(estimate_fit_to_dye(wl, b) for b in band)

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
    band ((list of) tuple of 2 or 5 floats, or BAND_PASS_THROUGH): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return (0<float): the more, the merrier
    """
    if band == BAND_PASS_THROUGH:
        return 0  # Pass-through is never good for fluorescence microscopy

    # if multi-band: get the best of all
    if isinstance(band[0], Iterable):
        return max(quantify_fit_to_dye(wl, b) for b in band)

    center = get_center(band)
    width = band[-1] - band[0]
    distance = abs(wl - center)
    dist_border = min(abs(wl - band[0]), abs(wl - band[-1]))

    if band[0] < wl < band[-1]:
        # ensure it cannot get infinite score for being in the center
        return 1 / (max(distance, 1e-9) * max(width, 1e-9))
    elif dist_border < 20e-9:
        # almost? => 100x less good
        return 0.01 / (max(distance, 1e-9) * max(width, 1e-9))
    else:
        # Not matching => at least report some small number so that if no band
        # matches, the closest one is selected
        return 0.0001 / max(dist_border, 1e-9)


def find_best_band_for_dye(wl, bands):
    """
    Pick the best band for the given dye emission or excitation.
    wl (float): the wavelength of peak of the dye
    bands ({(list of) tuple of 2 or 5 floats or BAND_PASS_THROUGH}): set of either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return ((list of) tuple of 2 or 5 floats): the best fitting bands
    """
    # The most fitting band: narrowest band centered around the wavelength
    return max((b for b in bands), key=lambda x: quantify_fit_to_dye(wl, x))


def to_readable_band(band):
    """ Convert a emission or excitation band into readable text

    :param band: (str or (list of) tuple of 2 or 5 floats): either the min/max of the band or the
        -99%, -25%, middle, +25%, +99% of the band in m. It can also be a string
        in which case it will be returned as-is
    :return: (unicode) human readable string

    """
    # if string: => return as is
    # if one band => center/bandwidth nm (bandwidth not displayed if < 5nm)
    #   ex: 453/19 nm
    # if multi-band => center, center... nm
    #   ex: 453, 568, 968 nm
    if isinstance(band, basestring):
        return band
    if not isinstance(band[0], Iterable):
        b = band
        center_nm = int(round(get_center(b) * 1e9))

        width = b[-1] - b[0]
        if width > 5e-9:
            width_nm = int(round(width * 1e9))
            return u"%d/%d nm" % (center_nm, width_nm)
        else:
            return u"%d nm" % center_nm
    else:  # multi-band
        centers = []
        for c in get_center(band):
            center_nm = int(round(c * 1e9))
            centers.append(u"%d" % center_nm)
        return u", ".join(centers) + " nm"

