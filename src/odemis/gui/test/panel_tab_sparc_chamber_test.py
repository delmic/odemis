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

from odemis.gui.comp.sparc2_sample_settings_panel import Sparc2SampleSettingsPanel
from odemis.gui.layout.components.panel_tab_sparc_chamber import PnlTabSparcChamber
from odemis.gui.layout import theme
from odemis.gui.model import Sparc2MainGUIData
from odemis.gui.model.main_gui_data import parse_sparc2_samples
from odemis.gui.test import gui_loop


class PnlTabSparcChamberTest(unittest.TestCase):
    """Verify the pure-Python SPARC2 chamber layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh SPARC2 chamber panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabSparcChamber(self.frame)

    def tearDown(self) -> None:
        """Destroy the chamber panel host frame."""
        gui_loop()  # Process the pending events while the widgets still exist
        self.frame.Destroy()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "btn_switch_mirror",
            "pnl_move",
            "gauge_move",
            "btn_cancel",
            "pnl_ref_msg",
            "txt_warning",
            "btn_log",
            "vp_chamber",
            "scr_win_right",
            "pnl_streams",
            "pnl_sample",
            "sample_settings_panel",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertFalse(self.panel.btn_cancel.IsEnabled())
        self.assertFalse(self.panel.pnl_sample.IsShown())
        self.assertEqual(self.panel.gauge_move.GetRange(), 100)
        self.assertEqual(self.panel.gauge_move.GetValue(), 0)
        self.assertEqual(
            self.panel.btn_switch_mirror.GetForegroundColour(),
            wx.Colour(theme.button_text),
        )
        self.assertEqual(
            self.panel.btn_cancel.GetForegroundColour(),
            wx.Colour(theme.button_text),
        )

    def test_sample_settings(self):
        """Show the sample entries, with the standard values of the selected sample type."""
        main_data = Sparc2MainGUIData(None)
        main_data.samples.value = parse_sparc2_samples({
            "wafer": [{"shape": "circle", "center": [0, 0], "radius": 75e-3, "materials": ["SiC", "Si"]},
                      {"shape": "circle", "center": [0, 0], "radius": 100e-3, "materials": ["Si"]}],
            "chip": [{"shape": "rectangle", "center": [0, 0], "size": [10e-3, 8e-3]}],
        })
        sample_settings = Sparc2SampleSettingsPanel(self.panel, main_data)
        self.assertTrue(self.panel.pnl_sample.IsShown())
        self.assertEqual(sample_settings.sample_type_ctrl.GetStrings(), ["wafer", "chip"])

        self.assertFalse(sample_settings.dimensions_ctrl.IsShown())

        # Selecting a sample type selects its first standard dimensions and material
        select_item(sample_settings.sample_type_ctrl, 0)
        wafer = main_data.samples.value["wafer"]
        self.assertIs(main_data.current_sample.value, wafer)
        self.assertIs(main_data.current_sample_dimension.value, wafer.dimensions[0])
        self.assertEqual(main_data.current_sample_material.value, "SiC")
        self.assertEqual(sample_settings.dimensions_ctrl.GetValue(), "Ø 150 mm")
        self.assertEqual(sample_settings.material_ctrl.GetStrings(), ["SiC", "Si"])

        # Only the materials of the selected dimensions are proposed
        select_item(sample_settings.dimensions_ctrl, 1)
        self.assertEqual(main_data.current_sample_material.value, "Si")
        self.assertEqual(sample_settings.material_ctrl.GetStrings(), ["Si"])

        # No standard material => the material entry is hidden
        select_item(sample_settings.sample_type_ctrl, 1)
        self.assertIsNone(main_data.current_sample_material.value)
        self.assertEqual(sample_settings.dimensions_ctrl.GetValue(), "10 x 8 mm")
        self.assertFalse(sample_settings.material_ctrl.IsShown())


def select_item(ctrl: wx.ComboBox, index: int) -> None:
    """
    Select an item of the combobox, as if done by the user.
    :param ctrl: the combobox.
    :param index: the position of the item to select.
    """
    ctrl.SetSelection(index)
    evt = wx.CommandEvent(wx.wxEVT_COMBOBOX, ctrl.GetId())
    evt.SetEventObject(ctrl)
    ctrl.GetEventHandler().ProcessEvent(evt)


if __name__ == "__main__":
    unittest.main()
