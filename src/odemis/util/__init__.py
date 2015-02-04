# -*- coding: utf-8 -*-
'''
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
'''
# Various helper functions that have a generic usefulness
# Warning: do not put anything that has dependencies on non default python modules

from __future__ import division

import collections
from decorator import decorator
import errno
from functools import wraps
import inspect
import logging
import math
from odemis import model
import os
import signal
import threading
import time


def find_closest(val, l):
    """
    finds in a list the closest existing value from a given value
    """
    return min(l, key=lambda x:abs(x - val))

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
    rtol (float): relative tolerance
    returns (bool): True if a and b are almost equal
    """
    if a == b:
        return True

    tol = max(atol, max(abs(a), abs(b)) * rtol)
    if abs(a - b) <= tol:
        return True

    return False

def rec_update(d, other):
    """
    Recursively update a dictionary with another one
    d (dict): dictionary to update
    other (dict): dictionary containing keys to add/overwrite
    """
    for k, v in other.iteritems():
        if isinstance(v, collections.Mapping):
            r = rec_update(d.get(k, {}), v)
            d[k] = r
        else:
            d[k] = other[k]
    return d

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


class TimeoutError(Exception):
    pass


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

    def limit(f, self, *args, **kwargs):
        if inspect.isclass(self):
            raise ValueError("limit_invocation decorators should only be "
                             "assigned to instance methods!")

        now = time.time()

        # The next statement was not useful in the sense that we cannot
        # add attributes to bound methods.
        # Get the bound version of the function
        #bf = f.__get__(self)

        # Hacky way to store value per instance and per methods
        last_call_name = '%s_lim_inv_last_call' % f.__name__
        timer_name = '%s_lim_inv_timer' % f.__name__

        # If the function was called later than 'delay_s' seconds ago...
        if (hasattr(self, last_call_name) and
            now - getattr(self, last_call_name) < delay_s):
            #logging.debug('Delaying method call')
            if now < getattr(self, last_call_name):
                # this means a timer is already set, nothing else to do
                return

            timer = threading.Timer(delay_s,
                          f,
                          args=[self] + list(args),
                          kwargs=kwargs)
            setattr(self, timer_name, timer)
            setattr(self, last_call_name, now + delay_s)
            timer.start()
        else:
            #execute method call now
            setattr(self, last_call_name, now)
            return f(self, *args, **kwargs)

    return decorator(limit)



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
        self.callback = model.WeakMethod(callback)
        self.period = period
        self.daemon = True
        self._must_stop = threading.Event()

    def run(self):
        # use the timeout as a timer
        try:
            while not self._must_stop.wait(self.period):
                try:
                    self.callback()
                except model.WeakRefLostError:
                    # it's gone, it's over
                    return
        except Exception:
            logging.exception("Failure while calling a repeating timer")
        finally:
            logging.debug("Repeating timer thread '%s' over", self.name)

    def cancel(self):
        self._must_stop.set()
