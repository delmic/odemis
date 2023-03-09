# -*- encoding: utf-8 -*-
"""
registration_test.py : unit tests for odemis.util.registration.

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

"""
import itertools
import operator
import unittest

import numpy
import scipy
from odemis.util import pairwise, synthetic
from odemis.util.graph import WeightedGraph
from odemis.util.random import check_random_state
from odemis.util.registration import (
    _canonical_matrix_form,
    bijective_matching,
    estimate_grid_orientation,
    estimate_grid_orientation_from_img,
    nearest_neighbor_graph,
    unit_gridpoints,
)
from odemis.util.transform import AffineTransform

from transform_test import random_transform


class BijectiveMatchingTest(unittest.TestCase):
    """Unit tests for `bijective_matching()`."""

    def setUp(self):
        """Ensure reproducible tests."""
        self._rng = check_random_state(12345)

    def test_trivial(self):
        """Check the trivial case of matching a point set with itself."""
        n = 100
        src = self._rng.random_sample((n, 2))
        correspondences = [(j,) * 2 for j in range(n)]
        out = bijective_matching(src, src)
        self.assertCountEqual(correspondences, out)

    def test_permutation(self):
        """Check that a random permutation of a point set can be recovered."""
        for n, m in itertools.product((100, 105), repeat=2):
            with self.subTest(n=n, m=m):
                k = max(n, m)
                idx = self._rng.permutation(k)
                correspondences = [(j, i) for j, i in zip(idx, range(m)) if j < n]
                points = self._rng.random_sample((k, 2))
                src = points[:n]
                dst = points[idx[:m]]
                out = bijective_matching(src, dst)
                self.assertCountEqual(correspondences, out)

    def test_maximum_size(self):
        """Check that the matching has maximum size."""
        for n, m in itertools.product((100, 105), repeat=2):
            with self.subTest(n=n, m=m):
                src = self._rng.random_sample((n, 2))
                dst = self._rng.random_sample((m, 2))
                correspondences = bijective_matching(src, dst)
                self.assertEqual(min(n, m), len(list(correspondences)))

    def test_uniqueness(self):
        """
        Check that each `src` or `dst` vertex of the matching is part of
        exactly one matching edge.

        """
        for n, m in itertools.product((100, 105), repeat=2):
            with self.subTest(n=n, m=m):
                src = self._rng.random_sample((n, 2))
                dst = self._rng.random_sample((m, 2))
                correspondences = bijective_matching(src, dst)
                for i in (0, 1):
                    vertices = list(map(operator.itemgetter(i), correspondences))
                    self.assertEqual(len(vertices), len(set(vertices)))

    def test_sorted(self):
        """
        The returned list of correspondences should be sorted by distance in
        ascending order.

        """
        for n, m in itertools.product((100, 105), repeat=2):
            with self.subTest(n=n, m=m):
                src = self._rng.random_sample((n, 2))
                dst = self._rng.random_sample((m, 2))
                correspondences = bijective_matching(src, dst)
                distance = [
                    scipy.spatial.distance.euclidean(src[j], dst[i])
                    for j, i in correspondences
                ]
                self.assertTrue(all(itertools.starmap(operator.le, pairwise(distance))))


class UnitGridPointsTest(unittest.TestCase):
    """Unit tests for `unit_gridpoints()`."""

    def test_raises_invalid_mode(self) -> None:
        """
        `unit_gridpoints()` should raise a ValueError when passed an invalid
        mode.

        """
        self.assertRaises(ValueError, unit_gridpoints, (8, 8), mode="ab")

    def test_shape(self):
        """
        The array returned by `unit_gridpoints()` should have the correct shape.

        """
        for shape in itertools.product((3, 5, 8), repeat=2):
            for mode in ("ji", "xy"):
                with self.subTest(shape=shape, mode=mode):
                    n, m = shape
                    pts = unit_gridpoints(shape, mode=mode)
                    self.assertEqual(pts.ndim, 2)
                    self.assertEqual(pts.shape[0], n * m)
                    self.assertEqual(pts.shape[1], 2)

    def test_zero_mean(self):
        """The array returned by `unit_gridpoints()` should have zero mean."""
        for shape in itertools.product((3, 5, 8), repeat=2):
            for mode in ("ji", "xy"):
                with self.subTest(shape=shape, mode=mode):
                    pts = unit_gridpoints(shape, mode=mode)
                    numpy.testing.assert_array_almost_equal(numpy.mean(pts, axis=0), 0)

    def test_orientation(self):
        """
        The array returned by `unit_gridpoints()` should have the correct
        ordering.

        """
        for shape in itertools.product((3, 5, 8), repeat=2):
            with self.subTest(shape=shape):
                n, m = shape
                a = 0.5 * float(n - 1)
                b = 0.5 * float(m - 1)

                ji = unit_gridpoints(shape, mode="ji")
                xy = unit_gridpoints(shape, mode="xy")
                # `out[0]` is the top-left corner
                numpy.testing.assert_array_almost_equal(ji[0], (-a, -b))
                numpy.testing.assert_array_almost_equal(xy[0], (-b, a))
                # `out[m - 1]` is the top-right corner
                numpy.testing.assert_array_almost_equal(ji[m - 1], (-a, b))
                numpy.testing.assert_array_almost_equal(xy[m - 1], (b, a))
                # `out[(n - 1) * m]` is the bottom-left corner
                numpy.testing.assert_array_almost_equal(ji[(n - 1) * m], (a, -b))
                numpy.testing.assert_array_almost_equal(xy[(n - 1) * m], (-b, -a))
                # `out[-1]` is the bottom right corner
                numpy.testing.assert_array_almost_equal(ji[-1], (a, b))
                numpy.testing.assert_array_almost_equal(xy[-1], (b, -a))

    def test_known_values(self):
        """`unit_gridpoints()` should return known result with known input."""
        shape = (3, 5)
        ji = unit_gridpoints(shape, mode="ji")
        xy = unit_gridpoints(shape, mode="xy")
        _ji = numpy.column_stack(
            (
                [-1, -1, -1, -1, -1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1],
                [-2, -1, 0, 1, 2, -2, -1, 0, 1, 2, -2, -1, 0, 1, 2],
            )
        )
        _xy = numpy.column_stack(
            (
                [-2, -1, 0, 1, 2, -2, -1, 0, 1, 2, -2, -1, 0, 1, 2],
                [1, 1, 1, 1, 1, 0, 0, 0, 0, 0, -1, -1, -1, -1, -1],
            )
        )
        numpy.testing.assert_array_almost_equal(xy, _xy)
        numpy.testing.assert_array_almost_equal(ji, _ji)


def unit_gridpoints_graph(shape, tform, index):
    """
    Return the known good graph of the unit grid.

    Parameters
    ----------
    shape : tuple of two ints
        The shape of the grid given as a tuple `(height, width)`.
    tform : GeometricTransform
        The geometric transform to apply to the unit grid.
    index : ndarray
        Indices to order the vertices.

    """
    n, m = shape
    graph = WeightedGraph(n * m, directed=False)
    distances = numpy.hypot(*tform.matrix)
    for j in range(n - 1):
        for i in range(m):
            vertex = index[j * m + i]
            neighbor = index[(j + 1) * m + i]
            graph.add_edge((vertex, neighbor), distances[0])
    for j in range(n):
        for i in range(m - 1):
            vertex = index[j * m + i]
            neighbor = index[j * m + i + 1]
            graph.add_edge((vertex, neighbor), distances[1])
    return graph


class NearestNeighborGraphTest(unittest.TestCase):
    """Unittest for `nearest_neighbor_graph()`."""

    def setUp(self):
        """Ensure reproducible tests."""
        self._rng = check_random_state(12345)

    def test_full_grid(self):
        """
        `nearest_neighbor_graph()` should return the expected graph for a full
        grid.

        """
        for shape in itertools.product((5, 8), repeat=2):
            for i in range(50):
                with self.subTest(shape=shape, i=i):
                    n, m = shape
                    shuffle = self._rng.permutation(n * m)
                    index = numpy.argsort(shuffle)
                    tform = random_transform(seed=self._rng)
                    ji = tform.apply(unit_gridpoints(shape, mode="ji")[shuffle])
                    graph = unit_gridpoints_graph(shape, tform, index)
                    out = nearest_neighbor_graph(ji)
                    numpy.testing.assert_array_almost_equal(
                        graph.adjacency_matrix(), out.adjacency_matrix()
                    )

    def test_incomplete_grid(self):
        """
        `nearest_neighbor_graph()` should return the expected graph for a grid
        with one point missing.

        """
        for shape in itertools.product((5, 8), repeat=2):
            for i in range(50):
                with self.subTest(shape=shape, i=i):
                    n, m = shape
                    shuffle = self._rng.permutation(n * m)
                    index = numpy.argsort(shuffle)
                    tform = random_transform(seed=self._rng)
                    ji = tform.apply(unit_gridpoints(shape, mode="ji")[shuffle])[:-1]
                    _graph = unit_gridpoints_graph(shape, tform, index)
                    vertex = n * m - 1
                    # To prevent a RuntimeError loop over a copy
                    for neighbor in _graph[vertex].copy():
                        _graph.remove_edge((vertex, neighbor))
                    graph = _graph[:-1]
                    out = nearest_neighbor_graph(ji)
                    numpy.testing.assert_array_almost_equal(
                        graph.adjacency_matrix(), out.adjacency_matrix()
                    )


class CanonicalMatrixFormTest(unittest.TestCase):
    """Unittest for `_canonical_matrix_form()`."""

    def setUp(self):
        """Ensure reproducible tests."""
        self._rng = check_random_state(12345)

    def test_identity(self):
        """
        Given one of the symmetrical versions of the canonical grid
        `_canonical_matrix_form()` should return the permutation to
        transform it into the identity matrix.

        """
        for matrix in map(
            numpy.array,
            [
                [(1, 0), (0, 1)],
                [(0, 1), (-1, 0)],
                [(-1, 0), (0, -1)],
                [(0, -1), (1, 0)],
                [(0, 1), (1, 0)],
                [(-1, 0), (0, 1)],
                [(0, -1), (-1, 0)],
                [(1, 0), (0, -1)],
            ],
        ):
            sign, perm = _canonical_matrix_form(matrix)
            out = sign * matrix[:, perm]
            numpy.testing.assert_array_almost_equal(out, numpy.eye(2))

    def test_random_input(self):
        """
        `_canonical_matrix_form()` should accept any 2x2 transformation matrix.
        The resulting matrix should not contain a reflection (have a positive
        determinant) and have a rotation between ±45°.

        """
        for i in range(50):
            matrix = self._rng.standard_normal((2, 2))
            with self.subTest(matrix=matrix, i=i):
                sign, perm = _canonical_matrix_form(matrix)
                matrix = sign * matrix[:, perm]
                tform = AffineTransform(matrix)
                self.assertGreater(numpy.linalg.det(matrix), 0)
                self.assertGreater(tform.rotation, -0.25 * numpy.pi)
                self.assertLess(tform.rotation, 0.25 * numpy.pi)

    def test_angle_difference(self):
        """
        When provided with an input transformation with positive determinant
        (i.e. no reflection), the resulting change in rotation should be a
        multiple of 90°.

        """
        for i in range(50):
            matrix = self._rng.standard_normal((2, 2))
            if numpy.linalg.det(matrix) < 0:
                matrix = numpy.fliplr(matrix)
            with self.subTest(matrix=matrix, i=i):
                sign, perm = _canonical_matrix_form(matrix)
                tform1 = AffineTransform(matrix)
                tform2 = AffineTransform(sign * matrix[:, perm])
                delta = tform2.rotation - tform1.rotation
                n = int(round(delta / (0.5 * numpy.pi)))
                self.assertAlmostEqual(delta - n * 0.5 * numpy.pi, 0)


class EstimateGridOrientationTest(unittest.TestCase):
    """
    Unittest for `estimate_grid_orientation() and
    `estimate_grid_orientation_from_img()`.

    """

    def setUp(self):
        """Ensure reproducible tests."""
        self._rng = check_random_state(12345)

    def test_full_grid(self):
        """
        `estimate_grid_orientation()` should return the expected
        AffineTransform for a full grid.

        """
        for shape in [(5, 5), (8, 8)]:
            for i in range(50):
                with self.subTest(shape=shape, i=i):
                    n, m = shape
                    shuffle = self._rng.permutation(n * m)
                    tform = random_transform(seed=self._rng)
                    ji = tform.apply(unit_gridpoints(shape, mode="ji")[shuffle])
                    out, error_metric = estimate_grid_orientation(ji, shape, AffineTransform)
                    numpy.testing.assert_array_almost_equal(tform.matrix, out.matrix)
                    numpy.testing.assert_array_almost_equal(
                        tform.translation, out.translation
                    )
                    self.assertAlmostEqual(error_metric, 0.0)

    def test_incomplete_grid(self):
        """
        `estimate_grid_orientation()` should return the expected
        AffineTransform for a grid with one point missing.

        """
        for shape in [(5, 5), (8, 8)]:
            for i in range(50):
                with self.subTest(shape=shape, i=i):
                    n, m = shape
                    shuffle = self._rng.permutation(n * m)
                    tform = random_transform(seed=self._rng)
                    ji = tform.apply(unit_gridpoints(shape, mode="ji")[shuffle])[:-1]
                    out, error_metric = estimate_grid_orientation(ji, shape, AffineTransform)
                    numpy.testing.assert_array_almost_equal(tform.matrix, out.matrix)
                    numpy.testing.assert_array_almost_equal(
                        tform.translation, out.translation
                    )
                    self.assertAlmostEqual(error_metric, 0.0)

    def test_grid_with_error(self):
        """
        `estimate_grid_orientation()` should return the expected
        AffineTransform for a grid with one point in a different location.

        """
        for shape in [(5, 5), (8, 8)]:
            for i in range(50):
                with self.subTest(shape=shape, i=i):
                    n, m = shape
                    shuffle = self._rng.permutation(n * m)
                    tform = random_transform(seed=self._rng)
                    ji = tform.apply(unit_gridpoints(shape, mode="ji")[shuffle])
                    ji[-1] += 0.2
                    out, error_metric = estimate_grid_orientation(ji, shape, AffineTransform)
                    self.assertGreater(error_metric, 0.01)

    def test_from_img(self):
        """
        `estimate_grid_orientation_from_img()` should return the expected
        AffineTransform for a generated test image containing a grid of points.

        """
        shape = (8, 8)
        tform = AffineTransform(
            matrix=numpy.array([(33, -3), (5, 41)]),
            translation=numpy.array([770, 1030]),
        )
        ji = tform.apply(unit_gridpoints(shape, mode="ji"))

        # Generate a test image containing the grid of points
        sigma = 1.45
        image = synthetic.psf_gaussian((1542, 2056), ji, sigma)

        out, error_metric = estimate_grid_orientation_from_img(image, shape, AffineTransform, sigma)
        numpy.testing.assert_array_almost_equal(tform.matrix, out.matrix)
        numpy.testing.assert_array_almost_equal(tform.translation, out.translation)
        self.assertAlmostEqual(error_metric, 0.0)


if __name__ == "__main__":
    unittest.main()
