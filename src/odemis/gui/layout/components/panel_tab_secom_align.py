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
import wx.adv
import wx.html

from odemis.gui import img
from odemis.gui.comp.buttons import (
    ImageButton,
    ImageTextButton,
    ImageTextToggleButton,
)
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.slider import UnitFloatSlider
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import LiveViewport
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants import strings
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import (
    create_text_button,
)


class PnlTabSecomAlign(wx.Panel):
    """Provide the SECOM alignment tab layout."""

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the alignment controls, settings, and viewports.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, style=wx.WANTS_CHARS)
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_alignment_controls(),
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            ccd_item = root_sizer.Add(
                self._build_ccd_viewport(),
                proportion=1,
                flag=wx.EXPAND,
            )
            ccd_item.SetRatio(1)
            root_sizer.Add(
                self._build_sem_column(),
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_alignment_controls(self) -> wx.Panel:
        """Build the left alignment controls and instructions.

        :returns: Left alignment column.
        """
        panel = wx.Panel(self, size=(300, -1))
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            step_label = wx.StaticText(panel, label="Step size")
            sizer.Add(
                step_label,
                flag=wx.BOTTOM,
                border=5,
            )

            self.lens_align_slider_aligner = UnitFloatSlider(
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
            self.lens_align_slider_aligner.SetForegroundColour(
                self._theme.text_primary
            )
            sizer.Add(self.lens_align_slider_aligner, flag=wx.EXPAND)

            self.pnl_ab_align = self._build_ab_align_panel(panel)
            self.pnl_ab_align.Hide()
            sizer.Add(self.pnl_ab_align, flag=wx.ALIGN_CENTRE)

            self.pnl_xy_align = self._build_xy_align_panel(panel)
            self.pnl_xy_align.Hide()
            sizer.Add(self.pnl_xy_align, flag=wx.ALIGN_CENTRE)

            sizer.Add(
                self._build_alignment_tool_panels(panel),
                flag=wx.ALL | wx.EXPAND,
                border=5,
            )

            self.html_alignment_doc = wx.html.HtmlWindow(
                panel,
                style=wx.html.HW_SCROLLBAR_AUTO | wx.html.HW_NO_SELECTION,
            )
            self.html_alignment_doc.SetForegroundColour("#BBBBBB")
            self.html_alignment_doc.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.html_alignment_doc,
                proportion=1,
                flag=wx.TOP | wx.EXPAND,
                border=5,
            )

            self.btn_log = ImageButton(
                panel,
                icon=img.getBitmap("icon/ico_chevron_up.png"),
                height=16,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_log.SetToolTip(strings.TOOLTIP_OPEN_LOG_PANEL)
            sizer.Add(self.btn_log)

        panel.SetSizer(sizer)
        return panel

    def _build_ccd_viewport(self) -> LiveViewport:
        """Build the optical alignment viewport.

        :returns: Optical alignment viewport.
        """
        self.vp_align_ccd = LiveViewport(self, size=(400, -1))
        return self.vp_align_ccd

    def _build_sem_column(self) -> wx.Panel:
        """Build the SEM controls, settings, toolbar, and viewport column.

        :returns: Right SEM column.
        """
        panel = wx.Panel(self, size=(512, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.main_buttons = self._build_main_buttons(panel)
            self.main_buttons.Hide()
            sizer.Add(self.main_buttons, flag=wx.EXPAND)

            sizer.Add(
                self._build_settings_scroller(panel),
                proportion=1,
                flag=wx.EXPAND,
            )

            self.pnl_sem_toolbar = self._build_sem_toolbar(panel)
            sizer.Add(
                self.pnl_sem_toolbar,
                flag=wx.TOP | wx.EXPAND,
                border=5,
            )

            self.vp_align_sem = LiveViewport(panel)
            self.vp_align_sem.SetMinSize((512, 512))
            sizer.Add(self.vp_align_sem)

        panel.SetSizer(sizer)
        return panel

    def _build_ab_align_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the A/B actuator alignment panel.

        :param parent: Parent window.
        :returns: A/B alignment panel.
        """
        panel = self._dark_panel(parent)
        grid = wx.GridBagSizer()

        label_specs = (
            ("lbl_mb", "-B", (0, 0), 0, 0),
            ("lbl_pa", "+A", (0, 3), 0, 0),
            ("lbl_ma", "-A", (3, 0), wx.ALIGN_RIGHT, 0),
            ("lbl_pb", "+B", (3, 3), 0, 0),
        )
        for attribute, text, position, flag, border in label_specs:
            label = self._axis_label(panel, text)
            setattr(self, attribute, label)
            grid.Add(label, pos=position, flag=flag, border=border)

        button_specs = (
            (
                "lens_align_btn_m_aligner_b",
                "↖",
                (1, 1),
                wx.BOTTOM | wx.RIGHT,
            ),
            (
                "lens_align_btn_p_aligner_a",
                "↗",
                (1, 2),
                wx.BOTTOM | wx.LEFT,
            ),
            (
                "lens_align_btn_m_aligner_a",
                "↙",
                (2, 1),
                wx.TOP | wx.RIGHT,
            ),
            (
                "lens_align_btn_p_aligner_b",
                "↘",
                (2, 2),
                wx.TOP | wx.LEFT,
            ),
        )
        for attribute, text, position, flag in button_specs:
            button = self._alignment_button(panel, text)
            setattr(self, attribute, button)
            grid.Add(button, pos=position, flag=flag, border=7)

        panel.SetSizer(grid)
        return panel

    def _build_xy_align_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the X/Y actuator alignment panel.

        :param parent: Parent window.
        :returns: X/Y alignment panel.
        """
        panel = self._dark_panel(parent)
        grid = wx.GridBagSizer(vgap=0, hgap=5)

        label_specs = (
            ("lbl_py", "+Y", (0, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5),
            ("lbl_my", "-Y", (4, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5),
            ("lbl_px", "+X", (2, 4), wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, 5),
            (
                "lbl_mx",
                "-X",
                (2, 0),
                wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
                5,
            ),
        )
        for attribute, text, position, flag, border in label_specs:
            label = self._axis_label(panel, text)
            setattr(self, attribute, label)
            grid.Add(label, pos=position, flag=flag, border=border)

        button_specs = (
            ("lens_align_btn_p_aligner_y", "↑", (1, 2), wx.LEFT | wx.RIGHT, 7),
            ("lens_align_btn_m_aligner_y", "↓", (3, 2), wx.LEFT | wx.RIGHT, 7),
            ("lens_align_btn_m_aligner_x", "←", (2, 1), 0, 0),
            ("lens_align_btn_p_aligner_x", "→", (2, 3), 0, 0),
        )
        for attribute, text, position, flag, border in button_specs:
            button = self._alignment_button(panel, text)
            setattr(self, attribute, button)
            grid.Add(button, pos=position, flag=flag, border=border)

        panel.SetSizer(grid)
        return panel

    def _build_alignment_tool_panels(self, parent: wx.Window) -> wx.Sizer:
        """Build the dichotomy and automated alignment tool panels.

        :param parent: Parent window.
        :returns: Tool panel group.
        """
        with vbox() as sizer:
            self.pnl_move_to_center = self._build_move_to_center_panel(parent)
            self.pnl_move_to_center.Hide()
            sizer.Add(
                self.pnl_move_to_center,
                flag=wx.TOP | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.pnl_align_tools = self._build_auto_align_panel(parent)
            self.pnl_align_tools.Hide()
            sizer.Add(
                self.pnl_align_tools,
                flag=wx.TOP | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        return sizer

    def _build_move_to_center_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the dichotomy move-to-center panel.

        :param parent: Parent window.
        :returns: Move-to-center panel.
        """
        panel = self._dark_panel(parent)
        with vbox() as sizer:
            self.lens_align_lbl_approc_center = wx.StaticText(panel)
            sizer.Add(self.lens_align_lbl_approc_center, flag=wx.EXPAND)

            self.lens_align_btn_to_center = create_text_button(panel, "Move to center", height=24, text_colour=self._theme.button_text, contrast_text_colour=self._theme.button_text_contrast, style=wx.ALIGN_CENTRE)
            sizer.Add(
                self.lens_align_btn_to_center,
                flag=wx.ALIGN_RIGHT,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_auto_align_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the auto-centering and fine-alignment controls.

        :param parent: Parent window.
        :returns: Automated alignment panel.
        """
        panel = self._dark_panel(parent)
        with vbox() as sizer:
            sizer.Add(
                self._build_progress_action_row(
                    panel,
                    button_attribute="btn_auto_center",
                    label="Auto centering...",
                    label_attribute="lbl_auto_center",
                    gauge_attribute="gauge_auto_center",
                ),
                flag=wx.BOTTOM | wx.EXPAND,
                border=14,
            )
            sizer.Add(
                self._build_progress_action_row(
                    panel,
                    button_attribute="btn_fine_align",
                    label="Fine alignment...",
                    label_attribute="lbl_fine_align",
                    gauge_attribute="gauge_fine_align",
                ),
                flag=wx.BOTTOM | wx.EXPAND,
                border=14,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_progress_action_row(
        self,
        parent: wx.Window,
        button_attribute: str,
        label: str,
        label_attribute: str,
        gauge_attribute: str,
    ) -> wx.Sizer:
        """Build an action button, estimate label, and progress gauge row.

        :param parent: Parent window.
        :param button_attribute: Public button attribute name.
        :param label: Button label.
        :param label_attribute: Public estimate label attribute name.
        :param gauge_attribute: Public gauge attribute name.
        :returns: Progress action row.
        """
        with hbox() as sizer:
            button = create_text_button(parent, label, height=24, text_colour=self._theme.button_text, contrast_text_colour=self._theme.button_text_contrast, style=wx.ALIGN_CENTRE)
            setattr(self, button_attribute, button)
            sizer.Add(button, flag=wx.RIGHT, border=self._theme.spacing_standard)

            estimate = wx.StaticText(
                parent,
                label="~ 30 seconds",
                style=wx.ALIGN_CENTRE,
            )
            setattr(self, label_attribute, estimate)
            sizer.Add(estimate, flag=wx.TOP, border=3)

            gauge = wx.Gauge(
                parent,
                range=100,
                size=(-1, 10),
                style=wx.GA_HORIZONTAL | wx.GA_SMOOTH,
            )
            gauge.SetValue(0)
            gauge.Hide()
            setattr(self, gauge_attribute, gauge)
            sizer.Add(
                gauge,
                proportion=1,
                flag=wx.TOP,
                border=7,
            )

        return sizer

    def _build_main_buttons(self, parent: wx.Window) -> wx.Panel:
        """Build SEM, optical, and preset controls.

        :param parent: Parent window.
        :returns: Main buttons panel.
        """
        panel = wx.Panel(parent, size=(512, -1))
        panel.SetBackgroundColour(self._theme.field_background)

        with hbox() as sizer:
            self.lens_align_btn_sem = self._toggle_button(
                panel,
                "SEM",
                "ico_sem.png",
                "ico_sem_green.png",
            )
            sizer.Add(
                self.lens_align_btn_sem,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT,
                border=self._theme.spacing_standard,
            )

            self.lens_align_btn_opt = self._toggle_button(
                panel,
                "OPTICAL",
                "ico_optical.png",
                "ico_optical_green.png",
            )
            sizer.Add(
                self.lens_align_btn_opt,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT,
                border=self._theme.spacing_standard,
            )
            sizer.AddStretchSpacer()
            sizer.Add(
                self._build_preset_controls(panel),
                flag=wx.TOP | wx.RIGHT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_preset_controls(self, parent: wx.Window) -> wx.Sizer:
        """Build the hidden preset label and combo box.

        :param parent: Parent window.
        :returns: Preset control sizer.
        """
        with vbox() as sizer:
            label = wx.StaticText(parent, label="Presets")
            label.SetForegroundColour(self._theme.button_text_contrast)
            label.Hide()
            sizer.Add(
                label,
                flag=wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=6,
            )

            self.cmb_lens_align_presets = wx.adv.OwnerDrawnComboBox(
                parent,
                size=(-1, 16),
                style=(
                    wx.BORDER_NONE
                    | wx.CB_DROPDOWN
                    | wx.CB_READONLY
                    | wx.TE_PROCESS_ENTER
                ),
            )
            self.cmb_lens_align_presets.SetButtonBitmaps(
                img.getBitmap("button/btn_down.png"),
                pushButtonBg=False,
            )
            self.cmb_lens_align_presets.SetForegroundColour(
                self._theme.text_edit
            )
            self.cmb_lens_align_presets.SetBackgroundColour("#424242")
            self.cmb_lens_align_presets.Hide()
            sizer.Add(self.cmb_lens_align_presets, flag=wx.EXPAND)

        return sizer

    def _build_settings_scroller(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable optical and SEM settings stream bars.

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
        """Build optical and SEM settings sections.

        :param fold_bar: Parent fold-panel bar.
        """
        optical_item = self._fold_item(fold_bar, "OPTICAL SETTINGS")
        self.pnl_opt_streams = self._stream_bar(optical_item)
        optical_item.add_item(self.pnl_opt_streams)

        sem_item = self._fold_item(fold_bar, "SEM SETTINGS")
        self.pnl_sem_streams = self._stream_bar(sem_item)
        sem_item.add_item(self.pnl_sem_streams)

    def _build_sem_toolbar(self, parent: wx.Window) -> wx.Panel:
        """Build the SEM viewport toolbar panel.

        :param parent: Parent window.
        :returns: SEM toolbar panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour("#BBBBBB")
        panel.SetBackgroundColour(self._theme.background)

        with hbox() as sizer:
            self.lens_align_tb = ToolBar(panel, style=wx.HORIZONTAL)
            sizer.Add(
                self.lens_align_tb,
                flag=wx.RIGHT,
                border=self._theme.spacing_standard,
            )
            sizer.AddStretchSpacer()

        panel.SetSizer(sizer)
        return panel

    def _axis_label(self, parent: wx.Window, text: str) -> wx.StaticText:
        """Create an actuator axis label.

        :param parent: Parent window.
        :param text: Label text.
        :returns: Axis label.
        """
        label = wx.StaticText(parent, label=text)
        label.SetForegroundColour(self._theme.text_primary)
        set_font(
            label,
            self._theme.font_size_axis_label,
            wx.FONTWEIGHT_BOLD,
        )
        return label

    def _alignment_button(
        self,
        parent: wx.Window,
        label: str,
    ) -> ImageTextButton:
        """Create a large actuator movement button.

        :param parent: Parent window.
        :param label: Button label.
        :returns: Actuator movement button.
        """
        return create_text_button(parent, label, height=48, text_colour=self._theme.button_text, contrast_text_colour=self._theme.button_text_contrast, size=(64, -1), style=wx.ALIGN_CENTRE, font_size=self._theme.font_size_directional_button, font_weight=wx.FONTWEIGHT_BOLD)

    def _toggle_button(
        self,
        parent: wx.Window,
        label: str,
        icon: str,
        icon_on: str,
    ) -> ImageTextToggleButton:
        """Create a default-face hardware toggle button.

        :param parent: Parent window.
        :param label: Button label.
        :param icon: Inactive icon file name.
        :param icon_on: Active icon file name.
        :returns: Hardware toggle button.
        """
        button = ImageTextToggleButton(
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

    def _stream_bar(self, parent: wx.Window) -> StreamBar:
        """Create a styled settings stream bar.

        :param parent: Parent window.
        :returns: Stream bar.
        """
        stream_bar = StreamBar(parent)
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

    def _dark_panel(self, parent: wx.Window) -> wx.Panel:
        """Create a panel with the alignment column colours.

        :param parent: Parent window.
        :returns: Styled panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.background)
        return panel


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabSecomAlign)
