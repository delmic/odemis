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
from odemis.gui.comp.buttons import ImageButton, TabButton
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants.strings import (
    LABEL_ACQUISITION,
    TOOLTIP_OPEN_LOG_PANEL,
)
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_chevron_button


class PnlTabFastemMain(wx.Panel):
    """Provide the FastEM main tab layout.

    Hosts the collapsible user-settings panel, the viewport grid with its
    project-manager overlay, and the setup/acquisition sub-tab switcher.
    """

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the FastEM main tab controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_user_settings_panel(),
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_toolbar_panel(),
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_viewport_project_manager_panel(),
                proportion=1,
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_tab_section(),
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_user_settings_panel(self) -> wx.Panel:
        """Build the collapsible left-hand user-settings host panel.

        :returns: User-settings host panel.
        """
        self.pnl_user_settings = wx.Panel(self, size=(300, 600))
        self.pnl_user_settings.SetMinSize((300, 600))
        self.pnl_user_settings.SetBackgroundColour(self._theme.background)
        return self.pnl_user_settings

    def _build_toolbar_panel(self) -> wx.Panel:
        """Build the vertical toolbar column between the settings and the
        viewport grid.

        :returns: Toolbar panel.
        """
        self.pnl_toolbar = wx.Panel(self, size=(-1, -1))
        self.pnl_toolbar.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.AddSpacer(20)

            self.btn_pnl_user_settings = create_chevron_button(
                self.pnl_toolbar,
                "left",
            )
            sizer.Add(self.btn_pnl_user_settings)

            sizer.AddStretchSpacer()

            self.toolbar = ToolBar(self.pnl_toolbar, style=wx.VERTICAL)
            sizer.Add(self.toolbar, flag=wx.ALIGN_RIGHT)

            sizer.AddStretchSpacer(8)

            self.btn_log = create_chevron_button(self.pnl_toolbar, "up")
            self.btn_log.SetToolTip(TOOLTIP_OPEN_LOG_PANEL)
            sizer.Add(self.btn_log)

        self.pnl_toolbar.SetSizer(sizer)
        return self.pnl_toolbar

    def _build_viewport_project_manager_panel(self) -> wx.Panel:
        """Build the viewport grid together with the project-manager
        header and its collapsible body.

        :returns: Viewport and project-manager host panel.
        """
        self.pnl_vp_grid_project_manager = wx.Panel(self)

        with vbox() as sizer:
            self.pnl_vp_grid = ViewportGrid(
                self.pnl_vp_grid_project_manager, size=(1108, 998)
            )
            self.pnl_vp_grid.SetMinSize((1108, 998))
            sizer.Add(self.pnl_vp_grid, proportion=1, flag=wx.EXPAND)

            sizer.Add(
                self._build_project_manager_housing(
                    self.pnl_vp_grid_project_manager
                ),
                flag=wx.EXPAND,
            )

            self.pnl_project_manager = wx.Panel(
                self.pnl_vp_grid_project_manager, size=(1108, 200)
            )
            self.pnl_project_manager.SetMinSize((1108, 200))
            self.pnl_project_manager.SetBackgroundColour(
                self._theme.button_text
            )
            self.pnl_project_manager.Hide()
            sizer.Add(self.pnl_project_manager, flag=wx.EXPAND)

        self.pnl_vp_grid_project_manager.SetSizer(sizer)
        return self.pnl_vp_grid_project_manager

    def _build_project_manager_housing(self, parent: wx.Window) -> wx.Panel:
        """Build the padded row that holds the project-manager header.

        :param parent: Parent window.
        :returns: Project-manager housing panel.
        """
        panel = wx.Panel(parent, size=(1108, 30))
        panel.SetMinSize((1108, 30))

        with hbox() as sizer:
            self.pnl_project_manager_left_padding = wx.Panel(panel)
            sizer.Add(
                self.pnl_project_manager_left_padding,
                proportion=1,
                flag=wx.EXPAND,
            )

            sizer.Add(
                self._build_project_manager_header(panel),
                proportion=1,
                flag=wx.EXPAND,
            )

            self.pnl_project_manager_right_padding = wx.Panel(panel)
            sizer.Add(
                self.pnl_project_manager_right_padding,
                proportion=1,
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_project_manager_header(self, parent: wx.Window) -> wx.Panel:
        """Build the project-manager header with its title and controls.

        :param parent: Parent window.
        :returns: Project-manager header panel.
        """
        self.pnl_project_manager_header = wx.Panel(parent)
        self.pnl_project_manager_header.SetBackgroundColour(
            self._theme.background
        )

        with hbox() as sizer:
            sizer.AddStretchSpacer()

            sizer.Add(
                self._build_project_manager_label(
                    self.pnl_project_manager_header
                ),
                proportion=1,
                flag=wx.ALIGN_CENTER_VERTICAL,
            )

            sizer.AddStretchSpacer()

            self.btn_pnl_project_manager = create_chevron_button(
                self.pnl_project_manager_header,
                "up",
            )
            sizer.Add(self.btn_pnl_project_manager)

            self.btn_detach_project_manager = ImageButton(
                self.pnl_project_manager_header,
                icon=img.getBitmap("icon/ico_eject.png"),
                height=16,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            sizer.Add(self.btn_detach_project_manager)

        self.pnl_project_manager_header.SetSizer(sizer)
        return self.pnl_project_manager_header

    def _build_project_manager_label(self, parent: wx.Window) -> wx.Panel:
        """Build the vertically centred "PROJECT MANAGER" title.

        :param parent: Parent window.
        :returns: Title panel.
        """
        panel = wx.Panel(parent, size=(-1, 30))
        panel.SetMinSize((-1, 30))

        with vbox() as sizer:
            sizer.AddStretchSpacer()
            self.lbl_project_manager = wx.StaticText(
                panel, label="PROJECT MANAGER", style=wx.ALIGN_CENTRE
            )
            self.lbl_project_manager.SetForegroundColour(
                self._theme.text_primary
            )
            set_font(self.lbl_project_manager, self._theme.font_size_body)
            sizer.Add(self.lbl_project_manager)
            sizer.AddStretchSpacer()

        panel.SetSizer(sizer)
        return panel

    def _build_tab_section(self) -> wx.Sizer:
        """Build the setup/acquisition tab buttons and the tab host panel.

        :returns: Tab section sizer.
        """
        with vbox() as sizer:
            sizer.Add(self._build_tab_buttons_panel(), flag=wx.EXPAND)

            line = wx.StaticLine(self, size=(-1, 1))
            line.SetMinSize((-1, 1))
            line.SetBackgroundColour(self._theme.background)
            sizer.Add(line, flag=wx.EXPAND)

            self.pnl_tabs = wx.Panel(self, size=(400, 1500))
            self.pnl_tabs.SetMinSize((400, 1500))
            self.pnl_tabs.SetBackgroundColour(self._theme.background)
            sizer.Add(self.pnl_tabs, flag=wx.EXPAND)

        return sizer

    def _build_tab_buttons_panel(self) -> wx.Panel:
        """Build the SETUP/ACQUISITION sub-tab switcher.

        :returns: Tab buttons panel.
        """
        self.pnl_tabbuttons = wx.Panel(self, size=(400, 40))
        self.pnl_tabbuttons.SetMinSize((400, 40))
        self.pnl_tabbuttons.SetBackgroundColour(self._theme.text_secondary)

        with hbox() as sizer:
            self.btn_tab_setup = self._tab_button(
                self.pnl_tabbuttons, "SETUP"
            )
            sizer.Add(
                self.btn_tab_setup,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=27,
            )

            self.btn_tab_acqui = self._tab_button(
                self.pnl_tabbuttons, LABEL_ACQUISITION
            )
            sizer.Add(
                self.btn_tab_acqui,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=27,
            )

        self.pnl_tabbuttons.SetSizer(sizer)
        return self.pnl_tabbuttons

    def _tab_button(self, parent: wx.Window, label: str) -> TabButton:
        """Create a SETUP/ACQUISITION sub-tab switching button.

        :param parent: Parent window.
        :param label: Button label.
        :returns: Tab switching button.
        """
        button = TabButton(
            parent,
            size=(160, 30),
            face_colour="def",
            label=label,
            style=wx.ALIGN_CENTRE,
        )
        button.SetForegroundColour(self._theme.text_primary)
        set_font(button, self._theme.font_size_button)
        return button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabFastemMain)
