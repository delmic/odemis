# -*- coding: utf-8 -*-
'''
Created on 27 Feb 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Various helper functions for handling calibration

from __future__ import division

import logging
import numpy
from odemis import model
from odemis.util import spectrum


# AR calibration data is a background image. The file format expected is a
# file with just one acquisition: the AR acquisition. However, it is fine if
# it contains also the survey and SEM data (and convenient as that's how
# Odemis saves it directly after an acquisition). If there is more than one AR
# image we try to pick the right one (eg, first acquired), but absolutely no
# correctness can be ensured.
def get_ar_data(das):
    """
    Finds the DataArray that contains the Angular Resolved data for calibration
    (typically, a "background" image, ie, an image taken without ebeam).
    das (list of DataArrays): all the DA into which to look for.
    return (DataArray): the first DA that seems good.
    raise LookupError: if no DA can be found
    """
    # TODO: also allow to pass an expected resolution, in order to support
    # a calibration file with multiple calibrations resolution?

    # expect the worse: multiple AR data
    ar_data = []
    for da in das:
        # AR data is simple to distinguish: it has a AR_POLE metadata
        if model.MD_AR_POLE in da.metadata:
            ar_data.append(da)

    if not ar_data:
        raise LookupError("Failed to find any AR data within the %d data acquisitions" %
                          (len(das)))
    elif len(ar_data) == 1:
        return ar_data[0]
    else:
        # look for the first one (in terms of time), hoping that it's the one
        # the user expects to be representing the background
        logging.warning("AR calibration file contained %d AR data, "
                        "will pick the earliest acquired", len(das))
        inf = float("inf")
        earliest = ar_data[0]
        for da in ar_data[1:]:
            if (earliest.metadata.get(model.MD_ACQ_DATE, inf) >
                da.metadata.get(model.MD_ACQ_DATE, inf)):
                earliest = da

        return da

# One calibration for spectrum is the efficiency correction. That's to 
# compensate the difference in intensity loss depending on the wavelength in
# the optical path. The main source of loss is the CCD quantum efficiency.
# It is saved as in a file with one data of shape C>1 and TZYX = 1111 (similar
# to a spectrum). The values are (floats) representing the factor by which to
# multiply a spectrum to compensate for the intensity loss. The wavelength to
# which they correspond is indicated (like for a spectrum) in MD_WL_LIST.
def get_spectrum_efficiency(das):
    """
    Finds the DataArray that contains the spectrum efficiency compensation data
    (a "spectrum" which contains factors for each given wavelength).
    das (list of DataArrays): all the DA into which to look for.
    return (DataArray): the first DA that seems good.
    raise LookupError: if no DA can be found
    """
    # expect the worse: multiple DAs available
    specs = []
    for da in das:
        # What we are looking for is very specific: has MD_WL_* and has C > 1.
        # Actually, we even check for C > 3 (as a spectrum with less than 4 
        # points would be very weird).
        if ((model.MD_WL_LIST in da.metadata or model.MD_WL_POLYNOMIAL in da.metadata)
            and len(da.shape) == 5 and da.shape[-5] > 4 and da.shape[-4:] == (1,1,1,1)
            ):
            specs.append(da)

    if not specs:
        raise LookupError("Failed to find any spectrum efficiency correction "
                           "data within the %d data acquisitions" %
                           (len(das)))
    elif len(specs) == 1:
        ret = specs[0]
    else:
        logging.warning("Spectrum efficiency file contained %d spectrum data, "
                        "will pick one randomly.", len(das))
        ret = specs[0]

    # do a few more checks on the data: at least it should be float > 0
    if not ret.dtype.kind == "f":
        logging.warning("Spectrum efficiency correction data is not of type float, "
                        "but %s.", ret.dtype.name)
    if numpy.any(ret < 0):
        logging.warning("Spectrum efficiency correction data contains "
                        "non-positive values.")
    return ret


def compensate_spectrum_efficiency(data, calib):
    """
    Apply the efficiency compensation factors to the given data.
    If the wavelength of the calibration doesn't cover the whole data wavelength,
    the missing wavelength is filled by the same value as the border. Wavelength
    in-between points is linearly interpolated.
    data (DataArray of at least 5 dims): the original data. Need MD_WL_* metadata
    calib (DataArray of at least 5 dims): the calibration data, with TZXY = 1111
      Need MD_WL_* metadata.
    returns (DataArray): same shape as original data. Can have dtype=float
    """
    # Need to get the calibration data for each wavelength of the data
    wl_data = spectrum.get_wavelength_per_pixel(data)
    # We could be more clever if calib has a MD_WL_POLYNOMIAL, but it's very
    # unlikely the calibration is in this form anyway.
    wl_calib = spectrum.get_wavelength_per_pixel(calib)

    # Warn if the calibration is not enough for the data
    if wl_calib[0] > wl_data or wl_calib[-1] < wl_data[-1]:
        logging.warning("Spectrum efficiency compensation is only between "
                        "%g->%g nm, while the spectrum is between %g-> %g nm.",
                        wl_calib[0] * 1e9, wl_calib[-1] * 1e9,
                        wl_data[0] * 1e9, wl_data[-1] * 1e9)

    # Interpolate the calibration data for each wl_data
    calib_fitted = numpy.interp(wl_data, wl_calib, calib[:, 0, 0, 0, 0])
    calib_fitted.shape += (1, 1, 1, 1) # put TZYX dims

    # Compensate the data
    return data * calib_fitted # will keep metadata from data

