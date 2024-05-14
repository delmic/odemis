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
from decorator import decorator
import logging
import os
from typing import Dict, Any
import wx

from odemis import gui
from odemis.gui.cont.microscope import FastEMStateController
from odemis.gui.util import call_in_wx_main, get_home_folder
from odemis.gui import FG_COLOUR_MAIN, BG_COLOUR_MAIN
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.comp.slider import UnitFloatSlider, UnitIntegerSlider, Slider
from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.text import UnitFloatCtrl, UnitIntegerCtrl
from odemis.gui.comp.spinner import UnitFloatSpinner, UnitIntegerSpinner
from odemis.gui.conf.data import get_hw_config
from odemis.gui.conf.util import process_setting_metadata
from odemis.util import read_json, write_json

# Default entry value
DEFAULT_USER = "default"
# Entries
USER_PROFILE = "User profile"
CURRENT = "Current"
VOLTAGE = "Voltage"
OVERVOLTAGE = "Overvoltage"
DWELL_TIME_OVERVIEW_IMAGE = "Dwell time (overview image)"
DWELL_TIME_ACQUISITION = "Dwell time (acquisition)"
SCINTILLATOR_HOLDER = "Scintillator holder"
ADDITIONAL_INFO = "Additional info"
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
        "labels": ["5 kV", "10 kV", "15 kV", "20 kV"],
        "choices": [5, 10, 15, 20],
        "style": wx.CB_READONLY,
    },
    OVERVOLTAGE: {
        "min_val": 0.0,
        "max_val": 50.0,
        "key_step_min": 1.0,
        "unit": "V",
        "accuracy": 3,
    },
    DWELL_TIME_OVERVIEW_IMAGE: {},
    DWELL_TIME_ACQUISITION: {},
    SCINTILLATOR_HOLDER: {
        "style": wx.CB_READONLY,
    },
    ADDITIONAL_INFO: {},
}


@decorator
def control_bookkeeper(f, self, *args, **kwargs):
    """Clear the default message, if needed, and advance the row count"""
    result = f(self, *args, **kwargs)

    # This makes the 2nd column's width variable
    if not self.gb_sizer.IsColGrowable(1):
        self.gb_sizer.AddGrowableCol(1)

    self.Layout()
    self.num_rows += 1
    return result


class UserSettingsPanel(wx.Panel):
    """The UserSettingsPanel class.

    The UserSettingsPanel consists of the following widgets:

        UserSettingsPanel
            BoxSizer
                Panel
                    BoxSizer
                        GridBagSizer

    Additional controls can be added to the GridBagSizer.

    """

    def __init__(
        self,
        parent,
        wid=wx.ID_ANY,
        pos=wx.DefaultPosition,
        size=wx.DefaultSize,
        style=wx.CP_DEFAULT_STYLE,
        name="UserSettingsPanel",
    ):
        wx.Panel.__init__(self, parent, wid, pos, size, style, name)

        # Appearance
        self.SetBackgroundColour(BG_COLOUR_MAIN)
        self.SetForegroundColour(FG_COLOUR_MAIN)

        # Child widgets
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        self._panel = None

        self.gb_sizer = wx.GridBagSizer()

        # Counter that keeps track of the number of rows containing controls inside this panel
        self.num_rows = 0

        self._create_controls()

    def _create_controls(self):
        """Set up the basic structure for the controls that are going to be used"""

        # Create the control panel
        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)

        # Add a simple sizer so we can create padding for the panel
        border_sizer = wx.BoxSizer(wx.HORIZONTAL)
        border_sizer.Add(self.gb_sizer, border=5, flag=wx.ALL | wx.EXPAND, proportion=1)

        self._panel.SetSizer(border_sizer)

        self._panel.SetBackgroundColour(BG_COLOUR_MAIN)
        self._panel.SetForegroundColour(FG_COLOUR_MAIN)
        self._panel.SetFont(self.GetFont())

        self._panel.Show()

        self.main_sizer.Add(self._panel, 0, wx.EXPAND)

    def Layout(self, *args, **kwargs):
        """Layout the UserSettingsPanel."""

        if not self._panel or not self.main_sizer:
            return False  # we need to complete the creation first!

        oursz = self.GetSize()

        # move & resize the button and the static line
        self.main_sizer.SetDimension(
            0, 0, oursz.GetWidth(), self.main_sizer.GetMinSize().GetHeight()
        )
        self.main_sizer.Layout()

        return True

    def OnSize(self, event):
        """Handles the wx.EVT_SIZE event for UserSettingsPanel"""
        self.Layout()
        event.Skip()

    # User Setting Control Addition Methods

    def _add_side_label(self, label_text, tooltip=None):
        """Add a text label to the control grid

        This method should only be called from other methods that add control to the control grid

        :param label_text: (str)
        :return: (wx.StaticText)

        """

        lbl_ctrl = wx.StaticText(self._panel, -1, label_text)
        if tooltip:
            lbl_ctrl.SetToolTip(tooltip)

        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 0),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        return lbl_ctrl

    def Destroy(self):
        super().Destroy()

    @control_bookkeeper
    def add_radio_control(self, label_text, value=None, conf=None):
        """Add a series of radio buttons to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = GraphicalRadioButtonControl(
            self._panel, -1, style=wx.NO_BORDER, **conf
        )
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            span=(1, 2),
            flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )

        if value is not None:
            value_ctrl.SetValue(value)

        return lbl_ctrl, value_ctrl

    def _add_slider(self, klass, label_text, value, conf):
        """Add a slider of type 'klass' to the user settings panel"""
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = klass(self._panel, value=value, **conf)
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            span=(1, 2),
            flag=wx.EXPAND | wx.ALL,
            border=5,
        )

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_slider(self, label_text, value=None, conf=None):
        """Add a slider to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(Slider, label_text, value, conf)

    @control_bookkeeper
    def add_integer_slider(self, label_text, value=None, conf=None):
        """Add an integer value slider to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(UnitIntegerSlider, label_text, value, conf)

    @control_bookkeeper
    def add_float_slider(self, label_text, value=None, conf=None):
        """Add a float value slider to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(UnitFloatSlider, label_text, value, conf)

    @control_bookkeeper
    def add_int_field(self, label_text, value=None, conf=None):
        """Add an integer value field to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """

        return self._add_num_field(UnitIntegerCtrl, label_text, value, conf)

    @control_bookkeeper
    def add_float_field(self, label_text, value=None, conf=None):
        """Add a float value field to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """

        return self._add_num_field(UnitFloatCtrl, label_text, value, conf)

    def _add_num_field(self, klass, label_text, value, conf):
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = klass(self._panel, value=value, style=wx.NO_BORDER, **conf)
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_combobox_control(self, label_text, value=None, conf=None):
        """Add a combo box to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display *NOT USED ATM*
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        cbstyle = wx.NO_BORDER | wx.TE_PROCESS_ENTER | conf.pop("style", 0)
        value_ctrl = ComboBox(
            self._panel, wx.ID_ANY, pos=(0, 0), size=(-1, 16), style=cbstyle, **conf
        )

        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            span=(1, 2),
            flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL,
            border=5,
        )

        if value is not None:
            value_ctrl.SetValue(str(value))

        return lbl_ctrl, value_ctrl

    def _add_spinner(self, klass, label_text, value, conf, *args):
        """Add a spinner of type 'klass' to the user settings panel"""
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = klass(
            self._panel,
            id=wx.ID_ANY,
            pos=(0, 0),
            size=(5, 16),
            value=value,
            *args,
            **conf,
        )
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            span=(1, 2),
            flag=wx.EXPAND | wx.ALL,
            border=5,
        )

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_float_spinner(self, label_text, value="", conf=None):
        """Add a float value spinner to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_spinner(UnitFloatSpinner, label_text, value, conf)

    @control_bookkeeper
    def add_integer_spinner(self, label_text, value="", conf=None):
        """Add a integer value spinner to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_spinner(UnitIntegerSpinner, label_text, value, conf)

    @control_bookkeeper
    def add_checkbox_control(self, label_text, value=True, conf=None):
        """Add a checkbox to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (bool) Value to display (True == checked)
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        # wx.ALIGN_RIGHT has the effect of only highlighting the box on hover,
        # which makes it less ugly with Ubuntu
        value_ctrl = wx.CheckBox(
            self._panel, wx.ID_ANY, style=wx.ALIGN_RIGHT | wx.NO_BORDER, **conf
        )
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            span=(1, 2),
            flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.TOP | wx.BOTTOM,
            border=5,
        )
        value_ctrl.SetValue(value)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_text_field(self, label_text, value=None, readonly=False, multiline=False):
        """Add a label and text control to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or str) Value to display
        :param readonly: (boolean) Whether the value can be changed by the user
        :param multiline: (boolean) Whether text control style should be multiline

        :return: (Ctrl, Ctrl) Label and text control

        """

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = wx.TextCtrl(
            self._panel,
            value=str(value or ""),
            style=wx.TE_PROCESS_ENTER
            | wx.BORDER_NONE
            | (wx.TE_READONLY if readonly else 0)
            | (wx.TE_MULTILINE if multiline else 0),
        )
        value_ctrl.MinSize = (-1, value_ctrl.BestSize[1])
        if readonly:
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
        else:
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_dir_picker(self, label_text, value=None):
        """Add a label and directory control to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or str) Value to display

        :return: (Ctrl, Ctrl) Label and text control

        """

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = wx.DirPickerCtrl(
            self._panel, path=str(value or ""), pos=(0, 0), size=(-1, 16)
        )
        value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
        value_ctrl.SetBackgroundColour(value_ctrl.Parent.BackgroundColour)
        self.gb_sizer.Add(
            value_ctrl, (self.num_rows, 1), flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL
        )

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_divider(self):
        """Add a dividing line to the user settings panel"""
        line_ctrl = wx.StaticLine(self._panel, size=(-1, 1))
        line_ctrl.SetBackgroundColour(gui.BG_COLOUR_SEPARATOR)
        self.gb_sizer.Add(
            line_ctrl,
            (self.num_rows, 0),
            span=(1, 3),
            flag=wx.ALL | wx.EXPAND,
            border=5,
        )

    # END User Setting Control Addition Methods


class FastEMUserSettingsPanel(object):
    """
    FastEM user settings panel contains pressure button, e-beam button,
    panel to select the scintillators and the user seetings panel.

    During creation, the following controllers are created:

    FastEMStateController
      Binds the 'hardware' buttons (pressure and ebeam) to their appropriate
      Vigilant Attributes in the tab and GUI models.

    """

    def __init__(self, panel, main_data):
        self.main_data = main_data
        self.panel = panel
        self.current_user = DEFAULT_USER

        self._create_state_controller()
        # Create the panel to select the scintillators
        self._create_selection_panel()

        # Get the user profile data, update the entries control config and finally
        # setup the user settings panel for the entries
        self._init_user_profile_config()
        self._update_user_profile_control_config()
        self._setup_user_settings_panel()

    def _create_selection_panel(self):
        self.panel.selection_panel.create_controls(self.main_data.scintillator_layout)
        for btn in self.panel.selection_panel.buttons.keys():
            btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_selection_button)

    def _create_state_controller(self):
        self._state_controller = FastEMStateController(self.main_data, self.panel)
        self.main_data.is_acquiring.subscribe(self._on_is_acquiring)

    def _on_selection_button(self, evt):
        btn = evt.GetEventObject()
        num = self.panel.selection_panel.buttons.get(btn)
        if btn.GetValue():
            btn.SetBackgroundColour(wx.GREEN)
            if num not in self.main_data.active_scintillators.value:
                self.main_data.active_scintillators.value.append(num)
            else:
                logging.warning("Scintillator %s has already been selected.", num)
        else:
            btn.SetBackgroundColour(gui.FG_COLOUR_BUTTON)
            if num in self.main_data.active_scintillators.value:
                self.main_data.active_scintillators.value.remove(num)
            else:
                logging.warning(
                    "Scintillator %s not found in list of active scintillators.", num
                )

    @call_in_wx_main
    def _on_is_acquiring(self, is_acquiring):
        self.panel.Enable(not is_acquiring)

    def _get_default_profile_data(self):
        # TODO uncomment the VOLTAGE entry when the development is finished
        # to support its value changes
        return {
            CURRENT: CONTROL_CONFIG[CURRENT]["labels"][0],
            # VOLTAGE: CONTROL_CONFIG[VOLTAGE]["labels"][0],
            OVERVOLTAGE: CONTROL_CONFIG[OVERVOLTAGE]["min_val"],
            DWELL_TIME_OVERVIEW_IMAGE: CONTROL_CONFIG[DWELL_TIME_OVERVIEW_IMAGE][
                "min_val"
            ],
            DWELL_TIME_ACQUISITION: CONTROL_CONFIG[DWELL_TIME_ACQUISITION]["min_val"],
        }

    def _init_user_profile_config(self):
        config_dir = os.path.join(get_home_folder(), ".config/odemis")
        self.user_profile_config_path = os.path.abspath(
            os.path.join(config_dir, "user_profile_config.json")
        )
        self.user_profile_data = read_json(self.user_profile_config_path)

        if self.user_profile_data is None:
            # Create the json file and write the data for the default user
            # user_profile_data is Dict[str, Dict[str, Any]]
            # Example {"default": {"Current": "1 nA", "Voltage": "5 kV"}}
            self.user_profile_data = {}
            self.user_profile_data[DEFAULT_USER] = {}
            self._update_user_profile_control_config()
            self.user_profile_data[DEFAULT_USER] = self._get_default_profile_data()
            self.write_user_profile_data()

    def _update_user_profile_control_config(self):
        ebeam_conf = get_hw_config(
            self.main_data.ebeam, self.main_data.hw_settings_config
        ).get("dwellTime")
        min_val, max_val, _, unit = process_setting_metadata(
            self.main_data.ebeam, self.main_data.ebeam.dwellTime, ebeam_conf
        )
        dwell_time_conf = {
            "min_val": min_val,
            "max_val": max_val,
            "scale": ebeam_conf.get("scale", None),
            "unit": unit,
            "accuracy": ebeam_conf.get("accuracy", 4),
        }
        CONTROL_CONFIG[DWELL_TIME_OVERVIEW_IMAGE].update(dwell_time_conf)
        CONTROL_CONFIG[DWELL_TIME_ACQUISITION].update(dwell_time_conf)
        CONTROL_CONFIG[USER_PROFILE].update(
            {"choices": list(self.user_profile_data.keys())}
        )
        CONTROL_CONFIG[SCINTILLATOR_HOLDER] = {
            "choices": list(self.main_data.samples.keys())
        }

    def write_user_profile_data(self):
        write_json(self.user_profile_config_path, self.user_profile_data)

    def get_entry_control_values(self) -> Dict[str, Any]:
        """Get the current entry control values."""
        return {
            entry: self.user_settings_panel.FindWindowByName(entry).GetValue()
            for entry in self.user_profile_data[DEFAULT_USER].keys()
        }

    def set_entry_control_values(self):
        """Set the entry control values for the current user."""
        for entry in self.user_profile_data[DEFAULT_USER].keys():
            ctrl = self.user_settings_panel.FindWindowByName(entry)
            if ctrl:
                ctrl.SetValue(self.user_profile_data[self.current_user][entry])

    def on_control_event(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return
        update_data = False
        entry = ctrl.GetName()

        if evt.GetEventType() == wx.EVT_TEXT_ENTER.typeId:
            if entry == USER_PROFILE:
                value = ctrl.GetValue().strip().lower()
                if value and value not in ctrl.GetStrings():
                    ctrl.Append(value)
                    ctrl.SetValue(value)
                    self.current_user = value
                    self.user_profile_data[self.current_user] = (
                        self.get_entry_control_values()
                    )
                    update_data = True
            elif entry == OVERVOLTAGE:
                # Call on_text_enter explicitly as it is not binded
                ctrl.on_text_enter(evt)
                value = ctrl.GetValue()
                self.user_profile_data[self.current_user][entry] = value
                update_data = True
        elif evt.GetEventType() == wx.EVT_COMBOBOX.typeId:
            value = ctrl.GetValue()
            if entry == USER_PROFILE:
                self.current_user = value
                self.set_entry_control_values()
                update_data = True
            elif entry == SCINTILLATOR_HOLDER:
                self.main_data.current_sample = value
            elif entry in [VOLTAGE, CURRENT]:
                self.user_profile_data[self.current_user][entry] = value
                update_data = True
        elif evt.GetEventType() == wx.EVT_SLIDER.typeId:
            value = ctrl.GetValue()
            self.user_profile_data[self.current_user][entry] = value
            update_data = True
        elif evt.GetEventType() == wx.EVT_CHAR.typeId:
            keycode = evt.GetKeyCode()
            if entry == USER_PROFILE and keycode == wx.WXK_DELETE:
                value = ctrl.GetValue()
                if value == DEFAULT_USER:
                    return
                elif value in ctrl.GetStrings():
                    ctrl.Delete(ctrl.GetStrings().index(value))
                    del self.user_profile_data[value]
                    # Set back the user profile to default user on delete
                    ctrl.SetValue(DEFAULT_USER)
                    self.current_user = DEFAULT_USER
                    self.set_entry_control_values()
                    update_data = True
            else:
                evt.Skip()

        if update_data:
            self.write_user_profile_data()

    def _bind_user_profile_control_events(self, ctrl, entry):
        if entry == USER_PROFILE:
            ctrl.Bind(wx.EVT_CHAR, self.on_control_event)
            ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_control_event)
        if entry in [USER_PROFILE, CURRENT, VOLTAGE, SCINTILLATOR_HOLDER]:
            ctrl.Bind(wx.EVT_COMBOBOX, self.on_control_event)
        if entry in [DWELL_TIME_OVERVIEW_IMAGE, DWELL_TIME_ACQUISITION]:
            ctrl.Bind(wx.EVT_SLIDER, self.on_control_event)
        if entry == OVERVOLTAGE:
            ctrl.Unbind(wx.EVT_TEXT_ENTER, handler=ctrl.on_text_enter)
            ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_control_event)

    def _create_user_profile_control_entries(self):
        # TODO uncomment the VOLTAGE entry when the development is finished
        # to support its value changes
        control_definitions = [
            (USER_PROFILE, CONTROL_CONFIG[USER_PROFILE]),
            (CURRENT, CONTROL_CONFIG[CURRENT]),
            # (VOLTAGE, CONTROL_CONFIG[VOLTAGE]),
            (OVERVOLTAGE, CONTROL_CONFIG[OVERVOLTAGE]),
            (DWELL_TIME_OVERVIEW_IMAGE, CONTROL_CONFIG[DWELL_TIME_OVERVIEW_IMAGE]),
            (DWELL_TIME_ACQUISITION, CONTROL_CONFIG[DWELL_TIME_ACQUISITION]),
            (SCINTILLATOR_HOLDER, CONTROL_CONFIG[SCINTILLATOR_HOLDER]),
            (ADDITIONAL_INFO, CONTROL_CONFIG[ADDITIONAL_INFO]),
        ]

        for entry, conf in control_definitions:
            if entry == OVERVOLTAGE:
                _, ctrl = self.user_settings_panel.add_float_field(
                    entry,
                    value=self.user_profile_data[self.current_user][entry],
                    conf=conf,
                )
            elif entry in [DWELL_TIME_OVERVIEW_IMAGE, DWELL_TIME_ACQUISITION]:
                lbl, ctrl = self.user_settings_panel.add_float_slider(
                    entry,
                    value=self.user_profile_data[self.current_user][entry],
                    conf=conf,
                )
                # Wrap the label text because its too long
                lbl.Wrap(100)
            elif entry in [USER_PROFILE, CURRENT, VOLTAGE, SCINTILLATOR_HOLDER]:
                if entry == USER_PROFILE:
                    value = DEFAULT_USER
                elif entry == SCINTILLATOR_HOLDER:
                    self.user_settings_panel.add_divider()
                    value = CONTROL_CONFIG[SCINTILLATOR_HOLDER]["choices"][0]
                else:
                    value = self.user_profile_data[self.current_user][entry]
                _, ctrl = self.user_settings_panel.add_combobox_control(
                    entry, value=value, conf=conf
                )
            elif entry == ADDITIONAL_INFO:
                self.user_settings_panel.add_divider()
                _, ctrl = self.user_settings_panel.add_text_field(entry, multiline=True)

            ctrl.SetName(entry)
            self._bind_user_profile_control_events(ctrl, entry)

    def _setup_user_settings_panel(self):
        self.user_settings_panel = UserSettingsPanel(
            self.panel.user_settings_panel, size=(300, 400)
        )

        # Create the user profile control entries
        self._create_user_profile_control_entries()
        self.user_settings_panel.Layout()
