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

import queue
from collections.abc import Mapping
from concurrent.futures import CancelledError
from decorator import decorator
from functools import wraps
import inspect
import itertools
import logging
import math
import numpy
import signal
import sys
import threading
import time
import weakref
import types
from typing import Iterable, Tuple, TypeVar

from . import weak

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

        if maxval <= numpy.iinfo(numpy.uint64).max:
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
        return min(l.items(), key=lambda x:abs(x[1] - val))[0]
    else:
        return min(enumerate(l), key=lambda x:abs(x[1] - val))[0]


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


def rot_almost_equal(a: float, b: float, atol: float=1e-18, rtol: float=1e-7) -> bool:
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


def rect_intersect(ra, rb):
    """
    Computes the rectangle representing the intersection area of two rectangles
    (aligned along the axes).
    ra (tuple of 4 floats): position of the first rectangle left, top, right, bottom
    rb (tuple of 4 floats): position of the second rectangle
    return (None or tuple of 4 floats): None if there is no intersection, or
     the rectangle representing the intersection
    Note that the rectangles can have the top/bottom and left/right in any order,
    but the return value will always have top < bottom and left < right.
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


def perpendicular_distance(start, end, point):
    """
    Computes the perpendicular distance between a line segment and a point (in 2D space).
    start (float, float): beginning of the line segment
    end (float, float): end of the line segment
    point (float, float): point anywhere in space
    return (0 <= float): distance
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


def clip_line(xmin, ymax, xmax, ymin, x1, y1, x2, y2):
    """ Clip a line to a rectangular area

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


def intersect(ra, rb):
    """
    Computes the intersection between two rectangles of the form (left, top, width, height)
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
    """ Ensure that the given rectangle actually is defined by xmin, ymin, xmax, ymax
    so that y1 < y2 and x1 < x2.

    rect (iterable of 4 floats): x1, y1, x2, y2
    return (iterable of 4 floats): xmin, ymin, xmax, ymax

    """

    x1, y1, x2, y2 = rect
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    # Re-create the result using the same type as the `rect` parameter
    return type(rect)((x1, y1, x2, y2))


def is_point_in_rect(p, rect):
    """
    Check if a point is inside in a rectangle.

    p (tuple of 2 floats): x, y coordinates of point
    rect (tuple of 4 floats): minx, miny, maxx, maxy positions of rectangle
    return (bool): True if point is in rectangle, False otherwise
    """
    minx, miny, maxx, maxy = rect
    return minx <= p[0] <= maxx and miny <= p[1] <= maxy


def expand_rect(rect, margin):
    """
    Expand a rectangle by a fixed margin.

    rect (tuple of 4 floats): minx, miny, maxx, maxy positions of rectangle
    margin (float): margin to increase rectangle by
    return (tuple of 4 floats): minx, miny, maxx, maxy positions of adjusted rectangle
    """
    minx, miny, maxx, maxy = rect
    return minx - margin, miny - margin, maxx + margin, maxy + margin


def get_polygon_bbox(coordinates):
    """
    Get the maximum and minimum values for a and b from a list of tuples
    with shape: [(a1,b1), (a2,b2), ....., (an,bn)]

    :param coordinates: (list of nested tuples (a,b))
    :return: a_min, b_min, a_max, b_max
    """
    if len(coordinates) <= 1:
        raise ValueError(f"Coordinates contains {len(coordinates)} elements, two or more are required.")

    for coordinate in coordinates:
        if len(coordinate) != 2:
            raise ValueError(f"The function only works for 2D coordinates, coordinate: {coordinate} has {len(coordinate)} dimensions.")

    maximum = list(map(max, zip(*coordinates)))
    minimum = list(map(min, zip(*coordinates)))

    return minimum[0], minimum[1], maximum[0], maximum[1]


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


def _li_thread(delay, q):

    try:
        exect = time.time()
        while True:
            # read the latest arguments in the queue (if there are more)
            t, f, args, kwargs = q.get() # first wait until there is something
            if t is None:
                return

            # wait until it's time for it
            next_t = (min(exect, t) + delay)
            while True: # discard arguments if there is newer calls already queued
                sleep_t = next_t - time.time()
                if sleep_t > 0:
                    # logging.debug("waiting %f s until executing call", sleep_t)
                    # time.sleep(sleep_t)
                    timeout = sleep_t
                    block = True
                else: # just check one last time
                    block = False
                    timeout = None

                try:
                    t, f, args, kwargs = q.get(block=block, timeout=timeout)
                    if t is None: # Sign that we should stop (object is gone)
                        return
                    # logging.debug("Overriding call with call at %f", t)
                except queue.Empty:
                    break

            try:
                exect = time.time()
                # logging.debug("executing function %s with a delay of %f s", f.__name__, exect - t)
                f(*args, **kwargs)
            except Exception:
                logging.exception("During limited invocation call")

            # clean up early, to avoid possible cyclic dep on the instance
            del f, args, kwargs

    finally:
        logging.debug("Ending li thread")


def limit_invocation(delay_s):
    """ This decorator limits how often a method will be executed.

    The first call will always immediately be executed. The last call will be
    delayed 'delay_s' seconds at the most. In between the first and last calls,
    the method will be executed at 'delay_s' intervals. In other words, it's
    a rate limiter.

    :param delay_s: (float) The minimum interval between executions in seconds.

    Note that the method might be called in a separate thread. In wxPython, you
    might need to decorate it by @call_in_wx_main to ensure it is called in the GUI
    thread.

    """

    if delay_s > 5:
        logging.warning("Warning! Long delay interval. Please consider using "
                     "an interval of 5 or less seconds")

    def li_dec(f):
        # Share a lock on the class (as it's not easy on the instance)
        # Note: we can only do this at init, after it's impossible to add/set
        # attribute on an method
        f._li_lock = threading.Lock()

        # Hacky way to store value per instance and per methods
        last_call_name = '%s_lim_inv_last_call' % f.__name__
        queue_name = '%s_lim_inv_queue' % f.__name__
        wr_name = '%s_lim_inv_wr' % f.__name__

        @wraps(f)
        def limit(self, *args, **kwargs):
            if inspect.isclass(self):
                raise ValueError("limit_invocation decorators should only be "
                                 "assigned to instance methods!")

            now = time.time()
            with f._li_lock:
                # If the function was called later than 'delay_s' seconds ago...
                if (hasattr(self, last_call_name) and
                    now - getattr(self, last_call_name) < delay_s):
                    # logging.debug('Delaying method call')
                    try:
                        q = getattr(self, queue_name)
                    except AttributeError:
                        # Create everything need
                        q = queue.Queue()
                        setattr(self, queue_name, q)

                        # Detect when instance of self is dereferenced
                        # and kill thread then
                        def on_deref(obj):
                            # logging.debug("object %r gone", obj)
                            q.put((None, None, None, None)) # ask the thread to stop

                        wref = weakref.ref(self, on_deref)
                        setattr(self, wr_name, wref)

                        t = threading.Thread(target=_li_thread,
                                             name="li thread for %s" % f.__name__,
                                             args=(delay_s, q))
                        t.daemon = True
                        t.start()

                    q.put((now, f, (self,) + args, kwargs))
                    setattr(self, last_call_name, now + delay_s)
                    return
                else:
                    # execute method call now
                    setattr(self, last_call_name, now)

            return f(self, *args, **kwargs)
        return limit
    return li_dec


def inspect_getmembers(object, predicate=None):
    """
    Fix for the corresponding function in inspect. If we modify __getattr__ of a function, inspect.getmembers()
    doesn't work as intended (a TypeError is raised). The change consists of one line (highlighted below).
    https://stackoverflow.com/questions/54478679/workaround-for-getattr-special-method-breaking-inspect-getmembers-in-pytho
    """
    # Line below adds inspect. reference to isclass()
    if inspect.isclass(object):
        # Line below adds inspect. reference to getmro()
        mro = (object,) + inspect.getmro(object)
    else:
        mro = ()
    results = []
    processed = set()
    names = dir(object)
    # :dd any DynamicClassAttributes to the list of names if object is a class;
    # this may result in duplicate entries if, for example, a virtual
    # attribute with the same name as a DynamicClassAttribute exists
    try:
        for base in object.__bases__:
            for k, v in base.__dict__.items():
                if isinstance(v, types.DynamicClassAttribute):
                    names.append(k)
    #################################################################
    ### Modification to inspect.getmembers: also catch TypeError here
    #################################################################
    except (AttributeError, TypeError):
        pass
    for key in names:
        # First try to get the value via getattr.  Some descriptors don't
        # like calling their __get__ (see bug #1785), so fall back to
        # looking in the __dict__.
        try:
            value = getattr(object, key)
            # handle the duplicate key
            if key in processed:
                raise AttributeError
        except AttributeError:
            for base in mro:
                if key in base.__dict__:
                    value = base.__dict__[key]
                    break
            else:
                # could be a (currently) missing slot member, or a buggy
                # __dir__; discard and move on
                continue
        if not predicate or predicate(value):
            results.append((key, value))
        processed.add(key)
    results.sort(key=lambda pair: pair[0])
    return results

class TimeoutError(Exception):
    pass


# TODO: only works on Unix, needs a fallback on windows (at least, don't complain)
# from http://stackoverflow.com/questions/2281850/timeout-function-if-it-takes-too-long-to-finish
# see http://code.activestate.com/recipes/577853-timeout-decorator-with-multiprocessing/
# for other implementation
def timeout(seconds):
    """
    timeout decorator. Stops a function from executing after a given time. The
      function will raise an exception in this case.
    seconds (0 < float): time in second before the timeout
    """
    assert seconds > 0
    def handle_timeout(signum, frame):
        logging.info("Stopping function after timeout of %g s", seconds)
        raise TimeoutError("Function took more than %g s to execute" % seconds)

    def wrapper(f, *args, **kwargs):
        prev_handler = signal.signal(signal.SIGALRM, handle_timeout)
        try:
            signal.setitimer(signal.ITIMER_REAL, seconds) # same as alarm, but accepts float
            return f(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prev_handler)

    return decorator(wrapper)


class RepeatingTimer(threading.Thread):
    """
    An almost endless timer thread.
    It stops when calling cancel() or the callback disappears.
    """
    def __init__(self, period, callback, name="TimerThread"):
        """
        period (float): time in second between two calls
        callback (callable): function to call
        name (str): fancy name to give to the thread
        """
        threading.Thread.__init__(self, name=name)
        self.callback = weak.WeakMethod(callback)
        self.period = period
        self.daemon = True
        self._must_stop = threading.Event()

    def run(self):
        # use the timeout as a timer
        try:
            wait_time = self.period
            while not self._must_stop.wait(wait_time):
                tstart = time.time()
                try:
                    self.callback()
                except weak.WeakRefLostError:
                    # it's gone, it's over
                    return
                wait_time = max(0, (tstart + self.period) - time.time())
        except Exception:
            logging.exception("Failure while calling repeating timer '%s'", self.name)
        finally:
            logging.debug("Repeating timer thread '%s' over", self.name)

    def cancel(self):
        self._must_stop.set()


def executeAsyncTask(future, fn, args=(), kwargs=None):
    """
    Execute a task in a separate thread. To follow the state of execution,
      the given future is bound to it. Handy to run a Future without an executor.
    future (Future): future that is used to represent the task
    fn (callable): function to call for running the future
    args, kwargs: passed to the fn
    returns Thread: the thread running the task
    """
    if kwargs is None:
        kwargs = {}
    thread = threading.Thread(target=bindFuture,
                              name="Future runner",
                              args=(future, fn),
                              kwargs={"args": args, "kwargs": kwargs})
    thread.start()
    return thread


def bindFuture(future, fn, args=(), kwargs=None):
    """
    Start and follow a task by connecting it to a Future. It takes care of
      updating the state of the future based on the call status. It is blocking
      until the task is finished (or cancelled), so usually, it is called as
      main target of a (separate) thread.
    Based on the standard futures code _WorkItem.run()
    future (Future): future that is used to represent the task
    fn (callable): function to call for running the future
    *args, **kwargs: passed to the fn
    returns None: when the task is over (or cancelled)
    """
    if kwargs is None:
        kwargs = {}
    if not future.set_running_or_notify_cancel():
        return

    try:
        result = fn(*args, **kwargs)
    except CancelledError:
        # cancelled via the future (while running) => it's all already handled
        pass
    except BaseException:
        e, tb = sys.exc_info()[1:]
        try:
            future.set_exception_info(e, tb)
        except AttributeError:  # Old futures (<v3) only had the non-traceback version
            future.set_exception(e)
    else:
        future.set_result(result)


def inumpyoly2(vert, node, edge=None, ftol=5.0e-14):
    """
    InumpyOLY2: compute "points-in-polygon" queries.

    STAT = InumpyOLY2(VERT, NODE, EDGE) returns the "inside/ou-
    tside" status for a set of vertices VERT and a polygon
    NODE, EDGE embedded in a two-dimensional plane. General
    non-convex and multiply-connected polygonal regions can
    be handled. VERT is an N-by-2 array of XY coordinates to
    be tested. STAT is an associated N-by-1 boolean array,
    with STAT[II] = TRUE if VERT[II, :] is an inside point.

    The polygonal region is defined as a piecewise-straight-
    line-graph, where NODE is an M-by-2 array of polygon ve-
    rtices and EDGE is a P-by-2 array of edge indexing. Each
    row in EDGE represents an edge of the polygon, such that
    NODE[EDGE[KK, 0], :] and NODE[EDGE[KK, 2], :] are the
    coordinates of the endpoints of the KK-TH edge. If the
    argument EDGE is omitted it assumed that the vertices in
    NODE are connected in ascending order.

    STAT, BNDS = InumpyOLY2(..., FTOL) also returns an N-by-1
    boolean array BNDS, with BNDS[II] = TRUE if VERT[II, :]
    lies "on" a boundary segment, where FTOL is a floating-
    point tolerance for boundary comparisons. By default,
    FTOL ~ EPS ^ 0.85.

    --------------------------------------------------------

    This algorithm is based on a "crossing-number" test,
    counting the number of times a line extending from each
    point past the right-most end of the polygon intersects
    with the polygonal boundary. Points with odd counts are
    "inside". A simple implementation requires that each
    edge intersection be checked for each point, leading to
    O(N*M) complexity...

    This implementation seeks to improve these bounds:

  * Sorting the query points by y-value and determining can-
    didate edge intersection sets via binary-search. Given a
    configuration with N test points, M edges and an average
    point-edge "overlap" of H, the overall complexity scales
    like O(M*H + M*LOG(N) + N*LOG(N)), where O(N*LOG(N))
    operations are required for sorting, O(M*LOG(N)) operat-
    ions required for the set of binary-searches, and O(M*H)
    operations required for the intersection tests, where H
    is typically small on average, such that H << N.

  * Carefully checking points against the bounding-box asso-
    ciated with each polygon edge. This minimises the number
    of calls to the (relatively) expensive edge intersection
    test.

    Updated: 19 Dec, 2020

    Authors: Darren Engwirda, Keith Roberts

    Source: https://github.com/dengwirda/inpoly-python

    """

    vert = numpy.asarray(vert, dtype=numpy.float64)
    node = numpy.asarray(node, dtype=numpy.float64)

    STAT = numpy.full(
        vert.shape[0], False, dtype=bool)
    BNDS = numpy.full(
        vert.shape[0], False, dtype=bool)

    if node.size == 0: return STAT, BNDS

    if edge is None:
#----------------------------------- set edges if not passed
        indx = numpy.arange(0, node.shape[0] - 1)

        edge = numpy.zeros((
            node.shape[0], +2), dtype=numpy.int32)

        edge[:-1, 0] = indx + 0
        edge[:-1, 1] = indx + 1
        edge[ -1, 0] = node.shape[0] - 1

    else:
        edge = numpy.asarray(edge, dtype=numpy.int32)

#----------------------------------- prune points using bbox
    used = edge.ravel()
    xmin = numpy.nanmin(node[used, 0])
    xmax = numpy.nanmax(node[used, 0])
    ymin = numpy.nanmin(node[used, 1])
    ymax = numpy.nanmax(node[used, 1])
    xdel = xmax - xmin
    ydel = ymax - ymin

    lbar = (xdel + ydel) / 2.0

    veps = (lbar * ftol)

    mask = numpy.logical_and.reduce((
        vert[:, 0] >= xmin - veps,
        vert[:, 1] >= ymin - veps,
        vert[:, 0] <= xmax + veps,
        vert[:, 1] <= ymax + veps)
    )

    vert = vert[mask]

    if vert.size == 0: return STAT, BNDS

#------------------ flip to ensure y-axis is the `long` axis
    xdel = numpy.amax(vert[:, 0]) - numpy.amin(vert[:, 0])
    ydel = numpy.amax(vert[:, 1]) - numpy.amin(vert[:, 1])

    lbar = (xdel + ydel) / 2.0

    if (xdel > ydel):
        vert = vert[:, (1, 0)]
        node = node[:, (1, 0)]

#----------------------------------- sort points via y-value
    swap = node[edge[:, 1], 1] < node[edge[:, 0], 1]
    temp = edge[swap]
    edge[swap, :] = temp[:, (1, 0)]

#----------------------------------- call crossing-no kernel
    stat, bnds = \
        _inumpyoly(vert, node, edge, ftol, lbar)

#----------------------------------- unumpyack array reindexing
    STAT[mask] = stat
    BNDS[mask] = bnds

    return STAT, BNDS


def _inumpyoly(vert, node, edge, ftol, lbar):
    """
    _InumpyOLY: the local pycode version of the crossing-number
    test. Loop over edges; do a binary-search for the first
    vertex that intersects with the edge y-range; crossing-
    number comparisons; break when the local y-range is met.

    """

    feps = ftol * (lbar ** +1)
    veps = ftol * (lbar ** +1)

    stat = numpy.full(
        vert.shape[0], False, dtype=bool)
    bnds = numpy.full(
        vert.shape[0], False, dtype=bool)

#----------------------------------- compute y-range overlap
    ivec = numpy.argsort(vert[:, 1], kind="quicksort")

    XONE = node[edge[:, 0], 0]
    XTWO = node[edge[:, 1], 0]
    YONE = node[edge[:, 0], 1]
    YTWO = node[edge[:, 1], 1]

    XMIN = numpy.minimum(XONE, XTWO)
    XMAX = numpy.maximum(XONE, XTWO)

    XMIN = XMIN - veps
    XMAX = XMAX + veps
    YMIN = YONE - veps
    YMAX = YTWO + veps

    YDEL = YTWO - YONE
    XDEL = XTWO - XONE

    EDEL = numpy.abs(XDEL) + YDEL

    ione = numpy.searchsorted(
        vert[:, 1], YMIN,  "left", sorter=ivec)
    itwo = numpy.searchsorted(
        vert[:, 1], YMAX, "right", sorter=ivec)

#----------------------------------- loop over polygon edges
    for epos in range(edge.shape[0]):

        xone = XONE[epos]; xtwo = XTWO[epos]
        yone = YONE[epos]; ytwo = YTWO[epos]

        xmin = XMIN[epos]; xmax = XMAX[epos]

        edel = EDEL[epos]

        xdel = XDEL[epos]; ydel = YDEL[epos]

    #------------------------------- calc. edge-intersection
        for jpos in range(ione[epos], itwo[epos]):

            jvrt = ivec[jpos]

            if bnds[jvrt]: continue

            xpos = vert[jvrt, 0]
            ypos = vert[jvrt, 1]

            if xpos >= xmin:
                if xpos <= xmax:
                #------------------- compute crossing number
                    mul1 = ydel * (xpos - xone)
                    mul2 = xdel * (ypos - yone)

                    if feps * edel >= abs(mul2 - mul1):
                #------------------- BNDS -- approx. on edge
                        bnds[jvrt] = True
                        stat[jvrt] = True

                    elif (ypos == yone) and (xpos == xone):
                #------------------- BNDS -- match about ONE
                        bnds[jvrt] = True
                        stat[jvrt] = True

                    elif (ypos == ytwo) and (xpos == xtwo):
                #------------------- BNDS -- match about TWO
                        bnds[jvrt] = True
                        stat[jvrt] = True

                    elif (mul1 <= mul2) and (ypos >= yone) \
                            and (ypos < ytwo):
                #------------------- advance crossing number
                        stat[jvrt] = not stat[jvrt]

            elif (ypos >= yone) and (ypos < ytwo):
            #----------------------- advance crossing number
                stat[jvrt] = not stat[jvrt]

    return stat, bnds
