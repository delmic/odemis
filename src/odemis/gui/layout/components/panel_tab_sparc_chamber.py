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
from odemis.gui.comp.buttons import (
    ImageButton,
    ImageTextToggleButton,
)
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import ARAcquiViewport
from odemis.gui.layout.constants.strings import (
    LABEL_CANCEL,
    TOOLTIP_OPEN_LOG_PANEL,
)
import odemis.gui.layout as layout
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_text_button


class PnlTabSparcChamber(wx.Panel):
    """Provide the SPARC2 chamber tab layout."""

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the mirror controls and chamber viewport.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_control_column(),
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            root_sizer.Add(
                self._build_chamber_viewport(),
                proportion=1,
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_settings_column(),
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_control_column(self) -> wx.Panel:
        """Build the left-hand mirror control column.

        :returns: Control column panel.
        """
        panel = wx.Panel(self, size=(300, -1))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.btn_switch_mirror = self._toggle_button(
                panel,
                "PARK MIRROR",
                icon="ico_eject.png",
                icon_on="ico_eject_orange.png",
            )
            sizer.Add(
                self.btn_switch_mirror,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.pnl_move = self._build_move_panel(panel)
            sizer.Add(
                self.pnl_move,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.pnl_ref_msg = self._build_warning_panel(panel)
            sizer.Add(
                self.pnl_ref_msg,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            sizer.AddStretchSpacer()

            self.btn_log = ImageButton(
                panel,
                icon=img.getBitmap("icon/ico_chevron_up.png"),
                height=16,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_log.SetToolTip(TOOLTIP_OPEN_LOG_PANEL)
            sizer.Add(self.btn_log)

        panel.SetSizer(sizer)
        return panel

    def _build_move_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the mirror move progress and cancel row.

        :param parent: Parent window.
        :returns: Move progress panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            self.gauge_move = wx.Gauge(
                panel,
                size=(-1, 10),
                range=100,
                style=wx.GA_SMOOTH,
            )
            self.gauge_move.SetValue(0)
            sizer.Add(
                self.gauge_move,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=7,
            )

            self.btn_cancel = create_text_button(
                panel,
                LABEL_CANCEL,
                height=24,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
            )
            self.btn_cancel.Disable()
            sizer.Add(self.btn_cancel, flag=wx.LEFT, border=self._theme.spacing_standard)

        panel.SetSizer(sizer)
        return panel

    def _build_warning_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the mirror reference/park warning message.

        :param parent: Parent window.
        :returns: Warning panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            icon = wx.StaticBitmap(
                panel,
                bitmap=img.getBitmap("icon/dialog_warning.png"),
            )
            sizer.Add(icon, flag=wx.RIGHT, border=5)

            self.txt_warning = wx.StaticText(panel, size=(-1, 54))
            self.txt_warning.SetForegroundColour(self._theme.text_primary)
            sizer.Add(self.txt_warning, proportion=1, flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_chamber_viewport(self) -> ARAcquiViewport:
        """Build the chamber view viewport.

        :returns: Chamber viewport.
        """
        self.vp_chamber = ARAcquiViewport(self)
        return self.vp_chamber

    def _build_settings_column(self) -> wx.Panel:
        """Build the scrollable optical stream settings column.

        :returns: Right settings column.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.scr_win_right = self._build_scrolled_settings(panel)
            sizer.Add(
                self.scr_win_right,
                proportion=1,
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_scrolled_settings(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the optical stream fold panel.

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
            fold_bar = FoldPanelBar(scroll_window)
            fold_bar.SetBackgroundColour(self._theme.background)
            scroll_sizer.Add(fold_bar, flag=wx.EXPAND)

            optical_item = self._fold_item(fold_bar, "OPTICAL")
            self.pnl_streams = StreamBar(
                optical_item,
                size=(300, -1),
            )
            self.pnl_streams.SetForegroundColour(self._theme.text_muted)
            self.pnl_streams.SetBackgroundColour(self._theme.background)
            optical_item.add_item(self.pnl_streams)

        scroll_window.SetSizer(scroll_sizer)
        scroll_window.FitInside()
        return scroll_window

    def _fold_item(
        self,
        fold_bar: FoldPanelBar,
        label: str,
    ) -> FoldPanelItem:
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

    def _toggle_button(
        self,
        parent: wx.Window,
        label: str,
        icon: str,
        icon_on: str,
    ) -> ImageTextToggleButton:
        """Create the default-face mirror park/engage toggle button.

        :param parent: Parent window.
        :param label: Button label.
        :param icon: Icon file name shown when untoggled.
        :param icon_on: Icon file name shown when toggled.
        :returns: Mirror toggle button.
        """
        button = ImageTextToggleButton(
            parent,
            icon=img.getBitmap(f"icon/{icon}"),
            icon_on=img.getBitmap(f"icon/{icon_on}"),
            height=48,
            face_colour="def",
            label=label,
            style=wx.ALIGN_CENTRE,
        )
        button.SetForegroundColour(self._theme.button_text)
        set_font(button, self._theme.font_size_button)
        return button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabSparcChamber)
