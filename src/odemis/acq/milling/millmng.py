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
import threading
import time
from concurrent.futures._base import (
    CANCELLED,
    FINISHED,
    RUNNING,
    CancelledError,
    Future,
)
from typing import List

from odemis import model
from odemis.acq.acqmng import acquire
from odemis.acq.drift import align_reference_image
from odemis.acq.milling.patterns import (
    RectanglePatternParameters,
)
from odemis.acq.milling.tasks import MillingTaskSettings
from odemis.acq.stream import FIBStream
from odemis.util import executeAsyncTask


class MillingTaskManager:
    """This class manages running milling tasks."""

    def __init__(self, future: Future, tasks: List[MillingTaskSettings]):
        """
        :param future: the future that will be executing the task
        :param tasks: The milling tasks to run (in order)
        """

        self.microscope = model.getComponent(role="fibsem")
        self.tasks = tasks

        # for reference image alignment,
        self.ion_beam = model.getComponent(role="ion-beam")
        self.ion_det = model.getComponent(role="se-detector-ion")
        self.ion_focus = model.getComponent(role="ion-focus")

        self.fib_stream = FIBStream(
            name="FIB",
            detector=self.ion_det,
            dataflow=self.ion_det.data,
            emitter=self.ion_beam,
            focuser=self.ion_focus,
        )

        self._future = future
        if future is not None:
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
            self.microscope.stop_milling()
            logging.debug("Milling procedure cancelled.")
        return True

    def estimate_milling_time(self) -> float:
        """
        Estimates the milling time for the given patterns.
        :return: (float > 0): the estimated time is in seconds
        """
        return self.microscope.estimate_milling_time()

    def run_milling(self, settings: MillingTaskSettings):
        """Run the milling task with the given settings. ThermoFisher implementation"""
        microscope = self.microscope

        # get the milling settings
        milling_current = settings.milling.current.value
        milling_voltage = settings.milling.voltage.value
        milling_fov = settings.milling.field_of_view.value
        milling_channel = settings.milling.channel.value
        milling_mode = settings.milling.mode.value
        align_at_milling_current = settings.milling.align.value

        # get initial imaging settings
        imaging_current = microscope.get_beam_current(milling_channel)
        imaging_voltage = microscope.get_high_voltage(milling_channel)
        imaging_fov = microscope.get_field_of_view(milling_channel)

        # error management
        e, ce = False, False
        try:

            # acquire a reference image at the imaging settings
            if align_at_milling_current:
                self._future.running_subf = acquire([self.fib_stream])
                data, _ = self._future.running_subf.result()
                ref_image = data[0]

            # set the milling state
            microscope.clear_patterns()
            microscope.set_default_patterning_beam_type(milling_channel)
            microscope.set_high_voltage(milling_voltage, milling_channel)
            microscope.set_beam_current(milling_current, milling_channel)
            # microscope.set_field_of_view(milling_fov, milling_channel) # tmp: disable until matched in gui
            microscope.set_patterning_mode(milling_mode)

            # acquire a new image at the milling settings and align
            if align_at_milling_current:
                self._future.running_subf = acquire([self.fib_stream])
                data, _ = self._future.running_subf.result()
                new_image = data[0]
                align_reference_image(ref_image, new_image, self.ion_beam)

            # draw milling patterns to microscope
            for pattern in settings.generate():
                if isinstance(pattern, RectanglePatternParameters):
                    microscope.create_rectangle(pattern.to_json())
                else:
                    raise NotImplementedError(f"Pattern {pattern} not supported") # TODO: support other patterns

            # estimate the milling time
            estimated_time = microscope.estimate_milling_time()
            self._future.set_end_time(time.time() + estimated_time)

            # start patterning (async)
            microscope.start_milling()

            # wait for milling to finish
            elapsed_time = 0
            wait_time = 5
            while microscope.get_patterning_state() == "Running":

                with self._future._task_lock:
                    if self._future.cancelled() == CANCELLED:
                        raise CancelledError()

                logging.info(f"Milling in progress... {elapsed_time} / {estimated_time}")
                time.sleep(wait_time)
                elapsed_time += wait_time

        except CancelledError as ce:
            logging.info(f"Cancelled milling: {ce}")
        except Exception as e:
            logging.error(f"Error while milling: {e}")
        finally:
            # restore imaging state
            microscope.set_beam_current(imaging_current, milling_channel)
            microscope.set_high_voltage(imaging_voltage, milling_channel)
            microscope.set_field_of_view(imaging_fov, milling_channel)
            #microscope.set_channel(milling_channel) # TODO: expose on server
            microscope.set_active_view(2)
            microscope.clear_patterns()

            if e:
                raise e
            if ce:
                raise ce # future excepts error to be raised
        return

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously
        """
        self._future._task_state = RUNNING

        try:
            for task in self.tasks:

                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

                logging.info(f"Running milling task: {task.name}")

                self.run_milling(task)
                logging.debug("The milling completed")

        except CancelledError:
            logging.debug("Stopping because milling was cancelled")
            raise
        except Exception:
            logging.exception("The milling failed")
            raise
        finally:
            self._future._task_state = FINISHED


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
