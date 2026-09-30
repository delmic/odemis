# -*- coding: utf-8 -*-
"""Tests for milling pattern controls and their parameter connections.

Created on Sep 2026

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

This file is part of Odemis, licensed under the GNU General Public License v2.
See the LICENSE.txt file for details.
"""

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

import odemis.gui.test as test
from odemis import model
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.gui.comp.overlay._constants import (
    MILLING_OVERLAY_ACTIVE_OPACITY,
    MILLING_OVERLAY_INACTIVE_OPACITY,
    MILLING_OVERLAY_LINE_WIDTH,
)
from odemis.gui.comp.overlay.milling import MillingPatternOverlay
from odemis.gui.comp.overlay.rectangle import MillingRectangleOverlay, RectangleOverlay
from odemis.gui.comp.text import IntegerTextCtrl, UnitFloatCtrl
from odemis.gui.cont.milling import (
    MILLING_THEME_COLORS,
    PATTERN_MOVE_WARNING_TIMEOUT,
    MillingTaskController,
    _get_milling_colour,
)


class MillingTaskPanelTestCase(test.GuiTestCase):
    """Test milling pattern controls and overlay interaction."""

    frame_class = test.test_gui.ButtonTestFrame

    def setUp(self) -> None:
        """Create the milling controls without microscope hardware."""
        super().setUp()
        self.tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        self.tasks["Ruler"].selected = True
        self.settings_panel = wx.Panel(self.panel)
        self.scroll = wx.ScrolledWindow(self.panel)
        self.controller = MillingTaskController.__new__(MillingTaskController)
        self.controller._panel = SimpleNamespace(
            pnl_patterns=self.settings_panel,
            scr_win_right=self.scroll,
            Layout=self.panel.Layout,
        )
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
        """Show controls specific to the ruler pattern."""
        controls = self.controller.controls
        ruler_panel = controls["Ruler"]["panel"]
        self.assertIsInstance(ruler_panel.ctrl_dict["height"], UnitFloatCtrl)
        self.assertIsInstance(
            ruler_panel.ctrl_dict["num_graduations"], IntegerTextCtrl)
        self.assertIn("num_graduations_connector", controls["Ruler"])
        self.assertIsInstance(
            ruler_panel.ctrl_dict["spot_size_correction"], UnitFloatCtrl)
        self.assertIn("spot_size_correction_connector", controls["Ruler"])
        self.assertNotIn("rotation", ruler_panel.ctrl_dict)
        notch_panel = controls["Notch"]["panel"]
        self.assertEqual(
            set(notch_panel.pattern_parameters),
            {
                "width",
                "height",
                "depth",
                "gap",
                "thickness",
                "offset",
                "mirrored",
                "spot_size_correction",
            },
        )
        self.assertTrue(all(
            isinstance(notch_panel.ctrl_dict[param], UnitFloatCtrl)
            for param in (
                "width",
                "height",
                "depth",
                "gap",
                "thickness",
                "offset",
                "spot_size_correction",
            )
        ))
        self.assertNotIn("overlap", notch_panel.ctrl_dict)
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
        for name in ("Microexpansion", "Rough Milling 01", "Polishing 01"):
            panel = controls[name]["panel"]
            self.assertNotIn("num_graduations", panel.ctrl_dict)
            self.assertEqual(
                set(panel.pattern_parameters),
                {"width", "height", "depth", "spacing", "spot_size_correction"},
            )

    def test_edit_ruler_parameters_updates_pattern_and_preview(self) -> None:
        """Apply ruler control edits and redraw the preview."""
        controls = self.controller.controls["Ruler"]["panel"].ctrl_dict
        ruler = self.tasks["Ruler"].patterns[0]
        self.controller.draw_milling_tasks.reset_mock()
        for param, value in (
            ("num_graduations", 16),
            ("height", 15e-6),
            ("spot_size_correction", 20e-9),
        ):
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
        ruler.num_graduations.value = 11
        test.gui_loop()
        self.assertEqual(controls["num_graduations"].GetValue(), 11)

    def test_pattern_movement_selection_and_recenter(self) -> None:
        """Move patterns and recenter only displayed patterns on the feature."""
        task_list = wx.CheckListBox(self.panel, choices=list(self.tasks))
        task_list.SetSelection(task_list.FindString("Microexpansion"))
        move_all = wx.CheckBox(self.panel)
        self.controller._panel.milling_task_chk_list = task_list
        self.controller._panel.chk_move_all_patterns = move_all

        microexpansion = self.tasks["Microexpansion"].patterns[0]
        self.assertEqual(
            self.controller._get_patterns_for_manual_move(), [microexpansion])
        self.assertIs(self.controller._get_highlighted_pattern(), microexpansion)

        self.tasks["Rough Milling 02"].selected = False
        move_all.SetValue(True)
        visible_patterns = [
            pattern for task in self.tasks.values() if task.selected
            for pattern in task.patterns
        ]
        hidden_pattern = self.tasks["Rough Milling 02"].patterns[0]
        self.assertEqual(
            self.controller._get_patterns_for_manual_move(), visible_patterns)

        microexpansion.center.value = (2e-6, -3e-6)
        other_pattern = self.tasks["Rough Milling 01"].patterns[0]
        other_pattern.center.value = (7e-6, 5e-6)
        hidden_pattern.center.value = (11e-6, 13e-6)
        target = (-4e-6, 6e-6)
        self.controller._tab_data = SimpleNamespace(main=Mock())
        with patch("odemis.gui.cont.milling.save_project"):
            self.controller._move_patterns(
                visible_patterns, target, microexpansion)
        self.assertEqual(microexpansion.center.value, target)
        self.assertEqual(other_pattern.center.value, (1e-6, 14e-6))
        self.assertEqual(hidden_pattern.center.value, (11e-6, 13e-6))

        notch_pattern = self.tasks["Notch"].patterns[0]
        polishing_pattern = self.tasks["Polishing 02"].patterns[0]
        marker = (2e-6, -3e-6)
        with patch("odemis.gui.cont.milling.save_project"):
            self.controller._move_patterns(
                [notch_pattern, polishing_pattern],
                marker,
                use_feature_offsets=True,
            )
        self.assertEqual(
            notch_pattern.center.value,
            notch_pattern.get_center_at_feature(marker),
        )
        self.assertEqual(polishing_pattern.center.value, marker)

        task_list.SetSelection(task_list.FindString("Notch"))
        move_all.SetValue(False)
        self.assertEqual(self.controller._get_patterns_for_manual_move(), [])

        for pattern in visible_patterns:
            pattern.center.value = (20e-6, 20e-6)
        hidden_center = hidden_pattern.center.value
        feature = SimpleNamespace(
            milling_feature_offset=model.TupleVA(marker),
            milling_tasks=self.tasks,
        )
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(currentFeature=model.VigilantAttribute(feature)))
        self.controller.allow_milling_pattern_move = True
        with patch("odemis.gui.cont.milling.save_project"):
            self.controller._snap_patterns_to_feature(None)
        for pattern in visible_patterns:
            self.assertEqual(
                pattern.center.value,
                pattern.get_center_at_feature(marker),
            )
        self.assertEqual(hidden_pattern.center.value, hidden_center)

    def test_ctrl_click_selected_list_row_deselects_pattern(self) -> None:
        """Clear a highlighted pattern only by Ctrl-clicking its selected row."""
        selected_index = list(self.tasks).index("Microexpansion")
        task_list = Mock()
        task_list.HitTest.return_value = selected_index
        task_list.GetSelection.return_value = selected_index
        self.controller._panel.milling_task_chk_list = task_list
        self.controller._active_spot_size_pattern = (
            self.tasks["Microexpansion"].patterns[0]
        )
        self.controller.draw_milling_tasks.reset_mock()
        list_mouse_event = Mock()
        list_mouse_event.ControlDown.return_value = True

        self.controller._on_milling_task_mouse_down(list_mouse_event)

        task_list.SetSelection.assert_called_once_with(wx.NOT_FOUND)
        self.assertIsNone(self.controller._active_spot_size_pattern)
        self.controller.draw_milling_tasks.assert_called_once_with()
        list_mouse_event.Skip.assert_not_called()

    def test_ctrl_click_other_list_row_preserves_pattern_selection(self) -> None:
        """Let Ctrl-clicking another row follow normal list selection behavior."""
        selected_index = list(self.tasks).index("Microexpansion")
        other_index = list(self.tasks).index("Rough Milling 01")
        task_list = Mock()
        task_list.HitTest.return_value = other_index
        task_list.GetSelection.return_value = selected_index
        self.controller._panel.milling_task_chk_list = task_list
        active_pattern = self.tasks["Microexpansion"].patterns[0]
        self.controller._active_spot_size_pattern = active_pattern
        self.controller.draw_milling_tasks.reset_mock()
        list_mouse_event = Mock()
        list_mouse_event.ControlDown.return_value = True

        self.controller._on_milling_task_mouse_down(list_mouse_event)

        task_list.SetSelection.assert_not_called()
        self.assertIs(self.controller._active_spot_size_pattern, active_pattern)
        self.controller.draw_milling_tasks.assert_not_called()
        list_mouse_event.Skip.assert_called_once_with()

    def test_ctrl_click_canvas_preserves_pattern_selection(self) -> None:
        """Do not clear the highlighted pattern when Ctrl-clicking the canvas."""
        task_list = wx.CheckListBox(self.panel, choices=list(self.tasks))
        selected_index = task_list.FindString("Microexpansion")
        task_list.Check(selected_index, True)
        task_list.SetSelection(selected_index)
        active_pattern = self.tasks["Microexpansion"].patterns[0]
        self.controller._panel.milling_task_chk_list = task_list
        self.controller._active_spot_size_pattern = active_pattern
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(currentFeature=SimpleNamespace(value=None)))
        self.controller.draw_milling_tasks.reset_mock()
        canvas_event = Mock()
        canvas_event.ControlDown.return_value = True
        canvas_event.ShiftDown.return_value = False

        self.controller.on_mouse_down(canvas_event)

        self.assertEqual(task_list.GetSelection(), selected_index)
        self.assertTrue(task_list.IsChecked(selected_index))
        self.assertIs(self.controller._active_spot_size_pattern, active_pattern)
        self.controller.draw_milling_tasks.assert_not_called()
        canvas_event.Skip.assert_called_once_with()

    def test_pattern_overlay_styles(self) -> None:
        """Give each default task a unique bright color and a thin overlay."""
        canvas = Mock()
        canvas.get_half_buffer_size.return_value = (0, 0)
        pattern_overlay = MillingPatternOverlay(canvas)
        self.assertIs(pattern_overlay.shape_cls, RectangleOverlay)

        colors = [
            _get_milling_colour(task, index)
            for index, task in enumerate(self.tasks.values())
        ]
        self.assertEqual(len(colors), len(set(colors)))
        self.assertEqual(colors, [task.color for task in self.tasks.values()])

        task_without_color = self.tasks["Microexpansion"]
        task_without_color.color = None
        self.assertEqual(
            _get_milling_colour(task_without_color, 0), MILLING_THEME_COLORS[0])

        rectangle = MillingRectangleOverlay(Mock(), colour=colors[0])
        context = Mock()
        with patch(
            "odemis.gui.comp.overlay.rectangle.RectangleOverlay.draw"
        ) as draw:
            rectangle.draw(context)
        draw.assert_called_once_with(
            context,
            shift=(0, 0),
            scale=1.0,
            line_width=MILLING_OVERLAY_LINE_WIDTH,
        )
        self.assertEqual(MILLING_OVERLAY_LINE_WIDTH, 3)
        self.assertAlmostEqual(
            rectangle.colour[3], MILLING_OVERLAY_ACTIVE_OPACITY)
        inactive_rectangle = MillingRectangleOverlay(
            Mock(), colour=colors[1], opacity=MILLING_OVERLAY_INACTIVE_OPACITY)
        self.assertAlmostEqual(
            inactive_rectangle.colour[3], MILLING_OVERLAY_INACTIVE_OPACITY)

    def test_pattern_labels_only_for_highlighted_task(self) -> None:
        """Show names and dimensions only for the highlighted pattern row."""
        rough = self.tasks["Rough Milling 01"]
        notch = self.tasks["Notch"]
        rough.selected = True
        notch.selected = True
        self.controller.milling_tasks = {
            "Rough Milling 01": rough,
            "Notch": notch,
        }
        task_list = wx.CheckListBox(
            self.panel, choices=list(self.controller.milling_tasks))
        self.controller._panel.milling_task_chk_list = task_list
        self.controller.selected_tasks = SimpleNamespace(
            value=list(self.controller.milling_tasks))
        reference_image = SimpleNamespace(metadata={model.MD_POS: (0, 0)})
        feature = SimpleNamespace(reference_image=reference_image)
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(currentFeature=SimpleNamespace(value=feature)))
        self.controller.canvas = Mock()
        self.controller.rectangles_overlay = Mock()
        self.controller.rectangles_overlay._shapes = SimpleNamespace(value=[])
        self.controller._active_spot_size_pattern = None
        self.controller._on_shapes_update = Mock()

        task_list.SetSelection(task_list.FindString("Rough Milling 01"))
        with patch(
            "odemis.gui.cont.milling.rectangle_pattern_to_shape",
            return_value=Mock(),
        ) as to_shape:
            MillingTaskController.draw_milling_tasks.__wrapped__(self.controller)

        rough_calls = [
            item for item in to_shape.call_args_list
            if item.kwargs["colour"] == rough.color
        ]
        notch_calls = [
            item for item in to_shape.call_args_list
            if item.kwargs["colour"] == notch.color
        ]
        self.assertTrue(all(item.kwargs["show_dimensions"] for item in rough_calls))
        self.assertTrue(all(
            item.kwargs["opacity"] == MILLING_OVERLAY_ACTIVE_OPACITY
            for item in rough_calls
        ))
        self.assertTrue(all(item.kwargs["name"] is None for item in rough_calls))
        self.assertTrue(all(item.kwargs["name"] is None for item in notch_calls))
        self.assertTrue(all(
            not item.kwargs["show_dimensions"] for item in notch_calls))
        self.assertTrue(all(
            item.kwargs["opacity"] == MILLING_OVERLAY_INACTIVE_OPACITY
            for item in notch_calls
        ))
        rough_shape_count = len(rough.patterns[0].generate())
        self.assertTrue(all(
            item.kwargs["colour"] == rough.color
            for item in to_shape.call_args_list[-rough_shape_count:]
        ))
        self.controller.rectangles_overlay.add_pattern_label.assert_called_once()
        rough_label = self.controller.rectangles_overlay.add_pattern_label.call_args
        self.assertEqual(rough_label.args[0], "Rough Milling 01")
        rough_pattern = rough.patterns[0]
        expected_position = (
            rough_pattern.center.value[0],
            rough_pattern.center.value[1]
            + rough_pattern.spacing.value / 2
            + rough_pattern.height.value,
        )
        for actual, expected in zip(rough_label.args[1], expected_position):
            self.assertAlmostEqual(actual, expected)

        task_list.SetSelection(task_list.FindString("Notch"))
        self.controller.rectangles_overlay.reset_mock()
        with patch(
            "odemis.gui.cont.milling.rectangle_pattern_to_shape",
            return_value=Mock(),
        ) as to_shape:
            MillingTaskController.draw_milling_tasks.__wrapped__(self.controller)

        rough_calls = [
            item for item in to_shape.call_args_list
            if item.kwargs["colour"] == rough.color
        ]
        notch_calls = [
            item for item in to_shape.call_args_list
            if item.kwargs["colour"] == notch.color
        ]
        self.assertTrue(all(item.kwargs["name"] is None for item in rough_calls))
        self.assertTrue(all(
            not item.kwargs["show_dimensions"] for item in rough_calls))
        self.assertTrue(all(
            item.kwargs["opacity"] == MILLING_OVERLAY_INACTIVE_OPACITY
            for item in rough_calls
        ))
        self.assertTrue(all(
            item.kwargs["opacity"] == MILLING_OVERLAY_ACTIVE_OPACITY
            for item in notch_calls
        ))
        notch_shape_count = len(notch.patterns[0].generate())
        self.assertTrue(all(
            item.kwargs["colour"] == notch.color
            for item in to_shape.call_args_list[-notch_shape_count:]
        ))
        self.assertEqual(
            self.controller.rectangles_overlay.add_pattern_label.call_count, 2)
        labels = [
            item.args[0]
            for item in (
                self.controller.rectangles_overlay.add_pattern_label.call_args_list
            )
        ]
        self.assertTrue(any(label.startswith("Notch · ") for label in labels))

    def test_pattern_selection_warning(self) -> None:
        """Show a transient popup when pattern movement has no anchor."""
        message = "Select a milling pattern before moving it."
        self.controller._tab = SimpleNamespace(main_frame=Mock())
        with patch("odemis.gui.cont.milling.show_message") as show_message:
            self.controller._show_pattern_selection_warning(message)
        show_message.assert_called_once_with(
            self.controller._tab.main_frame,
            title="Pattern movement",
            message=message,
            timeout=PATTERN_MOVE_WARNING_TIMEOUT,
            level=logging.WARNING,
        )


class MillingSpotSizeValidationTestCase(unittest.TestCase):
    """Check corrections against generated ruler graduations without a microscope."""

    def setUp(self) -> None:
        """Create a selected ruler task and a checked feature."""
        self.tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        self.task = self.tasks["Ruler"]
        self.task.selected = True
        self.ruler = self.task.patterns[0]
        feature = SimpleNamespace(
            name=model.StringVA("Feature 1"), milling_tasks=self.tasks)
        self.controller = MillingTaskController.__new__(MillingTaskController)
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(features=model.ListVA([feature])))
        self.feature_list = Mock()
        self.feature_list.GetCount.return_value = 1
        self.feature_list.IsChecked.return_value = True
        self.controller._panel = SimpleNamespace(
            workflow_features_chk_list=self.feature_list)

    def test_valid_correction_is_accepted(self) -> None:
        """Accept a correction smaller than every graduation dimension."""
        self.ruler.spot_size_correction.value = 20e-9
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())

    def test_correction_equal_to_graduation_height_is_invalid(self) -> None:
        """Reject a correction equal to the graduation height."""
        self.ruler.spot_size_correction.value = (
            self.ruler.generate()[0].height.value)
        self.assertEqual(
            self.controller._get_invalid_spot_size_correction(),
            ("Feature 1", "Ruler"),
        )

    def test_correction_exceeding_graduation_height_is_invalid(self) -> None:
        """Reject a correction greater than the graduation height."""
        self.ruler.spot_size_correction.value = (
            2 * self.ruler.generate()[0].height.value)
        self.assertEqual(
            self.controller._get_invalid_spot_size_correction(),
            ("Feature 1", "Ruler"),
        )

    def test_correction_exceeding_short_graduation_width_is_invalid(self) -> None:
        """Reject a correction greater than a short graduation width."""
        self.ruler.width.value = 20e-9
        self.ruler.spot_size_correction.value = 16e-9
        self.assertEqual(
            self.controller._get_invalid_spot_size_correction(),
            ("Feature 1", "Ruler"),
        )

    def test_increasing_graduation_count_can_invalidate_correction(self) -> None:
        """Revalidate correction when the graduation count changes."""
        self.ruler.spot_size_correction.value = 100e-9
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())
        self.ruler.num_graduations.value = 21
        self.assertEqual(
            self.controller._get_invalid_spot_size_correction(),
            ("Feature 1", "Ruler"),
        )

    def test_increasing_height_can_make_correction_valid(self) -> None:
        """Accept the correction when a height increase makes it valid."""
        self.ruler.spot_size_correction.value = 200e-9
        self.assertEqual(
            self.controller._get_invalid_spot_size_correction(),
            ("Feature 1", "Ruler"),
        )
        self.ruler.height.value = 20e-6
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())

    def test_disabled_task_is_ignored(self) -> None:
        """Ignore invalid corrections in disabled tasks."""
        self.ruler.spot_size_correction.value = 1e-6
        self.assertEqual(
            self.controller._get_invalid_spot_size_correction(),
            ("Feature 1", "Ruler"),
        )
        self.task.selected = False
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())

    def test_unchecked_feature_is_ignored(self) -> None:
        """Ignore invalid corrections for unchecked features."""
        self.ruler.spot_size_correction.value = 1e-6
        self.assertEqual(
            self.controller._get_invalid_spot_size_correction(),
            ("Feature 1", "Ruler"),
        )
        self.feature_list.IsChecked.return_value = False
        self.assertIsNone(self.controller._get_invalid_spot_size_correction())


if __name__ == "__main__":
    unittest.main()
