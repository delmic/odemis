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

import numpy

from odemis.util.raster import get_polygon_grid_cells


class TestGetPossibleIntersections(unittest.TestCase):
    def test_triangle(self):
        polygon = numpy.array([[1, 2], [2, 4], [3, 2], [1, 2]])
        expected = {(1, 2), (2, 4), (3, 2), (2, 3), (2, 2), (1, 3)}  # Adjusted for correct Bresenham tracing
        result = get_polygon_grid_cells(polygon)
        self.assertEqual(result, expected)

    def test_pentagon(self):
        polygon = numpy.array([[3, 3], [6, 2], [7, 4], [4, 6], [3, 4], [3, 3]])
        expected = {(7, 4), (6, 2), (5, 5), (3, 4), (6, 5), (4, 3), (4, 6), (4, 5), (3, 3), (6, 3), (5, 2)}
        result = get_polygon_grid_cells(polygon)
        self.assertEqual(result, expected)

    def test_auto_close_polygon(self):
        polygon = numpy.array([[2, 1], [3, 1], [4, 1], [3, 3]])  # Not explicitly closed
        expected = {(2, 1), (3, 1), (4, 1), (3, 3), (3, 2), (4, 2)}  # Auto-closing edge must be included
        result = get_polygon_grid_cells(polygon)
        self.assertEqual(result, expected)

    def test_square(self):
        """Tests a simple closed square."""
        polygon_vertices = numpy.array([[2, 2], [2, 5], [5, 5], [5, 2], [2, 2]])  # Square
        result = get_polygon_grid_cells(polygon_vertices, include_neighbours=False)
        expected = {
            (2, 2), (2, 3), (2, 4), (2, 5),
            (3, 5), (4, 5), (5, 5),
            (5, 4), (5, 3), (5, 2),
            (4, 2), (3, 2)
        }
        self.assertEqual(result, expected)

    def test_neighbours_enabled(self):
        """Tests polygon intersection with neighbour inclusion."""
        polygon_vertices = numpy.array([[2, 2], [4, 5], [6, 2], [2, 2]])  # Triangle with neighbours
        result = get_polygon_grid_cells(polygon_vertices, include_neighbours=True)

        for row, col in [(2, 2), (3, 3), (4, 4), (5, 3), (6, 2)]:
            self.assertIn((row, col), result)
            self.assertIn((row + 1, col), result)
            self.assertIn((row - 1, col), result)
            self.assertIn((row, col + 1), result)
            self.assertIn((row, col - 1), result)

    def test_invalid_polygon(self):
        """Tests that an invalid polygon (less than 3 vertices) raises an error."""
        polygon_vertices = numpy.array([[1, 1], [3, 3]])  # Only 2 points (invalid)
        with self.assertRaises(ValueError):
            get_polygon_grid_cells(polygon_vertices, include_neighbours=False)
        polygon_vertices = numpy.array([[1, 1, 1], [3, 3, 3], [4, 4, 4], [5, 5, 5]])
        with self.assertRaises(ValueError):
            get_polygon_grid_cells(polygon_vertices, include_neighbours=False)

if __name__ == "__main__":
    unittest.main()
