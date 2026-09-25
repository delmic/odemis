# -*- coding: utf-8 -*-
"""
Created on 1 April 2025

@author: Patrick Cleeve, Alexéy Ilyushkin

Copyright © 2025-2026 Patrick Cleeve, Alexéy Ilyushkin, Delmic

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
from odemis import gui, model
from odemis.acq.milling.patterns import (
    CorrelationPatternParameters,
    NotchPatternParameters,
    RulerPatternParameters,
    WaffleTrenchPatternParameters,
)
from odemis.acq.milling.tasks import  MillingTaskSettings
from odemis.gui.comp.text import IntegerTextCtrl, UnitFloatCtrl
from odemis.gui.comp.combo import ComboBox
from odemis.gui.layout import theme

class MillingTaskPanel(wx.Panel):
    """Panel for Milling Settings"""

    def __init__(self, parent, task: MillingTaskSettings):
        super().__init__(parent=parent, name=task.name)
        self._parent = parent
        self.SetForegroundColour(theme.text_edit)
        self.SetBackgroundColour(theme.background)

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.SetBackgroundColour(theme.background)
        self._panel.SetForegroundColour(theme.field_foreground)
        self._panel.SetFont(self.GetFont())

        self.gb_sizer = wx.GridBagSizer()
        self._panel.SetSizer(self.gb_sizer)

        self.main_sizer.Add(self._panel, 1, wx.ALL | wx.EXPAND, 5)

        self.num_rows = 0
        self.task = task

        # header
        title = self._add_side_label(task.name)
        font = title.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        font.SetPointSize(font.GetPointSize() + 1)
        title.SetFont(font)
        self.num_rows += 1

        # map of control fields
        self.ctrl_dict = {}

        CONFIG = {
            "current": {"label": "Current", "accuracy": 2, "unit": "A"},
            "align": {"label": "Align at milling current"},
            "mode": {"label": "Milling mode"},
            "width": {"label": "Width", "accuracy": 2, "unit": "m"},
            "height": {"label": "Height", "accuracy": 2, "unit": "m"},
            "marker_length": {"label": "Marker length", "accuracy": 2, "unit": "m"},
            "top_width": {"label": "Top width", "accuracy": 2, "unit": "m"},
            "top_height": {"label": "Top height", "accuracy": 2, "unit": "m"},
            "bottom_width": {"label": "Bottom width", "accuracy": 2, "unit": "m"},
            "bottom_height": {"label": "Bottom height", "accuracy": 2, "unit": "m"},
            "depth": {"label": "Depth", "accuracy": 2, "unit": "m"},
            "spacing": {"label": "Spacing", "accuracy": 2, "unit": "m"},
            "num_notches": {"label": "Notches"},
            "gap": {"label": "Gap", "accuracy": 2, "unit": "m"},
            "thickness": {"label": "Thickness", "accuracy": 2, "unit": "m"},
            "offset": {
                "label": "Loop offset", "accuracy": 2, "unit": "m",
                "key_step_min": 1e-6,
            },
            "mirrored": {"label": "Mirror"},
            "spot_size_correction": {
                "label": "Spot size correction",
                "accuracy": 2,
                "unit": "m",
                "key_step_min": 1e-6,
                "tooltip": (
                    "Total measured excess in the milled opening size.\n"
                    "The value is subtracted from each dimension sent to the microscope.\n\n"
                    "Solid: desired opening\n"
                    "Dashed: estimated uncorrected opening\n"
                    "Shaded band: measured excess"
                ),
            },
        }

        unsupported_parameters = ["name", "rotation",
                                  "center", "channel",
                                  "field_of_view", "voltage",
                                  "rate", "dwell_time"]

        for param in vars(task.milling):
            if param in unsupported_parameters:
                continue

            conf = CONFIG.get(param, {}).copy()
            label = conf.pop("label", param)

            val = getattr(task.milling, param)
            self._add_value_field(label, val, conf, param=param)

        pattern = task.patterns[0]
        self.pattern_parameters = []
        if isinstance(pattern, RulerPatternParameters):
            CONFIG["width"]["tooltip"] = "Length of the longest notches (even-numbered). Odd-numbered notches are 3/4 as long."
            CONFIG["height"]["tooltip"] = "Overall ruler length, including the first and last notches."
            CONFIG["spacing"]["tooltip"] = "Gap between the inner edges of the two rulers."
            CONFIG["num_notches"]["tooltip"] = "Notches on each side, starting at zero at the bottom and alternating long and short."
        elif isinstance(pattern, CorrelationPatternParameters):
            CONFIG["width"]["tooltip"] = "Overall width of the five-marker correlation pattern."
            CONFIG["height"]["tooltip"] = "Overall height of the five-marker correlation pattern."
            CONFIG["marker_length"]["tooltip"] = "Length of each bar forming a marker."
            CONFIG["thickness"]["tooltip"] = "Thickness of each bar forming a marker."
        elif isinstance(pattern, NotchPatternParameters):
            CONFIG["width"]["tooltip"] = "Overall width of the right-facing loop."
            CONFIG["height"]["tooltip"] = "Overall height including both whiskers."
            CONFIG["gap"]["tooltip"] = "Clear gap between the two horizontal segments."
            CONFIG["thickness"]["tooltip"] = "Thickness of all five segments."
            CONFIG["offset"]["tooltip"] = "Vertical loop offset from center; positive values move it upward."
            CONFIG["mirrored"]["tooltip"] = "Mirror the notch horizontally so the loop faces left."
        elif isinstance(pattern, WaffleTrenchPatternParameters):
            CONFIG["spacing"]["tooltip"] = "Clear distance between the top and bottom rectangles."

        for param in vars(pattern):

            if param in unsupported_parameters:
                continue

            conf = CONFIG.get(param, {}).copy()
            label = conf.pop("label", param)

            val = getattr(pattern, param)
            self._add_value_field(label, val, conf, param=param)
            if param in self.ctrl_dict:
                self.pattern_parameters.append(param)

        # Fit sizer
        self.main_sizer.AddSpacer(5)
        self.SetSizerAndFit(self.main_sizer)
        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Layout()
        self._parent.Refresh()

    def _add_value_field(self, label, val, conf, param: str):
        """Add a value field to the panel (label, ctrl)"""
        tooltip = conf.pop("tooltip", None)
        lbl_ctrl = self._add_side_label(label, tooltip=tooltip)
        value_ctrl = self._add_value_ctrl(val, conf)

        if value_ctrl is None:
            logging.debug(f"Unsupported parameter: {param}, {val}")
            return

        self.ctrl_dict[param] = value_ctrl
        if tooltip:
            value_ctrl.SetToolTip(tooltip)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                        flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                        border=5)
        # row height for milling pattern propeties controls
        row_height = 18
        # column width for milling pattern properties controls
        min_col_width = 120
        self.gb_sizer.SetItemMinSize(value_ctrl, min_col_width, row_height)
        self.gb_sizer.SetItemMinSize(lbl_ctrl, min_col_width, row_height)

        value_ctrl.SetForegroundColour(theme.text_edit)
        value_ctrl.SetBackgroundColour(theme.background)
        if tooltip:
            value_ctrl.SetToolTip(tooltip)
        self.num_rows += 1

    def _add_value_ctrl(self, val, conf):
        """Add a control for a value"""
        value_ctrl = None
        if isinstance(val, model.StringEnumerated):
            value_ctrl = ComboBox(self._panel, value=val.value,
                        choices=val.choices, style=wx.CB_READONLY | wx.BORDER_NONE)
        if isinstance(val, model.FloatContinuous):
            value_ctrl = UnitFloatCtrl(self._panel, value=val.value,
                                        style=wx.NO_BORDER, **conf)
        if isinstance(val, model.IntContinuous):
            value_ctrl = IntegerTextCtrl(self._panel, value=val.value,
                                        min_val=val.range[0], max_val=val.range[1],
                                        key_step=1, style=wx.NO_BORDER, **conf)
        if isinstance(val, model.BooleanVA):
            value_ctrl = wx.CheckBox(self._panel, **conf)
            value_ctrl.SetValue(val.value)

        return value_ctrl

    def _add_side_label(self, label_text, tooltip=None):
        """ Add a text label to the control grid

        This method should only be called from other methods that add control to the control grid

        :param label_text: (str)
        :return: (wx.StaticText)

        """

        lbl_ctrl = wx.StaticText(self._panel, -1, label_text)
        if tooltip:
            lbl_ctrl.SetToolTip(tooltip)

        self.gb_sizer.Add(lbl_ctrl, (self.num_rows, 0),
                        flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        return lbl_ctrl

        # ref: add_setting_entry

    def _on_size(self, event):
        """ Handle the wx.EVT_SIZE event for the Expander class """
        self.SetSize((self._parent.GetSize().x, -1))
        self.Layout()
        self.Refresh()
        event.Skip()

    def collapse(self, collapse):
        """ Collapses or expands the pane window """

        if self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse

        # Call after is used, so the fit will occur after everything has been hidden or shown
        # wx.CallAfter(self.Parent.fit_streams)

        self.Thaw()

    # GUI events: update the stream when the user changes the values

    def on_visibility_btn(self, evt):
        # generate EVT_STREAM_VISIBLE
        return
