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

from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, ImageTextButton, ViewButton
from odemis.gui.comp.foldpanelbar import CaptionBar, FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import (
    AngularSpectrumViewport,
    ARAcquiViewport,
    ChronographViewport,
    LiveViewport,
    PointSpectrumViewport,
    TemporalSpectrumViewport,
)
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants import strings
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class PnlTabSparcAcqui(wx.Panel):
    """Provide the SPARC acquisition tab layout."""

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the SPARC acquisition view controls, viewports, and settings.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(self._build_view_controls(), flag=wx.EXPAND)
            root_sizer.Add(
                self._build_viewport_grid(),
                proportion=1,
                flag=wx.EXPAND,
            )
            root_sizer.Add(self._build_settings_column(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_view_controls(self) -> wx.Panel:
        """Build the toolbar and viewport selector column.

        :returns: Left control column.
        """
        self.pnl_left = wx.Panel(self, size=(200, -1))
        self.pnl_left.SetBackgroundColour(self._theme.background)

        with vbox() as outer_sizer:
            outer_sizer.Add(
                self._build_view_selector(self.pnl_left),
                proportion=1,
                flag=wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            outer_sizer.Add(
                self._build_log_button(self.pnl_left),
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                border=self._theme.spacing_standard,
            )

        self.pnl_left.SetSizer(outer_sizer)
        return self.pnl_left

    def _build_view_selector(self, parent: wx.Window) -> wx.Sizer:
        """Build the view selector labels, buttons, and toolbar.

        :param parent: Parent window.
        :returns: View selector sizer.
        """
        with vbox() as selector_sizer:
            selector_sizer.Add(
                self._build_view_label(parent, "lbl_sparc_view_all", top_padding=False),
                flag=wx.RIGHT | wx.ALIGN_RIGHT,
                border=18,
            )
            self.btn_sparc_view_all = self._view_button(parent)
            selector_sizer.Add(
                self.btn_sparc_view_all,
                flag=wx.BOTTOM | wx.ALIGN_RIGHT,
                border=6,
            )

            self.sparc_acq_toolbar = ToolBar(parent, style=wx.VERTICAL)
            selector_sizer.Add(self.sparc_acq_toolbar, flag=wx.ALIGN_RIGHT)

            selector_specs = (
                ("lbl_sparc_view_tl", "btn_sparc_view_tl", True, True),
                ("lbl_sparc_view_tr", "btn_sparc_view_tr", True, True),
                ("lbl_sparc_view_bl", "btn_sparc_view_bl", True, True),
                ("lbl_sparc_view_br", "btn_sparc_view_br", True, False),
            )
            for label_name, button_name, top_padding, bottom_border in selector_specs:
                selector_sizer.Add(
                    self._build_view_label(parent, label_name, top_padding=top_padding),
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

    def _build_view_label(
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
        self.btn_log.SetToolTip(strings.TOOLTIP_OPEN_LOG_PANEL)
        return self.btn_log

    def _view_button(self, parent: wx.Window) -> ViewButton:
        """Create a default view selector button.

        :param parent: Parent window.
        :returns: View selector button.
        """
        return ViewButton(parent, face_colour="def")

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the SPARC acquisition viewport grid.

        :returns: Viewport grid hosting all acquisition viewports.
        """
        self.pnl_sparc_grid = ViewportGrid(self)

        self.vp_sparc_tl = LiveViewport(self.pnl_sparc_grid)
        self.vp_sparc_tr = ARAcquiViewport(self.pnl_sparc_grid)
        self.vp_sparc_bl = PointSpectrumViewport(self.pnl_sparc_grid)
        self.vp_sparc_ts = TemporalSpectrumViewport(self.pnl_sparc_grid)
        self.vp_sparc_br = ChronographViewport(self.pnl_sparc_grid)
        self.vp_sparc_as = AngularSpectrumViewport(self.pnl_sparc_grid)

        # Controllers access the viewport tuple before the first size event.
        self.pnl_sparc_grid.viewports = (
            self.vp_sparc_tl,
            self.vp_sparc_tr,
            self.vp_sparc_bl,
            self.vp_sparc_ts,
            self.vp_sparc_br,
            self.vp_sparc_as,
        )
        return self.pnl_sparc_grid

    def _build_settings_column(self) -> wx.Panel:
        """Build the streams, recipes, and acquisition action column.

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
            sizer.Add(self._build_acquisition_panel(panel), flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_settings_scroller(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable fold panel bar with the hardware and stream settings.

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
            self.fpb_settings = FoldPanelBar(self.scr_win_right)
            self.fpb_settings.SetBackgroundColour(self._theme.background)
            scroll_sizer.Add(self.fpb_settings, flag=wx.EXPAND)

            self.fp_settings_gun_exciter = self._fold_item(
                self.fpb_settings, "GUN EXCITER"
            )
            self.fp_settings_gun_exciter.Hide()

            self.fp_settings_ebeam_blanker = self._fold_item(
                self.fpb_settings, "ELECTRON PULSER"
            )
            self.fp_settings_ebeam_blanker.Hide()

            streams_item = self._fold_item(self.fpb_settings, "STREAMS")
            self.pnl_sparc_streams = self._stream_bar(
                streams_item,
                size=(300, -1),
                add_button=True,
            )
            streams_item.add_item(self.pnl_sparc_streams)

        self.scr_win_right.SetSizer(scroll_sizer)
        self.scr_win_right.FitInside()
        return self.scr_win_right

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
        stream_bar = StreamBar(parent, size=size, add_button=add_button)
        stream_bar.SetForegroundColour(self._theme.text_muted)
        stream_bar.SetBackgroundColour(self._theme.background)
        return stream_bar

    def _build_acquisition_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the acquisition recipes, filename, progress, and start controls.

        :param parent: Parent window.
        :returns: Acquisition action panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            self.cbar_acq_recipes = CaptionBar(
                panel, "ACQUISITION RECIPES", collapsed=True, foldable=True
            )
            self.cbar_acq_recipes.SetForegroundColour(self._theme.button_text)
            sizer.Add(self.cbar_acq_recipes, flag=wx.EXPAND)

            self.pnl_acq_recipes = wx.Panel(panel)
            self.pnl_acq_recipes.SetBackgroundColour(self._theme.background)
            self.pnl_acq_recipes.Hide()
            sizer.Add(self.pnl_acq_recipes, flag=wx.EXPAND)

            acquisition_caption = CaptionBar(panel, "ACQUISITION", collapsed=False)
            acquisition_caption.SetForegroundColour(self._theme.button_text)
            sizer.Add(acquisition_caption, flag=wx.EXPAND)

            sizer.Add(
                self._build_acquisition_details(panel),
                flag=wx.EXPAND,
            )

            self.btn_sparc_acquire = self._text_button(
                panel,
                label="START",
                icon="ico_acqui.png",
                height=48,
                face_colour="blue",
                contrast=True,
                font_size=self._theme.font_size_primary_action,
                style=wx.ALIGN_CENTRE,
            )
            sizer.Add(
                self.btn_sparc_acquire,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_acquisition_details(self, parent: wx.Window) -> wx.Panel:
        """Build the filename, status, and progress controls.

        :param parent: Parent window.
        :returns: Acquisition details panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_filename_grid(panel),
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_fold_acq_status(panel),
                flag=wx.LEFT | wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=12,
            )
            sizer.Add(
                self._build_acq_status(panel),
                flag=wx.LEFT | wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=12,
            )
            sizer.Add(self._build_progress_row(panel), flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_filename_grid(self, parent: wx.Window) -> wx.FlexGridSizer:
        """Build the filename and destination field grid.

        :param parent: Parent window.
        :returns: Filename grid sizer.
        """
        grid = wx.FlexGridSizer(rows=3, cols=2, vgap=5, hgap=10)
        grid.AddGrowableCol(1)

        lbl_filename = wx.StaticText(parent, label="Filename")
        lbl_filename.SetForegroundColour(self._theme.text_primary)
        grid.Add(lbl_filename, flag=wx.TOP, border=4)

        with hbox() as filename_sizer:
            self.txt_filename = wx.TextCtrl(
                parent,
                value=strings.DEFAULT_DESTINATION_FILE,
                size=(-1, 20),
                style=wx.BORDER_NONE | wx.TE_READONLY,
            )
            self.txt_filename.SetForegroundColour(self._theme.text_edit)
            self.txt_filename.SetBackgroundColour(self._theme.background)
            filename_sizer.Add(
                self.txt_filename,
                proportion=1,
                flag=wx.TOP | wx.EXPAND,
                border=2,
            )

            self.btn_sparc_change_file = self._text_button(
                parent,
                label="change…",
                height=24,
                face_colour="def",
            )
            filename_sizer.Add(self.btn_sparc_change_file, flag=wx.TOP, border=2)

        grid.Add(filename_sizer, flag=wx.EXPAND)

        lbl_destination = wx.StaticText(parent, label="Destination")
        lbl_destination.SetForegroundColour(self._theme.text_primary)
        grid.Add(lbl_destination, flag=wx.TOP, border=2)

        self.txt_destination = wx.TextCtrl(
            parent,
            value="...",
            size=(200, 20),
            style=wx.BORDER_NONE | wx.TE_READONLY,
        )
        self.txt_destination.SetForegroundColour(self._theme.text_secondary)
        self.txt_destination.SetBackgroundColour(self._theme.background)
        grid.Add(self.txt_destination)

        return grid

    def _build_fold_acq_status(self, parent: wx.Window) -> wx.Panel:
        """Build the fold-acquisition informational row.

        :param parent: Parent window.
        :returns: Fold acquisition status panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            self.bmp_fold_acq_info = wx.StaticBitmap(
                panel, bitmap=img.getBitmap("icon/dialog_info.png")
            )
            self.bmp_fold_acq_info.Hide()
            sizer.Add(self.bmp_fold_acq_info, flag=wx.RIGHT, border=5)

            self.lbl_sparc_fold_acq = wx.StaticText(
                panel, label="Some streams will be acquired simultaneously"
            )
            self.lbl_sparc_fold_acq.SetForegroundColour("#DDDDDD")
            set_font(self.lbl_sparc_fold_acq, self._theme.font_size_checklist)
            self.lbl_sparc_fold_acq.Hide()
            sizer.Add(self.lbl_sparc_fold_acq)

        panel.SetSizer(sizer)
        return panel

    def _build_acq_status(self, parent: wx.Window) -> wx.Panel:
        """Build the acquisition status informational row.

        :param parent: Parent window.
        :returns: Acquisition status panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            self.bmp_acq_status_info = wx.StaticBitmap(
                panel, bitmap=img.getBitmap("icon/dialog_info.png")
            )
            self.bmp_acq_status_info.Hide()
            sizer.Add(self.bmp_acq_status_info, flag=wx.RIGHT, border=5)

            self.bmp_acq_status_warn = wx.StaticBitmap(
                panel, bitmap=img.getBitmap("icon/dialog_warning.png")
            )
            self.bmp_acq_status_warn.Hide()
            sizer.Add(self.bmp_acq_status_warn, flag=wx.RIGHT, border=5)

            self.lbl_sparc_acq_estimate = wx.StaticText(
                panel, label="No region of interest selected."
            )
            self.lbl_sparc_acq_estimate.SetForegroundColour("#DDDDDD")
            set_font(self.lbl_sparc_acq_estimate, self._theme.font_size_checklist)
            sizer.Add(self.lbl_sparc_acq_estimate)

        panel.SetSizer(sizer)
        return panel

    def _build_progress_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the acquisition progress gauge and cancel button.

        :param parent: Parent window.
        :returns: Progress row sizer.
        """
        with hbox() as sizer:
            self.gauge_sparc_acq = wx.Gauge(
                parent, range=100, size=(-1, 10), style=wx.GA_SMOOTH
            )
            sizer.Add(
                self.gauge_sparc_acq,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT | wx.EXPAND,
                border=16,
            )

            self.btn_sparc_cancel = self._text_button(
                parent,
                label="Cancel",
                height=24,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_sparc_cancel.Hide()
            sizer.Add(self.btn_sparc_cancel, flag=wx.ALL, border=10)

        return sizer

    def _text_button(
        self,
        parent: wx.Window,
        label: str,
        height: int,
        face_colour: str = "def",
        icon: Optional[str] = None,
        size: Any = wx.DefaultSize,
        style: int = 0,
        contrast: bool = False,
        font_size: Optional[int] = None,
    ) -> ImageTextButton:
        """Create a styled image text button.

        :param parent: Parent window.
        :param label: Button label.
        :param height: Button face height.
        :param face_colour: Named button face colour.
        :param icon: Optional icon file name.
        :param size: Explicit button size.
        :param style: wx window style.
        :param contrast: Use contrasting foreground text.
        :param font_size: Optional explicit font size.
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
            style=style,
        )
        button.SetForegroundColour(
            self._theme.button_text_contrast if contrast else self._theme.button_text
        )
        if font_size is not None:
            set_font(button, font_size)
        return button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabSparcAcqui)
