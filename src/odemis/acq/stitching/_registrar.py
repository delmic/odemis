# -*- coding: utf-8 -*-
"""
Created on 19 Jul 2017

@author: Éric Piel, Philip Winkler

Copyright © 2017 Éric Piel, Philip Winkler, Delmic

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

This is a series of classes which use different methods to compute the best
location of a set of tiles in a tiled acquisition based on their metadata and 
content, in other words performing "image registration".

"""

from odemis.acq.drift import MeasureShift
import numpy
import math
from odemis import model
import logging
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree
from collections import deque

GOOD_MATCH = 0.9  # consider all registrations with match > GOOD_MATCH
LEFT_TO_RIGHT = 1
RIGHT_TO_LEFT = -1


class IdentityRegistrar(object):
    """ Returns position as-is """

    def __init__(self):
        self.tile_pos = []
        self.dep_tiles_pos = []

    def addTile(self, tile, dependent_tiles=None):
        """
        Extends grid by one tile.
        tile (DataArray of shape YX): each image must have at least MD_POS and MD_PIXEL_SIZE metadata. 
        They should all have the same dtype.
        dependent_tiles (list of DataArray or None): each of the dependent tile, where their position 
        can be considered fixed relative to the main tile. Their content and metadata are not used
        for the computation of the final position.
        """
        self.tile_pos.append(tile.metadata[model.MD_POS])
        if dependent_tiles:
            pos = [t.metadata[model.MD_POS] for t in dependent_tiles]
            self.dep_tiles_pos.append(pos)

    def getPositions(self):
        """
        returns:
        tile_positions (list of N tuples of 2 floats)): the adjusted position in X/Y for each tile, in the order they were added
        dep_tile_positions (list of N tuples of tuples of 2 floats): the adjusted position for each dependent tile 
        (in the order they were passed)
        """
        return self.tile_pos, self.dep_tiles_pos


class ShiftRegistrar(object):
    """
    Locates the position of the image relative to the previous top tiles (horizontally and vertically) 
    by using cross-correlation. The cross-correlation is done using just the part of the images which are 
    supposed to be overlapping. In case the cross-correlation doesn't work (based on a couple of simple tests), 
    fallback to the average shift on the same axis.
    """

    def __init__(self):
        # arrays to store the vertical/horizontal shift values measured for
        # each tile
        # initialize grid to 1x1. The size will increase as new tiles are
        # added.
        self.nx = 1  # (int) total number of cols
        self.registered_positions = [[None]]  # (None or tuple of 2 floats) list of calculated positions
        self.tiles = [[None]]  # (None or DataArray) list of tiles

        # Initialize overlap. This will be modified after the second tile has been added.
        # A value is needed to avoid errors when calculating the position for
        # the first tile.
        self.ovrlp = 0

        # List of 2D indices for grid positions in order of acquisition
        self.acqOrder = []

        # Shift between main tile and dependent tiles
        self.shift_tile_dep_tiles = []  # N x K list. N: number of tiles, K: number of dep_tiles.

        self.px_size = None  # Tuple of 2 ints. (X,Y) pixel size of the current tile in m/px.
        self.size = None  # Tuple of 2 ints. (Y,X) shape of the current tile in px.
        self.posX = None  # int. Position of the current tile in the grid. (0,0) is the top left, posX
        # increases when moving to the right
        self.posY = None  # int. Y position, increases when moving down.
        self.pos_prev_x = None  # int. X coordinate of previous tile. Used to determine whether
        # the current tile should be compared to the right or to the left tile.
        self.osize = None  # int. Overlap size in pixels

    def addTile(self, tile, dependent_tiles=None):
        """
        Extends grid by one tile.
        tile (DataArray of shape YX): each image must have at least MD_POS and MD_PIXEL_SIZE metadata. 
        They should all have the same dtype.
        dependent_tiles (list of K DataArray or None): each of the dependent tile, where their position 
        can be considered fixed relative to the main tile. Their content and metadata are not used
        for the computation of the final position.
        Not every random order of adding tiles is supported: the first tile has to be in the left top 
        position, any following tile must either be inserted to the right of an existing tile, to its left,
        or to the bottom. The grid has to be filled up from top to bottom, moving from the bottom up is not 
        supported.
        """

        if dependent_tiles is None:
            dependent_tiles = []
        else:
            # Shift between tile and its dependent tiles
            self.shift_tile_dep_tiles.append([])

        # Find position of the tile in the grid. Indices of grid position are
        # given as posX, posY.
        if self.tiles[0][0] is None:
            self.size = tile.shape
            self.px_size = tile.metadata[model.MD_PIXEL_SIZE]
            self.posX = 0
            self.posY = 0

            self.pos_prev_x = 0  # used to determine if tile should be compared to right or left tile
        else:
            if tile.shape != self.tiles[0][0].shape:
                raise ValueError("Tile shape differs from previous tile shapes %s != %s" %
                                 (tile.shape, self.tiles[0][0].shape))

            md_pos = tile.metadata[model.MD_POS]

            # Find the registered tile that is closest to the new tile.
            pos_prev, md_pos_prev = self._find_closest_tile(md_pos)

            # Insert new tile either to the right or to the bottom of the
            # closest tile.
            ver_diff = md_pos[1] - md_pos_prev[1]
            hor_diff = md_pos[0] - md_pos_prev[0]
            if abs(ver_diff) > abs(hor_diff):
                if ver_diff < 0:
                    self.posY = pos_prev[0] + 1
                    self.posX = pos_prev[1]
                    if len(self.registered_positions) <= self.posY:
                        self._updateGrid("y")  # extend grid in y direction
                else:
                    self.posY = pos_prev[0] - 1
                    self.posX = pos_prev[1]
            else:
                if hor_diff > 0:
                    self.posY = pos_prev[0]
                    self.posX = pos_prev[1] + 1
                    if len(self.registered_positions[0]) <= self.posX:
                        self._updateGrid("x")  # extend grid in x direction
                else:
                    self.posY = pos_prev[0]
                    self.posX = pos_prev[1] - 1

            # Calculate overlap
            if abs(ver_diff) > abs(hor_diff):
                size_meter = self.size[0] * self.px_size[1]
                diff = abs(tile.metadata[model.MD_POS][1] - md_pos_prev[1])
                self.ovrlp = abs(size_meter - diff) / size_meter
                # self.size is given as YX, MD_POS as XY
                self.osize = self.size[0] * self.ovrlp  # overlap size in pixels
            else:
                size_meter = self.size[1] * self.px_size[0]
                diff = abs(tile.metadata[model.MD_POS][0] - md_pos_prev[0])
                self.ovrlp = abs(size_meter - diff) / size_meter

                self.osize = self.size[1] * self.ovrlp  # overlap size in pixels

        for dt in dependent_tiles:
            sdt = numpy.subtract(dt.metadata[model.MD_POS], tile.metadata[model.MD_POS])
            self.shift_tile_dep_tiles[-1].append(sdt)

        self._compute_registration(tile, self.posY, self.posX)
        self.pos_prev_x = self.posX

    def getPositions(self):
        """
        returns:
        tile_positions (list of N tuples): the adjusted position in X/Y for each tile, in the order they were added
        dep_tile_positions (list of N tuples of K tuples of 2 floats): for each tile, it returns 
        the adjusted position of each dependent tile (in the order they were passed)
        """
        firstPosition = numpy.divide(
            self.tiles[0][0].metadata[model.MD_POS], self.px_size)
        tile_positions = []
        dep_tile_positions = []

        for ti in self.acqOrder:
            shift = self.registered_positions[ti[0]][ti[1]]
            tile_positions.append(((shift[0] + firstPosition[0]) * self.px_size[0],
                                   (firstPosition[1] - shift[1]) * self.px_size[1]))

        # Return positions for dependent tiles
        for t, sdts in zip(tile_positions, self.shift_tile_dep_tiles):
            dts = []  # dependent tiles for tile
            for sdt in sdts:
                dts.append((t[0] + sdt[0], t[1] + sdt[1]))
            dep_tile_positions.append(dts)

        return tile_positions, dep_tile_positions

    def _find_closest_tile(self, pos):
        """ finds the tile in the grid that is closest to pos.
        returns: 
        pos_prev: tuple of two ints. Grid position of closest tile.
        md_pos_prev: actual position of closest tile in m """
        minDist = float("inf")
        for i in range(len(self.tiles)):
            for j in range(len(self.tiles[0])):
                if self.tiles[i][j] is not None:
                    md_pos_ij = self.tiles[i][j].metadata[model.MD_POS]
                    dist = math.hypot(pos[0] - md_pos_ij[0], pos[1] - md_pos_ij[1])
                    if dist < minDist:
                        minDist = dist
                        pos_prev = (i, j)
                        md_pos_prev = self.tiles[i][j].metadata[model.MD_POS]
        return pos_prev, md_pos_prev

    def _updateGrid(self, direction):
        """ extends grid by one row (direction = "y") or one column (direction = "x") """
        if direction == "x":
            self.nx += 1
            for i in range(len(self.tiles)):
                self.tiles[i].append(None)
                self.registered_positions[i].append(None)
        else:
            self.tiles.append([None] * self.nx)
            self.registered_positions.append([None] * self.nx)

    def _estimateROI(self, shift):
        """
        Given a shift vector between two tiles, it returns the corresponding
        roi's that overlap.
        shift (2 ints): shift on the roi
        return (2 tuples of 4 ints): left, top, right, bottom pixels in each tile
        """
        if (abs(shift[0]) >= self.size[1]) or (abs(shift[1]) >= self.size[0]):
            raise ValueError("There is no overlap between tiles for the shift given %s" %
                             (shift,))

        # Tile size is given in YX coordinates, shift in XY
        max_x, max_y = self.size[1], self.size[0]
        shift_x, shift_y = int(shift[0]), int(shift[1])

        if shift_x < 0:
            l1, r1 = 0, max_x + shift_x
            l2, r2 = -shift_x, max_x
        else:
            l1, r1 = shift_x, max_x
            l2, r2 = 0, max_x - shift_x

        if shift_y < 0:
            t1, b1 = 0, max_y + shift_y
            t2, b2 = -shift_y, max_y
        else:
            t1, b1 = shift_y, max_y
            t2, b2 = 0, max_y - shift_y

        return (l1, t1, r1, b1), (l2, t2, r2, b2)

    def _estimateMatch(self, imageA, imageB, shift):
        """
        Returns an estimation of the similarity between the given images
        when the second is shifted by the shift value. It is used to assess 
        the quality of a shift measurement by giving the shifted image.
        return (0 <= float<=1): the bigger, the more similar are the images
        """
        # If the tile is shifted more than the size of the overlap region in one dimension,
        # force the match to 0.
        px_size = imageA.metadata[model.MD_PIXEL_SIZE]  # should be the same for A and B
        # y axis of shift has increasing values when going down, for MD_POS it is the opposite
        exp_shift_x = int((imageB.metadata[model.MD_POS][0] - imageA.metadata[model.MD_POS][0]) / px_size[0])
        exp_shift_y = -int((imageB.metadata[model.MD_POS][1] - imageA.metadata[model.MD_POS][1]) / px_size[1])
        if max(abs(exp_shift_x - shift[0]), abs(exp_shift_y - shift[1])) > max(numpy.multiply(self.size, self.ovrlp)):
            logging.info("Calculated shift is larger than the overlap size, using expected position "
                         "instead.")
            return 0

        (l1, t1, r1, b1), (l2, t2, r2, b2) = self._estimateROI(shift)

        imageA_sh = numpy.array(imageA)[t1:b1, l1:r1]
        imageB_sh = numpy.array(imageB)[t2:b2, l2:r2]

        avg = (numpy.sum(imageA_sh) / imageA_sh.size,
               numpy.sum(imageB_sh) / imageB_sh.size)

        dist = (numpy.subtract(imageA_sh, avg[0]),
                numpy.subtract(imageB_sh, avg[1]))

        covar = numpy.sum(dist[0] * dist[1]) / imageA_sh.size

        var = (numpy.sum(numpy.power(dist[0], 2)) / imageA_sh.size,
               numpy.sum(numpy.power(dist[1], 2)) / imageB_sh.size)

        stDev = (math.sqrt(var[0]), math.sqrt(var[1]))

        if stDev[0] == 0 or stDev[1] == 0:
            return 0

        return covar / (stDev[0] * stDev[1])

    def _get_shift(self, prev_tile, tile, shift):
        """
        Measures the shift on the overlap between the two tiles when the second is shifted
        by the shift value.
        shift (2 ints): shift between prev_tile and tile
        return (2 ints): guessed shift
        """
        (l1, t1, r1, b1), (l2, t2, r2, b2) = self._estimateROI(shift)
        a = prev_tile[t1:b1, l1:r1]
        b = tile[t2:b2, l2:r2]
        [x, y] = MeasureShift(b, a)
        return x, y

    def _register_horizontally(self, row, col, xdir):
        """
        Apply the registration algorithm to the neighbouring tile on the right or left.
        row/col (int): grid position of new tile
        xdir (LEFT_TO_RIGHT, RIGHT_TO_LEFT): direction of move
        returns (int or None, float): registered position of tile wrt horizontal neighbour if available
        (None otherwise), quality of the registration
        """
        if (xdir == LEFT_TO_RIGHT and col == 0) or (xdir == RIGHT_TO_LEFT and col == self.nx):
            return None, 0

        # Compute shift
        tile = self.tiles[row][col]
        if xdir == LEFT_TO_RIGHT:
            exp_shift = (int(self.size[1] - self.osize), 0)
            prev_pos = self.registered_positions[row][col - 1]
            prev_tile = self.tiles[row][col - 1]
        elif xdir == RIGHT_TO_LEFT:
            exp_shift = (-int(self.size[1] - self.osize), 0)
            prev_pos = self.registered_positions[row][col + 1]
            prev_tile = self.tiles[row][col + 1]
        else:
            raise ValueError("xdir argument is %s, must be either LEFT_TO_RIGHT or RIGHT_TO_LEFT." % xdir)
        x, y = self._get_shift(prev_tile, tile, exp_shift)

        # If the quality of the cross-correlation is low, use fallback shift
        match = self._estimateMatch(prev_tile, tile, (exp_shift[0] - x, exp_shift[1] - y))

        # Add shift to expected position
        exp_pos = numpy.add(exp_shift, prev_pos)
        pos = int(exp_pos[0] - x), int(exp_pos[1] - y)
        return pos, match

    def _register_vertically(self, row, col):
        """
        Apply the registration algorithm to the neighbouring tile on the top.
        row/col (int): grid position of new tile
        returns (pos, match): registered position of tile wrt top neighbour,
        quality of the registration
        """
        if row == 0:
            return None, 0

        tile = self.tiles[row][col]
        exp_shift = (0, int(self.size[0] - self.osize))
        prev_pos = self.registered_positions[row - 1][col]
        prev_tile = self.tiles[row - 1][col]
        x, y = self._get_shift(prev_tile, tile, exp_shift)
        match = self._estimateMatch(prev_tile, tile, (exp_shift[0] - x, exp_shift[1] - y))

        # Add shift to expected position
        exp_pos = numpy.add(exp_shift, prev_pos)
        pos = int(exp_pos[0] - x), int(exp_pos[1] - y)
        return pos, match

    def _compute_registration(self, tile, row, col):
        """
        Stitches one tile to the overall image. Tiles should be inserted in an
        order such that the previous top or previous left tiles have already been inserted.
        tile (DataArray of shape YX): Tile to be stitched
        row (0<=int): Row of the tile position
        col (0<=int): Column of the tile position
        raise ValueError: if the top or left tile hasn't been provided yet
        """
        if (row >= 1 and self.tiles[row - 1][col] is None) and (col >= 1 and self.tiles[row][col - 1] is None):
            raise ValueError("Trying to stitch image at %d,%d, "
                             "while its previous image hasn't been stitched yet" %
                             (row, col))
        # store the tile
        self.tiles[row][col] = tile

        if self.pos_prev_x < col and col != 0 and self.tiles[row][col - 1] is not None:
            pos_hor, match_hor = self._register_horizontally(row, col, LEFT_TO_RIGHT)
        elif self.pos_prev_x > col and col != self.nx and self.tiles[row][col + 1] is not None:
            pos_hor, match_hor = self._register_horizontally(row, col, RIGHT_TO_LEFT)
        else:
            pos_hor, match_hor = None, 0

        if row != 0 and self.tiles[row - 1][col] is not None:
            pos_ver, match_ver = self._register_vertically(row, col)
        else:
            pos_ver, match_ver = None, 0

        # Fallback to expected position if match is 0 (shift is larger than overlap size)
        if ((pos_hor is None) and (pos_ver is None)) or (match_hor == 0 and match_ver == 0):
            # expected tile position, if there would be no shift
            first_pos = self.tiles[0][0].metadata[model.MD_POS]
            # registered positions have their origin in (0, 0)
            registered_pos = numpy.divide((tile.metadata[model.MD_POS][0] - first_pos[0],
                                    -tile.metadata[model.MD_POS][1] + first_pos[1]),
                                    self.px_size)

        # In case both registrations give good matches, use the one that is closest to
        # the expected position. This decreases the chances of error propagation.
        elif match_hor > GOOD_MATCH and match_ver > GOOD_MATCH:
            registered_pos = min([pos_hor, pos_ver], key=lambda x: numpy.hypot(
                                    *numpy.subtract(x, tile.metadata[model.MD_POS])))
        elif ((pos_hor is None) or match_hor < match_ver) and pos_ver:
            registered_pos = pos_ver
        else:
            registered_pos = pos_hor

        # store the position of the tile
        self.registered_positions[row][col] = registered_pos
        self.acqOrder.append([row, col])


class GlobalShiftRegistrar(object):
    """
    Uses the cross-correlation algorithm to find the optimal shift for each tile with all of its
    neighbours and performs a global optimization to find the best path connecting the tiles.
    """

    def __init__(self):
        # Store all the tiles. Each cell contains either None or a DataArray
        self.tiles = [[None]]

        # Store the shifts in a data structure with shape num_rows x (num_cols - 1) x 2 for the
        # horizontal shifts and (num_cols - 1) x num_rows x 2 for the vertical shifts. The data
        # structure contains the shift on all edges in the tile grid from top left to bottom right.
        # Each cell contains a tuple (x, y shift) and a float (error value).
        # The main advantage of using this data structure over an adjacency matrix is
        # that it can be extended in the same way as the self.tiles attribute is extended
        # when a new tile is added.
        self.shifts_hor = [[None]]
        self.shifts_ver = [[None]]

        # Calculated position of each tile relative to the upper left (first) tile in pixels as a 3D array of floats
        self.registered_positions = None

        # List of 2D indices for grid positions in order of acquisition
        self.acq_order = []

        # Shift between main tile and dependent tiles, shape: number of tiles x number of dep_tiles.
        self.offsets_dep_tiles = []

    def addTile(self, tile, dependent_tiles=None):
        """
        Extends grid by one tile. The first tile is added at the top left position. Any following
        tile must have a neighbour (on either side left, right, top, bottom) that has previously
        been added.

        :param tile: (DataArray of shape YX) tile with MD_POS and MD_PIXEL_SIZE metadata.
        :param dependent_tiles: (list of K numpy.arrays or None): dependent tiles with fixed position
        relative to main tile. Their content and metadata are not used for the computation of the final position.
        """
        row, col = self._insert_tile_to_grid(tile)
        self._compute_registration(row, col)

        if dependent_tiles is not None:
            offsets = []
            for dt in dependent_tiles:
                offsets.append(numpy.subtract(dt.metadata[model.MD_POS], tile.metadata[model.MD_POS]))
            self.offsets_dep_tiles.append(offsets)

    def getPositions(self):
        """
        Updates the registered positions (found using cross correlation and a min spanning tree) and returns the 
        adjusted tile_positions & dep_tile_positions. When calling this function .registered_positions is updated with
        the calculated position of each tile relative to the upper left (first) tile in pixels as a 3D array.
        :returns tile_positions: (list of N tuples) the adjusted position in X/Y for each tile, in the
        order they were added
        :returns dep_tile_positions: (list of N tuples of K tuples of 2 floats) for each tile, it returns
        the adjusted position of all dependent tile (in the order they were passed)
        """
        px_size = self.tiles[0][0].metadata[model.MD_PIXEL_SIZE]
        firstPosition = numpy.divide(self.tiles[0][0].metadata[model.MD_POS], px_size)
        tile_positions = []
        dep_tile_positions = []

        self.registered_positions = self._assemble_mosaic()
        for ti in self.acq_order:
            shift = self.registered_positions[ti[0]][ti[1]]
            tile_positions.append(((shift[0] + firstPosition[0]) * px_size[0],
                                   (firstPosition[1] - shift[1]) * px_size[1]))

        # Return positions for dependent tiles
        for t, sdts in zip(tile_positions, self.offsets_dep_tiles):
            dts = []  # dependent tiles for tile
            for sdt in sdts:
                dts.append((t[0] + sdt[0], t[1] + sdt[1]))
            dep_tile_positions.append(dts)

        return tile_positions, dep_tile_positions

    def _insert_tile_to_grid(self, tile):
        """
        Stores the tile at the proper place in the grid. If necessary, the grid is
        extended to accommodate the new tile.

        :param tile: (DataArray) tile to be inserted
        :returns: (int, int) row, col grid position of the tile
        :updates self.tiles, self.shifts, self.acq_order:
        """
        if self.tiles[0][0] is None:
            self.tiles[0][0] = tile
            self.acq_order.append([0, 0])
            return 0, 0

        if tile.shape != self.tiles[0][0].shape:
            raise ValueError("Tile shape differs from first tile shape %s != %s" %
                             (tile.shape, self.tiles[0][0].shape))

        num_cols = len(self.tiles[0])
        num_rows = len(self.tiles)

        # Find the registered tile that is closest to the new tile.
        minDist = float("inf")
        for i in range(num_rows):
            for j in range(num_cols):
                if self.tiles[i][j] is not None:
                    dist = math.hypot(*numpy.subtract(tile.metadata[model.MD_POS],
                                                     self.tiles[i][j].metadata[model.MD_POS]))
                    if dist < minDist:
                        minDist = dist
                        prev_row, prev_col = (i, j)

        # Insert new tile either to the right or to the bottom of the closest tile.
        ver_diff = tile.metadata[model.MD_POS][1] - self.tiles[prev_row][prev_col].metadata[model.MD_POS][1]
        hor_diff = tile.metadata[model.MD_POS][0] - self.tiles[prev_row][prev_col].metadata[model.MD_POS][0]
        if abs(ver_diff) > abs(hor_diff) and ver_diff < 0:
            # new tile below previous tile
            row = prev_row + 1
            col = prev_col
            # extend grid in y direction if necessary
            if num_rows <= row:
                self.tiles.append([None] * num_cols)
                self.shifts_hor.append([None] * (num_cols - 1))
                self.shifts_ver.append([None] * num_cols)
        elif abs(ver_diff) > abs(hor_diff) and ver_diff > 0:
            # new tile on top of previous tile
            row = prev_row - 1
            col = prev_col
        elif abs(ver_diff) < abs(hor_diff) and hor_diff < 0:
            # new tile to the left of previous tile
            row = prev_row
            col = prev_col - 1
        elif abs(ver_diff) < abs(hor_diff) and hor_diff > 0:
            # new tile to the right of previous tile
            row = prev_row
            col = prev_col + 1
            # extend grid in x direction if necessary
            if num_cols <= col:
                for i in range(len(self.tiles)):
                    self.tiles[i].append(None)
                    self.shifts_hor[i].append(None)
                    self.shifts_ver[i].append(None)
        elif abs(hor_diff) == 0 and abs(ver_diff) == 0:
            raise ValueError("Cannot insert multiple tiles at the same position.")
        elif abs(ver_diff) == abs(hor_diff):
            # Purely diagonal shift. In practice, this will never happen because the smallest difference will be
            # picked up by one of the previous if-statements. This exception is likely to be triggered only in
            # artificial tests.
            raise ValueError("Horizontal and vertical shift are exactly equal and show a pure diagonal shift which is "
                             "a sign of missing tile.")
        else:
            # This should never happen
            raise ValueError("Unexpected error. Was not able to insert the tile into the grid for a horizontal "
                             "difference of %s meters with the closest tile and a vertical difference of %s meters "
                             "with the closest tile." % (hor_diff, ver_diff))

        self.tiles[row][col] = tile
        self.acq_order.append([row, col])
        return row, col

    def _get_shift(self, prev_tile, tile):
        """
        Calculates the shift  between the two tiles. It also returns an error metric based on the
        normalized cross-correlation (ncc).

        :param prev_tile: (DataArray) static tile to which other tile is compared
        :param tile: (DataArray) shifted tile
        :returns: ((float, float), float) x shift, y shift, normalized cross correlation (-1 <= ncc <= 1, higher
        is better)
        """
        px_size = tile.metadata[model.MD_PIXEL_SIZE]
        # The y-axis of the reference coordinate system used here is inverted compared to
        # the metadata position.
        exp_shift = ((tile.metadata[model.MD_POS][0] - prev_tile.metadata[model.MD_POS][0]) / px_size[0],
                     (-tile.metadata[model.MD_POS][1] + prev_tile.metadata[model.MD_POS][1]) / px_size[1])

        # Get the region of interest
        if abs(exp_shift[0]) >= tile.shape[1] or abs(exp_shift[1]) >= tile.shape[0]:
            raise ValueError("There is no overlap between tiles for the shift given %s" % (exp_shift,))
        if exp_shift[0] < 0:
            l1, r1 = 0, tile.shape[1] + int(exp_shift[0])
            l2, r2 = -int(exp_shift[0]), tile.shape[1]
        else:
            l1, r1 = int(exp_shift[0]), tile.shape[1]
            l2, r2 = 0, tile.shape[1] - int(exp_shift[0])

        if exp_shift[1] < 0:
            t1, b1 = 0, tile.shape[0] + int(exp_shift[1])
            t2, b2 = -int(exp_shift[1]), tile.shape[0]
        else:
            t1, b1 = int(exp_shift[1]), tile.shape[0]
            t2, b2 = 0, tile.shape[0] - int(exp_shift[1])
        prev_tile_roi = numpy.array(prev_tile)[t1:b1, l1:r1]
        tile_roi = numpy.array(tile)[t2:b2, l2:r2]

        # If you need to crop the tile without changing the output shift,
        # you can do it here with the pattern tile_roi[t:-b, l:-r]
        shift = MeasureShift(tile_roi, prev_tile_roi)
        shift_total = numpy.subtract(exp_shift, shift)

        # Measure accuracy (ncc value)
        avg = numpy.average(prev_tile), numpy.average(tile)
        dist = prev_tile_roi - avg[0], tile_roi - avg[1]
        covar = numpy.sum(dist[0] * dist[1]) / prev_tile_roi.size
        var = numpy.sum(dist[0] ** 2) / prev_tile_roi.size, numpy.sum(dist[1] ** 2) / tile_roi.size
        stDev = (numpy.sqrt(var[0]), numpy.sqrt(var[1]))
        if stDev[0] == 0 or stDev[1] == 0:
            return exp_shift, 0
        # Normalized cross correlation (values between -1 and 1)
        ncc = (covar / (stDev[0] * stDev[1]))

        overlap = min(numpy.abs(numpy.subtract(tile.shape, numpy.abs(exp_shift))))
        if max(numpy.abs(shift)) > overlap:
            logging.info("Calculated shift is larger than the overlap size, using expected position "
                         "instead.")
            return exp_shift, 0

        return shift_total, ncc

    def _compute_registration(self, row, col):
        """
        Performs registration of the tile at grid position row, col with respect to every
        available neighbour. The computed shifts and the respective cross-correlation values
        are stored in self.shifts.

        :param row: (int) row index
        :param col: (int) col index
        :updates self.shifts:
        """
        tile = self.tiles[row][col]
        num_cols = len(self.tiles[0])
        num_rows = len(self.tiles)

        # Neighbouring tiles and shifts
        nbr_left = self.tiles[row][col - 1] if col > 0 else None
        nbr_right = self.tiles[row][col + 1] if col < num_cols - 2 else None
        nbr_top = self.tiles[row - 1][col] if row > 0 else None
        nbr_bottom = self.tiles[row + 1][col] if row < num_rows - 2 else None

        shift_left = self.shifts_hor[row][col - 1] if col > 0 else None
        shift_right = self.shifts_hor[row][col] if col < num_cols - 2 else None
        shift_top = self.shifts_ver[row - 1][col] if row > 0 else None
        shift_bottom = self.shifts_ver[row][col] if row < num_rows - 2 else None

        # Calculate the shifts to all adjacent tiles that have not been calculated yet
        if nbr_left is not None and not shift_left:
            self.shifts_hor[row][col - 1] = self._get_shift(nbr_left, tile)
        if nbr_right is not None and not shift_right:
            self.shifts_hor[row][col] = self._get_shift(tile, nbr_right)
        if nbr_top is not None and not shift_top:
            self.shifts_ver[row - 1][col] = self._get_shift(nbr_top, tile)
        if nbr_bottom is not None and not shift_bottom:
            self.shifts_ver[row][col] = self._get_shift(tile, nbr_bottom)
            
    def _assemble_mosaic(self):
        """
        Performs a global optimization to find the best path through the tile grid using 
        a minimum spanning tree.

        :returns: (numpy array with shape: num_rows x num_cols x 2) registered positions relative to the upper left
        tile in pixels
        """
        # Transform shifts and errors to adjacency matrices
        # The normalized cross correlation value needs to be transformed, so it can be
        # used in the minimum spanning tree. Lower values are better and the value should
        # never be 0 --> convert to error between [100, 200]
        num_cols = len(self.tiles[0])
        num_rows = len(self.tiles)
        errors = numpy.zeros((num_rows * num_cols, num_rows * num_cols))
        shifts = numpy.zeros((num_rows * num_cols, num_rows * num_cols, 2))
        for row in range(num_rows):
            for col in range(num_cols - 1):
                idx = row * num_cols + col
                if self.shifts_hor[row][col]:
                    ncc = self.shifts_hor[row][col][1]
                    errors[idx, idx + 1] = 200 - (ncc + 1) * 50
                    shifts[idx, idx + 1] = self.shifts_hor[row][col][0]

        for row in range(num_rows - 1):
            for col in range(num_cols):
                idx = row * num_cols + col
                if self.shifts_ver[row][col]:
                    ncc = self.shifts_ver[row][col][1]
                    errors[idx, idx + num_cols] = 200 - (ncc + 1) * 50
                    shifts[idx, idx + num_cols] = self.shifts_ver[row][col][0]

        # Build the minimum spanning tree
        tree = minimum_spanning_tree(csr_matrix(errors))
        tree = tree.toarray().astype(int)

        # Follow the path through the tree and update positions with the corresponding shifts.
        # The minimum spanning tree is converted to an adjacency list that contains for every
        # tile a list of all tiles that should be updated based on the current tile.
        # The children of the newly updated tile will be placed in a queue waiting to be
        # updated themselves.
        adj_list = {}
        for i in range(len(tree)):
            adj = []
            for j in range(len(tree[0])):
                if tree[i][j] != 0:
                    adj.append([j, shifts[i][j]])
                if tree[j][i] != 0:
                    adj.append([j, shifts[j][i]])
            adj_list[i] = adj

        positions = numpy.zeros((num_rows, num_cols, 2))  # start with no shift for each tile
        positions_flat = positions.reshape((num_rows * num_cols, 2))  # the tiles in the "idx" notation
        idx_queue = deque([0])  # start with index 0
        idx_done = set()  # set of positions that have already been updated
        while idx_queue:
            key = idx_queue.popleft()
            for next_idx, shift in adj_list[key]:
                if next_idx not in idx_done:  # don't consider positions that have already been dealt with
                    if next_idx > key:
                        positions_flat[next_idx] = numpy.add(positions_flat[key], shift)
                    else:
                        positions_flat[next_idx] = numpy.subtract(positions_flat[key], shift)

                    idx_done.add(next_idx)
                    if next_idx not in idx_queue:
                        idx_queue.append(next_idx)

        return positions
