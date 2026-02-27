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

from typing import Dict, List

import yaml
from odemis import model
from odemis.acq.milling.patterns import (
    MillingPatternParameters,
    PATTERN_NAME_TO_CLASS,
)


class MillingSettings:
    """Represents milling settings for a single milling task"""

    def __init__(self, current: float, voltage: float, field_of_view: float, mode: str = "Serial", channel: str = "ion", align: bool = True):
        self.current = model.FloatContinuous(current, unit="A", range=(20e-12, 120e-9))
        self.voltage = model.FloatContinuous(voltage, unit="V", range=(0, 30e3))
        self.field_of_view = model.FloatContinuous(field_of_view, unit="m", range=(50e-06, 960e-06))
        self.mode = model.StringEnumerated(mode, choices={"Serial", "Parallel"})
        self.channel = model.StringEnumerated(channel, choices={"ion"})
        self.align = model.BooleanVA(align) # align at the milling current

    def to_dict(self) -> dict:
        return {"current": self.current.value,
                "voltage": self.voltage.value,
                "field_of_view": self.field_of_view.value,
                "mode": self.mode.value,
                "channel": self.channel.value,
                "align": self.align.value}

    @staticmethod
    def from_dict(data: dict) -> "MillingSettings":
        return MillingSettings(current=data["current"],
                                voltage=data["voltage"],
                                field_of_view=data["field_of_view"],
                                mode=data.get("mode", "Serial"),
                                channel=data.get("channel", "ion"),
                                align=data.get("align", True)
                                )

    def __repr__(self):
        return f"{self.to_dict()}"


class MillingTaskSettings:
    """Represents a milling tasks, which consists of a set of patterns and settings"""

    def __init__(self, milling: MillingSettings,
                 patterns: List[MillingPatternParameters],
                 name: str,
                 selected: bool = True):
        self.name: str = name
        self.selected: bool = selected  # Whether this task should be executed or not
        self.milling: MillingSettings = milling
        self.patterns: List[MillingPatternParameters] = patterns

    def to_dict(self) -> dict:
        """Convert the parameters to a dictionary
        :return: dictionary containing the milling task settings
        """
        return {"name": self.name,
                "selected": self.selected,
                "milling": self.milling.to_dict(),
                "patterns": [pattern.to_dict() for pattern in self.patterns]}

    @staticmethod
    def from_dict(data: dict) -> "MillingTaskSettings":
        """Create a MillingTaskSettings object from a dictionary
        :param data: dictionary containing the milling task settings
        :return: MillingTaskSettings"""
        return MillingTaskSettings(
            name=data.get("name", "Milling Task"),
            selected=data.get("selected", True),
            milling=MillingSettings.from_dict(data["milling"]),
            patterns=[PATTERN_NAME_TO_CLASS[p["pattern"]].from_dict(p) for p in data["patterns"]])

    def __repr__(self):
        return f"{self.to_dict()}"

    def generate(self) -> List[MillingPatternParameters]:
        """Generate the list of invidual shapes that can be drawn on the microscope from the high-level patterns.
        :return: list of individual shapes to be drawn on the microscope
        """
        patterns = []
        if not self.selected:
            return patterns

        for pattern in self.patterns:
            patterns.extend(pattern.generate())
        return patterns


def save_milling_tasks(path: str, milling_tasks: Dict[str, MillingTaskSettings]) -> None:
    """Save milling tasks to a yaml file.
    :param path: path to the yaml file
    :param milling_tasks: dictionary of milling tasks
    :return: None
    """
    mdict = {k: v.to_dict() for k, v in milling_tasks.items()}
    with open(path, "w") as f:
        yaml.dump(mdict, f)

def load_milling_tasks(path: str) -> Dict[str, MillingTaskSettings]:
    """Load milling tasks from a yaml file.
    :param path: path to the yaml file
    :return: dictionary of milling tasks
    """
    milling_tasks = {}
    with open(path, "r") as f:
        yaml_file = yaml.safe_load(f)

    # convert the dictionary to Dict[str, MillingTaskSettings]
    milling_tasks = {k: MillingTaskSettings.from_dict(v) for k, v in yaml_file.items()}

    return milling_tasks
