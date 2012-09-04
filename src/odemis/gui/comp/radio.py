#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

Custom (graphical) radio button control.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import wx

from odemis.gui.comp.buttons import GraphicRadioButton
from img.data import getbtn_smlBitmap, getbtn_sml_hBitmap, getbtn_sml_aBitmap
from log import log

class GraphicalRadioButtonControl(wx.Panel):

    def __init__(self, *args, **kwargs)    :

        self.bnt_width = kwargs.pop("bnt_width", 48)

        self.choices = kwargs.pop("choices", [])
        self.buttons = []



        wx.Panel.__init__(self, *args, **kwargs)

        self.SetBackgroundColour(self.Parent.GetBackgroundColour())

        sizer = wx.BoxSizer(wx.HORIZONTAL)

        for choice in self.choices:
            btn = GraphicRadioButton(self,
                                     -1,
                                     getbtn_smlBitmap(),
                                     value=choice,
                                     size=(self.bnt_width, 16),
                                     style=wx.ALIGN_CENTER,
                                     label_delta=1)

            btn.SetForegroundColour("#000000")

            btn.SetBitmaps(getbtn_sml_hBitmap(),
                           getbtn_sml_aBitmap(),
                           getbtn_sml_aBitmap())

            self.buttons.append(btn)

            sizer.Add(btn, flag=wx.RIGHT, border=5)
            btn.Bind(wx.EVT_LEFT_DOWN, self.OnClick)

        self.SetSizer(sizer)

    def _reset_buttons(self):
        for button in self.buttons:
            button.SetToggle(False)

    def SetValue(self, value):
        log.debug("Set radio button control to %s", value)
        self._reset_buttons()

        for i, btn in enumerate(self.buttons):
            if btn.value == value:
                self.buttons[i].SetToggle(True)

    def GetValue(self):
        for btn in self.buttons:
            if btn.GetToggle():
                return btn.value

    def OnClick(self, evt):

        btn = evt.GetEventObject()
        if btn.GetToggle():
            return

        self._reset_buttons()
        #if not btn.GetToggle():
        evt.Skip()

