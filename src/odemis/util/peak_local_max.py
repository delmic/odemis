# -*- encoding: utf-8 -*-
"""
Created on 26 Jul 2021

@author: Andries Effting

The functions below are copied from skimage._shared.coord and
skimage.feature.peak, with minor modification for use in Odemis.

The following copyright notice applies:

Copyright (C) 2019, the scikit-image team
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in
    the documentation and/or other materials provided with the
    distribution.
 3. Neither the name of skimage nor the names of its contributors may be
    used to endorse or promote products derived from this software without
    specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

"""

from warnings import warn
from typing import Optional, Set, Tuple, Union

import numpy as np
import scipy.ndimage as ndi
from scipy.spatial import cKDTree, distance


def _ensure_spacing(
    coord: np.ndarray, spacing: float, p_norm: float, max_out: Optional[int]
) -> np.ndarray:
    """
    Returns a subset of coord where a minimum spacing is guaranteed.

    Parameters
    ----------
    coord : ndarray
        The coordinates of the considered points.
    spacing : float
        The maximum allowed spacing between the points.
    p_norm : float
        Which Minkowski p-norm to use. Should be in the range [1, inf].
        A finite large p may cause a ValueError if overflow can occur.
        ``inf`` corresponds to the Chebyshev distance and 2 to the
        Euclidean distance.
    max_out: int
        If not None, at most the first ``max_out`` candidates are
        returned.

    Returns
    -------
    output : ndarray
        A subset of coord where a minimum spacing is guaranteed.

    """
    # Use KDtree to find the peaks that are too close to each other
    tree = cKDTree(coord)

    indices = tree.query_ball_point(coord, r=spacing, p=p_norm)
    rejected_peaks_indices: Set[int] = set()
    naccepted = 0
    for idx, candidates in enumerate(indices):
        if idx not in rejected_peaks_indices:
            # keep current point and the points at exactly spacing from it
            candidates.remove(idx)
            dist = distance.cdist(
                [coord[idx]], coord[candidates], distance.minkowski, p=p_norm
            ).reshape(-1)
            candidates = [c for c, d in zip(candidates, dist) if d < spacing]

            # candidates.remove(keep)
            rejected_peaks_indices.update(candidates)
            naccepted += 1
            if max_out is not None and naccepted >= max_out:
                break

    # Remove the peaks that are too close to each other
    output = np.delete(coord, tuple(rejected_peaks_indices), axis=0)
    if max_out is not None:
        output = output[:max_out]

    return output


def ensure_spacing(
    coords: np.ndarray,
    spacing: float = 1,
    p_norm: float = np.inf,
    min_split_size: Optional[int] = 50,
    max_out: Optional[int] = None,
) -> np.ndarray:
    """
    Returns a subset of coord where a minimum spacing is guaranteed.

    Parameters
    ----------
    coords : array_like
        The coordinates of the considered points.
    spacing : float
        The maximum allowed spacing between the points.
    p_norm : float
        Which Minkowski p-norm to use. Should be in the range [1, inf].
        A finite large p may cause a ValueError if overflow can occur.
        ``inf`` corresponds to the Chebyshev distance and 2 to the
        Euclidean distance.
    min_split_size : int
        Minimum split size used to process ``coord`` by batch to save
        memory. If None, the memory saving strategy is not applied.
    max_out : int
        If not None, only the first ``max_out`` candidates are returned.

    Returns
    -------
    output : array_like
        A subset of coord where a minimum spacing is guaranteed.

    """
    output = coords
    if len(coords):
        coords = np.atleast_2d(coords)
        if min_split_size is None:
            batch_list = [coords]
        else:
            coord_count = len(coords)
            split_count = int(np.log2(coord_count / min_split_size)) + 1
            split_idx = np.cumsum(
                [coord_count // (2 ** i) for i in range(1, split_count)]
            )
            batch_list = np.array_split(coords, split_idx)

        output = np.zeros((0, coords.shape[1]), dtype=coords.dtype)
        for batch in batch_list:
            output = _ensure_spacing(
                np.vstack([output, batch]), spacing, p_norm, max_out
            )
            if max_out is not None and len(output) >= max_out:
                break

    return output


def _get_high_intensity_peaks(
    image: np.ndarray,
    mask: np.ndarray,
    num_peaks: Optional[int],
    min_distance: float,
    p_norm: float,
) -> np.ndarray:
    """
    Return the coordinates of the peaks with the highest intensity.

    Parameters
    ----------
    image : ndarray
        Input image.
    mask : ndarray
        Binary array defining the region of `image` to evaluate.
    num_peaks : int, optional
        Maximum number of peaks. When the number of peaks exceeds `num_peaks`,
        return `num_peaks` peaks based on highest peak intensity. If
        `num_peaks` is None, return all peaks.
    min_distance : float
        The maximum allowed spacing between the points.
    p_norm : float
        Which Minkowski p-norm to use. Should be in the range [1, inf].
        A finite large p may cause a ValueError if overflow can occur.
        ``inf`` corresponds to the Chebyshev distance and 2 to the
        Euclidean distance.

    Returns
    -------
    coord : ndarray

    """
    # Get coordinates of peaks
    idx = np.nonzero(mask)
    intensities = image[idx]
    # Highest peak first
    idx_maxsort = np.argsort(-intensities)
    coord = np.column_stack(idx)[idx_maxsort]
    coord = ensure_spacing(
        coord, spacing=min_distance, p_norm=p_norm, max_out=num_peaks
    )

    if num_peaks is not None:
        if len(coord) > num_peaks:
            coord = coord[:num_peaks]

    return coord


def _get_peak_mask(
    image: np.ndarray, footprint: np.ndarray, threshold: float
) -> np.ndarray:
    """
    Return the mask containing all peak candidates above thresholds.

    Parameters
    ----------
    image : ndarray
        Input image.
    footprint : ndarray
        Array that specifies the local region within which to search for peaks
        at every point in `image`.
    threshold : ndarray
        Minimum intensity of peaks.

    Returns
    -------
    out : ndarray

    """
    if footprint.size == 1 or image.size == 1:
        return image > threshold

    image_max = ndi.maximum_filter(image, footprint=footprint, mode="constant")

    out = image == image_max

    # no peak for a trivial image
    if np.all(out):
        out[:] = False

    out &= image > threshold
    return out


def _exclude_border(label: np.ndarray, border_width: Tuple[int, ...]) -> np.ndarray:
    """
    Set label border values to 0.

    Parameters
    ----------
    label : ndarray
    border_width : tuple of ints

    Returns
    -------
    label : ndarray

    """
    # zero out label borders
    for i, width in enumerate(border_width):
        if width == 0:
            continue
        label[(slice(None),) * i + (slice(None, width),)] = 0
        label[(slice(None),) * i + (slice(-width, None),)] = 0
    return label


def _get_threshold(
    image: np.ndarray, threshold_abs: Optional[float], threshold_rel: Optional[float]
) -> float:
    """
    Return the threshold value according to an absolute and a relative value.

    Parameters
    ----------
    image : ndarray
        Input image.
    threshold_abs : float, optional
        Minimum intensity of peaks. By default, the absolute threshold is the
        minimum intensity of the image.
    threshold_rel : float, optional
        Minimum intensity of peaks, calculated as `max(image) * threshold_rel`.

    Returns
    -------
    threshold : float

    """
    threshold = threshold_abs if threshold_abs is not None else image.min()

    if threshold_rel is not None:
        threshold = max(threshold, threshold_rel * image.max())

    return threshold


def _get_excluded_border_width(
    image: np.ndarray, min_distance: int, exclude_border: Union[bool, int, Tuple[int]]
) -> Tuple[int, ...]:
    """
    Return border_width values relative to a min_distance if requested.

    Parameters
    ----------
    image : ndarray
        Input image.
    min_distance : int
        The minimal allowed distance separating peaks.
    exclude_border : int, tuple of ints, or bool
        If positive integer, use `exclude_border` for all dimensions.
        If tuple of non-negative ints, check that the length of the tuple
        matches the input array's dimensionality.
        If True, takes the `min_distance` parameter as value for all
        dimensions.
        If zero or False, return tuple of zeros.

    Returns
    -------
    border_width : tuple of ints, same length as the dimensionality of `image`.

    """

    if isinstance(exclude_border, bool):
        border_width = (min_distance if exclude_border else 0,) * image.ndim
    elif isinstance(exclude_border, int):
        if exclude_border < 0:
            raise ValueError("`exclude_border` cannot be a negative value")
        border_width = (exclude_border,) * image.ndim
    elif isinstance(exclude_border, tuple):
        if len(exclude_border) != image.ndim:
            raise ValueError(
                "`exclude_border` should have the same length as the "
                "dimensionality of the image."
            )
        for exclude in exclude_border:
            if not isinstance(exclude, int):
                raise ValueError(
                    "`exclude_border`, when expressed as a tuple, must only "
                    "contain ints."
                )
            if exclude < 0:
                raise ValueError("`exclude_border` can not be a negative value")
        border_width = exclude_border
    else:
        raise TypeError(
            "`exclude_border` must be bool, int, or tuple with the same "
            "length as the dimensionality of the image."
        )

    return border_width


def peak_local_max(
    image: np.ndarray,
    min_distance: int = 1,
    threshold_abs: Optional[float] = None,
    threshold_rel: Optional[float] = None,
    exclude_border: bool = True,
    num_peaks: Optional[int] = None,
    footprint: Optional[np.ndarray] = None,
    p_norm: float = np.inf,
) -> np.ndarray:
    """
    Find peaks in an image as coordinate list.

    Peaks are the local maxima in a region of `2 * min_distance + 1`
    (i.e. peaks are separated by at least `min_distance`).
    If both `threshold_abs` and `threshold_rel` are provided, the maximum
    of the two is chosen as the minimum intensity threshold of peaks.

    Parameters
    ----------
    image : ndarray
        Input image.
    min_distance : int, optional
        The minimal allowed distance separating peaks. To find the
        maximum number of peaks, use `min_distance=1`.
    threshold_abs : float, optional
        Minimum intensity of peaks. By default, the absolute threshold is
        the minimum intensity of the image.
    threshold_rel : float, optional
        Minimum intensity of peaks, calculated as `max(image) * threshold_rel`.
    exclude_border : int, tuple of ints, or bool, optional
        If positive integer, `exclude_border` excludes peaks from within
        `exclude_border`-pixels of the border of the image.
        If tuple of non-negative ints, the length of the tuple must match the
        input array's dimensionality.  Each element of the tuple will exclude
        peaks from within `exclude_border`-pixels of the border of the image
        along that dimension.
        If True, takes the `min_distance` parameter as value.
        If zero or False, peaks are identified regardless of their distance
        from the border.
    num_peaks : int, optional
        Maximum number of peaks. When the number of peaks exceeds `num_peaks`,
        return `num_peaks` peaks based on highest peak intensity.
    footprint : ndarray of bools, optional
        If provided, `footprint == 1` represents the local region within which
        to search for peaks at every point in `image`.
    p_norm : float
        Which Minkowski p-norm to use. Should be in the range [1, inf].
        A finite large p may cause a ValueError if overflow can occur.
        ``inf`` corresponds to the Chebyshev distance and 2 to the
        Euclidean distance.

    Returns
    -------
    output : ndarray or ndarray of bools
        (row, column, ...) coordinates of peaks.

    Notes
    -----
    The peak local maximum function returns the coordinates of local peaks
    (maxima) in an image. Internally, a maximum filter is used for finding local
    maxima. This operation dilates the original image. After comparison of the
    dilated and original image, this function returns the coordinates of the
    peaks where the dilated image equals the original image.

    Examples
    --------
    >>> img1 = np.zeros((7, 7))
    >>> img1[3, 4] = 1
    >>> img1[3, 2] = 1.5
    >>> img1
    array([[0. , 0. , 0. , 0. , 0. , 0. , 0. ],
           [0. , 0. , 0. , 0. , 0. , 0. , 0. ],
           [0. , 0. , 0. , 0. , 0. , 0. , 0. ],
           [0. , 0. , 1.5, 0. , 1. , 0. , 0. ],
           [0. , 0. , 0. , 0. , 0. , 0. , 0. ],
           [0. , 0. , 0. , 0. , 0. , 0. , 0. ],
           [0. , 0. , 0. , 0. , 0. , 0. , 0. ]])
    >>> peak_local_max(img1, min_distance=1)
    array([[3, 2],
           [3, 4]])
    >>> peak_local_max(img1, min_distance=2)
    array([[3, 2]])
    >>> img2 = np.zeros((20, 20, 20))
    >>> img2[10, 10, 10] = 1
    >>> img2[15, 15, 15] = 1
    >>> peak_idx = peak_local_max(img2, exclude_border=0)
    >>> peak_idx
    array([[10, 10, 10],
           [15, 15, 15]])
    >>> peak_mask = np.zeros_like(img2, dtype=bool)
    >>> peak_mask[tuple(peak_idx.T)] = True
    >>> np.argwhere(peak_mask)
    array([[10, 10, 10],
           [15, 15, 15]])

    """
    if (footprint is None or footprint.size == 1) and min_distance < 1:
        warn(
            "When min_distance < 1, peak_local_max acts as finding "
            "image > max(threshold_abs, threshold_rel * max(image)).",
            RuntimeWarning,
            stacklevel=2,
        )

    border_width = _get_excluded_border_width(image, min_distance, exclude_border)

    threshold = _get_threshold(image, threshold_abs, threshold_rel)

    if footprint is None:
        size = 2 * min_distance + 1
        footprint = np.ones((size,) * image.ndim, dtype=bool)
    else:
        footprint = np.asarray(footprint)

    # Non maximum filter
    mask = _get_peak_mask(image, footprint, threshold)

    mask = _exclude_border(mask, border_width)

    # Select highest intensities (num_peaks)
    coordinates = _get_high_intensity_peaks(
        image, mask, num_peaks, min_distance, p_norm
    )

    return coordinates
