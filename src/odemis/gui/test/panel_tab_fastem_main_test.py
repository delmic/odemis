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

from odemis.gui.layout import PnlTabFastemMain
from odemis.gui.layout.constants.themes import DARK


class PnlTabFastemMainTest(unittest.TestCase):
    """Verify the pure-Python FastEM main tab layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh FastEM main panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabFastemMain(self.frame)

    def tearDown(self) -> None:
        """Destroy the FastEM main panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "pnl_user_settings",
            "pnl_toolbar",
            "btn_pnl_user_settings",
            "toolbar",
            "btn_log",
            "pnl_vp_grid_project_manager",
            "pnl_vp_grid",
            "pnl_project_manager_header",
            "btn_pnl_project_manager",
            "btn_detach_project_manager",
            "pnl_project_manager",
            "pnl_tabbuttons",
            "btn_tab_setup",
            "btn_tab_acqui",
            "pnl_tabs",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertFalse(self.panel.pnl_project_manager.IsShown())
        self.assertEqual(
            self.panel.btn_tab_setup.GetForegroundColour(),
            wx.Colour(DARK.text_primary),
        )
        self.assertEqual(
            self.panel.btn_tab_acqui.GetForegroundColour(),
            wx.Colour(DARK.text_primary),
        )

    def test_min_size_preserved_for_fixed_panels(self) -> None:
        """Ensure fixed-size panels keep a MinSize floor so sizers do not
        collapse them via GetBestSize()."""
        self.assertEqual(self.panel.pnl_user_settings.GetMinSize(), (300, 600))
        self.assertEqual(self.panel.pnl_vp_grid.GetMinSize(), (1108, 998))
        self.assertEqual(
            self.panel.pnl_project_manager.GetMinSize(), (1108, 200)
        )
        self.assertEqual(self.panel.pnl_tabbuttons.GetMinSize(), (400, 40))
        self.assertEqual(self.panel.pnl_tabs.GetMinSize(), (400, 1500))


if __name__ == "__main__":
    unittest.main()
