

import math
from abc import ABC, abstractmethod

from odemis import model

class MillingPatternParameters(ABC):
    """Represents milling pattern parameters"""

    def __init__(self, name: str):
        self.name = model.StringVA(name)

    @abstractmethod
    def to_json(self) -> dict:
        pass

    @staticmethod
    @abstractmethod
    def from_json(data: dict):
        pass

    def __repr__(self):
        return f"{self.to_json()}"
    
    @abstractmethod
    def generate(self):
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

    def to_json(self) -> dict:
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
    def from_json(data: dict):
        return RectanglePatternParameters(width=data["width"], 
                                        height=data["height"], 
                                        depth=data["depth"], 
                                        rotation=data.get("rotation", 0), 
                                        center=(data.get("center_x", 0), data.get("center_y", 0)), 
                                        scan_direction=data.get("scan_direction", "TopToBottom"), 
                                        name=data.get("name", "Rectangle"))

    def __repr__(self):
        return f"{self.to_json()}"

    def generate(self):
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

    def to_json(self) -> dict:
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
    def from_json(data: dict):
        return TrenchPatternParameters(width=data["width"], 
                                        height=data["height"], 
                                        depth=data["depth"], 
                                        spacing=data["spacing"],
                                        center=(data.get("center_x", 0), data.get("center_y", 0)), 
                                        name=data.get("name", "Trench"))

    def __repr__(self):
        return f"{self.to_json()}"

    def generate(self):
        """Generate a list of milling patterns for the microscope"""
        name = self.name.value
        width = self.width.value
        height = self.height.value
        depth = self.depth.value
        spacing = self.spacing.value

        # pattern center
        center_y = height / 2 + spacing / 2
        
        patterns = [
            RectanglePatternParameters(
                name=f"{name} (Upper)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (0, center_y), # x, y
                scan_direction="TopToBottom",
            ),
            RectanglePatternParameters(
                name=f"{name} (Lower)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (0, -center_y), # x, y
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

    def to_json(self) -> dict:
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
    def from_json(data: dict):
        return MicroexpansionPatternParameters(
                        width=data["width"], 
                        height=data["height"], 
                        depth=data["depth"], 
                        spacing=data["spacing"],
                        center=(data.get("center_x", 0), data.get("center_y", 0)), 
                        name=data.get("name", "Trench"))

    def __repr__(self):
        return f"{self.to_json()}"


    def generate(self):
        """Generate a list of milling patterns for the microscope"""
        name = self.name.value
        width = self.width.value
        height = self.height.value
        depth = self.depth.value
        spacing = self.spacing.value / 2       

        patterns = [
            RectanglePatternParameters(
                name=f"{name} (Left)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (-spacing, 0),
                scan_direction="TopToBottom",
            ),
            RectanglePatternParameters(
                name=f"{name} (Right)",
                width=width,
                height=height,
                depth=depth,
                rotation=0,
                center = (spacing, 0),
                scan_direction="TopToBottom",
            ),
        ]

        return patterns

pattern_generator = {
    "rectangle": RectanglePatternParameters,
    "trench": TrenchPatternParameters,
    "microexpansion": MicroexpansionPatternParameters,
}