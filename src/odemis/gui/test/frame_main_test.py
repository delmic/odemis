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

from odemis.gui.layout import MainFrame


class FrMainTest(unittest.TestCase):
    """Verify the pure-Python main application frame layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh main frame."""
        self.frame = MainFrame(None)

    def tearDown(self) -> None:
        """Destroy the main frame."""
        self.frame.Destroy()

    def test_menu_bar_order_and_item_identity(self) -> None:
        """Expose File/View/Help menus in order, as real menu items."""
        menu_bar = self.frame.GetMenuBar()
        self.assertEqual(menu_bar.GetMenuCount(), 3)
        self.assertEqual(menu_bar.GetMenuLabel(0), "File")
        self.assertEqual(menu_bar.GetMenuLabel(1), "View")
        self.assertEqual(menu_bar.GetMenuLabel(2), "Help")

        file_menu = menu_bar.GetMenu(0)
        self.assertIs(self.frame.menu_item_open.GetMenu(), file_menu)
        self.assertEqual(
            self.frame.menu_item_open.GetItemLabel(), "Open...\tCtrl+O"
        )

    def test_menu_item_attributes_and_initial_state(self) -> None:
        """Expose every controller-used menu item with correct state."""
        disabled = (
            "menu_item_reset_finealign",
            "menu_item_reset_overview",
            "menu_item_22view",
            "menu_item_play_stream",
            "menu_item_fit_content",
            "menu_item_auto_cont",
            "menu_item_auto_focus",
            "menu_item_cross",
            "menu_item_interpolation",
            "menu_item_rawpixel",
            "menu_item_manual",
            "menu_item_devmanual",
            "menu_item_edit_meteor_calibration",
        )
        for attribute in disabled:
            with self.subTest(attribute=attribute):
                item = getattr(self.frame, attribute)
                self.assertFalse(item.IsEnabled())

        checkable = (
            "menu_item_22view",
            "menu_item_play_stream",
            "menu_item_auto_cont",
            "menu_item_cross",
            "menu_item_interpolation",
            "menu_item_rawpixel",
            "menu_item_show_correlation",
            "menu_item_debug",
            "menu_item_data_sharing",
        )
        for attribute in checkable:
            with self.subTest(attribute=attribute):
                item = getattr(self.frame, attribute)
                self.assertTrue(item.IsCheckable())

        self.assertTrue(self.frame.menu_item_show_correlation.IsEnabled())
        self.assertFalse(hasattr(self.frame, "menu_view"))

    def test_menu_item_removable_from_owning_menu(self) -> None:
        """Support the Remove()/Destroy() patterns used by MenuController."""
        file_menu = self.frame.menu_item_reset_finealign.GetMenu()
        file_menu.Remove(self.frame.menu_item_reset_finealign)

        menu = self.frame.menu_item_data_sharing.GetMenu()
        menu.Remove(self.frame.menu_item_data_sharing)
        self.frame.menu_item_data_sharing.Destroy()

    def test_tab_buttons_and_temperature_display_hidden(self) -> None:
        """Expose all tab buttons, initially hidden, sized for the bar."""
        names = (
            "btn_tab_cryosecom_chamber",
            "btn_tab_secom_streams",
            "btn_tab_localization",
            "btn_tab_correlation",
            "btn_tab_fibsem",
            "btn_tab_sparc_acqui",
            "btn_tab_fastem_main",
            "btn_tab_inspection",
            "btn_tab_sparc_chamber",
            "btn_tab_align",
        )
        for name in names:
            with self.subTest(name=name):
                button = getattr(self.frame, name)
                self.assertFalse(button.IsShown())
                self.assertEqual(button.GetSize(), (160, 30))

        self.assertFalse(self.frame.temperature_display.IsShown())
        self.assertTrue(self.frame.logo.IsShown())

    def test_root_sizer_layout_for_tab_bar_controller(self) -> None:
        """Expose a 2-item root sizer so tab panels can be inserted at 1."""
        sizer = self.frame.GetSizer()
        self.assertEqual(sizer.GetItemCount(), 2)
        self.assertIs(sizer.GetItem(0).GetWindow(), self.frame.pnl_tabbuttons)
        self.assertIs(sizer.GetItem(1).GetWindow(), self.frame.pnl_log)

        tab_panel = wx.Panel(self.frame)
        sizer.Insert(1, tab_panel, flag=wx.EXPAND, proportion=1)
        self.assertEqual(sizer.GetItemCount(), 3)
        self.assertIs(sizer.GetItem(1).GetWindow(), tab_panel)
        self.assertIs(sizer.GetItem(2).GetWindow(), self.frame.pnl_log)

    def test_pnl_tabbuttons_min_size_and_hide(self) -> None:
        """Keep the tab-button bar height fixed and hideable."""
        self.assertEqual(self.frame.pnl_tabbuttons.GetMinSize(), (-1, 40))
        self.frame.pnl_tabbuttons.Hide()
        self.assertFalse(self.frame.pnl_tabbuttons.IsShown())

    def test_log_panel_initial_state_and_widgets(self) -> None:
        """Expose the log panel widgets, with the panel initially hidden."""
        self.assertFalse(self.frame.pnl_log.IsShown())
        self.assertEqual(
            self.frame.txt_log.GetValue(), "Log message panel"
        )
        self.assertEqual(
            self.frame.btn_log.GetToolTipText(), "Close log panel"
        )


if __name__ == "__main__":
    unittest.main()
