# -*- coding: utf-8 -*-

"""
Created on 28 Feb 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

# Various function to handle spectral data
# Note that the spectrum is normally contained on the C dimension, which is
# by convention the first of the 5 dimensions of a DataArray (CTZYX).

from __future__ import division

import logging
from numpy.polynomial import polynomial
from odemis import model


def get_wavelength_per_pixel(da):
    """
    Computes the wavelength for each pixel along the C dimension

    :param da: (model.DataArray of shape C...): the DataArray with metadata
        either MD_WL_POLYNOMIAL or MD_WL_LIST
    :return: (list of float of length C): the wavelength (in m) for each pixel
        in C
    :raises:
        AttributeError: if no metadata is present
        KeyError: if no metadata is available
        ValueError: if the metadata doesn't provide enough information
    """

    if not hasattr(da, 'metadata'):
        raise AttributeError("No metadata found in data array")

    # MD_WL_LIST has priority
    if model.MD_WL_LIST in da.metadata:
        wl = da.metadata[model.MD_WL_LIST]
        if len(wl) == da.shape[0]:
            return wl
        else:
            raise ValueError("Wavelength metadata (MD_WL_LIST) is not the same "
                             "length as the data")

    if model.MD_WL_POLYNOMIAL in da.metadata:
        pn = da.metadata[model.MD_WL_POLYNOMIAL]
        pn = polynomial.polytrim(pn)
        if len(pn) >= 2:
            npn = polynomial.Polynomial(pn,  #pylint: disable=E1101
                                        domain=[0, da.shape[0] - 1],
                                        window=[0, da.shape[0] - 1])
            ret = npn.linspace(da.shape[0])[1]
            return ret.tolist()
        else:
            # a polynomial of 0 or 1 value is useless
            raise ValueError("Wavelength polynomial has only %d degree"
                             % len(pn))

    raise KeyError("No MD_WL_* metadata available")


def get_spectrum_range(data):
    """ Return the wavelength for each pixel of a (complete) spectrum

    :param data: (model.DataArray of shape C...): the DataArray with metadata
        either MD_WL_POLYNOMIAL or MD_WL_LIST

    :return: (list of numbers or None): one wavelength per spectrum pixel.
      Values are in meters, unless the spectrum cannot be determined, in
      which case integers representing pixels index is returned.
      If no data is available, None is returned.
            (str): unit of spectrum range
    """

    try:
        return get_wavelength_per_pixel(data), "m"
    except (ValueError, KeyError):
        # useless polynomial => just show pixels values (ex: -50 -> +50 px)
        max_bw = data.shape[0] // 2
        min_bw = (max_bw - data.shape[0]) + 1
        return range(min_bw, max_bw + 1), "px"


def get_time_per_pixel(da):
    """
    Computes the time list for each pixel along the T dimension

    :param da: (model.DataArray of shape C...): the DataArray with metadata
        MD_TIME_LIST
    :return: (list of float of length T): the time (in s) for each pixel
        in T
    :raises:
        AttributeError: if no metadata is present
        KeyError: if no metadata is available
        ValueError: if the metadata doesn't provide enough information
    """

    if not hasattr(da, 'metadata'):
        raise AttributeError("No metadata found in data array")

    # MD_TIME_LIST has priority
    if model.MD_TIME_LIST in da.metadata:
        tl = da.metadata[model.MD_TIME_LIST]
        if len(tl) == da.shape[0]:
            return tl
        else:
            raise ValueError("Time list metadata (MD_TIME_LIST) is not the same "
                             "length as the data")

    raise KeyError("No MD_TIME_LIST metadata available")


def get_time_range(data):
    """ Return the time range

    :param data: (model.DataArray of shape C...): the DataArray with metadata
        MD_TIME_LIST

    :return: (list of numbers or None):
    """

    try:
        return get_time_per_pixel(data), "s"
    except (ValueError, KeyError):
        # useless polynomial => just show pixels values (ex: -50 -> +50 px)
        max_t = data.shape[0] // 2
        min_t = (max_t - data.shape[0]) + 1
        return range(min_t, max_t + 1), "px"


def coefficients_to_dataarray(coef):
    """
    Convert a spectrum efficiency coefficient array to a DataArray as expected
    by odemis.acq.calibration
    coef (numpy array of shape (N,2)): first column is the wavelength in nm, second
      column is the coefficient (float > 0)
    returns (DataArray of shape (N,1,1,1,1)): the same data with the wavelength
     encoded in the metadata, as understood by the rest of Odemis.
    """
    # Create the content of the DataArray directly from the second column
    da = model.DataArray(coef[:, 1])
    da.shape += (1, 1, 1, 1) # add another 4 dims

    # metadata from the first column, converting from nm to m
    wl_list = (1e-9 * coef[:, 0]).tolist()
    da.metadata[model.MD_WL_LIST] = wl_list

    return da
