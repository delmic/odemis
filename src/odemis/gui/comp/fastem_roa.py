# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2024 Nandish Patel, Delmic

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
import math
import threading
from abc import ABCMeta, abstractmethod
from typing import List, Tuple

import numpy
from scipy.ndimage import binary_fill_holes
from shapely.geometry import Polygon, box

from odemis import model
from odemis.acq.fastem import STAGE_PRECISION
from odemis.gui.comp.overlay.base import Vec
from odemis.gui.comp.overlay.ellipse import EllipseOverlay
from odemis.gui.comp.overlay.polygon import PolygonOverlay
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.gui.model import CALIBRATION_2, CALIBRATION_3
from odemis.util.raster import get_polygon_grid_cells

# The threshold is used to check if the ROA/TOA bounding box is larger in size
ACQ_SIZE_THRESHOLD = 0.002  # 2 mm
# The limit is used to check if the TOA grid rects will be more
HFW_LIMIT = 0.0005  # 500 μm


class FastEMROABase(metaclass=ABCMeta):
    """
    Base class for FastEM ROA (region of acquisition) and FastEM TOA (tiled overview acquisition).
    """

    def __init__(self, shape, main_data, overlap=0.06, name="", slice_index=0):
        """
        :param shape: (EditableShape or None) The shape representing the region of acquisition in the canvas.
        :param main_data: (MainGUIData) The data corresponding to the entire GUI.
        :param overlap: (float), optional
            The amount of overlap required between single fields. An overlap of 0.2 means that two neighboring fields
            overlap by 20%. By default, the overlap is 0.06, this means there is 6% overlap between the fields.
        :param name: (str) Name of the region of acquisition (ROA).
        :param slice_index: (int) The slice index of the region of acquisition.
        """
        self.shape = shape
        self.name = model.StringVA(name)
        self.slice_index = model.IntVA(slice_index)
        self.main_data = main_data
        # Shape represented using shapely.geometry.Polygon
        self.polygon_shape = None
        # List of tuples(int, int) containing the position indices of each field to be acquired.
        # Automatically updated when the coordinates change.
        self.field_indices = []
        # List of tuples(Vec, Vec) containing the start and end position of rectangle representing a field to be acquired.
        # Calculated based on field_indices
        # Automatically updated when the coordinates change.
        self.field_rects: List[Tuple[Vec, Vec]] = []
        self.overlap = overlap

    @abstractmethod
    def calculate_field_indices(self):
        """
        Calculate and assign the field indices required to cover a polygon,
        considering overlap between cells. The field_indices attribute is updated
        and not returned by the function.
        """
        pass

    @abstractmethod
    def calculate_grid_rects(self):
        """
        Calculate the bounding rectangles for the grid cells that cover the polygon shape.
        The field_rects attribute is updated and not returned by the function. The
        field_indices attribute must be updated first using calculate_field_indices().
        """
        pass

    @abstractmethod
    def to_dict(self) -> dict:
        """
        Convert the necessary class attributes and its values to a dict.
        This method can be used to gather data for creating a json file.
        """
        pass

    @staticmethod
    @abstractmethod
    def from_dict(roa: dict, tab_data):
        """
        Use the dict keys and values to reconstruct the class from a json file.
        """
        pass

    @abstractmethod
    def estimate_acquisition_time(self, acq_dwell_time: float) -> float:
        """
        Computes the approximate time it will take to run the acquisition.
        """
        pass


class FastEMROA(FastEMROABase):
    """
    Representation of a FastEM ROA (region of acquisition).
    The region of acquisition is a megafield image, which consists of a sequence of single field images. Each single
    field image itself consists of cell images. The number of cell images is defined by the shape of the multiprobe
    and detector.
    """

    def __init__(self, shape, main_data, overlap=0.06, name="", slice_index=0):
        """
        :param shape: (EditableShape or None) The shape representing the region of acquisition in the canvas.
        :param main_data: (MainGUIData) The data corresponding to the entire GUI.
        :param overlap: (float), optional
            The amount of overlap required between single fields. An overlap of 0.2 means that two neighboring fields
            overlap by 20%. By default, the overlap is 0.06, this means there is 6% overlap between the fields.
        :param name: (str) Name of the region of acquisition (ROA). It is the stack_id of the megafield as stored on
                     the external storage.
        :param slice_index: (int) The slice index of the region of acquisition. It is the z_position of the megafield
                     as stored on the external storage.
        """
        super().__init__(shape, main_data, overlap, name, slice_index)
        self.roc_2 = model.VigilantAttribute(None)  # FastEMROC
        self.roc_3 = model.VigilantAttribute(None)  # FastEMROC
        self._asm = self.main_data.asm
        self._multibeam = self.main_data.multibeam
        self._descanner = self.main_data.descanner
        self._detector = self.main_data.mppc

        self.shape.points.subscribe(self.on_points, init=True)

    def to_dict(self) -> dict:
        """
        Convert the necessary class attributes and its values to a dict.
        This method can be used to gather data for creating a json file.
        """
        return {
            "name": self.name.value,
            "slice_index": self.slice_index.value,
            "overlap": self.overlap,
            "shape": self.shape.to_dict() if hasattr(self.shape, 'to_dict') else {},
        }

    @staticmethod
    def from_dict(roa: dict, tab_data):
        """
        Use the dict keys and values to reconstruct the class from a json file.

        :param roa: The dict containing the class attributes and its values as key value pairs.
                    to_dict() method must have been used previously to create this dict.
        :param tab_data: The data corresponding to a GUI tab helpful while reconstructing the class.
        :returns: (FastEMROA) reconstructed FastEMROA class.
        """
        name = roa["name"]
        slice_index  = int(roa["slice_index"])
        overlap = float(roa["overlap"])
        shape_data = roa["shape"]
        shape_type = shape_data["type"]
        if shape_type == RectangleOverlay.__name__:
            shape = RectangleOverlay.from_dict(shape_data, tab_data)
        elif shape_type == EllipseOverlay.__name__:
            shape = EllipseOverlay.from_dict(shape_data, tab_data)
        elif shape_type == PolygonOverlay.__name__:
            shape = PolygonOverlay.from_dict(shape_data, tab_data)
        else:
            raise ValueError("Unknown shape type.")
        roa = FastEMROA(shape, tab_data.main, overlap=overlap, name=name, slice_index=slice_index)
        return roa

    def update_roc(self):
        """Update the ROC 2 and 3 values based on ROA shape's position."""
        if self.shape:
            posx, posy = self.shape.get_position()
            current_sample = self.main_data.current_sample.value
            if current_sample:
                scintillator = current_sample.find_closest_scintillator((posx, posy))
                if scintillator:
                    self.roc_2.value = scintillator.calibrations[CALIBRATION_2].region
                    self.roc_3.value = scintillator.calibrations[CALIBRATION_3].region
                else:
                    self.roc_2.value = None
                    self.roc_2.value = None

    def on_points(self, points):
        """Recalculate the field indices and rectangles when the points of the region of acquisition (ROA) have changed
        (e.g. resize, moving). Also assign the region of calibration (ROC) 2 and 3.
        :param points: (List[Tuple[float, float]]) list of points (x, y) representing the shape in physical coordinates.
        """
        if points:
            # Update the ROC 2 and 3 values
            self.update_roc()
            # Update the polygon shape
            self.polygon_shape = Polygon(points)
            xmin, ymin, xmax, ymax = self.polygon_shape.bounds
            # If the ROA bounding box is larger in size, use threading so that the drawing operations are not affected
            if abs(xmax - xmin) >= ACQ_SIZE_THRESHOLD or abs(ymax - ymin) >= ACQ_SIZE_THRESHOLD:
                thread = threading.Thread(target=self.calculate_field_indices)
                thread.daemon = True
                thread.start()
            else:
                self.calculate_field_indices()

    def estimate_acquisition_time(self, acq_dwell_time: float) -> float:
        """
        Computes the approximate time it will take to run the ROA (megafield) acquisition.

        :param acq_dwell_time: (float) The acquisition dwell time.
        :return (0 <= float): The estimated time for the ROA (megafield) acquisition in s.
        """
        field_time = self._detector.getTotalFieldScanTime(acq_dwell_time) + 1.5  # there is about 1.5 seconds overhead per field
        tot_time = (len(self.field_indices) + 1) * field_time  # +1 because the first field is acquired twice

        return tot_time

    def calculate_field_indices(self):
        """
        Calculate and assign the field indices required to cover a polygon,
        considering overlap between cells. The field_indices attribute is updated
        and not returned by the function.
        :raises ValueError: If the polygon shape is not defined.
        """
        if self.polygon_shape is None:
            return
        # Bounding box of the polygon and its exterior points
        xmin, ymin, xmax, ymax = self.polygon_shape.bounds
        points = numpy.array(self.polygon_shape.exterior.coords)

        # Define grid cell size based on multibeam resolution and pixel size
        px_size = self._multibeam.pixelSize.value
        field_res = self._multibeam.resolution.value
        field_size = (field_res[0] * px_size[0], field_res[1] * px_size[1])

        # Calculate grid dimensions considering overlap
        r_grid_width = field_size[1] * (1 - self.overlap)
        c_grid_width = field_size[0] * (1 - self.overlap)
        grid_width = int(numpy.ceil((xmax - xmin) / c_grid_width))
        grid_height = int(numpy.ceil((ymax - ymin) / r_grid_width))
        megafield_grid = numpy.zeros((grid_height, grid_width), dtype=bool)  # row, col

        # Vectorized conversion of points to grid coordinates
        rows = numpy.floor((ymax - points[:, 1]) / r_grid_width).astype(int)
        cols = numpy.floor((points[:, 0] - xmin) / c_grid_width).astype(int)

        # Create array of (row, col) vertices
        polygon_vertices = numpy.stack((rows, cols), axis=1)

        intersected_fields = get_polygon_grid_cells(polygon_vertices, include_neighbours=True)

        for row, col in intersected_fields:
            if 0 <= row < grid_height and 0 <= col < grid_width:
                # Define the bounds of the current field
                field = box(
                    xmin + col * c_grid_width,
                    ymax - (row + 1) * r_grid_width,
                    xmin + (col + 1) * c_grid_width,
                    ymax - row * r_grid_width
                )
                # Check if the field intersects with the polygon shape
                if self.polygon_shape.intersects(field):
                    megafield_grid[row, col] = True

        # Fill holes in the grid to get a contiguous fields
        indices_array = binary_fill_holes(megafield_grid)
        rows, cols = numpy.nonzero(indices_array)
        indices_list = list(zip(cols.tolist(), rows.tolist()))

        # Assign the calculated field indices
        self.field_indices = indices_list

    def calculate_grid_rects(self):
        """
        Calculate the bounding rectangles for the grid cells that cover the polygon shape.
        The field_rects attribute is updated and not returned by the function. The
        field_indices attribute must be updated first using calculate_field_indices().
        """
        if self.polygon_shape is None:
            return
        # Extract bounding box coordinates from the polygon shape
        rects = []
        xmin, _, _, ymax = self.polygon_shape.bounds

        # Define grid cell size based on multibeam resolution and pixel size
        px_size = self._multibeam.pixelSize.value
        field_res = self._multibeam.resolution.value
        field_size = (field_res[0] * px_size[0], field_res[1] * px_size[1])

        # Calculate grid dimensions considering overlap
        r_grid_width = field_size[1] * (1 - self.overlap)
        c_grid_width = field_size[0] * (1 - self.overlap)

        # Iterate through each field index to compute the bounding rectangles
        for col, row in self.field_indices:
            start_pos_x = xmin + col * c_grid_width
            start_pos_y = ymax - (row + 1) * r_grid_width
            end_pos_x = start_pos_x + field_size[0]
            end_pos_y = start_pos_y + field_size[1]
            p_start_pos = Vec(start_pos_x, start_pos_y)
            p_end_pos = Vec(end_pos_x, end_pos_y)
            rects.append((p_start_pos, p_end_pos))

        # Assign the calculated field rectangles
        self.field_rects = rects


class FastEMTOA(FastEMROABase):
    """
    Representation of a FastEM TOA (tiled overview acquisition).
    The tiled overview acquisition is an overview image, which consists of a sequence of single-beam field images.
    """

    def __init__(self, shape, main_data, hfw, res, name="", slice_index=0):
        """
        :param shape: (EditableShape or None) The shape representing the region of acquisition in the canvas.
        :param main_data: (MainGUIData) The data corresponding to the entire GUI.
        :param hfw: (float) The horizontal field width of the tiled overview acquisition (TOA) in m.
        :param res: (tuple) The resolution of the TOA in pixels.
        :param name: (str) Name of the TOA.
        :param slice_index: (int) The slice index of the tiled overview acquisition.
        """
        overlap = STAGE_PRECISION / hfw
        super().__init__(shape, main_data, overlap, name, slice_index)
        self._area_size = (0, 0)
        self._xmin = None
        self._ymax = None
        self.hfw = model.FloatVA(hfw)
        self.res = model.TupleVA(tuple(res))

        self._stage = self.main_data.stage
        self._emitter = self.main_data.ebeam
        md_emt = self._emitter.getMetadata()
        self._detector = self.main_data.mppc
        md_det = self._detector.getMetadata()
        self._pxs_cor = md_det.get(model.MD_PIXEL_SIZE_COR, md_emt.get(model.MD_PIXEL_SIZE_COR, (1, 1)))
        self._fov = (hfw * self._pxs_cor[0], hfw * self._pxs_cor[1] * res[1] / res[0])
        self.shape.points.subscribe(self.on_points, init=True)
        self.hfw.subscribe(self._on_hfw)
        self.res.subscribe(self._on_res)

    def to_dict(self) -> dict:
        """
        Convert the necessary class attributes and its values to a dict.
        This method can be used to gather data for creating a json file.
        """
        return {
            "name": self.name.value,
            "slice_index": self.slice_index.value,
            "hfw": self.hfw.value,
            "resolution": self.res.value,
            "shape": self.shape.to_dict() if hasattr(self.shape, 'to_dict') else {},
        }

    @staticmethod
    def from_dict(toa: dict, tab_data):
        """
        Use the dict keys and values to reconstruct the class from a json file.

        :param roa: The dict containing the class attributes and its values as key value pairs.
                    to_dict() method must have been used previously to create this dict.
        :param tab_data: The data corresponding to a GUI tab helpful while reconstructing the class.
        :returns: (FastEMTOA) reconstructed FastEMTOA class.
        """
        name = toa["name"]
        slice_index  = int(toa["slice_index"])
        hfw = toa["hfw"]
        res = toa["resolution"]
        shape_data = toa["shape"]
        shape_type = shape_data["type"]
        if shape_type == RectangleOverlay.__name__:
            shape = RectangleOverlay.from_dict(shape_data, tab_data)
        elif shape_type == EllipseOverlay.__name__:
            shape = EllipseOverlay.from_dict(shape_data, tab_data)
        elif shape_type == PolygonOverlay.__name__:
            shape = PolygonOverlay.from_dict(shape_data, tab_data)
        else:
            raise ValueError("Unknown shape type.")
        toa = FastEMTOA(shape, tab_data.main, hfw=hfw, res=res, name=name, slice_index=slice_index)
        return toa

    def on_points(self, points):
        """Recalculate the field indices and rectangles when the points of the TOA have changed
        (e.g. resize, moving).
        :param points: (List[Tuple[float, float]]) list of points (x, y) representing the shape in physical coordinates.
        """
        if points:
            # Update the polygon shape
            self.polygon_shape = Polygon(points)
            xmin, ymin, xmax, ymax = self.polygon_shape.bounds
            self._area_size = (xmax - xmin, ymax - ymin)
            # If the TOA bounding box is larger in size and the HFW is smaller than the limit,
            # use threading so that the drawing operations are not affected
            if (
                (abs(self._area_size[0]) >= ACQ_SIZE_THRESHOLD or abs(self._area_size[1]) >= ACQ_SIZE_THRESHOLD)
                and self.hfw.value <= HFW_LIMIT
            ):
                thread = threading.Thread(target=self.calculate_field_indices)
                thread.daemon = True
                thread.start()
            else:
                self.calculate_field_indices()

    def _on_hfw(self, hfw):
        """Callback function on HFW change."""
        # Update the overlap based on the new HFW value
        self.overlap = STAGE_PRECISION / hfw
        # Update the field of view (FoV) based on the new HFW value
        self._fov = (hfw * self._pxs_cor[0], hfw * self._pxs_cor[1] * self.res.value[1] / self.res.value[0])

    def _on_res(self, res):
        """Callback function on resolution change."""
        # Update the field of view (FoV) based on the new resolution value
        self._fov = (self.hfw.value * self._pxs_cor[0], self.hfw.value * self._pxs_cor[1] * res[1] / res[0])

    def estimate_acquisition_time(self, acq_dwell_time: float) -> float:
        """
        Computes the approximate time it will take to run the TOA acquisition.

        :param acq_dwell_time: (float) The acquisition dwell time.
        :return (0 <= float): The estimated time for the TOA acquisition in s.
        """
        def count_stage_moves(field_indices):
            """Counts horizontal and vertical moves based on tile order."""
            indices = numpy.array(field_indices)

            # Compute differences between consecutive tiles
            diffs = numpy.diff(indices, axis=0)

            # Count horizontal and vertical moves
            horizontal_moves = numpy.count_nonzero(diffs[:, 0])  # Count nonzero x-differences
            vertical_moves = numpy.count_nonzero(diffs[:, 1])    # Count nonzero y-differences

            return horizontal_moves, vertical_moves

        num_tiles = len(self.field_indices)  # Total number of tiles

        # Return if there are no tiles to acquire
        if num_tiles == 0:
            return 0.0

        # Time for tile acquisition
        acq_time_tile = self.res.value[0] * self.res.value[1] * acq_dwell_time

        # Total acquisition time for imaging (all tiles)
        # add 2s to account for switching from one tile to next tile
        # this time is added in TiledAcquisitionTask.estimateTime
        acq_time = num_tiles * (acq_time_tile + 2)

        # Stage movement time calculations
        stage_speed_x = self._stage.speed.value['x']  # Speed of stage in x-direction [m/s]
        stage_speed_y = self._stage.speed.value['y']  # Speed of stage in y-direction [m/s]

        # Count horizontal and vertical moves
        horizontal_moves, vertical_moves = count_stage_moves(self.field_indices)

        # Horizontal movement: Total time for moving across rows
        time_x = (horizontal_moves * self._fov[0]) / stage_speed_x

        # Vertical movement: Time for repositioning to the next row
        time_y = (vertical_moves * self._fov[1]) / stage_speed_y

        # Total stage movement time
        stage_time = time_x + time_y

        # The stage movement precision is quite good (just a few pixels). The stage's
        # position reading is much better, and we can assume it's below a pixel.
        # So as long as we are sure there is some overlap, the tiles will be positioned
        # correctly and without gap.
        # Estimate stitching time based on number of pixels in the overlapping part
        max_pxs = self.res.value[0] * self.res.value[1]
        stitch_time = (num_tiles * max_pxs * self.overlap) / 1e8  # 1e8 is stitching speed

        # Combine imaging time, stage movement time and stitch time
        total_time = acq_time + stage_time + stitch_time

        return total_time

    def calculate_field_indices(self):
        """
        Calculate and assign the field indices required to cover a polygon,
        considering overlap between cells. The field_indices attribute is updated
        and not returned by the function.
        :raises ValueError: If the polygon shape is not defined.
        """
        if self.polygon_shape is None:
            raise ValueError("Polygon shape is not defined.")

        # The size of the smallest tile, non-including the overlap, which will be
        # lost (and also indirectly represents the precision of the stage)
        reliable_fov = ((1 - self.overlap) * self._fov[0], (1 - self.overlap) * self._fov[1])

        # Round up the number of tiles needed. With a twist: if we'd need less
        # than 1% of a tile extra, round down. This handles floating point
        # errors and other manual rounding when when the requested area size is
        # exactly a multiple of the FoV.
        area_size = [(s - f * 0.01) if s > f else s
                     for s, f in zip(self._area_size, reliable_fov)]
        nx = math.ceil(area_size[0] / reliable_fov[0])
        ny = math.ceil(area_size[1] / reliable_fov[1])

        # We have a little bit more tiles than needed, we then have two choices
        # on how to spread them:
        # 1. Increase the total area acquired (and keep the overlap)
        # 2. Increase the overlap (and keep the total area)
        # We pick alternative 1 (no real reason)
        xmin, ymin, xmax, ymax = self.polygon_shape.bounds
        center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        total_size = (
            nx * reliable_fov[0] + self._fov[0] * self.overlap,
            ny * reliable_fov[1] + self._fov[1] * self.overlap,
        )
        xmin = center[0] - total_size[0] / 2
        ymax = center[1] + total_size[1] / 2
        self._xmin = xmin
        self._ymax = ymax

        # Create an empty grid for storing intersected tiles
        # An intersected tile is any tile in the grid that intersects with (or falls within) the given polygon
        tile_grid = numpy.zeros((ny, nx), dtype=bool)

        # Vectorized conversion of polygon points to grid coordinates
        points = numpy.array(self.polygon_shape.exterior.coords)
        rows = numpy.floor((ymax - points[:, 1]) / reliable_fov[1]).astype(int)
        cols = numpy.floor((points[:, 0] - xmin) / reliable_fov[0]).astype(int)

        # Create array of (row, col) vertices
        polygon_vertices = numpy.stack((rows, cols), axis=1)

        intersected_tiles = get_polygon_grid_cells(polygon_vertices, include_neighbours=True)

        for row, col in intersected_tiles:
            if 0 <= row < ny and 0 <= col < nx:
                # Define the bounds of the current tile
                tile_bounds = box(
                    xmin + col * reliable_fov[0],
                    ymax - (row + 1) * reliable_fov[1],
                    xmin + (col + 1) * reliable_fov[0],
                    ymax - row * reliable_fov[1]
                )
                # Check if the tile intersects with the polygon shape
                if self.polygon_shape.intersects(tile_bounds):
                    tile_grid[row, col] = True

        # Fill any holes in the grid to get contiguous tiles
        filled_grid = binary_fill_holes(tile_grid)
        rows, cols = numpy.nonzero(filled_grid)
        tile_indices = list(zip(cols.tolist(), rows.tolist()))
        self.field_indices = tile_indices

    def calculate_grid_rects(self):
        """
        Calculate the bounding rectangles for the grid cells that cover the polygon shape.
        The field_rects attribute is updated and not returned by the function. The
        field_indices attribute must be updated first using calculate_field_indices().
        """
        if self._xmin is None or self._ymax is None:
            return
        # Extract bounding box coordinates from the polygon shape
        rects = []

        # Calculate grid dimensions considering overlap
        r_grid_width = self._fov[1] * (1 - self.overlap)
        c_grid_width = self._fov[0] * (1 - self.overlap)

        # Iterate through each field index to compute the bounding rectangles
        for col, row in self.field_indices:
            start_pos_x = self._xmin + col * c_grid_width
            start_pos_y = self._ymax - (row + 1) * r_grid_width
            end_pos_x = start_pos_x + self._fov[0]
            end_pos_y = start_pos_y + self._fov[1]
            p_start_pos = Vec(start_pos_x, start_pos_y)
            p_end_pos = Vec(end_pos_x, end_pos_y)
            rects.append((p_start_pos, p_end_pos))

        # Assign the calculated field rectangles
        self.field_rects = rects
