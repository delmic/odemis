import logging
import os
import threading
import time
from concurrent.futures._base import (
    CANCELLED,
    FINISHED,
    RUNNING,
    CancelledError,
    Future,
)
from typing import Dict, List

import yaml
from odemis import model
from odemis.acq.acqmng import acquire
from odemis.acq.milling.patterns import (
    MillingPatternParameters,
    RectanglePatternParameters,
    pattern_generator,
)
from odemis.acq.stream import FIBStream
from odemis.acq.drift import align_reference_image

class MillingSettings2:
    """Represents milling settings for a single milling task"""

    def __init__(self, current: float, voltage: float, field_of_view: float, mode: str = "Serial", channel: str = "ion", align: bool = True):
        self.current = model.FloatContinuous(current, unit="A", range=(20e-12, 120e-9))
        self.voltage = model.FloatContinuous(voltage, unit="V", range=(0, 30e3))
        self.field_of_view = model.FloatContinuous(field_of_view, unit="m", range=(50e-06, 960e-06))
        self.mode = model.StringEnumerated(mode, choices=set(["Serial", "Parallel"]))
        self.channel = model.StringEnumerated(channel, choices=set(["ion"]))
        self.align = model.BooleanVA(align) # align at the milling current

    def to_json(self) -> dict:
        return {"current": self.current.value,
                "voltage": self.voltage.value,
                "field_of_view": self.field_of_view.value,
                "mode": self.mode.value,
                "channel": self.channel.value,
                "align": self.align.value}

    @staticmethod
    def from_json(data: dict) -> "MillingSettings2":
        return MillingSettings2(current=data["current"],
                                voltage=data["voltage"],
                                field_of_view=data["field_of_view"],
                                mode=data.get("mode", "Serial"),
                                channel=data.get("channel", "ion"),
                                align=data.get("align", True)
                                )

    def __repr__(self):
        return f"{self.to_json()}"


class MillingTaskSettings:
    milling: MillingSettings2
    patterns: List[MillingPatternParameters]

    def __init__(self, milling: dict, patterns: List[MillingPatternParameters], name: str = "Milling Task"):
        self.name = name
        self.milling = milling
        self.patterns = patterns

    def to_json(self) -> dict:
        return {"name": self.name, "milling": self.milling.to_json(), "patterns": [pattern.to_json() for pattern in self.patterns]}

    @staticmethod
    def from_json(data: dict):
        return MillingTaskSettings(
            name=data.get("name", "Milling Task"),
            milling=MillingSettings2.from_json(data["milling"]),
            patterns=[pattern_generator[p["pattern"]].from_json(p) for p in data["patterns"]])

    def __repr__(self):
        return f"{self.to_json()}"

    def generate(self):
        """Generate a list of milling patterns for the microscope"""
        patterns = []
        for pattern in self.patterns:
            patterns.extend(pattern.generate())
        return patterns

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


def save_milling_tasks(path: str, milling_tasks: Dict[str, MillingTaskSettings]):
    with open(os.path.join(path, "milling_tasks.yaml"), "w") as f:
        yaml.dump(milling_tasks.to_json(), f)

def load_yaml(path: str):
    with open(path, "r") as f:
        yaml_file = yaml.safe_load(f)

    return yaml_file

def load_milling_tasks(path: str, task_list: List[str] = None) -> Dict[str, MillingTaskSettings]:
    milling_tasks = {}
    task_file = load_yaml(path)

    if task_list is None:
        task_list = task_file.keys()

    for task_name in task_list:
        task = MillingTaskSettings.from_json(task_file[task_name])
        milling_tasks[task_name] = task
    return milling_tasks
