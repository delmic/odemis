# -*- coding: utf-8 -*-
"""
@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

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
from dataclasses import replace
from types import SimpleNamespace

import wx

import odemis.gui.layout as layout
from odemis import model
from odemis.acq.feature import (
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
    FEATURE_READY_TO_MILL,
    FEATURE_ROUGH_MILLED,
    CryoFeature,
)
from odemis.gui.comp.viewport import FeatureOverviewViewport, LiveViewport
from odemis.gui.cont.features import CryoFeatureController
from odemis.gui.cont.milling import AutomatedMillingController
from odemis.gui.layout.components.panel_tab_fibsem import PnlTabFibsem


class PnlTabFibsemTest(unittest.TestCase):
    """Verify the pure-Python FIBSEM layout."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the wx application used by the layout tests."""
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        """Destroy the wx application after the layout tests."""
        cls.app.Destroy()

    def setUp(self) -> None:
        """Create a fresh FIBSEM panel."""
        self.frame = wx.Frame(None)
        self.panel = PnlTabFibsem(self.frame)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.panel, proportion=1, flag=wx.EXPAND)
        self.frame.SetSizer(sizer)
        self.frame.Maximize(True)
        self.frame.Show()
        wx.Yield()

    def tearDown(self) -> None:
        """Drain callbacks before destroying the FIBSEM panel host frame."""
        wx.Yield()
        self.frame.Destroy()
        wx.Yield()

    def test_controller_widget_api_and_initial_state(self) -> None:
        """Expose controller controls with their expected initial state."""
        attributes = (
            "secom_toolbar",
            "lbl_secom_view_all",
            "btn_secom_view_all",
            "lbl_secom_view_tl",
            "btn_secom_view_tl",
            "lbl_secom_view_tr",
            "btn_secom_view_tr",
            "lbl_secom_view_bl",
            "btn_secom_view_bl",
            "lbl_secom_view_br",
            "btn_secom_view_br",
            "btn_log",
            "pnl_secom_grid",
            "vp_secom_tl",
            "vp_secom_tr",
            "vp_secom_bl",
            "vp_secom_br",
            "scr_win_right",
            "btn_delete_feature",
            "cmb_features",
            "btn_create_move_feature",
            "cmb_feature_status",
            "btn_go_to_feature",
            "btn_feature_save_position",
            "btn_switch_sem_imaging",
            "btn_switch_milling",
            "ctrl_milling_angle",
            "fp_settings_secom_optical",
            "fp_secom_streams",
            "pnl_secom_streams",
            "fp_acquisitions",
            "streams_chk_list",
            "chkbox_save_acquisition",
            "txt_filename",
            "btn_cryosecom_change_file",
            "btn_cryosecom_acquire",
            "txt_cryosecom_est_time",
            "gauge_cryosecom_acq",
            "txt_cryosecom_left_time",
            "btn_cryosecom_acqui_cancel",
            "btn_acquire_all",
            "btn_acquire_overview",
            "btn_tdct",
            "fp_acquired",
            "pnl_cryosecom_acquired",
            "fp_automation",
            "workflow_features_chk_list",
            "workflow_task_chk_list",
            "btn_run_automated_milling",
            "txt_automated_milling_est_time",
            "gauge_automated_milling",
            "txt_automated_milling_left_time",
            "btn_automated_milling_cancel",
            "txt_automated_milling_status",
            "fp_milling",
            "milling_task_chk_list",
            "chk_move_all_patterns",
            "btn_snap_patterns_to_feature",
            "btn_run_milling",
            "txt_milling_est_time",
            "gauge_milling_series",
            "txt_milling_series_left_time",
            "btn_milling_cancel",
            "pnl_patterns",
        )

        for attribute in attributes:
            with self.subTest(attribute=attribute):
                self.assertTrue(hasattr(self.panel, attribute))

        self.assertEqual(
            self.panel.pnl_secom_grid.viewports,
            (
                self.panel.vp_secom_tl,
                self.panel.vp_secom_tr,
                self.panel.vp_secom_bl,
                self.panel.vp_secom_br,
            ),
        )
        self.assertIsInstance(self.panel.vp_secom_tl, LiveViewport)
        self.assertIsInstance(self.panel.vp_secom_tr, LiveViewport)
        self.assertIsInstance(
            self.panel.vp_secom_bl,
            FeatureOverviewViewport,
        )
        self.assertIsInstance(self.panel.vp_secom_br, LiveViewport)
        self.assertFalse(self.panel.txt_cryosecom_est_time.IsShown())
        self.assertFalse(self.panel.txt_cryosecom_left_time.IsShown())
        self.assertFalse(self.panel.btn_run_milling.IsShown())
        self.assertFalse(self.panel.txt_milling_est_time.IsShown())
        self.assertFalse(self.panel.gauge_milling_series.IsShown())
        self.assertFalse(self.panel.txt_milling_series_left_time.IsShown())
        self.assertFalse(self.panel.btn_milling_cancel.IsShown())
        self.assertEqual(
            self.panel.chk_move_all_patterns.GetForegroundColour(),
            wx.Colour(layout.theme.text_primary),
        )
        self.assertTrue(self.panel.chk_move_all_patterns.GetValue())
        self.assertTrue(self.panel.milling_task_chk_list.GetWindowStyleFlag() & wx.LB_EXTENDED)
        self.assertTrue(self.panel.workflow_features_chk_list.GetWindowStyleFlag() & wx.LB_EXTENDED)
        selection_tip = self.panel.milling_task_chk_list.GetToolTipText()
        self.assertIn("Ctrl+click: select multiple", selection_tip)
        self.assertEqual(
            self.panel.btn_snap_patterns_to_feature.GetLabel(),
            "Recenter on Feature",
        )
        self.assertEqual(self.panel.gauge_cryosecom_acq.GetRange(), 100)
        self.assertEqual(self.panel.gauge_automated_milling.GetRange(), 100)
        self.assertEqual(self.panel.gauge_milling_series.GetRange(), 100)

    def _create_automation_controller(self) -> AutomatedMillingController:
        """Connect real feature models to the panel without microscope hardware."""
        self.features = [CryoFeature(f"Feature {i}", {}, {}, milling_tasks={}) for i in range(3)]
        for feature in self.features:
            feature.status.value = FEATURE_READY_TO_MILL
        self.main = SimpleNamespace(
            features=model.ListVA(self.features),
            currentFeature=model.VigilantAttribute(self.features[0]),
        )
        tab = SimpleNamespace(milling_task_controller=SimpleNamespace(
            _update_milling_validation_message=lambda: None,
            _update_mill_btn=lambda: None,
        ))
        self.controller = AutomatedMillingController(SimpleNamespace(main=self.main), self.panel, tab)
        wx.Yield()
        return self.controller

    def test_workflow_step_interaction_clears_highlight_without_changing_checks(self) -> None:
        """Keep the original checklist while discarding its row-selection state."""
        self._create_automation_controller()
        task_list = self.panel.workflow_task_chk_list
        task_list.Check(0, False)
        for event_type in (wx.wxEVT_LISTBOX, wx.wxEVT_CHECKLISTBOX):
            task_list.SetSelection(0)
            event = wx.CommandEvent(event_type, task_list.GetId())
            event.SetInt(0)
            task_list.GetEventHandler().ProcessEvent(event)

            self.assertEqual(task_list.GetSelection(), wx.NOT_FOUND)
            self.assertEqual(list(task_list.GetCheckedItems()), [1])

    def _send_feature_selection(self, indices) -> None:
        """Dispatch a native selection event after setting the highlighted rows."""
        feature_list = self.panel.workflow_features_chk_list
        feature_list.SetSelection(wx.NOT_FOUND)
        for index in indices:
            feature_list.SetSelection(index)
        event = wx.CommandEvent(wx.wxEVT_LISTBOX, feature_list.GetId())
        event.SetInt(indices[-1] if indices else wx.NOT_FOUND)
        feature_list.GetEventHandler().ProcessEvent(event)
        wx.Yield()

    def _send_feature_key(self, key: int, control: bool = False) -> None:
        """Dispatch keyboard input through the real checklist's bound handlers."""
        feature_list = self.panel.workflow_features_chk_list
        event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(key)
        event.SetControlDown(control)
        feature_list.GetEventHandler().ProcessEvent(event)
        wx.Yield()

    def test_dropdown_navigation_replaces_bulk_selection(self) -> None:
        """Changing or reselecting the active dropdown feature restores one preview."""
        self._create_automation_controller()
        feature_list = self.panel.workflow_features_chk_list
        feature_list.Check(1, False)
        dropdown = self.panel.cmb_features
        dropdown.Clear()
        for feature in self.features:
            dropdown.Append(feature.name.value, feature)
        feature_controller = CryoFeatureController.__new__(CryoFeatureController)
        feature_controller._tab_data_model = self.controller._tab_data
        feature_controller._panel = self.panel
        dropdown.Bind(wx.EVT_COMBOBOX, feature_controller._on_cmb_features_change)

        for index in (1, 2):
            self._send_feature_selection([2])
            self._send_feature_selection([0, 1, 2])
            dropdown.SetSelection(index)
            event = wx.CommandEvent(wx.wxEVT_COMBOBOX, dropdown.GetId())
            dropdown.GetEventHandler().ProcessEvent(event)
            wx.Yield()

            self.assertIs(self.main.currentFeature.value, self.features[index])
            self.assertEqual(list(feature_list.GetSelections()), [index])
            self.assertEqual(list(feature_list.GetCheckedItems()), [0, 2])
            self.assertFalse(self.panel.pnl_milling_selection_hint.IsShown())

    def test_feature_removal_keeps_selection_valid(self) -> None:
        """Keep an active feature while any remain, and clear it after the last deletion."""
        self._create_automation_controller()
        self._send_feature_selection([2])
        self.main.features.value = self.features[:2]
        wx.Yield()
        self.assertIs(self.main.currentFeature.value, self.features[0])
        self.assertEqual(list(self.panel.workflow_features_chk_list.GetSelections()), [0])

        self.main.features.value = []
        wx.Yield()
        self.assertIsNone(self.main.currentFeature.value)
        self.assertEqual(self.panel.workflow_features_chk_list.GetCount(), 0)

    def test_new_feature_is_checked_and_selected(self) -> None:
        """Queue a new Active feature and preserve check choices as it is prepared."""
        self._create_automation_controller()
        feature_list = self.panel.workflow_features_chk_list
        feature_list.Check(1, False)
        self._send_feature_selection([0, 1])
        feature = CryoFeature("New feature", {}, {}, milling_tasks={})
        self.main.features.value.append(feature)
        self.main.currentFeature.value = feature
        wx.Yield()

        self.assertEqual(feature.status.value, FEATURE_ACTIVE)
        self.assertEqual(list(feature_list.GetSelections()), [3])
        self.assertEqual(list(feature_list.GetCheckedItems()), [0, 2, 3])
        self.assertFalse(self.panel.pnl_milling_selection_hint.IsShown())

        feature.status.value = FEATURE_READY_TO_MILL
        wx.Yield()
        self.assertEqual(list(feature_list.GetCheckedItems()), [0, 2, 3])

        feature_list.Check(3, False)
        feature.status.value = FEATURE_ROUGH_MILLED
        wx.Yield()
        self.assertEqual(list(feature_list.GetCheckedItems()), [0, 2])

    def test_status_refresh_preserves_bulk_highlights_checks_and_active_feature(self) -> None:
        """Refreshing labels keeps the active feature separate from bulk row selection."""
        self._create_automation_controller()
        feature_list = self.panel.workflow_features_chk_list
        feature_list.Check(1, False)
        self._send_feature_selection([2])
        self._send_feature_selection([0, 1])

        self.features[0].status.value = FEATURE_ROUGH_MILLED
        wx.Yield()

        self.assertEqual(list(feature_list.GetCheckedItems()), [0, 2])
        self.assertEqual(list(feature_list.GetSelections()), [0, 1])
        self.assertIs(self.main.currentFeature.value, self.features[2])
        self.assertTrue(self.panel.pnl_milling_selection_hint.IsShown())
        self.assertIn(FEATURE_ROUGH_MILLED, feature_list.GetString(0))

    def test_ctrl_a_and_space_keep_bulk_selection_separate_from_active_feature(self) -> None:
        """Ctrl+A highlights rows; Space changes their checks; both preserve the active feature."""
        self._create_automation_controller()
        feature_list = self.panel.workflow_features_chk_list
        feature_list.Check(1, False)
        self._send_feature_selection([2])
        self._send_feature_key(ord("A"), control=True)
        self.assertEqual(list(feature_list.GetSelections()), [0, 1, 2])
        self.assertEqual(list(feature_list.GetCheckedItems()), [0, 2])
        self.assertTrue(self.panel.pnl_milling_selection_hint.IsShown())
        for expected in ([0, 1, 2], []):
            self._send_feature_key(wx.WXK_SPACE)
            self.assertEqual(list(feature_list.GetCheckedItems()), expected)
            self.assertIs(self.main.currentFeature.value, self.features[2])

        self._send_feature_key(ord("A"), control=True)
        self.assertEqual(list(feature_list.GetSelections()), [2])
        self.assertEqual(list(feature_list.GetCheckedItems()), [])
        self.assertFalse(self.panel.pnl_milling_selection_hint.IsShown())

    def test_space_skips_ineligible_bulk_selected_features(self) -> None:
        """Bulk checks only include eligible features while preserving the highlight set."""
        self._create_automation_controller()
        feature_list = self.panel.workflow_features_chk_list
        self.features[0].status.value = FEATURE_ACTIVE
        self.features[2].status.value = FEATURE_DEACTIVE
        wx.Yield()
        self._send_feature_selection([0, 1, 2])
        for expected in ([], [0, 1]):
            self._send_feature_key(wx.WXK_SPACE)
            self.assertEqual(list(feature_list.GetCheckedItems()), expected)
            self.assertEqual(list(feature_list.GetSelections()), [0, 1, 2])

    def test_queued_navigation_cannot_erase_newer_bulk_highlights(self) -> None:
        """Pending navigation callbacks must not collapse a later Ctrl+A interaction."""
        self._create_automation_controller()
        self.main.currentFeature.value = self.features[2]
        self._send_feature_key(ord("A"), control=True)

        self.assertEqual(list(self.panel.workflow_features_chk_list.GetSelections()), [0, 1, 2])
        self.assertIs(self.main.currentFeature.value, self.features[2])
        self.assertTrue(self.panel.pnl_milling_selection_hint.IsShown())

    def test_empty_milling_selection_does_not_start_a_workflow(self) -> None:
        """Mass unchecking must safely prevent workflow startup with no selected features."""
        controller = self._create_automation_controller()
        for feature_checks, step_checks in (([], [0, 1]), ([0, 1, 2], [])):
            self.panel.workflow_features_chk_list.SetCheckedItems(feature_checks)
            self.panel.workflow_task_chk_list.SetCheckedItems(step_checks)
            controller._run_automated_milling(None)
            self.assertFalse(hasattr(controller, "automation_future"))

    def test_button_foregrounds(self) -> None:
        """Apply normal and contrasting foregrounds independently."""
        normal_buttons = (
            self.panel.btn_create_move_feature,
            self.panel.btn_go_to_feature,
            self.panel.btn_switch_sem_imaging,
            self.panel.btn_switch_milling,
            self.panel.btn_acquire_overview,
            self.panel.btn_snap_patterns_to_feature,
        )
        contrasting_buttons = (
            self.panel.btn_feature_save_position,
            self.panel.btn_cryosecom_acquire,
            self.panel.btn_acquire_all,
            self.panel.btn_run_automated_milling,
            self.panel.btn_run_milling,
        )

        for button in normal_buttons:
            with self.subTest(button=button):
                self.assertEqual(
                    button.GetForegroundColour(),
                    wx.Colour(layout.theme.button_text),
                )
        for button in contrasting_buttons:
            with self.subTest(button=button):
                self.assertEqual(
                    button.GetForegroundColour(),
                    wx.Colour(layout.theme.button_text_contrast),
                )

    def test_uses_current_layout_theme_at_construction(self) -> None:
        """Resolve the selected layout theme when a component is created."""
        original_theme = layout.theme
        selected_theme = replace(original_theme, background="#121212")
        try:
            layout.theme = selected_theme
            panel = PnlTabFibsem(self.frame)
            self.assertIs(panel._theme, selected_theme)
            self.assertEqual(
                panel.GetBackgroundColour(),
                wx.Colour(selected_theme.background),
            )
            wx.Yield()
            panel.Destroy()
            wx.Yield()
        finally:
            layout.theme = original_theme


if __name__ == "__main__":
    unittest.main()
