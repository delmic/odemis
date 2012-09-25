#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

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

import collections

import wx

import odemis.gui

from odemis.gui.log import log


class ViewSideBar(object):
    """ The main controller class for the view panel in the live view.

    This class can be used to set, get and otherwise manipulate the content
    of the setting panel.
    """

    def __init__(self, main_frame):
        self._main_frame = main_frame

        self._view_selector = ViewSelector(main_frame)

class ViewSelector(object):
    """ This class controls the view panels and the view selector buttons and
    labels associated with them.
    """

    def __init__(self, main_frame):

        self.main_frame = main_frame

        # View panels
        self.views = [(main_frame.btn_view_all, None),
                      (main_frame.btn_view_tl, main_frame.pnl_view_tl),
                      (main_frame.btn_view_tr, main_frame.pnl_view_tr),
                      (main_frame.btn_view_bl, main_frame.pnl_view_bl),
                      (main_frame.btn_view_br, main_frame.pnl_view_br)]

        for btn, _ in self.views:
            btn.Bind(wx.EVT_LEFT_DOWN, self.OnClick)

    def _reset_buttons(self, btn=None):
        log.debug("Resetting tab buttons")
        for button in [b for b, _ in self.views if b != btn]:
            button.SetToggle(False)


    def OnClick(self, evt):
        evt_btn = evt.GetEventObject()

        self.main_frame.Freeze()
        self._reset_buttons(evt_btn)

        for btn, view in self.views:
            if evt_btn == btn:
                # If no view is associated, show them all
                if view is None:
                    for v in [v for _, v in self.views if v]:
                        v.Show()
                    # Everything is shown, no further processing required
                    break
                else:
                    view.SetFocus(True)
                    view.Show()
            else:
                if view is not None:
                    view.SetFocus(False)
                    view.Hide()

        self.main_frame.pnl_tab_live.Layout()
        self.main_frame.Thaw()

        evt.Skip()
