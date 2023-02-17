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

import unittest

import numpy
from scipy.linalg.misc import LinAlgError

from odemis.util.linalg import tri_inv, get_z_pos_on_plane, get_point_on_plane, are_collinear, fit_plane_lstsq


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

        # linear points
        # intersection of z plane given by third element of normal should be non-zero
        coords = numpy.array([(1, 2, 10), (2, 3, 17), (3, 4, 24), (4, 5, 31)])
        _, normal = fit_plane_lstsq(coords)
        self.assertNotEqual(True, numpy.isnan(normal[2]))  # assert that c is not nan

        # non-linear points
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


if __name__ == "__main__":
    unittest.main()
