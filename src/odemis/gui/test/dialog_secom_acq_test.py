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

from odemis.gui.layout import SecomAcqDialogBase


class SecomAcqDialogTest(unittest.TestCase):
    """Verify the pure-Python SECOM acquisition dialog layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh acquisition dialog."""
        self.dialog = SecomAcqDialogBase(None)

    def tearDown(self) -> None:
        """Destroy the acquisition dialog."""
        self.dialog.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "pnl_view_acq",
            "cmb_presets",
            "txt_filename",
            "btn_change_file",
            "txt_destination",
            "fp_settings_secom_optical",
            "pnl_opt_streams",
            "fp_settings_secom_sem",
            "pnl_secom_streams",
            "chkbox_fine_align",
            "gauge_acq",
            "lbl_acqestimate",
            "btn_cancel",
            "btn_secom_acquire",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.dialog, attribute))

        self.assertFalse(self.dialog.gauge_acq.IsShown())
        self.assertEqual(self.dialog.gauge_acq.GetValue(), 50)
        self.assertEqual(self.dialog.GetTitle(), "Image Acquisition")
        self.assertEqual(
            self.dialog.txt_filename.GetValue(),
            "Select a destination file",
        )
        self.assertEqual(self.dialog.txt_destination.GetValue(), "...")

    def test_default_button_rejects_contrasting_text(self) -> None:
        """Reject contrasting text on the default button face."""
        with self.assertRaises(ValueError):
            self.dialog._text_button(
                self.dialog,
                "Invalid",
                height=24,
                contrast=True,
            )


if __name__ == "__main__":
    unittest.main()
