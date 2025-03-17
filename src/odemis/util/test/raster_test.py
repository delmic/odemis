#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

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

from odemis.util.raster import get_possible_intersections


class TestGetPossibleIntersections(unittest.TestCase):
    def test_basic_line(self):
        row_pairs = [(0, 3)]
        col_pairs = [(0, 3)]
        expected = {(0, 0), (1, 1), (2, 2), (3, 3)}
        self.assertEqual(get_possible_intersections(row_pairs, col_pairs, include_neighbors=False), expected)

    def test_single_point(self):
        row_pairs = [(2, 2)]
        col_pairs = [(3, 3)]
        expected = {(2, 3)}
        self.assertEqual(get_possible_intersections(row_pairs, col_pairs, include_neighbors=False), expected)

    def test_horizontal_line(self):
        row_pairs = [(1, 1)]
        col_pairs = [(0, 4)]
        expected = {(1, 0), (1, 1), (1, 2), (1, 3), (1, 4)}
        self.assertEqual(get_possible_intersections(row_pairs, col_pairs, include_neighbors=False), expected)

    def test_vertical_line(self):
        row_pairs = [(0, 4)]
        col_pairs = [(2, 2)]
        expected = {(0, 2), (1, 2), (2, 2), (3, 2), (4, 2)}
        self.assertEqual(get_possible_intersections(row_pairs, col_pairs, include_neighbors=False), expected)

    def test_with_neighbors(self):
        row_pairs = [(1, 3)]
        col_pairs = [(1, 3)]
        result = get_possible_intersections(row_pairs, col_pairs, include_neighbors=True)
        for row, col in [(1, 1), (2, 2), (3, 3)]:
            self.assertIn((row, col), result)
            self.assertIn((row + 1, col), result)
            self.assertIn((row - 1, col), result)
            self.assertIn((row, col + 1), result)
            self.assertIn((row, col - 1), result)

    def test_diagonal_line(self):
        row_pairs = [(2, 5)]
        col_pairs = [(5, 2)]
        expected = {(2, 5), (3, 4), (4, 3), (5, 2)}
        self.assertEqual(get_possible_intersections(row_pairs, col_pairs, include_neighbors=False), expected)

    def test_empty_input(self):
        row_pairs = []
        col_pairs = []
        expected = set()
        self.assertEqual(get_possible_intersections(row_pairs, col_pairs, include_neighbors=False), expected)

if __name__ == "__main__":
    unittest.main()
