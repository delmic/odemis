# -*- coding: utf-8 -*-
"""
Created on 09 Mar 2023

@author: Canberk Akin, Alexéy Ilyushkin

Copyright © 2023-2026 Canberk Akin, Alexéy Ilyushkin, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the milling.

"""

import logging
import os
from concurrent.futures import CancelledError
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import wx
from shapely import affinity
from shapely.geometry import Polygon, box

from odemis import model
from odemis.acq.feature import (
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
    CryoFeature,
    MillingAlignmentAreaTooSmallError,
    constrain_milling_alignment_area,
)
from odemis.acq.milling import millmng
from odemis.acq.milling.millmng import MillingWorkflowTask, run_automated_milling
from odemis.acq.milling.patterns import (
    CorrelationPatternParameters,
    MicroexpansionPatternParameters,
    MillingPatternParameters,
    NotchPatternParameters,
    RectanglePatternParameters,
    RulerPatternParameters,
    TrenchPatternParameters,
)
from odemis.acq.milling.tasks import MillingTaskSettings
from odemis.acq.stream import StaticStream
from odemis.gui.comp.milling import MillingTaskPanel
from odemis.gui.comp.overlay._constants import (
    MILLING_ALIGNMENT_AREA_COLOUR,
    MILLING_OVERLAY_ACTIVE_OPACITY,
    MILLING_OVERLAY_INACTIVE_OPACITY,
)
from odemis.gui.comp.overlay.base import Vec
from odemis.gui.comp.overlay.milling import (
    MillingAlignmentAreaOverlay,
    MillingAlignmentRectangleOverlay,
    MillingPatternOverlay,
    constrain_milling_alignment_area_from_shape,
    update_milling_alignment_area_shape,
)
from odemis.gui.comp.overlay.rectangle import MillingRectangleOverlay
from odemis.gui.comp.overlay.shapes import EditableShape
from odemis.gui.comp.popup import show_message
from odemis.gui.conf import get_acqui_conf
from odemis.gui.cont.features import save_project
from odemis.gui.layout import theme
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.gui.util.widgets import (
    ProgressiveFutureConnector,
    VigilantAttributeConnector,
)
from odemis.util import is_point_in_rect, units

MILLING_THEME_COLORS = (
    theme.categorical_red,
    theme.categorical_blue,
    theme.categorical_rose,
    theme.categorical_pink,
    theme.categorical_orange,
    theme.categorical_yellow,
    theme.categorical_cyan,
    theme.categorical_magenta,
    theme.categorical_green,
)
# Step sizes to move the milling patterns horizontally
MOVE_DELTA_X_SHORT = 1  # px
MOVE_DELTA_X_LONG = 5  # px
PATTERN_MOVE_WARNING_TIMEOUT = 3.0
PATTERN_DRAG_HINT_THRESHOLD = 5  # px


def _get_milling_colour(task: MillingTaskSettings, idx: int) -> str:
    """Get the configured task color, falling back to the application theme."""
    return task.color or MILLING_THEME_COLORS[idx % len(MILLING_THEME_COLORS)]


def pos_to_relative(pos: Tuple[float, float], ref_img: model.DataArray) -> Tuple[float, float]:
    """Convert the position from absolute position to relative position to the centre of image the given stream"""
    # get the center of the image, center of the pattern
    stream_pos = ref_img.metadata[model.MD_POS]

    # get the difference between the two
    center_x = pos[0] - stream_pos[0]
    center_y = pos[1] - stream_pos[1]

    return center_x, center_y

def pos_to_absolute(pos: Tuple[float, float], ref_img: model.DataArray) -> Tuple[float, float]:
    """Convert the position from relative to absolute coordinate position"""
    # get the center of the image, center of the pattern
    stream_pos = ref_img.metadata[model.MD_POS]

    # get the difference between the two
    center_x = pos[0] + stream_pos[0]
    center_y = pos[1] + stream_pos[1]

    return center_x, center_y


def _get_alignment_area_polygon(feature: CryoFeature) -> Optional[Polygon]:
    """Return a feature's alignment area in image-relative physical coordinates."""
    reference_image = feature.reference_image
    if reference_image is None:
        return None

    image_height, image_width = reference_image.shape[-2:]
    pixel_size_x, pixel_size_y = reference_image.metadata.get(model.MD_PIXEL_SIZE, (1e-6, 1e-6))[:2]
    left, top, width, height = feature.millingAlignmentArea.value
    x_min = (left - 0.5) * image_width * pixel_size_x
    x_max = (left + width - 0.5) * image_width * pixel_size_x
    y_max = (0.5 - top) * image_height * pixel_size_y
    y_min = (0.5 - top - height) * image_height * pixel_size_y
    return box(x_min, y_min, x_max, y_max)


def _get_milling_rectangle_polygon(rectangle: RectanglePatternParameters) -> Polygon:
    """Return a generated milling rectangle in image-relative physical coordinates."""
    center_x, center_y = rectangle.center.value
    half_width = rectangle.width.value / 2
    half_height = rectangle.height.value / 2
    geometry = box(
        center_x - half_width,
        center_y - half_height,
        center_x + half_width,
        center_y + half_height,
    )
    if rectangle.rotation.value:
        geometry = affinity.rotate(
            geometry, rectangle.rotation.value, origin=(center_x, center_y), use_radians=True)
    return geometry


def _get_pattern_stack_center(patterns: List[MillingPatternParameters]) -> Tuple[float, float]:
    """Return the center of the bounds of all generated pattern geometry."""
    bounds = [
        _get_milling_rectangle_polygon(rectangle).bounds
        for pattern in patterns
        for rectangle in pattern.generate()
        if isinstance(rectangle, RectanglePatternParameters)
    ]
    if bounds:
        min_x = min(bound[0] for bound in bounds)
        min_y = min(bound[1] for bound in bounds)
        max_x = max(bound[2] for bound in bounds)
        max_y = max(bound[3] for bound in bounds)
        return (min_x + max_x) / 2, (min_y + max_y) / 2

    centers = [pattern.center.value for pattern in patterns]
    if not centers:
        raise ValueError("Cannot calculate the center of an empty pattern stack.")
    return (
        sum(center[0] for center in centers) / len(centers),
        sum(center[1] for center in centers) / len(centers),
    )


# TODO: support other shapes
def rectangle_pattern_to_shape(canvas,
                        ref_img: model.DataArray,
                        pattern: RectanglePatternParameters,
                        colour: str = theme.categorical_yellow,
                        name: str = None,
                        show_spot_size_correction: bool = False,
                        show_dimensions: bool = True,
                        opacity: float = MILLING_OVERLAY_ACTIVE_OPACITY) -> EditableShape:
    """Convert a rectangle pattern to a shape"""
    rect = MillingRectangleOverlay(
        cnvs=canvas,
        colour=colour,
        show_selection_points=False,
        spot_size_correction=pattern.spot_size_correction.value,
        show_spot_size_correction=show_spot_size_correction,
        show_dimensions=show_dimensions,
        opacity=opacity,
    )
    width = pattern.width.value
    height = pattern.height.value
    x, y = pos_to_absolute(pattern.center.value, ref_img) # image coordinates -> physical coordinates
    if name is not None:
        rect.name.value = name

    # RectangleEditingMixin (point layout)
    # 1  -  2
    # |     |
    # 4  -  3

    rect.p_point1 = Vec(x - width / 2, y + height / 2)
    rect.p_point2 = Vec(x + width / 2, y + height / 2)
    rect.p_point3 = Vec(x + width / 2, y - height / 2)
    rect.p_point4 = Vec(x - width / 2, y - height / 2)

    # required for initialisation?
    rect._phys_to_view()
    rect._points = rect.get_physical_sel()
    rect.points.value = rect._points

    if pattern.rotation.value:
        rect.set_rotation(pattern.rotation.value)

    return rect


class MillingTaskController:
    """
    Takes care of handling the "PATTERNS" collapsible panel, which shows the selected milling tasks, and their settings.
    """
    def __init__(self, tab_data, tab_panel, tab):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        tab: (Tab): the tab object which controls the panel
        """
        self._tab_data = tab_data
        self._panel = tab_panel
        self._tab = tab

        if hasattr(self._tab, "_feature_panel_controller"):
            from odemis.gui.cont.features import CryoFeatureController
            self.feature_controller: CryoFeatureController = self._tab._feature_panel_controller

        # self.stream = tab.fib_stream  # fib stream
        self.acq_cont = tab._acquired_stream_controller
        self.viewport = tab_panel.pnl_secom_grid.viewports[3]  # fib acquired viewport
        self.canvas = self.viewport.canvas  # fib canvas

        self.pm = self._tab_data.main.posture_manager
        self.conf = get_acqui_conf()

        # load the milling tasks
        self.milling_tasks: Dict[str, MillingTaskSettings] = {} # TODO: move to main_data
        self.allow_milling_pattern_move = True
        self._active_spot_size_pattern = None
        self._pattern_drag_start = None

        # Draw pattern previews below the editable alignment area.
        self.rectangles_overlay = MillingPatternOverlay(cnvs=self.canvas)
        self.canvas.add_world_overlay(self.rectangles_overlay)

        # One editable alignment area, shared by all milling tasks of the feature.
        self.alignment_area_overlay = MillingAlignmentAreaOverlay(
            cnvs=self.canvas,
            on_area_changed=self._update_alignment_area_from_shape,
            on_area_selected=self._deselect_milling_task_for_alignment_area,
            blocks_area_interaction=self._blocks_alignment_area_interaction,
        )
        self.canvas.add_world_overlay(self.alignment_area_overlay)
        self.alignment_area_overlay.active.value = True

        self.canvas.Bind(wx.EVT_LEFT_DOWN, self.on_mouse_down) # bind the mouse down event
        self.canvas.Bind(wx.EVT_MOTION, self.on_mouse_motion)
        self.canvas.Bind(wx.EVT_CHAR, self.on_char)

        self.selected_tasks = model.ListVA([])  # List of strings, names of the selected milling tasks
        self._panel.milling_task_chk_list.Bind(
            wx.EVT_LEFT_DOWN, handler=self._on_milling_task_mouse_down)
        self._panel.milling_task_chk_list.Bind(
            wx.EVT_KEY_DOWN, handler=self._on_milling_task_key_down)
        self._panel.milling_task_chk_list.Bind(wx.EVT_CHECKLISTBOX, handler=self._update_selected_tasks)
        self._panel.milling_task_chk_list.Bind(wx.EVT_LISTBOX, handler=self._on_milling_task_selected)
        self._panel.btn_snap_patterns_to_feature.Bind(
            wx.EVT_BUTTON, self._snap_patterns_to_feature)

        self._tab_data.main.currentFeature.subscribe(self._on_current_feature_changes, init=True)

        # By default, all widgets are hidden => show button + estimated time at initialization
        self._panel.txt_milling_est_time.Hide()
        self._panel.btn_run_milling.Hide()
        self._panel.Layout()

        self._panel.txt_automated_milling_est_time.Hide()
        self._panel.gauge_automated_milling.Hide()
        self._panel.btn_automated_milling_cancel.Hide()

        self._tab_data.main.is_acquiring.subscribe(self._on_acquisition, init=True)

        # check pattern validity
        self.valid_patterns = model.BooleanVA(False)
        # self._tab_data.main.stage.position.subscribe(
        #     self._all_valid_patterns, init=True
        # )
        self._tab_data.streams.subscribe(self._update_mill_btn, init=True)
        self.valid_patterns.subscribe(self._update_mill_btn, init=True)

        # bind milling events
        self._panel.btn_run_milling.Bind(wx.EVT_BUTTON, self._run_milling)
        self._panel.btn_milling_cancel.Bind(wx.EVT_BUTTON, self._cancel_milling_series)

        # hide the milling button because we are using it for a workflow
        # self._panel.btn_run_milling.Hide()

    def _on_current_feature_changes(self, feature: Optional[CryoFeature]):
        """
        Called when the current feature is changed
        """
        # Update the checkbox list of milling tasks based on the ones of the new feature
        milling_tasks = feature.milling_tasks if feature else {}
        self.set_milling_tasks(milling_tasks)
        self._update_pattern_panels()
        self._update_pattern_movement_controls()
        self.draw_alignment_area()

    def _update_pattern_movement_controls(self) -> None:
        """Enable pattern movement controls when a feature can be edited."""
        feature = self._tab_data.main.currentFeature.value
        can_move = feature is not None and self.allow_milling_pattern_move
        self._panel.chk_move_all_patterns.Enable(can_move)
        self._panel.btn_snap_patterns_to_feature.Enable(can_move and feature.milling_feature_offset.value is not None)

    def _get_reference_stream(self, feature: CryoFeature) -> Optional[StaticStream]:
        """Return the displayed stream containing the feature reference image."""
        stream = self.acq_cont.stream
        if stream is not None and stream.raw and stream.raw[0] is feature.reference_image:
            return stream

        if self.viewport.view is None:
            return None
        displayed_streams = [
            candidate
            for candidate in self.viewport.view.getStreams()
            if candidate.raw
        ]
        for candidate in displayed_streams:
            if candidate.raw[0] is feature.reference_image:
                return candidate
        if len(displayed_streams) == 1:
            return displayed_streams[0]
        return None

    @call_in_wx_main
    def draw_alignment_area(self, _: Any = None) -> None:
        """Draw the current feature's editable area on the saved FIB image."""
        self.alignment_area_overlay.clear()

        feature = self._tab_data.main.currentFeature.value
        stream = self._get_reference_stream(feature) if feature is not None else None
        if feature is None or feature.reference_image is None or stream is None or not stream.raw:
            self.canvas.request_drawing_update()
            return

        try:
            area = constrain_milling_alignment_area(
                feature.millingAlignmentArea.value, feature.reference_image.shape)
        except MillingAlignmentAreaTooSmallError:
            self.canvas.request_drawing_update()
            return
        feature.millingAlignmentArea.value = area

        shape = MillingAlignmentRectangleOverlay(
            self.canvas,
            colour=MILLING_ALIGNMENT_AREA_COLOUR,
            show_dimensions=True,
            can_rotate=False,
        )
        shape.name.value = "Alignment area"
        shape.dashed = True
        update_milling_alignment_area_shape(shape, area, stream)
        shape.is_created.value = True
        shape.selected.value = False
        self.alignment_area_overlay.add_shape(shape)

    def _update_alignment_area_from_shape(
            self, shape: MillingAlignmentRectangleOverlay, commit: bool) -> None:
        """Constrain an edited area, update its feature, and optionally save it.

        :param shape: Alignment rectangle containing the edited geometry.
        :param commit: Whether to save the project after the completed edit.
            Live drag updates pass ``False`` and the final mouse release passes
            ``True``.
        """
        feature = self._tab_data.main.currentFeature.value
        stream = self._get_reference_stream(feature) if feature is not None else None
        if feature is None or feature.reference_image is None:
            return
        if stream is None or not stream.raw or shape.get_physical_sel() is None:
            return

        area = constrain_milling_alignment_area_from_shape(
            shape=shape,
            stream=stream,
            previous_area=feature.millingAlignmentArea.value,
            image_shape=feature.reference_image.shape,
        )
        if area is None:
            return
        feature.millingAlignmentArea.value = area

        update_milling_alignment_area_shape(shape, area, stream)
        self.canvas.request_drawing_update()
        self._update_milling_validation_message()
        self._update_mill_btn()

        if commit:
            save_project(self._tab_data.main)

    @call_in_wx_main
    def _update_pattern_panels(self) -> None:
        """
        Update the pattern settings control, when a new feature is selected.
        """
        if hasattr(self._panel.pnl_patterns, "_panel_sizer"):
            # self._panel.pnl_patterns._panel_sizer.Clear()
            # self._panel.pnl_patterns.Destroy()
            self._panel.pnl_patterns.DestroyChildren()
            self.controls = {}
        self._active_spot_size_pattern = None

        # create the panels
        self._panel.pnl_patterns._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self._panel.pnl_patterns.SetSizer(self._panel.pnl_patterns._panel_sizer)

        # create the setting panels, and connectors
        self.controls: Dict[str, MillingTaskPanel] = {}
        milling_parameters = ["current", "align", "mode"]

        # Note: always create all the panels, but hide for which the task is not selected.
        # This way, when a task is selected, we can just show the panel without having to create it.
        for task_name, task in self.milling_tasks.items():
            parameters = task.patterns[0]
            milling = task.milling

            # add the panel to the sizer
            panel = MillingTaskPanel(self._panel.pnl_patterns, task=task)
            self._panel.pnl_patterns._panel_sizer.Add(
                panel, border=10,
                flag=wx.EXPAND,
                proportion=0
            )

            self.controls[task_name] = {}
            self.controls[task_name]["panel"] = panel

            # pattern parameters
            for param in panel.pattern_parameters:
                value = getattr(parameters, param)
                event = wx.EVT_CHECKBOX if isinstance(value, model.BooleanVA) else wx.EVT_COMMAND_ENTER
                _va_connector = VigilantAttributeConnector(
                    value,
                    panel.ctrl_dict[param],
                    events=event,
                )
                self.controls[task_name][f"{param}_connector"] = _va_connector

                # VA connector, bind events
                value.subscribe(self._on_patterns)
                panel.ctrl_dict[param].Bind(
                    wx.EVT_SET_FOCUS,
                    lambda evt, pattern=parameters: self._on_pattern_control_interaction(evt, pattern),
                )

            # milling parameters
            for param in milling_parameters:
                val = getattr(milling, param)
                evt = wx.EVT_COMMAND_ENTER
                if isinstance(val, model.BooleanVA):
                    evt = wx.EVT_CHECKBOX
                if isinstance(val, model.StringEnumerated):
                    evt = wx.EVT_COMBOBOX
                _va_connector = VigilantAttributeConnector(
                    val,
                    panel.ctrl_dict[param],
                    events=evt,
                )
                self.controls[task_name][f"{param}_connector"] = _va_connector

                # VA connector, bind events
                getattr(milling, param).subscribe(self._on_patterns)
                activation_events = [wx.EVT_SET_FOCUS]
                if isinstance(val, model.BooleanVA):
                    activation_events.append(wx.EVT_CHECKBOX)
                if isinstance(val, model.StringEnumerated):
                    activation_events.append(wx.EVT_COMBOBOX)
                for activation_event in activation_events:
                    panel.ctrl_dict[param].Bind(
                        activation_event,
                        lambda event, pattern=parameters: self._on_pattern_control_interaction(
                            event, pattern
                        ),
                    )

            # Some wx controls, especially OwnerDrawnComboBox, send focus and
            # mouse events from an internal child window instead of the control.
            for control in panel.ctrl_dict.values():
                self._bind_pattern_activation(control, parameters)
            panel.Bind(
                wx.EVT_CHILD_FOCUS,
                lambda evt, pattern=parameters: self._on_pattern_control_interaction(evt, pattern),
            )

            if not task.selected:
                panel.Hide()

        self._panel.pnl_patterns.Layout()
        self._panel.Layout()

        # force the scrolled parent to recompute its layout, otherwise pnl_patterns
        # keeps the previous virtual size until the user triggers a resize
        self._panel.scr_win_right.FitInside()
        self._panel.scr_win_right.SendSizeEvent()

    def _get_invalid_spot_size_correction(self) -> Optional[Tuple[str, str]]:
        """Return the first checked feature and pattern with an invalid correction."""
        features = self._tab_data.main.features.value
        feature_list = self._panel.workflow_features_chk_list
        for index, feature in enumerate(features):
            if index >= feature_list.GetCount() or not feature_list.IsChecked(index):
                continue
            for task_name, task in feature.milling_tasks.items():
                if not task.selected:
                    continue
                for pattern in task.patterns:
                    # Validate the actual milling rectangles, including the ruler's thin graduations.
                    for rectangle in pattern.generate():
                        if not isinstance(rectangle, RectanglePatternParameters):
                            continue
                        correction = rectangle.spot_size_correction.value
                        if correction >= min(rectangle.width.value, rectangle.height.value):
                            return feature.name.value, task_name
        return None

    def _get_alignment_area_overlap(self) -> Optional[Tuple[str, str]]:
        """Return the first checked feature and task intersecting its alignment area."""
        features = self._tab_data.main.features.value
        feature_list = self._panel.workflow_features_chk_list
        for index, feature in enumerate(features):
            if index >= feature_list.GetCount() or not feature_list.IsChecked(index):
                continue
            alignment_area = _get_alignment_area_polygon(feature)
            if alignment_area is None:
                continue
            for task_name, task in feature.milling_tasks.items():
                if not task.selected:
                    continue
                for pattern in task.patterns:
                    for rectangle in pattern.generate():
                        if not isinstance(rectangle, RectanglePatternParameters):
                            continue
                        intersection = alignment_area.intersection(
                            _get_milling_rectangle_polygon(rectangle))
                        if intersection.area > 0:
                            return feature.name.value, task_name
        return None

    def _get_invalid_alignment_reference(self) -> Optional[str]:
        """Return the first checked feature whose reference image is too small."""
        features = self._tab_data.main.features.value
        feature_list = self._panel.workflow_features_chk_list
        for index, feature in enumerate(features):
            if index >= feature_list.GetCount() or not feature_list.IsChecked(index):
                continue
            if feature.reference_image is None:
                continue
            try:
                constrain_milling_alignment_area(
                    feature.millingAlignmentArea.value, feature.reference_image.shape)
            except MillingAlignmentAreaTooSmallError:
                return feature.name.value
        return None

    def _update_milling_validation_message(self) -> None:
        """Update the inline milling validation message."""
        invalid_pattern = self._get_invalid_spot_size_correction()
        if invalid_pattern:
            message = f"Invalid spot size correction: {invalid_pattern[0]}, {invalid_pattern[1]}."
        else:
            invalid_reference = self._get_invalid_alignment_reference()
            if invalid_reference:
                message = f"Acquire a higher-resolution reference image for {invalid_reference}."
            else:
                overlap = self._get_alignment_area_overlap()
                message = f"Alignment area overlaps a pattern for {overlap[0]}." if overlap else ""
        self._panel.txt_automated_milling_status.SetLabel(message)

    @call_in_wx_main
    def _on_shapes_update(self, shapes):
        """Called when the shapes are updated"""
        logging.debug("Shapes updated: %s", shapes)

        # check if any of the points of the shapes are outside the bounding box of the image
        s_bbox = self.acq_cont.stream.getBoundingBox()
        for shape in shapes:
            valid = all([is_point_in_rect(pt, s_bbox) for pt in shape.points.value])

            if not valid:
                logging.warning(f"Shape {shape} is not valid: {valid}")
                self.valid_patterns.value = False
                return # no point checking the rest, it's already invalid

        # all shapes are valid
        self.valid_patterns.value = True

    def _bind_pattern_activation(self, control: wx.Window, pattern) -> None:
        """Activate a pattern when its control or an internal child is clicked."""
        if isinstance(control, wx.TextCtrl):
            return
        control.Bind(
            wx.EVT_LEFT_DOWN,
            lambda evt, active_pattern=pattern: self._on_pattern_control_interaction(evt, active_pattern))
        for child in control.GetChildren():
            self._bind_pattern_activation(child, pattern)

    def _on_pattern_control_interaction(self, evt, pattern):
        if self._active_spot_size_pattern is not pattern:
            self._active_spot_size_pattern = pattern
            self.draw_milling_tasks()
        evt.Skip()

    def _on_milling_task_mouse_down(self, evt: wx.MouseEvent) -> None:
        """Deselect a selected row on Ctrl-click and preserve other rows."""
        task_list = self._panel.milling_task_chk_list
        clicked_index = task_list.HitTest(evt.GetPosition())
        selections = list(task_list.GetSelections())
        if evt.ControlDown() and clicked_index in selections:
            clicked_task = self.milling_tasks.get(task_list.GetString(clicked_index))
            active_pattern = self._active_spot_size_pattern
            clicked_task_was_active = clicked_task is not None and active_pattern in clicked_task.patterns
            task_list.Deselect(clicked_index)
            if clicked_task_was_active:
                remaining = list(task_list.GetSelections())
                if remaining:
                    self._activate_milling_task(task_list.GetString(remaining[-1]))
                    return
                self._active_spot_size_pattern = None
            self.draw_milling_tasks()
            return
        evt.Skip()

    def _on_milling_task_key_down(self, evt: wx.KeyEvent) -> None:
        """Toggle the checked state of all selected rows when Space is pressed."""
        if evt.GetKeyCode() != wx.WXK_SPACE:
            evt.Skip()
            return

        task_list = self._panel.milling_task_chk_list
        selections = list(task_list.GetSelections())
        if not selections:
            evt.Skip()
            return

        should_check = not all(task_list.IsChecked(index) for index in selections)
        for index in selections:
            task_list.Check(index, should_check)
        self._update_selected_tasks()

    def _on_milling_task_selected(self, evt: wx.CommandEvent) -> None:
        """Show the correction overlay for the highlighted pattern list row."""
        self._activate_milling_task(evt.GetString())
        evt.Skip()

    def _activate_milling_task(self, task_name: str) -> None:
        """Make one selected task active for labels and pattern controls."""
        task = self.milling_tasks.get(task_name)
        self._active_spot_size_pattern = task.patterns[0] if task and task.patterns else None
        self.draw_milling_tasks()

    def _deselect_milling_task(self) -> None:
        """Clear the highlighted milling task without changing its checked state."""
        task_list = self._panel.milling_task_chk_list
        if not task_list.GetSelections() and self._active_spot_size_pattern is None:
            return
        self._clear_milling_task_selection()
        self.draw_milling_tasks()

    def _clear_milling_task_selection(self) -> None:
        """Clear selected milling-task rows without redrawing their overlays."""
        self._panel.milling_task_chk_list.SetSelection(wx.NOT_FOUND)
        self._active_spot_size_pattern = None

    def _select_first_checked_milling_task(self) -> None:
        """Select the first checked milling task when no row is selected."""
        task_list = self._panel.milling_task_chk_list
        if task_list.GetSelections():
            return

        for index in range(task_list.GetCount()):
            if not task_list.IsChecked(index):
                continue
            task_list.SetSelection(index)
            self._activate_milling_task(task_list.GetString(index))
            return

    def _deselect_milling_task_for_alignment_area(self) -> None:
        """Clear the highlighted milling task without rebuilding the edited area."""
        task_list = self._panel.milling_task_chk_list
        if not task_list.GetSelections() and self._active_spot_size_pattern is None:
            return
        self._clear_milling_task_selection()
        self.draw_milling_tasks(redraw_alignment_area=False)

    def _select_milling_pattern(self, task_name: str, pattern: MillingPatternParameters) -> bool:
        """Highlight a task clicked in the viewport without changing checkboxes."""
        task_list = self._panel.milling_task_chk_list
        task_index = task_list.FindString(task_name)
        if task_index == wx.NOT_FOUND:
            return False

        task_list.SetSelection(wx.NOT_FOUND)
        task_list.SetSelection(task_index)
        self._active_spot_size_pattern = pattern
        self.alignment_area_overlay.deselect()
        self.draw_milling_tasks(redraw_alignment_area=False)
        return True

    def _is_feature_marker_at(self, v_pos: Tuple[float, float]) -> bool:
        """Return whether the canvas feature overlay owns a click position."""
        overlay = getattr(self.canvas, "cryofeature_overlay", None)
        if overlay is None:
            return False
        return overlay.show and overlay.active.value and overlay.get_feature_at(v_pos) is not None

    def _blocks_alignment_area_interaction(self, v_pos: Tuple[float, float]) -> bool:
        """Return whether a feature marker or milling pattern owns a position."""
        return self._is_feature_marker_at(v_pos) or self.rectangles_overlay.get_pattern_at(v_pos) is not None

    def on_mouse_down(self, evt):
        self._pattern_drag_start = None
        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        feature = self._tab_data.main.currentFeature.value
        has_reference = feature is not None and feature.reference_image is not None

        # check if shift is pressed, and if a stream is selected
        move_requested = evt.ShiftDown() and evt.ControlDown() and self.allow_milling_pattern_move
        if move_requested and has_reference:
            # get the position of the mouse, convert to physical position
            pos = evt.GetPosition()
            p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            logging.debug(f"shift + control pressed, mouse_pos: {pos}, phys_pos: {p_pos}")

            # TODO: validate if click is outside image bounds, don't move the pattern
            # TODO: validate whether the pattern is within the image bounds before moving it
            patterns = self._get_patterns_for_manual_move()
            if not patterns:
                self._show_pattern_selection_warning(
                    "Select a milling pattern before moving it.")
                return

            anchor_position = _get_pattern_stack_center(patterns)
            if self._panel.chk_move_all_patterns.GetValue():
                self._clear_milling_task_selection()
            self._move_patterns(patterns, pos_to_relative(p_pos, feature.reference_image), anchor_position)
            return

        is_plain_click = not (evt.ControlDown() or evt.ShiftDown() or evt.AltDown())
        if is_plain_click and has_reference:
            click_position = evt.GetPosition()
            if self._is_feature_marker_at(click_position):
                self._deselect_milling_task_for_alignment_area()
                evt.Skip()
                return

            if self.alignment_area_overlay.is_corner_handle_at(click_position):
                self._deselect_milling_task_for_alignment_area()
                evt.Skip()
                return

            clicked_pattern = self.rectangles_overlay.get_pattern_at(click_position)
            if clicked_pattern is not None and self._select_milling_pattern(*clicked_pattern):
                self._pattern_drag_start = Vec(click_position)
                evt.Skip(False)
                return

        if not evt.ControlDown():
            # The alignment overlay handles the same click after this canvas
            # handler. Keep its current shape alive so it can be selected on
            # the first click instead of being recreated as unselected.
            self._deselect_milling_task_for_alignment_area()

        # super event passthrough
        evt.Skip()

    def on_mouse_motion(self, evt: wx.MouseEvent) -> None:
        """Show guidance when a user tries to drag a milling pattern."""
        start = self._pattern_drag_start
        if start is None:
            evt.Skip()
            return

        if not evt.LeftIsDown():
            self._pattern_drag_start = None
            evt.Skip()
            return

        current = Vec(evt.GetPosition())
        distance_squared = (current.x - start.x) ** 2 + (current.y - start.y) ** 2
        if distance_squared >= PATTERN_DRAG_HINT_THRESHOLD ** 2:
            self._pattern_drag_start = None
            self._show_pattern_drag_hint()

        # The initial click was consumed for pattern selection. Keep the rest
        # of that gesture away from the image and alignment-area handlers too.
        evt.Skip(False)

    def _show_pattern_drag_hint(self) -> None:
        """Tell the user how milling patterns are moved in this viewport."""
        message = "To move milling patterns, use Shift+Ctrl+click."
        logging.info(message)
        show_message(
            self._tab.main_frame,
            title="Pattern movement",
            message=message,
            timeout=PATTERN_MOVE_WARNING_TIMEOUT,
            level=logging.WARNING,
        )

    def _show_pattern_selection_warning(self, message: str) -> None:
        """Log and temporarily display a pattern-selection warning.

        :param message: Warning text shown to the user and written to the log.
        """
        logging.info(message)
        show_message(
            self._tab.main_frame,
            title="Pattern movement",
            message=message,
            timeout=PATTERN_MOVE_WARNING_TIMEOUT,
            level=logging.WARNING,
        )

    def on_char(self, evt: wx.Event) -> None:
        """
        Handle keyboard button presses to move the milling pattern horizontally with the keyboard arrows,
        when control or control combination is used.
        :param evt: the event
        """
        # event data
        key = evt.GetKeyCode()
        shift_mod = evt.ShiftDown()
        ctrl_mod = evt.ControlDown()

        # pass through event, if not a valid arrow key is pressed
        valid_keys = [wx.WXK_LEFT, wx.WXK_RIGHT]
        if key not in valid_keys:
            evt.Skip()
            return

        active_canvas = evt.GetEventObject()
        logging.debug(f"keyboard button pressed event, canvas: {active_canvas}")

        feature = self._tab_data.main.currentFeature.value

        # move if a reference image exists, because coordinate conversion from view pixels to physical
        # meters relies on the MD_POS metadata stored in that image
        if (ctrl_mod
                and self.allow_milling_pattern_move
                and feature and feature.reference_image is not None
        ):
            # move in small step when both ctrl and shift are pressed, bigger step with only ctrl
            if shift_mod:
                step_px = MOVE_DELTA_X_SHORT
            else:
                step_px = MOVE_DELTA_X_LONG
            ref_img = feature.reference_image
            if key == wx.WXK_LEFT:
                view_dx = -step_px
            else:
                view_dx = step_px

            patterns = self._get_patterns_for_manual_move()
            if not patterns:
                self._show_pattern_selection_warning(
                    "Select a milling pattern before moving it.")
                return
            if self._panel.chk_move_all_patterns.GetValue():
                self._clear_milling_task_selection()
            for pattern in patterns:
                selected_center_rel = pattern.center.value
                offset = active_canvas.get_half_buffer_size()
                center_phys = pos_to_absolute(selected_center_rel, ref_img)
                center_view = active_canvas.phys_to_view(center_phys, offset)
                new_center_view = (center_view[0] + view_dx, center_view[1])
                new_center_phys = active_canvas.view_to_phys(new_center_view, offset)
                logging.debug(f"Move milling pattern {pattern.name.value} horizontally from physical position"
                              f" {center_phys}, to new position {new_center_phys}")

                relative_pos = pos_to_relative(new_center_phys, feature.reference_image)
                pattern.center.value = relative_pos

            save_project(self._tab_data.main)
            self.draw_milling_tasks()
            self._update_milling_validation_message()
            self._update_mill_btn()
            return

        # super event passthrough
        evt.Skip()

    @call_in_wx_main
    def set_milling_tasks(self, milling_tasks: Dict[str, MillingTaskSettings]):
        """
        Sets the milling tasks displayed to the provided ones
        """
        # Check if tasks actually changed to avoid the panel to flicker
        if self.milling_tasks is milling_tasks:
            logging.debug("Milling tasks unchanged, skipping update")
            return

        self.milling_tasks = milling_tasks

        # Update the selected tasks check box list

        all_tasks = []  # names of all the existing tasks
        selected_tasks = []  # names of the milling task selected (for milling)

        for name, milling_settings in milling_tasks.items():
            all_tasks.append(name)
            if milling_settings.selected:
                selected_tasks.append(name)

        # unsubscribe from updates to the selected tasks
        self.selected_tasks.unsubscribe(self._on_selected_tasks)
        self.selected_tasks.value = selected_tasks

        # update the checkbox list
        self._panel.milling_task_chk_list.SetItems(all_tasks)
        self._panel.milling_task_chk_list.SetCheckedStrings(selected_tasks)
        self.selected_tasks.subscribe(self._on_selected_tasks, init=True)

    # NOTE: we should add the bottom right viewport as the feature viewport, to show the saved reference image and the milling patterns
    # it's too confusing to hav the 'live' view and the 'saved' view in the same viewport
    # -> workflow tab is probably easier to use for this purpose

    def _on_selected_tasks(self, tasks: List[str]):
        if self._tab_data.main.currentFeature.value is None:
            return

        for task_name, task in self.milling_tasks.items():
            task.selected = task_name in tasks

        self._update_milling_validation_message()
        save_project(self._tab_data.main)
        self.draw_milling_tasks()
        self._update_mill_btn()

    def _get_patterns_for_manual_move(self) -> List[MillingPatternParameters]:
        """Return patterns affected by a manual movement command."""
        if self._panel.chk_move_all_patterns.GetValue():
            return [pattern for task in self.milling_tasks.values()
                    if task.selected for pattern in task.patterns]

        if not self._panel.milling_task_chk_list.GetSelections():
            self._select_first_checked_milling_task()

        highlighted_tasks = self._get_highlighted_tasks()
        checked_tasks = [task for task in highlighted_tasks if task.selected]
        return [pattern for task in checked_tasks for pattern in task.patterns]

    def _get_highlighted_tasks(self) -> List[MillingTaskSettings]:
        """Return milling tasks whose list rows are selected."""
        task_list = self._panel.milling_task_chk_list
        return [
            self.milling_tasks[task_list.GetString(index)]
            for index in task_list.GetSelections()
            if task_list.GetString(index) in self.milling_tasks
        ]

    def _get_checked_highlighted_tasks(self) -> List[MillingTaskSettings]:
        """Return selected milling-task rows that are also checked."""
        return [task for task in self._get_highlighted_tasks() if task.selected]

    def _get_highlighted_task(self) -> Optional[MillingTaskSettings]:
        """Return the milling task active for labels and movement.

        :return: Active milling task, or None when no task is active.
        """
        highlighted_tasks = self._get_highlighted_tasks()
        for task in highlighted_tasks:
            if self._active_spot_size_pattern in task.patterns:
                return task
        return highlighted_tasks[-1] if highlighted_tasks else None

    def _get_highlighted_pattern(self) -> Optional[MillingPatternParameters]:
        """Return the first pattern from the highlighted milling-task row.

        :return: Highlighted pattern, or None when no row is highlighted.
        """
        task = self._get_highlighted_task()
        return task.patterns[0] if task and task.patterns else None

    def _move_patterns(self, patterns: List[MillingPatternParameters],
                       pos: Tuple[float, float],
                       anchor_position: Optional[Tuple[float, float]] = None,
                       use_feature_offsets: bool = False) -> None:
        """Move patterns to a target position and persist the updated project.

        When an anchor position is provided, translate every pattern by the
        offset required to center that point at the target position.

        :param patterns: Patterns to move.
        :param pos: Target position relative to the reference-image center.
        :param anchor_position: Point to center at the target while preserving
            the relative positions of all patterns.
        :param use_feature_offsets: Arrange patterns at their feature-relative
            default offsets instead of placing every center at the target.
        """
        offset = None
        if anchor_position is not None:
            anchor_x, anchor_y = anchor_position
            offset = (pos[0] - anchor_x, pos[1] - anchor_y)

        for pattern in patterns:
            if use_feature_offsets:
                pattern.center.value = pattern.get_center_at_feature(pos)
            elif offset is None:
                pattern.center.value = pos
            else:
                center_x, center_y = pattern.center.value
                pattern.center.value = (center_x + offset[0], center_y + offset[1])

        save_project(self._tab_data.main)
        self.draw_milling_tasks()
        self._update_milling_validation_message()
        self._update_mill_btn()

    def move_milling_tasks(self, pos: Tuple[float, float]) -> None:
        """Arrange every displayed milling pattern around the current feature.

        :param pos: Position relative to the center of the ion-beam field of view.
        """
        feature = self._tab_data.main.currentFeature.value
        if feature is None:
            logging.warning("Cannot move milling tasks without a selected feature.")
            return

        patterns = [pattern for task in feature.milling_tasks.values()
                    if task.selected for pattern in task.patterns]
        self._move_patterns(patterns, pos, use_feature_offsets=True)

    def _snap_patterns_to_feature(self, _: wx.Event) -> None:
        """Recenter displayed milling patterns on the feature marker."""
        feature = self._tab_data.main.currentFeature.value
        if not self.allow_milling_pattern_move:
            logging.warning(
                "Cannot recenter milling patterns while pattern movement is disabled.")
            return
        if feature is None:
            logging.warning(
                "Cannot recenter milling patterns without a selected feature.")
            return
        if feature.milling_feature_offset.value is None:
            logging.warning("Cannot recenter milling patterns without a feature marker.")
            return
        self.move_milling_tasks(feature.milling_feature_offset.value)

    def set_milling_feature_position(self,
                                     pos: Tuple[float, float],
                                     move_patterns: bool = True) -> None:
        """Commit the feature marker and optionally recenter patterns around it."""
        feature = self._tab_data.main.currentFeature.value
        if feature is None:
            logging.warning("Cannot position milling feature without a selected feature.")
            return

        feature.set_milling_feature_offset(pos, move_patterns=move_patterns)
        self._update_pattern_movement_controls()

        save_project(self._tab_data.main)
        self.draw_milling_tasks()
        self._update_milling_validation_message()
        self._update_mill_btn()

    @call_in_wx_main
    def draw_milling_tasks(self, _: Any = None, redraw_alignment_area: bool = True) -> None:
        """Redraw all milling tasks on the canvas.
        """
        if redraw_alignment_area:
            self.draw_alignment_area()

        # Clears the rectangles_overlay first
        self.rectangles_overlay.clear()
        self.rectangles_overlay.clear_labels()

        # then, redraws all the patterns.
        feature = self._tab_data.main.currentFeature.value
        selected_tasks = self.selected_tasks.value
        # The patterns are defined relative to the center of the reference image
        if not self.milling_tasks or not selected_tasks or not feature or feature.reference_image is None:
            self.canvas.request_drawing_update()
            return

        highlighted_task = self._get_highlighted_task()
        highlighted_tasks = set(self._get_checked_highlighted_tasks())
        indexed_tasks = list(enumerate(self.milling_tasks.items()))
        # Draw selected tasks last, with the active task uppermost. This
        # temporary rendering order does not mutate milling order.
        rendering_order = sorted(
            indexed_tasks,
            key=lambda item: (
                item[1][1] in highlighted_tasks,
                item[1][1] is highlighted_task,
            ),
        )

        # redraw all patterns
        for i, (task_name, task) in rendering_order:
            if not task.selected:
                continue
            show_labels = task in highlighted_tasks
            opacity = MILLING_OVERLAY_ACTIVE_OPACITY if show_labels else MILLING_OVERLAY_INACTIVE_OPACITY
            for pattern in task.patterns:
                uses_shared_label = isinstance(
                    pattern,
                    (
                        CorrelationPatternParameters,
                        RulerPatternParameters,
                        NotchPatternParameters,
                    ),
                )
                uses_top_label = isinstance(
                    pattern, (MicroexpansionPatternParameters, TrenchPatternParameters))
                generated_patterns = pattern.generate()
                for j, pshape in enumerate(generated_patterns):
                    if show_labels and j == 0 and not uses_shared_label and not uses_top_label:
                        name = task_name
                    else:
                        name = None
                    shape = rectangle_pattern_to_shape(
                                            canvas=self.canvas,
                                            ref_img=feature.reference_image,
                                            pattern=pshape,
                                            colour=_get_milling_colour(task, i),
                                            name=name,
                                            show_spot_size_correction=(
                                                pattern is self._active_spot_size_pattern
                                            ),
                                            show_dimensions=(
                                                show_labels and not uses_shared_label
                                            ),
                                            opacity=opacity)
                    self.rectangles_overlay.add_pattern_shape(
                        shape, task_name, pattern)
                if uses_top_label and show_labels:
                    center_x, _ = pattern.center.value
                    top_y = max(
                        pshape.center.value[1] + pshape.height.value / 2
                        for pshape in generated_patterns
                    )
                    label_position = pos_to_absolute(
                        (center_x, top_y), feature.reference_image)
                    self.rectangles_overlay.add_pattern_label(
                        task_name, label_position)
                elif uses_shared_label and show_labels:
                    x, y = pos_to_absolute(
                        pattern.center.value, feature.reference_image)
                    if isinstance(pattern, CorrelationPatternParameters):
                        size = units.readable_str(
                            (pattern.width.value, pattern.height.value), "m", sig=3
                        )
                        # Cairo cannot provide font fallback for the uncommon glyphs in the full task name.
                        label = f"Correlation · {size}"
                    elif isinstance(pattern, NotchPatternParameters):
                        size = units.readable_str(
                            (pattern.width.value, pattern.height.value), "m", sig=3
                        )
                        label = f"{task_name} · {size}"
                        gap = units.readable_str(pattern.gap.value, "m", sig=3)
                        gap_side = 1 if pattern.mirrored.value else -1
                        gap_align = wx.ALIGN_LEFT if pattern.mirrored.value else wx.ALIGN_RIGHT
                        self.rectangles_overlay.add_pattern_label(
                            gap,
                            (x + gap_side * pattern.width.value / 2,
                             y + pattern.offset.value),
                            align=gap_align | wx.ALIGN_CENTER_VERTICAL,
                            offset=(gap_side * 8, 0),
                        )
                    else:
                        height = units.readable_str(
                            pattern.height.value, "m", sig=3)
                        label = f"{task_name} · {height}"
                    self.rectangles_overlay.add_pattern_label(
                        label,
                        (x, y + pattern.height.value / 2),
                    )

        # validate the patterns
        self._on_shapes_update(self.rectangles_overlay._shapes.value)

    def _update_selected_tasks(self, _: Optional[wx.Event] = None) -> None:
        """Synchronize checked pattern rows with milling tasks and controls."""
        self.selected_tasks.value = list(self._panel.milling_task_chk_list.GetCheckedStrings())
        # Update the 'Pattern' panel
        for task_name, controls in self.controls.items():
            panel = controls["panel"]
            should_show = task_name in self.selected_tasks.value
            panel.Show(should_show)

        self._panel.pnl_patterns.Layout()
        self._panel.Layout()

    @call_in_wx_main
    def _run_milling(self, evt: wx.Event):
        """
        called when the button "MILL" is pressed
        """

        # Make sure all the streams are paused
        self._tab.streambar_controller.pauseStreams()

        # hide/show/disable some widgets
        self._panel.txt_milling_est_time.Hide()
        self._panel.txt_milling_series_left_time.Show()
        self._panel.gauge_milling_series.Show()
        self._panel.btn_milling_cancel.Show()
        self._tab_data.main.is_acquiring.value = True

        # disable moving milling patterns while milling
        self.allow_milling_pattern_move = False

        # run the milling tasks
        tasks = [task for task_name, task in self.milling_tasks.items() if task_name in self.selected_tasks.value]
        self._mill_future = millmng.run_milling_tasks(tasks=tasks,
                                                      fib_stream=self._tab.fib_stream)

        # link the milling gauge to the milling future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._mill_future,
            bar=self._panel.gauge_milling_series,
            label=self._panel.txt_milling_series_left_time,
            full=False,
        )

        self._mill_future.add_done_callback(self._on_milling_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_milling_done(self, future):
        """
        Called when the acquisition process is
        done, failed or canceled
        """
        self._gauge_future_conn = None
        self._tab_data.main.is_acquiring.value = False
        self.allow_milling_pattern_move = True

        self._panel.gauge_milling_series.Hide()
        self._panel.btn_milling_cancel.Hide()
        self._panel.txt_milling_series_left_time.Hide()
        self._panel.txt_milling_est_time.Show()

        # Update the milling status text
        if future is None:
            milling_status_txt = "Milling cancelled."
        else:
            try:
                future.result()
                milling_status_txt = "Milling completed."
            except CancelledError:
                milling_status_txt = "Milling cancelled."
            except Exception:
                milling_status_txt = "Milling failed."

        self._panel.txt_milling_est_time.SetLabel(milling_status_txt)

    @wxlimit_invocation(1)  # max 1/s
    def _update_milling_time(self):
        """Updates the estimated time required for milling"""

        # display the time on the GUI
        txt = "Estimated time: {}.".format(
            units.readable_time(20 * len(self.selected_tasks.value), full=False)
        ) #TODO: accurate time estimate
        self._panel.txt_milling_est_time.SetLabel(txt)
        self._panel.txt_automated_milling_est_time.SetLabel(txt)

    def _on_patterns(self, dat):
        """
        Updates milling time and availability of the mill button when there's an update on the patterns
        """

        logging.warning(f"Pattern updated: {dat}")
        self.draw_milling_tasks()
        self._update_milling_validation_message()
        self._update_mill_btn()

    def _cancel_milling_series(self, _):
        """
        called when the button "Cancel" is pressed
        """
        logging.debug("Cancelling milling.")
        self._mill_future.cancel()

    def _on_acquisition(self, is_acquiring: bool):
        """
        Called when is_acquiring changes
        Enable/Disable mill button
        """
        self.allow_milling_pattern_move = not is_acquiring
        self._update_pattern_movement_controls()
        self._update_mill_btn()

    @call_in_wx_main
    def _update_mill_btn(self, _: wx.Event = None):
        """
        Enable/disable mill button depending on the state of the GUI
        """
        is_acquiring = self._tab_data.main.is_acquiring.value
        has_tasks = bool(self.selected_tasks.value)
        valid_patterns = self.valid_patterns.value
        invalid_spot_size_correction = self._get_invalid_spot_size_correction()
        invalid_alignment_reference = self._get_invalid_alignment_reference()
        alignment_area_overlap = self._get_alignment_area_overlap()
        milling_enabled = (
            has_tasks and not is_acquiring and valid_patterns
            and not invalid_spot_size_correction and not invalid_alignment_reference
            and not alignment_area_overlap
        )
        self._panel.btn_run_milling.Enable(milling_enabled)
        self._panel.btn_run_automated_milling.Enable(milling_enabled)

        if not has_tasks:
            txt = "No Tasks Selected..."
            self._panel.txt_milling_est_time.SetLabel(txt)
            self._panel.txt_automated_milling_est_time.SetLabel(txt)

        if not valid_patterns:
            txt = "Invalid milling pattern..."
            self._panel.txt_milling_est_time.SetLabel(txt)
            self._panel.txt_automated_milling_est_time.SetLabel(txt)

        if has_tasks and valid_patterns:
            self._update_milling_time()


class AutomatedMillingController:
    def __init__(self, tab_data, tab_panel, tab):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        tab: (Tab): the tab object which controls the panel
        """
        self._tab_data = tab_data
        self._panel = tab_panel
        self._tab = tab

        from odemis.gui.conf import get_acqui_conf
        self.conf = get_acqui_conf()

        # automated milling tasks
        self.task_list = [MillingWorkflowTask.RoughMilling, MillingWorkflowTask.Polishing]
        pretty_task_names = ["Rough Milling", "Polishing"]
        self._panel.workflow_task_chk_list.SetItems([task for task in pretty_task_names])
        for i in range(self._panel.workflow_task_chk_list.GetCount()):
            self._panel.workflow_task_chk_list.Check(i)

        self._panel.btn_run_automated_milling.Bind(wx.EVT_BUTTON, self._run_automated_milling)
        self._panel.btn_automated_milling_cancel.Bind(wx.EVT_BUTTON, self._cancel_automated_milling)

        # connect features to chklistbox
        self._tab_data.main.features.subscribe(self._update_features, init=True)
        self._panel.workflow_features_chk_list.Bind(wx.EVT_CHECKLISTBOX, self._update_checked_features)
        self._panel.workflow_features_chk_list.Bind(wx.EVT_LISTBOX, self._update_selected_feature)

    @call_in_wx_main
    def _update_selected_feature(self, evt: wx.Event):

        # TODO: disable multi-selection?
        # get the index of selected item
        index = self._panel.workflow_features_chk_list.GetSelection()
        f = self._tab_data.main.features.value[index]
        logging.debug(f"Feature {f.name.value} selected.")
        self._tab_data.main.currentFeature.value = f

    def _update_checked_features(self, evt: wx.Event):
        index = evt.GetInt()
        disabled_features_indexes = [i for i, f in enumerate(self._tab_data.main.features.value) if f.status.value in [FEATURE_ACTIVE, FEATURE_DEACTIVE]]

        # Prevent the change
        if index in disabled_features_indexes:
            self._panel.workflow_features_chk_list.Check(index, False)
            f = self._tab_data.main.features.value[index]
            disabled_txt = f"{f.name.value} is not ready for milling. Please prepare the feature first."
            wx.MessageBox(disabled_txt, "Info", wx.OK | wx.ICON_INFORMATION)

        self._tab.milling_task_controller._update_milling_validation_message()
        self._tab.milling_task_controller._update_mill_btn()

    def _update_feature_status(self, feature: CryoFeature):

        self._update_features(self._tab_data.main.features.value)

    @call_in_wx_main
    def _update_features(self, features: List[CryoFeature]):
        """
        Sync the features with the cklistbox
        """

        # clear the list
        self._panel.workflow_features_chk_list.Clear()
        for i, f in enumerate(features):
            txt = f"{f.name.value} ({f.status.value})"
            self._panel.workflow_features_chk_list.Append(txt)

            check = False if f.status.value in [FEATURE_ACTIVE, FEATURE_DEACTIVE] else True
            self._panel.workflow_features_chk_list.Check(i, check)

            # subscribe to the feature status, so we can update the list
            f.status.subscribe(self._update_feature_status, init=False)

        self._tab.milling_task_controller._update_milling_validation_message()
        self._tab.milling_task_controller._update_mill_btn()

    def _run_automated_milling(self, evt: wx.Event):

        # filter the features list, so only the checked ones are used
        features = self._tab_data.main.features.value
        features = [f for i, f in enumerate(features) if self._panel.workflow_features_chk_list.IsChecked(i)]
        stage = self._tab_data.main.stage_bare
        sem_stream = self._tab.sem_stream
        fib_stream = self._tab.fib_stream

        logging.warning(f"Running automated milling for {len(features)} features: {features}")

        # tmp: add the path to the features, as it's not saved in the feature
        for feature in features:
            feature.path = os.path.join(self.conf.pj_last_path, feature.name.value)

        task_list = [t for i, t in enumerate(self.task_list) if self._panel.workflow_task_chk_list.IsChecked(i)]
        logging.info(f"Running automated milling for tasks: {task_list}")

        # TODO: add estimated time to the dialog, gui
        # dialog to confirm the milling
        task_names = ", ".join([t.name for t in task_list])
        ftxt = f"{len(features)} features?" if len(features) > 1 else f"{features[0].name.value}?"
        dlg = wx.MessageDialog(
            self._panel,
            f"Start workflows ({task_names}) for {ftxt}",
            "Start Automated Milling",
            wx.YES_NO | wx.ICON_QUESTION,
        )

        if dlg.ShowModal() == wx.ID_NO:
            self._on_automation_done(None)
            return

        # hide/show/disable some widgets
        self._panel.txt_automated_milling_est_time.Hide()
        self._panel.txt_automated_milling_left_time.Show()
        self._panel.gauge_automated_milling.Show()
        self._panel.btn_automated_milling_cancel.Show()
        self._tab_data.main.is_acquiring.value = True
        self._tab_data.main.is_milling.value = True

        self.automation_future: model.ProgressiveFuture = run_automated_milling(
                                    features=features,
                                    stage=stage,
                                    sem_stream=sem_stream,
                                    fib_stream=fib_stream,
                                    task_list=task_list,
                                    )

        # link the milling gauge to the milling future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self.automation_future,
            bar=self._panel.gauge_automated_milling,
            label=self._panel.txt_automated_milling_left_time,
            full=False,
        )

        @call_in_wx_main
        def _update_progress(future, start, end):
            if hasattr(future, "msg"):
                startdt = datetime.fromtimestamp(start).strftime('%Y-%m-%d_%H-%M-%S')
                enddt = datetime.fromtimestamp(end).strftime('%Y-%m-%d_%H-%M-%S')
                now = datetime.now().timestamp()
                logging.info(f"automated milling update: {future.msg}, {startdt}, {enddt}, {end-now} seconds remaining")
                self._panel.txt_automated_milling_status.SetLabel(future.msg)

            if hasattr(future, "current_feature"):
                logging.debug(f"automated milling update: current feature is {future.current_feature.name.value}")
                self._tab_data.main.currentFeature.value = future.current_feature

        self.automation_future.add_update_callback(_update_progress)
        self.automation_future.add_done_callback(self._on_automation_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_automation_done(self, future):
        """
        Called when the acquisition process is
        done, failed or canceled
        """

        self._gauge_future_conn = None
        self._tab_data.main.is_acquiring.value = False
        self._tab_data.main.is_milling.value = False

        self._panel.gauge_automated_milling.Hide()
        self._panel.btn_automated_milling_cancel.Hide()
        self._panel.txt_automated_milling_left_time.Hide()

        if not future:
            return
        # Update the milling status text
        try:
            future.result()
            milling_status_txt = "Milling completed."
        except CancelledError:
            milling_status_txt = "Milling cancelled."
        except Exception:
            logging.exception("Automated milling failed.")
            milling_status_txt = "Milling failed."
        logging.info(f"Automated milling done: {milling_status_txt}")

        self._panel.txt_automated_milling_est_time.SetLabel(milling_status_txt)
        self._panel.txt_automated_milling_status.SetLabel(milling_status_txt)

    def _cancel_automated_milling(self, _):
        """
        called when the button "Cancel" is pressed
        """
        logging.debug("Cancelling automated milling.")
        self.automation_future.cancel()
