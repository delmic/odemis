# -*- encoding: utf-8 -*-
"""
graph.py : tools for constructing and modifying graphs, i.e. sets of vertices
connected by edges.

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
import collections
import itertools
from abc import ABCMeta, abstractmethod
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

import numpy
import sys


if sys.version_info < (3, 7, 4):
    from collections import UserList as _UserList

    class UserList(_UserList):
        """
        Backport of bugfix for when using Python v3.7.3 or lower.
        For more informations see: https://bugs.python.org/issue27639

        """

        def __getitem__(self, i):
            if isinstance(i, slice):
                return self.__class__(self.data[i])
            else:
                return self.data[i]


else:
    from collections import UserList


class GraphBase(UserList, metaclass=ABCMeta):
    """Abstract base class for graphs."""

    _item_type = object

    def __init__(
        self,
        n_or_initlist: Optional[Union[int, Sequence[Any]]] = None,
        directed: bool = True,
    ) -> None:
        self._directed = directed
        if n_or_initlist is None:
            super().__init__()
        elif isinstance(n_or_initlist, int):
            super().__init__((self._item_type() for _ in range(n_or_initlist)))
        elif isinstance(n_or_initlist, collections.abc.Sequence):
            super().__init__(map(self._item_type, n_or_initlist))
        else:
            raise ValueError(
                "Unsupported type '{}', expected int or sequence".format(
                    type(n_or_initlist).__name__
                )
            )

    @abstractmethod
    def add_edge(self, edge: Tuple[int, int]) -> None:
        pass

    @abstractmethod
    def remove_edge(self, edge: Tuple[int, int]) -> None:
        pass

    @abstractmethod
    def get_edge_weight(self, edge: Tuple[int, int]) -> float:
        pass

    def adjacency_matrix(self) -> numpy.ndarray:
        """
        Return the adjacency matrix of the graph.

        Returns
        -------
        matrix : ndarray
            The adjacency matrix.

        """
        n = len(self.data)
        matrix = numpy.zeros((n, n))
        for vertex, neighbors in enumerate(self.data):
            for neighbor in neighbors:
                matrix[vertex, neighbor] = self.get_edge_weight((vertex, neighbor))
        return matrix

    def iter_edges(self, directed: Optional[bool] = None) -> Iterator[Tuple[int, int]]:
        """
        Iterator over all the edges in the graph.

        Parameters
        ----------
        directed : bool, optional
            If `True` the edges `(j, i)` and `(i, j)` will be considered as
            being two separate edges. If `False`, only yield edges `(j, i)`
            with `j < i`. If not specified, uses `self.directed`.

        Yields
        ------
        edge : tuple `(j, i)`
            The edge connecting two vertices.

        """
        directed = self._directed if directed is None else directed
        for vertex, neighbors in enumerate(self.data):
            for neighbor in neighbors:
                if not directed and vertex > neighbor:
                    continue
                yield (vertex, neighbor)

    def iter_triangles(self) -> Iterator[Tuple[int, int, int]]:
        """
        Iterator over all triangles (3-cycles) in the graph.

        Yields
        -------
        triangle : 3-tuple `(s, t, v)`
            The vertices that form a triangle, in increasing order.

        References
        ----------
        .. [1] Schank, T., & Wagner, D. (2005, May). Finding, counting and
        listing all triangles in large graphs, an experimental study. In
        International workshop on experimental and efficient algorithms
        (pp. 606-609). Springer, Berlin, Heidelberg.

        """
        n = len(self.data)
        degree = list(map(len, self.data))
        vertices = numpy.argsort(degree)[::-1]
        index = numpy.argsort(vertices)
        visited: List[Set[int]] = [set() for _ in range(n)]
        for s in vertices:
            for t in self.data[s]:
                if index[s] < index[t]:
                    for v in visited[s].intersection(visited[t]):
                        yield tuple(sorted((v, s, t)))
                    visited[t].add(s)

    def remove_triangles(self) -> None:
        """Remove all triangles (3-cycles) from the graph."""
        triangles = set(self.iter_triangles())
        if not triangles:
            return  # Quick return if possible

        edge_counter: Dict[Tuple[int, int], int] = collections.Counter()
        edge_to_triangles_map = collections.defaultdict(set)
        triangle_to_edges_map = collections.defaultdict(set)
        for triangle in triangles:
            for edge in itertools.combinations(triangle, 2):
                edge_counter[edge] += 1
                edge_to_triangles_map[edge].add(triangle)
                triangle_to_edges_map[triangle].add(edge)

        # First consider all edges that are contained in at least two
        # triangles. Remove the edge that is contained in the largest number
        # of triangles and has the largest edge weight (distance).
        while True:
            count = max(edge_counter.values(), default=0)
            if count < 2:
                break
            edges = [edge for edge, n in edge_counter.items() if n == count]
            selected = max(edges, key=self.get_edge_weight)
            self.remove_edge(selected)
            # To prevent a RuntimeError loop over a copy of the set.
            for triangle in edge_to_triangles_map[selected].copy():
                for edge in triangle_to_edges_map[triangle]:
                    edge_counter[edge] -= 1
                    edge_to_triangles_map[edge].remove(triangle)
                del triangle_to_edges_map[triangle]
                triangles.remove(triangle)

        # For triangles that are isolated (i.e. those that do not contain an
        # edge contained in another triangle), remove the edge that has the
        # largest edge weight (distance).
        for triangle in triangles:
            edges = list(itertools.combinations(triangle, 2))
            selected = max(edges, key=self.get_edge_weight)
            self.remove_edge(selected)


class WeightedGraph(GraphBase):
    """
    represented as a list of dicts.

    Each list item is a dictionary of which
    the keys are the vertices to which the vertex represented by that list
    item is connected to. The dictionary values contain the edge weights.
    For example, `graph[0]` is the set of `(neighbor, distance)` pairs of
    vertex 0. The graph is undirected, which means that `j in graph[i]` is
    true if and only if `i in graph[j]`.

    """

    _item_type = dict

    def add_edge(self, edge: Tuple[int, int], weight: float = 1) -> None:
        j, i = edge
        self.data[j][i] = weight
        if not self._directed:
            self.data[i][j] = weight

    def remove_edge(self, edge: Tuple[int, int]) -> None:
        j, i = edge
        del self.data[j][i]
        if not self._directed:
            del self.data[i][j]

    def get_edge_weight(self, edge: Tuple[int, int]) -> float:
        j, i = edge
        return self.data[j][i]


class UnweightedGraph(GraphBase):
    """
    represented as a list of sets.

    Each list item is a dictionary of which
    the keys are the vertices to which the vertex represented by that list
    item is connected to. The dictionary values contain the edge weights.
    For example, `graph[0]` is the set of `(neighbor, distance)` pairs of
    vertex 0. The graph is undirected, which means that `j in graph[i]` is
    true if and only if `i in graph[j]`.

    """

    _item_type = set

    def add_edge(self, edge: Tuple[int, int]) -> None:
        j, i = edge
        self.data[j].add(i)
        if not self._directed:
            self.data[i].add(j)

    def remove_edge(self, edge: Tuple[int, int]) -> None:
        j, i = edge
        self.data[j].remove(i)
        if not self._directed:
            self.data[i].remove(j)

    def get_edge_weight(self, edge: Tuple[int, int]) -> float:
        return 1
