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
from future.utils import with_metaclass
import numbers
import numpy
import scipy.optimize
import warnings
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


def to_physical_space(ji, shape=None, pixel_size=None):
    """
    Converts an image pixel index into a coordinate in physical space.

    Images are displayed on a computer screen with the origin in the top-left
    corner. The position of a pixel is described by a pixel index `(j, i)`,
    where `j` is the row-index and `i` is the column-index. The index `(0, 0)`
    is located at the center of the top-left pixel. Note that the index does
    not need to be integer, i.e. the pixel index `(-0.5, -0.5)` is at the
    top-left corner of the image.

    A position in physical space is typically given in coordinates `(x, y)`.
    The x-axis is aligned with the columns of the image, and the y-axis with
    the rows. The direction of the y-axis is opposite to the row-index, such
    that it is increasing in the upward direction.

    `to_physical_space` converts an image pixel index into a coordinate in
    physical space. The origin is moved to the center of the image when
    provided with the shape of the image. Note that if the image has an even
    amount of rows and/or columns, the origin will be at the boundary between
    two pixels and the physical coordinates will be non-integer. If the shape
    of the image is not provided the origin is not altered. This is useful when
    converting a relative displacement (shift) in pixels.


              Image Pixel Indices                   Physical Coordinates

      o--> i
      | +---------+---------+---------+        +---------+---------+---------+
    j V |         |         |         |        |         |         |         |
        |  (0,0)  |  (0,1)  |  (0,2)  |        | (-1, 1) |  (0, 1) |  (1, 1) |
        |         |         |         |        |         |         |         |
        +---------+---------+---------+        +---------+---------+---------+
        |         |         |         |        |         |         |         |
        |  (1,0)  |  (1,1)  |  (1,2)  |        | (-1, 0) |  (0, 0) |  (1, 0) |
        |         |         |         |        |         |         |         |
        +---------+---------+---------+        +---------+---------+---------+
        |         |         |         |        |         |         |         |
        |  (2,0)  |  (2,1)  |  (2,2)  |        | (-1,-1) |  (0,-1) |  (1,-1) |
        |         |         |         |    y ^ |         |         |         |
        +---------+---------+---------+      | +---------+---------+---------+
                                             o--> x

    Parameters
    ----------
    ji : tuple, list of tuples, ndarray
        Pixel index, list of indices, or array of indices. For each index the
        first entry is the row-index `j` and the second entry is the
        column-index `i`.
    shape : tuple of ints (optional)
        Shape of the image. The first entry is the number of rows, the second
        entry is the number of columns in the image. Used to move the origin to
        the center of the image. If not provided the origin is not altered.
    pixel_size : tuple of 2 floats, float (optional)
        Pixel size in (x, y). For square pixels, a single float can be
        provided.

    Returns
    -------
    xy : ndarray
        Physical coordinates. Same shape as `ji`. For each coordinate the first
        entry is the x-coordinate and the second entry is the y-coordinate.

    Raises
    ------
    IndexError
        If either the index is negative or out-of-range.
    ValueError
        If the index is not 2-dimensional.

    Examples
    --------
    >>> ji = (0, 0)
    >>> shape = (8, 5)
    >>> to_physical_space(ji, shape)
    array([-2. ,  3.5])

    """
    ji = numpy.asarray(ji)

    if ji.shape[-1] != 2:
        raise ValueError("Indices must be 2-dimensional.")

    xy = numpy.empty(ji.shape, dtype=float)
    xy[..., 0] = ji[..., 1]   # map column-index `i` to x-axis
    xy[..., 1] = -ji[..., 0]  # map row-index `j` to y-axis

    if shape:
        # Move the origin to the center of the image.
        n, m = shape
        xy[..., 0] -= 0.5 * (m - 1)
        xy[..., 1] += 0.5 * (n - 1)

    if pixel_size is not None:
        xy *= pixel_size

    return xy


def to_pixel_index(xy, shape=None, pixel_size=None):
    """
    Converts a coordinate in physical space into an image pixel index.

    Inverse of `to_physical_space`. The function `to_pixel_index` converts a
    coordinate in physical space into an image pixel index. The columns of the
    image are aligned with the x-axis, and the rows with the y-axis. The
    direction of the y-axis is opposite to the row-index. The origin of the
    image pixel index is moved to the center of the top-left pixel when
    provided with the shape of the image. If the shape of the image is not
    provided the origin is not altered. This is useful when converting a
    relative displacement (shift).

    Parameters
    ----------
    xy : tuple, list of tuples, ndarray
        Physical coordinates, list of coordinates, or array of coordinates. For
        each coordinate the first entry is the `x`-coordinate and the second
        entry is the `y`-coordinate.
    shape : tuple of ints (optional)
        Shape of the image. The first entry is the number of rows, the second
        entry is the number of columns in the image. If not provided the origin
        is not altered.
    pixel_size : tuple of 2 floats, float (optional)
        Pixel size in (x, y). For square pixels, a single float can be
        provided. If not specified, a pixel size of 1 is used.

    Returns
    -------
    ji : ndarray
        Pixel indices. Same shape as `xy`. For each index the first entry is
        the row-index `j` and the second entry is the column-index `i`. Note
        that the pixel indices are returned as floats in order to support
        sub-pixel resolution.

    Raises
    ------
    ValueError
        If the coordinates are not 2-dimensional.

    Examples
    --------
    >>> xy = (-2., 3.5)
    >>> shape = (8, 5)
    >>> to_pixel_index(xy, shape)
    array([ 0. ,  0.])

    """
    if pixel_size is not None:
        xy = numpy.array(xy, copy=True, dtype=float)
        xy /= pixel_size
    else:
        xy = numpy.asarray(xy, dtype=float)

    if xy.shape[-1] != 2:
        raise ValueError("Coordinates must be 2-dimensional.")

    ji = numpy.empty(xy.shape, dtype=float)
    ji[..., 0] = -xy[..., 1]  # map y-axis to row-index `j`
    ji[..., 1] = xy[..., 0]   # map x-axis to column-index `i`

    if shape:
        n, m = shape
        ji[..., 0] += 0.5 * (n - 1)
        ji[..., 1] += 0.5 * (m - 1)

    return ji


class GeometricTransform(with_metaclass(ABCMeta, object)):
    """Base class for geometric transformations."""

    def __init__(self, matrix=None, **kwargs):
        """
        Basic initialisation helper.
        Note: if rotation, scale, shear, translation are not passed, the respective
         attributes will *not* be set. If they are set to None, the attributes will
         be set to default values.
        matrix : (2, 2) array, optional
            Transformation matrix, does not include translation.
            passing it will set .transformation_matrix
        rotation : float, optional
            Rotation angle in counter-clockwise direction as radians.
        scale : (sx, sy) as array, list, or tuple, optional
            x, y scale factors.
        shear : float, optional
            Shear factor.
        translation : (tx, ty) as array, list, or tuple, optional
            x, y translation parameters.
        """
        params = any(kwargs.get(param) is not None
                     for param in ("rotation", "scale", "shear"))

        if params and matrix is not None:
            raise ValueError("You cannot specify the transformation matrix "
                             "and the implicit parameters at the same time.")
        elif matrix is not None:
            matrix = numpy.asarray(matrix)
            if matrix.shape != (2, 2):
                raise ValueError("Transformation matrix should be 2x2, but got %s" % (matrix,))
            self.transformation_matrix = matrix
        else:
            if "rotation" in kwargs:
                rotation = kwargs.get("rotation")
                self.rotation = 0 if rotation is None else rotation
                if not isinstance(self.rotation, numbers.Real):
                    raise ValueError("Rotation should be a number, but got %s" % (self.rotation,))

            if "scale" in kwargs:
                scale = kwargs.get("scale")
                self.scale = (1, 1) if scale is None else scale
                if len(self.scale) != 2 or not all(isinstance(a, numbers.Real) for a in self.scale):
                    raise ValueError("Scale should be 2 floats, but got %s" % (self.scale,))

            if "shear" in kwargs:
                shear = kwargs.get("shear")
                self.shear = 0 if shear is None else shear
                if not isinstance(self.shear, numbers.Real):
                    raise ValueError("Shear should be a number, but got %s" % (self.shear,))

        if "translation" in kwargs:
            translation = kwargs.get("translation")
            self.translation = (0, 0) if translation is None else translation
            if len(self.translation) != 2 or not all(isinstance(a, numbers.Real) for a in self.translation):
                raise ValueError("Translation should be 2 floats, but got %s" % (self.translation,))

    def apply(self, x):
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
        return numpy.einsum('ik,...k->...i', self.transformation_matrix, x) + self.translation

    def __call__(self, x):
        warnings.warn("__call__ is deprecated, use apply instead",
                      DeprecationWarning)
        return self.apply(x)
    __call__.__doc__ = apply.__doc__

    def fre(self, x, y):
        """
        Returns the RMS value of the fiducial error registration (FRE).

        When estimating a coordinate transformation given a set of matching
        source and destination coordinates, due to fiducial localization error
        (FLE) it will typically not be possible to achieve perfect alignment.
        This resulting misalignment can be used to assess whether or not the
        registration was successful. Note that the FRE is not a good indicator
        of the accuracy of a registration.

        Parameters
        ----------
        x : (n, 2) array
            Coordinates in the source reference frame.
        y : (n, 2) array
            Coordinates in the destination reference frame. Must be of same
            dimensions as `x`.

        Returns
        -------
        fre : float, non-negative
            The root mean squared fiducial registration error. A smaller number
            indicates a better fit.

        """
        x = numpy.asarray(x)
        y = numpy.asarray(y)
        delta = self.apply(x) - y
        fre = numpy.sqrt(numpy.mean(delta * delta))
        return fre

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
        GeometricTransform.__init__(self, matrix=matrix, rotation=rotation,
                                    translation=translation)

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

    def __init__(self, matrix=None, rotation=None, scale=None, translation=None):
        GeometricTransform.__init__(self, matrix=matrix, rotation=rotation,
                                    translation=translation)

        # It's special as .scale is a single float, instead of 2 floats typically
        if matrix is not None:
            if scale is not None:
                raise ValueError("You cannot specify the transformation matrix "
                                 "and the implicit parameters at the same time.")
        else:
            self.scale = 1 if scale is None else scale
            if not isinstance(self.scale, numbers.Real):
                raise ValueError("Scale should be a single number, but got %s" % (self.scale,))

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

    def __init__(self, matrix=None, rotation=None, scale=None, translation=None):
        GeometricTransform.__init__(self, matrix=matrix, rotation=rotation,
                                    scale=scale, translation=translation)

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
        sinv = 1 / numpy.asarray(self.scale)  # S is diagonal
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
        GeometricTransform.__init__(self, matrix=matrix, rotation=rotation,
                                    scale=scale, shear=shear, translation=translation)

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


class AnamorphosisTransform(AffineTransform):
    """
    AnamorphosisTransform

    The anamorphosis transform is a polynomial model of the distortions in an
    electron optical system, and is described in more detail in [1].

    The anamorphosis transform has the following form:

        wᵢ = A + B₁ w + B₂ w̄ + C₁ w² + C₂ ww̄ + C₃ w̄² +
                                       D₁ w²w̄ + D₂ ww̄² + E₁ w³w̄² + E₂ w²w̄³,

    where A, B₁, B₂, C₁, C₂, C₃, D₁, D₂, E₁, and E₂ are the (complex)
    coefficients of the transform. The in- and output coordinates are the
    complex numbers w = x + j*y. Note that w̄ is the complex conjugate of w. The
    coefficients A, B₁, B₂ define an ordinary affine transform, and the other
    coefficients determine the higher order distortions.

    Parameters
    ----------
    coeffs : Transform coefficients as array, list, or tuple; optional.
        List of transform coefficients: A, B₁, B₂, C₁, C₂, C₃, D₁, D₂, E₁, E₂.
    rotation : float, optional
        Rotation angle in counter-clockwise direction as radians.
    scale : (sx, sy) as array, list, or tuple, optional
        x, y scale factors.
    shear : float, optional
        Shear factor.
    nlcoeffs : Non-linear transform coefficients as array, list, or tuple;
               optional.
        List of non-linear transform coefficients: C₁, C₂, C₃, D₁, D₂, E₁, E₂.
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
    coeffs : tuple of 7 floats
        Tuple of transform coefficients.
    nlcoeffs : tuple
        Tuple of non-linear transform coefficients.
    translation : (2,) array
        Translation vector.

    References
    ----------
    .. [1] J. Stopka, "Polynomial fit for Multi-beam distortions", internal
    note, 2019

    """

    def __init__(self, coeffs=None, rotation=None, scale=None, shear=None,
                 nlcoeffs=None, translation=None):

        params = any(param is not None
                     for param in (rotation, scale, shear, nlcoeffs, translation))

        if params and coeffs is not None:
            raise ValueError("You cannot specify the transformation "
                             "coefficients and the implicit parameters at the "
                             "same time.")
        elif coeffs is not None:
            self.coeffs = coeffs
        else:
            GeometricTransform.__init__(self, rotation=rotation, scale=scale,
                                        shear=shear, translation=translation)
            self.nlcoeffs = (0., 0., 0., 0., 0., 0., 0.) if nlcoeffs is None else nlcoeffs
            if len(self.nlcoeffs) != 7 or not all(isinstance(a, numbers.Real) for a in self.nlcoeffs):
                raise ValueError("nlcoeffs should be 7 floats, but got %s" % (self.nlcoeffs,))

    @staticmethod
    def _vandermonde(w):
        """Return the Vandermonde matrix."""
        # suffix cc denotes complex conjugate
        wcc = numpy.conj(w)  # w̄
        w2 = w * w  # w²
        wabs2 = w * wcc  # ww̄
        wcc2 = wcc * wcc  # w̄²
        w2wcc = w * wabs2  # w²w̄
        wwcc2 = wcc * wabs2  # ww̄²
        w3wcc2 = wabs2 * w2wcc  # w³w̄²
        w2wcc3 = wabs2 * wwcc2  # w²w̄³
        M = numpy.column_stack((numpy.ones_like(w),  # zero order
                                w, wcc,  # first order
                                w2, wabs2, wcc2,  # second order
                                w2wcc, wwcc2,  # third order
                                w3wcc2, w2wcc3))  # fifth order
        return M

    def apply(self, x):
        x = numpy.asarray(x)
        w = x[..., 0] + 1.0j * x[..., 1]

        M = self._vandermonde(w)
        v = numpy.dot(M, self.coeffs)

        if x.ndim == 1:
            return numpy.array((v.real, v.imag))
        return numpy.column_stack((v.real, v.imag))

    def __call__(self, x):
        warnings.warn("__call__ is deprecated, use apply instead",
                      DeprecationWarning)
        return self.apply(x)
    __call__.__doc__ = apply.__doc__

    @classmethod
    def from_pointset(cls, x, y):
        """
        Estimate the transformation from a set of corresponding points.

        Constructor for AnamorphosisTransform that determines the best
        coordinate transformation from two point sets `x` and `y` in a
        least-squares sense. For more information see [1].

        Parameters
        ----------
        x : (n, 2) array
            Coordinates in the source reference frame.
        y : (n, 2) array
            Coordinates in the destination reference frame. Must be of same
            dimensions as `x`.

        Returns
        -------
        tform : AnamorphosisTransform
            Optimal coordinate transformation.

        References
        ----------
        .. [1] J. Stopka, "Polynomial fit for Multi-beam distortions", internal
        note, 2019

        """
        x = numpy.asarray(x)
        y = numpy.asarray(y)

        w = x[:, 0] + 1.0j * x[:, 1]
        v = y[:, 0] + 1.0j * y[:, 1]

        M = cls._vandermonde(w)
        coeffs = numpy.linalg.lstsq(M, v)[0]
        return cls(coeffs=coeffs)

    @property
    def coeffs(self):
        tx, ty = self.translation
        M = self.transformation_matrix
        p = 0.5 * (M[0, 0] + M[1, 1])
        q = 0.5 * (M[1, 0] - M[0, 1])
        r = 0.5 * (M[0, 0] - M[1, 1])
        s = 0.5 * (M[1, 0] + M[0, 1])
        a = tx + 1.0j * ty
        b1 = p + 1.0j * q
        b2 = r + 1.0j * s
        c1, c2, c3, d1, d2, e1, e2 = self.nlcoeffs
        return (a, b1, b2, c1, c2, c3, d1, d2, e1, e2)

    @coeffs.setter
    def coeffs(self, coeffs):
        a, b1, b2, c1, c2, c3, d1, d2, e1, e2 = coeffs
        p, q = (b1.real, b1.imag)
        r, s = (b2.real, b2.imag)
        matrix = numpy.array([(p + r, -q + s), (q + s, p - r)])
        self.transformation_matrix = matrix
        self.nlcoeffs = (c1, c2, c3, d1, d2, e1, e2)
        self.translation = (a.real, a.imag)

    def inverse(self):
        raise NotImplementedError("The inverse of the AnamorphosisTransform "
                                  "is not an Anamorphosis transform itself.")
