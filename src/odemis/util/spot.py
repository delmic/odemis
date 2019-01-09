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

import logging
import numpy as np
from odemis import model
from odemis.util import img
import scipy.signal
from scipy import ndimage
from scipy.spatial import cKDTree as KDTree
from scipy.cluster.vq import kmeans


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
            noise_max = 1.3 * np.mean((data[0, 0], data[0, -1], data[-1, 0], data[-1, -1]))

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
    x = np.linspace(1, cols, num=cols)
    y = np.linspace(1, rows, num=rows)
    ysum = np.dot(data0.T, y).T.sum()
    xsum = np.dot(data0, x).sum()

    data_sum = data0.sum(dtype=np.int64)  # TODO: dtype should depend on data.dtype
    if data_sum == 0:
        return float('nan')
    cY = ysum / data_sum
    cX = xsum / data_sum
    xx = (x - cX) ** 2
    yy = (y - cY) ** 2
    XX = np.ndarray(shape=(rows, cols))  # float
    YY = np.ndarray(shape=(rows, cols))  # float
    XX[:] = xx
    YY.T[:] = yy
    diff = XX + YY
    totDist = np.sqrt(diff)
    rmsDist = data0 * totDist
    Mdist = float(rmsDist.sum() / data_sum)
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
    center = (int(round((data0.shape[1] - 1) / 2 + offset[0])),
              int(round((data0.shape[0] - 1) / 2 + offset[1])))
    # clip
    center = (max(1, center[0]),
              min(int(center[1]), data0.shape[0] - 3))
    # Take the 3x3 image around the center
    neighborhood = data0[(center[1] - 1):(center[1] + 2),
                         (center[0] - 1):(center[0] + 2)]
    intens = neighborhood.sum() / total
    return float(intens)


# def FindSubCenterCoordinates(subimages):
#     """
#     For each subimage, detects the center of the contained spot.
#     Finally produces a list with the center coordinates corresponding to each subimage.
#     subimages (List of model.DataArray): List of 2D arrays containing pixel intensity
#     returns (list of (float, float)): Coordinates of spot centers
#     """
#     return [FindCenterCoordinates(i) for i in subimages]


def FindCenterCoordinates(image, smoothing=True):
    """
    Returns the radial symmetry center of the image with sub-pixel resolution.
    This is the spot center location if there is only a single spot contained
    in the image.

    Parameters
    ----------
    image : array_like
        The image of which to determine the radial symmetry center.
    smoothing : boolean
        Apply a smoothing kernel to the intensity gradient.

    Returns
    -------
    pos : tuple
        Position of the radial symmetry center in px from the center of the
        image.

    Examples
    --------
    >>> img = np.zeros((5, 5))
    >>> img[2, 2] = 1
    >>> FindCenterCoordinates(img)
    (0.0, 0.0)

    """
    image = np.asarray(image, dtype=np.float64)

    # Compute lattice midpoints (ik, jk).
    n, m = image.shape
    jk, ik = np.meshgrid(np.arange(m - 1) + 0.5, np.arange(n - 1) + 0.5)

    # Calculate the intensity gradient.
    ki = np.array([(1, 1), (-1, -1)])
    kj = np.array([(1, -1), (1, -1)])
    dIdi = scipy.signal.convolve2d(image, ki, mode='valid')
    dIdj = scipy.signal.convolve2d(image, kj, mode='valid')
    if smoothing:
        k = np.ones((3, 3)) / 9.
        dIdi = scipy.signal.convolve2d(dIdi, k, boundary='symm', mode='same')
        dIdj = scipy.signal.convolve2d(dIdj, k, boundary='symm', mode='same')
    dI2 = np.square(dIdi) + np.square(dIdj)

    # Discard entries where the intensity gradient magnitude is zero, flatten
    # the array in the same go.
    idx = np.flatnonzero(dI2)
    ik = np.take(ik, idx)
    jk = np.take(jk, idx)
    dIdi = np.take(dIdi, idx)
    dIdj = np.take(dIdj, idx)
    dI2 = np.take(dI2, idx)

    # Construct the set of equations for a line passing through the midpoint
    # (ik, jk), parallel to the gradient intensity, in implicit form:
    # `a*i + b*j + c = 0`, normalized such that `a^2 + b^2 = 1`.
    dI = np.sqrt(dI2)
    a = -dIdj / dI
    b = dIdi / dI
    c = a * ik + b * jk

    # Weighting: weight by the square of the gradient magnitude and inverse
    # distance to the centroid of the square of the gradient intensity
    # magnitude.
    sdI2 = np.sum(dI2)
    i0 = np.sum(dI2 * ik) / sdI2
    j0 = np.sum(dI2 * jk) / sdI2
    w = np.sqrt(dI2 / np.hypot(ik - i0, jk - j0))

    # Solve the linear set of equations in a least-squares sense.
    ic, jc = np.linalg.lstsq(np.vstack((w * a, w * b)).T, w * c)[0]

    # Convert from index (top-left) to (center) position information.
    xc = jc - 0.5 * float(m) + 0.5
    yc = ic - 0.5 * float(n) + 0.5

    return xc, yc


def sedisk(r=3):
    """
    Create a flat disk-shaped structuring element with the specified radius r.

    Parameters
    ----------
    r : Non-negative integer. Disk radius (default: 3)

    Returns
    -------
    nhood : Structuring element
    """
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    nhood = (x * x + y * y) <= (r * (r + 1))
    return nhood


def MaximaFind(image, qty, nsize=18):
    """

    Parameters
    ----------
    image : array like
        Data array containing the greyscale image.
    qty : int
        The amount of maxima you want to find.
    nsize : int

    Returns
    -------
    refined_position : array like
        A 2D array of shape (N, 2) containing the coordinates of the maxima.

    """
    filtered = BandPassFilter(image, 1, nsize)

    # dilation
    s = sedisk(nsize // 2)
    dilated = ndimage.grey_dilation(filtered, footprint=s)

    # find local maxima
    binary = (filtered == dilated)

    # thresholding
    qradius = max(nsize // 4, 1)
    BWdil = ndimage.binary_dilation(binary, structure=sedisk(qradius))

    labels, num_features = ndimage.label(BWdil)
    index = np.arange(1, num_features + 1)
    mean = ndimage.mean(filtered, labels=labels, index=index)
    si = np.argsort(mean)[::-1][:qty]
    # estimate spot center position
    pos = np.array(ndimage.center_of_mass(filtered, labels=labels,
                                          index=index[si]))[:, ::-1]
    if np.any(np.isnan(pos)):
        logging.warning('not all positions found')
        pos = pos[np.any(~np.isnan(pos), axis=1)]
    # improve center estimate using radial symmetry method
    w = nsize // 2
    refined_position = np.zeros_like(pos)
    for idx, xy in enumerate(pos):
        _ij = np.rint(xy).astype(int)
        i0, j0 = _ij - w + 1
        i1, j1 = _ij + w
        # if spot is close to edge of image i0 and j0 can be smaller than 0, crop so the spot is still in the center.
        if i0 < 0:
            i1, j1 = np.array([i1, j1]) + i0
            i0, j0 = np.array([i0, j0]) - i0
        if j0 < 0:
            i1, j1 = np.array([i1, j1]) + j0
            i0, j0 = np.array([i0, j0]) - j0
        spot = filtered[j0:j1, i0:i1]
        refined_position[idx] = np.rint(xy) + np.array(FindCenterCoordinates(spot))

    return refined_position


def EstimateLatticeConstant(pos):
    """
    Estimate the lattice constant of a point set that represent a square grid.

    Parameters
    ----------
    pos : array like
        A 2D array of shape (N, 2) containing the coordinates of the points.

    Returns
    -------
    kxy : lattice constants
    """
    # Find the closest 4 neighbours (excluding itself) for each point.
    tree = KDTree(pos)
    dd, ii = tree.query(pos, k=5)
    dr = dd[:, 1:]

    # Determine the median radial distance and filter all points beyond
    # 2*sigma.
    med = np.median(dr)
    std = np.std(dr)
    outliers = np.abs(dr - med) > 2. * std  # doesn't work well if std is very high

    # Determine horizontal and vertical distance (only radial distance is
    # returned by tree.query).
    dpos = pos[ii[:, 0, np.newaxis]] - pos[ii[:, 1:]]
    dx, dy = dpos[:, :, 0], dpos[:, :, 1]
    assert np.all(np.abs(dr - np.hypot(dx, dy)) < 1.0e-12)
    # Use k-means to group the points into two directions.
    X = np.column_stack((dx[~outliers], dy[~outliers]))
    X[X[:, 0] < -0.5 * med] *= -1.
    X[X[:, 1] < -0.5 * med] *= -1.

    centroids, _ = kmeans(X, 2)
    tree = KDTree(centroids)
    _, labels = tree.query(X)
    kxy = np.vstack((np.median(X[labels.ravel() == 0], axis=0),
                     np.median(X[labels.ravel() == 1], axis=0)))

    # The angle between the two directions should be close to 90 degrees.
    alpha = np.math.atan2(np.linalg.norm(np.cross(*kxy)), np.dot(*kxy))
    if np.abs(alpha - np.pi / 2.) > np.deg2rad(2.5):
        logging.warning('Estimated lattice angle differs from 90 degrees by '
                        'more than 2.5 degrees. Input data could be wrong')

    return kxy


def GridPoints(grid_size_x, grid_size_y):
    """
    Parameters
    ----------
    grid_size_x : int
        size of the grid in the x direction.
    grid_size_y : int
        size of the grid in the y direction.

    Returns
    -------
    array like
        The coordinates of a grid of points of size x by y.

    """
    xv = np.arange(grid_size_x, dtype=float) - 0.5 * float(grid_size_x - 1)
    yv = np.arange(grid_size_y, dtype=float) - 0.5 * float(grid_size_y - 1)
    xx, yy = np.meshgrid(xv, yv)
    return np.column_stack((xx.ravel(), yy.ravel()))


def BandPassFilter(image, len_noise, len_object):
    """
    Implements a real-space bandpass filter that suppresses pixel noise and
    long-wavelength image variations while retaining information of a
    characteristic size.

    Adaptation from 'bpass.pro', written by John C. Crocker and David G. Grier.
    Source: http://physics-server.uoregon.edu/~raghu/particle_tracking.html

    Parameters
    ----------
    image : array_like
        The image to be filtered
    len_noise : float (positive)
        Characteristic length scale of noise in pixels. Additive noise averaged
        over this length should vanish.
    len_object : integer
        A length in pixels somewhat larger than a typical object. Must be
        integer.
    """
    image = np.asarray(image, dtype=np.float64)

    # Low-pass filter using a Gaussian kernel.
    std = np.sqrt(2.) * len_noise
    denoised = ndimage.filters.gaussian_filter(image, std, mode='reflect')

    # Estimate background variations using a boxcar kernel.
    N = 2 * int(round(len_object)) + 1
    background = ndimage.filters.uniform_filter(image, N, mode='reflect')

    return np.maximum(denoised - background, 0.)
