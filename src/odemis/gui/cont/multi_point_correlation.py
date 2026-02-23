# -*- coding: utf-8 -*-
"""
@author Karishma Kumar

Copyright © 2025, Delmic

Handles the controls for performing correlation using 3DCT algorithm.

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
import logging
import queue
import re
import threading
import time
from enum import Enum
from typing import List, Union, Optional, Tuple

import numpy
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.

from odemis import model, util
from odemis.acq.align.tdct import get_optimized_z_gauss, _convert_das_to_numpy_stack, run_tdct_correlation
from odemis.acq.feature import save_features, FIBFMCorrelationData, Target, TargetType
from odemis.acq.stream import StaticFluoStream, StaticSEMStream, StaticFIBStream, FluoStream
from odemis.gui import conf
from odemis.gui.model import CryoGUIData
from odemis.gui.util import call_in_wx_main
from odemis.model import ListVA
from odemis.util.dataio import data_to_static_streams
from odemis.util.interpolation import interpolate_z_stack
from odemis.util.units import readable_str

# create an enum with column labels and position
class GridColumns(Enum):
    Type = 0  # Column for "type"
    X = 1  # Column for "x"
    Y = 2  # Column for "y"
    Z = 3  # Column for "z"
    Index = 4  # Column for "index"

GRID_PRECISION = 2  # Number of decimal places to display in the grid

# Regex search pattern to distinguish between FIB and FM target. These targets can
# have the same type of Fiducials but there is a prefix in the name to distinguish them.
FIDUCIAL_PATTERN = r"^[^-]+-"
RIM_COR_DEFAULT = 0.495  # See MD_RIM_COR. This value works fine for 50x objectives, which are common

# Both functions getPixel3DCoordinates(args*, kwargs*) and getPhysical3DCoordinates(args*, kwargs*) need special
# conditions to convert between physical and pixel coordinate systems in order for multipoint correlation to operate.
# For coordinate conversions, we assume the pixels in 3D are isosymmetric
# i.e. size in pixel[0]=pixel[1]=pixel[2].

def getPixel3DCoordinates(stream: FluoStream, p_pos: Tuple[float, float, float], check_bbox: bool = False) \
        -> Optional[Tuple[float, float, float]]:
    """
    Translate 3D physical coordinates into 3D pixel coordinates. The z coordinate is computed assuming iso-voxel
    between x, y and z.
    :param stream: Stream which is used as reference for coordinate conversion
    :param p_pos: the position in physical coordinates (m). x and y are the sample position, z is the focus position
    :param check_bbox: if True, the function will return None if the position is outside of the image
    :returns: (x, y, z) in pixel coordinates or None if it's outside of the image. No boundary check is done
    """
    pixel_pos = stream.getPixelCoordinates(p_pos[:2], check_bbox=check_bbox)
    # pixel_pos_c is None if the position is checked and found to be outside the boundary box
    if pixel_pos is None:
        return None

    raw = stream.raw[0]
    md = stream._find_metadata(raw.metadata)
    pxs = md.get(model.MD_PIXEL_SIZE, (1e-6, 1e-6))
    # For multipoint correlation, we assume that the pixel size in x is the same as in y
    if not util.almost_equal(pxs[0], pxs[1], atol=1e-9):
        logging.warning("Pixel size in x and y are not equal while computing pixel coordinates")

    # Z position is found by taking into account MD_POS and subtracting it from the physical coordinates.
    # Pixel value used for Z enforces the iso-voxel condition between x, y and z. It is not the real pixel value in z.
    tpos = md.get(model.MD_POS, (0, 0, 0))
    tpos_z = tpos[2] if len(tpos) >= 3 else 0.0
    z = (p_pos[2] - tpos_z) / pxs[1]
    pixel_pos = (pixel_pos[0], pixel_pos[1], z)
    return pixel_pos

def getPhysical3DCoordinates(stream: FluoStream, pixel_pos: Tuple[float, float, float])\
                             -> Optional[Tuple[float, float, float]]:
    """
    Translate 3D pixel coordinates into 3D physical coordinates. The z coordinate is computed assuming iso-voxel
    between x, y and z.
    :param stream: Stream which is used as reference for coordinate conversion
    :param pixel_pos: the position in pixel coordinates (x, y, z)
    :returns: the position in physical coordinates (x, y, z) in meters
    """
    p_pos = stream.getPhysicalCoordinates(pixel_pos[:2])
    raw = stream.raw[0]
    md = stream._find_metadata(raw.metadata)
    pxs = md.get(model.MD_PIXEL_SIZE, (1e-6, 1e-6))[0:2]
    tpos = md.get(model.MD_POS, (0, 0, 0))
    tpos_z = tpos[2] if len(tpos) >= 3 else 0.0
    p_pos_z = pixel_pos[2] * pxs[1] + tpos_z
    return (p_pos[0], p_pos[1], p_pos_z)

def update_feature_correlation_target(correlation_target: FIBFMCorrelationData,
                                      tab_data: CryoGUIData) -> FIBFMCorrelationData:
    """
    Update the correlation target with the latest fiducials and POIs from the tab data model.
    :param correlation_target: (FIBFMCorrelationData) the correlation target to update
    :param tab_data: (CryoGUIData) the tab data model
    :return: (FIBFMCorrelationData) the updated correlation target
    """
    # Corner case: When fiducials are deleted and the indices are not continuous. Then the fiducial pairs
    # will be incorrectly matched together.
    # TODO Handle the corner case
    fib_fiducials = []
    fm_fiducials = []
    correlation_target.fm_pois = []
    correlation_target.fib_surface_fiducial = None
    for target in tab_data.main.targets.value:
        if target.type.value == TargetType.FibFiducial:
            fib_fiducials.append(target)
        elif target.type.value == TargetType.Fiducial:
            fm_fiducials.append(target)
        elif target.type.value == TargetType.PointOfInterest:
            correlation_target.fm_pois.append(target)
        elif target.type.value == TargetType.SurfaceFiducial:
            correlation_target.fib_surface_fiducial = target
    if fib_fiducials:
        fib_fiducials.sort(key=lambda x: x.index.value)
    correlation_target.fib_fiducials = fib_fiducials
    if fm_fiducials:
        fm_fiducials.sort(key=lambda x: x.index.value)
    correlation_target.fm_fiducials = fm_fiducials

    acq_conf = conf.get_acqui_conf()
    save_features(acq_conf.pj_last_path, tab_data.main.features.value)

    return correlation_target


class CorrelationPointsController:
    """
    Displays and modified the points in the grid based on user's interaction. When a minimum of 4 fiducial pairs and
    one poi in FM are present, correlation runs automatically. Any changes to the points will trigger a new correlation.
    The correlation result is displayed in the correlation_rms text box.
    """

    def __init__(self, frame):
        """
        :param frame: (wx.Frame) the frame containing the controls for the correlation points
        """
        self._tab_data_model = frame.tab_data
        self._main_data_model = self._tab_data_model.main
        self._panel = frame
        self._viewports = frame.pnl_correlation_grid.viewports
        self.grid_targets = (TargetType.PointOfInterest, TargetType.Fiducial, TargetType.FibFiducial)

        lens_md = self._main_data_model.lens.getMetadata()
        self.refractive_scale = lens_md.get(model.MD_RIM_COR, RIM_COR_DEFAULT)
        self._panel.fp_correlation_streams.Show(True)

        # Access the correlation points table (wxListCtrl)
        self.grid = self._panel.table_grid

        # Access the Z-targeting button
        self.z_targeting_btn = self._panel.btn_z_targeting
        self.z_targeting_btn.Bind(wx.EVT_BUTTON, self._on_z_targeting)
        self.z_targeting_btn.Enable(False)
        # Disable Z-targeting button if super z stream is available as Z-targeting is not required in that case
        self.refinez_active = True
        if self._tab_data_model.main.currentFeature.value.superz_stream_name:
            self.z_targeting_btn.Hide()
            self.refinez_active = False

        self.delete_btn = self._panel.btn_delete_row
        self.delete_btn.Bind(wx.EVT_BUTTON, self._on_delete_row)

        # Bind the event for cell selection
        self.grid.Bind(wx.grid.EVT_GRID_SELECT_CELL, self._on_cell_selected)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGING, self._on_cell_changing)
        self.grid.Bind(wx.grid.EVT_GRID_CELL_CHANGED, self._on_cell_changed)

        self.grid.CreateGrid(0, 5)
        self.grid.SetRowLabelSize(0)
        self.grid.SetColLabelValue(GridColumns.Type.value, GridColumns.Type.name)
        self.grid.SetColLabelValue(GridColumns.X.value, GridColumns.X.name)
        self.grid.SetColLabelValue(GridColumns.Y.value, GridColumns.Y.name)
        self.grid.SetColLabelValue(GridColumns.Z.value, GridColumns.Z.name)
        self.grid.SetColLabelValue(GridColumns.Index.value, GridColumns.Index.name)
        self.grid.Bind(wx.EVT_KEY_DOWN, self._on_key_down_grid)
        self.grid.EnableEditing(True)

        # Parameters to keep track of the latest changes and process the correlation result with the latest change
        self.correlation_txt = self._panel.txt_correlation_rms
        self.correlation_txt.Show(True)
        self.change_queue: queue.Queue[Union[bool, None]] = queue.Queue()  # Holds the latest change
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()
        self.is_processing = False  # To track if the function is currently processing
        # Create a dictionary to hold the groups of streams based on their shape and centre position
        # The keys will be tuples of (shape, position)
        # The values will be sets of stream indices
        self.stream_groups: Optional[
            dict[tuple[tuple[int], tuple[float]], list[int]]] = None  # Dictionary to hold the stream groups
        # Key of the selected group chosen previously
        self.previous_group: Optional[tuple[tuple[int], tuple[float]]] = None

        # Interpolate the fm streams such that the pixel size in z is the same as in x and y
        streams_list = []
        for stream in self._tab_data_model.main.currentFeature.value.streams.value:
            if isinstance(stream, StaticFluoStream) and hasattr(stream, "zIndex"):
                streams_list.append(StaticFluoStream(stream.name.value, stream.raw[0]))

        self.streams_list = streams_list
        self.correlation_target = self._tab_data_model.main.currentFeature.value.correlation_data

        # Reset targets and current target and populate it based on the current feature
        self._subscribed_target = None
        # On GUI start: ensure FM fiducial indices are contiguous (1..N) when there are no FIB fiducials.
        # This fixes cases like FM indices being 1,2,3,6 when no FIB fiducials are present on startup.
        self._renumber_fm_fiducials_on_start()
        self._tab_data_model.main.targets.subscribe(self._on_target_changes, init=True)
        self._tab_data_model.main.currentTarget.subscribe(self._on_current_target_changes, init=True)

        # Group the FM streams based on the shape and position
        # Load the streams (FIB and FM) in the multipoint correlation window
        self.group_streams()
        self._add_stream_group()

        self._panel.fp_correlation_panel.Show(True)

    @call_in_wx_main
    def _on_fm_streams_visiblity(self, stream_projections: ListVA) -> None:
        """
        Update the stream groups when the user selects the FM stream panel from a different group. The
        group of the selected stream panel will be visible and the other groups will be invisible
        as well as collapsed.
        """
        if not self.stream_groups or (not stream_projections):
            return

        stream_obj = stream_projections[-1].stream  # Get the stream name

        # Find the index of the stream in the stream list
        stream_index = next(
            (i for i, stream in enumerate(self.streams_list)
             if stream == stream_obj), None)

        if stream_index is None:
            return

        # Get the group this stream belongs to
        group_key = next(
            (key for key, indices in self.stream_groups.items() if stream_index in indices), None)
        if not group_key:
            return

        # Update visibility for the group
        if self.previous_group == group_key:
            return
        self.previous_group = group_key

        for key, indices in self.stream_groups.items():
            for index in indices:
                stream = self.streams_list[index]
                # find stream controller for the stream
                ssc = next(
                    (sc for sc in self._panel.streambar_controller.stream_controllers
                     if sc.stream == stream), None)
                if not ssc:
                    logging.error(f"Stream controller not found for stream {stream.name.value}")
                    return
                if isinstance(stream, StaticFluoStream) and not group_key:
                    group_key = key
                if key == group_key:
                    ssc.stream_panel.set_visible(True)
                    ssc.stream_panel.collapse(False)
                else:
                    if hasattr(self._tab_data_model.views.value[0], "removeStream"):
                        self._tab_data_model.views.value[0].removeStream(stream)
                    ssc.stream_panel.set_visible(False)
                    ssc.stream_panel.collapse(True)

    def _add_stream_group(self) -> None:
        """
        Based on the stream groups, add the streams to the stream bar such that the stream panels of one
        group are visible and uncollapsed/opened together while the other groups are set to invisible and collapsed.
        """
        # Load the FIB acquired stream
        acquired_fibsem_streams = data_to_static_streams(
            [self._tab_data_model.main.currentFeature.value.reference_image])
        for s in acquired_fibsem_streams:
            if isinstance(s, (StaticFIBStream, StaticSEMStream)):
                self._panel.streambar_controller.addStream(s, play=False, add_to_view=True)
                self.correlation_target.fib_stream = s
                current_shape = s.raw[0].shape
                centre_pos = s.raw[0].metadata[model.MD_POS]
                centre_pos = tuple([round(pos, 6) for pos in centre_pos])  # to handle floating point precision
                self.correlation_target.fib_stream_key = (current_shape, centre_pos)

        last_key = None
        last_indices = None

        for insertion_index, (key, indices) in enumerate(self.stream_groups.items()):
            for index in indices:
                stream = self.streams_list[index]
                if isinstance(stream, StaticFluoStream):
                    stream.name.value = f"{stream.name.value}-Group-{insertion_index}"
                    self.streams_list[index] = stream
                    self._panel.streambar_controller.addStream(self.streams_list[index], play=False)
            last_key, last_indices = key, indices

        # Assign correlation target explicitly for the last group processed
        # This fm group will be used for the coordinate conversions later on
        # from physical to pixel space and vice versa.
        if last_key and last_indices:
            self.correlation_target.fm_stream_key = last_key
            self.correlation_target.fm_streams = [self.streams_list[i] for i in last_indices]

        # Update the group visibility based on the latest changes
        self._tab_data_model.views.value[0].stream_tree.flat.subscribe(self._on_fm_streams_visiblity, init=True)

    def group_streams(self) -> None:
        """
        Group the FM streams based on the shape and position.
        """
        # Create a dictionary to hold the groups of streams based on their shape and centre position
        # The keys will be tuples of (shape, position)
        # The values will be sets of stream indices
        stream_groups = {}

        # Group the FM streams related to the current feature based on the shape and current position. THe FM streams
        # which have z stack are considered for grouping, the other FM streams without z stack are ignored.
        for stream_index, stream in enumerate(self.streams_list):
            if isinstance(stream, StaticFluoStream):
                # enable Maximum Intensity projection (MIP) by default
                stream.max_projection.value = True
                raw_shape = stream.raw[0].shape
                centre_pos = stream.raw[0].metadata[model.MD_POS]
                centre_pos = tuple([round(pos, 6) for pos in centre_pos])  # to handle floating point precision
                stream_name = stream.name.value
                # Create the key based on current shape, centre position, and zIndex
                key = (raw_shape, centre_pos)
                # Check if a set for this key already exists
                if key not in stream_groups:
                    # Create a new set if the combination of shape, centre position, and zIndex is different
                    stream_groups[key] = set()
                # # Check if there's already a stream with the same name in the set
                # # If a stream with the same name exists, replace the previous index to keep the latest stream index
                indices_to_remove = set()
                for idx in stream_groups[key]:
                    if self.streams_list[idx].name.value == stream_name:
                        indices_to_remove.add(idx)
                # Remove old indices with the same name
                stream_groups[key] -= indices_to_remove
                # Add the new stream index to the set
                stream_groups[key].add(stream_index)
        self.stream_groups = stream_groups

    def _on_key_down_grid(self, event) -> None:
        """Handle key down events on the grid, especially to suppress Enter key default behavior."""
        if event.GetKeyCode() == wx.WXK_RETURN:
            # Commit the value in the currently edited cell
            if self.grid.IsCellEditControlEnabled():
                self.grid.DisableCellEditControl()
                # Suppress the Enter key from moving to the next row
            return
        else:
            # For other keys, allow the default behavior
            event.Skip()

    def check_correlation_conditions(self) -> bool:
        """
        Minimum 4 FIB and FM fiducials and 1 POI in FM are required to run the correlation.
        :return: (bool) True if the conditions are met, False otherwise
        """
        if self.correlation_target:
            if ((len(self.correlation_target.fib_fiducials) >= 4 and
                 len(self.correlation_target.fm_fiducials) >= 4 and len(self.correlation_target.fm_pois) >= 1 and
                 len(self._tab_data_model.views.value[0].stream_tree) > 0) and self.correlation_target.fib_stream and
                    (len(self.correlation_target.fm_fiducials) == len(self.correlation_target.fib_fiducials))):
                return True
            else:
                self.correlation_target.clear()
                self._tab_data_model.projected_points = []
                self.correlation_txt.SetLabel("To run correlation, please add \n"
                                              "minimum 4 FIB-FM fiducial pairs, 1 POI in FM.")
                self._panel.Layout()
                # Update the FIB viewport because it shows the output overlays
                # It is the second viewport out of total two viewports
                self._viewports[1].canvas.update_drawing()
                return False
        else:
            return False

    def _need_reprocessing(self):
        """Indicate the correlation should be recomputed, due to a change in the data"""
        self.change_queue.put(True)

    def _process_queue(self):
        """Worker thread that continuously processes requests from the queue."""
        try:
            while True:
                block = True
                # Drop new requests, if so extra ones are queued
                while True:
                    try:
                        task = self.change_queue.get(block=block)
                        if task is None:  # Special exit signal
                            return
                    except queue.Empty:
                        break  # No more messages => ready to run the update!
                    # After the first message, read the other ones if they are already present
                    block = False

                self._process_latest_change()
                time.sleep(0.1)  # rate limit the update

        except Exception:
            logging.exception("Failure in the correlation update")

    def _process_latest_change(self):
        """Process the latest change in the queue."""
        self.is_processing = True
        self._do_correlation()
        rms = self.correlation_target.correlation_result["output"]["error"]["rms_error"]
        wx.CallAfter(self.correlation_txt.SetLabel,
                     f"Correlation RMS Deviation : {readable_str(rms, sig=3)}")
        # Display the output in the relevant views
        self._viewports[1].canvas.Refresh()
        self.is_processing = False  # Mark that processing is complete

    def stop(self):
        """Gracefully stop the worker thread."""
        self.change_queue.put(None)
        # unsubscribe when the correlation controller is closed
        if self._tab_data_model.main.currentTarget.value:
            self._tab_data_model.main.currentTarget.value.coordinates.unsubscribe(self._on_current_coordinates_changes)
        self._tab_data_model.main.currentTarget.unsubscribe(self._on_current_target_changes)
        self._tab_data_model.main.targets.unsubscribe(self._on_target_changes)
        self._tab_data_model.views.value[0].stream_tree.flat.unsubscribe(self._on_fm_streams_visiblity)
        self.worker_thread.join(5)

    def _do_correlation(self):
        """Run the correlation between the FIB and FM images."""
        # Modify the input data to match the required format to run 3DCT
        fm_das = [stream.raw[0] for stream in self.correlation_target.fm_streams]
        fm_image = _convert_das_to_numpy_stack(fm_das)
        fib_da = self.correlation_target.fib_stream.raw[0]
        fib_coords = []
        fm_coords = []
        poi_coords = []
        index_order = []  # append the same index order to the projected points as the input fiducials
        path = self._tab_data_model.main.project_path.value
        for fib_coord in self.correlation_target.fib_fiducials:
            index_order.append(fib_coord.index.value)
            fib_coord = self.correlation_target.fib_stream.getPixelCoordinates(fib_coord.coordinates.value[0:2],
                                                                               check_bbox=False)
            fib_coords.append(fib_coord)
        fib_coords = numpy.array(fib_coords, dtype=numpy.float32)
        for fm_coord in self.correlation_target.fm_fiducials:
            fm_coord_px = getPixel3DCoordinates(self.correlation_target.fm_streams[0], fm_coord.coordinates.value)
            fm_coords.append(fm_coord_px)
        fm_coords = numpy.array(fm_coords, dtype=numpy.float32)
        poi_coord = self.correlation_target.fm_pois[0]
        poi_coord_px = getPixel3DCoordinates(self.correlation_target.fm_streams[0], poi_coord.coordinates.value)
        poi_coords.append(poi_coord_px)
        poi_coords = numpy.array(poi_coords, dtype=numpy.float32)
        # Run the correlation
        self.correlation_target.correlation_result = run_tdct_correlation(fib_coords=fib_coords, fm_coords=fm_coords,
                                                                          poi_coords=poi_coords,
                                                                          fib_image=fib_da, fm_image=fm_image,
                                                                          path=path)
        # Update the output parameters of the correlation
        self._tab_data_model.projected_points = []
        self.correlation_target.fib_projected_fiducials = []
        points = self.correlation_target.correlation_result['output']['error']['reprojected_3d']
        for n, i in enumerate(points[0]):
            p_pos = self.correlation_target.fib_stream.getPhysicalCoordinates((points[0][n], points[1][n]))
            target = Target(x=p_pos[0], y=p_pos[1], z=0, name="PP" + str(index_order[n]),
                            type=TargetType.ProjectedFiducial, index=index_order[n],
                            fm_focus_position=0)
            self._tab_data_model.projected_points.append(target)
            self.correlation_target.fib_projected_fiducials.append(target)

        # Convert from pixel to physical coordinates
        projected_poi_px = self.correlation_target.correlation_result["output"]["poi"][0]["image_px"]

        projected_poi = self.correlation_target.fib_stream.getPhysicalCoordinates(projected_poi_px)
        projected_poi_target = Target(x=projected_poi[0], y=projected_poi[1], z=0, name="PPOI",
                                      type=TargetType.ProjectedPOI,
                                      index=1,
                                      fm_focus_position=0)
        if self.correlation_target.fib_surface_fiducial:
            edge = self.correlation_target.fib_surface_fiducial.coordinates.value
            # Explanation of not including the milling angle in the correction:
            # The Z correction is applied based on the distance from the surface fiducial to the projected POI
            # which already takes into account the tilted sample plane. Therefore, the distance in Y direction
            # of sample plane is sufficient to calculate the Z correction.
            correction = (projected_poi_target.coordinates.value[1] - edge[1]) * self.refractive_scale
            projected_poi_target.coordinates.value[1] += correction

        self._tab_data_model.projected_points.append(projected_poi_target)
        self.correlation_target.fib_projected_pois = [projected_poi_target]

    def _on_delete_row(self, event) -> None:
        """
        Deletes the currently selected row and clear the current target VA. Updates the correlation target based on the
        latest changes.
        """
        if not self._tab_data_model.main.currentTarget.value:
            self.grid.ClearSelection()
            return

        selected_rows = self.grid.GetSelectedRows()
        # The grid contains only FIB and FM fiducials and POIs.
        # So if the current target is of these types, delete from the grid.
        # Otherwise, it is a surface fiducial which is not present in the grid.
        # Check the type of the current target and delete accordingly.
        if selected_rows:
            for row in selected_rows:
                self.grid.DeleteRows(pos=row, numRows=1, updateLabels=True)

                for target in self._tab_data_model.main.targets.value:
                    if target.name.value == self._tab_data_model.main.currentTarget.value.name.value:
                        logging.debug(f"Deleting target: {target.name.value}")
                        self._tab_data_model.main.targets.value.remove(target)
                        self.z_targeting_btn.Enable(False)
                        self._tab_data_model.main.currentTarget.value = None
                        self.grid.ClearSelection()
                        break

        elif TargetType.SurfaceFiducial == self._tab_data_model.main.currentTarget.value.type.value:
            for target in self._tab_data_model.main.targets.value:
                if TargetType.SurfaceFiducial == target.type.value:
                    logging.debug("Deleting Surface Fiducial")
                    self._tab_data_model.main.targets.value.remove(target)
                    self._tab_data_model.main.currentTarget.value = None
                    break

        self.correlation_target = update_feature_correlation_target(self.correlation_target, self._tab_data_model)
        if self.check_correlation_conditions():
            self._need_reprocessing()

    def _on_cell_selected(self, event) -> None:
        """Highlight the selected row in the grid and update the current target."""
        row = event.GetRow()
        for target in self._tab_data_model.main.targets.value:
            if self._selected_target_in_grid(target, row):
                self._tab_data_model.main.currentTarget.value = target
                break

        for vp in self._viewports:
            vp.canvas.update_drawing()

        # highlight the selected row
        self.grid.SelectRow(event.GetRow())
        event.Skip()

    def _selected_target_in_grid(self, target: Target, row: int) -> bool:
        """
        Checks if the given target is present in the selected row of the grid.
        :param target: (Target) the target to check
        :param row: (int) the row to check
        :return: (bool) True if the target is present in the selected row, False otherwise
        """
        # check if target and row values exist
        grid_selection = False
        if not target or (target.type.value not in self.grid_targets) or self.grid.GetCellValue(row, GridColumns.Index.value) == "":
            return False

        if target.index.value == int(self.grid.GetCellValue(row, GridColumns.Index.value)) and (
                self.grid.GetCellValue(row, GridColumns.Type.value) in target.name.value):
            return True

        return grid_selection

    def _on_cell_changing(self, event) -> None:
        """Update the target based on the cell change."""
        col = event.GetCol()
        new_value = event.GetString()
        col_name = self.grid.GetColLabelValue(col)
        count_row_index = event.GetRow()

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

                current_name_type = re.search(FIDUCIAL_PATTERN, current_name).group()
                for target in self._tab_data_model.main.targets.value:
                    target_name_type = re.search(FIDUCIAL_PATTERN, target.name.value).group()
                    if target_name_type == current_name_type:
                        indices.append(target.index.value)
                        if target.index.value == int(new_value):
                            target_swap = target
                            break

                index_max = max(indices)
                assert 1 <= int(new_value) <= index_max
                if target_swap:
                    target_swap.index.value = current_index
                    target_swap.name.value = current_name_type + str(target_swap.index.value)
                    self._on_target_changes(self._tab_data_model.main.targets.value)
                self._tab_data_model.main.currentTarget.value.index.value = int(new_value)
                self._tab_data_model.main.currentTarget.value.name.value = current_name_type + str(new_value)
                self.grid.SetCellValue(count_row_index, GridColumns.Type.value,
                                       self._tab_data_model.main.currentTarget.value.name.value)
                self._tab_data_model.main.currentTarget.value = None

            except (ValueError, AssertionError):
                wx.MessageBox(f"Index must be an int in the range (1, {index_max})!", "Invalid Input",
                              wx.OK | wx.ICON_ERROR)
                event.Veto()  # Prevent the change
                return

        elif col_name in [GridColumns.X.name, GridColumns.Y.name, GridColumns.Z.name]:
            x = float(self.grid.GetCellValue(count_row_index, GridColumns.X.value))
            y = float(self.grid.GetCellValue(count_row_index, GridColumns.Y.value))
            try:
                if col_name == GridColumns.X.name:
                    if self._tab_data_model.main.currentTarget.value.type.value == TargetType.FibFiducial:
                        p_coord = self.correlation_target.fib_stream.getPhysicalCoordinates((float(new_value),
                                                                                             y))
                    else:
                        p_coord = self.correlation_target.fm_streams[0].getPhysicalCoordinates((float(new_value),
                                                                                                y))
                    self._tab_data_model.main.currentTarget.value.coordinates.value[0] = p_coord[0]
                    self._tab_data_model.main.currentTarget.value.coordinates.value[1] = p_coord[1]
                if col_name == GridColumns.Y.name:
                    if self._tab_data_model.main.currentTarget.value.type.value == TargetType.FibFiducial:
                        p_coord = self.correlation_target.fib_stream.getPhysicalCoordinates((x, float(new_value)))
                    else:
                        p_coord = self.correlation_target.fm_streams[0].getPhysicalCoordinates((x, float(new_value)))
                    self._tab_data_model.main.currentTarget.value.coordinates.value[0] = p_coord[0]
                    self._tab_data_model.main.currentTarget.value.coordinates.value[1] = p_coord[1]
                elif col_name == GridColumns.Z.name and (
                        self._tab_data_model.main.currentTarget.value.type.value != TargetType.FibFiducial):
                    self._tab_data_model.main.currentTarget.value.coordinates.value[2] = \
                    getPhysical3DCoordinates(self.correlation_target.fm_streams[0], (x, y, float(new_value)))[2]
            except ValueError:
                wx.MessageBox("X, Y, Z values must be a float!", "Invalid Input", wx.OK | wx.ICON_ERROR)
                event.Veto()  # Prevent the change
                return

        event.Skip()  # Allow the change if validation passes

    def _on_cell_changed(self, event) -> None:
        """Get the cell column of the modified cell and reorder the grid/table based on the index column."""
        # Refresh the canvas to update the target overlays in the viewports
        for vp in self._viewports:
            vp.canvas.update_drawing()
        # If the index column is modified, reorder the grid based on the index column
        col = event.GetCol()
        col_name = self.grid.GetColLabelValue(col)
        if col_name == GridColumns.Index.name:
            self._reorder_grid()

    @call_in_wx_main
    def _on_current_target_changes(self, target: Target) -> None:
        """
        Highlight the selected row in the grid when the current target changes and subscribe to the coordinate
        changes of the current target.
        :param target: (Target) the current target
        """
        # Enable or disable buttons based on stream selection.
        # When FM is selected, the Z-targeting button is enabled.
        # When FIB is selected, the Z-targeting button is disabled.
        # For new targets, automatically perform Z targeting if MIP is checked for at least one FM stream
        if not target:
            self.grid.ClearSelection()
            self.z_targeting_btn.Enable(False)
            return None

        mip_enabled = any([stream.max_projection.value for stream in self.correlation_target.fm_streams])

        # Refine z should be disabled if the the Z information was obtained using SuperZ
        if self.refinez_active and (target.type.value in self.grid_targets):
            if TargetType.FibFiducial == target.type.value:
                self.z_targeting_btn.Enable(False)
            else:
                self.z_targeting_btn.Enable(True)
                if mip_enabled:
                    self._on_z_targeting(None)

        for row in range(self.grid.GetNumberRows()):
            if self._selected_target_in_grid(target, row):
                self.grid.SelectRow(row)
                break

        for vp in self._viewports:
            vp.canvas.update_drawing()

        if self._subscribed_target is not None:
            self._subscribed_target.coordinates.unsubscribe(self._on_current_coordinates_changes)
            self._subscribed_target = None

        if target:
            target.coordinates.subscribe(self._on_current_coordinates_changes, init=True)
            self._subscribed_target = target

    @call_in_wx_main
    def _on_current_coordinates_changes(self, coordinates: ListVA) -> None:
        """
        Update the coordinates of the current target in the grid and update the correlation result.
        :param coordinates: the coordinates of the current target
        """
        target = self._tab_data_model.main.currentTarget.value
        temp_check = False
        for row in range(self.grid.GetNumberRows()):
            if self._selected_target_in_grid(target, row):
                if target.type.value == TargetType.FibFiducial:
                    pixel_coords = self.correlation_target.fib_stream.getPixelCoordinates(
                        (target.coordinates.value[0], target.coordinates.value[1]), check_bbox=False)
                else:
                    pixel_coords = getPixel3DCoordinates(self.correlation_target.fm_streams[0], target.coordinates.value)
                    if (self.grid.GetCellValue(row,
                                               GridColumns.Z.value)) != f"{pixel_coords[2]:.{GRID_PRECISION}f}":
                        temp_check = True
                    self.grid.SetCellValue(row, GridColumns.Z.value, f"{pixel_coords[2]:.{GRID_PRECISION}f}")
                # Get cell value
                if (self.grid.GetCellValue(row, GridColumns.X.value) != f"{pixel_coords[0]:.{GRID_PRECISION}f}" or
                        self.grid.GetCellValue(row, GridColumns.Y.value) != f"{pixel_coords[1]:.{GRID_PRECISION}f}"):
                    temp_check = True
                self.grid.SetCellValue(row, GridColumns.X.value, f"{pixel_coords[0]:.{GRID_PRECISION}f}")
                self.grid.SetCellValue(row, GridColumns.Y.value, f"{pixel_coords[1]:.{GRID_PRECISION}f}")

        self.correlation_target = update_feature_correlation_target(self.correlation_target,
                                                                    self._tab_data_model)

        if self.check_correlation_conditions() and (temp_check or target.type.value == TargetType.SurfaceFiducial):
            self._need_reprocessing()

    @call_in_wx_main
    def _on_target_changes(self, targets: List[Target]) -> None:
        """
        Update the grid with the new targets and update the correlation target based on the latest changes.
        :param targets: the updated list of targets
        """
        # Clear the grid before populating it with new data
        self.grid.ClearGrid()
        # delete the empty rows
        if self.grid.GetNumberRows() > 0:
            self.grid.DeleteRows(0, self.grid.GetNumberRows())
        for target in targets:
            if target.type.value not in self.grid_targets:
                self.grid.ClearSelection()
            else:
                current_row_count = self.grid.GetNumberRows()
                self.grid.AppendRows(1)
                # Get the pixel coordinates of the target and first set the z value in the grid
                if target.type.value == TargetType.FibFiducial:
                    pixel_coords = self.correlation_target.fib_stream.getPixelCoordinates(
                        (target.coordinates.value[0], target.coordinates.value[1]), check_bbox=False)
                    self.grid.SetCellValue(current_row_count, GridColumns.Z.value, "")
                else:
                    pixel_coords = getPixel3DCoordinates(self.correlation_target.fm_streams[0], target.coordinates.value)
                    self.grid.SetCellValue(current_row_count, GridColumns.Z.value,
                                           f"{pixel_coords[2]:.{GRID_PRECISION}f}")
                # Set x and y position in the grid
                self.grid.SetCellValue(current_row_count, GridColumns.X.value,
                                       f"{pixel_coords[0]:.{GRID_PRECISION}f}")
                self.grid.SetCellValue(current_row_count, GridColumns.Y.value,
                                       f"{pixel_coords[1]:.{GRID_PRECISION}f}")
                self.grid.SetCellValue(current_row_count, GridColumns.Index.value, str(target.index.value))
                self.grid.SetCellValue(current_row_count, GridColumns.Type.value, target.name.value)

        self._reorder_grid()
        self._panel.Layout()
        self.correlation_target = update_feature_correlation_target(self.correlation_target, self._tab_data_model)

        for vp in self._viewports:
            vp.canvas.update_drawing()

        if self.check_correlation_conditions():
            self._need_reprocessing()

    def _on_z_targeting(self, evt) -> None:
        """
        Handle Z-targeting when the Z-targeting button is clicked.
        """
        if self._tab_data_model.main.currentTarget.value:

            # Select the streams which are visible in the view for Z-targeting
            streams_projections = self._tab_data_model.views.value[0].stream_tree.flat.value
            if not streams_projections:
                wx.MessageBox("FM streams are not available for refining Z", "Error", wx.OK | wx.ICON_ERROR)
                return

            coords = self._tab_data_model.main.currentTarget.value.coordinates.value
            pixel_coords = getPixel3DCoordinates(self.correlation_target.fm_streams[0], coords)
            das = [interpolate_z_stack(da=stream_projection.stream.raw[0]
                                       [:,
                                       int(pixel_coords[1]):int(pixel_coords[1])+1,
                                       int(pixel_coords[0]):int(pixel_coords[0])+1],
                                       method="linear")
                   for stream_projection in streams_projections]

            z = float(get_optimized_z_gauss(das, int(0), int(0), int(pixel_coords[2])))
            z_p = getPhysical3DCoordinates(self.correlation_target.fm_streams[0],
                                 (pixel_coords[0],pixel_coords[1], z))[2]
            self._tab_data_model.main.currentTarget.value.coordinates.value[2] = z_p

    def _reorder_grid(self) -> None:
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
        data.sort(key=lambda x: (int(x[GridColumns.Index.value]), -ord(x[GridColumns.Type.value][1])))

        # Repopulate the grid with sorted data
        for row, row_data in enumerate(data):
            for col, value in enumerate(row_data):
                self.grid.SetCellValue(row, col, str(value))

    def _renumber_fm_fiducials_on_start(self) -> None:
        """
        If there are no FIB fiducials at GUI start, renumber FM fiducials so their indices are contiguous
        starting from 1. This handles cases where FM fiducials may have gaps (e.g. 1,2,3,6).

        This method updates both the `index` and `name` fields of the FM targets in-place and
        persists the feature file via `update_feature_correlation_target` so the rest of the GUI
        sees the corrected ordering when it initializes.
        """
        try:
            targets = self._tab_data_model.main.targets.value
        except Exception:
            # If targets are not available for any reason, skip renumbering
            return

        # Check if any FIB fiducials exist; if so, we don't renumber on startup
        has_fib = any(t.type.value == TargetType.FibFiducial for t in targets)
        if has_fib:
            return

        # Collect FM fiducials (TargetType.Fiducial) and sort by current index
        fm_fiducials = [t for t in targets if t.type.value == TargetType.Fiducial]
        if not fm_fiducials:
            return

        fm_fiducials.sort(key=lambda t: t.index.value)

        # If indices are already contiguous starting from 1, nothing to do
        # The fiducials are sorted when saved, so we just need to check the last index
        expected_last_index = len(fm_fiducials)
        actual_last_index = fm_fiducials[-1].index.value
        if expected_last_index == actual_last_index:
            return

        # Renumber sequentially and update names preserving the prefix (if present)
        for new_idx, target in enumerate(fm_fiducials, start=1):
            old_name = target.name.value
            old_name_type = re.search(FIDUCIAL_PATTERN, old_name).group()
            target.index.value = new_idx
            target.name.value = old_name_type + str(target.index.value)

        # Update the correlation_target and persist features
        logging.debug(f"Renumbered FM fiducials on GUI start to contiguous indices 1..{len(fm_fiducials)}")
        self.correlation_target = update_feature_correlation_target(self.correlation_target, self._tab_data_model)
