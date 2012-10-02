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



    # TODO merge with viewselector
    # TODO use microscopeGUI: display .stream and modify currentView/viewLayout 

class ViewSelector(object):
    """
    This class controls the view selector buttons and labels associated with them.
    """

    def __init__(self, micgui, main_frame):
        """
        micgui (MicroscopeGUI): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self._main_frame = main_frame

        # TODO: should create buttons 
        # btn -> viewports
        self.viewports = [(main_frame.btn_view_all, None),
                      (main_frame.btn_view_tl, main_frame.pnl_view_tl),
                      (main_frame.btn_view_tr, main_frame.pnl_view_tr),
                      (main_frame.btn_view_bl, main_frame.pnl_view_bl),
                      (main_frame.btn_view_br, main_frame.pnl_view_br)]

        for btn, _ in self.views:
            btn.Bind(wx.EVT_BUTTON, self.OnClick)

    def select_view(self, view_num):
        """ Selects the view with the provided number.

        view_num 0 activates the 2x2 view.
        """

        if not 0 <= view_num < len(self.views):
            raise ValueError("Illegal view number %s" % view_num)

        self._reset()
        btn, view = self.views[view_num]

        self.main_frame.Freeze()

        if view:
            for v in [v for _, v in self.views if v is not None and v != view]:
                v.Hide()

            view.SetFocus(True)
            view.Show()
        else:
            for v in [v for _, v in self.views if v]:
                v.Show()

        btn.SetToggle(True)

        self.main_frame.Thaw()


    def _reset(self, btn=None):
        """ Hide all views and remove any focus, and unset any navigation
        button except possibly the one provided as a parameter.
        """

        log.debug("Resetting views")
        for button, view in [(b, v) for b, v in self.views if b != btn]:
            button.SetToggle(False)
            if view:
                view.SetFocus(False)
                view.Hide()

    def show_all(self):
        """ This method will show all views and set the appropriate navigation
        button.
        """



    def OnClick(self, evt):
        """ Navigation button click event handler

        Show the related view(s) and sets the focus if needed.
        """

        log.debug("View button click")

        evt_btn = evt.GetEventObject()

        if evt_btn == self.views[0][0]:
            self.show_all() # XXX
            # The event does not need to be 'skipped' because
            # the button will be toggled in the method we called.
        else:

            self._reset(evt_btn)
            for view in [v for b, v in self.views if b == evt_btn]:
                b.set_overlay(view.get_screenshot())

            # Skip the event, so the button will toggle
            evt.Skip()
