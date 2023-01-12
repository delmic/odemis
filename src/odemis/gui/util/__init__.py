# -*- coding: utf-8 -*-
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

import queue
from decorator import decorator
from functools import wraps
import inspect
import logging
from odemis import util
import os.path
import subprocess
import sys
import threading
import time
import weakref
import wx

# Decorators & Wrappers
# They are almost the same but the decorators assume that they are decorating
# a method of a wx.Object (ie, it has a "self" argument)
from odemis.acq.move import getCurrentPositionLabel

@decorator
def call_in_wx_main(f, self, *args, **kwargs):
    """ This method decorator makes sure the method is called from the main
    (GUI) thread.
    The function will run asynchronously, so the function return value cannot
    be returned. So it's typically an error if a decorated function returns
    something useful.
    """
    # We could try to be clever, and only run asynchronously if it's not called
    # from the main thread, but that can cause anachronic issues. For example:
    # 1. Call from another thread -> queued for later
    # 2. Call from main thread -> immediately executed
    # => Call 2 is executed before call 1, which could mean that an old value
    # is displayed on the GUI.
    # TODO: with Python 3, update that line to:
    # if threading.current_thread() == threading.main_thread()
#     if isinstance(threading.current_thread(), threading._MainThread):
#         f(self, *args, **kwargs)
#         return

    wx.CallAfter(f, self, *args, **kwargs)


def call_in_wx_main_wrapper(f):

    @wraps(f)
    def call_after_wrapzor(*args, **kwargs):
        try:
            wx.CallAfter(f, *args, **kwargs)
        except AssertionError:
            if not wx.GetApp():
                logging.info("Skipping call to %s() as wxApp is already ended", f.__name__)
            else:
                raise

    return call_after_wrapzor


@decorator
def ignore_dead(f, self, *args, **kwargs):
    """
    Suppresses errors caused by code trying to access wxPython widgets that have
    already been destroyed.

    If used on a function also decorated with call_in_wx_main, it should be
    the closest to the real function. IOW, always put this decorator at the
    bottom of the decorators.
    """
    if not self:
        logging.info("Skipping call to %s() as object is already dead", f.__name__)
        return

    try:
        return f(self, *args, **kwargs)
    except RuntimeError:
        logging.warning("Dead object %s ignored in %s", self, f.__name__)


def dead_object_wrapper(f):

    @wraps(f)
    def dead_object_wrapzor(*args, **kwargs):
        if not wx.GetApp():
            logging.info("Skipping call to %s() as wxApp is already ended", f.__name__)
            return

        try:
            return f(*args, **kwargs)
        except RuntimeError:
            logging.warning("Dead object ignored in %s", f.__name__)

    return dead_object_wrapzor


def wxlimit_invocation(delay_s):
    """ This decorator limits how often a method will be executed.

    Same as util.limit_invocation, but also avoid problems with wxPython dead
    objects that can happen due to delaying a calling a method, and ensure it
    runs in the main GUI thread.

    The first call will always immediately be executed. The last call will be
    delayed 'delay_s' seconds at the most. In between the first and last calls,
    the method will be executed at 'delay_s' intervals. In other words, it's
    a rate limiter.

    :param delay_s: (float) The minimum interval between executions in seconds.

    Note that the method is _always_ called within the main GUI thread, and
    with dead object protection, so there is no need to also decorate it with
    @call_in_wx_main or @ignore_dead.
    """
    liwrapper = util.limit_invocation(delay_s)

    def wxwrapper(f):
        # The order matters: dead protection must happen _after_ the call has
        # been delayed
        wf = dead_object_wrapper(f)
        wf = call_in_wx_main_wrapper(wf)
        return liwrapper(wf)
    return wxwrapper


if sys.platform.startswith('linux'):
    # On Ubuntu (ie, with wxPython/GTK), it's actually not needed to capture the
    # mouse if the button is down, as it is followed properly. In addition on
    # Linux the capture is very "strong" and captures it for the screen, it's
    # nice to avoid doing it if possible.
    # On the contrary, on Windows it's necessary to capture it in order to know
    # its movement (and button up) outside of the widget.

    def capture_mouse_on_drag(obj):
        return

    def release_mouse_on_drag(obj):
        return

else:

    def capture_mouse_on_drag(obj):
        """
        Safe(r) mouse capture, for keeping track of dragging movement.
        """
        if obj.HasCapture():
            logging.warning("Mouse was already captured, so not capturing it again")
            return

        obj.CaptureMouse()

    def release_mouse_on_drag(obj):
        """
        Releases the mouse when acquired via capture_mouse_on_drag()
        """
        if not obj.HasCapture():
            logging.info("Mouse was not captured, so not releasing it")
            return

        obj.ReleaseMouse()

# Path functions

def get_home_folder():
    """ Return the home directory of the user running the Odemis GUI
    """
    # fall-back to HOME
    if sys.platform.startswith('linux'):
        folder = os.path.expanduser(u"~")
    elif sys.platform.startswith('win32'):
        # expanduser(u) fails with non-ASCII usernames in Python2,
        # see https://bugs.python.org/issue13207
        # Import functions here because wintypes in ctypes library cannot be opened in linux
        from odemis.gui.util.winknownpaths import get_path, FOLDERID
        folder = get_path(FOLDERID.Profile)
    if os.path.isdir(folder):
        return folder

    # last resort: current working directory should always be existing
    return os.getcwd()


def get_picture_folder():
    """
    return (unicode): a full path to the "Picture" user folder.
    It tries to always return an existing folder.
    """
    if sys.platform.startswith('linux'):
        # First try to find the XDG picture folder
        folder = None
        try:
            folder = subprocess.check_output(["xdg-user-dir", "PICTURES"])
            folder = folder.strip().decode(sys.getfilesystemencoding())
        except subprocess.CalledProcessError:
            # XDG not supported
            pass
        if os.path.isdir(folder):
            return folder
        # drop to default
    elif sys.platform.startswith('win32'):
        # expanduser(u) fails with non-ASCII usernames in Python2,
        # see https://bugs.python.org/issue13207
        # Import functions here because wintypes in ctypes library cannot be opened in linux
        from odemis.gui.util.winknownpaths import get_path, FOLDERID
        try:
            folder = get_path(FOLDERID.Pictures)
            return folder
        except:
            logging.warning("Cannot find picture folder")
    else:
        logging.warning("Platform not supported for picture folder")

    # fall-back to HOME
    folder = os.path.expanduser(u"~")
    if os.path.isdir(folder):
        return folder

    # last resort: current working directory should always be existing
    return os.getcwd()


def formats_to_wildcards(formats2ext, include_all=False, include_any=False, suffix=" files"):
    """Convert formats into wildcards string compatible with wx.FileDialog()

    formats2ext (dict (unicodes -> list of unicodes)): format names and lists of
        their possible extensions.
    include_all (boolean): If True, also include as first wildcards for all the formats
    include_any (boolean): If True, also include as last the *.* wildcards

    returns (tuple (unicode, list of unicodes)): wildcards, name of the format
        in the same order as in the wildcards (or None if all/any format)
    """
    formats = []
    wildcards = []
    for fmt, extensions in formats2ext.items():
        ext_wildcards = u";".join([u"*" + e for e in extensions])
        wildcard = u"%s%s (%s)|%s" % (fmt, suffix, ext_wildcards, ext_wildcards)
        formats.append(fmt)
        wildcards.append(wildcard)

    if include_all:
        fmt_wildcards = []
        for extensions in formats2ext.values():
            fmt_wildcards.append(u";".join([u"*" + e for e in extensions]))
        ext_wildcards = u";".join(fmt_wildcards)
        wildcard = u"All supported files (%s)|%s" % (ext_wildcards, ext_wildcards)
        wildcards.insert(0, wildcard)
        formats.insert(0, None)

    if include_any:
        wildcards.append(u"Any file (*.*)|*.*")
        formats.append(None)

    # the whole importance is that they are in the same order
    return u"|".join(wildcards), formats


# Data container

class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self


@call_in_wx_main
def enable_tab_on_stage_position(button, stage, pos, target, aligner=None, tooltip=None):
    """
    Enable the given tab button if the stage is in target position, disable it otherwise
    :param button: (Button) the Tab button to enable/disable
    :param pos: (dict str->float) current position to check its label
    :param target: (list) target position labels for which the tab button is enabled [IMAGING, FM_IMAGING]
    :param aligner: (Actuator) the align component
    :param tooltip: (str or None) Tooltip message to show when disabled
    """
    within_target = getCurrentPositionLabel(pos, stage, aligner) in target
    button.Enable(within_target)

    if tooltip is not None:
        if not within_target:
            button.SetToolTip(tooltip)
        else:
            button.SetToolTip(None)
