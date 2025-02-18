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

This module contains structures to define milling tasks and parameters.

"""

import os
from typing import Dict, List

import yaml
from odemis import model
from odemis.acq.milling.patterns import (
    MillingPatternParameters,
    pattern_generator,
)


class MillingSettings:
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
    def from_json(data: dict) -> "MillingSettings":
        return MillingSettings(current=data["current"],
                                voltage=data["voltage"],
                                field_of_view=data["field_of_view"],
                                mode=data.get("mode", "Serial"),
                                channel=data.get("channel", "ion"),
                                align=data.get("align", True)
                                )

    def __repr__(self):
        return f"{self.to_json()}"


class MillingTaskSettings:
    milling: MillingSettings
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
            milling=MillingSettings.from_json(data["milling"]),
            patterns=[pattern_generator[p["pattern"]].from_json(p) for p in data["patterns"]])

    def __repr__(self):
        return f"{self.to_json()}"

    def generate(self):
        """Generate a list of milling patterns for the microscope"""
        patterns = []
        for pattern in self.patterns:
            patterns.extend(pattern.generate())
        return patterns


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
