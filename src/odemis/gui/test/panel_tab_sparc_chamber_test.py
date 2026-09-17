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

from odemis.gui.layout import PnlTabSparcChamber
from odemis.gui.layout.constants.themes import DARK


class PnlTabSparcChamberTest(unittest.TestCase):
    """Verify the pure-Python SPARC2 chamber layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh SPARC2 chamber panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabSparcChamber(self.frame)

    def tearDown(self) -> None:
        """Destroy the chamber panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "btn_switch_mirror",
            "pnl_move",
            "gauge_move",
            "btn_cancel",
            "pnl_ref_msg",
            "txt_warning",
            "btn_log",
            "vp_chamber",
            "scr_win_right",
            "pnl_streams",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertFalse(self.panel.btn_cancel.IsEnabled())
        self.assertEqual(self.panel.gauge_move.GetRange(), 100)
        self.assertEqual(self.panel.gauge_move.GetValue(), 0)
        self.assertEqual(
            self.panel.btn_switch_mirror.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )
        self.assertEqual(
            self.panel.btn_cancel.GetForegroundColour(),
            wx.Colour(DARK.button_text),
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
