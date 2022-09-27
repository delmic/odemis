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

import csv
import logging
import math
from odemis import model
from odemis.model import MD_THETA_LIST
from odemis.util import spectrum, img, find_closest, almost_equal
from typing import Tuple

import numpy


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

    :param das: (list of DataArrays): all the DA into which to look for.
    :return: (DataArray): if polarimetry, return list of bg DAs, else the first DA that seems good.
    :raises: LookupError: if no DA can be found
    """
    # TODO: also allow to pass an expected resolution, in order to support
    # a calibration file with multiple calibrations resolution?

    # expect the worst: multiple AR data (per polarization mode)
    ar_data = {}  # pol-mode -> list of DAs
    for da in das:
        # AR data is simple to distinguish: it has a AR_POLE metadata
        if model.MD_AR_POLE in da.metadata:
            polmode = da.metadata.get(model.MD_POL_MODE)
            ar_data.setdefault(polmode, []).append(da)

    if not ar_data:
        raise LookupError("Failed to find any AR data within the %d data acquisitions" %
                          (len(das)))
    # For a polarization mode, if there is more than one data, average them.
    # They all should be very similar, so this just reduces the noise.
    # Note: it's typical, as often, after an AR acquisition the user re-acquires
    # the same area with the same settings excepted the e-beam is blanked. So we
    # end-up with MxN AR background data.
    for polmode, ldas in ar_data.items():
        if len(ldas) > 1:
            # Just a check that all AR is the same shape (and if not, just pick
            # the ones which are the same as the first found).
            ar_same_shape = [da for da in ldas if da.shape == ldas[0].shape]
            logging.warning("AR calibration file contained %d AR data, "
                            "will use the average of %d", len(ldas), len(ar_same_shape))
            ar_mean = numpy.mean(ar_same_shape, axis=0)
            ar_data[polmode] = [model.DataArray(ar_mean, ar_same_shape[0].metadata)]

    # Merge all the data together as a single
    ar_data = list(ldas[0] for ldas in ar_data.values())

    if len(ar_data) == 1:
        ar_data = ar_data[0]
    return ar_data


# Spectrum calibration data is a background image (to subtract from the raw data, C1111)
# and a list of coefficients (C1111) to compensate for the system response disparities.
# The file format expected is a file with just one acquisition: the spectrum acquisition.
# However, it is fine if it contains also the survey and SEM data (and convenient as that's
# how Odemis saves it directly after an acquisition). If there is more than one spectrum
# image we try to pick the right one (eg, first acquired), but absolutely no
# correctness can be ensured.
def get_spectrum_data(das):
    """
    Finds the DataArray that contains a spectrum data.
    (typically, a "background" image, ie, an image taken without ebeam).

    :param das: (list of DataArrays): all the DA into which to look for.
    :return: (DataArray of shape C1111): The first data array that seems good, if e.g. multiple
             streams were acquired. If only one bg stream, but multiple ebeam positions, returns the
             averaged image from all ebeam positions (reduces noise).
    :raises: LookupError: If no DA can be found.
    """
    # TODO: also allow to pass an expected resolution/wavelength polynomial, in
    # order to support a calibration file with multiple calibrations resolution?
    # TODO: also allow to pass a file with both a efficiency correction data and
    # various background data without mixing them up (maybe rely on the fact
    # it should have an acquisition date or position?)

    # expect the worse: multiple spectrum data
    specs = []
    for da in das:
        # What we are looking for is very specific: has MD_WL_LIST and has C > 1.
        # Actually, we even check for C > 3 (as a spectrum with less than 4
        # points would be very weird).
        if (model.MD_WL_LIST in da.metadata and len(da.shape) == 5
                and da.shape[-5] > 4 and da.shape[-4:] == (1, 1, 1, 1)):
            specs.append(da)

    if not specs:
        # be more flexible, and allow X/Y shape > 1, which permits to directly
        # use multiple acquisitions and average them to remove the noise
        for da in das:
            if model.MD_WL_LIST in da.metadata and len(da.shape) == 5 and da.shape[-5] > 4:
                # take the average for each wavelength (accumulated with a float64)
                dam = da.reshape((da.shape[0], -1)).mean(axis=1)  # TODO replace with da.mean(axis=(1, 2, 3, 4))?
                dam = dam.astype(da.dtype)  # put back into original dtype
                dam.shape += (1, 1, 1, 1)
                specs.append(dam)

    if not specs:
        raise LookupError("Failed to find any Spectrum data within the %d data acquisitions" %
                          (len(das)))
    elif len(specs) == 1:
        return specs[0]
    else:
        # look for the first one (in terms of time), hoping that it's the one
        # the user expects to be representing the background
        logging.warning("Spectrum file contained %d spectrum data, "
                        "will pick the earliest acquired", len(das))
        earliest = min(specs,
                       key=lambda d: d.metadata.get(model.MD_ACQ_DATE, float("inf")))
        return earliest

# Temporal spectrum calibration data is a background image (to subtract from the raw data, CT111)
# and a list of coefficients (C1111) to compensate for the system response disparities.
# The file format expected is a file with just one acquisition: the temporal spectrum acquisition.
# However, it is fine if it contains also the survey and SEM data (and convenient as that's
# how Odemis saves it directly after an acquisition). If there is more than one temporal spectrum
# image we try to pick the right one (eg, first acquired), but absolutely no
# correctness can be ensured.
def get_temporalspectrum_data(das):
    """
    Finds the DataArray that contains temporal spectrum data.
    (typically, a "background" image, ie, an image taken with a blanket ebeam).

    :param das: (list of DataArrays): All the data arrays into which to look for a bg image.
    :return: (DataArray of shape CT111): The first data array that seems good, if e.g. multiple
             streams were acquired. If only one bg stream, but multiple ebeam positions, returns the
             averaged image from all ebeam positions (reduces noise).
    :raises: LookupError: If no suitable background data array can be found.
    """

    # expect the worse: multiple temporal spectrum data (e.g. multiple streams acquired in one bg file)
    temporalspecs = []
    for da in das:
        # What we are looking for is very specific:
        # Need to have C >1 and T > 1.
        # Actually, we even check for C > 4 and T > 4
        # (as a spectrum and time info with less than 4 points would be very weird).
        # Note: We check for same shape, streak mode (MD_STREAK_MODE), time range (MD_STREAK_TIMERANGE),
        # wavelength (MD_WL_LIST) and time (MD_TIME_LIST) info etc. in bg setter.
        if len(da.shape) == 5 and da.shape[-5] > 4 and da.shape[-4] > 4 and da.shape[-3:] == (1, 1, 1):
            temporalspecs.append(da)

    if not temporalspecs:
        # Be more flexible, and allow X/Y shape > 1 (multiple ebeam positions), which permits to directly
        # use multiple acquisitions and average them to remove the noise.
        for da in das:
            if len(da.shape) == 5 and da.shape[-5] > 4 and da.shape[-4] > 4:
                # take the average image of all ebeam pos (accumulated with a float64)
                da_mean = da.mean(axis=(2, 3, 4))
                da_mean = da_mean.astype(da.dtype)  # put back into original dtype
                da_mean.shape += (1, 1, 1)
                temporalspecs.append(da_mean)

    if not temporalspecs:
        raise LookupError("Failed to find any temporal spectrum data within the %d data acquisitions" %
                          (len(das)))
    elif len(temporalspecs) == 1:
        return temporalspecs[0]
    else:
        # look for the first one (in terms of time), hoping that it's the one
        # the user expects to be representing the background
        logging.warning("Temporal spectrum file contained %d temporal spectrum data, "
                        "will pick the earliest acquired", len(das))
        earliest = min(temporalspecs,
                       key=lambda d: d.metadata.get(model.MD_ACQ_DATE, float("inf")))
        return earliest


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

    :param das: (list of DataArrays): all the DA into which to look for.
    :return: (DataArray of shape C1111): the first DA that seems good.
    :raises: LookupError: if no DA can be found
    """
    # expect the worse: multiple DAs available
    specs = []
    for da in das:
        # What we are looking for is very specific: has MD_WL_LIST and has C > 1.
        # Actually, we even check for C > 3 (as a spectrum with less than 4
        # points would be very weird).
        if (model.MD_WL_LIST in da.metadata and len(da.shape) == 5
                and da.shape[-5] > 4 and da.shape[-4:] == (1, 1, 1, 1)):
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

    # wavelength should be in meter, so detect wavelength recorded in nm
    if any(wl > 50e-6 for wl in ret.metadata[model.MD_WL_LIST]):
        raise ValueError("Spectrum efficiency correction data very large "
                         "wavelength, probably not in meters.")

    return ret


def apply_spectrum_corrections(data, bckg=None, coef=None):
    """
    Apply the background correction and the spectrum efficiency compensation
    factors to the given data if applicable. In case the MD_THETA_LIST is present,
    remove NaN values before applying the above factors.
    If the wavelength of the calibration doesn't cover the whole data wavelength,
    the missing wavelength is filled by the same value as the border. Wavelength
    in-between points is linearly interpolated.
    :param data: (DataArray of at least 5 dims) The original data.
            Spectrum data can be of two types:
            - mirror (no wl info)
            - grating (wl info)
        Temporal spectrum data can be of four types:
            - mirror + focus mode (no wl and time info)
            - mirror + operate mode (no wl but time info)
            - grating + focus mode (wl but no time info)
            - grating + operate mode (wl and time info)
        Chronograph data can be only of one type, with no wl but time info. So far no bg correction is
            supported for chronograph data. Spectrum efficiency correction do not apply for this type of data.
    :param bckg: (None or DataArray of at least 5 dims) The background data, with
        CTZYX = C1111 (spectrum), CTZYX = CT111 (temporal spectrum) or CTZYX = 1T111 (time correlator).
    :param coef: (None or DataArray of at least 5 dims) The coefficient data, with CTZXY = C1111.
    :returns: (DataArray) Same shape as original data. Can have dtype=float.
      If MD_THETA_LIST is present and contains NaN values (which is typical), then
      the A dimension (theta angles) is shorten by removing the indices where the
      theta is NaN. The MD_THETA_LIST is updated to only contain the part with numbers.
    """

    # handle time correlator data (chronograph) data
    # -> no spectrum efficiency compensation and bg correction supported
    if data.shape[-5] <= 1 and data.shape[-4] > 1:
        if bckg is not None or coef is not None:
            raise ValueError("Background correction and spectrum efficiency compensation "
                             "not supported on time correlator (chronograph) data")

    # TODO: use MD_BASELINE as a fallback?
    if bckg is not None:

        # Check that the bg matches the data.
        # TODO: support if the data is binned?
        if data.shape[0:2] != bckg.shape[0:2]:
            raise ValueError("Background should have the same shape as the data, but got %s != %s" %
                             (bckg.shape[0:2], data.shape[0:2]))

        # If temporal spectrum data, check for time range and streak mode.
        if model.MD_STREAK_MODE in data.metadata.keys():
            # Check that the data and the bg image were acquired with the same streak mode.
            if data.metadata[model.MD_STREAK_MODE] != bckg.metadata[model.MD_STREAK_MODE]:
                raise ValueError("Background should have the same streak mode as the data, but got %d != %d" %
                                 (bckg.metadata[model.MD_STREAK_MODE], data.metadata[model.MD_STREAK_MODE]))
            # Check that the time range of the data matches with the bg image.
            if data.metadata[model.MD_STREAK_TIMERANGE] != bckg.metadata[model.MD_STREAK_TIMERANGE]:
                raise ValueError("Background should have the same time range as the data, but got %s != %s" %
                                 (bckg.metadata[model.MD_STREAK_TIMERANGE], data.metadata[model.MD_STREAK_TIMERANGE]))

        # Check if we have any wavelength information.
        if model.MD_WL_LIST not in data.metadata:
            # temporal spectrum data, but acquired in mirror mode (with/without time info)
            # spectrum data, but acquired in mirror mode

            # check that bg data also doesn't contain wl info
            if model.MD_WL_LIST in bckg.metadata:
                raise ValueError("Found MD_WL_LIST metadata in background image, but "
                                 "data does not provide any wavelength information")
            data = img.Subtract(data, bckg)

        else:
            # temporal spectrum with wl info (with/without time info)
            # spectrum data with wl info

            # Need to get the calibration data for each wavelength of the data
            wl_data = spectrum.get_wavelength_per_pixel(data)

            # Check that bg data also contains wl info.
            try:
                wl_bckg = spectrum.get_wavelength_per_pixel(bckg)
            except KeyError:
                raise ValueError("Found no spectrum metadata (MD_WL_LIST) in the background image.")

            # Warn if not the same wavelength
            if not numpy.allclose(wl_bckg, wl_data):
                logging.warning("Spectrum background is between %g->%g nm, "
                                "while the spectrum is between %g->%g nm.",
                                wl_bckg[0] * 1e9, wl_bckg[-1] * 1e9,
                                wl_data[0] * 1e9, wl_data[-1] * 1e9)

            data = img.Subtract(data, bckg)

    # Remove NaN values from the theta list, if exists, and update the calibrated data to have the same length
    if model.MD_THETA_LIST in data.metadata:
        angle_range, _ = spectrum.get_angle_range(data)
        angles, data = filter_nan_from_data(angle_range, data)
        data = model.DataArray(data, data.metadata.copy())
        data.metadata[MD_THETA_LIST] = angles

    if coef is not None:
        # Check if we have any wavelength information in data.
        if model.MD_WL_LIST not in data.metadata:
            raise ValueError("Cannot apply spectrum correction as "
                             "data does not provide any wavelength information.")
        if coef.shape[1:] != (1, 1, 1, 1):
            raise ValueError("Spectrum efficiency compensation should have shape C1111.")

        # Need to get the calibration data for each wavelength of the data
        wl_data = spectrum.get_wavelength_per_pixel(data)
        wl_coef = spectrum.get_wavelength_per_pixel(coef)

        # Warn if the calibration is not enough for the data
        if wl_coef[0] > wl_data[0] or wl_coef[-1] < wl_data[-1]:
            logging.warning("Spectrum efficiency compensation is only between "
                            "%g->%g nm, while the spectrum is between %g->%g nm.",
                            wl_coef[0] * 1e9, wl_coef[-1] * 1e9,
                            wl_data[0] * 1e9, wl_data[-1] * 1e9)

        # Interpolate the calibration data for each wl_data
        calib_fitted = numpy.interp(wl_data, wl_coef, coef[:, 0, 0, 0, 0])
        calib_fitted.shape += (1, 1, 1, 1)  # put TZYX dims
        # Compensate the data
        data = data * calib_fitted  # will keep metadata from data

    return data


def filter_nan_from_data(l: list, data: model.DataArray) -> Tuple[list, model.DataArray]:
    """
    Filters out NaN values from the list, and filters out the same indices in the data.
    l (list of floats of length N): typically the MD_THETA_LIST
    data (DataArray of floats with shape .N...): Typically the whole angular spectrum data of shape CAZYX
    return l, data, without the NaNs in l, and the corresponding indices in data removed
    """
    if len(l) != data.shape[1]:
        raise ValueError(f"l has length {len(l)} != data second dimension {data.shape}.")

    l = numpy.array(l)
    not_nan_mask = ~numpy.isnan(l)
    # data is expected to be CAZYX => so filter second dimension
    return l[not_nan_mask], data[:, not_nan_mask]


def write_trigger_delay_csv(filename, trig_delays):
    """
    Store the MD_TIME_RANGE_TO_DELAY into a CSV file
    filename (str): the path to file (if it already exists, it will be overwritten)
    trig_delays (dict float -> float): time range to trigger delay info
    """

    with open(filename, 'w', newline='') as csvfile:
        calibFile = csv.writer(csvfile, delimiter=':')
        for time_range, trig_delay in trig_delays.items():
            calibFile.writerow([time_range, trig_delay])


def read_trigger_delay_csv(filename, time_choices, trigger_delay_range):
    """
    Read the MD_TIME_RANGE_TO_DELAY from a CSV file, and check its validity based on the hardware
    filename (str): the path to file
    time_choices (set): choices possible for timeRange VA
    trigger_delay_range (float, float): min/max value of the trigger delay
    return (dict float -> float): new dictionary containing the loaded time range to trigger delay info
    raise ValueError: if the data of the CSV file cannot be parsed or doesn't fit the hardware
    raise IOError: if the file doesn't exist
    """
    tr2d = {}
    with open(filename, 'r', newline='') as csvfile:
        calibFile = csv.reader(csvfile, delimiter=':')
        for time_range, delay in calibFile:
            try:
                time_range = float(time_range)
                delay = float(delay)
            except ValueError:
                raise ValueError("Trigger delay %s and/or time range %s is not of type float. "
                                 "Please check calibration file for trigger delay." % (delay, time_range))

            # check delay in range allowed
            if not trigger_delay_range[0] <= delay <= trigger_delay_range[1]:
                raise ValueError("Trigger delay %s corresponding to time range %s is not in range %s. "
                                 "Please check the calibration file for the trigger delay." %
                                 (delay, time_range, trigger_delay_range))

            # check timeRange is in possible choices for timeRange on HW
            time_range_hw = find_closest(time_range, time_choices)
            if not almost_equal(time_range, time_range_hw):
                raise ValueError("Time range % s found in calibration file is not a possible choice "
                                 "for the time range of the streak unit. "
                                 "Please modify CSV file so it fits the possible choices for the "
                                 "time range of the streak unit. "
                                 "Values in file must be of format timeRange:triggerDelay (per line)."
                                 % time_range)
            tr2d[time_range_hw] = delay

    # check all time ranges are there
    if len(tr2d) != len(time_choices):
        raise ValueError("The total number of %s time ranges in the loaded calibration file does not "
                         "match the requested number of %s time ranges."
                         % (len(tr2d), len(time_choices)))
    return tr2d
