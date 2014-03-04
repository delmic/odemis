# -*- coding: utf-8 -*-

"""

@author: Rinze de Laat

Copyright © 2014Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Content:

    This module contains controls for file selection.

"""

import os

import wx

import odemis.gui
from .buttons import ImageTextButton
from odemis.gui.img import data
from odemis.gui.util import get_picture_folder

class FileBrowser(wx.Panel):

    def __init__(self, parent, id=-1,
                  pos=wx.DefaultPosition,
                  size=wx.DefaultSize,
                  style=wx.TAB_TRAVERSAL,
                  tool_tip=None,
                  dialog_title="Browse for file",
                  wildcard="*.*",
                  name='fileBrowser',
        ):

        self.file_path = None

        self.dialog_title = dialog_title
        self.wildcard = wildcard

        self.text_ctrl = None
        self.btn_ctrl = None

        self.create_dialog(parent, id, pos, size, style, name)

    def create_dialog(self, parent, id, pos, size, style, name):
        """Setup the graphic representation of the dialog"""
        wx.Panel.__init__ (self, parent, id, pos, size, style, name)
        self.SetBackgroundColour(parent.GetBackgroundColour())

        box = wx.BoxSizer(wx.HORIZONTAL)

        self.text_ctrl = wx.TextCtrl(self,
                            style=wx.BORDER_NONE|wx.TE_READONLY)
        self.text_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
        self.text_ctrl.SetBackgroundColour(odemis.gui.BACKGROUND_COLOUR)
        self.text_ctrl.Bind(wx.EVT_TEXT, self.on_changed)

        box.Add(self.text_ctrl, 1)

        self.btn_ctrl = ImageTextButton(self, -1, data.getbtn_64x16Bitmap(),
                                        label_delta=1)
        self.btn_ctrl.SetBitmaps(data.getbtn_64x16_hBitmap(),
                                 data.getbtn_64x16_aBitmap())
        self.btn_ctrl.SetForegroundColour("#000000")
        self.btn_ctrl.SetLabel("Browse")
        self.btn_ctrl.Bind(wx.EVT_BUTTON, self._on_browse)

        box.Add(self.btn_ctrl, 0, wx.LEFT, 10)

        self.SetAutoLayout(True)
        self.SetSizer(box)
        self.Layout()
        if isinstance(size, tuple):
            size = wx.Size(size)
        self.SetDimensions(-1, -1, size.width, size.height, wx.SIZE_USE_EXISTING)

    def on_changed(self, evt):
        evt.SetEventObject(self)
        evt.Skip()

    def SetValue(self, file_path):
        if file_path:
            self.file_path = file_path
            if not os.path.exists(file_path):
                self.text_ctrl.SetForegroundColour(odemis.gui.ALERT_COLOUR)
            self.text_ctrl.SetValue(os.path.basename(file_path))
            self.text_ctrl.SetToolTipString(file_path)
            self.text_ctrl.SetInsertionPointEnd()
        else:
            self.file_path = None

    def GetValue(self):
        return self.file_path

    @property
    def basename(self):
        """
        the base name of the file
        """
        return os.path.basename(self.file_path or "")

    @property
    def path(self):
        """
        the name of the directory containing the file
        """
        return os.path.dirname(self.file_path or "")

    def SetWildcard(self, wildcard):
        self.wildcard = wildcard

    def _on_browse(self, evt):
        current = self.GetValue() or ""
        directory = os.path.split(current)

        if os.path.isdir( current):
            directory = current
            current = ""
        elif directory and os.path.isdir( directory[0] ):
            current = directory[1]
            directory = directory [0]
        else:
            directory = get_picture_folder()
            current = ""

        dlg = wx.FileDialog(self, self.dialog_title, directory, current,
                            wildcard=self.wildcard,
                            style=wx.FD_OPEN)


        if dlg.ShowModal() == wx.ID_OK:
            self.SetValue(dlg.GetPath())
        dlg.Destroy()
