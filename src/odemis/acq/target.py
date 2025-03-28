# -*- coding: utf-8 -*-
"""
@author Karishma Kumar

Copyright © 2024, Delmic

Handles the controls for correlating two (or more) streams together.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
from typing import List

from odemis import model

FIDUCIAL, POI, SURFACE_FIDUCIAL, PROJECTED_FIDUCIAL, PROJECTED_POI = (
    "Fiducial",
    "PointOfInterest",
    "SurfaceFiducial",
    "ProjectedPoints",
    "ProjectedPOI",
)


class Target:
    def __init__(self, x:float, y:float, z:float, name:str, type:str, index: int,  fm_focus_position: float, size: float = None ):
        """
        Target class to store the target information for multipoint correlation.
        :param x: physical position in x in meters
        :param y: physical position in y in meters
        :param z: physical position in z in meters
        :param name: name of the target saved as <type>-<index>. For example, "FM-1", "POI-2", "FIB-3", "PP", "PPOI"
        :param type: type of the given target like Fiducial, PointOfInterest, ProjectedPoints, ProjectedPOI or SurfaceFiducial
        :param index: index of the target in the given type. For example, "FM-1", "FM-2", "FM-3"...
        :param fm_focus_position: position of the focus (objective in cased of Meteor) in meters
        :param size: size of the area of interest in meters. Only used for super Z workflow
        """
        self.coordinates = model.ListVA((x, y, z), unit="m")
        self.type = model.StringEnumerated(type, choices={FIDUCIAL, POI, SURFACE_FIDUCIAL, PROJECTED_FIDUCIAL,
                                                          PROJECTED_POI})
        self.name = model.StringVA(name)
        # Warning: The index and target name are in sync. To increase the index limit more than 9, please first change the logic of finding indices from the
        # target name in add_new_target() in tab_gui_data.py and
        # _on_current_coordinates_changes(), _on_cell_changing in  multi_point_correlation.py
        self.index = model.IntContinuous(index, range=(1, 9))
        if size:
            self.size = model.FloatContinuous(size, range=(1, 20)) # for super Z workflow
        else:
            self.size = None
        self.fm_focus_position = model.FloatVA(fm_focus_position, unit="m")

        def to_dict(self) -> dict:
            return {
                "coordinates": self.coordinates.value,
                "type": self.type.value,
                "name": self.name.value,
                "index": self.index.value,
                "fm_focus_position": self.fm_focus_position.value,
                "size": self.size.value if self.size else None,
            }

        @staticmethod
        def from_dict(d: dict) -> "Target":
            x, y, z = d["coordinates"]
            return Target(
                x=x,
                y=y,
                z=z,
                name=d["name"],
                type=d["type"],
                index=d["index"],
                fm_focus_position=d["fm_focus_position"],
                size=d.get("size", None),
            )
