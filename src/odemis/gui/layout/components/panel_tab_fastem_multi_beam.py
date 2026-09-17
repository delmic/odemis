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
from odemis.gui.comp.foldpanelbar import CaptionBar
from odemis.gui.layout.constants.strings import (
    LABEL_ACQUISITION,
    LABEL_CANCEL,
    LABEL_NO_REGION_OF_ACQUISITION_SELECTED,
    LABEL_PROJECTS,
    LABEL_START,
    LABEL_TOTAL_NUMBER,
)
import odemis.gui.layout as layout
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_text_button


class PnlTabFastemMultiBeam(wx.Panel):
    """Provide the FastEM multi-beam acquisition tab layout.

    Hosts the projects section, a flexible gap, and the acquisition section
    with its settings, status indicators, progress gauge, and start/cancel
    controls.
    """

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the FastEM multi-beam acquisition tab controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, size=(400, 700))
        # Height 700 (not -1) is required: the controller reads
        # pnl_projects.Size immediately after construction, before the
        # host tab is shown or resized. Starting at -1 collapses this
        # panel (and pnl_projects within it) to a near-zero height at
        # that point, baking a zero-height project tree into
        # FastEMProjectList before any later resize can correct it.
        self.SetMinSize((400, 700))
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(self._build_content_panel(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_content_panel(self) -> wx.Panel:
        """Build the projects section, the flexible gap, and the
        acquisition section.

        :returns: Content panel.
        """
        panel = wx.Panel(self, style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_projects_section(panel),
                proportion=1,
                flag=wx.EXPAND,
            )

            gap = wx.FlexGridSizer(cols=1, vgap=0, hgap=0)
            gap.Add(wx.Panel(panel))
            sizer.Add(gap, proportion=1, flag=wx.EXPAND)

            sizer.Add(
                self._build_acquisition_section(panel),
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_projects_section(self, parent: wx.Window) -> wx.Panel:
        """Build the PROJECTS caption bar and its project list host panel.

        :param parent: Parent window.
        :returns: Projects section panel.
        """
        section = wx.Panel(parent)
        section.SetBackgroundColour(self._theme.panel_background)

        with vbox() as sizer:
            caption = CaptionBar(section, LABEL_PROJECTS, False)
            caption.SetForegroundColour(self._theme.button_text)
            sizer.Add(caption, flag=wx.EXPAND)

            self.pnl_projects = wx.Panel(section, size=(400, 700))
            self.pnl_projects.SetMinSize((400, 700))
            self.pnl_projects.SetBackgroundColour(self._theme.background)
            self.pnl_projects.SetForegroundColour(self._theme.text_muted)
            sizer.Add(
                self.pnl_projects,
                proportion=1,
                flag=wx.TOP | wx.EXPAND,
            )

        section.SetSizer(sizer)
        return section

    def _build_acquisition_section(self, parent: wx.Window) -> wx.Panel:
        """Build the ACQUISITION caption bar, its settings panel, and the
        start button.

        :param parent: Parent window.
        :returns: Acquisition section panel.
        """
        section = wx.Panel(parent)
        section.SetBackgroundColour(self._theme.panel_background)

        with vbox() as sizer:
            caption = CaptionBar(section, LABEL_ACQUISITION, False)
            caption.SetForegroundColour(self._theme.button_text)
            sizer.Add(caption, flag=wx.EXPAND)

            sizer.Add(
                self._build_acquisition_controls(section),
                flag=wx.EXPAND,
            )

            self.btn_acquire = create_text_button(
                section,
                LABEL_START,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_multi_beam.png",
                font_size=self._theme.font_size_primary_action,
                contrast=True,
            )
            self.btn_acquire.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.btn_acquire,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        section.SetSizer(sizer)
        return section

    def _build_acquisition_controls(self, parent: wx.Window) -> wx.Panel:
        """Build the settings host panel, ROA count row, status row, and
        progress/cancel row.

        :param parent: Parent window.
        :returns: Acquisition controls panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.pnl_acq = wx.Panel(panel, size=(400, 140))
            self.pnl_acq.SetMinSize((400, 140))
            self.pnl_acq.SetBackgroundColour(self._theme.background)
            self.pnl_acq.SetForegroundColour(self._theme.text_muted)
            sizer.Add(
                self.pnl_acq,
                proportion=1,
                flag=wx.TOP | wx.EXPAND,
            )

            sizer.Add(
                self._build_roa_count_row(panel),
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            sizer.Add(
                self._build_status_row(panel),
                flag=wx.LEFT | wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=12,
            )

            sizer.Add(
                self._build_progress_row(panel),
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_roa_count_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the "Total number" ROA count row.

        :param parent: Parent window.
        :returns: ROA count grid sizer.
        """
        grid = wx.FlexGridSizer(rows=2, cols=2, vgap=5, hgap=10)
        grid.AddGrowableCol(1)

        label = wx.StaticText(parent, label=LABEL_TOTAL_NUMBER)
        label.SetForegroundColour(self._theme.text_primary)
        grid.Add(label, flag=wx.TOP, border=2)

        self.txt_num_roas = wx.TextCtrl(
            parent,
            value="0",
            size=(200, 20),
            style=wx.BORDER_NONE | wx.TE_READONLY,
        )
        self.txt_num_roas.SetForegroundColour(self._theme.text_secondary)
        self.txt_num_roas.SetBackgroundColour(self._theme.background)
        grid.Add(self.txt_num_roas)

        return grid

    def _build_status_row(self, parent: wx.Window) -> wx.Panel:
        """Build the acquisition status row with its info/warning icons
        and estimate label.

        :param parent: Parent window.
        :returns: Status row panel.
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

            self.lbl_acq_estimate = wx.StaticText(
                panel, label=LABEL_NO_REGION_OF_ACQUISITION_SELECTED
            )
            self.lbl_acq_estimate.SetForegroundColour(self._theme.field_foreground)
            set_font(self.lbl_acq_estimate, self._theme.font_size_checklist)
            sizer.Add(self.lbl_acq_estimate)

        panel.SetSizer(sizer)
        return panel

    def _build_progress_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the acquisition progress gauge and cancel button row.

        :param parent: Parent window.
        :returns: Progress row sizer.
        """
        with hbox() as sizer:
            self.gauge_acq = wx.Gauge(
                parent, range=100, size=(-1, 10), style=wx.GA_SMOOTH
            )
            self.gauge_acq.SetValue(0)
            sizer.Add(
                self.gauge_acq,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT | wx.EXPAND,
                border=16,
            )

            self.btn_cancel = create_text_button(
                parent,
                LABEL_CANCEL,
                height=24,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="def",
            )
            self.btn_cancel.Hide()
            sizer.Add(self.btn_cancel, flag=wx.ALL, border=10)

        return sizer


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabFastemMultiBeam)
