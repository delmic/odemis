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

from builtins import range  # For Python 2 & 3

import math


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
