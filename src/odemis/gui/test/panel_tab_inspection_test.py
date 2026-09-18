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

from odemis.gui.comp.viewport import (
    AngularResolvedViewport,
    AngularSpectrumViewport,
    ChronographViewport,
    LineSpectrumViewport,
    MicroscopeViewport,
    PointSpectrumViewport,
    TemporalSpectrumViewport,
    ThetaViewport,
)
from odemis.gui.layout.components.panel_tab_inspection import PnlTabInspection
from odemis.gui.layout import theme


class PnlTabInspectionTest(unittest.TestCase):
    """Verify the pure-Python inspection layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh inspection panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabInspection(self.frame)

    def tearDown(self) -> None:
        """Destroy the inspection panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "btn_open_image",
            "ana_toolbar",
            "lbl_inspection_view_all",
            "btn_inspection_view_all",
            "lbl_inspection_view_tl",
            "btn_inspection_view_tl",
            "lbl_inspection_view_tr",
            "btn_inspection_view_tr",
            "lbl_inspection_view_bl",
            "btn_inspection_view_bl",
            "lbl_inspection_view_br",
            "btn_inspection_view_br",
            "btn_log",
            "pnl_inspection_grid",
            "vp_inspection_tl",
            "vp_inspection_tr",
            "vp_inspection_bl",
            "vp_inspection_br",
            "vp_angular",
            "vp_inspection_plot",
            "vp_linespec",
            "vp_temporalspec",
            "vp_timespec",
            "vp_angular_pol",
            "vp_angularspec",
            "vp_thetaspec",
            "scr_win_right",
            "fp_fileinfo",
            "pnl_inspection_streams",
            "btn_export",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertEqual(
            self.panel.pnl_inspection_grid.viewports,
            (
                self.panel.vp_inspection_tl,
                self.panel.vp_inspection_tr,
                self.panel.vp_inspection_bl,
                self.panel.vp_inspection_br,
                self.panel.vp_angular,
                self.panel.vp_inspection_plot,
                self.panel.vp_linespec,
                self.panel.vp_temporalspec,
                self.panel.vp_timespec,
                self.panel.vp_angular_pol,
                self.panel.vp_angularspec,
                self.panel.vp_thetaspec,
            ),
        )
        self.assertIsInstance(self.panel.vp_inspection_tl, MicroscopeViewport)
        self.assertIsInstance(self.panel.vp_inspection_tr, MicroscopeViewport)
        self.assertIsInstance(self.panel.vp_inspection_bl, MicroscopeViewport)
        self.assertIsInstance(self.panel.vp_inspection_br, MicroscopeViewport)
        self.assertIsInstance(self.panel.vp_angular, AngularResolvedViewport)
        self.assertIsInstance(self.panel.vp_inspection_plot, PointSpectrumViewport)
        self.assertIsInstance(self.panel.vp_linespec, LineSpectrumViewport)
        self.assertIsInstance(self.panel.vp_temporalspec, TemporalSpectrumViewport)
        self.assertIsInstance(self.panel.vp_timespec, ChronographViewport)
        self.assertIsInstance(self.panel.vp_angular_pol, AngularResolvedViewport)
        self.assertIsInstance(self.panel.vp_angularspec, AngularSpectrumViewport)
        self.assertIsInstance(self.panel.vp_thetaspec, ThetaViewport)

        for viewport in self.panel.pnl_inspection_grid.viewports[4:]:
            with self.subTest(viewport=viewport):
                self.assertFalse(viewport.IsShown())

        self.assertTrue(self.panel.pnl_inspection_streams.btn_add_stream.IsShown())
        self.assertEqual(self.panel.btn_open_image.GetLabel(), "Select image...")
        self.assertEqual(self.panel.btn_export.GetLabel(), "EXPORT IMAGE")
        self.assertEqual(
            self.panel.btn_open_image.GetForegroundColour(),
            wx.Colour(theme.button_text),
        )
        self.assertEqual(
            self.panel.btn_export.GetForegroundColour(),
            wx.Colour(theme.button_text_contrast),
        )


if __name__ == "__main__":
    unittest.main()
