# -*- coding: utf-8 -*-
"""
@author: Bassim Lazem

Copyright © 2020 Bassim Lazem, Delmic

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
from concurrent.futures import CancelledError, TimeoutError
from concurrent.futures._base import RUNNING, FINISHED, CANCELLED
import copy
from enum import Enum
import logging
import math
import numpy
from odemis import model, dataio
from odemis.acq import acqmng
from odemis.acq.align.autofocus import MeasureOpticalFocus, AutoFocus, MTD_EXHAUSTIVE
from odemis.acq.stitching._constants import WEAVER_MEAN, REGISTER_IDENTITY, REGISTER_GLOBAL_SHIFT
from odemis.acq.stitching._simple import register, weave
from odemis.acq.stream import Stream, EMStream, ARStream, \
    SpectrumStream, FluoStream, MultipleDetectorStream, util, executeAsyncTask, \
    CLStream
from odemis.model import DataArray
from odemis.util import dataio as udataio, img
from odemis.util.img import assembleZCube
import os
import psutil
import threading
import time

# TODO: Find a value that works fine with common cases
# Ratio of the allowed difference of tile focus from good focus
FOCUS_FIDELITY = 0.3
# Limit focus range, half the margin will be used on each side of initial focus
FOCUS_RANGE_MARGIN = 100e-6  # m
# Indicate the number of tiles to skip during focus adjustment
SKIP_TILES = 3

MOVE_SPEED_DEFAULT = 100e-6  # m/s

class FocusingMethod(Enum):
    NONE = 0  # Never auto-focus
    ALWAYS = 1  # Before every tile
    # If the previous tile focus level is too far from the original level
    ON_LOW_FOCUS_LEVEL = 2
    # Acquisition is done at several zlevels, and they are merged to obtain a focused image
    MAX_INTENSITY_PROJECTION = 3


class TiledAcquisitionTask(object):
    """
    The goal of this task is to acquire a set of tiles then stitch them together
    """

    def __init__(self, streams, stage, area, overlap, settings_obs=None, log_path=None, future=None, zlevels=None,
                 registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN, focusing_method=FocusingMethod.NONE):
        """
        :param streams: (list of Streams) the streams to acquire
        :param stage: (Actuator) the sample stage to move to the possible tiles locations
        :param area: (float, float, float, float) left, top, right, bottom points of acquisition area
        :param overlap: (float) the amount of overlap between each acquisition
        :param settings_obs: (SettingsObserver or None) class that contains a list of all VAs
            that should be saved as metadata
        :param log_path: (string) directory and filename pattern to save acquired images for debugging
        :param future: (ProgressiveFuture or None) future to track progress, pass None for estimation only
        :param zlevels: (list(float) or None) focus z positions required zstack acquisition.
           Currently, can only be used if focusing_method == MAX_INTENSITY_PROJECTION.
        :param registrar: (REGISTER_*) type of registration method
        :param weaver: (WEAVER_*) type of weaving method
        :param focusing_method: (FocusingMethod) Defines when will the autofocuser be run.
           The autofocuser uses the first stream with a .focuser.
           If MAX_INTENSITY_PROJECTION is used, zlevels must be provided too.
        """
        self._future = future
        self._streams = streams
        self._stage = stage
        # Get total area as a tuple of width, height from ltrb area points
        normalized_area = util.normalize_rect(area)
        if area[0] != normalized_area[0] or area[1] != normalized_area[1]:
            logging.warning("Acquisition area {} rearranged into {}".format(area, normalized_area))

        self._area = normalized_area
        height = normalized_area[3] - normalized_area[1]
        width = normalized_area[2] - normalized_area[0]
        self._area_size = (width, height)
        self._overlap = overlap

        # Note: we used to change the stream horizontalFoV VA to the max, if available (eg, SEM).
        # However, it's annoying if the caller actually cares about the FoV (eg,
        # because it wants a large number of pixels, or there are artifacts at
        # the largest FoV). In addition, it's quite easy to do for the caller anyway.
        # Something like this:
        # for stream in self._streams:
        #     if model.hasVA(stream, "horizontalFoV"):
        #         stream.horizontalFoV.value = stream.horizontalFoV.clip(max(self._area_size))

        # Get the smallest field of view
        self._sfov = self._guessSmallestFov(streams)
        logging.debug("Smallest FoV: %s", self._sfov)

        (self._nx, self._ny), self._starting_pos = self._getNumberOfTiles()

        # To check and adjust the focus in between tiles
        if not isinstance(focusing_method, FocusingMethod):
            raise ValueError(f"focusing_method should be of type FocusingMethod, but got {focusing_method}")
        self._focusing_method = focusing_method
        self._focus_stream = next((sd for sd in self._streams if sd.focuser is not None), None)
        if self._focus_stream:
            # save initial focus value to be used in the AutoFocus function
            self._good_focus = self._focus_stream.focuser.position.value['z']
            focuser_range = self._focus_stream.focuser.axes['z'].range
            # Calculate the focus range by half the focus margin on each side of good focus
            focus_rng = (self._good_focus - FOCUS_RANGE_MARGIN / 2, self._good_focus + FOCUS_RANGE_MARGIN / 2)
            # Clip values with focuser_range
            self._focus_rng = (max(focus_rng[0], focuser_range[0]), min((focus_rng[1], focuser_range[1])))
            logging.debug("Calculated focus range ={}".format(self._focus_rng))

        if focusing_method == FocusingMethod.MAX_INTENSITY_PROJECTION and not zlevels:
            raise ValueError("MAX_INTENSITY_PROJECTION requires zlevels, but none passed")
            # Note: we even allow if only one zlevels. It would not do MIP, but
            # that allows for flexibility where the user explicitly wants to disable
            # MIP by setting only one zlevel. Same if there is no focuser.

        if zlevels:
            if self._focus_stream is None:
                logging.warning("No focuser found in any of the streams, only one acquisition will be performed.")
            self._zlevels = zlevels
        else:
            self._zlevels = []

        if len(self._zlevels) > 1 and focusing_method != FocusingMethod.MAX_INTENSITY_PROJECTION:
            raise NotImplementedError("Multiple zlevels currently only works with focusing method MAX_INTENSITY_PROJECTION")

        # For "ON_LOW_FOCUS_LEVEL" method: a focus level which is corresponding to a in-focus image.
        self._good_focus_level = None  # float

        # Rough estimate of the stage movement speed, for estimating the extra
        # duration due to movements
        self._move_speed = MOVE_SPEED_DEFAULT
        if model.hasVA(stage, "speed"):
            try:
                self._move_speed = (stage.speed.value["x"] + stage.speed.value["y"]) / 2
            except Exception as ex:
                logging.warning("Failed to read the stage speed: %s", ex)

        # TODO: allow to change the stage movement pattern
        self._settings_obs = settings_obs

        self._log_path = log_path
        if self._log_path:
            filename = os.path.basename(self._log_path)
            if not filename:
                raise ValueError("Filename is not found on log path.")
            self._exporter = dataio.find_fittest_converter(filename)
            self._fn_bs, self._fn_ext = udataio.splitext(filename)
            self._log_dir = os.path.dirname(self._log_path)

        self._registrar = registrar
        self._weaver = weaver

    def _getFov(self, sd):
        """
        sd (Stream or DataArray): If it's a stream, it must be a live stream,
          and the FoV will be estimated based on the settings.
        return (float, float): width, height in m
        """
        if isinstance(sd, model.DataArray):
            # The actual FoV, as the data recorded it
            im_bbox = img.getBoundingBox(sd)
            logging.debug("Bounding box of stream data: %s", im_bbox)
            return im_bbox[2] - im_bbox[0], im_bbox[3] - im_bbox[1]
        elif isinstance(sd, Stream):
            # Ask the stream, which estimates based on the emitter/detector settings
            try:
                return sd.guessFoV()
            except (NotImplementedError, AttributeError):
                raise TypeError("Unsupported Stream %s, it doesn't have a .guessFoV()" % (sd,))
        else:
            raise TypeError("Unsupported object")

    def _guessSmallestFov(self, ss):
        """
        Return (float, float): smallest width and smallest height of all the FoV
          Note: they are not necessarily from the same FoV.
        raise ValueError: If no stream selected
        """
        fovs = [self._getFov(s) for s in ss]
        if not fovs:
            raise ValueError("No stream so no FoV, so no minimum one")

        return (min(f[0] for f in fovs),
                min(f[1] for f in fovs))

    def _getNumberOfTiles(self):
        """
        Calculate needed number of tiles (horizontal and vertical) to cover the whole area,
        and the adjusted position of the first tile.
        return:
            nb (int, int): number of tile in X, Y
            starting_position (float, float): center of the first tile (at the top-left)
        """
        # The size of the smallest tile, non-including the overlap, which will be
        # lost (and also indirectly represents the precision of the stage)
        reliable_fov = ((1 - self._overlap) * self._sfov[0], (1 - self._overlap) * self._sfov[1])

        logging.debug("Would need tiles: nx= %s, ny= %s",
                      abs(self._area_size[0] / reliable_fov[0]),
                      abs(self._area_size[1] / reliable_fov[1])
                      )
        # Round up the number of tiles needed. With a twist: if we'd need less
        # than 1% of a tile extra, round down. This handles floating point
        # errors and other manual rounding when when the requested area size is
        # exactly a multiple of the FoV.
        area_size = [(s - f * 0.01) if s > f else s
                     for s, f in zip(self._area_size, reliable_fov)]
        nx = math.ceil(area_size[0] / reliable_fov[0])
        ny = math.ceil(area_size[1] / reliable_fov[1])
        logging.debug("Calculated number of tiles nx= %s, ny= %s" % (nx, ny))

        # We have a little bit more tiles than needed, we then have two choices
        # on how to spread them:
        # 1. Increase the total area acquired (and keep the overlap)
        # 2. Increase the overlap (and keep the total area)
        # We pick alternative 1 (no real reason)
        center = (self._area[0] + self._area[2]) / 2, (self._area[1] + self._area[3]) / 2
        total_size = nx * reliable_fov[0], ny * reliable_fov[1]

        # Compute the top-left of the "bigger" area, and from it, shift by half
        # the size of the smallest tile.
        starting_pos = {'x': center[0] - total_size[0] / 2 + reliable_fov[0] / 2,  # left
                        'y': center[1] + total_size[1] / 2 - reliable_fov[1] / 2}  # top

        return (nx, ny), starting_pos

    def _cancelAcquisition(self, future):
        """
        Canceler of acquisition task.
        """
        logging.debug("Canceling acquisition...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Acquisition cancelled.")
        return True

    def _generateScanningIndices(self, rep):
        """
        Generate the explicit X/Y position of each tile, in the scanning order
        # Go left/down, with every second line backward:
        # similar to writing/scanning convention, but move of just one unit
        # every time.
        # A-->-->-->--v
        #             |
        # v--<--<--<---
        # |
        # --->-->-->--Z
        rep (int, int): X, Y number of tiles
        return (generator of tuple(int, int)): x/y positions, starting from 0,0
        """
        # For now we do forward/backward on X (fast), and Y (slowly)
        direction = 1
        for iy in range(rep[1]):
            if direction == 1:
                for ix in range(rep[0]):
                    yield (ix, iy)
            else:
                for ix in range(rep[0] - 1, -1, -1):
                    yield (ix, iy)

            direction *= -1

    def _moveToTile(self, idx, prev_idx, tile_size):
        """
        Move the stage to the tile position
        :param idx: (tuple (float, float)) current index of tile
        :param prev_idx: (tuple (float, float)) previous index of tile
        :param tile_size: (tuple (float, float)) total tile size
        """
        overlap = 1 - self._overlap
        # don't move on the axis that is not supposed to have changed
        m = {}
        idx_change = numpy.subtract(idx, prev_idx)
        if idx_change[0]:
            m["x"] = self._starting_pos["x"] + idx[0] * tile_size[0] * overlap
        if idx_change[1]:
            m["y"] = self._starting_pos["y"] - idx[1] * tile_size[1] * overlap

        logging.debug("Moving to tile %s at %s m", idx, m)
        self._future.running_subf = self._stage.moveAbs(m)
        try:
            # Don't wait forever for the stage to move: guess the time it should
            # take and then give a large margin
            t = math.hypot(abs(idx_change[0]) * tile_size[0] * overlap,
                           abs(idx_change[1]) * tile_size[1] * overlap) / self._move_speed
            t = 5 * t + 3  # s
            self._future.running_subf.result(t)
        except TimeoutError:
            logging.warning("Failed to move to tile %s within %s s", idx, t)
            self._future.running_subf.cancel()
            # Continue acquiring anyway... maybe it has moved somewhere near

    def _sortDAs(self, das, ss):
        """
        Sorts das based on priority for stitching, i.e. largest SEM da first, then
        other SEM das, and finally das from other streams.
        das: list of DataArrays
        ss: streams from which the das were extracted

        returns: list of DataArrays, reordered input
        """
        # Add the ACQ_TYPE metadata (in case it's not there)
        # In practice, we check the stream the DA came from, and based on the stream
        # type, fill the metadata
        # TODO: make sure acquisition type is added to data arrays before, so this
        # code can be deleted
        for da in das:
            if model.MD_ACQ_TYPE in da.metadata:
                continue
            for s in ss:
                for sda in s.raw:
                    if da is sda:  # Found it!
                        if isinstance(s, EMStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_EM
                        elif isinstance(s, ARStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_AR
                        elif isinstance(s, SpectrumStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
                        elif isinstance(s, FluoStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_FLUO
                        elif isinstance(s, MultipleDetectorStream):
                            if model.MD_OUT_WL in da.metadata:
                                da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_CL
                            else:
                                da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_EM
                        else:
                            logging.warning("Unknown acq stream type for %s", s)
                        break
                if model.MD_ACQ_TYPE in da.metadata:
                    # if da is found, no need to search other streams
                    break
            else:
                logging.warning("Couldn't find the stream for DA of shape %s", da.shape)

        # # Remove the DAs we don't want to (cannot) stitch
        das = [da for da in das if da.metadata[model.MD_ACQ_TYPE] \
               not in (model.MD_AT_AR, model.MD_AT_SPECTRUM)]

        def leader_quality(da):
            """
            return int: The bigger the more leadership
            """
            # For now, we prefer a lot the EM images, because they are usually the
            # one with the smallest FoV and the most contrast
            if da.metadata[model.MD_ACQ_TYPE] == model.MD_AT_EM:
                return numpy.prod(da.shape)  # More pixel to find the overlap
            elif da.metadata[model.MD_ACQ_TYPE]:
                # A lot less likely
                return numpy.prod(da.shape) / 100

        das.sort(key=leader_quality, reverse=True)
        das = tuple(das)
        return das

    def _updateFov(self, das, sfov):
        """
        Checks the fov and update it based on the data arrays
        das: list of DataArryas
        sfov: previous estimate for the fov
        :returns same fov or updated from the data arrays
        """
        afovs = [self._getFov(d) for d in das]
        asfov = (min(f[0] for f in afovs),
                 min(f[1] for f in afovs))
        if not all(util.almost_equal(e, a) for e, a in zip(sfov, asfov)):
            logging.warning("Unexpected min FoV = %s, instead of %s", asfov, sfov)
            sfov = asfov
        return sfov

    def _estimateStreamPixels(self, s):
        """
        return (int): the number of pixels the stream will generate during an
          acquisition
        """
        px = 0
        if isinstance(s, MultipleDetectorStream):
            for st in s.streams:
                # For the EMStream of a SPARC MDStream, it's just one pixel per
                # repetition (excepted in case  of fuzzing, but let's be optimistic)
                if isinstance(st, (EMStream, CLStream)):
                    px += 1
                else:
                    px += self._estimateStreamPixels(st)

            if hasattr(s, 'repetition'):
                px *= s.repetition.value[0] * s.repetition.value[1]

            return px
        elif isinstance(s, (ARStream, SpectrumStream)):
            # Temporarily reports 0 px, as we don't stitch these streams for now
            return 0

        if hasattr(s, 'emtResolution'):
            px = numpy.prod(s.emtResolution.value)
        elif hasattr(s, 'detResolution'):
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

    MEMPP = 22  # bytes per pixel, found empirically

    def estimateMemory(self):
        """
        Makes an estimate for the amount of memory that will be consumed during
        stitching and compares it to the available memory on the computer.
        :returns (bool) True if sufficient memory available, (float) estimated memory
        """
        # Number of pixels for acquisition
        pxs = sum(self._estimateStreamPixels(s) for s in self._streams)
        pxs *= self._nx * self._ny

        # Memory calculation
        mem_est = pxs * self.MEMPP
        mem_computer = psutil.virtual_memory().total
        logging.debug("Estimating %g GB needed, while %g GB available",
                      mem_est / 1024 ** 3, mem_computer / 1024 ** 3)
        # Assume computer is using 2 GB RAM for odemis and other programs
        mem_sufficient = mem_est < mem_computer - (2 * 1024 ** 3)

        return mem_sufficient, mem_est

    STITCH_SPEED = 1e8  # px/s

    def estimateTime(self, remaining=None):
        """
        Estimates duration for acquisition and stitching.
        :param remaining: (int > 0) The number of remaining tiles
        :returns: (float) estimated required time
        """
        if remaining is None:
            remaining = self._nx * self._ny

        acq_time = 0
        for stream in self._streams:
            acq_stream_time = acqmng.estimateTime([stream])
            if stream.focuser is not None and len(self._zlevels) > 1:
                # Acquisition time for each stream will be multiplied by the number of zstack levels
                acq_stream_time *= len(self._zlevels)
            acq_time += acq_stream_time

        # Estimate stitching time based on number of pixels in the overlapping part
        max_pxs = 0
        for s in self._streams:
            for sda in s.raw:
                pxs = sda.shape[0] * sda.shape[1]
                if pxs > max_pxs:
                    max_pxs = pxs

        stitch_time = (self._nx * self._ny * max_pxs * self._overlap) / self.STITCH_SPEED
        try:
            move_time = max(self._guessSmallestFov(self._streams)) * (remaining - 1) / self._move_speed
            # current tile is part of remaining, so no need to move there
        except ValueError:  # no current streams
            move_time = 0.5

        return acq_time * remaining + move_time + stitch_time

    def _save_tiles(self, ix, iy, das, stream_cube_id=None):
        """
        Save the acquired data array to disk (for debugging)
        """

        def save_tile(ix, iy, das, stream_cube_id=None):
            if stream_cube_id is not None:
                # Indicate it's a stream cube in the file name
                fn_tile = "%s-cube%d-%.5dx%.5d%s" % (self._fn_bs, stream_cube_id, ix, iy, self._fn_ext)
            else:
                fn_tile = "%s-%.5dx%.5d%s" % (self._fn_bs, ix, iy, self._fn_ext)
            logging.debug("Will save data of tile %dx%d to %s", ix, iy, fn_tile)
            self._exporter.export(os.path.join(self._log_dir, fn_tile), das)

        # Run in a separate thread
        threading.Thread(target=save_tile, args=(ix, iy, das, stream_cube_id), ).start()

    def _acquireStreamCompressedZStack(self, i, ix, iy, stream):
        """
        Acquire a compressed zstack image for the given stream.
        The method does the following:
            - Move focus over the list of zlevels
            - For each focus level acquire image of the stream
            - Construct xyz cube for the acquired zstack
            - Compress the cube into a single image using 'maximum intensity projection'
        :return DataArray: Acquired da for the current tile stream
        """
        zstack = []
        for z in self._zlevels:
            logging.debug(f"Moving focus for tile {ix}x{iy} to {z}.")
            stream.focuser.moveAbsSync({'z': z})
            da = self._acquireStreamTile(i, ix, iy, stream)
            zstack.append(da)

        if self._future._task_state == CANCELLED:
            raise CancelledError()
        logging.debug(f"Zstack acquisition for tile {ix}x{iy}, stream {stream.name} finished, compressing data into a single image.")
        # Convert zstack into a cube
        fm_cube = assembleZCube(zstack, self._zlevels)
        # Save the cube on disk if a log path exists
        if self._log_path:
            self._save_tiles(ix, iy, fm_cube, stream_cube_id=self._streams.index(stream))

        if self._focusing_method == FocusingMethod.MAX_INTENSITY_PROJECTION:
            # Compress the cube into a single image (using maximum intensity projection)
            mip_image = numpy.amax(fm_cube, axis=0)
            if self._future._task_state == CANCELLED:
                raise CancelledError()
            logging.debug(f"Zstack compression for tile {ix}x{iy}, stream {stream.name} finished.")
            return DataArray(mip_image, copy.copy(zstack[0].metadata))
        else:
            # TODO: support stitched Z-stacks
            # For now, the init will raise NotImplementedError in such case
            logging.warning("Zstack returned as-is, while it is not supported")
            return fm_cube

    def _acquireStreamTile(self, i, ix, iy, stream):
        """
        Calls acquire function and blocks until the data is returned.
        :return DataArray: Acquired da for the current tile stream
        """
        # Update the progress bar
        self._future.set_progress(end=self.estimateTime((self._nx * self._ny) - i) + time.time())
        # Acquire data array for passed stream
        self._future.running_subf = acqmng.acquire([stream], self._settings_obs)
        das, e = self._future.running_subf.result()  # blocks until all the acquisitions are finished
        if e:
            logging.warning(f"Acquisition for tile {ix}x{iy}, stream {stream.name} partially failed: {e}")

        if self._future._task_state == CANCELLED:
            raise CancelledError()
        try:
            return das[0]  # return first da
        except IndexError:
            raise IndexError(f"Failure in acquiring tile {ix}x{iy}, stream {stream.name}.")

    def _getTileDAs(self, i, ix, iy):
        """
        Iterate over each tile stream and construct their data arrays list
        :return: list(DataArray) list of each stream DataArray
        """
        das = []
        for stream in self._streams:
            if stream.focuser is not None and len(self._zlevels) > 1:
                # Acquire zstack images based on the given zlevels, and compress them into a single da
                da = self._acquireStreamCompressedZStack(i, ix, iy, stream)
            else:
                # Acquire a single image of the stream
                da = self._acquireStreamTile(i, ix, iy, stream)
            das.append(da)
        return das

    def _acquireTiles(self):
        """
         Acquire needed tiles by moving the stage to the tile position then calling acqmng.acquire
        :return: (list of list of DataArrays): list of acquired data for each stream on each tile
        """
        da_list = []  # for each position, a list of DataArrays
        prev_idx = [0, 0]
        i = 0
        # Make sure to begin from starting position
        logging.debug("Moving to tile (0, 0) at %s m", self._starting_pos)
        self._future.running_subf = self._stage.moveAbs(self._starting_pos)
        self._future.running_subf.result()

        for ix, iy in self._generateScanningIndices((self._nx, self._ny)):
            logging.debug("Acquiring tile %dx%d", ix, iy)
            self._moveToTile((ix, iy), prev_idx, self._sfov)
            prev_idx = ix, iy

            das = self._getTileDAs(i, ix, iy)

            if i == 0:
                # Check the FoV is correct using the data, and if not update
                self._sfov = self._updateFov(das, self._sfov)

            if self._focus_stream:
                # Adjust focus of current tile and reacquire image
                das = self._adjustFocus(das, i, ix, iy)

            # Save the das on disk if an log path exists
            if self._log_path:
                self._save_tiles(ix, iy, das)

            # Sort tiles (largest sem on first position)
            da_list.append(self._sortDAs(das, self._streams))

            i += 1
        return da_list

    def _adjustFocus(self, das, i, ix, iy):
        """
        das (list of DataArray): the data of each stream which has just been acquired
        i (int): the acquisition number
        ix (int): the tile number in x
        iy (int): the tile number in y
        return (list of DataArray): the data of each stream, possibly replaced
          by a new version at a better focus level.
        """
        refocus = False
        # If autofocus explicitly disabled, or MPI => don't do anything
        if self._focusing_method in (FocusingMethod.NONE, FocusingMethod.MAX_INTENSITY_PROJECTION):
            return das
        elif self._focusing_method == FocusingMethod.ON_LOW_FOCUS_LEVEL:
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
        elif self._focusing_method == FocusingMethod.ALWAYS:
            pass
        else:
            raise ValueError(f"Unexpected focusing method {self._focusing_method}")

        try:
            self._future.running_subf = AutoFocus(self._focus_stream.detector,
                                                  self._focus_stream.emitter,
                                                  self._focus_stream.focuser,
                                                  good_focus=self._good_focus,
                                                  rng_focus=self._focus_rng,
                                                  method=MTD_EXHAUSTIVE)
            _, focus_pos = self._future.running_subf.result()  # blocks until autofocus is finished

            # Corner case where it started very badly: update the "good focus"
            # as it's likely going to be better.
            if self._good_focus_level == 0:
                self._good_focus_level = focus_pos

            if self._future._task_state == CANCELLED:
                raise CancelledError()
        except CancelledError:
            raise
        except Exception:
            logging.exception("Running autofocus failed on image i= %s." % i)
        else:
            # Reacquire the out of focus tile (which should be corrected now)
            logging.debug("Reacquiring tile %dx%d at better focus %f", ix, iy, focus_pos)
            das = self._getTileDAs(i, ix, iy)

        return das

    def _stitchTiles(self, da_list):
        """
        Stitch the acquired tiles to create a complete view of the required total area
        :return: (list of DataArrays): a stitched data for each stream acquisition
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

    def run(self):
        """
        Runs the tiled acquisition procedure
        returns:
            (list of DataArrays): a stitched data for each stream acquisition
        raise:
            CancelledError: if acquisition is cancelled
            Exception: if it failed before any result were acquired
        """
        if not self._future:
            return
        self._future._task_state = RUNNING
        st_data = []
        try:
            # Acquire the needed tiles
            da_list = self._acquireTiles()

            if not da_list or not da_list[0]:
                logging.warning("No stream acquired that can be used for stitching.")
            else:
                logging.info("Acquisition completed, now stitching...")
                # Stitch the acquired tiles
                self._future.set_progress(end=self.estimateTime(0) + time.time())
                st_data = self._stitchTiles(da_list)

            if self._future._task_state == CANCELLED:
                raise CancelledError()
        except CancelledError:
            logging.debug("Acquisition cancelled")
            raise
        except Exception as ex:
            logging.exception("Acquisition failed.")
            self._future.running_subf.cancel()
            raise
        finally:
            logging.info("Tiled acquisition ended")
            with self._future._task_lock:
                self._future._task_state = FINISHED
        return st_data


def estimateTiledAcquisitionTime(*args, **kwargs):
    """
    Estimate the time required to complete a tiled acquisition task
    Parameters are the same as for TiledAcquisitionTask
    :returns: (float) estimated required time
    """
    # Create a tiled acquisition task with future = None
    task = TiledAcquisitionTask(*args, **kwargs)
    return task.estimateTime()


def estimateTiledAcquisitionMemory(*args, **kwargs):
    """
    Estimate the amount of memory required to complete a tiled acquisition task
    Parameters are the same as for TiledAcquisitionTask
    :returns (bool) True if sufficient memory available, (float) estimated memory
    """
    # Create a tiled acquisition task with future = None
    task = TiledAcquisitionTask(*args, **kwargs)
    return task.estimateMemory()


def acquireTiledArea(streams, stage, area, overlap=0.2, settings_obs=None, log_path=None, zlevels=None,
                     registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN, focusing_method=FocusingMethod.NONE):
    """
    Start a tiled acquisition task for the given streams (SEM or FM) in order to
    build a complete view of the TEM grid. Needed tiles are first acquired for
    each stream, then the complete view is created by stitching the tiles.

    Parameters are the same as for TiledAcquisitionTask
    :return: (ProgressiveFuture) an object that represents the task, allow to
        know how much time before it is over and to cancel it. It also permits
        to receive the result of the task, which is a list of model.DataArray:
        the stitched acquired tiles data
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    future.running_subf = model.InstantaneousFuture()
    future._task_lock = threading.Lock()
    # Create a tiled acquisition task
    task = TiledAcquisitionTask(streams, stage, area, overlap, settings_obs, log_path, future=future, zlevels=zlevels,
                                registrar=registrar, weaver=weaver, focusing_method=focusing_method)
    future.task_canceller = task._cancelAcquisition  # let the future cancel the task
    # Estimate memory and check if it's sufficient to decide on running the task
    mem_sufficient, mem_est = task.estimateMemory()
    if not mem_sufficient:
        raise IOError("Not enough RAM to safely acquire the overview: %g GB needed" % (mem_est / 1024 ** 3,))

    future.set_progress(end=task.estimateTime() + time.time())
    # connect the future to the task and run in a thread
    executeAsyncTask(future, task.run)

    return future
