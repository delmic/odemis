#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Andries Effting

Copyright © 2020 Andries Effting, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import math
import unittest

import numpy
from scipy.linalg.misc import LinAlgError

from odemis.util.linalg import tri_inv, get_z_pos_on_plane, get_point_on_plane, are_collinear, fit_plane_lstsq, \
    generate_triangulation_points


class TriInvBadInput(unittest.TestCase):
    def testSingularMatrix(self):
        """
        tri_inv should fail when the input is a singular matrix
        """
        matrix = numpy.array([(0., 1.), (0., 1.)])
        self.assertRaises(LinAlgError, tri_inv, matrix)

    def testNotSquare(self):
        """
        tri_inv should fail when the input is a non-square matrix
        """
        matrix = numpy.arange(6.).reshape(2, 3)
        self.assertRaises(ValueError, tri_inv, matrix)


class InverseCheck(unittest.TestCase):
    def testInverseUpper(self):
        """
        c * tri_inv(c) == I, with c an upper triangular matrix
        """
        for i in range(1, 10):
            matrix = numpy.arange(float(i * i)).reshape(i, i) + 1.
            c = numpy.triu(matrix)
            self.assertTrue(numpy.allclose(numpy.dot(c, tri_inv(c)), numpy.eye(i)))

    def testInverseLower(self):
        """
        c * tri_inv(c) == I, with c a lower triangular matrix
        """
        for i in range(1, 10):
            matrix = numpy.arange(float(i * i)).reshape(i, i) + 1.
            c = numpy.tril(matrix)
            self.assertTrue(numpy.allclose(numpy.dot(c, tri_inv(c, lower=True)),
                                           numpy.eye(i)))

    def testUnity(self):
        """
        tri_inv(I) == I
        """
        for i in range(1, 10):
            c = numpy.eye(i)
            self.assertTrue(numpy.allclose(tri_inv(c), c))


class PlaneFittingTestCase(unittest.TestCase):
    def test_fit_plane_lstsq(self):
        """
        Test fitting a plane to a set of 3D coordinates using least-squares fitting.
        """
        # Create a plane with equation z = 2x + 3y + 4
        # intersection of z plane given by third element of normal should be non-zero

        # linear points
        # more than one plane equation possible
        # check c is not infinity
        coords = numpy.array([(1, 2, 10), (2, 3, 17), (3, 4, 24), (4, 5, 31)])
        _, normal = fit_plane_lstsq(coords)
        self.assertFalse(numpy.isnan(normal[2]))  # assert that c is not nan

        # non-linear points
        # unique plane equation
        coords = numpy.array([(1, 2, 12), (-1, -2, -4), (3, 4, 22), (-3, -4, -14)])
        z, normal = fit_plane_lstsq(coords)
        self.assertAlmostEqual(z, 4)
        self.assertTrue(numpy.allclose(normal, (2, 3, -1)))

    def test_get_z_pos_on_plane(self):
        """
        Test getting the z position on a plane given a point on the plane and the normal vector.
        """
        # Create a plane with equation z = 2x + 3y + 4
        point_on_plane = (1, 2, 12)
        normal = [2, 3, -1]
        z = get_z_pos_on_plane(2, 3, point_on_plane, normal)
        self.assertEqual(z, 17)

    def test_get_point_on_plane(self):
        """
        Test getting the z position on a plane given a triangle.
        """
        # Create a plane with equation z = 2x + 3y + 4
        # Create non-linear points for the triangle
        tr = ((1, 2, 12), (-1, -2, -4), (3, 4, 22))
        z = get_point_on_plane(1, 5, tr)
        self.assertEqual(z, 21)

    def test_are_collinear(self):
        """
        Test if three points are collinear.
        """
        # Create a plane with equation z = 2x + 3y + 4
        # Create points in same line with above equation
        linear_points = numpy.array([[1, 2, 12], [2, 3, 17], [3, 4, 22]])
        self.assertTrue(are_collinear(linear_points[0], linear_points[1], linear_points[2]))

        # Create points not in same line with above equation
        non_linear_points = numpy.array([[1, 2, 12], [-1, -2, -4], [3, 4, 22]])
        self.assertFalse(are_collinear(non_linear_points[0], non_linear_points[1], non_linear_points[2]))

    def test_max_dist_generate_triangulation_points(self):
        """
        Test the number of focus points when the maximum distance between focus points changes
        """
        # 4x4 focus points
        max_dis = 1e-06
        given_area_coords = (0,  0, 3.9e-06,  3.9e-06)
        focus_points = generate_triangulation_points(max_dis, given_area_coords)
        self.assertEqual(len(focus_points), 16)
        self.assertEqual(len(focus_points[0]), 2)  # (x,y)

        # 3x3 focus points
        max_dis = 1.5e-06  #
        given_area_coords = (0,  0, 3.9e-06,  3.9e-06)
        focus_points = generate_triangulation_points(max_dis, given_area_coords)
        self.assertEqual(len(focus_points), 9)
        self.assertEqual(len(focus_points[0]), 2)  # (x,y)

        # 2x2 focus points
        max_dis = 2e-06  #
        given_area_coords = (0,  0, 3.9e-06,  3.9e-06)
        focus_points = generate_triangulation_points(max_dis, given_area_coords)
        self.assertEqual(len(focus_points), 4)
        self.assertEqual(len(focus_points[0]), 2)  # (x,y)

        # 1 focus point
        max_dis = 4e-06  #
        given_area_coords = (0,  0, 3.9e-06,  3.9e-06)
        focus_points = generate_triangulation_points(max_dis, given_area_coords)
        self.assertEqual(len(focus_points), 1)
        self.assertEqual(len(focus_points[0]), 2)  # (x,y)

    def test_area_generate_triangulation_points(self):
        """
        Test the number of focus points when the given area changes
        """
        # 4x4 focus points when given area is a square
        max_dis = 1e-06
        area_coords = (0,  0, 3.9e-06,  3.9e-06)
        focus_points = generate_triangulation_points(max_dis, area_coords)
        self.assertEqual(len(focus_points), 16)
        self.assertEqual(len(focus_points[0]), 2)  # (x,y)

        # 4x3 focus points when the given area is a rectangle
        max_dis = 1e-06  #
        area_coords = (0,  0, 3.9e-06,  2.9e-06)
        focus_points = generate_triangulation_points(max_dis, area_coords)
        self.assertEqual(len(focus_points), 12)
        self.assertEqual(len(focus_points[0]), 2)  # (x,y)

    def test_focus_values_generate_triangulation_points(self):
        """
        Test that values of focus points are within the given area and check boundary conditions
        """
        # 4x4 focus points when given area is a square
        max_dis = 1e-06
        area_coords = (0,  0, 3.9e-06,  3.9e-06)
        focus_points = generate_triangulation_points(max_dis, area_coords)
        x_points = [i[0] for i in focus_points]
        y_points = [i[1] for i in focus_points]
        xmin = min(x_points)
        ymin = min(y_points)
        xmax = max(x_points)
        ymax = max(y_points)

        # The min and maximum of focus points should be within the given area coordinates
        self.assertTrue(area_coords[0] <= xmin <= xmax <= area_coords[2])
        self.assertTrue(area_coords[1] <= ymin <= ymax <= area_coords[3])

        # Test that the distance between the nearest focus point and a selected point within the given area is
        # less than the maximum distance (max_dis)
        for p in [(0, 0), (0.5e-06, 0.5e-06), (2e-06, 2e-06)]:
            shortest_dist = min(math.hypot(f[0] - p[0], f[1] - p[1]) for f in focus_points)
            self.assertLessEqual(shortest_dist, max_dis)


if __name__ == "__main__":
    unittest.main()
