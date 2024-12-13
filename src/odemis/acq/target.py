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


class Target:
    def __init__(self, x,y,z, name:str, type:str, index: int,  fm_focus_position: float, size: float = None ):
        self.coordinates = model.TupleVA((x, y, z), unit="px")
        self.type = model.StringVA(type)
        self.name = model.StringVA(name)
        self.index = model.IntContinuous(index, range=(1, 20))
        if size:
            self.size = model.FloatContinuous(size, range=(1, 20))# for super Z workflow
        else:
            self.size = None
        self.fm_focus_position = model.FloatVA(fm_focus_position, unit="m")

