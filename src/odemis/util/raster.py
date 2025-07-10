# -*- coding: utf-8 -*-

"""
:created: 2015-01-12
:author: Rinze de Laat
:copyright: © 2015 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

import math
from typing import Set, Tuple

import numpy


def rasterize_line(p0, p1, width=1):
    """ Return a list of points that form a line between the two given points
    p0 (int, int): x, y of the first point on the line
    p1 (int, int): x, y of the last point on the line
    return (list of (int, int)): all the points on the line segment, including p0,
     and p1.
    """
    x0, y0 = p0
    x1, y1 = p1
    points = []

    if width == 1:
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = x0 < x1 and 1 or -1
        sy = y0 < y1 and 1 or -1
        err = dx - dy

        x, y = x0, y0

        while True:
            points.append((x, y))
            if x == x1 and y == y1:
                break
            e2 = err * 2
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
    elif width > 1:
        # Vector
        vx = x1 - x0
        vy = y1 - y0

        if vx or vy:
            # Perpendicular vector
            px = vy
            py = -vx

            # Normalize
            plen = math.sqrt(px * px + py * py)
            nx = px / plen
            ny = py / plen

            # Corners
            r1 = (int(round(x0 - nx * width / 2)), int(round(y0 - ny * width / 2)))
            r2 = (int(round(x0 + nx * width / 2)), int(round(y0 + ny * width / 2)))
            r3 = (int(round(x1 + nx * width / 2)), int(round(y1 + ny * width / 2)))
            r4 = (int(round(x1 - nx * width / 2)), int(round(y1 - ny * width / 2)))

            points = rasterize_rectangle((r1, r2, r3, r4))
    else:
        raise ValueError("Illegal line width!")

    return points


def rasterize_rectangle(rectangle):
    """ Return a list of points that form a rasterized rectangle
    retangle (4 x (int, int)): the x,y coordinates of each corner of the rectangle
    return (list of (int, int)): all the points in the rectangle.
    """

    if len(rectangle) != 4:
        raise ValueError("Incorrect number of vertices!")

    r1, r2, r3, r4 = rectangle
    points = []

    l1 = rasterize_line(r1, r2)
    l2 = rasterize_line(r2, r3)
    l3 = rasterize_line(r3, r4)
    l4 = rasterize_line(r4, r1)

    points.extend(l1[:-1])
    points.extend(l2[:-1])
    points.extend(l3[:-1])
    points.extend(l4[:-1])

    for x in range(min(r1[0], r2[0], r3[0], r4[0]), max(r1[0], r2[0], r3[0], r4[0])):
        for y in range(min(r1[1], r2[1], r3[1], r4[1]), max(r1[1], r2[1], r3[1], r4[1])):
            if point_in_polygon((x, y), [r1, r2, r3, r4]):
                points.append((x, y))

    return points


def point_in_polygon(p, polygon):
    """ Determine if the given point is inside the polygon
    p (int, int): x, y of the point
    polygon (4 x (int, int)): the x,y coordinates of each vertices of the polygon
    return (bool): True if the point is within the polygon
    """
    x, y = p

    n = len(polygon)
    inside = False

    p1x, p1y = polygon[0]
    for i in range(n+1):
        p2x, p2y = polygon[i % n]
        # consider the case where the point is on the vertex of the polygon
        if p2x == x and p2y == y:
            return True
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x)/(p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


def get_polygon_grid_cells(polygon_vertices: numpy.ndarray, include_neighbours: bool = False) -> Set[Tuple[int, int]]:
    """
    Returns the set of grid cells (tiles) that are touched or intersected by the edges of a polygon.

    This function is useful when you want to determine which grid-based tiles (e.g. pixels, data acquisition fields) are
    crossed by the boundary of a polygon. This is commonly needed in contexts like:
    - rasterizing a polygon onto a discrete grid

    The polygon is defined in grid coordinates (rows, columns), and must be "closed" — i.e. the last point should
    connect back to the first one. If not already closed, the function will auto-close the polygon.

    :param polygon_vertices: NumPy array of shape (N, 2), where each row is (row, col) — the corners of a closed polygon.
    :param include_neighbours: If True, includes adjacent grid cells around each edge point. This is useful for ensuring
                               complete fill coverage or avoiding boundary artifacts.

    :return: A set of (row, col) tuples representing the intersected grid cells (tiles).
    :raises ValueError: If the polygon has less than 3 vertices or if the vertices are not in 2D format.
    """
    # Note: Efficiently implemented using Bresenham's line algorithm for minimal memory and fast performance.
    #  █ █ █ █ █ █ █ █ █ █ █ █ █ █ █
    #  █ ░ █ █ █ █ █ █ █ █ █ █ █ █ █
    #  █ █ ░ ░ █ █ █ █ █ █ █ █ █ █ █
    #  █ █ █ █ ░ ░ █ █ █ █ █ █ █ █ █
    #  █ █ █ █ █ █ ░ █ █ █ █ █ █ █ █
    #  █ █ █ █ █ █ █ ░ ░ █ █ █ █ █ █
    #  █ █ █ █ █ █ █ █ █ ░ ░ █ █ █ █
    #  █ █ █ █ █ █ █ █ █ █ █ ░ █ █ █
    #  █ █ █ █ █ █ █ █ █ █ █ █ ░ █ █
    #  █ █ █ █ █ █ █ █ █ █ █ █ █ █ █
    #  \___________________________/
    #         Bresenham's Line
    #
    # Legend:
    # '█' - Background grid
    # '░' - Cells forming the Bresenham line
    # '\' - Approximate direction of the line
    #
    #  █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █
    #  █ █ █ █ █ █ █ █ █ █ █ █ █ ░ ░ ░ ░ ░ ░ █ █ █ █ █ █
    #  █ █ █ █ █ █ █ █ █ █ ░ ░ ░ █ █ █ █ █ ░ ░ █ █ █ █ █
    #  █ █ █ █ █ █ █ █ ░ ░ █ █ █ █ █ █ █ █ █ ░ ░ █ █ █ █
    #  █ █ █ █ █ █ ░ ░ █ █ █ █ █ █ █ █ █ █ █ █ ░ ░ █ █ █
    #  █ █ █ █ ░ ░ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ░ █ █
    #  █ █ █ ░ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ░ █ █
    #  █ █ ░ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ░ █ █
    #  █ █ █ ░ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ░ █ █
    #  █ █ █ █ ░ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ░ █ █ █
    #  █ █ █ █ █ ░ █ █ █ █ █ █ █ █ █ █ █ █ █ █ ░ █ █ █ █
    #  █ █ █ █ █ █ ░ ░ ░ ░ ░ ░ ░ ░ ░ ░ ░ ░ ░ ░ █ █ █ █ █
    #  █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █ █
    #  \_______________________________________________/
    #  Polygon intersections using Bresenham's Line algorithm
    #
    # Legend:
    # '█' - Background grid
    # '░' - Cells forming the polygon's outline
    # '\' - Visual boundary
    # if include_neighbours is True, the adjacent cells of '░' will also be included in the output set.

    if polygon_vertices.shape[0] < 3:
        raise ValueError("The polygon must have at least 3 vertices.")
    if polygon_vertices.shape[1] != 2:
        raise ValueError("The polygon vertices should be a 2D array with shape (N, 2).")

    # Auto-close the polygon if it's not closed
    if not numpy.array_equal(polygon_vertices[0], polygon_vertices[-1]):
        polygon_vertices = numpy.vstack([polygon_vertices, polygon_vertices[0]])

    intersections = set()
    for i in range(len(polygon_vertices) - 1):
        row1, col1 = polygon_vertices[i]
        row2, col2 = polygon_vertices[i + 1]

        dx = abs(row2 - row1)
        dy = abs(col2 - col1)
        sx = 1 if row1 < row2 else -1
        sy = 1 if col1 < col2 else -1
        err = dx - dy

        while True:
            intersections.add((row1, col1))

            if include_neighbours:
                # Add adjacent grid cells to ensure complete coverage
                intersections.add((row1 + sx, col1))
                intersections.add((row1 - sx, col1))
                intersections.add((row1, col1 + sy))
                intersections.add((row1, col1 - sy))

            if row1 == row2 and col1 == col2:
                break

            e2 = err * 2
            if e2 > -dy:
                err -= dy
                row1 += sx
            if e2 < dx:
                err += dx
                col1 += sy

    return intersections
