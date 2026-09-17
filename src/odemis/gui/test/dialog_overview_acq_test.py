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

from odemis.gui.layout import DlgOverviewAcq
from odemis.gui.layout.constants.themes import DARK


class DlgOverviewAcqTest(unittest.TestCase):
    """Verify the pure-Python overview acquisition dialog layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh overview acquisition dialog."""
        self.dlg = DlgOverviewAcq(None)

    def tearDown(self) -> None:
        """Destroy the dialog."""
        self.dlg.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "pnl_view_acq",
            "scr_win_right",
            "fp_settings_secom_optical",
            "pnl_secom_streams",
            "zstack_steps_label",
            "zstack_steps",
            "zstep_size_label",
            "zstep_size_ctrl",
            "whole_grid_chkbox",
            "tiles_number_x",
            "tiles_number_y",
            "selected_grid_lbl",
            "selected_grid_pnl_holder",
            "area_size_txt",
            "autofocus_chkbox",
            "focus_points_dist_lbl",
            "focus_points_dist_ctrl",
            "gauge_acq",
            "lbl_acqestimate",
            "btn_cancel",
            "btn_secom_acquire",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.dlg, attribute))

        self.assertFalse(self.dlg.gauge_acq.IsShown())
        self.assertEqual(self.dlg.gauge_acq.GetRange(), 100)
        self.assertEqual(self.dlg.gauge_acq.GetValue(), 50)

        self.assertEqual(self.dlg.zstack_steps.GetMin(), 1)
        self.assertEqual(self.dlg.zstack_steps.GetMax(), 51)
        self.assertEqual(self.dlg.zstack_steps.GetValue(), 21)

        self.assertEqual(self.dlg.tiles_number_x.GetValue(), 10)
        self.assertEqual(self.dlg.tiles_number_y.GetValue(), 10)

        self.assertEqual(
            self.dlg.btn_cancel.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )
        self.assertEqual(
            self.dlg.btn_secom_acquire.GetForegroundColour(),
            wx.Colour(DARK.button_text_contrast),
        )

    def test_default_button_rejects_contrasting_text(self) -> None:
        """Reject contrasting text on the default button face."""
        with self.assertRaises(ValueError):
            self.dlg._text_button(
                self.dlg,
                "Invalid",
                height=24,
                contrast=True,
            )


if __name__ == "__main__":
    unittest.main()
