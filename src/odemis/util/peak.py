# -*- coding: utf-8 -*-
'''
Created on 20 Oct 2015

@author: Kimon Tsitsikas

Copyright © 2015-2016 Kimon Tsitsikas and Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
import logging
import numpy
from odemis import model
from scipy.optimize import curve_fit, OptimizeWarning
import threading
import time
import warnings
from builtins import range

H_PLANK = 6.6260715e-34  # J/s(e-34)
C_LIGHT = 299792458  # m/s
E_CHARGE = 1.602e-19  # J --> eV
WL_TO_ENERGY = H_PLANK * C_LIGHT / E_CHARGE
WIDTH_RATIO = 0.01

# TODO: this code is full of reliance on numpy being quite lax with wrong
# computation, and easily triggers numpy warnings. To force numpy to be
# stricter:
# import warnings
# warnings.filterwarnings('error')
# numpy.seterr(all='raise')

# These four fitting functions are called back from curve_fit()
# Note: when returning NaN, curve_fit() appears to not like the proposed parameters


def GaussianFit(data, *peaks):
    """
    Applies gaussian fitting to data given the "peaks" parameters.
    data (1d array of floats): the wavelength list for each N pixel
    peaks (list of floats): series of pos, width, amplitude and initial offset (given in space domain)
    """
    gau = peaks[-1]  # Automatically converted to a vector in the addition
    for pos, width, amplitude in _Grouped(peaks[:-1], 3):
        gau += amplitude * numpy.exp(-(data - pos) ** 2 / (2 * width ** 2))
    return gau


def GaussianEnergyFit(data, *peaks):
    """
    Applies gaussian fitting to data given the "peaks" parameters.
    data (1d array of floats): the energy list for each N pixel
    peaks (list of floats): series of pos, width, amplitude and initial offset (currently given in the energy domain)
    """
    gau = peaks[-1] * WL_TO_ENERGY / data**2  # offset is a constant in space domain, so it's converted to energy here
    for pos, width, amplitude in _Grouped(peaks[:-1], 3):
        gau += amplitude * numpy.exp(-(data - pos) ** 2 / (2 * width ** 2))
    return gau


def LorentzianFit(data, *peaks):
    """
    Applies lorentzian fitting to data given the "peaks" parameters.
    data (1d array of floats): the wavelength list for each N pixel
    peaks (list of floats): series of pos, width, amplitude and initial offset (given in space domain)
    """
    lor = peaks[-1]  # Automatically converted to a vector in the addition
    for pos, width, amplitude in _Grouped(peaks[:-1], 3):
        lor += amplitude * width ** 2 / ((data - pos) ** 2 + width ** 2)
    return lor


def LorentzianEnergyFit(data, *peaks):
    """
    Applies lorentzian fitting to data given the "peaks" parameters.
    data (1d array of floats): the energy list for each N pixel
    peaks (list of floats): series of pos, width, amplitude and initial offset (currently given in the energy domain)
    """
    lor = peaks[-1] * WL_TO_ENERGY / data**2 # offset is a constant in space domain, so it's converted to energy here
    for pos, width, amplitude in _Grouped(peaks[:-1], 3):
        lor += amplitude * width ** 2 / ((data - pos) ** 2 + width ** 2)
    return lor


def Smooth(signal, window_len=11, window='hanning'):
    """
    Based at https://github.com/dannyjacobs/capo/blob/master/dcj/cals/smooth.py

    This method is based on the convolution of a scaled window with the signal.
    The signal is prepared by introducing reflected copies of the signal
    (with the window size) in both ends so that transient parts are minimized
    in the begining and end part of the output signal.
    signal (list of floats): The input signal.
    window_len (int): The dimension of the smoothing window; should be an odd integer.
    window (str): The type of window from 'flat', 'hanning', 'hamming', 'bartlett',
    'blackman' flat window will produce a moving average smoothing.
    returns (list of floats): The smoothed signal
    """
    # First check if parameters can be processed
    if signal.ndim != 1:
        raise ValueError("Smooth only accepts 1 dimension arrays.")
    if signal.size < window_len:
        raise ValueError("Input vector needs to be bigger than window size.")
    if window_len < 3:
        return signal
    if window not in ('flat', 'hanning', 'hamming', 'bartlett', 'blackman'):
        raise ValueError("Window has to be one of 'flat', 'hanning', "
                         "'hamming', 'bartlett', 'blackman'")
    s = numpy.r_[2 * signal[0] - signal[window_len - 1::-1],
                 signal,
                 2 * signal[-1] - signal[-1:-window_len:-1]]
    if window == 'flat':  # moving average
        w = numpy.ones(window_len, 'd')
    else:
        f = getattr(numpy, window)
        w = f(window_len)
    y = numpy.convolve(w / w.sum(), s, mode='same')
    return y[window_len:-window_len + 1]


PEAK_FUNCTIONS = {'gaussian_space': GaussianFit, 'lorentzian_space': LorentzianFit, 'gaussian_energy': GaussianEnergyFit,
                  'lorentzian_energy': LorentzianEnergyFit}


def Detect(y_vector, x_vector=None, lookahead=5, delta=0):
    """
    Inspired by MATLAB script at http://billauer.co.il/peakdet.html

    Finds the local maxima and minima ("peaks") in the signal represented by
    y_vector.
    y_vector (list of floats): The signal where the peaks are to be found on.
    x_vector (list of floats): Represents the position of the corresponding element
    of y_vector.
    lookahead (int): Distance to look ahead from a peak candidate to determine
    if it is an actual peak.
    delta (int): Minimum difference between a candidate peak and the following
    points for this to be considered a peak.
    returns (2 lists of tuple of floats): Contain the positive and negative peaks
    respectively.
    """
    maxtab = []
    mintab = []
    dump = []

    length = len(y_vector)
    if x_vector is None:
        x_vector = numpy.arange(length)

    # First check if parameters can be processed
    if length != len(x_vector):
        raise ValueError("Input vectors y_vector and x_vector must have same length")
    elif lookahead < 1:
        raise ValueError("Lookahead must be greater than 1")
    elif not (numpy.isscalar(delta) and delta >= 0):
        raise ValueError("Delta must be a positive number")

    y_vector = numpy.asarray(y_vector)
    mn, mx = numpy.Inf, -numpy.Inf

    # Compare candidate peak to the lookahead amount of points in front of it
    for index, (x, y) in enumerate(zip(x_vector[:-lookahead], y_vector[:-lookahead])):
        if y > mx:
            mx = y
            mxpos = x
        if y < mn:
            mn = y
            mnpos = x

        if y < mx - delta and mx != numpy.Inf:
            if y_vector[index:index + lookahead].max() < mx:
                maxtab.append((mxpos, mx))
                dump.append(True)
                mx = numpy.Inf
                mn = numpy.Inf

        if y > mn + delta and mn != -numpy.Inf:
            if y_vector[index:index + lookahead].min() > mn:
                mintab.append((mnpos, mn))
                dump.append(False)
                mn = -numpy.Inf
                mx = -numpy.Inf

    # remove the first value since it is always detected as peak
    try:
        if dump[0]:
            maxtab.pop(0)
        else:
            mintab.pop(0)
        del dump
    except IndexError:
        # just return empty lists
        pass

    return maxtab, mintab


class PeakFitter(object):
    def __init__(self):
        # will take care of executing peak fitting asynchronously
        # Maximum one task at a time as curve_fit() is not thread-safe
        self._executor = model.CancellableThreadPoolExecutor(max_workers=1)

    def __del__(self):
        if self._executor:
            self._executor.cancel()
            self._executor.shutdown()
            self._executor = None
        logging.debug("PeakFitter destroyed")

    def Fit(self, spectrum, wavelength, type='gaussian_space'):
        """
        Wrapper for _DoFit. It provides the ability to check the progress of fitting
        procedure or even cancel it.
        spectrum (1d array of floats): The data representing the spectrum.
        wavelength (1d array of floats): The wavelength values corresponding to the
        spectrum given.
        type (str): Type of fitting to be applied ('gaussian_space', 'lorentzian_space',
        'gaussian_energy' or 'lorentzian_energy')
        returns (model.ProgressiveFuture):  Progress of DoFit
        """
        # Create ProgressiveFuture and update its state to RUNNING
        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateFitTime(spectrum))
        f._fit_state = RUNNING
        f._fit_lock = threading.Lock()
        f.task_canceller = self._CancelFit

        return self._executor.submitf(f, self._DoFit, f, spectrum, wavelength, type)

    def _DoFit(self, future, spectrum, wavelength, type='gaussian_space'):
        """
        Smooths the spectrum signal, detects the peaks and applies the type of peak
        fitting required. Finally returns the optimized peak parameters.
        future (model.ProgressiveFuture): Progressive future provided by the wrapper
        spectrum (1d array of floats): The data representing the spectrum.
        wavelength (1d array of floats): The wavelength values corresponding to the
        spectrum given.
        type (str): Type of fitting to be applied (for now only ‘gaussian’ and
        ‘lorentzian’ are available).
        returns:
             params (list of 3-tuple): Each peak parameters as (pos, width, amplitude)
             offset (float): global offset to add
        raises:
                KeyError if given type not available
                ValueError if fitting cannot be applied
        """
        try:
            # values based on experimental datasets
            if len(wavelength) >= 2000:
                divider = 20
            elif len(wavelength) >= 1000:
                divider = 25
            else:
                divider = 30
            init_window_size = max(3, len(wavelength) // divider)
            window_size = init_window_size
            logging.debug("Starting peak detection on data (len = %d) with window = %d",
                          len(wavelength), window_size)
            try:
                wl_rng = wavelength[-1] - wavelength[0]
                width = wl_rng * WIDTH_RATIO  # initial peak width estimation
                FitFunction = PEAK_FUNCTIONS[type]
            except KeyError:
                raise KeyError("Given type %s not in available fitting types: %s" % (type, list(PEAK_FUNCTIONS.keys())))
            for step in range(5):
                if future._fit_state == CANCELLED:
                    raise CancelledError()
                smoothed = Smooth(spectrum, window_len=window_size)
                # Increase window size until peak detection finds enough peaks to fit
                # the spectrum curve
                peaks = Detect(smoothed, wavelength, lookahead=window_size, delta=5)[0]
                if not peaks:
                    window_size = int(round(window_size * 1.2))
                    logging.debug("Retrying to fit peak with window = %d", window_size)
                    continue

                fit_list = []
                lower_bounds = []
                upper_bounds = []
                for (pos, amplitude) in peaks:
                    if type in {'gaussian_energy', 'lorentzian_energy'}:
                        energy = apply_jacobian_x(wavelength)
                        spectra_energy = apply_jacobian_y(wavelength, spectrum)
                        fit_list.extend(peak_to_energy(pos, width, amplitude))
                        # lower & upper bounds for center position, width, amplitude in energy domain
                        en_rng = energy[0] - energy[-1]
                        lower_bounds.extend([energy[-1] - en_rng / 2, en_rng / 1e4, 0])
                        upper_bounds.extend([energy[0] + en_rng / 2, en_rng * 10, numpy.inf])
                    else:
                        # lower & upper bounds for center position, width, amplitude in space domain
                        fit_list.extend([pos, width, amplitude])
                        lower_bounds.extend([wavelength[0] - wl_rng / 2, wl_rng / 1e3, 0])
                        upper_bounds.extend([wavelength[-1] + wl_rng / 2, wl_rng * 10, numpy.inf])

                # Initialize the offset with the minimum possible value
                offset = 0
                fit_list.append(offset)
                # Set the lower & upper bounds for the offset
                lower_bounds.extend([0])
                upper_bounds.extend([min(spectrum)])
                param_bounds = (lower_bounds, upper_bounds)

                if future._fit_state == CANCELLED:
                    raise CancelledError()

                try:
                    with warnings.catch_warnings():
                        # Hide scipy/optimize/minpack.py:690: OptimizeWarning: Covariance of the parameters could not be estimated
                        warnings.filterwarnings("ignore", "", OptimizeWarning)
                        # TODO, from scipy 0.17, curve_fit() supports the 'bounds' parameter.
                        # It could be used to ensure the peaks params are positives.
                        # (Once we don't support Ubuntu 12.04)
                        if type in {'gaussian_energy', 'lorentzian_energy'}:
                            params, _ = curve_fit(FitFunction, energy, spectra_energy, p0=fit_list, bounds=param_bounds)
                        else:
                            params, _ = curve_fit(FitFunction, wavelength, spectrum, p0=fit_list, bounds=param_bounds)
                    break
                except Exception as ex:
                    window_size = int(round(window_size * 1.2))
                    logging.debug("Retrying to fit peak with window = %d due to error %s", window_size, ex)
                    continue
            else:
                raise ValueError("Could not apply peak fitting of type %s." % type)
            # reformat parameters to (list of 3 tuples, offset)
            peaks_params = []
            for pos, width, amplitude in _Grouped(params[:-1], 3):
                # Note: to avoid negative peaks, the fit functions only take the
                # absolute of the amplitude/width. So now amplitude and width
                # have 50% chances to be negative => Force positive now.
                if type in {'gaussian_energy', 'lorentzian_energy'}:
                    peaks_params.append(peak_to_wavelength(pos, width, amplitude))
                else:
                    peaks_params.append((pos, width, amplitude))

            return peaks_params, params[-1], type
        except CancelledError:
            logging.debug("Fitting of type %s was cancelled.", type)
        finally:
            with future._fit_lock:
                if future._fit_state == CANCELLED:
                    raise CancelledError()
                future._fit_state = FINISHED

    def _CancelFit(self, future):
        """
        Canceller of _DoFit task.
        """
        logging.debug("Cancelling fitting...")

        with future._fit_lock:
            if future._fit_state == FINISHED:
                return False
            future._fit_state = CANCELLED
            logging.debug("Fitting cancelled.")

        return True

    def estimateFitTime(self, data):
        """
        Estimates fitting duration
        """
        # really rough estimation
        return len(data) * 10e-3  # s


def peak_to_energy(pos, width, amplitude):
    """
    Converts the peaks to energy domain.
    Args:
        pos: the center position of the peak in 'm'
        width: the width of the peak in 'm'
        amplitude: the amplitude of the peak in space domain

    Returns: peak parameters as (pos, width, amplitude) in energy domain

    """
    pos_e = WL_TO_ENERGY / pos
    width_e = (WL_TO_ENERGY / (pos - width / 2)) - (WL_TO_ENERGY / (pos + width / 2))
    amplitude_e = (amplitude * pos ** 2) / WL_TO_ENERGY
    return pos_e, width_e, amplitude_e


def peak_to_wavelength(pos_e, width_e, amplitude_e):
    """
    Converts the peaks to space domain.
    Args:
        pos_e: the center position of the peak in 'eV'
        width_e: the width of the peak in 'eV'
        amplitude_e: the amplitude of the peak in energy domain

    Returns: peak parameters as (pos, width, amplitude) in space domain

    """
    pos = WL_TO_ENERGY / pos_e
    width = (WL_TO_ENERGY / (pos_e - width_e / 2)) - (WL_TO_ENERGY / (pos_e + width_e / 2))
    amplitude = (amplitude_e * WL_TO_ENERGY) / pos ** 2
    return pos, width, amplitude


def apply_jacobian_x(wavelength):
    """
    Converts the wavelength from 'm' (space domain) to 'eV' (energy domain).
    Args:
        wavelength(1d array of floats): the list of wavelengths in 'm'

    Returns:
         energy (1d array of floats): wavelength in 'eV'

    """
    wavelength = numpy.asarray(wavelength)
    energy = WL_TO_ENERGY / wavelength
    return energy


def apply_jacobian_y(wavelength, spectrum):
    """
    Applies Jacobian transformation to the spectrum.
    Args:
        wavelength(1d array of floats): wavelength values corresponding to the given spectrum in 'm'
        spectrum(1d array of floats): data representing the spectrum in space domain

    Returns:
         spectra_energy (1d array of floats): data representing the spectrum in energy domain

    """
    wavelength = numpy.asarray(wavelength)
    spectra_energy = spectrum * (wavelength ** 2) / WL_TO_ENERGY
    return spectra_energy


def apply_reverse_jacobian_y(wavelength, curve_energy):
    """
    Applied inverse Jacobian transformation to the curve.
    Args:
        wavelength (1d array of floats): wavelength values in 'eV'
        curve_energy (1d array of floats): dataset of points representing the curve in energy domain

    Returns:
        curve_space (1d array of floats): dataset of points representing the curve in space domain

    """
    wavelength = numpy.asarray(wavelength)
    curve_space = (curve_energy * WL_TO_ENERGY) / (wavelength ** 2)
    return curve_space


def Curve(wavelength, peak_parameters, offset, type='gaussian_space'):
    """
    Given the peak parameters and the wavelength values returns the actual
    dataset of curve points.
    wavelength (1d array of floats): The wavelength values corresponding to the
    spectrum given.
    peak_parameters (list of tuples): The parameters of the peak curves to
    be depicted. They can be either in space or energy domain depending on the
    fitting type.
    offset (float): peaks offset
    type (str): Type of fitting to be applied (for now only ‘gaussian’ and
    ‘lorentzian’ are available).
    returns (1d array of floats): Dataset of points representing the curve.
    raises:
            KeyError if given type not available
            ValueError if fitting cannot be applied
    """
    try:
        FitFunction = PEAK_FUNCTIONS[type]
    except KeyError:
        raise KeyError("Given type %s not in available fitting types: %s" % (type, list(PEAK_FUNCTIONS.keys())))

    if type in {'gaussian_energy', 'lorentzian_energy'}:
        peaks_params = []
        for (pos, width, amplitude) in peak_parameters:
            peaks_params.append(peak_to_energy(pos, width, amplitude))
        peak_parameters = peaks_params
        rng = apply_jacobian_x(wavelength)  # energy
    else:
        rng = wavelength
    # Flatten the peak parameters tuples
    peak_flat = [p for l in peak_parameters for p in l]
    peak_flat.append(offset)

    curve = FitFunction(rng, *peak_flat)
    if type in {'gaussian_energy', 'lorentzian_energy'}:
        curve = apply_reverse_jacobian_y(wavelength, curve)
    # residual = numpy.sqrt((abs(output - spectrum) ** 2).sum() / len(spectrum))
    # logging.info("Residual error of spectrum fitting is %f", residual)
    return curve


def _Grouped(iterable, n):
    """
    Iterate over the iterable, n elements at a time
    """
    return zip(*[iter(iterable)] * n)