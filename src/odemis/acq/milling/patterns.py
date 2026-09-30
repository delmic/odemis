"""
@author: Patrick Cleeve, Alexéy Ilyushkin

Copyright © 2025-2026 Patrick Cleeve, Alexéy Ilyushkin, Delmic

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
from typing import List, Tuple

from odemis import model

class MillingPatternParameters(ABC):
    """Represents milling pattern parameters"""

    FEATURE_CENTER_OFFSET = (0.0, 0.0)

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

    def get_center_at_feature(
        self,
        feature_center: Tuple[float, float],
    ) -> Tuple[float, float]:
        """Return this pattern's center when arranged around a feature.

        :param feature_center: Feature position relative to the reference image.
        :return: Pattern center including its pattern-specific layout offset.
        """
        return (
            feature_center[0] + self.FEATURE_CENTER_OFFSET[0],
            feature_center[1] + self.FEATURE_CENTER_OFFSET[1],
        )

    @abstractmethod
    def generate(self) -> List['MillingPatternParameters']:
        """generate the milling pattern for the microscope"""
        pass


class RectanglePatternParameters(MillingPatternParameters):
    """Represents rectangle pattern parameters"""

    def __init__(self, width: float, height: float, depth: float, rotation: float = 0.0,
                 center=(0, 0), scan_direction: str = "TopToBottom", name: str = "Rectangle",
                 spot_size_correction: float = 0.0):
        self.name = model.StringVA(name)
        self.width = model.FloatContinuous(width, unit="m", range=(1e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(1e-9, 900e-6))
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.rotation = model.FloatContinuous(rotation, unit="rad", range=(0, 2 * math.pi))
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.scan_direction = model.StringEnumerated(scan_direction, choices=set(["TopToBottom", "BottomToTop", "LeftToRight", "RightToLeft"]))
        self.spot_size_correction = model.FloatContinuous(spot_size_correction, unit="m", range=(0, 900e-6))

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
                "spot_size_correction": self.spot_size_correction.value,
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
                                        name=data.get("name", "Rectangle"),
                                        spot_size_correction=data.get("spot_size_correction", 0.0))

    def __repr__(self) -> str:
        return f"{self.to_dict()}"

    def generate(self) -> List[MillingPatternParameters]:
        """Generate a list of milling shapes for the microscope.
        Note: the rectangle is a pattern that is always generated as a single shape"""
        return [self]


class TrenchPatternParameters(MillingPatternParameters):
    """Represents trench pattern parameters"""

    def __init__(self, width: float, height: float, depth: float, spacing: float,
                 center=(0, 0), name: str = "Trench", spot_size_correction: float = 0.0):
        self.name = model.StringVA(name)
        self.width = model.FloatContinuous(width, unit="m", range=(1e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(1e-9, 900e-6))
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.spacing = model.FloatContinuous(spacing, unit="m", range=(1e-9, 900e-6))
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.spot_size_correction = model.FloatContinuous(spot_size_correction, unit="m", range=(0, 900e-6))

    def to_dict(self) -> dict:
        """Convert the parameters to a json object"""
        return {"name": self.name.value,
                "width": self.width.value,
                "height": self.height.value,
                "depth": self.depth.value,
                "spacing": self.spacing.value,
                "center_x": self.center.value[0],
                "center_y": self.center.value[1],
                "spot_size_correction": self.spot_size_correction.value,
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
                                        name=data.get("name", "Trench"),
                                        spot_size_correction=data.get("spot_size_correction", 0.0))

    def __repr__(self) -> str:
        return f"{self.to_dict()}"

    def generate(self) -> List[MillingPatternParameters]:
        """Generate a list of milling shapes for the microscope"""
        name = self.name.value
        width = self.width.value
        height = self.height.value
        depth = self.depth.value
        spacing = self.spacing.value
        spot_size_correction = self.spot_size_correction.value
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
                spot_size_correction=spot_size_correction,
            ),
            RectanglePatternParameters(
                name=f"{name} (Lower)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (center_x, lower_center_y), # x, y
                scan_direction="BottomToTop",
                spot_size_correction=spot_size_correction,
            ),
        ]

        return patterns


class MicroexpansionPatternParameters(MillingPatternParameters):
    """Represents microexpansion pattern parameters"""

    def __init__(self, width: float, height: float, depth: float, spacing: float,
                 center=(0, 0), name: str = "Trench", spot_size_correction: float = 0.0):
        self.name = model.StringVA(name)
        self.width = model.FloatContinuous(width, unit="m", range=(1e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(1e-9, 900e-6))
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.spacing = model.FloatContinuous(spacing, unit="m", range=(1e-9, 900e-6))
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.spot_size_correction = model.FloatContinuous(spot_size_correction, unit="m", range=(0, 900e-6))

    def to_dict(self) -> dict:
        """Convert the parameters to a json object"""
        return {"name": self.name.value,
                "width": self.width.value,
                "height": self.height.value,
                "depth": self.depth.value,
                "spacing": self.spacing.value,
                "center_x": self.center.value[0],
                "center_y": self.center.value[1],
                "spot_size_correction": self.spot_size_correction.value,
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
                        name=data.get("name", "Microexpansion"),
                        spot_size_correction=data.get("spot_size_correction", 0.0))

    def __repr__(self) -> str:
        return f"{self.to_dict()}"

    def generate(self) -> List[MillingPatternParameters]:
        """Generate a list of milling shapes for the microscope"""
        name = self.name.value
        width = self.width.value
        height = self.height.value
        depth = self.depth.value
        spacing = self.spacing.value
        spot_size_correction = self.spot_size_correction.value
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
                spot_size_correction=spot_size_correction,
            ),
            RectanglePatternParameters(
                name=f"{name} (Right)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (center_x + spacing, center_y),
                scan_direction="TopToBottom",
                spot_size_correction=spot_size_correction,
            ),
        ]

        return patterns


class CompositeRectanglePatternParameters(MillingPatternParameters):
    """Marker base for patterns composed of rectangle milling shapes."""


class RulerPatternParameters(CompositeRectanglePatternParameters):
    """Two mirrored rulers, with alternating horizontal graduations bottom to top.

    Width is the even-numbered graduation length; odd-numbered graduations are 3/4
    of that length. Spacing is the gap between the inner edges of the
    rulers. Height includes the full extent of the graduations. Each graduation is
    one fifth of the pitch thick, so changing height scales the whole ruler.
    ``num_graduations`` counts all graduations on each side, including the zero mark.
    """

    PARAMETER_TOOLTIPS = {
        "width": (
            "Length of the longest graduations (even-numbered). "
            "Odd-numbered graduations are 3/4 as long."),
        "height": "Overall ruler length, including the first and last graduations.",
        "spacing": "Gap between the inner edges of the two rulers.",
        "num_graduations": (
            "Graduations on each side, starting at zero at the bottom and "
            "alternating long and short."),
    }

    def __init__(self, width: float, height: float, depth: float, spacing: float,
                 num_graduations: int = 11, center: Tuple[float, float] = (0, 0),
                 name: str = "Ruler", spot_size_correction: float = 0.0) -> None:
        """Initialize ruler pattern parameters.

        :param width: Length of each long graduation.
        :param height: Overall ruler height.
        :param depth: Milling depth of each graduation.
        :param spacing: Gap between the rulers' inner edges.
        :param num_graduations: Number of graduations on each side.
        :param center: Center of the complete ruler pattern.
        :param name: Pattern name.
        :param spot_size_correction: Beam spot size correction.
        """
        self.name = model.StringVA(name)
        # Short graduations must also meet the rectangle's minimum width of 1 nm.
        self.width = model.FloatContinuous(width, unit="m", range=(2e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(6e-9, 900e-6), setter=self._set_height)
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.spacing = model.FloatContinuous(spacing, unit="m", range=(1e-9, 900e-6))
        self.num_graduations = model.IntContinuous(num_graduations, range=(2, 1000), setter=self._set_num_graduations)
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.spot_size_correction = model.FloatContinuous(spot_size_correction, unit="m", range=(0, 900e-6))
        self._check_graduation_height(height, num_graduations)

    @staticmethod
    def _check_graduation_height(height: float, num_graduations: int) -> None:
        """Validate that the generated graduations meet the minimum height.

        :param height: Overall ruler height.
        :param num_graduations: Number of graduations on each side.
        :raises ValueError: If a graduation would be less than one nanometer high.
        """
        if height / (5 * num_graduations - 4) < 1e-9:
            raise ValueError("Ruler height is too small for this number of graduations (minimum thickness is 1 nm)")

    def _set_height(self, height: float) -> float:
        """Validate and return a new ruler height.

        :param height: Proposed ruler height.
        :return: Validated ruler height.
        """
        self._check_graduation_height(height, self.num_graduations.value)
        return height

    def _set_num_graduations(self, num_graduations: int) -> int:
        """Validate and return a new graduation count.

        :param num_graduations: Proposed number of graduations on each side.
        :return: Validated graduation count.
        """
        self._check_graduation_height(self.height.value, num_graduations)
        return num_graduations

    def to_dict(self) -> dict:
        """Serialize the ruler parameters.

        :return: Serializable ruler parameters.
        """
        return {"name": self.name.value,
                "width": self.width.value,
                "height": self.height.value,
                "depth": self.depth.value,
                "spacing": self.spacing.value,
                "num_graduations": self.num_graduations.value,
                "center_x": self.center.value[0],
                "center_y": self.center.value[1],
                "spot_size_correction": self.spot_size_correction.value,
                "pattern": "ruler"}

    @staticmethod
    def from_dict(data: dict) -> 'RulerPatternParameters':
        """Create ruler parameters from serialized data.

        :param data: Serialized ruler parameters.
        :return: Restored ruler parameters.
        """
        return RulerPatternParameters(
            width=data["width"],
            height=data["height"],
            depth=data["depth"],
            spacing=data["spacing"],
            num_graduations=data.get("num_graduations", 11),
            center=(data.get("center_x", 0), data.get("center_y", 0)),
            name=data.get("name", "Ruler"),
            spot_size_correction=data.get("spot_size_correction", 0.0))

    def generate(self) -> List[MillingPatternParameters]:
        """Generate rectangles alternating between full and three-quarter width.

        :return: Rectangle parameters for both sides of the ruler.
        """
        count = self.num_graduations.value
        graduation_height = self.height.value / (5 * count - 4)
        pitch = 5 * graduation_height
        center_x, center_y = self.center.value
        bottom_y = center_y - (self.height.value - graduation_height) / 2
        patterns = []
        for index in range(count):
            width = self.width.value if index % 2 == 0 else self.width.value * 0.75

            # All graduations start at the inner edge and extend outwards.
            offset_x = (self.spacing.value + width) / 2
            for side, direction in (("Left", -1), ("Right", 1)):
                patterns.append(RectanglePatternParameters(
                    name=f"{self.name.value} ({side} {index})",
                    width=width,
                    height=graduation_height,
                    depth=self.depth.value,
                    center=(center_x + direction * offset_x, bottom_y + index * pitch),
                    rotation=0,
                    scan_direction="TopToBottom",
                    spot_size_correction=self.spot_size_correction.value))
        return patterns


# dictionary to map pattern names to pattern classes
PATTERN_NAME_TO_CLASS = {
    "rectangle": RectanglePatternParameters,
    "trench": TrenchPatternParameters,
    "microexpansion": MicroexpansionPatternParameters,
    "ruler": RulerPatternParameters,
}
