# -*- coding: utf-8 -*-
"""
Created on 17 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from .instrmodel import InstrumentalImage
from odemis.gui.img.data import gettest_patternImage
from odemis.model import VigilantAttribute

class DblMscopeViewModel(object):
    """
    Data model of a view from 2 microscopes
    """

    def __init__(self):
        # image density => field of view, position...
        # 0<float
        self.mpp = VigilantMPP(0.000025) # m/px  (0.25mm/px)

        # how much one image is displayed on the other one
        # 0<=float<=1
        self.merge_ratio = VigilantMergeRatio(0.3) # no unit

        #TODO default to black? 
        self.images = [VigilantAttribute(InstrumentalImage(gettest_patternImage(), mpp=self.mpp.value, center=(0.0, 0.0))),
                       VigilantAttribute(InstrumentalImage(None, None, None))]
        #self.images = [VigilantAttribute(InstrumentalImage(None, None, None)),
        #               VigilantAttribute(InstrumentalImage(None, None, None))]

        # center position of the view
        self.center = VigilantAttribute((0, 0)) # (m, m)

        self.crosshair = VigilantAttribute(True)
        self.opt_focus = None # should be an actuator

class VigilantMPP(VigilantAttribute):
    """
    VigilantAttribute with special validation for MPP (float>0)
    """
    def _check(self, value):
        assert(0.0 < value)

class VigilantMergeRatio(VigilantAttribute):
    """
    VigilantAttribute with special validation for merge ratio
    # 0<=float<=1
    """
    def _set_value(self, value):
        # don't raise an error, just clamp the values
        final_val = sorted((0.0, 1.0) + (value,))[1] # clamp
        VigilantAttribute._set_value(self, final_val)

    def add_value(self, value):
        self._set_value(self.value + value)
