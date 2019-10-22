# -*- coding: utf-8 -*-
"""
Created on 10 Jan 2019

@author: Andries Effting

This file is part of Odemis.

This file is inspired by skimage.transform, which is licences under the
following terms and conditions:

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


The methods of calculating the geometric transformations is different from
skimage.transform and is based on the algorithms listed in [1]. The algorithm
ensures that the resulting transfomation only includes proper rotations, i.e.
physical transformations of a rigid object.

The code can be extended to include weighted estimations and/or to support
3-dimensional transformations without changes to the algorithm.

.. [1] Beutel, J., Kundel, H. L., Van Metter, R. L., & Fitzpatrick, J. M.
   (2000). Handbook of Medical Imaging: Medical image processing and analysis
   (Vol. 2). Spie Press.

"""

from __future__ import division

from abc import ABCMeta, abstractmethod

import numpy
import scipy.optimize
from future.utils import with_metaclass
from numpy.linalg import LinAlgError

from odemis.util.linalg import qrp, tri_inv


def _assertRotationMatrix(matrix):
    """
    Check if a matrix is a rotation matrix.

    Parameters
    ----------
    matrix : array_like
        The matrix to check.

    Raises
    ------
    LinAlgError
        If the supplied matrix is not a rotation matrix.

    """
    if matrix.ndim != 2:
        raise LinAlgError('%d-dimensional array given. Array must be '
                          'two-dimensional' % matrix.ndim)
    m, n = matrix.shape
    if m != n:
        raise LinAlgError('Array must be square')
    if not numpy.allclose(numpy.dot(matrix.T, matrix), numpy.eye(n)):
        raise LinAlgError('Matrix is not orthogonal')
    if not numpy.allclose(numpy.linalg.det(matrix), 1.0):
        raise LinAlgError('Matrix is not a proper rotation matrix')


def _rotation_matrix_from_angle(angle):
    """
    Returns the 2x2 rotation matrix for a given angle.

    Parameters
    ----------
    angle : float
        Rotation angle in radians.

    Returns
    -------
    matrix : 2x2 array
        Rotation matrix.

    Examples
    --------
    >>> _rotation_matrix_from_angle(numpy.pi / 2.)
    array([[ 0., -1.],
           [ 1.,  0.]])

    """
    ct = numpy.cos(angle)
    st = numpy.sin(angle)
    return numpy.array([(ct, -st),
                        (st, ct)])


def _rotation_matrix_to_angle(matrix):
    """
    Returns the angle for a given 2x2 rotation matrix.

    Parameters
    ----------
    matrix : 2x2 array
        Rotation matrix.

    Returns
    ------
    angle : float
        Rotation angle in radians.

    Raises
    ------
    LinAlgError
        If the supplied matrix is not a rotation matrix.
    NotImplementedError
        If the supplied matrix is not of size 2x2.

    Examples
    --------
    >>> matrix = numpy.array([(0., -1.), (1., 0.)])
    >>> _rotation_matrix_to_angle(matrix)
    1.5707963267948966

    """
    _assertRotationMatrix(matrix)
    if matrix.shape != (2, 2):
        raise NotImplementedError('Can only handle 2x2 matrices')
    return numpy.arctan2(matrix[1, 0], matrix[0, 0])


def _optimal_rotation(x, y):
    """
    Returns the optimal rotation matrix between two zero mean point sets,
    such that |Rx-y|^2 is minimized.

    Parameters
    ----------
    x : list of tuples
        List of coordinates with zero mean in the source frame of reference.
    y : list of tuples
        List of coordinates with zero mean in the destination frame of
        reference.

    Returns
    -------
    matrix : 2x2 array
        Rotation matrix.

    Raises
    ------
    AssertionError
        If either the source or destination point set is not zero mean.

    Examples
    --------
    >>> x = [(1., 0.), (-1., 0.)]
    >>> y = [(0., 2.), (0., -2.)]
    >>> _optimal_rotation(x, y)
    array([[ 0., -1.],
           [ 1.,  0.]])

    """
    assert numpy.allclose(numpy.average(x, axis=0), numpy.zeros(2))
    assert numpy.allclose(numpy.average(y, axis=0), numpy.zeros(2))
    H = numpy.dot(numpy.transpose(x), y)
    U, _, V = numpy.linalg.svd(H, full_matrices=False)  # H = USV (not V')
    if numpy.linalg.det(U) * numpy.linalg.det(V) < 0.0:
        U[:, -1] = -U[:, -1]
    return numpy.dot(V.T, U.T)


class GeometricTransform(with_metaclass(ABCMeta, object)):
    """Base class for geometric transformations."""

    def __call__(self, x):
        """
        Apply the forward transformation to a (set of) input coordinates.

        Parameters
        ----------
        x : ndarray
            Input coordinates

        Returns
        -------
        y : ndarray
            Output coordinates; same shape as input.

        """
        x = numpy.asarray(x)
        if x.ndim == 1:
            return (numpy.einsum('ik,k->i', self.transformation_matrix, x) +
                    self.translation)
        else:
            return (numpy.einsum('ik,jk->ji', self.transformation_matrix, x) +
                    self.translation)

    @abstractmethod
    def inverse(self):
        """
        Return the inverse transformation.

        Returns:
        --------
        tform : GeometricTransform
            The inverse transformation.

        """
        raise NotImplementedError()

    @property
    @abstractmethod
    def transformation_matrix(self):
        pass


class RigidTransform(GeometricTransform):
    """
    Rigid transformation.

    A rigid transformation is a geometrical transformation that preserves all
    distances. A rigid transformation also preserves the straightness of lines
    (and the planarity of surfaces) and all nonzero angles between straight
    lines.

    The rigid transform has the following form:

        y = Rx + t

    where `R` is an orthogonal matrix and `t` is a translation vector. To
    eliminate improper rotations (reflections) it is required that the
    determinant of `R` equals 1.

    Parameters
    ----------
    matrix : (2, 2) array, optional
        Transformation matrix, does not include translation.
    rotation : float, optional
        Rotation angle in counter-clockwise direction as radians.
    translation : (tx, ty) as array, list, or tuple, optional
        x, y translation parameters.

    Attributes
    ----------
    rotation_matrix
    transformation_matrix
    rotation : float
        Rotation.
    translation : (2,) array
        Translation vector.

    """

    def __init__(self, matrix=None, rotation=None, translation=None):
        params = any(param is not None
                     for param in (rotation,))

        if params and matrix is not None:
            raise ValueError("You cannot specify the transformation matrix "
                             "and the implicit parameters at the same time.")
        elif matrix is not None:
            self.transformation_matrix = matrix
        else:
            self.rotation = 0. if rotation is None else rotation

        self.translation = (0., 0.) if translation is None else translation

    @classmethod
    def from_pointset(cls, x, y):
        """
        Estimate the transformation from a set of corresponding points.

        Constructor for RigidTransform that determines the best coordinate
        transformation from two point sets `x` and `y` in a least-squares
        sense.

        Parameters
        ----------
        x : (n, 2) array
            Coordinates in the source reference frame.
        y : (n, 2) array
            Coordinates in the destination reference frame. Must be of same
            dimensions as `x`.

        Returns
        -------
        tform : RigidTransform
            Optimal coordinate transformation.

        """
        x = numpy.asarray(x)
        y = numpy.asarray(y)
        x0 = numpy.mean(x, axis=0)
        y0 = numpy.mean(y, axis=0)
        dx = x - x0
        dy = y - y0
        R = _optimal_rotation(dx, dy)
        t = y0 - numpy.dot(R, x0)
        return cls(matrix=R, translation=t)

    def inverse(self):
        """
        Return the inverse transformation.

        Returns
        -------
        tform : RigidTransform
            The inverse transformation.

        """
        Rinv = numpy.transpose(self.rotation_matrix)  # R is orthogonal
        tinv = -numpy.dot(Rinv, self.translation)
        return self.__class__(matrix=Rinv, translation=tinv)

    @property
    def rotation_matrix(self):
        """The 2x2 rotation matrix as calculated from self.rotation."""
        return _rotation_matrix_from_angle(self.rotation)

    @rotation_matrix.setter
    def rotation_matrix(self, matrix):
        self.rotation = _rotation_matrix_to_angle(matrix)

    @property
    def transformation_matrix(self):
        """The 2x2 transformation matrix. Does not include translation."""
        return self.rotation_matrix

    @transformation_matrix.setter
    def transformation_matrix(self, matrix):
        self.rotation_matrix = matrix


class SimilarityTransform(RigidTransform):
    """
    Similarity transform.

    A similarity transform is rigid except for isotropic scaling.

    The similarity transform has the following form:

        y = sRx + t

    where `s` is a positive scalar, `R` is an orthogonal matrix, and `t` is a
    translation vector. To eliminate improper rotations (reflections) is
    required that the determinant of `R` equals 1.

    Parameters
    ----------
    matrix : (2, 2) array, optional
        Transformation matrix, does not include translation.
    rotation : float, optional
        Rotation angle in counter-clockwise direction as radians.
    scale : float, optional
        Scale factor.
    translation : (tx, ty) as array, list, or tuple, optional
        x, y translation parameters.

    Attributes
    ----------
    rotation_matrix
    transformation_matrix
    rotation : float
        Rotation.
    scale : float
        Scale factor.
    translation : (2,) array
        Translation vector.

    """

    def __init__(self, matrix=None, rotation=None, scale=None,
                 translation=None):
        params = any(param is not None
                     for param in (rotation, scale))

        if params and matrix is not None:
            raise ValueError("You cannot specify the transformation matrix "
                             "and the implicit parameters at the same time.")
        elif matrix is not None:
            self.transformation_matrix = matrix
        else:
            self.rotation = 0. if rotation is None else rotation
            self.scale = 1. if scale is None else scale

        self.translation = (0., 0.) if translation is None else translation

    @classmethod
    def from_pointset(cls, x, y):
        """
        Estimate the transformation from a set of corresponding points.

        Constructor for SimilarityTransform that determines the best coordinate
        transformation from two point sets `x` and `y` in a least-squares
        sense.

        Parameters
        ----------
        x : (n, 2) array
            Coordinates in the source reference frame.
        y : (n, 2) array
            Coordinates in the destination reference frame. Must be of same
            dimensions as `x`.

        Returns
        -------
        tform : SimilarityTransform
            Optimal coordinate transformation.

        """
        x = numpy.asarray(x)
        y = numpy.asarray(y)
        x0 = numpy.mean(x, axis=0)
        y0 = numpy.mean(y, axis=0)
        dx = x - x0
        dy = y - y0
        R = _optimal_rotation(dx, dy)
        s = numpy.einsum('ik,jk,ji', R, dx, dy) / numpy.einsum('ij,ij', dx, dx)
        A = s * R
        t = y0 - numpy.dot(A, x0)
        return cls(matrix=A, translation=t)

    def inverse(self):
        """
        Return the inverse transformation.

        Returns
        -------
        tform : SimilarityTransform
            The inverse transformation.

        """
        Rinv = numpy.transpose(self.rotation_matrix)  # R is orthogonal
        sinv = 1. / self.scale
        Ainv = sinv * Rinv
        tinv = -numpy.dot(Ainv, self.translation)
        return self.__class__(matrix=Ainv, translation=tinv)

    @property
    def transformation_matrix(self):
        """The 2x2 transformation matrix. Does not include translation."""
        A = self.scale * self.rotation_matrix
        return A

    @transformation_matrix.setter
    def transformation_matrix(self, matrix):
        s = numpy.sqrt(numpy.linalg.det(matrix))
        R = matrix / s
        self.rotation_matrix = R
        self.scale = s


class ScalingTransform(RigidTransform):
    """
    Scaling transform.

    A scaling transform is rigid except for scaling. If the scaling is
    isotropic it is called a similarity transform.

    The scaling transform has the following form:

        y = RSx + t

    where `R` is an orthogonal matrix, `S` is a diagonal matrix whose elements
    represent scale factors along the coordinate axis, and `t` is a translation
    vector. To eliminate improper rotations (reflections) it is required that
    the determinant of `R` equals 1.

    NOTE: `RS` is not in general equal to `SR`; so these are two different
          classes of transformations.

    Parameters
    ----------
    matrix : (2, 2) array, optional
        Transformation matrix, does not include translation.
    rotation : float, optional
        Rotation angle in counter-clockwise direction as radians.
    scale : (sx, sy) as array, list, or tuple, optional
        x, y scale factors.
    translation : (tx, ty) as array, list, or tuple, optional
        x, y translation parameters.

    Attributes
    ----------
    rotation_matrix
    transformation_matrix
    rotation : float
        Rotation.
    scale : (sx, sy) as array
        Scale factors.
    translation : (2,) array
        Translation vector.

    """

    def __init__(self, matrix=None, rotation=None, scale=None,
                 translation=None):
        params = any(param is not None
                     for param in (rotation, scale))

        if params and matrix is not None:
            raise ValueError("You cannot specify the transformation matrix "
                             "and the implicit parameters at the same time.")
        elif matrix is not None:
            self.transformation_matrix = matrix
        else:
            self.rotation = 0. if rotation is None else rotation
            self.scale = numpy.ones(2) if scale is None else scale

        self.translation = (0., 0.) if translation is None else translation

    @classmethod
    def from_pointset(cls, x, y):
        """
        Estimate the transformation from a set of corresponding points.

        Constructor for ScalingTransform that determines the best coordinate
        transformation from two point sets `x` and `y` in a least-squares
        sense.

        Parameters
        ----------
        x : (n, 2) array
            Coordinates in the source reference frame.
        y : (n, 2) array
            Coordinates in the destination reference frame. Must be of same
            dimensions as `x`.

        Returns
        -------
        tform : ScalingTransform
            Optimal coordinate transformation.

        """
        x = numpy.asarray(x)
        y = numpy.asarray(y)
        x0 = numpy.mean(x, axis=0)
        y0 = numpy.mean(y, axis=0)
        dx = x - x0
        dy = y - y0
        R = _optimal_rotation(dx, dy)
        # Use the similarity transform as initial guess to start the search.
        sx = sy = numpy.einsum('ik,jk,ji', R, dx, dy) / numpy.einsum('ij,ij', dx, dx)

        def _fre(s, x, y):
            """
            Return the fiducial registration error (FRE) for a scaling
            transformation between two zero-mean point sets `x` and `y` with
            given non-isotropic scaling, and optimal rotation.

            Parameters
            ----------
            s : ndarray
                Non-isotropic scaling, equal to [sx, sy].
            x : ndarray
                Zero-mean coordinates in the source reference frame.
            y : ndarray
                Zero-mean coordinates in the destination reference frame.

            Returns
            -------
            delta : ndarray
                The fiducial registration error as a flattened array.
            """
            # scipy.optimize.leastsq does not support bounds; therefore we
            # perform the search using the absolute value of the scaling to
            # ensure s > 0.
            s = numpy.abs(s)
            _x = s * x
            R = _optimal_rotation(_x, y)
            delta = numpy.einsum('ik,jk->ji', R, _x) - y
            return delta.ravel()

        # Find the non-isotropic scaling using an optimization search.
        s, ier = scipy.optimize.leastsq(_fre, x0=(sx, sy), args=(dx, dy))
        assert ier in (1, 2, 3, 4)

        # Rotation is now the rigid transform of the scaled input
        R = _optimal_rotation(s * dx, dy)
        A = R * s
        t = y0 - numpy.dot(A, x0)
        return cls(matrix=A, translation=t)

    @property
    def transformation_matrix(self):
        """The 2x2 transformation matrix. Does not include translation."""
        A = self.rotation_matrix * self.scale
        return A

    @transformation_matrix.setter
    def transformation_matrix(self, matrix):
        R, S = qrp(matrix)
        s = numpy.diag(S)
        if not numpy.allclose(S, numpy.diag(s)):
            raise LinAlgError('Array must be diagonal')
        self.rotation_matrix = R
        self.scale = s

    def inverse(self):
        """
        Return the inverse transformation.

        Returns
        -------
        tform : AffineTransform
            The inverse transformation.

        Note
        ----
        The inverse transformation of a scaling transform is an affine
        transformation.

        """
        sinv = 1. / self.scale  # S is diagonal
        Ainv = numpy.transpose(sinv * self.rotation_matrix)
        tinv = -numpy.dot(Ainv, self.translation)
        return AffineTransform(matrix=Ainv, translation=tinv)


class AffineTransform(RigidTransform):
    """
    Affine transform

    An affine transformation preserves the straightness of lines, and hence,
    the planarity of surfaces, and it preserves parallelism, but it allows
    angles between lines to change.

    The affine transform has the following form:

        x' = RSLx + t

    where `R` is an orthogonal matrix, `S` is a diagonal matrix whose elements
    represent scale factors along the coordinate axis, `L` is an upper
    triangular matrix with all diagonal elements equal to one and a single
    off-diagonal non-zero element, and `t` is a translation vector. To
    eliminate improper rotations (reflections) it is required that the
    determinant of `R` equals 1.

    Parameters
    ----------
    matrix : (2, 2) array, optional
        Transformation matrix, does not include translation.
    rotation : float, optional
        Rotation angle in counter-clockwise direction as radians.
    scale : (sx, sy) as array, list, or tuple, optional
        x, y scale factors.
    shear : float, optional
        Shear factor.
    translation : (tx, ty) as array, list, or tuple, optional
        x, y translation parameters.

    Attributes
    ----------
    rotation_matrix
    transformation_matrix
    rotation : float
        Rotation.
    scale : (sx, sy) as array
        Scale factors.
    shear : float
        Shear factor.
    translation : (2,) array
        Translation vector.

    """

    def __init__(self, matrix=None, rotation=None, scale=None, shear=None,
                 translation=None):
        params = any(param is not None
                     for param in (rotation, scale, shear))

        if params and matrix is not None:
            raise ValueError("You cannot specify the transformation matrix "
                             "and the implicit parameters at the same time.")
        elif matrix is not None:
            self.transformation_matrix = matrix
        else:
            self.rotation = 0. if rotation is None else rotation
            self.scale = (1., 1.) if scale is None else scale
            self.shear = 0. if shear is None else shear

        self.translation = (0., 0.) if translation is None else translation

    @classmethod
    def from_pointset(cls, x, y):
        """
        Estimate the transformation from a set of corresponding points.

        Constructor for AffineTransform that determines the best coordinate
        transformation from two point sets `x` and `y` in a least-squares
        sense.

        Parameters
        ----------
        x : (n, 2) array
            Coordinates in the source reference frame.
        y : (n, 2) array
            Coordinates in the destination reference frame. Must be of same
            dimensions as `x`.

        Returns
        -------
        tform : RigidTransform
            Optimal coordinate transformation.

        """
        x = numpy.asarray(x)
        y = numpy.asarray(y)
        x0 = numpy.mean(x, axis=0)
        y0 = numpy.mean(y, axis=0)
        dx = x - x0
        dy = y - y0
        R = _optimal_rotation(dx, dy)
        # Use the similarity transform as initial guess to start the search.
        sx = sy = numpy.einsum('ik,jk,ji', R, dx, dy) / numpy.einsum('ij,ij', dx, dx)

        def _fre(p, x, y):
            """
            Return the fiducial registration error (FRE) for an affine
            transformation between two zero-mean point sets `x` and `y` with
            given non-isotropic scaling, shear, and optimal rotation.

            Parameters
            ----------
            p : ndarray
                Non-isotropic scaling and shear, equal to [sx, sy, m].
            x : ndarray
                Zero-mean coordinates in the source reference frame.
            y : ndarray
                Zero-mean coordinates in the destination reference frame.

            Returns
            -------
            delta : ndarray
                The fiducial registration error as a flattened array.
            """
            # scipy.optimize.leastsq does not support bounds; therefore we
            # perform the search using the absolute value of the scaling to
            # ensure s > 0.
            s = numpy.abs(p[0:2])
            m = p[2]
            SL = numpy.diag(s)
            SL[0, 1] = m * s[0]
            _x = numpy.einsum('ik,jk->ji', SL, x)
            R = _optimal_rotation(_x, y)
            delta = numpy.einsum('ik,jk->ji', R, _x) - y
            return delta.ravel()

        # Find the shear and non-isotropic scaling using an optimization
        # search.
        p, ier = scipy.optimize.leastsq(_fre, x0=(sx, sy, 0.), args=(dx, dy))
        assert ier in (1, 2, 3, 4)
        s = numpy.abs(p[0:2])
        m = p[2]
        SL = numpy.diag(s)
        SL[0, 1] = m * s[0]

        # Rotation is now the rigid transform of the scaled input
        _x = numpy.einsum('ik,jk->ji', SL, dx)
        R = _optimal_rotation(_x, dy)
        A = numpy.dot(R, SL)
        t = y0 - numpy.dot(A, x0)
        return cls(matrix=A, translation=t)

    @property
    def transformation_matrix(self):
        """The 2x2 transformation matrix. Does not include translation."""
        R = self.rotation_matrix
        S = self.scale * numpy.eye(2)
        L = numpy.array([(1., self.shear), (0., 1.)])
        return numpy.dot(numpy.dot(R, S), L)

    @transformation_matrix.setter
    def transformation_matrix(self, matrix):
        R, S = qrp(matrix)
        self.rotation_matrix = R
        self.scale = numpy.diag(S)
        self.shear = S[0, 1] / S[0, 0]

    def inverse(self):
        """
        Return the inverse transformation.

        Returns
        -------
        tform : AffineTransform
            The inverse transformation.

        """
        S = self.scale * numpy.eye(2)
        L = numpy.array([(1., self.shear), (0., 1.)])
        SL = numpy.dot(S, L)
        Ainv = numpy.dot(tri_inv(SL), numpy.transpose(self.rotation_matrix))
        tinv = -numpy.dot(Ainv, self.translation)
        return self.__class__(matrix=Ainv, translation=tinv)
