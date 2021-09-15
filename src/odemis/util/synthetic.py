# -*- encoding: utf-8 -*-
"""
synthetic.py : To obtain the characteristic size of the point spread function
(PSF) of a microscope system, and to generate simulated images containing one
or multiple spots (PSF's).

Copyright (C) 2021  Andries Effting, Delmic

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
USA.

"""
import math
from typing import List, Tuple, Union

import numpy


Shape2D = Tuple[int, int]
Coordinate = Tuple[float, float]
CoordinateList = List[Coordinate]


UINT16_MAX = numpy.iinfo(numpy.uint16).max


def psf_sigma_wffm(
    refractive_index: float, numerical_aperture: float, wavelength: float
) -> float:
    """
    Calculate the Gaussian approximation of a wide field fluorescence
    microscope point spread function.

    Parameters
    ----------
    refractive_index : float, >= 1
        Refractive index
    numerical_aperture: float, positive
        Numerical aperture of the optical system
    wavelength : float
        Wavelength.

    Returns
    -------
    sigma : float
        The standard deviation of the Gaussian approximation of a fluorescence
        microscope point spread function. Same units as `wavelength`.

    References
    ----------
    .. [1] Zhang, B., Zerubia, J., & Olivo-Marin, J. C. (2007). Gaussian
    approximations of fluorescence microscope point-spread function models.
    Applied optics, 46(10), 1819-1829.

    """
    if refractive_index < 1:
        raise ValueError("The refractive index should be greater than or equal to 1.")
    if numerical_aperture <= 0:
        raise ValueError("The numerical aperture should be positive.")
    if wavelength <= 0:
        raise ValueError("The wavelength should be positive.")
    if numerical_aperture >= refractive_index:
        raise ValueError(
            "The numerical aperture should be less than the refractive index."
        )

    k = 2 * math.pi / wavelength
    nk = refractive_index * k
    sa = numerical_aperture / refractive_index
    ca = math.sqrt(1 - sa ** 2)
    t = ca ** 1.5
    sigma = 1 / (nk * math.sqrt((4 - 7 * t + 3 * ca ** 3.5) / (7 * (1 - t))))
    return sigma


def psf_gaussian(
    shape: Shape2D, loc: Union[Coordinate, CoordinateList], sigma: float
) -> numpy.ndarray:
    """
    Return a synthetic spot image of a point-spread function (PSF) approximated
    by a 2-dimensional Gaussian function.

    Parameters
    ----------
    shape : tuple of ints
        Shape of the array, e.g. ``(9, 9)``.
    loc : tuple of floats, or list of tuple of floats
        Position of the maximum in pixel coordinates `(j0, i0)` relative to the
        center of the spot image.
    sigma : float, positive
        Standard deviation of the Gaussian.

    Returns
    -------
    image : ndarray, dtype=numpy.uint16
        Array with the image of the point spread function with the given shape
        and size and at the given location.

    """
    if sigma <= 0:
        raise ValueError("sigma should be positive")

    n, m = shape
    j = numpy.arange(n, dtype=numpy.float64)
    i = numpy.arange(m, dtype=numpy.float64)
    out = numpy.zeros((n, m), dtype=numpy.float64)
    for j0, i0 in numpy.atleast_2d(loc):
        kj = numpy.exp(-0.5 * numpy.square((j - j0) / sigma))
        ki = numpy.exp(-0.5 * numpy.square((i - i0) / sigma))
        out += numpy.outer(kj, ki)

    # convert to uint16
    numpy.clip(out, 0, 1, out=out)
    numpy.rint(UINT16_MAX * out, out=out)
    return out.astype(numpy.uint16)
