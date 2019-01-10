# -*- coding: utf-8 -*-
"""
Created on 29 Nov 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

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

import numpy
from numpy.linalg import LinAlgError
import scipy.optimize

from odemis.util.linalg import tri_inv


class Transform(object):
    """
    A Transform class that is useful for geometrical coordinate transformations.
    """

    def __init__(self, rotation=0, scaling=1, shear=0, translation=numpy.zeros(2)):
        self.rotation = rotation
        self.scaling = scaling
        self.shear = shear
        self.translation = translation

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.__dict__)

    @staticmethod
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
        >>> Transform._rotation_matrix_from_angle(numpy.pi / 2.)
        array([[ 0., -1.],
               [ 1.,  0.]])

        """
        ct = numpy.cos(angle)
        st = numpy.sin(angle)
        return numpy.array([(ct, -st),
                            (st, ct)])

    @staticmethod
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
        >>> Transform._rotation_matrix_to_angle(matrix)
        1.5707963267948966

        """
        if len(matrix.shape) != 2:
            raise LinAlgError('%d-dimensional array given. Array must be '
                              'two-dimensional' % len(matrix.shape))
        if max(matrix.shape) != min(matrix.shape):
            raise LinAlgError('Array must be square')
        if matrix.shape != (2, 2):
            raise NotImplementedError('Can only handle 2x2 matrices')
        if not numpy.allclose(numpy.dot(matrix.T, matrix), numpy.eye(2)):
            raise LinAlgError('Matrix is not orthogonal')
        if not numpy.allclose(numpy.linalg.det(matrix), 1.0):
            raise LinAlgError('Matrix is not a proper rotation matrix')
        return numpy.arctan2(matrix[1, 0], matrix[0, 0])

    @staticmethod
    def _optimal_rotation(src, dst):
        """
        Returns the optimal rotation matrix between two zero mean point sets,
        such that |Rx-y|^2 is minimized.

        Parameters
        ----------
        src : list of tuples
            List of coordinates (x, y) with zero mean in the source frame of
            reference.
        dst : list of tuples
            List of coordinates (x, y) with zero mean in the destination frame
            of reference.

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
        >>> src = [(1., 0.), (-1., 0.)]
        >>> dst = [(0., 2.), (0., -2.)]
        >>> Transform._optimal_rotation(src, dst)
        array([[ 0., -1.],
               [ 1.,  0.]])

        """
        assert numpy.allclose(numpy.average(src, axis=0), numpy.zeros(2))
        assert numpy.allclose(numpy.average(dst, axis=0), numpy.zeros(2))
        H = numpy.dot(numpy.transpose(src), dst)
        U, _, V = numpy.linalg.svd(H, full_matrices=False)  # H = USV (not V')
        if numpy.linalg.det(U) * numpy.linalg.det(V) < 0.0:
            U[:, -1] = -U[:, -1]
        return numpy.dot(V.T, U.T)

    @classmethod
    def from_pointset(cls, src, dst, method='affine'):
        """
        Constructor for Transform object that determines the best coordinate
        transformation from two point sets `src` and `dst` in a least-squares
        sense.

        Parameters
        ----------
        src : list of tuples
            Coordinates in the source reference frame.
        dst : list of tuples
            Coordinates in the destination reference frame.
        method : {'rigid', 'similarity', 'scaling', 'affine'}, optional
            Type of transform to fit to the provided point sets.

        Returns
        -------
        tform : Transform
            Object that contains the optimal transform.
        """
        x = numpy.asarray(src)
        y = numpy.asarray(dst)
        x0 = numpy.average(x, axis=0)
        y0 = numpy.average(y, axis=0)
        dx = x - x0
        dy = y - y0
        R = cls._optimal_rotation(dx, dy)
        m = 0.
        if method == 'rigid':
            s = 1.
        else:
            s = (numpy.einsum('ik,jk,ji', R, dx, dy) /
                 numpy.einsum('ij,ij', dx, dx))

        if method in ['scaling', 'affine']:
            # Use the similarity transform as the initial guess to start the
            # search.
            if method == 'scaling':
                p0 = numpy.array([s, s])
            else:  # affine transform
                p0 = numpy.array([s, s, 0.])

            def _fre(p, x, y):
                """
                Return the fiducial registration error (FRE) for a coordinate
                transformation between two zero-mean point sets `x` and `y`
                with given shear (optional) and non-isotropic scaling, and
                optimal rotation.

                Parameters
                ----------
                p : ndarray
                    Transformation parameters. Equal to [sx, sy] for scaling
                    transform, and [sx, sy, m] for affine transform.
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
                SL = numpy.diag(s)
                if len(p) == 3:  # affine transform
                    SL[0, 1] = s[0] * p[2]
                _x = numpy.einsum('ik,jk->ji', SL, x)
                R = cls._optimal_rotation(_x, y)
                delta = numpy.einsum('ik,jk->ji', R, _x) - y
                return delta.ravel()

            # Find the shear (affine transform only) and non-isotropic scaling
            # using an optimization search.
            p, ier = scipy.optimize.leastsq(_fre, p0, args=(dx, dy))
            assert ier in (1, 2, 3, 4)
            s = numpy.abs(p[0:2])
            SL = numpy.diag(s)
            if len(p) == 3:  # affine transform
                SL[0, 1] = s[0] * p[2]
                m = p[2]

            # Rotation is now the rigid transform of the scaled and sheared
            # input
            _x = numpy.einsum('ik,jk->ji', SL, dx)
            R = cls._optimal_rotation(_x, dy)

        SL = s * numpy.eye(2)
        SL[0, 1] = numpy.atleast_1d(s)[0] * m
        A = numpy.dot(R, SL)
        t = y0 - numpy.dot(A, x0)
        tform = cls(scaling=s, shear=m, translation=t)
        tform.rotation_matrix = R
        return tform

    def apply(self, x):
        """
        Apply the coordinate transformation to a (set of) input coordinates.

        Parameters
        ----------
        x : ndarray
            Input coordinates (x, y)

        Returns
        -------
        y : ndarray
            Output coordinates (x, y); same shape as input.

        Examples
        --------
        >>> t = Transform(rotation=(numpy.pi / 2.), scaling=2.0)
        >>> x = numpy.array([1.0, 2.0])
        >>> t.apply(x)
        array([-4.,  2.])

        """
        x = numpy.asarray(x)
        if x.ndim == 1:
            return (numpy.einsum('ik,k->i', self.transformation_matrix, x) +
                    self.translation)
        else:
            return (numpy.einsum('ik,jk->ji', self.transformation_matrix, x) +
                    self.translation)

    def inverse(self):
        """
        Return the inverse transformation.

        Returns
        -------
        tform : Transform
            The inverse transformation.

        Examples
        --------
        >>> t = Transform(rotation=(numpy.pi / 4.), scaling=0.5)
        >>> tinv = t.inverse()
        >>> numpy.dot(t.transformation_matrix, tinv.transformation_matrix)
        array([[ 1., -0.],
               [ 0.,  1.]])

        """
        S = self.scaling * numpy.eye(2)
        L = numpy.array([(1., self.shear), (0., 1.)])
        SL = numpy.dot(S, L)
        Ainv = numpy.dot(tri_inv(SL), numpy.transpose(self.rotation_matrix))
        tinv = -numpy.dot(Ainv, self.translation)
        tform = self.__class__(translation=tinv)
        tform.transformation_matrix = Ainv
        return tform

    @property
    def rotation_matrix(self):
        """The 2x2 rotation matrix as calculated from self.rotation."""
        return self._rotation_matrix_from_angle(self.rotation)

    @rotation_matrix.setter
    def rotation_matrix(self, matrix):
        self.rotation = self._rotation_matrix_to_angle(matrix)

    @property
    def transformation_matrix(self):
        """The 2x2 transformation matrix. Does not include translation."""
        R = self.rotation_matrix
        S = self.scaling * numpy.eye(2)
        L = numpy.array([(1., self.shear), (0., 1.)])
        return numpy.dot(numpy.dot(R, S), L)

    @transformation_matrix.setter
    def transformation_matrix(self, matrix):
        # Use a QR-decomposition to retrieve the rotation and scale-shear
        # matrices. We require the diagonal elements of S to be positive such
        # that the factorization is unique.
        R, S = numpy.linalg.qr(matrix)
        mask = numpy.diag(S) < 0.
        R[:, mask] *= -1.
        S[mask, :] *= -1.
        self.rotation_matrix = R
        self.scaling = numpy.diag(S)
        self.shear = S[0, 1] / S[0, 0]
