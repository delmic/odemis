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
import logging

# If you import this module, it will try to work around some bugs in wxPython
# by "monkey-patching" the module.

import wx

def fix_static_text_clipping(panel):
    # There is a bug in wxPython/GTK3 (up to 4.0.7, at least), which causes
    # the StaticText's not shown at init to be initialized with a size as if
    # the font was standard size. So if the font is big, the text is cropped.
    # See: https://github.com/wxWidgets/Phoenix/issues/1452
    # https://trac.wxwidgets.org/ticket/16088
    # This following forces resizing of all static text found on the panel and its children
    _force_resize_static_text(panel)
    # Eventually, update the size of the parent, based on everything inside it
    wx.CallLater(100, _update_layout_big_text, panel)  # Quickly
    wx.CallLater(500, _update_layout_big_text, panel)  # Later, in case the first time was too early

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
