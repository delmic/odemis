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
from typing import Tuple
from unittest.mock import Mock, patch

import wx

import odemis.gui as gui
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.test as test
from odemis import model
from odemis.acq.feature import MIN_MILLING_ALIGNMENT_AREA_PIXELS
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.gui.comp.overlay._constants import (
    MILLING_OVERLAY_ACTIVE_OPACITY,
    MILLING_OVERLAY_INACTIVE_OPACITY,
    MILLING_OVERLAY_LINE_WIDTH,
)
from odemis.gui.comp.overlay.base import SEL_MODE_EDIT, SEL_MODE_NONE, Vec
from odemis.gui.comp.overlay.milling import (
    MillingAlignmentAreaOverlay,
    MillingAlignmentRectangleOverlay,
    MillingPatternOverlay,
)
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
        waffle_panel = controls["Waffle Trench"]["panel"]
        self.assertEqual(
            set(waffle_panel.pattern_parameters),
            {"top_width", "top_height", "bottom_width", "bottom_height",
             "depth", "spacing", "spot_size_correction"})
        correlation_panel = controls["Correlation (NΠ+⅂Ʇ)"]["panel"]
        self.assertEqual(
            set(correlation_panel.pattern_parameters),
            {"width", "height", "marker_length", "thickness", "depth",
             "spot_size_correction"})
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
        for actual_center, expected_center in (
                (microexpansion.center.value, target),
                (other_pattern.center.value, (1e-6, 14e-6))):
            for actual_value, expected_value in zip(actual_center, expected_center):
                self.assertAlmostEqual(actual_value, expected_value)
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

    def test_selecting_alignment_area_deselects_pattern_without_unchecking(self) -> None:
        """Clear the pattern highlight when the alignment area is selected."""
        task_list = wx.CheckListBox(self.panel, choices=list(self.tasks))
        selected_index = task_list.FindString("Microexpansion")
        task_list.Check(selected_index, True)
        task_list.SetSelection(selected_index)
        self.controller._panel.milling_task_chk_list = task_list
        self.controller._active_spot_size_pattern = self.tasks["Microexpansion"].patterns[0]
        self.controller.draw_milling_tasks.reset_mock()

        self.controller._deselect_milling_task_for_alignment_area()

        self.assertEqual(task_list.GetSelection(), wx.NOT_FOUND)
        self.assertTrue(task_list.IsChecked(selected_index))
        self.assertIsNone(self.controller._active_spot_size_pattern)
        self.controller.draw_milling_tasks.assert_called_once_with(
            redraw_alignment_area=False)

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
        for color in colors:
            red, green, blue = (
                int(color[index:index + 2], 16) for index in (1, 3, 5)
            )
            self.assertEqual(max(red, green, blue), 255)
            self.assertGreater(len({red, green, blue}), 1)
        self.assertEqual(
            {
                task_name: self.tasks[task_name].color
                for task_name in (
                    "Microexpansion",
                    "Rough Milling 01",
                    "Rough Milling 02",
                    "Polishing 01",
                    "Polishing 02",
                )
            },
            {
                "Microexpansion": "#FFA500",
                "Rough Milling 01": "#FFFF00",
                "Rough Milling 02": "#00FFFF",
                "Polishing 01": "#FF00FF",
                "Polishing 02": "#00FF00",
            },
        )

        task_without_color = self.tasks["Correlation (NΠ+⅂Ʇ)"]
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

    def test_alignment_overlay_uses_milling_style_without_hover_focus(self) -> None:
        """Use the milling rectangle style without activating the viewport on hover."""
        canvas = Mock()
        canvas.get_half_buffer_size.return_value = (0, 0)
        area_selected = Mock()
        overlay = MillingAlignmentAreaOverlay(canvas, Mock(), area_selected)

        self.assertIs(overlay.shape_cls, MillingAlignmentRectangleOverlay)
        self.assertTrue(issubclass(overlay.shape_cls, MillingRectangleOverlay))
        self.assertFalse(overlay.shape_creation_allowed)

        alignment_rectangle = MillingAlignmentRectangleOverlay(Mock(), can_rotate=False)
        points = (Vec(0, 0), Vec(100, 0), Vec(100, 100), Vec(0, 100))
        (
            alignment_rectangle.v_point1,
            alignment_rectangle.v_point2,
            alignment_rectangle.v_point3,
            alignment_rectangle.v_point4,
        ) = points
        alignment_rectangle._calc_edges()
        self.assertNotIn(gui.HOVER_LINE, alignment_rectangle.v_edges)
        self.assertEqual(len(alignment_rectangle.v_edges[gui.HOVER_EDGE]), 4)
        context = Mock()
        alignment_rectangle.selected.value = False
        alignment_rectangle.draw_edges(context, *points)
        context.arc.assert_not_called()
        with patch.object(MillingRectangleOverlay, "draw_name_label") as draw_name_label:
            alignment_rectangle.draw_name_label(context)
            draw_name_label.assert_not_called()

            alignment_rectangle.selected.value = True
            alignment_rectangle.draw_name_label(context)
            draw_name_label.assert_called_once_with(context)
        alignment_rectangle.draw_edges(context, *points)
        self.assertEqual(context.arc.call_count, 4)

        overlay.active.value = True
        event = Mock()
        overlay.on_enter(event)

        canvas.SetFocus.assert_not_called()
        canvas.set_default_cursor.assert_not_called()
        event.Skip.assert_called_once_with(False)

        shape = Mock()
        shape.cnvs = canvas
        shape.selected = model.BooleanVA(False)
        shape.is_created = model.BooleanVA(True)
        shape.get_hover.return_value = (gui.HOVER_EDGE, 1)
        overlay._shapes.value.append(shape)

        left_down_event = Mock()
        left_down_event.ControlDown.return_value = False
        left_down_event.Position = Vec(10, 10)
        with patch.object(overlay, "_get_shape", return_value=shape):
            overlay.on_left_down(left_down_event)
        self.assertIs(overlay._selected_shape, shape)
        area_selected.assert_called_once_with()
        canvas.cancel_drag.assert_called_once_with()
        left_down_event.Skip.assert_called_with(False)

        motion_event = Mock()
        canvas.set_dynamic_cursor.reset_mock()
        overlay.on_motion(motion_event)
        canvas.set_dynamic_cursor.assert_called_once_with(wx.CURSOR_HAND)

        shape.selection_mode = SEL_MODE_NONE
        overlay.on_motion(motion_event)
        canvas.set_dynamic_cursor.assert_called_with(wx.CURSOR_SIZING)
        shape.get_hover.return_value = (gui.HOVER_EDGE, 2)
        overlay.on_motion(motion_event)
        canvas.set_dynamic_cursor.assert_called_with(wx.CURSOR_SIZING)

        shape.get_hover.return_value = (gui.HOVER_SELECTION, None)
        overlay.on_motion(motion_event)
        canvas.set_dynamic_cursor.assert_called_with(wx.CURSOR_HAND)

    def test_unselected_alignment_corner_does_not_show_resize_cursor(self) -> None:
        """Keep the default cursor over an unselected alignment-area corner."""
        canvas = Mock()
        canvas.get_half_buffer_size.return_value = (0, 0)
        overlay = MillingAlignmentAreaOverlay(canvas, Mock())
        overlay.active.value = True
        shape = Mock()
        shape.cnvs = canvas
        shape.selected = model.BooleanVA(False)
        shape.is_created = model.BooleanVA(True)
        shape.get_hover.return_value = (gui.HOVER_EDGE, 1)
        overlay._shapes.value.append(shape)

        overlay.on_motion(Mock())

        shape.get_hover.assert_not_called()
        canvas.set_dynamic_cursor.assert_not_called()
        canvas.reset_dynamic_cursor.assert_called_once_with()

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
        self.assertEqual(
            list(self.controller.milling_tasks), ["Rough Milling 01", "Notch"])
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


class MillingAlignmentAreaTestCase(test.GuiTestCase):
    """Exercise alignment-area interactions through a real microscope canvas."""

    frame_class = test.test_gui.CanvasTestFrame

    def setUp(self) -> None:
        """Create a canvas with one editable alignment rectangle."""
        super().setUp()
        self.canvas = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(self.canvas, flags=wx.EXPAND, proportion=1, clear=True)
        test.gui_loop()

        self.area_changed = Mock()
        self.overlay = MillingAlignmentAreaOverlay(self.canvas, self.area_changed)
        self.canvas.add_world_overlay(self.overlay)
        self.overlay.active.value = True

        self.shape = MillingAlignmentRectangleOverlay(self.canvas, can_rotate=False)
        offset = self.canvas.get_half_buffer_size()
        physical_points = [
            self.canvas.view_to_phys(point, offset)
            for point in (
                Vec(100, 100),
                Vec(250, 100),
                Vec(250, 250),
                Vec(100, 250),
            )
        ]
        self.shape.set_physical_sel(physical_points)
        self.shape._points = self.shape.get_physical_sel()
        self.shape.points.value = self.shape._points
        self.shape.is_created.value = True
        self.shape.selected.value = False
        self.overlay.add_shape(self.shape)
        test.gui_loop()

        self.assertIsNone(self.overlay._selected_shape)
        self.assertFalse(self.shape.selected.value)

    def tearDown(self) -> None:
        """Destroy the canvas created for a test."""
        test.gui_loop()
        self.remove_all()
        test.gui_loop()
        super().tearDown()

    def _send_mouse_event(self, event_type: int, position: Vec) -> None:
        """Dispatch a mouse event through the canvas event-handler chain.

        :param event_type: wx mouse event type.
        :param position: Event position in viewport pixels.
        """
        event = wx.MouseEvent(event_type)
        event.x, event.y = position
        event.SetEventObject(self.canvas)
        self.canvas.GetEventHandler().ProcessEvent(event)

    def test_dragging_area_does_not_pan_canvas(self) -> None:
        """Move the area while leaving the reference-image canvas stationary."""
        self._send_mouse_event(wx.wxEVT_LEFT_DOWN, Vec(175, 175))
        self.assertFalse(self.canvas.left_dragging)

        self._send_mouse_event(wx.wxEVT_MOTION, Vec(195, 185))
        self._send_mouse_event(wx.wxEVT_LEFT_UP, Vec(195, 185))

        self.assertEqual(self.canvas.drag_shift, (0, 0))
        self.assertEqual(self.shape.v_point1, Vec(120, 110))
        self.assertEqual(self.shape.v_point3, Vec(270, 260))
        self.assertTrue(self.shape.selected.value)
        self.assertGreaterEqual(self.area_changed.call_count, 2)
        self.assertTrue(self.area_changed.call_args.args[1])

    def test_resizing_area_does_not_pan_canvas(self) -> None:
        """Move on the first corner gesture, then resize while staying selected."""
        self._send_mouse_event(wx.wxEVT_LEFT_DOWN, Vec(100, 100))
        self.assertFalse(self.canvas.left_dragging)

        self._send_mouse_event(wx.wxEVT_MOTION, Vec(80, 80))
        self._send_mouse_event(wx.wxEVT_LEFT_UP, Vec(80, 80))

        self.assertTrue(self.shape.selected.value)
        self.assertEqual(self.shape.v_point1, Vec(80, 80))
        self.assertEqual(self.shape.v_point3, Vec(230, 230))
        self.assertGreaterEqual(self.area_changed.call_count, 2)
        self.area_changed.reset_mock()

        self._send_mouse_event(wx.wxEVT_LEFT_DOWN, Vec(80, 80))
        self._send_mouse_event(wx.wxEVT_MOTION, Vec(60, 60))
        self._send_mouse_event(wx.wxEVT_LEFT_UP, Vec(20, 20))

        self.assertEqual(self.canvas.drag_shift, (0, 0))
        self.assertEqual(self.shape.v_point1, Vec(60, 60))
        self.assertEqual(self.shape.v_point2, Vec(230, 60))
        self.assertEqual(self.shape.v_point4, Vec(60, 230))
        self.assertTrue(self.shape.selected.value)
        self.assertGreaterEqual(self.area_changed.call_count, 2)
        self.assertTrue(self.area_changed.call_args.args[1])


class MillingAlignmentConstraintTestCase(unittest.TestCase):
    """Check alignment resizing constraints without microscope hardware."""

    def _create_controller(
            self, area: Tuple[float, float, float, float]) -> Tuple[MillingTaskController, SimpleNamespace]:
        """Create a controller whose stream uses physical pixels as coordinates."""
        reference_image = SimpleNamespace(shape=(1000, 1000))
        feature = SimpleNamespace(
            reference_image=reference_image,
            millingAlignmentArea=model.TupleVA(area),
        )
        stream = SimpleNamespace(raw=[reference_image])
        stream.getPixelCoordinates = Mock(
            side_effect=lambda point, check_bbox=False: point)
        stream.getPhysicalCoordinates = Mock(side_effect=lambda point: point)

        controller = MillingTaskController.__new__(MillingTaskController)
        controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(
                currentFeature=SimpleNamespace(value=feature)))
        controller._get_reference_stream = Mock(return_value=stream)
        controller.canvas = Mock()
        return controller, feature

    def _create_resize_shape(self, corner_index: int, dragged_point: Vec) -> Mock:
        """Create a shape mock with one corner at the requested pixel position."""
        corners = [
            Vec(300, 300),
            Vec(500, 300),
            Vec(500, 500),
            Vec(300, 500),
        ]
        corners[corner_index - 1] = dragged_point
        shape = Mock()
        shape.interaction_mode = SEL_MODE_EDIT
        shape.edit_v_point_idx = corner_index
        shape.get_physical_sel.return_value = corners
        shape.points = SimpleNamespace(value=None)
        return shape

    def test_resizing_keeps_opposite_corner_fixed(self) -> None:
        """Anchor the diagonal corner for every resize direction."""
        cases = (
            (1, Vec(200, 250), (0.2, 0.25, 0.5, 0.45)),
            (2, Vec(800, 250), (0.3, 0.25, 0.5, 0.45)),
            (3, Vec(750, 800), (0.3, 0.3, 0.45, 0.5)),
            (4, Vec(200, 800), (0.2, 0.3, 0.5, 0.5)),
        )
        for corner_index, dragged_point, expected in cases:
            with self.subTest(corner_index=corner_index):
                controller, feature = self._create_controller((0.3, 0.3, 0.4, 0.4))
                shape = self._create_resize_shape(corner_index, dragged_point)

                controller._update_alignment_area_from_shape(shape, commit=False)

                for actual_value, expected_value in zip(
                        feature.millingAlignmentArea.value, expected):
                    self.assertAlmostEqual(actual_value, expected_value)

    def test_resizing_stays_steady_at_minimum_size(self) -> None:
        """Keep the fixed corner still when the pointer requests a smaller area."""
        controller, feature = self._create_controller((0.1, 0.1, 0.4, 0.4))
        shape = self._create_resize_shape(3, Vec(200, 200))

        controller._update_alignment_area_from_shape(shape, commit=False)
        first_area = feature.millingAlignmentArea.value
        shape.get_physical_sel.return_value[2] = Vec(250, 250)
        controller._update_alignment_area_from_shape(shape, commit=False)

        for actual_value, expected_value in zip(
                feature.millingAlignmentArea.value, first_area):
            self.assertAlmostEqual(actual_value, expected_value)
        minimum_side = MIN_MILLING_ALIGNMENT_AREA_PIXELS ** 0.5 / 1000
        for actual_value, expected_value in zip(
                first_area, (0.1, 0.1, minimum_side, minimum_side)):
            self.assertAlmostEqual(actual_value, expected_value)

    def test_small_legacy_reference_does_not_draw_alignment_area(self) -> None:
        """Leave an invalid legacy reference visible without drawing its area."""
        reference_image = SimpleNamespace(shape=(200, 300))
        feature = SimpleNamespace(
            reference_image=reference_image,
            millingAlignmentArea=model.TupleVA((0.1, 0.1, 0.5, 0.5)),
        )
        stream = SimpleNamespace(raw=[reference_image])
        controller = MillingTaskController.__new__(MillingTaskController)
        controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(currentFeature=SimpleNamespace(value=feature)))
        controller._get_reference_stream = Mock(return_value=stream)
        controller.alignment_area_overlay = Mock()
        controller.canvas = Mock()

        MillingTaskController.draw_alignment_area.__wrapped__(controller)

        controller.alignment_area_overlay.clear.assert_called_once_with()
        controller.alignment_area_overlay.add_shape.assert_not_called()
        controller.canvas.request_drawing_update.assert_called_once_with()


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

    def test_small_legacy_reference_disables_milling_and_shows_warning(self) -> None:
        """Require reacquisition without preventing the project from loading."""
        self.feature.reference_image.shape = (200, 300)

        self.assertEqual(
            self.controller._get_invalid_alignment_reference(),
            "Feature 1",
        )
        self.controller._update_milling_validation_message()
        self.controller._panel.txt_automated_milling_status.SetLabel.assert_called_once_with(
            "Acquire a higher-resolution reference image for Feature 1.")

        self.controller.selected_tasks = SimpleNamespace(value=["Ruler"])
        self.controller.valid_patterns = SimpleNamespace(value=True)
        self.controller._tab_data.main.is_acquiring = SimpleNamespace(value=False)
        self.controller._panel.btn_run_milling = Mock()
        self.controller._panel.btn_run_automated_milling = Mock()
        self.controller._panel.txt_milling_est_time = Mock()
        self.controller._panel.txt_automated_milling_est_time = Mock()
        self.controller._update_milling_time = Mock()
        MillingTaskController._update_mill_btn.__wrapped__(self.controller)

        self.controller._panel.btn_run_milling.Enable.assert_called_once_with(False)
        self.controller._panel.btn_run_automated_milling.Enable.assert_called_once_with(False)

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


class MillingReferenceStreamTestCase(unittest.TestCase):
    """Resolve the saved-image stream even during controller initialization."""

    def setUp(self) -> None:
        """Create a milling controller with mocked acquired-image state."""
        self.reference_image = object()
        self.feature = SimpleNamespace(reference_image=self.reference_image)
        self.controller = MillingTaskController.__new__(MillingTaskController)
        self.controller.acq_cont = SimpleNamespace(stream=None)

    def test_uses_displayed_stream_before_acquired_controller_is_ready(self) -> None:
        """Find the reference image directly in the viewport during startup."""
        displayed_stream = SimpleNamespace(raw=[self.reference_image])
        self.controller.viewport = SimpleNamespace(
            view=SimpleNamespace(getStreams=Mock(return_value=[displayed_stream])))

        self.assertIs(
            self.controller._get_reference_stream(self.feature),
            displayed_stream,
        )

    def test_ignores_stale_acquired_stream(self) -> None:
        """Prefer the displayed stream belonging to the newly selected feature."""
        stale_stream = SimpleNamespace(raw=[object()])
        displayed_stream = SimpleNamespace(raw=[self.reference_image])
        self.controller.acq_cont.stream = stale_stream
        self.controller.viewport = SimpleNamespace(
            view=SimpleNamespace(
                getStreams=Mock(return_value=[stale_stream, displayed_stream])))

        self.assertIs(
            self.controller._get_reference_stream(self.feature),
            displayed_stream,
        )


if __name__ == "__main__":
    unittest.main()
