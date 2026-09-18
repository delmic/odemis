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

import wx

from odemis.gui.layout.components.panel_tab_localization import PnlTabLocalization
from odemis.gui.layout import theme


class PnlTabLocalizationTest(unittest.TestCase):
    """Verify the pure-Python localization layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh localization panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabLocalization(self.frame)

    def tearDown(self) -> None:
        """Destroy the localization panel host frame."""
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
            "pnl_current_posture",
            "bmp_current_posture",
            "lbl_current_posture",
            "fp_feature_panel",
            "pnl_features",
            "fp_settings_secom_optical",
            "fp_secom_streams",
            "pnl_secom_streams",
            "fp_acquisitions",
            "streams_chk_list",
            "z_stack_chkbox",
            "param_Zmin",
            "param_Zstep",
            "param_Zmax",
            "txt_filename",
            "btn_cryosecom_change_file",
            "btn_cryosecom_acquire",
            "txt_cryosecom_est_time",
            "gauge_cryosecom_acq",
            "txt_cryosecom_left_time",
            "btn_cryosecom_acqui_cancel",
            "btn_acquire_overview",
            "fp_automation",
            "pnl_automation",
            "automation_sizer",
            "acquire_features_chk_list",
            "chk_use_autofocus_acquire_features",
            "btn_acquire_features",
            "txt_acquire_features_est_time",
            "pnl_cryosecom_acquired",
            "cmb_features",
            "cmb_feature_status",
            "btn_create_move_feature",
            "btn_delete_feature",
            "btn_go_to_feature",
            "ctrl_feature_z",
            "btn_use_current_z",
            "menu_localization_streams",
            "btn_z_localization",
            "lbl_z_localization",
            "gauge_z_localization",
            "lbl_fiducial_size",
            "cmb_fiducial_size",
            "lbl_poi_size",
            "cmb_poi_size",
            "btn_delete_target",
            "cmb_targets",
            "btn_go_to_target",
            "lbl_target_z",
            "ctrl_target_z",
            "btn_use_current_target_z",
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
        self.assertFalse(self.panel.gauge_z_localization.IsShown())
        self.assertFalse(self.panel.txt_cryosecom_est_time.IsShown())
        self.assertFalse(self.panel.txt_cryosecom_left_time.IsShown())
        self.assertEqual(self.panel.gauge_z_localization.GetRange(), 100)
        self.assertEqual(self.panel.gauge_cryosecom_acq.GetRange(), 100)
        self.assertEqual(
            self.panel.btn_create_move_feature.GetForegroundColour(),
            wx.Colour(theme.button_text),
        )
        self.assertEqual(
            self.panel.btn_go_to_feature.GetForegroundColour(),
            wx.Colour(theme.button_text),
        )
        self.assertEqual(
            self.panel.btn_cryosecom_acquire.GetForegroundColour(),
            wx.Colour(theme.button_text_contrast),
        )


if __name__ == "__main__":
    unittest.main()
