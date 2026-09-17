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
from odemis.gui.comp.buttons import ImageToggleButton
from odemis.gui.comp.foldpanelbar import CaptionBar
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox

# Default foreground colour for the pressure readout row. Not yet a shared
# semantic theme role: this is the only converted layout using it so far.
_PRESSURE_TEXT_COLOUR = "#DDDDDD"


class PnlFastemUserSettings(wx.Panel):
    """Provide the FastEM user settings layout.

    Exposes the pump/e-beam state buttons, the pressure readout label, and
    the user settings host panel for the FastEMUserSettingsPanel controller.
    """

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the user settings controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, size=(300, -1))
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with vbox() as root_sizer:
            root_sizer.Add(
                self._build_content_panel(),
                flag=wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_content_panel(self) -> wx.Panel:
        """Build the button row, pressure readout, and user settings section.

        :returns: Content panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_button_row(panel),
                flag=wx.ALL,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_pressure_row(panel),
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.Add(self._build_user_settings_section(panel), flag=wx.EXPAND)
            sizer.AddStretchSpacer()

        panel.SetSizer(sizer)
        return panel

    def _build_button_row(self, parent: wx.Window) -> wx.GridBagSizer:
        """Build the pump and e-beam state toggle buttons.

        :param parent: Parent window.
        :returns: Grid bag sizer holding both buttons.
        """
        sizer = wx.GridBagSizer(
            vgap=self._theme.spacing_standard, hgap=self._theme.spacing_standard
        )

        self.btn_pressure = ImageToggleButton(
            parent,
            icon=img.getBitmap("icon/ico_press.png"),
            icon_on=img.getBitmap("icon/ico_press_green.png"),
            height=48,
            size=(120, 48),
            face_colour="def",
            label="PUMP",
            style=wx.ALIGN_CENTRE,
        )
        self.btn_pressure.SetForegroundColour(self._theme.button_text)
        set_font(self.btn_pressure, self._theme.font_size_button)
        sizer.Add(self.btn_pressure, pos=(0, 0), flag=wx.EXPAND)

        self.btn_ebeam = ImageToggleButton(
            parent,
            icon=img.getBitmap("icon/ico_sem.png"),
            icon_on=img.getBitmap("icon/ico_sem_green.png"),
            height=48,
            size=(120, 48),
            face_colour="def",
            label="E-BEAM",
            style=wx.ALIGN_CENTRE,
        )
        self.btn_ebeam.SetForegroundColour(self._theme.button_text)
        set_font(self.btn_ebeam, self._theme.font_size_button)
        sizer.Add(self.btn_ebeam, pos=(0, 1), flag=wx.EXPAND)

        return sizer

    def _build_pressure_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the pressure readout label row.

        :param parent: Parent window.
        :returns: Pressure readout row.
        """
        with hbox() as sizer:
            label = wx.StaticText(parent, label="Pressure: ")
            label.SetForegroundColour(_PRESSURE_TEXT_COLOUR)
            set_font(label, self._theme.font_size_checklist)
            sizer.Add(label)

            self.pressure_label = wx.StaticText(
                parent, label="...", size=(150, 20)
            )
            self.pressure_label.SetForegroundColour(_PRESSURE_TEXT_COLOUR)
            self.pressure_label.SetBackgroundColour(self._theme.background)
            sizer.Add(self.pressure_label)

        return sizer

    def _build_user_settings_section(self, parent: wx.Window) -> wx.Panel:
        """Build the caption bar and host panel for the user settings entries.

        :param parent: Parent window.
        :returns: User settings section panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            caption_bar = CaptionBar(panel, "USER SETTINGS", False)
            caption_bar.SetForegroundColour(self._theme.button_text)
            sizer.Add(caption_bar, flag=wx.EXPAND)

            self.user_settings_panel = wx.Panel(panel, size=(300, 600))
            self.user_settings_panel.SetBackgroundColour(self._theme.background)
            self.user_settings_panel.SetForegroundColour("#999999")
            sizer.Add(self.user_settings_panel)

        panel.SetSizer(sizer)
        return panel


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlFastemUserSettings)
