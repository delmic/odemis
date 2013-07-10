#-*- coding: utf-8 -*-

import functools
import logging
import time
import inspect
import sys
import os.path
import subprocess

from threading import Timer

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

def limit_invocation(delay_s):
    """ This decorator limits how often a method will be executed.

    The first call will always immediately be executed. The last call will be
    delayed 'delay_s' seconds at the most. In between the first and last calls,
    the method will be executed at 'delay_s' intervals.

    :param delay_s: (float) The minimum interval between executions in seconds.

    Note that the method might be called in a separate thread. In wxPython, you
    might need to decorate it by @call_after to ensure it is called in the GUI
    thread.
    """
    def limit(f, self, *args, **kwargs):

        if inspect.isclass(self):
            raise ValueError("limit_invocation decorators should only be "
                             "assigned to instance methods!")

        if delay_s > 5:
            logging.warn("Warning! Long delay interval. Please consider using "
                         "and interval of 5 or less seconds")
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


class memoize(object):
    """Decorator that caches a function's return value each time it is called.
    If called later with the same arguments, the cached value is returned, and
    not re-evaluated.
    """

    def __init__(self, func):
        self.func = func
        self.cache = {}

    def __call__(self, *args):
        try:
            if len(self.cache) > 1000:
                self._reset()
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
        fn.reset = self._reset
        return fn

    def _reset(self):
        self.cache = {}


#### Wrappers ########

def call_after_wrapper(f, *args, **kwargs):
    def wrapzor(*args, **kwargs):
        app = wx.GetApp()
        if app:
            return wx.CallAfter(f, *args, **kwargs)
    return wrapzor

def dead_object_wrapper(f, *args, **kwargs):
    """ This simple wrapper suppresses errors caused code trying to access
    wxPython widgets that have already been destroyed
    """
    def wrapzor(*args, **kwargs):
        try:
            app = wx.GetApp()
            if app:
                return f(*args, **kwargs)
        except wx.PyDeadObjectError:
            logging.debug("PyDeadObjectError avoided")
    return wrapzor


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
