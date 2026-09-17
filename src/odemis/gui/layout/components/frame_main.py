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

from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, TabButton
from odemis.gui.layout.constants.strings import (
    LABEL_ACQUISITION,
    LABEL_CHAMBER,
    LABEL_STREAMS,
    TOOLTIP_CLOSE_LOG_PANEL,
)
import odemis.gui.layout as layout
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_chevron_button

class MainFrame(wx.Frame):
    """Provide the application's top-level frame layout.

    Hosts the main menu bar, the horizontal tab-button bar (with the
    temperature readout and logo), and the collapsible log panel. Tab
    content panels are inserted between the tab-button bar and the log
    panel by the tab-bar controller at runtime.
    """

    def __init__(self, parent: Optional[wx.Window], theme: Optional[Theme] = None):
        super().__init__(parent, title="Odemis")
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(self._theme.viewport_background)
        set_font(self, self._theme.font_size_default)

        self._build_menu_bar()

        with vbox() as sizer:
            sizer.Add(self._build_tab_buttons_panel(), flag=wx.EXPAND)
            sizer.Add(self._build_log_panel(), flag=wx.EXPAND)

        self.SetSizer(sizer)
        # wx's XRC loader always calls Layout() once a top-level window's
        # sizer tree has been fully parsed. Without it, the frame's first
        # size request (used by the window manager to place/maximize it,
        # before Show() is even called) is computed from an unlaidout
        # sizer, which can be far smaller than the eventual maximized
        # size; the window then visibly snaps to full size a moment
        # later, once a real resize event triggers layout.
        self.Centre()

    def _build_menu_bar(self) -> None:
        """Build and install the File/View/Help menu bar."""
        menu_bar = wx.MenuBar()
        menu_bar.Append(self._build_file_menu(), "File")
        menu_bar.Append(self._build_view_menu(), "View")
        menu_bar.Append(self._build_help_menu(), "Help")
        self.SetMenuBar(menu_bar)

    def _build_file_menu(self) -> wx.Menu:
        """Build the File menu.

        :returns: File menu.
        """
        menu = wx.Menu()
        self.menu_item_open = menu.Append(wx.ID_ANY, "Open...\tCtrl+O")
        self.menu_item_snapshot = menu.Append(
            wx.ID_ANY, "Save Snapshot\tCtrl+S"
        )
        self.menu_item_snapshot_as = menu.Append(
            wx.ID_ANY, "Save Snapshot as...\tCtrl+Shift+S"
        )
        self.menu_item_export_as = menu.Append(
            wx.ID_ANY, "Export as...\tCtrl+E"
        )
        self.menu_item_reset_finealign = menu.Append(
            wx.ID_ANY, "Reset Fine Alignment"
        )
        self.menu_item_reset_finealign.Enable(False)
        self.menu_item_reset_overview = menu.Append(
            wx.ID_ANY, "Reset Overview Image"
        )
        self.menu_item_reset_overview.Enable(False)
        self.menu_item_halt = menu.Append(
            wx.ID_ANY, "Emergency hardware protection\tPause"
        )
        self.menu_item_recalibrate = menu.Append(
            wx.ID_ANY, "Recalibrate Sample Holder..."
        )
        menu.AppendSeparator()
        self.menu_item_quit = menu.Append(wx.ID_ANY, "Quit\tCtrl+Q")
        return menu

    def _build_view_menu(self) -> wx.Menu:
        """Build the View menu.

        :returns: View menu.
        """
        menu = wx.Menu()
        self.menu_item_22view = menu.AppendCheckItem(
            wx.ID_ANY, "2x2 view\tF5"
        )
        self.menu_item_22view.Enable(False)
        menu.AppendSeparator()
        self.menu_item_play_stream = menu.AppendCheckItem(
            wx.ID_ANY, "Play Stream\tF6"
        )
        self.menu_item_play_stream.Enable(False)
        self.menu_item_fit_content = menu.Append(
            wx.ID_ANY, "Zoom to fit content\tF7"
        )
        self.menu_item_fit_content.Enable(False)
        self.menu_item_auto_cont = menu.AppendCheckItem(
            wx.ID_ANY, "Auto Brightness/Contrast\tF8"
        )
        self.menu_item_auto_cont.Enable(False)
        self.menu_item_auto_focus = menu.Append(
            wx.ID_ANY, "Auto Focus\tF4"
        )
        self.menu_item_auto_focus.Enable(False)
        menu.AppendSeparator()
        self.menu_item_cross = menu.AppendCheckItem(
            wx.ID_ANY, "Show Cross Hair"
        )
        self.menu_item_cross.Enable(False)
        self.menu_item_interpolation = menu.AppendCheckItem(
            wx.ID_ANY, "Interpolate Content"
        )
        self.menu_item_interpolation.Enable(False)
        self.menu_item_rawpixel = menu.AppendCheckItem(
            wx.ID_ANY, "Show pixel value\tCtrl+U"
        )
        self.menu_item_rawpixel.Enable(False)
        self.menu_item_show_correlation = menu.AppendCheckItem(
            wx.ID_ANY, "Show Correlation Tab"
        )
        return menu

    def _build_help_menu(self) -> wx.Menu:
        """Build the Help menu, including the Development submenu.

        :returns: Help menu.
        """
        menu = wx.Menu()
        self.menu_item_manual = menu.Append(wx.ID_ANY, "User manual\tF1")
        self.menu_item_manual.Enable(False)

        dev_menu = wx.Menu()
        self.menu_item_devmanual = dev_menu.Append(
            wx.ID_ANY, "Developer documentation"
        )
        self.menu_item_devmanual.Enable(False)
        self.menu_item_inspect = dev_menu.Append(
            wx.ID_ANY, "Inspect GUI\tCtrl+I"
        )
        self.menu_item_debug = dev_menu.AppendCheckItem(
            wx.ID_ANY, "Show log panel\tCtrl+D"
        )
        self.menu_item_edit_meteor_calibration = dev_menu.Append(
            wx.ID_ANY, "Edit METEOR Calibration"
        )
        self.menu_item_edit_meteor_calibration.Enable(False)
        menu.AppendSubMenu(dev_menu, "Development")

        self.menu_item_bugreport = menu.Append(
            wx.ID_ANY, "Report a problem..."
        )
        self.menu_item_data_sharing = menu.AppendCheckItem(
            wx.ID_ANY, "Share Data with Delmic"
        )
        menu.AppendSeparator()
        self.menu_item_update = menu.Append(wx.ID_ANY, "Check for update")
        self.menu_item_about = menu.Append(wx.ID_ANY, "About...")
        return menu

    def _build_tab_buttons_panel(self) -> wx.Panel:
        """Build the horizontal tab-button bar.

        :returns: Tab-buttons panel.
        """
        self.pnl_tabbuttons = wx.Panel(self, size=(-1, 40))
        self.pnl_tabbuttons.SetMinSize((-1, 40))
        self.pnl_tabbuttons.SetBackgroundColour(self._theme.text_secondary)

        with hbox() as sizer:
            self.btn_tab_cryosecom_chamber = self._tab_button(
                self.pnl_tabbuttons, LABEL_CHAMBER
            )
            sizer.Add(
                self.btn_tab_cryosecom_chamber,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_secom_streams = self._tab_button(
                self.pnl_tabbuttons, LABEL_STREAMS
            )
            sizer.Add(
                self.btn_tab_secom_streams,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_localization = self._tab_button(
                self.pnl_tabbuttons, "LOCALIZATION"
            )
            sizer.Add(
                self.btn_tab_localization,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_correlation = self._tab_button(
                self.pnl_tabbuttons, "CORRELATION"
            )
            sizer.Add(
                self.btn_tab_correlation,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_fibsem = self._tab_button(
                self.pnl_tabbuttons, "FIBSEM"
            )
            sizer.Add(
                self.btn_tab_fibsem,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_sparc_acqui = self._tab_button(
                self.pnl_tabbuttons, LABEL_ACQUISITION
            )
            sizer.Add(
                self.btn_tab_sparc_acqui,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_fastem_main = self._tab_button(
                self.pnl_tabbuttons, "MAIN"
            )
            sizer.Add(
                self.btn_tab_fastem_main,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_inspection = self._tab_button(
                self.pnl_tabbuttons, "ANALYSIS"
            )
            sizer.Add(
                self.btn_tab_inspection,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            sizer.AddStretchSpacer()

            self.btn_tab_sparc_chamber = self._tab_button(
                self.pnl_tabbuttons, LABEL_CHAMBER
            )
            sizer.Add(
                self.btn_tab_sparc_chamber,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.temperature_display = wx.StaticText(
                self.pnl_tabbuttons,
                label="20°C",
                size=(160, 30),
                style=wx.ALIGN_CENTRE,
            )
            self.temperature_display.SetForegroundColour(
                self._theme.background
            )
            set_font(
                self.temperature_display, self._theme.font_size_button
            )
            self.temperature_display.Hide()
            sizer.Add(
                self.temperature_display,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            self.btn_tab_align = self._tab_button(
                self.pnl_tabbuttons, "ALIGNMENT"
            )
            sizer.Add(
                self.btn_tab_align,
                flag=wx.LEFT | wx.ALIGN_BOTTOM,
                border=20,
            )

            sizer.Add((32, -1))

            self.logo = wx.StaticBitmap(
                self.pnl_tabbuttons, bitmap=img.getBitmap("logo_h30.png")
            )
            sizer.Add(
                self.logo, flag=wx.RIGHT | wx.ALIGN_CENTRE, border=10
            )

        self.pnl_tabbuttons.SetSizer(sizer)
        return self.pnl_tabbuttons

    def _tab_button(self, parent: wx.Window, label: str) -> TabButton:
        """Create a hidden main tab-switching button.

        :param parent: Parent window.
        :param label: Button label.
        :returns: Tab-switching button, initially hidden. The tab-bar
            controller shows only the buttons relevant to the current
            microscope role.
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
        button.Hide()
        return button

    def _build_log_panel(self) -> wx.Panel:
        """Build the collapsible log panel with its toggle and text view.

        :returns: Log panel, initially hidden.
        """
        self.pnl_log = wx.Panel(self)
        self.pnl_log.SetBackgroundColour(self._theme.button_text)
        self.pnl_log.Hide()

        with hbox() as sizer:
            self.btn_log = create_chevron_button(
                self.pnl_log, "down", tooltip=TOOLTIP_CLOSE_LOG_PANEL
            )
            sizer.Add(
                self.btn_log, flag=wx.ALL | wx.ALIGN_BOTTOM, border=10
            )

            self.txt_log = wx.TextCtrl(
                self.pnl_log,
                value="Log message panel",
                size=(-1, 200),
                style=wx.BORDER_NONE | wx.TE_MULTILINE | wx.TE_RICH,
            )
            self.txt_log.SetBackgroundColour(self._theme.button_text)
            # A monospace face (rather than just a point size) is required
            # so that log message columns stay aligned; set_font() only
            # changes size/weight and preserves the native font family.
            log_font = wx.Font(
                10,
                wx.FONTFAMILY_TELETYPE,
                wx.FONTSTYLE_NORMAL,
                wx.FONTWEIGHT_NORMAL,
                faceName="Monospace",
            )
            self.txt_log.SetFont(log_font)
            sizer.Add(self.txt_log, proportion=1, flag=wx.EXPAND)

        self.pnl_log.SetSizer(sizer)
        return self.pnl_log


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(MainFrame)
