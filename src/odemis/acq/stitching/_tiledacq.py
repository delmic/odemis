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
from __future__ import division

import logging
import math
import os
import threading
import time
from concurrent.futures._base import RUNNING, FINISHED, CANCELLED, CancelledError

import numpy
import psutil

from odemis import model, dataio
from odemis.acq import acqmng
from odemis.acq import stitching
from odemis.acq.align.autofocus import MeasureOpticalFocus, AutoFocus, MTD_EXHAUSTIVE
from odemis.acq.stitching._constants import WEAVER_COLLAGE_REVERSE
from odemis.acq.stream import Stream, SEMStream, CameraStream, RepetitionStream, EMStream, ARStream, \
    SpectrumStream, FluoStream, MultipleDetectorStream, util, executeAsyncTask, \
    CLStream
from odemis.util import dataio as udataio
from odemis.util.comp import compute_scanner_fov, compute_camera_fov

# TODO: Find a value that works fine with cryo-secom
# Percentage of the allowed difference of tile focus from good focus
FOCUS_FIDELITY = 0.3
# Limit focus range, half the margin will be used on each side of initial focus
FOCUS_RANGE_MARGIN = 10e-5
# Indicate the number of tiles to skip during focus adjustment
SKIP_TILES = 3


class TiledAcquisitionTask(object):
    """
    The goal of this task is to acquire a set of tiles then stitch them together
    """

    def __init__(self, streams, stage, area, overlap, settings_obs=None, log_path=None, future=None):
        """
        :param streams: (Stream) the streams to acquire
        :param stage: (Actuator) the sample stage to move to the possible tiles locations
        :param area: (float, float, float, float) left, top, right, bottom points of acquisition area
        :param overlap: (float) the amount of overlap between each acquisition
        :param settings_obs: (SettingsObserver or None) class that contains a list of all VAs
            that should be saved as metadata
        :param log_path: (string) directory and filename pattern to save acquired images for debugging
        :param future: (ProgressiveFuture or None) future to track progress, pass None for estimation only
        """
        self._future = future
        self._streams = streams

        # Get total area as a tuple of width, height from ltrb area points
        area = util.normalize_rect(area)
        height = area[3] - area[1]
        width = area[2] - area[0]
        self._total_area = (width, height)

        if future:
            # Change the SEM stream horizontalFoV VA to the max if it's found
            for stream in self._streams:
                if model.hasVA(stream, "horizontalFoV"):
                    # Take the max. of either width or height
                    stream.horizontalFoV.value = max(stream.horizontalFoV.range[1])
                    # Clip horizontal fov to total area in case it's smaller than max. value
                    stream.horizontalFoV.value = stream.horizontalFoV.clip(max(self._total_area))
        # Get the smallest field of view
        self._sfov = self._guessSmallestFov(streams)

        self._overlap = overlap
        self._nx, self._ny = self._getNumberOfTiles()

        # To use in re-focusing acquired images in case they fell out of focus
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

        self._stage = stage
        self._starting_pos = {'x': area[0], 'y': area[1]}  # left, top
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

    def _getFov(self, sd):
        """
        sd (Stream or DataArray): If it's a stream, it must be a live stream,
          and the FoV will be estimated based on the settings.
        return (float, float): width, height in m
        """
        if isinstance(sd, model.DataArray):
            # The actual FoV, as the data recorded it
            return (sd.shape[0] * sd.metadata[model.MD_PIXEL_SIZE][0],
                    sd.shape[1] * sd.metadata[model.MD_PIXEL_SIZE][1])
        elif isinstance(sd, Stream):
            # Estimate the FoV, based on the emitter/detector settings
            if isinstance(sd, SEMStream):
                return compute_scanner_fov(sd.emitter)
            elif isinstance(sd, CameraStream):
                return compute_camera_fov(sd.detector)
            elif isinstance(sd, RepetitionStream):
                # CL, Spectrum, AR
                ebeam = sd.emitter
                global_fov = (ebeam.shape[0] * ebeam.pixelSize.value[0],
                              ebeam.shape[1] * ebeam.pixelSize.value[1])
                l, t, r, b = sd.roi.value
                fov = abs(r - l) * global_fov[0], abs(b - t) * global_fov[1]
                return fov
            else:
                raise TypeError("Unsupported Stream %s" % (sd,))
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
        Calculate needed number of tiles (horizontal and vertical) to cover the whole area
        """
        nx = math.ceil(abs(self._total_area[0] / ((1 - self._overlap) * self._sfov[0])))
        ny = math.ceil(abs(self._total_area[1] / ((1 - self._overlap) * self._sfov[1])))
        logging.debug("Calculated number of tiles nx= %s, ny= %s" % (nx, ny))
        return nx, ny

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
        f = self._stage.moveAbs(m)
        try:
            speed = min(self._stage.speed.value.values()) if model.hasVA(self._stage, "speed") else 10e-6
            # add 1 to make sure it doesn't time out in case of a very small move
            t = math.hypot(abs(idx_change[0]) * tile_size[0] * overlap,
                           abs(idx_change[1]) * tile_size[1] * overlap) / speed + 1
            f.result(t)
        except TimeoutError:
            logging.warning("Failed to move to tile %s", idx)
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
        asfov = (min(f[1] for f in afovs),
                 min(f[0] for f in afovs))
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
    MOVE_SPEED = 100e-6  # m/s

    def estimateTime(self, remaining=None):
        """
        Estimates duration for acquisition and stitching.
        :param remaining: (int > 0) The number of remaining tiles
        :returns: (float) estimated required time
        """
        if remaining is None:
            remaining = self._nx * self._ny
        acq_time = acqmng.estimateTime(self._streams)

        # Estimate stitching time based on number of pixels in the overlapping part
        max_pxs = 0
        for s in self._streams:
            for sda in s.raw:
                pxs = sda.shape[0] * sda.shape[1]
                if pxs > max_pxs:
                    max_pxs = pxs

        stitch_time = (self._nx * self._ny * max_pxs * self._overlap) / self.STITCH_SPEED
        try:
            move_time = max(self._guessSmallestFov(self._streams)) * (remaining - 1) / self.MOVE_SPEED
            # current tile is part of remaining, so no need to move there
        except ValueError:  # no current streams
            move_time = 0.5

        return acq_time * remaining + move_time + stitch_time

    def _save_tiles(self, ix, iy, das):
        """
        Save the acquired data array to disk (for debugging)
        """

        def save_tile(ix, iy, das):
            fn_tile = "%s-%.5dx%.5d%s" % (self._fn_bs, ix, iy, self._fn_ext)
            logging.debug("Will save data of tile %dx%d to %s", ix, iy, fn_tile)
            self._exporter.export(os.path.join(self._log_dir, fn_tile), das)

        # Run in a separate thread
        threading.Thread(target=save_tile, args=(ix, iy, das), ).start()

    def _acquireTile(self, i, ix, iy):
        """
        Calls acquire function and blocks until the data is returned.
        :return (list of DataArrays): list of acquired das for the current tile
        """
        # Update the progress bar
        self._future.set_progress(end=self.estimateTime((self._nx * self._ny) - i) + time.time())

        self._future.running_subf = acqmng.acquire(self._streams, self._settings_obs)
        das, e = self._future.running_subf.result()  # blocks until all the acquisitions are finished
        if e:
            logging.warning("Acquisition for tile %dx%d partially failed: %s",
                            ix, iy, e)

        if self._future._task_state == CANCELLED:
            raise CancelledError()
        return das

    def _acquireTiles(self):
        """
         Acquire needed tiles by moving the stage to the tile position then calling acqmng.acquire
        :return: (list of list of DataArrays): list of acquired data for each stream on each tile
        """
        da_list = []  # for each position, a list of DataArrays
        prev_idx = [0, 0]
        i = 0
        for ix, iy in self._generateScanningIndices((self._nx, self._ny)):
            logging.debug("Acquiring tile %dx%d", ix, iy)
            self._moveToTile((ix, iy), prev_idx, self._sfov)
            prev_idx = ix, iy

            das = self._acquireTile(i, ix, iy)

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
        # Run autofocus if current focus got worse than permitted deviation
        if abs(current_focus_level - self._good_focus_level) / self._good_focus_level > FOCUS_FIDELITY:
            try:
                self._future.running_subf = AutoFocus(self._focus_stream.detector,
                                                      self._focus_stream.emitter,
                                                      self._focus_stream.focuser,
                                                      good_focus=self._good_focus,
                                                      rng_focus=self._focus_rng,
                                                      method=MTD_EXHAUSTIVE)
                self._future.running_subf.result()  # blocks until autofocus is finished
                if self._future._task_state == CANCELLED:
                    raise CancelledError()
            except CancelledError:
                raise
            except Exception as ex:
                logging.exception("Running autofocus failed on image i= %s." % i)
            else:
                # Reacquire the out of focus tile (which should be corrected now)
                das = self._acquireTile(i, ix, iy)
        return das

    def _stitchTiles(self, da_list):
        """
        Stitch the acquired tiles to create a complete view of the required total area
        :return: (list of DataArrays): a stitched data for each stream acquisition
        """
        st_data = []
        logging.info("Computing big image out of %d images", len(da_list))
        # TODO: Do this registration step in a separate thread while acquiring
        das_registered = stitching.register(da_list)

        weaving_method = WEAVER_COLLAGE_REVERSE  # Method used for SECOM
        logging.info("Using weaving method WEAVER_COLLAGE_REVERSE.")
        # Weave every stream
        if isinstance(das_registered[0], tuple):
            for s in range(len(das_registered[0])):
                streams = []
                for da in das_registered:
                    streams.append(da[s])
                da = stitching.weave(streams, weaving_method)
                st_data.append(da)
        else:
            da = stitching.weave(das_registered, weaving_method)
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
            # Move stage to original position
            sub_f = self._stage.moveAbs(self._starting_pos)
            sub_f.result()

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
        except Exception as ex:
            logging.exception("Acquisition failed.")
            self._future.running_subf.cancel()
        finally:
            logging.info("Tiled acquisition ended")
            self._stage.moveAbs(self._starting_pos)
            with self._future._task_lock:
                self._future._task_state = FINISHED
        return st_data


def estimateTiledAcquisitionTime(streams, stage, area, overlap=0.2, settings_obs=None, log_path=None):
    """
    Estimate the time required to complete a tiled acquisition task
    :returns: (float) estimated required time
    """
    # Create a tiled acquisition task with future = None
    task = TiledAcquisitionTask(streams, stage, area, overlap, settings_obs, log_path, future=None)
    return task.estimateTime()


def estimateTiledAcquisitionMemory(streams, stage, area, overlap=0.2, settings_obs=None, log_path=None):
    """
    Estimate the amount of memory required to complete a tiled acquisition task
    :returns (bool) True if sufficient memory available, (float) estimated memory
    """
    # Create a tiled acquisition task with future = None
    task = TiledAcquisitionTask(streams, stage, area, overlap, settings_obs, log_path, future=None)
    return task.estimateMemory()


def acquireTiledArea(streams, stage, area, overlap=0.2, settings_obs=None, log_path=None):
    """
    Start a tiled acquisition task for the given streams (SEM or FM) in order to
    build a complete view of the TEM grid. Needed tiles are first acquired for
    each stream, then the complete view is created by stitching the tiles.

    :param streams: (Stream) the streams to acquire
    :param stage: (Actuator) the sample stage to move to the possible tiles locations
    :param area: (float, float, float, float) left, top, right, bottom points of acquisition area
    :param overlap: (float) the amount of overlap between each acquisition
    :param settings_obs: (SettingsObserver or None) class that contains a list of all VAs
        that should be saved as metadata
    :param log_path: (string) directory and filename pattern to save acquired images for debugging
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
    task = TiledAcquisitionTask(streams, stage, area, overlap, settings_obs, log_path, future=future)
    future.task_canceller = task._cancelAcquisition  # let the future cancel the task
    # Estimate memory and check if it's sufficient to decide on running the task
    mem_sufficient, mem_est = task.estimateMemory()
    if mem_sufficient:
        future.set_progress(end=mem_est + time.time())
        # connect the future to the task and run in a thread
        executeAsyncTask(future, task.run)

    return future
