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

from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import LiveViewport, PointSpectrumViewport
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class PluginDialogBase(wx.Dialog):
    """Provide the plugin acquisition dialog layout.

    Plugins build the actual acquisition UI at runtime on top of the
    controls exposed here (description panel, viewports, settings fold
    panels, progress gauge, and button bar).
    """

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the description banner, viewports, and settings column.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(
            parent,
            title="Image Acquisition",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._theme = theme
        self.SetBackgroundColour(theme.viewport_background)
        set_font(self, 9)

        with vbox() as root_sizer:
            self.pnl_desc = self._build_description_panel()
            root_sizer.Add(self.pnl_desc, flag=wx.EXPAND)
            root_sizer.Add(
                self._build_content_row(),
                proportion=1,
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_description_panel(self) -> wx.Panel:
        """Build the description banner populated by the plugin at runtime.

        :returns: Description banner panel.
        """
        panel = wx.Panel(self)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.button_text)
        set_font(panel, 13)
        panel.SetSizer(wx.BoxSizer(wx.VERTICAL))
        return panel

    def _build_content_row(self) -> wx.Sizer:
        """Build the viewports and settings column row.

        :returns: Content row sizer.
        """
        with hbox() as sizer:
            self.viewport_l = LiveViewport(self)
            self.viewport_l.Hide()
            sizer.Add(self.viewport_l, proportion=1, flag=wx.EXPAND)

            self.viewport_r = LiveViewport(self)
            self.viewport_r.Hide()
            sizer.Add(self.viewport_r, proportion=1, flag=wx.EXPAND)

            self.spectrum_viewport = PointSpectrumViewport(self)
            self.spectrum_viewport.Hide()
            sizer.Add(self.spectrum_viewport, proportion=1, flag=wx.EXPAND)

            sizer.Add(self._build_settings_column(), flag=wx.EXPAND)

        return sizer

    def _build_settings_column(self) -> wx.Sizer:
        """Build the settings scroller, info bar, gauge, and button bar.

        :returns: Settings column sizer.
        """
        with vbox() as sizer:
            sizer.Add(
                self._build_scroller_panel(),
                proportion=1,
                flag=wx.EXPAND,
            )

            self.pnl_info = self._build_info_panel()
            self.pnl_info.Hide()
            sizer.Add(self.pnl_info, flag=wx.EXPAND)

            self.pnl_gauge = self._build_gauge_panel()
            self.pnl_gauge.Hide()
            sizer.Add(self.pnl_gauge, flag=wx.EXPAND)

            self.pnl_buttons = self._build_buttons_panel()
            sizer.Add(self.pnl_buttons, flag=wx.EXPAND)

        return sizer

    def _build_scroller_panel(self) -> wx.Panel:
        """Build the panel wrapping the scrollable settings window.

        :returns: Scroller wrapper panel.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            scroller_item = sizer.Add(
                self._build_settings_scroller(panel),
                proportion=1,
                flag=wx.EXPAND,
            )
            scroller_item.SetMinSize((400, 400))

        panel.SetSizer(sizer)
        return panel

    def _build_settings_scroller(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable settings window.

        :param parent: Parent window.
        :returns: Settings scrolled window.
        """
        scr_win_right = wx.ScrolledWindow(
            parent,
            size=(400, -1),
            style=wx.VSCROLL,
        )
        scr_win_right.SetBackgroundColour(self._theme.background)
        scr_win_right.EnableScrolling(False, True)
        scr_win_right.SetScrollbars(-1, 10, 1, 1)

        with vbox() as scroll_sizer:
            scroll_sizer.Add(self._build_fold_bar(scr_win_right), flag=wx.EXPAND)

        scr_win_right.SetSizer(scroll_sizer)
        scr_win_right.FitInside()
        return scr_win_right

    def _build_fold_bar(self, parent: wx.Window) -> FoldPanelBar:
        """Build the plugin settings and streams fold-panel bar.

        :param parent: Parent scrolled window.
        :returns: Fold-panel bar.
        """
        fold_bar = FoldPanelBar(parent)
        fold_bar.SetBackgroundColour(self._theme.background)

        self.fp_settings = FoldPanelItem(fold_bar, nocaption=True)
        self.fp_settings.SetForegroundColour(self._theme.button_text)
        self.fp_settings.SetBackgroundColour(self._theme.section_header)
        fold_bar.add_item(self.fp_settings)

        self.fp_streams = FoldPanelItem(fold_bar, label="STREAMS")
        self.fp_streams.SetForegroundColour(self._theme.button_text)
        self.fp_streams.SetBackgroundColour(self._theme.section_header)
        self.pnl_streams = StreamBar(self.fp_streams, size=(300, -1))
        self.pnl_streams.SetForegroundColour(self._theme.text_muted)
        self.pnl_streams.SetBackgroundColour(self._theme.background)
        self.fp_streams.add_item(self.pnl_streams)
        fold_bar.add_item(self.fp_streams)
        self.fp_streams.Hide()

        return fold_bar

    def _build_info_panel(self) -> wx.Panel:
        """Build the acquisition info banner.

        :returns: Info banner panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            self.lbl_acquisition_info = wx.StaticText(panel)
            self.lbl_acquisition_info.SetForegroundColour(self._theme.field_foreground)
            set_font(self.lbl_acquisition_info, self._theme.font_size_checklist)
            sizer.Add(
                self.lbl_acquisition_info,
                proportion=1,
                flag=wx.ALL | wx.EXPAND,
                border=10,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_gauge_panel(self) -> wx.Panel:
        """Build the progress gauge and cancel button panel.

        :returns: Progress gauge panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            self.gauge_progress = wx.Gauge(
                panel,
                range=100,
                size=(-1, 8),
                style=wx.GA_SMOOTH,
            )
            self.gauge_progress.SetValue(0)
            sizer.Add(
                self.gauge_progress,
                proportion=1,
                flag=wx.ALL | wx.EXPAND,
                border=10,
            )

            with hbox() as row:
                self.lbl_gauge = wx.StaticText(panel)
                self.lbl_gauge.SetForegroundColour(self._theme.field_foreground)
                set_font(self.lbl_gauge, self._theme.font_size_prominent_button)
                row.Add(
                    self.lbl_gauge,
                    proportion=1,
                    flag=wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND,
                    border=5,
                )

                self.btn_cancel = ImageTextButton(
                    panel,
                    label="cancel",
                    height=24,
                    face_colour="def",
                )
                self.btn_cancel.SetForegroundColour(self._theme.button_text)
                self.btn_cancel.Disable()
                row.Add(
                    self.btn_cancel,
                    flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                    border=5,
                )

            sizer.Add(row, flag=wx.LEFT | wx.RIGHT | wx.EXPAND, border=5)

        panel.SetSizer(sizer)
        return panel

    def _build_buttons_panel(self) -> wx.Panel:
        """Build the panel that plugins populate with action buttons.

        :returns: Button bar panel.
        """
        panel = wx.Panel(self, size=(-1, 68))
        panel.SetBackgroundColour(self._theme.panel_background)
        panel.SetSizer(wx.BoxSizer(wx.HORIZONTAL))
        return panel


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PluginDialogBase)
