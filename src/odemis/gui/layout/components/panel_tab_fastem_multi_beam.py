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
from typing import Any, Optional

from odemis.gui import img
from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.comp.foldpanelbar import CaptionBar
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox

# Foreground colour of the section caption bars.
_CAPTION_TEXT = "#1A1A1A"
# Font point size of the acquisition status label, distinct from any
# shared semantic role in the theme.
_STATUS_LABEL_FONT_SIZE = 10


class PnlTabFastemMultiBeam(wx.Panel):
    """Provide the FastEM multi-beam acquisition tab layout.

    Hosts the projects section, a flexible gap, and the acquisition section
    with its settings, status indicators, progress gauge, and start/cancel
    controls.
    """

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
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
            caption = CaptionBar(section, "PROJECTS", False)
            caption.SetForegroundColour(_CAPTION_TEXT)
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
            caption = CaptionBar(section, "ACQUISITION", False)
            caption.SetForegroundColour(_CAPTION_TEXT)
            sizer.Add(caption, flag=wx.EXPAND)

            sizer.Add(
                self._build_acquisition_controls(section),
                flag=wx.EXPAND,
            )

            self.btn_acquire = self._text_button(
                section,
                "START",
                height=48,
                face_colour="blue",
                icon="icon/ico_multi_beam.png",
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

        label = wx.StaticText(parent, label="Total number:")
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
                panel, label="No region of acquisition selected."
            )
            self.lbl_acq_estimate.SetForegroundColour("#DDDDDD")
            set_font(self.lbl_acq_estimate, _STATUS_LABEL_FONT_SIZE)
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

            self.btn_cancel = self._text_button(
                parent,
                "Cancel",
                height=24,
                face_colour="def",
            )
            self.btn_cancel.Hide()
            sizer.Add(self.btn_cancel, flag=wx.ALL, border=10)

        return sizer

    def _text_button(
        self,
        parent: wx.Window,
        label: str,
        height: int,
        face_colour: str = "def",
        icon: Optional[str] = None,
        font_size: Optional[int] = None,
        size: Any = wx.DefaultSize,
        contrast: bool = False,
    ) -> ImageTextButton:
        """Create a styled text button.

        :param parent: Parent window.
        :param label: Button label.
        :param height: Button face height.
        :param face_colour: Named button face colour.
        :param icon: Optional icon file path relative to the image root.
        :param font_size: Optional font point size.
        :param size: Explicit button size.
        :param contrast: Use contrasting foreground text.
        :returns: Styled text button.
        """
        if contrast and face_colour == "def":
            raise ValueError(
                "Contrasting text requires a non-default button face"
            )

        button = ImageTextButton(
            parent,
            label=label,
            icon=img.getBitmap(icon) if icon else wx.NullBitmap,
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
        if font_size is not None:
            set_font(button, font_size)
        return button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabFastemMultiBeam)
