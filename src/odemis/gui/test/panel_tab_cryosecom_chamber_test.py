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

from odemis.gui.layout.components.panel_tab_cryosecom_chamber import PnlTabCryosecomChamber
from odemis.gui.layout.constants.strings import DEFAULT_DESTINATION_FILE


class PnlTabCryosecomChamberTest(unittest.TestCase):
    """Verify the pure-Python CryoSECOM chamber layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh chamber panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabCryosecomChamber(self.frame)

    def tearDown(self) -> None:
        """Destroy the chamber panel host frame."""
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "txt_projectpath",
            "btn_change_folder",
            "btn_load_project",
            "btn_switch_sem_imaging",
            "btn_switch_fm_imaging",
            "btn_switch_milling",
            "btn_switch_fib_view_fm",
            "btn_switch_fib_imaging",
            "btn_switch_grid1",
            "btn_switch_grid2",
            "gauge_move",
            "btn_cancel",
            "pnl_ref_msg",
            "txt_warning",
            "btn_switch_advanced",
            "pnl_advanced_align",
            "ctrl_rx",
            "stage_align_slider_aligner",
            "stage_align_btn_p_aligner_y",
            "stage_align_btn_m_aligner_y",
            "stage_align_btn_m_aligner_x",
            "stage_align_btn_p_aligner_x",
            "stage_align_btn_p_aligner_z",
            "stage_align_btn_m_aligner_z",
            "btn_switch_align",
            "pnl_temperature",
            "ctrl_sample_heater",
            "ctrl_sample_target_tmp",
            "btn_log",
            "vp_overview_map",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertFalse(self.panel.btn_cancel.IsEnabled())
        self.assertFalse(self.panel.pnl_temperature.IsShown())
        self.assertFalse(self.panel.pnl_advanced_align.IsShown())
        self.assertEqual(self.panel.gauge_move.GetRange(), 100)
        self.assertEqual(
            self.panel.txt_projectpath.GetValue(),
            DEFAULT_DESTINATION_FILE,
        )


if __name__ == "__main__":
    unittest.main()
