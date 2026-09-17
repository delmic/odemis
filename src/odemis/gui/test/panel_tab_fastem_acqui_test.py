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

from odemis.gui.layout.components.panel_tab_fastem_acqui import PnlTabFastemAcqui
from odemis.gui.layout import theme


class PnlTabFastemAcquiTest(unittest.TestCase):
    """Verify the pure-Python FastEM acquisition tab layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh FastEM acquisition panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabFastemAcqui(self.frame)

    def tearDown(self) -> None:
        """Destroy the FastEM acquisition panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "pnl_tabbuttons",
            "btn_tab_single_beam",
            "btn_tab_multi_beam",
            "pnl_acqui_tabs",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertEqual(
            self.panel.btn_tab_single_beam.GetForegroundColour(),
            wx.Colour(theme.text_primary),
        )
        self.assertEqual(
            self.panel.btn_tab_multi_beam.GetForegroundColour(),
            wx.Colour(theme.text_primary),
        )


if __name__ == "__main__":
    unittest.main()
