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

from __future__ import division, absolute_import

import Queue
import collections
from concurrent.futures import CancelledError
from decorator import decorator
from functools import wraps
import inspect
import logging
import math
import signal
import sys
import threading
import time
import weakref

from . import weak


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
    if a == b:
        return True

    tol = max(atol, max(abs(a), abs(b)) * rtol)
    if abs(a - b) <= tol:
        return True

    return False


def recursive_dict_update(d, other):
    """ Recursively update the values of the first dictionary with the values of the second

    Args:
        d (dict): dictionary to update
        other (dict): dictionary containing keys to add/overwrite

    Returns:
        (dict) The updated dictionary

    """

    for k, v in other.iteritems():
        if isinstance(v, collections.Mapping):
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
    """ Return the perpendicular distance between the line described by start and end, and point """

    x1, y1 = start
    x2, y2 = end
    x3, y3 = point

    px = x2 - x1
    py = y2 - y1

    v = px * px + py * py
    u = ((x3 - x1) * px + (y3 - y1) * py) / float(v)

    u = min(max(u, 0), 1)

    x = x1 + u * px
    y = y1 + u * py

    dx = x - x3
    dy = y - y3

    return math.sqrt(dx*dx + dy*dy)

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
    """ Ensure that the given rectangle actually is defined by top, left, bottom, right

    rect (iterable of 4 floats): x1, y1, x2, y2
    return (iterable of 4 floats): left, top, right, bottom

    """

    l, t, r, b = rect
    if l > r:
        l, r = r, l
    if t > b:
        t, b = b, t

    # Re-create the result using the same type as the `rect` parameter
    return type(rect)((l, t, r, b))


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
                except Queue.Empty:
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
        logging.warn("Warning! Long delay interval. Please consider using "
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
                        q = Queue.Queue()
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
            while not self._must_stop.wait(self.period):
                try:
                    self.callback()
                except weak.WeakRefLostError:
                    # it's gone, it's over
                    return
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
