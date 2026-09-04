# -*- coding: utf-8 -*-
"""
Created on 09 Mar 2023

@author: Canberk Akin

Copyright © 2023 Canberk Akin, Delmic

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
import math
import os
from concurrent.futures import CancelledError
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import wx

from odemis import model
from odemis.acq.feature import (
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
    CryoFeature,
)
from odemis.acq.stream import FIBStream
from odemis.acq.milling import millmng
from odemis.acq.milling.millmng import MillingWorkflowTask, run_automated_milling
from odemis.acq.milling.patterns import CrossPatternParameters, RectanglePatternParameters
from odemis.acq.milling.tasks import MillingSettings, MillingTaskSettings
from odemis.gui.comp.milling import MillingTaskPanel
from odemis.gui.comp.overlay.base import Vec
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.gui.comp.overlay.shapes import EditableShape, ShapesOverlay
from odemis.gui.conf import get_acqui_conf
from odemis.gui.cont.features import save_project
from odemis.gui.cont.tabs import Tab
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.gui.util.widgets import (
    ProgressiveFutureConnector,
    VigilantAttributeConnector,
)
from odemis.util import is_point_in_rect, units

# yellow, cyan, magenta, lime, orange, hotpink
MILLING_COLOURS_CYCLE = ["#FFFF00", "#00FFFF", "#FF00FF", "#00FF00", "#FFA500", "#FF69B4"]
MILLING_COLOURS_CANONICAL = {
    "Rough Milling 01": "#FFFF00",
    "Rough Milling 02": "#00FFFF",
    "Polishing 01": "#FF00FF",
    "Polishing 02": "#00FF00",
    "Microexpansion": "#FFA500",
    "Fiducial": "#FF69B4",
    "Fibucial": "#FF69B4",
}
# Step sizes to move the milling patterns horizontally
MOVE_DELTA_X_SHORT = 1  # px
MOVE_DELTA_X_LONG = 5  # px

def _get_milling_colour(task_name: str, idx: int) -> str:
    """Get the colour based on the task name or index"""
    if task_name in MILLING_COLOURS_CANONICAL:
        return MILLING_COLOURS_CANONICAL[task_name]
    return MILLING_COLOURS_CYCLE[idx % len(MILLING_COLOURS_CYCLE)]

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

# TODO: support other shapes
def rectangle_pattern_to_shape(canvas,
                        ref_img: model.DataArray,
                        pattern: RectanglePatternParameters,
                        colour: str = "#FFFF00",
                        name: Optional[str] = None,
                        show_selection_points: bool = False) -> EditableShape:
    """Convert a rectangle pattern to a shape"""
    rect = RectangleOverlay(cnvs=canvas, colour=colour, show_selection_points=show_selection_points)
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

    if hasattr(pattern, "rotation"):
        rect.set_rotation(pattern.rotation.value)

    # Generated shapes are complete objects and should immediately support editing.
    rect.is_created.value = True

    return rect

def _point_to_xy(point: Any) -> Tuple[float, float]:
    """Convert a point-like object to an x/y tuple.

    Accepts both Vec-like objects exposing x/y and indexable 2-item sequences.
    """
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(point.x), float(point.y)

    try:
        return float(point[0]), float(point[1])
    except Exception as exc:
        raise ValueError(f"Unsupported point format: {point!r}") from exc


def rectangle_dimensions_from_points(points: List[Any]) -> Tuple[float, float]:
    """Calculate rectangle width/height from ordered corner points.

    The expected point order is p1->p2->p3->p4 around the rectangle.
    """
    if len(points) < 3:
        raise ValueError(f"At least 3 points required, got {len(points)}")

    x1, y1 = _point_to_xy(points[0])
    x2, y2 = _point_to_xy(points[1])
    x3, y3 = _point_to_xy(points[2])

    width = math.hypot(x2 - x1, y2 - y1)
    height = math.hypot(x3 - x2, y3 - y2)
    return width, height


class FibucialMillingTaskController:
    """Control a single cross-shaped fibucial milling task inside SLM alignment dialog."""

    def __init__(self, panel: wx.Window, tab: Tab) -> None:
        """Initialize the controller and bind UI actions for fibucial milling."""
        self._panel = panel
        self._tab = tab
        self._main_data_model = tab._main_data_model

        self._ion_beam = tab._main_data_model.ion_beam
        self._fib_stream = tab._fib_stream
        self._canvas = panel.vp_slm_fib_live.canvas
        self._editable_shape: Optional[EditableShape] = None
        self._va_connectors: List[VigilantAttributeConnector] = []
        # self._milling_task_panel: Optional[MillingTaskPanel] = None
        self._updating_shapes = False
        self._is_edit_dragging = False
        self._mill_future: Optional[model.ProgressiveFuture] = None

        self.cross_pattern = CrossPatternParameters(
            width=2e-6,
            height=20e-6,
            depth=1e-6,
            rotation=math.pi / 4,
            center=(0.0, 0.0),
            name="fibucial")

        self.milling_task = MillingTaskSettings(
            milling=self._default_milling_settings(),
            patterns=[self.cross_pattern],
            name="fibucial",
            selected=True)
        self.allow_milling_pattern_move = True

        self.overlay = ShapesOverlay(
            cnvs=self._canvas,
            shape_cls=RectangleOverlay,
            shape_creation_allowed=False)

        self._canvas.add_world_overlay(self.overlay)
        self._canvas.Bind(wx.EVT_LEFT_DOWN, self._on_mouse_left_down)
        self._canvas.Bind(wx.EVT_LEFT_UP, self._on_mouse_left_up)
        self._canvas.Bind(wx.EVT_MOTION, self._on_mouse_motion)

        self._panel.btn_slm_run_milling.Bind(wx.EVT_BUTTON, self._run_milling)
        self._panel.btn_slm_milling_cancel.Bind(wx.EVT_BUTTON, self._cancel_milling)
        self._panel.btn_slm_milling_cancel.Hide()
        self._panel.txt_slm_milling_est_time.SetLabel("fibucial cross ready")
        self._create_milling_task_panel()
        # self._bind_milling_controls()
        self._panel.Layout()

        if hasattr(self._fib_stream, "image"):
            self._fib_stream.image.subscribe(self._on_new_fib_image, init=False)
        self.draw_cross_pattern()

    def _default_milling_settings(self) -> MillingSettings:
        """Create default milling settings from hardware values when available."""
        voltage = self._ion_beam.accelVoltage.value
        fov = self._ion_beam.horizontalFoV.value
        return MillingSettings(
            current=1e-9,
            voltage=voltage,
            field_of_view=fov,
            mode="Serial",
            align=True,
        )

    def _create_milling_task_panel(self) -> None:
        """Create fibucial milling controls dynamically inside the SLM milling panel."""
        if hasattr(self._panel.pnl_slm_milling_task, "_panel_sizer"):
            self._panel.pnl_slm_milling_task.DestroyChildren()

        # create the panels
        self._panel.pnl_slm_milling_task._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self._panel.pnl_slm_milling_task.SetSizer(self._panel.pnl_slm_milling_task._panel_sizer)

        pattern_parameters = ["width", "height", "depth"]
        milling_parameters = ["current", "align"]

        task = self.milling_task
        parameters = task.patterns[0]
        milling = task.milling

        # add the panel to the sizer
        panel = MillingTaskPanel(self._panel.pnl_slm_milling_task, task=task)
        self._panel.pnl_slm_milling_task._panel_sizer.Add(panel, border=10, flag=wx.EXPAND, proportion=0)

        # Hide the auto-generated mode row for fibucial settings.
        # Mode for single pattern is not usefull
        mode_ctrl = panel.ctrl_dict.get("mode")
        if mode_ctrl is not None:
            item = panel.gb_sizer.GetItem(mode_ctrl)
            if item is not None:
                row = item.GetPos().GetRow()
                label_item = panel.gb_sizer.FindItemAtPosition((row, 0))
                if label_item is not None and label_item.GetWindow() is not None:
                    label_item.GetWindow().Hide()
            mode_ctrl.Hide()

        for param in pattern_parameters:
            _va_connector = VigilantAttributeConnector(
                getattr(parameters, param),
                panel.ctrl_dict[param],
                events=wx.EVT_COMMAND_ENTER,
            )

            # VA connector, bind events
            getattr(parameters, param).subscribe(self._on_patterns)

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
            self._va_connectors.append(_va_connector)
            # VA connector, bind events
            getattr(milling, param).subscribe(self._on_patterns)

        # self._milling_task_panel = MillingTaskPanel(
        #     parent=self._panel.pnl_slm_milling_task,
        #     task=self.milling_task,
        # )
        # self._hide_task_panel_parameter("mode")
        # self._panel.pnl_slm_milling_task._panel_sizer.Add(self._milling_task_panel, proportion=0, flag=wx.EXPAND)
        self._panel.pnl_slm_milling_task.Layout()

        # force the scrolled parent to recompute its layout, otherwise pnl_patterns
        # keeps the previous virtual size until the user triggers a resize
        # self._panel.pnl_slm_milling_task.scr_win_right.FitInside()
        # self._panel.pnl_slm_milling_task.scr_win_right.SendSizeEvent()

    # def _hide_task_panel_parameter(self, parameter_name: str) -> None:
    #     """Hide a parameter row in the generated milling panel.
    #
    #     :param parameter_name: Name of the parameter key in ctrl_dict.
    #     """

    #     if self._milling_task_panel is None:
    #         return
    #
    #     ctrl = self._milling_task_panel.ctrl_dict.get(parameter_name)
    #     if ctrl is None:
    #         return
    #
    #     gb_sizer = self._milling_task_panel.gb_sizer
    #     item = gb_sizer.GetItem(ctrl)
    #     if item is None:
    #         return
    #
    #     row = item.GetPos().GetRow()
    #     label_item = gb_sizer.FindItemAtPosition((row, 0))
    #     if label_item is not None and label_item.GetWindow() is not None:
    #         label_item.GetWindow().Hide()
    #     ctrl.Hide()

    # def _bind_milling_controls(self) -> None:
    #     """Bind SLM milling controls to fibucial pattern and milling settings."""
    #     if self._milling_task_panel is None:
    #         return
    #
    #     for control_name, control in self._milling_task_panel.ctrl_dict.items():
    #         if control_name == "mode":
    #             continue
    #
    #         va = None
    #         if hasattr(self.cross_pattern, control_name):
    #             va = getattr(self.cross_pattern, control_name)
    #         elif hasattr(self.milling_task.milling, control_name):
    #             va = getattr(self.milling_task.milling, control_name)
    #
    #         if va is None:
    #             logging.warning("Missing SLM milling VA for %s", control_name)
    #             continue
    #
    #         evt = wx.EVT_COMMAND_ENTER
    #         if isinstance(va, model.BooleanVA):
    #             evt = wx.EVT_CHECKBOX
    #         elif isinstance(va, model.StringEnumerated):
    #             evt = wx.EVT_COMBOBOX
    #
    #         if control is None:
    #             logging.warning("Missing SLM milling control for %s", control_name)
    #             continue
    #         connector = VigilantAttributeConnector(
    #             va,
    #             control,
    #             events=evt,
    #         )
    #         self._va_connectors.append(connector)
    #         va.subscribe(self._on_cross_parameters_changed, init=False)

    def _on_patterns(self, _value: Any) -> None:
        """Redraw the fibucial cross when numeric controls update model values."""
        if self._updating_shapes:
            return
        logging.warning(f"Pattern updated: {_value}")
        self.draw_cross_pattern()

    # def _consume_event(self, evt: wx.Event) -> None:
    #     """Stop event propagation when fibucial edit interactions are active."""
    #     try:
    #         evt.StopPropagation()
    #     except Exception:
    #         pass

    def _on_mouse_left_down(self, evt: wx.MouseEvent) -> None:
        """Start shape edit interactions without forwarding to stage drag handlers."""
        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        if (evt.ShiftDown() and evt.ControlDown()) and self.allow_milling_pattern_move:
            pos = evt.GetPosition()
            hovered_shape = self.overlay._get_shape(pos)
            if hovered_shape is None:
                return
            logging.debug(f"Fibucial shape edit started, shape: {hovered_shape}, view pos = {pos}")
            self._is_edit_dragging = True
            self._set_editable_shape(hovered_shape)
            # p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            # task = self.milling_task
            # for pattern in task.patterns:
            #     pattern.center.value = p_pos

            self.overlay.on_left_down(evt)
            return

        evt.Skip()

    def _on_mouse_left_up(self, evt: wx.MouseEvent) -> None:
        """Finish shape edit interactions and keep stage handlers suppressed."""
        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        if not self._is_edit_dragging:
            evt.Skip()
            return

        self.overlay.on_left_up(evt)
        self._is_edit_dragging = False
        # self._consume_event(evt)

    def _on_mouse_motion(self, evt: wx.MouseEvent) -> None:
        """Route drag motion to shape editing and prevent stage movement."""
        if not self._is_edit_dragging:
            evt.Skip()
            return

        self.overlay.on_motion(evt)
        # self._consume_event(evt)

    def _get_reference_image(self) -> Optional[model.DataArray]:
        """Get the latest FIB image used as reference for coordinate conversion."""
        image_va = getattr(self._fib_stream, "image", None)
        if image_va is None:
            return None
        return image_va.value

    def _set_editable_shape(self, shape: Optional[EditableShape]) -> None:
        """Switch the tracked editable shape and keep callback wiring in one place."""
        if self._editable_shape is not None:
            try:
                self._editable_shape.points.unsubscribe(self._on_shape_points_changed)
            except Exception:
                logging.debug("Skipping stale shape callback during cleanup")

        self._editable_shape = shape
        if self._editable_shape is not None:
            self._editable_shape.points.subscribe(self._on_shape_points_changed, init=False)

    def draw_cross_pattern(self) -> None:
        """Draw the symmetric cross rectangles from the model parameters."""
        # todo check the drawing pattern from other class, why should ref image be needed?
        ref_img = self._get_reference_image()
        if ref_img is None:
            return

        self._updating_shapes = True
        self._set_editable_shape(None)
        self.overlay.clear()
        self.overlay.clear_labels()

        editable_shape: Optional[EditableShape] = None
        for idx, generated_pattern in enumerate(self.cross_pattern.generate()):
            shape = rectangle_pattern_to_shape(
                canvas=self._canvas,
                ref_img=ref_img,
                pattern=generated_pattern,
                colour=MILLING_COLOURS_CANONICAL["Fibucial"],
                name="fibucial" if idx == 0 else None,
                show_selection_points=False,
            )
            self.overlay.add_shape(shape)
            if idx == 0:
                editable_shape = shape

        self._set_editable_shape(editable_shape)
        self._updating_shapes = False
        self._canvas.request_drawing_update()

    def _on_shape_points_changed(self, _points: Any) -> None:
        """Map editable shape point changes back to cross pattern parameters."""
        shape = self._editable_shape
        if shape is None:
            return
        self._on_shape_edited(shape)

    def _on_shape_edited(self, shape: EditableShape) -> None:
        """Update cross parameters from edited shape and redraw the symmetric pair."""
        if self._updating_shapes:
            return

        logging.debug(f"Fibucial shape edited to {shape}")

        ref_img = self._get_reference_image()
        if ref_img is None:
            return

        center_abs = shape.get_position()
        width, height = shape.get_size()
        try:
            points = list(shape.points.value)
            width, height = rectangle_dimensions_from_points(points)
        except (TypeError, ValueError):
            # Fallback to get_size() if points are unavailable during transient edits.
            pass
        center_rel = pos_to_relative(center_abs, ref_img)

        self._updating_shapes = True
        try:
            self.cross_pattern.center.value = center_rel
            self.cross_pattern.width.value = width
            self.cross_pattern.height.value = height
        except ValueError:
            logging.warning("Ignoring fibucial shape update outside configured range")
            self._updating_shapes = False
            return
        self._updating_shapes = False

        self.draw_cross_pattern()

    @call_in_wx_main
    def _on_new_fib_image(self, image: Optional[model.DataArray]) -> None:
        """Draw the pattern when the first usable FIB image becomes available."""
        # todo why should this be needed?
        if image is None:
            return
        if not self.overlay._shapes.value:
            self.draw_cross_pattern()

    @call_in_wx_main
    def _run_milling(self, _evt: wx.Event) -> None:
        """Start fibucial milling with the current cross pattern parameters."""
        # Make sure all the streams are paused
        self._panel.streambar_controller.pauseStreams()

        self._panel.btn_slm_run_milling.Disable()
        self._panel.btn_slm_milling_cancel.Show()
        self._panel.txt_slm_milling_est_time.SetLabel("Running fibucial milling...")
        self._main_data_model.is_acquiring.value = True

        # disable moving milling patterns while milling
        self.allow_milling_pattern_move = False

        self._mill_future = millmng.run_milling_tasks(tasks=[self.milling_task], fib_stream=self._fib_stream)
        self._mill_future.add_done_callback(self._on_milling_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_milling_done(self, future: Optional[model.ProgressiveFuture]) -> None:
        """Update UI when fibucial milling completes, fails or is cancelled."""
        self._panel.btn_slm_milling_cancel.Hide()
        self._panel.btn_slm_run_milling.Enable()
        self._main_data_model.is_acquiring.value = False
        self.allow_milling_pattern_move = True

        if future is None:
            self._panel.txt_slm_milling_est_time.SetLabel("Fibucial milling cancelled")
            self._panel.Layout()
            return

        try:
            future.result()
            status = "fibucial milling completed"
        except CancelledError:
            status = "fibucial milling cancelled"
        except Exception:
            logging.exception("fibucial milling failed")
            status = "fibucial milling failed"

        self._panel.txt_slm_milling_est_time.SetLabel(status)
        self._panel.Layout()

    def _cancel_milling(self, _evt: wx.Event) -> None:
        """Cancel an in-progress fibucial milling task."""
        if self._mill_future is not None:
            self._mill_future.cancel()

    def stop(self) -> None:
        """Tear down subscriptions and overlays on dialog close."""
        self._set_editable_shape(None)
        # self.cross_pattern.width.unsubscribe(self._on_patterns)
        # self.cross_pattern.height.unsubscribe(self._on_patterns)
        # self.cross_pattern.depth.unsubscribe(self._on_patterns)
        # self.milling_task.milling.current.unsubscribe(self._on_patterns)
        self._va_connectors.clear()
        image_va = getattr(self._fib_stream, "image", None)
        if image_va is not None:
            try:
                image_va.unsubscribe(self._on_new_fib_image)
            except Exception:
                pass
        if self._mill_future is not None:
            self._mill_future.cancel()
        try:
            self.overlay.clear()
            self._canvas.remove_world_overlay(self.overlay)
        except Exception:
            pass
        self._canvas.Unbind(wx.EVT_LEFT_DOWN, handler=self._on_mouse_left_down)
        self._canvas.Unbind(wx.EVT_LEFT_UP, handler=self._on_mouse_left_up)
        self._canvas.Unbind(wx.EVT_MOTION, handler=self._on_mouse_motion)

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

        # pattern overlay
        self.rectangles_overlay = ShapesOverlay(
            cnvs=self.canvas,
            shape_cls=RectangleOverlay,
        )
        self.canvas.add_world_overlay(self.rectangles_overlay)
        self.canvas.Bind(wx.EVT_LEFT_DOWN, self.on_mouse_down) # bind the mouse down event
        self.canvas.Bind(wx.EVT_CHAR, self.on_char)

        self.selected_tasks = model.ListVA([])  # List of strings, names of the selected milling tasks
        self._panel.milling_task_chk_list.Bind(wx.EVT_CHECKLISTBOX, handler=self._update_selected_tasks)

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

        # create the panels
        self._panel.pnl_patterns._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self._panel.pnl_patterns.SetSizer(self._panel.pnl_patterns._panel_sizer)

        # create the setting panels, and connectors
        self.controls: Dict[str, MillingTaskPanel] = {}
        pattern_parameters = ["width", "height", "depth", "spacing"] # TODO: add milling params
        # milling params: current, voltage, field of view, mode
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
            for param in pattern_parameters:
                _va_connector = VigilantAttributeConnector(
                    getattr(parameters, param),
                    panel.ctrl_dict[param],
                    events=wx.EVT_COMMAND_ENTER,
                )
                self.controls[task_name][f"{param}_connector"] = _va_connector

                # VA connector, bind events
                getattr(parameters, param).subscribe(self._on_patterns)

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

            if not task.selected:
                panel.Hide()

        self._panel.pnl_patterns.Layout()
        self._panel.Layout()

        # force the scrolled parent to recompute its layout, otherwise pnl_patterns
        # keeps the previous virtual size until the user triggers a resize
        self._panel.scr_win_right.FitInside()
        self._panel.scr_win_right.SendSizeEvent()

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

    def on_mouse_down(self, evt):
        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        feature = self._tab_data.main.currentFeature.value

        # check if shift is pressed, and if a stream is selected
        if (evt.ShiftDown() and evt.ControlDown()
                and self.allow_milling_pattern_move
                and feature and feature.reference_image is not None
        ):
            # get the position of the mouse, convert to physical position
            pos = evt.GetPosition()
            p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            logging.debug(f"shift + control pressed, mouse_pos: {pos}, phys_pos: {p_pos}")

            # TODO: validate if click is outside image bounds, don't move the pattern
            # TODO: validate whether the pattern is within the image bounds before moving it
            # move selected stream to position
            self.move_milling_tasks(pos_to_relative(p_pos, feature.reference_image))
            return

        # super event passthrough
        evt.Skip()

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
        # metres relies on the MD_POS metadata stored in that image
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

            # All patterns across all tasks are shifted by the same view-space offset so
            # their relative positions are preserved (the whole milling stack moves together).
            for task in self.milling_tasks.values():
                for pattern in task.patterns:
                    selected_center_rel = pattern.center.value
                    offset = active_canvas.get_half_buffer_size()
                    center_phys = pos_to_absolute(selected_center_rel, ref_img)
                    center_view = active_canvas.phys_to_view(center_phys, offset)
                    new_center_view = (center_view[0] + view_dx, center_view[1])
                    new_center_phys = active_canvas.view_to_phys(new_center_view, offset)
                    logging.debug(f"Move milling pattern {pattern.name.value} horizontally from physical position"
                                  f" {center_phys}, to new position {new_center_phys}")

                    # move selected pattern to position
                    relative_pos = pos_to_relative(new_center_phys, feature.reference_image)
                    pattern.center.value = relative_pos

            save_project(self._tab_data.main)
            self.draw_milling_tasks()
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

        save_project(self._tab_data.main)
        self.draw_milling_tasks()

    def move_milling_tasks(self, pos: Tuple[float, float]):
        """
        Update the position of the milling patterns for the current feature.
        Also updates the saved positions, and redraws the patterns on the viewport.
        :param pos: the position to draw the patterns at (in m, as relative coordinates to the center of the ion-beam FoV)
        """
        for task in self.milling_tasks.values():
            for pattern in task.patterns:
                pattern.center.value = pos

        save_project(self._tab_data.main)
        self.draw_milling_tasks()

    @call_in_wx_main
    def draw_milling_tasks(self, _=None):
        """Redraw all milling tasks on the canvas.
        """
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

        # redraw all patterns
        for i, (task_name, task) in enumerate(self.milling_tasks.items()):
            if not task.selected:
                continue
            for pattern in task.patterns:
                # logging.debug(f"{task_name}: {pattern.to_json()}")
                for j, pshape in enumerate(pattern.generate()):
                    name = task_name if j == 0 else None
                    shape = rectangle_pattern_to_shape(
                                            canvas=self.canvas,
                                            ref_img=feature.reference_image,
                                            pattern=pshape,
                                            colour=_get_milling_colour(task_name, i),
                                            name=name)
                    self.rectangles_overlay.add_shape(shape)

        # validate the patterns
        self._on_shapes_update(self.rectangles_overlay._shapes.value)

    def _update_selected_tasks(self, evt: wx.Event):
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
        self._update_mill_btn()

    @call_in_wx_main
    def _update_mill_btn(self, _: wx.Event = None):
        """
        Enable/disable mill button depending on the state of the GUI
        """
        is_acquiring = self._tab_data.main.is_acquiring.value
        has_tasks = bool(self.selected_tasks.value)
        valid_patterns = self.valid_patterns.value
        milling_enabled = not is_acquiring and valid_patterns
        self._panel.btn_run_milling.Enable(milling_enabled)
        self._panel.btn_run_automated_milling.Enable(milling_enabled)

        if not has_tasks:
            txt = "No Tasks Selected..."
            self._panel.txt_milling_est_time.SetLabel(txt)
            self._panel.txt_automated_milling_est_time.SetLabel(txt)

        if not valid_patterns:
            txt = "Patterns drawn outside image..."
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
