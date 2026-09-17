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

from odemis.gui.layout import PnlTabSecomStreams
from odemis.gui.layout.constants.themes import DARK


class PnlTabSecomStreamsTest(unittest.TestCase):
    """Verify the pure-Python SECOM streams layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh SECOM streams panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabSecomStreams(self.frame)

    def tearDown(self) -> None:
        """Destroy the SECOM streams panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "lbl_secom_overview",
            "btn_secom_overview",
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
            "vp_flim_chronograph",
            "vp_overview_sem",
            "main_buttons",
            "btn_press",
            "btn_opt",
            "btn_sem",
            "pnl_hw_info",
            "pnl_load_status",
            "gauge_load_time",
            "lbl_load_time",
            "pnl_stream_status",
            "bmp_stream_status_info",
            "bmp_stream_status_warn",
            "bmp_stream_status_error",
            "lbl_stream_status",
            "scr_win_right",
            "fp_settings_secom_optical",
            "pnl_opt_streams",
            "fp_settings_secom_sem",
            "pnl_secom_streams",
            "btn_secom_acquire",
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
                self.panel.vp_flim_chronograph,
                self.panel.vp_overview_sem,
            ),
        )
        self.assertFalse(self.panel.vp_flim_chronograph.IsShown())
        self.assertFalse(self.panel.vp_overview_sem.IsShown())
        self.assertFalse(self.panel.pnl_hw_info.IsShown())
        self.assertFalse(self.panel.pnl_load_status.IsShown())
        self.assertFalse(self.panel.pnl_stream_status.IsShown())
        self.assertFalse(self.panel.bmp_stream_status_info.IsShown())
        self.assertFalse(self.panel.bmp_stream_status_warn.IsShown())
        self.assertFalse(self.panel.bmp_stream_status_error.IsShown())
        self.assertEqual(self.panel.gauge_load_time.GetRange(), 100)
        self.assertEqual(self.panel.lbl_load_time.GetLabel(), "test st s")
        self.assertTrue(self.panel.pnl_secom_streams.btn_add_stream.IsShown())
        self.assertFalse(self.panel.pnl_opt_streams.btn_add_stream.IsShown())
        self.assertEqual(
            self.panel.btn_press.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )
        self.assertEqual(
            self.panel.btn_secom_acquire.GetForegroundColour(),
            wx.Colour(DARK.button_text_contrast),
        )


if __name__ == "__main__":
    unittest.main()
