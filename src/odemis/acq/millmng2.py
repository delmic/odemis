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
import time
from typing import List

from concurrent.futures import Future
from odemis import model
from odemis.acq.milling.tasks import MillingTaskManager, MillingTaskSettings
from odemis.util import executeAsyncTask


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
