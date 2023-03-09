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
import math
from abc import ABCMeta, abstractmethod
from typing import List, Optional, Tuple, Type, TypeVar, Union

import numpy
import scipy.linalg
from numpy.linalg import LinAlgError
from odemis.util.linalg import qlp, qrp

T = TypeVar("T", bound="GeometricTransform")

__all__ = [
    "cartesian_to_polar",
    "polar_to_cartesian",
    "to_physical_space",
    "to_pixel_index",
    "AffineTransform",
    "ScalingTransform",
    "SimilarityTransform",
    "RigidTransform",
]


def cartesian_to_polar(xy: numpy.ndarray) -> Tuple[numpy.ndarray, numpy.ndarray]:
    """
    Transform Cartesian coordinates to polar coordinates.

    Parameters
    ----------
    xy : ndarray
        Cartesian coordinates.

    Returns
    -------
    rho : ndarray
        Radial coordinates.
    theta : ndarray
        Angular coordinates.

    """
    xy = numpy.asarray(xy)
    rho = numpy.hypot(xy[..., 0], xy[..., 1])
    theta = numpy.arctan2(xy[..., 1], xy[..., 0])
    return rho, theta


def polar_to_cartesian(rho: numpy.ndarray, theta: numpy.ndarray) -> numpy.ndarray:
    """
    Transform polar coordinates to Cartesian coordinates.

    Parameters
    ----------
    rho : ndarray
        Radial coordinates.
    theta : ndarray
        Angular coordinates.

    Returns
    -------
    xy : ndarray
        Cartesian coordinates.

    """
    x = rho * numpy.cos(theta)
    y = rho * numpy.sin(theta)
    return numpy.stack((x, y), axis=-1)


def to_physical_space(
    ji: Union[Tuple[float, float], List[Tuple[float, float]], numpy.ndarray],
    shape: Optional[Tuple[int, int]] = None,
    pixel_size: Optional[Union[float, Tuple[float, float]]] = None,
) -> numpy.ndarray:
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
    xy[..., 0] = ji[..., 1]  # map column-index `i` to x-axis
    xy[..., 1] = -ji[..., 0]  # map row-index `j` to y-axis

    if shape:
        # Move the origin to the center of the image.
        n, m = shape
        xy[..., 0] -= 0.5 * (m - 1)
        xy[..., 1] += 0.5 * (n - 1)

    if pixel_size is not None:
        xy *= pixel_size

    return xy


def to_pixel_index(
    xy: Union[Tuple[float, float], List[Tuple[float, float]], numpy.ndarray],
    shape: Optional[Tuple[int, int]] = None,
    pixel_size: Optional[Union[float, Tuple[float, float]]] = None,
) -> numpy.ndarray:
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
    ji[..., 1] = xy[..., 0]  # map x-axis to column-index `i`

    if shape:
        n, m = shape
        ji[..., 0] += 0.5 * (n - 1)
        ji[..., 1] += 0.5 * (m - 1)

    return ji


def _assert_is_rotation_matrix(matrix: numpy.ndarray) -> None:
    """
    Check if a matrix is a rotation matrix.

    Parameters
    ----------
    matrix : ndarray
        The matrix to check.

    Raises
    ------
    LinAlgError
        If the supplied matrix is not a rotation matrix.

    """
    if matrix.ndim != 2:
        raise LinAlgError(
            "%d-dimensional array given. Array must be two-dimensional" % matrix.ndim
        )
    m, n = matrix.shape
    if m != n:
        raise LinAlgError("Array must be square")
    if not numpy.allclose(numpy.dot(matrix.T, matrix), numpy.eye(n)):
        raise LinAlgError("Matrix is not orthogonal")
    if not numpy.allclose(numpy.linalg.det(matrix), 1.0):
        raise LinAlgError("Matrix is not a proper rotation matrix")


def _rotation_matrix_from_angle(angle: float) -> numpy.ndarray:
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
    return numpy.array([(ct, -st), (st, ct)])


def _rotation_matrix_to_angle(matrix: numpy.ndarray) -> float:
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
    _assert_is_rotation_matrix(matrix)
    if matrix.shape != (2, 2):
        raise NotImplementedError("Can only handle 2x2 matrices")
    return math.atan2(matrix[1, 0], matrix[0, 0])


def _ldlt_decomposition(a: numpy.ndarray) -> Tuple[numpy.ndarray, numpy.ndarray]:
    """
    LDLT decomposition of a positive definite matrix.

    Return the decomposition `L * D * L.T` of the positive definite matrix `a`,
    where `L` is a unit lower triangular matrix, and `D` is a diagonal matrix
    with positive elements.

    NOTE: No checking is performed to verify whether `a` is symmetric or not.
          Only the lower triangular and diagonal elements of `a` are used.

    Parameters
    ----------
    a : ndarray
        Symmetric, positive definite input matrix.

    Returns
    -------
    lu : ndarray
        The unit lower triangular outer factor of the factorization.
    d : ndarray
        The diagonal multiplier of the factorization.

    Raises
    ------
    LinAlgError
        If the decomposition fails, for example, if `a` is not positive
        definite.

    """
    c = numpy.linalg.cholesky(a)
    s = numpy.diagonal(c)
    lu = c / s
    d = numpy.square(s)
    return lu, d


def _transformation_matrix_from_implicit(
    scale: float, rotation: float, squeeze: float, shear: float
) -> numpy.ndarray:
    """
    Return the transformation matrix given the implicit parameters.

    Parameters
    ----------
    scale : float, positive
        Isotropic scale factor.
    rotation : float
        Roation angle in counter-clockwise direction as radians.
    squeeze : float, positive
        Anisotropic scale factor.
    shear : float
        Shear factor.

    Returns
    -------
    matrix : ndarray
        The transformation matrix.

    """
    if scale <= 0:
        raise ValueError("The scale factor should be positive.")
    if squeeze <= 0:
        raise ValueError("The squeeze factor should be positive.")
    R = _rotation_matrix_from_angle(rotation)
    L = numpy.array([(1, 0), (shear, 1)], dtype=float)
    D = numpy.array([squeeze, 1.0 / squeeze], dtype=float)
    return scale * R @ (L * D) @ L.T  # type: ignore


def _transformation_matrix_to_implicit(
    matrix: numpy.ndarray,
) -> Tuple[float, float, float, float]:
    """
    Return the implicit parameters given a transformation matrix.

    Parameters
    ----------
    matrix : ndarray
        The transformation matrix.

    Returns
    -------
    scale : float, positive
        Isotropic scale factor.
    rotation : float
        Roation angle in counter-clockwise direction as radians.
    squeeze : float, positive
        Anisotropic scale factor.
    shear : float
        Shear factor.

    """
    R, S3 = scipy.linalg.polar(matrix)
    if not numpy.allclose(numpy.linalg.det(R), 1.0):
        raise ValueError("Matrix is not a proper rotation matrix")
    rotation = _rotation_matrix_to_angle(R)
    scale = numpy.sqrt(numpy.linalg.det(S3))
    lu, d = _ldlt_decomposition(S3 / scale)
    squeeze = d[0]
    shear = lu[1, 0]
    return scale, rotation, squeeze, shear


def alt_transformation_matrix_from_implicit(
    scale: Union[numpy.ndarray, List[float], Tuple[float, float]],
    rotation: float,
    shear: float,
    form: str,
) -> numpy.ndarray:
    """
    Return a transformation matrix given an alternative description of the
    implicit parameters. This implementation is provided for backwards
    compatibility. Do not use for new design.

    Returns a matrix of the form `RSL` or `RSU`, where `R` is an orthogonal
    matrix and `S` is a diagonal matrix whose elements represent scale factors
    along the coordinate axis. Shear is provided by the matrix `L` or `U`,
    where `L` is a lower and `U` is an upper unitriangular matrix.

    Parameters
    ----------
    scale : (sx, sy) as array, list, or tuple
        x, y scale factors.
    rotation : float
        Rotation angle in counter-clockwise direction as radians.
    shear : float
        Shear factor.
    form : {"RSU", "RSL"}
        Whether the matrix is of the form `RSU` or `RSL`.

    Returns
    -------
    matrix : ndarray
        The transformation matrix.

    """
    R = _rotation_matrix_from_angle(rotation)
    S = scale * numpy.eye(2)
    if form == "RSU":
        U = numpy.array([(1, shear), (0, 1)])
        matrix = R @ S @ U
    elif form == "RSL":
        L = numpy.array([(1, 0), (shear, 1)])
        matrix = R @ S @ L
    else:
        raise ValueError("`form` must be either 'RSU' or 'RSL'")
    return matrix


def alt_transformation_matrix_to_implicit(
    matrix: numpy.ndarray, form: str
) -> Tuple[numpy.ndarray, float, float]:
    """
    Return an alternative description of the implicit parameters given a
    transformation matrix. This implementation is provided for backwards
    compatibility. Do not use for new design.

    Parameters
    ----------
    matrix : ndarray
        The transformation matrix.
    form : {"RSU", "RSL"}
        Whether the matrix is of the form `RSU` or `RSL`.

    Returns
    -------
    scale : (sx, sy) as ndarray
        x, y scale factors.
    rotation : float
        Roation angle in counter-clockwise direction as radians.
    shear : float
        Shear factor.

    """
    if form == "RSU":
        R, SU = qrp(matrix)
        scale = numpy.diag(SU)
        rotation = _rotation_matrix_to_angle(R)
        shear = SU[0, 1] / SU[0, 0]
    elif form == "RSL":
        R, SL = qlp(matrix)
        scale = numpy.diag(SL)
        rotation = _rotation_matrix_to_angle(R)
        shear = SL[1, 0] / SL[1, 1]
    else:
        raise ValueError("`form` must be either 'RSU' or 'RSL'")
    return scale, rotation, shear


def _optimal_rotation(x: numpy.ndarray, y: numpy.ndarray) -> numpy.ndarray:
    """
    Returns the optimal rigid transformation matrix between two zero mean point
    sets, such that |Rx-y|^2 is minimized.

    NOTE: No checking is performed to verify that `x` and `y` have zero mean.

    Parameters
    ----------
    x : ndarray
        Coordinates with zero mean in the source frame of reference.
    y : ndarray
        Coordinates with zero mean in the destination frame of reference.

    Returns
    -------
    matrix : 2x2 array
        Rotation matrix.

    Examples
    --------
    >>> x = numpy.array([(1., 0.), (-1., 0.)])
    >>> y = numpy.array([(0., 2.), (0., -2.)])
    >>> _optimal_rotation(x, y)
    array([[ 0., -1.],
           [ 1.,  0.]])

    """
    H = numpy.empty((2, 2), dtype=float)
    numpy.matmul(x.T, y, out=H)
    u, _, vh = numpy.linalg.svd(H, full_matrices=False)  # H = (u * s) @ vh
    if numpy.linalg.det(u) * numpy.linalg.det(vh) < 0.0:
        u[:, -1] = -u[:, -1]
    numpy.matmul(vh.T, u.T, out=H)
    return H


class ImplicitParameter:
    """
    Descriptor for implicit parameters. Customizes lookup and storage of a
    float-type attribute. Implements default value, positivity constraint, and
    immutable (fixed) value. Must be instantiated as a class variable in
    another class. For more info on the use of descriptors see [1]_.

    References
    ----------
    .. [1] Hettinger, R. Descriptor HowTo Guide,
           https://docs.python.org/3/howto/descriptor.html

    """

    def __init__(
        self, default: float, constrained: bool = False, positive: bool = False
    ) -> None:
        """
        Initialize an implicit parameter.

        Parameters
        ----------
        default : float
            The default value provided on attribute lookup if not initialized.
        constrained : bool
            If True, the attribute can only be set to the default value.
        positive : bool
            If True, the attribute can only be set to a positive value.

        """
        self.default = default
        self.constrained = constrained
        self.positive = positive

    def __set_name__(self, owner: Type["GeometricTransform"], name: str) -> None:
        self.private_name = "_" + name

    def __get__(
        self,
        instance: Optional["GeometricTransform"],
        owner: Optional[Type["GeometricTransform"]] = None,
    ) -> Union[float, "ImplicitParameter"]:
        """
        return the value (float) when a instance is passed.
          If the instance is None (ie, accessed on a class), returns itself.
        """
        if instance is None:
            return self
        return getattr(instance, self.private_name, self.default)

    def __set__(self, instance: "GeometricTransform", value: float) -> None:
        value = self.default if value is None else value
        if not isinstance(value, (int, float)):
            raise TypeError(f"Expected {value!r} to be an int or a float")
        if not math.isfinite(value):
            raise ValueError(f"Expected {value!r} to be finite")
        if self.positive and value <= 0:
            raise ValueError(f"Expected {value!r} to be positive")
        if self.constrained:
            if not math.isclose(value, self.default, rel_tol=1e-05, abs_tol=1e-08):
                raise ValueError(f"Expected {value!r} to be equal to {self.default!r}")
        else:
            setattr(instance, self.private_name, value)
            instance._matrix = None  # invalidate cache


class GeometricTransform(metaclass=ABCMeta):
    """
    Base class for geometric transformations.

    The transformation can either be descibed as a matrix multiplication using
    the _explicit_ parameter `matrix`, or alternatively using the _implicit_
    parameters `scale`, `rotation`, `squeeze`, and `shear`. Different transform
    types are implemented as a subclass of `GeometricTransform` by imposing a
    different set of constraints on the implicit parameters. For example, an
    instance of `RigidTransform` has unit scale, whereas this can be any
    positive number for a `SimilarityTransform` instance.

    """

    _matrix: Optional[numpy.ndarray] = None
    scale = ImplicitParameter(1, positive=True)
    rotation = ImplicitParameter(0)
    squeeze = ImplicitParameter(1, positive=True)
    shear = ImplicitParameter(0)

    def __init__(
        self,
        matrix: Optional[numpy.ndarray] = None,
        translation: Optional[numpy.ndarray] = None,
        **kwargs: Optional[float],
    ) -> None:
        """
        Basic initialisation helper.

        Parameters
        ----------
        matrix : ndarray of shape (2, 2), optional
            Transformation matrix, does not include translation.
        translation : ndarray of shape (2,), optional
            x, y translation parameters.
        scale : float, optional
            Scale factor.
        rotation : float, optional
            Rotation angle in counter-clockwise direction in radians.
        squeeze : float, optional
            Squeeze factor. Scales the input in one axis by this factor, and in
            the other axis by its multiplicative inverse.
        shear : float, optional
            Shear factor.

        """
        params = any(value is not None for value in kwargs.values())
        if params and matrix is not None:
            raise ValueError(
                "You cannot specify the transformation matrix and the "
                "implicit parameters at the same time."
            )
        elif matrix is not None:
            self.matrix = matrix
        else:
            for name, value in kwargs.items():
                setattr(self, name, value)

        self.translation = (
            numpy.zeros(2, dtype=float) if translation is None else translation
        )

    def __matmul__(self, other: "GeometricTransform") -> "GeometricTransform":
        """
        Overloads the `@` operator to combine two transforms into one.

        Parameters
        ----------
        other : GeometricTransform
            The geometric transform with which to combine.

        Returns
        -------
        out : GeometricTransform
            The combined transform.

        Examples
        --------
        >>> rotation = 0.5 * numpy.pi
        >>> translation = numpy.array((1, 0))
        >>> a = RigidTransform(rotation=rotation, translation=translation)
        >>> b = a @ a
        >>> numpy.isclose(b.rotation, numpy.pi)
        True
        >>> numpy.allclose(b.translation, numpy.array((1, 1)))
        True

        """
        cls = getattr(self, "_inverse_type", type(self))
        if not isinstance(other, cls):
            cls = type(other)
        matrix = self.matrix @ other.matrix
        translation = self.apply(other.translation)
        return cls(matrix, translation)

    @property
    def matrix(self) -> numpy.ndarray:
        """The 2x2 transformation matrix. Does not include translation."""
        if self._matrix is None:
            self._matrix = _transformation_matrix_from_implicit(
                self.scale, self.rotation, self.squeeze, self.shear
            )
        return self._matrix

    @matrix.setter
    def matrix(self, matrix: numpy.ndarray) -> None:
        if matrix.shape != (2, 2):
            raise ValueError("Transformation matrix should be 2x2, but got %s" % matrix)
        (
            self.scale,
            self.rotation,
            self.squeeze,
            self.shear,
        ) = _transformation_matrix_to_implicit(matrix)
        self._matrix = matrix

    @staticmethod
    @abstractmethod
    def _estimate_matrix(x: numpy.ndarray, y: numpy.ndarray) -> numpy.ndarray:
        """
        Returns the optimal transformation matrix between two zero mean point
        sets, such that |Ax-y|^2 is minimized.

        NOTE: No checking is performed to verify that `x` and `y` have zero
              mean.

        Parameters
        ----------
        x : ndarray
            Coordinates with zero mean in the source frame of reference.
        y : ndarray
            Coordinates with zero mean in the destination frame of reference.

        Returns
        -------
        matrix : 2x2 array
            Rotation matrix.

        """
        pass

    @classmethod
    def from_pointset(cls: Type[T], x: numpy.ndarray, y: numpy.ndarray) -> T:
        """
        Estimate the transformation from a set of corresponding points.

        Constructor for a GeometricTransform that determines the best
        coordinate transformation from two point sets `x` and `y` in a
        least-squares sense.

        Parameters
        ----------
        x : (n, 2) array
            Coordinates in the source reference frame.
        y : (n, 2) array
            Coordinates in the destination reference frame. Must be of same
            dimensions as `x`.

        Returns
        -------
        tform : GeometricTransform
            Optimal coordinate transformation.

        """
        x = numpy.asarray(x)
        y = numpy.asarray(y)
        x0 = numpy.mean(x, axis=0)
        y0 = numpy.mean(y, axis=0)
        dx = x - x0
        dy = y - y0
        matrix = cls._estimate_matrix(dx, dy)
        translation = y0 - numpy.matmul(matrix, x0)
        return cls(matrix=matrix, translation=translation)

    def apply(self, x: numpy.ndarray) -> numpy.ndarray:
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
        return numpy.einsum("ik,...k->...i", self.matrix, x) + self.translation  # type: ignore

    def inverse(self) -> "GeometricTransform":
        """
        Return the inverse transformation.

        By default the type of the inverse of a GeometricTransform is the same
        as the GeometricTransform itself. May be overridden by a subclass by
        setting `self._inverse_type` to the inverse class.

        Returns
        -------
        tform : GeometricTransform
            The inverse transformation.

        """
        matrix = numpy.linalg.inv(self.matrix)
        translation = -numpy.matmul(matrix, self.translation)
        cls = getattr(self, "_inverse_type", type(self))
        return cls(matrix, translation)

    def fre(self, x: numpy.ndarray, y: numpy.ndarray) -> float:
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
        fre = math.sqrt(numpy.mean(delta * delta))
        return fre


class AffineTransform(GeometricTransform):
    """
    Affine transform

    An affine transformation preserves the straightness of lines, and hence,
    the planarity of surfaces, and it preserves parallelism, but it allows
    angles between lines to change.

    The affine transform has the following form:

        x' = sRLDL⸆x + t

    where `s` is a positive scalar representing isotropic scaling, `R` is an
    orthogonal matrix representing rotation, `L` is a lower triangular matrix
    with all diagonal elements equal to one and a single off-diagonal non-zero
    element representing shear, `D` is a diagonal matrix with diagonal entries
    `k` and `1/k` representing anisotropic scaling where `k` is the squeeze
    factor, `L⸆` is the matrix transpose of `L`, and `t` is a translation
    vector. To eliminate improper rotations (reflections) it is required that
    the determinant of `R` equals 1.

    Parameters
    ----------
    matrix : ndarray of shape (2, 2), optional
        Transformation matrix, does not include translation.
    translation : ndarray of shape (2,), optional
        x, y translation parameters.
    scale : float, optional
        Scale factor.
    rotation : float, optional
        Rotation angle in counter-clockwise direction in radians.
    squeeze : float, optional
        Squeeze factor. Scales the input in one axis by this factor, and in the
        other axis by its multiplicative inverse.
    shear : float, optional
        Shear factor.

    Attributes
    ----------
    matrix : ndarray of shape (2, 2)
    translation : ndarray of shape (2,)
        Translation vector.
    scale : float
        Scale factor.
    rotation : float
        Rotation angle in radians.
    squeeze : float
        Squeeze factor.
    shear : float
        Shear factor.

    """

    def __init__(
        self,
        matrix: Optional[numpy.ndarray] = None,
        translation: Optional[numpy.ndarray] = None,
        *,
        scale: Optional[float] = None,
        rotation: Optional[float] = None,
        squeeze: Optional[float] = None,
        shear: Optional[float] = None,
    ) -> None:
        GeometricTransform.__init__(
            self,
            matrix,
            translation,
            scale=scale,
            rotation=rotation,
            squeeze=squeeze,
            shear=shear,
        )

    @staticmethod
    def _estimate_matrix(x: numpy.ndarray, y: numpy.ndarray) -> numpy.ndarray:
        at = numpy.linalg.lstsq(x, y, rcond=0)[0]
        return numpy.transpose(at)


class ScalingTransform(AffineTransform):
    """
    Scaling transform.

    A scaling transform is rigid except for scaling. If the scaling is
    isotropic it is called a similarity transform.

    The scaling transform has the following form:

        x' = sRDx + t

    where `s` is a positive scalar representing isotropic scaling, `R` is an
    orthogonal matrix representing rotation, `D` is a diagonal matrix with
    diagonal entries `k` and `1/k` representing anisotropic scaling where `k`
    is the squeeze factor, and `t` is a translation vector. To eliminate
    improper rotations (reflections) it is required that the determinant of `R`
    equals 1.

    NOTE: `RD` is not in general equal to `DR`; so these are two different
          classes of transformations.

    Parameters
    ----------
    matrix : ndarray of shape (2, 2), optional
        Transformation matrix, does not include translation.
    translation : ndarray of shape (2,), optional
        x, y translation parameters.
    scale : float, optional
        Scale factor.
    rotation : float, optional
        Rotation angle in counter-clockwise direction in radians.
    squeeze : float, optional
        Squeeze factor. Scales the input in one axis by this factor, and in the
        other axis by its multiplicative inverse.

    Attributes
    ----------
    matrix : ndarray of shape (2, 2)
    translation : ndarray of shape (2,)
        Translation vector.
    scale : float
        Scale factor.
    rotation : float
        Rotation angle in radians.
    squeeze : float
        Squeeze factor.
    shear : float
        Shear factor. Always equal to 0.

    """

    shear = ImplicitParameter(0, constrained=True)

    def __init__(
        self,
        matrix: Optional[numpy.ndarray] = None,
        translation: Optional[numpy.ndarray] = None,
        *,
        scale: Optional[float] = None,
        rotation: Optional[float] = None,
        squeeze: Optional[float] = None,
    ) -> None:
        GeometricTransform.__init__(
            self, matrix, translation, scale=scale, rotation=rotation, squeeze=squeeze,
        )
        self._inverse_type = AffineTransform

    @staticmethod
    def _estimate_matrix(x: numpy.ndarray, y: numpy.ndarray) -> numpy.ndarray:
        """
        See GeometricTransform._estimate_matrix() for a more detailed
        description.

        This implementation uses the closed-form solution obtained from [1]_.

        References
        ----------
        .. [1] Škrinjar, O. (2006, July). Point-based registration with known
               correspondence: Closed form optimal solutions and properties. In
               International Workshop on Biomedical Image Registration
               (pp. 315-321). Springer, Berlin, Heidelberg.

        """
        alpha = numpy.einsum("ij,ik->jk", x, y)
        beta = numpy.einsum("ij,ij->j", x, x)
        (a11, a12), (a21, a22) = alpha
        b1, b2 = beta
        k1 = numpy.square(a11) / b1 + numpy.square(a22) / b2
        k2 = numpy.square(a12) / b1 + numpy.square(a21) / b2
        k3 = 2 * (a11 * a12 / b1 - a21 * a22 / b2)
        phi = 0.5 * numpy.arctan2(k3, k1 - k2)
        R = _rotation_matrix_from_angle(phi)
        scales = numpy.einsum("ij,ji->j", R, alpha) / beta
        return scales * R


class SimilarityTransform(ScalingTransform):
    """
    Similarity transform.

    A similarity transform is rigid except for isotropic scaling.

    The similarity transform has the following form:

        y = sRx + t

    where `s` is a positive scalar representing isotropic scaling, `R` is an
    orthogonal matrix representing rotation, and `t` is a translation vector.
    To eliminate improper rotations (reflections) it is required that the
    determinant of `R` equals 1.

    Parameters
    ----------
    matrix : ndarray of shape (2, 2), optional
        Transformation matrix, does not include translation.
    translation : ndarray of shape (2,), optional
        x, y translation parameters.
    scale : float, optional
        Scale factor.
    rotation : float, optional
        Rotation angle in counter-clockwise direction in radians.

    Attributes
    ----------
    matrix : ndarray of shape (2, 2)
    translation : ndarray of shape (2,)
        Translation vector.
    scale : float
        Scale factor.
    rotation : float
        Rotation angle in radians.
    squeeze : float
        Squeeze factor. Always equal to 1.
    shear : float
        Shear factor. Always equal to 0.

    """

    squeeze = ImplicitParameter(1, constrained=True)

    def __init__(
        self,
        matrix: Optional[numpy.ndarray] = None,
        translation: Optional[numpy.ndarray] = None,
        *,
        scale: Optional[float] = None,
        rotation: Optional[float] = None,
    ) -> None:
        GeometricTransform.__init__(
            self, matrix, translation, scale=scale, rotation=rotation
        )

    @staticmethod
    def _estimate_matrix(x: numpy.ndarray, y: numpy.ndarray) -> numpy.ndarray:
        R = _optimal_rotation(x, y)
        s = numpy.einsum("ik,jk,ji", R, x, y) / numpy.einsum("ij,ij", x, x)
        matrix = s * R
        return matrix


class RigidTransform(SimilarityTransform):
    """
    Rigid transformation.

    A rigid transformation is a geometrical transformation that preserves all
    distances. A rigid transformation also preserves the straightness of lines
    (and the planarity of surfaces) and all nonzero angles between straight
    lines.

    The rigid transform has the following form:

        y = Rx + t

    where `R` is an orthogonal matrix representing rotation, and `t` is a
    translation vector. To eliminate improper rotations (reflections) it is
    required that the determinant of `R` equals 1.

    Parameters
    ----------
    matrix : ndarray of shape (2, 2), optional
        Transformation matrix, does not include translation.
    translation : ndarray of shape (2,), optional
        x, y translation parameters.
    rotation : float, optional
        Rotation angle in counter-clockwise direction in radians.

    Attributes
    ----------
    matrix : ndarray of shape (2, 2)
    translation : ndarray of shape (2,)
        Translation vector.
    scale : float
        Scale factor. Always equal to 1.
    rotation : float
        Rotation angle in radians.
    squeeze : float
        Squeeze factor. Always equal to 1.
    shear : float
        Shear factor. Always equal to 0.

    """

    scale = ImplicitParameter(1, constrained=True)

    def __init__(
        self,
        matrix: Optional[numpy.ndarray] = None,
        translation: Optional[numpy.ndarray] = None,
        *,
        rotation: Optional[float] = None,
    ) -> None:
        GeometricTransform.__init__(self, matrix, translation, rotation=rotation)

    @staticmethod
    def _estimate_matrix(x: numpy.ndarray, y: numpy.ndarray) -> numpy.ndarray:
        return _optimal_rotation(x, y)
