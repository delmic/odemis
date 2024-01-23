# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel, Rinze de Laat, Philip Winkler

Copyright © 2012-2022 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the acquisition
of microscope images.

"""

import logging

import wx

import odemis.gui.model as guimod
from odemis.acq.stream import UNDEFINED_ROI, ScannedTCSettingsStream
from odemis.gui.preset import preset_as_is, get_global_settings_entries, \
    get_local_settings_entries, apply_preset
from odemis.gui.model import TOOL_NONE, TOOL_SPOT
from odemis.gui.util import call_in_wx_main
from odemis.gui.win.acquisition import AcquisitionDialog


# TODO: Once the Secom acquisition is merged back into the main stream tab,
# the difference between controller should be small enough to merge a lots of
# things together
class SecomAcquiController(object):
    """ controller to handle high-res image acquisition in a
    "global" context. In particular, it needs to be aware of which viewport
    is currently focused, and block any change of settings during acquisition.
    """

    def __init__(self, tab_data, tab_panel):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel

        # Listen to "acquire image" button
        self._tab_panel.btn_secom_acquire.Bind(wx.EVT_BUTTON, self.on_acquire)

        # Only possible to acquire if there are streams, and the chamber is
        # under vacuum
        tab_data.streams.subscribe(self.on_stream_chamber)
        tab_data.main.chamberState.subscribe(self.on_stream_chamber)

        if hasattr(tab_data, "roa"):
            tab_data.roa.subscribe(self.on_stream_chamber, init=True)

        # Disable the "acquire image" button while preparation is in progress
        self._main_data_model.is_preparing.subscribe(self.on_preparation)

    # Some streams (eg, TCSettingsStream) require a ROA for acquiring.
    # So if any of this type of Stream is present, forbid to acquire until the ROA is defined.
    def _roa_is_valid(self):
        roa_valid = True
        if hasattr(self._tab_data_model, "roa") and self._main_data_model.time_correlator is not None and \
                any(isinstance(s, ScannedTCSettingsStream) for s in self._tab_data_model.streams.value):
            roa_valid = self._tab_data_model.roa.value != UNDEFINED_ROI

        return roa_valid

    @call_in_wx_main
    def on_stream_chamber(self, _):
        """
        Called when chamber state or streams change.
        Used to update the acquire button state
        """
        st_present = not not self._tab_data_model.streams.value
        ch_vacuum = (self._tab_data_model.main.chamberState.value
                     in {guimod.CHAMBER_VACUUM, guimod.CHAMBER_UNKNOWN})

        should_enable = st_present and ch_vacuum and not self._main_data_model.is_preparing.value and self._roa_is_valid()

        self._tab_panel.btn_secom_acquire.Enable(should_enable)

    @call_in_wx_main
    def on_preparation(self, is_preparing):
        self._tab_panel.btn_secom_acquire.Enable(not is_preparing and self._roa_is_valid())

    def on_acquire(self, evt):
        self.open_acquisition_dialog()

    def open_acquisition_dialog(self):
        main_data = self._tab_data_model.main
        secom_live_tab = main_data.getTabByName("secom_live")

        # Indicate we are acquiring, especially important for the SEM which
        # need to get the external signal to not scan (cf MicroscopeController)
        main_data.is_acquiring.value = True

        # save the original settings
        settingsbar_controller = secom_live_tab.settingsbar_controller
        orig_entries = get_global_settings_entries(settingsbar_controller)
        for sc in secom_live_tab.streambar_controller.stream_controllers:
            orig_entries += get_local_settings_entries(sc)
        orig_settings = preset_as_is(orig_entries)
        settingsbar_controller.pause()
        settingsbar_controller.enable(False)

        # pause all the live acquisitions
        streambar_controller = secom_live_tab.streambar_controller
        streambar_controller.pauseStreams()
        streambar_controller.pause()

        if self._tab_data_model.tool.value == TOOL_SPOT:
            self._tab_data_model.tool.value = TOOL_NONE

        streambar_controller.enable(False)

        # create the dialog
        try:
            acq_dialog = AcquisitionDialog(self._tab_panel.Parent, self._tab_data_model)
            parent_size = [v * 0.77 for v in self._tab_panel.Parent.GetSize()]

            acq_dialog.SetSize(parent_size)
            acq_dialog.Center()
            action = acq_dialog.ShowModal()
        except Exception:
            logging.exception("Failed to create acquisition dialog")
            raise
        finally:
            apply_preset(orig_settings)

            settingsbar_controller.enable(True)
            settingsbar_controller.resume()

            streambar_controller.enable(True)
            streambar_controller.resume()

            main_data.is_acquiring.value = False

            acq_dialog.Destroy()

        if action == wx.ID_OPEN:
            tab = main_data.getTabByName('analysis')
            main_data.tab.value = tab
            tab.load_data(acq_dialog.last_saved_file)
