# -*- coding: utf-8 -*-
"""
Building blocks of the tiled acquisition (see TiledAcquisitionTask in _tiledacq.py).

The task owns the acquisition loop and the future. Everything else is delegated to:
 * GridPlan: which tiles to visit, in which order, and how to move to them
 * TileHook subclasses: per-tile behaviours run before/after each acquisition
   (focus map, FoV check, autofocus)
 * TileAcquirerBase: acquisition of the data of one tile
 * TileSaver: debug saving of the tiles
 * ProgressEstimator: time and memory estimates, and timing statistics
 * Stitcher: registration, weaving and stitching of tiles

@author: Nandish Patel

Copyright © 2026 Delmic B.V.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import copy
import logging
import math
import os
import statistics
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import CancelledError, ThreadPoolExecutor, TimeoutError
from concurrent.futures._base import CANCELLED, PENDING
from enum import Enum
from itertools import groupby
from typing import Dict, List, Optional, Tuple, Union

import numpy
import psutil
from scipy.ndimage import binary_fill_holes
from scipy.spatial import Delaunay
from shapely.geometry import Polygon, box

from odemis import dataio, model
from odemis.acq import acqmng
from odemis.acq.align.autofocus import MTD_EXHAUSTIVE, AutoFocus
from odemis.acq.stitching._constants import REGISTER_IDENTITY
from odemis.acq.stitching._simple import register, weave
from odemis.acq.stream import (
    ARStream,
    CLStream,
    EMStream,
    FluoStream,
    MonochromatorSettingsStream,
    MultipleDetectorStream,
    SpectrumStream,
    Stream,
    util,
)
from odemis.model import DataArray
from odemis.util import dataio as udataio
from odemis.util import img, linalg
from odemis.util.focus import MeasureOpticalFocus
from odemis.util.img import assembleZCube
from odemis.util.raster import get_polygon_grid_cells, point_in_polygon

# TODO: Find a value that works fine with common cases
# Ratio of the allowed difference of tile focus from good focus
FOCUS_FIDELITY = 0.3
# Limit focus range, half the margin will be used on each side of initial focus
FOCUS_RANGE_MARGIN = 100e-6  # m
# Indicate the number of tiles to skip during focus adjustment
SKIP_TILES = 3
# Starting index for the tile acquisition
# Start with a non-existing tile index so that the first tile is always moved to and acquired
START_INDEX = (-1, -1)

MOVE_SPEED_DEFAULT = 100e-6  # m/s
STITCH_SPEED = 1e8  # px/s


class FocusingMethod(Enum):
    NONE = 0  # Never auto-focus
    ALWAYS = 1  # Before every tile
    # If the previous tile focus level is too far from the original level
    ON_LOW_FOCUS_LEVEL = 2
    # Acquisition is done at several zlevels, and they are merged to obtain a focused image
    MAX_INTENSITY_PROJECTION = 3


def get_da_fov(da: model.DataArray) -> Tuple[float, float]:
    """
    :returns: width, height in m, of the actual FoV of the data
    """
    im_bbox = img.getBoundingBox(da)
    logging.debug("Bounding box of stream data: %s", im_bbox)
    return im_bbox[2] - im_bbox[0], im_bbox[3] - im_bbox[1]


def get_fov(s: Stream) -> Tuple[float, float]:
    """Get the field of view of a stream"""
    try:
        return s.guessFoV()
    except (NotImplementedError, AttributeError):
        raise TypeError("Unsupported Stream %s, it doesn't have a .guessFoV()" % (s,))


def guess_smallest_fov(streams: List[Stream]) -> Tuple[float, float]:
    """
    :returns: smallest width and smallest height of all the FoV
      Note: they are not necessarily from the same FoV.
    :raises ValueError: If no stream selected
    """
    fovs = [get_fov(s) for s in streams]
    if not fovs:
        raise ValueError("No stream so no FoV, so no minimum one")

    return (min(f[0] for f in fovs),
            min(f[1] for f in fovs))


class TaskContext:
    """Tile acquisition task context, providing cancellation and sub-future management."""

    def __init__(self, future: Optional[model.ProgressiveFuture]):
        """
        :param future: the future of the task, or None for estimation only.
          It is prepared to run the task.
        """
        self.future = future
        if future is not None:
            future.running_subf = model.InstantaneousFuture()
            future._task_lock = threading.Lock()
            future._task_state = PENDING

    def check_cancelled(self) -> None:
        """
        :raises CancelledError: if the task has been cancelled
        """
        if self.future._task_state == CANCELLED:
            raise CancelledError()

    def set_subf(self, subf: model.ProgressiveFuture) -> None:
        """Register the future which must be cancelled if the task is cancelled"""
        self.future.running_subf = subf


class Tile:
    """Represents a single tile in the acquisition grid."""

    def __init__(self, number: int, index: Tuple[int, int], zlevels: Optional[List[float]] = None):
        """
        :param number: the acquisition order of the tile, starting from 0
        :param index: the (col, row) index of the tile in the grid
        :param zlevels: the Z-levels to use for this tile. None means the acquirer's default ones
        """
        self.number = number  # order of acquisition, starting from 0
        self.index = index  # (col, row) in the grid
        self.zlevels = zlevels  # Z-levels to use for this tile. None means the acquirer's default ones


class TileHook:
    """
    Behaviour run around the acquisition of every tile. Hooks are run in order.
    """

    def before(self, tile: Tile, ctx: TaskContext) -> None:
        """Called once the stage is on the tile, before acquiring"""

    def after(self, tile: Tile, das: list, ctx: TaskContext) -> list:
        """
        Called after acquiring.
        :returns: the data of the tile, possibly replaced by a better version
        """
        return das


class GridPlan:
    """
    Tiles covering a region on a regular grid, visited in a zigzag order.
    """

    def __init__(self, region: Union[Tuple[float, float, float, float], List[Tuple[float, float]]],
                 overlap: float, sfov: Tuple[float, float], stage: model.Actuator, centered_acq: bool = True):
        """
        :param region: Either a bounding box (xmin, ymin, xmax, ymax), or a list of (x, y) points
          defining a polygon.
        :param overlap: the amount of overlap between each tile.
        :param sfov: the smallest field of view of the streams (in m). It can be updated
          once the actual FoV is known.
        :param stage: the sample stage.
        :param centered_acq: If True, the extra area is added symmetrically on all sides of the
          region. If False, the top-left of the grid is aligned with the top-left of the region.
        """
        self._polygon = self.convert_region_to_polygon(region)
        xmin, ymin, xmax, ymax = self._polygon.bounds
        self._area_size = (xmax - xmin, ymax - ymin)
        self._overlap = overlap
        self.sfov = sfov
        self._centered_acq = centered_acq
        self._stage = stage

        # Rough estimate of the stage movement speed, for estimating the extra
        # duration due to movements
        self.move_speed = MOVE_SPEED_DEFAULT
        if model.hasVA(stage, "speed"):
            try:
                self.move_speed = (stage.speed.value["x"] + stage.speed.value["y"]) / 2
            except Exception as ex:
                logging.warning("Failed to read the stage speed: %s", ex)

        self._starting_pos, self._tile_indices = self._get_tile_coverage()
        self.number_of_tiles = len(self._tile_indices)
        logging.debug("Calculated number of tiles: %s", self.number_of_tiles)

    @property
    def overlap(self) -> float:
        return self._overlap

    @staticmethod
    def convert_region_to_polygon(
            region: Union[Tuple[float, float, float, float], List[Tuple[float, float]]]
        ) -> Polygon:
        """
        Convert the region input into a polygon.

        :param region: Either a bounding box (xmin, ymin, xmax, ymax) or a list of (x, y) points.
        :returns: A polygon representing the region.
        :raises: ValueError if the region does not form a valid polygon.
        :raises: ValueError if the region is not a bounding box or a list of points.
        """
        if isinstance(region, tuple) and len(region) == 4:
            # Normalize the bounding box to ensure the order is correct
            xmin, ymin, xmax, ymax = util.sanitize_rect(region)
            if region[0] != xmin or region[1] != ymin:
                logging.warning("Acquisition area %s rearranged into %s", region, (xmin, ymin, xmax, ymax))
            points = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
        elif isinstance(region, list) and all(isinstance(point, tuple) and len(point) == 2 for point in region):
            points = region
        else:
            raise ValueError(
                "Region must either be a bounding box (xmin, ymin, xmax, ymax) or a list of (x, y) points."
            )

        polygon = Polygon(points)
        if not polygon.is_valid:
            raise ValueError("The provided region does not form a valid polygon.")
        return polygon

    def _get_tile_coverage(self) -> Tuple[Dict[str, float], List[Tuple[int, int]]]:
        """
        Calculate the exact tiles required to cover the region.

        :returns:
            starting_position: the center of the first tile (at the top-left)
            tile_indices: a list of (col, row) indices for the tiles
        """
        # The size of the smallest tile, non-including the overlap, which will be
        # lost (and also indirectly represents the precision of the stage)
        reliable_fov = ((1 - self._overlap) * self.sfov[0], (1 - self._overlap) * self.sfov[1])

        # Round up the number of tiles needed. With a twist: if we'd need less
        # than 1% of a tile extra, round down. This handles floating point
        # errors and other manual rounding when the requested area size is
        # exactly a multiple of the FoV.
        area_size = [(s - f * 0.01) if s > f else s
                     for s, f in zip(self._area_size, reliable_fov)]
        nx = math.ceil(area_size[0] / reliable_fov[0])
        ny = math.ceil(area_size[1] / reliable_fov[1])

        # If we have a little bit more tiles than needed, we have two choices
        # on how to spread them:
        # 1. Increase the total area acquired (and keep the overlap)
        # 2. Increase the overlap (and keep the total area)
        # We pick alternative 1 (no real reason)
        xmin, ymin, xmax, ymax = self._polygon.bounds

        if self._centered_acq:
            # Ensure extra acquisition area is symmetric around the center of the acquisition
            center = ((xmin + xmax) / 2, (ymin + ymax) / 2)
            total_size = (
                nx * reliable_fov[0] + self.sfov[0] * self._overlap,
                ny * reliable_fov[1] + self.sfov[1] * self._overlap,
            )
            xmin = center[0] - total_size[0] / 2
            ymax = center[1] + total_size[1] / 2

        # Create an empty grid for storing intersected tiles
        # An intersected tile is any tile in the grid that intersects with (or falls within) the given polygon
        tile_grid = numpy.zeros((ny, nx), dtype=bool)

        # Vectorized conversion of polygon points to grid coordinates
        points = numpy.array(self._polygon.exterior.coords)
        rows = numpy.floor((ymax - points[:, 1]) / reliable_fov[1]).astype(int)
        cols = numpy.floor((points[:, 0] - xmin) / reliable_fov[0]).astype(int)

        # Create array of (row, col) vertices
        polygon_vertices = numpy.stack((rows, cols), axis=1)

        intersected_tiles = get_polygon_grid_cells(polygon_vertices, include_neighbours=True)

        # The intersected tiles contains tiles which can be inside the polygon, outside the polygon and tiles which
        # actually intersect with the polygon. We only need the tiles which actually intersect the polygon, to binary
        # fill the holes and get the tile indices.
        for row, col in intersected_tiles:
            if not (0 <= row < ny and 0 <= col < nx):  # Certainly out of the polygon => not worthy to acquire
                continue
            # Define the bounds of the current tile
            tile_bounds = box(
                xmin + col * reliable_fov[0],
                ymax - (row + 1) * reliable_fov[1],
                xmin + (col + 1) * reliable_fov[0],
                ymax - row * reliable_fov[1]
            )
            # Check if the tile intersects with the polygon shape
            if self._polygon.intersects(tile_bounds):
                tile_grid[row, col] = True

        # Fill any holes in the grid to get contiguous tiles
        filled_grid = binary_fill_holes(tile_grid)
        rows, cols = numpy.nonzero(filled_grid)
        tile_indices = list(zip(cols.tolist(), rows.tolist()))

        # Calculate the starting position (top-left of the grid)
        if self._centered_acq:
            starting_position = {
                "x": xmin + reliable_fov[0] / 2,
                "y": ymax - reliable_fov[1] / 2
            }
        else:
            starting_position = {
                "x": xmin + self.sfov[0] / 2,
                "y": ymax - self.sfov[1] / 2
            }

        return starting_position, tile_indices

    @staticmethod
    def sort_tile_indices_zigzag(tile_indices: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """
        Sort the given tile indices in a zigzag scanning order.
        # Below is an example for a 5x3 tile grid:
        # A-->-->-->--v
        #             |
        # v--<--<--<---
        # |
        # --->-->-->--Z

        :param tile_indices: List of tuples representing the tile indices
        :returns: List of tuples sorted in a zigzag scanning order
        """
        # Sort tile_indices by row first, then column
        tile_indices.sort(key=lambda t: (t[1], t[0]))

        # Group indices by rows and apply zigzag pattern
        sorted_indices = []
        for row, group in groupby(tile_indices, key=lambda t: t[1]):
            group = list(group)
            # Reverse the order of the group if the row is odd
            if row % 2 == 1:
                group.reverse()
            sorted_indices.extend(group)

        return sorted_indices

    def tiles(self) -> List[Tile]:
        """
        :returns: the tiles, in the order in which they should be acquired
        """
        return [Tile(i, idx) for i, idx in enumerate(self.sort_tile_indices_zigzag(self._tile_indices))]

    def move_to(self, idx: Tuple[int, int], prev_idx: Tuple[int, int], ctx: TaskContext) -> None:
        """
        Move the stage to the tile position
        :param idx: index of the tile to move to
        :param prev_idx: index of the previous tile (START_INDEX if none)
        :raises OSError: if the stage failed to move after several trials
        """
        tile_size = self.sfov
        overlap = 1 - self._overlap
        # don't move on the axis that is not supposed to have changed
        m = {}
        idx_change = numpy.subtract(idx, prev_idx)
        if idx[0] != prev_idx[0]:  # x-axis changed
            m["x"] = self._starting_pos["x"] + idx[0] * tile_size[0] * overlap
        if idx[1] != prev_idx[1]:  # y-axis changed
            m["y"] = self._starting_pos["y"] - idx[1] * tile_size[1] * overlap

        # Compute the time to wait
        if prev_idx == START_INDEX:
            # If this is the first tile, wait for a long time to allow the stage to move
            # This is needed because the current stage position may be far from the first tile
            # so it may take a long time to move there
            timeout = 100  # s
        else:
            # For any tile after the first, don't wait forever for the stage to move,
            # guess the time it should take and then give a large margin
            t = math.hypot(abs(idx_change[0]) * tile_size[0] * overlap,
                           abs(idx_change[1]) * tile_size[1] * overlap) / self.move_speed
            timeout = 5 * t + 3  # s

        logging.debug("Moving to tile %s at %s m", idx, m)
        for i in range(3):  # Try moving up to 3 times
            ctx.check_cancelled()
            subf = self._stage.moveAbs(m)
            ctx.set_subf(subf)
            try:
                subf.result(timeout)
            except ValueError:  # Typically, asked to move to the wrong place, let's give up
                raise
            except CancelledError:
                logging.warning("Move to tile %s cancelled", idx)
                raise
            except TimeoutError:
                subf.cancel()
                pos = self._stage.position.value
                pos_xy = (pos["x"], pos["y"])
                logging.warning("Failed to move to tile %s within %s s, now at %s%s",
                                idx, timeout, pos_xy, ", will retry" if i < 2 else "")
                time.sleep(0.1)
                continue
            except Exception as ex:
                pos = self._stage.position.value
                pos_xy = (pos["x"], pos["y"])
                logging.warning("Failed to move to tile %s, now at %s: %s%s",
                                idx, pos_xy, ex, ", will retry" if i < 2 else "")
                time.sleep(0.1)
                continue

            # The moved finished successfully, just stop trying to move, we are done!
            break
        else:
            # We exhausted all retries, we failed to move to the tile, so we cancel the acquisition
            logging.error("Failed to move to tile %s after 3 trials", idx)
            raise OSError(f"Failed to move to tile {idx} after 3 trials")


class ZMap(ABC):
    """
    Abstract base class for a Z map.
    Provides the interface for obtaining the z position at a given (x, y) coordinate.
    """

    @abstractmethod
    def z_at(self, x: float, y: float) -> float:
        """Return the z position at the given (x, y) coordinate."""
        pass


class FocusMap(ZMap):
    """Interpolates the z position of the good focus, from known (x, y, z) focus points."""

    def __init__(self, focus_points: List[List[float]]):
        """
        :param focus_points: list of (x, y, z) at good focus. Either 1 or at least 3 points.
        :raises ValueError: if the number of points is not supported
        """
        self.points = numpy.array(focus_points)
        self._tri = None
        if len(self.points) >= 3:
            self._tri = Delaunay(self.points[:, :2])
        elif len(self.points) != 1:
            # Triangulation needs minimum three points to define a plane.
            # With a single point, the focus is constant.
            raise ValueError(f"focus_points length {len(self.points)} is not supported")
        self.plane: Dict[str, object] = {}

    def z_at(self, x: float, y: float) -> float:
        if self._tri is None:
            return self.points[0][2]
        return self._triangulated_z(x, y)

    def _get_z_on_plane(self, x: float, y: float) -> float:
        """z of the (x, y) point, on the plane fitted through all the focus points"""
        if not self.plane:
            gamma, normal = linalg.fit_plane_lstsq(self.points)
            self.plane["gamma"] = gamma
            self.plane["normal"] = normal
        point_on_plane = (0, 0, self.plane["gamma"])  # where the plane intersects with the z-axis
        return linalg.get_z_pos_on_plane(x, y, point_on_plane, self.plane["normal"])

    def _triangulated_z(self, x: float, y: float) -> float:
        """Get the z position of the (x, y) point in the corresponding focus-triangle."""
        point_in_triangle = False
        # Check in 2D if the point is in one of the triangles from the focus points
        for simplex in self._tri.simplices:
            triangle = [self.points[:, :2][simplex[0]],
                        self.points[:, :2][simplex[1]],
                        self.points[:, :2][simplex[2]]]
            point_in_triangle = point_in_polygon([x, y], polygon=triangle)
            if point_in_triangle:
                break

        if point_in_triangle is False:
            # TODO determine a better way for dealing with points outside of the focused area, for instance
            #  by using linear extrapolation.
            logging.debug("Acquiring tile outside of focused area, will use plane fitting to find the focus.")
            # If the point is not in one of the triangles fit a plane through all
            # focus points and base the z position on that plane fit.
            return self._get_z_on_plane(x, y)

        tr = [self.points[simplex[0]],
              self.points[simplex[1]],
              self.points[simplex[2]]]
        return linalg.get_point_on_plane(x, y, tr)


class FocusMapHook(TileHook):
    """Sets the z-levels of each tile, based on the focus map at the stage position."""

    def __init__(self, focus_map: FocusMap, stage: model.Actuator, focuser: model.Actuator, zlevels: List[float],
                 focus_range: Optional[Tuple[float, float]] = None):
        """
        :param init_zlevels: z-levels (can be relative) of the z-stack requested
        :param focus_range: range allowed for the z-levels. If None, the focuser range is used.
        """
        self._focus_map = focus_map
        self._stage = stage
        self._focuser = focuser
        self._init_zlevels = numpy.asarray(zlevels)
        self._focus_range = focus_range

    def before(self, tile: Tile, ctx: TaskContext) -> None:
        current_pos = self._stage.position.value
        z = self._focus_map.z_at(current_pos["x"], current_pos["y"])
        logging.info(f"Found z focus: {z}")
        tile.zlevels = self.get_zstack_levels(z)

    def get_zstack_levels(self, focus_value: float) -> List[float]:
        """
        Calculate the zstack levels from the focus position and the requested z-levels
        :param focus_value: the focus position to base the z-stack levels on
        :returns: zstack levels for zstack acquisition (just the focus value if no z-stack)
        """
        # When initial z level is one or None, use the current focus value
        if len(self._init_zlevels) <= 1:
            return [focus_value, ]

        zlevels = self._init_zlevels + focus_value

        # Check which focus range is available
        if self._focus_range is not None:
            comp_range = self._focus_range
        else:
            comp_range = self._focuser.axes['z'].range

        # The number of zlevels will remain the same, but the range will be adjusted
        zmin = min(zlevels)
        zmax = max(zlevels)
        if (zmax - zmin) > (comp_range[1] - comp_range[0]):
            # Corner case: it'd be larger than the entire range => limit to the entire range
            zmin = comp_range[0]
            zmax = comp_range[1]
        if zmax > comp_range[1]:
            # Too high => shift down
            shift = zmax - comp_range[1]
            zmin -= shift
            zmax -= shift
        if zmin < comp_range[0]:
            # Too low => shift up
            shift = comp_range[0] - zmin
            zmin += shift
            zmax += shift

        # Create focus zlevels from the given zsteps number
        return numpy.linspace(zmin, zmax, len(zlevels)).tolist()


class FovUpdateHook(TileHook):
    """Checks the FoV, based on the first tile acquired, and updates the plan if it is different."""

    def __init__(self, plan: GridPlan):
        self._plan = plan

    def after(self, tile: Tile, das: List[DataArray], ctx: TaskContext) -> List[DataArray]:
        if tile.number == 0:
            afovs = [get_da_fov(d) for d in das]
            asfov = (min(f[0] for f in afovs),
                     min(f[1] for f in afovs))
            if not all(util.almost_equal(e, a) for e, a in zip(self._plan.sfov, asfov)):
                logging.warning("Unexpected min FoV = %s, instead of %s", asfov, self._plan.sfov)
                self._plan.sfov = asfov
        return das


class AutoFocusHook(TileHook):
    """Runs the autofocus after a tile, if needed, and then re-acquires the tile."""

    def __init__(self, focusing_method: FocusingMethod, streams: List[Stream], focus_stream: Stream, acquirer: "TileAcquirer"):
        """
        :param focusing_method: ALWAYS or ON_LOW_FOCUS_LEVEL
        :param focus_stream: the stream (with a focuser) to autofocus on
        """
        self._method = focusing_method
        self._streams = streams
        self._focus_stream = focus_stream
        self._acquirer = acquirer

        # save initial focus value to be used in the AutoFocus function
        self._good_focus = focus_stream.focuser.position.value['z']
        focuser_range = focus_stream.focuser.axes['z'].range
        # Calculate the focus range by half the focus margin on each side of good focus
        focus_rng = (self._good_focus - FOCUS_RANGE_MARGIN / 2, self._good_focus + FOCUS_RANGE_MARGIN / 2)
        # Clip values with focuser_range
        self._focus_rng = (max(focus_rng[0], focuser_range[0]), min((focus_rng[1], focuser_range[1])))
        logging.debug("Calculated focus range ={}".format(self._focus_rng))

        # For "ON_LOW_FOCUS_LEVEL" method: a focus level which is corresponding to a in-focus image.
        self._good_focus_level = None  # float

    def after(self, tile: Tile, das: List[DataArray], ctx: TaskContext) -> List[DataArray]:
        i = tile.number
        ix, iy = tile.index
        if self._method == FocusingMethod.ON_LOW_FOCUS_LEVEL:
            if i % SKIP_TILES != 0:
                logging.debug("Skipping focus adjustment..")
                return das

            try:
                current_focus_level = MeasureOpticalFocus(das[self._streams.index(self._focus_stream)])
            except IndexError:
                logging.warning("Failed to get image to measure focus on.")
                return das

            if i == 0:
                # Use initial optical focus level to be compared to next tiles
                # TODO: instead of using the first image, use the best 10% images (excluding outliers)
                self._good_focus_level = current_focus_level
                return das

            logging.debug("Current focus level: %s (good = %s)", current_focus_level, self._good_focus_level)

            # Run autofocus if current focus got worse than permitted deviation,
            # or it was very bad (0) originally.
            if (self._good_focus_level != 0 and
                    (self._good_focus_level - current_focus_level) / self._good_focus_level < FOCUS_FIDELITY
            ):
                return das
        elif self._method != FocusingMethod.ALWAYS:
            raise ValueError(f"Unexpected focusing method {self._method}")

        try:
            subf = AutoFocus(self._focus_stream.detector,
                             self._focus_stream.emitter,
                             self._focus_stream.focuser,
                             good_focus=self._good_focus,
                             rng_focus=self._focus_rng,
                             method=MTD_EXHAUSTIVE)
            ctx.set_subf(subf)
            _, focus_pos, _ = subf.result()  # blocks until autofocus is finished

            # Corner case where it started very badly: update the "good focus"
            # as it's likely going to be better.
            if self._good_focus_level == 0:
                self._good_focus_level = focus_pos

            ctx.check_cancelled()
        except CancelledError:
            raise
        except Exception:
            logging.exception("Running autofocus failed on image i= %s." % i)
        else:
            # Reacquire the out of focus tile (which should be corrected now)
            logging.debug("Reacquiring tile %dx%d at better focus %f", ix, iy, focus_pos)
            das = self._acquirer.get_tile_das(tile)

        return das


class TileSaver:
    """Saves the tiles to disk in a background thread. Does nothing if there is no log path."""

    def __init__(self, log_path: Optional[str] = None):
        """
        :param log_path: directory and filename pattern to save the tiles
        :raises ValueError: if the log path has no filename
        """
        self.enabled = bool(log_path)
        self._executor = None
        if self.enabled:
            filename = os.path.basename(log_path)
            if not filename:
                raise ValueError("Filename is not found on log path.")
            self._exporter = dataio.find_fittest_converter(filename)
            self._fn_bs, self._fn_ext = udataio.splitext(filename)
            self._log_dir = os.path.dirname(log_path)
            self._executor = ThreadPoolExecutor(max_workers=5)

    def save(self, ix: int, iy: int, das: List[DataArray], stream_cube_id: Optional[int] = None) -> None:
        if not self.enabled:
            return

        def save_tile(ix, iy, das, stream_cube_id):
            if stream_cube_id is not None:
                # Indicate it's a stream cube in the file name
                fn_tile = "%s-cube%d-%.5dx%.5d%s" % (self._fn_bs, stream_cube_id, ix, iy, self._fn_ext)
            else:
                fn_tile = "%s-%.5dx%.5d%s" % (self._fn_bs, ix, iy, self._fn_ext)
            logging.debug("Will save data of tile %dx%d to %s", ix, iy, fn_tile)
            self._exporter.export(os.path.join(self._log_dir, fn_tile), das)

        self._executor.submit(save_tile, ix, iy, das, stream_cube_id)

    def close(self) -> None:
        """Wait for all the pending saves"""
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None


class TileAcquirerBase(ABC):
    """
    Acquires the data for a single tile, handling multiple streams.
    """

    def __init__(self, ctx: TaskContext, settings_obs: dict):
        """
        :param ctx: Task context for managing acquisition state.
        :param settings_obs: Acquisition settings observable.
        """

        self._ctx = ctx
        self._settings_obs = settings_obs

    @abstractmethod
    def get_tile_das(self, tile: Tile) -> List[DataArray]:
        pass

    def _acquire_stream_tile(self, tile: Tile, stream: Stream) -> DataArray:
        """
        Calls acquire function and blocks until the data is returned.
        :param tile: The tile to acquire.
        :param stream: The stream to acquire.
        :returns: Acquired DataArray for the current tile stream
        """
        ix, iy = tile.index
        subf = acqmng.acquire([stream], self._settings_obs)
        self._ctx.set_subf(subf)
        das, e = subf.result()  # blocks until all the acquisitions are finished
        if e:
            logging.warning(f"Acquisition for tile {ix}x{iy}, stream {stream.name} partially failed: {e}")

        self._ctx.check_cancelled()
        try:
            return das[0]  # return first da
        except IndexError:
            raise IndexError(f"Failure in acquiring tile {ix}x{iy}, stream {stream.name}.")

    def _acquire_streams_tile(self, tile: Tile, streams: List[Stream]) -> List[DataArray]:
        """
        Calls acquire function and blocks until the data is returned.
        :param tile: The tile to acquire.
        :param streams: The streams to acquire.
        :returns: Acquired das for the current tile streams
        """
        ix, iy = tile.index
        subf = acqmng.acquire(streams, self._settings_obs)
        self._ctx.set_subf(subf)
        das, e = subf.result()  # blocks until all the acquisitions are finished
        if e:
            logging.warning(f"Acquisition for tile {ix}x{iy}, streams partially failed: {e}")

        self._ctx.check_cancelled()
        return das  # return all das for the tile

    def _move_z(self, actuator: model.Actuator, z: float):
        """
        Moves the specified actuator to the given z position.
        :param actuator: The actuator to move.
        :param z: The target z position.
        """
        actuator.moveAbsSync({"z": z})


class TileAcquirer(TileAcquirerBase):

    def __init__(self, ctx: TaskContext, streams: List[Stream], settings_obs: dict, zlevels: numpy.ndarray,
                 focusing_method: FocusingMethod, saver: TileSaver):
        """
        :param zlevels: default z-levels, if the tile doesn't specify its own
        :raises ValueError: if the focusing method is not a FocusingMethod, or MAX_INTENSITY_PROJECTION
          is used without zlevels
        :raises NotImplementedError: if multiple zlevels are used without MAX_INTENSITY_PROJECTION
        """
        if not isinstance(focusing_method, FocusingMethod):
            raise ValueError(f"focusing_method should be of type FocusingMethod, but got {focusing_method}")

        if focusing_method == FocusingMethod.MAX_INTENSITY_PROJECTION and len(zlevels) == 0:
            raise ValueError("MAX_INTENSITY_PROJECTION requires zlevels, but none passed")
            # Note: we even allow if only one zlevels. It would not do MIP, but
            # that allows for flexibility where the user explicitly wants to disable
            # MIP by setting only one zlevel. Same if there is no focuser.

        if len(zlevels) > 0 and not any(s.focuser is not None for s in streams):
            logging.warning("No focuser found in any of the streams, only one acquisition will be performed.")

        if len(zlevels) > 1 and focusing_method != FocusingMethod.MAX_INTENSITY_PROJECTION:
            raise NotImplementedError(
                "Multiple zlevels currently only works with focusing method MAX_INTENSITY_PROJECTION")

        super().__init__(ctx, settings_obs)

        self._streams = streams
        self._focusing_method = focusing_method
        self._saver = saver
        # zlevels can be relative, therefore the FocusMapHook needs the initial zlevels
        self.zlevels = zlevels

    def get_tile_das(self, tile: Tile) -> List[DataArray]:
        """
        Acquire the data arrays for each stream of the given tile.
        :param tile: The tile to acquire.
        :returns: List[DataArray] list of acquired DataArray for each stream of the tile
        """
        zlevels = self.zlevels if tile.zlevels is None else numpy.asarray(tile.zlevels)
        if len(zlevels) == 0:
            return self._acquire_streams_tile(tile, self._streams)

        das = []
        for stream in self._streams:
            ix, iy = tile.index
            if stream.focuser is not None and len(zlevels) > 1:
                # Acquire zstack images based on the given zlevels, and compress them into a single da
                da = self._acquire_compressed_zstack(tile, stream, zlevels)
            elif stream.focuser is not None and len(zlevels) == 1:
                z = zlevels[0]
                logging.debug(f"Moving focus for tile {ix}x{iy} to {z}.")
                self._move_z(stream.focuser, z)
                da = self._acquire_stream_tile(tile, stream)
            else:
                # Acquire a single image of the stream
                da = self._acquire_stream_tile(tile, stream)
            das.append(da)
        return das

    def _acquire_compressed_zstack(self, tile: Tile, stream: Stream, zlevels: numpy.ndarray) -> DataArray:
        """
        Acquire a compressed zstack image for the given stream.
        The method does the following:
            - Move focus over the list of zlevels
            - For each focus level acquire image of the stream
            - Construct xyz cube for the acquired zstack
            - Compress the cube into a single image using 'maximum intensity projection'
        :param tile: The tile being acquired.
        :param stream: The stream to acquire.
        :param zlevels: The z-levels to acquire.
        :returns: Acquired DataArray for the current tile stream
        """
        ix, iy = tile.index
        zstack = []
        for z in zlevels:
            logging.debug(f"Moving focus for tile {ix}x{iy} to {z}.")
            self._move_z(stream.focuser, z)
            zstack.append(self._acquire_stream_tile(tile, stream))

        self._ctx.check_cancelled()
        logging.debug(
            f"Zstack acquisition for tile {ix}x{iy}, stream {stream.name} finished, compressing data into a single image.")
        # Convert zstack into a cube
        fm_cube = assembleZCube(zstack, zlevels)
        # Save the cube on disk, if there is a log path
        self._saver.save(ix, iy, fm_cube, stream_cube_id=self._streams.index(stream))

        if self._focusing_method == FocusingMethod.MAX_INTENSITY_PROJECTION:
            # Compress the cube into a single image along z-axis (using maximum intensity projection)
            mip_image = img.max_intensity_projection(fm_cube, axis=0)
            self._ctx.check_cancelled()
            logging.debug(f"Zstack compression for tile {ix}x{iy}, stream {stream.name} finished.")
            return DataArray(mip_image, copy.copy(zstack[0].metadata))
        else:
            # TODO: support stitched Z-stacks
            # For now, the task init will raise NotImplementedError in such case
            logging.warning("Zstack returned as-is, while it is not supported")
            return fm_cube


class Stitcher:
    """Stitches the acquired tiles together to form a complete image."""

    def __init__(self, registrar, weaver):
        """
        :param registrar: (REGISTER_* or None) type of registration method. If registrar and weaver are None, do not stitch.
        :param weaver: (WEAVER_* or None) type of weaving method. If registrar and weaver are None, do not stitch.
        """
        self._registrar = registrar
        self._weaver = weaver

    @property
    def enabled(self) -> bool:
        return self._registrar is not None and self._weaver is not None

    def stitch(self, da_list: List[DataArray]):
        """
        Stitch the acquired tiles to create a complete view of the required total area
        :returns: (list of DataArrays): a stitched data for each stream acquisition
        """
        st_data = []
        logging.info("Computing big image out of %d images", len(da_list))

        # TODO: Do this registration step in a separate thread while acquiring
        try:
            das_registered = register(da_list, method=self._registrar)
        except ValueError as exp:
            logging.warning("Registration with %s failed %s. Retrying with identity registrar.", self._registrar, exp)
            das_registered = register(da_list, method=REGISTER_IDENTITY)

        logging.info("Using weaving method %s.", self._weaver)
        # Weave every stream
        if isinstance(das_registered[0], tuple):
            for s in range(len(das_registered[0])):
                streams = []
                for da in das_registered:
                    streams.append(da[s])
                da = weave(streams, self._weaver)
                st_data.append(da)
        else:
            da = weave(das_registered, self._weaver)
            st_data.append(da)
        return st_data


class ProgressEstimator:
    """Estimates the time and memory needed, and records the actual time spent on each action."""

    MEMPP = 22  # bytes per pixel, found empirically

    def __init__(self, streams: List[Stream], plan: GridPlan, stitcher: Stitcher, zlevels: numpy.ndarray):
        self._streams = streams
        self._plan = plan
        self._stitcher = stitcher
        self._zlevels = zlevels
        # Average time taken for each tile acquisition
        self.average_acquisition_time = None
        # save actual time take for tile acquisition, stage movement from one tile to another
        # and stitching time for each tile. The keys tell the action, i.e. move or stitch and the time
        # taken for the same action per each tile is saved as a list of floats
        self.save_time: Dict[str, List[float]] = {}

    def reset(self) -> None:
        """Resets the recorded times for all actions."""
        self.save_time = {"acq": [], "stitch": [], "move": [], "save": []}
        # The increase in the number of scanning indices increase with overlap between tiles. The time take
        # by stage to move to different indices also includes the time taken to move when scanning indices increase due
        # to increase in overlap. This means stitching time is included when move time between tiles is observed.
        # Hence, observed time due to stitching is set to zero
        self.save_time["stitch"] = [0]

    def record(self, action: str, duration: float) -> None:
        """
        Records the duration of a specific action.

        :param action: The action being recorded (e.g., "acq", "stitch", "move", "save").
        :param duration: The time taken for the action.
        """
        self.save_time[action].append(duration)

    def log_summary(self) -> None:
        """Logs a summary of the recorded times for each action."""
        avg_per_action = {key: statistics.mean(val) for key, val in self.save_time.items() if len(val) > 0}
        logging.debug(f"The actual time taken per tile for each action is {self.save_time}")
        logging.debug(f"The average time taken per tile for each action is {avg_per_action}")
        logging.debug(f"The average time taken per tile is {self.average_acquisition_time}")

    def _estimate_stream_pixels(self, s: Stream) -> int:
        """
        Estimates the number of pixels the stream will generate during an acquisition.
        :param s: The stream for which to estimate the number of pixels.
        :returns: the number of pixels the stream will generate during an
          acquisition
        """
        px = 0
        if isinstance(s, MultipleDetectorStream):
            for st in s.streams:
                # For the EMStream of a SPARC MDStream, it's just one pixel per
                # repetition (excepted in case  of fuzzing, but let's be optimistic)
                if isinstance(st, (EMStream, CLStream, MonochromatorSettingsStream)):
                    px += 1
                else:
                    px += self._estimate_stream_pixels(st)

            if hasattr(s, 'repetition'):
                px *= s.repetition.value[0] * s.repetition.value[1]

            return px
        elif isinstance(s, (ARStream, SpectrumStream)):
            # Temporarily reports 0 px, as we don't stitch these streams for now
            return 0

        if hasattr(s, "emtResolution"):
            px = numpy.prod(s.emtResolution.value)
        elif hasattr(s, "detResolution"):
            px = numpy.prod(s.detResolution.value)
        elif model.hasVA(s.detector, "resolution"):
            px = numpy.prod(s.detector.resolution.value)
        elif model.hasVA(s.emitter, "resolution"):
            px = numpy.prod(s.emitter.resolution.value)
        else:
            # This shouldn't happen, but let's "optimistic" by assuming it'll
            # only acquire one pixel.
            logging.info("Resolution of stream %s cannot be determined.", s)
            px = 1

        return px

    def estimate_memory(self) -> Tuple[bool, float]:
        """
        Makes an estimate for the amount of memory that will be consumed during
        stitching and compares it to the available memory on the computer.
        :returns: if sufficient memory available, estimated memory
        """
        mem_sufficient = True
        mem_est = 0.0
        if self._stitcher.enabled:
            # Number of pixels for acquisition
            pxs = sum(self._estimate_stream_pixels(s) for s in self._streams)
            pxs *= self._plan.number_of_tiles

            # Memory calculation
            mem_est = pxs * self.MEMPP
            mem_computer = psutil.virtual_memory().total
            logging.debug("Estimating %g GB needed, while %g GB available",
                          mem_est / 1024 ** 3, mem_computer / 1024 ** 3)
            # Assume computer is using 2 GB RAM for odemis and other programs
            mem_sufficient = mem_est < mem_computer - (2 * 1024 ** 3)

        return mem_sufficient, mem_est

    def estimate_time(self, remaining: Optional[int] = None) -> float:
        """
        Estimates duration for acquisition and stitching.
        :param remaining: The number of remaining tiles
        :returns: estimated required time
        """
        if remaining is None:
            remaining = self._plan.number_of_tiles

        # Use observed per-tile time once available, otherwise estimate from stream settings.
        # Note: average_acquisition_time is measured from wall-clock time and already
        # includes per-tile movement, so move_time must not be added again in that case.
        if self.average_acquisition_time is not None:
            acq_time = self.average_acquisition_time * remaining
            move_time = 0  # already included in average_acquisition_time
        else:
            zlevels_dict = {s: self._zlevels for s in self._streams
                            if isinstance(s, (FluoStream))}
            acq_time = acqmng.estimateZStackAcquisitionTime(self._streams, zlevels_dict)
            acq_time *= remaining

            try:
                # move_speed is a default speed but not an actual stage speed due to which
                # extra time is added based on observed time taken to move stage from one tile position to another
                # remaining - 1: current tile is part of remaining, so no need to move there.
                # Use max(remaining - 1, 0) to avoid negative move_time when remaining == 0.
                move_time = max(self._plan.sfov) * max(remaining - 1, 0) / self._plan.move_speed + 0.3 * remaining
            except ValueError:  # no current streams
                move_time = 0.5

        # Estimate stitching time based on number of pixels in the overlapping part.
        # Always included (stitch happens once, after all tiles) so it stays in the
        # estimate until acquisition is fully done.
        stitch_time = 0
        if self._stitcher.enabled:
            max_pxs = 0
            for s in self._streams:
                for sda in s.raw:
                    pxs = sda.shape[0] * sda.shape[1]
                    if pxs > max_pxs:
                        max_pxs = pxs

            stitch_time = (self._plan.number_of_tiles * max_pxs * self._plan.overlap) / STITCH_SPEED

        logging.info(f"The computed time in seconds for tiled acquisition for {remaining} tiles for move is {move_time},"
                     f" acquisition is {acq_time}")

        return acq_time + move_time + stitch_time


def build_hooks(stage: model.Actuator, streams: List[Stream], zlevels: List[float], plan: GridPlan, acquirer: TileAcquirer,
                focusing_method: FocusingMethod,
                focus_points: Optional[List[List[float]]] = None,
                focus_range: Optional[Tuple[float, float]] = None) -> List[TileHook]:
    """
    Pick the hooks to run around every tile, in the order they must run.

    :param stage: The stage actuator.
    :param streams: The list of streams.
    :param zlevels: The list of z-levels.
    :param plan: The grid plan.
    :param acquirer: The tile acquirer.
    :param focusing_method: The focusing method to use.
    :param focus_points: The list of focus points.
    :param focus_range: The focus range.
    :returns: The list of tile hooks.
    """
    focus_stream = next((s for s in streams if s.focuser is not None), None)

    hooks: List[TileHook] = []
    if focus_points is not None:
        if focus_stream is None:
            logging.warning("No focuser found in any of the streams, the focus points are ignored.")
        else:
            hooks.append(FocusMapHook(FocusMap(focus_points), stage, focus_stream.focuser,
                                      zlevels, focus_range))
    hooks.append(FovUpdateHook(plan))
    if focus_stream is not None and focusing_method in (FocusingMethod.ALWAYS,
                                                        FocusingMethod.ON_LOW_FOCUS_LEVEL):
        hooks.append(AutoFocusHook(focusing_method, streams, focus_stream, acquirer))

    return hooks
