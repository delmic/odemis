# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

Custom (graphical) radio button control.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
from odemis import gui
from odemis.gui.comp.buttons import GraphicRadioButton
import wx


class GraphicalRadioButtonControl(wx.Panel):

    def __init__(self, *args, **kwargs):

        #self.bnt_width = kwargs.pop("bnt_width", 32)

        self.choices = kwargs.pop("choices", [])
        self.buttons = []
        self.labels = kwargs.pop("labels", [])
        self.units = kwargs.pop("units", None)

        wx.Panel.__init__(self, *args, **kwargs)

        self.SetBackgroundColour(self.Parent.GetBackgroundColour())

        sizer = wx.BoxSizer(wx.HORIZONTAL)

        for choice, label in zip(self.choices, self.labels):
            btn = GraphicRadioButton(self, value=choice, style=wx.ALIGN_CENTER, label=label,
                                      height=16)

            btn.SetForegroundColour(gui.FG_COLOUR_RADIO_INACTIVE)

            self.buttons.append(btn)

            sizer.Add(btn, flag=wx.RIGHT, border=5)
            btn.Bind(wx.EVT_BUTTON, self.OnClick)
            btn.Bind(wx.EVT_KEY_UP, self.OnKeyUp)

        if self.units:
            lbl = wx.StaticText(self, -1, self.units)
            lbl.SetForegroundColour(gui.FG_COLOUR_MAIN)
            sizer.Add(lbl, flag=wx.RIGHT, border=5)

        self.SetSizer(sizer)

    def _reset_buttons(self, button=None):
        for btn in self.buttons:
            self.SetActive(btn, btn == button)

    def SetValue(self, value):
        logging.debug("Set radio button control to %s", value)
        for btn in self.buttons:
            self.SetActive(btn, btn.value == value)

    def SetActive(self, btn, active):
        """Activates active button and deactivates all others.
        Sets color of text of buttons"""
        btn.SetToggle(active)
        if active:
            btn.SetForegroundColour(gui.FG_COLOUR_RADIO_ACTIVE)
        else:
            btn.SetForegroundColour(gui.FG_COLOUR_RADIO_INACTIVE)

    def GetValue(self):
        for btn in self.buttons:
            if btn.GetToggle():
                return btn.value

    def OnKeyUp(self, evt):
        btn = evt.GetEventObject()
        if btn.hasFocus and evt.GetKeyCode() == ord(" "):
            self._reset_buttons(btn)
            btn.up = False
            btn.Notify()
            btn.Refresh()

    def OnClick(self, evt):
        btn = evt.GetEventObject()
        self._reset_buttons(btn)
        #if not btn.GetToggle():
        evt.Skip()


