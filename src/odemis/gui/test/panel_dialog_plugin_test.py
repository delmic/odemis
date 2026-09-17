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

from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import LiveViewport, PointSpectrumViewport
from odemis.gui.layout import PluginDialog
from odemis.gui.layout.constants.themes import DARK


class PnlDialogPluginTest(unittest.TestCase):
    """Verify the pure-Python plugin acquisition dialog layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh plugin acquisition dialog."""
        self.dialog = PluginDialog(None)

    def tearDown(self) -> None:
        """Destroy the plugin acquisition dialog."""
        self.dialog.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller-accessed controls with their expected types."""
        attributes = (
            "pnl_desc",
            "viewport_l",
            "viewport_r",
            "spectrum_viewport",
            "fp_settings",
            "fp_streams",
            "pnl_streams",
            "pnl_info",
            "pnl_gauge",
            "gauge_progress",
            "lbl_gauge",
            "btn_cancel",
            "pnl_buttons",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.dialog, attribute))

        self.assertIsInstance(self.dialog.viewport_l, LiveViewport)
        self.assertIsInstance(self.dialog.viewport_r, LiveViewport)
        self.assertIsInstance(self.dialog.spectrum_viewport, PointSpectrumViewport)
        self.assertIsInstance(self.dialog.fp_settings, FoldPanelItem)
        self.assertIsInstance(self.dialog.fp_streams, FoldPanelItem)
        self.assertIsInstance(self.dialog.pnl_streams, StreamBar)
        self.assertIsInstance(self.dialog.btn_cancel, ImageTextButton)

        self.assertFalse(self.dialog.viewport_l.IsShown())
        self.assertFalse(self.dialog.viewport_r.IsShown())
        self.assertFalse(self.dialog.spectrum_viewport.IsShown())
        self.assertFalse(self.dialog.fp_streams.IsShown())
        self.assertFalse(self.dialog.pnl_info.IsShown())
        self.assertFalse(self.dialog.pnl_gauge.IsShown())
        self.assertFalse(self.dialog.btn_cancel.IsEnabled())

        self.assertEqual(self.dialog.gauge_progress.GetRange(), 100)
        self.assertEqual(self.dialog.btn_cancel.GetLabel(), "cancel")
        self.assertEqual(
            self.dialog.btn_cancel.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )

    def test_description_and_buttons_panels_have_empty_sizers(self) -> None:
        """Provide empty sizers for content plugins add at runtime."""
        self.assertIsNotNone(self.dialog.pnl_desc.GetSizer())
        self.assertEqual(self.dialog.pnl_desc.GetSizer().GetItemCount(), 0)
        self.assertIsNotNone(self.dialog.pnl_buttons.GetSizer())
        self.assertEqual(self.dialog.pnl_buttons.GetSizer().GetItemCount(), 0)


if __name__ == "__main__":
    unittest.main()
