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
from typing import Any, Optional

from odemis.gui import img
from odemis.gui.comp.buttons import (
    ImageButton,
    ImageTextButton,
    ImageToggleButton,
    ViewButton,
)
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.text import UnitFloatCtrl
from odemis.gui.comp.viewport import (
    FeatureOverviewViewport,
    LiveViewport,
    MicroscopeViewport,
)
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants import strings
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class PnlTabLocalization(wx.Panel):
    """Provide the CryoSECOM localization tab layout."""

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the localization controls and viewports.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
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
                    label = wx.StaticText(panel, label="view")
                    label.SetForegroundColour(self._theme.text_secondary)
                    setattr(self, label_name, label)
                    selector_sizer.Add(
                        label,
                        flag=(
                            wx.RIGHT
                            | wx.ALIGN_RIGHT
                            | wx.BOTTOM
                            | (wx.TOP if top_border else 0)
                        ),
                        border=2 if top_border else 18,
                    )

                    button = ViewButton(panel, face_colour="def")
                    setattr(self, button_name, button)
                    selector_sizer.Add(
                        button,
                        flag=wx.BOTTOM | wx.ALIGN_RIGHT,
                        border=6 if button_name != "btn_secom_view_br" else 0,
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
            self.btn_log.SetToolTip(strings.TOOLTIP_OPEN_LOG_PANEL)
            outer_sizer.Add(
                self.btn_log,
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(outer_sizer)
        return panel

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the four localization viewports.

        :returns: Viewport grid.
        """
        self.pnl_secom_grid = ViewportGrid(self)
        self.vp_secom_tl = FeatureOverviewViewport(self.pnl_secom_grid)
        self.vp_secom_tr = MicroscopeViewport(self.pnl_secom_grid)
        self.vp_secom_bl = LiveViewport(self.pnl_secom_grid)
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
        """Build the scrollable fold-panel settings column.

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
                self.pnl_current_posture = self._build_posture_panel(
                    self.scr_win_right
                )
                scroll_sizer.Add(
                    self.pnl_current_posture,
                    flag=wx.ALL,
                    border=self._theme.spacing_standard,
                )

                fold_bar = FoldPanelBar(self.scr_win_right)
                fold_bar.SetBackgroundColour(self._theme.background)
                scroll_sizer.Add(fold_bar, flag=wx.EXPAND)

                self._build_feature_section(fold_bar)
                self._build_optical_settings_section(fold_bar)
                self._build_streams_section(fold_bar)
                self._build_acquisition_section(fold_bar)
                self._build_automation_section(fold_bar)
                self._build_acquired_section(fold_bar)

            self.scr_win_right.SetSizer(scroll_sizer)
            self.scr_win_right.FitInside()
            sizer.Add(self.scr_win_right, proportion=1, flag=wx.EXPAND)

        panel.SetSizer(sizer)
        return panel

    def _build_posture_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the current posture indicator.

        :param parent: Parent window.
        :returns: Posture panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.background)
        with hbox() as sizer:
            label = wx.StaticText(panel, label="Current posture")
            label.SetForegroundColour(self._theme.text_primary)
            sizer.Add(
                label,
                flag=wx.RIGHT | wx.ALIGN_CENTER_VERTICAL,
                border=self._theme.spacing_standard,
            )

            self.bmp_current_posture = wx.StaticBitmap(
                panel,
                bitmap=img.getBitmap("icon/ico_meteorimaging_green.png"),
            )
            sizer.Add(self.bmp_current_posture)

            self.lbl_current_posture = wx.StaticText(panel, label="posture")
            self.lbl_current_posture.SetForegroundColour(
                self._theme.text_disabled
            )
            sizer.Add(
                self.lbl_current_posture,
                flag=wx.ALIGN_CENTER_VERTICAL,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_feature_section(self, fold_bar: FoldPanelBar) -> None:
        """Build feature and Z-localization controls.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_feature_panel = self._fold_item(fold_bar, "FEATURES")
        self.pnl_features = wx.Panel(self.fp_feature_panel)
        self.pnl_features.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_feature_selection_row(self.pnl_features),
                flag=wx.LEFT | wx.TOP,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_feature_status_row(self.pnl_features),
                flag=wx.LEFT | wx.TOP,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_feature_z_row(self.pnl_features),
                flag=wx.LEFT | wx.TOP | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_z_localization_row(self.pnl_features),
                flag=wx.LEFT | wx.BOTTOM | wx.RIGHT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_size_row(
                    self.pnl_features,
                    "lbl_fiducial_size",
                    "Fiducial size",
                    "cmb_fiducial_size",
                    10,
                ),
                flag=wx.LEFT | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_size_row(
                    self.pnl_features,
                    "lbl_poi_size",
                    "POI size     ",
                    "cmb_poi_size",
                    17,
                ),
                flag=wx.LEFT | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_target_selection_row(self.pnl_features),
                flag=wx.LEFT,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_target_z_row(self.pnl_features),
                flag=wx.LEFT | wx.TOP | wx.BOTTOM,
                border=self._theme.spacing_standard,
            )

        self.pnl_features.SetSizer(sizer)
        self.fp_feature_panel.add_item(self.pnl_features)

    def _build_feature_selection_row(self, parent: wx.Window) -> wx.Sizer:
        """Build feature selection controls.

        :param parent: Parent window.
        :returns: Feature selection row.
        """
        with hbox() as sizer:
            self.btn_delete_feature = self._icon_button(parent, "ico_trash.png")
            sizer.Add(self.btn_delete_feature)
            self.cmb_features = self._combo(parent, (145, 20), readonly=False)
            sizer.Add(self.cmb_features)
            self.btn_create_move_feature = self._text_button(
                parent,
                "Create / Move",
                height=24,
                size=(120, 24),
            )
            sizer.Add(
                self.btn_create_move_feature,
                flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
                border=52,
            )
        return sizer

    def _build_feature_status_row(self, parent: wx.Window) -> wx.Sizer:
        """Build feature status and navigation controls.

        :param parent: Parent window.
        :returns: Feature status row.
        """
        with hbox() as sizer:
            sizer.Add(self._label(parent, "Status"))
            self.cmb_feature_status = self._combo(
                parent,
                (122, 16),
                readonly=True,
            )
            sizer.Add(self.cmb_feature_status, flag=wx.LEFT, border=self._theme.spacing_standard)
            self.btn_go_to_feature = self._text_button(
                parent,
                "Go to Feature",
                height=24,
                size=(120, 24),
            )
            sizer.Add(
                self.btn_go_to_feature,
                flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL,
                border=52,
            )
        return sizer

    def _build_feature_z_row(self, parent: wx.Window) -> wx.Sizer:
        """Build feature Z controls.

        :param parent: Parent window.
        :returns: Feature Z row.
        """
        with hbox() as sizer:
            self.lbl_feature_z = self._label(parent, "Feature Z")
            sizer.Add(self.lbl_feature_z)
            self.ctrl_feature_z = self._unit_ctrl(
                parent,
                value=0.0,
                accuracy=5,
                key_step=0.001,
            )
            sizer.Add(self.ctrl_feature_z, flag=wx.LEFT, border=self._theme.spacing_standard)
            self.btn_use_current_z = self._text_button(
                parent,
                "Use Current Z",
                height=24,
            )
            self.btn_use_current_z.SetToolTip(
                "Save the current position of the focus as the feature Z "
                "position"
            )
            sizer.Add(self.btn_use_current_z, flag=wx.LEFT, border=65)
        return sizer

    def _build_z_localization_row(self, parent: wx.Window) -> wx.Sizer:
        """Build Z-localization action and progress controls.

        :param parent: Parent window.
        :returns: Z-localization row.
        """
        with hbox() as sizer:
            self.menu_localization_streams = ImageToggleButton(
                parent,
                icon=img.getBitmap("icon/arr_down_s.png"),
                height=24,
                size=(20, 24),
                face_colour="def",
            )
            self.menu_localization_streams.SetForegroundColour(
                self._theme.button_text
            )
            sizer.Add(
                self.menu_localization_streams,
                flag=wx.RIGHT,
                border=1,
            )

            self.btn_z_localization = self._text_button(
                parent,
                "Locate Z...",
                height=24,
            )
            sizer.Add(self.btn_z_localization, flag=wx.RIGHT, border=2)

            self.lbl_z_localization = self._label(parent, "~ 4 seconds")
            sizer.Add(self.lbl_z_localization, flag=wx.TOP, border=3)

            self.gauge_z_localization = wx.Gauge(
                parent,
                range=100,
                size=(-1, 10),
                style=wx.GA_HORIZONTAL | wx.GA_SMOOTH,
            )
            self.gauge_z_localization.Hide()
            sizer.Add(
                self.gauge_z_localization,
                proportion=1,
                flag=wx.TOP,
                border=4,
            )
        return sizer

    def _build_size_row(
        self,
        parent: wx.Window,
        label_attribute: str,
        text: str,
        combo_attribute: str,
        combo_border: int,
    ) -> wx.Sizer:
        """Build a target-size selector row.

        :param parent: Parent window.
        :param label_attribute: Public label attribute name.
        :param text: Label text.
        :param combo_attribute: Public combobox attribute name.
        :param combo_border: Left border before the combobox.
        :returns: Size selector row.
        """
        with hbox() as sizer:
            label = self._label(parent, text)
            setattr(self, label_attribute, label)
            sizer.Add(label)
            combo = self._combo(parent, (92, 16), readonly=True)
            setattr(self, combo_attribute, combo)
            sizer.Add(combo, flag=wx.LEFT, border=combo_border)
        return sizer

    def _build_target_selection_row(self, parent: wx.Window) -> wx.Sizer:
        """Build target selection controls.

        :param parent: Parent window.
        :returns: Target selection row.
        """
        with hbox() as sizer:
            self.btn_delete_target = self._icon_button(parent, "ico_trash.png")
            sizer.Add(self.btn_delete_target)
            self.cmb_targets = self._combo(parent, (145, 20), readonly=False)
            sizer.Add(self.cmb_targets)
            self.btn_go_to_target = self._text_button(
                parent,
                "Go to Target Z",
                height=24,
            )
            sizer.Add(self.btn_go_to_target, flag=wx.LEFT, border=52)
        return sizer

    def _build_target_z_row(self, parent: wx.Window) -> wx.Sizer:
        """Build target Z controls.

        :param parent: Parent window.
        :returns: Target Z row.
        """
        with hbox() as sizer:
            self.lbl_target_z = self._label(parent, "Target Z")
            sizer.Add(self.lbl_target_z)
            self.ctrl_target_z = self._unit_ctrl(
                parent,
                value=0.0,
                accuracy=5,
                key_step=0.001,
            )
            sizer.Add(self.ctrl_target_z, flag=wx.LEFT, border=self._theme.spacing_standard)
            self.btn_use_current_target_z = self._text_button(
                parent,
                "Use Current Z ",
                height=24,
            )
            self.btn_use_current_target_z.SetToolTip(
                "Save the current position of the focus as the target Z "
                "position"
            )
            sizer.Add(
                self.btn_use_current_target_z,
                flag=wx.LEFT,
                border=73,
            )
        return sizer

    def _build_optical_settings_section(
        self,
        fold_bar: FoldPanelBar,
    ) -> None:
        """Build the dynamic optical settings fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_settings_secom_optical = self._fold_item(
            fold_bar,
            "OPTICAL SETTINGS",
        )

    def _build_streams_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the live streams fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_secom_streams = self._fold_item(fold_bar, "STREAMS")
        self.pnl_secom_streams = StreamBar(
            self.fp_secom_streams,
            size=(300, -1),
            add_button=True,
        )
        self.pnl_secom_streams.SetForegroundColour(self._theme.text_muted)
        self.pnl_secom_streams.SetBackgroundColour(self._theme.background)
        self.pnl_secom_streams.btn_add_stream.SetBackgroundColour(
            self._theme.background
        )
        self.fp_secom_streams.add_item(self.pnl_secom_streams)

    def _build_acquisition_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the acquisition fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_acquisitions = self._fold_item(fold_bar, "ACQUISITIONS")
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

            self.z_stack_chkbox = wx.CheckBox(
                panel,
                label="Z-stack acquisition",
            )
            self.z_stack_chkbox.SetForegroundColour(self._theme.text_primary)
            sizer.Add(
                self.z_stack_chkbox,
                flag=wx.TOP | wx.LEFT,
                border=self._theme.spacing_standard,
            )
            sizer.Add(
                self._build_z_stack_parameters(panel),
                flag=wx.TOP,
                border=9,
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

            self.btn_acquire_overview = self._text_button(
                panel,
                "ACQUIRE OVERVIEW",
                height=48,
                font_size=self._theme.font_size_prominent_button,
            )
            sizer.Add(
                self.btn_acquire_overview,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        self.fp_acquisitions.add_item(panel)

    def _build_z_stack_parameters(self, parent: wx.Window) -> wx.Sizer:
        """Build Z-stack minimum, step, and maximum fields.

        :param parent: Parent window.
        :returns: Z-stack parameter row.
        """
        with hbox() as sizer:
            specs = (
                ("Zmin =", "param_Zmin", 10.0, -1000.0, 0.0, 25),
                ("Zstep =", "param_Zstep", 10.0, -100.0, 100.0, 1),
                ("Zmax =", "param_Zmax", 1.0, 0.0, 1000.0, 0),
            )
            for text, attribute, value, minimum, maximum, border in specs:
                grid = wx.FlexGridSizer(rows=1, cols=2, vgap=3, hgap=6)
                label = self._label(parent, text)
                grid.Add(label, flag=wx.TOP, border=3)
                control = self._unit_ctrl(
                    parent,
                    value=value,
                    accuracy=4,
                    key_step=0.000001,
                    minimum=minimum,
                    maximum=maximum,
                    font_size=self._theme.font_size_compact_control,
                )
                setattr(self, attribute, control)
                grid.Add(control, flag=wx.TOP | wx.EXPAND, border=3)
                grid.AddGrowableCol(1)
                sizer.Add(grid, flag=wx.LEFT, border=border)
        return sizer

    def _build_filename_row(self, parent: wx.Window) -> wx.Sizer:
        """Build acquisition filename controls.

        :param parent: Parent window.
        :returns: Filename row.
        """
        with hbox() as sizer:
            sizer.Add(
                self._label(parent, "Filename"),
                flag=wx.ALIGN_CENTER_VERTICAL,
            )
            self.txt_filename = wx.TextCtrl(
                parent,
                value=strings.DEFAULT_DESTINATION_FILE,
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
            self.btn_cryosecom_change_file = self._text_button(
                parent,
                "change…",
                height=24,
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
        self.btn_cryosecom_acquire = self._text_button(
            parent,
            "ACQUIRE",
            height=48,
            face_colour="blue",
            icon="ico_acqui.png",
            font_size=self._theme.font_size_primary_action,
            contrast=True,
        )
        grid.Add(
            self.btn_cryosecom_acquire,
            flag=wx.ALL | wx.EXPAND,
            border=2,
        )

        self.txt_cryosecom_est_time = self._label(
            parent,
            "Estimated time ...",
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
                self.txt_cryosecom_left_time = self._label(parent, "")
                self.txt_cryosecom_left_time.Hide()
                gauge_sizer.Add(
                    self.txt_cryosecom_left_time,
                    proportion=1,
                    flag=wx.TOP,
                    border=self._theme.spacing_standard,
                )
            progress_sizer.Add(gauge_sizer, flag=wx.TOP, border=-8)

            self.btn_cryosecom_acqui_cancel = self._text_button(
                parent,
                "cancel",
                height=24,
            )
            progress_sizer.Add(
                self.btn_cryosecom_acqui_cancel,
                flag=wx.TOP,
                border=12,
            )
        grid.Add(progress_sizer, flag=wx.EXPAND)
        grid.AddGrowableCol(1)
        return grid

    def _build_automation_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the optional automated feature acquisition section.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_automation = self._fold_item(fold_bar, "AUTOMATION")
        self.pnl_automation = wx.Panel(self.fp_automation)
        self.pnl_automation.SetBackgroundColour(self._theme.background)

        with vbox() as self.automation_sizer:
            self.acquire_features_chk_list = wx.CheckListBox(
                self.pnl_automation
            )
            set_font(
                self.acquire_features_chk_list,
                self._theme.font_size_checklist,
            )
            self.automation_sizer.Add(
                self.acquire_features_chk_list,
                proportion=1,
                flag=wx.RIGHT | wx.LEFT | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.chk_use_autofocus_acquire_features = wx.CheckBox(
                self.pnl_automation,
                label="AutoFocus before acquiring at features",
            )
            self.chk_use_autofocus_acquire_features.SetForegroundColour(
                self._theme.text_primary
            )
            self.automation_sizer.Add(
                self.chk_use_autofocus_acquire_features,
                flag=wx.LEFT,
                border=15,
            )

            self.btn_acquire_features = self._text_button(
                self.pnl_automation,
                "ACQUIRE AT FEATURES",
                height=48,
                face_colour="blue",
                icon="ico_acqui.png",
                font_size=self._theme.font_size_prominent_button,
                contrast=True,
            )
            self.automation_sizer.Add(
                self.btn_acquire_features,
                flag=wx.TOP | wx.BOTTOM | wx.LEFT,
                border=self._theme.spacing_standard,
            )

            self.txt_acquire_features_est_time = self._label(
                self.pnl_automation,
                "Estimated time ...",
            )
            self.txt_acquire_features_est_time.Hide()
            self.automation_sizer.Add(
                self.txt_acquire_features_est_time,
                flag=wx.LEFT,
                border=15,
            )

        self.pnl_automation.SetSizer(self.automation_sizer)
        self.fp_automation.add_item(self.pnl_automation)

    def _build_acquired_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the acquired streams section.

        :param fold_bar: Parent fold-panel bar.
        """
        item = self._fold_item(fold_bar, "ACQUIRED")
        self.pnl_cryosecom_acquired = StreamBar(
            item,
            size=(300, -1),
            add_button=False,
        )
        self.pnl_cryosecom_acquired.SetForegroundColour(
            self._theme.text_muted
        )
        self.pnl_cryosecom_acquired.SetBackgroundColour(
            self._theme.background
        )
        item.add_item(self.pnl_cryosecom_acquired)

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

    def _label(self, parent: wx.Window, text: str) -> wx.StaticText:
        """Create a primary-colour static label.

        :param parent: Parent window.
        :param text: Label text.
        :returns: Styled static label.
        """
        label = wx.StaticText(parent, label=text)
        label.SetForegroundColour(self._theme.text_primary)
        return label

    def _icon_button(
        self,
        parent: wx.Window,
        icon_name: str,
    ) -> ImageButton:
        """Create a small icon button.

        :param parent: Parent window.
        :param icon_name: Icon file name.
        :returns: Icon button.
        """
        return ImageButton(
            parent,
            icon=img.getBitmap(f"icon/{icon_name}"),
            height=16,
            style=wx.ALIGN_CENTRE,
        )

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
        :param icon: Optional icon file name.
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
        if font_size is not None:
            set_font(button, font_size)
        return button

    def _combo(
        self,
        parent: wx.Window,
        size: Any,
        readonly: bool,
    ) -> wx.adv.OwnerDrawnComboBox:
        """Create a themed owner-drawn combobox.

        :param parent: Parent window.
        :param size: Control size.
        :param readonly: Whether text entry is disabled.
        :returns: Styled combobox.
        """
        style = wx.BORDER_NONE | wx.CB_DROPDOWN | wx.TE_PROCESS_ENTER
        if readonly:
            style |= wx.CB_READONLY
        combo = wx.adv.OwnerDrawnComboBox(
            parent,
            size=size,
            style=style,
        )
        combo.SetButtonBitmaps(
            img.getBitmap("button/btn_down.png"),
            pushButtonBg=False,
        )
        combo.SetForegroundColour(self._theme.text_edit)
        combo.SetBackgroundColour(self._theme.background)
        return combo

    def _unit_ctrl(
        self,
        parent: wx.Window,
        value: float,
        accuracy: int,
        key_step: float,
        minimum: float = 0.0,
        maximum: float = 0.0,
        font_size: Optional[int] = None,
    ) -> UnitFloatCtrl:
        """Create a unit-aware floating-point control.

        :param parent: Parent window.
        :param value: Initial numeric value.
        :param accuracy: Significant-digit accuracy.
        :param key_step: Keyboard increment.
        :param minimum: Minimum accepted value.
        :param maximum: Maximum accepted value.
        :param font_size: Optional font point size.
        :returns: Unit-aware control.
        """
        control = UnitFloatCtrl(
            parent,
            value=value,
            size=(-1, 15),
            style=wx.BORDER_NONE,
            unit="m",
            min_val=minimum,
            max_val=maximum,
            key_step=key_step,
            accuracy=accuracy,
        )
        control.SetBackgroundColour(self._theme.background)
        if font_size is not None:
            set_font(control, font_size)
        return control


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabLocalization)
