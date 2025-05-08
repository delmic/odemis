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
import os
from typing import Any, Dict

import wx

from odemis import model
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.conf.data import get_hw_config
from odemis.gui.conf.util import process_setting_metadata
from odemis.gui.cont.microscope import FastEMStateController
from odemis.gui.model import VIEW_LAYOUT_DYNAMIC, VIEW_LAYOUT_ONE
from odemis.gui.util import call_in_wx_main, get_home_folder
from odemis.gui.util.conversion import sample_positions_to_layout
from odemis.util.dataio import read_json, write_json
from odemis.util.filename import make_compliant_string

# Default entry value
DEFAULT_USER = "fastem-user"
# Entries
USER_PROFILE = "User profile"
CURRENT = "Current"
VOLTAGE = "Voltage"
OVERVOLTAGE = "Overvoltage"
DWELL_TIME_SINGLE_BEAM = "Dwell time (single-beam)"
DWELL_TIME_MULTI_BEAM = "Dwell time (multi-beam)"
SAMPLE_CARRIER = "Sample carrier"
SELECTED_SCINTILLATORS = "Selected scintillators"
USER_NOTE = "User note"
IMMERSION = "Immersion mode"
RESOLUTION = "Resolution"
HFW = "HFW"
PIXEL_SIZE = "Pixel size"
# Control config for the entries
CONTROL_CONFIG = {
    USER_PROFILE: {
        "choices": [DEFAULT_USER],
    },
    CURRENT: {
        "labels": ["1 nA", "2 nA", "3 nA", "4 nA", "5 nA"],
        "choices": [1, 2, 3, 4, 5],
        "style": wx.CB_READONLY,
    },
    VOLTAGE: {
        "labels": ["2.5 kV", "4 kV", "5 kV", "10 kV"],
        "choices": [2500, 4000, 5000, 10000],
        "style": wx.CB_READONLY,
    },
    OVERVOLTAGE: {
        "key_step_min": 0.1,
        "accuracy": 3,
    },
    DWELL_TIME_SINGLE_BEAM: {},
    DWELL_TIME_MULTI_BEAM: {},
    SAMPLE_CARRIER: {
        "style": wx.CB_READONLY,
    },
    SELECTED_SCINTILLATORS: {
        "choices": [],
        "labels": [],
        "sizer_orient": wx.VERTICAL,
        "create_grid": True,
    },
    USER_NOTE: {},
}


class FastEMUserSettingsPanel(object):
    """
    FastEM user settings panel contains pressure button, e-beam button,
    panel for the user settings, selection of sample holder, scintillator, user note.

    During creation, the following controllers are created:

    FastEMStateController
      Binds the 'hardware' buttons (pressure and ebeam) to their appropriate
      Vigilant Attributes in the tab and GUI models.

    """

    def __init__(self, panel, tab_data):
        self.tab_data = tab_data
        self.main_data = tab_data.main
        self.panel = panel
        self.main_data.current_user.value = DEFAULT_USER
        self.original_user = DEFAULT_USER
        self.selected_scintillators = None
        self.user_note_timer = None
        self.user_note_ctrl = None
        self.user_profile_ctrl = None
        self.sample_carrier_ctrl = None
        self.user_profile_add_button_ctrl = None
        self.user_profile_delete_button_ctrl = None
        self.default_profile_data = None

        # Pump and ebeam state controller
        self._state_controller = FastEMStateController(self.main_data, panel)

        # Get the user profile data, update the entries control config and finally
        # setup the user settings panel for the entries
        self._init_user_profile_config()
        self._setup_user_settings_panel()
        self.main_data.is_acquiring.subscribe(self._on_is_acquiring)

    @call_in_wx_main
    def _on_is_acquiring(self, is_acquiring):
        self.panel.Enable(not is_acquiring)

    def __del__(self):
        self.write_user_profile_data()

    def _get_default_profile_data(self):
        # TODO uncomment the CURRENT entry when the development is finished
        # to support its value changes
        return {
            # CURRENT: CONTROL_CONFIG[CURRENT]["labels"][0],
            VOLTAGE: CONTROL_CONFIG[VOLTAGE]["labels"][0],
            OVERVOLTAGE: CONTROL_CONFIG[OVERVOLTAGE]["min_val"],
            DWELL_TIME_SINGLE_BEAM: CONTROL_CONFIG[DWELL_TIME_SINGLE_BEAM][
                "min_val"
            ],
            DWELL_TIME_MULTI_BEAM: CONTROL_CONFIG[DWELL_TIME_MULTI_BEAM]["min_val"],
        }

    def _init_user_profile_config(self):
        config_dir = os.path.join(get_home_folder(), ".config/odemis")
        self.user_profile_config_path = os.path.abspath(
            os.path.join(config_dir, "user_profile_config.json")
        )
        self.user_profile_data = read_json(self.user_profile_config_path)
        self._update_user_profile_control_config()
        self.default_profile_data = self._get_default_profile_data()

        if self.user_profile_data is None:
            # Create the json file and write the data for the default user
            # user_profile_data is Dict[str, Dict[str, Any]]
            # Example {"fastem-user": {"Current": "1 nA", "Voltage": "5 kV"}}
            self.user_profile_data = {}
            self.user_profile_data[DEFAULT_USER] = self.default_profile_data
        else:
            for user, data in self.user_profile_data.items():
                default_data = self.default_profile_data.copy()
                default_data.update(data)
                self.user_profile_data[user] = default_data
            if DEFAULT_USER not in self.user_profile_data:
                self.user_profile_data[DEFAULT_USER] = self.default_profile_data
        CONTROL_CONFIG[USER_PROFILE].update(
            {"choices": list(self.user_profile_data.keys())}
        )
        self.write_user_profile_data()

    def _update_user_profile_control_config(self):
        dwell_time_overview = get_hw_config(
            self.main_data.ebeam, self.main_data.hw_settings_config
        ).get("dwellTime")
        min_val, max_val, _, unit = process_setting_metadata(
            self.main_data.ebeam, self.main_data.ebeam.dwellTime, dwell_time_overview
        )
        dwell_time_overview_conf = {
            "min_val": min_val,
            "max_val": max_val,
            "scale": dwell_time_overview.get("scale", None),
            "unit": unit,
            "accuracy": dwell_time_overview.get("accuracy", 4),
        }
        dwell_time_acq = get_hw_config(
            self.main_data.multibeam, self.main_data.hw_settings_config
        ).get("dwellTime")
        min_val, max_val, _, unit = process_setting_metadata(
            self.main_data.multibeam, self.main_data.multibeam.dwellTime, dwell_time_acq
        )
        dwell_time_acq_conf = {
            "min_val": min_val,
            "max_val": max_val,
            "scale": dwell_time_acq.get("scale", None),
            "unit": unit,
            "accuracy": dwell_time_acq.get("accuracy", 4),
        }
        overvoltage = self.main_data.mppc.overVoltage
        overvoltage_conf = {
            "min_val": overvoltage.range[0],
            "max_val": overvoltage.range[1],
            "unit": overvoltage.unit,
        }
        CONTROL_CONFIG[OVERVOLTAGE].update(overvoltage_conf)
        CONTROL_CONFIG[DWELL_TIME_SINGLE_BEAM].update(dwell_time_overview_conf)
        CONTROL_CONFIG[DWELL_TIME_MULTI_BEAM].update(dwell_time_acq_conf)
        CONTROL_CONFIG[SAMPLE_CARRIER].update(
            {"choices": list(self.main_data.samples.value.keys())}
        )

    def _update_selected_scintillators_layout(self):
        current_sample = self.main_data.current_sample.value
        if current_sample:
            choices = []
            labels = []
            sample_positions = {}
            for scintillator_num, scintillator in current_sample.scintillators.items():
                choices.append(scintillator_num)
                labels.append(str(scintillator_num))
                sample_positions[scintillator_num] = scintillator.shape.position
            layout = sample_positions_to_layout(sample_positions)
            self.selected_scintillators.choices = choices
            self.selected_scintillators.labels = labels
            self.selected_scintillators.grid_layout = layout
            self.selected_scintillators.UpdateLayout()
        self.user_settings_panel.Layout()
        self.user_settings_panel.Refresh()

    def write_user_profile_data(self):
        write_json(self.user_profile_config_path, self.user_profile_data)

    def get_ctrl_values(self) -> Dict[str, Any]:
        """Get the current entries control values."""
        return {
            entry: self.user_settings_panel.FindWindowByName(entry).GetValue()
            for entry in self.default_profile_data
        }

    def _set_voltage_ctrl_value(self, ctrl, value: str) -> bool:
        idx = CONTROL_CONFIG[VOLTAGE]["labels"].index(value)
        voltage = CONTROL_CONFIG[VOLTAGE]["choices"][idx]
        if self.main_data.ebeam.accelVoltage.value != voltage:
            dlg = wx.MessageDialog(
                ctrl,
                "Do you want to change the Voltage value? "
                "If Yes, the system needs to be re-calibrated.",
                "Confirm",
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
            )
            result = dlg.ShowModal()
            dlg.Destroy()
            if result == wx.ID_YES:
                self.user_profile_data[self.main_data.current_user.value][
                    VOLTAGE
                ] = value
                self.main_data.ebeam.accelVoltage.value = voltage
            else:
                voltage = self.main_data.ebeam.accelVoltage.value
                idx = CONTROL_CONFIG[VOLTAGE]["choices"].index(voltage)
                value = CONTROL_CONFIG[VOLTAGE]["labels"][idx]
                self.user_profile_data[self.main_data.current_user.value][
                    VOLTAGE
                ] = value
                ctrl.SetValue(value)
            return True
        return False

    def _set_overvoltage_ctrl_value(self, ctrl, value: float) -> bool:
        if self.main_data.mppc.overVoltage.value != value:
            dlg = wx.MessageDialog(
                ctrl,
                "Do you want to change the Overvoltage value? "
                "If Yes, the system needs to be re-calibrated.",
                "Confirm",
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
            )
            result = dlg.ShowModal()
            dlg.Destroy()
            if result == wx.ID_YES:
                self.user_profile_data[self.main_data.current_user.value][
                    OVERVOLTAGE
                ] = value
                self.main_data.mppc.overVoltage.value = value
            else:
                value = self.main_data.mppc.overVoltage.value
                self.user_profile_data[self.main_data.current_user.value][
                    OVERVOLTAGE
                ] = value
                ctrl.SetValue(value)
            return True
        return False

    def set_ctrl_values(self):
        """Set the entries control values for the current user."""
        for entry in self.user_profile_data[DEFAULT_USER].keys():
            ctrl = self.user_settings_panel.FindWindowByName(entry)
            if ctrl:
                value = self.user_profile_data[self.main_data.current_user.value][entry]
                ctrl.SetValue(value)
                if entry == DWELL_TIME_SINGLE_BEAM:
                    self.main_data.user_dwell_time_sb.value = float(value)
                elif entry == DWELL_TIME_MULTI_BEAM:
                    self.main_data.user_dwell_time_mb.value = float(value)
                elif entry == OVERVOLTAGE:
                    self._set_overvoltage_ctrl_value(ctrl, float(value))
                elif entry == VOLTAGE:
                    self._set_voltage_ctrl_value(ctrl, value)

    def on_evt_text_enter_user_profile_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue().strip()
        value = make_compliant_string(value)
        if value and self.original_user:
            if (
                self.original_user != DEFAULT_USER
                and self.original_user in ctrl.GetStrings()
            ):
                self.main_data.current_user.value = value
                if value not in ctrl.GetStrings():
                    idx = ctrl.FindString(self.original_user)
                    ctrl.SetString(idx, value)
                    del self.user_profile_data[self.original_user]
                    self.user_profile_data[value] = self.get_ctrl_values()
                    self.write_user_profile_data()
                else:
                    self.set_ctrl_values()
                ctrl.SetValue(value)
                self.original_user = value
            else:
                ctrl.SetValue(DEFAULT_USER)
        else:
            ctrl.SetValue(DEFAULT_USER)
            self.main_data.current_user.value = DEFAULT_USER
            self.set_ctrl_values()

    def on_evt_set_focus_user_profile_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        self.original_user = ctrl.GetValue().strip()

    def on_evt_combobox_user_profile_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        if value != self.main_data.current_user.value:
            self.main_data.current_user.value = value
            self.set_ctrl_values()
            self.write_user_profile_data()

    def on_evt_text_enter_overvoltage_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        value = ctrl.GetValue()
        is_set = self._set_overvoltage_ctrl_value(ctrl, float(value))
        if is_set:
            self.write_user_profile_data()

    def on_evt_combobox_voltage_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        is_set = self._set_voltage_ctrl_value(ctrl, value)
        if is_set:
            self.write_user_profile_data()

    def on_evt_combobox_sample_carrier_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        current_sample = self.main_data.current_sample.value
        if (current_sample and current_sample.type != value) or (
            current_sample is None
        ):
            self.main_data.current_sample.value = self.main_data.samples.value[value]
            self._update_selected_scintillators_layout()
            self.sample_carrier_ctrl.Enable(False)

    def on_evt_slider_dwell_time_overview_image_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        self.user_profile_data[self.main_data.current_user.value][
            DWELL_TIME_SINGLE_BEAM
        ] = value
        self.main_data.user_dwell_time_sb.value = float(value)
        self.write_user_profile_data()

    def on_evt_slider_dwell_time_acquisition_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        self.user_profile_data[self.main_data.current_user.value][
            DWELL_TIME_SINGLE_BEAM
        ] = value
        self.main_data.user_dwell_time_mb.value = float(value)
        self.write_user_profile_data()

    def on_evt_button_selected_scintillators_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        visible_views_num = self.selected_scintillators.GetValue()
        visible_views = []
        for view in self.tab_data.views.value:
            if int(view.name.value) in visible_views_num:
                visible_views.append(view)
        self.tab_data.visible_views.value = visible_views
        if len(visible_views) == 1:
            self.tab_data.viewLayout.value = VIEW_LAYOUT_ONE
        elif len(visible_views) > 1:
            self.tab_data.viewLayout.value = VIEW_LAYOUT_DYNAMIC

    def on_evt_text_user_note_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        if self.user_note_timer.IsRunning():
            self.user_note_timer.Stop()
        self.user_note_timer.Start(1000)
        evt.Skip()

    def on_evt_timer_user_note_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        info = self.user_note_ctrl.GetValue()
        self.main_data.mppc.updateMetadata({model.MD_USER_NOTE: info})
        self.user_note_timer.Stop()

    def on_evt_add_button_user_profile_ctrl(self, evt):
        value = wx.GetTextFromUser(
            "Enter new user:", parent=self.user_profile_add_button_ctrl
        )
        value = value.strip()
        value = make_compliant_string(value)
        ctrl = self.user_profile_ctrl
        if value:
            self.main_data.current_user.value = value
            if value not in ctrl.GetStrings():
                ctrl.Append(value)
                self.user_profile_data[value] = self.get_ctrl_values()
            elif value != self.main_data.current_user.value:
                self.set_ctrl_values()
            ctrl.SetValue(value)
            self.write_user_profile_data()
        else:
            ctrl.SetValue(DEFAULT_USER)
            self.main_data.current_user.value = DEFAULT_USER
            self.set_ctrl_values()

    def on_evt_delete_button_user_profile_ctrl(self, evt):
        ctrl = self.user_profile_ctrl
        value = ctrl.GetValue()
        if value == DEFAULT_USER:
            return
        elif value in ctrl.GetStrings():
            ctrl.Delete(ctrl.GetStrings().index(value))
            del self.user_profile_data[value]
            # Set back the user profile to default user on delete
            ctrl.SetValue(DEFAULT_USER)
            self.main_data.current_user.value = DEFAULT_USER
            self.set_ctrl_values()
            self.write_user_profile_data()

    def _create_user_profile_ctrl_entry(self):
        value = DEFAULT_USER
        _, ctrl = self.user_settings_panel.add_combobox_with_buttons_control(
            USER_PROFILE, value=value, conf=CONTROL_CONFIG[USER_PROFILE]
        )
        self.user_profile_add_button_ctrl = ctrl.add_btn
        self.user_profile_delete_button_ctrl = ctrl.delete_btn
        self.user_profile_ctrl = ctrl
        ctrl.SetName(USER_PROFILE)
        ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_evt_text_enter_user_profile_ctrl)
        ctrl.Bind(wx.EVT_SET_FOCUS, self.on_evt_set_focus_user_profile_ctrl)
        self.user_profile_add_button_ctrl.Bind(
            wx.EVT_BUTTON, self.on_evt_add_button_user_profile_ctrl
        )
        self.user_profile_delete_button_ctrl.Bind(
            wx.EVT_BUTTON, self.on_evt_delete_button_user_profile_ctrl
        )
        ctrl.Bind(wx.EVT_COMBOBOX, self.on_evt_combobox_user_profile_ctrl)

    def _create_voltage_ctrl_entry(self):
        voltage = self.main_data.ebeam.accelVoltage.value
        idx = CONTROL_CONFIG[VOLTAGE]["choices"].index(voltage)
        value = CONTROL_CONFIG[VOLTAGE]["labels"][idx]
        _, ctrl = self.user_settings_panel.add_combobox_control(
            VOLTAGE, value=value, conf=CONTROL_CONFIG[VOLTAGE]
        )
        ctrl.SetName(VOLTAGE)
        ctrl.Bind(wx.EVT_COMBOBOX, self.on_evt_combobox_voltage_ctrl)

    def _create_overvoltage_ctrl_entry(self):
        _, ctrl = self.user_settings_panel.add_float_field(
            OVERVOLTAGE,
            value=self.main_data.mppc.overVoltage.value,
            conf=CONTROL_CONFIG[OVERVOLTAGE],
        )
        ctrl.SetName(OVERVOLTAGE)
        # Unbind _NumberTextCtrl on_text_enter first to make use of on_evt_text_enter_overvoltage_ctrl
        # on_evt_text_enter_overvoltage_ctrl first calls on_text_enter and then does additional things
        ctrl.Unbind(wx.EVT_TEXT_ENTER, handler=ctrl.on_text_enter)
        ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_evt_text_enter_overvoltage_ctrl)

    def _create_dwell_time_overview_ctrl_entry(self):
        value = self.user_profile_data[self.main_data.current_user.value][
            DWELL_TIME_SINGLE_BEAM
        ]
        lbl, ctrl = self.user_settings_panel.add_float_slider(
            DWELL_TIME_SINGLE_BEAM,
            value=value,
            conf=CONTROL_CONFIG[DWELL_TIME_SINGLE_BEAM],
            icon_path="icon/ico_single_beam.png"
        )
        # Wrap the label text because its too long
        lbl.Wrap(100)
        self.main_data.user_dwell_time_sb.value = float(value)
        ctrl.SetName(DWELL_TIME_SINGLE_BEAM)
        ctrl.Bind(wx.EVT_SLIDER, self.on_evt_slider_dwell_time_overview_image_ctrl)

    def _create_dwell_time_acquisition_ctrl_entry(self):
        value = self.user_profile_data[self.main_data.current_user.value][
            DWELL_TIME_MULTI_BEAM
        ]
        lbl, ctrl = self.user_settings_panel.add_float_slider(
            DWELL_TIME_MULTI_BEAM,
            value=value,
            conf=CONTROL_CONFIG[DWELL_TIME_MULTI_BEAM],
            icon_path="icon/ico_multi_beam.png"
        )
        # Wrap the label text because its too long
        lbl.Wrap(100)
        self.main_data.user_dwell_time_mb.value = float(value)
        ctrl.SetName(DWELL_TIME_MULTI_BEAM)
        ctrl.Bind(wx.EVT_SLIDER, self.on_evt_slider_dwell_time_acquisition_ctrl)

    def _create_sample_carrier_ctrl_entry(self):
        _, ctrl = self.user_settings_panel.add_combobox_control(
            SAMPLE_CARRIER, value=None, conf=CONTROL_CONFIG[SAMPLE_CARRIER]
        )
        self.sample_carrier_ctrl = ctrl
        ctrl.SetName(SAMPLE_CARRIER)
        ctrl.Bind(wx.EVT_COMBOBOX, self.on_evt_combobox_sample_carrier_ctrl)

    def _create_selected_scintillators_ctrl_entry(self):
        lbl, ctrl = self.user_settings_panel.add_toggle_control(
            SELECTED_SCINTILLATORS,
            values=CONTROL_CONFIG[SELECTED_SCINTILLATORS]["choices"],
            conf=CONTROL_CONFIG[SELECTED_SCINTILLATORS],
        )
        self.selected_scintillators = ctrl
        # Wrap the label text because its too long
        lbl.Wrap(100)
        ctrl.SetName(SELECTED_SCINTILLATORS)
        ctrl.Bind(wx.EVT_BUTTON, self.on_evt_button_selected_scintillators_ctrl)

    def _create_user_note_ctrl_entry(self):
        _, ctrl = self.user_settings_panel.add_text_field(
            USER_NOTE, value="Acquisition details: ", multiline=True
        )
        ctrl.MinSize = (-1, 60)
        self.user_note_ctrl = ctrl
        self.user_note_timer = wx.Timer(ctrl)
        ctrl.SetName(USER_NOTE)
        ctrl.Bind(wx.EVT_TEXT, self.on_evt_text_user_note_ctrl)
        ctrl.Bind(wx.EVT_TIMER, self.on_evt_timer_user_note_ctrl)

    def _create_ctrl_entries(self):
        # Create entries in the correct order
        self._create_user_profile_ctrl_entry()
        self._create_voltage_ctrl_entry()
        self._create_overvoltage_ctrl_entry()
        self._create_dwell_time_overview_ctrl_entry()
        self._create_dwell_time_acquisition_ctrl_entry()
        self.user_settings_panel.add_divider()
        self._create_sample_carrier_ctrl_entry()
        self.user_settings_panel.add_divider()
        self._create_selected_scintillators_ctrl_entry()
        self.user_settings_panel.add_divider()
        self._create_user_note_ctrl_entry()

    def _setup_user_settings_panel(self):
        self.user_settings_panel = SettingsPanel(
            self.panel.user_settings_panel, size=(300, 600)
        )

        # Create the user profile control entries
        self._create_ctrl_entries()
        self.user_settings_panel.Layout()
