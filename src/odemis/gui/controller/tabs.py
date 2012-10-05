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

from odemis.gui.log import log

class TabBar(object):

    def __init__(self, main_frame, btns_n_tabs):

        self.main_frame = main_frame
        self.btns_n_tabs = btns_n_tabs

        for btn, _ in self.btns_n_tabs:
            btn.Bind(wx.EVT_BUTTON, self.OnClick)

        btn, tab =self.btns_n_tabs[0]
        btn.SetToggle(True)
        tab.Show()

    def _reset_buttons(self, btn=None):
        log.debug("Resetting tab buttons")
        for button in [b for b, _ in self.btns_n_tabs if b != btn]:
            button.SetToggle(False)

    def OnClick(self, evt):
        log.debug("Tab button click")

        evt_btn = evt.GetEventObject()

        self._reset_buttons(evt_btn)

        self.main_frame.Freeze()

        for btn, tab in self.btns_n_tabs:
            if evt_btn == btn:
                tab.Show()
            else:
                tab.Hide()

        self.main_frame.Layout()
        self.main_frame.Thaw()

        #if not btn.GetToggle():
        evt.Skip()
