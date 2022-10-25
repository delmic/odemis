# -*- coding: utf-8 -*-
"""
Created on 3 Jan 2014

@author: Kimon Tsitsikas

Copyright © 2013-2017 Kimon Tsitsikas, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import numpy
from numpy import fft
from numpy.fft import fftfreq


def _upsampled_dft(data, upsampled_region_size,
                   upsample_factor=1, axis_offsets=None):
    """
    The function is exactly from skimage.registration.

    Upsampled DFT by matrix multiplication.

    This code is intended to provide the same result as if the following
    operations were performed:
        - Embed the array "data" in an array that is ``upsample_factor`` times
          larger in each dimension.  ifftshift to bring the center of the
          image to (1,1).
        - Take the FFT of the larger array.
        - Extract an ``[upsampled_region_size]`` region of the result, starting
          with the ``[axis_offsets+1]`` element.

    It achieves this result by computing the DFT in the output array without
    the need to zeropad. Much faster and memory efficient than the zero-padded
    FFT approach if ``upsampled_region_size`` is much smaller than
    ``data.size * upsample_factor``.


    data (numpy.array): The input data array (DFT of original data) to upsample.
    upsampled_region_size (int or tuple of int, optional): The size of the region to be sampled.
      If one integer is provided, it is duplicated up to the dimensionality of ``data``.
    upsample_factor (int, optional): The upsampling factor.  Defaults to 1.
    axis_offsets (tuple of int, optional): The offsets of the region to be sampled. Defaults to None (uses
      image center)

    returns (numpy.ndarray): The upsampled DFT of the specified region.
    """
    # if people pass in an integer, expand it to a list of equal-sized sections
    if not hasattr(upsampled_region_size, "__iter__"):
        upsampled_region_size = [upsampled_region_size, ] * data.ndim
    else:
        if len(upsampled_region_size) != data.ndim:
            raise ValueError("shape of upsampled region sizes must be equal "
                             "to input data's number of dimensions.")

    if axis_offsets is None:
        axis_offsets = [0, ] * data.ndim
    else:
        if len(axis_offsets) != data.ndim:
            raise ValueError("number of axis offsets must be equal to input "
                             "data's number of dimensions.")

    im2pi = 1j * 2 * numpy.pi

    dim_properties = list(zip(data.shape, upsampled_region_size, axis_offsets))

    for (n_items, ups_size, ax_offset) in dim_properties[::-1]:
        kernel = ((numpy.arange(ups_size) - ax_offset)[:, None]
                  * fftfreq(n_items, upsample_factor))
        kernel = numpy.exp(-im2pi * kernel)
        # use kernel with same precision as the data
        kernel = kernel.astype(data.dtype, copy=False)

        # Equivalent to:
        #   data[i, j, k] = kernel[i, :] @ data[j, k].T
        data = numpy.tensordot(kernel, data, axes=(1, -1))
    return data


def MeasureShift(previous_img, current_img, precision=1):
    """
    The function is taken from skimage.registration._phase_cross_correlation.
    Given two images, it calculates the shift in x and y axis i.e in column-wise and horizontal-wise respectively.
    It first computes the cross-correlation of the two images. Then it modifies cross-correlation values
    by reducing the amplitude of noise and enhancing the shift amplitude. The coordinates
    of the maximum of the cross-correlation define the shift vector between the two images.
    The implementation is based on the "Efficient subpixel image registration by
    cross-correlation" by Manuel Guizar, for the corresponding matlab code see
    http://www.mathworks.com/matlabcentral/fileexchange/
    18401-efficient-subpixel-image-registration-by-cross-correlation.

    For e.g.
    When the current image moves to top left corner relatively to the previous image.
    The returned shift value is positive in both x and y respectively.

    previous_img (numpy.array): 2d array with the previous frame
    current_img (numpy.array): 2d array with the last frame, must be of same
      shape as previous_img
    precision (1<=int): Calculate drift within 1/precision of a pixel
    returns (tuple of floats): Drift in pixels (horizontal, vertical).
    """
    if precision < 1:
        raise ValueError("Precision cannot be less than 1, got %s." % (precision,))
    assert previous_img.shape == current_img.shape, "Prev shape %s != new shape %s" % (
        previous_img.shape, current_img.shape)

    previous_fft = fft.fft2(previous_img)
    current_fft = fft.fft2(current_img)
    shape = previous_fft.shape
    m, n = previous_fft.shape
    image_product = previous_fft * current_fft.conj()

    # Cross-correlation computation
    eps = numpy.finfo(image_product.real.dtype).eps
    # pixel magnitude below 100*eps is magnified whereas values above it are normalized to one
    # this helps in finding low magnitude pixels which are related to small shifts
    image_product /= numpy.maximum(numpy.abs(image_product), 100 * eps)
    float_dtype = image_product.real.dtype
    cross_correlation = fft.ifft2(image_product)
    # Locate maximum
    maxima = numpy.unravel_index(numpy.argmax(numpy.abs(cross_correlation)),
                                 cross_correlation.shape)
    midpoints = numpy.array([numpy.fix(axis_size / 2) for axis_size in shape])

    float_dtype = image_product.real.dtype

    shifts = numpy.stack(maxima).astype(float_dtype, copy=False)
    shifts[shifts > midpoints] -= numpy.array(shape)[shifts > midpoints]

    if precision > 1:
        shifts = numpy.round(shifts * precision) / precision
        upsampled_region_size = numpy.ceil(precision * 1.5)
        # Center of output array at dftshift + 1
        dftshift = numpy.fix(upsampled_region_size / 2.0)
        # Matrix multiply DFT around the current shift estimate
        sample_region_offset = dftshift - shifts * precision
        cross_correlation = _upsampled_dft(image_product.conj(),
                                           upsampled_region_size,
                                           precision,
                                           sample_region_offset).conj()
        # Locate maximum and map back to original pixel grid
        maxima = numpy.unravel_index(numpy.argmax(numpy.abs(cross_correlation)),
                                     cross_correlation.shape)

        maxima = numpy.stack(maxima).astype(float_dtype, copy=False)
        maxima -= dftshift

        shifts += maxima / precision

    return shifts[1], shifts[0]
