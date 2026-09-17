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
import wx.adv
import wx.html

from odemis.gui.comp.slider import UnitFloatSlider
from odemis.gui.layout import PnlTabSecomAlign
from odemis.gui.layout.constants.themes import DARK


class PnlTabSecomAlignTest(unittest.TestCase):
    """Verify the pure-Python SECOM alignment layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh SECOM alignment panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabSecomAlign(self.frame)

    def tearDown(self) -> None:
        """Destroy the SECOM alignment panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "lens_align_slider_aligner",
            "pnl_ab_align",
            "lbl_mb",
            "lbl_pa",
            "lbl_ma",
            "lbl_pb",
            "lens_align_btn_m_aligner_b",
            "lens_align_btn_p_aligner_a",
            "lens_align_btn_m_aligner_a",
            "lens_align_btn_p_aligner_b",
            "pnl_xy_align",
            "lbl_py",
            "lbl_my",
            "lbl_px",
            "lbl_mx",
            "lens_align_btn_p_aligner_y",
            "lens_align_btn_m_aligner_y",
            "lens_align_btn_m_aligner_x",
            "lens_align_btn_p_aligner_x",
            "pnl_move_to_center",
            "lens_align_lbl_approc_center",
            "lens_align_btn_to_center",
            "pnl_align_tools",
            "btn_auto_center",
            "lbl_auto_center",
            "gauge_auto_center",
            "btn_fine_align",
            "lbl_fine_align",
            "gauge_fine_align",
            "html_alignment_doc",
            "btn_log",
            "vp_align_ccd",
            "main_buttons",
            "lens_align_btn_sem",
            "lens_align_btn_opt",
            "cmb_lens_align_presets",
            "scr_win_right",
            "pnl_opt_streams",
            "pnl_sem_streams",
            "pnl_sem_toolbar",
            "lens_align_tb",
            "vp_align_sem",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertIsInstance(
            self.panel.lens_align_slider_aligner,
            UnitFloatSlider,
        )
        self.assertIsInstance(self.panel.html_alignment_doc, wx.html.HtmlWindow)
        self.assertIsInstance(
            self.panel.cmb_lens_align_presets,
            wx.adv.OwnerDrawnComboBox,
        )
        self.assertFalse(self.panel.pnl_ab_align.IsShown())
        self.assertFalse(self.panel.pnl_xy_align.IsShown())
        self.assertFalse(self.panel.pnl_move_to_center.IsShown())
        self.assertFalse(self.panel.pnl_align_tools.IsShown())
        self.assertFalse(self.panel.gauge_auto_center.IsShown())
        self.assertFalse(self.panel.gauge_fine_align.IsShown())
        self.assertFalse(self.panel.main_buttons.IsShown())
        self.assertFalse(self.panel.cmb_lens_align_presets.IsShown())
        self.assertEqual(self.panel.gauge_auto_center.GetRange(), 100)
        self.assertEqual(self.panel.gauge_fine_align.GetRange(), 100)
        self.assertEqual(self.panel.lbl_auto_center.GetLabel(), "~ 30 seconds")
        self.assertEqual(self.panel.lbl_fine_align.GetLabel(), "~ 30 seconds")
        self.assertEqual(
            self.panel.lens_align_btn_m_aligner_x.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )
        self.assertEqual(
            self.panel.lens_align_btn_sem.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )


if __name__ == "__main__":
    unittest.main()
