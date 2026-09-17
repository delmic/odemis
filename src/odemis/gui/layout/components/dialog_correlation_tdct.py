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
import wx.grid

import odemis.gui.layout as layout
from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, ImageTextButton
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import MicroscopeViewport
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants.strings import (
    LABEL_CLOSE,
    LABEL_STREAMS,
)
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class TDCorrelationDialogBase(wx.Dialog):
    """Provide the multipoint 3DCT correlation dialog layout."""

    def __init__(
        self,
        parent: Optional[wx.Window],
        theme: Optional[Theme] = None,
    ) -> None:
        """Create the correlation dialog controls and viewports.

        :param parent: Parent window, or None for a top-level dialog.
        :param theme: Semantic layout theme.
        """
        super().__init__(
            parent,
            title="Multipoint Correlation",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(self._theme.viewport_background)
        set_font(self, 9)

        root_sizer = wx.FlexGridSizer(1, 3, 0, 0)
        root_sizer.AddGrowableCol(1)
        root_sizer.AddGrowableRow(0)
        root_sizer.Add(self._build_toolbar_panel(), flag=wx.EXPAND)
        root_sizer.Add(
            self._build_viewport_grid(),
            proportion=1,
            flag=wx.EXPAND,
        )
        root_sizer.Add(self._build_settings_column(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_toolbar_panel(self) -> wx.Panel:
        """Build the left column holding the vertical viewport toolbar.

        :returns: Toolbar column panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            with hbox() as toolbar_sizer:
                toolbar_sizer.AddStretchSpacer()
                self.correlation_toolbar = ToolBar(panel, style=wx.VERTICAL)
                toolbar_sizer.Add(self.correlation_toolbar)
                toolbar_sizer.AddStretchSpacer()
            sizer.Add(toolbar_sizer)

        panel.SetSizer(sizer)
        return panel

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the FM and FIB correlation viewports.

        :returns: Viewport grid.
        """
        self.pnl_correlation_grid = ViewportGrid(self)
        self.vp_correlation_tl = self._viewport(self.pnl_correlation_grid)
        self.vp_correlation_tr = self._viewport(self.pnl_correlation_grid)

        self.pnl_correlation_grid.viewports = (
            self.vp_correlation_tl,
            self.vp_correlation_tr,
        )
        return self.pnl_correlation_grid

    def _build_settings_column(self) -> wx.Panel:
        """Build the scrollable settings column and the close-button row.

        :returns: Right settings column.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_scrolled_settings(panel),
                proportion=1,
                flag=wx.EXPAND,
            )
            sizer.Add(self._build_close_panel(panel), flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_scrolled_settings(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the FM posture row and the correlation fold panels.

        :param parent: Parent settings panel.
        :returns: Scrollable settings window.
        """
        scroll_window = wx.ScrolledWindow(
            parent,
            size=(400, -1),
            style=wx.VSCROLL,
        )
        scroll_window.SetMinSize((400, 400))
        scroll_window.SetBackgroundColour(self._theme.background)
        scroll_window.EnableScrolling(False, True)
        scroll_window.SetScrollbars(-1, 10, 1, 1)

        with vbox() as scroll_sizer:
            scroll_sizer.Add(
                self._build_fm_posture_row(scroll_window),
                flag=wx.ALL,
                border=10,
            )
            scroll_sizer.Add(
                self._build_fold_panel_bar(scroll_window),
                flag=wx.EXPAND,
            )

        scroll_window.SetSizer(scroll_sizer)
        scroll_window.FitInside()
        return scroll_window

    def _build_fm_posture_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the row showing the FM acquisition posture.

        :param parent: Parent scrolled window.
        :returns: FM posture row sizer.
        """
        with hbox() as sizer:
            label = wx.StaticText(parent, label="FM acquired at")
            label.SetForegroundColour(self._theme.field_foreground)
            sizer.Add(
                label,
                flag=wx.RIGHT | wx.ALIGN_CENTER_VERTICAL,
                border=10,
            )

            self.bmp_fm_posture = wx.StaticBitmap(
                parent,
                bitmap=img.getBitmap("icon/ico_meteorimaging.png"),
            )
            sizer.Add(self.bmp_fm_posture)

            self.lbl_fm_posture = wx.StaticText(parent, label="posture")
            self.lbl_fm_posture.SetForegroundColour(self._theme.text_disabled)
            sizer.Add(self.lbl_fm_posture, flag=wx.ALIGN_CENTER_VERTICAL)

        return sizer

    def _build_fold_panel_bar(self, parent: wx.ScrolledWindow) -> FoldPanelBar:
        """Build the correlation and streams fold panels.

        :param parent: Parent scrolled window.
        :returns: Fold-panel bar.
        """
        fold_bar = FoldPanelBar(parent)
        fold_bar.SetBackgroundColour(self._theme.background)
        self._build_correlation_fold_item(fold_bar)
        self._build_streams_fold_item(fold_bar)
        return fold_bar

    def _build_correlation_fold_item(self, fold_bar: FoldPanelBar) -> None:
        """Build the fold-panel item holding the correlation point table.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_correlation_panel = FoldPanelItem(fold_bar, label="")
        fold_bar.add_item(self.fp_correlation_panel)

        pnl_correlation = wx.Panel(self.fp_correlation_panel)

        with vbox() as sizer:
            sizer.Add(
                self._build_correlation_button_row(pnl_correlation),
                flag=wx.ALL | wx.EXPAND,
                border=10,
            )

            self.table_grid = wx.grid.Grid(
                pnl_correlation,
                style=wx.WANTS_CHARS,
            )
            sizer.Add(self.table_grid)

            self.txt_correlation_rms = wx.StaticText(
                pnl_correlation,
                label="Correlation RMS Deviation :",
            )
            self.txt_correlation_rms.SetForegroundColour(self._theme.text_primary)
            self.txt_correlation_rms.Hide()
            sizer.Add(self.txt_correlation_rms, flag=wx.LEFT, border=10)

        pnl_correlation.SetSizer(sizer)
        self.fp_correlation_panel.add_item(pnl_correlation)

    def _build_correlation_button_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the delete, refine, and refine-status controls row.

        :param parent: Parent correlation panel.
        :returns: Button row sizer.
        """
        with hbox() as sizer:
            self.btn_delete_row = ImageButton(
                parent,
                icon=img.getBitmap("icon/ico_trash.png"),
                height=16,
                style=wx.ALIGN_CENTRE,
            )
            sizer.Add(
                self.btn_delete_row,
                flag=wx.ALL | wx.EXPAND,
                border=10,
            )

            self.btn_xyz_targeting = wx.Button(parent, label="Refine")
            sizer.Add(self.btn_xyz_targeting)

            self.txt_refine_xyz_active = wx.StaticText(parent, label=" ")
            self.txt_refine_xyz_active.SetForegroundColour(self._theme.text_primary)
            self.txt_refine_xyz_active.Hide()
            sizer.Add(
                self.txt_refine_xyz_active,
                flag=wx.ALIGN_CENTER_VERTICAL | wx.ALL,
                border=10,
            )

        return sizer

    def _build_streams_fold_item(self, fold_bar: FoldPanelBar) -> None:
        """Build the fold-panel item holding the correlation stream bar.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_correlation_streams = FoldPanelItem(
            fold_bar,
            label=LABEL_STREAMS,
        )
        self.fp_correlation_streams.SetForegroundColour(
            self._theme.button_text
        )
        self.fp_correlation_streams.SetBackgroundColour(
            self._theme.section_header
        )
        fold_bar.add_item(self.fp_correlation_streams)

        self.pnl_correlation_streams = StreamBar(
            self.fp_correlation_streams,
            size=(300, -1),
        )
        self.pnl_correlation_streams.SetForegroundColour(
            self._theme.text_muted
        )
        self.pnl_correlation_streams.SetBackgroundColour(
            self._theme.background
        )
        self.fp_correlation_streams.add_item(self.pnl_correlation_streams)

    def _build_close_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the bottom close-button row.

        :param parent: Parent settings panel.
        :returns: Close-button panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.panel_background)

        with hbox() as sizer:
            self.btn_close = ImageTextButton(
                panel,
                label=LABEL_CLOSE,
                height=48,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            set_font(self.btn_close, self._theme.font_size_prominent_button)
            self.btn_close.SetForegroundColour(self._theme.button_text)
            sizer.Add(
                self.btn_close,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT | wx.EXPAND,
                border=10,
            )

        panel.SetSizer(sizer)
        return panel

    def _viewport(self, parent: wx.Window) -> MicroscopeViewport:
        """Create a themed correlation viewport.

        :param parent: Parent viewport grid.
        :returns: Correlation viewport.
        """
        viewport = MicroscopeViewport(parent)
        viewport.SetForegroundColour(self._theme.text_secondary)
        viewport.SetBackgroundColour(self._theme.viewport_background)
        return viewport


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(TDCorrelationDialogBase)
