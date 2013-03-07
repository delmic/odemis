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

from odemis.gui.comp.buttons import ImageToggleButton
# from odemis.gui.cont.acquisition import AcquisitionController
# from odemis.gui.cont.microscope import MicroscopeController
# from odemis.gui.cont import settings
# from odemis.gui.cont.streams import StreamController
# from odemis.gui.cont.views import ViewController, ViewSelector

class ToolMenu(wx.Panel):
    """ Tool Menu base class """

    def __init__(self):
        pre = wx.PrePanel()
        # the Create step is done later by XRC.
        self.PostCreate(pre)
        self.Bind(wx.EVT_WINDOW_CREATE, self.OnCreate)

        self.toggle_buttons = []
        self.action_buttons = []

    def OnCreate(self, event):
        raise NotImplementedError

    def _sort_buttons(self, btn_list):

        for b in btn_list:
            if isinstance(b, ImageToggleButton):
                self.toggle_buttons.append(b)
            else:
                self.action_buttons.append(b)

class SemToolMenu(ToolMenu):

    def __init__(self):
        ToolMenu.__init__(self)

        self.btn_zoom_tool = None
        self.btn_update_tool = None
        self.btn_resize = None

    def OnCreate(self, event):
        self.Unbind(wx.EVT_WINDOW_CREATE)

        self.btn_zoom_tool = xrc.XRCCTRL(self, "btn_secom_view_zoom")
        self.btn_update_tool = xrc.XRCCTRL(self, "btn_secom_view_update")
        self.btn_resize = xrc.XRCCTRL(self, "btn_secom_view_resize")

        self._sort_buttons ([self.btn_zoom_tool,
                             self.btn_update_tool,
                             self.btn_resize])

        self.btn_zoom_tool.Bind(wx.EVT_BUTTON, self.on_zoom)

        logging.debug("Created SemToolMenu")

    def on_zoom(self, evt):
        logging.debug("Zoom tool clicked")
        pub.sendMessage('secom.tool.zoom.click',
                        enabled=self.btn_zoom_tool.GetToggle()
                        )
