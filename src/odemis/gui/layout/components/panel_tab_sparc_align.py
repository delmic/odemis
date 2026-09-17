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

from typing import Any

import wx

from odemis.gui import img
from odemis.gui.comp.buttons import GraphicRadioButton, ImageButton, ImageTextButton
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.slider import UnitFloatSlider
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import ARLiveViewport
from odemis.gui.layout.constants import strings
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class PnlTabSparcAlign(wx.Panel):
    """Provide the SPARC mirror/fiber alignment tab layout."""

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the alignment mode controls, viewport, and settings column.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, style=wx.WANTS_CHARS)
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_alignment_column(),
                flag=wx.EXPAND,
            )
            viewport_item = root_sizer.Add(
                self._build_ar_viewport(),
                proportion=1,
                flag=wx.EXPAND,
            )
            viewport_item.SetRatio(1)
            root_sizer.Add(
                self._build_settings_column(),
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_alignment_column(self) -> wx.Panel:
        """Build the alignment mode buttons, actuator panels, and log toggle.

        :returns: Left alignment column.
        """
        panel = wx.Panel(self)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.pnl_alignment_btns = self._build_alignment_mode_buttons(panel)
            sizer.Add(
                self.pnl_alignment_btns,
                flag=wx.BOTTOM | wx.EXPAND,
                border=5,
            )

            self.pnl_sparc_trans = self._build_translation_panel(panel)
            sizer.Add(
                self.pnl_sparc_trans,
                flag=wx.BOTTOM | wx.EXPAND,
                border=5,
            )

            self.pnl_sparc_rot = self._build_rotation_panel(panel)
            sizer.Add(
                self.pnl_sparc_rot,
                flag=wx.BOTTOM | wx.EXPAND,
                border=5,
            )

            self.pnl_fibaligner = self._build_fiber_panel(panel)
            sizer.Add(self.pnl_fibaligner, flag=wx.EXPAND)

            sizer.AddStretchSpacer()

            self.btn_log = ImageButton(
                panel,
                icon=img.getBitmap("icon/ico_chevron_up.png"),
                height=16,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_log.SetToolTip(strings.TOOLTIP_OPEN_LOG_PANEL)
            sizer.Add(self.btn_log, flag=wx.ALL, border=10)

        panel.SetSizer(sizer)
        return panel

    def _build_alignment_mode_buttons(self, parent: wx.Window) -> wx.Panel:
        """Build the chamber/mirror/fiber alignment mode selector.

        :param parent: Parent window.
        :returns: Alignment mode button panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.panel_background)

        with hbox() as sizer:
            self.btn_align_chamber = self._radio_button(
                panel, "CHAMBER", "ico_cam.png", "ico_cam_green.png"
            )
            sizer.Add(self.btn_align_chamber, flag=wx.ALL, border=10)

            self.btn_align_mirror = self._radio_button(
                panel, "MIRROR", "ico_ang.png", "ico_ang_green.png"
            )
            sizer.Add(self.btn_align_mirror, flag=wx.ALL, border=10)

            self.btn_align_fiber = self._radio_button(
                panel, "OPTICAL FIBER", "ico_fib.png", "ico_fib_green.png"
            )
            sizer.Add(self.btn_align_fiber, flag=wx.ALL, border=10)

        panel.SetSizer(sizer)
        return panel

    def _build_translation_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the mirror X/Y translation controls.

        :param parent: Parent window.
        :returns: Translation control panel.
        """
        panel = self._actuator_panel(parent)

        with vbox() as sizer:
            heading = wx.StaticText(panel, label="Translation")
            set_font(heading, self._theme.font_size_section_heading)
            sizer.Add(heading, flag=wx.ALL, border=5)

            label_x = wx.StaticText(panel, label="Step size X")
            sizer.Add(label_x, flag=wx.BOTTOM | wx.LEFT, border=5)

            self.mirror_align_slider_mirror_x = UnitFloatSlider(
                panel,
                value=0.000001,
                min_val=0.0000001,
                max_val=0.001,
                unit="m",
                scale="log",
                accuracy=2,
                size=(-1, 20),
                style=wx.BORDER_NONE,
            )
            self.mirror_align_slider_mirror_x.SetForegroundColour(
                self._theme.text_primary
            )
            sizer.Add(
                self.mirror_align_slider_mirror_x,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            label_y = wx.StaticText(panel, label="Step size Y")
            sizer.Add(label_y, flag=wx.BOTTOM | wx.LEFT, border=5)

            self.mirror_align_slider_mirror_y = UnitFloatSlider(
                panel,
                value=0.000001,
                min_val=0.0000001,
                max_val=0.001,
                unit="m",
                scale="log",
                accuracy=2,
                size=(-1, 20),
                style=wx.BORDER_NONE,
            )
            self.mirror_align_slider_mirror_y.SetForegroundColour(
                self._theme.text_primary
            )
            sizer.Add(
                self.mirror_align_slider_mirror_y,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            grid = wx.GridBagSizer(vgap=0, hgap=5)

            label_specs = (
                ("lbl_my", "-Y", (0, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5, False),
                ("lbl_py", "+Y", (4, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5, False),
                ("lbl_px", "+X", (2, 4), wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, 5, True),
                (
                    "lbl_mx",
                    "-X",
                    (2, 0),
                    wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
                    5,
                    False,
                ),
            )
            for attribute, text, position, flag, border, align_left in label_specs:
                label = self._axis_label(panel, text, align_left=align_left)
                setattr(self, attribute, label)
                grid.Add(label, pos=position, flag=flag, border=border)

            button_specs = (
                ("mirror_align_btn_p_mirror_y", "↑", (1, 2), wx.LEFT | wx.RIGHT, 7),
                ("mirror_align_btn_m_mirror_y", "↓", (3, 2), wx.LEFT | wx.RIGHT, 7),
                ("mirror_align_btn_p_mirror_x", "←", (2, 1), 0, 0),
                ("mirror_align_btn_m_mirror_x", "→", (2, 3), 0, 0),
            )
            for attribute, text, position, flag, border in button_specs:
                button = self._arrow_button(panel, text)
                setattr(self, attribute, button)
                grid.Add(button, pos=position, flag=flag, border=border)

            sizer.Add(grid, flag=wx.ALIGN_CENTRE)

        panel.SetSizer(sizer)
        return panel

    def _build_rotation_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the mirror pitch/yaw rotation controls.

        :param parent: Parent window.
        :returns: Rotation control panel.
        """
        panel = self._actuator_panel(parent)

        with vbox() as sizer:
            heading = wx.StaticText(panel, label="Rotation")
            heading.SetForegroundColour(self._theme.text_primary)
            set_font(heading, self._theme.font_size_section_heading)
            sizer.Add(heading, flag=wx.BOTTOM | wx.ALL, border=5)

            label = wx.StaticText(panel, label="Step size")
            sizer.Add(label, flag=wx.LEFT, border=5)

            self.mirror_align_slider_mirror_r = UnitFloatSlider(
                panel,
                value=0.000001,
                min_val=0.0000001,
                max_val=0.001,
                unit="m",
                scale="log",
                accuracy=2,
                size=(-1, 20),
                style=wx.BORDER_NONE,
            )
            sizer.Add(
                self.mirror_align_slider_mirror_r,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            grid = wx.GridBagSizer()

            label_specs = (
                ("lbl_pry", "+Pitch", (0, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5, False),
                ("lbl_mry", "-Pitch", (4, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5, False),
                ("lbl_prz", "+Yaw", (2, 4), wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, 5, True),
                (
                    "lbl_mrz",
                    "-Yaw",
                    (2, 0),
                    wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
                    5,
                    False,
                ),
            )
            for attribute, text, position, flag, border, align_left in label_specs:
                label_ctrl = self._axis_label(panel, text, align_left=align_left)
                setattr(self, attribute, label_ctrl)
                grid.Add(label_ctrl, pos=position, flag=flag, border=border)

            button_specs = (
                ("mirror_align_btn_m_mirror_ry", "↑", (1, 2), wx.LEFT | wx.RIGHT, 7),
                ("mirror_align_btn_p_mirror_ry", "↓", (3, 2), wx.LEFT | wx.RIGHT, 7),
                ("mirror_align_btn_m_mirror_rz", "←", (2, 1), 0, 0),
                ("mirror_align_btn_p_mirror_rz", "→", (2, 3), 0, 0),
            )
            for attribute, text, position, flag, border in button_specs:
                button = self._arrow_button(panel, text)
                setattr(self, attribute, button)
                grid.Add(button, pos=position, flag=flag, border=border)

            sizer.Add(
                grid,
                flag=wx.LEFT | wx.RIGHT | wx.ALIGN_CENTRE,
                border=5,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_fiber_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the optical fiber X/Y alignment controls.

        :param parent: Parent window.
        :returns: Fiber alignment control panel.
        """
        panel = self._actuator_panel(parent)

        with vbox() as sizer:
            heading = wx.StaticText(panel, label="Fiber")
            heading.SetForegroundColour(self._theme.text_primary)
            set_font(heading, self._theme.font_size_section_heading)
            sizer.Add(heading, flag=wx.BOTTOM | wx.ALL, border=5)

            label = wx.StaticText(panel, label="Step size")
            sizer.Add(label, flag=wx.LEFT, border=5)

            self.mirror_align_slider_fibaligner = UnitFloatSlider(
                panel,
                value=0.00001,
                min_val=0.0000001,
                max_val=0.001,
                unit="m",
                scale="log",
                accuracy=2,
                size=(-1, 20),
                style=wx.BORDER_NONE,
            )
            sizer.Add(
                self.mirror_align_slider_fibaligner,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            grid = wx.GridBagSizer()

            label_specs = (
                ("lbl_pfy", "+Y", (0, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5, False),
                ("lbl_mfy", "-Y", (4, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5, False),
                ("lbl_pfx", "+X", (2, 4), wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, 5, True),
                (
                    "lbl_mfx",
                    "-X",
                    (2, 0),
                    wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
                    5,
                    False,
                ),
            )
            for attribute, text, position, flag, border, align_left in label_specs:
                label_ctrl = self._axis_label(panel, text, align_left=align_left)
                setattr(self, attribute, label_ctrl)
                grid.Add(label_ctrl, pos=position, flag=flag, border=border)

            button_specs = (
                ("mirror_align_btn_p_fibaligner_y", "↑", (1, 2), wx.LEFT | wx.RIGHT, 7),
                ("mirror_align_btn_m_fibaligner_y", "↓", (3, 2), wx.LEFT | wx.RIGHT, 7),
                ("mirror_align_btn_m_fibaligner_x", "←", (2, 1), 0, 0),
                ("mirror_align_btn_p_fibaligner_x", "→", (2, 3), 0, 0),
            )
            for attribute, text, position, flag, border in button_specs:
                button = self._arrow_button(panel, text)
                setattr(self, attribute, button)
                grid.Add(button, pos=position, flag=flag, border=border)

            sizer.Add(
                grid,
                flag=wx.LEFT | wx.RIGHT | wx.ALIGN_CENTRE,
                border=5,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_ar_viewport(self) -> ARLiveViewport:
        """Build the angle-resolved live viewport with the mirror overlay.

        :returns: Angle-resolved live viewport.
        """
        self.vp_sparc_align = ARLiveViewport(self, size=(400, -1))
        return self.vp_sparc_align

    def _build_settings_column(self) -> wx.Panel:
        """Build the scrollable angle-resolved and spectrometer settings.

        :returns: Right settings column.
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
        self.scr_win_right = wx.ScrolledWindow(
            parent,
            size=(400, -1),
            style=wx.VSCROLL,
        )
        self.scr_win_right.SetBackgroundColour(self._theme.background)
        self.scr_win_right.EnableScrolling(False, True)
        self.scr_win_right.SetScrollbars(-1, 10, 1, 1)

        with vbox() as scroll_sizer:
            scroll_sizer.Add(
                self._build_ar_settings_bar(self.scr_win_right),
                flag=wx.EXPAND,
            )
            scroll_sizer.Add(
                self._build_spectrometer_settings_bar(self.scr_win_right),
                flag=wx.EXPAND,
            )

        self.scr_win_right.SetSizer(scroll_sizer)
        self.scr_win_right.FitInside()
        return self.scr_win_right

    def _build_ar_settings_bar(self, parent: wx.Window) -> FoldPanelBar:
        """Build the angle-resolved settings and stream fold panel bar.

        :param parent: Parent scrolled window.
        :returns: Angle-resolved fold-panel bar.
        """
        fold_bar = FoldPanelBar(parent)
        fold_bar.SetBackgroundColour(self._theme.background)

        self.fp_ma_settings_ar = self._fold_item(fold_bar, "ANGLE-RESOLVED")

        streams_item = FoldPanelItem(fold_bar, nocaption=True)
        streams_item.SetForegroundColour(self._theme.button_text)
        streams_item.SetBackgroundColour(self._theme.section_header)
        self.pnl_sparc_align_streams = self._stream_bar(
            streams_item, size=(300, -1)
        )
        streams_item.add_item(self.pnl_sparc_align_streams)
        fold_bar.add_item(streams_item)

        return fold_bar

    def _build_spectrometer_settings_bar(self, parent: wx.Window) -> FoldPanelBar:
        """Build the spectrometer settings fold panel bar.

        :param parent: Parent scrolled window.
        :returns: Spectrometer fold-panel bar.
        """
        fold_bar = FoldPanelBar(parent)
        fold_bar.SetBackgroundColour(self._theme.background)
        self.fp_ma_settings_spectrum = self._fold_item(fold_bar, "SPECTROMETER")
        return fold_bar

    def _radio_button(
        self,
        parent: wx.Window,
        label: str,
        icon: str,
        icon_on: str,
    ) -> GraphicRadioButton:
        """Create a default-face alignment mode radio button.

        :param parent: Parent window.
        :param label: Button label.
        :param icon: Inactive icon file name.
        :param icon_on: Active icon file name.
        :returns: Alignment mode radio button.
        """
        button = GraphicRadioButton(
            parent,
            label=label,
            icon=img.getBitmap(f"icon/{icon}"),
            icon_on=img.getBitmap(f"icon/{icon_on}"),
            height=48,
            face_colour="def",
        )
        button.SetForegroundColour(self._theme.button_text)
        set_font(button, self._theme.font_size_button)
        return button

    def _arrow_button(self, parent: wx.Window, label: str) -> ImageTextButton:
        """Create a large actuator movement button.

        :param parent: Parent window.
        :param label: Arrow glyph label.
        :returns: Actuator movement button.
        """
        button = ImageTextButton(
            parent,
            label=label,
            height=48,
            size=(64, -1),
            face_colour="def",
            style=wx.ALIGN_CENTRE,
        )
        button.SetForegroundColour(self._theme.button_text)
        set_font(
            button,
            self._theme.font_size_directional_button,
            wx.FONTWEIGHT_BOLD,
        )
        return button

    def _axis_label(
        self,
        parent: wx.Window,
        text: str,
        align_left: bool = False,
    ) -> wx.StaticText:
        """Create an actuator axis label.

        :param parent: Parent window.
        :param text: Label text.
        :param align_left: Use left text alignment instead of the default.
        :returns: Axis label.
        """
        style = wx.ALIGN_LEFT if align_left else 0
        label = wx.StaticText(parent, label=text, style=style)
        label.SetForegroundColour(self._theme.text_primary)
        set_font(
            label,
            self._theme.font_size_axis_label,
            wx.FONTWEIGHT_BOLD,
        )
        return label

    def _stream_bar(
        self,
        parent: wx.Window,
        size: Any = wx.DefaultSize,
    ) -> StreamBar:
        """Create a styled, locked settings stream bar.

        :param parent: Parent window.
        :param size: Explicit control size.
        :returns: Stream bar.
        """
        stream_bar = StreamBar(parent, size=size)
        stream_bar.SetForegroundColour(self._theme.text_muted)
        stream_bar.SetBackgroundColour(self._theme.background)
        return stream_bar

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

    def _actuator_panel(self, parent: wx.Window) -> wx.Panel:
        """Create a panel with the actuator group colours.

        :param parent: Parent window.
        :returns: Styled panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.panel_background)
        return panel


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabSparcAlign)
