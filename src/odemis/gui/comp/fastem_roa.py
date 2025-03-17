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
import threading
from typing import List, Tuple, Optional

import numpy
from scipy.ndimage import binary_fill_holes
from shapely.geometry import Polygon, box

from odemis import model
from odemis.gui.comp.overlay.base import Vec
from odemis.gui.comp.overlay.ellipse import EllipseOverlay
from odemis.gui.comp.overlay.polygon import PolygonOverlay
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.gui.model import CALIBRATION_2, CALIBRATION_3
from odemis.util.raster import get_polygon_grid_cells

# The threshold is used to check if the ROA bounding box is larger in size
ACQ_SIZE_THRESHOLD = 0.002  # 2 mm


class FastEMROA:
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
        self.shape = shape
        self.name = model.StringVA(name)
        self.slice_index = model.IntVA(slice_index)
        self._main_data = main_data
        self.roc_2 = model.VigilantAttribute(None)  # FastEMROC
        self.roc_3 = model.VigilantAttribute(None)  # FastEMROC
        self._asm = self._main_data.asm
        self._multibeam = self._main_data.multibeam
        self._descanner = self._main_data.descanner
        self._detector = self._main_data.mppc

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
            current_sample = self._main_data.current_sample.value
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
        :param points: list of nested points (x, y) representing the shape in physical coordinates.
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

    def estimate_acquisition_time(self, acq_dwell_time: Optional[float] = None):
        """
        Computes the approximate time it will take to run the ROA (megafield) acquisition.

        :param acq_dwell_time: (float or None) The acquisition dwell time.
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
