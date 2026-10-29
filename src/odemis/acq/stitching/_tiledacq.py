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
import logging
import threading
import time
from concurrent.futures import CancelledError, TimeoutError
from concurrent.futures._base import CANCELLED, FINISHED, PENDING, RUNNING
from typing import Dict, List, Optional, Tuple, Union

import numpy

from odemis import model
from odemis.acq.align.roi_autofocus import (
    autofocus_in_roi,
    estimate_autofocus_in_roi_time,
)
from odemis.acq.stitching._constants import (
    REGISTER_GLOBAL_SHIFT,
    WEAVER_MEAN,
)
from odemis.acq.stitching._tiledacq_components import (  # noqa: F401 (some are re-exported)
    START_INDEX,
    FocusingMethod,
    GridPlan,
    ProgressEstimator,
    Stitcher,
    TaskContext,
    TileAcquirer,
    TileAcquirerBase,
    TileHook,
    TileSaver,
    build_hooks,
    get_da_fov,
    get_fov,
    guess_smallest_fov,
)
from odemis.acq.stream import (
    ARStream,
    EMStream,
    FluoStream,
    MultipleDetectorStream,
    SpectrumStream,
    Stream,
    executeAsyncTask,
)
from odemis.util import rect_intersect
from odemis.util.linalg import generate_triangulation_points

# Default range for the optical focus adjustment
SAFE_REL_RANGE_DEFAULT = (-50e-6, 50e-6)  # m
# Maximum distance is used to separate two focus points in overview acquisition using autofocus
# Increasing this distance may result in out of focus overview acquisition image
MAX_DISTANCE_FOCUS_POINTS = 450e-06  # in m

DEFAULT_FOV = (100e-6, 100e-6) # m


class TiledAcquisitionTask:
    """
    The goal of this task is to acquire a set of tiles then stitch them together.
    It runs the acquisition loop, and delegates the actual work to collaborators
    (see _tiledacq_components.py). Use create_tiled_acquisition_task() to build one from the
    acquisition parameters.
    """

    def __init__(self, ctx: TaskContext, streams: List[Stream], plan: GridPlan, acquirer: TileAcquirerBase,
                 hooks: List[TileHook], stitcher: Stitcher, saver: TileSaver, estimator: ProgressEstimator):
        """
        :param ctx: the context, which holds the future of the task (None for estimation only).
          It must be the one used to build the other parts.
        :param streams: (list of Streams) the streams to acquire
        :param plan: which tiles to acquire, and how to move to them
        :param acquirer: acquires the data of one tile
        :param hooks: (list of TileHook) run, in order, around every tile
        :param stitcher: sorts the data and stitches the tiles
        :param saver: saves the tiles for debugging
        :param estimator: estimates the time and memory
        """
        self._ctx = ctx
        self._streams = streams
        self._plan = plan
        self._acquirer = acquirer
        self._hooks = hooks
        self._stitcher = stitcher
        self._saver = saver
        self._estimator = estimator

    @staticmethod
    def getFov(sd: Union[Stream, model.DataArray]) -> Tuple[float, float]:
        """
        :param sd: If it's a stream, it must be a live stream,
          and the FoV will be estimated based on the settings.
        :returns: width, height in m
        """
        if isinstance(sd, model.DataArray):
            # The actual FoV, as the data recorded it
            return get_da_fov(sd)
        elif isinstance(sd, Stream):
            return get_fov(sd)
        else:
            raise TypeError("Unsupported object")

    @classmethod
    def guessSmallestFov(cls, ss: List[Stream]) -> Tuple[float, float]:
        """
        :returns: smallest width and smallest height of all the FoV
          Note: they are not necessarily from the same FoV.
        :raises ValueError: If no stream selected
        """
        return guess_smallest_fov(ss)

    def _cancel_acquisition(self, future: model.ProgressiveFuture):
        """Canceler of acquisition task."""
        logging.debug("Canceling acquisition...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Acquisition cancelled.")
        return True

    def _update_progress(self, tile_number: int):
        """
        Update the progress bar, before acquiring the tile
        """
        self._ctx.future.set_progress(
            remaining_time=self._estimator.estimate_time(self._plan.number_of_tiles - tile_number))

    def _sort_das(self, das: List[model.DataArray], ss: List[Stream]) -> List[model.DataArray]:
        """
        Sorts das based on priority for stitching, i.e. largest SEM da first, then
        other SEM das, and finally das from other streams.
        :param das: list of DataArrays
        :param ss: streams from which the das were extracted

        :returns: list of DataArrays, reordered input
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

        # Remove the DAs we don't want to (cannot) stitch
        das = [da for da in das if da.metadata[model.MD_ACQ_TYPE] \
               not in (model.MD_AT_AR, model.MD_AT_SPECTRUM)]

        def leader_quality(da: model.DataArray) -> float:
            """
            Estimate how good a data is for computing registration between tiles.
            It favors images with more pixels, and EM images over others (as they typically have
            more contrast and smaller pixel size).
            :return: (> 0) The bigger, the better (at "leading" the stitching)
            """
            # For now, we prefer a lot the EM images, because they are usually the
            # one with the smallest FoV and the most contrast
            if da.metadata[model.MD_ACQ_TYPE] == model.MD_AT_EM:
                return numpy.prod(da.shape)  # More pixel to find the overlap
            else:
                # A lot less likely
                return numpy.prod(da.shape) / 100

        das.sort(key=leader_quality, reverse=True)
        das = tuple(das)
        return das

    def estimateMemory(self) -> Tuple[bool, float]:
        """
        Makes an estimate for the amount of memory that will be consumed during
        stitching and compares it to the available memory on the computer.
        :returns: if sufficient memory available, estimated memory
        """
        return self._estimator.estimate_memory()

    def estimateTime(self, remaining: Optional[int] = None) -> float:
        """
        Estimates duration for acquisition and stitching.
        :param remaining: (int >= 0) The number of remaining tiles
        :returns: estimated required time
        """
        return self._estimator.estimate_time(remaining)

    def _acquire_tiles(self):
        """
        Acquire needed tiles by moving the stage to the tile position then calling acqmng.acquire
        :return: (list of list of DataArrays): list of acquired data for each stream on each tile
        """
        da_list = []  # for each position, a list of DataArrays
        prev_idx = START_INDEX
        move_to_tile_start = None
        self._estimator.reset()

        for tile in self._plan.tiles():
            self._ctx.future.wait_if_paused()  # Block here between tiles if a pause is requested
            if tile.number > 0:
                elapsed = self._ctx.future.elapsed_time
                self._estimator.average_acquisition_time = elapsed / tile.number

            self._plan.move_to(tile.index, prev_idx, self._ctx)
            if move_to_tile_start:
                self._estimator.record("move", time.time() - move_to_tile_start)
            prev_idx = tile.index

            acquisition_start = time.time()
            for hook in self._hooks:
                hook.before(tile, self._ctx)

            self._update_progress(tile.number)
            logging.debug("Acquiring tile %dx%d", *tile.index)
            das = self._acquirer.get_tile_das(tile)

            for hook in self._hooks:
                das = hook.after(tile, das, self._ctx)

            self._estimator.record("acq", time.time() - acquisition_start)

            # Save the das on disk if a log path exists
            save_tile_start = time.time()
            self._saver.save(*tile.index, das)

            # Sort tiles (largest sem on first position)
            da_list.append(self._sort_das(das, self._streams))
            self._estimator.record("save", time.time() - save_tile_start)

            move_to_tile_start = time.time()

        return da_list

    def run(self):
        """
        Runs the tiled acquisition procedure
        returns:
            (list of DataArrays): a stitched data for each stream acquisition
        raise:
            CancelledError: if acquisition is cancelled
            Exception: if it failed before any result were acquired
        """
        if not self._ctx.future:
            return
        self._ctx.future._task_state = RUNNING
        st_data = []
        try:
            # Acquire the needed tiles
            da_list = self._acquire_tiles()

            if self._stitcher.enabled:
                if not da_list or not da_list[0]:
                    logging.warning("No stream acquired that can be used for stitching.")
                else:
                    logging.info("Acquisition completed, now stitching...")
                    # Stitch the acquired tiles
                    self._ctx.future.set_progress(remaining_time=self.estimateTime(0))
                    st_data = self._stitcher.stitch(da_list)

            if self._ctx.future._task_state == CANCELLED:
                raise CancelledError()
        except CancelledError:
            logging.debug("Acquisition cancelled")
            raise
        except Exception:
            logging.exception("Acquisition failed.")
            self._ctx.future.running_subf.cancel()
            raise
        finally:
            logging.info("Tiled acquisition ended")
            self._estimator.log_summary()
            self._saver.close()
            with self._ctx.future._task_lock:
                self._ctx.future._task_state = FINISHED
        return st_data


def create_tiled_acquisition_task(streams, stage, region, overlap=0.2, settings_obs=None, log_path=None,
                                  zlevels=None, registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN,
                                  focusing_method=FocusingMethod.NONE, focus_points=None, focus_range=None,
                                  centered_acq=True, future=None) -> TiledAcquisitionTask:
    """
    Build a TiledAcquisitionTask from the acquisition parameters.

    :param streams: The list of streams.
    :param stage: The stage actuator.
    :param region: The acquisition region.
    :param overlap: The overlap between tiles.
    :param settings_obs: The acquisition settings observer.
    :param log_path: The path to save logs.
    :param zlevels: The list of z-levels.
    :param registrar: The stitching registrar.
    :param weaver: The stitching weaver.
    :param focusing_method: The focusing method to use.
    :param focus_points: The list of focus points.
    :param focus_range: The focus range.
    :param centered_acq: Whether the acquisition is centered.
    :param future: The future of the task.
    :returns: The constructed TiledAcquisitionTask.
    """
    zlevels = numpy.asarray(zlevels) if zlevels is not None else numpy.empty(0)
    ctx = TaskContext(future)

    # Note: the stream FoV is not changed, as the caller might care about it.
    sfov = guess_smallest_fov(streams)
    logging.debug("Smallest FoV: %s", sfov)
    plan = GridPlan(region, overlap, sfov, stage, centered_acq)

    stitcher = Stitcher(registrar, weaver)
    saver = TileSaver(log_path)
    acquirer = TileAcquirer(ctx, streams, settings_obs, zlevels, focusing_method, saver)
    hooks = build_hooks(stage, streams, zlevels, plan, acquirer, focusing_method, focus_points, focus_range)
    estimator = ProgressEstimator(streams, plan, stitcher, zlevels)
    return TiledAcquisitionTask(ctx, streams, plan, acquirer, hooks, stitcher, saver, estimator)


def estimateTiledAcquisitionTime(streams, stage, region, overlap=0.2, settings_obs=None, log_path=None,
                                 zlevels=None, registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN,
                                 focusing_method=FocusingMethod.NONE, focus_points=None, focus_range=None,
                                 centered_acq=True, future=None) -> float:
    """
    Estimate the time required to complete a tiled acquisition task
    Parameters are the same as for create_tiled_acquisition_task
    """
    sfov = guess_smallest_fov(streams)
    plan = GridPlan(region, overlap, sfov, stage, centered_acq)
    stitcher = Stitcher(registrar, weaver)
    zlevels = numpy.asarray(zlevels) if zlevels is not None else numpy.empty(0)
    estimator = ProgressEstimator(streams, plan, stitcher, zlevels)
    return estimator.estimate_time()


def estimateTiledAcquisitionMemory(streams, stage, region, overlap=0.2, settings_obs=None, log_path=None,
                                   zlevels=None, registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN,
                                   focusing_method=FocusingMethod.NONE, focus_points=None, focus_range=None,
                                   centered_acq=True, future=None) -> Tuple[bool, float]:
    """
    Estimate the amount of memory required to complete a tiled acquisition task.
    Parameters are the same as for create_tiled_acquisition_task.
    """
    sfov = guess_smallest_fov(streams)
    plan = GridPlan(region, overlap, sfov, stage, centered_acq)
    stitcher = Stitcher(registrar, weaver)
    zlevels = numpy.asarray(zlevels) if zlevels is not None else numpy.empty(0)
    estimator = ProgressEstimator(streams, plan, stitcher, zlevels)
    return estimator.estimate_memory()


def acquireTiledArea(streams, stage, area, overlap=0.2, settings_obs=None, log_path=None, zlevels=None,
                     registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN, focusing_method=FocusingMethod.NONE,
                     focus_points=None, focus_range=None, centered_acq=True) -> model.ProgressiveFuture:
    """
    Start a tiled acquisition task for the given streams (SEM or FM) in order to
    build a complete view of the TEM grid. Needed tiles are first acquired for
    each stream, then the complete view is created by stitching the tiles.

    Parameters are the same as for create_tiled_acquisition_task
    :returns: An object that represents the task, allow to
        know how much time before it is over and to cancel it. It also permits
        to receive the result of the task, which is a list of model.DataArray:
        the stitched acquired tiles data
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    task = create_tiled_acquisition_task(streams, stage, area, overlap, settings_obs=settings_obs, log_path=log_path,
                                         zlevels= zlevels, registrar=registrar, weaver=weaver, focusing_method=focusing_method,
                                         focus_points=focus_points, focus_range=focus_range, centered_acq=centered_acq,
                                         future=future)
    future.task_canceller = task._cancel_acquisition  # let the future cancel the task
    # Estimate memory and check if it's sufficient to decide on running the task
    mem_sufficient, mem_est = task.estimateMemory()
    if not mem_sufficient:
        raise IOError("Not enough RAM to safely acquire the overview: %g GB needed" % (mem_est / 1024 ** 3,))

    future.set_progress(remaining_time=task.estimateTime())
    # connect the future to the task and run in a thread
    executeAsyncTask(future, task.run)

    return future


def estimateOverviewTime(*args, **kwargs):
    """
    Estimate the time required to complete a overview acquisition for a list of areas.
    Parameters are the same as for acquireOverview
    :returns: (float) estimated required time
    """
    # Create an overview acquisition task with future = None
    task = AcquireOverviewTask(*args, **kwargs)
    return task.estimate_remaining_time()

def acquireOverview(streams, stage, areas, focus, detector, overlap=0.2, settings_obs=None, log_path=None, zlevels=None,
                    registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN, focusing_method=FocusingMethod.NONE,
                    use_autofocus: bool = False, focus_points_dist: float = MAX_DISTANCE_FOCUS_POINTS):
    """
    Start autofocus and tiled acquisition tasks for each area in the list of area which is
    given by the input argument areas.

    :param streams: (list of Stream) the streams to acquire
    :param stage: (Actuator) the stage to move
    :param areas: (list of areas) List of areas where an overview image should be acquired.
        Each area is defined by 4 floats :left, top, right, bottom positions
        of the acquisition area (in m)
    :param focus: (Actuator) the focus actuator
    :param detector: (Detector) the detector to use for the acquisition
    :param overlap: (0<float<1) the overlap between tiles (in percentage)
    :param settings_obs: (dict) the settings for the acquisition
    :param log_path: (str) path to the log file
    :param zlevels: (list of floats) the zlevels to use for the stitching, in meters and as relative positions (from the
        focus points
    :param registrar: (str) the type of registration to use
    :param weaver: (str) the type of weaver to use
    :param focusing_method: (str) the focusing method to use
    :param use_autofocus: (bool) whether to use autofocus or not
    :return: (ProgressiveFuture) an object that represents the task, allow to
        know how much time before it is over and to cancel it. It also permits
        to receive the result of the task, which is a list of model.DataArray:
        the stitched acquired tiles data for each area
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    task = AcquireOverviewTask(streams, stage, areas, focus, detector, future, overlap, settings_obs,
                               log_path, zlevels,
                               registrar, weaver, focusing_method, use_autofocus, focus_points_dist)
    future.task_canceller = task.cancel  # let the future cancel the task

    future.set_progress(remaining_time=task.estimate_remaining_time())
    # connect the future to the task and run in a thread
    executeAsyncTask(future, task.run)

    return future


class AcquireOverviewTask(object):
    """
    Create a task to run autofocus and tiled acquisition for each area in the list of areas
    """

    def __init__(self, streams, stage, areas, focus, detector, future=None,
                 overlap=0.2, settings_obs=None, log_path=None,
                 zlevels=None, registrar=REGISTER_GLOBAL_SHIFT, weaver=WEAVER_MEAN,
                 focusing_method=FocusingMethod.NONE, use_autofocus: bool = False,
                 focus_points_dist: float = MAX_DISTANCE_FOCUS_POINTS):
        # site and feature means the same
        # list of time taken for each task i.e autofocus and tiled acquisition for each area
        self.time_per_task = []
        self._stage = stage
        self._future = future
        if future is not None:
            self._future.running_subf = model.InstantaneousFuture()
            self._future._task_lock = threading.Lock()
            self._future._task_state = PENDING
        self.streams = streams
        self.areas = areas  # list of areas
        self._det = detector  # TODO to delete after ccd.data components is used from streams in do_autofocus_roi
        self._focus = focus
        active_rng = self._focus.getMetadata().get(model.MD_POS_ACTIVE_RANGE, None)

        if active_rng and "z" in active_rng:
            self.focus_rng = tuple(active_rng["z"])
        else:
            # No absolute range is available, so use a relative range (e.g. as on METEOR
            rel_rng = self._focus.getMetadata().get(model.MD_SAFE_REL_RANGE, SAFE_REL_RANGE_DEFAULT)
            self.focus_rng = (self._focus.position.value["z"] + rel_rng[0],
                              self._focus.position.value["z"] + rel_rng[1])

        self.conf_level = 0.8
        self.focusing_method = focusing_method
        self._use_autofocus: bool = use_autofocus
        self._focus_points = []  # list of focus points per each area in areas
        self._total_nb_focus_points = 0
        for area in areas:
            focus_points = generate_triangulation_points(focus_points_dist, area)
            self._total_nb_focus_points += len(focus_points)
            self._focus_points.append(focus_points)
        self._overlap = overlap
        self._settings_obs = settings_obs
        self._log_path = log_path
        self._zlevels = zlevels
        self._registrar = registrar
        self._weaver = weaver

    def cancel(self, future):
        """
        Canceler of acquisition task.
        """
        logging.debug("canceling acquisition overview...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("acquisition overview cancelled.")
        return True

    def estimate_remaining_time(self) -> float:
        """
        Estimates the time for the rest of the acquisition.
        :return: (float) the estimated time for the rest of the acquisition
        """
        autofocus_time = 0
        self.time_per_task= []
        if self._use_autofocus:
            autofocus_time = estimate_autofocus_in_roi_time(self._total_nb_focus_points, self._det, self._focus, self.focus_rng)
        tiled_time = 0
        for area in self.areas:
            tiled_acq_time = estimateTiledAcquisitionTime(
                                    self.streams, self._stage, area,
                                    focus_range=self.focus_rng,
                                    overlap=self._overlap,
                                    settings_obs=self._settings_obs,
                                    log_path=self._log_path,
                                    zlevels=self._zlevels,
                                    registrar=self._registrar,
                                    weaver=self._weaver,
                                    focusing_method=self.focusing_method)
            tiled_time += tiled_acq_time

            if self._use_autofocus:
                self.time_per_task.extend([autofocus_time, tiled_acq_time])
            else:
                self.time_per_task.append(tiled_acq_time)


        logging.debug(f"Estimated autofocus time: {autofocus_time * len(self.areas)} s, Tiled acquisition time: {tiled_time} s")
        acquisition_time = autofocus_time + tiled_time

        return acquisition_time

    def _pass_future_progress(self, future, elapsed_time: float, remaining_time: float) -> None:
        # update the main future with running sub future time estimate and time estimation of remaining tasks
        self._future.set_progress(remaining_time=remaining_time + sum(self.time_per_task))

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously
        """
        if not self._future:
            raise ValueError("To execute the task, you should pass a Future at init")

        self._future._task_state = RUNNING
        da_rois = []
        try:
            self._future.set_progress(remaining_time=self.estimate_remaining_time())
            # create a for loop for roi to create sub futures
            for idx, roi in enumerate(self.areas):

                focus_points = None
                if self._use_autofocus:
                    # cancel the sub future
                    with self._future._task_lock:
                        if self._future._task_state == CANCELLED:
                            raise CancelledError()
                        # is_active set to True will keep the acquisition going on
                        self.streams[0].is_active.value = True
                        logging.debug(f"Autofocus is running for roi number {idx}, with bounding box: {roi} [m]")
                        # run autofocus for the selected roi

                        self._future.running_subf = autofocus_in_roi(roi,
                                                                    self._stage,
                                                                    self._det,
                                                                    self._focus,
                                                                    self.focus_rng,
                                                                    self._focus_points[idx],
                                                                    self.conf_level)
                        # remove the current autofocus time from the list and assign the remaining time to the future
                        # along with the time update of the current autofocus sub future
                        self.time_per_task.pop(0)
                        self._future.running_subf.add_update_callback(self._pass_future_progress)

                    try:
                        focus_points_per_area = len(self._focus_points[idx])
                        max_wait_t = estimate_autofocus_in_roi_time(focus_points_per_area, self._det, self._focus, self.focus_rng) * 3 + 1
                        focus_points = self._future.running_subf.result(max_wait_t)
                    except TimeoutError:
                        logging.debug(f"Autofocus timed out for roi number {idx}, with bounding box: {roi} [m]")
                        raise
                    except Exception:
                        logging.debug(f"Autofocus failed for roi number {idx}, with bounding box: {roi} [m]")
                        raise
                    finally:
                        self.streams[0].is_active.value = False

                # cancel the sub future
                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

                    # run tiled acquisition for the selected roi
                    logging.debug(f"Z-stack acquisition is running for roi number {idx} with {roi} values")

                    self._future.running_subf = acquireTiledArea(streams=self.streams,
                                                                 stage=self._stage,
                                                                 area=roi,
                                                                 focus_range=self.focus_rng,
                                                                 overlap=self._overlap,
                                                                 settings_obs=self._settings_obs,
                                                                 log_path=self._log_path,
                                                                 zlevels=self._zlevels,
                                                                 registrar=self._registrar,
                                                                 weaver=self._weaver,
                                                                 focusing_method=self.focusing_method,
                                                                 focus_points=focus_points)
                    # remove the current tiled acquisition time from the list and assign the remaining time to the future
                    # along with the time update of the current tiled acquisition sub future
                    self.time_per_task.pop(0)
                    self._future.running_subf.add_update_callback(self._pass_future_progress)

                try:
                    da = self._future.running_subf.result()
                    # append all of them, when multiple streams are acquired
                    da_rois.extend(da)
                except Exception:
                    logging.debug(
                        f"Z-stack acquisition within roi failed for roi number {idx} with {roi}")
                    raise

                logging.debug(f"acquisition overview is completed for roi number {idx} with {roi} values")

                # cancel the sub future
                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

        except CancelledError:
            logging.debug("Stopping because acquisition overview was cancelled")
            raise
        except Exception:
            logging.exception("acquisition overview failed")
            self._future.running_subf.cancel()
            raise
        finally:
            # state that the future has finished
            with self._future._task_lock:
                self._future._task_state = FINISHED

        return da_rois

def get_stream_based_bbox(
    pos: Dict[str, float],
    streams: List[Stream],
    tiles_nx: int,
    tiles_ny: int,
    overlap: float,
    tiling_rng: Dict[str, list],  # TODO: make optional, use axes range otherwise
) -> Optional[Tuple[float]]:
    """
    Compute a bounding box based on streams, based on the number of x tiles, stream fov and overlap.
    :param pos: the current position of the stage
    :param streams: the streams to acquire
    :param tiles_nx: the number of tiles in the x direction
    :param tiles_ny: the number of tiles in the y direction
    :param overlap: the overlap between tiles (in percentage)
    :param tiling_rng: the tiling range along x and y axes as (xmin, ymin, xmax, ymax), or (xmin, ymax, xmax, ymin)
    :return: the bounding box (xmin, ymin, xmax, ymax in physical coordinates). If no area at all, returns None.
    """
    # Get al stream fov's, if any
    fovs = [get_fov(s) for s in streams]
    if not fovs:
        # fall back to a small fov (default)
        fov = DEFAULT_FOV
    else:
        # smallest fov
        fov = tuple(map(min, zip(*fovs)))

    return get_fov_based_bbox(
        pos=pos,
        fov=fov,
        tiles_nx=tiles_nx,
        tiles_ny=tiles_ny,
        tiling_rng=tiling_rng,
        overlap=overlap,
    )


def get_tiled_bboxes(
    rel_bbox: Tuple[float],
    sample_centers: List[Tuple[float]],
) -> List[Tuple[float]]:
    """
    Compute bounding boxes for a given relative bounding box to different sample centers
    :param rel_bbox: the relative bounding box of the sample grid (xmin, ymin, xmax, ymax)
    :param sample_centers: the centers of the sample grids
    return: the bounding boxes (xmin, ymin, xmax, ymax in physical coordinates)
    corresponding to the sample centers.
    """
    # TODO: ensure that sample centers are converted to same coordinate system as stage position

    # Sort the (selected) centers along X, and for centers with the same X, along the Y.
    # In theory, the order doesn't really matter (at best it would safe a few
    # seconds of stage movement), but it's nice for the user that acquisitions
    # are done always in the same order, as it reduces "astonishment".
    sorted_centers = sorted(sample_centers)
    bboxes = [
        (
            center[0] + rel_bbox[0],
            center[1] + rel_bbox[1],
            center[0] + rel_bbox[2],
            center[1] + rel_bbox[3],
        )
        for center in sorted_centers
    ]

    return bboxes

def get_fov_based_bbox(
    pos: Dict[str, float],
    fov: Tuple[float, float],
    tiles_nx: int,
    tiles_ny: int,
    tiling_rng: Dict[str, list],
    overlap: float,
) -> Optional[Tuple[float, float]]:
    """
    Calculates the requested bounding box, based on the number of tiles, fov and overlap.
    :param pos: the current position of the stage
    :param fov: the fov to acquire, default is DEFAULT_FOV
    :param tiles_nx: the number of tiles in the x direction
    :param tiles_ny: the number of tiles in the y direction
    :param tiling_rng: the tiling range along x and y axes as (xmin, ymin, xmax, ymax), or (xmin, ymax, xmax, ymin)
    :param overlap: the overlap between tiles (in percentage)
    :return: the bounding box (xmin, ymin, xmax, ymax in physical coordinates). If no area at all, returns None.
    """
    # these formulas for w and h have to match the ones used in the 'stitching' module.
    w = tiles_nx * fov[0] * (1 - overlap)
    h = tiles_ny * fov[1] * (1 - overlap)

    # Note the area can accept LTRB or LBRT.
    bbox = clip_tiling_bbox_to_range(w, h, pos, tiling_rng)
    if bbox is None:
        # there is no intersection
        logging.warning(
            "Couldn't find intersection between stage pos %s and tiling range %s"
            % (pos, tiling_rng)
        )

    return bbox

def clip_tiling_bbox_to_range(
    w: float, h: float, pos: Dict[str, float], tiling_rng: Dict[str, List[float]]
) -> Optional[Tuple[float]]:
    """
    Finds the intersection between the requested tiling bounding box and the tiling range.
    :param w: the width of the tiling bounding box
    :param h: the height of the tiling bounding box
    :param pos: the current position of the stage
    :param tiling_rng: the maximum tiling range along x and y axes as
            (xmin, ymin, xmax, ymax), or (xmin, ymax, xmax, ymin). the maximum range is
            specified by the task, and is usually the stage axes range or the imaging range.
    :returns: (None or tuple of 4 floats): None if there is no intersection, or
        the rectangle representing the intersection as (xmin, ymin, xmax, ymax).
    """
    bbox_req = (pos["x"] - w / 2, pos["y"] - h / 2, pos["x"] + w / 2, pos["y"] + h / 2)
    # clip the tiling area, if needed (or find the intersection between the active range and the requested area)
    return rect_intersect(
        bbox_req,
        (
            tiling_rng["x"][0],
            tiling_rng["y"][1],
            tiling_rng["x"][1],
            tiling_rng["y"][0],
        ),
    )

def get_zstack_levels(zsteps: int, zstep_size: float, rel: bool = False, focuser=None):
    """
    Calculate the zstack levels from the current focus position and zsteps value
    :param zsteps: (int) the number of zsteps
    :param zstep_size: (float) the size of each zstep
    :param rel: If this is False (default), then z stack levels are in absolute values. If rel is set to True then
        the z stack levels are calculated relative to each other.
    :param focuser: (Actuator) the focuser to use for the zstack levels
    :returns:
        (list(float) or None) zstack levels for zstack acquisition. None if only one zstep is requested.
    """
    # TODO: consolidate with odemis.util.comp.generate_zlevels
    # TODO: fix corner cases for shifting the zlevels

    if zsteps == 1:
        return None

    if not rel and focuser is None:
        raise ValueError("A focuser is required to calculate absolute zstack levels.")

    if rel:
        zmin = -(zsteps / 2 * zstep_size)
        zmax = +(zsteps / 2 * zstep_size)
    else:
        # Clip zsteps value to allowed range
        focus_value = focuser.position.value["z"]
        focus_range = focuser.axes["z"].range
        zmin = focus_value - (zsteps / 2 * zstep_size)
        zmax = focus_value + (zsteps / 2 * zstep_size)
        if (zmax - zmin) > (focus_range[1] - focus_range[0]):
            # Corner case: it'd be larger than the entire range => limit to the entire range
            zmin = focus_range[0]
            zmax = focus_range[1]
        if zmax > focus_range[1]: # FIXME: this can shift the focus outside of the range
            # Too high => shift down
            shift = zmax - focus_range[1]
            zmin -= shift
            zmax -= shift

        if zmin < focus_range[0]: # FIXME: this can shift the focus outside of the range
            # Too low => shift up
            shift = focus_range[0] - zmin
            zmin += shift
            zmax += shift

    # TODO: if there is an even number of zsteps, the current focus position will not be part of the zlevels.
    # As the current focus position can often be the "best" position, we could change the algorithm in such case to
    # shift exactly symmetrical, but instead use the current position as one of the zlevels.

    # Create focus zlevels from the given zsteps number
    zlevels = numpy.linspace(zmin, zmax, zsteps).tolist()

    return zlevels
