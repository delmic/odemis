# -*- coding: utf-8 -*-

""" This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and
manipulate various data streams coming from the microscope.


@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Purpose:

This module contains classes that control the various tool menus.

"""

import logging

import wx
import wx.xrc as xrc
from wx.lib.pubsub import pub

from odemis.gui.util.widgets import get_all_children
from odemis.gui.comp.buttons import ImageButton, ImageToggleButton

# from odemis.gui.cont.acquisition import AcquisitionController
# from odemis.gui.cont.microscope import MicroscopeController
# from odemis.gui.cont import settings
# from odemis.gui.cont.streams import StreamController
# from odemis.gui.cont.views import SecomViewController, ViewSelector

class ToolMenu(wx.Panel):
    """ Tool Menu base class responsible for the general buttons states """

    def __init__(self):
        pre = wx.PrePanel()
        # the Create step is done later by XRC.
        self.PostCreate(pre)
        self.Bind(wx.EVT_WINDOW_CREATE, self.OnCreate)
        self.Bind(wx.EVT_BUTTON, self._on_button)

        self.toggle_buttons = []
        self.action_buttons = []

    def OnCreate(self, event):
        raise NotImplementedError

    def _on_button(self, evt):
        """ Clear the toggle buttons on any button click within the menu """
        evt_btn = evt.GetEventObject()

        for b in self.toggle_buttons:
            if b != evt_btn:
                b.SetToggle(False)
        evt.Skip()

    def _sort_buttons(self):
        self.toggle_buttons = get_all_children(self, ImageToggleButton)
        self.action_buttons = get_all_children(self, ImageButton)

class SemToolMenu(ToolMenu):
    """ Tool menu for SEM view manipulation """

    def __init__(self):
        ToolMenu.__init__(self)

        self.btn_zoom = None
        self.btn_update = None
        self.btn_resize = None

        pub.subscribe(self.clear_zoom, 'secom.canvas.zoom.end')

    def OnCreate(self, event):
        self.Unbind(wx.EVT_WINDOW_CREATE)

        self.btn_zoom = xrc.XRCCTRL(self, "btn_secom_view_zoom")
        self.btn_update = xrc.XRCCTRL(self, "btn_secom_view_update")
        self.btn_resize = xrc.XRCCTRL(self, "btn_secom_view_resize")

        self._sort_buttons()

        self.btn_zoom.Bind(wx.EVT_BUTTON, self.on_zoom)
        self.btn_update.Bind(wx.EVT_BUTTON, self.on_update)

        logging.debug("Created SemToolMenu")

    def on_zoom(self, evt):
        logging.debug("Zoom tool clicked")
        pub.sendMessage(
            'secom.tool.zoom.click',
            enabled=self.btn_zoom.GetToggle()
        )
        evt.Skip()

    def on_update(self, evt):
        logging.debug("Update tool clicked")
        pub.sendMessage(
            'secom.tool.update.click',
            enabled=self.btn_update.GetToggle()
        )
        evt.Skip()

    def on_resize(self, evt):
        logging.debug("Resize tool clicked")
        pub.sendMessage('secom.tool.resize.click')
        evt.Skip()

    # def toggle_tool(self, btn, pub_event):
    #     new_state = not btn.GetToggle()
    #     btn.SetToggle(new_state)
    #     pub.sendMessage(
    #         pub_event,
    #         enabled=new_state
    #     )

    def clear_zoom(self):
        self.btn_zoom.SetToggle(False)
        pub.sendMessage(
            'secom.tool.zoom.click',
            enabled=self.btn_zoom.GetToggle()
        )

    # def toggle_update(self):
    #     self._toggle_tool(self.btn_update, 'secom.tool.update.click')

class SparcAcquisitionToolMenu(ToolMenu):
    """ Tool menu for Sparc acquisition view manipulation """

    def __init__(self):
        ToolMenu.__init__(self)

        self.btn_select = None
        self.btn_pick = None
        self.btn_resize = None

        pub.subscribe(self.on_select_end, "sparc.acq.select.end")

    def OnCreate(self, event):
        self.Unbind(wx.EVT_WINDOW_CREATE)

        self.btn_select = xrc.XRCCTRL(self, "btn_sparc_acq_view_select")
        self.btn_pick = xrc.XRCCTRL(self, "btn_sparc_acq_view_pick")
        self.btn_resize = xrc.XRCCTRL(self, "btn_sparc_acq_view_resize")

        self._sort_buttons()

        self.btn_select.Bind(wx.EVT_BUTTON, self.on_select)
        self.btn_pick.Bind(wx.EVT_BUTTON, self.on_pick)

        logging.debug("Created SparcToolMenu")

    def on_select(self, evt):
        logging.debug("Select tool clicked")
        pub.sendMessage('sparc.acq.tool.select.click',
                        enabled=self.btn_select.GetToggle()
                        )
        evt.Skip()

    def on_select_end(self):
        self.btn_select.SetToggle(False)
        pub.sendMessage(
            'sparc.acq.tool.select.click',
            enabled=self.btn_select.GetToggle()
        )

    def on_pick(self, evt):
        logging.debug("Pick tool clicked")
        pub.sendMessage('sparc.acq.tool.pick.click',
                        enabled=self.btn_pick.GetToggle()
                        )
        evt.Skip()

    def on_resize(self, evt):
        logging.debug("Resize tool clicked")
        pub.sendMessage('sparc.acq.tool.resize.click')
        evt.Skip()

class SparcAnalysisToolMenu(ToolMenu):
    """ Tool menu for Sparc analysis view manipulation """

    def __init__(self):
        ToolMenu.__init__(self)

        self.btn_zoom = None
        self.btn_update = None
        self.btn_resize = None

    def OnCreate(self, event):
        self.Unbind(wx.EVT_WINDOW_CREATE)

        self.btn_zoom = xrc.XRCCTRL(self, "btn_sparc_ana_view_zoom")
        self.btn_update = xrc.XRCCTRL(self, "btn_sparc_ana_view_update")
        self.btn_resize = xrc.XRCCTRL(self, "btn_sparc_ana_view_resize")

        self._sort_buttons()

        self.btn_zoom.Bind(wx.EVT_BUTTON, self.on_zoom)
        self.btn_update.Bind(wx.EVT_BUTTON, self.on_update)

        logging.debug("Created SparcToolMenu")

    def on_zoom(self, evt):
        logging.debug("Zoom tool clicked")
        pub.sendMessage('sparc.ana.tool.zoom.click',
                        enabled=self.btn_zoom.GetToggle()
                        )
        evt.Skip()

    def on_update(self, evt):
        logging.debug("Update tool clicked")
        pub.sendMessage('sparc.ana.tool.update.click',
                        enabled=self.btn_update.GetToggle()
                        )
        evt.Skip()

    def on_resize(self, evt):
        logging.debug("Resize tool clicked")
        pub.sendMessage('sparc.ana.tool.resize.click')
        evt.Skip()
