
# -*- coding: utf-8 -*-

"""
@author Patrick Cleeve

Copyright © 2023, Delmic

Handles the controls for correlating two (or more) streams together.

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
import copy
import logging
import math
import threading
import time
from enum import Enum
from typing import List

import wx

# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

from odemis.acq.target import Target
from odemis.gui.model import TOOL_FEATURE, TOOL_FIDUCIAL

import odemis.acq.stream as acqstream
import odemis.gui.model as guimod
from odemis import model
from odemis.acq.stream import RGBStream, StaticFluoStream, StaticSEMStream, StaticStream
from odemis.gui.cont.tabs.localization_tab import LocalizationTab
from odemis.gui.util import call_in_wx_main

# TODO: move to more approprate location
def update_image_in_views(s: StaticStream, views: List[guimod.StreamView]) -> None:
    """Force update the static stream in the selected views (forces image update)
    :param s: (StaticStream) the static stream to update
    :param views: (list[StreamView]) the list of views to update"""
    v: guimod.StreamView
    for v in views:
        for sp in v.stream_tree.getProjections():  # stream or projection
            st = sp.stream if isinstance(sp, acqstream.DataProjection) else sp

            # only update the selected stream
            if st is s:
                sp.force_image_update()
def convert_rgb_to_sem(rgb_stream: RGBStream) -> StaticSEMStream:
    """Convert an RGB stream to a SEM stream
    :param rgb_stream: (RGBStream) the RGB stream to convert
    :return: (StaticSEMStream) the converted SEM stream
    """
    d = rgb_stream.raw[0]
    if isinstance(d, model.DataArrayShadow):
        d = d.getData()

    # get dim order, and select the first channel (arbitrary choice)
    dims = d.metadata[model.MD_DIMS]
    if dims == "YXC":
        d = d[:, :, 0]
    elif dims == "CYX":
        d = d[0, :, :]

    # convert to sem stream
    sem_stream = StaticSEMStream(rgb_stream.name.value, raw=d)

    # update md
    sem_stream.raw[0].metadata = copy.deepcopy(d.metadata)
    sem_stream.raw[0].metadata[model.MD_ACQ_TYPE] = model.MD_AT_EM
    sem_stream.raw[0].metadata[model.MD_DIMS] = "YX"

    return sem_stream

class CorrelationMetadata:
    """
    Required image metadata for correlation calculation, alternatively use data directly.
    """
    def __init__(self, fib_image_shape: List[int], fib_pixel_size: List[float], fm_image_shape: List[int], fm_pixel_size: List[float]):
        self.fib_image_shape = fib_image_shape
        self.fib_pixel_size = fib_pixel_size
        self.fm_image_shape = fm_image_shape
        self.fm_pixel_size = fm_pixel_size

# class CorrelationTargets:
#     def __init__(self, targets: List[Target], projected_targets: List[Target], fib_surface_fiducial: Target,
#                  fib_stream: StaticSEMStream, fm_streams: List[StaticFluoStream],
#                  image_metadata: CorrelationMetadata,
#                  correlation_result: float = None, refractive_index_correction: bool = True,
#                  superz: StaticFluoStream = None):
#         self.targets = targets
#         self.projected_targets = projected_targets
#         self.correlation_result = correlation_result
#         self.fib_surface_fiducial = fib_surface_fiducial
#         self.refractive_index_correction = refractive_index_correction
#         # self.fm_roi = fm_roi
#         # self.fm_fiducials = fm_fiducials
#         # self.fib_fiducials = fib_fiducials
#         # self.fib_roi = fib_roi
#         self.fib_stream = fib_stream
#         self.fm_streams = fm_streams
#         self.superz = superz
#         self.image_metadata = image_metadata

class CorrelationTarget:
    def __init__(self):
        self.fm_pois : List[Target] = []
        self.fm_fiducials : List[Target] = []
        self.fib_fiducials : List[Target] = []
        self.fib_surface_fiducial : Target = None

        self.correlation_result: float = None
        self.refractive_index_correction: bool = True
        self.fib_projected_pois: List[Target] = []
        self.fib_projected_fiducials: List[Target] = []

        self.fib_stream :StaticSEMStream = None
        self.fm_streams: List[StaticFluoStream] = []
        self.superz: StaticFluoStream = None
        self.image_metadata: CorrelationMetadata = None

    def reset_attributes(self):
        # rest of the attributes is set to none except the streams
        self.correlation_result = None
        self.fib_projected_pois = None
        self.fib_projected_fiducials = None

class CorrelationController(object):

    def __init__(self, tab_data, panel, tab, viewports):
        """
        :param tab_data: (MicroscopyGUIData) the representation of the microscope GUI
        :param panel: (wx._windows.Panel) the panel containing the UI controls
        :param tab: (Tab) the tab which should show the data
        :param viewports: (ViewPorts) the tab view ports
        """

        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._panel = panel
        self._tab = tab
        self._viewports = viewports

        # disable if no streams are present
        self._tab_data_model.streams.subscribe(self._on_correlation_streams_change, init=True)
        self._tab_data_model.streams.subscribe(self._update_correlation_cmb, init=True)
        if not self._main_data_model.is_viewer:
            # localisation tab doesn't exist in viewer
            self._tab_data_model.streams.subscribe(self.update_localization_tab_streams, init=False)

        # connect the correlation streams to the tab data
        self._panel.cmb_correlation_stream.Bind(wx.EVT_COMBOBOX, self._on_selected_stream_change)

        # reference frames
        self.ref_frame_names = ["METEOR", "FIBSEM", "No Reference"] # for display purposes
        self.ref_stream_map = {0: StaticFluoStream,  # METEOR
                               1: StaticSEMStream,  # FIBSEM
                               2: type(None)} # match everything except None (No reference type)
        self.ref_stream = self.ref_stream_map[0]

        # connect the reference stream to the tab data
        self._panel.cmb_correlation_reference.Append(self.ref_frame_names)
        self._panel.cmb_correlation_reference.SetSelection(0)
        self._panel.cmb_correlation_reference.Bind(wx.EVT_COMBOBOX, self._on_reference_stream_change)

        # reset correlation data
        self._panel.btn_reset_correlation.Bind(wx.EVT_BUTTON, self.reset_correlation_pressed)

        # enable correlation controls
        self._panel.ctrl_enable_correlation.SetValue(True) # enable by default
        self._panel.ctrl_auto_resize_view.SetValue(False) # disable auto resize by default
        self._panel.ctrl_enable_correlation.SetToolTip("Enable/Disable correlation controls")
        self._panel.ctrl_auto_resize_view.SetToolTip("Automatically resize correlation overlay viewports after moving the streams.")

        # bind mouse and keyboard events for correlation controls
        for vp in self._viewports:
            vp.canvas.Bind(wx.EVT_CHAR, self.on_char)
            vp.canvas.Bind(wx.EVT_LEFT_DOWN, self.on_mouse_down)

        # localization tab
        self.localization_tab: LocalizationTab = None

    @call_in_wx_main
    def _update_correlation_cmb(self, streams: list) -> None:
        """update the correlation combo box with the available streams
        :param streams: (list[StaticStream]) the streams to add to the combo box"""
        # keep the combobox in sync with streams
        self._panel.cmb_correlation_stream.Clear()
        for s in streams:
            if isinstance(s, self.ref_stream):
                continue # cant move fluo streams
            self._panel.cmb_correlation_stream.Append(s.name.value, s)

        # select the first stream, if available and nothing is selected
        if (self._panel.cmb_correlation_stream.GetCount() > 0
            and self._panel.cmb_correlation_stream.GetSelection() == wx.NOT_FOUND):
            self._panel.cmb_correlation_stream.SetSelection(0) # this doesn't trigger selection event

            # trigger the event manually, to automatically select the first stream
            if self._tab_data_model.selected_stream.value is None:
                logging.debug(f"Forcing selected stream change to first stream")
                self._on_selected_stream_change(None)

    @call_in_wx_main
    def _on_correlation_streams_change(self, streams: list) -> None:
        """hide/show the correlation panel if there are no streams
        :param streams: (list[StaticStream]) the streams in the correlation tab"""
        visible = len(streams) != 0
        self._panel.fp_meteor_correlation.Show(visible)
        # reset selected stream
        if not visible:
            self._panel.cmb_correlation_stream.Clear()
            self._tab_data_model.selected_stream.value = None

    @call_in_wx_main
    def _on_selected_stream_change(self, evt: wx.Event) -> None:
        """change the selected stream to the one selected in the combo box
        :param evt: (wx.Event) the event"""
        idx = self._panel.cmb_correlation_stream.GetSelection()
        self._tab_data_model.selected_stream.value = self._panel.cmb_correlation_stream.GetClientData(idx)
        logging.debug(f"Selected Stream Changed to {idx}: {self._tab_data_model.selected_stream.value.name.value}")

    @call_in_wx_main
    def _on_reference_stream_change(self, evt: wx.Event) -> None:
        """change the reference stream to the one selected in the combo box
        :param evt: (wx.Event) the event"""
        idx = self._panel.cmb_correlation_reference.GetSelection()
        self.ref_stream = self.ref_stream_map[idx]
        logging.debug(f"Reference Frame: {self.ref_frame_names[idx]} - Fixed stream: {idx}, {self.ref_stream}")

        # refresh combobox
        self._update_correlation_cmb(self._tab_data_model.streams.value)

    def reset_correlation_pressed(self, evt: wx.Event) -> None:
        """"Reset the correlation data for the selected stream, and re-draw
        :param evt: (wx.Event) the event"""
        s = self._tab_data_model.selected_stream.value
        self._reset_stream_correlation_data(s)
        update_image_in_views(s, self._tab_data_model.views.value)
        self.fit_correlation_views_to_content(force=True)

    def _reset_stream_correlation_data(self, s: StaticStream) -> None:
        """reset the stream position to the original position / rotation / scale
        :param s: (StaticStream) the stream to reset"""
        if s is not None and s.raw:
            s.raw[0].metadata[model.MD_POS_COR] = (0, 0)
            s.raw[0].metadata[model.MD_ROTATION_COR] = 0
            s.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = (1, 1)

            self.update_localization_tab_streams_metadata(s)

    def add_streams(self, streams: list) -> None:
        """add streams to the correlation tab
        :param streams: (list[StaticStream]) the streams to add"""

        # NOTE: we only add streams if they are not already in the correlation tab
        # we will not remove streams from the correlation tab from outside it,
        # as the user may still want to correlate them,
        # even if they have been removed from another tab, e.g. 'localization'

        # add streams to correlation tab
        logging.debug(f"Adding {len(streams)} streams to correlation tab {streams}")
        for s in streams:

            # skip existing streams, live streams
            if s in self._tab_data_model.streams.value or not isinstance(s, StaticStream):
                continue

            # if the user has loaded a rgb stream, assume it is meant to be a SEM stream
            # (convert to 2D SEM stream, fix metadata, etc.)
            if isinstance(s, RGBStream):
                logging.debug(f"Converting RGB stream to SEM: {s.name.value}")
                s = convert_rgb_to_sem(s)

            # reset the stream correlation data, and add to correlation streams
            self._reset_stream_correlation_data(s)
            self._tab_data_model.streams.value.append(s)

            # add stream to streambar
            sc = self._tab.streambar_controller.addStream(s, add_to_view=True, play=False)
            sc.stream_panel.show_remove_btn(True)

    def _stop_streams_subscriber(self):
        self._tab_data_model.streams.unsubscribe(self.update_localization_tab_streams)

    def _start_streams_subscriber(self):
        self._tab_data_model.streams.subscribe(self.update_localization_tab_streams, init=False)

    @call_in_wx_main
    def update_localization_tab_streams(self, streams: list) -> None:
        """add streams to the localization tab
        :param streams: (list[StaticStream]) the streams to add"""
        if self.localization_tab is None:
            self.localization_tab: LocalizationTab  = self._main_data_model.getTabByName("cryosecom-localization")

        # TODO: extend this to support non-overviews
        # remove streams from localization tab when they are deleted from correlation tab
        current_streams = len(streams)
        localization_streams = len(self.localization_tab.tab_data_model.overviewStreams.value)
        if current_streams < localization_streams:
            # remove streams from localization tab
            logging.debug("Attempting to remove streams from localization tab")
            for s in self.localization_tab.tab_data_model.overviewStreams.value:
                if s not in streams:
                    logging.debug(f"Removing stream from other tabs: {s.name.value}")
                    # remove from model
                    self.localization_tab.tab_data_model.overviewStreams.value.remove(s)
                    self.localization_tab.tab_data_model.streams.value.remove(s)

                    # remove from overview view
                    self.localization_tab._acquired_stream_controller._ov_view.removeStream(s)
                    update_image_in_views(s, self.localization_tab.tab_data_model.views.value)
                    logging.debug(f"Stream removed from localization tab: {s.name.value}")

                    # remove from chamber tab
                    chamber_tab = self._main_data_model.getTabByName("cryosecom_chamber")
                    chamber_tab.remove_overview_streams([s])
                    logging.debug(f"Stream removed from chamber tab: {s.name.value}")

                    return

        logging.debug(f"Adding {len(streams)} streams to localization tab {streams}")
        for s in streams:
            # add stream to localizations tab
            if s not in self.localization_tab.tab_data_model.streams.value:
                self.localization_tab.tab_data_model.overviewStreams.value.append(s)
                self.localization_tab.tab_data_model.streams.value.insert(0, s)
                self.localization_tab._acquired_stream_controller.showOverviewStream(s)


    def clear_streams(self) -> None:
        """clears streams from the correlation tab"""
        self._tab.streambar_controller.clear()

    def correlation_enabled(self) -> bool:
        """return if correlation controls are enabled and a stream is selected"""
        logging.debug(f"correlation enabled: {self._panel.ctrl_enable_correlation.IsChecked()}")
        logging.debug(f"selected stream: {self._tab_data_model.selected_stream.value}")
        return (self._panel.ctrl_enable_correlation.IsChecked() and
                self._tab_data_model.selected_stream.value is not None)

    def on_mouse_down(self, evt: wx.Event) -> None:
        """handle mouse down events
        :param evt: (wx.Event) the event"""
        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        # check if shift is pressed, and if a stream is selected
        if evt.ShiftDown() and self.correlation_enabled():

            # get the position of the mouse, convert to physical position
            pos = evt.GetPosition()
            p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            logging.debug(f"shift pressed, mouse_pos: {pos}, phys_pos: {p_pos}")

            # move selected stream to position
            self._move_stream_to_pos(p_pos)

        elif self._tab_data_model.tool.value == guimod.TOOL_RULER:
            logging.debug(f"Ruler is active, passing event to gadget overlay")
            active_canvas.gadget_overlay.on_left_down(evt)  # ruler is active, pass event to ruler
        else:
            logging.debug("invalid correlation event, passing event to canvas")
            active_canvas.on_left_down(evt)       # super event passthrough

    def on_char(self, evt: wx.Event) -> None:
        """handle key presses
        :param evt: (wx.Event) the event"""

        # event data
        key = evt.GetKeyCode()
        shift_mod = evt.ShiftDown()

        # pass through event, if not a valid correlation key or enabled
        valid_keys = [wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_UP, wx.WXK_DOWN]
        if key not in valid_keys or not self.correlation_enabled():
            evt.Skip()
            return

        ### CONTROLS ##############################
        # SHIFT + LEFT CLICK -> MOVE_TO_POSITION
        # LEFT, RIGHT -> TRANSLATION X
        # UP, DOWN -> TRANSLATION Y
        # SHIFT + LEFT, RIGHT -> ROTATION
        # SHIFT + UP, DOWN -> SCALE
        ###########################################
        dx, dy, dr, dpx  = 0, 0, 0, 0

        # correlation control modifiers
        if shift_mod:
            dr = math.radians(self._panel.dr_step_cntrl.GetValue())
            dpx = self._panel.dpx_step_cntrl.GetValue() / 100
        else:
            dx = dy = self._panel.dxy_step_cntrl.GetValue()

        logging.debug(f"key: {key}, shift: {shift_mod}")
        logging.debug(f"dx: {dx}, dy: {dy}, dr: {dr}, dpx: {dpx}")

        if key == wx.WXK_LEFT:
            self._move_stream(-dx, 0, -dr, 0)
        elif key == wx.WXK_RIGHT:
            self._move_stream(dx, 0, dr, 0)
        elif key == wx.WXK_UP:
            self._move_stream(0, dy, 0, dpx)
        elif key == wx.WXK_DOWN:
            self._move_stream(0, -dy, 0, -dpx)

    def _move_stream(self, dx: float, dy: float , dr: float = 0, dpx: float = 0) -> None:
        """move the selected stream by the specified amount. the stream is forced
        to update in the views.
        :param dx: (float) the change in x translation (metres)
        :param dy: (float) the change y translation (metres)
        :param dr: (float) the change in rotation (radians)
        :param dpx: (float) the change in scale of the pixelsize (percentage)
        """
        if self._tab_data_model.selected_stream.value is None:
            return

        s = self._tab_data_model.selected_stream.value

        logging.debug(f"move stream {s.name.value}: {dx}, {dy}, {dr}, {dpx}")

        # translation
        p = s.raw[0].metadata[model.MD_POS_COR]
        if len(p) == 2:
            x, y = p
            s.raw[0].metadata[model.MD_POS_COR] = (x - dx, y - dy) # correlation direction is reversed
        else:
            x, y, z = p
            s.raw[0].metadata[model.MD_POS_COR] = (x - dx, y - dy, z)

        # rotation
        rotation = s.raw[0].metadata.get(model.MD_ROTATION_COR, 0)
        s.raw[0].metadata[model.MD_ROTATION_COR] = (rotation + dr)

        # scale (pixel size)
        scalecor = s.raw[0].metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1))
        s.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = (scalecor[0] + dpx, scalecor[1] + dpx)

        # TODO: split x, y scale, add shear?

        # update the image in the views
        update_image_in_views(s, self._tab_data_model.views.value)

        # fit viewports to content, as they have moved
        self.fit_correlation_views_to_content()

        # update the localization tab
        if not self._main_data_model.is_viewer:
            self.update_localization_tab_streams_metadata(s)

    def fit_correlation_views_to_content(self, force: bool = False) -> None:
        # TODO: be more selective about which viewports to fit
        # fit the source viewports to the content, as the image may have moved
        self._panel.vp_correlation_tl.canvas.fit_view_to_content()
        self._panel.vp_correlation_tr.canvas.fit_view_to_content()

        # fit the overlay viewports to the content (optional)
        auto_resize = self._panel.ctrl_auto_resize_view.IsChecked()
        if auto_resize or force:
            self._panel.vp_correlation_bl.canvas.fit_view_to_content()
            self._panel.vp_correlation_br.canvas.fit_view_to_content()

    def update_localization_tab_streams_metadata(self, s: StaticStream) -> None:
        """update the metadata of the stream in the localization tab"""

        if self.localization_tab is None:
            self.localization_tab: LocalizationTab = self._main_data_model.getTabByName("cryosecom-localization")

        # also update the localization tab
        if s in self.localization_tab.tab_data_model.streams.value:

            logging.debug(f"Updating localisation stream: {s.name.value}")

            # get stream in localization tab
            idx = self.localization_tab.tab_data_model.streams.value.index(s)
            sl = self.localization_tab.tab_data_model.streams.value[idx]

            # match cor metadata
            sl.raw[0].metadata[model.MD_POS_COR] = s.raw[0].metadata[model.MD_POS_COR]
            sl.raw[0].metadata[model.MD_ROTATION_COR] = s.raw[0].metadata[model.MD_ROTATION_COR]
            sl.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = s.raw[0].metadata[model.MD_PIXEL_SIZE_COR]

            update_image_in_views(s, self.localization_tab.tab_data_model.views.value)

    def _move_stream_to_pos(self, pos: tuple) -> None:
        """move the selected stream to the position pos
        :param pos: (tuple) the realspace position to move the stream to (metres)"""
        # the difference between the clicked position, and the position in metadata
        # is the offset (be careful because correlation is sign flipped)
        s = self._tab_data_model.selected_stream.value

        # the cur_pos is the realspace position of the image
        p = s.raw[0].metadata.get(model.MD_POS, (0,0))
        x, y = p[:2]  # x, y positions only (ignore z)

        # the correlation pos is the change in position in the viewer
        cx, cy = s.raw[0].metadata[model.MD_POS_COR]

        # the new position is the difference between the clicked position and the realspace position
        # i.e. the correlation position. However, already have an existing correlation position, so we need to
        # find how much we need to modify this by.
        nx = -(pos[0] - x)
        ny = -(pos[1] - y)

        # the offset is the difference between the current cor position and the new position
        # that is how much we need to move the current correlation position by.
        dx = cx - nx
        dy = cy - ny

        logging.debug(f"cur_pos: {x}, {y}, cor_pos: {cx}, {cy}, new_pos: {nx}, {ny}, offset_pos: {dx}, {dy}")

        # move the stream using the correlation position offset
        self._move_stream(dx=dx, dy=dy, dr=0, dpx=0)

# create an enum with column labels and position
class GridColumns(Enum):
    Type = 0  # Column for "type"
    X = 1     # Column for "x"
    Y = 2     # Column for "y"
    Z = 3     # Column for "z"
    Index = 4 # Column for "index"


DEBUG = False
class CorrelationPointsController(object):

    def __init__(self, tab_data, panel, tab, viewports):
        """
        :param tab_data: (MicroscopyGUIData) the representation of the microscope GUI
        :param panel: (wx._windows.Panel) the panel containing the UI controls
        :param tab: (Tab) the tab which should show the data
        :param viewports: (ViewPorts) the tab view ports
        """

        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._panel = panel
        self._tab = tab
        self._viewports = viewports

        self._panel.fp_correlation_streams.Show(True)


        # Access the correlation points table (wxListCtrl)
        self.grid = panel.table_grid

        # Access the stream selector (for FIB/FM selection)
        # self.stream_selector = panel.stream_selector

        # Access the Z-targeting button
        self.z_targeting_btn = panel.btn_z_targeting
        self.z_targeting_btn.Enable(False)  # Initially disable the Z-targeting button

        # Access the Refractive Index correction
        self.refractive_index_btn = panel.btn_refractive_index
        # self.fib_surface_fiducial: Target = None
        self.refractive_index_btn.Bind(wx.EVT_BUTTON, self.on_refractive_index)

        self.delete_btn = panel.btn_delete_row

        # Bind events for stream selector and Z-targeting
        # self.stream_selector.Bind(wx.EVT_COMBOBOX, self.on_stream_change)
        self.z_targeting_btn.Bind(wx.EVT_BUTTON, self.on_z_targeting)

        # Bind the event for cell selection
        self.grid.Bind(wx.grid.EVT_GRID_SELECT_CELL, self.on_cell_selected)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGING, self.on_cell_changing)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGED, self.on_row_selected)

        # Initialize the table for displaying points

        # self.load_btn.Bind(wx.EVT_BUTTON, self.on_load_points)
        self.delete_btn.Bind(wx.EVT_BUTTON, self.on_delete_row)



        self.grid.CreateGrid(0, 5)  # TODO make variables

        # Hide the default row labels (serial numbers)
        self.grid.SetRowLabelSize(0)

        # Set column labels for correlation points
        # Set the data type and if the column can be edited
        self.grid.SetColLabelValue(GridColumns.Type.value, GridColumns.Type.name)
        attr = wx.grid.GridCellAttr()
        attr.SetReadOnly(True)
        self.grid.SetColAttr(0, attr)
        # self.grid.SetColLabelValue(1, "X")
        # self.grid.SetColLabelValue(2, "Y")
        # self.grid.SetColLabelValue(3, "Z")
        # self.grid.SetColLabelValue(4, "index")
        self.grid.SetColLabelValue(GridColumns.X.value, GridColumns.X.name)
        self.grid.SetColLabelValue(GridColumns.Y.value, GridColumns.Y.name)
        self.grid.SetColLabelValue(GridColumns.Z.value, GridColumns.Z.name)
        self.grid.SetColLabelValue(GridColumns.Index.value, GridColumns.Index.name)


        # Set column 1 (Index) as an integer column
        int_renderer = wx.grid.GridCellNumberRenderer()
        int_attr = wx.grid.GridCellAttr()
        int_attr.SetRenderer(int_renderer)
        self.grid.SetColAttr(4, int_attr)

        # Set columns 2, 3, and 4 (X, Y, Z Coordinates) as float columns with 2 decimal places
        float_renderer = wx.grid.GridCellFloatRenderer(precision=3)
        float_attr = wx.grid.GridCellAttr()
        float_attr.SetRenderer(float_renderer)

        self.grid.SetColAttr(1, float_attr)
        self.grid.SetColAttr(2, float_attr)
        self.grid.SetColAttr(3, float_attr)
        # Enable cell editing
        self.grid.EnableEditing(True)

        # Auto-size columns
        # self.grid.AutoSizeColumns()

        # self._populate_table()
        # TODO make sure before initializing this class, feature ans feature status is fixed ? (Controller)
        if DEBUG:
            self.correlation_target = CorrelationTarget()
        else:
            self.correlation_target = None
        # self._tab_data_model.main.currentFeature.correlation_targets[self._tab_data_model.main.currentFeature.status.value] = CorrelationTarget()
        # self.correlation_target = self._tab_data_model.main.currentFeature.correlation_targets[self._tab_data_model.main.currentFeature.status.value]

        self._tab_data_model.main.targets.subscribe(self._on_target_changes, init=True)
        self.current_target_coordinate_subscription = False
        self._tab_data_model.main.currentTarget.subscribe(self._on_current_target_changes, init=True)
        self._tab_data_model.fib_surface_point.subscribe(self._on_current_fib_surface, init=True)
        # if self._tab_data_model.main.currentTarget:
        #     self._tab_data_model.main.currentTarget.value.coordinates.subscribe(self._on_current_coordinates_changes, init=True)

        panel.fp_correlation_panel.Show(True)
        # Show the main frame
        # self.main_frame.Show()
        for vp in self._viewports:
            vp.canvas.Bind(wx.EVT_CHAR, self.on_char)

        self.correlation_txt = panel.txt_correlation_rms
        self.correlation_txt.Show(True)
        self.latest_change = None           # Holds the latest change
        self.lock = threading.Lock()        # Lock to synchronize access to changes
        self.is_processing = False          # To track if the function is currently processing
        self.process_thread = None          # The thread handling the change

    # @call_in_wx_main
    # def _on_target_changes(self, targets: list) -> None:
    #     pass
    #     # self._populate_table()

    # initialize correlation target class in the current feature, once the minimum requirements
    # make sure it is not initialized multiple times
    # add correlation target as an attribute in cryo feature, no need of VA
    # make an update function, which updates the specific parts of the class when changed

    def update_feature_correlation_target(self, surface_fiducial = False):

        if not self.correlation_target:
            return

        if surface_fiducial:
            # todo check if it has a value
            fib_surface_fiducial = self._tab_data_model.fib_surface_point.value
            self.correlation_target.fib_surface_fiducial = fib_surface_fiducial
        else:
        # if True:
            fib_fiducials = []
            fm_fiducials = []
            fm_pois = []
            # fib_surface_fiducial = None
            for target in self._tab_data_model.main.targets.value:
                if "FIB" in target.name.value:
                    fib_fiducials.append(target)
                elif "FM" in target.name.value:
                    fm_fiducials.append(target)
                elif "POI" in target.name.value:
                    fm_pois.append(target)
            if fib_fiducials:
                fib_fiducials.sort(key=lambda x: x.index.value)
                self.correlation_target.fib_fiducials = fib_fiducials
            if fm_fiducials:
                fm_fiducials.sort(key=lambda x: x.index.value)
                self.correlation_target.fm_fiducials = fm_fiducials
            if fm_pois:
                fm_pois.sort(key=lambda x: x.index.value)
                self.correlation_target.fm_pois = fm_pois


        self.correlation_target.reset_attributes()


    def check_correlation_conditions(self):
        if not DEBUG:
            if not self._tab_data_model.main.currentFeature.value:
                return False
            elif not self._tab_data_model.main.currentFeature.value.correlation_targets:
                self._tab_data_model.main.currentFeature.value.correlation_targets[self._tab_data_model.main.currentFeature.value.status.value] = CorrelationTarget()
                self.correlation_target = self._tab_data_model.main.currentFeature.value.correlation_targets[self._tab_data_model.main.currentFeature.value.status.value]


        if self.correlation_target:
            if not DEBUG:
                if len(self.correlation_target.fib_fiducials) >= 4 and len(self.correlation_target.fm_fiducials) >= 4 and len(self.correlation_target.fm_pois) > 0 and self.correlation_target.fib_surface_fiducial:
                    return True
                else:
                    return False
            else:
                if len(self.correlation_target.fib_fiducials) >= 1 and len(self.correlation_target.fm_fiducials) >= 1 and self.correlation_target.fib_surface_fiducial:
                    return True
                else:
                    return False
        else:
            return False




    def on_refractive_index(self, evt):
        # only one target that keeps on changing, at the end if do correlation possible,
        # rerun the correlation calculation (call decorator)
        # how to do calculation if things rapidly change than the calculation speed

        if self._tab_data_model.focussedView.value.name.value == "SEM Overview":
            # pass
            self._tab_data_model.main.selected_target_type.value = "SurfaceFiducial"
            self._tab_data_model.tool.value = TOOL_FIDUCIAL   # TODO should not select this (confusing)
            # self.update_feature_correlation_target(surface_fiducial=True)

        # if self.check_correlation_conditions():
        #     self.latest_change = True
        #     self.queue_latest_change()

    def queue_latest_change(self):
        """
        This method is called when there's an input change.
        If there's a change already being processed, it will cancel it and process the latest change.
        """
        with self.lock:  # Ensure thread-safe access to latest_change
            self.latest_change = True # Overwrite with the latest change

            if self.is_processing:  # If a process is running, it will handle the latest change.
                logging.warning("Multipoint Correlation is running. It will handle the latest change.")
                self.correlation_txt.SetLabel("Correlation RMS Deviation :  Calculating...")
                return

            # Start processing the latest change in a separate thread
            self.process_thread = threading.Thread(target=self.process_latest_change)
            self.process_thread.start()

    def process_latest_change(self):
        """
        Processes only the latest change. If a new change comes in, it will only process that.
        """
        while True:
            with self.lock:
                if not self.latest_change:
                    break  # No more changes to process

                self.latest_change = False  # Clear the latest change to indicate it's being processed
                self.is_processing = True

            # Process the change (simulate long processing)
            # TODO add logging commands
            self.do_correlation()

            with self.lock:
                self.correlation_txt.SetLabel("Correlation RMS Deviation :  Result")
                self.is_processing = False  # Mark that processing is complete

    # @call_in_wx_main
    def do_correlation(self):
        # type the text
        time.sleep(5) #Will slow down
        self._tab_data_model.projected_points = []
        for target in self._tab_data_model.main.targets.value:
            if "FIB" in target.name.value:
                # deep copy of target such that type can be changed without changing the target
                target_copy = copy.deepcopy(target)
                target_copy.type.value = "ProjectedPoints"
                self._tab_data_model.projected_points.append(target_copy)

        for vp in self._viewports:
            if vp.view.name.value == "SEM Overview":
                vp.canvas.update_drawing()



        # self._tab_data_model.main.selected_target_type.value = "ProjectedPoints"
        # self._tab_data_model.tool.value = TOOL_FIDUCIAL

    def on_char(self, evt: wx.Event) -> None:
        """handle key presses
        :param evt: (wx.Event) the event"""

        # event data
        key = evt.GetKeyCode()
        # shift_mod = evt.ShiftDown()
        ctrl_mode = evt.ControlDown()

        # pass through event, if not a valid correlation key or enabled
        valid_keys = [wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_UP, wx.WXK_DOWN]
        if key not in valid_keys:
            evt.Skip()
            return

        if ctrl_mode:
            # add fiducials if up
            # add pois if down (only for FLM)
            if key == wx.WXK_UP:
                if self._tab_data_model.focussedView.value.name.value == "FLM Overview" or self._tab_data_model.focussedView.value.name.value == "SEM Overview":
                    self._tab_data_model.main.selected_target_type.value = "Fiducial"
                    self._tab_data_model.tool.value = TOOL_FIDUCIAL
                      # in tad data ? TODO
            elif key == wx.WXK_DOWN:
                if self._tab_data_model.focussedView.value.name.value == "FLM Overview":
                    self._tab_data_model.main.selected_target_type.value = "RegionOfInterest"
                    self._tab_data_model.tool.value = TOOL_FIDUCIAL   # POI
                      # in tad data ? TODO
            # Static Fluo Stream

        ### CONTROLS ##############################
        # SHIFT + LEFT CLICK -> MOVE_TO_POSITION
        # LEFT, RIGHT -> TRANSLATION X
        # UP, DOWN -> TRANSLATION Y
        # SHIFT + LEFT, RIGHT -> ROTATION
        # SHIFT + UP, DOWN -> SCALE
        # ###########################################
        # dx, dy, dr, dpx  = 0, 0, 0, 0
        #
        # # correlation control modifiers
        # if shift_mod:
        #     dr = math.radians(self._panel.dr_step_cntrl.GetValue())
        #     dpx = self._panel.dpx_step_cntrl.GetValue() / 100
        # else:
        #     dx = dy = self._panel.dxy_step_cntrl.GetValue()
        #
        # logging.debug(f"key: {key}, shift: {shift_mod}")
        # logging.debug(f"dx: {dx}, dy: {dy}, dr: {dr}, dpx: {dpx}")
        #
        # if key == wx.WXK_LEFT:
        #     self._move_stream(-dx, 0, -dr, 0)
        # elif key == wx.WXK_RIGHT:
        #     self._move_stream(dx, 0, dr, 0)
        # elif key == wx.WXK_UP:
        #     self._move_stream(0, dy, 0, dpx)
        # elif key == wx.WXK_DOWN:
        #     self._move_stream(0, -dy, 0, -dpx)

    def on_load_points(self, event):
        """
        Add a new point (Index, X, Y, Z) to the grid.
        """
        new_point = ["6", "15.0", "25.1", "35.3"]  # Example new point, could be loaded from a file
        new_row = self.grid.GetNumberRows()
        self.grid.AppendRows(1)  # Add a new row

        for col, value in enumerate(new_point):
            self.grid.SetCellValue(new_row, col, value)

    def on_delete_row(self, event):
        """
        Delete the currently selected row.
        """
        selected_rows = self.grid.GetSelectedRows()
        # row_evt = event.GetRow()
        if selected_rows:
            # for col in range(self.grid.GetNumberCols()):
            #     self.grid.SetCellBackgroundColour(selected_rows[0], col, wx.RED)
            for row in selected_rows:
                self.grid.DeleteRows(pos=row, numRows=1, updateLabels=True)

                # delete the from targets list by checking the row label which is same as the target name
                for target in self._tab_data_model.main.targets.value:
                    # if target.name.value == self.grid.GetRowLabelValue(row):
                    #     self._tab_data_model.main.targets.value.remove(target)
                    #     break
                    if target.name.value == self._tab_data_model.main.currentTarget.value.name.value:
                        self._tab_data_model.main.targets.value.remove(target)
                        self._tab_data_model.main.currentTarget.value = None
                        break

    def on_cell_selected(self, event):
        row = event.GetRow()
        # col = event.GetCol()
        # logging.debug(f"Cell selected at row {row}, column {col}")
        # # Add your logic here for when a cell is selected
        # event.Skip()
        row_label = self.grid.GetRowLabelValue(event.GetRow())
        # if row_label:
        #     self._tab_data_model.main.currentTarget.value = [t for t in self._tab_data_model.main.targets.value
        #                                                      if t.name.value == row_label][0]
        # self._tab_data_model.main.currentTarget.value = [t for t in self._tab_data_model.main.targets.value
        #                                                  if t.name.value == row_label][0]
        for target in self._tab_data_model.main.targets.value:
            if self.selected_target_in_grid(target, row):
            # if target.name.value == self.grid.GetRowLabelValue(event.GetRow()):
                self._tab_data_model.main.currentTarget.value = target
                break
        # highlight the selected row
        self.grid.SelectRow(event.GetRow())
        event.Skip()

    def selected_target_in_grid(self, target, row) -> bool:
        # check if target and row values exist
        grid_selection = False
        if not target or self.grid.GetCellValue(row, GridColumns.Index.value) == "":
            return False

        if target.index.value == int(self.grid.GetCellValue(row, GridColumns.Index.value)) and (
                self.grid.GetCellValue(row, GridColumns.Type.value) in target.name.value):
            return True

        return grid_selection


    def on_cell_changing(self, event):
        col = event.GetCol()
        new_value = event.GetString()
        col_name = self.grid.GetColLabelValue(col)

        # get the row label and select the current target based on the row label
        # todo should happen before the event is triggered
        # TODO index when changes cannot be more than the number of max indices in the list
        # row_label = self.grid.GetRowLabelValue(event.GetRow())
        # self._tab_data_model.main.currentTarget.value = [t for t in self._tab_data_model.main.targets.value
        #                                                  if t.name.value == row_label][0]
        #
        # # highlight the selected row
        # self.grid.SelectRow(event.GetRow())

        if col_name == GridColumns.Type.name:
            wx.MessageBox("Type cannot be changed", "Invalid Input", wx.OK | wx.ICON_ERROR)
            event.Veto()
            return
        elif col_name == GridColumns.Index.name:
            try:
                int(new_value)
                self._tab_data_model.main.currentTarget.value.index.value = int(new_value)
            except ValueError:
                wx.MessageBox("Index must be a int!", "Invalid Input", wx.OK | wx.ICON_ERROR)
                event.Veto()  # Prevent the change
                return
        elif col_name in [GridColumns.X.name, GridColumns.Y.name, GridColumns.Z.name]:
            try:
                p = float(new_value)
                # if Z and FIB target, do not allow the change, before calling this function TODO
                if col_name == GridColumns.X.name:
                    coord = self._tab_data_model.main.currentTarget.value.coordinates.value
                    self._tab_data_model.main.currentTarget.value.coordinates.value = tuple((p, coord[1], coord[2]))
                    # self._tab_data_model.main.currentTarget.value.coordinates.value[0] = float(new_value)
                # elif col_name == "Y":
                #     self._tab_data_model.main.currentTarget.value.coordinates.value[1] = float(new_value)
                # elif col_name == "Z" and ("FIB" not in self._tab_data_model.main.currentTarget.value.name.value):
                #     # keep Z value empty for FIB targets as they don't have Z coordinates
                #     self._tab_data_model.main.currentTarget.value.coordinates.value[2] = float(new_value)
            except ValueError:
                wx.MessageBox("X, Y, Z values must be a float!", "Invalid Input", wx.OK | wx.ICON_ERROR)
                event.Veto()  # Prevent the change
                return

        event.Skip()  # Allow the change if validation passes

    def on_row_selected(self, event):

        col = event.GetCol()
        # col_ind = self.grid.GetColIndex("Index")
        # col_name = self.grid.GetColLabelValue(col)
        # TODO
        if col == 4:  # Index column was changed
            self.reorder_table()

    @call_in_wx_main
    def _on_current_target_changes(self, target):
        # according to target name, highlight the row
        for row in range(self.grid.GetNumberRows()):
        # if target and target.name.value in [self.grid.GetRowLabelValue(row) for row in range(self.grid.GetNumberRows())]:
        #     if target and self.grid.GetRowLabelValue(row) == target.name.value:
            if self.selected_target_in_grid(target, row):
                # get the row index from row label which is target name
                # row_index = self.grid.get_row_index(target.name.value)
                self.grid.SelectRow(row)
                self.reorder_table()
                break

        if self._tab_data_model.main.currentTarget.value and not self.current_target_coordinate_subscription:
            self._tab_data_model.main.currentTarget.value.coordinates.subscribe(self._on_current_coordinates_changes, init=True)
            self.current_target_coordinate_subscription = True
            # subscribe only once


    def _on_current_fib_surface(self, fib_surface_fiducial):
        #todo can be done in intialization
        if self._tab_data_model.fib_surface_point.value:
            self._tab_data_model.fib_surface_point.value.coordinates.subscribe(self._on_current_coordinates_fib_surface, init=True)

    def _on_current_coordinates_fib_surface(self, coordinates):
        self.update_feature_correlation_target(surface_fiducial=True)

        if self.check_correlation_conditions():
            self.latest_change = True
            self.queue_latest_change()

    @call_in_wx_main
    def _on_current_coordinates_changes(self, coordinates):
        target = self._tab_data_model.main.currentTarget.value
        existing_target =  False
        self.current_target_coordinate_subscription = False
        # existing_names = [t.name.value for t in self._tab_data_model.main.targets.value]
        # if existing_names and target.name.value in existing_names:
        for row in range(self.grid.GetNumberRows()):
            if self.selected_target_in_grid(target, row):
        # if target and target.name.value in [self.grid.GetRowLabelValue(row) for row in range(self.grid.GetNumberRows())]:
        #     if target and self.grid.GetRowLabelValue(row) == target.name.value:

                # get the row index from row label which is target name
                # row_index = self.grid.get_row_index(target.name.value)
                # self.grid.SelectRow(row)
                self.grid.SetCellValue(row, GridColumns.X.value, str(target.coordinates.value[0]))
                self.grid.SetCellValue(row, GridColumns.Y.value, str(target.coordinates.value[1]))
                if target.coordinates.value[2]:
                    self.grid.SetCellValue(row, GridColumns.Z.value, str(target.coordinates.value[2]))

                self.update_feature_correlation_target()

            if self.check_correlation_conditions():
                self.latest_change = True
                self.queue_latest_change()


    @call_in_wx_main
    def _on_target_changes(self, targets):
        """
        Enable or disable buttons based on stream selection.
        When FM is selected, the Z-targeting button is enabled.
        When FIB is selected, the Z-targeting button is disabled.
        """
        # # select the grid row according the current target selection
        # for row in range(self.grid.GetNumberRows()):
        #     if self.grid.GetRowLabelValue(row) == target.name.value:
        #         self.grid.SelectRow(row)
        #         break
        # if the value of the current target is changed, update the corresponing grid row
        # if new value add row in the grid otherwise update the row
        target = self._tab_data_model.main.currentTarget.value
        existing_target =  False
        # existing_names = [t.name.value for t in self._tab_data_model.main.targets.value]
        # if existing_names and target.name.value in existing_names:

        # for row in range(self.grid.GetNumberRows()):
        # # if target and target.name.value in [self.grid.GetRowLabelValue(row) for row in range(self.grid.GetNumberRows())]:
        # # get selected row index and type
        #     if self.selected_target_in_grid(target, row):
        #     # if target and self.grid.GetRowLabelValue(row) == target.name.value:
        #         # get the row index from row label which is target name
        #         # row_index = self.grid.get_row_index(target.name.value)
        #         self.grid.SelectRow(row)
        #         self.grid.SetCellValue(row, GridColumns.X.value, str(target.coordinates.value[0]))
        #         self.grid.SetCellValue(row, GridColumns.Y.value, str(target.coordinates.value[1]))
        #         if target.coordinates.value[2]:
        #             self.grid.SetCellValue(row, GridColumns.Z.value, str(target.coordinates.value[2]))
        #         # self.grid.SetCellValue(row, 4, str(target.index.value))
        #         existing_target = True
        #         # else if a new target is added which is not present in target list, add it in the grid

        if target: # and not existing_target:

            current_row_count = self.grid.GetNumberRows()
            self.grid.SelectRow(current_row_count)
            self.grid.AppendRows(1)
            self.grid.SetRowLabelValue(current_row_count, target.name.value)
            self.grid.SetCellValue(current_row_count, GridColumns.X.value, str(target.coordinates.value[0]))
            self.grid.SetCellValue(current_row_count, GridColumns.Y.value, str(target.coordinates.value[1]))
            self.grid.SetCellValue(current_row_count, GridColumns.Index.value, str(target.index.value))

            if target.type.value == "Fiducial":
                if target.coordinates.value[2]:
                    self.grid.SetCellValue(current_row_count, GridColumns.Type.value, "FM")
                    self.grid.SetCellValue(current_row_count, GridColumns.Z.value, str(target.coordinates.value[2]))
                else:
                    self.grid.SetCellValue(current_row_count, GridColumns.Type.value, "FIB")
                    self.grid.SetCellValue(current_row_count, GridColumns.Z.value, "")
            elif target.type.value == "RegionOfInterest":
                self.grid.SetCellValue(current_row_count, GridColumns.Type.value, "POI")

        # self.grid.Layout()
        self._panel.Layout()


        # if target:
        #     if "FM" in target.name.value:
        #         self.z_targeting_btn.Enable(True)
        #     else:
        #         self.z_targeting_btn.Enable(False)

    def on_z_targeting(self, event):
        """
        Handle Z-targeting when the Z-targeting button is clicked.
        This will update the Z value in the table and change the color based on success.
        """
        selected_row = self.table.GetFirstSelected()
        if selected_row == -1:
            return  # No row selected

        # Simulate Z-targeting (replace this with actual Z-targeting logic)
        success = self.perform_z_targeting()

        if success:
            z_value = self.get_z_target_value()  # Get the Z-targeting result
            self.table.SetItem(selected_row, 3, str(z_value))
            self.table.SetItemTextColour(selected_row, wx.Colour(0, 255, 0))  # Green for success
        else:
            previous_z_value = self.table.GetItemText(selected_row, 3)
            self.table.SetItemTextColour(selected_row, wx.Colour(255, 0, 0))  # Red for failure

    def perform_z_targeting(self) -> bool:
        """ Simulate a Z-targeting success/failure. Replace with actual Z-targeting logic. """
        return True  # Simulating success for now

    def get_z_target_value(self) -> float:
        """ Return the simulated Z-targeting value. Replace with real value. """
        return 42.0  # Example Z-value for demonstration

    def _populate_table(self):
        """
        Populates the wxListCtrl with initial values (if any).
        You can customize this method to load saved data or start with an empty table.
        """



        # from target list, add 0,1,2 to x y z
        # the row label is the target.name with type being the target.type
        # set the index from 1 and increment, separately for target.type in Fiducials and RegionOfInterests with Z none and RegionOfInterests with Z
        # set the index from 1 and increment, separately for target.type in Fiducials and RegionOfInterests with Z none and RegionOfInterests with Z

        for i, target in enumerate(self._tab_data_model.main.targets.value):
            # row = self.grid.GetNumberRows()
            # check if target name is already present in the grid row label
            # if not append row
            if target.name.value in [self.grid.GetRowLabelValue(row) for row in range(self.grid.GetNumberRows())]:
                self.grid.SetCellValue(i, 1, str(target.coordinates.value[0]))
                self.grid.SetCellValue(i, 2, str(target.coordinates.value[1]))
                if target.coordinates.value[2]:
                    self.grid.SetCellValue(i, 3, str(target.coordinates.value[2]))
            else:
                current_row_count = self.grid.GetNumberRows()
                self.grid.AppendRows(1)
                # get the len of the grid from the grid rows

                # get the new row index of the grid newly appended


                # self.grid.SetRowLabelValue(current_row_count, target.name.value)
                self.grid.SetCellValue(current_row_count, 1, str(target.coordinates.value[0]))
                self.grid.SetCellValue(current_row_count, 2, str(target.coordinates.value[1]))
                self.grid.SetCellValue(current_row_count, 4, str(target.index.value))

                if target.type.value == "Fiducial":
                    if target.coordinates.value[2]:
                        self.grid.SetCellValue(current_row_count, 0, "FM")
                        self.grid.SetCellValue(current_row_count, 3, str(target.coordinates.value[2]))
                    else:
                        self.grid.SetCellValue(current_row_count, 0, "FIB")
                        self.grid.SetCellValue(current_row_count, 3, "")
                elif target.type.value == "RegionOfInterest":
                    self.grid.SetCellValue(current_row_count, 0, "POI")


        # Ensure the rows are in order of the Index
        # self.reorder_table()


        # Ensure only one row can be selected at a time
        # self.grid.SetSelectionMode(wx.grid.wxGridSelectRows)

        # # Clear any existing rows
        # self.table.DeleteAllItems()
        #
        # # Add initial dummy data (replace this with actual data if available)
        # self.add_table_data(0, 10.0, 20.0, 30.0)
        # self.add_table_data(1, 15.0, 25.0, None)

    def reorder_table(self):
        """
        Sorts the rows by 'Index' column. If an index exists, replace the row.
        """
        # when index is changed, reorder the table, such that index is in increasing order, rows with same indices
        # have poi (target type Region of Intered) , fm fiducial (tartget type fidcuial and FM in name) and then fib fiducial4
        # get the rows from column label index and reorder
        # get the rows from column label index and reorder
        rows = self.grid.GetNumberRows()
        if rows == 0:
            return

        # Get the column index for the 'Index' column
        col_ind = 4  #self.grid.GetColIndex("Index")
        col_type = 0    #self.grid.GetColIndex("type")
        # Get the data from the grid
        data = []
        for row in range(self.grid.GetNumberRows()):
            row_data = [self.grid.GetCellValue(row, col) for col in range(self.grid.GetNumberCols())]
            data.append(row_data)

        # Sort the data by the Index first, and then by Type in case of a tie
        # Index column: 1, Type column: 2
        data.sort(key=lambda x: (x[col_ind], x[col_type]))

        # Repopulate the grid with sorted data
        for row, row_data in enumerate(data):
            for col, value in enumerate(row_data):
                self.grid.SetCellValue(row, col, str(value))

    # TODO not used
    # Todo add type in the list?
    # def save_table_to_csv(self):
    #     """
    #     Save the data from both FIB and FM tables to a CSV file.
    #     """
    #     with open('correlation_points.csv', 'w', newline='') as csvfile:
    #         writer = csv.writer(csvfile)
    #         # Write the header
    #         writer.writerow(["Index", "X", "Y", "Z"])
    #
    #         # Write FIB points
    #         for row in range(self.table_fib.GetItemCount()):
    #             index = self.table_fib.GetItemText(row, 0)
    #             x = self.table_fib.GetItemText(row, 1)
    #             y = self.table_fib.GetItemText(row, 2)
    #             z = self.table_fib.GetItemText(row, 3)
    #             writer.writerow([index, x, y, z])
    #
    #         # Write FM points
    #         for row in range(self.table_fm.GetItemCount()):
    #             index = self.table_fm.GetItemText(row, 0)
    #             x = self.table_fm.GetItemText(row, 1)
    #             y = self.table_fm.GetItemText(row, 2)
    #             z = self.table_fm.GetItemText(row, 3)
    #             writer.writerow([index, x, y, z])
