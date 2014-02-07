# -*- coding: utf-8 -*-
"""
Created on 3 Jan 2014

@author: kimon

Copyright © 2013-2014 Éric Piel & Kimon Tsitsikas, Delmic

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

from __future__ import division

import logging
import numpy
import math

from numpy import arange
from numpy import fft

def CalculateDrift(previous_img, current_img, precision):
    """
    Given two images, it calculates the drift in x and y axis. It first computes
    the cross-correlation of the two images and then locates the peak. The coordinates 
    of the peak of the cross-correlation define the shift vector between the two images. 
    previous_img (numpy.array): 2d array with the previous frame
    current_img (numpy.array): 2d array with the last frame
    precision (int): Calculate drift within 1/precision of a pixel
    returns (tuple of floats): Drift in pixels
    """
    cor_precision = precision
    if precision < 1:
        cor_precision = 1
        logging.warning("Precision cannot be less than 1. It is now reset to " + str(cor_precision) + ".")

    previous_fft = fft.fft2(previous_img)
    current_fft = fft.fft2(current_img)

    (m, n) = previous_fft.shape

    if cor_precision == 1:

        # Cross-correlation computation
        CC = fft.ifft2(previous_fft * (current_fft.conj()))

        # Locate the peak
        max1, loc1 = abs(CC).max(0), abs(CC).argmax(0)
        max2, loc2 = abs(max1).max(0), abs(max1).argmax(0)

        rloc = loc1[loc2]
        cloc = loc2

        # Calculate shift from the peak
        md2 = numpy.fix(m / 2)
        nd2 = numpy.fix(n / 2)
        if rloc > md2:
            row_shift = rloc - m
        else:
            row_shift = rloc

        if cloc > nd2:
            col_shift = cloc - n
        else:
            col_shift = cloc

    else:
        mlarge = m * 2
        nlarge = n * 2

        # Upsample by factor of 2 to obtain initial estimation and
        # embed Fourier data in a 2x larger array
        CC_not_complex = numpy.zeros((mlarge, nlarge))
        CC = CC_not_complex + 0j
        CC[m - numpy.fix(m / 2):m + 1 + numpy.fix((m - 1) / 2), n - numpy.fix(n / 2):n + 1 + numpy.fix((n - 1) / 2)] = \
        											fft.fftshift(previous_fft) * ((fft.fftshift(current_fft)).conj())

        # Cross-correlation computation
        CC = fft.ifft2(fft.ifftshift(CC))

        # Locate the peak
        max1, loc1 = abs(CC).max(0), abs(CC).argmax(0)
        max2, loc2 = abs(max1).max(0), abs(max1).argmax(0)

        rloc = loc1[loc2]
        cloc = loc2
       
        # Calculate shift in previous pixel grid from the position of the peak
        (m, n) = CC.shape
        md2 = numpy.fix(m / 2)
        nd2 = numpy.fix(n / 2)

        if rloc > md2:
            row_shift = rloc - m
        else:
            row_shift = rloc

        if cloc > nd2:
            col_shift = cloc - n
        else:
            col_shift = cloc

        row_shift = row_shift / 2
        col_shift = col_shift / 2

        # DFT computation
        # Initial shift estimation in upsampled grid
        row_shift = numpy.round(row_shift * cor_precision) / cor_precision
        col_shift = numpy.round(col_shift * cor_precision) / cor_precision
        dft_shift = numpy.fix(numpy.ceil(cor_precision * 1.5) / 2)  # Center of output at dft_shift+1

        # Matrix multiply DFT around the current shift estimation
        CC = (_UpsampledDFT(current_fft * (previous_fft.conj()), numpy.ceil(cor_precision * 1.5), numpy.ceil(cor_precision * 1.5),
                            cor_precision, dft_shift - row_shift * cor_precision, dft_shift - col_shift * cor_precision)).conj() \
                            / (md2 * nd2 * (cor_precision ** 2))

        # Locate maximum and map back to original pixel grid
        max1, loc1 = abs(CC).max(0), abs(CC).argmax(0)
        max2, loc2 = abs(max1).max(0), abs(max1).argmax(0)

        rloc = loc1[loc2]
        cloc = loc2

        rloc = rloc - dft_shift
        cloc = cloc - dft_shift

        row_shift = row_shift + rloc / cor_precision
        col_shift = col_shift + cloc / cor_precision

        if md2 == 1:
            row_shift = 0
        if nd2 == 1:
            col_shift = 0

    return row_shift, col_shift


def _UpsampledDFT(input, nor, noc, precision=1, roff=0, coff=0):
    """
    Upsampled DFT by matrix multiplies. 
    input (numpy.array): 2d array 
    nor, noc (ints): Number of pixels in the output upsampled DFT, in units
    of upsampled pixels
    precision (int): Calculate drift within 1/precision of a pixel
    roff, coff (ints): Row and column offsets, allow to shift the output array
                    to a region of interest on the DFT 
    returns (tuple of floats): Drift in pixels
    """
    z = 1j  # imaginary unit
    nr, nc = input.shape

    # Compute kernels and obtain DFT by matrix products
    kernc = numpy.power(math.e, (-z * 2 * math.pi / (nc * precision)) * ((fft.ifftshift((arange(0, nc)))[:, None]).transpose() \
                                - numpy.floor(nc / 2)) * (arange(0, noc) - coff)[:, None])

    kernr = numpy.power(math.e, (-z * 2 * math.pi / (nr * precision)) * ((fft.ifftshift(arange(0, nr)))[:, None] \
                                - numpy.floor(nr / 2)) * ((arange(0, nor)[:, None]).transpose() - roff))

    ret = numpy.dot(numpy.dot((kernr.transpose()), input), kernc.transpose())

    return ret

