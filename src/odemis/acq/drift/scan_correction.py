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


def ScanWithDriftCorrection(detector, emitter, ccd, dwell_time, selected_region):
    """
    It performs the scanning procedure. After each line is scanned, it moves the
    e-beam to the selected region, scans it and provides the generated frame, 
    along with the previous one, to CalculateDrift. Then it sets the translation
    of the e-beam based on the drift value calculated by CalculateDrift and starts 
    scanning the next line. Finally, it provides the optical and electron images 
    captured free of drift effect.
    detector (model.Detector): The sec. electron detector
    emitter (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    dwell_time (float): Time to scan each pixel
	selected_region (tuple of 3 floats): roi of the selected region for drift calculation
    returns (ProgressiveFuture): Progress of ScanWithDriftCorrection
    result: optical_image (model.DataArray)
    		electron_image (model.DataArray)
    """
    return
