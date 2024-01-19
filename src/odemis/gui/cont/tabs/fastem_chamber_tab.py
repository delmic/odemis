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

import collections
import logging
import wx

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui
from odemis.gui.cont.stream_bar import StreamBarController
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.microscope import FastEMStateController
from odemis.gui.cont.tabs.tab import Tab


class FastEMChamberTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """ FastEM chamber view tab """

        tab_data = guimod.MicroscopyGUIData(main_data)
        self.main_data = main_data
        super(FastEMChamberTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("CHAMBER")

        self.panel.selection_panel.create_controls(tab_data.main.scintillator_layout)
        for btn in self.panel.selection_panel.buttons.keys():
            btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_selection_button)

        # Create stream & view
        self._stream_controller = StreamBarController(
            tab_data,
            panel.pnl_streams,
            locked=True
        )

        # create a view on the microscope model
        vpv = collections.OrderedDict((
            (self.panel.vp_chamber,
                {
                    "name": "Chamber view",
                }
            ),
        ))
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)
        view = self.tab_data_model.focussedView.value
        view.interpolate_content.value = False
        view.show_crosshair.value = False
        view.show_pixelvalue.value = False

        if main_data.chamber_ccd:
            # Just one stream: chamber view
            self._ccd_stream = acqstream.CameraStream("Chamber view",
                                      main_data.chamber_ccd, main_data.chamber_ccd.data,
                                      emitter=None,
                                      focuser=None,
                                      detvas=get_local_vas(main_data.chamber_ccd, main_data.hw_settings_config),
                                      forcemd={model.MD_POS: (0, 0),  # Just in case the stage is there
                                               model.MD_ROTATION: 0}  # Force the CCD as-is
                                      )
            # Make sure image has square pixels and full FoV
            if hasattr(self._ccd_stream, "detBinning"):
                self._ccd_stream.detBinning.value = (1, 1)
            if hasattr(self._ccd_stream, "detResolution"):
                self._ccd_stream.detResolution.value = self._ccd_stream.detResolution.range[1]
            ccd_spe = self._stream_controller.addStream(self._ccd_stream)
            ccd_spe.stream_panel.flatten()  # No need for the stream name
            self._ccd_stream.should_update.value = True
        else:
            logging.info("No CCD found, so chamber view will have no stream")

        # Pump and ebeam state controller
        self._state_controller = FastEMStateController(tab_data, panel)

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

    def Show(self, show=True):
        super().Show(show)

        # Start chamber view when tab is displayed, and otherwise, stop it
        if self.tab_data_model.main.chamber_ccd:
            self._ccd_stream.should_update.value = show

    def terminate(self):
        if self.tab_data_model.main.chamber_ccd:
            self._ccd_stream.is_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 3
        else:
            return None
