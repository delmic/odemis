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

from odemis.gui.cont.acquisition import AcquisitionController
from odemis.gui.cont.microscope import MicroscopeController
from odemis.gui.cont.settings import SettingsBarController
from odemis.gui.cont.streams import StreamController
from odemis.gui.cont.views import ViewController, ViewSelector

main_tab_controller = None

class Tab(object):
    """ Small helper class representing a tab (= tab button + panel) """

    def __init__(self, name, button, panel):
        self.name = name
        self.button = button
        self.panel = panel
        self.active = True

        self.initialized = False

    def _show(self, show):

        if show and not self.initialized:
            self._initialize()
            self.initialized = True

        self.button.SetToggle(show)
        self.panel.Show(show)

    def show(self):
        self._show(True)

    def hide(self):
        self._show(False)

    def _initialize(self):
        pass

class SecomLiveTab(Tab):

    def __init__(self, name, button, panel, main_frame, interface_model):
        super(SecomLiveTab, self).__init__(name, button, panel)

        self.interface_model = interface_model
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images

        self._settings_controller = None
        self._view_controller = None
        self._vstream_controller = None
        self._view_selector = None
        self._acquisition_controller = None
        self._microscope_controller = None

    def _initialize(self):
        """ This method is called when the tab is first shown """

        self._settings_controller = SettingsBarController(self.interface_model,
                                                         self.main_frame)

        # Order matters!
        # First we create the views, then the streams
        self._view_controller = ViewController(self.interface_model,
                                              self.main_frame)

        self._stream_controller = StreamController(self.interface_model,
                                                  self.main_frame.pnl_stream)

        self._view_selector = ViewSelector(self.interface_model,
                                          self.main_frame)

        self._acquisition_controller = AcquisitionController(self.interface_model,
                                                            self.main_frame)

        self._microscope_controller = MicroscopeController(self.interface_model,
                                                          self.main_frame)

    @property
    def settings_controller(self):
        return self._settings_controller

    @property
    def stream_controller(self):
        return self._stream_controller


class TabBarController(object):

    def __init__(self, tab_list, main_frame):


        self.main_frame = main_frame

        self.tab_list = tab_list

        for tab in self.tab_list:
            tab.button.Bind(wx.EVT_BUTTON, self.OnClick)
            tab.button.Bind(wx.EVT_KEY_UP, self.OnKeyUp)

        # Show first tab by default
        self.show(0)

    def __getitem__(self, name):
        return self.get_tab(name)

    def get_tab(self, tab_name_or_index):
        for i, tab in enumerate(self.tab_list):
            if i == tab_name_or_index or tab.name == tab_name_or_index:
                return tab

        raise LookupError

    def show(self, tab_name_or_index):
        for i, tab in enumerate(self.tab_list):
            if i == tab_name_or_index or tab.name == tab_name_or_index:
                tab.show()
            else:
                tab.hide()

    def hide_all(self):
        for tab in self.tab_list:
            tab.hide()

    def OnKeyUp(self, evt):
        evt_btn = evt.GetEventObject()

        if evt_btn.hasFocus and evt.GetKeyCode() == ord(" "):
            self.hide_all()
            self.main_frame.Freeze()

            for tab in self.tab_list:
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

        for tab in self.tab_list:
            if evt_btn == tab.button:
                tab.show()
            else:
                tab.hide()

        self.main_frame.Layout()
        self.main_frame.Thaw()

        #if not btn.GetToggle():
        evt.Skip()
