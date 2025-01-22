# -*- coding: utf-8 -*-
"""
Created on 09 Mar 2023

@author: Canberk Akin

Copyright © 2023 Canberk Akin, Delmic

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
from enum import Enum
from typing import Dict, List


from odemis import model
from odemis.acq.acqmng import acquire
from odemis.acq.align.shift import MeasureShift
from odemis.acq.feature import (
    FEATURE_DEACTIVE,
    FEATURE_POLISHED,
    FEATURE_ROUGH_MILLED,
    FEATURE_ACTIVE,
    FEATURE_READY_TO_MILL,
    CryoFeature,
)
from odemis.acq.milling.tasks import MillingTaskManager, MillingTaskSettings
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

# TODO: replace with run_milling_tasks_openfibsem
def run_milling_tasks(tasks: List[MillingTaskSettings]) -> Future:
    """
    Run multiple milling tasks in order.
    :param tasks: List of milling tasks to be executed in order.
    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create acquisition task
    milling_task_manager = MillingTaskManager(future, tasks)
    # add the ability of cancelling the future during execution
    future.task_canceller = milling_task_manager.cancel

    # set the progress of the future (TODO: fix dummy time estimate)
    future.set_end_time(time.time() + 10 * len(tasks))

    # assign the acquisition task to the future
    executeAsyncTask(future, milling_task_manager.run)

    return future


#### NEW ####

class MillingWorkflowTask(Enum):
    TrenchMilling = "Trench Milling"
    RoughMilling = "Rough Milling"
    Polishing = "Polishing"

status_map: Dict[MillingWorkflowTask, str] = {
    MillingWorkflowTask.RoughMilling: FEATURE_ROUGH_MILLED,
    MillingWorkflowTask.Polishing: FEATURE_POLISHED,
}

def get_associated_tasks(wt: MillingWorkflowTask,
                         milling_tasks: Dict[str, MillingTaskSettings]) -> List[MillingTaskSettings]:
    associated_tasks = []
    for task in milling_tasks.values():

        if wt.value in task.name:
            associated_tasks.append(task)

        # special case for micro-expansion to associate with rough milling
        if wt is MillingWorkflowTask.RoughMilling and "Microexpansion" in task.name:
            associated_tasks.insert(0, task) # should be the first task

    return associated_tasks

def align_reference_image(ref_image: model.DataArray,
                            new_image: model.DataArray,
                            scanner: model.Emitter) -> None:
    """Align the new image to the reference image using beam shift."""
    shift_px = MeasureShift(ref_image, new_image, 10)
    # shift_px = (1, 1)
    pixelsize = ref_image.metadata[model.MD_PIXEL_SIZE]
    shift_m = (shift_px[0] * pixelsize[0], shift_px[1] * pixelsize[1])

    previous_shift = scanner.shift.value
    shift = (shift_m[0] + previous_shift[0], shift_m[1] + previous_shift[1])  # m
    scanner.shift.value = shift
    logging.debug(f"reference image alignment: previous: {previous_shift}, calculated shift: {shift_m}, beam shift: {scanner.shift.value}")


class AutomatedMillingManager(object):

    def __init__(self, future,
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
        self.posture_manager = MicroscopePostureManager(model.getMicroscope())

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

            current_posture = self.posture_manager.getCurrentPostureLabel()
            if current_posture not in [SEM_IMAGING, MILLING]:
                raise ValueError(f"Current posture is {POSITION_NAMES[current_posture]}. Please switch to SEM_IMAGING or MILLING before starting automated milling.")

            for feature in self.features:

                if feature.status.value == FEATURE_DEACTIVE:
                    logging.info(f"Skipping {feature.name.value} as it is deactivated.")
                    continue

                if feature.status.value == FEATURE_ACTIVE:
                    logging.info(f"Skipping {feature.name.value} as it is not ready for milling.")
                    continue

                self._future.msg = f"{feature.name.value}: Starting {self.current_workflow}"
                self._future.current_feature = feature
                self._future.set_progress()

                logging.info(f"Starting {self.current_workflow} for {feature.name.value}, status: {feature.status.value}")

                ############# STAGE MOVEMENT #############
                self._move_to_milling_position(feature)

                ############# ALIGNMENT #############
                self._align_reference_image(feature)

                ############# MILLLING #############
                self._run_milling_tasks(feature, workflow_task)

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

    def _run_milling_tasks(self, feature: CryoFeature, workflow_task: MillingWorkflowTask) -> None:
        """Run the milling tasks for the given feature and the workflow."""
        # get milling tasks
        milling_tasks = get_associated_tasks(
            wt=workflow_task,
            milling_tasks=feature.milling_tasks)

        self._future.msg = f"{feature.name.value}: Milling: {self.current_workflow}"
        self._future.set_progress()

        self._future.running_subf = run_milling_tasks(milling_tasks)
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
        filename = f"{feature.name.value}-Reference-Alignment-FIB.ome.tiff"
        ref_image = open_acquisition(os.path.join(feature.path, filename))[0].getData()
        pixel_size = ref_image.metadata[model.MD_PIXEL_SIZE]
        fov = pixel_size[0] * ref_image.shape[1]
        self.ion_beam.horizontalFoV.value = fov

        # beam shift alignment
        self._future.running_subf = acquire([self.fib_stream])
        data, _ = self._future.running_subf.result()
        new_image = data[0]

        # roll data by a random amount (for simulation)
        # import random
        # x, y = random.randint(0, 100), random.randint(0, 100)
        # new_image = numpy.roll(new_image, [x, y], axis=[0, 1])
        # print(f"Shifted image by {x}, {y} pixels")

        align_filename = os.path.join(feature.path, f"{feature.name.value}-{self.current_workflow}-Pre-Alignment-FIB.ome.tiff".replace(" ", "-")) # TODO: make unique?
        self._exporter.export(align_filename, new_image)

        align_reference_image(ref_image, new_image, scanner=self.ion_beam)

        # save post-alignment image
        self._future.running_subf = acquire([self.fib_stream])
        data, _ = self._future.running_subf.result()
        new_image = data[0]

        align_filename = os.path.join(feature.path, f"{feature.name.value}-{self.current_workflow}-Post-Alignment-FIB.ome.tiff".replace(" ", "-")) # TODO: make unique?
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
        sem_filename = os.path.join(feature.path, f"{feature.name.value}-{self.current_workflow}-Finished-SEM.ome.tiff".replace(" ", "-")) # TODO: make unique
        fib_filename = os.path.join(feature.path, f"{feature.name.value}-{self.current_workflow}-Finished-FIB.ome.tiff".replace(" ", "-")) # TODO: make unique
        self._exporter.export(sem_filename, sem_image)
        self._exporter.export(fib_filename, fib_image)


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
    future.set_end_time(time.time() + total_duration) # TODO: get proper time estimate

    # assign the acquisition task to the future
    executeAsyncTask(future, amm.run)

    return future
