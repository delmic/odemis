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

import logging
import wx


class Tab(object):
    """ Small helper class representing a tab (= tab button + panel) """

    def __init__(self, name, button, panel):
        self.name = name
        self.button = button
        self.panel = panel
        self.active = True

    def _show(self, show):
        self.button.SetToggle(show)
        self.panel.Show(show)

    def show(self):
        self._show(True)

    def hide(self):
        self._show(False)


class TabBarController(object):

    def __init__(self, main_frame):


        self.main_frame = main_frame

        self.tabs = [Tab("secom_live",
                         main_frame.btn_tab_secom_live,
                         main_frame.pnl_tab_secom_live),
                     Tab("secom_gallery",
                         main_frame.btn_tab_secom_gallery,
                         main_frame.pnl_tab_secom_gallery),
                     Tab("sparc_acqui",
                         main_frame.btn_tab_sparc_acqui,
                         main_frame.pnl_tab_sparc_acqui),
                     Tab("sparc_analysis",
                         main_frame.btn_tab_sparc_analysis,
                         main_frame.pnl_tab_sparc_analysis)
                     ]

        for tab in self.tabs:
            tab.button.Bind(wx.EVT_BUTTON, self.OnClick)
            tab.button.Bind(wx.EVT_KEY_UP, self.OnKeyUp)

        # Show first tab by default
        self.show(0)

    def show(self, tab_name_or_index):
        for i, tab in enumerate(self.tabs):
            if i == tab_name_or_index or tab.name == tab_name_or_index:
                tab.show()
            else:
                tab.hide()

    def hide_all(self):
        for tab in self.tabs:
            tab.hide()

    def OnKeyUp(self, evt):
        evt_btn = evt.GetEventObject()

        if evt_btn.hasFocus and evt.GetKeyCode() == ord(" "):
            self.hide_all()
            self.main_frame.Freeze()

            for tab in self.tabs:
                if evt_btn == tab.button:
                    tab.show()
                else:
                    tab.hide()

            self.main_frame.Layout()
            self.main_frame.Thaw()

    def OnClick(self, evt):
        logging.debug("Tab button click")

        evt_btn = evt.GetEventObject()

        self.hide_all()

        self.main_frame.Freeze()

        for tab in self.tabs:
            if evt_btn == tab.button:
                tab.show()
            else:
                tab.hide()

        self.main_frame.Layout()
        self.main_frame.Thaw()

        #if not btn.GetToggle():
        evt.Skip()
