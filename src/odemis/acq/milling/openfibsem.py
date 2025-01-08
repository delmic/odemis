
import logging
import os
import threading
import time
from concurrent import futures
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError
from typing import Dict, List

from autolamella.protocol.validation import (
    MICROEXPANSION_KEY,
    MILL_POLISHING_KEY,
    MILL_ROUGH_KEY,
)
from fibsem import utils
from fibsem.milling import FibsemMillingStage, mill_stages, estimate_total_milling_time
from fibsem.milling.patterning.patterns2 import (
    BasePattern,
    MicroExpansionPattern,
    RectanglePattern,
    TrenchPattern,
)
from fibsem.structures import FibsemMillingSettings, Point
from odemis import model

from odemis.acq.milling.patterns import (
    MicroexpansionPatternParameters,
    MillingPatternParameters,
    RectanglePatternParameters,
    TrenchPatternParameters,
)
from odemis.acq.milling.tasks import (
    MillingSettings2,
    MillingTaskSettings,
)
from odemis.util import executeAsyncTask

def convert_pattern(p: MillingPatternParameters) -> BasePattern:
    """Convert from an odemis pattern to an openfibsem pattern"""
    if isinstance(p, RectanglePatternParameters):
        return _convert_rectangle_pattern(p)
    
    if isinstance(p, TrenchPatternParameters):
        return _convert_trench_pattern(p)
    
    if isinstance(p, MicroexpansionPatternParameters):
        return _convert_microexpansion_pattern(p)

def _convert_rectangle_pattern(p: RectanglePatternParameters) -> RectanglePattern:
    return RectanglePattern(
        width=p.width.value,
        height=p.height.value,
        depth=p.depth.value,
        rotation=p.rotation.value,
        scan_direction=p.scan_direction.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_trench_pattern(p: TrenchPatternParameters) -> TrenchPattern:

    return TrenchPattern(
        width=p.width.value,
        upper_trench_height=p.height.value,
        lower_trench_height=p.height.value,
        spacing=p.spacing.value,
        depth=p.depth.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_microexpansion_pattern(p: MicroexpansionPatternParameters) -> MicroExpansionPattern:
    return MicroExpansionPattern(
        width=p.width.value,
        height=p.height.value,
        depth=p.depth.value,
        distance=p.spacing.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def convert_milling_settings(s: MillingSettings2) -> FibsemMillingSettings:
    """Convert from an odemis milling settings to an openfibsem milling settings"""
    return FibsemMillingSettings(
        milling_current=s.current.value,
        milling_voltage=s.voltage.value,
        patterning_mode=s.mode.value,
        hfw=s.field_of_view.value,
    )

# task converter
def convert_task_to_milling_stage(task: MillingTaskSettings) -> FibsemMillingStage:
    """Convert from an odemis milling task to an openfibsem milling stage"""
    s = convert_milling_settings(task.milling)
    p = convert_pattern(task.patterns[0])

    milling_stage = FibsemMillingStage(
        name=task.name,
        milling=s,
        pattern=p
    )
    return milling_stage

def convert_milling_tasks_to_milling_stages(milling_tasks: Dict[str, MillingTaskSettings]) -> List[FibsemMillingStage]:
    """Convert from odemis milling tasks to openfibsem milling stages"""
    milling_stages = []
    for task_name, task in milling_tasks.items():
        milling_stage = convert_task_to_milling_stage(task)
        milling_stages.append(milling_stage)

    return milling_stages

# convert to milling workflow
def _convert_milling_stages_to_workflow(milling_stages: List[FibsemMillingStage]) -> Dict[str, List[FibsemMillingStage]]:
    rough_milling_stages = [stage for stage in milling_stages if "rough" in stage.name.lower()]
    polishing_milling_stages = [stage for stage in milling_stages if "polishing" in stage.name.lower()]
    microexpansion_milling_stages = [stage for stage in milling_stages if "microexpansion" in stage.name.lower()]

    milling_workflow = {
        MICROEXPANSION_KEY: microexpansion_milling_stages,
        MILL_ROUGH_KEY: rough_milling_stages,
        MILL_POLISHING_KEY: polishing_milling_stages
    }

    return milling_workflow

def convert_milling_tasks_to_workflow(milling_tasks: Dict[str, MillingTaskSettings]) -> Dict[str, List[FibsemMillingStage]]:
    milling_stages = convert_milling_tasks_to_milling_stages(milling_tasks)
    return _convert_milling_stages_to_workflow(milling_stages)


class OpenFIBSEMMillingTaskManager:
    """This class manages running milling tasks via openfibsem."""

    def __init__(self, future: futures.Future, tasks: List[MillingTaskSettings]):
        """
        :param future: the future that will be executing the task
        :param tasks: The milling tasks to run (in order)
        """
        self.tasks = tasks
        self.milling_stages = convert_milling_tasks_to_milling_stages(self.tasks)

        self._future = future
        if future is not None:
            self._future.running_subf = model.InstantaneousFuture()
            self._future._task_lock = threading.Lock()

    def cancel(self, future: futures.Future) -> bool:
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
        return estimate_total_milling_time(self.milling_stages)

    def run_milling(self):
        """Run the milling tasks via openfibsem"""

        microscope, settings = utils.setup_session()            # TODO: think of a simpler way to connect this without requiring configuration.yaml
        # for this, we can probably just get the info directly, 
        # QUERY: which System settings are required?
        # QUERY: get the system settings from the microscope metadata?
        microscope._last_imaging_settings.path = os.getcwd()    # TODO: resolve the path issue

        mill_stages(microscope, self.milling_stages) # TODO: put this in subf
        # when cancel is called, stop_milling will exit the loop 
        # note: only exits the current milling stage... need to exit the whole milling process

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously
        """
        self._future._task_state = RUNNING

        try:
            self.run_milling()
        except CancelledError:
            logging.debug("Stopping because milling was cancelled")
            raise
        except Exception:
            logging.exception("The milling failed")
            raise
        finally:
            self._future._task_state = FINISHED


def run_milling_tasks_openfibsem(tasks: List[MillingTaskSettings]) -> futures.Future:
    """
    Run multiple milling tasks in order.
    :param tasks: List of milling tasks to be executed in order.
    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create milling task
    millmng = OpenFIBSEMMillingTaskManager(future, tasks)
    # add the ability of cancelling the future during execution
    future.task_canceller = millmng.cancel

    # set the progress of the future
    future.set_end_time(time.time() + millmng.estimate_milling_time() + 30)

    # assign the acquisition task to the future
    executeAsyncTask(future, millmng.run)

    return future
