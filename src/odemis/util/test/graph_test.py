# -*- encoding: utf-8 -*-
"""
graph_test.py : unit tests for odemis.util.graph

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
import unittest

from odemis.util import pairwise
from odemis.util.graph import GraphBase


def complete_graph(n, graph_type):
    """
    A complete graph is a simple undirected graph in which every pair of
    distinct vertices is connected by a unique edge.

    Parameters
    ----------
    n : int
        Order of the graph (number of vertices).
    graph_type : GraphBase
        Graph type.

    """
    graph = graph_type(n, directed=False)
    for j in range(n):
        for i in range(j + 1, n):
            graph.add_edge((j, i))
    return graph


def wheel_graph(n, graph_type):
    """
    A wheel graph consists of a single universal vertex connected to all
    vertices of a cycle of `(n - 1)` vertices.

    Parameters
    ----------
    n : int
        Order of the graph (number of vertices).
    graph_type : GraphBase
        Graph type.

    """
    if n < 3:
        raise ValueError("")
    graph = graph_type(n, directed=False)
    for i in range(1, n):
        graph.add_edge((0, i))
    for j, i in pairwise(range(1, n)):
        graph.add_edge((j, i))
    graph.add_edge((1, n - 1))
    return graph


class IterEdgesTest(unittest.TestCase):
    """Unit tests for `GraphBase.iter_edges()`."""

    def test_complete_graph_undirected(self):
        """
        `iter_edges()` should return all edges in an undirected complete graph.
        The edges `(j, i)` and `(i, j)` should be treated identical and only
        reported once as `(j, i)` with `j < i` when `directed=False`.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = complete_graph(n, graph_type)
                    edges = itertools.combinations(range(n), 2)
                    out = list(graph.iter_edges(directed=False))
                    self.assertCountEqual(edges, out)
                    for vertex, neighbor in out:
                        self.assertLess(vertex, neighbor)

    def test_complete_graph_directed(self):
        """
        `iter_edges()` should return all edges in a directed complete graph.
        The edges `(j, i)` and `(i, j)` should be treated as two distinct edges
        when `directed=True`.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = complete_graph(n, graph_type)
                    edges = itertools.permutations(range(n), 2)
                    out = graph.iter_edges(directed=True)
                    self.assertCountEqual(edges, out)


class IterTrianglesTest(unittest.TestCase):
    """Unit tests for `GraphBase.iter_triangles()`."""

    _known_data = [
        (
            [
                {1: 1, 2: 1, 3: 1, 4: 1},
                {0: 1, 2: 1, 3: 1},
                {0: 1, 1: 1},
                {0: 1, 1: 1, 4: 1},
                {0: 1, 3: 1},
            ],
            [(0, 1, 2), (0, 1, 3), (0, 3, 4)],
        ),
        (
            [{1: 1, 2: 1}, {0: 1, 2: 1, 3: 1}, {0: 1, 1: 1, 3: 1}, {1: 1, 2: 1}],
            [(0, 1, 2), (1, 2, 3)],
        ),
    ]

    def test_known_data(self):
        """
        `iter_triangles()` should return known good results for known input.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for initlist, triangles in self._known_data:
                    graph = graph_type(initlist)
                    out = graph.iter_triangles()
                    self.assertCountEqual(triangles, out)

    def test_complete_graph(self):
        """
        `iter_triangles()` should return all triangles in a complete graph.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = complete_graph(n, graph_type)
                    triangles = list(itertools.combinations(range(n), 3))
                    out = graph.iter_triangles()
                    self.assertCountEqual(triangles, out)

    def test_wheel_graph(self):
        """`iter_triangles()` should return all triangles in a wheel graph."""
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = wheel_graph(n, graph_type)
                    triangles = [(0, u, v) for u, v in pairwise(range(1, n))]
                    if n > 3:
                        triangles.append((0, 1, n - 1))
                    if n == 4:
                        triangles.append((1, 2, 3))
                    out = graph.iter_triangles()
                    self.assertCountEqual(triangles, out)


class RemoveTrianglesTest(unittest.TestCase):
    """Unit tests for `GraphBase.remove_triangles()`."""

    def test_complete_graph(self):
        """`remove_triangles()` should remove all triangles in a complete graph."""
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = complete_graph(n, graph_type)
                    graph.remove_triangles()
                    triangles = list(graph.iter_triangles())
                    self.assertListEqual(triangles, [])

    def test_wheel_graph(self):
        """`remove_triangles()` should remove all triangles in a wheel graph."""
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = wheel_graph(n, graph_type)
                    graph.remove_triangles()
                    triangles = list(graph.iter_triangles())
                    self.assertListEqual(triangles, [])


if __name__ == "__main__":
    unittest.main()
