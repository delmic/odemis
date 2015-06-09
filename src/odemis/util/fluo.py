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


def get_center(band):
    """
    Return the center wavelength(es) of a emission/excitation band
    band ((list of) tuple of 2 or 5 floats): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return ((tuple of) float): wavelength in m or list of wavelength for each band
    """
    if isinstance(band, basestring):
        raise TypeError("Band must be a list or a tuple")

    if isinstance(band[0], collections.Iterable):
        return tuple(get_center(b) for b in band)

    if len(band) % 2 == 0:
        center = sum(band) / len(band) # works well at least with 2 values
    else:
        center = band[len(band) // 2]
    return center


def get_one_band_em(band, ex_band):
    """
    Return the band given or if it's a multi-band, return just the most likely
    one based on the current excitation band
    band ((list of) tuple of 2 or 5 floats): emission band(s)
    ex_band ((list of) tuple of 2 or 5 floats): excitation band(s)
    return (tuple of 2 or 5 floats): emission band
    """
    if isinstance(band[0], collections.Iterable):
        # Need to guess: the closest above the excitation wavelength
        if isinstance(ex_band[0], collections.Iterable):
            # It's getting tricky, but at least above the smallest one
            ex_center = min(get_center(ex_band))
        else:
            ex_center = get_center(ex_band)

        em_b2c = dict((b, get_center(b)) for b in band)
        bands_above = [b for b, c in em_b2c.items() if c > ex_center]
        if bands_above:
            em_band = min(bands_above, key=em_b2c.get)
        else:
            # excitation and emission don't seem to match, so fallback to the
            # less crazy value
            em_band = max(band, key=em_b2c.get)

        return em_band
    else:
        return band


def get_one_center_em(band, ex_band):
    """
    Return the center of an emission band, and if it's a multi-band, return just
    one of the centers based on the current excitation band
    band ((list of) tuple of 2 or 5 floats): emission band(s)
    ex_band ((list of) tuple of 2 or 5 floats): excitation band(s)
    return (float): wavelength in m
    """
    return get_center(get_one_band_em(band, ex_band))


def get_one_center_ex(band, em_band):
    """
    Return the center of an excitation band, and if it's a multi-band, return
    just one of the centers based on the current emission band
    band ((list of) tuple of 2 or 5 floats): excitation band(s)
    em_band ((list of) tuple of 2 or 5 floats): emission band(s)
    return (float): wavelength in m
    """
    if isinstance(band[0], collections.Iterable):
        # Need to guess: the closest below the emission wavelength
        if isinstance(em_band[0], collections.Iterable):
            # It's getting tricky, but at least below the biggest one
            em_center = max(get_center(em_band))
        else:
            em_center = get_center(em_band)

        ex_centers = get_center(band)
        ex_centers_below = [c for c in ex_centers if c < em_center]
        if ex_centers_below:
            ex_center = max(ex_centers_below)
        else:
            # excitation and emission don't seem to match, so fallback to the
            # less crazy value
            ex_center = min(ex_centers)

        return ex_center
    else:
        return get_center(band)


def get_one_center(band):
    """
    Return the center of a band, and if it's a multi-band, return just one of the centers.
    If possible use get_one_center_ex() or get_one_center_em() to get more
    likely value.

    :return: (float) wavelength in m
    """

    if isinstance(band[0], collections.Iterable):
        return get_center(band[0])
    else:
        return get_center(band)


def estimate_fit_to_dye(wl, band):
    """
    Estimate how well the light settings of the hardware fit for a given dye
    emission or excitation wavelength.
    wl (float): the wavelength of peak of the dye
    band ((list of) tuple of 2 or 5 floats): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return (FIT_*): how well it fits (the higher the better)
    """
    # TODO: support multiple-peak/band/curve for the dye

    # if multi-band: get the best of all
    if isinstance(band[0], collections.Iterable):
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
    band ((list of) tuple of 2 or 5 floats): either the min/max
      of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
    return (0<float): the more, the merrier
    """
    # if multi-band: get the best of all
    if isinstance(band[0], collections.Iterable):
        return max(quantify_fit_to_dye(wl, b) for b in band)

    center = get_center(band)
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


def to_readable_band(band):
    """ Convert a emission or excitation band into readable text

    :param band: ((list of) tuple of 2 or 5 floats): either the min/max of the band or the
        -99%, -25%, middle, +25%, +99% of the band in m.
    :return: (unicode) human readable string

    """

    # if one band => center/bandwidth nm (bandwidth not displayed if < 5nm)
    #   ex: 453/19 nm
    # if multi-band => center, center... nm
    #   ex: 453, 568, 968 nm
    if not isinstance(band[0], collections.Iterable):
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

