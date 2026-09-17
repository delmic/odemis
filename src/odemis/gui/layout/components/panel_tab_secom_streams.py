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
from odemis.gui import img
from odemis.gui.comp.buttons import (
    ImageButton,
    ImageTextToggleButton,
    ViewButton,
)
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import (
    ChronographViewport,
    FixedOverviewViewport,
    LiveViewport,
)
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants.strings import (
    LABEL_CHAMBER,
    LABEL_OPTICAL_SETTINGS,
    LABEL_SEM_SETTINGS,
    LABEL_STREAMS,
    TOOLTIP_OPEN_LOG_PANEL,
)
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_text_button


class PnlTabSecomStreams(wx.Panel):
    """Provide the SECOM streams tab layout."""

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the SECOM streams controls and viewports.

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
        """Build the toolbar and viewport selector column.

        :returns: Left control column.
        """
        panel = wx.Panel(self, size=(200, -1))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as outer_sizer:
            outer_sizer.Add(
                self._build_view_selector(panel),
                proportion=1,
                flag=wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            outer_sizer.Add(
                self._build_log_button(panel),
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(outer_sizer)
        return panel

    def _build_view_selector(self, parent: wx.Window) -> wx.Sizer:
        """Build the viewport selector labels, buttons, and toolbar.

        :param parent: Parent window.
        :returns: View selector sizer.
        """
        with vbox() as selector_sizer:
            selector_sizer.Add(
                self._build_view_selector_label_row(
                    parent,
                    "lbl_secom_overview",
                    top_padding=False,
                ),
                flag=wx.TOP | wx.RIGHT | wx.ALIGN_RIGHT,
                border=18,
            )
            self.btn_secom_overview = self._view_button(parent)
            selector_sizer.Add(
                self.btn_secom_overview,
                flag=wx.BOTTOM | wx.ALIGN_RIGHT,
                border=6,
            )

            selector_sizer.AddStretchSpacer()
            self.secom_toolbar = ToolBar(parent, style=wx.VERTICAL)
            selector_sizer.Add(
                self.secom_toolbar,
                flag=wx.ALIGN_RIGHT,
            )
            selector_sizer.AddStretchSpacer()

            selector_specs = (
                ("lbl_secom_view_all", "btn_secom_view_all", False, True),
                ("lbl_secom_view_tl", "btn_secom_view_tl", True, True),
                ("lbl_secom_view_tr", "btn_secom_view_tr", True, True),
                ("lbl_secom_view_bl", "btn_secom_view_bl", True, True),
                ("lbl_secom_view_br", "btn_secom_view_br", True, False),
            )
            for label_name, button_name, top_border, bottom_border in selector_specs:
                selector_sizer.Add(
                    self._build_view_selector_label_row(
                        parent,
                        label_name,
                        top_padding=top_border,
                    ),
                    flag=wx.RIGHT | wx.ALIGN_RIGHT,
                    border=18,
                )

                button = self._view_button(parent)
                setattr(self, button_name, button)
                selector_sizer.Add(
                    button,
                    flag=(wx.BOTTOM if bottom_border else 0) | wx.ALIGN_RIGHT,
                    border=6 if bottom_border else 0,
                )

        return selector_sizer

    def _build_view_selector_label_row(
        self,
        parent: wx.Window,
        attribute: str,
        top_padding: bool,
    ) -> wx.Sizer:
        """Build a vertical sizer containing one view selector label.

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
        """Build the SECOM viewport grid.

        :returns: Viewport grid.
        """
        self.pnl_secom_grid = ViewportGrid(self)
        self.vp_secom_tl = LiveViewport(self.pnl_secom_grid)
        self.vp_secom_tr = LiveViewport(self.pnl_secom_grid)
        self.vp_secom_bl = LiveViewport(self.pnl_secom_grid)
        self.vp_secom_br = LiveViewport(self.pnl_secom_grid)
        self.vp_flim_chronograph = ChronographViewport(self.pnl_secom_grid)
        self.vp_overview_sem = FixedOverviewViewport(self.pnl_secom_grid)

        viewports = (
            self.vp_secom_tl,
            self.vp_secom_tr,
            self.vp_secom_bl,
            self.vp_secom_br,
            self.vp_flim_chronograph,
            self.vp_overview_sem,
        )
        for viewport in viewports[:4]:
            viewport.SetForegroundColour(self._theme.text_secondary)
            viewport.SetBackgroundColour(self._theme.viewport_background)

        self.vp_flim_chronograph.Hide()
        self.vp_overview_sem.Hide()

        # Controllers access the viewport tuple before the first size event.
        self.pnl_secom_grid.viewports = viewports
        return self.pnl_secom_grid

    def _build_settings_column(self) -> wx.Panel:
        """Build the hardware controls, settings, and acquisition column.

        :returns: Right settings column.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.main_buttons = self._build_main_buttons(panel)
            sizer.Add(self.main_buttons, flag=wx.EXPAND)
            sizer.Add(
                self._build_settings_scroller(panel),
                proportion=1,
                flag=wx.EXPAND,
            )
            sizer.Add(self._build_acquisition_panel(panel), flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_main_buttons(self, parent: wx.Window) -> wx.Panel:
        """Build chamber, optical, SEM, and status controls.

        :param parent: Parent window.
        :returns: Main buttons panel.
        """
        panel = wx.Panel(parent, size=(400, -1))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_hardware_buttons(panel),
                flag=wx.EXPAND,
            )
            self.pnl_hw_info = self._build_hardware_info(panel)
            self.pnl_hw_info.Hide()
            sizer.Add(self.pnl_hw_info, flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_hardware_buttons(self, parent: wx.Window) -> wx.Sizer:
        """Build the chamber, optical, and SEM toggle button row.

        :param parent: Parent window.
        :returns: Hardware button row.
        """
        with hbox() as sizer:
            self.btn_press = self._toggle_button(
                parent,
                label=LABEL_CHAMBER,
                icon="ico_press.png",
                size=(130, -1),
                style=wx.ALIGN_LEFT,
            )
            sizer.Add(
                self.btn_press,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.AddStretchSpacer()

            self.btn_opt = self._toggle_button(
                parent,
                label="OPTICAL",
                icon="ico_optical.png",
                icon_on="ico_optical_green.png",
            )
            sizer.Add(
                self.btn_opt,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT,
                border=self._theme.spacing_standard,
            )

            self.btn_sem = self._toggle_button(
                parent,
                label="SEM",
                icon="ico_sem.png",
                icon_on="ico_sem_green.png",
            )
            sizer.Add(
                self.btn_sem,
                flag=wx.ALL,
                border=self._theme.spacing_standard,
            )

        return sizer

    def _build_hardware_info(self, parent: wx.Window) -> wx.Panel:
        """Build the hardware progress and stream status panels.

        :param parent: Parent window.
        :returns: Hardware information panel.
        """
        panel = wx.Panel(parent, size=(-1, 24))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.pnl_load_status = self._build_load_status(panel)
            self.pnl_load_status.Hide()
            sizer.Add(
                self.pnl_load_status,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.pnl_stream_status = self._build_stream_status(panel)
            self.pnl_stream_status.Hide()
            sizer.Add(
                self.pnl_stream_status,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_load_status(self, parent: wx.Window) -> wx.Panel:
        """Build the chamber loading progress row.

        :param parent: Parent window.
        :returns: Load status panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            self.gauge_load_time = wx.Gauge(
                panel,
                range=100,
                size=(-1, 10),
                style=wx.GA_SMOOTH,
            )
            self.gauge_load_time.SetValue(0)
            sizer.Add(
                self.gauge_load_time,
                proportion=1,
                flag=wx.BOTTOM | wx.RIGHT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.lbl_load_time = wx.StaticText(
                panel,
                label="test st s",
                size=(100, -1),
                style=wx.ALIGN_RIGHT | wx.ST_NO_AUTORESIZE,
            )
            self.lbl_load_time.SetForegroundColour(self._theme.text_primary)
            sizer.Add(
                self.lbl_load_time,
                flag=wx.BOTTOM,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_stream_status(self, parent: wx.Window) -> wx.Panel:
        """Build the stream status icons and label.

        :param parent: Parent window.
        :returns: Stream status panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            icon_specs = (
                ("bmp_stream_status_info", "dialog_info.png"),
                ("bmp_stream_status_warn", "dialog_warning.png"),
                ("bmp_stream_status_error", "dialog_error.png"),
            )
            for attribute, icon_name in icon_specs:
                bitmap = wx.StaticBitmap(
                    panel,
                    bitmap=img.getBitmap(f"icon/{icon_name}"),
                )
                bitmap.Hide()
                setattr(self, attribute, bitmap)
                sizer.Add(
                    bitmap,
                    flag=wx.RIGHT,
                    border=5,
                )

            self.lbl_stream_status = wx.StaticText(panel)
            self.lbl_stream_status.SetForegroundColour(
                self._theme.text_primary
            )
            sizer.Add(self.lbl_stream_status)

        panel.SetSizer(sizer)
        return panel

    def _build_settings_scroller(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable settings fold panels.

        :param parent: Parent window.
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

            self._build_settings_sections(fold_bar)

        self.scr_win_right.SetSizer(scroll_sizer)
        self.scr_win_right.FitInside()
        return self.scr_win_right

    def _build_settings_sections(self, fold_bar: FoldPanelBar) -> None:
        """Build the optical, SEM, and stream settings sections.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_settings_secom_optical = self._fold_item(
            fold_bar,
            LABEL_OPTICAL_SETTINGS,
        )

        opt_streams_item = self._fold_item(fold_bar, nocaption=True)
        self.pnl_opt_streams = self._stream_bar(
            opt_streams_item,
            add_button=False,
        )
        opt_streams_item.add_item(self.pnl_opt_streams)

        self.fp_settings_secom_sem = self._fold_item(
            fold_bar,
            LABEL_SEM_SETTINGS,
        )

        streams_item = self._fold_item(fold_bar, LABEL_STREAMS)
        self.pnl_secom_streams = self._stream_bar(
            streams_item,
            size=(300, -1),
            add_button=True,
        )
        self.pnl_secom_streams.btn_add_stream.SetBackgroundColour(
            self._theme.background
        )
        streams_item.add_item(self.pnl_secom_streams)

    def _build_acquisition_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the bottom acquisition action panel.

        :param parent: Parent window.
        :returns: Acquisition panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            self.btn_secom_acquire = create_text_button(
                panel,
                "ACQUIRE IMAGE",
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_acqui.png",
                size=(382, -1),
                style=wx.ALIGN_CENTRE,
                contrast=True,
            )
            sizer.Add(
                self.btn_secom_acquire,
                flag=wx.ALL,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _view_button(self, parent: wx.Window) -> ViewButton:
        """Create a default view selector button.

        :param parent: Parent window.
        :returns: View selector button.
        """
        return ViewButton(parent, face_colour="def")

    def _toggle_button(
        self,
        parent: wx.Window,
        label: str,
        icon: str,
        icon_on: Optional[str] = None,
        size: Any = wx.DefaultSize,
        style: int = 0,
    ) -> ImageTextToggleButton:
        """Create a default-face image text toggle button.

        :param parent: Parent window.
        :param label: Button label.
        :param icon: Icon file name.
        :param icon_on: Optional active icon file name.
        :param size: Explicit button size.
        :param style: wx window style.
        :returns: Toggle button.
        """
        button = ImageTextToggleButton(
            parent,
            label=label,
            icon=img.getBitmap(f"icon/{icon}"),
            icon_on=(
                img.getBitmap(f"icon/{icon_on}")
                if icon_on is not None
                else wx.NullBitmap
            ),
            height=48,
            face_colour="def",
            size=size,
            style=style,
        )
        button.SetForegroundColour(self._theme.button_text)
        set_font(button, self._theme.font_size_button)
        return button

    def _stream_bar(
        self,
        parent: wx.Window,
        add_button: bool,
        size: Any = wx.DefaultSize,
    ) -> StreamBar:
        """Create a styled stream bar.

        :param parent: Parent window.
        :param add_button: Whether to show the add-stream button.
        :param size: Explicit stream bar size.
        :returns: Stream bar.
        """
        stream_bar = StreamBar(
            parent,
            size=size,
            add_button=add_button,
        )
        stream_bar.SetForegroundColour(self._theme.text_muted)
        stream_bar.SetBackgroundColour(self._theme.background)
        return stream_bar

    def _fold_item(
        self,
        fold_bar: FoldPanelBar,
        label: str = "",
        nocaption: bool = False,
    ) -> FoldPanelItem:
        """Create and register a styled fold-panel item.

        :param fold_bar: Parent fold-panel bar.
        :param label: Caption label.
        :param nocaption: Whether the item omits its caption bar.
        :returns: Registered fold-panel item.
        """
        item = FoldPanelItem(fold_bar, label=label, nocaption=nocaption)
        item.SetForegroundColour(self._theme.button_text)
        item.SetBackgroundColour(self._theme.section_header)
        fold_bar.add_item(item)
        return item


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabSecomStreams)
