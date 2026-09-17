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

from odemis.gui.layout import FrCorrelation


class FrCorrelationTest(unittest.TestCase):
    """Verify the pure-Python multipoint correlation dialog layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh correlation dialog."""
        self.dialog = FrCorrelation(None)

    def tearDown(self) -> None:
        """Destroy the correlation dialog."""
        self.dialog.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "correlation_toolbar",
            "pnl_correlation_grid",
            "vp_correlation_tl",
            "vp_correlation_tr",
            "bmp_fm_posture",
            "lbl_fm_posture",
            "fp_correlation_panel",
            "btn_delete_row",
            "btn_xyz_targeting",
            "txt_refine_xyz_active",
            "table_grid",
            "txt_correlation_rms",
            "fp_correlation_streams",
            "pnl_correlation_streams",
            "btn_close",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.dialog, attribute))

        self.assertEqual(
            self.dialog.pnl_correlation_grid.viewports,
            (self.dialog.vp_correlation_tl, self.dialog.vp_correlation_tr),
        )
        self.assertFalse(self.dialog.txt_refine_xyz_active.IsShown())
        self.assertFalse(self.dialog.txt_correlation_rms.IsShown())
        self.assertEqual(self.dialog.GetTitle(), "Multipoint Correlation")


if __name__ == "__main__":
    unittest.main()
