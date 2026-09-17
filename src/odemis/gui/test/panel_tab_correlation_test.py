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

from odemis.gui.layout import PnlTabCorrelation
from odemis.gui.layout.constants.themes import DARK


class PnlTabCorrelationTest(unittest.TestCase):
    """Verify the pure-Python correlation layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh correlation panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabCorrelation(self.frame)

    def tearDown(self) -> None:
        """Destroy the correlation panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "correlation_toolbar",
            "lbl_correlation_view_all",
            "btn_correlation_view_all",
            "lbl_correlation_view_tl",
            "btn_correlation_view_tl",
            "lbl_correlation_view_tr",
            "btn_correlation_view_tr",
            "lbl_correlation_view_bl",
            "btn_correlation_view_bl",
            "lbl_correlation_view_br",
            "btn_correlation_view_br",
            "btn_log",
            "pnl_correlaton_grid",
            "vp_correlation_tl",
            "vp_correlation_tr",
            "vp_correlation_bl",
            "vp_correlation_br",
            "fp_correlation_streams",
            "pnl_correlation_streams",
            "fp_meteor_correlation",
            "ctrl_enable_correlation",
            "ctrl_auto_resize_view",
            "cmb_correlation_reference",
            "cmb_correlation_stream",
            "dxy_step_cntrl",
            "dr_step_cntrl",
            "dpx_step_cntrl",
            "btn_reset_correlation",
            "btn_correlate",
            "btn_export",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertEqual(
            self.panel.pnl_correlaton_grid.viewports,
            (
                self.panel.vp_correlation_tl,
                self.panel.vp_correlation_tr,
                self.panel.vp_correlation_bl,
                self.panel.vp_correlation_br,
            ),
        )
        self.assertEqual(self.panel.dxy_step_cntrl.GetValue(), 1e-6)
        self.assertEqual(self.panel.dr_step_cntrl.GetValue(), 1.0)
        self.assertEqual(self.panel.dpx_step_cntrl.GetValue(), 1.0)
        self.assertEqual(
            self.panel.btn_reset_correlation.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )
        self.assertEqual(
            self.panel.btn_correlate.GetForegroundColour(),
            wx.Colour(DARK.button_text_contrast),
        )
        self.assertEqual(
            self.panel.btn_export.GetForegroundColour(),
            wx.Colour(DARK.button_text_contrast),
        )

    def test_default_button_rejects_contrasting_text(self) -> None:
        """Reject contrasting text on the default button face."""
        with self.assertRaises(ValueError):
            self.panel._text_button(
                self.panel,
                "Invalid",
                height=24,
                contrast=True,
            )


if __name__ == "__main__":
    unittest.main()
