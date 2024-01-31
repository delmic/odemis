# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Nandish Patel

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
import wx

import odemis.gui
from odemis.gui.cont.microscope import FastEMStateController


class FastEMUserSettingsPanel(object):
    def __init__(self, panel, main_data):
        """FastEM user settings panel"""
        self.main_data = main_data
        self.panel = panel

        self.panel.selection_panel.create_controls(self.main_data.scintillator_layout)
        for btn in self.panel.selection_panel.buttons.keys():
            btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_selection_button)

        # Pump and ebeam state controller
        self._state_controller = FastEMStateController(main_data, panel)
        panel.btn_pressure.Refresh()
        panel.pressure_label.Refresh()

    def _on_selection_button(self, evt):
        # update main_data.active_scintillators and toggle colour for better visibility
        btn = evt.GetEventObject()
        num = self.panel.selection_panel.buttons.get(btn)
        if btn.GetValue():
            btn.SetBackgroundColour(wx.GREEN)
            if num not in self.main_data.active_scintillators.value:
                self.main_data.active_scintillators.value.append(num)
            else:
                logging.warning("Scintillator %s has already been selected.", num)
        else:
            btn.SetBackgroundColour(odemis.gui.FG_COLOUR_BUTTON)
            if num in self.main_data.active_scintillators.value:
                self.main_data.active_scintillators.value.remove(num)
            else:
                logging.warning("Scintillator %s not found in list of active scintillators.", num)
