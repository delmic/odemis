# -*- coding: utf-8 -*-
"""
Created on 8 Jan 2014

@author: kimon

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

import logging
import numpy
import math
import cv2


def SelectRegion(detector, emitter, dwell_time, sample_region):
    """
    It performs a scan of the whole image in order to detect a region with clean
    edges, proper for drift measurements. This region must not overlap with the
    sample that is to be scanned due to the danger of contamination.
    detector (model.Detector): The sec. electron detector
    emitter (model.Emitter): The e-beam scanner
    dwell_time (float): Time to scan each pixel
	sample_region (tuple of 3 floats): roi of the sample in order to avoid overlap
    returns (tuple of 3 floats): roi of the selected region
    """
    # like in stream_drift_test for roi 0,0,1,1
    # cv2.Canny(img, 100, 200)
    return
