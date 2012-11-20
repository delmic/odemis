# -*- coding: utf-8 -*-
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

# from odemis.gui.util import img
# import os
# import re
# import subprocess
# import sys
# import threading
# import time

import wx

import odemis.gui.instrmodel as instrmodel
from odemis.gui.log import log

class MicroscopeController(object):
    """ This controller class controls the main microscope buttons and allow
    querying of various status attributes.
    """
    def __init__(self, interface_model, main_frame):
        """
        interface_model: GUIMicroscope
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self.interface_model = interface_model

        # Microscope buttons

        self.btn_pressure = main_frame.btn_toggle_press
        self.btn_optical = main_frame.btn_toggle_opt
        self.btn_sem = main_frame.btn_toggle_sem
        self.btn_pause = main_frame.btn_toggle_pause

        # FIXME: special _bitmap_ toggle button doesn't seem to generate
        # EVT_TOGGLEBUTTON
        # self.btn_optical.Bind(wx.EVT_TOGGLEBUTTON, self.on_toggle_opt)
        self.btn_optical.Bind(wx.EVT_BUTTON, self.on_toggle_optical)
        self.btn_sem.Bind(wx.EVT_BUTTON, self.on_toggle_sem)

    # Event handlers

    def on_toggle_optical(self, event):
        log.debug("Optical toggle button pressed")
        if self.interface_model:
            if event.isDown:
                self.interface_model.opticalState.value = instrmodel.STATE_ON
            else:
                self.interface_model.opticalState.value = instrmodel.STATE_OFF

    def on_toggle_sem(self, event):
        log.debug("SEM toggle button pressed")
        if self.interface_model:
            if event.isDown:
                self.interface_model.emState.value = instrmodel.STATE_ON
            else:
                self.interface_model.emState.value = instrmodel.STATE_OFF

    # Status checking methods

    def is_pressurizing(self):
        """ For now it returns if the pressure button is on, but later it
        probably should check if the button is on *and* if the desired pressure
        is reached. (Or, the button should automatically turn off when the
        desired pressure is reached?)
        """

        return self.btn_pressure.IsEnabled() and self.btn_pressure.GetToggle()

    def optical_is_on(self):
        """ Returns True if the optical microscope button is toggled
        """

        return self.btn_optical.IsEnabled() and self.btn_optical.GetToggle()

    def sem_is_on(self):
        """ Returns True if the scanning elecltron microscope button is toggled
        """

        return self.btn_sem.IsEnabled() and self.btn_sem.GetToggle()

    def is_pauzed(self):
        return self.btn_pause.IsEnabled() and self.btn_pause.GetToggle()

