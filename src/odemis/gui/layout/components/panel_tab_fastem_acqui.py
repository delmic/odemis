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

from typing import Optional

import wx

import odemis.gui.layout as layout
from odemis.gui import img
from odemis.gui.comp.buttons import TabButton
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class PnlTabFastemAcqui(wx.Panel):
    """Provide the FastEM acquisition tab layout.

    Hosts the single-beam/multi-beam sub-tab switcher and the panel that
    contains the corresponding sub-tabs.
    """

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the FastEM acquisition tab controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, size=(400, -1))
        self.SetMinSize((400, -1))
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(self._build_tab_section(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_tab_section(self) -> wx.Panel:
        """Build the single-beam/multi-beam tab buttons and the tab host
        panel.

        :returns: Tab section panel.
        """
        panel = wx.Panel(self, style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(self._build_tab_buttons_panel(panel), flag=wx.EXPAND)

            self.pnl_acqui_tabs = wx.Panel(panel)
            self.pnl_acqui_tabs.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.pnl_acqui_tabs,
                proportion=1,
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_tab_buttons_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the SINGLE-BEAM/MULTI-BEAM sub-tab switcher.

        :param parent: Parent window.
        :returns: Tab buttons panel.
        """
        self.pnl_tabbuttons = wx.Panel(parent, size=(400, 40))
        self.pnl_tabbuttons.SetMinSize((400, 40))
        self.pnl_tabbuttons.SetBackgroundColour(self._theme.text_secondary)

        with hbox() as sizer:
            self.btn_tab_single_beam = self._tab_button(
                self.pnl_tabbuttons, "SINGLE-BEAM", "icon/ico_single_beam.png"
            )
            sizer.Add(
                self.btn_tab_single_beam,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=27,
            )

            self.btn_tab_multi_beam = self._tab_button(
                self.pnl_tabbuttons, "MULTI-BEAM", "icon/ico_multi_beam.png"
            )
            sizer.Add(
                self.btn_tab_multi_beam,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=27,
            )

        self.pnl_tabbuttons.SetSizer(sizer)
        return self.pnl_tabbuttons

    def _tab_button(
        self, parent: wx.Window, label: str, icon_name: str
    ) -> TabButton:
        """Create a SINGLE-BEAM/MULTI-BEAM sub-tab switching button.

        :param parent: Parent window.
        :param label: Button label.
        :param icon_name: Icon file path relative to the image root.
        :returns: Tab switching button.
        """
        button = TabButton(
            parent,
            size=(160, 30),
            face_colour="def",
            icon=img.getBitmap(icon_name),
            label=label,
            style=wx.ALIGN_CENTRE,
        )
        button.SetForegroundColour(self._theme.text_primary)
        set_font(button, self._theme.font_size_button)
        return button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabFastemAcqui)
