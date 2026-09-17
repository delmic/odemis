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

from odemis.gui import img
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.sizers import vbox


class ProgressDialog(wx.Dialog):
    """Provide the progress dialog layout used for long-running operations.

    Subclasses build the actual progress reporting behaviour on top of the
    controls exposed here (info label, gauge, remaining-time label, and
    cancel button).
    """

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the content panel with its progress controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, style=wx.DEFAULT_DIALOG_STYLE)
        self._theme = theme
        self.SetIcon(img.getIcon("odemis.ico"))
        self.SetBackgroundColour(self._theme.field_background)

        with vbox() as root_sizer:
            root_sizer.Add(
                self._build_content_panel(), proportion=1, flag=wx.EXPAND
            )

        self.SetSizer(root_sizer)
        self.Layout()
        self.CenterOnParent()

    def _build_content_panel(self) -> wx.Panel:
        """Build the panel hosting the info label, gauge, and buttons.

        :returns: Content panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            self.info_txt = wx.StaticText(
                panel, label="Amazing text about what's happening"
            )
            self.info_txt.SetForegroundColour(self._theme.button_text_contrast)
            sizer.Add(self.info_txt, flag=wx.ALL | wx.EXPAND, border=10)

            self.gauge = wx.Gauge(panel, range=100, size=(-1, 10))
            self.gauge.SetValue(50)
            sizer.Add(
                self.gauge,
                flag=wx.TOP | wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=10,
            )

            self.time_txt = wx.StaticText(
                panel,
                label="Time remaining: 2 hours, 16 minutes and 34 seconds",
            )
            self.time_txt.SetForegroundColour(self._theme.button_text_contrast)
            sizer.Add(self.time_txt, flag=wx.ALL | wx.EXPAND, border=10)

            self.cancel_btn = wx.Button(panel, label="Cancel")
            sizer.Add(
                self.cancel_btn,
                flag=wx.ALL | wx.ALIGN_CENTRE,
                border=10,
            )

        panel.SetSizer(sizer)
        return panel


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(ProgressDialog)
