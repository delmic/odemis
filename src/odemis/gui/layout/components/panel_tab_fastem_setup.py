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
from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.comp.foldpanelbar import CaptionBar, FoldPanelBar
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.layout.constants.strings import (
    LABEL_CANCEL,
    LABEL_START,
)
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_fold_item, create_text_button


class PnlTabFastemSetup(wx.Panel):
    """Provide the FastEM setup tab layout.

    Hosts the active-scintillator selector, the SEM overview stream fold
    panel, and the overview-acquisition and calibration controls for the
    FastEMSetupTab controller.
    """

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the FastEM setup tab controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, size=(400, -1))
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with vbox() as root_sizer:
            root_sizer.Add(self._build_content_panel(), flag=wx.EXPAND)

        # This panel lives inside pnl_tabs, whose EVT_SIZE handler resizes
        # every tab panel explicitly (see FastEMMainTab.on_pnl_tabs_size), so
        # a plain SetSizer is sufficient here, matching PnlTabFastemAcqui.
        self.SetSizer(root_sizer)
        self.Layout()

    def _build_content_panel(self) -> wx.Panel:
        """Build the scintillator, stream, and acquisition/calibration areas.

        :returns: Content panel.
        """
        panel = wx.Panel(self, style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(self._build_scintillator_section(panel), flag=wx.EXPAND)

            scroll_window = self._build_streams_scroll_section(panel)
            sizer.Add(scroll_window, proportion=1, flag=wx.EXPAND)

            sizer.Add(self._build_filler(panel), proportion=1, flag=wx.EXPAND)

            sizer.Add(
                self._build_acquisition_calibration_section(panel),
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_scintillator_section(self, parent: wx.Window) -> wx.Panel:
        """Build the caption bar and active-scintillator host panel.

        :param parent: Parent window.
        :returns: Scintillator section panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            caption_bar = CaptionBar(panel, "SCINTILLATOR", False)
            caption_bar.SetForegroundColour(self._theme.button_text)
            sizer.Add(caption_bar, flag=wx.EXPAND)

            self.pnl_active_scintillator = wx.Panel(panel, size=(400, 40))
            self.pnl_active_scintillator.SetBackgroundColour(self._theme.background)
            self.pnl_active_scintillator.SetForegroundColour(self._theme.panel_foreground)
            sizer.Add(self.pnl_active_scintillator)

        panel.SetSizer(sizer)
        return panel

    def _build_streams_scroll_section(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable SEM overview stream fold panel.

        :param parent: Parent window.
        :returns: Scrollable settings window.
        """
        scroll_window = wx.ScrolledWindow(
            parent,
            size=(400, -1),
            style=wx.VSCROLL,
        )
        scroll_window.SetMinSize((400, 650))
        scroll_window.SetBackgroundColour(self._theme.background)
        scroll_window.EnableScrolling(False, True)
        scroll_window.SetScrollbars(-1, 10, 1, 1)

        with vbox() as scroll_sizer:
            fold_bar = FoldPanelBar(scroll_window)
            fold_bar.SetBackgroundColour(self._theme.background)
            scroll_sizer.Add(fold_bar, flag=wx.EXPAND)

            sem_item = create_fold_item(
                fold_bar,
                "SEM",
                text_colour=self._theme.button_text,
                background_colour=self._theme.section_header,
            )

            self.pnl_overview_streams = StreamBar(sem_item, size=(300, -1))
            self.pnl_overview_streams.SetForegroundColour(self._theme.text_muted)
            self.pnl_overview_streams.SetBackgroundColour(self._theme.background)
            sem_item.add_item(self.pnl_overview_streams)

        scroll_window.SetSizer(scroll_sizer)
        scroll_window.FitInside()
        return scroll_window

    def _build_filler(self, parent: wx.Window) -> wx.Sizer:
        """Build the empty flex-grid spacer between the stream fold panel and
        the acquisition/calibration section.

        :param parent: Parent window.
        :returns: Flex-grid sizer wrapping the spacer panel.
        """
        sizer = wx.FlexGridSizer(cols=1, vgap=0, hgap=0)

        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)
        sizer.Add(panel, proportion=1, flag=wx.EXPAND)

        return sizer

    def _build_acquisition_calibration_section(self, parent: wx.Window) -> wx.Panel:
        """Build the overview-acquisition and calibration caption bars, host
        panels, and status/progress/action controls.

        :param parent: Parent window.
        :returns: Acquisition and calibration section panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            overview_caption = CaptionBar(panel, "OVERVIEW ACQUISITION", False)
            overview_caption.SetForegroundColour(self._theme.button_text)
            sizer.Add(overview_caption, flag=wx.EXPAND)

            self.pnl_overview_acq = wx.Panel(panel, size=(400, 100))
            self.pnl_overview_acq.SetBackgroundColour(self._theme.background)
            sizer.Add(self.pnl_overview_acq)

            sizer.Add(self._build_acquisition_status_panel(panel), flag=wx.EXPAND)

            calibration_caption = CaptionBar(panel, "CALIBRATION", False)
            calibration_caption.SetForegroundColour(self._theme.button_text)
            sizer.Add(calibration_caption, flag=wx.EXPAND)

            self.pnl_calib = wx.Panel(panel, size=(400, 80))
            self.pnl_calib.SetBackgroundColour(self._theme.background)
            sizer.Add(self.pnl_calib)

            sizer.Add(self._build_calibration_status_panel(panel), flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_acquisition_status_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the overview-acquisition status, progress, and start button.

        :param parent: Parent window.
        :returns: Acquisition status panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            (
                status_row,
                self.bmp_acq_status_info,
                self.bmp_acq_status_warn,
                self.lbl_acq_estimate,
            ) = self._build_status_row(
                panel,
                "No scintillator selected for overview acquisition",
                warn_hidden=True,
            )
            sizer.Add(
                status_row,
                flag=wx.LEFT | wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=12,
            )

            progress_row, self.gauge_acq, self.btn_cancel_acq = (
                self._build_progress_row(panel, gauge_hidden=False)
            )
            sizer.Add(progress_row, flag=wx.EXPAND)

            self.btn_acq = create_text_button(
                panel,
                LABEL_START,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_single_beam.png",
                font_size=self._theme.font_size_primary_action,
                contrast=True,
            )
            self.btn_acq.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.btn_acq,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_calibration_status_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the calibration status, progress, and run button.

        :param parent: Parent window.
        :returns: Calibration status panel.
        """
        self.pnl_calib_status = wx.Panel(parent)
        self.pnl_calib_status.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            (
                status_row,
                self.bmp_calib_status_info,
                self.bmp_calib_status_warn,
                self.lbl_calib,
            ) = self._build_status_row(
                self.pnl_calib_status,
                "No calibration run",
                warn_hidden=False,
            )
            sizer.Add(
                status_row,
                flag=wx.LEFT | wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=12,
            )

            progress_row, self.gauge_calib, self.btn_cancel_calib = (
                self._build_progress_row(self.pnl_calib_status, gauge_hidden=True)
            )
            sizer.Add(progress_row, flag=wx.EXPAND)

            self.btn_calib = create_text_button(
                self.pnl_calib_status,
                "RUN",
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                font_size=self._theme.font_size_primary_action,
                contrast=True,
            )
            self.btn_calib.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.btn_calib,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        self.pnl_calib_status.SetSizer(sizer)
        return self.pnl_calib_status

    def _build_status_row(
        self,
        parent: wx.Window,
        label_text: str,
        warn_hidden: bool,
    ) -> Tuple[wx.Panel, wx.StaticBitmap, wx.StaticBitmap, wx.StaticText]:
        """Build an info/warning icon and status label row.

        :param parent: Parent window.
        :param label_text: Initial status label text.
        :param warn_hidden: Whether the warning icon starts hidden.
        :returns: Row panel, info icon, warning icon, and status label.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            bmp_info = wx.StaticBitmap(
                panel, bitmap=img.getBitmap("icon/dialog_info.png")
            )
            bmp_info.Hide()
            sizer.Add(bmp_info, flag=wx.RIGHT, border=5)

            bmp_warn = wx.StaticBitmap(
                panel, bitmap=img.getBitmap("icon/dialog_warning.png")
            )
            bmp_warn.Show(not warn_hidden)
            sizer.Add(bmp_warn, flag=wx.RIGHT, border=5)

            label = wx.StaticText(panel, label=label_text)
            label.SetForegroundColour(self._theme.field_foreground)
            set_font(label, self._theme.font_size_checklist)
            sizer.Add(label)

        panel.SetSizer(sizer)
        return panel, bmp_info, bmp_warn, label

    def _build_progress_row(
        self,
        parent: wx.Window,
        gauge_hidden: bool,
    ) -> Tuple[wx.Sizer, wx.Gauge, ImageTextButton]:
        """Build a progress gauge and cancel button row.

        :param parent: Parent window.
        :param gauge_hidden: Whether the gauge starts hidden.
        :returns: Row sizer, gauge, and cancel button.
        """
        with hbox() as sizer:
            gauge = wx.Gauge(parent, size=(-1, 10), range=100, style=wx.GA_SMOOTH)
            gauge.SetValue(0)
            gauge.Show(not gauge_hidden)
            sizer.Add(
                gauge,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT | wx.EXPAND,
                border=16,
            )

            cancel_button = create_text_button(
                parent,
                LABEL_CANCEL,
                height=24,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
            )
            cancel_button.Hide()
            sizer.Add(cancel_button, flag=wx.ALL, border=self._theme.spacing_standard)

        return sizer, gauge, cancel_button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabFastemSetup)
