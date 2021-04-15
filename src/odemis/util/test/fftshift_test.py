# -*- encoding: utf-8 -*-
"""
Created on 15 Apr 2021

@author: Andries Effting

Copyright © 2021 Andries Effting, Delmic

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

import itertools
import unittest
import numpy as np

from odemis.util.fftshift import rfftshift2


class TestRfftshift2(unittest.TestCase):

    def testReadOnlyInput(self):
        """
        rfftshift2 should not over-write the input array.
        """
        for shape in itertools.product((3, 4, 5, 6, 7), repeat=2):
            for shift in itertools.product((0, 1, -2, 0.5, -0.3), repeat=2):
                _a = np.ones(shape)
                a = _a.copy()
                out = rfftshift2(a, shift)  # noqa: F841
                np.testing.assert_array_equal(_a, a)

    def testSameShape(self):
        """
        rfftshift2 should return an array of the same shape as the input array.
        """
        for shape in itertools.product((3, 4, 5, 6, 7), repeat=2):
            for shift in itertools.product((0, 1, -2, 0.5, -0.3), repeat=2):
                a = np.ones(shape)
                out = rfftshift2(a, shift)
                self.assertEqual(shape, out.shape)

    def testParseval(self):
        """
        rfftshift2 should conserve the sum squared of the input array.
        """
        for shape in itertools.product((3, 4, 5, 6, 7), repeat=2):
            is_even = any(x % 2 == 0 for x in shape)
            for shift in itertools.product((0, 1, -2, 0.5, -0.3), repeat=2):
                is_subpixel = any(isinstance(x, float) for x in shift)
                if is_even and is_subpixel:
                    # When applying a sub-pixel shift on a ndarray with at
                    # least one dimension of even length, the Parseval identity
                    # is not fulfilled. This is caused by the enforcement of
                    # conjugate symmetry in rfftshift2.
                    continue
                a = np.random.rand(*shape)
                out = rfftshift2(a, shift)
                self.assertAlmostEqual(np.sum(a * a), np.sum(out * out))

    def testTrivial(self):
        """
        rfftshift2 should return the same array when shifting by an integer
        amount of pixels.
        """
        for n, m in itertools.product((3, 4, 5, 6, 7), repeat=2):
            for shift in [(j, i) for j in range(-n, n) for i in range(-m, m)]:
                a = np.random.rand(n, m)
                expected = np.roll(a, shift, axis=(0, 1))
                out = rfftshift2(a, shift)
                np.testing.assert_array_almost_equal(expected, out)

    def testSymmetry(self):
        """
        rfftshift2 should return a similar result when applying the same shift
        in the opposite direction to a flipped image.
        """
        for shape in itertools.product((3, 4, 5, 6, 7), repeat=2):
            for shift in itertools.product((0, 1, -2, 0.5, -0.3), repeat=2):
                shift = np.asarray(shift)
                a = np.random.rand(*shape)
                out1 = rfftshift2(a, shift)
                out2 = rfftshift2(a[::-1, ::-1], -shift)[::-1, ::-1]
                np.testing.assert_array_almost_equal(out1, out2)

    def testNoSkew(self):
        """
        rfftshift2 should return a result that is symmetrically distributed
        when shifting by exactly half a pixel.
        """
        shift = (0.5, 0.5)
        for shape in itertools.product(range(2, 11, 2), repeat=2):
            ji = tuple(x // 2 - 1 for x in shape)
            a = np.zeros(shape)
            a[ji] = 1
            out = rfftshift2(a, shift)
            np.testing.assert_array_almost_equal(out, np.flipud(out))
            np.testing.assert_array_almost_equal(out, np.fliplr(out))


if __name__ == '__main__':
    unittest.main()
