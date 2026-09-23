# -*- coding: utf-8 -*-
"""
@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

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

import odemis.gui.layout as layout
from odemis.gui import img
from odemis.gui.comp.buttons import (
    ImageButton,
    ViewButton,
)
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.text import UnitFloatCtrl
from odemis.gui.comp.viewport import FeatureOverviewViewport, LiveViewport
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants.strings import (
    DEFAULT_DESTINATION_FILE,
    LABEL_ACQUIRE_OVERVIEW,
    LABEL_CANCEL,
    LABEL_ESTIMATED_TIME,
    LABEL_FILENAME,
    LABEL_MILL,
    LABEL_MILLING,
    LABEL_OPTICAL_SETTINGS,
    LABEL_SEM_IMAGING,
    LABEL_STATUS,
    LABEL_STREAMS,
    LABEL_TRENCHING,
    TOOLTIP_OPEN_LOG_PANEL,
    LABEL_CHANGE,
    LABEL_ACQUIRE,
    LABEL_ACQUIRED,
    LABEL_PATTERNS,
    LABEL_CREATE_MOVE,
    LABEL_GO_TO_FEATURE,
    LABEL_ACQUISITIONS,
)
from odemis.gui.layout.constants.themes import Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox
from odemis.gui.layout.util.widgets import (
    create_combo,
    create_icon_button,
    create_label,
    create_progress_button,
    create_text_button,
)


class PnlTabFibsem(wx.Panel):
    """Provide the FIBSEM imaging, acquisition, and milling tab layout."""

    def __init__(self, parent: wx.Window, theme: Optional[Theme] = None) -> None:
        """Create the FIBSEM controls and viewports.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
        theme = layout.theme if theme is None else theme
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_view_controls(),
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_viewport_grid(),
                proportion=1,
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_settings_column(),
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_view_controls(self) -> wx.Panel:
        """Build the toolbar and viewport selector column.

        :returns: Left control column.
        """
        panel = wx.Panel(self, size=(200, -1))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as outer_sizer:
            with vbox() as selector_sizer:
                selector_sizer.AddStretchSpacer()
                self.secom_toolbar = ToolBar(panel, style=wx.VERTICAL)
                selector_sizer.Add(
                    self.secom_toolbar,
                    flag=wx.ALIGN_RIGHT,
                )
                selector_sizer.AddStretchSpacer()

                selectors = (
                    ("lbl_secom_view_all", "btn_secom_view_all", False),
                    ("lbl_secom_view_tl", "btn_secom_view_tl", True),
                    ("lbl_secom_view_tr", "btn_secom_view_tr", True),
                    ("lbl_secom_view_bl", "btn_secom_view_bl", True),
                    ("lbl_secom_view_br", "btn_secom_view_br", True),
                )
                for label_name, button_name, top_border in selectors:
                    with vbox() as label_sizer:
                        label = wx.StaticText(panel, label="view")
                        label.SetForegroundColour(self._theme.text_secondary)
                        setattr(self, label_name, label)
                        label_sizer.Add(
                            label,
                            flag=wx.BOTTOM | (wx.TOP if top_border else 0),
                            border=2,
                        )
                    selector_sizer.Add(
                        label_sizer,
                        flag=wx.RIGHT | wx.ALIGN_RIGHT,
                        border=18,
                    )

                    button = ViewButton(panel, face_colour="def")
                    setattr(self, button_name, button)
                    selector_sizer.Add(
                        button,
                        flag=(
                            wx.ALIGN_RIGHT
                            | (wx.BOTTOM if button_name != "btn_secom_view_br" else 0)
                        ),
                        border=6,
                    )

            outer_sizer.Add(
                selector_sizer,
                proportion=1,
                flag=wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_log = ImageButton(
                panel,
                icon=img.getBitmap("icon/ico_chevron_up.png"),
                height=16,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_log.SetToolTip(TOOLTIP_OPEN_LOG_PANEL)
            outer_sizer.Add(
                self.btn_log,
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(outer_sizer)
        return panel

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the four FIBSEM viewports.

        :returns: Viewport grid.
        """
        self.pnl_secom_grid = ViewportGrid(self)
        self.vp_secom_tl = LiveViewport(self.pnl_secom_grid)
        self.vp_secom_tr = LiveViewport(self.pnl_secom_grid)
        self.vp_secom_bl = FeatureOverviewViewport(self.pnl_secom_grid)
        self.vp_secom_br = LiveViewport(self.pnl_secom_grid)

        viewports = (
            self.vp_secom_tl,
            self.vp_secom_tr,
            self.vp_secom_bl,
            self.vp_secom_br,
        )
        for viewport in viewports:
            viewport.SetForegroundColour(self._theme.text_secondary)
            viewport.SetBackgroundColour(self._theme.viewport_background)

        # Controllers access the viewport tuple before the first size event.
        self.pnl_secom_grid.viewports = viewports
        return self.pnl_secom_grid

    def _build_settings_column(self) -> wx.Panel:
        """Build the scrollable FIBSEM settings column.

        :returns: Right settings column.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.scr_win_right = wx.ScrolledWindow(
                panel,
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

                self._build_feature_section(fold_bar)
                self._build_stage_position_section(fold_bar)
                self._build_optical_settings_section(fold_bar)
                self._build_streams_section(fold_bar)
                self._build_acquisition_section(fold_bar)
                self._build_acquired_section(fold_bar)
                self._build_automation_section(fold_bar)
                self._build_milling_section(fold_bar)

            self.scr_win_right.SetSizer(scroll_sizer)
            self.scr_win_right.FitInside()
            sizer.Add(self.scr_win_right, proportion=1, flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_feature_section(self, fold_bar: FoldPanelBar) -> None:
        """Build feature selection and positioning controls.

        :param fold_bar: Parent fold-panel bar.
        """
        feature_item = self._fold_item(fold_bar, "FEATURES")
        panel = wx.Panel(feature_item)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_feature_selection_row(panel),
                flag=wx.LEFT | wx.TOP,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_feature_status_row(panel),
                flag=wx.LEFT | wx.TOP,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_save_position_row(panel),
                flag=wx.LEFT | wx.TOP | wx.BOTTOM,
                border=5,
            )

        panel.SetSizer(sizer)
        feature_item.add_item(panel)

    def _build_feature_selection_row(self, parent: wx.Window) -> wx.Sizer:
        """Build feature selection controls.

        :param parent: Parent window.
        :returns: Feature selection row.
        """
        with hbox() as sizer:
            self.btn_delete_feature = create_icon_button(
                parent,
                "ico_trash.png",
                text_colour=self._theme.button_text_contrast,
            )
            sizer.Add(self.btn_delete_feature)

            self.cmb_features = create_combo(
                parent,
                (156, 20),
                readonly=False,
                text_colour=self._theme.text_edit,
                background_colour=self._theme.background,
            )
            sizer.Add(self.cmb_features)

            button_panel = wx.Panel(parent, size=(120, 24))
            button_panel.SetBackgroundColour(self._theme.background)
            with hbox() as button_sizer:
                self.btn_create_move_feature = create_text_button(
                    button_panel,
                    LABEL_CREATE_MOVE,
                    height=24,
                    text_colour=self._theme.button_text,
                    contrast_text_colour=self._theme.button_text_contrast,
                )
                button_sizer.Add(
                    self.btn_create_move_feature,
                    proportion=1,
                    flag=wx.ALIGN_CENTER,
                )
            button_panel.SetSizer(button_sizer)
            sizer.Add(
                button_panel,
                flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
                border=82,
            )
        return sizer

    def _build_feature_status_row(self, parent: wx.Window) -> wx.Sizer:
        """Build feature status and navigation controls.

        :param parent: Parent window.
        :returns: Feature status row.
        """
        with hbox() as sizer:
            sizer.Add(create_label(parent, LABEL_STATUS, self._theme.text_primary))
            self.cmb_feature_status = create_combo(
                parent,
                (133, 16),
                readonly=True,
                text_colour=self._theme.text_edit,
                background_colour=self._theme.background,
            )
            sizer.Add(
                self.cmb_feature_status,
                flag=wx.LEFT,
                border=self._theme.spacing_standard,
            )

            button_panel = wx.Panel(parent, size=(120, 24))
            button_panel.SetBackgroundColour(self._theme.background)
            with hbox() as button_sizer:
                self.btn_go_to_feature = create_text_button(
                    button_panel,
                    LABEL_GO_TO_FEATURE,
                    height=24,
                    text_colour=self._theme.button_text,
                    contrast_text_colour=self._theme.button_text_contrast,
                )
                button_sizer.Add(
                    self.btn_go_to_feature,
                    proportion=1,
                    flag=wx.ALIGN_CENTER,
                )
            button_panel.SetSizer(button_sizer)
            sizer.Add(
                button_panel,
                flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
                border=82,
            )
        return sizer

    def _build_save_position_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the feature milling-position action.

        :param parent: Parent window.
        :returns: Save-position row.
        """
        with hbox() as sizer:
            self.btn_feature_save_position = create_text_button(
                parent,
                "SAVE POSITION",
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_save.png",
                font_size=self._theme.font_size_body,
                size=(180, 48),
                contrast=True,
            )
            sizer.Add(
                self.btn_feature_save_position,
                flag=wx.ALL | wx.EXPAND,
                border=5,
            )
        return sizer

    def _build_stage_position_section(self, fold_bar: FoldPanelBar) -> None:
        """Build posture switching and milling-angle controls.

        :param fold_bar: Parent fold-panel bar.
        """
        item = self._fold_item(fold_bar, "STAGE POSITION")
        panel = wx.Panel(item)
        panel.SetForegroundColour(self._theme.text_muted)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_posture_buttons(panel),
                proportion=1,
                flag=wx.EXPAND,
            )
            sizer.Add(
                self._build_milling_angle_row(panel),
                flag=wx.ALIGN_CENTRE,
            )
            sizer.Add(
                self._build_trenching_angle_row(panel),
                flag=wx.ALIGN_CENTRE,
            )

        panel.SetSizer(sizer)
        item.add_item(panel)

    def _build_posture_buttons(self, parent: wx.Window) -> wx.Sizer:
        """Build SEM-imaging, milling and trenching posture buttons.

        :param parent: Parent window.
        :returns: Posture button grid.
        """
        grid = wx.GridBagSizer(vgap=0, hgap=0)
        specs = (
            (
                "btn_switch_sem_imaging",
                LABEL_SEM_IMAGING,
                "ico_sem",
                (0, 0),
            ),
            (
                "btn_switch_milling",
                LABEL_MILLING,
                "ico_milling",
                (0, 1),
            ),
            (
                "btn_switch_trenching",
                LABEL_TRENCHING,
                "ico_trenching",
                (1, 0),
            ),
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
            setattr(self, attribute, button)
            grid.Add(
                button,
                pos=position,
                flag=wx.EXPAND | wx.ALL,
                border=self._theme.spacing_standard,
            )
        grid.AddGrowableCol(0)
        grid.AddGrowableCol(1)
        return grid

    def _build_milling_angle_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the milling-angle editor.

        :param parent: Parent window.
        :returns: Milling-angle row.
        """
        with hbox() as sizer:
            label = wx.StaticText(parent, label="Milling angle")
            label.SetForegroundColour(self._theme.text_secondary)
            set_font(label, self._theme.font_size_checklist)
            sizer.Add(
                label,
                flag=wx.LEFT | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )

            self.ctrl_milling_angle = UnitFloatCtrl(
                parent,
                value=10.0,
                size=(-1, 20),
                style=wx.BORDER_NONE,
                unit="°",
                min_val=0.0,
                max_val=0.0,
                key_step=0.1,
                accuracy=2,
            )
            self.ctrl_milling_angle.SetForegroundColour(
                self._theme.text_edit
            )
            self.ctrl_milling_angle.SetBackgroundColour(
                self._theme.background
            )
            set_font(
                self.ctrl_milling_angle,
                self._theme.font_size_checklist,
            )
            sizer.Add(
                self.ctrl_milling_angle,
                flag=wx.LEFT | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )
        return sizer

    def _build_trenching_angle_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the trenching-angle read-out.

        :param parent: Parent window.
        :returns: Trenching-angle row.
        """
        with hbox() as sizer:
            self.lbl_trenching_angle = wx.StaticText(
                parent, label="Trenching angle"
            )
            self.lbl_trenching_angle.SetForegroundColour(
                self._theme.text_secondary
            )
            set_font(
                self.lbl_trenching_angle,
                self._theme.font_size_checklist,
            )
            sizer.Add(
                self.lbl_trenching_angle,
                flag=wx.LEFT | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )

            self.ctrl_trenching_angle = UnitFloatCtrl(
                parent,
                value=10.0,
                size=(-1, 20),
                style=wx.BORDER_NONE,
                unit="°",
                min_val=0.0,
                max_val=0.0,
                key_step=0.1,
                accuracy=2,
            )
            self.ctrl_trenching_angle.SetForegroundColour(
                self._theme.text_muted
            )
            self.ctrl_trenching_angle.SetBackgroundColour(
                self._theme.background
            )
            set_font(
                self.ctrl_trenching_angle,
                self._theme.font_size_checklist,
            )
            sizer.Add(
                self.ctrl_trenching_angle,
                flag=wx.LEFT | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )
        return sizer

    def _build_optical_settings_section(
        self,
        fold_bar: FoldPanelBar,
    ) -> None:
        """Build the dynamic optical-settings fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_settings_secom_optical = self._fold_item(
            fold_bar,
            LABEL_OPTICAL_SETTINGS,
        )

    def _build_streams_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the live-streams fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_secom_streams = self._fold_item(fold_bar, LABEL_STREAMS)
        self.pnl_secom_streams = StreamBar(
            self.fp_secom_streams,
            size=(300, -1),
            add_button=False,
        )
        self.pnl_secom_streams.SetForegroundColour(self._theme.text_muted)
        self.pnl_secom_streams.SetBackgroundColour(self._theme.background)
        self.fp_secom_streams.add_item(self.pnl_secom_streams)

    def _build_acquisition_section(self, fold_bar: FoldPanelBar) -> None:
        """Build acquisition controls.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_acquisitions = self._fold_item(fold_bar, LABEL_ACQUISITIONS)
        panel = wx.Panel(
            self.fp_acquisitions,
            size=(400, -1),
            style=wx.BORDER_NONE,
        )
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            self.streams_chk_list = wx.CheckListBox(panel)
            set_font(
                self.streams_chk_list,
                self._theme.font_size_checklist,
            )
            sizer.Add(
                self.streams_chk_list,
                proportion=1,
                flag=wx.RIGHT | wx.LEFT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.chkbox_save_acquisition = wx.CheckBox(
                panel,
                label="Auto save acquisition",
            )
            self.chkbox_save_acquisition.SetForegroundColour(
                self._theme.text_primary
            )
            self.chkbox_save_acquisition.SetValue(False)
            sizer.Add(
                self.chkbox_save_acquisition,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_filename_row(panel),
                flag=wx.TOP | wx.LEFT | wx.RIGHT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_acquire_row(panel),
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_acquire_all = create_text_button(
                panel,
                "ACQUIRE BOTH",
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_acqui.png",
                font_size=self._theme.font_size_primary_action,
                contrast=True,
            )
            sizer.Add(
                self.btn_acquire_all,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_acquire_overview = create_text_button(
                panel,
                LABEL_ACQUIRE_OVERVIEW,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                font_size=self._theme.font_size_prominent_button,
            )
            sizer.Add(
                self.btn_acquire_overview,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_correlation_row(panel),
                flag=wx.TOP | wx.BOTTOM | wx.LEFT,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        self.fp_acquisitions.add_item(panel)

    def _build_filename_row(self, parent: wx.Window) -> wx.Sizer:
        """Build acquisition filename controls.

        :param parent: Parent window.
        :returns: Filename row.
        """
        with hbox() as sizer:
            sizer.Add(
                create_label(parent, LABEL_FILENAME, self._theme.text_primary),
                flag=wx.ALIGN_CENTER_VERTICAL,
            )
            self.txt_filename = wx.TextCtrl(
                parent,
                value=DEFAULT_DESTINATION_FILE,
                size=(-1, 20),
                style=wx.BORDER_NONE | wx.TE_READONLY,
            )
            self.txt_filename.SetForegroundColour(self._theme.text_edit)
            self.txt_filename.SetBackgroundColour(self._theme.background)
            sizer.Add(
                self.txt_filename,
                proportion=1,
                flag=wx.LEFT | wx.EXPAND,
                border=5,
            )
            self.btn_cryosecom_change_file = create_text_button(
                parent,
                LABEL_CHANGE,
                height=24,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
            )
            sizer.Add(
                self.btn_cryosecom_change_file,
                flag=wx.LEFT,
                border=5,
            )
        return sizer

    def _build_acquire_row(self, parent: wx.Window) -> wx.Sizer:
        """Build acquisition action, estimate, and progress controls.

        :param parent: Parent window.
        :returns: Acquisition row.
        """
        grid = wx.FlexGridSizer(rows=1, cols=3, vgap=0, hgap=5)

        button_panel = wx.Panel(parent, size=(200, 48))
        button_panel.SetBackgroundColour(self._theme.background)
        with hbox() as button_sizer:
            self.btn_cryosecom_acquire = create_text_button(
                button_panel,
                LABEL_ACQUIRE,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_acqui.png",
                font_size=self._theme.font_size_primary_action,
                contrast=True,
            )
            button_sizer.Add(
                self.btn_cryosecom_acquire,
                proportion=1,
                flag=wx.ALIGN_CENTER,
            )
        button_panel.SetSizer(button_sizer)
        grid.Add(
            button_panel,
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=2,
        )

        self.txt_cryosecom_est_time = create_label(
            parent,
            LABEL_ESTIMATED_TIME,
            self._theme.text_primary,
        )
        self.txt_cryosecom_est_time.Hide()
        grid.Add(self.txt_cryosecom_est_time, flag=wx.TOP, border=17)

        with hbox() as progress_sizer:
            with vbox() as gauge_sizer:
                self.gauge_cryosecom_acq = wx.Gauge(
                    parent,
                    range=100,
                    size=(-1, 10),
                    style=wx.GA_SMOOTH,
                )
                gauge_sizer.Add(
                    self.gauge_cryosecom_acq,
                    proportion=1,
                    flag=wx.TOP,
                    border=self._theme.spacing_standard,
                )
                self.txt_cryosecom_left_time = create_label(
                    parent, "", self._theme.text_primary
                )
                self.txt_cryosecom_left_time.Hide()
                gauge_sizer.Add(
                    self.txt_cryosecom_left_time,
                    proportion=1,
                    flag=wx.TOP,
                    border=self._theme.spacing_standard,
                )
            progress_sizer.Add(gauge_sizer, flag=wx.TOP, border=-8)

            self.btn_cryosecom_acqui_cancel = create_text_button(
                parent,
                LABEL_CANCEL,
                height=24,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
            )
            progress_sizer.Add(
                self.btn_cryosecom_acqui_cancel,
                flag=wx.TOP | wx.LEFT,
                border=12,
            )
        grid.Add(progress_sizer, flag=wx.EXPAND)
        grid.AddGrowableCol(1)
        return grid

    def _build_correlation_row(self, parent: wx.Window) -> wx.Sizer:
        """Build the FIB/FM correlation action.

        :param parent: Parent window.
        :returns: Correlation action row.
        """
        with hbox() as sizer:
            self.btn_tdct = create_text_button(
                parent,
                "Correlate FIB/FM",
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                font_size=self._theme.font_size_prominent_button,
            )
            sizer.Add(self.btn_tdct)
        return sizer

    def _build_acquired_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the acquired-streams section.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_acquired = self._fold_item(fold_bar, LABEL_ACQUIRED)
        self.pnl_cryosecom_acquired = StreamBar(
            self.fp_acquired,
            size=(300, -1),
            add_button=False,
        )
        self.pnl_cryosecom_acquired.SetForegroundColour(
            self._theme.text_muted
        )
        self.pnl_cryosecom_acquired.SetBackgroundColour(
            self._theme.background
        )
        self.fp_acquired.add_item(self.pnl_cryosecom_acquired)

    def _build_automation_section(self, fold_bar: FoldPanelBar) -> None:
        """Build automated milling controls.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_automation = self._fold_item(fold_bar, LABEL_MILLING)
        panel = wx.Panel(self.fp_automation)
        panel.SetForegroundColour(self._theme.button_text)
        panel.SetBackgroundColour(self._theme.section_header)

        with vbox() as sizer:
            self.workflow_features_chk_list = wx.CheckListBox(panel)
            set_font(
                self.workflow_features_chk_list,
                self._theme.font_size_checklist,
            )
            sizer.Add(
                self.workflow_features_chk_list,
                proportion=1,
                flag=wx.RIGHT | wx.LEFT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.workflow_task_chk_list = wx.CheckListBox(panel)
            set_font(
                self.workflow_task_chk_list,
                self._theme.font_size_checklist,
            )
            sizer.Add(
                self.workflow_task_chk_list,
                proportion=1,
                flag=wx.RIGHT | wx.LEFT | wx.TOP | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_run_automated_milling = create_text_button(
                panel,
                LABEL_MILL,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_milling.png",
                font_size=self._theme.font_size_primary_action,
                contrast=True,
            )
            sizer.Add(
                self.btn_run_automated_milling,
                flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.txt_automated_milling_est_time = create_label(
                panel,
                LABEL_ESTIMATED_TIME,
                self._theme.text_primary,
            )
            sizer.Add(
                self.txt_automated_milling_est_time,
                flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_automated_milling_progress_row(panel),
                flag=wx.EXPAND,
            )

            self.txt_automated_milling_status = create_label(
                panel, "", self._theme.text_primary
            )
            sizer.Add(
                self.txt_automated_milling_status,
                flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        self.fp_automation.add_item(panel)

    def _build_automated_milling_progress_row(
        self,
        parent: wx.Window,
    ) -> wx.Sizer:
        """Build automated milling progress and cancellation controls.

        :param parent: Parent window.
        :returns: Automated milling progress row.
        """
        with hbox() as sizer:
            gauge_panel = wx.Panel(parent, size=(200, 8))
            gauge_panel.SetBackgroundColour(self._theme.section_header)
            with vbox() as gauge_sizer:
                gauge_sizer.AddStretchSpacer()
                self.gauge_automated_milling = wx.Gauge(
                    gauge_panel,
                    range=100,
                    size=(200, 8),
                    style=wx.GA_SMOOTH,
                )
                gauge_sizer.Add(self.gauge_automated_milling)
                gauge_sizer.AddStretchSpacer()
            gauge_panel.SetSizer(gauge_sizer)
            sizer.Add(
                gauge_panel,
                flag=wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_VERTICAL,
                border=self._theme.spacing_standard,
            )

            time_panel = wx.Panel(parent, size=(80, 24))
            time_panel.SetBackgroundColour(self._theme.section_header)
            with hbox() as time_sizer:
                self.txt_automated_milling_left_time = create_label(
                    time_panel,
                    "",
                    self._theme.text_primary,
                )
                time_sizer.Add(
                    self.txt_automated_milling_left_time,
                    proportion=1,
                    flag=wx.ALIGN_CENTER,
                )
            time_panel.SetSizer(time_sizer)
            sizer.Add(
                time_panel,
                flag=(
                    wx.LEFT
                    | wx.RIGHT
                    | wx.TOP
                    | wx.ALIGN_CENTER_VERTICAL
                ),
                border=self._theme.spacing_standard,
            )

            self.btn_automated_milling_cancel = create_text_button(
                parent,
                LABEL_CANCEL,
                height=24,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
            )
            sizer.Add(
                self.btn_automated_milling_cancel,
                flag=(
                    wx.LEFT
                    | wx.RIGHT
                    | wx.TOP
                    | wx.ALIGN_CENTER_VERTICAL
                ),
                border=self._theme.spacing_standard,
            )
        return sizer

    def _build_milling_section(self, fold_bar: FoldPanelBar) -> None:
        """Build milling task, settings, and pattern controls.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_milling = self._fold_item(fold_bar, LABEL_PATTERNS)

        controls_panel = wx.Panel(self.fp_milling)
        controls_panel.SetForegroundColour(self._theme.button_text)
        controls_panel.SetBackgroundColour(self._theme.section_header)
        with vbox() as controls_sizer:
            self.milling_task_chk_list = wx.CheckListBox(controls_panel)
            set_font(
                self.milling_task_chk_list,
                self._theme.font_size_checklist,
            )
            controls_sizer.Add(
                self.milling_task_chk_list,
                proportion=1,
                flag=wx.RIGHT | wx.LEFT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            controls_sizer.Add((0, self._theme.spacing_standard))

            with hbox() as movement_sizer:
                self.chk_move_all_patterns = wx.CheckBox(
                    controls_panel,
                    label="Move all patterns",
                )
                self.chk_move_all_patterns.SetForegroundColour(self._theme.text_primary)
                self.chk_move_all_patterns.SetBackgroundColour(self._theme.section_header)
                self.chk_move_all_patterns.SetToolTip(
                    "Apply Ctrl+Shift+click and Ctrl+arrow movement to every pattern."
                )
                movement_sizer.Add(
                    self.chk_move_all_patterns,
                    proportion=1,
                    flag=wx.ALIGN_CENTER_VERTICAL,
                )

                self.btn_snap_patterns_to_feature = create_text_button(
                    controls_panel,
                    "Snap to Feature",
                    height=24,
                    text_colour=self._theme.button_text,
                    contrast_text_colour=self._theme.button_text_contrast,
                )
                self.btn_snap_patterns_to_feature.SetToolTip(
                    "Move every milling pattern back to the feature marker."
                )
                movement_sizer.Add(
                    self.btn_snap_patterns_to_feature,
                    flag=wx.ALIGN_CENTER_VERTICAL,
                )
            controls_sizer.Add(
                movement_sizer,
                flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_run_milling = create_text_button(
                controls_panel,
                LABEL_MILL,
                height=48,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
                face_colour="blue",
                icon="ico_milling.png",
                font_size=self._theme.font_size_primary_action,
                contrast=True,
            )
            self.btn_run_milling.Hide()
            controls_sizer.Add(
                self.btn_run_milling,
                flag=wx.ALL | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.txt_milling_est_time = create_label(
                controls_panel,
                LABEL_ESTIMATED_TIME,
                self._theme.text_primary,
            )
            self.txt_milling_est_time.Hide()
            controls_sizer.Add(
                self.txt_milling_est_time,
                flag=wx.TOP | wx.LEFT,
                border=17,
            )
            controls_sizer.Add(
                self._build_milling_progress_row(controls_panel),
                flag=wx.EXPAND,
            )

            self.btn_milling_cancel = create_text_button(
                controls_panel,
                LABEL_CANCEL,
                height=24,
                text_colour=self._theme.button_text,
                contrast_text_colour=self._theme.button_text_contrast,
            )
            self.btn_milling_cancel.Hide()
            controls_sizer.Add(
                self.btn_milling_cancel,
                flag=wx.TOP,
                border=12,
            )

        controls_panel.SetSizer(controls_sizer)
        self.fp_milling.add_item(controls_panel)

        settings_panel = wx.Panel(self.fp_milling)
        settings_panel.SetForegroundColour(self._theme.button_text)
        settings_panel.SetBackgroundColour(self._theme.section_header)
        self.fp_milling.add_item(settings_panel)

        self.pnl_patterns = wx.Panel(self.fp_milling)
        self.pnl_patterns.SetForegroundColour(self._theme.button_text)
        self.pnl_patterns.SetBackgroundColour(self._theme.section_header)
        self.fp_milling.add_item(self.pnl_patterns)

    def _build_milling_progress_row(self, parent: wx.Window) -> wx.Sizer:
        """Build milling-series progress controls.

        :param parent: Parent window.
        :returns: Milling-series progress row.
        """
        with hbox() as sizer:
            with vbox() as gauge_sizer:
                self.gauge_milling_series = wx.Gauge(
                    parent,
                    range=100,
                    size=(-1, 10),
                    style=wx.GA_SMOOTH,
                )
                self.gauge_milling_series.Hide()
                gauge_sizer.Add(
                    self.gauge_milling_series,
                    proportion=1,
                    flag=wx.TOP,
                    border=self._theme.spacing_standard,
                )

                self.txt_milling_series_left_time = create_label(
                    parent, "", self._theme.text_primary
                )
                self.txt_milling_series_left_time.Hide()
                gauge_sizer.Add(
                    self.txt_milling_series_left_time,
                    proportion=1,
                    flag=wx.TOP,
                    border=self._theme.spacing_standard,
                )
            sizer.Add(gauge_sizer, flag=wx.TOP, border=-8)
        return sizer

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


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabFibsem)
