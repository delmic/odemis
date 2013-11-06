#-*- coding: utf-8 -*-
"""
Created on 21 Aug 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""


import functools
import logging
import time
import inspect
import sys
import os.path
import subprocess

from threading import Timer
from itertools import izip

import wx
from decorator import decorator


#### Decorators ########

# TODO: rename to something more clear, like "call_in_wx_main"
@decorator
def call_after(f, self, *args, **kwargs):
    """ This method decorator makes sure the method is called from the main
    (GUI) thread.
    """
    return wx.CallAfter(f, self, *args, **kwargs)

    # The dead_object_wrapper was added to prevent PyDeadObjectError when
    # delayed calls were made on a deleted dialog (i.e. the acquisition dialog)
    # return wx.CallAfter(dead_object_wrapper(f, self, *args, **kwargs),
    #                     self,
    #                     *args,
    #                     **kwargs)

def limit_invocation(delay_s):
    """ This decorator limits how often a method will be executed.

    The first call will always immediately be executed. The last call will be
    delayed 'delay_s' seconds at the most. In between the first and last calls,
    the method will be executed at 'delay_s' intervals. In other words, it's
    a rate limiter.

    :param delay_s: (float) The minimum interval between executions in seconds.

    Note that the method might be called in a separate thread. In wxPython, you
    might need to decorate it by @call_after to ensure it is called in the GUI
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

            timer = Timer(delay_s,
                          dead_object_wrapper(f, self, *args, **kwargs),
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

@decorator
def ignore_dead(f, self, *args, **kwargs):
    try:
        return f(self, *args, **kwargs)
    except wx.PyDeadObjectError:
        logging.warn("Dead object ignored in %s", f.__name__)

class memoize(object):
    """ Decorator that caches a function's return value each time it is called.
    If called later with the same arguments, the cached value is returned, and
    not re-evaluated.
    """

    def __init__(self, func):
        self.func = func
        self.cache = {}

    def __call__(self, *args):
        try:
            if len(self.cache) > 1000:
                self._flush()
            return self.cache[args]
        except KeyError:
            value = self.func(*args)
            self.cache[args] = value
            return value
        except TypeError:
            # uncachable -- for instance, passing a list as an argument.
            # Better to not cache than to blow up entirely.
            return self.func(*args)

    def __repr__(self):
        """Return the function's docstring."""
        return self.func.__doc__

    def __get__(self, obj, objtype):
        """Support instance methods."""
        fn = functools.partial(self.__call__, obj)
        fn.flush = self._flush
        return fn

    def _flush(self):
        self.cache = {}


#### Wrappers ########

def call_after_wrapper(f, *args, **kwargs):
    def call_after_wrapzor(*args, **kwargs):
        app = wx.GetApp()
        if app:
            return wx.CallAfter(f, *args, **kwargs)
    return call_after_wrapzor

def dead_object_wrapper(f, *args, **kwargs):
    """ This simple wrapper suppresses errors caused code trying to access
    wxPython widgets that have already been destroyed
    """
    def dear_object_wrapzor(*args, **kwargs):
        try:
            app = wx.GetApp()
            if app:
                return f(*args, **kwargs)
        except wx.PyDeadObjectError:
            logging.warn("Dead object ignored in %s", f.__name__)
    return dear_object_wrapzor


# Path functions

def get_home_folder():
    """ Return the home directory of the user running the Odemis GUI
    """
    # fall-back to HOME
    folder = os.path.expanduser("~")
    if os.path.isdir(folder):
        return folder

    # last resort: current working directory should always be existing
    return os.getcwd()


def get_picture_folder():
    """
    return (string): a full path to the "Picture" user folder.
    It tries to always return an existing folder.
    """
    if sys.platform.startswith('linux'):
        # First try to find the XDG picture folder
        folder = None
        try:
            folder = subprocess.check_output(["xdg-user-dir", "PICTURES"])
            folder = folder.strip()
        except subprocess.CalledProcessError:
            # XDG not supported
            pass
        if os.path.isdir(folder):
            return folder
        # drop to default
    elif sys.platform.startswith('win32'):
        # TODO Windows code
        pass
        # drop to default
    else:
        logging.warning("Platform not supported for picture folder")


    # fall-back to HOME
    folder = os.path.expanduser("~")
    if os.path.isdir(folder):
        return folder

    # last resort: current working directory should always be existing
    return os.getcwd()


def formats_to_wildcards(formats2ext, include_all=False, include_any=False):
    """Convert formats into wildcards string compatible with wx.FileDialog()

    formats2ext (dict (string -> list of strings)): format names and lists of
        their possible extensions.
    include_all (boolean): If True, also include as first wildcards for all the formats
    include_any (boolean): If True, also include as last the *.* wildcards

    returns (tuple (string, list of strings)): wildcards, name of the format
        in the same order as in the wildcards (or None if all/any format)
    """
    formats = []
    wildcards = []
    for fmt, extensions in formats2ext.items():
        ext_wildcards = ";".join(["*" + e for e in extensions])
        wildcard = "%s files (%s)|%s" % (fmt, ext_wildcards, ext_wildcards)
        formats.append(fmt)
        wildcards.append(wildcard)

    if include_all:
        fmt_wildcards = []
        for extensions in formats2ext.values():
            fmt_wildcards.append(";".join(["*" + e for e in extensions]))
        ext_wildcards = ";".join(fmt_wildcards)
        wildcard = "All supported files (%s)|%s" % (ext_wildcards, ext_wildcards)
        wildcards.insert(0, wildcard)
        formats.insert(0, None)

    if include_any:
        wildcards.append("Any file (*.*)|*.*")
        formats.append(None)

    # the whole importance is that they are in the same order
    return "|".join(wildcards), formats

# Data container

class DictObj(dict):
    """ Dict like object that allows the values to be accessed like attributes
    """
    def __init__(self, **kw):
        dict.__init__(self, kw)
        self.__dict__.update(kw)

def tuple_add(t1, t2):
    """ Add t1 to t2 """
    return tuple(x + y for x, y in izip(t1, t2))

def tuple_subtract(t1, t2):
    """ subtract t1 from t2 """
    return tuple(x - y for x, y in izip(t1, t2))

def tuple_multiply(t, m):
    """ Multiply the elements of t with the value m """
    return tuple(v * m for v in t)

def tuple_fdiv(t, f):
    """ Divide tuple elements by float value f """
    if f:
        f = float(f)
        return tuple(v / f for v in t)

def tuple_idiv(t, i):
    """ Divide tuple elements by integer value i """
    if i:
        return tuple(v // i for v in t)

def tuple_tdiv(t1, t2):
    """ Divide t1 elements by the corresponding elements in t2 """
    return tuple(x / y for x, y in izip(t1, t2))
