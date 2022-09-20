# -*- coding: utf-8 -*-
"""
Created on 10 Jan 2019

@author: Andries Effting

Copyright © 2019 Andries Effting, Delmic

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
import copy
import inspect
import numpy
from numpy.linalg import LinAlgError
import operator
import unittest

from odemis.util.random import check_random_state
from odemis.util.spot import GridPoints
from odemis.util.transform import (
    _rotation_matrix_from_angle,
    _rotation_matrix_to_angle,
    cartesian_to_polar,
    polar_to_cartesian,
    to_physical_space,
    to_pixel_index,
    AffineTransform,
    ScalingTransform,
    SimilarityTransform,
    RigidTransform,
)
from transform_known_values import transform_known_values


def _angle_diff(x, y):
    """
    Returns the signed difference between two angles, taking into account
    the branch cut at the negative x-axis.
    """
    return min(y - x, y - x + 2.0 * numpy.pi, y - x - 2.0 * numpy.pi, key=abs)


class PolarCoordinateTransformationTest(unittest.TestCase):
    """Unit tests for `cartesian_to_polar()` and `polar_to_cartesian()`."""

    def setUp(self):
        """Ensure reproducible tests."""
        self._rng = check_random_state(12345)

    def test_cartesian_to_polar_known_values(self):
        """
        `cartesian_to_polar()` should return known result with known input.

        """
        xy = numpy.array([(1, 0), (0, 1), (-1, 0), (0, -1)])
        _rho = numpy.array([1, 1, 1, 1])
        _theta = numpy.pi * numpy.array([0, 0.5, 1, -0.5])
        rho, theta = cartesian_to_polar(xy)
        numpy.testing.assert_array_almost_equal(rho, _rho)
        numpy.testing.assert_array_almost_equal(theta, _theta)

    def test_polar_to_cartesian_known_values(self):
        """
        `polar_to_cartesian()` should return known result with known input.

        """
        rho = numpy.array([1, 1, 1, 1])
        theta = numpy.pi * numpy.array([0, 0.5, 1, -0.5])
        _xy = numpy.array([(1, 0), (0, 1), (-1, 0), (0, -1)])
        xy = polar_to_cartesian(rho, theta)
        numpy.testing.assert_array_almost_equal(xy, _xy)

    def test_round_trip(self):
        """
        Cartesian coordinates should remain the same when converted to polar
        coordinates and then back.

        """
        _xy = self._rng.standard_normal((100, 2))
        rho, theta = cartesian_to_polar(_xy)
        xy = polar_to_cartesian(rho, theta)
        numpy.testing.assert_array_almost_equal(xy, _xy)


class PixelIndexCoordinateTransformBase(unittest.TestCase):
    def setUp(self):
        ji = [(0, 0), (0, 4), (7, 0), (7, 4), (0.5, 0.5)]

        # List of tuples with known values `(ji, xy, kwargs)` for which
        # both `xy == to_physical_space(ji, **kwargs)` as well as
        # `ji == to_pixel_index(xy, **kwargs)` evaluate to True.
        self._known_values = [
            (
                ji,
                [(0, 0), (4, 0), (0, -7), (4, -7), (0.5, -0.5)],
                {},  # empty dict means default function arguments are used
            ),
            (
                ji,
                [(0, 0), (8, 0), (0, -14), (8, -14), (1, -1)],
                {"pixel_size": 2},
            ),
            (
                ji,
                [(0, 0), (8, 0), (0, -21), (8, -21), (1, -1.5)],
                {"pixel_size": (2, 3)},
            ),
            (
                ji,
                [(-2, 3.5), (2, 3.5), (-2, -3.5), (2, -3.5), (-1.5, 3)],
                {"shape": (8, 5)},
            ),
            (
                ji,
                [(-4, 7), (4, 7), (-4, -7), (4, -7), (-3, 6)],
                {"shape": (8, 5), "pixel_size": 2},
            ),
            (
                ji,
                [(-4, 10.5), (4, 10.5), (-4, -10.5), (4, -10.5), (-3, 9)],
                {"shape": (8, 5), "pixel_size": (2, 3)},
            ),
        ]


class ToPhysicalSpaceKnownValues(PixelIndexCoordinateTransformBase):
    def test_to_physical_space_known_values(self):
        """
        to_physical_space should return known result with known input.

        """
        # tuple
        for _ji, _xy, kwargs in self._known_values:
            for ji, xy in zip(_ji, _xy):
                res = to_physical_space(ji, **kwargs)
                numpy.testing.assert_array_almost_equal(xy, res)

        # list of tuples
        for ji, xy, kwargs in self._known_values:
            res = to_physical_space(ji, **kwargs)
            numpy.testing.assert_array_almost_equal(xy, res)

        # ndarray
        for ji, xy, kwargs in self._known_values:
            ji = numpy.asarray(ji)
            res = to_physical_space(ji, **kwargs)
            numpy.testing.assert_array_almost_equal(xy, res)

    def test_to_physical_space_zero_pixel_size(self):
        """
        to_physical space should return zero when given zero pixel size.

        """
        for ji, _, _ in self._known_values:
            res = to_physical_space(ji, pixel_size=0)
            numpy.testing.assert_allclose(0, res)

    def test_to_physical_space_multiple(self):
        """
        to_physical_space should return the same result when called on a list
        of indices, or on individual indices.

        """
        for _ji, _, kwargs in self._known_values:
            _xy = to_physical_space(_ji, **kwargs)
            for ji, xy in zip(_ji, _xy):
                res = to_physical_space(ji, **kwargs)
                numpy.testing.assert_array_almost_equal(xy, res)

    def test_to_physical_space_raises_value_error(self):
        """
        to_physical_space should raise a ValueError when the provided index is
        not 2-dimensional.

        """
        self.assertRaises(ValueError, to_physical_space, ())
        self.assertRaises(ValueError, to_physical_space, (1,))
        self.assertRaises(ValueError, to_physical_space, (1, 2, 3))


class ToPixelIndexKnownValues(PixelIndexCoordinateTransformBase):
    def test_to_pixel_index_known_values(self):
        """
        to_pixel_index should return known result with known input.

        """
        # tuple
        for _ji, _xy, kwargs in self._known_values:
            for ji, xy in zip(_ji, _xy):
                res = to_pixel_index(xy, **kwargs)
                numpy.testing.assert_array_almost_equal(ji, res)

        # list of tuples
        for ji, xy, kwargs in self._known_values:
            res = to_pixel_index(xy, **kwargs)
            numpy.testing.assert_array_almost_equal(ji, res)

        # ndarray
        for ji, xy, kwargs in self._known_values:
            xy = numpy.asarray(xy)
            res = to_pixel_index(xy, **kwargs)
            numpy.testing.assert_array_almost_equal(ji, res)

    def test_to_pixel_index_multiple(self):
        """
        to_pixel_index should return the same result when called on a list of
        indices, or on individual indices.

        """
        for _, _xy, kwargs in self._known_values:
            _ji = to_pixel_index(_xy, **kwargs)
            for ji, xy in zip(_ji, _xy):
                res = to_pixel_index(xy, **kwargs)
                numpy.testing.assert_array_almost_equal(ji, res)

    def test_to_pixel_index_raises_value_error(self):
        """
        to_pixel_index should raise a ValueError when the provided coordinate
        is not 2-dimensional.

        """
        self.assertRaises(ValueError, to_pixel_index, ())
        self.assertRaises(ValueError, to_pixel_index, (1,))
        self.assertRaises(ValueError, to_pixel_index, (1, 2, 3))


class RotationMatrixKnownValues(unittest.TestCase):
    known_values = [
        (-numpy.deg2rad(180), numpy.array([(-1, 0), (0, -1)])),
        (-numpy.deg2rad(135), numpy.array([(-1, 1), (-1, -1)]) / numpy.sqrt(2)),
        (-numpy.deg2rad(90), numpy.array([(0, 1), (-1, 0)])),
        (-numpy.deg2rad(45), numpy.array([(1, 1), (-1, 1)]) / numpy.sqrt(2)),
        (0, numpy.array([(1, 0), (0, 1)])),
        (numpy.deg2rad(45), numpy.array([(1, -1), (1, 1)]) / numpy.sqrt(2)),
        (numpy.deg2rad(90), numpy.array([(0, -1), (1, 0)])),
        (numpy.deg2rad(135), numpy.array([(-1, -1), (1, -1)]) / numpy.sqrt(2)),
        (numpy.deg2rad(180), numpy.array([(-1, 0), (0, -1)])),
    ]

    def test_rotation_matrix_to_angle_known_values(self):
        """
        _rotation_matrix_to_angle should give known result with known input.
        """
        for angle, matrix in self.known_values:
            result = _rotation_matrix_to_angle(matrix)
            self.assertAlmostEqual(_angle_diff(angle, result), 0)

    def test_rotation_matrix_from_angle_known_values(self):
        """
        _rotation_matrix_from_angle should give known result with known input.
        """
        for angle, matrix in self.known_values:
            result = _rotation_matrix_from_angle(angle)
            numpy.testing.assert_array_almost_equal(matrix, result)


class RotationMatrixToAngleBadInput(unittest.TestCase):
    def test_wrong_dimension(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the number of
        dimensions of the array is other than 2.
        """
        for s in [(), (2,), (2, 2, 2)]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle, numpy.zeros(s))

    def test_not_square(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        not square.
        """
        for s in [(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle, numpy.zeros(s))

    def test_not_2d(self):
        """
        _rotation_matrix_to_angle should fail when the matrix is not a 2-D
        matrix.
        """
        for s in (1, 3):
            self.assertRaises(
                NotImplementedError, _rotation_matrix_to_angle, numpy.eye(s)
            )

    def test_not_orthogonal(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        not orthogonal.
        """
        for matrix in [
            numpy.array([(0, 0), (0, 0)]),
            numpy.array([(1, 0), (1, 0)]),
            numpy.array([(1, 1), (0, 0)]),
            numpy.array([(0, 1), (0, 1)]),
            numpy.array([(0, 0), (1, 1)]),
        ]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle, matrix)

    def test_improper_rotation(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        an improper rotation (contains a reflection).
        """
        for matrix in [
            numpy.array([(1, 0), (0, -1)]),
            numpy.array([(-1, 0), (0, 1)]),
            numpy.array([(0, 1), (1, 0)]),
            numpy.array([(0, -1), (-1, 0)]),
        ]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle, matrix)


class RotationMatrixProperties(unittest.TestCase):
    def test_rotation_matrix_properties(self):
        """
        Test that the rotation matrix is a 2x2 square orthogonal matrix, with
        determinant equal to 1.
        """
        for angle in numpy.pi * numpy.linspace(-1, 1, 1000):
            matrix = _rotation_matrix_from_angle(angle)
            self.assertEqual(matrix.shape, (2, 2))
            numpy.testing.assert_array_almost_equal(
                numpy.dot(matrix.T, matrix), numpy.eye(2)
            )
            self.assertAlmostEqual(numpy.linalg.det(matrix), 1)


class RotationMatrixRoundTripCheck(unittest.TestCase):
    def test_roundtrip(self):
        """
        _rotation_matrix_to_angle(_rotation_matrix_from_angle(angle)) == angle
        for all angles.
        """
        for angle in numpy.pi * numpy.linspace(-1, 1, 1000):
            matrix = _rotation_matrix_from_angle(angle)
            result = _rotation_matrix_to_angle(matrix)
            self.assertAlmostEqual(angle, result)


class TransformTestBase:
    @classmethod
    def setUpClass(cls):
        numpy.random.seed(0)

    def test_attributes(self):
        """
        Each GeometricTransform instance should have the attributes `matrix`,
        `translation`, `scale`, `rotation`, `squeeze`, and `shear`.

        """
        tform = self.transform_type()
        for attr in ("matrix", "translation", "scale", "rotation", "squeeze", "shear"):
            with self.subTest(attr=attr):
                self.assertTrue(hasattr(tform, attr))

    def test_default_identity(self):
        """
        The returned GeometricTransform instance should be equal to the
        identity transform when instantiated without arguments.

        """
        tform = self.transform_type()
        numpy.testing.assert_array_equal(tform.matrix, numpy.eye(2))
        numpy.testing.assert_array_equal(tform.translation, numpy.zeros(2))

    def test_init_from_matrix_no_reflection(self):
        """
        When instantiated with a matrix that contains a reflection a ValueError
        should be raised.

        """
        matrix = numpy.array([(1, 0), (0, -1)])
        self.assertRaises(ValueError, self.transform_type, matrix)

    def test_init_from_matrix_known_values(self):
        """
        The returned GeometricTransform instance should be equal to a known
        result when instantiated with known input.

        """
        for cls, matrix, translation, params, src, dst in transform_known_values():
            if issubclass(cls, self.transform_type):
                with self.subTest(**params):
                    tform = self.transform_type(matrix, translation)
                    self.assertAlmostEqual(params.get("scale", 1), tform.scale)
                    self.assertAlmostEqual(
                        0, _angle_diff(params.get("rotation", 0), tform.rotation)
                    )
                    self.assertAlmostEqual(params.get("squeeze", 1), tform.squeeze)
                    self.assertAlmostEqual(params.get("shear", 0), tform.shear)

    def test_init_from_matrix_invalid_input(self):
        """
        When instantiated with an invalid matrix a ValueError should be raised.

        """
        for cls, matrix, translation, params, src, dst in transform_known_values():
            if not issubclass(cls, self.transform_type):
                with self.subTest(**params):
                    self.assertRaises(
                        ValueError, self.transform_type, matrix, translation
                    )

    def test_init_from_implicit_known_values(self):
        """
        The returned GeometricTransform instance should be equal to a known
        result when instantiated with known input.

        """
        for cls, matrix, translation, params, src, dst in transform_known_values():
            if issubclass(cls, self.transform_type):
                with self.subTest(**params):
                    tform = self.transform_type(translation=translation, **params)
                    numpy.testing.assert_array_almost_equal(matrix, tform.matrix)
                    numpy.testing.assert_array_almost_equal(
                        translation, tform.translation
                    )

    def test_init_from_implicit_invalid_input(self):
        """
        When instantiated with invalid input a ValueError should be raised.

        """
        sig = inspect.signature(self.transform_type)
        if "scale" in sig.parameters:
            self.assertRaises(ValueError, self.transform_type, scale=-1)
        if "squeeze" in sig.parameters:
            self.assertRaises(ValueError, self.transform_type, squeeze=-1)

    def test_from_pointset_known_values(self):
        """
        `GeometricTransform.from_pointset()` should return known result with
        known input.

        """
        for cls, matrix, translation, params, src, dst in transform_known_values():
            if issubclass(cls, self.transform_type):
                tform = self.transform_type.from_pointset(src, dst)
                with self.subTest(**params):
                    numpy.testing.assert_array_almost_equal(matrix, tform.matrix)
                    numpy.testing.assert_array_almost_equal(
                        translation, tform.translation
                    )
                    self.assertAlmostEqual(tform.fre(src, dst), 0)

    def test_from_pointset_identity_property(self):
        """
        `GeometricTransform.from_pointset()` should generate the identity
        transformation when applied to two identical point sets.

        """
        src = numpy.random.random_sample((10, 2))
        tform = self.transform_type.from_pointset(src, src)
        numpy.testing.assert_array_almost_equal(tform.matrix, numpy.eye(2))
        numpy.testing.assert_array_almost_equal(tform.translation, numpy.zeros(2))

    def test_from_pointset_optimal(self):
        """
        The transform returned by `GeometricTransform.from_pointset()` should
        minimize the fiducial registration error (FRE). Here we test that a
        pertubation of any of the non-constrained parameters results in an
        increased FRE.

        """
        params = {
            "scale": 1 + 0.5 * (2 * numpy.random.random_sample() - 1),
            "rotation": numpy.pi * (2 * numpy.random.random_sample() - 1),
            "squeeze": 1 + 0.2 * (2 * numpy.random.random_sample() - 1),
            "shear": 0.2 * (2 * numpy.random.random_sample() - 1),
        }
        src = GridPoints(8, 8)
        noise = 0.1 * (2 * numpy.random.random_sample(src.shape) - 1)
        dst = AffineTransform(**params).apply(src + noise)
        tform0 = self.transform_type.from_pointset(src, dst)
        fre0 = tform0.fre(src, dst)
        for param in ("scale", "rotation", "squeeze", "shear"):
            if getattr(self.transform_type, param).constrained:
                continue
            val = getattr(tform0, param)
            tform = copy.copy(tform0)
            for op in (operator.add, operator.sub):
                setattr(tform, param, op(val, 1.0e-6))
                fre = tform.fre(src, dst)
                self.assertGreater(fre, fre0)

    def test_apply_known_values(self):
        """
        `GeometricTransform.apply()` should return known result with known
        input.

        """
        for cls, matrix, translation, params, src, dst in transform_known_values():
            if issubclass(cls, self.transform_type):
                with self.subTest(**params):
                    tform = self.transform_type(matrix, translation)
                    numpy.testing.assert_array_almost_equal(dst, tform.apply(src))

    def test_inverse_known_values(self):
        """
        `GeometricTransform.inverse()` should return known result with known
        input.

        """
        for cls, matrix, translation, params, src, dst in transform_known_values():
            if issubclass(cls, self.transform_type):
                with self.subTest(**params):
                    tform = self.transform_type(matrix, translation).inverse()
                    numpy.testing.assert_array_almost_equal(src, tform.apply(dst))


class AffineTransformTest(TransformTestBase, unittest.TestCase):
    transform_type = AffineTransform


class ScalingTransformTest(TransformTestBase, unittest.TestCase):
    transform_type = ScalingTransform


class SimilarityTransformTest(TransformTestBase, unittest.TestCase):
    transform_type = SimilarityTransform

    def test_similarity_transform_from_pointset_umeyama(self):
        """
        SimilarityTransform.from_pointset should return the known results for
        the specific known input as described in the paper by Umeyama.
        """
        src = numpy.array([(0, 0), (1, 0), (0, 2)])
        dst = numpy.array([(0, 0), (-1, 0), (0, 2)])
        tform = SimilarityTransform.from_pointset(src, dst)
        numpy.testing.assert_array_almost_equal(
            _rotation_matrix_from_angle(tform.rotation),
            numpy.array([(0.832, 0.555), (-0.555, 0.832)]),
            decimal=3,
        )
        self.assertAlmostEqual(tform.scale, 0.721, places=3)
        numpy.testing.assert_array_almost_equal(
            tform.translation, numpy.array([-0.800, 0.400])
        )
        self.assertAlmostEqual(tform.fre(src, dst), 0.516, places=3)


class RigidTransformTest(TransformTestBase, unittest.TestCase):
    transform_type = RigidTransform


class TransformFromPointsetEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        numpy.random.seed(0)

    def test_transform_equal_rotation(self):
        """
        When estimating the transform using `from_pointset()` where the source
        coordinates have zero moments, the estimated rotation should be equal
        for transforms of class `AffineTransform`, `SimilarityTransform`, and
        `RigidTransform`. Note: this list excludes `ScalingTransform`.

        """
        params = {
            "scale": 1 + 0.5 * (2 * numpy.random.random_sample() - 1),
            "rotation": numpy.pi * (2 * numpy.random.random_sample() - 1),
            "squeeze": 1 + 0.2 * (2 * numpy.random.random_sample() - 1),
            "shear": 0.2 * (2 * numpy.random.random_sample() - 1),
        }
        src = GridPoints(8, 8)
        noise = 0.1 * (2 * numpy.random.random_sample(src.shape) - 1)
        dst = AffineTransform(**params).apply(src + noise)
        rotation = None
        for transform_type in (AffineTransform, SimilarityTransform, RigidTransform):
            tform = transform_type.from_pointset(src, dst)
            if rotation is None:
                rotation = tform.rotation
            else:
                self.assertAlmostEqual(rotation, tform.rotation)


if __name__ == "__main__":
    unittest.main()
