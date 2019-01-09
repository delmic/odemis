#!/usr/bin/python
# -*- encoding: utf-8 -*-

import unittest
import numpy as np
from scipy.linalg.misc import LinAlgError

from odemis.acq.align.misc import tri_inv


class TriInvBadInput(unittest.TestCase):
    def testSingularMatrix(self):
        """
        tri_inv should fail when the input is a singular matrix
        """
        matrix = np.array([(0., 1.), (0., 1.)])
        self.assertRaises(LinAlgError, tri_inv, matrix)

    def testNotSquare(self):
        """
        tri_inv should fail when the input is a non-square matrix
        """
        matrix = np.arange(6.).reshape(2, 3)
        self.assertRaises(ValueError, tri_inv, matrix)


class InverseCheck(unittest.TestCase):
    def testInverseUpper(self):
        """
        c * tri_inv(c) == I, with c an upper triangular matrix
        """
        for i in range(1, 10):
            matrix = np.arange(float(i * i)).reshape(i, i) + 1.
            c = np.triu(matrix)
            self.assertTrue(np.allclose(np.dot(c, tri_inv(c)), np.eye(i)))

    def testInverseLower(self):
        """
        c * tri_inv(c) == I, with c a lower triangular matrix
        """
        for i in range(1, 10):
            matrix = np.arange(float(i * i)).reshape(i, i) + 1.
            c = np.tril(matrix)
            self.assertTrue(np.allclose(np.dot(c, tri_inv(c, lower=True)),
                            np.eye(i)))

    def testUnity(self):
        """
        tri_inv(I) == I
        """
        for i in range(1, 10):
            c = np.eye(i)
            self.assertTrue(np.allclose(tri_inv(c), c))


if __name__ == "__main__":
    unittest.main()
