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
import copy
import functools
import itertools
import random
import unittest
from typing import Callable, Optional, Type

import numpy

from odemis.util import pairwise
from odemis.util.graph import (
    AntiSymmetricGraph,
    DisjointSetUnion,
    Graph,
    GraphBase,
    UnweightedGraph,
    WeightedGraph,
    depth_first_walk,
    is_connected,
    iter_triangles,
    maximum_spanning_tree,
    minimum_spanning_tree,
    remove_triangles,
)


def complete_graph(n: int, graph_type: Type[Graph] = UnweightedGraph) -> Graph:
    """
    A complete graph is a simple undirected graph in which every pair of
    distinct vertices is connected by a unique edge.

    Parameters
    ----------
    n : int
        Order of the graph (number of vertices).
    graph_type : Graph, optional
        Graph type, default is UnweightedGraph.

    """
    graph = graph_type(n, directed=False)
    for j in range(n):
        for i in range(j + 1, n):
            graph.add_edge((j, i), weight=1)
    return graph


def wheel_graph(n: int, graph_type: Type[Graph] = UnweightedGraph) -> Graph:
    """
    A wheel graph consists of a single universal vertex connected to all
    vertices of a cycle of `(n - 1)` vertices.

    Parameters
    ----------
    n : int
        Order of the graph (number of vertices).
    graph_type : Graph, optional
        Graph type, default is UnweightedGraph.

    """
    if n < 3:
        raise ValueError("")
    graph = graph_type(n, directed=False)
    for i in range(1, n):
        graph.add_edge((0, i), weight=1)
    for j, i in pairwise(range(1, n)):
        graph.add_edge((j, i), weight=1)
    graph.add_edge((1, n - 1), weight=1)
    return graph


def random_weighted_tree(
    n: int, randfunc: Optional[Callable[[], float]] = None
) -> WeightedGraph:
    """
    Returns a random tree of order `n`.

    Parameters
    ----------
    n : int
        Order of the tree (number of vertices).
    randfunc : Callable, optional
        A zero-argument function returning a random float. By default, this is
        the function random.random().

    """
    if randfunc is None:
        randfunc = random.random
    tree = WeightedGraph(n, directed=False)
    root, *vertices = random.sample(range(n), n)
    connected = [root]
    for vertex in vertices:
        parent = random.choice(connected)
        weight = randfunc()
        tree.add_edge((parent, vertex), weight)
        connected.append(vertex)
    return tree


class GraphTest(unittest.TestCase):
    """Unit tests for `UnweightedGraph` and `WeightedGraph`."""

    def test_init_zero_order_zero_size(self) -> None:
        """
        When initialized with no arguments, a graph of zero order and zero size
        should be returned.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                graph = graph_type()
                self.assertListEqual(list(graph), [])

    def test_init_zero_size(self) -> None:
        """
        When initialized with integer argument, a graph of zero size and
        non-zero order should be returned.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                n = 10
                graph = graph_type(n)
                if graph_type is UnweightedGraph:
                    expected = [set() for _ in range(n)]
                else:
                    expected = [dict() for _ in range(n)]
                self.assertListEqual(list(graph), expected)

    def test_init_iterable(self) -> None:
        """A graph can be initialized using an iterable."""
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                if graph_type is UnweightedGraph:
                    iterable = [{1, 2, 3}, {0, 2, 3}, {0, 1, 3}, {0, 1, 2}]
                else:
                    iterable = [
                        {1: 1, 2: 1, 3: 1},
                        {0: 1, 2: 1, 3: 1},
                        {0: 1, 1: 1, 3: 1},
                        {0: 1, 2: 1, 1: 1},
                    ]
                graph = graph_type(iterable)
                self.assertListEqual(list(graph), iterable)

    def test_init_raises(self) -> None:
        """
        When in initialized with an argument that is not an integer or
        iterable, a TypeError should be raised.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                self.assertRaises(TypeError, graph_type, 3.14)

    def test_adjacency_matrix(self) -> None:
        """
        A known-good adjacency matrix should be returned for known input data.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                graph = wheel_graph(5, graph_type)
                expected = numpy.array(
                    [
                        (0, 1, 1, 1, 1),
                        (1, 0, 1, 0, 1),
                        (1, 1, 0, 1, 0),
                        (1, 0, 1, 0, 1),
                        (1, 1, 0, 1, 0),
                    ],
                )
                numpy.testing.assert_array_equal(graph.adjacency_matrix(), expected)

    def test_add_edge(self) -> None:
        """
        Add an edge to a graph. If the graph is undirected, test that the
        reversed edge is also added to the graph.

        """
        for graph_type in GraphBase.__subclasses__():
            for directed in (False, True):
                with self.subTest(graph_type=graph_type.__name__, directed=directed):
                    graph = graph_type(5, directed=directed)
                    graph.add_edge((2, 3), weight=1)
                    self.assertIn((2, 3), graph)
                    self.assertEqual((3, 2) in graph, not directed)

    def test_remove_edge(self) -> None:
        """
        Remove an edge from a graph. If the graph is undirected, test that the
        reversed edge is also removed.

        """
        for graph_type in GraphBase.__subclasses__():
            for directed in (False, True):
                with self.subTest(graph_type=graph_type.__name__, directed=directed):
                    data = list(wheel_graph(5, graph_type))
                    graph = graph_type(data, directed=directed)
                    graph.remove_edge((2, 3))
                    self.assertNotIn((2, 3), graph)
                    self.assertEqual((3, 2) in graph, directed)

    def test_edge_weight(self) -> None:
        """Test that `get_edge_weight()` returns the correct value."""
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                graph = wheel_graph(5, graph_type)
                self.assertEqual(graph.get_edge_weight((0, 1)), 1)

    def test_edge_weight_raises(self) -> None:
        """
        When called with an edge that does not exist in the graph,
        `get_edge_weight()` should raise a KeyError.

        """
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                graph = wheel_graph(5, graph_type)
                self.assertRaises(KeyError, graph.get_edge_weight, (1, 3))

    def test_iter_edges_undirected(self) -> None:
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

    def test_iter_edges_directed(self) -> None:
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


class AntiSymmetricGraphTest(unittest.TestCase):
    """Unit tests for `AntiSymmetricGraph`."""

    def test_adjacency_matrix(self) -> None:
        """The adjacency matrix of an AntiSymmetricGraph should be skew-symmetric."""
        n = 10
        graph = AntiSymmetricGraph(n)
        for j in range(n):
            for i in range(j + 1, n):
                graph.add_edge((j, i), weight=random.random())
        matrix = graph.adjacency_matrix()
        numpy.testing.assert_array_equal(matrix.T, -matrix)


class IterTrianglesTest(unittest.TestCase):
    """Unit tests for `GraphBase.iter_triangles()`."""

    _known_data = [
        (
            [{1, 2, 3, 4}, {0, 2, 3}, {0, 1}, {0, 1, 4}, {0, 3}],
            [(0, 1, 2), (0, 1, 3), (0, 3, 4)],
        ),
        (
            [{1, 2}, {0, 2, 3}, {0, 1, 3}, {1, 2}],
            [(0, 1, 2), (1, 2, 3)],
        ),
    ]

    def test_known_data(self) -> None:
        """
        `iter_triangles()` should return known good results for known input.

        """
        for initlist, triangles in self._known_data:
            graph = UnweightedGraph(initlist)
            out = iter_triangles(graph)
            self.assertCountEqual(triangles, out)

    def test_complete_graph(self) -> None:
        """
        `iter_triangles()` should return all triangles in a complete graph.

        """
        for n in range(3, 10):
            graph = complete_graph(n, UnweightedGraph)
            triangles = list(itertools.combinations(range(n), 3))
            out = iter_triangles(graph)
            self.assertCountEqual(triangles, out)

    def test_wheel_graph(self) -> None:
        """`iter_triangles()` should return all triangles in a wheel graph."""
        for n in range(3, 10):
            graph = wheel_graph(n, UnweightedGraph)
            triangles = [(0, u, v) for u, v in pairwise(range(1, n))]
            if n > 3:
                triangles.append((0, 1, n - 1))
            if n == 4:
                triangles.append((1, 2, 3))
            out = iter_triangles(graph)
            self.assertCountEqual(triangles, out)


class RemoveTrianglesTest(unittest.TestCase):
    """Unit tests for `GraphBase.remove_triangles()`."""

    def test_complete_graph(self) -> None:
        """`remove_triangles()` should remove all triangles in a complete graph."""
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = remove_triangles(complete_graph(n, graph_type))
                    triangles = list(iter_triangles(graph))
                    self.assertListEqual(triangles, [])

    def test_wheel_graph(self) -> None:
        """`remove_triangles()` should remove all triangles in a wheel graph."""
        for graph_type in GraphBase.__subclasses__():
            with self.subTest(graph_type=graph_type.__name__):
                for n in range(3, 10):
                    graph = remove_triangles(wheel_graph(n, graph_type))
                    triangles = list(iter_triangles(graph))
                    self.assertListEqual(triangles, [])

    def test_overwrite(self) -> None:
        """
        `remove_triangles()` should modify a graph in-place when overwrite is
        True.

        """
        graph = wheel_graph(10, UnweightedGraph)
        out = remove_triangles(graph, overwrite=True)
        triangles = list(iter_triangles(graph))
        self.assertIs(graph, out)
        self.assertListEqual(triangles, [])

    def test_no_overwrite(self) -> None:
        """
        `remove_triangles()` should not modify the input graph when overwrite
        is False.

        """
        graph = wheel_graph(10, UnweightedGraph)
        store = copy.deepcopy(graph)
        out = remove_triangles(graph, overwrite=False)
        triangles = list(iter_triangles(out))
        self.assertIsNot(graph, out)
        self.assertListEqual(triangles, [])
        for edge in store.iter_edges():
            self.assertIn(edge, graph)

    def test_no_triangles(self) -> None:
        """
        `remove_triangles()` should return an unmodified graph if the input
        graph does not contain any triangle.

        """
        data = [{1}, {2}, {3}, {4}, {5}, {0}]
        graph = UnweightedGraph(data)
        self.assertEqual(graph, remove_triangles(graph))


class DisjointSetUnionTest(unittest.TestCase):
    """Unit tests for `DisjointSetUnion`."""

    def test_negative_init(self) -> None:
        """
        DisjointSetUnion should raise a ValueError when initialized with a not
        strictly positive number

        """
        self.assertRaises(ValueError, DisjointSetUnion, 0)
        self.assertRaises(ValueError, DisjointSetUnion, -1)

    def test_init_disjoint(self) -> None:
        """Upon initialization, all nodes are disjoint."""
        n = 10
        dsu = DisjointSetUnion(n)
        for a, b in itertools.permutations(range(n), 2):
            self.assertNotEqual(dsu.find(a), dsu.find(b))

    def test_merge_one(self) -> None:
        """
        With a single union operation, only the two nodes merged should belong
        to the same set.

        """
        n = 10
        dsu = DisjointSetUnion(n)
        dsu.union(3, 7)
        for a, b in itertools.permutations(range(n), 2):
            if (a, b) in [(3, 7), (7, 3)]:
                self.assertEqual(dsu.find(a), dsu.find(b))
            else:
                self.assertNotEqual(dsu.find(a), dsu.find(b))

    def test_merge_all(self) -> None:
        """When fully merged, all nodes should belong to the same set."""
        n = 10
        dsu = DisjointSetUnion(n)
        for a in range(n - 1):
            dsu.union(a, a + 1)
        for a, b in itertools.permutations(range(n), 2):
            self.assertEqual(dsu.find(a), dsu.find(b))

    def test_merge_twice(self) -> None:
        """
        When merging two nodes that already belong to the same set, nothing
        should change.

        """
        n = 10
        dsu = DisjointSetUnion(n)
        dsu.union(3, 7)
        roots0 = list(map(dsu.find, range(n)))
        dsu.union(3, 7)
        roots1 = list(map(dsu.find, range(n)))
        self.assertListEqual(roots0, roots1)

    def test_merge_largest(self) -> None:
        """When merging two nodes, the smallest is merged into the largest."""
        n = 10
        dsu = DisjointSetUnion(n)
        for a in range(n - 1):
            if a == 3:
                continue
            dsu.union(a, a + 1)
        root = dsu.find(4)
        dsu.union(3, 4)
        self.assertEqual(root, dsu.find(3))
        self.assertEqual(root, dsu.find(4))


class MinimumSpanningTreeTest(unittest.TestCase):
    """Unit tests for `minimum_spanning_tree()`."""

    def test_raises(self) -> None:
        """
        When given a directed graph, `minimum_spanning_tree()` should raise a
        ValueError.

        """
        graph = WeightedGraph([{1: 1}, {}, {3: 8, 4: 5}, {4: 1}, {}], directed=True)
        self.assertRaises(ValueError, minimum_spanning_tree, graph)

    def test_known_result(self) -> None:
        """
        `minimum_spanning_tree()` should return a known good result for known
        input data.

        """
        graph = WeightedGraph(
            [{1: 1}, {0: 1}, {3: 8, 4: 5}, {2: 8, 4: 1}, {2: 5, 3: 1}], directed=False
        )
        expected = [{1: 1}, {0: 1}, {4: 5}, {4: 1}, {2: 5, 3: 1}]
        mst = minimum_spanning_tree(graph)
        self.assertListEqual(list(mst), expected)

    def test_identity(self) -> None:
        """
        If the input provided to `minimum_spanning_tree()` already is a tree,
        then the output should be identical to the input.

        """
        for n in (5, 25, 100):
            with self.subTest(n=n):
                graph = random_weighted_tree(n)
                mst = minimum_spanning_tree(graph)
                self.assertListEqual(list(mst), list(graph))

    def test_random(self) -> None:
        """
        Generate a complete graph with random weights with known minimum
        spanning tree. Check that `minimum_spanning_tree()` returns the correct
        result.

        """
        n = 100
        tree = random_weighted_tree(n)
        graph = copy.deepcopy(tree)
        for edge in complete_graph(n).iter_edges():
            if edge in tree:
                continue
            graph.add_edge(edge, weight=random.uniform(1, 2))
        mst = minimum_spanning_tree(graph)
        self.assertListEqual(list(mst), list(tree))


class MaximumSpanningTreeTest(unittest.TestCase):
    """Unit tests for `maximum_spanning_tree()`."""

    def test_raises(self) -> None:
        """
        When given a directed graph, `maximum_spanning_tree()` should raise a
        ValueError.

        """
        graph = WeightedGraph([{1: 1}, {}, {3: 8, 4: 5}, {4: 1}, {}], directed=True)
        self.assertRaises(ValueError, maximum_spanning_tree, graph)

    def test_known_result(self) -> None:
        """
        `maximum_spanning_tree()` should return a known good result for known
        input data.

        """
        graph = WeightedGraph(
            [{1: 1}, {0: 1}, {3: 8, 4: 5}, {2: 8, 4: 1}, {2: 5, 3: 1}], directed=False
        )
        expected = [{1: 1}, {0: 1}, {3: 8, 4: 5}, {2: 8}, {2: 5}]
        mst = maximum_spanning_tree(graph)
        self.assertListEqual(list(mst), expected)

    def test_identity(self) -> None:
        """
        If the input provided to `maximum_spanning_tree()` already is a tree,
        then the output should be identical to the input.

        """
        for n in (5, 25, 100):
            with self.subTest(n=n):
                graph = random_weighted_tree(n)
                mst = maximum_spanning_tree(graph)
                self.assertListEqual(list(mst), list(graph))

    def test_random(self) -> None:
        """
        Generate a complete graph with random weights with known maximum
        spanning tree. Check that `maximum_spanning_tree()` returns the correct
        result.

        """
        n = 100
        tree = random_weighted_tree(n, functools.partial(random.uniform, a=1, b=2))
        graph = copy.deepcopy(tree)
        for edge in complete_graph(n).iter_edges():
            if edge in tree:
                continue
            graph.add_edge(edge, weight=random.random())
        mst = maximum_spanning_tree(graph)
        self.assertListEqual(list(mst), list(tree))


class DepthFirstWalkTest(unittest.TestCase):
    """Unit tests for `depth_first_walk()`."""

    def test_known_result(self) -> None:
        """
        `depth_first_walk()` should return a known good result for known input
        data.

        """
        graph = UnweightedGraph(
            [{1, 2, 4}, {0, 3, 5}, {0, 6}, {1}, {0}, {1}, {2}], directed=False
        )
        expected = [(None, 0), (0, 4), (0, 2), (2, 6), (0, 1), (1, 5), (1, 3)]
        edges = depth_first_walk(graph, 0)
        self.assertCountEqual(edges, expected)

    def test_connected_graph(self) -> None:
        """
        When provided a connected graph, `depth_first_walk()` should visit all
        nodes exactly once.

        """
        for n in range(3, 10):
            graph = wheel_graph(n, UnweightedGraph)
            for start in range(0, n):
                vertices = [edge[1] for edge in depth_first_walk(graph, start)]
                self.assertCountEqual(vertices, range(n))


class IsConnectedTest(unittest.TestCase):
    """Unit tests for `is_connected()`."""

    def test_connected(self):
        """`is_connected()` should return True for a connected graph."""
        graph = UnweightedGraph(
            [{1, 3}, {0, 2, 4}, {1, 5}, {0, 4}, {1, 3, 5}, {2, 4}],
            directed=False,
        )
        self.assertTrue(is_connected(graph))

    def test_not_connected(self):
        """`is_connected()` should return False for a disconnected graph."""
        graph = UnweightedGraph(2, directed=False)
        self.assertFalse(is_connected(graph))

    def test_raises_directed(self):
        """
        `is_connected()` should raise a ValueError when given a directed graph.

        """
        graph = UnweightedGraph(2, directed=True)
        self.assertRaises(ValueError, is_connected, graph)


if __name__ == "__main__":
    unittest.main()
