# -*- coding: utf-8 -*-
'''
Created on 24 Aug 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas and Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import numpy
from odemis import model
from odemis.util import img
import scipy.signal
import warnings


def _SubtractBackground(data, background=None):
    # We actually want to make really sure that only real signal is > 0.
    if background is not None:
        # So we subtract the "almost max" of the background signal
        hist, edges = img.histogram(background)
        noise_max = img.findOptimalRange(hist, edges, outliers=1e-6)[1]
    else:
        try:
            noise_max = 1.3 * data.metadata[model.MD_BASELINE]
        except (AttributeError, KeyError):
            # Fallback: take average of the four corner pixels
            noise_max = 1.3 * (data[0, 0] + data[0, -1] + data[-1, 0] + data[-1, -1]) / 4

    noise_max = data.dtype.type(noise_max)  # ensure we don't change the dtype
    data0 = img.Subtract(data, noise_max)
    # Alternative way (might work better if background is really not uniform):
    # 1.3 corresponds to 3 times the noise
    # data0 = img.Subtract(data - 1.3 * background)

    return data0


def MomentOfInertia(data, background=None):
    """
    Calculates the moment of inertia for a given optical image
    data (model.DataArray): The optical image
    background (None or model.DataArray): Background image subtracted from the data
      If None, it will try to use the MD_BASELINE metadata, and fall-back to the
      corner pixels
    returns (float): moment of inertia
       Note: if the image is entirely black, it will return NaN.
    """
    # Subtract background, to make sure there is no noise
    data0 = _SubtractBackground(data, background)

    rows, cols = data0.shape
    x = numpy.linspace(1, cols, num=cols)
    y = numpy.linspace(1, rows, num=rows)
    ysum = numpy.dot(data0.T, y).T.sum()
    xsum = numpy.dot(data0, x).sum()

    data_sum = data0.sum(dtype=numpy.int64)  # TODO: dtype should depend on data.dtype
    cY = ysum / data_sum
    cX = xsum / data_sum
    xx = (x - cX) ** 2
    yy = (y - cY) ** 2
    XX = numpy.ndarray(shape=(rows, cols))  # float
    YY = numpy.ndarray(shape=(rows, cols))  # float
    XX[:] = xx
    YY.T[:] = yy
    diff = XX + YY
    totDist = numpy.sqrt(diff)
    rmsDist = data0 * totDist
    Mdist = rmsDist.sum() / data_sum
    # In case of just one bright pixel, avoid returning 0 and return unknown
    # instead.
    if Mdist == 0:
        return float('nan')
    return Mdist


def SpotIntensity(data, background=None):
    """
    Gives an estimation of the spot intensity given the optical and background image.
    The bigger the value is, the more "spotty" the spot is.
    It provides a ratio that is comparable to the same measurement for another
    image, but it is not possible to compare images with different dimensions (binning).
    data (model.DataArray): The optical image
    background (None or model.DataArray): Background image that we use for subtraction
    returns (0<=float<=1): spot intensity estimation
    """
    data0 = _SubtractBackground(data, background)
    total = data0.sum()
    if total == 0:
        return 0  # No data, no spot => same as hugely spread spot

    # center of mass
    offset = FindCenterCoordinates(data0)
    im_center = (data0.shape[1] / 2, data0.shape[0] / 2)
    center = tuple(a + b for a, b in zip(im_center, offset))
    neighborhood = data0[(center[1] - 1):(center[1] + 2),
                         (center[0] - 1):(center[0] + 2)]
    intens = neighborhood.sum() / total
    return intens


# def FindSubCenterCoordinates(subimages):
#     """
#     For each subimage, detects the center of the contained spot.
#     Finally produces a list with the center coordinates corresponding to each subimage.
#     subimages (List of model.DataArray): List of 2D arrays containing pixel intensity
#     returns (list of (float, float)): Coordinates of spot centers
#     """
#     return [FindCenterCoordinates(i) for i in subimages]


def FindCenterCoordinates(image):
    """
    Detects the center of the contained spot.
    It assumes there is only one spot.
    subimages (model.DataArray): 2D arrays containing pixel intensity
    returns (float, float): Position of the spot center (from the upper left),
      possibly with sub-pixel resolution.
    """
    # Input might be integer
    # TODO Dummy, change the way that you handle the array e.g. convolution
    image = image.astype(numpy.float64)
    image_x, image_y = image.shape

    # See Parthasarathy's paper for details
    xk_onerow = numpy.arange(-(image_y - 1) / 2 + 0.5, (image_y - 1) / 2, 1)
    (xk_onerow_x,) = xk_onerow.shape
    xk = numpy.tile(xk_onerow, image_x - 1)
    xk = xk.reshape((image_x - 1, xk_onerow_x))
    yk_onecol = numpy.arange((image_x - 1) / 2 - 0.5, -(image_x - 1) / 2, -1)
    (yk_onecol_x,) = yk_onecol.shape
    yk_onecol = yk_onecol.reshape((yk_onecol_x, 1))
    yk = numpy.tile(yk_onecol, image_y - 1)

    dIdu = image[0:image_x - 1, 1:image_y] - image[1:image_x, 0:image_y - 1]
    dIdv = image[0:image_x - 1, 0:image_y - 1] - image[1:image_x, 1:image_y]

    # Smoothing
    h = numpy.tile(numpy.ones(3) / 9, 3).reshape(3, 3)  # simple 3x3 averaging filter
    # TODO: explain why it's ok to catch these warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", numpy.ComplexWarning)
        dIdu = scipy.signal.convolve2d(dIdu, h, mode='same', fillvalue=0)
        dIdv = scipy.signal.convolve2d(dIdv, h, mode='same', fillvalue=0)

    # Calculate intensity gradient in xy coordinate system
    dIdx = dIdu - dIdv
    dIdy = dIdu + dIdv

    # Assign a,b
    a = -dIdy
    b = dIdx

    # Normalize such that a^2 + b^2 = 1
    I2 = numpy.hypot(a, b)
    s = (I2 != 0)
    a[s] = a[s] / I2[s]
    b[s] = b[s] / I2[s]

    # Solve for c
    c = -a * xk - b * yk

    # Weighting: weight by square of gradient magnitude and inverse distance to gradient intensity centroid.
    dI2 = dIdu * dIdu + dIdv * dIdv
    sdI2 = numpy.sum(dI2[:])
    x0 = numpy.sum(dI2[:] * xk[:]) / sdI2
    y0 = numpy.sum(dI2[:] * yk[:]) / sdI2
    w = dI2 / (0.05 + numpy.sqrt((xk - x0) * (xk - x0) + (yk - y0) * (yk - y0)))

    # Make the edges zero, because of the filter
    w[0, :] = 0
    w[w.shape[0] - 1, :] = 0
    w[:, 0] = 0
    w[:, w.shape[1] - 1] = 0

    # Find radial center
    swa2 = numpy.sum(w[:] * a[:] * a[:])
    swab = numpy.sum(w[:] * a[:] * b[:])
    swb2 = numpy.sum(w[:] * b[:] * b[:])
    swac = numpy.sum(w[:] * a[:] * c[:])
    swbc = numpy.sum(w[:] * b[:] * c[:])
    det = swa2 * swb2 - swab * swab
    xc = (swab * swbc - swb2 * swac) / det
    yc = (swab * swac - swa2 * swbc) / det

    # Output relative to upper left coordinate
    xc = xc  # + 1 / 2
    yc = -yc  # + 1 / 2
    return (xc, yc)
