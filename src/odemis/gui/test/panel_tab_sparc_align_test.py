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

from odemis.gui.comp.buttons import GraphicRadioButton
from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.comp.slider import UnitFloatSlider
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import ARLiveViewport
from odemis.gui.layout import PnlTabSparcAlign
from odemis.gui.layout.constants.themes import DARK


class PnlTabSparcAlignTest(unittest.TestCase):
    """Verify the pure-Python SPARC alignment layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh SPARC alignment panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabSparcAlign(self.frame)

    def tearDown(self) -> None:
        """Destroy the SPARC alignment panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller-accessed controls with their expected types."""
        attributes = (
            "pnl_alignment_btns",
            "btn_align_chamber",
            "btn_align_mirror",
            "btn_align_fiber",
            "pnl_sparc_trans",
            "mirror_align_slider_mirror_x",
            "mirror_align_slider_mirror_y",
            "lbl_my",
            "lbl_py",
            "lbl_px",
            "lbl_mx",
            "mirror_align_btn_p_mirror_y",
            "mirror_align_btn_m_mirror_y",
            "mirror_align_btn_p_mirror_x",
            "mirror_align_btn_m_mirror_x",
            "pnl_sparc_rot",
            "mirror_align_slider_mirror_r",
            "lbl_pry",
            "lbl_mry",
            "lbl_prz",
            "lbl_mrz",
            "mirror_align_btn_m_mirror_ry",
            "mirror_align_btn_p_mirror_ry",
            "mirror_align_btn_m_mirror_rz",
            "mirror_align_btn_p_mirror_rz",
            "pnl_fibaligner",
            "mirror_align_slider_fibaligner",
            "lbl_pfy",
            "lbl_mfy",
            "lbl_pfx",
            "lbl_mfx",
            "mirror_align_btn_p_fibaligner_y",
            "mirror_align_btn_m_fibaligner_y",
            "mirror_align_btn_m_fibaligner_x",
            "mirror_align_btn_p_fibaligner_x",
            "btn_log",
            "vp_sparc_align",
            "scr_win_right",
            "fp_ma_settings_ar",
            "pnl_sparc_align_streams",
            "fp_ma_settings_spectrum",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertIsInstance(self.panel.btn_align_chamber, GraphicRadioButton)
        self.assertIsInstance(self.panel.btn_align_mirror, GraphicRadioButton)
        self.assertIsInstance(self.panel.btn_align_fiber, GraphicRadioButton)
        self.assertIsInstance(
            self.panel.mirror_align_slider_mirror_x, UnitFloatSlider
        )
        self.assertIsInstance(
            self.panel.mirror_align_slider_fibaligner, UnitFloatSlider
        )
        self.assertIsInstance(self.panel.vp_sparc_align, ARLiveViewport)
        self.assertIsInstance(self.panel.fp_ma_settings_ar, FoldPanelItem)
        self.assertIsInstance(self.panel.pnl_sparc_align_streams, StreamBar)

        self.assertEqual(
            self.panel.btn_align_chamber.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )
        self.assertEqual(
            self.panel.mirror_align_btn_p_mirror_x.GetForegroundColour(),
            wx.Colour(DARK.button_text),
        )


if __name__ == "__main__":
    unittest.main()
