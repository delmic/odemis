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
import copy
import itertools
import sys
from abc import ABCMeta, abstractmethod
from typing import (
    Any,
    Deque,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
)

import numpy


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
            return self.data[i]

else:
    from collections import UserList


Edge = Tuple[int, int]
Triangle = Tuple[int, int, int]
Graph = TypeVar("Graph", bound="GraphBase[Any]")


class GraphBase(UserList, metaclass=ABCMeta):
    """Abstract base class for graphs."""

    _item_type = object

    def __init__(
        self,
        n_or_initlist: Union[Iterable[Any], int, None] = None,
        *,
        directed: bool = True,
    ) -> None:
        """
        Initializer for GraphBase.

        Parameters
        ----------
        n_or_initlist : int or iterable, optional
            If `None` (default) initializes a graph of zero order and zero size
            (i.e. no vertices and no edges). If int, initializes a graph of
            order `n_or_initlist` and zero size. Otherwise initialize the graph
            using the iterable `n_or_initlist`.
        directed : bool
            If `False` the graph is undirected and symmetry of the adjacency
            matrix is enforced when adding or removing edges. For undirected
            graphs this means that `j in graph[i]` is True if and only if
            `i in graph[j]`.

        """
        self._directed = directed
        if n_or_initlist is None:
            super().__init__()
        elif isinstance(n_or_initlist, int):
            super().__init__((self._item_type() for _ in range(n_or_initlist)))
        elif isinstance(n_or_initlist, collections.abc.Iterable):
            super().__init__(map(self._item_type, n_or_initlist))
        else:
            raise TypeError(
                f"Unsupported type '{type(n_or_initlist).__name__}', "
                "expected int or iterable"
            )

    def __contains__(self, edge: Edge) -> bool:
        """Check if the graph contains a specific edge."""
        j, i = edge
        return i in self.data[j]

    @property
    def directed(self) -> bool:
        """Returns `True` if the graph is directed and `False` otherwise."""
        return self._directed

    @abstractmethod
    def add_edge(self, edge: Edge, weight) -> None:
        """Add an edge to the graph."""

    @abstractmethod
    def remove_edge(self, edge: Edge) -> None:
        """Remove an edge from the graph."""

    @abstractmethod
    def get_edge_weight(self, edge: Edge):
        """Get the edge weight of an edge in the graph."""

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
        for edge in self.iter_edges(True):
            matrix[edge] = self.get_edge_weight(edge)
        return matrix

    def iter_edges(self, directed: Optional[bool] = None) -> Iterator[Edge]:
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


class UnweightedGraph(GraphBase):
    """
    Unweighted graph represented as a list of sets.

    Each list item describes the set of neighbors of a particular vertex in the
    graph. For example, `graph[j]` is the set of neighbors of vertex `j`.

    """

    _item_type = set

    def add_edge(self, edge: Edge, weight=1) -> None:
        j, i = edge
        self.data[j].add(i)
        if not self._directed:
            self.data[i].add(j)

    def remove_edge(self, edge: Edge) -> None:
        j, i = edge
        self.data[j].remove(i)
        if not self._directed:
            self.data[i].remove(j)

    def get_edge_weight(self, edge: Edge) -> float:
        if edge not in self:
            raise KeyError(f"Edge `{edge}` not in graph")
        # Always return an edge weight of 1 for an unweighted graph
        return 1


class WeightedGraph(GraphBase):
    """
    Weighted graph represented as a list of dicts.

    Each list item describes the set of neighbors of a particular vertex and
    their associated weights. For example, `graph[j]` is a dictionary of which
    the keys form the set of neighbors of vertex `j` and the values contain the
    edge weights.

    Edge weights are typically floats. It is possible to use a custom object as
    edge weight if the object is sortable and can be converted into a float. In
    other words, the object should implement the `__lt__` and `__float__`
    special methods.

    """

    _item_type = dict

    def add_edge(self, edge: Edge, weight) -> None:
        j, i = edge
        self.data[j][i] = weight
        if not self._directed:
            self.data[i][j] = weight

    def remove_edge(self, edge: Edge) -> None:
        j, i = edge
        del self.data[j][i]
        if not self._directed:
            del self.data[i][j]

    def get_edge_weight(self, edge: Edge):
        j, i = edge
        return self.data[j][i]


class SkewSymmetricAdjacencyGraph(WeightedGraph):
    """
    Weighted undirected graph of which the adjacency matrix is skew-symmetric,
    i.e. the edge weights change sign when the direction is reversed.

    """

    def __init__(
        self,
        n_or_initlist: Union[Iterable[Any], int, None] = None,
    ) -> None:
        super().__init__(n_or_initlist, directed=False)

    def add_edge(self, edge: Edge, weight) -> None:
        j, i = edge
        self.data[j][i] = weight
        self.data[i][j] = -weight


class DisjointSetUnion:
    """
    DisjointSetUnion data structure. Allows to keep track in which set a
    specific element is, and has an operation to combine any two sets.

    """

    def __init__(self, n: int) -> None:
        """Initialize a new DisjointSetUnion data structure."""
        if not n > 0:
            raise ValueError(f"Size `n` must be positive, got `{n}`.")
        self._parents = list(range(n))
        self._size = [1] * n

    def find(self, node: int) -> int:
        """Find the set containing a node."""
        root = node
        while root != self._parents[root]:
            root = self._parents[root]

        # path compression
        while node != root:
            parent = self._parents[node]
            self._parents[node] = root
            node = parent

        return root

    def union(self, a: int, b: int) -> None:
        """
        Find the sets containing two nodes and if they are distinct, merge
        them.

        """
        a = self.find(a)
        b = self.find(b)

        if a == b:
            # these nodes already belong to the same set
            return

        if self._size[a] < self._size[b]:
            a, b = b, a
        self._parents[b] = a
        self._size[a] += self._size[b]


def iter_triangles(graph: Graph) -> Iterator[Triangle]:
    """
    Iterator over all triangles (3-cycles) in a graph.

    Parameters
    ----------
    graph : Graph
        The graph of which to find all triangles.

    Yields
    -------
    triangle : 3-tuple `(s, t, v)`
        The vertices that form a triangle, in increasing order.

    References
    ----------
    .. [1] Schank, T., & Wagner, D. (2005, May). Finding, counting and listing
    all triangles in large graphs, an experimental study. In International
    workshop on experimental and efficient algorithms (pp. 606-609). Springer,
    Berlin, Heidelberg.

    """
    n = len(graph)
    degree = list(map(len, graph))
    vertices = numpy.argsort(degree)[::-1]
    index = numpy.argsort(vertices)
    visited: List[Set[int]] = [set() for _ in range(n)]
    for s in vertices:
        for t in graph[s]:
            if index[s] < index[t]:
                for v in visited[s].intersection(visited[t]):
                    yield tuple(sorted((v, s, t)))
                visited[t].add(s)


def remove_triangles(graph: Graph, *, overwrite: bool = False) -> Graph:
    """
    Removes all triangles (3-cycles) from a graph.

    Triangles are removed by deleting the least amount of edges from a graph.
    This is done using a greedy algorithm where edges that are contained in
    more than one triangle are removed first. If two edges are contained in the
    same amount of triangles, the edge that has the largest edge weight
    (distance) is removed first.

    Parameters
    ----------
    graph : Graph
        The graph from which to remove all triangles.
    overwrite : bool
        If `True` the the input graph will be modified in-place. Default is
        `False`.

    """
    if not overwrite:
        graph = copy.deepcopy(graph)

    triangles = set(iter_triangles(graph))
    if not triangles:
        # Quick return if possible
        return graph

    edge_counter: Dict[Tuple[int, int], int] = collections.Counter()
    edge_to_triangles_map = collections.defaultdict(set)
    triangle_to_edges_map = collections.defaultdict(set)
    for triangle in triangles:
        for edge in itertools.combinations(triangle, 2):
            edge_counter[edge] += 1
            edge_to_triangles_map[edge].add(triangle)
            triangle_to_edges_map[triangle].add(edge)

    # First consider all edges that are contained in at least two triangles.
    # Remove the edge that is contained in the largest number of triangles and
    # has the largest edge weight (distance).
    while True:
        count = max(edge_counter.values(), default=0)
        if count < 2:
            break
        edges = [edge for edge, n in edge_counter.items() if n == count]
        selected = max(edges, key=graph.get_edge_weight)
        graph.remove_edge(selected)
        # To prevent a RuntimeError loop over a copy of the set.
        for triangle in edge_to_triangles_map[selected].copy():
            for edge in triangle_to_edges_map[triangle]:
                edge_counter[edge] -= 1
                edge_to_triangles_map[edge].remove(triangle)
            del triangle_to_edges_map[triangle]
            triangles.remove(triangle)

    # For triangles that are isolated (i.e. those that do not contain an edge
    # contained in another triangle), remove the edge that has the largest edge
    # weight (distance).
    for triangle in triangles:
        edges = list(itertools.combinations(triangle, 2))
        selected = max(edges, key=graph.get_edge_weight)
        graph.remove_edge(selected)

    return graph


def _minmax_spanning_tree(
    graph: Graph, *, reverse: bool, overwrite: bool = False
) -> Graph:
    """
    Returns the minimum or maximum spanning tree of a graph.

    Parameters
    ----------
    graph : Graph
        The graph of which to determine the minimum or maximum spanning tree.
    reverse : bool
        When `False` returns a minimum spanning tree, when `True` returns a
        maximum spanning tree.
    overwrite : bool
        If `True` the the input graph will be modified in-place. Default is
        `False`.

    Returns
    -------
    graph : Graph
        The minimum or maximum spanning tree of `graph`.

    """
    if graph.directed:
        raise ValueError("Expected an undirected graph, but got a directed graph.")
    n = len(graph)
    if not overwrite:
        graph = copy.deepcopy(graph)
    dsu = DisjointSetUnion(n)
    for edge in sorted(graph.iter_edges(), key=graph.get_edge_weight, reverse=reverse):
        vertex, neighbor = edge
        if dsu.find(vertex) != dsu.find(neighbor):
            dsu.union(*edge)
        else:
            graph.remove_edge(edge)
    return graph


def minimum_spanning_tree(graph: Graph, *, overwrite: bool = False) -> Graph:
    """
    Returns the minimum spanning tree of a graph.

    Parameters
    ----------
    graph : Graph
        The graph of which to determine the minimum spanning tree.
    overwrite : bool
        If `True` the the input graph will be modified in-place. Default is
        `False`.

    Returns
    -------
    mst : Graph
        The minimum spanning tree of `graph`.

    """
    return _minmax_spanning_tree(graph, reverse=False, overwrite=overwrite)


def maximum_spanning_tree(graph: Graph, *, overwrite: bool = False) -> Graph:
    """
    Returns the maximum spanning tree of a graph.

    Parameters
    ----------
    graph : Graph
        The graph of which to determine the maximum spanning tree.
    overwrite : bool
        If `True` the the input graph will be modified in-place. Default is
        `False`.

    Returns
    -------
    mst : Graph
        The minimum spanning tree of `graph`.

    """
    return _minmax_spanning_tree(graph, reverse=True, overwrite=overwrite)


def depth_first_walk(graph: Graph, vertex: int) -> Iterator[Tuple[Optional[int], int]]:
    """
    Iterate over all reachable vertices in a graph from a starting vertex using
    a depth first walk.

    Parameters
    ----------
    graph : Graph
        The graph on which to perform the depth first walk.
    vertex : int
        The index of the starting vertex.

    Yields
    ------
    predecessor : int or None
        The predecessor of the vertex. Is `None` for the starting vertex.
    vertex : int

    """
    stack: Deque[Tuple[Optional[int], int]] = collections.deque()
    stack.append((None, vertex))
    explored = set()
    while stack:
        predecessor, vertex = stack.pop()
        if vertex in explored:
            continue
        yield predecessor, vertex
        explored.add(vertex)
        for neighbor in graph[vertex]:
            stack.append((vertex, neighbor))


def is_connected(graph: Graph) -> bool:
    """
    Returns True if the graph is connected, False otherwise.

    Parameters
    ----------
    graph : Graph
       An undirected graph.

    Returns
    -------
    connected : bool
      True if the graph is connected, false otherwise.

    """
    if graph.directed:
        raise ValueError("Expected an undirected graph, but got a directed graph.")
    walker = depth_first_walk(graph, 0)
    counter = itertools.count()
    collections.deque(zip(walker, counter), maxlen=0)
    connected = len(graph) == next(counter)
    return connected
