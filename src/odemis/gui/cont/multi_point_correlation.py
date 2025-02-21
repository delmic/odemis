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
import threading
import time
from enum import Enum

import wx

# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

from odemis.acq.align.tdct import get_optimized_z_gauss
from odemis.acq.feature import save_features, CorrelationTarget
from odemis.gui import conf
from odemis.gui.model import TOOL_REGION_OF_INTEREST, TOOL_FIDUCIAL, TOOL_SURFACE_FIDUCIAL

from odemis import model
from odemis.acq.stream import StaticFluoStream, StaticSEMStream, StaticStream, StaticFIBStream

from odemis.gui.util import call_in_wx_main
from odemis.model import ListVA


# create an enum with column labels and position
class GridColumns(Enum):
    Type = 0  # Column for "type"
    X = 1  # Column for "x"
    Y = 2  # Column for "y"
    Z = 3  # Column for "z"
    Index = 4  # Column for "index"


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

        # Access the Z-targeting button
        self.z_targeting_btn = panel.btn_z_targeting
        self.z_targeting_btn.Bind(wx.EVT_BUTTON, self.on_z_targeting)
        self.z_targeting_btn.Enable(False)

        self.delete_btn = panel.btn_delete_row
        self.delete_btn.Bind(wx.EVT_BUTTON, self.on_delete_row)

        # Bind the event for cell selection
        self.grid.Bind(wx.grid.EVT_GRID_SELECT_CELL, self.on_cell_selected)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGING, self.on_cell_changing)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGED, self.on_row_selected)

        self.grid.CreateGrid(0, 5)  # TODO make variables
        self.grid.SetRowLabelSize(0)
        # Set column labels for correlation points
        # Set the data type and if the column can be edited
        self.grid.SetColLabelValue(GridColumns.Type.value, GridColumns.Type.name)
        self.grid.SetColLabelValue(GridColumns.X.value, GridColumns.X.name)
        self.grid.SetColLabelValue(GridColumns.Y.value, GridColumns.Y.name)
        self.grid.SetColLabelValue(GridColumns.Z.value, GridColumns.Z.name)
        self.grid.SetColLabelValue(GridColumns.Index.value, GridColumns.Index.name)
        self.grid.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        self.grid.EnableEditing(True)

        # TODO make sure before initializing this class, feature ana feature status is fixed ? (Controller)
        # if DEBUG:
        #     self.correlation_target = CorrelationTarget()
        # else:
        self.correlation_target = None

        # self._tab_data_model.main.currentFeature.correlation_targets[self._tab_data_model.main.currentFeature.status.value] = CorrelationTarget()
        # self.correlation_target = self._tab_data_model.main.currentFeature.correlation_targets[self._tab_data_model.main.currentFeature.status.value]
        self.correlation_txt = panel.txt_correlation_rms
        self.correlation_txt.Show(True)
        self.latest_change = None  # Holds the latest change
        self.lock = threading.Lock()  # Lock to synchronize access to changes
        self.is_processing = False  # To track if the function is currently processing
        self.process_thread = None  # The thread handling the change
        self.stream_groups = None
        self.previous_group = None

        # reset targets and current target and populate it based on the current feature
        self._tab_data_model.main.targets = model.ListVA()
        self._tab_data_model.main.currentTarget = model.VigilantAttribute(None)
        self._tab_data_model.fib_surface_point = model.VigilantAttribute(None)
        self._tab_data_model.main.targets.subscribe(self._on_target_changes, init=True)
        self.current_target_coordinate_subscription = False
        self._tab_data_model.main.currentTarget.subscribe(self._on_current_target_changes, init=True)
        # self._tab_data_model.fib_surface_point.value.coordinates.subscribe(self._on_current_coordinates_fib_surface,
        #                                                                    init=True)
        self._tab_data_model.fib_surface_point.subscribe(self._on_current_fib_surface, init=True)
        # self.add_streams()
        if self._tab_data_model.main.currentFeature.value.correlation_targets:
            # load the values
            # to be outside correlation button maybe?
            self.correlation_target = self._tab_data_model.main.currentFeature.value.correlation_targets[
                self._tab_data_model.main.currentFeature.value.status.value]
            # load FM and FIB stream in the panel
            # TODO save the FM and FIB streams in the json file because fiducials are dependent on the dataarray
            # self.correlation_target = correlation_target
            if self.correlation_target.fm_streams:
                # Will load the streams from the given streams
                self.add_streams(self.correlation_target.fm_streams)
            else:
                # TODO this should be removed, first streams then fiducials should be loaded
                # Will load the relevant streams from the current feature
                # which one the fiducials should obey?
                self.add_streams()

            if self.correlation_target.fib_stream:
                self.add_streams([self.correlation_target.fib_stream])

            targets = []
            projected_points = []
            # TODO Should the fiducials be converted in m , fm stream should be selected by then (do it at the end)
            if self.correlation_target.fm_fiducials:
                targets.append(self.correlation_target.fm_fiducials)
            if self.correlation_target.fm_pois:
                targets.append(self.correlation_target.fm_pois)
            if self.correlation_target.fib_fiducials and self.correlation_target.fib_stream:
                targets.append(self.correlation_target.fib_fiducials)
            if self.correlation_target.fib_projected_fiducials:
                projected_points.append(self.correlation_target.fib_projected_fiducials)
            if self.correlation_target.fib_projected_pois:
                projected_points.append(self.correlation_target.fib_projected_pois)



            # TOdo not used as output gets reset
            # projected_points = list(
            #     itertools.chain.from_iterable([x] if not isinstance(x, list) else x for x in projected_points))
            # self._tab_data_model.projected_points = projected_points
            # flatten the list of lists
            targets = list(
                itertools.chain.from_iterable([x] if not isinstance(x, list) else x for x in targets))

            # check the update TODO
            # TODO same stream (?) limit to 1, is there a stream
            # for target in targets:
            #     if "FIB" in target.name.value:
            #         p_pos = self.correlation_target.fib_stream.getPhysicalCoordinates(
            #         (target.coordinates.value[0], target.coordinates.value[1]))
            #     else:
            #         p_pos = self.correlation_target.fm_streams[0].getPhysicalCoordinates(
            #             (target.coordinates.value[0], target.coordinates.value[1]))
            #     target.coordinates.value[0:2] = p_pos

            self._tab_data_model.main.targets.value = targets
            if self.correlation_target.fib_surface_fiducial:
                self._tab_data_model.fib_surface_point.value = self.correlation_target.fib_surface_fiducial

        else:
            # initialize the correlation target
            self._tab_data_model.main.currentFeature.value.correlation_targets = {}
            self._tab_data_model.main.currentFeature.value.correlation_targets[
                self._tab_data_model.main.currentFeature.value.status.value] = CorrelationTarget()
            self.correlation_target = self._tab_data_model.main.currentFeature.value.correlation_targets[
                self._tab_data_model.main.currentFeature.value.status.value]
            self.add_streams()

        # self.add_streams()
        panel.fp_correlation_panel.Show(True)
        for vp in self._viewports:
            vp.canvas.Bind(wx.EVT_CHAR, self.on_char)

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
        stream_obj = stream_projections[-1].stream  # Get the stream name

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
                    # self.correlation_target.fm_streams = []
                    for index in indices:
                        stream = streams_list[index]
                        # find stream controller for the stream
                        ssc = next(
                            (sc for sc in self._tab.streambar_controller.stream_controllers
                             if sc.stream == stream), None)

                        if isinstance(stream, StaticFluoStream) and not group_key:
                            group_key = key

                        if key == group_key:
                            # # TODO it should not be here,
                            # self.correlation_target.fm_streams.append(stream)
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
            self.correlation_target.fm_streams = []
            for index in indices:
                stream = streams_list[index]
                stream.name.value = f"{stream.name.value}-Group-{insertion_index}"
                ssc = self._tab.streambar_controller.addStream(stream, play=False)
                ssc.stream_panel.show_remove_btn(True)
                # TODO it should not be here,
                self.correlation_target.fm_streams.append(stream)

                if not isinstance(stream, StaticFluoStream):
                    ssc.stream_panel.set_visible(True)
                    ssc.stream_panel.collapse(False)

        self._tab_data_model.views.value[0].stream_tree.flat.subscribe(self._on_fm_streams_change, init=True)

    def add_streams(self, streams: list = None) -> None:
        """add streams to the tdct correlation dialog box
        :param streams: (list[StaticStream]) new streams to add"""
        if not self.correlation_target:
            return

        if streams:
            streams_list = streams
            # TODO remove special treatment for FIB, the FIB stream should be available in current feature streams
            for stream_index, stream in enumerate(streams_list):
                # current_shape = stream.raw[0].shape
                # centre_pos = stream.raw[0].metadata[model.MD_POS]
                if isinstance(stream, StaticStream):
                    # TODO check fib stream index
                    # key = (current_shape, centre_pos)
                    # if key not in self.stream_groups:
                    #     self.stream_groups[key] = set()
                    # self.stream_groups[key].add(stream_index)
                    ssc = self._tab.streambar_controller.addStream(stream, play=False, add_to_view=True)
                    ssc.stream_panel.show_remove_btn(True)
                    if isinstance(stream, StaticFIBStream) or isinstance(stream, StaticSEMStream):
                        if not self.correlation_target.fib_stream:
                            self.correlation_target.fib_stream = stream
                            if all("FIB" not in target.name.value for target in self._tab_data_model.main.targets.value):
                                if self.correlation_target.fib_fiducials:
                                # for target in targets:
                                    # if "FIB" in target.name.value:
                                    #     p_pos = self.correlation_target.fib_stream.getPhysicalCoordinates(
                                    #         (target.coordinates.value[0], target.coordinates.value[1]))
                                    #     target.coordinates.value[0:2] = p_pos

                                    self._tab_data_model.main.targets.value.extend(self.correlation_target.fib_fiducials)
                            # Get the data array
                            # stream.raw[0]

                        elif stream != self.correlation_target.fib_stream:
                            self._tab.streambar_controller.removeStreamPanel(self.correlation_target.fib_stream)
                            self.correlation_target.fib_stream = stream

            return

        else:
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

            elif isinstance(stream, StaticFIBStream):
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

    def update_feature_correlation_target(self, surface_fiducial=False):  # , fm_poi = False):

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
            self.correlation_target.fm_pois = []
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

        acq_conf = conf.get_acqui_conf()
        save_features(acq_conf.pj_last_path, self._tab_data_model.main.features.value)

    def check_correlation_conditions(self):
        # Todo conditions change when initializing the correlation target
        # should npt be here, duplicate
        if not DEBUG:
            if not self._tab_data_model.main.currentFeature.value:
                return False
            elif not self._tab_data_model.main.currentFeature.value.correlation_targets:
                self._tab_data_model.main.currentFeature.value.correlation_targets[
                    self._tab_data_model.main.currentFeature.value.status.value] = CorrelationTarget()
                self.correlation_target = self._tab_data_model.main.currentFeature.value.correlation_targets[
                    self._tab_data_model.main.currentFeature.value.status.value]
                # draw
                # for vp in self._viewports:
                #     # if vp.view.name.value == "SEM Overview":
                #     vp.canvas.update_drawing()

        if self.correlation_target:
            if not DEBUG:
                if (len(self.correlation_target.fib_fiducials) >= 4 and len(
                        self.correlation_target.fm_fiducials) >= 4 and self.correlation_target.fm_pois and len(
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

    def queue_latest_change(self):
        """
        This method is called when there's an input change.
        If there's a change already being processed, it will cancel it and process the latest change.
        """
        with self.lock:  # Ensure thread-safe access to latest_change
            self.latest_change = True  # Overwrite with the latest change

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
        time.sleep(5)  # Will slow down
        self._tab_data_model.projected_points = []
        for target in self._tab_data_model.main.targets.value:
            if "FIB" in target.name.value:
                # deep copy of target such that type can be changed without changing the target
                target_copy = copy.deepcopy(target)
                target_copy.type.value = "ProjectedPoints"
                self._tab_data_model.projected_points.append(target_copy)

        if self._tab_data_model.projected_points:
            self.correlation_target.fib_projected_fiducials = self._tab_data_model.projected_points
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
        # TODO change the keys to Ctrl + Left and Alt + Left
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
                        self.z_targeting_btn.Enable(False)
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
                self.grid.SetCellValue(current_row_count, GridColumns.Type.value,
                                       self._tab_data_model.main.currentTarget.value.name.value)

                if target_swap:
                    for row in range(self.grid.GetNumberRows()):
                        if self.grid.GetCellValue(row,
                                                  GridColumns.X.value) == f"{target_swap.coordinates.value[0]:.{GRID_PRECISION}f}":
                            self._tab_data_model.main.targets.value.remove(target_swap)
                            break
                    target_swap.index.value = current_index
                    target_swap.name.value = current_name[:-1] + str(target_swap.index.value)
                    self._tab_data_model.main.targets.value.append(target_swap)
                    self._tab_data_model.main.currentTarget.value = None

                for vp in self._viewports:
                    vp.canvas.update_drawing()

            except (ValueError, AssertionError):
                wx.MessageBox(f"Index must be an int in the range (1, {index_max + 1})!", "Invalid Input",
                              wx.OK | wx.ICON_ERROR)
                event.Veto()  # Prevent the change
                return
        elif col_name in [GridColumns.X.name, GridColumns.Y.name, GridColumns.Z.name]:
            try:
                if col_name == GridColumns.X.name:
                    self._tab_data_model.main.currentTarget.value.coordinates.value[0] = float(new_value)
                elif col_name == GridColumns.Y.name:
                    self._tab_data_model.main.currentTarget.value.coordinates.value[1] = float(new_value)
                elif col_name == GridColumns.Z.name and (
                        "FIB" not in self._tab_data_model.main.currentTarget.value.name.value):
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
        if target and "FIB" not in target.name.value:
            self.z_targeting_btn.Enable(True)
        else:
            self.z_targeting_btn.Enable(False)

        for row in range(self.grid.GetNumberRows()):
            if self.selected_target_in_grid(target, row):
                self.grid.SelectRow(row)
                self.reorder_table()
                break

        if self._tab_data_model.main.currentTarget.value and not self.current_target_coordinate_subscription:
            self._tab_data_model.main.currentTarget.value.coordinates.subscribe(self._on_current_coordinates_changes,
                                                                                init=True)
            # subscribe only once
            self.current_target_coordinate_subscription = True

    def _on_current_fib_surface(self, fib_surface_fiducial):
        # todo can be done in intialization
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
        self.current_target_coordinate_subscription = False
        temp_check = False
        for row in range(self.grid.GetNumberRows()):
            # TODO Check grid cells are changing
            # TODO add changes due to Z
            if self.selected_target_in_grid(target, row):
                if "FM" or "POI" in target.name.value:
                    pixel_coords = self.correlation_target.fm_streams[0].getPixelCoordinates_alt(
                        (target.coordinates.value[0], target.coordinates.value[1]))
                    if int(self.grid.GetCellValue(row, GridColumns.Z.value)) != int(target.coordinates.value[2]):
                        temp_check = True
                    self.grid.SetCellValue(row, GridColumns.Z.value, f"{target.coordinates.value[2]:.{GRID_PRECISION}f}")
                else:
                    pixel_coords = self.correlation_target.fib_stream.getPixelCoordinates_alt(
                        (target.coordinates.value[0], target.coordinates.value[1]))
                # Get cell value
                if (self.grid.GetCellValue(row, GridColumns.X.value) != f"{pixel_coords[0]:.{GRID_PRECISION}f}" or
                        self.grid.GetCellValue(row,GridColumns.Y.value) != f"{pixel_coords[1]:.{GRID_PRECISION}f}"):
                    temp_check = True
                self.grid.SetCellValue(row, GridColumns.X.value, f"{pixel_coords[0]:.{GRID_PRECISION}f}")
                self.grid.SetCellValue(row, GridColumns.Y.value, f"{pixel_coords[1]:.{GRID_PRECISION}f}")
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
        if not self.correlation_target.fm_streams and self.previous_group:
            streams_list = self._tab_data_model.main.currentFeature.value.streams.value
            for key, indices in self.stream_groups.items():
                if key == self.previous_group:
                    for index in indices:
                        stream = streams_list[index]
                        self.correlation_target.fm_streams.append(stream)
                        # find stream controller for the stream
                        # ssc = next(
                        #     (sc for sc in self._tab.streambar_controller.stream_controllers
                        #      if sc.stream == stream), None)
                else:
                    for index in indices:
                        stream = streams_list[index]
                        # FIB static stream should not be removed
                        if isinstance(stream, StaticFluoStream):
                            self._tab.streambar_controller.removeStreamPanel(stream)

        # Clear the grid before populating it with new data
        self.grid.ClearGrid()
        # delete the empty rows
        if self.grid.GetNumberRows() > 0:
            self.grid.DeleteRows(0, self.grid.GetNumberRows())
        for target in targets:
            current_row_count = self.grid.GetNumberRows()
            self.grid.SelectRow(current_row_count)
            self.grid.AppendRows(1)
            if "FM" or "POI" in target.name.value:
                # TODO save the fm and fib stream metadata
                pixel_coords = self.correlation_target.fm_streams[0].getPixelCoordinates_alt((target.coordinates.value[0],target.coordinates.value[1]))
                self.grid.SetCellValue(current_row_count, GridColumns.Z.value, f"{target.coordinates.value[2]:.{GRID_PRECISION}f}")
            else:
                pixel_coords = self.correlation_target.fib_stream.getPixelCoordinates_alt((target.coordinates.value[0],target.coordinates.value[1]))
                self.grid.SetCellValue(current_row_count, GridColumns.Z.value, "")


            self.grid.SetCellValue(current_row_count, GridColumns.X.value,
                                   f"{pixel_coords[0]:.{GRID_PRECISION}f}")
            self.grid.SetCellValue(current_row_count, GridColumns.Y.value,
                                   f"{pixel_coords[1]:.{GRID_PRECISION}f}")
            self.grid.SetCellValue(current_row_count, GridColumns.Index.value, str(target.index.value))
            self.grid.SetCellValue(current_row_count, GridColumns.Type.value, target.name.value)

        self.reorder_table()
        self._panel.Layout()
        self.update_feature_correlation_target()
        if self.check_correlation_conditions():
            self.latest_change = True
            self.queue_latest_change()

    def on_z_targeting(self, event):
        """
        Handle Z-targeting when the Z-targeting button is clicked.
        This will update the Z value in the table and change the color based on success.
        """
        if self._tab_data_model.main.currentTarget.value:
            das = [stream.raw[0] for stream in self.correlation_target.fm_streams]
            coords = self._tab_data_model.main.currentTarget.value.coordinates.value
            pixel_coords = self.correlation_target.fm_streams[0].getPixelCoordinates_alt((coords[0], coords[1]))
            self._tab_data_model.main.currentTarget.value.coordinates.value[2] = get_optimized_z_gauss(das, float(pixel_coords[0]), float(pixel_coords[1]), coords[2])

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
