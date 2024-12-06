# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2024 Nandish Patel, Delmic

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

import wx

import odemis.gui.model as guimod
from odemis.gui.comp.fastem_user_settings_panel import (
    CONTROL_CONFIG,
    DWELL_TIME_ACQUISITION,
    OVERVOLTAGE,
    VOLTAGE,
)
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.cont.tabs.tab import Tab


class FastEMProjectSettingsTab(Tab):
    """A tab for managing project-specific settings."""
    def __init__(self, name, button, panel, main_frame, main_data, main_tab_data):
        """
        Initializes the FastEMProjectSettingsTab with the provided parameters.

        :param name: (str) The name of the tab.
        :param button: (wx.Button) The button associated with the tab.
        :param panel: (wx.Panel) The panel that contains the tab's UI components.
        :param main_frame: (wx.Frame) The main application window.
        :param main_data: (MainGUIData) The main data model for the entire GUI.
        :param main_tab_data: The FastEMMainTab tab data.
        """
        self.tab_data = guimod.MicroscopyGUIData(main_data)
        self.main_tab_data = main_tab_data
        self.panel = panel
        super().__init__(name, button, panel, main_frame, self.tab_data)
        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.dwell_time_acquisition_ctrl = None

        self.project_settings = SettingsPanel(
            panel, size=(int(panel.Parent.Size[0] / 2.5), int(panel.Parent.Size[1] / 1.5))
        )

        self._create_project_settings_entries()
        self.main_tab_data.current_project.subscribe(
            self._on_current_project, init=True
        )
        self.tab_data.main.user_dwell_time_acquisition.subscribe(
            self._on_user_dwell_time_acquisition
        )

    def _on_user_dwell_time_acquisition(self, dwell_time):
        """Handles changes to the user dwell time acquisition and updates the control and data model."""
        self.dwell_time_acquisition_ctrl.SetValue(dwell_time)
        settings_data = self.main_tab_data.project_settings_data.value
        for project in settings_data.keys():
            settings_data[project][DWELL_TIME_ACQUISITION] = dwell_time
        self.main_tab_data.project_settings_data.value = settings_data

    def _on_current_project(self, current_project):
        """Updates the controls when the current project changes."""
        if len(current_project) > 0:
            if current_project in self.main_tab_data.project_settings_data.value:
                self.dwell_time_acquisition_ctrl.SetValue(
                    self.main_tab_data.project_settings_data.value[current_project][
                        DWELL_TIME_ACQUISITION
                    ]
                )
            else:
                self.main_tab_data.project_settings_data.value[current_project] = {}
                self.main_tab_data.project_settings_data.value[current_project][
                    DWELL_TIME_ACQUISITION
                ] = self.tab_data.main.user_dwell_time_acquisition.value

    def on_control_event(self, evt):
        """Handles control events from the various input elements in the tab."""
        ctrl = evt.GetEventObject()
        if not ctrl:
            return
        entry = ctrl.GetName()

        if evt.GetEventType() == wx.EVT_TEXT_ENTER.typeId:
            if entry == OVERVOLTAGE:
                # Call on_text_enter explicitly as it is not binded
                ctrl.on_text_enter(evt)
                value = ctrl.GetValue()
                if len(self.main_tab_data.current_project.value) > 0:
                    self.main_tab_data.project_settings_data.value[
                        self.main_tab_data.current_project.value
                    ][entry] = value
                    self.tab_data.main.mppc.overVoltage.value = value
        elif evt.GetEventType() == wx.EVT_COMBOBOX.typeId:
            value = ctrl.GetValue()
            if entry == VOLTAGE:
                if len(self.main_tab_data.current_project.value) > 0:
                    self.main_tab_data.project_settings_data.value[
                        self.main_tab_data.current_project.value
                    ][entry] = value
                    self.tab_data.main.ebeam.accelVoltage.value = ctrl.GetClientData(
                        ctrl.GetSelection()
                    )
        elif evt.GetEventType() == wx.EVT_SLIDER.typeId:
            value = ctrl.GetValue()
            if entry == DWELL_TIME_ACQUISITION:
                if len(self.main_tab_data.current_project.value) > 0:
                    self.main_tab_data.project_settings_data.value[
                        self.main_tab_data.current_project.value
                    ][entry] = value

    def _bind_project_settings_control_events(self, ctrl, entry):
        """Binds the appropriate event handlers to the project settings controls."""
        if entry == VOLTAGE:
            ctrl.Bind(wx.EVT_COMBOBOX, self.on_control_event)
        if entry == DWELL_TIME_ACQUISITION:
            ctrl.Bind(wx.EVT_SLIDER, self.on_control_event)
        if entry == OVERVOLTAGE:
            ctrl.Unbind(wx.EVT_TEXT_ENTER, handler=ctrl.on_text_enter)
            ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_control_event)

    def _create_project_settings_entries(self):
        """Creates and initializes the controls for project-specific settings."""
        control_definitions = [
            # For now, Voltage and Overvoltage can only be set in the user settings panel
            # Don't have them for project specific settings
            # (VOLTAGE, CONTROL_CONFIG[VOLTAGE]),
            # (OVERVOLTAGE, CONTROL_CONFIG[OVERVOLTAGE]),
            (DWELL_TIME_ACQUISITION, CONTROL_CONFIG[DWELL_TIME_ACQUISITION]),
        ]
        for entry, conf in control_definitions:
            if entry == OVERVOLTAGE:
                _, ctrl = self.project_settings.add_float_field(
                    entry,
                    value=self.tab_data.main.mppc.overVoltage.value,
                    conf=conf,
                )
            elif entry == DWELL_TIME_ACQUISITION:
                lbl, ctrl = self.project_settings.add_float_slider(
                    entry,
                    value=self.tab_data.main.user_dwell_time_acquisition.value,
                    conf=conf,
                )
                self.dwell_time_acquisition_ctrl = ctrl
                # Wrap the label text because its too long
                lbl.Wrap(100)
            elif entry == VOLTAGE:
                voltage = self.tab_data.main.ebeam.accelVoltage.value
                idx = CONTROL_CONFIG[VOLTAGE]["choices"].index(int(voltage))
                value = CONTROL_CONFIG[VOLTAGE]["labels"][idx]
                _, ctrl = self.project_settings.add_combobox_control(
                    entry, value=value, conf=conf
                )

            ctrl.SetName(entry)
            self._bind_project_settings_control_events(ctrl, entry)

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            self._initialized_after_show = True

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 1
        else:
            return None
