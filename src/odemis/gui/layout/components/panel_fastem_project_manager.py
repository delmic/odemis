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

from typing import Optional, Tuple

import wx

import odemis.gui.layout as layout
from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, TabButton
from odemis.gui.comp.foldpanelbar import CaptionBar
from odemis.gui.layout.constants.strings import LABEL_PROJECTS
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox

# (attribute name, label, icon file name or None)
_TAB_BUTTONS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("btn_tab_settings", "SETTINGS", None),
    ("btn_tab_toas", "TOAS", "icon/ico_single_beam.png"),
    ("btn_tab_ribbons", "RIBBONS", None),
    ("btn_tab_sections", "SECTIONS", None),
    ("btn_tab_roas", "ROAS", "icon/ico_multi_beam.png"),
)

# (attribute name, icon file name)
_ROW_BUTTONS: Tuple[Tuple[str, str], ...] = (
    ("btn_move_up", "icon/arr_up.png"),
    ("btn_move_down", "icon/arr_down.png"),
    ("btn_delete", "icon/ico_trash.png"),
    ("btn_export", "icon/ico_download.png"),
    ("btn_import", "icon/ico_upload.png"),
)


class PnlFastemProjectManager(wx.Panel):
    """Provide the FastEM project manager layout.

    The projects column, row-action button strip, and project tab area
    are exposed as attributes for the FastEMProjectManagerPanel controller.
    """

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the project manager controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(self._build_active_project_column())
            root_sizer.Add(self._build_row_button_column(), flag=wx.EXPAND)
            root_sizer.Add(self._build_tab_area(), proportion=1, flag=wx.EXPAND)

        # This panel is a plain child of pnl_project_manager, not one of its
        # sizer items, so it needs to size itself to its content immediately
        # (mirrors the SetSizeHints call the XRC loader performs on a root
        # panel). Without it, controllers reading e.g. pnl_project_tabs.Size
        # right after construction would see wx's default tiny window size.
        self.SetSizerAndFit(root_sizer)

    def _build_active_project_column(self) -> wx.Panel:
        """Build the projects caption bar and active project panel.

        :returns: Left projects column.
        """
        self.pnl_active_project = wx.Panel(self, size=(230, 200))
        self.pnl_active_project.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            caption_bar = CaptionBar(self.pnl_active_project, LABEL_PROJECTS, False)
            caption_bar.SetForegroundColour(self._theme.button_text)
            sizer.Add(caption_bar, flag=wx.EXPAND)

            self.active_project_panel = wx.Panel(
                self.pnl_active_project, size=(230, 160)
            )
            self.active_project_panel.SetBackgroundColour(self._theme.background)
            self.active_project_panel.SetForegroundColour(
                self._theme.panel_foreground
            )
            sizer.Add(self.active_project_panel)

        self.pnl_active_project.SetSizer(sizer)
        return self.pnl_active_project

    def _build_row_button_column(self) -> wx.Panel:
        """Build the vertical row-action button strip.

        :returns: Row-action button column.
        """
        self.btn_panel = wx.Panel(self)
        self.btn_panel.SetBackgroundColour(self._theme.text_secondary)

        with vbox() as sizer:
            sizer.AddSpacer(40)

            buttons_panel = wx.Panel(self.btn_panel)
            buttons_panel.SetBackgroundColour(self._theme.viewport_background)

            with vbox() as buttons_sizer:
                for attr_name, icon_name in _ROW_BUTTONS:
                    button = ImageButton(
                        buttons_panel,
                        icon=img.getBitmap(icon_name),
                        height=16,
                        style=wx.ALIGN_CENTRE,
                    )
                    setattr(self, attr_name, button)
                    buttons_sizer.Add(
                        button, flag=wx.EXPAND | wx.BOTTOM, border=8
                    )

            buttons_panel.SetSizer(buttons_sizer)
            sizer.Add(buttons_panel)

        self.btn_panel.SetSizer(sizer)
        return self.btn_panel

    def _build_tab_area(self) -> wx.Sizer:
        """Build the tab-button strip and the tab content panel.

        :returns: Vertical sizer containing both.
        """
        with vbox() as sizer:
            sizer.Add(self._build_tab_buttons_panel(), flag=wx.EXPAND)

            self.pnl_project_tabs = wx.Panel(self)
            self.pnl_project_tabs.SetBackgroundColour(self._theme.background)
            sizer.Add(self.pnl_project_tabs, proportion=1, flag=wx.EXPAND)
        return sizer

    def _build_tab_buttons_panel(self) -> wx.Panel:
        """Build the horizontal row of project tab buttons.

        :returns: Tab-button panel.
        """
        self.pnl_project_tabbuttons = wx.Panel(self, size=(878, 40))
        self.pnl_project_tabbuttons.SetBackgroundColour(self._theme.text_secondary)

        with hbox() as sizer:
            for attr_name, label, icon_name in _TAB_BUTTONS:
                kwargs = {}
                if icon_name:
                    kwargs["icon"] = img.getBitmap(icon_name)
                button = TabButton(
                    self.pnl_project_tabbuttons,
                    label=label,
                    size=(160, 30),
                    face_colour="def",
                    style=wx.ALIGN_CENTRE,
                    **kwargs,
                )
                button.SetForegroundColour(self._theme.text_primary)
                set_font(button, self._theme.font_size_button)
                setattr(self, attr_name, button)
                sizer.Add(
                    button,
                    flag=wx.LEFT | wx.ALIGN_BOTTOM,
                    border=10,
                )

        self.pnl_project_tabbuttons.SetSizer(sizer)
        return self.pnl_project_tabbuttons


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlFastemProjectManager)
