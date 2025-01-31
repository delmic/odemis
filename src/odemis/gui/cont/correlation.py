
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
import itertools
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

from odemis.acq.feature import save_features, CorrelationTarget
from odemis.gui.model import TOOL_REGION_OF_INTEREST, TOOL_FIDUCIAL, TOOL_SURFACE_FIDUCIAL

import odemis.acq.stream as acqstream
import odemis.gui.model as guimod
from odemis import model
from odemis.acq.stream import RGBStream, StaticFluoStream, StaticSEMStream, StaticStream
from odemis.gui.cont.tabs.localization_tab import LocalizationTab
from odemis.gui.util import call_in_wx_main
from odemis.model import ListVA


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

GRID_PRECISION = 2

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
        # self.refractive_index_btn = panel.btn_refractive_index
        # self.refractive_index_btn.Bind(wx.EVT_BUTTON, self.on_refractive_index)

        self.delete_btn = panel.btn_delete_row
        self.delete_btn.Bind(wx.EVT_BUTTON, self.on_delete_row)

        # Bind events for stream selector and Z-targeting
        # self.stream_selector.Bind(wx.EVT_COMBOBOX, self.on_stream_change)
        self.z_targeting_btn.Bind(wx.EVT_BUTTON, self.on_z_targeting)

        # Bind the event for cell selection
        self.grid.Bind(wx.grid.EVT_GRID_SELECT_CELL, self.on_cell_selected)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGING, self.on_cell_changing)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGED, self.on_row_selected)



        self.grid.CreateGrid(0, 5)  # TODO make variables
        # Hide the default row labels (serial numbers)
        self.grid.SetRowLabelSize(0)
        # Set column labels for correlation points
        # Set the data type and if the column can be edited
        self.grid.SetColLabelValue(GridColumns.Type.value, GridColumns.Type.name)
        # attr = wx.grid.GridCellAttr()
        # attr.SetReadOnly(True)
        self.grid.SetColLabelValue(GridColumns.X.value, GridColumns.X.name)
        self.grid.SetColLabelValue(GridColumns.Y.value, GridColumns.Y.name)
        self.grid.SetColLabelValue(GridColumns.Z.value, GridColumns.Z.name)
        self.grid.SetColLabelValue(GridColumns.Index.value, GridColumns.Index.name)

        # Set column 1 (Index) as an integer column
        # int_renderer = wx.grid.GridCellNumberEditor(min=1)
        # int_attr = wx.grid.GridCellAttr()
        # int_attr.SetEditor(int_renderer)
        # self.grid.SetColAttr(4, int_attr)
        #
        # # Set columns 2, 3, and 4 (X, Y, Z Coordinates) as float columns with 2 decimal places
        # float_renderer = wx.grid.GridCellFloatEditor(precision=3)
        # float_attr = wx.grid.GridCellAttr()
        # float_attr.SetEditor(float_renderer)
        #
        # self.grid.SetColAttr(1, float_attr)
        # self.grid.SetColAttr(2, float_attr)
        # self.grid.SetColAttr(3, float_attr)
        # Enable cell editing
        # Bind the key down event to handle Enter key suppression
        self.grid.Bind(wx.EVT_KEY_DOWN, self.on_key_down)

        self.grid.EnableEditing(True)

        # TODO make sure before initializing this class, feature ana feature status is fixed ? (Controller)
        if DEBUG:
            self.correlation_target = CorrelationTarget()
        else:
            self.correlation_target = None
        # self._tab_data_model.main.currentFeature.correlation_targets[self._tab_data_model.main.currentFeature.status.value] = CorrelationTarget()
        # self.correlation_target = self._tab_data_model.main.currentFeature.correlation_targets[self._tab_data_model.main.currentFeature.status.value]

        self._tab_data_model.main.targets.subscribe(self._on_target_changes, init=True)
        self.current_target_coordinate_subscription = False
        self._tab_data_model.main.currentTarget.subscribe(self._on_current_target_changes, init=True)
        self._tab_data_model.main.currentFeature.subscribe(self.init_ct, init=True)
        self._tab_data_model.fib_surface_point.subscribe(self._on_current_fib_surface, init=True)
        # self._tab_data_model.fm_poi.subscribe(self._on_current_fm_poi, init=True)


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
        self.stream_groups = None
        self.previous_group = None


    @call_in_wx_main
    def _on_fm_streams_change(self, stream_projections: ListVA) -> None:
        if not self.stream_groups or len(stream_projections) <= 0:
            return
        # go for the latest stream projection
        # get the stream name
        # find the index in stream list and find the stream group
        # update the visibility of the stream group
        # Assuming stream_projections contains the updated streams
        # for projection in stream_projections:
        stream_obj = stream_projections[-1].stream # Get the stream name

        # Find the index of the stream in the stream list
        stream_index = next(
            (i for i, stream in enumerate(self._tab_data_model.main.currentFeature.value.streams.value)
             if stream == stream_obj), None)

        # If the stream exists in the list
        if stream_index is not None:
            # Get the group this stream belongs to
            group_key = next(
                (key for key, indices in self.stream_groups.items() if stream_index in indices), None)

            # Update visibility for the group
            if self.previous_group == group_key:
                return

            self.previous_group = group_key
            if group_key:
                # should not add streams
                # self._update_stream_group_visibility(group_key)
                streams_list = self._tab_data_model.main.currentFeature.value.streams.value
                for key, indices in self.stream_groups.items():
                    for index in indices:
                        stream = streams_list[index]
                        # find stream controller for the stream
                        ssc = next(
                            (sc for sc in self._tab.streambar_controller.stream_controllers
                             if sc.stream == stream), None)

                        if isinstance(stream, StaticFluoStream) and not group_key:
                            group_key = key

                        if key == group_key:
                            ssc.stream_panel.set_visible(True)
                            ssc.stream_panel.collapse(False)
                        elif isinstance(stream, StaticFluoStream):
                            if hasattr(self._tab_data_model.views.value[0], "removeStream"):
                                self._tab_data_model.views.value[0].removeStream(stream)
                            ssc.stream_panel.set_visible(False)
                            ssc.stream_panel.collapse(True)


    def _update_stream_group_visibility(self, group_key: tuple) -> None:
        streams_list = self._tab_data_model.main.currentFeature.value.streams.value
        for insertion_index, (key, indices) in enumerate(self.stream_groups.items()):
            for index in indices:
                stream = streams_list[index]
                stream.name.value = f"{stream.name.value}-Group-{insertion_index}"
                ssc = self._tab.streambar_controller.addStream(stream,  play=False)
                ssc.stream_panel.show_remove_btn(True)

                if not  isinstance(stream, StaticFluoStream):
                    ssc.stream_panel.set_visible(True)
                    ssc.stream_panel.collapse(False)

        self._tab_data_model.views.value[0].stream_tree.flat.subscribe(self._on_fm_streams_change, init=True)

    def add_streams(self) -> None:
        """add streams to the correlation tab
        :param streams: (list[StaticStream]) the streams to add"""

        streams_list = self._tab_data_model.main.currentFeature.value.streams.value
        stream_groups = {}

        for stream_index, stream in enumerate(streams_list):

            current_shape = stream.raw[0].shape
            centre_pos = stream.raw[0].metadata[model.MD_POS]
            if isinstance(stream, StaticFluoStream):
                check_zindex = getattr(stream, "zIndex", None)  # Use getattr to handle if zIndex is not present
                stream_name = stream.name.value

                if check_zindex:
                    # Create the key based on current shape, centre position, and zIndex
                    key = (current_shape, centre_pos)

                    # Check if a set for this key already exists
                    if key not in stream_groups:
                        # Create a new set if the combination of shape, centre position, and zIndex is different
                        stream_groups[key] = set()

                    # Check if there's already a stream with the same name in the set
                    # If a stream with the same name exists, replace the previous index to keep the latest stream index
                    indices_to_remove = set()
                    for idx in stream_groups[key]:
                        if streams_list[idx].name.value == stream_name:
                            indices_to_remove.add(idx)

                    # Remove old indices with the same name
                    stream_groups[key] -= indices_to_remove

                    # Add the new stream index to the set
                    stream_groups[key].add(stream_index)

            elif isinstance(stream, StaticSEMStream):
                # TODO check fib stream index
                key = (current_shape, centre_pos)
                if key not in stream_groups:
                    stream_groups[key] = set()
                stream_groups[key].add(stream_index)



        # add streams in stream controller
        # TODO add FIB stream logic
        first_valid_key = None
        self.stream_groups = stream_groups
        self._update_stream_group_visibility(first_valid_key)

        # if stream_groups:
        #     # Get the first key from the dictionary (unordered since dictionaries are unordered before Python 3.7)
        #     first_key = next(iter(stream_groups))
        #
        #     # Get the set of indices associated with the first key
        #     first_indices = stream_groups[first_key]
        #
        #     # "Activate" or process these indices (example: print them or do something with the streams)
        #     for index in first_indices:
        #         stream = streams_list[index]  # Access the stream using the index
        #         ssc = self._tab.streambar_controller.addStream(stream, add_to_view=True, play=False)
        #         ssc.stream_panel.set_visible(True)
        #         ssc.stream_panel.collapse(False)
        #         print(f"Processing stream at index {index}, stream name: {stream.name.value}")
        # #         # Add your activation logic here (e.g., further processing, applying a function)
        # else:
        #     print("No stream groups available.")

        # todo sem/fib STREAM?

    # initialize correlation target class in the current feature, once the minimum requirements
    # make sure it is not initialized multiple times
    # add correlation target as an attribute in cryo feature, no need of VA
    # make an update function, which updates the specific parts of the class when changed
    def on_key_down(self, event):
        """Handle key down events, especially to suppress Enter key default behavior."""
        if event.GetKeyCode() == wx.WXK_RETURN:
            # Commit the value in the currently edited cell
            if self.grid.IsCellEditControlEnabled():
                self.grid.DisableCellEditControl()
                # Suppress the Enter key from moving to the next row
            return
        else:
            # For other keys, allow the default behavior
            event.Skip()

    def init_ct(self, val):
        if not DEBUG:
            if not self._tab_data_model.main.currentFeature.value:
                return False
            elif not self._tab_data_model.main.currentFeature.value.correlation_targets:
                self._tab_data_model.main.currentFeature.value.correlation_targets = {}
                self._tab_data_model.main.currentFeature.value.correlation_targets[self._tab_data_model.main.currentFeature.value.status.value] = CorrelationTarget()
                self.correlation_target = self._tab_data_model.main.currentFeature.value.correlation_targets[self._tab_data_model.main.currentFeature.value.status.value]
            elif self.correlation_target is None:
                # to be outside correlation button maybe?
                self.add_streams()
                correlation_target = self._tab_data_model.main.currentFeature.value.correlation_targets[self._tab_data_model.main.currentFeature.value.status.value]
                targets = []
                projected_points = []
                if correlation_target.fm_fiducials:
                    targets.append(correlation_target.fm_fiducials)
                if correlation_target.fm_pois:
                    targets.append(correlation_target.fm_pois)
                if correlation_target.fib_fiducials:
                    targets.append(correlation_target.fib_fiducials)
                if correlation_target.fib_projected_fiducials:
                    projected_points.append(correlation_target.fib_projected_fiducials)
                if correlation_target.fib_projected_pois:
                    projected_points.append(correlation_target.fib_projected_pois)

                # TOdo not used as output gets reset
                # projected_points = list(
                #     itertools.chain.from_iterable([x] if not isinstance(x, list) else x for x in projected_points))
                # self._tab_data_model.projected_points = projected_points
                # flatten the list of lists
                targets = list(
                    itertools.chain.from_iterable([x] if not isinstance(x, list) else x for x in targets))
                self._tab_data_model.main.targets.value = targets
                if correlation_target.fib_surface_fiducial:
                    self._tab_data_model.fib_surface_point.value = correlation_target.fib_surface_fiducial
                # if correlation_target.fm_pois:
                #     self._tab_data_model.fm_poi.value = correlation_target.fm_pois
                self.correlation_target = correlation_target

    def update_feature_correlation_target(self, surface_fiducial = False):#, fm_poi = False):

        if not self.correlation_target:
            return

        if surface_fiducial:
            # todo check if it has a value
            fib_surface_fiducial = self._tab_data_model.fib_surface_point.value
            self.correlation_target.fib_surface_fiducial = fib_surface_fiducial
        # elif fm_poi:
        #     self.correlation_target.fm_pois = self._tab_data_model.fm_poi.value
        else:
        # if True:
            fib_fiducials = []
            fm_fiducials = []
            # fm_pois = []
            # fib_surface_fiducial = None
            for target in self._tab_data_model.main.targets.value:
                if "FIB" in target.name.value:
                    fib_fiducials.append(target)
                elif "FM" in target.name.value:
                    fm_fiducials.append(target)
                elif "POI" in target.name.value:
                    self.correlation_target.fm_pois = target
            if fib_fiducials:
                fib_fiducials.sort(key=lambda x: x.index.value)
                self.correlation_target.fib_fiducials = fib_fiducials
            if fm_fiducials:
                fm_fiducials.sort(key=lambda x: x.index.value)
                self.correlation_target.fm_fiducials = fm_fiducials
            # if fm_pois:
            #     fm_pois.sort(key=lambda x: x.index.value)
            #     self.correlation_target.fm_pois = fm_pois


        # self.correlation_target.reset_attributes()
        # self._tab_data_model.main.currentFeature.value.correlation_targets[
        #     self._tab_data_model.main.currentFeature.value.status.value] = CorrelationTarget()
        # self._tab_data_model.main.currentFeature.value.correlation_targets[
        #     self._tab_data_model.main.currentFeature.value.status.value] = self.correlation_target
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.main.features.value)


    def check_correlation_conditions(self):
        # Todo conditions change when initializing the correlation target

        # should npt be here, duplicate
        if not DEBUG:
            if not self._tab_data_model.main.currentFeature.value:
                return False
            elif not self._tab_data_model.main.currentFeature.value.correlation_targets:
                self._tab_data_model.main.currentFeature.value.correlation_targets[self._tab_data_model.main.currentFeature.value.status.value] = CorrelationTarget()
                self.correlation_target = self._tab_data_model.main.currentFeature.value.correlation_targets[self._tab_data_model.main.currentFeature.value.status.value]
                # draw
                # for vp in self._viewports:
                #     # if vp.view.name.value == "SEM Overview":
                #     vp.canvas.update_drawing()

        if self.correlation_target:
            if not DEBUG:
                if (len(self.correlation_target.fib_fiducials) >= 4 and len(
                        self.correlation_target.fm_fiducials) >= 4 and self.correlation_target.fm_pois  and len(
                        self._tab_data_model.views.value[0].stream_tree) > 0):
                    return True
                else:
                    self.correlation_target.reset_attributes()
                    self._tab_data_model.projected_points = []
                    self.correlation_txt.SetLabel("Correlation RMS Deviation :")
                    for vp in self._viewports:
                        if vp.view.name.value == "SEM Overview":
                            vp.canvas.update_drawing()
                    return False
            else:
                if len(self.correlation_target.fib_fiducials) >= 1 and self.correlation_target.fm_fiducials and self.correlation_target.fib_surface_fiducial:
                    return True
                else:
                    self.correlation_target.reset_attributes()
                    self._tab_data_model.projected_points = []
                    self.correlation_txt.SetLabel("Correlation RMS Deviation :")
                    for vp in self._viewports:
                        if vp.view.name.value == "SEM Overview":
                            vp.canvas.update_drawing()
                    return False
        else:
            return False

    # def on_refractive_index(self, evt):
    #     # only one target that keeps on changing, at the end if do correlation possible,
    #     # rerun the correlation calculation (call decorator)
    #     # how to do calculation if things rapidly change than the calculation speed
    #
    #     if self._tab_data_model.focussedView.value.name.value == "SEM Overview":
    #         # pass
    #         self._tab_data_model.main.selected_target_type.value = "SurfaceFiducial"
    #         self._tab_data_model.tool.value = TOOL_SURFACE_FIDUCIAL
    #         self.update_feature_correlation_target(surface_fiducial=True)
    #
    #     if self.check_correlation_conditions():
    #         self.latest_change = True
    #         self.queue_latest_change()

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

        if self._tab_data_model.projected_points:
            self.correlation_target.fib_projected_fiducials =  self._tab_data_model.projected_points
            target_copy = copy.deepcopy(target_copy)
            target_copy.type.value = "ProjectedPOI"
            self._tab_data_model.projected_points.append(target_copy)
            self.correlation_target.fib_projected_pois = [self._tab_data_model.projected_points[-1]]

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
            if key == wx.WXK_UP:
                if self._tab_data_model.focussedView.value.name.value == "FLM Overview" or self._tab_data_model.focussedView.value.name.value == "SEM Overview":
                    self._tab_data_model.tool.value = TOOL_FIDUCIAL
            elif key == wx.WXK_DOWN:
                if self._tab_data_model.focussedView.value.name.value == "FLM Overview":
                    self._tab_data_model.tool.value = TOOL_REGION_OF_INTEREST

    def on_delete_row(self, event):
        """
        Delete the currently selected row.
        """
        if not self._tab_data_model.main.currentTarget.value:
            self.grid.SelectRow(-1)
            return

        selected_rows = self.grid.GetSelectedRows()
        # row_evt = event.GetRow()
        if selected_rows:
            for row in selected_rows:
                self.grid.DeleteRows(pos=row, numRows=1, updateLabels=True)

                for target in self._tab_data_model.main.targets.value:
                    if target.name.value == self._tab_data_model.main.currentTarget.value.name.value:
                        self._tab_data_model.main.targets.value.remove(target)
                        self._tab_data_model.main.currentTarget.value = None
                        # unselect grid row
                        self.grid.SelectRow(-1)
                        # if "POI" in target.name.value:
                        #     self._tab_data_model.fm_poi = model.VigilantAttribute(None) # noone listening
                        break
        self.update_feature_correlation_target()
        if self.check_correlation_conditions():
            self.latest_change = True
            self.queue_latest_change()

    def on_cell_selected(self, event):
        row = event.GetRow()
        for target in self._tab_data_model.main.targets.value:
            if self.selected_target_in_grid(target, row):
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
        current_row_count = event.GetRow()

        if col_name == GridColumns.Type.name:
            wx.MessageBox("Type cannot be changed", "Invalid Input", wx.OK | wx.ICON_ERROR)
            event.Veto()
            return
        elif col_name == GridColumns.Index.name:
            try:
                current_name = self._tab_data_model.main.currentTarget.value.name.value
                current_index = self._tab_data_model.main.currentTarget.value.index.value
                # index value for the target should be less than the maximum number of indices for that type
                indices = []
                target_swap = None
                for target in self._tab_data_model.main.targets.value:
                    if target.name.value[:-1] == current_name[:-1]:
                        indices.append(target.index.value)
                        if target.index.value == int(new_value):
                            target_swap = target

                index_max = max(indices)
                assert 1 <= int(new_value) <= index_max
                self._tab_data_model.main.currentTarget.value.index.value = int(new_value)
                self._tab_data_model.main.currentTarget.value.name.value = current_name[:-1] + str(new_value)
                # todo grid set value shouldn't be used, use VA connector and disconnector
                self.grid.SetCellValue(current_row_count, GridColumns.Type.value, self._tab_data_model.main.currentTarget.value.name.value)

                if target_swap:
                    for row in range(self.grid.GetNumberRows()):
                        if self.grid.GetCellValue(row, GridColumns.X.value) == f"{target_swap.coordinates.value[0]:.{GRID_PRECISION}f}":
                            self._tab_data_model.main.targets.value.remove(target_swap)
                            break
                    target_swap.index.value = current_index
                    target_swap.name.value = current_name[:-1] + str(target_swap.index.value)
                    self._tab_data_model.main.targets.value.append(target_swap)
                    self._tab_data_model.main.currentTarget.value = None

                for vp in self._viewports:
                    vp.canvas.update_drawing()
                # should happen before the event is triggered
                # check difeerent feature different points
                # save fib point
                # fiducials icon change
                # features still there when new project is used
                # reset output
                # load and save json in the same way
                # pop up
                # doc string
                # todos
                # enable z targetting
                # change the keys
                # new tool icon
                # mto pixel proper

                # float, 2 values more
                # none value not there
                # pencil in fib
                # keep more than one poi (big fov)
                # enter issue resolved
                # xyz change
                # poi up
                # index when changes cannot be more than the number of max indices in the list

            except (ValueError, AssertionError):
                wx.MessageBox(f"Index must be an int in the range (1, {index_max + 1})!", "Invalid Input", wx.OK | wx.ICON_ERROR)
                event.Veto()  # Prevent the change
                return
        elif col_name in [GridColumns.X.name, GridColumns.Y.name, GridColumns.Z.name]:
            try:
                if col_name == GridColumns.X.name:
                    self._tab_data_model.main.currentTarget.value.coordinates.value[0] = float(new_value)
                elif col_name == GridColumns.Y.name:
                    self._tab_data_model.main.currentTarget.value.coordinates.value[1] = float(new_value)
                elif col_name == GridColumns.Z.name and ("FIB" not in self._tab_data_model.main.currentTarget.value.name.value):
                    # keep Z value empty for FIB targets as they don't have Z coordinates
                    self._tab_data_model.main.currentTarget.value.coordinates.value[2] = float(new_value)
            except ValueError:
                wx.MessageBox("X, Y, Z values must be a float!", "Invalid Input", wx.OK | wx.ICON_ERROR)
                event.Veto()  # Prevent the change
                return

        event.Skip()  # Allow the change if validation passes

    def on_row_selected(self, event):

        col = event.GetCol()
        col_name = self.grid.GetColLabelValue(col)

        if col_name == GridColumns.Index.name:  # Index column was changed
            self.reorder_table()

    @call_in_wx_main
    def _on_current_target_changes(self, target):
        # according to target name, highlight the row
        for row in range(self.grid.GetNumberRows()):
            if self.selected_target_in_grid(target, row):
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

    # def _on_current_fm_poi(self, fm_poi):
    #     if self._tab_data_model.fm_poi.value:
    #         self._tab_data_model.fm_poi.value.coordinates.subscribe(self._on_current_coordinates_fm_poi, init=True)

    def _on_current_coordinates_fib_surface(self, coordinates):
        self.update_feature_correlation_target(surface_fiducial=True)

        if self.check_correlation_conditions():
            self.latest_change = True
            self.queue_latest_change()

    # def _on_current_coordinates_fm_poi(self, coordinates):
    #     self.update_feature_correlation_target(fm_poi=True)
    #
    #     if self.check_correlation_conditions():
    #         self.latest_change = True
    #         self.queue_latest_change()

    @call_in_wx_main
    def _on_current_coordinates_changes(self, coordinates):
        target = self._tab_data_model.main.currentTarget.value
        self.current_target_coordinate_subscription = False
        temp_check = False
        for row in range(self.grid.GetNumberRows()):
            # TODO Check grid cells are changing
            # TODO add changes due to Z
            if self.selected_target_in_grid(target, row):
                # Get cell value
                if self.grid.GetCellValue(row, GridColumns.X.value) != f"{target.coordinates.value[0]:.{GRID_PRECISION}f}" or self.grid.GetCellValue(row, GridColumns.Y.value) != f"{target.coordinates.value[1]:.{GRID_PRECISION}f}" or self.grid.GetCellValue(row, GridColumns.Z.value) != str(target.coordinates.value[2]):
                    temp_check = True
                self.grid.SetCellValue(row, GridColumns.X.value, f"{target.coordinates.value[0]:.{GRID_PRECISION}f}")
                self.grid.SetCellValue(row, GridColumns.Y.value, f"{target.coordinates.value[1]:.{GRID_PRECISION}f}")
                if "FIB" not in target.name.value:
                    self.grid.SetCellValue(row, GridColumns.Z.value, str(target.coordinates.value[2]))

                self.update_feature_correlation_target()

            if self.check_correlation_conditions() and temp_check:
                self.latest_change = True
                self.queue_latest_change()

    @call_in_wx_main
    def _on_target_changes(self, targets):
        """
        Enable or disable buttons based on stream selection.
        When FM is selected, the Z-targeting button is enabled.
        When FIB is selected, the Z-targeting button is disabled.
        """
        # Clear the grid before populating it with new data
        self.grid.ClearGrid()
        # delete the empty rows
        if self.grid.GetNumberRows() > 0:
            self.grid.DeleteRows(0, self.grid.GetNumberRows())
        for target in targets:

            current_row_count = self.grid.GetNumberRows()
            self.grid.SelectRow(current_row_count)
            self.grid.AppendRows(1)
            self.grid.SetCellValue(current_row_count, GridColumns.X.value, f"{target.coordinates.value[0]:.{GRID_PRECISION}f}")
            self.grid.SetCellValue(current_row_count, GridColumns.Y.value, f"{target.coordinates.value[1]:.{GRID_PRECISION}f}")
            self.grid.SetCellValue(current_row_count, GridColumns.Index.value, str(target.index.value))
            self.grid.SetCellValue(current_row_count, GridColumns.Type.value, target.name.value)

            if "FIB" in target.name.value:
                 self.grid.SetCellValue(current_row_count, GridColumns.Z.value, "")
            else:
                self.grid.SetCellValue(current_row_count, GridColumns.Z.value, str(target.coordinates.value[2]))

        self.reorder_table()
        self._panel.Layout()
        self.update_feature_correlation_target()
        if self.check_correlation_conditions():
            self.latest_change = True
            self.queue_latest_change()


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

    def reorder_table(self):
        """
        Sorts the rows by 'Index' column. If an index exists, replace the row.
        """
        rows = self.grid.GetNumberRows()
        if rows == 0:
            return

        # Get the data from the grid
        data = []
        for row in range(self.grid.GetNumberRows()):
            row_data = [self.grid.GetCellValue(row, col) for col in range(self.grid.GetNumberCols())]
            data.append(row_data)

        # Sort the data by the Index first, and then by Type in case of a tie
        data.sort(key=lambda x: (x[GridColumns.Index.value], -ord(x[GridColumns.Type.value][0])))

        # Repopulate the grid with sorted data
        for row, row_data in enumerate(data):
            for col, value in enumerate(row_data):
                self.grid.SetCellValue(row, col, str(value))
