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
import wx.adv
import wx.html

from odemis.gui import img
from odemis.gui.comp.buttons import (
    GraphicRadioButton,
    ImageButton,
    ImageTextButton,
    ImageTextToggleButton,
)
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.slider import UnitFloatSlider
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import (
    ARLiveViewport,
    ChronographViewport,
    EKLiveViewport,
    LiveViewport,
    TemporalSpectrumViewport,
)
from odemis.gui.layout.constants import strings
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import create_text_button

# (attribute name, label, icon file name, active icon file name, cellpos column)
_MODE_BUTTONS: Tuple[Tuple[str, str, str, str, int], ...] = (
    ("btn_align_lens", "LENS", "ico_lens.png", "ico_lens_green.png", 0),
    ("btn_align_mirror", "MIRROR", "ico_mirror.png", "ico_mirror_green.png", 1),
    ("btn_align_centering", "CENTERING", "ico_ang.png", "ico_ang_green.png", 2),
    ("btn_align_lens2", "EK LENS", "ico_lens_ek.png", "ico_lens_green_ek.png", 3),
    ("btn_align_ek", "EK CENTERING", "ico_ang_ek.png", "ico_ang_green_ek.png", 4),
    ("btn_align_streakcam", "STREAK", "ico_fib.png", "ico_fib_green.png", 5),
    ("btn_align_fiber", "FIBER", "ico_fib.png", "ico_fib_green.png", 6),
    ("btn_align_light_in", "LIGHT-IN", "ico_fib.png", "ico_fib_green.png", 8),
    ("btn_align_light_in_ar", "LIGHT-IN AR", "ico_fib.png", "ico_fib_green.png", 9),
    (
        "btn_align_tunnel_lens",
        "TUNNEL",
        "ico_freespacetunnel.png",
        "ico_freespacetunnel_green.png",
        10,
    ),
)


class PnlTabSparc2Align(wx.Panel):
    """Provide the SPARC2 alignment tab layout.

    Hosts the alignment mode selector, the per-mode actuator control
    panels, the multi-viewport grid, and the settings column for the
    Sparc2AlignTab controller.
    """

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the alignment mode, actuator, viewport, and settings
        controls.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, style=wx.WANTS_CHARS)
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(self._build_controls_column(), flag=wx.EXPAND)

            self.pnl_vp_grid = self._build_viewport_grid()
            root_sizer.Add(self.pnl_vp_grid, proportion=1, flag=wx.EXPAND)

            root_sizer.Add(self._build_settings_column(), flag=wx.EXPAND)

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_controls_column(self) -> wx.Panel:
        """Build the alignment mode buttons, actuator sections, and log
        toggle.

        :returns: Left controls column.
        """
        panel = wx.Panel(self, size=(400, -1))
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.pnl_mode_btns = self._build_mode_buttons(panel)
            sizer.Add(self.pnl_mode_btns, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_blanker_status = self._build_blanker_status(panel)
            sizer.Add(
                self.pnl_blanker_status,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.pnl_focus = self._build_focus_panel(
                panel, "Spectrograph Focus", suffix=""
            )
            sizer.Add(self.pnl_focus, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_grating = self._build_grating_panel(panel)
            sizer.Add(self.pnl_grating, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_focus_ext = self._build_focus_panel(
                panel, "Focus Ext", suffix="_ext"
            )
            sizer.Add(self.pnl_focus_ext, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_lens_mover = self._build_single_axis_panel(
                panel,
                "Lens 1",
                slider_attr="slider_lens_mover",
                plus_label_attr="lbl_p_lens",
                minus_label_attr="lbl_m_lens",
                minus_btn_attr="btn_m_lens_mover_x",
                plus_btn_attr="btn_p_lens_mover_x",
            )
            sizer.Add(self.pnl_lens_mover, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_lens_switch = self._build_single_axis_panel(
                panel,
                "Lens 2",
                slider_attr="slider_lens_switch",
                plus_label_attr="lbl_p_lens2",
                minus_label_attr="lbl_m_lens2",
                minus_btn_attr="btn_m_lens_switch_x",
                plus_btn_attr="btn_p_lens_switch_x",
            )
            sizer.Add(self.pnl_lens_switch, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_lens_tunnel = self._build_lens_tunnel_panel(panel)
            sizer.Add(self.pnl_lens_tunnel, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_mirror = self._build_mirror_panel(panel)
            sizer.Add(self.pnl_mirror, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_light_aligner = self._build_light_aligner_panel(panel)
            sizer.Add(
                self.pnl_light_aligner, flag=wx.BOTTOM | wx.EXPAND, border=5
            )

            self.pnl_streak = self._build_streak_panel(panel)
            sizer.Add(self.pnl_streak, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_spec_switch = self._build_spec_switch_panel(panel)
            sizer.Add(self.pnl_spec_switch, flag=wx.BOTTOM | wx.EXPAND, border=5)

            self.pnl_fibaligner = self._build_fibaligner_panel(panel)
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

    def _build_mode_buttons(self, parent: wx.Window) -> wx.Panel:
        """Build the alignment mode selector grid.

        :param parent: Parent window.
        :returns: Mode button panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            grid = wx.GridBagSizer(vgap=10, hgap=10)
            for attribute, label, icon, icon_on, column in _MODE_BUTTONS:
                button = self._mode_button(panel, label, icon, icon_on)
                setattr(self, attribute, button)
                grid.Add(button, pos=(5, column), flag=wx.EXPAND)
            sizer.Add(grid, flag=wx.ALL, border=self._theme.spacing_standard)

        panel.SetSizer(sizer)
        return panel

    def _build_blanker_status(self, parent: wx.Window) -> wx.Panel:
        """Build the manual e-beam blanker warning row.

        :param parent: Parent window.
        :returns: Blanker status panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.Hide()

        with hbox() as sizer:
            self.bmp_blanker_status = wx.StaticBitmap(
                panel, bitmap=img.getBitmap("icon/dialog_warning.png")
            )
            sizer.Add(self.bmp_blanker_status, flag=wx.RIGHT, border=5)

            self.lbl_blanker_status = wx.StaticText(
                panel, label="Make sure the e-beam is blanked manually"
            )
            sizer.Add(self.lbl_blanker_status)

        panel.SetSizer(sizer)
        return panel

    def _build_focus_panel(
        self, parent: wx.Window, title: str, suffix: str
    ) -> wx.Panel:
        """Build a spectrograph focus panel with detector/grating selectors.

        :param parent: Parent window.
        :param title: Section title.
        :param suffix: Attribute name suffix, empty for the primary
            spectrograph or "_ext" for the external one.
        :returns: Focus panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(
                self._section_title(panel, title), flag=wx.BOTTOM | wx.ALL, border=5
            )

            grid = wx.GridBagSizer(vgap=5, hgap=5)

            autofocus_btn = create_text_button(
                panel,
                "Auto focus",
                height=24,
                text_colour=self._theme.button_text,
            )
            autofocus_btn.SetToolTip(
                "Attempts to auto focus the spectrometer with all its "
                "gratings and detectors."
            )
            setattr(self, f"btn_autofocus{suffix}", autofocus_btn)
            grid.Add(
                autofocus_btn,
                pos=(0, 0),
                flag=wx.LEFT,
                border=5,
            )

            gauge = wx.Gauge(panel, size=(-1, 10), range=100, style=wx.GA_SMOOTH)
            gauge.SetValue(0)
            setattr(self, f"gauge_autofocus{suffix}", gauge)
            grid.Add(
                gauge, pos=(0, 1), flag=wx.ALL | wx.EXPAND, border=7
            )

            manual_focus_btn = ImageTextToggleButton(
                panel,
                label="Manual focus",
                height=24,
                face_colour="def",
                active_colour="#106090",
                style=wx.ALIGN_CENTRE,
            )
            manual_focus_btn.SetForegroundColour(self._theme.button_text)
            manual_focus_btn.SetToolTip(
                "If active allows to manually focus the spectrometer for "
                "the selected grating and detector."
            )
            setattr(self, f"btn_manual_focus{suffix}", manual_focus_btn)
            grid.Add(manual_focus_btn, pos=(1, 0), flag=wx.LEFT, border=5)

            slider = UnitFloatSlider(
                panel,
                value=0.00001,
                min_val=0.0000001,
                max_val=0.001,
                unit="m",
                scale="linear",
                accuracy=2,
                size=(-1, 20),
                style=wx.BORDER_NONE,
            )
            setattr(self, f"slider_focus{suffix}", slider)
            grid.Add(
                slider,
                pos=(1, 1),
                flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
                border=5,
            )
            grid.AddGrowableCol(1)

            sizer.Add(grid, flag=wx.BOTTOM | wx.EXPAND, border=10)

            with hbox() as combo_sizer:
                combo_sizer.Add(
                    self._label(panel, "Detectors"), flag=wx.LEFT, border=5
                )

                detectors = self._combo_box(panel)
                setattr(self, f"cmb_focus_detectors{suffix}", detectors)
                combo_sizer.Add(detectors, flag=wx.EXPAND)

                gratings_label = wx.StaticText(panel, label="Gratings")
                setattr(self, f"cmb_focus_gratings_label{suffix}", gratings_label)
                combo_sizer.Add(gratings_label, flag=wx.LEFT, border=5)

                gratings = self._combo_box(panel)
                setattr(self, f"cmb_focus_gratings{suffix}", gratings)
                combo_sizer.Add(gratings, flag=wx.EXPAND)

            sizer.Add(combo_sizer, flag=wx.BOTTOM | wx.EXPAND, border=10)

        panel.SetSizer(sizer)
        return panel

    def _build_grating_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the grating auto-calibration panel.

        :param parent: Parent window.
        :returns: Grating calibration panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(
                self._section_title(panel, "Grating calibration"),
                flag=wx.ALL,
                border=5,
            )

            grid = wx.GridBagSizer(vgap=5, hgap=5)

            self.btn_auto_grating_center = create_text_button(
                panel,
                "Auto calib",
                height=24,
                text_colour=self._theme.button_text,
            )
            self.btn_auto_grating_center.SetToolTip(
                "Auto-calibrate grating offset by centering the zero-th "
                "order peak."
            )
            grid.Add(
                self.btn_auto_grating_center, pos=(0, 0), flag=wx.LEFT, border=5
            )

            self.gauge_auto_grating_center = wx.Gauge(
                panel, size=(-1, 10), range=100, style=wx.GA_SMOOTH
            )
            self.gauge_auto_grating_center.SetValue(0)
            grid.Add(
                self.gauge_auto_grating_center,
                pos=(0, 1),
                flag=wx.ALL | wx.EXPAND,
                border=7,
            )
            grid.AddGrowableCol(1)

            sizer.Add(grid, flag=wx.BOTTOM | wx.EXPAND, border=10)

        panel.SetSizer(sizer)
        return panel

    def _build_single_axis_panel(
        self,
        parent: wx.Window,
        title: str,
        slider_attr: str,
        plus_label_attr: str,
        minus_label_attr: str,
        minus_btn_attr: str,
        plus_btn_attr: str,
    ) -> wx.Panel:
        """Build a single X-axis step-size and arrow-button panel.

        :param parent: Parent window.
        :param title: Section title.
        :param slider_attr: Public step-size slider attribute name.
        :param plus_label_attr: Public +X label attribute name.
        :param minus_label_attr: Public -X label attribute name.
        :param minus_btn_attr: Public -X button attribute name.
        :param plus_btn_attr: Public +X button attribute name.
        :returns: Single-axis actuator panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(
                self._section_title(panel, title), flag=wx.BOTTOM | wx.ALL, border=5
            )
            sizer.Add(
                self._step_size_row(panel, slider_attr),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            grid = wx.GridBagSizer(vgap=0, hgap=5)
            minus_label = self._axis_label(minus_label_attr, panel, "-X")
            grid.Add(
                minus_label,
                pos=(0, 0),
                flag=wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
                border=5,
            )
            minus_btn = self._arrow_button(minus_btn_attr, panel, "\u2190")
            grid.Add(minus_btn, pos=(0, 1))
            plus_btn = self._arrow_button(plus_btn_attr, panel, "\u2192")
            grid.Add(plus_btn, pos=(0, 3))
            plus_label = self._axis_label(
                plus_label_attr, panel, "+X", align_left=True
            )
            grid.Add(
                plus_label,
                pos=(0, 4),
                flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
                border=5,
            )
            sizer.Add(grid, flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, border=10)

        panel.SetSizer(sizer)
        return panel

    def _build_lens_tunnel_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the dedicated-spectrometer-aligner X/Y/Z panel.

        :param parent: Parent window.
        :returns: Lens tunnel panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(self._section_title(panel, "Lens"), flag=wx.ALL, border=5)
            sizer.Add(
                self._step_size_row(panel, "slider_spec_ded_aligner_xy"),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )
            sizer.Add(
                self._build_xy_grid(
                    panel,
                    py="lbl_spec_ded_aligner_py",
                    my="lbl_spec_ded_aligner_my",
                    px="lbl_spec_ded_aligner_px",
                    mx="lbl_spec_ded_aligner_mx",
                    btn_py="btn_p_spec_ded_aligner_y",
                    btn_my="btn_m_spec_ded_aligner_y",
                    btn_mx="btn_m_spec_ded_aligner_x",
                    btn_px="btn_p_spec_ded_aligner_x",
                ),
                flag=wx.ALIGN_CENTRE,
            )

            self.lbl_ss_spec_ded_aligner_z = self._label(panel, "Step size")
            sizer.Add(
                self.lbl_ss_spec_ded_aligner_z, flag=wx.LEFT, border=5
            )
            self.slider_spec_ded_aligner_z = self._slider(
                panel, value=0.000025, min_val=0.0000003, max_val=0.003
            )
            sizer.Add(
                self.slider_spec_ded_aligner_z,
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            sizer.Add(
                self._build_xy_grid(
                    panel,
                    py="lbl_spec_ded_aligner_pz",
                    my="lbl_spec_ded_aligner_mz",
                    px=None,
                    mx=None,
                    btn_py="btn_m_spec_ded_aligner_z",
                    btn_my="btn_p_spec_ded_aligner_z",
                    btn_mx=None,
                    btn_px=None,
                    single_row=True,
                ),
                flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE,
                border=10,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_mirror_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the mirror auto-align and X/Y/Z stepper panel.

        :param parent: Parent window.
        :returns: Mirror panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(self._section_title(panel, "Mirror"), flag=wx.ALL, border=5)

            grid = wx.GridBagSizer(vgap=5, hgap=5)

            self.btn_auto_align = create_text_button(
                panel,
                "Auto align",
                height=24,
                text_colour=self._theme.button_text,
            )
            self.btn_auto_align.Hide()
            grid.Add(self.btn_auto_align, pos=(0, 0), flag=wx.LEFT, border=5)

            self.gauge_auto_align = wx.Gauge(
                panel, size=(-1, 10), range=100, style=wx.GA_SMOOTH
            )
            self.gauge_auto_align.SetValue(0)
            self.gauge_auto_align.Hide()
            grid.Add(
                self.gauge_auto_align, pos=(0, 1), flag=wx.ALL | wx.EXPAND, border=7
            )
            grid.AddGrowableCol(1)
            sizer.Add(grid, flag=wx.BOTTOM | wx.EXPAND, border=10)

            self.lbl_step_size_xy = self._label(panel, "Step size (X, Y)")
            sizer.Add(
                self._step_size_row(
                    panel, "slider_mirror_xy", label=self.lbl_step_size_xy
                ),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            self.lbl_step_size_x = self._label(panel, "Step size (X)")
            self.lbl_step_size_x.Hide()
            self.slider_mirror_x = self._slider(panel)
            self.slider_mirror_x.Hide()
            sizer.Add(
                self._step_size_row(
                    panel,
                    "slider_mirror_x",
                    label=self.lbl_step_size_x,
                    slider=self.slider_mirror_x,
                ),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            self.lbl_step_size_y = self._label(panel, "Step size (Y)")
            self.lbl_step_size_y.Hide()
            self.slider_mirror_y = self._slider(panel)
            self.slider_mirror_y.Hide()
            sizer.Add(
                self._step_size_row(
                    panel,
                    "slider_mirror_y",
                    label=self.lbl_step_size_y,
                    slider=self.slider_mirror_y,
                ),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            self.lbl_step_size_z = self._label(panel, "Step size (Z)")
            self.lbl_step_size_z.Hide()
            self.slider_stage = self._slider(panel)
            self.slider_stage.Hide()
            sizer.Add(
                self._step_size_row(
                    panel,
                    "slider_stage",
                    label=self.lbl_step_size_z,
                    slider=self.slider_stage,
                ),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            xy_grid = self._build_xy_grid(
                panel,
                py="lbl_py",
                my="lbl_my",
                px="lbl_px",
                mx="lbl_mx",
                btn_py="btn_p_mirror_xy_y",
                btn_my="btn_m_mirror_xy_y",
                btn_mx="btn_m_mirror_xy_x",
                btn_px="btn_p_mirror_xy_x",
            )
            self.lbl_pz = self._axis_label("lbl_pz", panel, "+Z")
            self.lbl_pz.Hide()
            xy_grid.Add(
                self.lbl_pz,
                pos=(0, 6),
                flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE,
                border=5,
            )
            self.btn_p_stage_z = self._arrow_button("btn_p_stage_z", panel, "\u2191")
            self.btn_p_stage_z.Hide()
            xy_grid.Add(
                self.btn_p_stage_z,
                pos=(1, 6),
                flag=wx.LEFT | wx.RIGHT,
                border=7,
            )
            self.btn_m_stage_z = self._arrow_button("btn_m_stage_z", panel, "\u2193")
            self.btn_m_stage_z.Hide()
            xy_grid.Add(
                self.btn_m_stage_z,
                pos=(3, 6),
                flag=wx.LEFT | wx.RIGHT,
                border=7,
            )
            self.lbl_mz = self._axis_label("lbl_mz", panel, "-Z")
            self.lbl_mz.Hide()
            xy_grid.Add(
                self.lbl_mz,
                pos=(4, 6),
                flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE,
                border=5,
            )
            sizer.Add(xy_grid, flag=wx.ALIGN_CENTRE)

        panel.SetSizer(sizer)
        return panel

    def _build_light_aligner_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the light-aligner X/Z stepper panel.

        :param parent: Parent window.
        :returns: Light aligner panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(
                self._section_title(panel, "Light Aligner"),
                flag=wx.BOTTOM | wx.ALL,
                border=5,
            )
            sizer.Add(
                self._step_size_row(panel, "slider_light_aligner"),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

            grid = wx.GridBagSizer(vgap=0, hgap=5)
            self.lbl_p_light_aligner_z = self._axis_label(
                None, panel, "+Z"
            )
            grid.Add(
                self.lbl_p_light_aligner_z,
                pos=(0, 2),
                flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE,
                border=5,
            )
            self.btn_p_light_aligner_z = self._arrow_button(
                None, panel, "\u2191"
            )
            grid.Add(self.btn_p_light_aligner_z, pos=(1, 2))

            self.lbl_m_light_aligner_x = self._axis_label(
                None,
                panel,
                "-X",
            )
            grid.Add(
                self.lbl_m_light_aligner_x,
                pos=(2, 0),
                flag=wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
                border=5,
            )
            self.btn_m_light_aligner_x = self._arrow_button(
                None, panel, "\u2190"
            )
            grid.Add(self.btn_m_light_aligner_x, pos=(2, 1))
            self.btn_p_light_aligner_x = self._arrow_button(
                None, panel, "\u2192"
            )
            grid.Add(self.btn_p_light_aligner_x, pos=(2, 3))
            self.lbl_p_light_aligner_x = self._axis_label(
                None, panel, "+X", align_left=True
            )
            grid.Add(
                self.lbl_p_light_aligner_x,
                pos=(2, 4),
                flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
                border=5,
            )
            self.btn_m_light_aligner_z = self._arrow_button(
                None, panel, "\u2193"
            )
            grid.Add(self.btn_m_light_aligner_z, pos=(3, 2))
            self.lbl_m_light_aligner_z = self._axis_label(
                None, panel, "-Z"
            )
            grid.Add(
                self.lbl_m_light_aligner_z,
                pos=(4, 2),
                flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE,
                border=5,
            )
            sizer.Add(grid, flag=wx.BOTTOM | wx.ALIGN_CENTRE, border=10)

        panel.SetSizer(sizer)
        return panel

    def _build_streak_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the streak camera calibration-file panel.

        :param parent: Parent window.
        :returns: Streak panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(self._section_title(panel, "Streak"), flag=wx.ALL, border=5)

            with hbox() as file_sizer:
                label = wx.StaticText(panel, label="Calibration File")
                set_font(label, 9)
                file_sizer.Add(label, flag=wx.ALL | wx.ALIGN_CENTRE, border=5)

                self.txt_StreakCalibFilename = wx.TextCtrl(
                    panel,
                    value="Calibration not saved!",
                    style=wx.BORDER_NONE | wx.TE_READONLY,
                )
                self.txt_StreakCalibFilename.SetForegroundColour(
                    self._theme.text_edit
                )
                self.txt_StreakCalibFilename.SetBackgroundColour(
                    self._theme.field_background
                )
                file_sizer.Add(
                    self.txt_StreakCalibFilename,
                    proportion=1,
                    flag=wx.ALL | wx.EXPAND,
                    border=5,
                )

                self.btn_open_streak_calib_file = create_text_button(
                    panel,
                    "Load...",
                    height=16,
                    text_colour=self._theme.button_text,
                )
                self.btn_open_streak_calib_file.SetToolTip(
                    "Open a trigger calibration file."
                )
                file_sizer.Add(
                    self.btn_open_streak_calib_file,
                    flag=wx.LEFT | wx.ALIGN_CENTRE,
                    border=5,
                )

                self.btn_save_streak_calib_file = create_text_button(
                    panel,
                    "Save...",
                    height=16,
                    text_colour=self._theme.button_text,
                )
                self.btn_save_streak_calib_file.SetToolTip(
                    "Save the trigger calibration to a file."
                )
                file_sizer.Add(
                    self.btn_save_streak_calib_file,
                    flag=wx.LEFT | wx.ALIGN_CENTRE,
                    border=5,
                )

            sizer.Add(
                file_sizer, flag=wx.EXPAND | wx.RIGHT, border=5
            )

        panel.SetSizer(sizer)
        return panel

    def _build_spec_switch_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the light-out selector-mirror stepper and retract/engage
        controls.

        :param parent: Parent window.
        :returns: Spectrometer switch panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(
                self._section_title(panel, "Light Out"),
                flag=wx.BOTTOM | wx.ALL,
                border=5,
            )
            sizer.Add(
                self._step_size_row(panel, "slider_spec_switch"),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )
            sizer.Add(
                self._build_single_axis_grid(
                    panel,
                    minus_label_attr="lbl_m_spec_switch",
                    plus_label_attr="lbl_p_spec_switch",
                    minus_btn_attr="btn_m_spec_switch_x",
                    plus_btn_attr="btn_p_spec_switch_x",
                ),
                flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE,
                border=10,
            )

            with hbox() as action_sizer:
                self.btn_spec_switch_retract = create_text_button(
                    panel,
                    "Retract",
                    height=24,
                    text_colour=self._theme.button_text,
                )
                self.btn_spec_switch_retract.SetToolTip(
                    "Moves the selector mirror away, so that the light "
                    "goes to the internal spectrograph."
                )
                action_sizer.Add(
                    self.btn_spec_switch_retract, flag=wx.LEFT, border=5
                )

                self.btn_spec_switch_engage = create_text_button(
                    panel,
                    "Engage",
                    height=24,
                    text_colour=self._theme.button_text,
                )
                self.btn_spec_switch_engage.SetToolTip(
                    "Moves the selector mirror so that the light goes "
                    "out. It is then possible to align it."
                )
                action_sizer.Add(
                    self.btn_spec_switch_engage, flag=wx.LEFT, border=5
                )

                self.gauge_specswitch = wx.Gauge(
                    panel, size=(-1, 10), range=100, style=wx.GA_SMOOTH
                )
                self.gauge_specswitch.SetValue(0)
                action_sizer.Add(
                    self.gauge_specswitch,
                    proportion=1,
                    flag=wx.LEFT | wx.RIGHT | wx.ALIGN_CENTRE,
                    border=10,
                )

            sizer.Add(action_sizer, flag=wx.BOTTOM | wx.EXPAND, border=10)

        panel.SetSizer(sizer)
        return panel

    def _build_fibaligner_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the optical-fiber X/Y stepper and focus panel.

        :param parent: Parent window.
        :returns: Fiber aligner panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            sizer.Add(
                self._section_title(panel, "Fiber"), flag=wx.BOTTOM | wx.ALL, border=5
            )
            sizer.Add(
                self._step_size_row(panel, "slider_fibaligner"),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )
            sizer.Add(
                self._build_xy_grid(
                    panel,
                    py="lbl_pfy",
                    my="lbl_mfy",
                    px="lbl_pfx",
                    mx="lbl_mfx",
                    btn_py="btn_p_fibaligner_y",
                    btn_my="btn_m_fibaligner_y",
                    btn_mx="btn_m_fibaligner_x",
                    btn_px="btn_p_fibaligner_x",
                ),
                flag=wx.LEFT | wx.RIGHT | wx.ALIGN_CENTRE,
                border=5,
            )
            sizer.Add(
                self._build_fib_focus_panel(panel),
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_fib_focus_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the fiber-coupled spectrometer autofocus row.

        :param parent: Parent window.
        :returns: Fiber focus panel.
        """
        self.pnl_fib_focus = self._section_panel(parent)

        with hbox() as sizer:
            self.btn_fib_autofocus = create_text_button(
                self.pnl_fib_focus,
                "Auto focus",
                height=24,
                text_colour=self._theme.button_text,
            )
            self.btn_fib_autofocus.SetToolTip(
                "Attempts to auto focus the spectrometer, which is "
                "connected via the optical fiber, with all its gratings "
                "and detectors."
            )
            sizer.Add(self.btn_fib_autofocus)

            self.gauge_fib_autofocus = wx.Gauge(
                self.pnl_fib_focus, size=(-1, 10), range=100, style=wx.GA_SMOOTH
            )
            self.gauge_fib_autofocus.SetValue(0)
            sizer.Add(
                self.gauge_fib_autofocus,
                proportion=1,
                flag=wx.LEFT | wx.ALIGN_CENTRE,
                border=7,
            )

        self.pnl_fib_focus.SetSizer(sizer)
        return self.pnl_fib_focus

    def _build_xy_grid(
        self,
        parent: wx.Window,
        py: Optional[str],
        my: Optional[str],
        px: Optional[str],
        mx: Optional[str],
        btn_py: str,
        btn_my: str,
        btn_mx: Optional[str],
        btn_px: Optional[str],
        single_row: bool = False,
    ) -> wx.GridBagSizer:
        """Build a plus/minus X/Y (or single-column) actuator button grid.

        :param parent: Parent window.
        :param py: Public +Y (or top) label attribute name, or None.
        :param my: Public -Y (or bottom) label attribute name, or None.
        :param px: Public +X label attribute name, or None.
        :param mx: Public -X label attribute name, or None.
        :param btn_py: Public up-arrow button attribute name.
        :param btn_my: Public down-arrow button attribute name.
        :param btn_mx: Public left-arrow button attribute name, or None.
        :param btn_px: Public right-arrow button attribute name, or None.
        :param single_row: Build only the vertical (Y) column, no X row.
        :returns: Actuator button grid.
        """
        grid = wx.GridBagSizer(vgap=0, hgap=5)

        if py is not None:
            label = self._axis_label(py, parent, "+Y")
            grid.Add(
                label, pos=(0, 2), flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, border=5
            )
        if my is not None:
            label = self._axis_label(my, parent, "-Y")
            grid.Add(
                label, pos=(4, 2), flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, border=5
            )

        up_btn = self._arrow_button(btn_py, parent, "\u2191")
        grid.Add(up_btn, pos=(1, 2), flag=wx.LEFT | wx.RIGHT, border=7)
        down_btn = self._arrow_button(btn_my, parent, "\u2193")
        grid.Add(down_btn, pos=(3, 2), flag=wx.LEFT | wx.RIGHT, border=7)

        if not single_row:
            if mx is not None:
                label = self._axis_label(mx, parent, "-X")
                grid.Add(
                    label,
                    pos=(2, 0),
                    flag=wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
                    border=5,
                )
            if btn_mx is not None:
                left_btn = self._arrow_button(btn_mx, parent, "\u2190")
                grid.Add(left_btn, pos=(2, 1))
            if btn_px is not None:
                right_btn = self._arrow_button(btn_px, parent, "\u2192")
                grid.Add(right_btn, pos=(2, 3))
            if px is not None:
                label = self._axis_label(px, parent, "+X", align_left=True)
                grid.Add(
                    label,
                    pos=(2, 4),
                    flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
                    border=5,
                )

        return grid

    def _build_single_axis_grid(
        self,
        parent: wx.Window,
        minus_label_attr: str,
        plus_label_attr: str,
        minus_btn_attr: str,
        plus_btn_attr: str,
    ) -> wx.GridBagSizer:
        """Build a single-row plus/minus X actuator button grid.

        :param parent: Parent window.
        :param minus_label_attr: Public -X label attribute name.
        :param plus_label_attr: Public +X label attribute name.
        :param minus_btn_attr: Public left-arrow button attribute name.
        :param plus_btn_attr: Public right-arrow button attribute name.
        :returns: Actuator button grid.
        """
        grid = wx.GridBagSizer(vgap=0, hgap=5)
        minus_label = self._axis_label(minus_label_attr, parent, "-X")
        grid.Add(
            minus_label,
            pos=(0, 0),
            flag=wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL,
            border=5,
        )
        minus_btn = self._arrow_button(minus_btn_attr, parent, "\u2190")
        grid.Add(minus_btn, pos=(0, 1))
        plus_btn = self._arrow_button(plus_btn_attr, parent, "\u2192")
        grid.Add(plus_btn, pos=(0, 3))
        plus_label = self._axis_label(plus_label_attr, parent, "+X", align_left=True)
        grid.Add(
            plus_label,
            pos=(0, 4),
            flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
            border=5,
        )
        return grid

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the multi-modality live viewport grid.

        :returns: Viewport grid hosting all alignment viewports.
        """
        grid = ViewportGrid(self)

        self.vp_align_lens = LiveViewport(grid)
        self.vp_align_center = ARLiveViewport(grid, size=(400, -1))
        self.vp_align_ek = EKLiveViewport(grid, size=(400, -1))
        self.vp_align_streak = TemporalSpectrumViewport(grid, size=(400, -1))
        self.vp_align_fiber = ChronographViewport(grid, size=(400, -1))
        self.vp_align_lens_ext = LiveViewport(grid, size=(400, -1))
        self.vp_align_light = LiveViewport(grid)
        self.vp_align_light_ar = LiveViewport(grid)

        grid.viewports = (
            self.vp_align_lens,
            self.vp_align_center,
            self.vp_align_ek,
            self.vp_align_streak,
            self.vp_align_fiber,
            self.vp_align_lens_ext,
            self.vp_align_light,
            self.vp_align_light_ar,
        )
        return grid

    def _build_settings_column(self) -> wx.Panel:
        """Build the scrollable stream, background, and documentation column.

        :returns: Right settings column.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            scroller_item = sizer.Add(
                self._build_settings_scroller(panel), proportion=1, flag=wx.EXPAND
            )
            scroller_item.SetMinSize((400, 400))

        panel.SetSizer(sizer)
        return panel

    def _build_settings_scroller(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the scrollable fold panel, background acquisition, and
        modality documentation controls.

        :param parent: Parent window.
        :returns: Settings scrolled window.
        """
        self.scr_win_right = wx.ScrolledWindow(
            parent, size=(400, -1), style=wx.VSCROLL
        )
        self.scr_win_right.SetBackgroundColour(self._theme.background)
        self.scr_win_right.EnableScrolling(False, True)
        self.scr_win_right.SetScrollbars(-1, 10, 1, 1)

        with vbox() as sizer:
            fold_bar = FoldPanelBar(self.scr_win_right)
            fold_bar.SetBackgroundColour(self._theme.background)
            sizer.Add(fold_bar, flag=wx.EXPAND)

            self.fp_settings_ebeam_blanker = self._fold_item(
                fold_bar, "ELECTRON PULSER"
            )
            self.fp_settings_ebeam_blanker.Hide()

            optical_item = self._fold_item(fold_bar, "OPTICAL")
            self.pnl_streams = StreamBar(optical_item, size=(300, -1))
            self.pnl_streams.SetForegroundColour(self._theme.text_muted)
            self.pnl_streams.SetBackgroundColour(self._theme.background)
            optical_item.add_item(self.pnl_streams)

            self.pnl_moi_settings = self._build_moi_settings_panel(
                self.scr_win_right
            )
            sizer.Add(self.pnl_moi_settings, flag=wx.EXPAND)

            self.html_moi_doc = wx.html.HtmlWindow(
                self.scr_win_right,
                size=(-1, 400),
                style=wx.html.HW_SCROLLBAR_AUTO | wx.html.HW_NO_SELECTION,
            )
            self.html_moi_doc.SetForegroundColour("#BBBBBB")
            self.html_moi_doc.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.html_moi_doc,
                proportion=1,
                flag=wx.ALL | wx.EXPAND,
                border=5,
            )

        self.scr_win_right.SetSizer(sizer)
        self.scr_win_right.FitInside()
        return self.scr_win_right

    def _build_moi_settings_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the hidden mirror/optical background acquisition panel.

        :param parent: Parent window.
        :returns: Background acquisition panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)
        panel.Hide()

        with vbox() as sizer:
            self.btn_bkg_acquire = create_text_button(
                panel,
                "Acquire background",
                height=24,
                text_colour=self._theme.button_text,
            )
            sizer.Add(
                self.btn_bkg_acquire,
                flag=wx.ALL,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _section_panel(self, parent: wx.Window) -> wx.Panel:
        """Create an actuator section panel with the standard styling.

        :param parent: Parent window.
        :returns: Styled section panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.field_background)
        return panel

    def _section_title(self, parent: wx.Window, text: str) -> wx.StaticText:
        """Create a section heading label.

        :param parent: Parent window.
        :param text: Heading text.
        :returns: Section heading label.
        """
        label = wx.StaticText(parent, label=text)
        label.SetForegroundColour(self._theme.text_primary)
        set_font(label, self._theme.font_size_section_heading)
        return label

    def _label(self, parent: wx.Window, text: str) -> wx.StaticText:
        """Create a primary-colour static label.

        :param parent: Parent window.
        :param text: Label text.
        :returns: Styled static label.
        """
        return wx.StaticText(parent, label=text)

    def _slider(
        self,
        parent: wx.Window,
        value: float = 0.000001,
        min_val: float = 0.0000001,
        max_val: float = 0.001,
        scale: str = "log",
    ) -> UnitFloatSlider:
        """Create a styled step-size slider.

        :param parent: Parent window.
        :param value: Initial slider value.
        :param min_val: Minimum slider value.
        :param max_val: Maximum slider value.
        :param scale: Slider scale, linear or log.
        :returns: Step-size slider.
        """
        slider = UnitFloatSlider(
            parent,
            value=value,
            min_val=min_val,
            max_val=max_val,
            unit="m",
            scale=scale,
            accuracy=2,
            size=(-1, 20),
            style=wx.BORDER_NONE,
        )
        return slider

    def _step_size_row(
        self,
        parent: wx.Window,
        slider_attr: str,
        label: Optional[wx.StaticText] = None,
        slider: Optional[UnitFloatSlider] = None,
    ) -> wx.Sizer:
        """Build a "Step size" label and slider row.

        :param parent: Parent window.
        :param slider_attr: Public slider attribute name.
        :param label: Existing label control, created if not given.
        :param slider: Existing slider control, created if not given.
        :returns: Step-size row.
        """
        with hbox() as sizer:
            if label is None:
                label = self._label(parent, "Step size")
            sizer.Add(label, flag=wx.RIGHT, border=5)

            if slider is None:
                slider = self._slider(parent)
            setattr(self, slider_attr, slider)
            sizer.Add(slider, proportion=1, flag=wx.EXPAND)

        return sizer

    def _combo_box(self, parent: wx.Window) -> wx.adv.OwnerDrawnComboBox:
        """Create a styled read-only combo box.

        :param parent: Parent window.
        :returns: Owner-drawn combo box.
        """
        combo = wx.adv.OwnerDrawnComboBox(
            parent,
            size=(-1, 16),
            style=(
                wx.BORDER_NONE
                | wx.CB_DROPDOWN
                | wx.CB_READONLY
                | wx.TE_PROCESS_ENTER
            ),
        )
        combo.SetForegroundColour(self._theme.text_edit)
        combo.SetBackgroundColour("#424242")
        return combo

    def _mode_button(
        self, parent: wx.Window, label: str, icon: str, icon_on: str
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

    def _axis_label(
        self,
        attribute: Optional[str],
        parent: wx.Window,
        text: str,
        align_left: bool = False,
    ) -> wx.StaticText:
        """Create an actuator axis label and optionally expose it.

        :param attribute: Public attribute name to assign the label to,
            or None to keep it local.
        :param parent: Parent window.
        :param text: Label text.
        :param align_left: Use left text alignment instead of the default.
        :returns: Axis label.
        """
        style = wx.ALIGN_LEFT if align_left else 0
        label = wx.StaticText(parent, label=text, style=style)
        label.SetForegroundColour(self._theme.text_primary)
        set_font(label, self._theme.font_size_axis_label, wx.FONTWEIGHT_BOLD)
        if attribute is not None:
            setattr(self, attribute, label)
        return label

    def _arrow_button(
        self,
        attribute: Optional[str],
        parent: wx.Window,
        label: str,
    ) -> ImageTextButton:
        """Create a large actuator movement button and optionally expose it.

        :param attribute: Public attribute name to assign the button to,
            or None to keep it local.
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
        set_font(button, self._theme.font_size_directional_button, wx.FONTWEIGHT_BOLD)
        if attribute is not None:
            setattr(self, attribute, button)
        return button

    def _fold_item(self, fold_bar: FoldPanelBar, label: str) -> FoldPanelItem:
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


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabSparc2Align)
