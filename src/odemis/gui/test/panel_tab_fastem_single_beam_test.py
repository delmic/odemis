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

from odemis.gui.layout import PnlTabFastemSingleBeam
from odemis.gui.layout.constants.themes import DARK


class PnlTabFastemSingleBeamTest(unittest.TestCase):
    """Verify the pure-Python FastEM single-beam acquisition tab layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh FastEM single-beam acquisition panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabFastemSingleBeam(self.frame)

    def tearDown(self) -> None:
        """Destroy the FastEM single-beam acquisition panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "pnl_projects",
            "pnl_acq",
            "txt_num_roas",
            "bmp_acq_status_info",
            "bmp_acq_status_warn",
            "lbl_acq_estimate",
            "gauge_acq",
            "btn_cancel",
            "btn_acquire",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertFalse(self.panel.bmp_acq_status_info.IsShown())
        self.assertFalse(self.panel.bmp_acq_status_warn.IsShown())
        self.assertFalse(self.panel.btn_cancel.IsShown())
        self.assertEqual(self.panel.txt_num_roas.GetValue(), "0")
        self.assertEqual(self.panel.gauge_acq.GetRange(), 100)
        self.assertEqual(self.panel.gauge_acq.GetValue(), 0)
        self.assertEqual(
            self.panel.btn_cancel.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )
        self.assertEqual(
            self.panel.btn_acquire.GetForegroundColour(),
            wx.Colour(DARK.button_text_contrast),
        )


if __name__ == "__main__":
    unittest.main()
