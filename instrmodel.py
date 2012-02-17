#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 16 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

from model import ActiveValue
import wx

class SECOMModel(object):
    """
    Represent the data of a SECOM microscope
    This is the main Model, from a Model/View/Controller perspective
    """
    
    def __init__(self):
        self.stage_pos = ActiveValue((0,0)) # m,m
        self.stage_pos.bind(self.avOnStagePos)
        
        # FIXME: maybe could go into (sub)classes like OpticalEmitter, SEDetector... 
        self.optical_emt_wavelength = ActiveValue(450) # nm XXX a range?
        self.optical_det_wavelength = ActiveValue(568) # nm
        self.optical_det_exposure_time = ActiveValue(0.5) # s
        self.optical_det_image = ActiveValue(InstrumentalImage(None, None, None))
        
        self.sem_emt_dwell_time = ActiveValue(0.00001) #s
        self.sem_emt_spot = ActiveValue(4) # no unit (could be m²)
        self.sem_emt_hv = ActiveValue(30000) # V
        self.sem_det_image = ActiveValue(InstrumentalImage(None, None, None))
    
    def avOnStagePos(self, val):
        print "requested to move stage to pos:", val 

class InstrumentalImage(object):
    """
    Contains a bitmap and meta data about it
    """
    
    def __init__(self, im, mpp, center):
        """
        im wx.Image
        mpp (float>0)
        center (2-tuple float)
        """
        self.image = im
        self.mpp = mpp
        self.center = center
        

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: