import logging
import math
import os
import threading
import time
from pathlib import Path
from concurrent import futures
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError
from typing import List, Optional

from odemis import model
from odemis.acq.milling.patterns import (
    MicroexpansionPatternParameters,
    MillingPatternParameters,
    RectanglePatternParameters,
    TrenchPatternParameters,
)
from odemis.acq.milling.tasks import (
    MillingSettings,
    MillingTaskSettings,
)
from odemis.util import executeAsyncTask

# Check if fibsemOS is available
try:
    from fibsem.microscopes.odemis_microscope import OdemisThermoMicroscope, OdemisTescanMicroscope
    from fibsem.milling import (
        FibsemMillingStage,
        MillingAlignment,
        estimate_total_milling_time,
        mill_stages,
    )
    from fibsem.milling.patterning.patterns2 import (
        BasePattern,
        MicroExpansionPattern,
        RectanglePattern,
        TrenchPattern,
    )
    from fibsem.structures import FibsemMillingSettings, Point
    from fibsem.utils import load_microscope_configuration
    FIBSEMOS_INSTALLED = True
except ImportError as e:
    logging.warning(f"fibsemOS is not installed or not available: {e}")
    FIBSEMOS_INSTALLED = False


def create_fibsemos_tfs_microscope() -> 'OdemisThermoMicroscope':
    """Create a fibsemOS microscope instance with the current microscope configuration."""

    # TODO: extract the rest of the required metadata

    # stage metadata
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    pre_tilt = stage_md[model.MD_CALIB].get(model.MD_SAMPLE_PRE_TILT, math.radians(35))
    rotation_reference = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rz"]

    # loads the default config
    config = load_microscope_configuration()
    config.system.stage.shuttle_pre_tilt = math.degrees(pre_tilt)
    # Used by fibsemOS for moving the stage flat to the electron beam
    config.system.stage.rotation_reference = math.degrees(rotation_reference)
    # Used by fibsemOS for moving the stage flat to the ion beam
    config.system.stage.rotation_180 = math.degrees(rotation_reference + math.pi)
    microscope = OdemisThermoMicroscope(config.system)

    return microscope

def create_fibsemos_tescan_microscope(config_path: Path = None) -> 'OdemisTescanMicroscope':
    """Create a fibsemOS Tescan microscope instance with the current microscope configuration."""

    # TODO: Extract the rest of the required metadata

    # stage metadata
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    pre_tilt = stage_md[model.MD_CALIB].get(model.MD_SAMPLE_PRE_TILT, math.radians(35))
    rotation_reference = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rz"]

    # loads the default config
    config = load_microscope_configuration(config_path)
    config.system.stage.shuttle_pre_tilt = math.degrees(pre_tilt)
    # Used by fibsemOS for moving the stage flat to the electron beam
    config.system.stage.rotation_reference = math.degrees(rotation_reference)
    # Used by fibsemOS for moving the stage flat to the ion beam
    config.system.stage.rotation_180 = math.degrees(rotation_reference + math.pi)

    # Get the Tescan SEM component to extract host and port info
    fibsem = model.getComponent(role="fibsem")
    ip_address: str = fibsem.host
    port: int = fibsem.port
    # Pass the IP address to the fibsemOS config as well
    config.system.info.ip_address = ip_address
    microscope = OdemisTescanMicroscope(config.system)

    microscope.connect_to_microscope(ip_address, port)

    return microscope

def create_fibsemos_microscope(config_path: Path = None) -> 'OdemisThermoMicroscope | OdemisTescanMicroscope':
    """Create a fibsemOS microscope instance with the current microscope configuration.
    """
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    md_calib = stage_md.get(model.MD_CALIB, {})
    stage_version = md_calib.get("version", None)

    if stage_version == "tfs_3":
        return create_fibsemos_tfs_microscope()
    elif stage_version == "tescan_1":
        return create_fibsemos_tescan_microscope(config_path)
    else:
        raise ValueError(f"Stage version {stage_version} is not supported")


def convert_pattern_to_fibsemos(p: MillingPatternParameters) -> 'BasePattern':
    """Convert from an Odemis pattern to a fibsemOS pattern"""
    if isinstance(p, RectanglePatternParameters):
        return _convert_rectangle_pattern(p)

    elif isinstance(p, TrenchPatternParameters):
        return _convert_trench_pattern(p)

    elif isinstance(p, MicroexpansionPatternParameters):
        return _convert_microexpansion_pattern(p)
    else:
        raise NotImplementedError(f"Conversion not implemented for pattern type: {type(p)}")

def _convert_rectangle_pattern(p: RectanglePatternParameters) -> 'RectanglePattern':
    return RectanglePattern(
        width=p.width.value,
        height=p.height.value,
        depth=p.depth.value,
        rotation=p.rotation.value,
        scan_direction=p.scan_direction.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_trench_pattern(p: TrenchPatternParameters) -> 'TrenchPattern':
    return TrenchPattern(
        width=p.width.value,
        upper_trench_height=p.height.value,
        lower_trench_height=p.height.value,
        spacing=p.spacing.value,
        depth=p.depth.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_microexpansion_pattern(p: MicroexpansionPatternParameters) -> 'MicroExpansionPattern':
    return MicroExpansionPattern(
        width=p.width.value,
        height=p.height.value,
        depth=p.depth.value,
        distance=p.spacing.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def convert_milling_settings(s: MillingSettings) -> 'FibsemMillingSettings':
    """Convert from an Odemis milling settings to a fibsemOS milling settings"""
    return FibsemMillingSettings(
        milling_current=s.current.value,
        milling_voltage=s.voltage.value,
        patterning_mode=s.mode.value,
        hfw=s.field_of_view.value,
        rate=s.rate.value,                # um^3/nA/s
        dwell_time=s.dwell_time.value     # us
    )

# task converter
def convert_task_to_milling_stage(task: MillingTaskSettings) -> 'FibsemMillingStage':
    """Convert from an Odemis milling task to a fibsemOS milling stage.
    A fibsemOS milling stage is roughly equivalent to an Odemis milling task.
    """
    s = convert_milling_settings(task.milling)
    p = convert_pattern_to_fibsemos(task.patterns[0])
    a = MillingAlignment(enabled=task.milling.align.value)

    milling_stage = FibsemMillingStage(
        name=task.name,
        milling=s,
        pattern=p,
        alignment=a,
    )
    return milling_stage

def convert_milling_tasks_to_milling_stages(milling_tasks: List[MillingTaskSettings]) -> List['FibsemMillingStage']:
    """Convert from Odemis milling tasks to fibsemOS milling stages.
    An fibsemOS milling stage is roughly equivalent to an Odemis milling task.
    """
    milling_stages = []

    for task in milling_tasks:
        milling_stage = convert_task_to_milling_stage(task)
        milling_stages.append(milling_stage)

    return milling_stages

class FibsemOSMillingTaskManager:
    """This class manages running milling tasks via fibsemOS."""

    def __init__(self, future: futures.Future,
                 tasks: List[MillingTaskSettings],
                 path: Optional[str] = None,
                 config_path: Path = None):
        """
        :param future: the future that will be executing the task
        :param tasks: The milling tasks to run (in order)
        :param path: The path to save the images (optional)
        :param config_path: The path to the fibsemOS microscope configuration file
        """

        # create microscope connection
        self.microscope = create_fibsemos_microscope(config_path)
        if path is None:
            path = os.getcwd()
        self.microscope._last_imaging_settings.path = path # note: image acquisition post-milling is not yet supported via odemis

        # convert the tasks to milling stages
        self.milling_stages = convert_milling_tasks_to_milling_stages(tasks)

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

    def run_milling(self, stage: 'FibsemMillingStage') -> None:
        """Run the milling task via fibsemOS
        :param stage: the milling stage to run"""
        mill_stages(self.microscope, [stage])

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously
        """
        self._future._task_state = RUNNING

        # TODO: connect the progress signal
        try:
            for stage in self.milling_stages:
                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

                logging.info(f"Running milling stage: {stage.name}")
                self.run_milling(stage=stage)
        except CancelledError:
            logging.debug("Stopping because milling was cancelled")
            raise
        except Exception:
            logging.exception("The milling failed")
            raise
        finally:
            self._future._task_state = FINISHED


def run_milling_tasks_fibsemos(tasks: List[MillingTaskSettings],
                               path: Optional[str] = None,
                               config_path: Path = None) -> futures.Future:
    """
    Run multiple milling tasks in order via fibsemOS.
    :param tasks: List of milling tasks to be executed in order.
    :param path: The path to save the images
    :param config_path: The path to the fibsemOS microscope configuration file
    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create milling task
    millmng = FibsemOSMillingTaskManager(future, tasks, path, config_path)
    # add the ability of cancelling the future during execution
    future.task_canceller = millmng.cancel

    # set the progress of the future
    # (+30sec as estimate time only includes milling time, not current switching time, etc)
    future.set_end_time(time.time() + millmng.estimate_milling_time() + 30)

    # assign the acquisition task to the future
    executeAsyncTask(future, millmng.run)

    return future
