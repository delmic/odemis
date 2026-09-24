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

from typing import Any, Optional

import wx

import odemis.gui.layout as layout
from odemis.gui.comp.foldpanelbar import FoldPanelBar
from odemis.gui.comp.slider import UnitIntegerSlider
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.text import UnitFloatCtrl, UnitIntegerCtrl
from odemis.gui.comp.viewport import LiveViewport
from odemis.gui.layout.constants.strings import (
    LABEL_ACQUIRE_OVERVIEW,
    LABEL_CLOSE,
    LABEL_OPTICAL_SETTINGS,
    LABEL_STREAMS,
)
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_fold_item, create_label, create_text_button, \
    size_window_to_available_space


class OverviewAcqDialogBase(wx.Dialog):
    """Provide the overview acquisition dialog layout."""

    def __init__(self, parent: Optional[wx.Window], theme: Optional[Theme] = None) -> None:
        """Create the viewport, settings column, and action rows.

        :param parent: Parent window, or None to size against the display.
        :param theme: Semantic layout theme.
        """
        super().__init__(
            parent,
            title="Overview Acquisition",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(self._theme.viewport_background)
        set_font(self, 9)

        root_sizer = wx.FlexGridSizer(cols=2, rows=2, vgap=0, hgap=0)
        root_sizer.AddGrowableCol(0)
        root_sizer.AddGrowableRow(0)

        root_sizer.Add(self._build_viewport(), flag=wx.EXPAND)
        root_sizer.Add(self._build_settings_column(), flag=wx.EXPAND)
        root_sizer.Add(self._build_progress_panel(), flag=wx.EXPAND)
        root_sizer.Add(self._build_action_panel(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        size_window_to_available_space(self, parent)

    def _build_viewport(self) -> LiveViewport:
        """Build the overview acquisition viewport.

        :returns: Overview viewport.
        """
        self.pnl_view_acq = LiveViewport(self)
        return self.pnl_view_acq

    def _build_settings_column(self) -> wx.Panel:
        """Build the acquisition settings column.

        :returns: Settings column panel.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            main_buttons = wx.Panel(panel, size=(400, -1))
            main_buttons.SetForegroundColour(self._theme.field_foreground)
            main_buttons.SetBackgroundColour(self._theme.field_background)
            sizer.Add(main_buttons, flag=wx.EXPAND)

            self.scr_win_right = self._build_scrolled_settings(panel)
            sizer.Add(
                self.scr_win_right,
                proportion=1,
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_scrolled_settings(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable optical settings, streams, and tiling controls.

        :param parent: Parent settings column.
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
            fold_bar = FoldPanelBar(scroll_window)
            fold_bar.SetBackgroundColour(self._theme.background)
            scroll_sizer.Add(fold_bar, flag=wx.EXPAND)

            self.fp_settings_secom_optical = create_fold_item(
                fold_bar,
                LABEL_OPTICAL_SETTINGS,
                text_colour=self._theme.button_text,
                background_colour=self._theme.section_header,
            )

            streams_item = create_fold_item(
                fold_bar,
                LABEL_STREAMS,
                text_colour=self._theme.button_text,
                background_colour=self._theme.section_header,
            )
            self.pnl_secom_streams = self._stream_bar(
                streams_item, size=(300, -1)
            )
            streams_item.add_item(self.pnl_secom_streams)

            scroll_sizer.Add(
                self._build_zstack_grid(scroll_window),
                flag=wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=5,
            )

            self.whole_grid_chkbox = wx.CheckBox(
                scroll_window, label="Whole grid acquisition"
            )
            self.whole_grid_chkbox.SetForegroundColour(self._theme.text_primary)
            scroll_sizer.Add(
                self.whole_grid_chkbox, flag=wx.LEFT, border=self._theme.spacing_standard
            )

            scroll_sizer.Add(
                self._build_tiles_grid(scroll_window),
                flag=wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=5,
            )

            self.selected_grid_lbl = create_label(
                scroll_window,
                "Selected grid areas",
                self._theme.text_primary,
                9,
            )
            scroll_sizer.Add(
                self.selected_grid_lbl, flag=wx.LEFT, border=13
            )

            self.selected_grid_pnl_holder = wx.Panel(scroll_window)
            self.selected_grid_pnl_holder.SetBackgroundColour(self._theme.background)
            scroll_sizer.Add(
                self.selected_grid_pnl_holder,
                flag=wx.EXPAND | wx.LEFT | wx.RIGHT,
                border=13,
            )

            scroll_sizer.Add(
                self._build_area_size_grid(scroll_window),
                flag=wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=5,
            )

            self.autofocus_chkbox = wx.CheckBox(scroll_window, label="Run AutoFocus")
            self.autofocus_chkbox.SetForegroundColour(self._theme.text_primary)
            scroll_sizer.Add(
                self.autofocus_chkbox, flag=wx.LEFT, border=self._theme.spacing_standard
            )

            scroll_sizer.Add(self._build_focus_points_row(scroll_window))

        scroll_window.SetSizer(scroll_sizer)
        scroll_window.FitInside()
        return scroll_window

    def _build_zstack_grid(self, parent: wx.Window) -> wx.GridBagSizer:
        """Build the Z-stack steps and step-size row.

        :param parent: Parent scrolled window.
        :returns: Z-stack settings grid.
        """
        grid = wx.GridBagSizer(vgap=10, hgap=50)

        self.zstack_steps_label = create_label(
            parent,
            "ZStack steps",
            self._theme.text_primary,
            9,
        )
        grid.Add(self.zstack_steps_label, pos=(0, 0), flag=wx.LEFT, border=13)

        self.zstack_steps = UnitIntegerSlider(
            parent,
            value=21,
            min_val=1,
            max_val=51,
            size=(-1, 20),
            style=wx.BORDER_NONE,
            unit="",
        )
        self.zstack_steps.SetForegroundColour(self._theme.text_primary)
        grid.Add(
            self.zstack_steps,
            pos=(0, 1),
            flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
            border=10,
        )

        self.zstep_size_label = create_label(
            parent,
            "Zstep size",
            self._theme.text_primary,
            9,
        )
        grid.Add(self.zstep_size_label, pos=(1, 0), flag=wx.LEFT, border=13)

        self.zstep_size_ctrl = self._float_ctrl(
            parent,
            value=1,
            min_val=0,
            max_val=1,
            unit="m",
            key_step=0.000001,
            accuracy=3,
            font_size=9,
        )
        grid.Add(
            self.zstep_size_ctrl, pos=(1, 1), flag=wx.LEFT, border=10
        )
        grid.AddGrowableCol(1)
        return grid

    def _build_tiles_grid(self, parent: wx.Window) -> wx.GridBagSizer:
        """Build the tile-count row for a non-whole-grid acquisition.

        :param parent: Parent scrolled window.
        :returns: Tile-count settings grid.
        """
        grid = wx.GridBagSizer(vgap=10, hgap=50)

        tiles_number_x_lbl = create_label(
            parent,
            "Tiles number x",
            self._theme.text_primary,
            9,
        )
        grid.Add(tiles_number_x_lbl, pos=(0, 0), flag=wx.LEFT, border=13)

        self.tiles_number_x = self._integer_ctrl(parent, value=10, min_val=1, max_val=1000)
        grid.Add(
            self.tiles_number_x,
            pos=(0, 1),
            flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
            border=10,
        )

        tiles_number_y_lbl = create_label(
            parent,
            "Tiles number y",
            self._theme.text_primary,
            9,
        )
        grid.Add(tiles_number_y_lbl, pos=(1, 0), flag=wx.LEFT, border=13)

        self.tiles_number_y = self._integer_ctrl(parent, value=10, min_val=1, max_val=1000)
        grid.Add(
            self.tiles_number_y,
            pos=(1, 1),
            flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
            border=10,
        )
        grid.AddGrowableCol(1)
        return grid

    def _build_area_size_grid(self, parent: wx.Window) -> wx.GridBagSizer:
        """Build the tiled-area size display row.

        :param parent: Parent scrolled window.
        :returns: Tiled-area size grid.
        """
        grid = wx.GridBagSizer(vgap=10, hgap=50)

        area_size_lbl = create_label(
            parent,
            "Tiled area size",
            self._theme.text_primary,
            9,
        )
        grid.Add(area_size_lbl, pos=(0, 0), flag=wx.LEFT, border=13)

        self.area_size_txt = wx.StaticText(parent, label="...")
        self.area_size_txt.SetForegroundColour(self._theme.text_disabled)
        set_font(self.area_size_txt, 9)
        grid.Add(self.area_size_txt, pos=(0, 1), flag=wx.LEFT, border=10)
        grid.AddGrowableCol(1)
        return grid

    def _build_focus_points_row(self, parent: wx.Window) -> wx.BoxSizer:
        """Build the distance-between-focus-points row.

        :param parent: Parent scrolled window.
        :returns: Focus-points distance row.
        """
        with hbox() as sizer:
            self.focus_points_dist_lbl = create_label(
                parent,
                "Distance between Focus Points",
                self._theme.text_primary,
                9,
            )
            sizer.Add(
                self.focus_points_dist_lbl, flag=wx.LEFT, border=self._theme.spacing_standard
            )

            self.focus_points_dist_ctrl = self._float_ctrl(
                parent,
                value=0.0,
                unit="m",
                accuracy=4,
                font_size=9,
            )
            sizer.Add(
                self.focus_points_dist_ctrl,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=10,
            )
        return sizer

    def _build_progress_panel(self) -> wx.Panel:
        """Build the acquisition progress gauge and estimate label row.

        :returns: Progress panel.
        """
        panel = wx.Panel(self, size=(-1, 60))
        panel.SetBackgroundColour(self._theme.field_background)

        with hbox() as sizer:
            self.gauge_acq = wx.Gauge(
                panel,
                size=(-1, 10),
                range=100,
                style=wx.GA_SMOOTH,
            )
            self.gauge_acq.SetValue(50)
            self.gauge_acq.Hide()
            sizer.Add(
                self.gauge_acq,
                proportion=1,
                flag=wx.ALL | wx.EXPAND,
                border=30,
            )

            self.lbl_acqestimate = wx.StaticText(
                panel,
                label="Estimated acquisition time is 9999 seconds",
                style=wx.ALIGN_RIGHT,
            )
            self.lbl_acqestimate.SetForegroundColour(self._theme.field_foreground)
            set_font(self.lbl_acqestimate, self._theme.font_size_prominent_button)
            sizer.Add(self.lbl_acqestimate, flag=wx.ALL, border=23)

        panel.SetSizer(sizer)
        return panel

    def _build_action_panel(self) -> wx.Panel:
        """Build the bottom close/acquire action row.

        :returns: Action panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour(self._theme.panel_background)

        with hbox() as sizer:
            self.btn_cancel = create_text_button(
                panel,
                LABEL_CLOSE,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                font_size=self._theme.font_size_prominent_button,
            )
            sizer.Add(
                self.btn_cancel,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT | wx.EXPAND,
                border=10,
            )

            self.btn_secom_acquire = create_text_button(
                panel,
                LABEL_ACQUIRE_OVERVIEW,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_acqui.png",
                size=(242, 48),
                font_size=self._theme.font_size_prominent_button,
                contrast=True,
            )
            sizer.Add(
                self.btn_secom_acquire,
                proportion=2,
                flag=wx.ALL | wx.EXPAND,
                border=10,
            )
        panel.SetSizer(sizer)
        return panel

    def _stream_bar(self, parent: wx.Window, size: Any = wx.DefaultSize) -> StreamBar:
        """Create a styled stream bar without an add-stream button.

        :param parent: Parent window.
        :param size: Explicit stream bar size.
        :returns: Stream bar.
        """
        stream_bar = StreamBar(parent, size=size, add_button=False)
        stream_bar.SetForegroundColour(self._theme.text_muted)
        stream_bar.SetBackgroundColour(self._theme.background)
        return stream_bar

    def _integer_ctrl(
        self,
        parent: wx.Window,
        value: int,
        min_val: int,
        max_val: int,
    ) -> UnitIntegerCtrl:
        """Create a unit-aware integer control.

        :param parent: Parent window.
        :param value: Initial value.
        :param min_val: Minimum accepted value.
        :param max_val: Maximum accepted value.
        :returns: Unit-aware integer control.
        """
        control = UnitIntegerCtrl(
            parent,
            value=value,
            size=(-1, 15),
            style=wx.BORDER_NONE,
            unit="",
            min_val=min_val,
            max_val=max_val,
        )
        set_font(control, 9)
        return control

    def _float_ctrl(
        self,
        parent: wx.Window,
        value: float,
        unit: str,
        accuracy: int,
        min_val: float = 0.0,
        max_val: float = 0.0,
        key_step: float = 0.0,
        font_size: Optional[int] = None,
    ) -> UnitFloatCtrl:
        """Create a unit-aware floating-point control.

        :param parent: Parent window.
        :param value: Initial numeric value.
        :param unit: Displayed unit string.
        :param accuracy: Significant-digit accuracy.
        :param min_val: Minimum accepted value.
        :param max_val: Maximum accepted value.
        :param key_step: Keyboard increment.
        :param font_size: Optional font point size.
        :returns: Unit-aware control.
        """
        control = UnitFloatCtrl(
            parent,
            value=value,
            size=(-1, 15),
            style=wx.BORDER_NONE,
            unit=unit,
            min_val=min_val,
            max_val=max_val,
            key_step=key_step,
            accuracy=accuracy,
        )
        if font_size is not None:
            set_font(control, font_size)
        return control


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(OverviewAcqDialogBase)
