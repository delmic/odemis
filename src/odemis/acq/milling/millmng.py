# -*- coding: utf-8 -*-
"""
@author: Patrick Cleeve

Copyright © 2025 Delmic

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

This module contains classes to control the actions related to the milling.

"""
import logging
import os
import threading
import time
from concurrent.futures import Future
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError
from datetime import datetime
from enum import Enum
from typing import Dict, List


from odemis import model
from odemis.acq.acqmng import acquire
from odemis.acq.drift import align_reference_image
from odemis.acq.milling import fibsemos
from odemis.acq.feature import (
    FEATURE_DEACTIVE,
    FEATURE_POLISHED,
    FEATURE_ROUGH_MILLED,
    FEATURE_ACTIVE,
    FEATURE_READY_TO_MILL,
    CryoFeature,
    REFERENCE_IMAGE_FILENAME,
)
from odemis.acq.milling.tasks import MillingTaskSettings
from odemis.acq.milling.patterns import RectanglePatternParameters
from odemis.acq.milling.fibsemos import run_milling_tasks_fibsemos
from odemis.acq.move import (
    MILLING,
    POSITION_NAMES,
    SEM_IMAGING,
    MicroscopePostureManager,
)
from odemis.acq.stream import FIBStream, SEMStream
from odemis.dataio import find_fittest_converter
from odemis.util import executeAsyncTask
from odemis.util.dataio import open_acquisition


class TFSMillingTaskManager:
    """This class manages running milling tasks."""

    def __init__(self, future: Future, tasks: List[MillingTaskSettings], fib_stream: FIBStream, filename: str = None):
        """
        :param future: the future that will be executing the task
        :param tasks: The milling tasks to run (in order)
        :param fib_stream: The FIB stream to use for milling
        :param filename: The filename to use for saving images
        """

        self.fibsem = model.getComponent(role="fibsem")
        self.tasks = tasks

        # for reference image alignment
        self.fib_stream = fib_stream
        self.filename = filename
        if filename is not None:
            self._exporter = find_fittest_converter(filename=filename)

        self._future = future
        self._future.running_subf = model.InstantaneousFuture()
        self._future._task_lock = threading.Lock()

    def cancel(self, future: Future) -> bool:
        """
        Canceler of acquisition task.
        :param future: the future that will be executing the task
        :return: True if it successfully cancelled (stopped) the future
        """
        logging.debug("Canceling milling procedure...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            self.fibsem.stop_milling()
            logging.debug("Milling procedure cancelled.")
        return True

    def estimate_milling_time(self) -> float:
        """
        Estimates the milling time for the given patterns.
        :return: (float > 0): the estimated time is in seconds
        """
        return self.fibsem.estimate_milling_time()

    def run_milling(self, settings: MillingTaskSettings):
        """Run the milling task with the given settings. ThermoFisher implementation"""

        # get the milling settings
        milling_current = settings.milling.current.value
        milling_voltage = settings.milling.voltage.value
        milling_fov = settings.milling.field_of_view.value
        milling_channel = settings.milling.channel.value
        milling_mode = settings.milling.mode.value
        align_at_milling_current = settings.milling.align.value

        # get initial imaging settings
        imaging_current = self.fibsem.get_beam_current(milling_channel)
        imaging_voltage = self.fibsem.get_high_voltage(milling_channel)
        imaging_fov = self.fibsem.get_field_of_view(milling_channel)

        try:

            # acquire a reference image at the imaging settings
            if align_at_milling_current:
                initial_beam_shift = self.fib_stream.emitter.shift.value
                DEFAULT_ALIGNMENT_AREA = {"left": 0.7, "top": 0.3, "width": 0.25, "height": 0.4}
                self.fibsem.set_reduced_area_scan_mode(channel=milling_channel,
                                                       left=DEFAULT_ALIGNMENT_AREA["left"],
                                                       top=DEFAULT_ALIGNMENT_AREA["top"],
                                                       width=DEFAULT_ALIGNMENT_AREA["width"],
                                                       height=DEFAULT_ALIGNMENT_AREA["height"])
                self.fibsem.run_auto_contrast_brightness(channel=milling_channel)
                self._future.running_subf = acquire([self.fib_stream])
                data, _ = self._future.running_subf.result()
                ref_image = data[0]

            # set the milling state
            self.fibsem.clear_patterns()
            self.fibsem.set_default_patterning_beam_type(milling_channel)
            self.fibsem.set_high_voltage(milling_voltage, milling_channel)
            self.fibsem.set_beam_current(milling_current, milling_channel)
            # self.fibsem.set_field_of_view(milling_fov, milling_channel) # tmp: disable until matched in gui
            self.fibsem.set_patterning_mode(milling_mode)

            # acquire a new image at the milling settings and align
            if align_at_milling_current:
                # reset beam shift?
                for i in range(3):
                    self._future.running_subf = acquire([self.fib_stream])
                    data, _ = self._future.running_subf.result()
                    new_image = data[0]
                    align_reference_image(ref_image, new_image, self.fib_stream.emitter)

                # save the alignment images
                if self.filename is not None:
                    base_filename = self.filename
                    pre_filename = base_filename.replace(".ome.tiff", "-At-Imaging-Current-FIB.ome.tiff")
                    post_filename = base_filename.replace(".ome.tiff", "-At-Milling-Current-Pre-Alignment-FIB.ome.tiff")
                    self._exporter.export(pre_filename, ref_image)
                    self._exporter.export(post_filename, new_image)

                    self._future.running_subf = acquire([self.fib_stream])
                    data, _ = self._future.running_subf.result()
                    post_image = data[0]
                    post_filename = base_filename.replace(".ome.tiff", "-At-Milling-Current-Post-Alignment-FIB.ome.tiff")
                    self._exporter.export(post_filename, post_image)
                # restore full frame after alignment
                self.fibsem.set_full_frame_scan_mode(milling_channel)

            # draw milling patterns to microscope
            for pattern in settings.generate():
                if isinstance(pattern, RectanglePatternParameters):
                    self.fibsem.create_rectangle(pattern.to_dict())
                else:
                    raise NotImplementedError(f"Pattern {pattern} not supported") # TODO: support other patterns

            # estimate the milling time
            estimated_time = self.fibsem.estimate_milling_time()
            self._future.set_end_time(time.time() + estimated_time)

            # start patterning (async)
            self.fibsem.start_milling()

            # wait for milling to finish
            elapsed_time = 0
            wait_time = 5
            while self.fibsem.get_patterning_state() == "Running":

                with self._future._task_lock:
                    if self._future.cancelled() == CANCELLED:
                        raise CancelledError()

                logging.debug(f"Milling in progress... elapsed time: {elapsed_time} s, estimated time:  {estimated_time} s")
                time.sleep(wait_time)
                elapsed_time += wait_time

        except CancelledError as ce:
            logging.debug(f"Cancelled milling: {ce}")
            raise
        except Exception as e:
            logging.exception(f"Error while milling: {e}")
            raise
        finally:
            # restore imaging state
            self.fibsem.set_beam_current(imaging_current, milling_channel)
            self.fibsem.set_high_voltage(imaging_voltage, milling_channel)
            self.fibsem.set_field_of_view(imaging_fov, milling_channel)
            self.fibsem.clear_patterns()
            self.fibsem.emitter.shift.value = initial_beam_shift
        return

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously
        """
        self._future._task_state = RUNNING

        try:
            for i, task in enumerate(self.tasks, 1):

                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

                logging.debug(f"Running milling task: {task.name}")

                self.run_milling(task)
                logging.debug("The milling completed")

        except CancelledError:
            logging.debug("Stopping because milling was cancelled")
            raise
        except Exception:
            logging.warning("The milling failed")
            raise
        finally:
            self._future._task_state = FINISHED


# TODO: replace with run_milling_tasks_fibsemos
def run_milling_tasks(tasks: List[MillingTaskSettings], fib_stream: FIBStream, filename: str = None) -> Future:
    """
    Run multiple milling tasks in order.
    :param tasks: List of milling tasks to be executed in order.
    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create acquisition task
    milling_task_manager = TFSMillingTaskManager(future, tasks, fib_stream, filename)
    # add the ability of cancelling the future during execution
    future.task_canceller = milling_task_manager.cancel

    # set the progress of the future (TODO: fix dummy time estimate)
    future.set_end_time(time.time() + 10 * len(tasks))

    # assign the acquisition task to the future
    executeAsyncTask(future, milling_task_manager.run)

    return future

class MillingWorkflowTask(Enum):
    RoughMilling = "Rough Milling"
    Polishing = "Polishing"

status_map: Dict[MillingWorkflowTask, str] = {
    MillingWorkflowTask.RoughMilling: FEATURE_ROUGH_MILLED,
    MillingWorkflowTask.Polishing: FEATURE_POLISHED,
}
def get_associated_tasks(wt: MillingWorkflowTask,
                         milling_tasks: Dict[str, MillingTaskSettings]) -> List[MillingTaskSettings]:
    """Get the milling tasks associated with the given workflow task.
    :param wt: The workflow task to get associated tasks for.
    :param milling_tasks: The dictionary of all milling tasks.
    :return: List of associated tasks."""
    associated_tasks = []
    for task in milling_tasks.values():
        if not task.selected:
            continue

        if wt.value in task.name:
            associated_tasks.append(task)

        # special case for micro-expansion to associate with rough milling
        if wt is MillingWorkflowTask.RoughMilling and "Microexpansion" in task.name:
            associated_tasks.insert(0, task) # should be the first task

    return associated_tasks

class AutomatedMillingManager(object):

    def __init__(self,
                 future: Future,
                 features: List[CryoFeature],
                 stage: model.Actuator,
                 sem_stream: SEMStream,
                 fib_stream: FIBStream,
                 task_list: List[MillingWorkflowTask],
                 ):

        self.stage = stage
        self.sem_stream = sem_stream
        self.fib_stream = fib_stream
        self.ion_beam = fib_stream.emitter
        self.features = features
        self.task_list = task_list
        self._exporter = find_fittest_converter("filename.ome.tiff")
        self.pm = MicroscopePostureManager(model.getMicroscope())
        self._prefix: str = ""

        self._future = future
        if future is not None:
            self._future.running_subf = model.InstantaneousFuture()
            self._future._task_lock = threading.Lock()

    def cancel(self, future: Future) -> bool:
        """
        Canceler of milling task.
        :param future: the future that will be executing the task
        :return: True if it successfully cancelled (stopped) the future
        """
        logging.debug("Canceling milling procedure...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Milling procedure cancelled.")
        return True

    def run(self):
        self._future._task_state = RUNNING

        for task_num, workflow_task in enumerate(self.task_list, 1):

            self.current_workflow = workflow_task.value
            logging.info(f"Starting {task_num}/{len(self.task_list)}: {self.current_workflow} for {len(self.features)} features...")

            current_posture = self.pm.getCurrentPostureLabel()
            if current_posture not in [SEM_IMAGING, MILLING]:
                error_text = (f"Current posture is {POSITION_NAMES[current_posture]}. "
                               "Please switch to SEM_IMAGING or MILLING before starting automated milling.")
                logging.error(error_text)
                raise ValueError(error_text)

            for feature in self.features:

                if feature.status.value == FEATURE_DEACTIVE:
                    logging.info(f"Skipping {feature.name.value} as it is deactivated.")
                    continue
                elif feature.status.value == FEATURE_ACTIVE:
                    logging.info(f"Skipping {feature.name.value} as it is not ready for milling.")
                    continue
                elif status_map[workflow_task] == feature.status.value == FEATURE_ROUGH_MILLED:
                    logging.info(f"Skipping {feature.name.value} as it was already rough milled.")
                    continue
                elif status_map[workflow_task] == feature.status.value == FEATURE_POLISHED:
                    logging.info(f"Skipping {feature.name.value} as it was already polished.")
                    continue
                elif workflow_task == MillingWorkflowTask.RoughMilling and feature.status.value == FEATURE_POLISHED:
                    logging.info(f"Skipping {feature.name.value} as it was already rough milled and polished.")
                    continue

                # get milling tasks
                milling_tasks = get_associated_tasks(
                    wt=workflow_task,
                    milling_tasks=feature.milling_tasks)

                if not milling_tasks:
                    logging.info(f"Skipping {feature.name.value} as it has no tasks to mill.")
                    continue

                # prefix for images
                self._prefix = f"{feature.name.value}-{self.current_workflow}"

                self._future.msg = f"{feature.name.value}: Starting {self.current_workflow}"
                self._future.current_feature = feature
                self._future.set_progress()

                logging.info(f"Starting {self.current_workflow} for {feature.name.value}, status: {feature.status.value}")

                ############# STAGE MOVEMENT #############
                self._move_to_milling_position(feature)

                ############# MILLING #############
                self._run_milling_tasks(feature, milling_tasks)

                ############# REFERENCE IMAGING #############
                self._acquire_reference_images(feature)

                # update status
                feature.status.value = status_map[workflow_task]

                logging.info(f"Finished {self.current_workflow} for {feature.name.value}")

        # TODO: implement fm imaging between workflow tasks
        # TODO: configuraable settings for sem/fib imaging
        # TODO: configurable workflow for flm imaging

    def check_cancelled(self):
        with self._future._task_lock:
            if self._future.cancelled() == CANCELLED:
                raise CancelledError()

    def _move_to_milling_position(self, feature: CryoFeature) -> None:

        self.check_cancelled()
        self._future.msg = f"{feature.name.value}: Moving to Milling Position"
        self._future.set_progress()

        # milling position
        stage_position = feature.get_posture_position(MILLING)

        # move to position
        self._future.running_subf = self.stage.moveAbs(stage_position)
        self._future.running_subf.result()

        self._future.msg = f"Moved to {feature.name.value}"
        self._future.set_progress()

    def _run_milling_tasks(self, feature: CryoFeature, milling_tasks: List[MillingTaskSettings]) -> None:
        """Run the milling tasks for the given feature."""

        self._future.msg = f"{feature.name.value}: Milling: {self.current_workflow}"
        self._future.set_progress()

        if fibsemos.FIBSEMOS_INSTALLED:
            self._future.running_subf = run_milling_tasks_fibsemos(tasks=milling_tasks, feature=feature, path=feature.path)
        else:
            filename = self.get_filename(feature, "Milling-Tasks")
            self._future.running_subf = run_milling_tasks(tasks=milling_tasks,
                                                      fib_stream=self.fib_stream,
                                                      filename=filename)
        self._future.running_subf.result()

    def _align_reference_image(self, feature: CryoFeature) -> None:
        """Align the reference image to the current image using beam shift."""

        self.check_cancelled()
        self._future.msg = f"{feature.name.value}: Aligning Reference Image"
        self._future.set_progress()

        # reset beam shift
        self.ion_beam.shift.value = (0, 0)

        # match image settings for alignment
        ref_image = feature.reference_image # load from directory?
        if ref_image is None:
            filename = f"{feature.name.value}-{REFERENCE_IMAGE_FILENAME}"
            ref_image = open_acquisition(os.path.join(feature.path, filename))[0].getData()
        if ref_image is None:
            raise ValueError("Reference image not found.")
        pixel_size = ref_image.metadata[model.MD_PIXEL_SIZE]
        fov = pixel_size[0] * ref_image.shape[1]
        self.ion_beam.horizontalFoV.value = fov
        self.ion_beam.resolution.value = ref_image.shape[::-1]

        # beam shift alignment
        for i in range(3):
            self._future.running_subf = acquire([self.fib_stream])
            data, _ = self._future.running_subf.result()
            new_image = data[0]

            align_filename = self.get_filename(feature, f"Pre-Alignment-FIB-{i}")
            self._exporter.export(align_filename, new_image)

            align_reference_image(ref_image, new_image, scanner=self.ion_beam)

        # save post-alignment image
        self._future.running_subf = acquire([self.fib_stream])
        data, _ = self._future.running_subf.result()
        new_image = data[0]

        align_filename = self.get_filename(feature, "Post-Alignment-FIB")
        self._exporter.export(align_filename, new_image)

    def _acquire_reference_images(self, feature: CryoFeature) -> None:
        self.check_cancelled()

        self._future.msg = f"{feature.name.value}: Acquiring Reference Images"
        self._future.set_progress()

        # acquire images
        self._future.running_subf = acquire([self.sem_stream, self.fib_stream])
        data, ex = self._future.running_subf.result()
        sem_image, fib_image = data

        # save images
        sem_filename = self.get_filename(feature, "Finished-SEM")
        fib_filename = self.get_filename(feature, "Finished-FIB")
        self._exporter.export(sem_filename, sem_image)
        self._exporter.export(fib_filename, fib_image)

    def get_filename(self, feature: CryoFeature, basename: str) -> str:
        """Get a unique filename for the given feature and basename.
        :param feature: The feature to get the filename for.
        :param basename: The basename of the filename.
        :return: The full filename."""
        ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        filename = f"{self._prefix}-{basename}-{ts}.ome.tiff".replace(" ", "-")
        return os.path.join(os.path.join(feature.path, filename))


def run_automated_milling(features: List[CryoFeature],
                          stage: model.Actuator,
                          sem_stream: SEMStream,
                          fib_stream: FIBStream,
                          task_list: List[MillingWorkflowTask],
                          ) -> Future:
    """
    Automatically mill and image a list of features.

    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create automated milling task
    amm = AutomatedMillingManager(
        future=future,
        stage=stage,
        sem_stream=sem_stream,
        fib_stream=fib_stream,
        task_list=task_list,
        features=features,
    )
    # add the ability of cancelling the future during execution
    future.task_canceller = amm.cancel

    # set the progress of the future
    total_duration = len(task_list) * len(features) * 30
    future.set_end_time(time.time() + total_duration) # TODO: get proper time estimate from fibsemOS

    # assign the acquisition task to the future
    executeAsyncTask(future, amm.run)

    return future
