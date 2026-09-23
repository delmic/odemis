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


class WaffleTrenchPatternParameters(CompositeRectanglePatternParameters):
    """Two independently sized rectangles separated by a centered opening."""

    def __init__(self, top_width: float, top_height: float,
                 bottom_width: float, bottom_height: float,
                 depth: float, spacing: float,
                 center: Tuple[float, float] = (0, 0),
                 name: str = "Waffle Trench",
                 spot_size_correction: float = 0.0) -> None:
        """Initialize a waffle trench.

        :param top_width: Width of the top rectangle.
        :param top_height: Height of the top rectangle.
        :param bottom_width: Width of the bottom rectangle.
        :param bottom_height: Height of the bottom rectangle.
        :param depth: Milling depth shared by both rectangles.
        :param spacing: Clear distance between the rectangles.
        :param center: Center of the opening between the rectangles.
        :param name: Pattern name.
        :param spot_size_correction: Beam spot size correction.
        """
        self.name = model.StringVA(name)
        self.top_width = model.FloatContinuous(top_width, unit="m", range=(1e-9, 900e-6))
        self.top_height = model.FloatContinuous(top_height, unit="m", range=(1e-9, 900e-6))
        self.bottom_width = model.FloatContinuous(bottom_width, unit="m", range=(1e-9, 900e-6))
        self.bottom_height = model.FloatContinuous(bottom_height, unit="m", range=(1e-9, 900e-6))
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.spacing = model.FloatContinuous(spacing, unit="m", range=(1e-9, 900e-6))
        self.center = model.TupleContinuous(
            center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.spot_size_correction = model.FloatContinuous(
            spot_size_correction, unit="m", range=(0, 900e-6))

    def to_dict(self) -> dict:
        """Serialize the waffle trench parameters."""
        return {
            "name": self.name.value,
            "top_width": self.top_width.value,
            "top_height": self.top_height.value,
            "bottom_width": self.bottom_width.value,
            "bottom_height": self.bottom_height.value,
            "depth": self.depth.value,
            "spacing": self.spacing.value,
            "center_x": self.center.value[0],
            "center_y": self.center.value[1],
            "spot_size_correction": self.spot_size_correction.value,
            "pattern": "waffle_trench",
        }

    @staticmethod
    def from_dict(data: dict) -> 'WaffleTrenchPatternParameters':
        """Restore waffle trench parameters from serialized data."""
        return WaffleTrenchPatternParameters(
            top_width=data["top_width"],
            top_height=data["top_height"],
            bottom_width=data["bottom_width"],
            bottom_height=data["bottom_height"],
            depth=data["depth"],
            spacing=data["spacing"],
            center=(data.get("center_x", 0), data.get("center_y", 0)),
            name=data.get("name", "Waffle Trench"),
            spot_size_correction=data.get("spot_size_correction", 0.0))

    def generate(self) -> List[MillingPatternParameters]:
        """Generate the top and bottom rectangles."""
        center_x, center_y = self.center.value
        top_center_y = center_y + self.spacing.value / 2 + self.top_height.value / 2
        bottom_center_y = center_y - self.spacing.value / 2 - self.bottom_height.value / 2
        return [
            RectanglePatternParameters(
                name=f"{self.name.value} (Top)",
                width=self.top_width.value,
                height=self.top_height.value,
                depth=self.depth.value,
                center=(center_x, top_center_y),
                scan_direction="TopToBottom",
                spot_size_correction=self.spot_size_correction.value),
            RectanglePatternParameters(
                name=f"{self.name.value} (Bottom)",
                width=self.bottom_width.value,
                height=self.bottom_height.value,
                depth=self.depth.value,
                center=(center_x, bottom_center_y),
                scan_direction="BottomToTop",
                spot_size_correction=self.spot_size_correction.value),
        ]


class RulerPatternParameters(CompositeRectanglePatternParameters):
    """Two mirrored rulers, with alternating horizontal notches bottom to top.

    Width is the even-numbered notch length; odd-numbered notches are 3/4
    of that length. Spacing is the gap between the inner edges of the
    rulers. Height includes the full extent of the notches. Each notch is
    one fifth of the pitch thick, so changing height scales the whole ruler.
    Num_notches counts all notches on each side, including the zero mark.
    """

    def __init__(self, width: float, height: float, depth: float, spacing: float,
                 num_notches: int = 11, center: Tuple[float, float] = (0, 0),
                 name: str = "Ruler", spot_size_correction: float = 0.0) -> None:
        """Initialize ruler pattern parameters.

        :param width: Length of each long notch.
        :param height: Overall ruler height.
        :param depth: Milling depth of each notch.
        :param spacing: Gap between the rulers' inner edges.
        :param num_notches: Number of notches on each side.
        :param center: Center of the complete ruler pattern.
        :param name: Pattern name.
        :param spot_size_correction: Beam spot size correction.
        """
        self.name = model.StringVA(name)
        # Short notches must also meet the rectangle's minimum width of 1 nm.
        self.width = model.FloatContinuous(width, unit="m", range=(2e-9, 900e-6))
        self.height = model.FloatContinuous(height, unit="m", range=(6e-9, 900e-6),
                                            setter=self._set_height)
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.spacing = model.FloatContinuous(spacing, unit="m", range=(1e-9, 900e-6))
        self.num_notches = model.IntContinuous(num_notches, range=(2, 1000),
                                              setter=self._set_num_notches)
        self.center = model.TupleContinuous(center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.spot_size_correction = model.FloatContinuous(spot_size_correction, unit="m", range=(0, 900e-6))
        self._check_notch_height(height, num_notches)

    @staticmethod
    def _check_notch_height(height: float, num_notches: int) -> None:
        """Validate that the generated notches meet the minimum height.

        :param height: Overall ruler height.
        :param num_notches: Number of notches on each side.
        :raises ValueError: If a notch would be less than one nanometer high.
        """
        if height / (5 * num_notches - 4) < 1e-9:
            raise ValueError("Ruler height is too small for this number of notches (minimum notch thickness is 1 nm)")

    def _set_height(self, height: float) -> float:
        """Validate and return a new ruler height.

        :param height: Proposed ruler height.
        :return: Validated ruler height.
        """
        self._check_notch_height(height, self.num_notches.value)
        return height

    def _set_num_notches(self, num_notches: int) -> int:
        """Validate and return a new notch count.

        :param num_notches: Proposed number of notches on each side.
        :return: Validated notch count.
        """
        self._check_notch_height(self.height.value, num_notches)
        return num_notches

    def to_dict(self) -> dict:
        """Serialize the ruler parameters.

        :return: Serializable ruler parameters.
        """
        return {"name": self.name.value,
                "width": self.width.value,
                "height": self.height.value,
                "depth": self.depth.value,
                "spacing": self.spacing.value,
                "num_notches": self.num_notches.value,
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
            num_notches=data.get("num_notches", 11),
            center=(data.get("center_x", 0), data.get("center_y", 0)),
            name=data.get("name", "Ruler"),
            spot_size_correction=data.get("spot_size_correction", 0.0))

    def generate(self) -> List[MillingPatternParameters]:
        """Generate rectangles alternating between full and three-quarter width.

        :return: Rectangle parameters for both sides of the ruler.
        """
        count = self.num_notches.value
        notch_height = self.height.value / (5 * count - 4)
        pitch = 5 * notch_height
        center_x, center_y = self.center.value
        bottom_y = center_y - (self.height.value - notch_height) / 2
        patterns = []
        for index in range(count):
            width = self.width.value if index % 2 == 0 else self.width.value * 0.75

            # All notches start at the inner edge and extend outwards.
            offset_x = (self.spacing.value + width) / 2
            for side, direction in (("Left", -1), ("Right", 1)):
                patterns.append(RectanglePatternParameters(
                    name=f"{self.name.value} ({side} {index})",
                    width=width,
                    height=notch_height,
                    depth=self.depth.value,
                    center=(center_x + direction * offset_x, bottom_y + index * pitch),
                    rotation=0,
                    scan_direction="TopToBottom",
                    spot_size_correction=self.spot_size_correction.value))
        return patterns


class NotchPatternParameters(CompositeRectanglePatternParameters):
    """Five connected rectangles forming one right-facing square-wave loop."""

    _DIMENSIONS = ("width", "height", "gap", "thickness", "offset")

    def __init__(self, width: float, height: float, depth: float, gap: float,
                 thickness: float, offset: float = 0.0, mirrored: bool = False,
                 center: Tuple[float, float] = (0, 0), name: str = "Notch",
                 spot_size_correction: float = 0.0) -> None:
        """Initialize notch pattern parameters.

        :param width: Overall width of the square-wave loop.
        :param height: Overall height including both whiskers.
        :param depth: Milling depth of every segment.
        :param gap: Clear vertical gap between the horizontal segments.
        :param thickness: Thickness of every segment.
        :param offset: Vertical displacement of the loop from the pattern center.
        :param mirrored: Whether the loop faces left instead of right.
        :param center: Center of the complete pattern bounds.
        :param name: Pattern name.
        :param spot_size_correction: Beam spot size correction.
        """
        self.name = model.StringVA(name)
        self.width = model.FloatContinuous(
            width, unit="m", range=(1e-9, 900e-6),
            setter=self._set_width)
        self.height = model.FloatContinuous(
            height, unit="m", range=(1e-9, 900e-6),
            setter=self._set_height)
        self.depth = model.FloatContinuous(depth, unit="m", range=(1e-9, 100e-6))
        self.gap = model.FloatContinuous(
            gap, unit="m", range=(1e-9, 900e-6),
            setter=self._set_gap)
        self.thickness = model.FloatContinuous(
            thickness, unit="m", range=(1e-9, 900e-6),
            setter=self._set_thickness)
        self.offset = model.FloatContinuous(
            offset, unit="m", range=(-900e-6, 900e-6), setter=self._set_offset)
        self.mirrored = model.BooleanVA(mirrored)
        self.center = model.TupleContinuous(
            center, unit="m", range=((-1e3, -1e3), (1e3, 1e3)), cls=(int, float))
        self.spot_size_correction = model.FloatContinuous(
            spot_size_correction, unit="m", range=(0, 900e-6))

    @staticmethod
    def _check_geometry(width: float, height: float, gap: float,
                        thickness: float, offset: float) -> None:
        """Validate that all five generated rectangles have positive dimensions.

        :param width: Overall loop width.
        :param height: Overall pattern height.
        :param gap: Clear height inside the loop.
        :param thickness: Segment thickness.
        :param offset: Vertical displacement of the loop from the pattern center.
        :raises ValueError: If the dimensions cannot form a five-segment notch.
        """
        if width <= thickness:
            raise ValueError("Notch width must be greater than its thickness")
        whisker_space = (height - gap - 2 * thickness) / 2
        if whisker_space - abs(offset) < 1e-9:
            raise ValueError("Notch offset or loop height leaves no room for both whiskers")

    def _set_dimension(self, parameter: str, value: float) -> float:
        """Validate and return a changed geometry dimension.

        :param parameter: Name of the dimension being changed.
        :param value: Proposed dimension value.
        :return: Validated dimension value.
        """
        values = {}
        for name in self._DIMENSIONS:
            if name == parameter:
                values[name] = value
            elif hasattr(self, name):
                values[name] = getattr(self, name).value
        if len(values) == len(self._DIMENSIONS):
            self._check_geometry(**values)
        return value

    def _set_width(self, value: float) -> float:
        """Validate a changed loop width."""
        return self._set_dimension("width", value)

    def _set_height(self, value: float) -> float:
        """Validate a changed overall height."""
        return self._set_dimension("height", value)

    def _set_gap(self, value: float) -> float:
        """Validate a changed internal gap."""
        return self._set_dimension("gap", value)

    def _set_thickness(self, value: float) -> float:
        """Validate a changed segment thickness."""
        return self._set_dimension("thickness", value)

    def _set_offset(self, value: float) -> float:
        """Validate a changed loop offset."""
        return self._set_dimension("offset", value)

    def to_dict(self) -> dict:
        """Serialize the notch parameters.

        :return: Serializable notch parameters.
        """
        return {
            "name": self.name.value,
            "width": self.width.value,
            "height": self.height.value,
            "depth": self.depth.value,
            "gap": self.gap.value,
            "thickness": self.thickness.value,
            "offset": self.offset.value,
            "mirrored": self.mirrored.value,
            "center_x": self.center.value[0],
            "center_y": self.center.value[1],
            "spot_size_correction": self.spot_size_correction.value,
            "pattern": "notch",
        }

    @staticmethod
    def from_dict(data: dict) -> 'NotchPatternParameters':
        """Create notch parameters from serialized data.

        :param data: Serialized notch parameters.
        :return: Restored notch parameters.
        """
        offset = data.get("offset")
        if offset is None:
            upper_whisker = data.get("upper_whisker")
            offset = (0.0 if upper_whisker is None else
                      data["height"] / 2 - data["gap"] / 2
                      - data["thickness"] - upper_whisker)
        return NotchPatternParameters(
            width=data["width"],
            height=data["height"],
            depth=data["depth"],
            gap=data["gap"],
            thickness=data["thickness"],
            offset=offset,
            mirrored=data.get("mirrored", False),
            center=(data.get("center_x", 0), data.get("center_y", 0)),
            name=data.get("name", "Notch"),
            spot_size_correction=data.get("spot_size_correction", 0.0))

    def generate(self) -> List[MillingPatternParameters]:
        """Generate the five rectangles forming the notch.

        :return: Rectangle parameters ordered along the square-wave path.
        """
        width = self.width.value
        height = self.height.value
        gap = self.gap.value
        thickness = self.thickness.value
        offset = self.offset.value
        whisker_space = (height - gap - 2 * thickness) / 2
        upper_whisker = whisker_space - offset
        lower_whisker = whisker_space + offset
        center_x, center_y = self.center.value
        top = center_y + height / 2
        loop_center_y = center_y + offset
        upper_bar_y = loop_center_y + (gap + thickness) / 2
        lower_bar_y = loop_center_y - (gap + thickness) / 2
        direction = -1 if self.mirrored.value else 1
        whisker_x = center_x - direction * (width - thickness) / 2
        outer_leg_x = center_x + direction * (width - thickness) / 2
        outer_side = "Left" if self.mirrored.value else "Right"

        rectangles = (
            ("Upper Whisker", thickness, upper_whisker,
             (whisker_x, top - upper_whisker / 2)),
            ("Upper Bar", width, thickness,
             (center_x, upper_bar_y)),
            (f"{outer_side} Leg", thickness, gap,
             (outer_leg_x, loop_center_y)),
            ("Lower Bar", width, thickness,
             (center_x, lower_bar_y)),
            ("Lower Whisker", thickness, lower_whisker,
             (whisker_x, center_y - height / 2 + lower_whisker / 2)),
        )
        return [
            RectanglePatternParameters(
                name=f"{self.name.value} ({segment})",
                width=rectangle_width,
                height=rectangle_height,
                depth=self.depth.value,
                center=rectangle_center,
                rotation=0,
                scan_direction="TopToBottom",
                spot_size_correction=self.spot_size_correction.value)
            for segment, rectangle_width, rectangle_height, rectangle_center in rectangles
        ]


# dictionary to map pattern names to pattern classes
PATTERN_NAME_TO_CLASS = {
    "rectangle": RectanglePatternParameters,
    "trench": TrenchPatternParameters,
    "microexpansion": MicroexpansionPatternParameters,
    "waffle_trench": WaffleTrenchPatternParameters,
    "ruler": RulerPatternParameters,
    "notch": NotchPatternParameters,
}
