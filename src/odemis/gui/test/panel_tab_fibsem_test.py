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

import unittest
from dataclasses import replace

import wx

import odemis.gui.layout as layout
from odemis.gui.comp.viewport import FeatureOverviewViewport, LiveViewport
from odemis.gui.layout.components.panel_tab_fibsem import PnlTabFibsem


class PnlTabFibsemTest(unittest.TestCase):
    """Verify the pure-Python FIBSEM layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh FIBSEM panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabFibsem(self.frame)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.panel, proportion=1, flag=wx.EXPAND)
        self.frame.SetSizer(sizer)
        self.frame.Maximize(True)
        self.frame.Show()
        wx.Yield()

    def tearDown(self) -> None:
        """Destroy the FIBSEM panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "secom_toolbar",
            "lbl_secom_view_all",
            "btn_secom_view_all",
            "lbl_secom_view_tl",
            "btn_secom_view_tl",
            "lbl_secom_view_tr",
            "btn_secom_view_tr",
            "lbl_secom_view_bl",
            "btn_secom_view_bl",
            "lbl_secom_view_br",
            "btn_secom_view_br",
            "btn_log",
            "pnl_secom_grid",
            "vp_secom_tl",
            "vp_secom_tr",
            "vp_secom_bl",
            "vp_secom_br",
            "scr_win_right",
            "btn_delete_feature",
            "cmb_features",
            "btn_create_move_feature",
            "cmb_feature_status",
            "btn_go_to_feature",
            "btn_feature_save_position",
            "btn_switch_sem_imaging",
            "btn_switch_milling",
            "ctrl_milling_angle",
            "fp_settings_secom_optical",
            "fp_secom_streams",
            "pnl_secom_streams",
            "fp_acquisitions",
            "streams_chk_list",
            "chkbox_save_acquisition",
            "txt_filename",
            "btn_cryosecom_change_file",
            "btn_cryosecom_acquire",
            "txt_cryosecom_est_time",
            "gauge_cryosecom_acq",
            "txt_cryosecom_left_time",
            "btn_cryosecom_acqui_cancel",
            "btn_acquire_all",
            "btn_acquire_overview",
            "btn_tdct",
            "fp_acquired",
            "pnl_cryosecom_acquired",
            "fp_automation",
            "workflow_features_chk_list",
            "workflow_task_chk_list",
            "btn_run_automated_milling",
            "txt_automated_milling_est_time",
            "gauge_automated_milling",
            "txt_automated_milling_left_time",
            "btn_automated_milling_cancel",
            "txt_automated_milling_status",
            "fp_milling",
            "milling_task_chk_list",
            "btn_run_milling",
            "txt_milling_est_time",
            "gauge_milling_series",
            "txt_milling_series_left_time",
            "btn_milling_cancel",
            "pnl_patterns",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertEqual(
            self.panel.pnl_secom_grid.viewports,
            (
                self.panel.vp_secom_tl,
                self.panel.vp_secom_tr,
                self.panel.vp_secom_bl,
                self.panel.vp_secom_br,
            ),
        )
        self.assertIsInstance(self.panel.vp_secom_tl, LiveViewport)
        self.assertIsInstance(self.panel.vp_secom_tr, LiveViewport)
        self.assertIsInstance(
            self.panel.vp_secom_bl,
            FeatureOverviewViewport,
        )
        self.assertIsInstance(self.panel.vp_secom_br, LiveViewport)
        self.assertFalse(self.panel.txt_cryosecom_est_time.IsShown())
        self.assertFalse(self.panel.txt_cryosecom_left_time.IsShown())
        self.assertFalse(self.panel.btn_run_milling.IsShown())
        self.assertFalse(self.panel.txt_milling_est_time.IsShown())
        self.assertFalse(self.panel.gauge_milling_series.IsShown())
        self.assertFalse(self.panel.txt_milling_series_left_time.IsShown())
        self.assertFalse(self.panel.btn_milling_cancel.IsShown())
        self.assertEqual(self.panel.gauge_cryosecom_acq.GetRange(), 100)
        self.assertEqual(self.panel.gauge_automated_milling.GetRange(), 100)
        self.assertEqual(self.panel.gauge_milling_series.GetRange(), 100)


    def test_button_foregrounds(self) -> None:
        """Apply normal and contrasting foregrounds independently."""
        normal_buttons = (
            self.panel.btn_create_move_feature,
            self.panel.btn_go_to_feature,
            self.panel.btn_switch_sem_imaging,
            self.panel.btn_switch_milling,
            self.panel.btn_acquire_overview,
        )
        contrasting_buttons = (
            self.panel.btn_feature_save_position,
            self.panel.btn_cryosecom_acquire,
            self.panel.btn_acquire_all,
            self.panel.btn_run_automated_milling,
            self.panel.btn_run_milling,
        )

        for button in normal_buttons:
            with self.subTest(button=button):
                self.assertEqual(
                    button.GetForegroundColour(),
                    wx.Colour(layout.theme.button_text),
                )
        for button in contrasting_buttons:
            with self.subTest(button=button):
                self.assertEqual(
                    button.GetForegroundColour(),
                    wx.Colour(layout.theme.button_text_contrast),
                )

    def test_uses_current_layout_theme_at_construction(self) -> None:
        """Resolve the selected layout theme when a component is created."""
        original_theme = layout.theme
        selected_theme = replace(original_theme, background="#121212")
        try:
            layout.theme = selected_theme
            panel = PnlTabFibsem(self.frame)
            self.assertIs(panel._theme, selected_theme)
            self.assertEqual(
                panel.GetBackgroundColour(),
                wx.Colour(selected_theme.background),
            )
            panel.Destroy()
        finally:
            layout.theme = original_theme


if __name__ == "__main__":
    unittest.main()
