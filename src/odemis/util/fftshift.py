# -*- encoding: utf-8 -*-
"""
Created on 15 Apr 2021

@author: Andries Effting

Copyright © 2021 Andries Effting, Delmic

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

import numpy as np


def rfftshift2(a, shift):
    """
    Compute the 2-dimensional shifted data for real input.

    This function computes the circular shifted copy of any 2-dimensional real
    array by means of the dicrete Fast Fourier Transform.

    Parameters
    ----------
    a : array_like
        Input array, real.
    shift :
        The shift to be applied.

    Returns
    -------
    out : ndarray
        The shifted image.

    """
    a = np.asarray(a)
    n, m = a.shape
    dj, di = shift
    kj = np.exp(-2j * np.pi * dj * np.fft.fftfreq(n)).reshape(n, 1)
    ki = np.exp(-2j * np.pi * di * np.fft.rfftfreq(m))
    # Force conjugate symmetry. Otherwise these frequency components have no
    # corresponding negative frequency to cancel out the imaginary part.
    if (n % 2) == 0:
        kj[n // 2] = np.real(kj[n // 2])
    if (m % 2) == 0:
        ki[-1] = np.real(ki[-1])
    a = np.fft.irfft2(kj * ki * np.fft.rfft2(a), s=(n, m))
    return a
