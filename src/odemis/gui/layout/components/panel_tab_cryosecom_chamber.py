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

from odemis.gui import img
from odemis.gui.comp.buttons import (
    ImageTextToggleButton,
)
from odemis.gui.comp.slider import UnitFloatSlider
from odemis.gui.comp.text import UnitFloatCtrl
from odemis.gui.comp.viewport import FeatureOverviewViewport
from odemis.gui.layout.constants import strings
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import (
    create_chevron_button,
    create_label,
    create_progress_button,
    create_text_button,
)


class PnlTabCryosecomChamber(wx.Panel):
    """Provide the CryoSECOM chamber tab layout."""

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the chamber controls and overview viewport.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent, style=wx.WANTS_CHARS)
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_control_column(),
                proportion=0,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            root_sizer.Add(
                self._build_overview_viewport(),
                proportion=1,
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_control_column(self) -> wx.Panel:
        """Build the left-hand chamber control column.

        :returns: Control column panel.
        """
        panel = wx.Panel(self, size=(300, -1))
        panel.SetMinSize((400, -1))
        panel.SetBackgroundColour(self._theme.background)
        panel.SetForegroundColour(self._theme.text_primary)

        with vbox() as sizer:
            sizer.Add(
                self._build_project_panel(panel),
                flag=wx.BOTTOM | wx.EXPAND,
                border=5,
            )
            sizer.Add(
                self._build_position_panel(panel),
                flag=wx.BOTTOM | wx.EXPAND,
                border=5,
            )
            self.pnl_temperature = self._build_temperature_panel(panel)
            self.pnl_temperature.Hide()
            sizer.Add(
                self.pnl_temperature,
                flag=wx.BOTTOM | wx.EXPAND,
                border=5,
            )
            sizer.AddStretchSpacer()

            self.btn_log = create_chevron_button(
                panel, "up", tooltip=strings.TOOLTIP_OPEN_LOG_PANEL
            )
            sizer.Add(self.btn_log)

        panel.SetSizer(sizer)
        return panel

    def _build_overview_viewport(self) -> FeatureOverviewViewport:
        """Build the chamber overview viewport.

        :returns: Overview viewport.
        """
        self.vp_overview_map = FeatureOverviewViewport(self)
        self.vp_overview_map.SetForegroundColour(self._theme.text_secondary)
        self.vp_overview_map.SetBackgroundColour(
            self._theme.viewport_background
        )
        return self.vp_overview_map

    def _build_project_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the project selection section.

        :param parent: Parent window.
        :returns: Project panel.
        """
        panel = self._section_panel(parent)
        grid = wx.GridBagSizer(vgap=0, hgap=50)

        title = create_label(
            panel,
            "Project",
            self._theme.text_primary,
            self._theme.font_size_section_heading,
        )
        grid.Add(
            title,
            pos=(0, 0),
            span=(1, 3),
            flag=wx.ALL | wx.EXPAND,
            border=self._theme.spacing_standard,
        )

        self.txt_projectpath = wx.TextCtrl(
            panel,
            value=strings.DEFAULT_DESTINATION_FILE,
            style=wx.BORDER_NONE | wx.TE_READONLY,
        )
        self.txt_projectpath.SetForegroundColour(self._theme.text_edit)
        self.txt_projectpath.SetBackgroundColour(self._theme.field_background)
        set_font(self.txt_projectpath, self._theme.font_size_body)
        grid.Add(
            self.txt_projectpath,
            pos=(1, 0),
            span=(1, 3),
            flag=wx.ALL | wx.EXPAND,
            border=self._theme.spacing_standard,
        )

        self.btn_change_folder = create_text_button(
            panel,
            label="New Project",
            height=24,
            text_colour=self._theme.button_text,
            face_colour="blue",
            font_size=self._theme.font_size_prominent_button,
        )
        grid.Add(
            self.btn_change_folder,
            pos=(2, 0),
            flag=wx.ALL | wx.EXPAND,
            border=self._theme.spacing_standard,
        )

        self.btn_load_project = create_text_button(
            panel,
            label="Load Project",
            height=24,
            text_colour=self._theme.button_text,
            face_colour="blue",
            font_size=self._theme.font_size_prominent_button,
        )
        grid.Add(
            self.btn_load_project,
            pos=(2, 1),
            flag=wx.ALL | wx.EXPAND,
            border=self._theme.spacing_standard,
        )

        panel.SetSizer(grid)
        return panel

    def _build_position_panel(self, parent: wx.Window) -> wx.Panel:
        """Build position, progress, warning, and advanced controls.

        :param parent: Parent window.
        :returns: Position panel.
        """
        panel = self._section_panel(parent)

        with vbox() as sizer:
            title = create_label(
                panel,
                "Position",
                self._theme.text_primary,
                self._theme.font_size_section_heading,
            )
            sizer.Add(title, flag=wx.ALL | wx.BOTTOM, border=self._theme.spacing_standard)

            sizer.Add(
                self._build_meteor_position_grid(panel),
                flag=wx.ALL | wx.ALIGN_CENTRE,
            )
            sizer.Add(
                self._build_legacy_position_grid(panel),
                flag=wx.ALL | wx.ALIGN_CENTRE,
            )
            sizer.Add(
                self._build_chamber_position_grid(panel),
                flag=wx.ALL | wx.ALIGN_CENTRE,
            )
            sizer.Add(self._build_progress_row(panel), flag=wx.ALIGN_CENTRE)

            self.pnl_ref_msg = self._build_warning_panel(panel)
            sizer.Add(
                self.pnl_ref_msg,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_switch_advanced = ImageTextToggleButton(
                panel,
                icon=img.getBitmap("icon/arr_down_s.png"),
                icon_on=img.getBitmap("icon/arr_down_s.png"),
                height=48,
                face_colour="def",
                label="Advanced",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_switch_advanced.SetForegroundColour(
                self._theme.button_text
            )
            set_font(
                self.btn_switch_advanced,
                self._theme.font_size_button,
            )
            self.btn_switch_advanced.Hide()
            sizer.Add(self.btn_switch_advanced, flag=wx.ALL, border=self._theme.spacing_standard)

            self.pnl_advanced_align = self._build_advanced_panel(panel)
            self.pnl_advanced_align.Hide()
            sizer.Add(
                self.pnl_advanced_align,
                flag=wx.BOTTOM | wx.EXPAND,
                border=5,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_meteor_position_grid(self, parent: wx.Window) -> wx.Sizer:
        """Build the METEOR posture and grid buttons.

        :param parent: Parent window.
        :returns: Position button grid.
        """
        grid = wx.GridBagSizer(vgap=5, hgap=20)
        specs = (
            ("btn_switch_sem_imaging", "SEM IMAGING", "ico_sem", (0, 0)),
            ("btn_switch_fm_imaging", "FM IMAGING", "ico_meteorimaging", (0, 1)),
            ("btn_switch_milling", "MILLING", "ico_milling", (1, 0)),
            (
                "btn_switch_fib_view_fm",
                "FIB-VIEW FM",
                "ico_meteor_fib_view_fm",
                (1, 1),
            ),
            ("btn_switch_fib_imaging", "FIB IMAGING", "ico_imaging", (2, 0)),
            ("btn_switch_grid1", "GRID 1", "ico_meteorgrid", (3, 0)),
            ("btn_switch_grid2", "GRID 2", "ico_meteorgrid", (3, 1)),
        )
        for attribute, label, icon_name, position in specs:
            button = create_progress_button(
                parent,
                label,
                f"{icon_name}.png",
                f"{icon_name}_orange.png",
                f"{icon_name}_green.png",
                text_colour=self._theme.button_text,
                font_size=self._theme.font_size_button,
            )
            button.Hide()
            setattr(self, attribute, button)
            grid.Add(
                button,
                pos=position,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
        return grid

    def _build_legacy_position_grid(self, parent: wx.Window) -> wx.Sizer:
        """Build hidden legacy posture controls retained from the XRC.

        :param parent: Parent window.
        :returns: Legacy position button grid.
        """
        grid = wx.GridBagSizer(vgap=5, hgap=20)
        specs = (
            ("LOADING", "ico_eject", (0, 0)),
            ("3 BEAMS", "ico_imaging", (0, 1)),
            ("SEM IMAGING", "ico_sem", (1, 0)),
            ("COATING", "ico_coating", (1, 1)),
        )
        for label, icon_name, position in specs:
            button = create_progress_button(
                parent,
                label,
                f"{icon_name}.png",
                f"{icon_name}_orange.png",
                f"{icon_name}_green.png",
                text_colour=self._theme.button_text,
                font_size=self._theme.font_size_button,
            )
            button.Hide()
            grid.Add(
                button,
                pos=position,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
        return grid

    def _build_chamber_position_grid(self, parent: wx.Window) -> wx.Sizer:
        """Build hidden chamber posture controls retained from the XRC.

        :param parent: Parent window.
        :returns: Chamber position button grid.
        """
        grid = wx.GridBagSizer(vgap=5, hgap=20)
        specs = (
            ("LOADING", "ico_eject", (0, 0)),
            ("OPTICAL", "ico_optical", (0, 1)),
            ("FIB", "ico_sem", (1, 0)),
            ("COATING", "ico_coating", (1, 1)),
        )
        for label, icon_name, position in specs:
            button = create_progress_button(
                parent,
                label,
                f"{icon_name}.png",
                f"{icon_name}_orange.png",
                f"{icon_name}_green.png",
                text_colour=self._theme.button_text,
                font_size=self._theme.font_size_button,
            )
            button.SetInitialSize((140, -1))
            button.Hide()
            grid.Add(
                button,
                pos=position,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
        return grid

    def _build_progress_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the movement gauge and cancel button.

        :param parent: Parent window.
        :returns: Horizontal progress controls.
        """
        with hbox() as sizer:
            self.gauge_move = wx.Gauge(
                parent,
                range=100,
                size=(150, 10),
                style=wx.GA_SMOOTH,
            )
            self.gauge_move.SetValue(0)
            sizer.Add(
                self.gauge_move,
                proportion=1,
                flag=wx.TOP | wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_cancel = create_text_button(
                parent,
                label="Cancel",
                height=24,
                text_colour=self._theme.button_text,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_cancel.Disable()
            sizer.Add(self.btn_cancel, flag=wx.LEFT, border=self._theme.spacing_standard)

        return sizer

    def _build_warning_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the movement warning message.

        :param parent: Parent window.
        :returns: Warning panel.
        """
        panel = self._section_panel(parent)
        with hbox() as sizer:
            icon = wx.StaticBitmap(
                panel,
                bitmap=img.getBitmap("icon/dialog_warning.png"),
            )
            sizer.Add(icon, flag=wx.RIGHT, border=5)

            self.txt_warning = wx.StaticText(panel, size=(-1, 20))
            self.txt_warning.SetForegroundColour(self._theme.text_primary)
            sizer.Add(self.txt_warning, proportion=1, flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_advanced_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the hidden advanced stage alignment section.

        :param parent: Parent window.
        :returns: Advanced alignment panel.
        """
        panel = self._section_panel(parent)
        with vbox() as sizer:
            title = create_label(
                panel,
                "Stage",
                self._theme.text_primary,
                self._theme.font_size_section_heading,
            )
            sizer.Add(title, flag=wx.ALL | wx.BOTTOM, border=5)
            sizer.Add(self._build_rx_row(panel), flag=wx.ALIGN_CENTRE)
            sizer.Add(
                self._build_step_size_row(panel),
                flag=wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=5,
            )
            sizer.Add(self._build_axis_grid(panel), flag=wx.ALIGN_CENTRE)

            self.btn_switch_align = create_progress_button(
                panel,
                "FACTORY ALIGNMENT",
                "ico_lens.png",
                "ico_lens_orange.png",
                "ico_lens_green.png",
                text_colour=self._theme.button_text,
                font_size=self._theme.font_size_button,
            )
            self.btn_switch_align.Hide()
            sizer.Add(self.btn_switch_align, flag=wx.ALIGN_CENTRE)

        panel.SetSizer(sizer)
        return panel

    def _build_rx_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the milling angle controls.

        :param parent: Parent window.
        :returns: Milling angle row.
        """
        with hbox() as sizer:
            label = create_label(
                parent,
                "RX Angle",
                self._theme.text_secondary,
                self._theme.font_size_body,
            )
            sizer.Add(label, flag=wx.TOP | wx.LEFT, border=25)

            self.ctrl_rx = UnitFloatCtrl(
                parent,
                value=10.0,
                size=(-1, 20),
                style=wx.BORDER_NONE,
                unit="°",
                min_val=0,
                max_val=0,
                key_step=0.1,
                accuracy=2,
            )
            set_font(self.ctrl_rx, self._theme.font_size_body)
            sizer.Add(
                self.ctrl_rx,
                flag=wx.LEFT | wx.TOP | wx.BOTTOM,
                border=25,
            )
        return sizer

    def _build_step_size_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the stage alignment step-size slider.

        :param parent: Parent window.
        :returns: Step-size row.
        """
        with hbox() as sizer:
            label = create_label(
                parent,
                "Step size",
                self._theme.text_primary,
                self._theme.font_size_body,
            )
            sizer.Add(label, flag=wx.RIGHT, border=5)

            self.stage_align_slider_aligner = UnitFloatSlider(
                parent,
                value=0.000001,
                min_val=0.0000001,
                max_val=0.001,
                unit="m",
                scale="log",
                accuracy=2,
                style=wx.BORDER_NONE,
            )
            self.stage_align_slider_aligner.SetForegroundColour(
                self._theme.text_primary
            )
            sizer.Add(
                self.stage_align_slider_aligner,
                proportion=1,
                flag=wx.EXPAND,
            )
        return sizer

    def _build_axis_grid(self, parent: wx.Window) -> wx.Sizer:
        """Build the relative X, Y, and Z movement controls.

        :param parent: Parent window.
        :returns: Axis movement grid.
        """
        grid = wx.GridBagSizer(vgap=0, hgap=5)
        label_specs = (
            ("+Y", (0, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5),
            ("-Y", (4, 2), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5),
            ("+X", (2, 4), wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, 5),
            ("-X", (2, 0), wx.RIGHT | wx.ALIGN_RIGHT | wx.ALIGN_CENTRE_VERTICAL, 5),
            ("+Z", (0, 5), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5),
            ("-Z", (4, 5), wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE, 5),
        )
        for text, position, flag, border in label_specs:
            label = create_label(
                parent,
                text,
                self._theme.text_primary,
                self._theme.font_size_axis_label,
                wx.FONTWEIGHT_BOLD,
            )
            grid.Add(label, pos=position, flag=flag, border=border)

        button_specs = (
            ("stage_align_btn_p_aligner_y", "↑", (1, 2), wx.LEFT | wx.RIGHT, 7),
            ("stage_align_btn_m_aligner_y", "↓", (3, 2), wx.LEFT | wx.RIGHT, 7),
            ("stage_align_btn_m_aligner_x", "←", (2, 1), 0, 0),
            ("stage_align_btn_p_aligner_x", "→", (2, 3), 0, 0),
            ("stage_align_btn_p_aligner_z", "↑", (1, 5), wx.LEFT | wx.RIGHT, 7),
            ("stage_align_btn_m_aligner_z", "↓", (3, 5), wx.LEFT | wx.RIGHT, 7),
        )
        for attribute, text, position, flag, border in button_specs:
            button = create_text_button(
                parent,
                label=text,
                height=48,
                text_colour=self._theme.button_text,
                face_colour="def",
                font_size=self._theme.font_size_directional_button,
                font_weight=wx.FONTWEIGHT_BOLD,
                size=(64, -1),
                style=wx.ALIGN_CENTRE,
            )
            setattr(self, attribute, button)
            grid.Add(button, pos=position, flag=flag, border=border)

        return grid

    def _build_temperature_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the optional sample thermostat section.

        :param parent: Parent window.
        :returns: Temperature panel.
        """
        panel = self._section_panel(parent)
        with vbox() as sizer:
            title = create_label(
                panel,
                "Temperature",
                self._theme.text_primary,
                self._theme.font_size_section_heading,
            )
            sizer.Add(title, flag=wx.ALL | wx.BOTTOM, border=5)

            with hbox() as heater_sizer:
                label = create_label(
                    panel,
                    "Sample heater",
                    self._theme.text_secondary,
                    self._theme.font_size_body,
                )
                heater_sizer.Add(
                    label,
                    flag=wx.TOP | wx.LEFT | wx.RIGHT,
                    border=5,
                )
                self.ctrl_sample_heater = wx.CheckBox(panel)
                self.ctrl_sample_heater.SetForegroundColour(
                    self._theme.text_primary
                )
                heater_sizer.Add(
                    self.ctrl_sample_heater,
                    flag=wx.TOP | wx.LEFT,
                    border=5,
                )
            sizer.Add(heater_sizer)

            with hbox() as target_sizer:
                label = create_label(
                    panel,
                    "Target temperature",
                    self._theme.text_secondary,
                    self._theme.font_size_body,
                )
                target_sizer.Add(
                    label,
                    flag=wx.TOP | wx.LEFT,
                    border=5,
                )
                self.ctrl_sample_target_tmp = UnitFloatCtrl(
                    panel,
                    value=-100.0,
                    size=(-1, 20),
                    style=wx.BORDER_NONE,
                    unit="°C",
                    min_val=0,
                    max_val=0,
                    key_step=0.1,
                    accuracy=2,
                )
                set_font(
                    self.ctrl_sample_target_tmp,
                    self._theme.font_size_body,
                )
                target_sizer.Add(
                    self.ctrl_sample_target_tmp,
                    flag=wx.LEFT | wx.TOP | wx.BOTTOM,
                    border=5,
                )
            sizer.Add(target_sizer)

        panel.SetSizer(sizer)
        return panel

    def _section_panel(self, parent: wx.Window) -> wx.Panel:
        """Create a panel using the standard section colours.

        :param parent: Parent window.
        :returns: Styled section panel.
        """
        panel = wx.Panel(parent)
        panel.SetForegroundColour(self._theme.text_primary)
        panel.SetBackgroundColour(self._theme.panel_background)
        return panel

if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabCryosecomChamber)
