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
from functools import partial

import wx

import odemis.acq.stream as acqstream
import odemis.gui.conf.file as conffile
from odemis import model
from odemis.acq import millmng
from odemis.acq.milling.patterns import RectanglePatternParameters
from odemis.acq.milling.tasks import MillingSettings2
from odemis.acq.feature import FEATURE_ROUGH_MILLED, FEATURE_ACTIVE
from odemis.gui import model as guimod
from odemis.gui.comp.overlay.base import Vec
from odemis.gui.comp.milling import MillingPatternPanel, MillingSettingsPanel
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.gui.comp.overlay.shapes import MillingShapesOverlay, EditableShape
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.gui.util.widgets import (
    ProgressiveFutureConnector,
    VigilantAttributeConnector,
)
from odemis.util import units, conversion, is_point_in_rect
from odemis.util.filename import make_unique_name

MILLING_SETTINGS_PATH = os.path.join(conffile.CONF_PATH, "mimas.mill.yaml")


class MillingButtonController:
    """
    Takes care of the mill button and initiates the serial milling job.
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

        # By default, all widgets are hidden => show button + estimated time at initialization
        self._panel.txt_milling_est_time.Show()
        self._panel.btn_mill_active_features.Show()
        self._panel.Layout()

        self._tab_data.main.features.subscribe(self._on_features, init=True)

        self._tab_data.main.is_acquiring.subscribe(self._on_acquisition, init=True)

        # bind events (buttons, checking, ...) with callbacks
        # for "MILL" button
        self._panel.btn_mill_active_features.Bind(wx.EVT_BUTTON, self._on_milling_series)

        # for "Cancel" button
        self._panel.btn_milling_cancel.Bind(wx.EVT_BUTTON, self._cancel_milling_series)

    @call_in_wx_main
    def _on_milling_series(self, evt: wx.Event):
        """
        called when the button "MILL" is pressed
        """
        try:
            millings = millmng.load_config(MILLING_SETTINGS_PATH)
        except Exception:
            logging.exception("Failed to load milling settings from %s", MILLING_SETTINGS_PATH)
            return

        # Make sure all the streams are paused
        self._tab.streambar_controller.pauseStreams()

        # hide/show/disable some widgets
        self._panel.txt_milling_est_time.Hide()
        self._panel.txt_milling_series_left_time.Show()
        self._panel.gauge_milling_series.Show()
        self._panel.btn_milling_cancel.Show()
        self._tab_data.main.is_acquiring.value = True

        aligner = self._tab_data.main.aligner
        ion_beam = self._tab_data.main.ion_beam
        sed = self._tab_data.main.sed
        stage = self._tab_data.main.stage

        # filter the features that have status active
        sites = [f for f in self._tab_data.main.features.value if f.status.value == FEATURE_ACTIVE]

        feature_post_status = FEATURE_ROUGH_MILLED

        acq_streams = self._tab_data.acquisitionStreams.value

        logging.info("Going to start milling")
        self._mill_future = millmng.mill_features(millings, sites, feature_post_status, acq_streams,
                                                  ion_beam, sed, stage, aligner)

        # # link the milling gauge to the milling future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._mill_future,
            bar=self._panel.gauge_milling_series,
            label=self._panel.txt_milling_series_left_time,
            full=False,)

        self._mill_future.add_done_callback(self._on_milling_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_milling_done(self, future):
        """
        Called when the acquisition process is
        done, failed or canceled
        """
        self._acq_future = None
        self._gauge_future_conn = None
        self._tab_data.main.is_acquiring.value = False

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
        """
        Updates the estimated time required for millings
        """
        try:
            millings = millmng.load_config(MILLING_SETTINGS_PATH)
        except Exception as ex:
            logging.info("Failed to load milling settings from %s: %s", MILLING_SETTINGS_PATH, ex)
            return

        aligner = self._tab_data.main.aligner
        ion_beam = self._tab_data.main.ion_beam
        sed = self._tab_data.main.sed
        stage = self._tab_data.main.stage

        # filter the features that have status active
        sites = [f for f in self._tab_data.main.features.value if f.status.value == FEATURE_ACTIVE]
        feature_post_status = FEATURE_ROUGH_MILLED
        acq_streams = self._tab_data.acquisitionStreams.value
        millings_time = millmng.estimate_milling_time(millings, sites, feature_post_status, acq_streams,
                                                      ion_beam, sed, stage, aligner)
        millings_time = math.ceil(millings_time)

        # display the time on the GUI
        txt = u"Estimated time: {}.".format(units.readable_time(millings_time, full=False))
        self._panel.txt_milling_est_time.SetLabel(txt)

    def _cancel_milling_series(self, _):
        """
        called when the button "Cancel" is pressed
        """
        logging.debug("Cancelling milling.")
        self._mill_future.cancel()

    @call_in_wx_main
    def _on_features(self, features):
        """
        Updates milling time and availability of the mill button when there's an update on the features
        """
        self._update_milling_time()
        self._update_mill_btn()

        # In case there is a new feature, also listen when its status changes
        # (no effect on the features we already listen too)
        for f in self._tab_data.main.features.value:
            f.status.subscribe(self._on_features)

    def _on_acquisition(self, is_acquiring: bool):
        """
        Called when is_acquiring changes
        Enable/Disable mill button
        """
        self._update_mill_btn()

    @call_in_wx_main
    def _update_mill_btn(self):
        """
        Enable/disable mill button depending on the state of the GUI
        """
        # milling button is enabled if and only if there is at least one site, and no acquisition is active
        sites = [f for f in self._tab_data.main.features.value if f.status.value == FEATURE_ACTIVE]
        # enable or disable the mill button
        has_sites = bool(sites)
        is_acquiring = self._tab_data.main.is_acquiring.value

        self._panel.btn_mill_active_features.Enable(not is_acquiring and has_sites)

def _get_pattern_centre(pos: tuple, stream: acqstream.Stream):
    """Convert the position to the centre of the image coordinate (pattern coordinate system)"""
    # get the center of the image, center of the pattern
    stream_pos = stream.raw[0].metadata[model.MD_POS]

    # get the difference between the two
    center_x = pos[0] - stream_pos[0]
    center_y = pos[1] - stream_pos[1]

    return center_x, center_y

class MillingPatternController:
    """Controller for a single milling pattern"""

    def __init__(
        self,
        parent: "MillingTaskController",
        name: str,
        shape: EditableShape,
        colour: str = "#ffff00",
    ):
        self.parameters = RectanglePatternParameters(
            width=10e-6, height=10e-6, depth=1e-6, name=name
        )
        self.shape = shape
        self.parent = parent
        self.stream = parent.stream
        self.valid = model.BooleanVA(False)

        self.panel = None  # panel not created until later

        self.shape.colour = conversion.hex_to_frgba(colour)
        self.shape.points.subscribe(self._on_shape_points)
        self.shape.selected.subscribe(self._on_shape_selected)
        self.valid.subscribe(self.parent._all_valid_patterns)

    def __repr__(self):
        return f"{self.parameters.name} {self.parameters.to_json()}"

    def _on_shape_selected(self, selected):
        """Called when the shape is selected or deselected"""

        if self.panel:
            self.panel.Show(selected)  # show the panel if selected
            self.parent._panel.Layout()

            logging.debug(f"{self.parameters.name.value} selected: {selected}")

    def _on_shape_points(self, points: list) -> None:
        """Called when the shape points change"""
        s = self.shape
        pos = s.get_position()
        w, h = s.get_size()

        # update parameters
        self.parameters.width.value = w
        self.parameters.height.value = h
        self.parameters.center.value = _get_pattern_centre(pos, self.stream)

        # check if pattern is valid
        self._is_valid_pattern()

    def _is_valid_pattern(self) -> bool:
        """Check if any pattern points are outside image bounds"""
        shape = self.shape
        s_bbox = self.stream.getBoundingBox()

        # check if any of the points are outside the bounding box of the image
        self.valid.value = all(
            [is_point_in_rect(pt, s_bbox) for pt in shape.points.value]
        )

        logging.debug(f"{self.parameters.name.value} valid: {self.valid.value}")

        return self.valid.value

    def create_panel(self):
        """Add a panel for the pattern"""

        # create panel
        self.panel = MillingPatternPanel(self.parent._panel, self.parameters)

        # add the panel to the sizer
        self.parent._panel.pnl_patterns._panel_sizer.Add(
            self.panel, border=10, flag=wx.EXPAND, proportion=1
        )
        self.parent._panel.pnl_patterns.Layout()
        self.parent._panel.Layout()

        # VA connector, bind events
        # TODO: dynamically bind these to support abc parameters
        # TODO: name, rotation, center
        self._width_va_connector = VigilantAttributeConnector(
            self.parameters.width,
            self.panel.ctrl_dict["width"],
            events=wx.EVT_TEXT_ENTER,
        )
        self._height_va_connector = VigilantAttributeConnector(
            self.parameters.height,
            self.panel.ctrl_dict["height"],
            events=wx.EVT_TEXT_ENTER,
        )
        self._depth_va_connector = VigilantAttributeConnector(
            self.parameters.depth,
            self.panel.ctrl_dict["depth"],
            events=wx.EVT_TEXT_ENTER,
        )
        self._scan_direction_va_connector = VigilantAttributeConnector(
            self.parameters.scan_direction,
            self.panel.ctrl_dict["scan_direction"],
            events=wx.EVT_COMBOBOX,
        )

        # update shapes when attributes change
        self.parameters.width.subscribe(self._update_shape)
        self.parameters.height.subscribe(self._update_shape)

    def _update_shape(self, _: float):
        """Update shape overlay when shape size parameter changes"""
        # TODO: also support rotation

        width = self.parameters.width.value
        height = self.parameters.height.value
        shape = self.shape
        pos = shape.get_position()

        logging.debug(
            f"updating shape for {self.parameters.name.value} to size {width}x{height}"
        )

        # pause the event connector to avoid infinite loop
        self.shape.points.unsubscribe(self._on_shape_points)

        # update the shape with new width, height
        # RectangleEditingMixin (point layout)
        # 1  -  2
        # |     |
        # 4  -  3

        x, y = pos
        shape.p_point1 = Vec(x - width / 2, y + height / 2)
        shape.p_point2 = Vec(x + width / 2, y + height / 2)
        shape.p_point3 = Vec(x + width / 2, y - height / 2)
        shape.p_point4 = Vec(x - width / 2, y - height / 2)

        # # update the shape points
        shape._phys_to_view()
        shape._points = shape.get_physical_sel()
        shape.points.value = shape._points

        # redraw, and re-connect event listeners
        wx.CallAfter(self.parent.viewport.canvas.request_drawing_update)
        self.shape.points.subscribe(self._on_shape_points)

        logging.debug(f"shape size after update: {shape.get_size()}")

        return

class MillingSettingsController:
    def __init__(self, parent: "MillingTaskController"):
        self.parent = parent
        self.settings = MillingSettings2(
            current=2e-9, voltage=30e3, field_of_view=80e-6
        )
        self.panel = None

        self.create_panel()

    def create_panel(self):
        """Add a panel for the pattern"""

        # create panel
        self.panel = MillingSettingsPanel(self.parent._panel, self.settings)

        # add the panel to the sizer
        self.parent._panel.pnl_milling_settings._panel_sizer.Add(
            self.panel, border=10, flag=wx.EXPAND, proportion=1
        )
        self.parent._panel.pnl_milling_settings.Layout()
        self.parent._panel.Layout()

        # VA connector, bind events
        # TODO: dynamically bind these to support abc parameters
        self._current_va_connector = VigilantAttributeConnector(
            self.settings.current,
            self.panel.ctrl_dict["current"],
            events=wx.EVT_TEXT_ENTER,
        )
        self._voltage_va_connector = VigilantAttributeConnector(
            self.settings.voltage,
            self.panel.ctrl_dict["voltage"],
            events=wx.EVT_TEXT_ENTER,
        )
        self._fov_va_connector = VigilantAttributeConnector(
            self.settings.field_of_view,
            self.panel.ctrl_dict["field_of_view"],
            events=wx.EVT_TEXT_ENTER,
        )
        self._mode_va_connector = VigilantAttributeConnector(
            self.settings.mode,
            self.panel.ctrl_dict["mode"],
            events=wx.EVT_COMBOBOX
        )


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

        self.stream = tab.fib_stream  # fib stream
        self.viewport = tab_panel.pnl_secom_grid.viewports[1]  # fib viewport
        self.canvas = self.viewport.canvas  # fib canvas
        self._create_panels()

        self.patterns = model.ListVA([])
        self.settings_controller = MillingSettingsController(self)

        # pattern overlay
        self.rectangles_overlay = MillingShapesOverlay(
            cnvs=self.canvas,
            shape_cls=RectangleOverlay,
            tool=guimod.TOOL_RECTANGLE,
            tool_va=tab_data.tool
        )
        self.canvas.add_world_overlay(self.rectangles_overlay)
        self.rectangles_overlay.new_shape.subscribe(self.add_pattern)
        self.rectangles_overlay.shapes.subscribe(self._on_shapes_update) 
        # TODO: integrate with undo/redo functionality

        self._pattern_sub_callback = {}

        # By default, all widgets are hidden => show button + estimated time at initialization
        self._panel.txt_milling_est_time.Show()
        self._panel.btn_run_milling.Show()
        self._panel.Layout()

        self.patterns.subscribe(self._on_patterns, init=True)
        self._tab_data.main.is_acquiring.subscribe(self._on_acquisition, init=True)

        # check pattern validity
        self.valid_patterns = model.BooleanVA(False)
        self._tab_data.main.stage.position.subscribe(
            self._all_valid_patterns, init=True
        )
        self._tab_data.streams.subscribe(self._all_valid_patterns, init=True)
        self.valid_patterns.subscribe(self._on_patterns, init=True)

        # bind milling events
        self._panel.btn_run_milling.Bind(wx.EVT_BUTTON, self._run_milling)
        self._panel.btn_clear_patterns.Bind(wx.EVT_BUTTON, self._clear_patterns)
        self._panel.btn_milling_cancel.Bind(wx.EVT_BUTTON, self._cancel_milling_series)

    def add_pattern(self, shape: EditableShape = None):
        """Add a new pattern controller"""
        # TODO: deselect other shapes on creation

        num = len(self.patterns.value) + 1
        names = [
            p.parameters.name.value for p in self.patterns.value
        ]  # existing shapes names
        name = make_unique_name(f"Rectangle-{num}", names)

        pattern_ctrl = MillingPatternController(
            name=name, shape=shape, colour="#ffff00", parent=self
        )
        logging.debug(f"Adding pattern {pattern_ctrl.parameters.name.value}")

        # fix from fastem roa creation (prevents phantom shapes from being created)
        sub_callback = partial(self._add_pattern_ctrl, pattern_ctrl=pattern_ctrl)
        self._pattern_sub_callback[pattern_ctrl] = sub_callback
        pattern_ctrl.shape.points.subscribe(sub_callback)

    def _add_pattern_ctrl(self, points: list, pattern_ctrl: MillingPatternController):
        pattern_ctrl.shape.points.unsubscribe(
            self._pattern_sub_callback[pattern_ctrl]
        )

        # Abort pattern creation if nothing was selected
        if len(points) == 0:
            logging.warning("Aborting pattern creation. Removing shape")
            self._remove_shape(pattern_ctrl.shape)
            del self._pattern_sub_callback[pattern_ctrl]
            return

        else:
            # add the panel
            pattern_ctrl.create_panel()
            # add the pattern to model
            self.patterns.value.append(pattern_ctrl)

        logging.debug(
            f"Pattern added: {pattern_ctrl.parameters.name.value} ({len(self.patterns.value)} patterns)"
        )

    @call_in_wx_main
    def _clear_patterns(self, evt: wx.Event):
        logging.debug("Removing all patterns from model")

        while self.patterns.value:
            self.remove_pattern(self.patterns.value[0])
        wx.CallAfter(self.canvas.request_drawing_update)

        logging.debug(f"{len(self.patterns.value)} patterns remaining.")

    def remove_pattern(self, pattern: MillingPatternController):
        logging.debug(f"Removing Pattern {pattern.parameters.name.value}")

        # deselect shape
        pattern.shape.selected.value = False

        # remove panel
        pattern.panel.Destroy()
        self._panel.Layout()

        # remove shape
        self._remove_shape(pattern.shape)

        # remove pattern
        self.patterns.value.remove(pattern)

    def _remove_shape(self, shape):
        """Remove the shape from the canvas and redraw"""
        # TODO: migrate to new overlay
        self.rectangles_overlay.remove_shape(shape)
        self.canvas.remove_world_overlay(shape)
        wx.CallAfter(self.canvas.request_drawing_update)

    @call_in_wx_main
    def _on_shapes_update(self, shapes):
        """Called when the shapes are updated"""
        logging.debug("Shapes updated: %s", shapes)

        # practically only way to detect a shape has been removed
        if len(shapes) < len(self.patterns.value):
            # a shape was removed, remove the corresponding pattern
            for p in self.patterns.value:
                # find which shape was removed
                if p.shape not in shapes:
                    self.remove_pattern(p)
                    continue

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

        # construct milling task settings
        settings = millmng.MillingTaskSettings(
            milling=self.settings_controller.settings,
            patterns=[p.parameters for p in self.patterns.value],
        )

        logging.debug("---------- Preparing to start milling ----------")
        logging.debug(f"Mill Settings: {settings.milling}")
        logging.debug(f"{len(settings.patterns)} Patterns")
        for p in settings.patterns:
            logging.debug(f"Pattern: {p}")
        logging.debug("-------------------------------------------------")

        # run the milling task
        self._mill_future = millmng.mill_patterns(settings=settings)

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
            units.readable_time(10 * len(self.patterns.value), full=False)
        )
        self._panel.txt_milling_est_time.SetLabel(txt)

    def _on_patterns(self, _):
        """
        Updates milling time and availability of the mill button when there's an update on the patterns
        """
        self._update_milling_time()
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
        self._update_mill_btn()

    @call_in_wx_main
    def _update_mill_btn(self):
        """
        Enable/disable mill button depending on the state of the GUI
        """
        is_acquiring = self._tab_data.main.is_acquiring.value
        has_patterns = bool(self.patterns.value)
        valid_patterns = self.valid_patterns.value
        self._panel.btn_run_milling.Enable(
            not is_acquiring and has_patterns and valid_patterns
        )

        if not has_patterns:
            txt = "No patterns drawn..."
            self._panel.txt_milling_est_time.SetLabel(txt)

        if not valid_patterns:
            txt = "Patterns drawn outside image..."
            self._panel.txt_milling_est_time.SetLabel(txt)

    def _all_valid_patterns(self, _) -> bool:
        """Check if all patterns are valid"""
        self.valid_patterns.value = all(p.valid.value for p in self.patterns.value)
        return self.valid_patterns.value

    def _create_panels(self):
        """Create the panels for the patterns and settings"""
        self._panel.pnl_patterns._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self._panel.pnl_patterns.SetSizer(self._panel.pnl_patterns._panel_sizer)
        self._panel.pnl_milling_settings._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self._panel.pnl_milling_settings.SetSizer(self._panel.pnl_milling_settings._panel_sizer)

# structure:
# MillingTaskController
#   settings: MillingSettingsController
#               settings
#               panel
#   patterns: list[MillingPatternController]
#               pattern
#               panel
#               shape

## KNOWN BUGS
# Need to double enter to update pattern width and height and depth
# time estimate is not correct
# pattern validation not working when moving stage or re-imaging


# TODO:
# hide pattern -> dont show pattern
# disable pattern -> skipped when milling
# display and edit centre position
# consolidate panels into a single class
