# -*- coding: utf-8 -*-

"""

@author: Rinze de Laat

Copyright © 2014, 2018 Rinze de Laat, Éric Piel, Delmic

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
import fnmatch
import logging
from odemis.gui import img
import odemis.gui
from odemis.util.dataio import splitext
import os
import wx
import wx.lib.newevent

from .buttons import ImageTextButton, ImageButton

FileSelectEvent, EVT_FILE_SELECT = wx.lib.newevent.NewEvent()


class FileBrowser(wx.Panel):
    """ Widget that displays a file name and allows to change it by selecting a different file.

    It will generate a EVT_FILE_SELECT when the file changes.
    Note that like most of the wx widgets, SetValue does not generate an event.
    """

    def __init__(self, parent, id=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=0,
                 dialog_style=wx.FD_OPEN,
                 clear_btn=False,
                 clear_label="",
                 dialog_title="Browse for file",
                 wildcard=None,
                 name='fileBrowser',
                 file_path=None,
                 default_dir=None,
        ):
        """
        wildcard (None or str): the list of wildcard to pass to file dialog.
          If it's None, it will use the default, which is to show all files (*.*).
        """

        style |= wx.TAB_TRAVERSAL

        self.file_path = file_path
        self.default_dir = os.path.abspath(default_dir or os.path.dirname(file_path) or
                                           os.path.curdir)
        if not os.path.dirname(self.file_path):
            self.file_path = os.path.join(self.default_dir, self.file_path)

        self.dialog_title = dialog_title
        self.dialog_style = dialog_style
        if wildcard is None:
            self.wildcard = "Any file (*.*)|*.*"
        else:
            self.wildcard = wildcard
        self.label = clear_label  # Text to show when the control is cleared

        self.text_ctrl = None
        self.btn_ctrl = None
        self._btn_clear = None

        self.create_dialog(parent, id, pos, size, style, name, clear_btn)

    def create_dialog(self, parent, id, pos, size, style, name, clear_btn):
        """ Setup the graphic representation of the dialog """

        wx.Panel.__init__(self, parent, id, pos, size, style, name)
        self.SetBackgroundColour(parent.GetBackgroundColour())

        box = wx.BoxSizer(wx.HORIZONTAL)

        self.text_ctrl = wx.TextCtrl(self, style=wx.BORDER_NONE | wx.TE_READONLY)
        self.text_ctrl.MinSize = (-1, 20)
        self.text_ctrl.SetForegroundColour(odemis.gui.FG_COLOUR_EDIT)
        self.text_ctrl.SetBackgroundColour(odemis.gui.BG_COLOUR_MAIN)
        self.text_ctrl.Bind(wx.EVT_TEXT, self.on_changed)
        if self.file_path:
            self.SetValue(self.file_path)

        box.Add(self.text_ctrl, 1)

        if clear_btn:
            self._btn_clear = ImageButton(self, bitmap=img.getBitmap("icon/ico_clear.png"),
                                          pos=(10, 8))
            self._btn_clear.bmpHover = img.getBitmap("icon/ico_clear_h.png")

            self._btn_clear.SetToolTip("Clear calibration")  # FIXME: do not hard code
            self._btn_clear.Hide()
            self._btn_clear.Bind(wx.EVT_BUTTON, self._on_clear)
            box.Add(self._btn_clear, 0, wx.LEFT, 10)

        self.btn_ctrl = ImageTextButton(self, label="change...", height=16, style=wx.ALIGN_CENTER)
        self.btn_ctrl.Bind(wx.EVT_BUTTON, self._on_browse)

        box.Add(self.btn_ctrl, 0, wx.LEFT, 5)

        self.SetAutoLayout(True)
        self.SetSizer(box)
        self.Layout()
        self.SetSize(size)

    def on_changed(self, evt):
        evt.SetEventObject(self)
        evt.Skip()

    def _SetValue(self, file_path, raise_event):

        if file_path:
            logging.debug("Setting file control to %s", file_path)

            self.file_path = file_path

            if self.dialog_style & wx.FD_SAVE == 0 and not os.path.exists(self.file_path):
                self.text_ctrl.SetForegroundColour(odemis.gui.FG_COLOUR_ERROR)
            else:
                self.text_ctrl.SetForegroundColour(odemis.gui.FG_COLOUR_EDIT)

            self.text_ctrl.SetValue(self.file_path)

            self.text_ctrl.SetToolTip(self.file_path)
            self.text_ctrl.SetInsertionPointEnd()

            if self._btn_clear:
                self._btn_clear.Show()
        else:
            logging.debug("Clearing file control")

            self.file_path = None
            self.text_ctrl.SetForegroundColour(odemis.gui.FG_COLOUR_DIS)

            self.text_ctrl.SetValue(self.label)

            self.text_ctrl.SetToolTip("")
            if self._btn_clear:
                self._btn_clear.Hide()

        self.Layout()

        if raise_event:
            wx.PostEvent(self, FileSelectEvent(selected_file=self.file_path))

    def SetValue(self, file_path):
        logging.debug("File set to '%s' by Odemis", file_path)
        self._SetValue(file_path, raise_event=False)

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

    def _on_clear(self, evt):
        self._SetValue(None, raise_event=True)

    def clear(self):
        self.SetValue(None)

    def _get_all_exts_from_wildcards(self, wildcards):
        """
        Decode a wildcard list into a list of extensions
        wildcards (str): in the format "User text|*.glob;*.ext|..."
        return (list of list of str): for each format, each extension in a separate string
        """
        if "|" in wildcards:
            # Separate the parts of the wildcards, and skip the user-friendly text
            exts = wildcards.split("|")[1::2]
        else:
            # Support very basic wildcard "*.png"
            exts = [wildcards]
        return [es.split(";") for es in exts]

    def _get_exts_from_wildcards(self, wildcards, i):
        """
        Locate the extension corresponding to the given index in a wildcard list
        wildcards (str): in the format "User text|*.glob;*.ext|..."
        i (int>=0): the wildcard index number
        return (list of str): all the extensions for the given format, in a separate string
        raise IndexError: if the index is not in the wildcards
        """
        if "|" in wildcards:
            # Separate the parts of the wildcards, and skip the user-friendly text
            exts = wildcards.split("|")[1::2]
        else:
            # Support very basic wildcard "*.png"
            exts = [wildcards]
        return exts[i].split(";")

    def _on_browse(self, evt):
        current = self.GetValue() or ""
        if current and os.path.isdir(current):
            path = current
            bn = ""
        else:
            path, bn = os.path.split(current)
            if not (path and os.path.isdir(path)):
                path = self.default_dir
                bn = ""

        dlg = wx.FileDialog(self, self.dialog_title, path, bn,
                            wildcard=self.wildcard,
                            style=self.dialog_style)

        if self.dialog_style & wx.FD_SAVE and bn != "":
            # Select the format corresponding to the current file extension
            # (or the format with the longest matching extension, if there are several)
            all_exts = self._get_all_exts_from_wildcards(self.wildcard)
            best_len = 0
            for i, exts in enumerate(all_exts):
                len_ext = max((len(ext) for ext in exts if fnmatch.fnmatch(bn, ext)), default=0)
                if len_ext > best_len:
                    dlg.SetFilterIndex(i)
                    best_len = len_ext
            else:
                logging.debug("File %s didn't match any extension", bn)

        if dlg.ShowModal() == wx.ID_OK:
            fullpath = dlg.GetPath()

            if self.dialog_style & wx.FD_SAVE:
                # Make sure the extension fits the select file format
                exts = self._get_exts_from_wildcards(self.wildcard, dlg.GetFilterIndex())
                for ext in exts:
                    if fnmatch.fnmatch(fullpath, ext) or ext == "*.*":
                        logging.debug("File %s matches extension %s", fullpath, ext)
                        break  # everything is fine
                else:
                    # Remove the current extension, and use a fitting one
                    bn, oldext = splitext(fullpath)
                    try:
                        newext = "." + exts[0].split(".", 1)[1]
                        fullpath = bn + newext
                        logging.debug("Adjusted file extension to %s", newext)
                    except Exception:
                        logging.exception("Failed to adjust filename to extensions %s" % (exts,))

            self._SetValue(fullpath, raise_event=True)
            # Update default_dir so that if the value is cleared, we start from
            # this directory
            self.default_dir = os.path.dirname(fullpath)

        dlg.Destroy()
