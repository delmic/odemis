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


if __name__ == "__main__":
    unittest.main()
