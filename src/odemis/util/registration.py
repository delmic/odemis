# -*- encoding: utf-8 -*-
"""
registration.py : Utility functions for point set registration.

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
import math
from typing import Iterator, NamedTuple, Optional, Tuple, Type, TypeVar

import numpy
import scipy.spatial
from odemis.util.cluster import kmeans2
from odemis.util.graph import (
    SkewSymmetricAdjacencyGraph,
    depth_first_walk,
    is_connected,
    minimum_spanning_tree,
    remove_triangles,
)
from odemis.util.spot import find_spot_positions
from odemis.util.transform import (
    GeometricTransform,
    cartesian_to_polar,
    polar_to_cartesian,
)

T = TypeVar("T", bound="GeometricTransform")


def _bijective_matching(dm: numpy.ndarray) -> Iterator[Tuple[int, int]]:
    """
    Matching of two un-ordered point sets to determine their correspondences.

    See `bijective_matching()` for more information.

    Parameters
    ----------
    dm : ndarray
        Distance matrix, where entry `(j, i)` contains the distance between
        `src[j]` and `dst[i]`.

    Yields
    ------
    correspondence : tuple of ints
        Yields the edges connecting the source and destination point sets,
        sorted by distance in ascending order.

    """
    n, m = dm.shape
    if n > m:
        j = numpy.argmin(dm, axis=0)
        i = numpy.arange(m)
    else:
        j = numpy.arange(n)
        i = numpy.argmin(dm, axis=1)

    # Create a list of nearest neigbor pairs `(j, i)` sorted by distance in
    # ascending order.
    matches = sorted(zip(j, i), key=dm.__getitem__)

    # Ensure that each vertex can only be part of a single matching edge.
    mask_src = numpy.ones(n, dtype=bool)
    mask_dst = numpy.ones(m, dtype=bool)
    for j, i in matches:
        if mask_src[j] and mask_dst[i]:
            # Add correspondence and remove vertices from future consideration.
            yield (j, i)
            mask_src[j] = False
            mask_dst[i] = False
        else:
            # Duplicate found: solve the unmatched pairs recursively.
            map_src = numpy.flatnonzero(mask_src)
            map_dst = numpy.flatnonzero(mask_dst)
            tail = _bijective_matching(dm[numpy.ix_(mask_src, mask_dst)])
            yield from ((map_src[j], map_dst[i]) for j, i in tail)
            break


def bijective_matching(
    src: numpy.ndarray, dst: numpy.ndarray
) -> Iterator[Tuple[int, int]]:
    """
    Matching of two un-ordered point sets to determine their correspondences.

    This function can be used as the matching step in the iterative closest
    point (ICP) algorithm.

    Parameters
    ----------
    src : ndarray
        Point set in the source reference frame.
    dst : ndarray
        Point set in the destination reference frame.

    Yields
    ------
    correspondence : tuple of ints
        Yields the edges connecting the source and destination point sets,
        sorted by distance in ascending order.

    References
    ----------
    .. [1] Almhdie, A., Léger, C., Deriche, M., & Lédée, R. (2007). 3D
    registration using a new implementation of the ICP algorithm based on a
    comprehensive lookup matrix: Application to medical imaging. Pattern
    Recognition Letters, 28(12), 1523-1533.

    """
    dm = scipy.spatial.distance.cdist(src, dst, "euclidean")
    yield from _bijective_matching(dm)


def unit_gridpoints(shape: Tuple[int, int], *, mode: str) -> numpy.ndarray:
    """
    Returns an ordered array of coordinates of a square grid with unit spacing
    centered around zero.

    This function returns a row-major ordered array of coordinates of a square
    grid of points with unit spacing. The coordinates are returned either in
    Cartesian or matrix mode.

    Parameters
    ----------
    shape : tuple of two ints
        The shape of the grid given as a tuple `(height, width)`.
    mode : {"ji", "xy"}
        Cartesian ("xy") or matrix ("ji") indexing of output.

    Returns
    -------
    out : ndarray
        Array with the coordinates of a square grid of points with unit spacing
        in row-major order. I.e. for a grid of shape `(n, m)` the first entry
        `out[0]` is the top-left corner of the grid, `out[m - 1]` is the
        top-right corner point, `out[(n - 1) * m]` is the bottom-left corner
        point, and `out[-1]` is the bottom right corner.

    """
    if mode not in ("ji", "xy"):
        raise ValueError("Valid values for `mode` are 'ji' and 'xy'.")
    n, m = shape
    if mode == "ji":
        j = numpy.reshape(numpy.arange(n, dtype=float) - 0.5 * float(n - 1), (n, 1))
        i = numpy.reshape(numpy.arange(m, dtype=float) - 0.5 * float(m - 1), (1, m))
        return numpy.stack(numpy.broadcast_arrays(j, i), axis=-1).reshape(n * m, 2)
    # mode == "xy"
    x = numpy.reshape(numpy.arange(m, dtype=float) - 0.5 * float(m - 1), (1, m))
    y = numpy.reshape(numpy.arange(n, dtype=float)[::-1] - 0.5 * float(n - 1), (n, 1))
    return numpy.stack(numpy.broadcast_arrays(x, y), axis=-1).reshape(n * m, 2)


class WeightedShift(NamedTuple):
    """
    Named tuple consisting of a weight and a shift, of which the weight
    attribute is invariant under negation (change of sign), and the shift
    attribute is not.

    Can be used as an edge weight in a graph. In particular when using a
    WeightedShift as an edge weight in a SkewSymmetricAdjacencyGraph this
    makes the behavior of the weight attribute symmetric, and the shift
    attribute skew-symmetric.

    Attributes
    ----------
    weight : float
    shift : numpy.ndarray

    """

    weight: float
    shift: numpy.ndarray

    def __float__(self) -> float:
        # The ability to return an instance of a strict subclass of float is
        # deprecated. For example it's not allowed to return a numpy.float64,
        # hence the call to `float()`.
        return float(self.weight)

    def __neg__(self) -> "WeightedShift":
        return WeightedShift(self.weight, -self.shift)

    # overriding comparison special methods in typing.NamedTuple
    def __lt__(self, other: "WeightedShift") -> bool:
        return self.weight < other.weight

    def __le__(self, other: "WeightedShift") -> bool:
        return self.weight <= other.weight

    def __eq__(self, other: "WeightedShift") -> bool:
        return self is other

    def __ne__(self, other: "WeightedShift") -> bool:
        return self is not other

    def __gt__(self, other: "WeightedShift") -> bool:
        return self.weight > other.weight

    def __ge__(self, other: "WeightedShift") -> bool:
        return self.weight >= other.weight


def nearest_neighbor_graph(ji: numpy.ndarray) -> SkewSymmetricAdjacencyGraph:
    """
    Returns a undirected weighted simple graph of 4-connected nearest neighbors
    of a square grid of points.

    Parameters
    ----------
    ji : ndarray of shape (n, 2)
        Array with the determined coordinates of the grid points.

    Returns
    -------
    graph : SkewSymmetricAdjacencyGraph
        Undirected weighted simple graph of 4-connected nearest neighbors. For
        points located on the edge of the grid only 3 nearest neighbors are
        returned, and for the corners only 2.

    """
    # Find the closest 4 neighbors (excluding itself) for each point.
    tree = scipy.spatial.cKDTree(ji)
    # NOTE: Starting SciPy v1.6.0 the `n_jobs` argument will be renamed `workers`
    distances, indices = tree.query(ji, k=5, n_jobs=-1)
    distances = distances[:, 1:]  # exclude the point itself
    indices = indices[:, 1:]  # same

    # Construct an undirected weighted simple graph
    graph = SkewSymmetricAdjacencyGraph(len(ji))
    for vertex, neighbors in enumerate(indices):
        for neighbor, distance in zip(neighbors, distances[vertex]):
            # An edge connects two vertices that are each others nearest neighbor.
            if (vertex < neighbor) and (vertex in indices[neighbor]):
                shift = ji[neighbor] - ji[vertex]
                graph.add_edge((vertex, neighbor), WeightedShift(distance, shift))

    # The code above assumes that each point has 4 nearest neighbors. In
    # practice we would like to only consider the 2 nearest neighbors for the
    # points at the corners of the grid, and likewise 3 nearest neighbors for
    # the points on the sides of the grid. The following line takes care of
    # that.
    remove_triangles(graph, overwrite=True)

    return graph


_PERMUTATION_LUT = {
    -2: ((-1, -1), (0, 1)),
    -1: ((1, -1), (1, 0)),
    0: ((1, 1), (0, 1)),
    1: ((-1, 1), (1, 0)),
    2: ((-1, -1), (0, 1)),
}


def _canonical_matrix_form(matrix: numpy.ndarray) -> numpy.ndarray:
    """
    Returns the signed permutation required to convert a transformation matrix
    into canonical form.

    The multi-probe pattern has a dual mirror symmetry as well as a 4-fold
    rotational symmetry. Hence there are 8 degenerate orientations of the
    multi-probe pattern. This function takes as input an estimated
    transformation matrix and returns the signed permutation such that
    `sign * matrix[:, perm]` is in canonical form: no reflection and a rotation
    between -pi/4 and +pi/4.

    Parameters
    ----------
    matrix : ndarray
        Input transformation matrix.

    Returns
    -------
    sign : numpy.ndarray
        Array containing the reflections to be applied to the columns of the
        input transformation matrix.
    perm : numpy.ndarray
        Array containing the permutations of the columns of the input
        transformation matrix.

    Examples
    --------
    >>> matrix = numpy.array([(1, 0), (0, -1)])
    >>> _canonical_matrix_form(matrix)
    (array([ 1, -1]), array([0, 1]))

    """
    # The rotation follows from a polar decomposition of the input matrix:
    # `A = R * S`, where `R` is an orthogonal matrix and `S` is
    # positive-definite. Let `P` be an orthogonal permutation matrix.
    # Then `A' = A * P` and `S' = Pᵀ * S * P`. By definition `S'` is congruent
    # to `S`, and is thus also positive-definite. Then by the uniqueness of the
    # polar decomposition we have `A' = R' * S'`, where `R' = R * P`.
    R, _ = scipy.linalg.polar(matrix)
    rotation = math.atan2(R[1, 0], R[0, 0])
    k = math.floor(0.5 + 2 * rotation / math.pi)

    # Based on the determined rotation swap and/or reflect the columns of the
    # matrix to ensure that the resulting matrix has a rotation part that
    # represents an angle between -pi/4 and +pi/4.
    sign, perm = map(numpy.array, _PERMUTATION_LUT[k])

    # If the transformation matrix contains a reflection, its determinant will
    # be negative. In that case, reflect the last column (pre-permutation) of
    # the matrix.
    if numpy.linalg.det(matrix) < 0:
        sign[perm[-1]] *= -1

    return sign, perm


def _cluster_edges(
    graph: SkewSymmetricAdjacencyGraph,
) -> Tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    """
    Classifies the set of edges in a nearest neighbor graph of a square grid of
    points into 4 clusters 'up', 'right', 'down', and 'left' using the k-means
    algorithm.

    Parameters
    ----------
    graph : SkewSymmetricAdjacencyGraph
        Undirected weighted simple graph of 4-connected nearest neighbors.
        The edge weights should be of type `WeightedShift`, each having an
        attribute `shift` equal to the displacement between the connected
        vertices.

    Returns
    -------
    centroids : numpy.ndarray
        A `4-by-2` array of centroids representing the directions 'up',
        'right', 'down', and 'left'. The first and second centroids correspond
        to a positive displacement in the first and second axis respectively.
        The third and fourth centroids correspond to a negative displacement,
        i.e. `centroid[2] = -centroid[0]`, and `centroid[3] = -centroid[1]`.
    labels : numpy.ndarray
        `labels[i]` is the index of the centroid the i'th edge is closest to.
    distances : numpy.ndarray
        The Euclidean distances between the observations passed and their
        matched centroids.

    """
    if not is_connected(graph):
        # no point in continuing if the graph is disconnected.
        raise ValueError("Expected a connected graph, but got a disconnected graph.")

    # Create an array `shifts` containing the displacement vectors for all
    # edges in the graph.
    shifts = numpy.vstack(
        [graph.get_edge_weight(edge).shift for edge in graph.iter_edges(False)]
    )

    # The array `shifts` typically contains the lattice vectors for the 'left',
    # 'right', 'up', and 'down' directions, but it is not guaranteed that it
    # will contain all of them, nor that there are equal amounts of edges in
    # each direction. The solution is to not make any distinction between
    # 'left' and 'right', or 'up' and 'down'. This is done using a mapping of
    # `(ρ, θ)` to `(ρ, 2*θ)`. The clustering itself is done in Cartesian
    # coordinates to not be impacted by the branch cut at `θ = ±π`.
    rho, theta = cartesian_to_polar(shifts)
    dpq = polar_to_cartesian(rho, 2 * theta)
    centroids, labels = kmeans2(dpq, 2, minit="++")
    rho, theta = cartesian_to_polar(centroids)
    centroids = polar_to_cartesian(rho, 0.5 * theta)

    # Each rows of the array `centroid` now contains a lattice vector (direction).
    # By transposing the array, these lattice vectors are the columns of the
    # new matrix. This allows to directly use them as a transformation matrix.
    # For example: let `x₁` and `x₂` be the two 2-by-1 lattice vectors, then
    # `(x₁, x₂) * (n, m)ᵀ = n * x₁ + m * x₂`.
    sign, perm = _canonical_matrix_form(numpy.transpose(centroids))
    centroids = sign[:, None] * centroids[perm]
    labels = perm[labels]

    # extend
    reverse = numpy.einsum("ji,ji->j", shifts, centroids[labels]) < 0
    labels[reverse] += 2
    centroids = numpy.vstack((centroids, -centroids))

    # determine distance to centroid
    distances = numpy.linalg.norm(shifts - centroids[labels], axis=1)

    return centroids, labels, distances


_SHIFT_LUT = numpy.array([(1, 0), (0, 1), (-1, 0), (0, -1)], dtype=numpy.int64)


def _enumerate_grid(
    graph: SkewSymmetricAdjacencyGraph,
    labels: numpy.ndarray,
    distances: numpy.ndarray,
    shape: Tuple[int, int],
) -> numpy.ndarray:
    """
    Enumerates a square grid of points.

    Parameters
    ----------
    graph : SkewSymmetricAdjacencyGraph
        Undirected weighted simple graph of 4-connected nearest neighbors.
        The edge weights should be of type `WeightedShift`, each having an
        attribute `shift` equal to the displacement between the connected
        vertices.
    labels : numpy.ndarray
        `labels[i]` is the index of the centroid the i'th edge is closest to.
    distances : numpy.ndarray
        The Euclidean distances between the observations passed and their
        matched centroids.
    shape : tuple of two ints
        The shape of the grid given as a tuple `(height, width)`.

    Returns
    -------
    nodes : numpy.ndarray
        Node indices of the grid.

    """
    # build a minimum spanning tree
    n = len(graph)
    mst = SkewSymmetricAdjacencyGraph(n)
    edges = graph.iter_edges(False)
    weights = map(WeightedShift, distances, _SHIFT_LUT[labels])
    for edge, weight in zip(edges, weights):
        mst.add_edge(edge, weight)
    minimum_spanning_tree(mst, overwrite=True)

    # number the beamlets by walking the tree
    coords = numpy.zeros((n, 2), dtype=numpy.int64)
    walker = depth_first_walk(mst, 0)
    next(walker)  # skip the first `(None, start)` entry
    for predecessor, vertex in walker:
        shift = mst.get_edge_weight((predecessor, vertex)).shift
        coords[vertex] = coords[predecessor] + shift
    coords -= numpy.min(coords, axis=0)

    # convert from coords to node numbering
    nodes = numpy.ravel_multi_index(numpy.transpose(coords), dims=shape, mode="raise")

    return nodes


def estimate_grid_orientation(
    ji: numpy.ndarray, shape: Tuple[int, int], transform_type: Type[T]
) -> T:
    """
    Estimate the orientation of a square grid of points.

    Parameters
    ----------
    ji : ndarray of shape (n, 2)
        Array with the determined coordinates of the grid points.
    shape : tuple of two ints
        The shape of the grid given as a tuple `(height, width)`. Current
        implementation only supports grids of points with `height == width`.
    transform_type : GeometricTransform
        The transform class to use for estimating the orientation.

    Returns
    -------
    tform : instance of `transform_type`
        The orientation of the pattern.

    """
    if shape[0] != shape[1]:
        raise NotImplementedError(
            "Only grids with `shape[0] == shape[1]` are supported."
        )
    graph = nearest_neighbor_graph(ji)
    _, labels, distances = _cluster_edges(graph)
    nodes = _enumerate_grid(graph, labels, distances, shape)
    grid = unit_gridpoints(shape, mode="ji")
    return transform_type.from_pointset(grid[nodes], ji)


def estimate_grid_orientation_from_img(
    image: numpy.ndarray,
    shape: Tuple[int, int],
    transform_type: Type[T],
    sigma: float,
    threshold_abs: Optional[float] = None,
    threshold_rel: Optional[float] = None,
    num_spots: Optional[int] = None,
) -> T:
    """
    Image based estimation of the orientation of a square grid of points.

    Parameters
    ----------
    image : ndarray
        The input image.
    shape : tuple of two ints
        The shape of the grid given as a tuple `(height, width)`.
    transform_type : GeometricTransform
        The transform class to use for estimating the orientation.
    sigma : float
        Expected size of the spots. Assuming the spots are Gaussian shaped,
        this is the standard deviation.
    threshold_abs : float, optional
        Minimum intensity of peaks. By default, the absolute threshold is
        the minimum intensity of the image.
    threshold_rel : float, optional
        If provided, apply a threshold on the minimum intensity of peaks,
        calculated as `max(image) * threshold_rel`.
    num_spots : int, optional
        Maximum number of spots. When the number of spots exceeds `num_spots`,
        return `num_spots` peaks based on highest spot intensity. Will use
        `num_spots = shape[0] * shape[1]` as default when set to `None`. Set
        `num_spots = 0` to not impose a maximum. Note that this behavior is
        different from odemis.util.spot.find_spot_position().

    Returns
    -------
    tform : instance of `transform_type`
        The orientation of the pattern.

    """
    if num_spots is None:
        num_spots = shape[0] * shape[1]
    elif num_spots == 0:
        num_spots = None
    ji = find_spot_positions(image, sigma, threshold_abs, threshold_rel, num_spots)
    return estimate_grid_orientation(ji, shape, transform_type)
