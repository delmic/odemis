# -*- coding: utf-8 -*-
"""
Created on 27 Feb 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Éric Piel & Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from __future__ import division
from .calculation import *
from .dc_region import *


class AnchoredEstimator(object):
    """
    Drift estimator based on an "anchor" area. Periodically, a small region
    (the anchor) is scanned. By comparing the images of the anchor area over
    time, an estimation of the drift is computed.
    
    To use, call .scan() periodically (and preferably at specific places of 
    the global scan, such as at the beginning of a line), and call .estimate()
    to measure the drift.
    """
    def __init__(self, scanner, detector, region, dwell_time, scale):
        """
        """
        # TODO
        
        self.raw = [] # all the anchor areas acquired (in order)
        
    def scan(self):
        """
        Scan the anchor area
        """
        # TODO
     
    def estimate(self):
        """
        return (float, float): estimated current drift in X/Y SEM px
        """
        # TODO
        pass
    
    def estimateAcquisitionTime(self):
        """
        return (float): estimated time to acquire 1 anchor area
        """
        # TODO
        pass
