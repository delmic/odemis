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

This module contains structures to define milling patterns.

"""

import math
from abc import ABC, abstractmethod
from typing import List

from odemis import model

class MillingPatternParameters(ABC):
    """Represents milling pattern parameters"""

    def __init__(self, name: str):
        self.name = model.StringVA(name)

    @abstractmethod
    def to_dict(self) -> dict:
        pass

    @staticmethod
    @abstractmethod
    def from_dict(data: dict):
        pass

    def __repr__(self):
        return f"{self.to_dict()}"

    @abstractmethod
    def generate(self) -> List['MillingPatternParameters']:
        """generate the milling pattern for the microscope"""
        pass


class RectanglePatternParameters(MillingPatternParameters):
    """Represents rectangle pattern parameters"""

    def __init__(self, width: float, height: float, depth: float, rotation: float = 0.0, center = (0, 0), scan_direction: str = "TopToBottom", name: str = "Rectangle"):
        self.name = model.StringVA(name)
        self.width = model.FloatContinuous(width, unit="m", range=(1e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(1e-9, 900e-6))
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.rotation = model.FloatContinuous(rotation, unit="rad", range=(0, 2 * math.pi))
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.scan_direction = model.StringEnumerated(scan_direction, choices=set(["TopToBottom", "BottomToTop", "LeftToRight", "RightToLeft"]))

    def to_dict(self) -> dict:
        """Convert the parameters to a json object"""
        return {"name": self.name.value,
                "width": self.width.value,
                "height": self.height.value,
                "depth": self.depth.value,
                "rotation": self.rotation.value,
                "center_x": self.center.value[0],
                "center_y": self.center.value[1],
                "scan_direction": self.scan_direction.value,
                "pattern": "rectangle"
                }

    @staticmethod
    def from_dict(data: dict) -> 'RectanglePatternParameters':
        """Create a RectanglePatternParameters object from a json object"""
        return RectanglePatternParameters(width=data["width"],
                                        height=data["height"],
                                        depth=data["depth"],
                                        rotation=data.get("rotation", 0),
                                        center=(data.get("center_x", 0), data.get("center_y", 0)),
                                        scan_direction=data.get("scan_direction", "TopToBottom"),
                                        name=data.get("name", "Rectangle"))

    def __repr__(self) -> str:
        return f"{self.to_dict()}"

    def generate(self) -> List[MillingPatternParameters]:
        """Generate a list of milling shapes for the microscope.
        Note: the rectangle is a pattern that is always generated as a single shape"""
        return [self]


class TrenchPatternParameters(MillingPatternParameters):
    """Represents trench pattern parameters"""

    def __init__(self, width: float, height: float, depth: float, spacing: float, center = (0, 0), name: str = "Trench"):
        self.name = model.StringVA(name)
        self.width = model.FloatContinuous(width, unit="m", range=(1e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(1e-9, 900e-6))
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.spacing = model.FloatContinuous(spacing, unit="m", range=(1e-9, 900e-6))
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))

    def to_dict(self) -> dict:
        """Convert the parameters to a json object"""
        return {"name": self.name.value,
                "width": self.width.value,
                "height": self.height.value,
                "depth": self.depth.value,
                "spacing": self.spacing.value,
                "center_x": self.center.value[0],
                "center_y": self.center.value[1],
                "pattern": "trench"
        }

    @staticmethod
    def from_dict(data: dict) -> 'TrenchPatternParameters':
        """Create a TrenchPatternParameters object from a json object"""
        return TrenchPatternParameters(width=data["width"],
                                        height=data["height"],
                                        depth=data["depth"],
                                        spacing=data["spacing"],
                                        center=(data.get("center_x", 0), data.get("center_y", 0)),
                                        name=data.get("name", "Trench"))

    def __repr__(self) -> str:
        return f"{self.to_dict()}"

    def generate(self) -> List[MillingPatternParameters]:
        """Generate a list of milling shapes for the microscope"""
        name = self.name.value
        width = self.width.value
        height = self.height.value
        depth = self.depth.value
        spacing = self.spacing.value
        center = self.center.value

        # pattern center
        center_x = center[0]
        upper_center_y = center[1] + (height / 2 + spacing / 2)
        lower_center_y = center[1] - (height / 2 + spacing / 2)

        patterns = [
            RectanglePatternParameters(
                name=f"{name} (Upper)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (center_x, upper_center_y), # x, y
                scan_direction="TopToBottom",
            ),
            RectanglePatternParameters(
                name=f"{name} (Lower)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (center_x, lower_center_y), # x, y
                scan_direction="BottomToTop",
            ),
        ]

        return patterns


class MicroexpansionPatternParameters(MillingPatternParameters):
    """Represents microexpansion pattern parameters"""

    def __init__(self, width: float, height: float, depth: float, spacing: float, center = (0, 0), name: str = "Trench"):
        self.name = model.StringVA(name)
        self.width = model.FloatContinuous(width, unit="m", range=(1e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(1e-9, 900e-6))
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.spacing = model.FloatContinuous(spacing, unit="m", range=(1e-9, 900e-6))
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))

    def to_dict(self) -> dict:
        """Convert the parameters to a json object"""
        return {"name": self.name.value,
                "width": self.width.value,
                "height": self.height.value,
                "depth": self.depth.value,
                "spacing": self.spacing.value,
                "center_x": self.center.value[0],
                "center_y": self.center.value[1],
                "pattern": "microexpansion"
        }

    @staticmethod
    def from_dict(data: dict) -> 'MicroexpansionPatternParameters':
        """Create a MicroexpansionPatternParameters object from a json object"""
        return MicroexpansionPatternParameters(
                        width=data["width"],
                        height=data["height"],
                        depth=data["depth"],
                        spacing=data["spacing"],
                        center=(data.get("center_x", 0), data.get("center_y", 0)),
                        name=data.get("name", "Microexpansion"))

    def __repr__(self) -> str:
        return f"{self.to_dict()}"

    def generate(self) -> List[MillingPatternParameters]:
        """Generate a list of milling shapes for the microscope"""
        name = self.name.value
        width = self.width.value
        height = self.height.value
        depth = self.depth.value
        spacing = self.spacing.value / 2
        center_x, center_y = self.center.value

        patterns = [
            RectanglePatternParameters(
                name=f"{name} (Left)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (center_x - spacing, center_y),
                scan_direction="TopToBottom",
            ),
            RectanglePatternParameters(
                name=f"{name} (Right)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (center_x + spacing, center_y),
                scan_direction="TopToBottom",
            ),
        ]

        return patterns

# dictionary to map pattern names to pattern classes
PATTERN_NAME_TO_CLASS = {
    "rectangle": RectanglePatternParameters,
    "trench": TrenchPatternParameters,
    "microexpansion": MicroexpansionPatternParameters,
}
