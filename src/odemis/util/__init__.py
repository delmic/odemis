# -*- coding: utf-8 -*-
"""
Created on 26 Feb 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

# Various helper functions that have a generic usefulness
# Warning: do not put anything that has dependencies on non default python modules

import itertools
import logging
import math
from collections.abc import Mapping
from typing import Iterable, Tuple, TypeVar

import numpy

from .concurrent import (  # noqa: F401
    BackgroundWorker,
    RepeatingTimer,
    bindFuture,
    executeAsyncTask,
    inspect_getmembers,
    limit_invocation,
    timeout,
)
# Re-export from sub-modules for backward compatibility
from .geometry import (  # noqa: F401
    INSIDE, LEFT, RIGHT, LOWER, UPPER,
    clip_line,
    expand_rect,
    get_polygon_bbox,
    intercept_of_line,
    intersect,
    is_point_in_rect,
    normalize_rect,
    perpendicular_distance,
    project_point_on_line,
    rect_intersect,
    rotate_rect,
    separate_rect_rotation,
    slope_of_line,
)

# Used in the type signature of `pairwise()` below such that we can define the
# return type as a function of the input type.
T = TypeVar("T")


# helper functions
def pairwise(iterable: Iterable[T]) -> Iterable[Tuple[T, T]]:
    """s -> (s0,s1), (s1,s2), (s2, s3), ..."""
    # will be added to itertools in Python 3.10
    a, b = itertools.tee(iterable)
    next(b, None)
    return zip(a, b)


def get_best_dtype_for_acc(idtype, count):
    """
    Computes the smallest dtype that allows to integrate the number of inputs without overflowing.
    :param idtype: (dtype) dtype of the input (the raw data that is integrated).
    :param count: (int) Number of values/images to be accumulated/integrated.
    :returns: (adtype) The best fitting dtype.
    """
    if idtype.kind not in "ui":
        return idtype
    else:
        maxval = numpy.iinfo(idtype).max * count
        if idtype.kind == "i":
            maxval = -maxval  # force an signed int

        if -2 ** 63 <= maxval < 2 ** 64:
            # Anything bigger, numpy returns a Python integer type (very slow & big)
            adtype = numpy.min_scalar_type(maxval)
        else:
            logging.debug("Going to use lossy intermediate type in order to support values up to %d", maxval)
            adtype = numpy.float64  # might accumulate errors

        return adtype


def find_closest(val, l):
    """ finds in a list the closest existing value from a given value """
    return min(l, key=lambda x: abs(x - val))


def index_closest(val, l):
    """
    finds in a list the index of the closest existing value from a given value
    Works also with dict and tuples.
    """
    if isinstance(l, dict):
        return min(l.items(), key=lambda x: abs(x[1] - val))[0]
    else:
        return min(enumerate(l), key=lambda x: abs(x[1] - val))[0]


def round_up_to_multiple(v: float, m: float) -> float:
    """
    Rounds up a value to the nearest multiple of another value.
    :param v: value to round up
    :param m: the multiplicand
    :return: the closest value >= v, that is k x m, with k an integer.
    """
    # Subtract a small value to avoid rounding errors when values are already a multiple of m
    return math.ceil((v - 1e-18) / m) * m


def almost_equal(a, b, atol=1e-18, rtol=1e-7):
    """
    Compares two floats within a margin (to handle rounding errors).
    a (float)
    b (float)
    atol (float): absolute tolerance
    rtol (float): relative tolerance
    returns (bool): True if a and b are almost equal
    """
    # Since Python 3.5, there exists an almost equal function
    return math.isclose(a, b, rel_tol=rtol, abs_tol=atol)


def rot_almost_equal(a: float, b: float, atol: float = 1e-18, rtol: float = 1e-7) -> bool:
    """
    Check the rotation difference between two radian angles is within a margin
    a: an angle, in radians
    b: an angle, in radians
    atol: absolute tolerance
    rtol: relative tolerance
    returns: True if a and b rotation is within a margin
    """
    if a == b:
        return True

    # Convert rtol to an absolute value (based on the largest argument)
    # and pick the final tolerance as the biggest of the tolerances.
    tol = max(atol, abs(wrap_to_mpi_ppi(a)) * rtol, abs(wrap_to_mpi_ppi(b)) * rtol)
    # calculate the difference as a value between -pi and pi, and check it's near 0
    return abs(wrap_to_mpi_ppi(a - b)) <= tol


def wrap_to_mpi_ppi(a: float) -> float:
    """
    Convert an angle to a value between -pi and +pi.
    That's the representation of the angle with the smallest absolute value.
    a: an angle, in radians
    return (-pi <= float <= pi): same angle, but modulo
    """
    return (a + math.pi) % (2 * math.pi) - math.pi


def rot_shortest_move(a: float, b: float, cycle: float = 2 * math.pi) -> float:
    """
    Computes the small rotational move to go from angle a to angle b, assuming
    that a whole cycle is possible.
    For instance, to go from 0 rad to 1.5π, with the usual cycle of 2π, the shortest move is -0.5π.
    :param a: an angle, in any unit, can be any value
    :param b: an angle, in the same unit as b, can be any value
    :param cycle (> 0): the angle corresponding to a whole rotation.
    :return: an angle between -cycle/2 and +cycle/2, in the same unit as a and b
    """
    vector = b - a
    # mod1 and mod2 are always positive as cycle is positive
    mod1 = vector % cycle
    mod2 = -vector % cycle

    if mod1 < mod2:
        return mod1
    else:
        return -mod2


def to_str_escape(s):
    """
    Escapes the given string (or bytes) in such a way that all the
    non-displayable characters converted to "\\??". It's possible to use that string
    in a Python interpreter to obtain the original data.
    s (bytes or str): the data to escape
    return (str): A user-displayable string, which has no control character.
    """
    # Python 3 "lost" its ability to directly escape a string. It's still
    # possible to call .encode("unicode_escape"), but only on a string, and
    # it returns bytes (which need to be converted back to string for display)
    if isinstance(s, bytes):
        # 40 % faster than "repr(s)[2:-1]"
        return s.decode("latin1").encode("unicode_escape").decode("ascii")
    else:  # str
        # 50% faster than "repr(s)[1:-1]"
        return s.encode("unicode_escape").decode("ascii")


def recursive_dict_update(d, other):
    """ Recursively update the values of the first dictionary with the values of the second

    Args:
        d (dict): dictionary to update
        other (dict): dictionary containing keys to add/overwrite

    Returns:
        (dict) The updated dictionary

    """

    for k, v in other.items():
        if isinstance(v, Mapping):
            r = recursive_dict_update(d.get(k, {}), v)
            d[k] = r
        else:
            d[k] = other[k]
    return d


def sorted_according_to(la, lb):
    """
    Sort a list (or any iterable) following the order of another list. If an
      item of a la is not in lb, then it'll be placed at the end.
    la (iterable): the list to sort
    lb (list or tuple): a list the same values as la, but in the order expected
    return (list): la sorted according to lb
    """

    def index_in_lb(e):
        """ return the position of the element in lb """
        try:
            return lb.index(e)
        except ValueError:  # e is not in lb => put last
            return len(lb) + 1

    return sorted(la, key=index_in_lb)


def find_plot_content(xd, yd):
    """
    Locate the first leftmost non-zero value and the first rightmost non-zero
    value for a set of points.
    xd (list of floats): The X coordinates of the points, ordered.
    yd (list of floats): The Y coordinates of the points.
      Must be the same length as xd.
    return (float, float): the horizontal range that fits the data content.
      IOW, these are the xd value for the first and last non-null yd.
    """
    if len(xd) != len(yd):
        raise ValueError("xd and yd should be the same length")

    ileft = 0  # init value (useful in case len(yd) = 0)
    for ileft in range(len(yd)):
        if yd[ileft] != 0:
            break

    iright = len(yd) - 1  # init value (useful in case len(yd) <= 1)
    for iright in range(len(yd) - 1, 0, -1):
        if yd[iright] != 0:
            break

    # if ileft is > iright, then the data must be all 0.
    if ileft >= iright:
        return xd[0], xd[-1]

    # If the empty portion on the edge is less than 5% of the full data
    # width, show it anyway.
    threshold = 0.05 * len(yd)
    if ileft < threshold:
        ileft = 0
    if (len(yd) - iright) < threshold:
        iright = len(yd) - 1

    return xd[ileft], xd[iright]
