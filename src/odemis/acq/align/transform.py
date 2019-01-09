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

import math
import numpy as np
from numpy.linalg import LinAlgError
import scipy.optimize

from odemis.acq.align.misc import tri_inv


def CalculateTransform(optical_coordinates, electron_coordinates, skew=False):
    """
    Returns the translation, scaling and rotation for the optical and electron image coordinates.
    optical_coordinates (List of tuples): Coordinates of spots in optical image
    electron_coordinates (List of tuples): Coordinates of spots in electron image
    skew (boolean): If True, also compute scaling ratio and shear
    returns translation (Tuple of 2 floats),
            scaling (Tuple of 2 floats),
            rotation (Float): Transformation parameters
            shear (Float):
    """
    # Create numpy arrays out of the coordinate lists
    optical_array = np.array(optical_coordinates)
    electron_array = np.array(electron_coordinates)

    # Make matrix X
    list_len = len(electron_coordinates)  # We assume that both lists have the same length
    if optical_array.shape[0] != list_len:
        raise ValueError("Mismatch between the number of expected and found coordinates.")

    if skew is False:
        x_array = np.zeros(shape=(2 * list_len, 4))
        x_array[0:list_len, 2].fill(1)
        x_array[0:list_len, 0:2] = optical_array
        x_array[list_len:2 * list_len, 3].fill(1)
        x_array[list_len:2 * list_len, 0] = optical_array[:, 1]
        x_array[list_len:2 * list_len, 1] = -optical_array[:, 0]

        # Make matrix U
        u_array = np.zeros(shape=(2 * list_len, 1))
        u_array[0: list_len, 0] = electron_array[:, 0]
        u_array[list_len: 2 * list_len, 0] = electron_array[:, 1]

        # Calculate matrix R, R = X\U
        r_array, resid, rank, s = np.linalg.lstsq(x_array, u_array)
        # if r_array[1][0] == 0:
        #    r_array[1][0] = 1
        translation_x = -r_array[2][0]
        translation_y = -r_array[3][0]
        scaling_x = 1 / math.sqrt((r_array[1][0] ** 2) + (r_array[0][0] ** 2))
        scaling_y = 1 / math.sqrt((r_array[1][0] ** 2) + (r_array[0][0] ** 2))
        rotation = math.atan2(-r_array[1][0], r_array[0][0])

        return (translation_x, translation_y), (scaling_x, scaling_y), rotation
    else:
        # Calculate including shear
        x_array = np.zeros(shape=(list_len, 3))
        x_array[0:list_len, 2].fill(1)
        x_array[0:list_len, 0:2] = optical_array

        # Make matrix U
        u_array = electron_array

        # We know that X*T=U
        t_inv, resid, rank, s = np.linalg.lstsq(x_array, u_array)
        translation_xy = t_inv[2, :]
        theta = math.atan2(t_inv[1, 0], t_inv[1, 1])
        scaling_x = t_inv[0, 0] * math.cos(theta) - t_inv[0, 1] * math.sin(theta)
        scaling_y = math.sqrt(math.pow(t_inv[1, 0], 2) + math.pow(t_inv[1, 1], 2))
        shear = (t_inv[0, 0] * math.sin(theta) + t_inv[0, 1] * math.cos(theta)) / scaling_x

        # change values for return values
        translation_xy_ret = -translation_xy
        scaling_ret = (1 / scaling_x + 1 / scaling_y) / 2
        theta_ret = -theta
        scaling_xy_ret = (1 / scaling_x) / scaling_ret - 1
        shear_ret = -shear

        return (translation_xy_ret[0], translation_xy_ret[1]), (scaling_ret, scaling_ret), theta_ret, scaling_xy_ret, shear_ret


class Transform(object):
    """
    A Transform class that is useful for geometrical coordinate transformations.
    """

    def __init__(self, **kwargs):
        self.rotation = 0.
        self.scaling = 1.
        self.shear = 0.
        self.translation = np.zeros(2)
        for key, value in kwargs.iteritems():
            setattr(self, key, value)

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
        >>> Transform._rotation_matrix_from_angle(np.pi / 2.)
        array([[ 0., -1.],
               [ 1.,  0.]])

        """
        ct = np.cos(angle)
        st = np.sin(angle)
        return np.array([(ct, -st),
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
        >>> matrix = np.array([(0., -1.), (1., 0.)])
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
        if not np.allclose(np.dot(matrix.T, matrix), np.eye(2)):
            raise LinAlgError('Matrix is not orthogonal')
        if not np.allclose(np.linalg.det(matrix), 1.0):
            raise LinAlgError('Matrix is not a proper rotation matrix')
        return np.arctan2(matrix[1, 0], matrix[0, 0])

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
        assert np.allclose(np.average(src, axis=0), np.zeros(2))
        assert np.allclose(np.average(dst, axis=0), np.zeros(2))
        H = np.dot(np.transpose(src), dst)
        U, _, V = np.linalg.svd(H, full_matrices=False)  # H = USV (not V')
        if np.linalg.det(U) * np.linalg.det(V) < 0.0:
            U[:, -1] = -U[:, -1]
        return np.dot(V.T, U.T)

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
        x = np.asarray(src)
        y = np.asarray(dst)
        x0 = np.average(x, axis=0)
        y0 = np.average(y, axis=0)
        dx = x - x0
        dy = y - y0
        R = cls._optimal_rotation(dx, dy)
        m = 0.
        if method == 'rigid':
            s = 1.
        else:
            s = (np.einsum('ik,jk,ji', R, dx, dy) /
                 np.einsum('ij,ij', dx, dx))

        if method in ['scaling', 'affine']:
            # Use the similarity transform as the initial guess to start the
            # search.
            if method == 'scaling':
                p0 = np.array([s, s])
            else:  # affine transform
                p0 = np.array([s, s, 0.])

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
                s = np.abs(p[0:2])
                SL = np.diag(s)
                if len(p) == 3:  # affine transform
                    SL[0, 1] = s[0] * p[2]
                _x = np.einsum('ik,jk->ji', SL, x)
                R = cls._optimal_rotation(_x, y)
                delta = np.einsum('ik,jk->ji', R, _x) - y
                return delta.ravel()

            # Find the shear (affine transform only) and non-isotropic scaling
            # using an optimization search.
            p, ier = scipy.optimize.leastsq(_fre, p0, args=(dx, dy))
            assert ier in (1, 2, 3, 4)
            s = np.abs(p[0:2])
            SL = np.diag(s)
            if len(p) == 3:  # affine transform
                SL[0, 1] = s[0] * p[2]
                m = p[2]

            # Rotation is now the rigid transform of the scaled and sheared
            # input
            _x = np.einsum('ik,jk->ji', SL, dx)
            R = cls._optimal_rotation(_x, dy)

        SL = s * np.eye(2)
        SL[0, 1] = np.atleast_1d(s)[0] * m
        A = np.dot(R, SL)
        t = y0 - np.dot(A, x0)
        return cls(rotation_matrix=R, scaling=s, shear=m, translation=t)

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
        >>> t = Transform(rotation=(np.pi / 2.), scaling=2.0)
        >>> x = np.array([1.0, 2.0])
        >>> t.apply(x)
        array([-4.,  2.])

        """
        x = np.asarray(x)
        if x.ndim == 1:
            return (np.einsum('ik,k->i', self.transformation_matrix, x) +
                    self.translation)
        else:
            return (np.einsum('ik,jk->ji', self.transformation_matrix, x) +
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
        >>> t = Transform(rotation=(np.pi / 4.), scaling=0.5)
        >>> tinv = t.inverse()
        >>> np.dot(t.transformation_matrix, tinv.transformation_matrix)
        array([[ 1., -0.],
               [ 0.,  1.]])

        """
        S = self.scaling * np.eye(2)
        L = np.array([(1., self.shear), (0., 1.)])
        SL = np.dot(S, L)
        Ainv = np.dot(tri_inv(SL), np.transpose(self.rotation_matrix))
        tinv = -np.dot(Ainv, self.translation)
        return self.__class__(transformation_matrix=Ainv, translation=tinv)

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
        S = self.scaling * np.eye(2)
        L = np.array([(1., self.shear), (0., 1.)])
        return np.dot(np.dot(R, S), L)

    @transformation_matrix.setter
    def transformation_matrix(self, matrix):
        # Use a QR-decomposition to retrieve the rotation and scale-shear
        # matrices. We require the diagonal elements of S to be positive such
        # that the factorization is unique.
        R, S = np.linalg.qr(matrix)
        mask = np.diag(S) < 0.
        R[:, mask] *= -1.
        S[mask, :] *= -1.
        self.rotation_matrix = R
        self.scaling = np.diag(S)
        self.shear = S[0, 1] / S[0, 0]

