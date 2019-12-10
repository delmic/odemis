# -*- coding: utf-8 -*-
'''
Created on Aug 17, 2018

@author: piel

Copyright © 2018 piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

# If you import this module, it will ensure that wxPython follows the v4 API.
# Note that it only converts the v4 -> v3 API that Odemis needs, nothing more.

from past.builtins import basestring
import wx
import sys
import io

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

if wx.MAJOR_VERSION <= 3:
    # ComboBox is now in .adv
    import wx.combo
    wx.adv = wx.combo
    wx.adv.AboutDialogInfo = wx.AboutDialogInfo
    wx.adv.AboutBox = wx.AboutBox
    sys.modules["wx.adv"] = wx.adv

    import wx.xrc
    wx.xrc.XmlResource = wx.xrc.EmptyXmlResource

    # wx.Image
    wx._Image_orig = wx.Image
    wx.Image._SetRGB_orig = wx.Image.SetRGB

    class ImageClever(wx.Image):

        def __new__(cls, *args, **kwargs):
            if isinstance(args[0], (file, io.IOBase)):
                return wx.ImageFromStream(*args)
            elif len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], int):
                if "data" in kwargs and "alpha" in kwargs:
                    return wx.ImageFromDataWithAlpha(*args, **kwargs)
                else:
                    return wx.EmptyImage(*args, **kwargs)
            return super(ImageClever, cls).__new__(cls, *args, **kwargs)

    def SetRGBClever(self, *args, **kwargs):
        if isinstance(args[0], wx.Rect):
            return self.SetRGBRect(*args, **kwargs)
        return wx._Image_orig.SetRGB(self, *args, **kwargs)

    wx.Image.SetRGB = SetRGBClever

    class BitmapClever(wx.Bitmap):

        def __new__(cls, *args, **kwargs):
            if isinstance(args[0], wx._core.Image):
                return wx.BitmapFromImage(*args, **kwargs)
            elif len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], int):
                return wx.EmptyBitmap(*args, **kwargs)
            return super(BitmapClever, cls).__new__(cls, *args, **kwargs)

    class IconClever(wx.Icon):

        def __new__(cls, *args, **kwargs):
            if isinstance(args[0], wx._core.Bitmap):
                return wx.IconFromBitmap(*args, **kwargs)
            return super(IconClever, cls).__new__(cls, *args, **kwargs)

    class CursorClever(wx.Cursor):

        def __new__(cls, *args, **kwargs):
            if isinstance(args[0], int):
                return wx.StockCursor(*args, **kwargs)
            return super(CursorClever, cls).__new__(cls, *args, **kwargs)

    wx.Image = ImageClever
    wx.Bitmap = BitmapClever
    wx.Icon = IconClever
    wx.Cursor = CursorClever

    # wx.Window
    wx.Window._SetToolTip_orig = wx.Window.SetToolTip
    wx.Window._SetSize_orig = wx.Window.SetSize
    wx.Window._ClientToScreen_orig = wx.Window.ClientToScreen

    def SetToolTipClever(self, *args):
        if isinstance(args[0], basestring):
            return self.SetToolTipString(*args)
        return wx.Window._SetToolTip_orig(self, *args)

    def SetSizeClever(self, *args):
        if len(args) >= 4:
            return self.SetDimensions(*args)
        return wx.Window._SetSize_orig(self, *args)

    def ClientToScreenClever(self, *args):
        if len(args) == 2:
            return self.ClientToScreenXY(*args)
        return wx.Window._ClientToScreen_orig(self, *args)

    wx.Window.ClientToScreen = ClientToScreenClever
    wx.Window.SetToolTip = SetToolTipClever
    wx.Window.SetSize = SetSizeClever

    # wx.Menu
    wx.Menu._Append_orig = wx.Menu.Append
    wx.Menu._Remove_orig = wx.Menu.Remove

    def AppendClever(self, *args, **kwargs):
        if isinstance(args[0], wx.MenuItem):
            return self.AppendItem(*args, **kwargs)
        return wx.Menu._Append_orig(self, *args, **kwargs)

    def RemoveClever(self, *args, **kwargs):
        if isinstance(args[0], wx.MenuItem):
            return self.RemoveItem(*args, **kwargs)
        return wx.Menu._Remove_orig(self, *args, **kwargs)

    wx.Menu.Append = AppendClever
    wx.Menu.Remove = RemoveClever

    wx.DC._DrawRectangle_orig = wx.DC.DrawRectangle

    def DrawRectangleClever(self, *args, **kwargs):
        if isinstance(args[0], wx.Rect):
            return self.DrawRectangleRect(*args, **kwargs)
        return wx.DC._DrawRectangle_orig(self, *args, **kwargs)

    wx.DC.DrawRectangle = DrawRectangleClever

    wx.ListCtrl._InsertItem_orig = wx.ListCtrl.InsertItem
    wx.ListCtrl._SetItem_orig = wx.ListCtrl.SetItem

    def InsertItemClever(self, *args, **kwargs):
        if len(args) >= 2:
            return self.InsertImageStringItem(*args, **kwargs)
        return wx.ListCtrl._InsertItem_orig(self, *args, **kwargs)

    def SetItemClever(self, *args, **kwargs):
        if len(args) >= 3:
            return self.SetStringItem(*args, **kwargs)
        return wx.ListCtrl._SetItem_orig(self, *args, **kwargs)

    wx.ListCtrl.InsertItem = InsertItemClever
    wx.ListCtrl.SetItem = SetItemClever
