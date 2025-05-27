# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

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
from enum import Enum


class ROIColumnNames(Enum):
    NAME = "Name"
    SLICE_IDX = "ROI Index"
    POSX = "Position.X [m]"
    POSY = "Position.Y [m]"
    SIZEX = "Size.X"
    SIZEY = "Size.Y"
    ROT = "Rotation [°]"
    CONTRAST = "Contrast"
    BRIGHTNESS = "Brightness"
    DWELL_TIME = "Dwell Time [µs]"
    FIELDS = "Show Fields"
    SCINTILLATOR_NUM = "Scintillator"


class RibbonColumnNames(Enum):
    NAME = "Name"
    SLICE_IDX = "Ribbon Index"
    POSX = "Position.X [m]"
    POSY = "Position.Y [m]"
    SIZEX = "Size.X"
    SIZEY = "Size.Y"
    ROT = "Rotation [°]"


class SectionColumnNames(Enum):
    NAME = "Name"
    SLICE_IDX = "Slice Index"
    POSX = "Position.X [m]"
    POSY = "Position.Y [m]"
    SIZEX = "Size.X"
    SIZEY = "Size.Y"
    ROT = "Rotation [°]"
    PARENT = "Parent"


class ROAColumnNames(Enum):
    NAME = "Name"
    SLICE_IDX = "Slice Index"
    POSX = "Position.X [m]"
    POSY = "Position.Y [m]"
    SIZEX = "Size.X"
    SIZEY = "Size.Y"
    ROT = "Rotation [°]"
    PARENT = "Parent"
    FIELDS = "Show Fields"
    SCINTILLATOR_NUM = "Scintillator"
