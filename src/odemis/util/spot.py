# -*- coding: utf-8 -*-
"""
Created on 24 Aug 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas and Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import math
from typing import Optional, Tuple

import cv2
import numpy
import scipy.signal
from odemis import model, util
from odemis.util import img
from odemis.util.peak_local_max import peak_local_max
from odemis.util.transform import to_physical_space
from scipy import ndimage
from scipy.cluster.vq import kmeans
from scipy.spatial import cKDTree as KDTree
from scipy.spatial.distance import cdist


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
            noise_max = 1.3 * numpy.mean((data[0, 0], data[0, -1], data[-1, 0], data[-1, -1]))

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
    if data_sum == 0:
        return float('nan')
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


def FindCenterCoordinates(
    image: numpy.ndarray, smoothing: bool = True
) -> Tuple[float, float]:
    """
    Returns the radial symmetry center of the image with sub-pixel resolution.
    The position is returned as a tuple `(dx, -dy)` in pixels relative to the
    center of the image.

    For more information see radial_symmetry_center().

    """
    ji = radial_symmetry_center(image, smoothing)
    xc, yc = to_physical_space(ji, image.shape)
    return xc, -yc


def radial_symmetry_center(
    image: numpy.ndarray, smoothing: bool = True
) -> Tuple[float, float]:
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
        Position of the radial symmetry center `(j, i)` as pixel index of the
        image, where `j` is the row-index and `i` is the column index.

    References
    ----------
    .. [1] Parthasarathy, R. (2012). Rapid, accurate particle tracking by
    calculation of radial symmetry centers. Nature methods, 9(7), 724-726.

    Examples
    --------
    >>> img = numpy.zeros((5, 7))
    >>> img[2, 4] = 1
    >>> radial_symmetry_center(img, smoothing=False)
    (2.0, 4.0)

    """
    image = numpy.asarray(image, dtype=numpy.float64)

    # Compute lattice midpoints (jk, ik).
    n, m = image.shape
    jk, ik = numpy.meshgrid(
        numpy.arange(n - 1, dtype=float) + 0.5,
        numpy.arange(m - 1, dtype=float) + 0.5,
        indexing="ij",
    )

    # Calculate the intensity gradient.
    kj = numpy.array([(1, 1), (-1, -1)])
    ki = numpy.array([(1, -1), (1, -1)])
    dIdj = scipy.signal.convolve2d(image, kj, mode="valid")
    dIdi = scipy.signal.convolve2d(image, ki, mode="valid")
    if smoothing:
        k = numpy.ones((3, 3)) / 9.0
        dIdj = scipy.signal.convolve2d(dIdj, k, boundary="symm", mode="same")
        dIdi = scipy.signal.convolve2d(dIdi, k, boundary="symm", mode="same")
    dI2 = numpy.square(dIdj) + numpy.square(dIdi)

    # Discard entries where the intensity gradient magnitude is zero, flatten
    # the array in the same go.
    idx = numpy.flatnonzero(dI2)
    jk = numpy.take(jk, idx)
    ik = numpy.take(ik, idx)
    dIdj = numpy.take(dIdj, idx)
    dIdi = numpy.take(dIdi, idx)
    dI2 = numpy.take(dI2, idx)

    # Construct the set of equations for a line passing through the midpoint
    # `(jk, ik)`, parallel to the gradient intensity, in implicit form:
    # `a*j + b*i = c`, normalized such that `a^2 + b^2 = 1`.
    dI = numpy.sqrt(dI2)
    a = -dIdi / dI
    b = dIdj / dI
    c = a * jk + b * ik

    # Weighting: weight by the square of the gradient magnitude and inverse
    # distance to the centroid of the square of the gradient intensity
    # magnitude.
    sdI2 = numpy.sum(dI2)
    j0 = numpy.sum(dI2 * jk) / sdI2
    i0 = numpy.sum(dI2 * ik) / sdI2
    w = numpy.sqrt(dI2 / numpy.hypot(jk - j0, ik - i0))

    # Solve the linear set of equations in a least-squares sense.
    # Note: rcond is set explicitly to have correct and consistent behavior,
    #       independent on numpy version.
    rcond = numpy.finfo(numpy.float64).eps * max(n, m)
    jc, ic = numpy.linalg.lstsq(numpy.vstack((w * a, w * b)).T, w * c, rcond)[0]

    return jc, ic


def _CreateSEDisk(r=3):
    """
    Create a flat disk-shaped structuring element with the specified radius r. The structuring element can be used
    to interact with an image, for instance with morphological operations like dilation and erosion.

    Parameters
    ----------
    r : Non-negative integer. Disk radius (default: 3)

    Returns
    -------
    neighborhood : array like of booleans of shape [2r+1, 2r+1]
        Disk shaped structuring element.

    """
    y, x = numpy.mgrid[-r:r + 1, -r:r + 1]
    neighborhood = (x * x + y * y) <= (r * (r + 1))
    return numpy.array(neighborhood).astype(numpy.uint8)


def MaximaFind(image, qty, len_object=18):
    """
    Find the center coordinates of maximum spots in an image.

    Parameters
    ----------
    image : array like
        Data array containing the greyscale image.
    qty : int
        The amount of maxima you want to find.
    len_object : int
        A length in pixels somewhat larger than a typical spot.

    Returns
    -------
    refined_position : array like
        A 2D array of shape (N, 2) containing the coordinates of the maxima.

    """
    # Reduce the noise in the image.
    filtered = bandpass_filter(image, math.sqrt(2), len_object)

    # Dilate the filtered image to make the size of each spot larger.
    structure = _CreateSEDisk(len_object // 2)
    dilated = cv2.dilate(filtered, structure)

    # Find local maxima.
    binary = (filtered == dilated).astype(numpy.uint8)

    # Threshold to filter out small spots.
    qradius = max(len_object // 4, 1)
    structure = _CreateSEDisk(qradius)
    binary_dilated = cv2.dilate(binary, structure)

    # Identify all spots (features) and then select the amount of spots equal to
    # qty with highest mean value in the filtered image.
    labels, num_features = ndimage.label(binary_dilated)
    index = numpy.arange(1, num_features + 1)
    mean = ndimage.mean(filtered, labels=labels, index=index)
    si = numpy.argsort(mean)[::-1][:qty]
    # Estimate spot center positions.
    pos = numpy.array(ndimage.center_of_mass(filtered, labels=labels, index=index[si]))[:, ::-1]

    if numpy.any(numpy.isnan(pos)):
        pos = pos[numpy.any(~numpy.isnan(pos), axis=1)]
        logging.debug("Only %d maxima found, while expected %d", len(pos), qty)
    # Improve center estimate using radial symmetry method.
    w = len_object // 2
    refined_center = numpy.zeros_like(pos)
    pos = numpy.rint(pos).astype(numpy.int16)
    y_max, x_max = image.shape
    for idx, xy in enumerate(pos):
        x_start, y_start = xy - w + 1
        x_end, y_end = xy + w
        # If the spot is near the edge of the image, crop so it is still in the center of the sub-image. Subtract the
        # value of x/y_start from x/y_end to keep the spot in the center when x/y_start is set to 0. Add the difference
        # between x/y_end and x/y_max to x/y_start to keep the spot in the center when x/y_end is set to x/y_max.
        if x_start < 0:
            x_end += x_start
            x_start = 0
        elif x_end > x_max:
            x_start += x_end - x_max
            x_end = x_max
        if y_start < 0:
            y_end += y_start
            y_start = 0
        elif y_end > y_max:
            y_start += y_end - y_max
            y_end = y_max
        spot = filtered[y_start:y_end, x_start:x_end]
        refined_center[idx] = numpy.array(FindCenterCoordinates(spot))
    refined_position = pos + refined_center
    return refined_position


def _get_subimage(
    image: numpy.ndarray, ji: Tuple[int, int], size: int
) -> numpy.ndarray:
    """
    Returns a square shaped region of interest of the input array `image`
    as a view, where the region of interest is centered at `image[j, i]`.

    Parameters
    ----------
    image : ndarray
        The input image
    ji : tuple of ints (j, i)
        2-dimensional index of the array `image`
    size : int
        Controls the shape of the output subimage.

    Returns
    -------
    out : ndarray
        A view of the input array `image` centered at `ji` of shape
        `(2 * size + 1, 2 * size + 1)`.

    """
    n, m = image.shape
    j, i = ji
    if any((j < size, i < size, j >= n - size, i >= m - size)):
        raise IndexError("Position too close to the edge of the image.")
    return image[(j - size):(j + size + 1), (i - size):(i + size + 1)]


def find_spot_positions(
    image: numpy.ndarray,
    sigma: float,
    threshold_abs: Optional[float] = None,
    threshold_rel: Optional[float] = None,
    num_spots: Optional[int] = None,
) -> numpy.ndarray:
    """
    Find the center coordinates of spots with the highest intensity in an
    image.

    Parameters
    ----------
    image : ndarray
        The input image.
    sigma : float
        Expected size of the spots. Assuming the spots are Gaussian shaped,
        this is the standard deviation.
    threshold_abs : float, optional
        Minimum intensity of peaks. By default, the absolute threshold is
        the minimum intensity of the image.
    threshold_rel : float, optional
        If provided, apply a threshold on the minimum intensity of peaks,
        calculated as `max(image) * threshold_rel`.
    num_spots : int, optional
        Maximum number of spots. When the number of spots exceeds `num_spots`,
        return `num_spots` peaks based on highest spot intensity.

    Returns
    -------
    refined_position : ndarray
        A 2-dimensional array of shape `(N, 2)` containing the coordinates of
        the maxima.

    """
    size = int(round(3 * sigma))
    min_distance = 2 * size
    filtered = bandpass_filter(image, sigma, min_distance)
    coordinates = peak_local_max(
        filtered,
        min_distance=min_distance,
        threshold_abs=threshold_abs,
        threshold_rel=threshold_rel,
        num_peaks=num_spots,
        p_norm=2,
    )

    # Improve coordinate estimate using radial symmetry center.
    refined_coordinates = numpy.empty_like(coordinates, dtype=float)
    for idx, ji in enumerate(coordinates):
        subimg = _get_subimage(filtered, ji, size)
        rsc = radial_symmetry_center(subimg, smoothing=False)
        delta = numpy.subtract(rsc, size)
        refined_coordinates[idx] = ji + delta

    return refined_coordinates


def EstimateLatticeConstant(pos):
    """
    Estimate the lattice constant of a point set that represent a square grid.
    The lattice constant refers to the physical dimension of unit cells in a
    crystal lattice. It is the physical dimension of the smallest repeating
    unit that possesses all the symmetry of the crystal structure.

    It is used here to estimate the physical dimension in i and j of the
    smallest repeating unit of a point set in a square grid.

    Parameters
    ----------
    pos : array like
        A 2D array of shape (N, 2) containing the (x, y) coordinates of the points.
        Where N must be at least 5.

    Returns
    -------
    lattice_constants : array like [2x2]
        The lattice constants. The first row corresponds to the (x, y) direction of the fist lattice constant,
        the second row corresponds to the (x, y) direction of the second lattice constant.

    """
    if len(pos) < 5:
        # Because the lattice constants are based on the 4 nearest neighbours of a point,
        # a total of 5 points is needed to estimate the lattice constants.
        raise ValueError("Need at least 5 positions to estimate the lattice constants")
    # Find the closest 4 neighbours (excluding itself) for each point.
    tree = KDTree(pos)
    dd, ii = tree.query(pos, k=5)
    dr = dd[:, 1:]  # exclude the point itself

    # Determine the median radial distance and filter all points beyond
    # 2*sigma.
    med = numpy.median(dr)
    std = numpy.std(dr)
    outliers = numpy.abs(dr - med) > (2 * std)  # doesn't work well if std is very high

    # Determine horizontal and vertical distance from the point itself to its 4
    # closest neighbours (only radial distance is returned by tree.query).
    dpos = pos[ii[:, 0, numpy.newaxis]] - pos[ii[:, 1:]]
    dx, dy = dpos[:, :, 0], dpos[:, :, 1]
    assert numpy.all(numpy.abs(dr - numpy.hypot(dx, dy)) < 1.0e-12)
    # Stack the x and y directions from a point to its 4 nearest neighbours.
    X = numpy.column_stack((dx[~outliers], dy[~outliers]))
    # Make sure the x and y directions are positive.
    X[X[:, 0] < -0.5 * med] *= -1
    X[X[:, 1] < -0.5 * med] *= -1

    # Use k-means to group the points into the two most common directions.
    centroids, _ = kmeans(X, 2)
    # For each point add a label of which of the two most common directions it belongs to.
    labels = numpy.argmin(cdist(X, centroids), axis=1)
    # Find the median of each of the two most common directions.
    lattice_constants = numpy.array([numpy.median(X[labels.ravel() == 0], axis=0),
                                     numpy.median(X[labels.ravel() == 1], axis=0)])

    # The angle between the two directions should be close to 90 degrees.
    alpha = numpy.math.atan2(numpy.linalg.norm(numpy.cross(*lattice_constants)), numpy.dot(*lattice_constants))
    if not util.rot_almost_equal(alpha, math.pi / 2, math.radians(2.5)):
        logging.warning('Estimated lattice angle differs from 90 degrees by '
                        'more than 2.5 degrees. Input data could be wrong')
    if numpy.linalg.det(lattice_constants) < 0.:
        # A negative determinant means the first axis is rotated 90 degrees CCW compared to the second.
        # Flip to make the lattice constants consistent with the pos input.
        lattice_constants = numpy.flipud(lattice_constants)

    return lattice_constants


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
    xv = numpy.arange(grid_size_x).astype(float) - 0.5 * float(grid_size_x - 1)
    yv = numpy.arange(grid_size_y).astype(float) - 0.5 * float(grid_size_y - 1)
    xx, yy = numpy.meshgrid(xv, yv)
    return numpy.column_stack((xx.ravel(), yy.ravel()))


def bandpass_filter(image, len_noise, len_object):
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
        A length in pixels somewhat larger than a typical object. Must be integer.

    Returns
    -------
    array like
        The filtered image

    """
    image = numpy.asarray(image)

    # Low-pass filter using a Gaussian kernel.
    denoised = ndimage.filters.gaussian_filter(image, len_noise, mode="reflect")

    # Estimate background variations using a boxcar kernel.
    N = 2 * int(round(len_object)) + 1
    background = ndimage.filters.uniform_filter(image, N, mode="reflect")

    if image.dtype.kind == "u":
        # Use cv2.subtract for unsigned integers (no wrap-around).
        return cv2.subtract(denoised, background)
    else:
        return numpy.maximum(denoised - background, 0)
