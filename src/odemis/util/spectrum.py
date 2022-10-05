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

from builtins import range
import logging
import math
import numpy
from odemis import model
from odemis.util import angleres
from odemis.model import MD_THETA_LIST


def get_wavelength_per_pixel(da):
    """
    Computes the wavelength for each pixel along the C dimension

    :param da: (model.DataArray of shape C...): the DataArray with metadata
        MD_WL_LIST
    :return: (list of float of length C): the wavelength (in m) for each pixel
        in C
    :raises:
        AttributeError: if no metadata is present
        KeyError: if no metadata is available
        ValueError: if the metadata doesn't provide enough information
    """

    if not hasattr(da, 'metadata'):
        raise AttributeError("No metadata found in data array")

    # check dimension of data
    dims = da.metadata.get(model.MD_DIMS, "CTZYX"[-da.ndim:])
    if len(dims) == 3 and dims == "YXC" and da.shape[2] in (3, 4):  # RGB?
        # This is a hack to handle RGB projections of CX (ie, line spectrum)
        # and CT (temporal spectrum) and CA (angular spectrum) data. In theory the MD_DIMS should be
        # XCR and TCR and ACR (where the R is about the RGB channels). However,
        # this is confusing, and the GUI would not know how to display it.
        ci = 1
    else:
        try:
            ci = dims.index("C")  # get index of dimension C
        except ValueError:
            raise ValueError("Dimension 'C' not in dimensions, so skip computing wavelength list.")

    # MD_WL_LIST has priority
    try:
        wl = da.metadata[model.MD_WL_LIST]

        if len(wl) != da.shape[ci]:
            raise ValueError("Length of wavelength list does not match length of wavelength data.")
        return wl
    except KeyError:
        raise KeyError("No MD_WL_LIST metadata available")


def get_spectrum_range(data):
    """ Return the wavelength for each pixel of a (complete) spectrum

    :param data: (model.DataArray of shape C...): the DataArray with metadata
        MD_WL_LIST

    :return: (list of numbers or None): one wavelength per spectrum pixel.
      Values are in meters, unless the spectrum cannot be determined, in
      which case integers representing pixels index is returned.
      If no data is available, None is returned.
            (str): unit of spectrum range
    """

    try:
        return get_wavelength_per_pixel(data), "m"
    except (ValueError, KeyError) as ex:
        dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim:])
        # useless polynomial => just show pixels values (ex: -50 -> +50 px)
        if len(dims) == 3 and dims == "YXC" and data.shape[2] in (3, 4):  # RGB?
            # This is a hack that works the same way as in get_wavelength_per_pixel
            ci = 1
        else:
            try:
                ci = dims.index("C")  # get index of dimension C
            except ValueError:
                raise ValueError("Dimension 'C' not in dimensions, so skip computing wavelength list.")

        logging.info("Couldn't get spectrum metadata: %s", ex)
        max_bw = data.shape[ci] // 2
        min_bw = (max_bw - data.shape[ci]) + 1
        return list(range(min_bw, max_bw + 1)), "px"


def get_time_per_pixel(da):
    """
    Computes the time list for each pixel along the T dimension

    :param da: (model.DataArray of shape T...): the DataArray with metadata
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
        # check available dimension of data
        dims = da.metadata.get(model.MD_DIMS, "CTZYX"[-da.ndim:])
        
        if len(dims) == 3 and dims == "YXC" and da.shape[2] in (3, 4):  # RGB?
            # This is a hack that works the same way as in get_wavelength_per_pixel
            # Typically, CT data is represented as TCR.
            ti = 0
        else:
            try:
                ti = dims.index("T")  # get index of dimension T
            except ValueError:
                raise ValueError("Dimension 'T' not in dimensions, so skip computing time list.")

        if len(tl) != da.shape[ti]:
            raise ValueError("Length of time list does not match length of time data.")
        return tl

    raise KeyError("No MD_TIME_LIST metadata available")


def get_time_range(data):
    """ Return the time range

    :param data: (model.DataArray of shape T...): the DataArray with metadata
        MD_TIME_LIST

    :return: (list of numbers or None), unit string:
    """

    try:
        return get_time_per_pixel(data), "s"
    except (ValueError, KeyError) as ex:
        dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim:])

        if len(dims) == 3 and dims == "YXC" and data.shape[2] in (3, 4):  # RGB?
            # This is a hack that works the same way as in get_wavelength_per_pixel
            ti = 0
        else:
            try:
                ti = dims.index("T")  # get index of dimension T
            except ValueError:
                raise ValueError("Dimension 'T' not in dimensions, so skip computing time list.")

        logging.info("Couldn't get time metadata: %s", ex)
        # no time list. just show pixels values (ex: 0 -> +50 px)
        max_t = data.shape[ti] // 2  # Typically, a TC array
        min_t = (max_t - data.shape[ti]) + 1
        return list(range(min_t, max_t + 1)), "px"


def get_angle_range(data):
    """ Returns the list of theta values.

    :param data: (model.DataArray of shape A...): the DataArray with metadata
        MD_THETA_LIST (or mirror information)

    :returns a list of numbers or None and the unit string. The unit is either "rad" if metadata exists,
    or "px" if metadata is missing.
    """

    try:
        return get_angle_per_pixel(data), "rad"
    except (ValueError, KeyError) as ex:
        # Special default dimension with A instead of T.
        # If the function was called, let's assume there are some chances
        # that it really has an angle somewhere so fallback to the 4th dimension
        # (which is the most common place for the angle to be stored).
        dims = data.metadata.get(model.MD_DIMS, "CAZYX"[-data.ndim:])

        if len(dims) == 3 and dims == "YXC" and data.shape[2] in (3, 4):  # RGB?
            # This is a hack that works the same way as in get_wavelength_per_pixel
            thetai = 0
        else:
            try:
                thetai = dims.index("A")  # get index of dimension A
            except ValueError:
                raise ValueError("Dimension 'A' not in dimensions, so skip computing angle list.")

        # Shows pixels values (ex: 0 -> +50 px), if theta list is not present
        logging.info("Couldn't get angle metadata: %s", ex)
        max_t = data.shape[thetai] // 2  # array of shape AC
        min_t = (max_t - data.shape[thetai]) + 1
        return list(range(min_t, max_t + 1)), "px"


def get_angle_per_pixel(da):
    """
    Computes the angle list for each pixel along the A dimension.

    :param da: (model.DataArray of shape ..A..): the DataArray with metadata MD_THETA_LIST
      or with MD_AR_MIRROR_TOP, MD_AR_MIRROR_BOTTOM, and MD_WL_LIST.
    :return: (list of float of length A): the theta values (in radians) for each pixel in A
    Note that the theta list consists of NaN values, that later are to be removed for display
    and the radians will be converted to degrees.

    :raises:
        AttributeError: if no metadata is present
        KeyError: if MD_THETA_LIST is not available or the mirror metadata.
        ValueError: if the metadata doesn't provide enough information
    """

    if not hasattr(da, 'metadata'):
        raise AttributeError("No metadata found in data array")

    if model.MD_THETA_LIST in da.metadata:
        thetal = da.metadata[model.MD_THETA_LIST]
    else:
        # Try to build the MD_THETA_LIST from the mirror information metadata
        try:
            thetal = angleres.ExtractThetaList(da)
        except ValueError:
            raise KeyError("No MD_THETA_LIST metadata available, nor enough mirror metadata to reconstruct it")

        # Update the data array with the information
        da.metadata[MD_THETA_LIST] = thetal

    # check available dimension of data
    dims = da.metadata.get(model.MD_DIMS, "CAZYX"[-da.ndim:])

    if len(dims) == 3 and dims == "YXC" and da.shape[2] in (3, 4):  # RGB data
        thetai = 0
    else:
        try:
            thetai = dims.index("A")  # get index of dimension A
        except ValueError:
            raise ValueError("Dimension 'A' not in dimensions, so skip computing theta list.")

    if len(thetal) != da.shape[thetai]:
        raise ValueError("Length of theta list does not match length of theta data.")

    return thetal


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
