#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 17 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

from instrmodel import InstrumentalImage
from model import ActiveValue

class DblMscopeViewModel(object):
    """
    Data model of a view from 2 microscopes
    """
    
    def __init__(self):
        # image density => field of view, position...
        # 0<float
        self.mpp = ActiveMPP(0.000025) # m/px  (0.25mm/px)
        
        # how much one image is displayed on the other one
        # 0<=float<=1
        self.merge_ratio = ActiveMergeRatio(0.3) # no unit
        
        self.images = [ActiveValue(InstrumentalImage(None, None, None)),
                       ActiveValue(InstrumentalImage(None, None, None))]
        
        # center position of the view
        self.center = ActiveValue((0,0)) # (m, m)
        
        self.crosshair = ActiveValue(True)
        
class ActiveMPP(ActiveValue):
    """
    ActiveValue with special validation for MPP (float>0)
    """
    def _set(self, value):
        assert(0.0 < value)
        ActiveValue._set(self, value)
        
class ActiveMergeRatio(ActiveValue):
    """
    ActiveValue with special validation for merge ratio
    # 0<=float<=1
    """
    def _set(self, value):
        # don't raise an error, just clamp the values
        final_val = sorted((0.0, 1.0) + (value,))[1] # clamp
        ActiveValue._set(self, final_val)