# -*- coding: utf-8 -*-
"""
Utility functions for 2D geometry: rectangles, lines, polygons and projections.

Copyright © 2013-2024 Delmic

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

import math
from typing import List, Optional, Tuple


def rect_intersect(ra: Tuple[float, float, float, float],
                   rb: Tuple[float, float, float, float]
                   ) -> Optional[Tuple[float, float, float, float]]:
    """
    Computes the rectangle representing the intersection area of two rectangles
    (aligned along the axes).

    :param ra: position of the first rectangle as left, top, right, bottom
    :param rb: position of the second rectangle
    :returns: None if there is no intersection, or the rectangle representing
        the intersection as left, top, right, bottom. The return value always
        has top < bottom and left < right.

    Note that the rectangles can have the top/bottom and left/right in any order.
    """
    # Make sure that t<b and l<r
    ra = (min(ra[0], ra[2]), min(ra[1], ra[3]),
          max(ra[0], ra[2]), max(ra[1], ra[3]))

    rb = (min(rb[0], rb[2]), min(rb[1], rb[3]),
          max(rb[0], rb[2]), max(rb[1], rb[3]))

    # Any intersection?
    if ra[0] >= rb[2] or ra[2] <= rb[0] or ra[1] >= rb[3] or ra[3] <= rb[1]:
        return None

    inter = (max(ra[0], rb[0]), max(ra[1], rb[1]),
             min(ra[2], rb[2]), min(ra[3], rb[3]))

    return inter


def perpendicular_distance(start: Tuple[float, float],
                           end: Tuple[float, float],
                           point: Tuple[float, float]) -> float:
    """
    Computes the perpendicular distance between a line segment and a point (in 2D space).

    :param start: beginning of the line segment
    :param end: end of the line segment
    :param point: point anywhere in space
    :returns: distance (>= 0)
    """
    x1, y1 = start
    x2, y2 = end
    x3, y3 = point

    # Find the closest point on the segment
    px = x2 - x1
    py = y2 - y1
    v = px * px + py * py

    if v == 0:
        # If start and end are the same point => it's also the closest point
        u = 0  # any value works
    else:
        u = ((x3 - x1) * px + (y3 - y1) * py) / v
        u = min(max(u, 0), 1)

    x = x1 + u * px
    y = y1 + u * py

    # Compute the distance between the external point and the closest point
    dx = x - x3
    dy = y - y3
    return math.hypot(dx, dy)


INSIDE, LEFT, RIGHT, LOWER, UPPER = 0, 1, 2, 4, 8


def clip_line(xmin: float, ymax: float, xmax: float, ymin: float,
              x1: float, y1: float, x2: float, y2: float
              ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    """
    Clip a line to a rectangular area.

    This implements the Cohen-Sutherland line clipping algorithm. Although it's not the most
    efficient clipping algorithm, it was chosen because it's best at cheaply determining the trivial
    cases (line being completely inside or outside the bounding box).

    Code based on https://github.com/scienceopen/cv-utils/blob/master/lineClipping.py
    Copyright (c) 2014 Michael Hirsch
    """

    def _get_pos(xa, ya):
        p = INSIDE  # default is inside

        # consider x
        if xa < xmin:
            p |= LEFT
        elif xa > xmax:
            p |= RIGHT

        # consider y
        if ya < ymin:
            p |= LOWER  # bitwise OR
        elif ya > ymax:
            p |= UPPER  # bitwise OR
        return p

    # check for trivially outside lines
    k1 = _get_pos(x1, y1)
    k2 = _get_pos(x2, y2)

    while (k1 | k2) != 0:  # if both points are inside box (0000) , ACCEPT trivial whole line in box

        # if line trivially outside window, REJECT
        if (k1 & k2) != 0:
            return None, None, None, None

        # this is not a bitwise or, it's the word "or"
        opt = k1 or k2  # take first non-zero point, short circuit logic
        if opt & UPPER:
            x = x1 + (x2 - x1) * (ymax - y1) / (y2 - y1)
            y = ymax
        elif opt & LOWER:
            x = x1 + (x2 - x1) * (ymin - y1) / (y2 - y1)
            y = ymin
        elif opt & RIGHT:
            y = y1 + (y2 - y1) * (xmax - x1) / (x2 - x1)
            x = xmax
        elif opt & LEFT:
            y = y1 + (y2 - y1) * (xmin - x1) / (x2 - x1)
            x = xmin
        else:
            raise RuntimeError('Undefined clipping state')

        if opt == k1:
            x1, y1 = x, y
            k1 = _get_pos(x1, y1)
        elif opt == k2:
            x2, y2 = x, y
            k2 = _get_pos(x2, y2)

    return int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))


def intersect(ra: Tuple[float, float, float, float],
              rb: Tuple[float, float, float, float]
              ) -> Optional[Tuple[float, float, float, float]]:
    """
    Computes the intersection between two rectangles of the form (left, top, width, height).

    :param ra: first rectangle as left, top, width, height
    :param rb: second rectangle as left, top, width, height
    :returns: None if no intersection, otherwise the intersection as left, top, width, height
    """
    ax, ay, aw, ah = ra
    bx, by, bw, bh = rb

    # Return None if there's no intersection
    if ax >= bx + bw or ay >= by + bh or bx >= ax + aw or by >= ay + ah:
        return None

    # Calculate the intersection's top left and width and height
    ix = max(ax, bx)
    iy = max(ay, by)
    iw = min(ax + aw, bx + bw) - ix
    ih = min(ay + ah, by + bh) - iy

    return ix, iy, iw, ih


def normalize_rect(rect):
    """
    Ensure that the given rectangle actually is defined by xmin, ymin, xmax, ymax
    so that y1 < y2 and x1 < x2.

    :param rect: iterable of 4 floats: x1, y1, x2, y2
    :returns: same type as rect, with xmin, ymin, xmax, ymax
    """
    x1, y1, x2, y2 = rect
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    # Re-create the result using the same type as the `rect` parameter
    return type(rect)((x1, y1, x2, y2))


def is_point_in_rect(p: Tuple[float, float],
                     rect: Tuple[float, float, float, float]) -> bool:
    """
    Check if a point is inside in a rectangle.

    :param p: x, y coordinates of point
    :param rect: minx, miny, maxx, maxy positions of rectangle
    :returns: True if point is in rectangle, False otherwise
    """
    minx, miny, maxx, maxy = rect
    return minx <= p[0] <= maxx and miny <= p[1] <= maxy


def expand_rect(rect: Tuple[float, float, float, float],
                margin: float) -> Tuple[float, float, float, float]:
    """
    Expand a rectangle by a fixed margin.

    :param rect: minx, miny, maxx, maxy positions of rectangle
    :param margin: margin to increase rectangle by
    :returns: minx, miny, maxx, maxy positions of adjusted rectangle
    """
    minx, miny, maxx, maxy = rect
    return minx - margin, miny - margin, maxx + margin, maxy + margin


def rotate_rect(rect: Tuple[float, float, float, float],
                angle: float,
                center: Optional[Tuple[float, float]] = None
                ) -> List[Tuple[float, float]]:
    """
    Rotate a rectangle (aligned on the axes) around a center point.

    :param rect: minx, miny, maxx, maxy positions of rectangle
    :param angle: angle of rotation in radians
    :param center: x, y coordinates of center point. If None, the rectangle center is used.
    :returns: position (x, y) of the 4 corners of the rotated rectangle (ordered clockwise,
        starting from minx, miny)
    """
    minx, miny, maxx, maxy = rect
    if center is None:
        center = ((minx + maxx) / 2, (miny + maxy) / 2)
    cx, cy = center

    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    rotated_corners = []
    for x, y in corners:
        # Translate point to origin
        x -= cx
        y -= cy

        # Rotate point
        x_new = x * cos_a - y * sin_a
        y_new = x * sin_a + y * cos_a

        # Translate point back
        x_new += cx
        y_new += cy

        rotated_corners.append((x_new, y_new))

    return rotated_corners


def separate_rect_rotation(corners: List[Tuple[float, float]],
                           ) -> Tuple[Tuple[float, float, float, float], float]:
    """
    Given a rectangle defined by its 4 corner points, return a rectangle aligned with the axes
    plus an angle of rotation to be applied around the center.
    It assumes the corners correspond to a rotated rectangle (no noise).

    :param corners: position (x, y) of the 4 corners of the rectangle. The order is::

         +---------------------> X
         |   0 -------------- 1
         |   |                |
         |   |                |
         |   |                |
         V   3 -------------- 2
         Y

    :returns:
      * rect (minx, miny, maxx, maxy): positions of the rectangle aligned with the axes
      * angle: rotation in radians (between 0 and 2pi)
    """
    # Compute the rotation as the angle from corner 0 to corner 1
    x0, y0 = corners[0]
    x1, y1 = corners[1]
    if x0 == x1 and y0 == y1:
        # If corners 0 & 1 are at the same position (meaning it's extremely thin rectangle), it's
        # not possible to deduce the rotation from them. So instead, we use corners 0 & 3 + 90° to define
        # the rotation.
        x3, y3 = corners[3]
        if x1 == x3 and y1 == y3:  # It's just a point => let's say the rotation is 0
            return (x1, y1, x3, y3), 0.0
        angle = (math.atan2(y3 - y0, x3 - x0) - math.pi / 2) % (2 * math.pi)
    else:
        angle = math.atan2(y1 - y0, x1 - x0) % (2 * math.pi)
        x3, y3 = corners[3]

    # Rotate the first and third corner back to align with the axes
    cx = (x0 + corners[2][0]) / 2
    cy = (y0 + corners[2][1]) / 2

    cos_a = math.cos(-angle)
    sin_a = math.sin(-angle)

    rect = []
    for x, y in (corners[0], corners[2]):
        # Translate point to origin
        x -= cx
        y -= cy

        # Rotate point
        x_new = x * cos_a - y * sin_a
        y_new = x * sin_a + y * cos_a

        # Translate point back
        rect.append(x_new + cx)
        rect.append(y_new + cy)

    rect = normalize_rect(tuple(rect))
    return rect, angle


def get_polygon_bbox(coordinates: List[Tuple[float, float]]
                     ) -> Tuple[float, float, float, float]:
    """
    Get the bounding box of a polygon defined by a list of 2D coordinates.

    :param coordinates: list of (x, y) tuples
    :returns: a_min, b_min, a_max, b_max
    :raises ValueError: if coordinates has fewer than 2 elements or contains
        non-2D coordinates
    """
    if len(coordinates) <= 1:
        raise ValueError(f"Coordinates contains {len(coordinates)} elements, two or more are required.")

    for coordinate in coordinates:
        if len(coordinate) != 2:
            raise ValueError(
                f"The function only works for 2D coordinates, coordinate: {coordinate} has {len(coordinate)} dimensions.")

    maximum = list(map(max, zip(*coordinates)))
    minimum = list(map(min, zip(*coordinates)))

    return minimum[0], minimum[1], maximum[0], maximum[1]


def slope_of_line(point1: Tuple[float, float], point2: Tuple[float, float]) -> float:
    """
    Calculate the slope of a line passing through two given points.

    :param point1: first point (x, y)
    :param point2: second point (x, y)
    :returns: slope of the line, or math.inf for vertical lines
    """
    if point1[0] == point2[0]:  # Vertical line
        slope = math.inf  # Slope is undefined for vertical lines
    else:
        slope = (point2[1] - point1[1]) / (point2[0] - point1[0])
    return slope


def intercept_of_line(point: Tuple[float, float], slope: float) -> float:
    """
    Calculate the intercept of a line passing through a given point with a specified slope.

    The equation of a line in slope-intercept form is y = mx + c, where:
    y is the vertical position, x is the horizontal position, m is the slope,
    and c is the y-intercept. For vertical lines, the x-coordinate of the point
    is returned instead.

    :param point: the coordinates of the point through which the line passes
    :param slope: the slope of the line
    :returns: y-intercept for non-vertical lines, x-intercept for vertical lines
    """
    if math.isinf(slope):  # line is vertical
        intercept = point[0]  # x-intercept for vertical line
    else:
        intercept = point[1] - slope * point[0]  # y-intercept for non-vertical line
    return intercept


def project_point_on_line(
        point: Tuple[float, float], line_slope: float, line_intercept: float
) -> Tuple[float, float]:
    """
    Calculate the projection of a point on a line.

    The projected point is the intersection point of the line passing through
    the given point and perpendicular to the given line.

    x_projected = x + m * (y - c) / (1 + m^2)
    y_projected = m * x_projected + c

    :param point: the coordinates (x, y) of the point to be projected on the line
    :param line_slope: the slope of the line
    :param line_intercept: the intercept of the line
    :returns: the coordinates of the projected point on the line
    """
    if math.isinf(line_slope):  # Vertical line
        x_projected = line_intercept
        y_projected = point[1]
    else:
        x_projected = (point[0] + line_slope * (point[1] - line_intercept)) / (1 + line_slope ** 2)
        y_projected = line_slope * x_projected + line_intercept
    return (x_projected, y_projected)
