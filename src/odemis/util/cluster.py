# -*- encoding: utf-8 -*-
"""
cluster.py : backport of k-means++ initialisation method

The SciPy version available via default package managment of Ubuntu 18.04LT is
v0.19.1, which is quite old and lacks new features. This file backports the
k-means++ initialisation method for scipy.cluster.vq.kmeans2() which is
available from SciPy v1.2.0 onwards. When support for Ubuntu 18.04LT is no
longer required, this file can be removed.

@author: Andries Effting

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


The function `_kpp()` is copied from scipy cluster/vq, which is licensed under
the following terms and conditions:

    Copyright (c) 2001-2002 Enthought, Inc.  2003-2019, SciPy Developers.
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions
    are met:

    1. Redistributions of source code must retain the above copyright
       notice, this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above
       copyright notice, this list of conditions and the following
       disclaimer in the documentation and/or other materials provided
       with the distribution.

    3. Neither the name of the copyright holder nor the names of its
       contributors may be used to endorse or promote products derived
       from this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
    "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
    LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
    A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
    OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
    SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
    LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
    DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
    THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
    OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
import numpy
import scipy.cluster
import scipy.spatial

from odemis.util.random import check_random_state, rng_integers


def _kpp(data, k, rng):
    """
    Picks k points in the data based on the kmeans++ method.

    Parameters
    ----------
    data : ndarray
        Expect a rank 1 or 2 array. Rank 1 is assumed to describe 1-D
        data, rank 2 multidimensional data, in which case one
        row is one observation.
    k : int
        Number of samples to generate.
    rng : `numpy.random.Generator` or `numpy.random.RandomState`
        Random number generator.

    Returns
    -------
    init : ndarray
        A 'k' by 'N' containing the initial centroids.

    References
    ----------
    .. [1] D. Arthur and S. Vassilvitskii, "k-means++: the advantages of
       careful seeding", Proceedings of the Eighteenth Annual ACM-SIAM Symposium
       on Discrete Algorithms, 2007.

    """
    dims = data.shape[1] if len(data.shape) > 1 else 1
    init = numpy.ndarray((k, dims))

    for i in range(k):
        if i == 0:
            init[i, :] = data[rng_integers(rng, data.shape[0])]
        else:
            D2 = scipy.spatial.distance.cdist(
                init[:i, :], data, metric="sqeuclidean"
            ).min(axis=0)
            probs = D2 / D2.sum()
            cumprobs = probs.cumsum()
            r = rng.uniform()
            init[i, :] = data[numpy.searchsorted(cumprobs, r)]
    return init


def kmeans2(
    data,
    k,
    iter=10,
    thresh=1e-5,
    minit="random",
    missing="warn",
    check_finite=True,
    *,
    seed=None
):
    """
    Classify a set of observations into k clusters using the k-means algorithm.

    Allows `minit = "++"` as initialization method: choose k observations
    accordingly to the kmeans++ method (careful seeding).

    For documentation see scipy.cluster.vq.kmeans2

    """
    if minit == "++":
        rng = check_random_state(seed)
        k = _kpp(data, k, rng)
        minit = "matrix"

    return scipy.cluster.vq.kmeans2(data, k, iter, thresh, minit, missing, check_finite)
