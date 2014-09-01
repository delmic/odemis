#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the settings controls in the right
setting column of the user interface.

"""

import wx

import odemis.gui as gui
from odemis.gui.comp.foldpanelbar import FoldPanelItem


class SettingsPanel(wx.Panel):

    def __init__(self, *args, **kwargs):
        default_msg = kwargs.pop('default_msg', "")
        super(SettingsPanel, self).__init__(*args, **kwargs)

        assert isinstance(self.Parent, FoldPanelItem)

        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.SetForegroundColour(gui.FG_COLOUR_MAIN)

        # The main sizer is used to create a margin on the inside of the panel
        self._main_sizer = wx.BoxSizer(wx.VERTICAL)
        # The GridBagSizer is use to create a 2-column lay-out for the settings controls
        self._gb_sizer = wx.GridBagSizer()

        # The default message text is added here, because at least control needs to be present
        # before the growable column can be added.
        self.message_text = None
        self._set_default_message(default_msg)

        # Make the 2nd column expand
        self._gb_sizer.AddGrowableCol(1)

        self.SetSizer(self._main_sizer)
        self._main_sizer.Add(self._gb_sizer, proportion=1, flag=wx.ALL | wx.EXPAND, border=5)

        self.num_rows = 1  # The number of rows in the GridBagSizer

    def _set_default_message(self, msg):
        assert not self.GetChildren()
        self.message_text = wx.StaticText(self, -1, msg)
        self._gb_sizer.Add(self.message_text, (0, 0), (1, 2))

    def clear_message(self):
        """ Remove the default message if it exsists """
        if self.message_text:
            self.message_text.Destroy()
            self.Layout()
            self.num_rows = 0

    def clear_all(self):
        """ Remove all children """
        for c in self.GetChildren():
            c.Destroy()
        self.num_rows = 0

    # TODO: unused?
    def add_label(self, label, value=None, selectable=True):
        """ Adds a label to the settings panel, accompanied by an immutable
        value if one's provided.

        value (None or stringable): value to display after the label
        selectable (boolean): whether the value can be selected by the user
          (iow, copy/pasted)
        returns (SettingEntry): the new SettingEntry created
        """

        self.clear_message()

        # Create label
        lbl_ctrl = wx.StaticText(self, -1, unicode(label))
        self._gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 0),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5
        )

        if value and not selectable:
            value_ctrl = wx.StaticText(self.panel, label=unicode(value))
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
            self.panel._gb_sizer.Add(value_ctrl, (self.num_entries, 1),
                               flag=wx.ALL, border=5)
        elif value and selectable:
            value_ctrl = wx.TextCtrl(self.panel, value=unicode(value),
                                     style=wx.BORDER_NONE | wx.TE_READONLY)
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
            value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
            self.panel._gb_sizer.Add(value_ctrl, (self.num_entries, 1),
                               flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                               border=5)
        else:
            value_ctrl = None

        ne = SettingEntry(name=label, label=lbl_ctrl, ctrl=value_ctrl)
        self.entries.append(ne)
        self.num_entries += 1

        return ne
