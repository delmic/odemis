# -*- coding: utf-8 -*-
"""
Created on 10 Jan 2019

@author: Andries Effting

The function tri_inv() is a modified version of scipy.linalg's
solve_triangular(); available from:

    https://github.com/scipy/scipy/blob/v1.2.0/scipy/linalg/basic.py#L261

and _datacopied() from:

    https://github.com/scipy/scipy/blob/v1.2.0/scipy/linalg/misc.py#L177

Copyright (c) 2001, 2002 Enthought, Inc.
All rights reserved.

Copyright (c) 2003-2019 SciPy Developers.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

  a. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.
  b. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.
  c. Neither the name of Enthought nor the names of the SciPy Developers
     may be used to endorse or promote products derived from this software
     without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDERS OR CONTRIBUTORS
BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF
THE POSSIBILITY OF SUCH DAMAGE.
"""

import numpy
from scipy.linalg.lapack import get_lapack_funcs
from scipy.linalg.misc import LinAlgError

__all__ = ['qrp', 'qlp', 'tri_inv']


# Duplicate from scipy.linalg.misc
def _datacopied(arr, original):
    """
    Strict check for `arr` not sharing any data with `original`,
    under the assumption that arr = asarray(original)

    """
    if arr is original:
        return False
    if not isinstance(original, numpy.ndarray) and hasattr(original, '__array__'):
        return False
    return arr.base is None


def tri_inv(c, lower=False, unit_diagonal=False, overwrite_c=False,
            check_finite=True):
    """
    Compute the inverse of a triangular matrix.

    Parameters
    ----------
    c : array_like
        A triangular matrix to be inverted
    lower : bool, optional
        Use only data contained in the lower triangle of `c`.
        Default is to use upper triangle.
    unit_diagonal : bool, optional
        If True, diagonal elements of `c` are assumed to be 1 and
        will not be referenced.
    overwrite_c : bool, optional
        Allow overwriting data in `c` (may improve performance).
    check_finite : bool, optional
        Whether to check that the input matrix contains only finite numbers.
        Disabling may give a performance gain, but may result in problems
        (crashes, non-termination) if the inputs do contain infinities or NaNs.

    Returns
    -------
    inv_c : ndarray
        Inverse of the matrix `c`.

    Raises
    ------
    LinAlgError
        If `c` is singular
    ValueError
        If `c` is not square, or not 2-dimensional.

    Examples
    --------
    >>> c = numpy.array([(1., 2.), (0., 4.)])
    >>> tri_inv(c)
    array([[ 1.  , -0.5 ],
           [ 0.  ,  0.25]])
    >>> numpy.dot(c, tri_inv(c))
    array([[ 1.,  0.],
           [ 0.,  1.]])

    """
    if check_finite:
        c1 = numpy.asarray_chkfinite(c)
    else:
        c1 = numpy.asarray(c)
    if len(c1.shape) != 2 or c1.shape[0] != c1.shape[1]:
        raise ValueError('expected square matrix')
    overwrite_c = overwrite_c or _datacopied(c1, c)
    trtri, = get_lapack_funcs(('trtri',), (c1,))
    inv_c, info = trtri(c1, overwrite_c=overwrite_c, lower=lower,
                        unitdiag=unit_diagonal)
    if info > 0:
        raise LinAlgError("singular matrix")
    if info < 0:
        raise ValueError("illegal value in %d-th argument of internal trtri" %
                         -info)
    return inv_c


def qrp(a, mode='reduced'):
    """
    Compute the qr factorization of a matrix.

    Factor the matrix `a` as *qr*, where `q` is orthonormal and `r` is
    upper-triangular. The diagonal entries of `r` are nonnegative.

    For documentation see numpy.linalg.qr

    """
    q, r = numpy.linalg.qr(a, mode)
    mask = numpy.diag(r) < 0.
    q[:, mask] *= -1.
    r[mask, :] *= -1.
    return q, r


def qlp(a, mode='reduced'):
    """
    Compute the ql factorization of a matrix.

    Factor the matrix `a` as *ql*, where `q` is orthonormal and `l` is
    lower-triangular. The diagonal entries of `l` are nonnegative.

    For documentation see numpy.linalg.qr

    """
    q, r = qrp(numpy.flip(a), mode)
    return numpy.flip(q), numpy.flip(r)
