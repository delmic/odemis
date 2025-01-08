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
from odemis.acq import millmng
from odemis.acq.feature import (
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
    MILLING_TASKS_PATH,
    CryoFeature,
)
from odemis.acq.milling.patterns import RectanglePatternParameters
from odemis.acq.milling.tasks import MillingTaskSettings, load_milling_tasks
from odemis.acq.millmng import MillingWorkflowTask, run_automated_milling
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
MILLING_COLOURS = ["#FFFF00", "#00FFFF", "#FF00FF", "#00FF00", "#FFA500", "#FF69B4"]

def _get_pattern_centre(pos: Tuple[float, float], stream: acqstream.Stream) -> Tuple[float, float]:
    """Convert the position to the centre of the image coordinate (pattern coordinate system)"""
    # get the center of the image, center of the pattern
    stream_pos = stream.raw[0].metadata[model.MD_POS]

    # get the difference between the two
    center_x = pos[0] - stream_pos[0]
    center_y = pos[1] - stream_pos[1]

    return center_x, center_y

def _to_physical_position(pos: Tuple[float, float], stream: acqstream.Stream) -> Tuple[float, float]:
    """Convert the position (pattern coordinates) to the physical coordinate position"""
    # get the center of the image, center of the pattern
    stream_pos = stream.raw[0].metadata[model.MD_POS]

    # get the difference between the two
    center_x = pos[0] + stream_pos[0]
    center_y = pos[1] + stream_pos[1]

    return center_x, center_y

def rectangle_pattern_to_shape(canvas,
                        stream: acqstream.Stream,
                        pattern: RectanglePatternParameters,
                        colour: str = "#FFFF00",
                        name: str = None) -> EditableShape:
    """Convert a rectangle pattern to a shape"""
    rect = RectangleOverlay(cnvs=canvas, colour = colour)
    width = pattern.width.value
    height = pattern.height.value
    x, y = _to_physical_position(pattern.center.value, stream) # image coordinates -> physical coordinates
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
        self.milling_tasks = load_milling_tasks(MILLING_TASKS_PATH) # TODO: move to main_data
        self._default_milling_tasks = copy.deepcopy(self.milling_tasks)
        self.allow_milling_pattern_move = True

        # pattern overlay
        self.rectangles_overlay = ShapesOverlay(
            cnvs=self.canvas,
            shape_cls=RectangleOverlay,
        )
        self.canvas.add_world_overlay(self.rectangles_overlay)
        self.canvas.Bind(wx.EVT_LEFT_DOWN, self.on_mouse_down) # bind the mouse down event

        # set all the tasks to be checked by default
        self.selected_tasks = model.ListVA(list(self.milling_tasks.keys())) # all tasks are selected by default
        self._panel.milling_task_chk_list.SetItems(self.selected_tasks.value)
        for i in range(self._panel.milling_task_chk_list.GetCount()):
            self._panel.milling_task_chk_list.Check(i)
        self._panel.milling_task_chk_list.Bind(wx.EVT_CHECKLISTBOX, self._update_selected_tasks)
        self.selected_tasks.subscribe(self.draw_milling_tasks, init=True)
        # self.selected_tasks.subscribe(self._update_mill_btn, init=True)

        # NOTE: when the feature is changed, the milling tasks should be updated
        # therefore, we should remove these panels, and create new ones with connectors.
        self._create_panels()

        # By default, all widgets are hidden => show button + estimated time at initialization
        self._panel.txt_milling_est_time.Show()
        self._panel.btn_run_milling.Show()
        self._panel.Layout()

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

##########

    @call_in_wx_main
    def _create_panels(self):


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
        milling_parameters = ["current"]

        for task_name, task in self.milling_tasks.items():

            parameters = task.patterns[0]
            milling = task.milling

            # add the panel to the sizer
            panel = MillingTaskPanel(self._panel, task=task)
            self._panel.pnl_patterns._panel_sizer.Add(
                panel, border=10, flag=wx.EXPAND, proportion=1
            )

            self.controls[task_name] = {}
            self.controls[task_name]["panel"] = panel

            # pattern parameters
            for param in pattern_parameters:
                _va_connector = VigilantAttributeConnector(
                    getattr(parameters, param),
                    panel.ctrl_dict[param],
                    events=wx.EVT_TEXT_ENTER,
                )
                self.controls[task_name][f"{param}_connector"] = _va_connector

                # VA connector, bind events
                getattr(parameters, param).subscribe(self._on_patterns)

            # milling parameters
            for param in milling_parameters:
                _va_connector = VigilantAttributeConnector(
                    getattr(milling, param),
                    panel.ctrl_dict[param],
                    events=wx.EVT_TEXT_ENTER,
                )
                self.controls[task_name][f"{param}_connector"] = _va_connector

        self._panel.pnl_patterns.Layout()
        self._panel.Layout()

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

        # check if shift is pressed, and if a stream is selected
        if evt.ShiftDown() and evt.ControlDown() and self.allow_milling_pattern_move:

            # get the position of the mouse, convert to physical position
            pos = evt.GetPosition()
            p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            logging.debug(f"shift + control pressed, mouse_pos: {pos}, phys_pos: {p_pos}")

            # move selected stream to position
            self.draw_milling_tasks(p_pos)
            return

        # super event passthrough
        active_canvas.on_left_down(evt)

    @call_in_wx_main
    def set_milling_tasks(self, milling_tasks: Dict[str, MillingTaskSettings]):
        self.milling_tasks = copy.deepcopy(milling_tasks)

        # unsubscribe from updates to the selected tasks
        self.selected_tasks.unsubscribe(self.draw_milling_tasks)
        self._panel.milling_task_chk_list.Unbind(wx.EVT_CHECKLISTBOX, handler=self._update_selected_tasks)

        # update the selected tasks to tasks in milling_tasks
        self.selected_tasks.value = list(self.milling_tasks.keys())

        # update the checkboxes
        for i in range(self._panel.milling_task_chk_list.GetCount()):
            is_checked = self._panel.milling_task_chk_list.GetString(i) in self.selected_tasks.value
            self._panel.milling_task_chk_list.Check(i, is_checked)

        self._panel.milling_task_chk_list.Bind(wx.EVT_CHECKLISTBOX, self._update_selected_tasks)
        self.selected_tasks.subscribe(self.draw_milling_tasks, init=True)

        self._create_panels()
    # NOTE: we should add the bottom right viewport as the feature viewport, to show the saved reference image and the milling patterns
    # it's too confusing to hav the 'live' view and the 'saved' view in the same viewport
    # -> workflow tab is probably easier to use for this purpose

    @call_in_wx_main
    def draw_milling_tasks(self, pos: Optional[Tuple[float, float]] = None, convert_pos: bool = True):
        """Redraw all milling tasks on the canvas. Clears the rectangles_overlay first, 
        and then redraws all the patterns. If pos is given, the patterns are drawn at that position, 
        otherwise they are drawn at the existing positions. 
        :param pos: the position to draw the patterns at (Optional)
        :param convert_pos: whether to convert the position to the centre of the image coordinate (pattern coordinate system)
        """
        self.rectangles_overlay.clear()
        self.rectangles_overlay.clear_labels()
        tasks_to_draw = self.selected_tasks.value

        # if there are tasks_to_draw that aren't in the milling tasks, add them from _default
        # for task_name in tasks_to_draw:
        #     t1 = list(self.milling_tasks.keys())
        #     if task_name not in self.milling_tasks:
        #         self.milling_tasks[task_name] = copy.deepcopy(self._default_milling_tasks[task_name])
        #         # match the center to the first tasks' center
        #         if t1:
        #             self.milling_tasks[task_name].patterns[0].center.value = self.milling_tasks[t1[0]].patterns[0].center.value

        # stream
        if not self.milling_tasks:
            return

        if not self.acq_cont.stream or not self.acq_cont.stream.raw:
            return

        if not tasks_to_draw:
            self.canvas.request_drawing_update()
            return

        # convert the position to the centre of the image coordinate (pattern coordinate system)
        if isinstance(pos, tuple):
            if convert_pos:
                pos = _get_pattern_centre(pos, self.acq_cont.stream)
            for task_name, task in self.milling_tasks.items():
                for pattern in task.patterns:
                    pattern.center.value = pos # image centre

        # redraw all patterns
        for i, (task_name, task) in enumerate(self.milling_tasks.items()):
            colour = MILLING_COLOURS[i % len(MILLING_COLOURS)]
            if task_name not in tasks_to_draw:
                continue
            for pattern in task.patterns:
                # logging.debug(f"{task_name}: {pattern.to_json()}")
                for j, pshape in enumerate(pattern.generate()):
                    name = task_name if j == 0 else None
                    shape = rectangle_pattern_to_shape(
                                            canvas=self.canvas,
                                            stream=self.acq_cont.stream,
                                            pattern=pshape,
                                            colour=colour,
                                            name=name)
                    self.rectangles_overlay.add_shape(shape)

        # validate the patterns
        self._on_shapes_update(self.rectangles_overlay._shapes.value)

        # auto save the milling tasks on the feature
        self.feature_controller.save_milling_tasks(self.milling_tasks, self.selected_tasks.value)

        return

        # notes:
        # can we fill the rectangles?
        # can we re-colour / hide the control points?
        # can we toggle labels visiblility
        # can we toggle shape visibility
        # how to rotate the shapes?

    def _update_selected_tasks(self, evt: wx.Event):

        checked_indices = self._panel.milling_task_chk_list.GetCheckedItems()
        self.selected_tasks.value = [self._panel.milling_task_chk_list.GetString(i) for i in checked_indices]
        # TODO: migrate to using client data instead of dict -> index?

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
        self._mill_future = millmng.run_milling_tasks(tasks=tasks)

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

        if not has_tasks:
            txt = "No Tasks Selected..."
            self._panel.txt_milling_est_time.SetLabel(txt)

        if not valid_patterns:
            txt = "Patterns drawn outside image..."
            self._panel.txt_milling_est_time.SetLabel(txt)

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
        self._panel.workflow_task_chk_list.SetItems([task.name for task in self.task_list])
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

        # hide/show/disable some widgets
        self._panel.txt_automated_milling_est_time.Hide()
        self._panel.txt_automated_milling_left_time.Show()
        self._panel.gauge_automated_milling.Show()
        self._panel.btn_automated_milling_cancel.Show()
        self._tab_data.main.is_acquiring.value = True
        self._tab_data.main.is_milling.value = True

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

        # dialog to confirm the milling
        task_names = ", ".join([t.name for t in task_list])
        dlg = wx.MessageDialog(
            self._panel,
            f"Start workflows ({task_names}) for {len(features)} features?",
            "Start Automated Milling",
            wx.YES_NO | wx.ICON_QUESTION,
        )

        # TODO: add estimated time to the dialog, gui

        if dlg.ShowModal() == wx.ID_NO:
            self._on_automation_done(None)
            return

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

        # Update the milling status text
        try:
            future.result()
            milling_status_txt = "Milling completed."
        except CancelledError:
            milling_status_txt = "Milling cancelled."
        except Exception:
            milling_status_txt = "Milling failed."

        self._panel.txt_automated_milling_est_time.SetLabel(milling_status_txt)
        self._panel.txt_automated_milling_status.SetLabel(milling_status_txt)

    def _cancel_automated_milling(self, _):
        """
        called when the button "Cancel" is pressed
        """
        logging.debug("Cancelling automated milling.")
        self.automation_future.cancel()
