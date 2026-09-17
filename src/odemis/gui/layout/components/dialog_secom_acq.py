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
import wx.adv

from odemis.gui import img
from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import LiveViewport
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class SecomAcqDialogBase(wx.Dialog):
    """Provide the SECOM image acquisition dialog layout."""

    def __init__(
        self,
        parent: Optional[wx.Window],
        theme: Theme = DARK,
    ) -> None:
        """Create the acquisition dialog controls and viewport.

        :param parent: Parent window, or None for a top-level dialog.
        :param theme: Semantic layout theme.
        """
        super().__init__(
            parent,
            title="Image Acquisition",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._theme = theme
        self.SetBackgroundColour("#000000")
        set_font(self, 9)

        root_sizer = wx.FlexGridSizer(2, 2, 0, 0)
        root_sizer.AddGrowableCol(0)
        root_sizer.AddGrowableRow(0)

        self.pnl_view_acq = LiveViewport(self)
        root_sizer.Add(self.pnl_view_acq, flag=wx.EXPAND)
        root_sizer.Add(self._build_settings_column(), flag=wx.EXPAND)
        root_sizer.Add(self._build_gauge_panel(), flag=wx.EXPAND)
        root_sizer.Add(self._build_action_panel(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_settings_column(self) -> wx.Panel:
        """Build the presets/filename row and the settings fold panels.

        :returns: Settings column panel.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(self._build_main_buttons(panel), flag=wx.EXPAND)
            sizer.Add(
                self._build_scrolled_settings(panel),
                proportion=1,
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_main_buttons(self, parent: wx.Window) -> wx.Panel:
        """Build the presets, filename, and destination fields.

        :param parent: Parent settings column.
        :returns: Main buttons panel.
        """
        panel = wx.Panel(parent, size=(400, -1))
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as outer_sizer:
            grid = wx.FlexGridSizer(3, 2, 5, 10)
            grid.AddGrowableCol(1)

            lbl_presets = wx.StaticText(panel, label="Presets")
            grid.Add(lbl_presets)
            self.cmb_presets = self._combo(panel, size=(-1, 16))
            grid.Add(self.cmb_presets, flag=wx.EXPAND)

            lbl_filename = wx.StaticText(panel, label="Filename")
            grid.Add(lbl_filename, flag=wx.TOP, border=4)
            grid.Add(self._build_filename_row(panel), flag=wx.EXPAND)

            lbl_destination = wx.StaticText(panel, label="Destination")
            grid.Add(lbl_destination, flag=wx.TOP, border=2)

            self.txt_destination = wx.TextCtrl(
                panel,
                size=(200, 20),
                value="...",
                style=wx.BORDER_NONE | wx.TE_READONLY,
            )
            self.txt_destination.SetForegroundColour(
                self._theme.text_secondary
            )
            self.txt_destination.SetBackgroundColour(
                self._theme.field_background
            )
            grid.Add(self.txt_destination)

            outer_sizer.Add(grid, flag=wx.ALL | wx.EXPAND, border=10)

        panel.SetSizer(outer_sizer)
        return panel

    def _build_filename_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the filename field and its change button.

        :param parent: Parent main buttons panel.
        :returns: Filename row sizer.
        """
        with hbox() as sizer:
            self.txt_filename = wx.TextCtrl(
                parent,
                size=(-1, 20),
                value="Select a destination file",
                style=wx.BORDER_NONE | wx.TE_READONLY,
            )
            self.txt_filename.SetForegroundColour(self._theme.text_edit)
            self.txt_filename.SetBackgroundColour(
                self._theme.field_background
            )
            sizer.Add(self.txt_filename, proportion=1, flag=wx.EXPAND)

            self.btn_change_file = ImageTextButton(
                parent,
                label="change…",
                height=16,
            )
            self.btn_change_file.SetForegroundColour(
                self._theme.button_text
            )
            set_font(self.btn_change_file, 9)
            sizer.Add(self.btn_change_file)

        return sizer

    def _combo(
        self,
        parent: wx.Window,
        size: Any,
    ) -> wx.adv.OwnerDrawnComboBox:
        """Create a themed read-only owner-drawn combobox.

        :param parent: Parent panel.
        :param size: Explicit combobox size.
        :returns: Styled combobox.
        """
        combo = wx.adv.OwnerDrawnComboBox(
            parent,
            size=size,
            style=(
                wx.BORDER_NONE
                | wx.CB_DROPDOWN
                | wx.CB_READONLY
                | wx.TE_PROCESS_ENTER
            ),
        )
        combo.SetButtonBitmaps(
            img.getBitmap("button/btn_down.png"),
            pushButtonBg=False,
        )
        combo.SetForegroundColour(self._theme.text_edit)
        combo.SetBackgroundColour(self._theme.field_background)
        return combo

    def _build_scrolled_settings(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the optical, SEM, and stream settings fold panels.

        :param parent: Parent settings column.
        :returns: Scrollable settings window.
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
        """Build the optical, SEM, stream, and fine-alignment sections.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_settings_secom_optical = self._fold_item(
            fold_bar,
            "OPTICAL SETTINGS",
        )

        opt_streams_item = self._fold_item(fold_bar, nocaption=True)
        self.pnl_opt_streams = self._stream_bar(opt_streams_item)
        opt_streams_item.add_item(self.pnl_opt_streams)

        self.fp_settings_secom_sem = self._fold_item(
            fold_bar,
            "SEM SETTINGS",
        )

        streams_item = self._fold_item(fold_bar, "STREAMS")
        self.pnl_secom_streams = self._stream_bar(
            streams_item,
            size=(300, -1),
        )
        streams_item.add_item(self.pnl_secom_streams)

        fine_align_item = self._fold_item(fold_bar, nocaption=True)
        fine_align_item.SetForegroundColour("#BBBBBB")
        fine_align_item.SetBackgroundColour(self._theme.background)

        self.chkbox_fine_align = wx.CheckBox(
            fine_align_item,
            size=(-1, 20),
            label="Fine alignment",
        )
        fine_align_item.add_item(self.chkbox_fine_align)

    def _stream_bar(
        self,
        parent: wx.Window,
        size: Any = wx.DefaultSize,
    ) -> StreamBar:
        """Create a themed, read-only (no add-stream button) stream bar.

        :param parent: Parent fold-panel item.
        :param size: Explicit stream bar size.
        :returns: Stream bar.
        """
        stream_bar = StreamBar(parent, size=size)
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

    def _build_gauge_panel(self) -> wx.Panel:
        """Build the acquisition progress gauge and estimate label.

        :returns: Gauge panel.
        """
        panel = wx.Panel(self, size=(-1, 60))
        panel.SetBackgroundColour(self._theme.field_background)

        with hbox() as sizer:
            self.gauge_acq = wx.Gauge(
                panel,
                range=100,
                size=(-1, 10),
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
            self.lbl_acqestimate.SetForegroundColour(
                self._theme.text_primary
            )
            set_font(self.lbl_acqestimate, 14)
            sizer.Add(self.lbl_acqestimate, flag=wx.ALL, border=23)

        panel.SetSizer(sizer)
        return panel

    def _build_action_panel(self) -> wx.Panel:
        """Build the cancel and acquire action buttons.

        :returns: Action button panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour(self._theme.panel_background)

        with hbox() as sizer:
            self.btn_cancel = self._text_button(
                panel,
                label="Close",
                height=48,
            )
            sizer.Add(
                self.btn_cancel,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT | wx.EXPAND,
                border=10,
            )

            self.btn_secom_acquire = self._text_button(
                panel,
                label="START",
                height=48,
                face_colour="blue",
                icon="ico_acqui.png",
                size=(242, 48),
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

    def _text_button(
        self,
        parent: wx.Window,
        label: str,
        height: int,
        face_colour: str = "def",
        icon: Optional[str] = None,
        size: Any = wx.DefaultSize,
        contrast: bool = False,
    ) -> ImageTextButton:
        """Create a styled image text button.

        :param parent: Parent window.
        :param label: Button label.
        :param height: Button face height.
        :param face_colour: Named button face colour.
        :param icon: Optional icon file name.
        :param size: Explicit button size.
        :param contrast: Use contrasting foreground text.
        :returns: Image text button.
        """
        if contrast and face_colour == "def":
            raise ValueError(
                "Contrasting text requires a non-default button face"
            )

        button = ImageTextButton(
            parent,
            label=label,
            icon=img.getBitmap(f"icon/{icon}") if icon else wx.NullBitmap,
            height=height,
            face_colour=face_colour,
            size=size,
            style=wx.ALIGN_CENTRE,
        )
        button.SetForegroundColour(
            self._theme.button_text_contrast
            if contrast
            else self._theme.button_text
        )
        return button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(SecomAcqDialogBase)
