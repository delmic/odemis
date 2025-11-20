# -*- coding: utf-8 -*-
'''
Created on Aug 17, 2018

@author: Éric Piel

Copyright © 2018-2020 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# If you import this module, it will try to work around some bugs in wxPython
# by "monkey-patching" the module.

import functools
import inspect
import logging

import wx

LOG_WX_CALLS = False  # Set to True to log all wx calls (can be very verbose)

def fix_static_text_clipping(panel):
    # There is a bug in wxPython/GTK3 (up to 4.0.7, at least), which causes
    # the StaticText's not shown at init to be initialized with a size as if
    # the font was standard size. So if the font is big, the text is cropped.
    # See: https://github.com/wxWidgets/Phoenix/issues/1452
    # https://github.com/wxWidgets/wxWidgets/issues/16088 (fixed in v3.1.6+).
    # This following forces resizing of all static text found on the panel and its children
    _force_resize_static_text(panel)
    # Eventually, update the size of the parent, based on everything inside it
    wx.CallLater(500, _update_layout_big_text, panel)  # Quickly
    wx.CallLater(1000, _update_layout_big_text, panel)  # Later, in case the first time was too early

def _update_layout_big_text(panel):
    _force_resize_static_text(panel)
    panel.Layout()

def _force_resize_static_text(root):
    # Force re-calculate the size of all StaticTexts contained in the object
    for c in root.GetChildren():
        if isinstance(c, wx.StaticText):
            logging.debug("Fixing size of the text %s", c.Label)
            c.InvalidateBestSize()
        elif isinstance(c, wx.Window):
            _force_resize_static_text(c)

if "gtk3" in wx.version():

    # Fix StaticText on GTK3:
    # There is a bug in wxPython/GTK3 (up to 4.0.7, at least), which causes
    # the StaticText's which are not shown to be set as size 1,1 when changing
    # the text. The size is not updated when it's shown.
    # See: https://github.com/wxWidgets/Phoenix/issues/1452
    # https://trac.wxwidgets.org/ticket/16088
    # => Force size update when showing
    wx.StaticText._Show_orig = wx.StaticText.Show

    def ShowFixed(self, show=True):
        wx.StaticText._Show_orig(self, show)
        if show:
            # Force the static text to update (hopefully, there is no wrapping)
            self.Wrap(-1)  # -1 = Disable wrapping

    wx.StaticText.Show = ShowFixed


def _wrap_callable(orig, name, logger):
    @functools.wraps(orig)
    def _wrapped(*args, **kwargs):
        try:
            logger.debug("CALL %s args=%s kwargs=%s", name,
                         tuple(repr(a) for a in args),
                         {k: repr(v) for k, v in kwargs.items()})
        except Exception:
            logger.debug("CALL %s (args not serializable)", name)
        try:
            result = orig(*args, **kwargs)
            try:
                logger.debug("RETURN %s -> %s", name, repr(result))
            except Exception:
                logger.debug("RETURN %s -> (unrepresentable)", name)
            return result
        except Exception:
            logger.exception("EXCEPTION in %s", name)
            raise
    return _wrapped

def instrument_class(cls, logger=None):
    """Monkey-patch all callable attributes on a class to log their calls."""
    logger = logger or logging.getLogger("wx_calls")
    for name in dir(cls):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(cls, name)
        except Exception:
            continue
        if callable(attr):
            if inspect.ismethoddescriptor(attr) or inspect.isbuiltin(attr):
                logging.debug("Skipping wrapping of method descriptor %s.%s", cls.__name__, name)
                continue

            try:
                wrapped = _wrap_callable(attr, f"{cls.__name__}.{name}", logger)
                setattr(cls, name, wrapped)
                logging.debug("Wrapped %s.%s", cls.__name__, name)
            except Exception:
                # Some attributes on extension types may be read-only; skip them.
                logger.debug("Could not wrap %s.%s", cls.__name__, name)
                continue

def instrument_module(module, logger=None):
    """Wrap top-level callables defined on a module object."""
    logger = logger or logging.getLogger("wx_calls")
    for name in dir(module):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(module, name)
        except Exception:
            continue
        if inspect.isfunction(attr) or inspect.ismethod(attr):# or callable(attr):
            try:
                wrapped = _wrap_callable(attr, f"{module.__name__}.{name}", logger)
                setattr(module, name, wrapped)
                logging.debug("Wrapped %s.%s", module.__name__, name)
            except Exception:
                logger.debug("Could not wrap %s.%s", module.__name__, name)
                continue
        elif inspect.isclass(attr):
            try:
                instrument_class(attr, logger)
            except Exception:
                logger.debug("Could not instrument class %s.%s", module.__name__, name)
                continue

if LOG_WX_CALLS:
    instrument_module(wx)
