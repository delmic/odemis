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

        marker = (2e-6, -3e-6)
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


if __name__ == "__main__":
    unittest.main()
