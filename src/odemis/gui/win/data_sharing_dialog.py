# -*- coding: utf-8 -*-
"""
Copyright © 2026 Delmic

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

from odemis.util.datacollector import DataCollector


class DataSharingDialog(wx.Dialog):
    """
    Dialog to choose the data sharing settings for the current session.
    It has no button: closing the window saves the selection (in memory only).
    """

    WIDTH = 940  # px, total width of the window
    MARGIN = 32  # px, margin all around the content

    def __init__(self, parent: wx.Window, data_collector: DataCollector, role: str):
        """
        :param parent: The parent window.
        :param data_collector: The data collector, whose temporary consent is set on close.
        :param role: The microscope role, used to show the examples relevant to the system.
        """
        super().__init__(parent, title="Data sharing")
        self._data_collector = data_collector
        default = data_collector.get_consent_default()
        current = data_collector.get_consent()

        # (consent value, label) of the two choices: the default one, and the opposite one
        if default:
            choices = [(True, "Enabled (default set by the organization)"),
                       (False, "Disabled for the session (until closing the Odemis window)")]
        else:
            choices = [(False, "Disabled (default set by the organization)"),
                       (True, "Enabled for the session "
                              "(100% of the data collected until closing the Odemis window)")]

        # Content in an inner sizer, to have the margin all around
        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="Acquisition analytics")
        title.SetFont(title.GetFont().Bold())
        sizer.Add(title, flag=wx.BOTTOM, border=10)

        if role in ("meteor", "mimas"):
            example = "SEM and FM overview images, Z-stacks, and feature placement and status"
        else:
            example = "parabolic-mirror images before, during, and after automatic and manual alignment"
        desc = wx.StaticText(
            self,
            label="Selected acquisition-related data and interaction logs are collected. "
                  f"For example: {example}. "
                  "By default, only a 10% random sample of relevant events is included; "
                  "enabling it for the session collects all of them. "
                  "Logs may incidentally contain a user-defined file path, but personal identifiers "
                  "(e.g. usernames) are stripped from such paths before transmission. Data is stored "
                  "securely on EU servers and never shared with third parties.")
        desc.Wrap(self.WIDTH - 2 * self.MARGIN)
        sizer.Add(desc, flag=wx.BOTTOM, border=10)

        self._checkboxes = []  # list of (consent value, wx.CheckBox)
        for value, label in choices:
            cb = wx.CheckBox(self, label=label)
            cb.SetValue(value == current)
            cb.Bind(wx.EVT_CHECKBOX, self._on_checkbox)
            sizer.Add(cb, flag=wx.BOTTOM, border=10)
            self._checkboxes.append((value, cb))

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(sizer, proportion=1, flag=wx.EXPAND | wx.ALL, border=self.MARGIN)
        self.SetSizer(main_sizer)
        self.SetMinSize((self.WIDTH, -1))
        self.Fit()
        self.CentreOnParent()
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _on_checkbox(self, evt: wx.CommandEvent) -> None:
        """Make the checkboxes behave as radio buttons: exactly one is always selected."""
        clicked = evt.GetEventObject()
        for _, cb in self._checkboxes:
            cb.SetValue(cb is clicked)

    def get_selection(self) -> bool:
        """
        :returns: the consent value of the selected checkbox
        """
        for value, cb in self._checkboxes:
            if cb.GetValue():
                return value
        return self._data_collector.get_consent()

    def _on_close(self, evt: wx.CloseEvent) -> None:
        """Save the selection when the window is closed."""
        self._data_collector.set_temporary_consent(self.get_selection())
        evt.Skip()  # default handling: close the window
