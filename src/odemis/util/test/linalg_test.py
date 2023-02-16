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

from odemis.util.linalg import tri_inv


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

def fit_plane_lstsq(coords: list):
    """
    Fit a plane to a set of 3D coordinates using least-squares fitting.
    :param coords: list of 3D coordinates
    :return: the z-position of the plane and the normal vector
    """
    A = numpy.ones_like(coords)
    A[:, :2] = coords[:, :2]
    B = coords[:, 2]
    # Using least-squares fitting minimize ||Ax - B||^2 with x in R3,
    # to find the equation for a plane: z = αx + βy + γ
    (a, b, gamma), *_ = numpy.linalg.lstsq(A, B)
    normal = (a, b, -1)
    return gamma, normal


def get_z_pos_on_plane(x: float, y: float, point_on_plane: tuple, normal: numpy.ndarray) -> float:
    """
    Get the z position on a plane given a point on the plane and the normal vector.
    :param x: the x-position of the point
    :param y: the y-position of the point
    :param point_on_plane: a point on the plane
    :param normal: the normal vector of the plane
    :return: the z-position of the point
    """
    d = -numpy.dot(point_on_plane, normal)
    a, b, c = normal
    # equation for a plane is ax + by + cz + d = 0
    z = -(d + a * x + b * y) / c
    return z


def get_point_on_plane(x: float, y: float, tr: tuple) -> float:
    """
    Get the z position on a plane given a triangle.
    :param x: the x-position of the point
    :param y: the y-position of the point
    :param tr: a triangle describing the plane
    :return: the z-position of the point
    """
    # These two vectors are in the plane
    v1 = tr[2] - tr[0]
    v2 = tr[1] - tr[0]
    # the cross product is a vector normal to the plane
    normal = numpy.cross(v1, v2)
    z = get_z_pos_on_plane(x, y, tr[1], normal)

    return z

class PlaneFittingTestCase(unittest.TestCase):
    def test_fit_plane_lstsq(self):
        """
        Test fitting a plane to a set of 3D coordinates using least-squares fitting.
        """
        # Create a plane with equation z = 2x + 3y + 4
        coords = numpy.array([(1, 2, 10), (2, 3, 17), (3, 4, 24), (4, 5, 31)])
        z, normal = fit_plane_lstsq(coords)
        self.assertAlmostEqual(z, 4)
        self.assertTrue(numpy.allclose(normal, (2, 3, -1)))

    def test_get_z_pos_on_plane(self):
        """
        Test getting the z position on a plane given a point on the plane and the normal vector.
        """
        # Create a plane with equation z = 2x + 3y + 4
        point_on_plane = (1, 2, 10)
        normal = (2, 3, -1)
        z = get_z_pos_on_plane(2, 3, point_on_plane, normal)
        self.assertAlmostEqual(z, 17)

    def test_get_point_on_plane(self):
        """
        Test getting the z position on a plane given a triangle.
        """
        # Create a plane with equation z = 2x + 3y + 4
        tr = numpy.array([(1, 2, 10), (2, 3, 17), (3, 4, 24)])
        z = get_point_on_plane(2, 3, tr)
        self.assertAlmostEqual(z, 17)

if __name__ == "__main__":
    unittest.main()
