# -*- coding: utf-8 -*-
"""
Created on 12 Apr 2013

@author: Rinze de Laat

Copyright © 2013-2024 Éric Piel, Rinze de Laat, Thera Pals, Delmic

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
import logging

import cv2
import numpy
from scipy import ndimage
from scipy.optimize import curve_fit
from scipy.signal import medfilt


def _convertRBGToGrayscale(image):
    """
    Quick and dirty convertion of RGB data to grayscale
    image (numpy array of shape YX3)
    return (numpy array of shape YX)
    """
    r, g, b = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    gray = numpy.empty(image.shape[0:2], dtype="uint16")
    gray[...] = r
    gray += g
    gray += b

    return gray


def MeasureSEMFocus(image):
    """
    Given an image, focus measure is calculated using the standard deviation of
    the raw data.
    image (model.DataArray): SEM image
    returns (float): The focus level of the SEM image (higher is better)
    """
    # Handle RGB image
    if len(image.shape) == 3:
        # TODO find faster/better solution
        image = _convertRBGToGrayscale(image)

    return ndimage.standard_deviation(image)


def MeasureOpticalFocus(image):
    """
    Given an image, focus measure is calculated using the variance of Laplacian
    of the raw data.
    image (model.DataArray): Optical image
    returns (float): The focus level of the optical image (higher is better)
    """
    # Handle RGB image
    if len(image.shape) == 3:
        # TODO find faster/better solution
        image = _convertRBGToGrayscale(image)

    # TODO: maybe switch to scipy.ndimage.laplace ?
    # OpenCV only supports int of 8 & 16 bits and float32/64 images. So if we get anything else,
    # we first convert to float64, to make sure it's compatible.
    if image.dtype not in (numpy.int8, numpy.uint8, numpy.int16, numpy.uint16, numpy.float32, numpy.float64):
         image = image.astype(numpy.float64)

    return cv2.Laplacian(image, cv2.CV_64F).var()


def Measure1d(image):
    """
    Given an image of a 1 line ccd, measure the focus based on the inverse of the width of a gaussian fit of the data.
    It is assumed that the signal is in focus when the width of the signal is smallest and therefore sigma is smallest.
    image (model.DataArray): 1D image from 1 line ccd.
    returns (float): The focus level of the image, based on the inverse of the width of a gaussian fitted on the image.
    """
    # Use the gauss function to fit a gaussian to the 1 line image.
    def gauss(x, amplitude, pos, width, base):
        y = amplitude * numpy.exp(-(x - pos) ** 2 / (2 * width ** 2)) + base
        return y
    # squeeze to make sure the image array is 1d.
    signal = numpy.squeeze(image)
    # Apply a median filter with a kernel of 5, to handle noise with up to 2 neighbouring pixels with a very high value,
    # resembling a peak, which sometimes happens on CCDs.
    signal = medfilt(signal, 5)
    x = numpy.arange(len(signal))
    width = max(3.0, 0.01 * len(signal))
    # determine the indices and the values of the 1% highest points in the signal.
    max_ids = signal.argsort()[-int(width):]
    max_sig = signal[max_ids]
    med_sig = numpy.median(signal)
    # give an initial estimate for the parameters of the gaussian fit: [amplitude, expected position, width, base]
    p_initial = [numpy.median(max_sig) - med_sig, numpy.median(max_ids), width, med_sig]
    # Use curve_fit to fit the gauss function to the data. Use p_initial as our initial guess.
    try:
        popt, pcov = curve_fit(gauss, x, signal, p0=p_initial)
    except RuntimeError as ex:
        # No fitting can be found => the focus is really bad
        logging.debug("Failed to estimate focus level, assuming a very bad level: %s", ex)
        return 0

    # The focus metric is the inverse of width of the gaussian fit (a smaller width is a higher focus level).
    return 1 / abs(popt[2])


def MeasureSpotsFocus(image):
    """
    Focus measurement metric based on Tenengrad variance:
        Pech, J.; Cristobal, G.; Chamorro, J. & Fernandez, J. Diatom autofocusing in brightfield microscopy: a
        comparative study. 2000.

    Given an image, the focus measure is calculated using the variance of a Sobel filter applied in the
    x and y directions of the raw data.
    image (model.DataArray): Optical image
    returns (float): The focus level of the image (higher is better)
    """
    # TODO: maybe switch to scipy.ndimage.sobel ?
    # OpenCV only supports int of 8 & 16 bits and float32/64 images. So if we get anything else,
    # we first convert to float64, to make sure it's compatible.
    if image.dtype not in (numpy.int8, numpy.uint8, numpy.int16, numpy.uint16, numpy.float32, numpy.float64):
         image = image.astype(numpy.float64)
    sobelx = cv2.Sobel(image, cv2.CV_64F, 1, 0, ksize=5)
    sobely = cv2.Sobel(image, cv2.CV_64F, 0, 1, ksize=5)
    sobel_image = sobelx ** 2 + sobely ** 2
    return sobel_image.var()


def AssessFocus(levels, min_ratio=15):
    """
    Given a list of focus levels, it decides if there is any significant value
    or it only contains noise.
    levels (list of floats): List of focus levels
    min_ratio (0 < float): minimum ratio between the focus level max-mean and
      the standard deviation to be considered "significant".
    returns (boolean): True if there is significant deviation
    """
    std_l = numpy.std(levels)

    levels_nomax = list(levels)
    max_l = max(levels)
    levels_nomax.remove(max_l)
    avg_l = numpy.mean(levels_nomax)
    l_diff = max_l - avg_l

    logging.debug("Focus level std dev: %f, avg: %f, diff max: %f", std_l, avg_l, l_diff)
    if std_l > 0 and l_diff >= min_ratio * std_l:
        logging.debug("Significant focus level deviation was found")
        return True
    return False
