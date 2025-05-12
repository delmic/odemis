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

import logging

import wx

import odemis.gui.model as guimod
from odemis.acq.fastem import STAGE_PRECISION
from odemis.gui import BG_COLOUR_LEGEND, BG_COLOUR_MAIN, BG_COLOUR_SEPARATOR, img
from odemis.gui.comp.fastem_user_settings_panel import (
    CONTROL_CONFIG,
    DWELL_TIME_MULTI_BEAM,
    DWELL_TIME_SINGLE_BEAM,
    HFW,
    IMMERSION,
    OVERVOLTAGE,
    PIXEL_SIZE,
    RESOLUTION,
    VOLTAGE,
)
from odemis.gui.comp.foldpanelbar import CaptionBar
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.conf.data import HW_SETTINGS_CONFIG
from odemis.gui.conf.util import format_choices, hfw_choices, str_to_value, value_to_str
from odemis.gui.cont.tabs.tab import Tab
from odemis.util import almost_equal, units


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
        self.main_data = main_data
        self.main_tab_data = main_tab_data
        self.panel = panel
        super().__init__(name, button, panel, main_frame, self.tab_data)
        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.dwell_time_mb_ctrl = None
        self.dwell_time_sb_ctrl = None
        self.hfw_ctrl = None
        self.resolution_ctrl = None
        self.pixel_size_ctrl = None
        self.immersion_mode_ctrl = None
        self._immersion_mode_hfw_choices = {}
        self._fill_hfw_choices()

        # Split the panel into two sub-panels
        h_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # Left panel is for single beam settings
        # Create a scrolled window for the left panel since it has more controls
        left_panel = wx.ScrolledWindow(
            panel,
            size=(int(panel.Parent.Size[0] / 2.5), panel.Size[1]),
            style=wx.VSCROLL,
        )
        left_panel.SetScrollRate(5, 5)
        left_panel.SetBackgroundColour("#4D4D4D")
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        single_beam_caption = CaptionBar(left_panel, "SINGLE-BEAM", False)
        single_beam_caption.set_logo(img.getBitmap("icon/ico_single_beam.png"))
        single_beam_caption.SetForegroundColour(BG_COLOUR_LEGEND)
        single_beam_panel = wx.Panel(
            left_panel, name="pnl_single_beam_settings", size=left_panel.Size
        )
        single_beam_panel.SetBackgroundColour(BG_COLOUR_MAIN)
        left_sizer.Add(single_beam_caption, 0, wx.EXPAND)
        left_sizer.Add(single_beam_panel, 1, wx.EXPAND | wx.TOP, 5)
        left_panel.SetSizer(left_sizer)

        # Right panel is for multi beam settings
        right_panel = wx.Panel(
            panel, size=(int(panel.Parent.Size[0] / 2.5), panel.Size[1])
        )
        right_panel.SetBackgroundColour("#4D4D4D")
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        multi_beam_caption = CaptionBar(right_panel, "MULTI-BEAM", False)
        multi_beam_caption.set_logo(img.getBitmap("icon/ico_multi_beam.png"))
        multi_beam_caption.SetForegroundColour(BG_COLOUR_LEGEND)
        multi_beam_panel = wx.Panel(
            right_panel, name="pnl_multi_beam_settings", size=right_panel.Size
        )
        multi_beam_panel.SetBackgroundColour(BG_COLOUR_MAIN)
        right_sizer.Add(multi_beam_caption, 0, wx.EXPAND)
        right_sizer.Add(multi_beam_panel, 1, wx.EXPAND | wx.TOP, 5)
        right_panel.SetSizer(right_sizer)

        divider_line = wx.StaticLine(panel, style=wx.LI_VERTICAL, size=(1, -1))
        divider_line.SetBackgroundColour(BG_COLOUR_SEPARATOR)

        # Add components to the horizontal sizer
        h_sizer.Add(left_panel, 1, wx.EXPAND | wx.ALL)
        h_sizer.Add(divider_line, 0, wx.EXPAND | wx.TOP | wx.BOTTOM)
        h_sizer.Add(right_panel, 1, wx.EXPAND | wx.ALL)

        # Apply sizer to main panel
        self.panel.SetSizer(h_sizer)

        self.single_beam_settings = SettingsPanel(
            single_beam_panel, size=single_beam_panel.Size
        )
        self.multi_beam_settings = SettingsPanel(
            multi_beam_panel, size=multi_beam_panel.Size
        )

        # Create the controls for the single beam and multi beam settings
        self._create_ctrl_entries()
        self.main_tab_data.current_project.subscribe(
            self._on_current_project, init=True
        )
        self.tab_data.main.user_dwell_time_mb.subscribe(self._on_user_dwell_time_mb)
        self.tab_data.main.user_dwell_time_sb.subscribe(self._on_user_dwell_time_sb)

    def _on_user_dwell_time_sb(self, dwell_time):
        """Handles changes to the user dwell time for single-beam and updates the control and data model."""
        self.dwell_time_sb_ctrl.SetValue(dwell_time)
        settings_data = self.main_tab_data.project_settings_data.value
        for project in settings_data.keys():
            settings_data[project][DWELL_TIME_SINGLE_BEAM] = dwell_time
        self.main_tab_data.project_settings_data._set_value(
            settings_data, must_notify=True
        )

    def _on_user_dwell_time_mb(self, dwell_time):
        """Handles changes to the user dwell time for multi-beam and updates the control and data model."""
        self.dwell_time_mb_ctrl.SetValue(dwell_time)
        settings_data = self.main_tab_data.project_settings_data.value
        for project in settings_data.keys():
            settings_data[project][DWELL_TIME_MULTI_BEAM] = dwell_time
        self.main_tab_data.project_settings_data._set_value(
            settings_data, must_notify=True
        )

    def _on_current_project(self, current_project):
        """Updates the controls when the current project changes."""
        if len(current_project) > 0:
            if current_project in self.main_tab_data.project_settings_data.value:
                current_settings = self.main_tab_data.project_settings_data.value[
                    current_project
                ]
                dwell_time_mb = current_settings.get(
                    DWELL_TIME_MULTI_BEAM, self.dwell_time_mb_ctrl.GetValue()
                )
                self.dwell_time_mb_ctrl.SetValue(dwell_time_mb)
                dwell_time_sb = current_settings.get(
                    DWELL_TIME_SINGLE_BEAM, self.dwell_time_sb_ctrl.GetValue()
                )
                self.dwell_time_sb_ctrl.SetValue(dwell_time_sb)
                current_immersion = self.immersion_mode_ctrl.GetValue()
                immersion = current_settings.get(IMMERSION, current_immersion)
                self.immersion_mode_ctrl.SetValue(immersion)
                hfw = current_settings.get(HFW, self.hfw_ctrl.GetValue())
                if current_immersion != immersion:
                    self._reset_hfw_ctrl(hfw, immersion)
                else:
                    self.hfw_ctrl.SetValue(hfw)
                    self.main_data.user_hfw_sb.value = self.hfw_ctrl.GetClientData(
                        self.hfw_ctrl.FindString(hfw)
                    )
                resolution = current_settings.get(
                    RESOLUTION, self.resolution_ctrl.GetValue()
                )
                self.resolution_ctrl.SetValue(resolution)
                pixel_size = current_settings.get(
                    PIXEL_SIZE, self.pixel_size_ctrl.GetValue()
                )
                self.pixel_size_ctrl.SetValue(pixel_size)
                self.main_data.user_resolution_sb.value = (
                    self.resolution_ctrl.GetClientData(
                        self.resolution_ctrl.FindString(resolution)
                    )
                )
            else:
                self.main_tab_data.project_settings_data.value[current_project] = {}
                self.main_tab_data.project_settings_data.value[current_project][
                    DWELL_TIME_MULTI_BEAM
                ] = self.dwell_time_mb_ctrl.GetValue()
                self.main_tab_data.project_settings_data.value[current_project][
                    DWELL_TIME_SINGLE_BEAM
                ] = self.dwell_time_sb_ctrl.GetValue()
                self.main_tab_data.project_settings_data.value[current_project][
                    HFW
                ] = self.hfw_ctrl.GetValue()
                self.main_tab_data.project_settings_data.value[current_project][
                    RESOLUTION
                ] = self.resolution_ctrl.GetValue()
                self.main_tab_data.project_settings_data.value[current_project][
                    IMMERSION
                ] = self.immersion_mode_ctrl.GetValue()
                self.main_tab_data.project_settings_data.value[current_project][
                    PIXEL_SIZE
                ] = self.pixel_size_ctrl.GetValue()

    def _fill_hfw_choices(self):
        """Fills the HFW choices based on the current immersion mode."""
        init_immersion = self.tab_data.main.ebeam.immersion.value
        horizontal_fw_choices = hfw_choices(
            None, self.tab_data.main.ebeam.horizontalFoV, None
        )
        hfw_unit = self.tab_data.main.ebeam.horizontalFoV.unit
        hfw_choices_formatted, choices_si_prefix = format_choices(horizontal_fw_choices)
        hfw_choices_formatted = [(c, f) for c, f in hfw_choices_formatted if c > STAGE_PRECISION]
        self._immersion_mode_hfw_choices[init_immersion] = (
            hfw_choices_formatted,
            choices_si_prefix,
            hfw_unit,
        )
        self.tab_data.main.ebeam.immersion.value = not init_immersion
        horizontal_fw_choices = hfw_choices(
            None, self.tab_data.main.ebeam.horizontalFoV, None
        )
        hfw_choices_formatted, choices_si_prefix = format_choices(horizontal_fw_choices)
        hfw_choices_formatted = [(c, f) for c, f in hfw_choices_formatted if c > STAGE_PRECISION]
        self._immersion_mode_hfw_choices[not init_immersion] = (
            hfw_choices_formatted,
            choices_si_prefix,
            hfw_unit,
        )
        self.tab_data.main.ebeam.immersion.value = init_immersion

    def on_evt_combobox_hfw_sb_ctrl(self, evt):
        """
        Handles the combobox event for the single beam HFW control.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetClientData(ctrl.GetSelection())
        self._on_hfw_ctrl(ctrl.GetValue(), value)

    def on_evt_txt_enter_hfw_sb_ctrl(self, evt):
        """
        Handles the text enter event for the single beam HFW control.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        ctrl_value = ctrl.GetValue()
        try:
            value = str_to_value(ctrl_value, self.tab_data.main.ebeam.horizontalFoV)
        except (ValueError, TypeError):
            value = self.main_data.user_hfw_sb.value
        # Check if the entered value is in the list of choices
        for i in range(ctrl.GetCount()):
            d = ctrl.GetClientData(i)
            if (d == value or
                (all(isinstance(v, float) for v in (value, d)) and
                 almost_equal(d, value))
               ):
                logging.debug("Setting combobox value to %s", ctrl.Items[i])
                ctrl.SetSelection(i)
                break
        # A custom value was entered
        else:
            logging.debug("No existing label found for value %s in combobox ctrl %d",
                          value, id(ctrl))
            hfw_choices_formatted, _, hfw_unit = (
                self._immersion_mode_hfw_choices[self.immersion_mode_ctrl.GetValue()]
            )
            min_hfw = min(hfw_choices_formatted, key=lambda x: x[0])[0]
            max_hfw = max(hfw_choices_formatted, key=lambda x: x[0])[0]
            # Check if the value is within the range of HFW choices
            if value < min_hfw:
                value = min_hfw
            elif value > max_hfw:
                value = max_hfw
            acc = HW_SETTINGS_CONFIG["e-beam"]["horizontalFoV"]["accuracy"]
            txt = value_to_str(value, hfw_unit, acc)
            ctrl.SetValue(txt)
        self._on_hfw_ctrl(ctrl_value, value)

    def _on_hfw_ctrl(self, label: str, choice: float):
        """
        Handle updates when the HFW control changes.
        Updates the pixel size control and the project settings data.

        :param label: The label of the HFW.
        :param choice: The new HFW value.
        """
        if choice != self.main_data.user_hfw_sb.value:
            self.main_data.user_hfw_sb.value = choice
            resolution = self.resolution_ctrl.GetClientData(
                self.resolution_ctrl.GetSelection()
            )
            # Update the pixel size control
            self.pixel_size_ctrl.SetValue(
                units.readable_str(choice / resolution[0], unit="m", sig=4)
            )
            settings_data = self.main_tab_data.project_settings_data.value
            current_project = self.main_tab_data.current_project.value
            settings_data[current_project][HFW] = label
            settings_data[current_project][PIXEL_SIZE] = self.pixel_size_ctrl.GetValue()
            self.main_tab_data.project_settings_data._set_value(
                settings_data, must_notify=True
            )

    def _create_hfw_sb_entry(self):
        """Creates the control for single beam HFW."""
        _, ctrl = self.single_beam_settings.add_combobox_control(HFW)
        self.hfw_ctrl = ctrl

        hfw_choices_formatted, choices_si_prefix, hfw_unit = (
            self._immersion_mode_hfw_choices[self.tab_data.main.ebeam.immersion.value]
        )
        # Set choices
        if choices_si_prefix:
            for choice, formatted in hfw_choices_formatted:
                ctrl.Append(
                    "%s %s" % (formatted, choices_si_prefix + hfw_unit), choice
                )
        else:
            for choice, formatted in hfw_choices_formatted:
                ctrl.Append("%s%s" % (formatted, hfw_unit), choice)
        ctrl.SetSelection(0)
        self.main_data.user_hfw_sb.value = ctrl.GetClientData(ctrl.GetSelection())
        ctrl.SetName(HFW)
        ctrl.Bind(wx.EVT_COMBOBOX, self.on_evt_combobox_hfw_sb_ctrl)
        ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_evt_txt_enter_hfw_sb_ctrl)

    def on_evt_resolution_sb_ctrl(self, evt):
        """
        Handles the event when the single beam resolution control is changed.
        Updates the pixel size control and the project settings data.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetClientData(ctrl.GetSelection())
        if value != self.main_data.user_resolution_sb.value:
            self.main_data.user_resolution_sb.value = value
            hfw = self.hfw_ctrl.GetClientData(self.hfw_ctrl.GetSelection())
            # Update the pixel size control
            self.pixel_size_ctrl.SetValue(
                units.readable_str(hfw / value[0], unit="m", sig=4)
            )
            settings_data = self.main_tab_data.project_settings_data.value
            current_project = self.main_tab_data.current_project.value
            settings_data[current_project][RESOLUTION] = ctrl.GetValue()
            settings_data[current_project][PIXEL_SIZE] = self.pixel_size_ctrl.GetValue()
            self.main_tab_data.project_settings_data._set_value(
                settings_data, must_notify=True
            )

    def _create_resolution_sb_entry(self):
        """Creates the control for single beam resolution."""
        # Format the provided choices
        _, ctrl = self.single_beam_settings.add_combobox_control(RESOLUTION)
        self.resolution_ctrl = ctrl
        res_unit = self.tab_data.main.ebeam.resolution.unit
        res_choices_formatted, choices_si_prefix = format_choices(
            self.tab_data.main.ebeam.resolution.choices
        )
        # Set choices
        if choices_si_prefix:
            for choice, formatted in res_choices_formatted:
                ctrl.Append("%s %s" % (formatted, choices_si_prefix + res_unit), choice)
        else:
            for choice, formatted in res_choices_formatted:
                ctrl.Append("%s%s" % (formatted, res_unit), choice)
        ctrl.SetSelection(0)
        self.main_data.user_resolution_sb.value = ctrl.GetClientData(
            ctrl.GetSelection()
        )
        ctrl.SetName(RESOLUTION)
        ctrl.Bind(wx.EVT_COMBOBOX, self.on_evt_resolution_sb_ctrl)

    def _create_pixel_size_sb_entry(self):
        """Creates the control for single beam pixel size."""
        _, self.pixel_size_ctrl = self.single_beam_settings.add_text_field(
            PIXEL_SIZE, readonly=True
        )
        hfw = self.hfw_ctrl.GetClientData(self.hfw_ctrl.GetSelection())
        resolution = self.resolution_ctrl.GetClientData(
            self.resolution_ctrl.GetSelection()
        )
        self.pixel_size_ctrl.SetValue(
            units.readable_str(hfw / resolution[0], unit="m", sig=4)
        )

    def on_dwell_time_sb_entry(self, evt):
        """
        Handles the event when the single beam dwell time control is changed.
        Updates the project settings data with the new dwell time value.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        if len(self.main_tab_data.current_project.value) > 0:
            settings_data = self.main_tab_data.project_settings_data.value
            settings_data[self.main_tab_data.current_project.value][
                DWELL_TIME_SINGLE_BEAM
            ] = value
            self.main_tab_data.project_settings_data._set_value(
                settings_data, must_notify=True
            )

    def _create_dwell_time_sb_entry(self):
        """Creates the control for single beam dwell time."""
        _, ctrl = self.single_beam_settings.add_float_slider(
            "Dwell time",
            value=self.tab_data.main.user_dwell_time_sb.value,
            conf=CONTROL_CONFIG[DWELL_TIME_SINGLE_BEAM],
        )
        self.dwell_time_sb_ctrl = ctrl
        ctrl.SetName(DWELL_TIME_SINGLE_BEAM)
        # The wx.EVT_SLIDER is binded to on_dwell_time_sb_entry in fastem_project_manager_panel.py

    def _reset_hfw_ctrl(self, hfw: str, immersion_mode: bool):
        """
        Resets the HFW control based on the immersion mode.

        :param hfw: The HFW label. Used to set the current selection.
        :param immersion_mode: The immersion mode.
        """
        self.hfw_ctrl.Clear()
        hfw_choices_formatted, choices_si_prefix, hfw_unit = (
            self._immersion_mode_hfw_choices[immersion_mode]
        )
        # Set labels, choices based on the immersion mode
        if choices_si_prefix:
            for choice, formatted in hfw_choices_formatted:
                self.hfw_ctrl.Append(
                    "%s %s" % (formatted, choices_si_prefix + hfw_unit), choice
                )
        else:
            for choice, formatted in hfw_choices_formatted:
                self.hfw_ctrl.Append("%s%s" % (formatted, hfw_unit), choice)
        # Based on the immersion mode the labels, choices of HFW are different
        # If the current HFW label is not in the updated control set the first
        # label as the current
        if self.hfw_ctrl.FindString(hfw, caseSensitive=True) == wx.NOT_FOUND:
            self.hfw_ctrl.SetSelection(0)
            self.main_data.user_hfw_sb.value = self.hfw_ctrl.GetClientData(
                self.hfw_ctrl.GetSelection()
            )
        else:
            self.hfw_ctrl.SetValue(hfw)
            self.main_data.user_hfw_sb.value = self.hfw_ctrl.GetClientData(
                self.hfw_ctrl.FindString(hfw)
            )

    def on_immersion_mode_sb_entry(self, evt):
        """
        Handles the event when the single beam immersion mode control is changed.
        Updates the HFW control and the project settings data.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        if len(self.main_tab_data.current_project.value) > 0:
            settings_data = self.main_tab_data.project_settings_data.value
            settings_data[self.main_tab_data.current_project.value][IMMERSION] = value
            current_hfw = self.hfw_ctrl.GetValue()
            self._reset_hfw_ctrl(current_hfw, value)
            self.main_tab_data.project_settings_data._set_value(
                settings_data, must_notify=True
            )

    def _create_immersion_mode_sb_entry(self):
        """Creates the control for single beam immersion mode."""
        _, ctrl = self.single_beam_settings.add_checkbox_control(
            IMMERSION,
            value=self.tab_data.main.ebeam.immersion.value,
            pos_col=2,
            span=(1, 1),
        )
        self.immersion_mode_ctrl = ctrl
        ctrl.SetName(IMMERSION)
        ctrl.Bind(wx.EVT_CHECKBOX, self.on_immersion_mode_sb_entry)

    def on_overvoltage_mb_event(self, evt):
        """
        Handles the event when the multi beam overvoltage control is changed.
        Updates the project settings data with the new overvoltage value.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        value = ctrl.GetValue()
        if len(self.main_tab_data.current_project.value) > 0:
            self.main_tab_data.project_settings_data.value[
                self.main_tab_data.current_project.value
            ][OVERVOLTAGE] = value
            self.tab_data.main.mppc.overVoltage.value = value

    def _create_overvoltage_mb_entry(self):
        """Creates the control for multi-beam overvoltage."""
        _, ctrl = self.multi_beam_settings.add_float_field(
            OVERVOLTAGE,
            value=self.tab_data.main.mppc.overVoltage.value,
            conf=CONTROL_CONFIG[OVERVOLTAGE],
        )
        ctrl.SetName(OVERVOLTAGE)
        ctrl.Unbind(wx.EVT_TEXT_ENTER, handler=ctrl.on_text_enter)
        ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_overvoltage_mb_event)

    def on_voltage_mb_entry(self, evt):
        """
        Handles the event when the multi beam voltage control is changed.
        Updates the project settings data with the new voltage value.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        if len(self.main_tab_data.current_project.value) > 0:
            self.main_tab_data.project_settings_data.value[
                self.main_tab_data.current_project.value
            ][VOLTAGE] = value
            self.tab_data.main.ebeam.accelVoltage.value = ctrl.GetClientData(
                ctrl.GetSelection()
            )

    def _create_voltage_mb_entry(self):
        """Creates the control for multi-beam voltage."""
        voltage = self.tab_data.main.ebeam.accelVoltage.value
        idx = CONTROL_CONFIG[VOLTAGE]["choices"].index(int(voltage))
        value = CONTROL_CONFIG[VOLTAGE]["labels"][idx]
        _, ctrl = self.multi_beam_settings.add_combobox_control(
            VOLTAGE, value=value, conf=CONTROL_CONFIG[VOLTAGE]
        )
        ctrl.SetName(VOLTAGE)
        ctrl.Bind(wx.EVT_COMBOBOX, self.on_voltage_mb_entry)

    def on_dwell_time_mb_entry(self, evt):
        """
        Handles the event when the multi beam dwell time control is changed.
        Updates the project settings data with the new dwell time value.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        if len(self.main_tab_data.current_project.value) > 0:
            settings_data = self.main_tab_data.project_settings_data.value
            settings_data[self.main_tab_data.current_project.value][
                DWELL_TIME_MULTI_BEAM
            ] = value
            self.main_tab_data.project_settings_data._set_value(
                settings_data, must_notify=True
            )

    def _create_dwell_time_mb_entry(self):
        """Creates the control for multi-beam dwell time."""
        lbl, ctrl = self.multi_beam_settings.add_float_slider(
            "Dwell Time",
            value=self.tab_data.main.user_dwell_time_mb.value,
            conf=CONTROL_CONFIG[DWELL_TIME_MULTI_BEAM],
        )
        self.dwell_time_mb_ctrl = ctrl
        # Wrap the label text because its too long
        lbl.Wrap(100)
        ctrl.SetName(DWELL_TIME_MULTI_BEAM)
        ctrl.Bind(wx.EVT_SLIDER, self.on_dwell_time_mb_entry)

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            self._initialized_after_show = True

    def _create_ctrl_entries(self):
        """Creates the controls for the single beam and multi beam settings."""
        # Create the controls for the single beam settings
        self._create_hfw_sb_entry()
        self._create_resolution_sb_entry()
        self._create_pixel_size_sb_entry()
        self._create_dwell_time_sb_entry()
        self._create_immersion_mode_sb_entry()
        # Create the controls for the multi beam settings
        # TODO: Uncomment when voltage and overvoltage are implemented
        # self._create_voltage_mb_entry()
        # self._create_overvoltage_mb_entry()
        self._create_dwell_time_mb_entry()

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 1
        else:
            return None
