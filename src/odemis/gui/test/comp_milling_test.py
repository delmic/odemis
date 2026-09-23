# -*- coding: utf-8 -*-
"""Tests for milling pattern controls and their parameter connections.

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

This file is part of Odemis, licensed under the GNU General Public License v2.
See the LICENSE.txt file for details.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import wx

import odemis.gui.test as test
from odemis import model
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.gui.comp.text import IntegerTextCtrl, UnitFloatCtrl
from odemis.gui.cont.milling import MillingTaskController


class MillingTaskPanelTestCase(test.GuiTestCase):
    """Test controls for the ruler and other milling patterns."""

    frame_class = test.test_gui.ButtonTestFrame

    def setUp(self) -> None:
        """Create the milling controls without microscope hardware."""
        super().setUp()
        self.tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        self.tasks["Ruler"].selected = True
        self.settings_panel = wx.Panel(self.panel)
        self.scroll = wx.ScrolledWindow(self.panel)
        # Construct the settings section without a microscope or acquired image.
        self.controller = MillingTaskController.__new__(MillingTaskController)
        self.controller._panel = SimpleNamespace(
            pnl_patterns=self.settings_panel, scr_win_right=self.scroll, Layout=self.panel.Layout)
        self.controller.milling_tasks = self.tasks
        self.controller.draw_milling_tasks = Mock()
        self.controller._update_spot_size_validation_message = Mock()
        self.controller._update_mill_btn = Mock()
        self.controller._update_pattern_panels()
        test.gui_loop()

    def tearDown(self) -> None:
        """Destroy the GUI controls created for a test."""
        self.settings_panel.Destroy()
        self.scroll.Destroy()
        test.gui_loop()
        super().tearDown()

    def test_pattern_specific_controls(self) -> None:
        """Show controls specific to composite milling patterns."""
        controls = self.controller.controls
        ruler_panel = controls["Ruler"]["panel"]
        self.assertIsInstance(ruler_panel.ctrl_dict["height"], UnitFloatCtrl)
        self.assertIsInstance(ruler_panel.ctrl_dict["num_notches"], IntegerTextCtrl)
        self.assertIn("num_notches_connector", controls["Ruler"])
        self.assertIsInstance(ruler_panel.ctrl_dict["spot_size_correction"], UnitFloatCtrl)
        self.assertIn("spot_size_correction_connector", controls["Ruler"])
        self.assertNotIn("rotation", ruler_panel.ctrl_dict)
        notch_panel = controls["Notch"]["panel"]
        self.assertEqual(
            set(notch_panel.pattern_parameters),
            {"width", "height", "depth", "gap", "thickness", "offset", "mirrored",
             "spot_size_correction"})
        self.assertTrue(all(isinstance(notch_panel.ctrl_dict[param], UnitFloatCtrl)
                            for param in ("width", "height", "depth", "gap", "thickness", "offset",
                                          "spot_size_correction")))
        notch_panel.ctrl_dict["offset"].SetValue(0)
        test.gui_loop()
        self.assertEqual(notch_panel.ctrl_dict["offset"].get_value_str(), "0 µm")
        self.assertIsInstance(notch_panel.ctrl_dict["mirrored"], wx.CheckBox)
        self.assertIn("mirrored_connector", controls["Notch"])
        mirror = notch_panel.ctrl_dict["mirrored"]
        mirror.SetValue(True)
        event = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, mirror.Id)
        event.SetEventObject(mirror)
        mirror.GetEventHandler().ProcessEvent(event)
        self.assertTrue(self.tasks["Notch"].patterns[0].mirrored.value)
        waffle_panel = controls["Waffle Trench"]["panel"]
        self.assertEqual(
            set(waffle_panel.pattern_parameters),
            {"top_width", "top_height", "bottom_width", "bottom_height",
             "depth", "spacing", "spot_size_correction"})
        for name in ("Microexpansion", "Rough Milling 01", "Polishing 01"):
            self.assertNotIn("num_notches", controls[name]["panel"].ctrl_dict)
            self.assertEqual(set(controls[name]["panel"].pattern_parameters),
                             {"width", "height", "depth", "spacing", "spot_size_correction"})

    def test_edit_ruler_parameters_updates_pattern_and_preview(self) -> None:
        """Apply ruler control edits and redraw the preview."""
        controls = self.controller.controls["Ruler"]["panel"].ctrl_dict
        ruler = self.tasks["Ruler"].patterns[0]
        self.controller.draw_milling_tasks.reset_mock()
        for param, value in (("num_notches", 16), ("height", 15e-6), ("spot_size_correction", 20e-9)):
            ctrl = controls[param]
            ctrl.SetValue(value)
            event = wx.CommandEvent(wx.EVT_COMMAND_ENTER.typeId, ctrl.Id)
            event.SetEventObject(ctrl)
            ctrl.GetEventHandler().ProcessEvent(event)
            test.gui_loop()
            self.assertEqual(getattr(ruler, param).value, value)
        self.assertEqual(len(ruler.generate()), 32)
        self.assertEqual(self.controller.draw_milling_tasks.call_count, 3)
        self.controller._update_spot_size_validation_message.assert_called()
        self.controller._update_mill_btn.assert_called()
        ruler.num_notches.value = 11
        test.gui_loop()
        self.assertEqual(controls["num_notches"].GetValue(), 11)


class MillingSpotSizeValidationTestCase(unittest.TestCase):
    """Check corrections against generated notch dimensions without a microscope."""

    def setUp(self) -> None:
        """Create a selected ruler task and a checked feature."""
        self.tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        self.task = self.tasks["Ruler"]
        self.task.selected = True
        self.ruler = self.task.patterns[0]
        feature = SimpleNamespace(name=model.StringVA("Feature 1"), milling_tasks=self.tasks)
        self.controller = MillingTaskController.__new__(MillingTaskController)
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(features=model.ListVA([feature])))
        self.feature_list = Mock()
        self.feature_list.GetCount.return_value = 1
        self.feature_list.IsChecked.return_value = True
        self.controller._panel = SimpleNamespace(workflow_features_chk_list=self.feature_list)

    def test_valid_correction_is_accepted(self) -> None:
        """Accept a correction smaller than every notch dimension."""
        self.ruler.spot_size_correction.value = 20e-9
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())

    def test_correction_equal_to_notch_height_is_invalid(self) -> None:
        """Reject a correction equal to the notch height."""
        self.ruler.spot_size_correction.value = self.ruler.generate()[0].height.value
        self.assertEqual(self.controller._get_invalid_spot_size_correction(), ("Feature 1", "Ruler"))

    def test_correction_exceeding_notch_height_is_invalid(self) -> None:
        """Reject a correction greater than the notch height."""
        self.ruler.spot_size_correction.value = 2 * self.ruler.generate()[0].height.value
        self.assertEqual(self.controller._get_invalid_spot_size_correction(), ("Feature 1", "Ruler"))

    def test_correction_exceeding_short_notch_width_is_invalid(self) -> None:
        """Reject a correction greater than a short notch width."""
        self.ruler.width.value = 20e-9  # Long ticks are 20 nm, short ticks are 15 nm.
        self.ruler.spot_size_correction.value = 16e-9
        self.assertEqual(self.controller._get_invalid_spot_size_correction(), ("Feature 1", "Ruler"))

    def test_increasing_notch_count_can_invalidate_correction(self) -> None:
        """Revalidate correction when the notch count changes."""
        self.ruler.spot_size_correction.value = 100e-9
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())
        self.ruler.num_notches.value = 21
        self.assertEqual(self.controller._get_invalid_spot_size_correction(), ("Feature 1", "Ruler"))

    def test_increasing_height_can_make_correction_valid(self) -> None:
        """Accept the correction when a height increase makes it valid."""
        self.ruler.spot_size_correction.value = 200e-9
        self.assertEqual(self.controller._get_invalid_spot_size_correction(), ("Feature 1", "Ruler"))
        self.ruler.height.value = 20e-6
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())

    def test_disabled_task_is_ignored(self) -> None:
        """Ignore invalid corrections in disabled tasks."""
        self.ruler.spot_size_correction.value = 1e-6
        self.assertEqual(self.controller._get_invalid_spot_size_correction(), ("Feature 1", "Ruler"))
        self.task.selected = False
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())

    def test_unchecked_feature_is_ignored(self) -> None:
        """Ignore invalid corrections for unchecked features."""
        self.ruler.spot_size_correction.value = 1e-6
        self.assertEqual(self.controller._get_invalid_spot_size_correction(), ("Feature 1", "Ruler"))
        self.feature_list.IsChecked.return_value = False
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())


if __name__ == "__main__":
    unittest.main()
