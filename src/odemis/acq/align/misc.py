#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import division, print_function, absolute_import

import numpy as np
from scipy.linalg.lapack import get_lapack_funcs
from scipy.linalg.misc import LinAlgError, _datacopied

__all__ = ['tri_inv']


def tri_inv(c, lower=False, unit_diagonal=False, overwrite_c=False,
            check_finite=True):
    """
    Compute the inverse of a triangular matrix

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
    >>> c = np.array([(1., 2.), (0., 4.)])
    >>> tri_inv(c)
    array([[ 1.  , -0.5 ],
           [ 0.  ,  0.25]])
    >>> np.dot(c, tri_inv(c))
    array([[ 1.,  0.],
           [ 0.,  1.]])

    """
    if check_finite:
        c1 = np.asarray_chkfinite(c)
    else:
        c1 = np.asarray(c)
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
