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

from __future__ import annotations
from collections.abc import Iterable
from typing import Sequence, Tuple, Union

from odemis.model import BAND_PASS_THROUGH

# A band is either the min/max (2 floats) or the -99%, -25%, middle, +25%, +99% (5 floats)
# of the band in m, or BAND_PASS_THROUGH (a string).
Band = Union[str, Sequence[float]]
# One band, or several bands (multi-band filter)
MultiBand = Union[Band, Sequence[Sequence[float]]]

# constants to indicate how well a emission/excitation setting fits a dye
# emission/excitation (peak)
FIT_GOOD = 2 # Should be fine
FIT_BAD = 1 # Might work, but not at its best
FIT_IMPOSSIBLE = 0 # Unlikely to work


def get_center(band: MultiBand) -> Union[float, Tuple]:
    """
    Return the center wavelength(s) of an emission/excitation band.
    :param band: either the min/max of the band or the -99%, -25%, middle, +25%,
        +99% of the band in m, or a list of such bands, or BAND_PASS_THROUGH.
    :returns: wavelength in m, or tuple of wavelengths (one for each band) if
        multiple bands were given.
    :raises TypeError: if the band is a string other than BAND_PASS_THROUGH.
    """
    if band == BAND_PASS_THROUGH:
        return 5000e-9  # Something large, so that if it's sorted, it's after every other band

    if isinstance(band, str):
        raise TypeError(f"Band must be a list or a tuple, got '{band}'")

    if isinstance(next(iter(band)), Iterable):
        return tuple(get_center(b) for b in band)

    if len(band) % 2 == 0:
        center = sum(band) / len(band) # works well at least with 2 values
    else:
        center = band[len(band) // 2]
    return center


def get_one_band_em(bands: MultiBand, ex_band: MultiBand) -> Band:
    """
    Return the band given or if it's a multi-band, return just the most likely
    one based on the current excitation band.
    :param bands: emission band(s)
    :param ex_band: excitation band(s)
    :returns: the emission band (2 or 5 floats), or BAND_PASS_THROUGH
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


def get_one_center_em(bands: MultiBand, ex_band: MultiBand) -> float:
    """
    Return the center of an emission band, and if it's a multi-band, return just
    one of the centers based on the current excitation band.
    :param bands: emission band(s)
    :param ex_band: excitation band(s)
    :returns: wavelength in m
    """
    return get_center(get_one_band_em(bands, ex_band))


def get_one_band_ex(bands: MultiBand, em_band: MultiBand) -> Band:
    """
    Return the excitation band, and if it's a multi-band, return the band
    fitting best the current emission band: the first excitation wavelength
    below the emission.
    :param bands: excitation band(s)
    :param em_band: emission band(s)
    :returns: the excitation band (2 or 5 floats), or BAND_PASS_THROUGH
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


def get_one_center_ex(bands: MultiBand, em_band: MultiBand) -> float:
    """
    Return the center of an excitation band, and if it's a multi-band, return
    just one of the centers based on the current emission band.
    :param bands: excitation band(s)
    :param em_band: emission band(s)
    :returns: wavelength in m
    """
    return get_center(get_one_band_ex(bands, em_band))


def get_one_center(band: MultiBand) -> float:
    """
    Return the center of a band, and if it's a multi-band, return just one of the centers.
    If possible use get_one_center_ex() or get_one_center_em() to get more
    likely value.
    :param band: band(s) to get the center from
    :returns: wavelength in m
    """
    if isinstance(band[0], Iterable) and band != BAND_PASS_THROUGH:
        return get_center(band[0])
    else:
        return get_center(band)


def estimate_fit_to_dye(wl: float, band: MultiBand) -> int:
    """
    Estimate how well the light settings of the hardware fit for a given dye
    emission or excitation wavelength.
    :param wl: the wavelength of peak of the dye, in m
    :param band: either the min/max of the band or the -99%, -25%, middle,
        +25%, +99% of the band in m, or a list of such bands, or BAND_PASS_THROUGH.
    :returns: how well it fits, as one of FIT_GOOD, FIT_BAD, or FIT_IMPOSSIBLE
        (the higher the better)
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


def quantify_fit_to_dye(wl: float, band: MultiBand) -> float:
    """
    Quantifies how well the given wavelength matches the given
    band: the better the match, the higher the return value will be.
    :param wl: the wavelength of peak of the dye, in m
    :param band: either the min/max of the band or the -99%, -25%, middle,
        +25%, +99% of the band in m, or a list of such bands, or BAND_PASS_THROUGH.
    :returns: a score >= 0. The higher, the better the fit.
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


def find_best_band_for_dye(wl: float, bands: Iterable[MultiBand]) -> MultiBand:
    """
    Pick the best band for the given dye emission or excitation.
    :param wl: the wavelength of peak of the dye, in m
    :param bands: set of bands, each being either the min/max of the band or the
        -99%, -25%, middle, +25%, +99% of the band in m, or a list of such bands,
        or BAND_PASS_THROUGH.
    :returns: the best fitting band
    """
    # The most fitting band: narrowest band centered around the wavelength
    return max((b for b in bands), key=lambda x: quantify_fit_to_dye(wl, x))


def to_readable_band(band: MultiBand) -> str:
    """
    Convert an emission or excitation band into readable text.
    :param band: either the min/max of the band or the -99%, -25%, middle,
        +25%, +99% of the band in m, or a list of such bands. It can also be a
        string in which case it will be returned as-is.
    :returns: human readable string
    """
    # if string: => return as is
    # if one band => center/bandwidth nm (bandwidth not displayed if < 5nm)
    #   ex: 453/19 nm
    # if multi-band => center, center... nm
    #   ex: 453, 568, 968 nm
    if isinstance(band, str):
        return band
    if not isinstance(band[0], Iterable):
        b = band
        center_nm = int(round(get_center(b) * 1e9))

        width = b[-1] - b[0]
        if width > 5e-9:
            width_nm = int(round(width * 1e9))
            return "%d/%d nm" % (center_nm, width_nm)
        else:
            return "%d nm" % center_nm
    else:  # multi-band
        centers = []
        for c in get_center(band):
            center_nm = int(round(c * 1e9))
            centers.append("%d" % center_nm)
        return ", ".join(centers) + " nm"
