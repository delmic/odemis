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

from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import LiveViewport
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import vbox


class DialogSlmAlignment(wx.Dialog):
    """Provide the SLM alignment workflow dialog layout."""

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the SLM alignment dialog controls and viewports.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(
            parent,
            title="SLM Alignment",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._theme = theme
        self.SetBackgroundColour(theme.viewport_background)

        root_sizer = wx.FlexGridSizer(rows=1, cols=3, vgap=0, hgap=0)
        root_sizer.AddGrowableCol(1)
        root_sizer.AddGrowableRow(0)
        root_sizer.Add(self._build_left_panel(), flag=wx.EXPAND)
        root_sizer.Add(self._build_viewport_grid(), proportion=1, flag=wx.EXPAND)
        root_sizer.Add(self._build_settings_column(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_left_panel(self) -> wx.Panel:
        """Build the fine-alignment action and workflow instructions column.

        :returns: Left control column.
        """
        panel = wx.Panel(self, size=(350, -1))
        panel.SetMinSize((350, -1))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.btn_fine_alignment = wx.Button(panel, label="Fine Alignment")
            set_font(self.btn_fine_alignment, self._theme.font_size_body)
            sizer.Add(
                self.btn_fine_alignment,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.txt_stage_moving = wx.StaticText(panel, label="")
            set_font(self.txt_stage_moving, self._theme.font_size_body)
            self.txt_stage_moving.SetForegroundColour(self._theme.text_primary)
            sizer.Add(
                self.txt_stage_moving,
                flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            txt_slm_workflow = wx.StaticText(
                panel,
                label=(
                    "SLM Alignment Workflow:\n"
                    "1. Locate and move to an empty area\n"
                    "2. Mill fibucial\n"
                    "3. Play SLM reflection stream, focus on the center of the "
                    "fibucial\n"
                    "4. Using fine alignment, click on the center of the "
                    "fibucial"
                ),
            )
            set_font(txt_slm_workflow, self._theme.font_size_body)
            txt_slm_workflow.SetForegroundColour(self._theme.text_primary)
            sizer.Add(
                txt_slm_workflow,
                proportion=1,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the FM and FIB live-view viewport grid.

        :returns: Viewport grid.
        """
        self.pnl_slm_alignment_grid = ViewportGrid(self)
        self.vp_slm_fm_live = LiveViewport(self.pnl_slm_alignment_grid)
        self.vp_slm_fib_live = LiveViewport(self.pnl_slm_alignment_grid)

        viewports = (self.vp_slm_fm_live, self.vp_slm_fib_live)
        for viewport in viewports:
            viewport.SetForegroundColour(self._theme.text_secondary)
            viewport.SetBackgroundColour(self._theme.viewport_background)

        # Controllers access the viewport tuple before the first size event.
        self.pnl_slm_alignment_grid.viewports = viewports
        return self.pnl_slm_alignment_grid

    def _build_settings_column(self) -> wx.ScrolledWindow:
        """Build the scrollable streams and milling settings column.

        :returns: Right settings column.
        """
        self.scr_win_slm_right = wx.ScrolledWindow(
            self,
            size=(380, -1),
            style=wx.VSCROLL,
        )
        self.scr_win_slm_right.SetMinSize((340, -1))
        self.scr_win_slm_right.SetBackgroundColour(self._theme.background)
        self.scr_win_slm_right.EnableScrolling(False, True)
        self.scr_win_slm_right.SetScrollbars(-1, 10, 1, 1)

        fold_bar = FoldPanelBar(self.scr_win_slm_right)
        fold_bar.SetBackgroundColour(self._theme.background)

        self._build_streams_section(fold_bar)
        self._build_milling_section(fold_bar)

        with vbox() as sizer:
            sizer.Add(fold_bar, flag=wx.EXPAND)

        self.scr_win_slm_right.SetSizer(sizer)
        self.scr_win_slm_right.FitInside()
        return self.scr_win_slm_right

    def _build_streams_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the live streams fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        fp_slm_alignment_streams = self._fold_item(fold_bar, "STREAMS")
        self.pnl_slm_alignment_streams = StreamBar(
            fp_slm_alignment_streams,
            size=(300, -1),
            add_button=False,
        )
        self.pnl_slm_alignment_streams.SetForegroundColour(
            self._theme.text_muted
        )
        self.pnl_slm_alignment_streams.SetBackgroundColour(
            self._theme.background
        )
        fp_slm_alignment_streams.add_item(self.pnl_slm_alignment_streams)

    def _build_milling_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the fibucial milling fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        fp_slm_alignment_milling = self._fold_item(fold_bar, "MILLING")
        pnl_slm_fiducial_milling = wx.Panel(fp_slm_alignment_milling)
        pnl_slm_fiducial_milling.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.pnl_slm_milling_task = wx.Panel(pnl_slm_fiducial_milling)
            self.pnl_slm_milling_task.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.pnl_slm_milling_task,
                flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND,
                border=8,
            )

            self.txt_slm_milling_est_time = wx.StaticText(
                pnl_slm_fiducial_milling,
                label="Fiducial cross ready",
            )
            self.txt_slm_milling_est_time.SetForegroundColour(
                self._theme.text_primary
            )
            sizer.Add(
                self.txt_slm_milling_est_time,
                flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND,
                border=8,
            )

            self.btn_slm_run_milling = wx.Button(
                pnl_slm_fiducial_milling,
                label="Run Milling",
            )
            sizer.Add(
                self.btn_slm_run_milling,
                flag=wx.ALL | wx.EXPAND,
                border=8,
            )

            self.btn_slm_milling_cancel = wx.Button(
                pnl_slm_fiducial_milling,
                label="Cancel Milling",
            )
            sizer.Add(
                self.btn_slm_milling_cancel,
                flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
                border=8,
            )

        pnl_slm_fiducial_milling.SetSizer(sizer)
        fp_slm_alignment_milling.add_item(pnl_slm_fiducial_milling)

    def _fold_item(self, fold_bar: FoldPanelBar, label: str) -> FoldPanelItem:
        """Create and register a styled fold-panel item.

        :param fold_bar: Parent fold-panel bar.
        :param label: Caption label.
        :returns: Registered fold-panel item.
        """
        item = FoldPanelItem(fold_bar, label=label)
        item.SetForegroundColour(self._theme.button_text)
        item.SetBackgroundColour(self._theme.section_header)
        fold_bar.add_item(item)
        return item


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(DialogSlmAlignment)
