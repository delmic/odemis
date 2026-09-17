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

from odemis.gui.layout import PnlTabFastemMultiBeam
from odemis.gui.layout.constants.themes import DARK


class PnlTabFastemMultiBeamTest(unittest.TestCase):
    """Verify the pure-Python FastEM multi-beam acquisition tab layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh FastEM multi-beam acquisition panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabFastemMultiBeam(self.frame)

    def tearDown(self) -> None:
        """Destroy the FastEM multi-beam acquisition panel host frame."""
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

    def test_min_size_preserved_for_fixed_panels(self) -> None:
        """Ensure fixed-size panels keep a MinSize floor so sizers do not
        collapse them via GetBestSize(), which previously hid the ROA
        project tree and acquisition settings after construction."""
        self.assertEqual(self.panel.pnl_projects.GetMinSize(), (400, 700))
        self.assertEqual(self.panel.pnl_acq.GetMinSize(), (400, 140))

    def test_projects_panel_has_nonzero_height_before_show(self) -> None:
        """Ensure pnl_projects already has usable height immediately after
        construction, before any Show or resize event.

        Controllers read pnl_projects.Size right after construction to
        size the project tree; if this panel starts at height 0 (as it
        did when the root panel's initial size was (400, -1)), the tree
        gets built with zero height and never recovers even after later
        layout passes resize the surrounding panels."""
        self.assertGreater(self.panel.pnl_projects.GetSize().height, 0)

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
