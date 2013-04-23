#-*- coding: utf-8 -*-

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


#### Wrappers ########

def call_after_wrapper(f, *args, **kwargs):
    def wrapzor(*args, **kwargs):
        return wx.CallAfter(f, *args, **kwargs)
    return wrapzor

def dead_object_wrapper(f, *args, **kwargs):
    """ This simple wrapper suppresses errors caused code trying to access
    wxPython widgets that have already been destroyed
    """
    def wrapzor(*args, **kwargs):
        try:
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

# Data container

class DictObj(dict):
    """ Dict like object that allows the values to be accessed like attributes
    """
    def __init__(self, **kw):
        dict.__init__(self, kw)
        self.__dict__.update(kw)
