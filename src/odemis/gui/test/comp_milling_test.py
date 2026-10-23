# -*- coding: utf-8 -*-
"""Tests for milling pattern controls and their parameter connections.

Created on Sep 2026

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

This file is part of Odemis, licensed under the GNU General Public License v2.
See the LICENSE.txt file for details.
"""

import logging
import math
import unittest
from types import SimpleNamespace
from typing import Tuple
from unittest.mock import Mock, call, patch

import cairo
import numpy
import wx
from shapely.geometry import Polygon

import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.test as test
from odemis import model
from odemis.acq.feature import CryoFeature, MIN_MILLING_ALIGNMENT_AREA_PIXELS
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.patterns import RectanglePatternParameters
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.acq.stream import StaticSEMStream
from odemis.gui.comp.overlay._constants import (
    MILLING_ALIGNMENT_AREA_COLOR,
    MILLING_OVERLAY_ACTIVE_OPACITY,
    MILLING_OVERLAY_INACTIVE_OPACITY,
    MILLING_OVERLAY_LINE_WIDTH,
)
from odemis.gui.comp.overlay.base import SEL_MODE_DRAG, SEL_MODE_EDIT, Vec
from odemis.gui.comp.overlay.cryo_feature import CryoFeatureOverlay, MODE_SHOW_FEATURES
from odemis.gui.comp.overlay.milling import (
    MillingAlignmentAreaOverlay,
    MillingAlignmentRectangleOverlay,
    MillingPatternOverlay,
    update_milling_alignment_area_shape,
)
from odemis.gui.comp.overlay.rectangle import MillingRectangleOverlay, RectangleOverlay
from odemis.gui.comp.text import IntegerTextCtrl, UnitFloatCtrl
from odemis.gui.cont.milling import (
    AutomatedMillingController,
    MILLING_THEME_COLORS,
    MOVE_DELTA_X_LONG,
    PATTERN_DRAG_HINT_THRESHOLD,
    PATTERN_MOVE_WARNING_TIMEOUT,
    MillingTaskController,
    _get_alignment_area_polygon,
    _get_milling_colour,
    _get_milling_rectangle_polygon,
    _get_pattern_stack_center,
    rectangle_pattern_to_shape,
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
        self.controller.draw_alignment_area = Mock()
        self.controller._update_milling_validation_message = Mock()
        self.controller._update_mill_btn = Mock()
        self.controller._update_pattern_panels()
        test.gui_loop()

    def tearDown(self) -> None:
        """Destroy the GUI controls created for a test."""
        self.settings_panel.Destroy()
        self.scroll.Destroy()
        test.gui_loop()
        super().tearDown()

    def test_pattern_polygon_matches_displayed_rotation(self) -> None:
        """Use the same rotation direction for validation and the drawn rectangle."""
        canvas = Mock()
        canvas.get_half_buffer_size.return_value = (0, 0)
        canvas.phys_to_view.side_effect = lambda point, offset: (point[0] / 1e-6, -point[1] / 1e-6)
        canvas.view_to_phys.side_effect = lambda point, offset: (point[0] * 1e-6, -point[1] * 1e-6)
        image = SimpleNamespace(metadata={model.MD_POS: (0, 0)})
        for rotation in (0, math.pi / 6, math.pi / 4, 3 * math.pi / 4):
            with self.subTest(rotation=rotation):
                pattern = RectanglePatternParameters(width=80e-6, height=10e-6, depth=1e-6,
                                                     center=(20e-6, -30e-6), rotation=rotation)
                shape = rectangle_pattern_to_shape(canvas, image, pattern)
                expected = Polygon(shape.get_physical_sel())
                actual = _get_milling_rectangle_polygon(pattern)
                self.assertLess(actual.symmetric_difference(expected).area / expected.area, 1e-10)


    def test_resetting_current_feature_clears_milling_overlays(self) -> None:
        """Redraw empty overlays when a new project removes its current feature."""
        self.controller.set_milling_tasks = Mock()
        self.controller._update_pattern_panels = Mock()
        self.controller._update_pattern_movement_controls = Mock()
        self.controller.draw_alignment_area = Mock()
        self.controller.draw_milling_tasks.reset_mock()

        self.controller._on_current_feature_changes(None)

        self.controller.set_milling_tasks.assert_called_once_with({})
        self.controller.draw_milling_tasks.assert_called_once_with()
        self.controller.draw_alignment_area.assert_not_called()

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
        self.controller._update_milling_validation_message.assert_called()
        self.controller._update_mill_btn.assert_called()
        ruler.num_graduations.value = 11
        test.gui_loop()
        self.assertEqual(controls["num_graduations"].GetValue(), 11)

    def test_pattern_parameter_text_field_accepts_typed_value(self) -> None:
        """Keep native text editing available while activating a pattern."""
        panel = self.controller.controls["Microexpansion"]["panel"]
        spacing = panel.ctrl_dict["spacing"]
        pattern = self.tasks["Microexpansion"].patterns[0]
        self.controller.draw_milling_tasks.reset_mock()

        mouse_event = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
        mouse_event.SetEventObject(spacing)
        spacing.GetEventHandler().ProcessEvent(mouse_event)
        self.controller.draw_milling_tasks.assert_not_called()

        focus_event = wx.FocusEvent(wx.wxEVT_SET_FOCUS, spacing.Id)
        focus_event.SetEventObject(spacing)
        spacing.GetEventHandler().ProcessEvent(focus_event)
        test.gui_loop()
        spacing.SetSelection(0, -1)
        spacing.WriteText("12 µm")
        enter_event = wx.CommandEvent(wx.wxEVT_TEXT_ENTER, spacing.Id)
        enter_event.SetEventObject(spacing)
        spacing.GetEventHandler().ProcessEvent(enter_event)
        test.gui_loop()

        self.assertAlmostEqual(pattern.spacing.value, 12e-6)

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
                visible_patterns, target, microexpansion.center.value)
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

    def test_pattern_stack_center_uses_generated_geometry_bounds(self) -> None:
        """Center movement on the bounds of all displayed pattern geometry."""
        patterns = [
            RectanglePatternParameters(
                width=2e-6, height=2e-6, depth=1e-6, center=(0, 0)),
            RectanglePatternParameters(
                width=4e-6, height=2e-6, depth=1e-6, center=(5e-6, 3e-6)),
        ]

        center = _get_pattern_stack_center(patterns)

        self.assertAlmostEqual(center[0], 3e-6)
        self.assertAlmostEqual(center[1], 1.5e-6)

    def _create_task_list(self, selected_names: Tuple[str, ...] = ()) -> wx.CheckListBox:
        """Create real checked and selected rows for the controller's tasks."""
        task_list = wx.CheckListBox(self.panel, choices=list(self.controller.milling_tasks), style=wx.LB_EXTENDED)
        self.addCleanup(task_list.Destroy)
        for index, (name, task) in enumerate(self.controller.milling_tasks.items()):
            task_list.Check(index, task.selected)
            if name in selected_names:
                task_list.SetSelection(index)
        self.controller._panel.milling_task_chk_list = task_list
        return task_list

    def test_ctrl_click_deselects_only_the_clicked_selected_row(self) -> None:
        """Preserve checks and other selections, activating a remaining row when needed."""
        first, second = "Microexpansion", "Rough Milling 01"
        cases = (
            ((first,), first, (), None),
            ((first,), second, (first,), first),
            ((first, second), first, (second,), second),
            ((first, second), second, (first,), first),
        )
        for selected, clicked, remaining, active in cases:
            with self.subTest(selected=selected, clicked=clicked):
                task_list = self._create_task_list(selected)
                checked = task_list.GetCheckedStrings()
                self.controller._active_spot_size_pattern = self.tasks[first].patterns[0]
                self.controller.draw_milling_tasks.reset_mock()
                event = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
                event.SetControlDown(True)
                with patch.object(task_list, "HitTest", return_value=task_list.FindString(clicked)):
                    self.controller._on_milling_task_mouse_down(event)

                self.assertEqual(list(task_list.GetSelections()), [task_list.FindString(name) for name in remaining])
                self.assertEqual(task_list.GetCheckedStrings(), checked)
                expected_active = self.tasks[active].patterns[0] if active else None
                self.assertIs(self.controller._active_spot_size_pattern, expected_active)
                self.assertEqual(event.GetSkipped(), clicked not in selected)
                self.assertEqual(self.controller.draw_milling_tasks.call_count, int(clicked in selected))

    def test_space_checks_or_unchecks_selected_rows(self) -> None:
        """Toggle all selected rows and synchronize the visible parameter panels."""
        names = ("Microexpansion", "Rough Milling 01")
        task_list = self._create_task_list(names)
        task_list.SetCheckedStrings([names[0]])
        self.controller.selected_tasks = model.ListVA([names[0]])
        key_event = Mock()
        key_event.GetKeyCode.return_value = wx.WXK_SPACE

        for expected in (list(names), []):
            with self.subTest(checked=expected):
                self.controller._on_milling_task_key_down(key_event)
                self.assertEqual(list(task_list.GetCheckedStrings()), expected)
                self.assertEqual(self.controller.selected_tasks.value, expected)
                for name in names:
                    self.assertEqual(self.controller.controls[name]["panel"].IsShown(), name in expected)
        key_event.Skip.assert_not_called()

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

    def test_click_canvas_deselects_pattern_without_rebuilding_alignment_area(self) -> None:
        """Clear the pattern highlight while preserving the alignment overlay."""
        task_list = wx.CheckListBox(self.panel, choices=list(self.tasks))
        selected_index = task_list.FindString("Microexpansion")
        task_list.Check(selected_index, True)
        task_list.SetSelection(selected_index)
        self.controller._panel.milling_task_chk_list = task_list
        self.controller._active_spot_size_pattern = self.tasks["Microexpansion"].patterns[0]
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(currentFeature=SimpleNamespace(value=None)))
        self.controller.draw_milling_tasks.reset_mock()
        canvas_event = Mock()
        canvas_event.ControlDown.return_value = False
        canvas_event.ShiftDown.return_value = False

        self.controller.on_mouse_down(canvas_event)

        self.assertEqual(task_list.GetSelection(), wx.NOT_FOUND)
        self.assertTrue(task_list.IsChecked(selected_index))
        self.assertIsNone(self.controller._active_spot_size_pattern)
        self.controller.draw_milling_tasks.assert_called_once_with(
            redraw_alignment_area=False)
        canvas_event.Skip.assert_called_once_with()

    def test_feature_click_is_not_passed_to_canvas_dragging(self) -> None:
        """Consume feature selection so the canvas does not show a drag cursor."""
        feature = Mock()
        feature_overlay = CryoFeatureOverlay.__new__(CryoFeatureOverlay)
        feature_overlay.active = model.BooleanVA(True)
        feature_overlay._mode = MODE_SHOW_FEATURES
        feature_overlay._detect_point_inside_feature = Mock(return_value=feature)
        feature_overlay.tab_data = SimpleNamespace(
            main=SimpleNamespace(currentFeature=model.VigilantAttribute(None)))
        event = Mock()
        event.Position = wx.Point(20, 30)

        feature_overlay.on_left_down(event)

        self.assertIs(feature_overlay.tab_data.main.currentFeature.value, feature)
        event.Skip.assert_not_called()

        feature_overlay._detect_point_inside_feature.return_value = None
        empty_space_event = Mock()
        empty_space_event.Position = wx.Point(40, 50)
        feature_overlay.on_left_down(empty_space_event)

        empty_space_event.Skip.assert_called_once_with()

    def test_feature_marker_and_alignment_corner_take_priority_over_pattern(self) -> None:
        """Leave checked patterns unchanged while passing overlay-owned clicks through."""
        selected = "Microexpansion"
        for owner in ("feature", "corner"):
            with self.subTest(owner=owner):
                task_list = self._create_task_list((selected,))
                checked = task_list.GetCheckedStrings()
                self.controller._active_spot_size_pattern = self.tasks[selected].patterns[0]
                self.controller._tab_data = SimpleNamespace(
                    main=SimpleNamespace(currentFeature=SimpleNamespace(
                        value=SimpleNamespace(reference_image=object()))))
                feature_overlay = SimpleNamespace(show=True, active=model.BooleanVA(True),
                                                  get_feature_at=Mock(return_value=object()))
                self.controller.canvas = SimpleNamespace(cryofeature_overlay=feature_overlay if owner == "feature" else None)
                self.controller.alignment_area_overlay = Mock()
                self.controller.alignment_area_overlay.is_corner_handle_at.return_value = True
                self.controller.rectangles_overlay = Mock()
                self.controller.draw_milling_tasks.reset_mock()
                event = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
                event.SetPosition(wx.Point(10, 20))

                self.controller.on_mouse_down(event)

                self.assertEqual(list(task_list.GetSelections()), [])
                self.assertEqual(task_list.GetCheckedStrings(), checked)
                self.assertIsNone(self.controller._active_spot_size_pattern)
                self.controller.rectangles_overlay.get_pattern_at.assert_not_called()
                self.controller.draw_milling_tasks.assert_called_once_with(redraw_alignment_area=False)
                self.assertTrue(event.GetSkipped())
                if owner == "feature":
                    self.controller.alignment_area_overlay.is_corner_handle_at.assert_not_called()

    def test_alignment_corner_is_not_blocked_by_overlapping_pattern(self) -> None:
        """Let a selected alignment corner resize over a milling pattern."""
        position = wx.Point(10, 20)
        self.controller.canvas = SimpleNamespace(cryofeature_overlay=None)
        self.controller.alignment_area_overlay = Mock()
        self.controller.alignment_area_overlay.is_corner_handle_at.return_value = True
        self.controller.rectangles_overlay = Mock()
        self.controller.rectangles_overlay.get_pattern_at.return_value = Mock()

        self.assertFalse(
            self.controller._blocks_alignment_area_interaction(position))
        self.controller.rectangles_overlay.get_pattern_at.assert_not_called()

    def test_manual_move_selection_fallback(self) -> None:
        """Use the first checked task only when no selection exists."""
        cases = (
            (("Ruler", "Polishing 01"), (), ("Ruler",)),
            (("Ruler", "Polishing 01"), ("Polishing 01",), ("Polishing 01",)),
            (("Ruler",), ("Notch",), ()),
            ((), (), ()),
            ((), ("Notch",), ()),
        )
        for checked, selected, movable in cases:
            with self.subTest(checked=checked, selected=selected):
                for name, task in self.tasks.items():
                    task.selected = name in checked
                task_list = self._create_task_list(selected)
                move_all = wx.CheckBox(self.panel)
                self.addCleanup(move_all.Destroy)
                self.controller._panel.chk_move_all_patterns = move_all
                active = self.tasks[selected[0]].patterns[0] if selected else None
                self.controller._active_spot_size_pattern = active
                self.controller.draw_milling_tasks.reset_mock()

                patterns = self.controller._get_patterns_for_manual_move()

                self.assertEqual(patterns, [pattern for name in movable for pattern in self.tasks[name].patterns])
                expected_selection = selected or movable
                self.assertEqual(list(task_list.GetSelections()), [task_list.FindString(name) for name in expected_selection])
                self.assertEqual(list(task_list.GetCheckedStrings()), list(checked))
                expected_active = self.tasks[expected_selection[0]].patterns[0] if expected_selection else None
                self.assertIs(self.controller._active_spot_size_pattern, expected_active)
                self.assertEqual(self.controller.draw_milling_tasks.call_count, int(bool(movable) and not selected))
                self.controller.draw_milling_tasks.reset_mock()
                self.controller._select_first_checked_milling_task()
                self.assertEqual(list(task_list.GetSelections()), [task_list.FindString(name) for name in expected_selection])
                self.assertIs(self.controller._active_spot_size_pattern, expected_active)
                self.controller.draw_milling_tasks.assert_not_called()

    def _prepare_pattern_move(self, selected_names: Tuple[str, ...], move_all: bool = False) -> wx.CheckListBox:
        """Prepare real pattern parameters with a simple canvas coordinate transform."""
        task_list = self._create_task_list(selected_names)
        checkbox = wx.CheckBox(self.panel)
        checkbox.SetValue(move_all)
        self.addCleanup(checkbox.Destroy)
        self.controller._panel.chk_move_all_patterns = checkbox
        self.controller._active_spot_size_pattern = self.controller.milling_tasks[selected_names[-1]].patterns[0]
        self.controller.allow_milling_pattern_move = True
        reference_image = model.DataArray(numpy.zeros((10, 10)), metadata={model.MD_POS: (100e-6, -200e-6)})
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(currentFeature=SimpleNamespace(value=SimpleNamespace(reference_image=reference_image))))
        self.controller._show_pattern_selection_warning = Mock()
        self.controller.canvas = Mock()
        self.controller.canvas.get_half_buffer_size.return_value = (0, 0)
        self.controller.canvas.phys_to_view.side_effect = lambda point, _: point
        self.controller.canvas.view_to_phys.side_effect = lambda point, _: point
        self.controller.draw_milling_tasks.reset_mock()
        self.controller._update_milling_validation_message.reset_mock()
        self.controller._update_mill_btn.reset_mock()
        return task_list

    def _pattern_move_event(self) -> Mock:
        """Create a Ctrl+Shift event on the movement canvas."""
        event = Mock()
        event.GetEventObject.return_value = self.controller.canvas
        event.GetPosition.return_value = wx.Point(0, 0)
        event.ControlDown.return_value = True
        event.ShiftDown.return_value = True
        event.AltDown.return_value = False
        return event

    def test_move_command_does_not_replace_unchecked_selection(self) -> None:
        """Warn and leave every pattern in place when the selected task is unchecked."""
        task_list = self._prepare_pattern_move(("Notch",))
        self.assertFalse(task_list.IsChecked(task_list.FindString("Notch")))
        centers = {pattern: pattern.center.value for task in self.tasks.values() for pattern in task.patterns}
        with patch("odemis.gui.cont.milling.save_project") as save:
            self.controller.on_mouse_down(self._pattern_move_event())
        self.assertEqual(list(task_list.GetSelections()), [task_list.FindString("Notch")])
        self.assertIs(self.controller._active_spot_size_pattern, self.tasks["Notch"].patterns[0])
        self.assertEqual({pattern: pattern.center.value for pattern in centers}, centers)
        self.controller._show_pattern_selection_warning.assert_called_once_with("Select a milling pattern before moving it.")
        save.assert_not_called()

    def test_move_command_translates_selected_or_all_checked_patterns(self) -> None:
        """Center the requested stack at the click, preserving offsets and excluded patterns."""
        names = ("Microexpansion", "Rough Milling 01", "Rough Milling 02", "Polishing 01")
        self.controller.milling_tasks = {name: self.tasks[name] for name in names}
        for move_all in (False, True):
            with self.subTest(move_all=move_all):
                centers = ((0, 0), (6e-6, 4e-6), (20e-6, 0), (30e-6, 0))
                for index, (name, center) in enumerate(zip(names, centers)):
                    task = self.tasks[name]
                    task.selected = index < 3
                    task.patterns = [RectanglePatternParameters(width=2e-6, height=2e-6, depth=1e-6, center=center)]
                task_list = self._prepare_pattern_move(names[:2], move_all=move_all)
                checked = task_list.GetCheckedStrings()
                event = self._pattern_move_event()
                event.GetPosition.return_value = (112e-6, -190e-6)
                anchor = (10e-6, 2e-6) if move_all else (3e-6, 2e-6)
                with patch("odemis.gui.cont.milling.save_project") as save:
                    self.controller.on_mouse_down(event)

                for index, (name, center) in enumerate(zip(names, centers)):
                    expected = center
                    if index < (3 if move_all else 2):
                        expected = (center[0] + 12e-6 - anchor[0], center[1] + 10e-6 - anchor[1])
                    for actual_value, expected_value in zip(self.tasks[name].patterns[0].center.value, expected):
                        self.assertAlmostEqual(actual_value, expected_value, delta=1e-12)
                expected_selection = [] if move_all else [task_list.FindString(name) for name in names[:2]]
                self.assertEqual(list(task_list.GetSelections()), expected_selection)
                self.assertEqual(task_list.GetCheckedStrings(), checked)
                expected_active = None if move_all else self.tasks[names[1]].patterns[0]
                self.assertIs(self.controller._active_spot_size_pattern, expected_active)
                self.controller._show_pattern_selection_warning.assert_not_called()
                save.assert_called_once_with(self.controller._tab_data.main)

    def test_ctrl_alt_click_does_not_select_milling_task(self) -> None:
        """Leave Ctrl+Alt+click available to the normal canvas handlers."""
        task_list = wx.CheckListBox(self.panel, choices=list(self.tasks))
        for index, task in enumerate(self.tasks.values()):
            task_list.Check(index, task.selected)
        self.controller._panel.milling_task_chk_list = task_list
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(
                currentFeature=SimpleNamespace(
                    value=SimpleNamespace(reference_image=Mock()),
                ),
            ),
        )
        self.controller._active_spot_size_pattern = None
        self.controller.draw_milling_tasks.reset_mock()
        self.panel.Bind(wx.EVT_LEFT_DOWN, self.controller.on_mouse_down)
        canvas_event = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
        canvas_event.SetEventObject(self.panel)
        canvas_event.SetPosition(wx.Point(10, 10))
        canvas_event.SetControlDown(True)
        canvas_event.SetAltDown(True)

        self.panel.GetEventHandler().ProcessEvent(canvas_event)

        self.assertEqual(task_list.GetSelection(), wx.NOT_FOUND)
        self.assertIsNone(self.controller._active_spot_size_pattern)
        self.controller.draw_milling_tasks.assert_not_called()
        self.assertTrue(canvas_event.GetSkipped())

    def test_ctrl_arrow_moves_all_checked_patterns_and_clears_selection(self) -> None:
        """Translate checked patterns, preserving the centers of unchecked tasks."""
        self.tasks["Notch"].selected = False
        task_list = self._prepare_pattern_move(("Microexpansion",), move_all=True)
        checked = task_list.GetCheckedStrings()
        centers = {pattern: pattern.center.value for task in self.tasks.values() for pattern in task.patterns}
        event = self._pattern_move_event()
        event.GetKeyCode.return_value = wx.WXK_RIGHT
        event.ShiftDown.return_value = False
        with patch("odemis.gui.cont.milling.save_project"):
            self.controller.on_char(event)
        for task in self.tasks.values():
            for pattern in task.patterns:
                original = centers[pattern]
                expected_x = original[0] + MOVE_DELTA_X_LONG if task.selected else original[0]
                self.assertAlmostEqual(pattern.center.value[0], expected_x, delta=1e-12)
                self.assertAlmostEqual(pattern.center.value[1], original[1], delta=1e-12)
        self.assertEqual(list(task_list.GetSelections()), [])
        self.assertEqual(task_list.GetCheckedStrings(), checked)
        self.assertIsNone(self.controller._active_spot_size_pattern)
        self.controller._update_milling_validation_message.assert_called_once_with()
        self.controller._update_mill_btn.assert_called_once_with()

    def test_ctrl_arrow_without_checked_patterns_preserves_selection(self) -> None:
        """Warn without clearing the selected row or moving any pattern."""
        for task in self.tasks.values():
            task.selected = False
        task_list = self._prepare_pattern_move(("Notch",), move_all=True)
        centers = {pattern: pattern.center.value for task in self.tasks.values() for pattern in task.patterns}
        event = self._pattern_move_event()
        event.GetKeyCode.return_value = wx.WXK_RIGHT
        with patch("odemis.gui.cont.milling.save_project") as save:
            self.controller.on_char(event)
        self.assertEqual(list(task_list.GetSelections()), [task_list.FindString("Notch")])
        self.assertIs(self.controller._active_spot_size_pattern, self.tasks["Notch"].patterns[0])
        self.assertEqual({pattern: pattern.center.value for pattern in centers}, centers)
        self.controller._show_pattern_selection_warning.assert_called_once_with("Select a milling pattern before moving it.")
        save.assert_not_called()

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
        self.assertNotIn(MILLING_ALIGNMENT_AREA_COLOR, colors)
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
            self.panel, choices=list(self.controller.milling_tasks), style=wx.LB_EXTENDED)
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

        task_list.SetSelection(wx.NOT_FOUND)
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

        task_list.SetSelection(wx.NOT_FOUND)
        task_list.SetSelection(task_list.FindString("Rough Milling 01"))
        task_list.SetSelection(task_list.FindString("Notch"))
        self.controller.rectangles_overlay.reset_mock()
        with patch(
            "odemis.gui.cont.milling.rectangle_pattern_to_shape",
            return_value=Mock(),
        ) as to_shape:
            MillingTaskController.draw_milling_tasks.__wrapped__(self.controller)

        self.assertTrue(all(
            item.kwargs["opacity"] == MILLING_OVERLAY_ACTIVE_OPACITY
            for item in to_shape.call_args_list
        ))
        labels = [
            item.args[0]
            for item in self.controller.rectangles_overlay.add_pattern_label.call_args_list
        ]
        self.assertIn("Rough Milling 01", labels)
        self.assertTrue(any(label.startswith("Notch · ") for label in labels))

        # Simulate Shift+arrow moving the list selection from a checked task to
        # an unchecked task while the cached active pattern still points to the
        # previously selected task.
        notch.selected = False
        self.controller._active_spot_size_pattern = rough.patterns[0]
        task_list.SetSelection(wx.NOT_FOUND)
        task_list.SetSelection(task_list.FindString("Notch"))
        self.controller.rectangles_overlay.reset_mock()
        with patch(
            "odemis.gui.cont.milling.rectangle_pattern_to_shape",
            return_value=Mock(),
        ) as to_shape:
            MillingTaskController.draw_milling_tasks.__wrapped__(self.controller)

        self.assertIs(self.controller._get_highlighted_task(), notch)
        self.assertTrue(to_shape.call_args_list)
        self.assertTrue(all(
            item.kwargs["opacity"] == MILLING_OVERLAY_INACTIVE_OPACITY
            for item in to_shape.call_args_list
        ))
        self.controller.rectangles_overlay.add_pattern_label.assert_not_called()

    def test_pattern_selection_warning(self) -> None:
        """Show a transient popup when no displayed pattern can move."""
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
        self.area_selected = Mock()
        self.overlay = MillingAlignmentAreaOverlay(
            cnvs=self.canvas, on_area_changed=self.area_changed, on_area_selected=self.area_selected)
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

    def _send_mouse_event(
            self, event_type: int, position: Vec, left_is_down: bool = False) -> wx.MouseEvent:
        """Dispatch a mouse event through the canvas event-handler chain.

        :param event_type: wx mouse event type.
        :param position: Event position in viewport pixels.
        :return: The dispatched event.
        """
        event = wx.MouseEvent(event_type)
        event.x, event.y = position
        event.SetLeftDown(left_is_down)
        event.SetEventObject(self.canvas)
        self.canvas.GetEventHandler().ProcessEvent(event)
        return event

    def _create_pattern_shape(self, view_points: Tuple[Vec, ...]) -> RectangleOverlay:
        """Create a noneditable rectangle at the supplied viewport corners."""
        shape = RectangleOverlay(self.canvas, show_selection_points=False, can_rotate=False)
        offset = self.canvas.get_half_buffer_size()
        shape.set_physical_sel([self.canvas.view_to_phys(point, offset) for point in view_points])
        shape._points = shape.get_physical_sel()
        shape.points.value = shape._points
        shape.is_created.value = True
        return shape

    def test_pattern_hit_testing_checks_beyond_four_nearest_centers(self) -> None:
        """Find an enclosing pattern behind five closer rectangles that miss the click."""
        tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        overlay = MillingPatternOverlay(self.canvas)
        for index in range(5):
            left = 200 + index
            decoy = self._create_pattern_shape(
                (Vec(left, 170), Vec(left + 10, 170), Vec(left + 10, 180), Vec(left, 180)))
            overlay.add_pattern_shape(decoy, "Ruler", tasks["Ruler"].patterns[0])
        target = self._create_pattern_shape((Vec(100, 100), Vec(350, 100), Vec(350, 350), Vec(100, 350)))
        pattern = tasks["Waffle Trench"].patterns[0]
        overlay.add_pattern_shape(target, "Waffle Trench", pattern)

        self.assertEqual(overlay.get_pattern_at((175, 175)), ("Waffle Trench", pattern))
        self.assertIsNone(overlay.get_pattern_at((50, 50)))
        overlay.clear()
        self.assertIsNone(overlay.get_pattern_at((175, 175)))

    def test_selection_changes_alignment_area_opacity(self) -> None:
        """Use the milling-pattern opacity convention for the alignment area."""
        context = Mock()
        with patch("odemis.gui.comp.overlay.milling.MillingRectangleOverlay.draw") as draw:
            self.shape.draw(context)
            self.assertAlmostEqual(
                self.shape.colour[3], MILLING_OVERLAY_INACTIVE_OPACITY)
            self.shape.selected.value = True
            self.shape.draw(context)

        self.assertAlmostEqual(
            self.shape.colour[3], MILLING_OVERLAY_ACTIVE_OPACITY)
        self.assertEqual(
            draw.call_args_list,
            [call(context, (0, 0), 1.0), call(context, (0, 0), 1.0)],
        )

    def test_selected_alignment_area_draws_only_corner_handles(self) -> None:
        """Show four handles after selection, leaving edge midpoints undecorated."""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 128, 128)
        context = cairo.Context(surface)
        points = (Vec(20, 20), Vec(100, 20), Vec(100, 100), Vec(20, 100))
        self.shape.draw_edges(context, *points)
        surface.flush()
        pixels = numpy.frombuffer(surface.get_data(), dtype=numpy.uint8).reshape(128, 128, 4)
        self.assertFalse(pixels.any())

        self.shape.selected.value = True
        self.shape.draw_edges(context, *points)
        surface.flush()
        for x, y in points:
            self.assertTrue(pixels[y, x].any())
        for x, y in ((60, 20), (100, 60), (60, 100), (20, 60)):
            self.assertFalse(pixels[y, x].any())

    def test_alignment_title_is_visible_only_when_selected(self) -> None:
        """Render the title after selection and leave it hidden otherwise."""
        width, height = (2 * value for value in self.canvas.get_half_buffer_size())
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        self.shape.name.value = "Alignment area"
        self.shape.draw_name_label(context)
        surface.flush()
        self.assertFalse(numpy.frombuffer(surface.get_data(), dtype=numpy.uint8).any())
        self.shape.selected.value = True
        self.shape.draw_name_label(context)
        surface.flush()
        self.assertTrue(numpy.frombuffer(surface.get_data(), dtype=numpy.uint8).any())

    def test_alignment_hover_preserves_focus_and_requires_selection_for_resize(self) -> None:
        """Keep viewport focus on hover and expose a resize cursor only after selection."""
        with patch.object(self.canvas, "SetFocus") as focus:
            self._send_mouse_event(wx.wxEVT_ENTER_WINDOW, Vec(100, 100))
            focus.assert_not_called()

        self._send_mouse_event(wx.wxEVT_MOTION, Vec(100, 100))
        self.assertIsNone(self.canvas.dynamic_cursor)
        self.shape.selected.value = True
        self.overlay._selected_shape = self.shape
        with patch.object(self.canvas, "set_dynamic_cursor", wraps=self.canvas.set_dynamic_cursor) as cursor:
            cases = ((Vec(100, 100), wx.CURSOR_SIZING), (Vec(250, 100), wx.CURSOR_SIZING),
                     (Vec(175, 175), wx.CURSOR_HAND))
            for position, expected_cursor in cases:
                with self.subTest(position=position):
                    cursor.reset_mock()
                    self._send_mouse_event(wx.wxEVT_MOTION, position)
                    cursor.assert_called_with(expected_cursor)
        self.assertIsNotNone(self.canvas.dynamic_cursor)

    def test_overlapping_pattern_blocks_alignment_interior_interaction(self) -> None:
        """Avoid an area drag cursor and deselect the area when a pattern owns the click."""
        pattern_overlay = MillingPatternOverlay(self.canvas)
        pattern_shape = self._create_pattern_shape((Vec(140, 140), Vec(210, 140), Vec(210, 210), Vec(140, 210)))
        pattern = RectanglePatternParameters(width=2e-6, height=2e-6, depth=1e-6)
        pattern_overlay.add_pattern_shape(pattern_shape, "Pattern", pattern)
        self.overlay._blocks_area_interaction = lambda position: pattern_overlay.get_pattern_at(position) is not None
        self.shape.selected.value = True
        self.overlay._selected_shape = self.shape
        self.canvas.set_dynamic_cursor(wx.CURSOR_HAND)
        motion = wx.MouseEvent(wx.wxEVT_MOTION)
        motion.SetPosition(wx.Point(175, 175))

        self.overlay.on_motion(motion)

        self.assertIsNone(self.canvas.dynamic_cursor)
        event = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
        event.SetPosition(wx.Point(175, 175))
        self.overlay.on_left_down(event)
        self.assertIsNone(self.overlay._selected_shape)
        self.assertFalse(self.shape.selected.value)
        self.assertFalse(self.canvas.left_dragging)
        self.assertTrue(event.GetSkipped())
        self.area_changed.assert_not_called()

    def test_stream_change_rebuilds_alignment_area(self) -> None:
        """Rebuild the displayed area from the current reference when its stream changes."""
        image = model.DataArray(numpy.zeros((1000, 1000)), metadata={model.MD_PIXEL_SIZE: (1e-6, 1e-6)})
        stream = StaticSEMStream("Reference", image)
        area = (0.2, 0.2, 0.6, 0.6)
        feature = SimpleNamespace(reference_image=image, milling_alignment_area=model.TupleVA(area))
        controller = MillingTaskController.__new__(MillingTaskController)
        controller._tab_data = SimpleNamespace(main=SimpleNamespace(currentFeature=model.VigilantAttribute(feature)))
        controller._get_reference_stream = lambda current: stream
        controller.canvas = self.canvas
        controller.alignment_area_overlay = self.overlay

        controller._redraw_alignment_area([stream])
        test.gui_loop()

        self.assertEqual(len(self.overlay._shapes.value), 1)
        rebuilt = self.overlay._shapes.value[0]
        self.assertIsNot(rebuilt, self.shape)
        self.assertFalse(rebuilt.selected.value)
        pixel_corners = ((200, 200), (600, 200), (600, 600), (200, 600))
        expected = [stream.getPhysicalCoordinates(point) for point in pixel_corners]
        numpy.testing.assert_allclose(rebuilt.get_physical_sel(), expected)

    def test_pattern_click_and_drag_win_over_alignment_area_interior(self) -> None:
        """Select an overlapping pattern and explain its special move gesture."""
        tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        tasks["Ruler"].selected = True
        task_list = wx.CheckListBox(self.panel, choices=list(tasks))
        self.addCleanup(task_list.Destroy)
        for index, task in enumerate(tasks.values()):
            task_list.Check(index, task.selected)
        checked = task_list.GetCheckedStrings()

        pattern_overlay = MillingPatternOverlay(self.canvas)
        self.canvas.add_world_overlay(pattern_overlay)
        pattern_shape = self._create_pattern_shape((Vec(140, 140), Vec(210, 140), Vec(210, 210), Vec(140, 210)))
        ruler_pattern = tasks["Ruler"].patterns[0]
        pattern_overlay.add_pattern_shape(
            pattern_shape, "Ruler", ruler_pattern)

        self.shape.selected.value = True
        self.overlay._selected_shape = self.shape
        controller = MillingTaskController.__new__(MillingTaskController)
        controller._panel = SimpleNamespace(milling_task_chk_list=task_list)
        controller._tab = SimpleNamespace(main_frame=Mock())
        controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(
                currentFeature=SimpleNamespace(
                    value=SimpleNamespace(reference_image=Mock()),
                ),
            ),
        )
        controller.milling_tasks = tasks
        controller.canvas = self.canvas
        controller.rectangles_overlay = pattern_overlay
        controller.alignment_area_overlay = self.overlay
        controller._active_spot_size_pattern = None
        controller.draw_milling_tasks = Mock()
        self.canvas.Bind(wx.EVT_LEFT_DOWN, controller.on_mouse_down)
        self.canvas.Bind(wx.EVT_MOTION, controller.on_mouse_motion)

        with patch("odemis.gui.cont.milling.show_message") as show_message, patch.object(
                self.canvas, "SetFocus", wraps=self.canvas.SetFocus) as focus:
            event = self._send_mouse_event(wx.wxEVT_LEFT_DOWN, Vec(175, 175))
            focus.assert_called_once_with()
            self.assertIs(controller._active_spot_size_pattern, ruler_pattern)
            self.assertFalse(self.shape.selected.value)
            self._send_mouse_event(
                wx.wxEVT_MOTION, Vec(175 + PATTERN_DRAG_HINT_THRESHOLD - 1, 175), left_is_down=True)
            show_message.assert_not_called()
            self._send_mouse_event(
                wx.wxEVT_MOTION,
                Vec(175 + PATTERN_DRAG_HINT_THRESHOLD, 175),
                left_is_down=True,
            )
            self._send_mouse_event(
                wx.wxEVT_MOTION, Vec(175 + PATTERN_DRAG_HINT_THRESHOLD + 1, 175), left_is_down=True)
            self._send_mouse_event(wx.wxEVT_LEFT_UP, Vec(180, 175))

        expected_index = task_list.FindString("Ruler")
        self.assertEqual(task_list.GetSelection(), expected_index)
        self.assertTrue(task_list.IsChecked(expected_index))
        self.assertEqual(task_list.GetCheckedStrings(), checked)
        self.assertIs(controller._active_spot_size_pattern, ruler_pattern)
        self.assertIsNone(controller._pattern_drag_start)
        self.assertFalse(self.shape.selected.value)
        self.assertFalse(self.canvas.left_dragging)
        self.assertFalse(event.GetSkipped())
        show_message.assert_called_once_with(
            controller._tab.main_frame,
            title="Pattern movement",
            message="To move milling patterns, use Shift+Ctrl+click.",
            timeout=PATTERN_MOVE_WARNING_TIMEOUT,
            level=logging.WARNING,
        )
        controller.draw_milling_tasks.assert_called_once_with(
            redraw_alignment_area=False)

    def test_dragging_area_does_not_pan_canvas(self) -> None:
        """Move the area while leaving the reference-image canvas stationary."""
        self._send_mouse_event(wx.wxEVT_LEFT_DOWN, Vec(175, 175))
        self.area_selected.assert_called_once_with()
        self.assertFalse(self.canvas.left_dragging)

        self._send_mouse_event(wx.wxEVT_MOTION, Vec(195, 185))
        self._send_mouse_event(wx.wxEVT_LEFT_UP, Vec(195, 185))

        self.assertEqual(self.canvas.drag_shift, (0, 0))
        self.assertEqual(self.shape.v_point1, Vec(120, 110))
        self.assertEqual(self.shape.v_point3, Vec(270, 260))
        self.assertTrue(self.shape.selected.value)
        self.assertGreaterEqual(self.area_changed.call_count, 2)
        self.assertTrue(self.area_changed.call_args.kwargs["commit"])

    def test_reference_image_drag_keeps_canvas_cursor_until_release(self) -> None:
        """Leave cursor ownership with the canvas throughout an image drag."""
        self._send_mouse_event(wx.wxEVT_LEFT_DOWN, Vec(300, 300))
        drag_cursor = self.canvas.dynamic_cursor

        self.assertTrue(self.canvas.left_dragging)
        self.assertIsNotNone(drag_cursor)

        self._send_mouse_event(wx.wxEVT_MOTION, Vec(320, 310))

        self.assertTrue(self.canvas.left_dragging)
        self.assertIs(self.canvas.dynamic_cursor, drag_cursor)

        # This lightweight canvas has no microscope view to recenter on release.
        with patch.object(self.canvas, "recenter_buffer"), patch.object(
                self.canvas, "update_drawing"):
            self._send_mouse_event(wx.wxEVT_LEFT_UP, Vec(320, 310))

        self.assertFalse(self.canvas.left_dragging)
        self.assertIsNone(self.canvas.dynamic_cursor)

    def test_feature_overlay_does_not_reset_reference_image_drag_cursor(self) -> None:
        """Keep the canvas drag cursor when the feature overlay sees motion."""
        feature_overlay = CryoFeatureOverlay.__new__(CryoFeatureOverlay)
        feature_overlay.active = model.BooleanVA(True)
        feature_overlay._left_dragging = False
        feature_overlay._right_dragging = False
        feature_overlay.cnvs = Mock(left_dragging=True)
        event = Mock()
        event.Position = wx.Point(200, 200)

        feature_overlay.on_motion(event)

        feature_overlay.cnvs.reset_dynamic_cursor.assert_not_called()
        feature_overlay.cnvs.set_default_cursor.assert_not_called()
        event.Skip.assert_called_once_with()

    def test_resizing_area_does_not_pan_canvas(self) -> None:
        """Move then resize without a selection callback or canvas pan."""
        self.overlay._on_area_selected = None
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
        self.area_selected.assert_not_called()
        self.assertGreaterEqual(self.area_changed.call_count, 2)
        self.assertTrue(self.area_changed.call_args.kwargs["commit"])


class MillingAlignmentConstraintTestCase(unittest.TestCase):
    """Check alignment resizing constraints without microscope hardware."""

    def _create_controller(
            self, area: Tuple[float, float, float, float]) -> Tuple[MillingTaskController, SimpleNamespace]:
        """Create a controller whose stream uses physical pixels as coordinates."""
        reference_image = SimpleNamespace(shape=(1000, 1000))
        feature = SimpleNamespace(
            reference_image=reference_image,
            milling_alignment_area=model.TupleVA(area),
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
        controller._update_milling_validation_message = Mock()
        controller._update_mill_btn = Mock()
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
            (1, Vec(200, 250), (0.2, 0.25, 0.7, 0.7)),
            (2, Vec(800, 250), (0.3, 0.25, 0.8, 0.7)),
            (3, Vec(750, 800), (0.3, 0.3, 0.75, 0.8)),
            (4, Vec(200, 800), (0.2, 0.3, 0.7, 0.8)),
        )
        for corner_index, dragged_point, expected in cases:
            with self.subTest(corner_index=corner_index):
                controller, feature = self._create_controller((0.3, 0.3, 0.7, 0.7))
                shape = self._create_resize_shape(corner_index, dragged_point)

                controller._update_alignment_area_from_shape(shape, commit=False)

                for actual_value, expected_value in zip(
                        feature.milling_alignment_area.value, expected):
                    self.assertAlmostEqual(actual_value, expected_value)

    def test_resizing_stays_steady_at_minimum_size(self) -> None:
        """Keep the fixed corner still when the pointer requests a smaller area."""
        controller, feature = self._create_controller((0.1, 0.1, 0.5, 0.5))
        shape = self._create_resize_shape(3, Vec(200, 200))

        controller._update_alignment_area_from_shape(shape, commit=False)
        first_area = feature.milling_alignment_area.value
        shape.get_physical_sel.return_value[2] = Vec(250, 250)
        controller._update_alignment_area_from_shape(shape, commit=False)

        for actual_value, expected_value in zip(
                feature.milling_alignment_area.value, first_area):
            self.assertAlmostEqual(actual_value, expected_value)
        minimum_side = MIN_MILLING_ALIGNMENT_AREA_PIXELS ** 0.5 / 1000
        for actual_value, expected_value in zip(
                first_area, (0.1, 0.1, 0.1 + minimum_side, 0.1 + minimum_side)):
            self.assertAlmostEqual(actual_value, expected_value)

    def test_small_legacy_reference_does_not_draw_alignment_area(self) -> None:
        """Leave an invalid legacy reference visible without drawing its area."""
        reference_image = SimpleNamespace(shape=(200, 300))
        feature = SimpleNamespace(
            reference_image=reference_image,
            milling_alignment_area=model.TupleVA((0.1, 0.1, 0.6, 0.6)),
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


class MillingValidationTestCase(unittest.TestCase):
    """Check milling constraints without a microscope."""

    def setUp(self) -> None:
        """Create milling tasks and one checked feature."""
        self.tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        self.task = self.tasks["Ruler"]
        self.task.selected = True
        self.ruler = self.task.patterns[0]
        reference_image = SimpleNamespace(
            shape=(1000, 1000),
            metadata={model.MD_PIXEL_SIZE: (1e-6, 1e-6)},
        )
        feature = SimpleNamespace(
            name=model.StringVA("Feature 1"),
            milling_tasks=self.tasks,
            milling_alignment_area=model.TupleVA((0.45, 0.45, 0.55, 0.55)),
            reference_image=reference_image,
        )
        self.feature = feature
        self.controller = MillingTaskController.__new__(MillingTaskController)
        self.controller._tab_data = SimpleNamespace(
            main=SimpleNamespace(features=model.ListVA([feature])))
        self.feature_list = Mock()
        self.feature_list.GetCount.return_value = 1
        self.feature_list.IsChecked.return_value = True
        self.controller._panel = SimpleNamespace(
            workflow_features_chk_list=self.feature_list,
            txt_automated_milling_status=Mock(),
        )

    def _prepare_overlap_validation(self, metadata: dict) -> StaticSEMStream:
        """Use real stream coordinates and button validation for interactive edits."""
        reference_image = model.DataArray(numpy.zeros((1000, 1000), dtype=numpy.uint8), metadata=metadata)
        self.feature.reference_image = reference_image
        stream = StaticSEMStream("Reference", reference_image)
        for task in self.tasks.values():
            task.selected = False
        self.tasks["Rough Milling 01"].selected = True
        self.controller.milling_tasks = self.tasks
        self.controller.selected_tasks = model.ListVA(["Rough Milling 01"])
        self.controller.valid_patterns = model.BooleanVA(True)
        self.controller._tab_data.main.currentFeature = SimpleNamespace(value=self.feature)
        self.controller._tab_data.main.is_acquiring = model.BooleanVA(False)
        self.controller._get_reference_stream = Mock(return_value=stream)
        self.controller.draw_milling_tasks = Mock()
        self.controller._update_milling_time = Mock()
        self.controller.canvas = Mock()
        for name in ("btn_run_milling", "btn_run_automated_milling",
                     "txt_milling_est_time", "txt_automated_milling_est_time"):
            setattr(self.controller._panel, name, Mock())
        self.controller._update_mill_btn = lambda: MillingTaskController._update_mill_btn.__wrapped__(self.controller)
        return stream

    def _assert_overlap_state(self, overlaps: bool) -> None:
        """Check the displayed warning and both milling buttons."""
        message = "Alignment area overlaps a pattern for Feature 1." if overlaps else ""
        self.controller._panel.txt_automated_milling_status.SetLabel.assert_called_with(message)
        self.controller._panel.btn_run_milling.Enable.assert_called_with(not overlaps)
        self.controller._panel.btn_run_automated_milling.Enable.assert_called_with(not overlaps)

    def test_alignment_polygon_matches_displayed_reference_coordinates(self) -> None:
        """Validate the drawn area with rotation, shear, and metadata corrections."""
        cases = (
            {},
            {model.MD_ROTATION: math.pi / 2},
            {model.MD_ROTATION: math.pi},
            {model.MD_SHEAR: 0.3},
            {model.MD_PIXEL_SIZE_COR: (0.5, 1.5), model.MD_POS_COR: (40e-6, -25e-6)},
            {model.MD_ROTATION: 0.7, model.MD_ROTATION_COR: 0.2,
             model.MD_SHEAR: 0.3, model.MD_SHEAR_COR: 0.1},
        )
        self.feature.milling_alignment_area.value = (0.1, 0.35, 0.4, 0.7)
        for corrections in cases:
            with self.subTest(metadata=corrections):
                metadata = {model.MD_POS: (200e-6, -100e-6), model.MD_PIXEL_SIZE: (1e-6, 2e-6)}
                metadata.update(corrections)
                original_metadata = dict(metadata)
                reference_image = model.DataArray(numpy.zeros((1000, 1000), dtype=numpy.uint8), metadata=metadata)
                self.feature.reference_image = reference_image
                stream = StaticSEMStream("Reference", reference_image)
                shape = Mock(points=SimpleNamespace(value=None))
                update_milling_alignment_area_shape(shape, self.feature.milling_alignment_area.value, stream)
                corners = shape.set_physical_sel.call_args.args[0]
                origin_x, origin_y = metadata[model.MD_POS]
                expected = Polygon([(x - origin_x, y - origin_y) for x, y in corners])
                actual = _get_alignment_area_polygon(self.feature)
                self.assertLess(actual.symmetric_difference(expected).area / expected.area, 1e-10)
                self.assertEqual(reference_image.metadata, original_metadata)

    def test_rotated_reference_does_not_report_overlap_on_opposite_side(self) -> None:
        """Keep the screenshot's separated area valid on a 180-degree reference image."""
        self._prepare_overlap_validation(
            {model.MD_POS: (-0.002340024, -0.026692104877927),
             model.MD_PIXEL_SIZE: (9.765625e-8, 9.765625e-8), model.MD_ROTATION: math.pi})
        self.feature.reference_image = model.DataArray(
            numpy.zeros((1024, 1024), dtype=numpy.uint8), metadata=self.feature.reference_image.metadata)
        self.feature.milling_alignment_area.value = (
            0.5922123563641044, 0.3736441486539639, 0.8474624782933127, 0.7409309467617526)
        for name in ("Microexpansion", "Rough Milling 01", "Rough Milling 02", "Polishing 01", "Polishing 02"):
            self.tasks[name].selected = True
            self.tasks[name].patterns[0].center.value = (3.85135135135147e-5, 3.3783783783755683e-6)
        self.assertIsNone(self.controller._get_alignment_area_overlap())
        self.controller._update_milling_validation_message()
        self.controller._update_mill_btn()
        self._assert_overlap_state(False)

    def test_touching_alignment_boundary_is_not_overlap(self) -> None:
        """Allow a pattern to touch the area without entering its interior."""
        for rotation, center in ((0, (5e-6, 0)), (math.pi, (-5e-6, 0))):
            with self.subTest(rotation=rotation):
                self._prepare_overlap_validation(
                    {model.MD_POS: (0, 0), model.MD_PIXEL_SIZE: (1e-6, 1e-6), model.MD_ROTATION: rotation})
                self.feature.milling_alignment_area.value = (0.2, 0.3, 0.5, 0.7)
                self.tasks["Rough Milling 01"].patterns[0].center.value = center
                self.assertIsNone(self.controller._get_alignment_area_overlap())
                direction = -1 if rotation == 0 else 1
                self.tasks["Rough Milling 01"].patterns[0].center.value = (center[0] + direction * 1e-9, center[1])
                self.assertEqual(self.controller._get_alignment_area_overlap(), ("Feature 1", "Rough Milling 01"))

    def test_moving_pattern_updates_overlap_with_reference_at_default_position(self) -> None:
        """Move without an anchor and refresh overlap when the reference has no saved position."""
        self._prepare_overlap_validation({model.MD_PIXEL_SIZE: (1e-6, 1e-6)})
        pattern = self.tasks["Rough Milling 01"].patterns[0]
        for center, overlaps in (((0, 0), True), ((200e-6, 200e-6), False)):
            with self.subTest(center=center), patch("odemis.gui.cont.milling.save_project"):
                self.controller.draw_milling_tasks.reset_mock()
                self.controller._move_patterns([pattern], center)
                self.assertEqual(pattern.center.value, center)
                self.controller.draw_milling_tasks.assert_called_once_with()
                expected = ("Feature 1", "Rough Milling 01") if overlaps else None
                self.assertEqual(self.controller._get_alignment_area_overlap(), expected)
                self._assert_overlap_state(overlaps)

    def test_ctrl_shift_click_updates_overlap_in_both_directions(self) -> None:
        """Moving selected or all patterns into and out of the area updates both buttons."""
        stream = self._prepare_overlap_validation(
            {model.MD_POS: (200e-6, -100e-6), model.MD_PIXEL_SIZE: (1e-6, 1e-6),
             model.MD_ROTATION: math.pi / 2})
        self.feature.milling_alignment_area.value = (0.1, 0.35, 0.4, 0.7)
        self.controller.allow_milling_pattern_move = True
        self.controller._clear_milling_task_selection = Mock()
        self.controller._panel.chk_move_all_patterns = Mock()
        task_list = self.controller._panel.milling_task_chk_list = Mock()
        task_list.GetSelections.return_value = [0]
        task_list.GetString.return_value = "Rough Milling 01"
        self.controller.canvas.get_half_buffer_size.return_value = (0, 0)
        event = Mock()
        event.GetEventObject.return_value = self.controller.canvas
        event.ShiftDown.return_value = True
        event.ControlDown.return_value = True
        pattern = self.tasks["Rough Milling 01"].patterns[0]
        for move_all in (False, True):
            with self.subTest(move_all=move_all), patch("odemis.gui.cont.milling.save_project"):
                self.controller._panel.chk_move_all_patterns.GetValue.return_value = move_all
                for pixel_center, overlaps in (((250, 525), True), ((750, 250), False)):
                    self.controller.canvas.view_to_phys.return_value = stream.getPhysicalCoordinates(pixel_center)
                    self.controller.on_mouse_down(event)
                    self._assert_overlap_state(overlaps)
                    self.assertEqual(self.controller._get_alignment_area_overlap() is not None, overlaps)
                self.assertNotEqual(pattern.center.value, (0, 0))

    def test_alignment_edits_update_overlap_in_both_directions(self) -> None:
        """Dragging or resizing the area into and away from patterns refreshes validation."""
        stream = self._prepare_overlap_validation(
            {model.MD_POS: (200e-6, -100e-6), model.MD_PIXEL_SIZE: (1e-6, 1e-6),
             model.MD_ROTATION: math.pi / 2})
        self.tasks["Rough Milling 01"].patterns[0].center.value = (-250e-6, 25e-6)
        initial = (0.1, 0.35, 0.4, 0.7)
        cases = ((SEL_MODE_DRAG, (0.375, 0.075, 0.675, 0.425)),
                 (SEL_MODE_EDIT, (0.1, 0.1, 0.6, 0.7)))
        for mode, overlapping in cases:
            for commit in (False, True):
                with self.subTest(mode=mode, commit=commit), patch("odemis.gui.cont.milling.save_project"):
                    self.feature.milling_alignment_area.value = initial
                    self.controller._update_milling_validation_message()
                    self.controller._update_mill_btn()
                    self._assert_overlap_state(False)
                    shape = Mock(points=SimpleNamespace(value=None), interaction_mode=mode, edit_v_point_idx=2)
                    for area, overlaps in ((overlapping, True), (initial, False)):
                        left, top, right, bottom = area
                        pixel_corners = ((left * 1000, top * 1000), (right * 1000, top * 1000),
                                         (right * 1000, bottom * 1000), (left * 1000, bottom * 1000))
                        shape.get_physical_sel.return_value = [stream.getPhysicalCoordinates(point) for point in pixel_corners]
                        self.controller._update_alignment_area_from_shape(shape, commit=commit)
                        self._assert_overlap_state(overlaps)

    def test_small_legacy_reference_disables_milling_and_shows_warning(self) -> None:
        """Require reacquisition without preventing the project from loading."""
        self._prepare_overlap_validation({model.MD_PIXEL_SIZE: (1e-6, 1e-6)})
        self.feature.reference_image = model.DataArray(numpy.zeros((200, 300)), metadata={})
        self.assertEqual(self.controller._get_invalid_alignment_reference(), "Feature 1")
        self.controller._update_milling_validation_message()
        self.controller._update_mill_btn()
        self.controller._panel.txt_automated_milling_status.SetLabel.assert_called_with(
            "Acquire a higher-resolution reference image for Feature 1.")
        self.controller._panel.btn_run_milling.Enable.assert_called_with(False)
        self.controller._panel.btn_run_automated_milling.Enable.assert_called_with(False)

    def test_legacy_features_require_new_references_before_automation(self):
        """Name only checked features and stop before accessing any microscope hardware."""
        features = [CryoFeature(name, {}, {}) for name in ("Legacy 1", "Unchecked", "Legacy 2", "Recorded")]
        features[-1].system_state = {"ion-beam": {"probeCurrent": [20e-12, "A"]}}
        self.controller._tab_data.main.features.value = features
        self.feature_list.IsChecked.side_effect = lambda index: index != 1
        automation = AutomatedMillingController.__new__(AutomatedMillingController)
        automation._tab_data = self.controller._tab_data
        automation._panel = self.controller._panel
        with patch("odemis.gui.cont.milling.fibsemos.FIBSEMOS_INSTALLED", True), \
             patch("odemis.gui.cont.milling.wx.MessageBox") as message:
            automation._run_automated_milling(None)
        text = message.call_args.args[0]
        self.assertIn("Legacy 1, Legacy 2", text)
        self.assertIn("SAVE POSITION", text)
        self.assertNotIn("Unchecked", text)
        self.assertNotIn("Recorded", text)

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

    def test_reference_stream_matches_current_feature(self) -> None:
        """Find the current reference at startup and reject streams from the previous feature."""
        matching = SimpleNamespace(raw=[self.reference_image])
        stale = SimpleNamespace(raw=[object()])
        cases = ((None, [matching], matching), (stale, [stale, matching], matching), (stale, [stale], None))
        for acquired, displayed, expected in cases:
            with self.subTest(acquired=acquired, displayed=displayed):
                self.controller.acq_cont.stream = acquired
                self.controller.viewport = SimpleNamespace(
                    view=SimpleNamespace(getStreams=lambda: displayed))
                self.assertIs(self.controller._get_reference_stream(self.feature), expected)


if __name__ == "__main__":
    unittest.main()
