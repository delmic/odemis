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

import copy
import logging
import os
from concurrent.futures import CancelledError
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import wx

import odemis.acq.stream as acqstream
from odemis import model
from odemis.acq.feature import (
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
    CryoFeature, save_features,
)
from odemis.acq.milling import millmng, DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.millmng import MillingWorkflowTask, run_automated_milling
from odemis.acq.milling.patterns import RectanglePatternParameters
from odemis.acq.milling.tasks import MillingTaskSettings, load_milling_tasks
from odemis.acq.move import (
    MILLING,
)
from odemis.gui.comp.milling import MillingTaskPanel
from odemis.gui.comp.overlay.base import Vec
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.gui.comp.overlay.shapes import EditableShape, ShapesOverlay
from odemis.gui.conf import get_acqui_conf
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
}

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
                        name: str = None) -> EditableShape:
    """Convert a rectangle pattern to a shape"""
    rect = RectangleOverlay(cnvs=canvas, colour = colour, show_selection_points = False)
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

    # rect.set_rotation(math.radians(45)) #  TODO: how to rotate the shape?

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

        # pattern overlay
        self.rectangles_overlay = ShapesOverlay(
            cnvs=self.canvas,
            shape_cls=RectangleOverlay,
        )
        self.canvas.add_world_overlay(self.rectangles_overlay)
        self.canvas.Bind(wx.EVT_LEFT_DOWN, self.on_mouse_down) # bind the mouse down event

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
    def _update_pattern_panels(self):
        """
        Update the pattern settings control, when a new feature is selected.
        :return:
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

        save_features(self._tab.conf.pj_last_path, self._tab_data.main.features.value)
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

        save_features(self._tab.conf.pj_last_path, self._tab_data.main.features.value)
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
