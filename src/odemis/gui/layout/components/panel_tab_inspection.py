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

from typing import Optional, Type

import wx

import odemis.gui.layout as layout
from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, ViewButton
from odemis.gui.comp.foldpanelbar import FoldPanelBar
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import (
    AngularResolvedViewport,
    AngularSpectrumViewport,
    ChronographViewport,
    LineSpectrumViewport,
    MicroscopeViewport,
    PointSpectrumViewport,
    TemporalSpectrumViewport,
    ThetaViewport,
    ViewPort,
)
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants.strings import (
    LABEL_STREAMS,
    TOOLTIP_OPEN_LOG_PANEL,
)
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import (
    create_fold_item,
    create_text_button,
)


class PnlTabInspection(wx.Panel):
    """Provide the analysis and gallery inspection tab layout."""

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the inspection controls, viewports, and settings.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_view_controls(),
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_viewport_grid(),
                proportion=1,
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_settings_column(),
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_view_controls(self) -> wx.Panel:
        """Build the file-open button, toolbar, and viewport selectors.

        :returns: Left control column.
        """
        panel = wx.Panel(self, size=(200, -1))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.btn_open_image = create_text_button(panel, "Select image...", height=24, text_colour=self._theme.button_text, contrast_text_colour=self._theme.button_text_contrast)
            sizer.Add(
                self.btn_open_image,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.AddStretchSpacer()

            self.ana_toolbar = ToolBar(
                panel,
                style=wx.VERTICAL | wx.HORIZONTAL,
            )
            sizer.Add(self.ana_toolbar, flag=wx.ALIGN_RIGHT)
            sizer.AddStretchSpacer()

            sizer.Add(
                self._build_view_selector(panel),
                proportion=1,
                flag=wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_log_button(panel),
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_view_selector(self, parent: wx.Window) -> wx.Sizer:
        """Build the inspection viewport selector controls.

        :param parent: Parent window.
        :returns: View selector sizer.
        """
        with vbox() as selector_sizer:
            selectors = (
                ("lbl_inspection_view_all", "btn_inspection_view_all"),
                ("lbl_inspection_view_tl", "btn_inspection_view_tl"),
                ("lbl_inspection_view_tr", "btn_inspection_view_tr"),
                ("lbl_inspection_view_bl", "btn_inspection_view_bl"),
                ("lbl_inspection_view_br", "btn_inspection_view_br"),
            )
            for index, (label_name, button_name) in enumerate(selectors):
                selector_sizer.Add(
                    self._build_view_selector_label_row(
                        parent,
                        label_name,
                        top_padding=index > 0,
                    ),
                    flag=wx.RIGHT | wx.ALIGN_RIGHT,
                    border=18,
                )

                button = ViewButton(parent, face_colour="def")
                setattr(self, button_name, button)
                selector_sizer.Add(
                    button,
                    flag=(
                        wx.ALIGN_RIGHT
                        | (wx.BOTTOM if index < len(selectors) - 1 else 0)
                    ),
                    border=6,
                )

        return selector_sizer

    def _build_view_selector_label_row(
        self,
        parent: wx.Window,
        attribute: str,
        top_padding: bool,
    ) -> wx.Sizer:
        """Build one selector label inside its nested vertical sizer.

        :param parent: Parent window.
        :param attribute: Public attribute name for the label.
        :param top_padding: Whether the label has top padding.
        :returns: Label sizer.
        """
        with vbox() as label_sizer:
            label = wx.StaticText(parent, label="view")
            label.SetForegroundColour(self._theme.text_secondary)
            setattr(self, attribute, label)
            label_sizer.Add(
                label,
                flag=wx.BOTTOM | (wx.TOP if top_padding else 0),
                border=2,
            )
        return label_sizer

    def _build_log_button(self, parent: wx.Window) -> ImageButton:
        """Build the log panel toggle button.

        :param parent: Parent window.
        :returns: Log button.
        """
        self.btn_log = ImageButton(
            parent,
            icon=img.getBitmap("icon/ico_chevron_up.png"),
            height=16,
            face_colour="def",
            style=wx.ALIGN_CENTRE,
        )
        self.btn_log.SetToolTip(TOOLTIP_OPEN_LOG_PANEL)
        return self.btn_log

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the analysis viewport grid.

        :returns: Viewport grid.
        """
        self.pnl_inspection_grid = ViewportGrid(self)

        self.vp_inspection_tl = self._viewport(
            self.pnl_inspection_grid,
            MicroscopeViewport,
        )
        self.vp_inspection_tr = self._viewport(
            self.pnl_inspection_grid,
            MicroscopeViewport,
        )
        self.vp_inspection_bl = self._viewport(
            self.pnl_inspection_grid,
            MicroscopeViewport,
        )
        self.vp_inspection_br = self._viewport(
            self.pnl_inspection_grid,
            MicroscopeViewport,
        )
        self.vp_angular = self._hidden_viewport(
            self.pnl_inspection_grid,
            AngularResolvedViewport,
        )
        self.vp_inspection_plot = self._hidden_viewport(
            self.pnl_inspection_grid,
            PointSpectrumViewport,
        )
        self.vp_linespec = self._hidden_viewport(
            self.pnl_inspection_grid,
            LineSpectrumViewport,
        )
        self.vp_temporalspec = self._hidden_viewport(
            self.pnl_inspection_grid,
            TemporalSpectrumViewport,
        )
        self.vp_timespec = self._hidden_viewport(
            self.pnl_inspection_grid,
            ChronographViewport,
        )
        self.vp_angular_pol = self._hidden_viewport(
            self.pnl_inspection_grid,
            AngularResolvedViewport,
        )
        self.vp_angularspec = self._hidden_viewport(
            self.pnl_inspection_grid,
            AngularSpectrumViewport,
        )
        self.vp_thetaspec = self._hidden_viewport(
            self.pnl_inspection_grid,
            ThetaViewport,
        )

        self.pnl_inspection_grid.viewports = (
            self.vp_inspection_tl,
            self.vp_inspection_tr,
            self.vp_inspection_bl,
            self.vp_inspection_br,
            self.vp_angular,
            self.vp_inspection_plot,
            self.vp_linespec,
            self.vp_temporalspec,
            self.vp_timespec,
            self.vp_angular_pol,
            self.vp_angularspec,
            self.vp_thetaspec,
        )
        return self.pnl_inspection_grid

    def _build_settings_column(self) -> wx.Panel:
        """Build the file info, streams, and export settings column.

        :returns: Right settings column.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_settings_scroller(panel),
                proportion=1,
                flag=wx.EXPAND,
            )
            sizer.Add(
                self._build_export_panel(panel),
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_settings_scroller(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable file info and stream settings.

        :param parent: Parent settings panel.
        :returns: Settings scrolled window.
        """
        self.scr_win_right = wx.ScrolledWindow(
            parent,
            size=(400, -1),
            style=wx.VSCROLL,
        )
        self.scr_win_right.SetMinSize((400, 400))
        self.scr_win_right.SetBackgroundColour(self._theme.background)
        self.scr_win_right.EnableScrolling(False, True)
        self.scr_win_right.SetScrollbars(-1, 10, 1, 1)

        with vbox() as scroll_sizer:
            fold_bar = FoldPanelBar(self.scr_win_right)
            fold_bar.SetBackgroundColour(self._theme.background)
            scroll_sizer.Add(fold_bar, flag=wx.EXPAND)

            self._build_file_info_section(fold_bar)
            self._build_streams_section(fold_bar)

        self.scr_win_right.SetSizer(scroll_sizer)
        self.scr_win_right.FitInside()
        return self.scr_win_right

    def _build_file_info_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the file information fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_fileinfo = create_fold_item(
            fold_bar,
            "FILE INFO",
            text_colour=self._theme.button_text,
            background_colour=self._theme.section_header,
        )

    def _build_streams_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the analysis streams fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        streams_item = create_fold_item(
            fold_bar,
            LABEL_STREAMS,
            text_colour=self._theme.button_text,
            background_colour=self._theme.section_header,
        )
        self.pnl_inspection_streams = StreamBar(
            streams_item,
            size=(300, -1),
            add_button=True,
        )
        self.pnl_inspection_streams.SetForegroundColour(
            self._theme.text_muted
        )
        self.pnl_inspection_streams.SetBackgroundColour(
            self._theme.background
        )
        self.pnl_inspection_streams.btn_add_stream.SetBackgroundColour(
            self._theme.background
        )
        streams_item.add_item(self.pnl_inspection_streams)

    def _build_export_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the export action panel.

        :param parent: Parent settings panel.
        :returns: Export panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            self.btn_export = create_text_button(panel, "EXPORT IMAGE", height=48, text_colour=self._theme.button_text, contrast_text_colour=self._theme.button_text_contrast, face_colour="blue", icon="ico_export.png", size=(382, -1), contrast=True)
            sizer.Add(
                self.btn_export,
                flag=wx.ALL,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _viewport(
        self,
        parent: wx.Window,
        cls: Type[ViewPort],
    ) -> ViewPort:
        """Create a themed analysis viewport.

        :param parent: Parent viewport grid.
        :param cls: Viewport class to instantiate.
        :returns: Themed viewport.
        """
        viewport = cls(parent)
        viewport.SetForegroundColour(self._theme.text_secondary)
        viewport.SetBackgroundColour(self._theme.viewport_background)
        return viewport

    def _hidden_viewport(
        self,
        parent: wx.Window,
        cls: Type[ViewPort],
    ) -> ViewPort:
        """Create an initially hidden analysis viewport.

        :param parent: Parent viewport grid.
        :param cls: Viewport class to instantiate.
        :returns: Hidden viewport.
        """
        viewport = cls(parent)
        viewport.Hide()
        return viewport


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabInspection)
