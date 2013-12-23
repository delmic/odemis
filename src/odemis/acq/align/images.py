# -*- coding: utf-8 -*-
"""
Created on 18 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

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

import operator
import logging

from odemis import model
from odemis.dataio import hdf5

def _discard_data(df, data):
    """
    Does nothing, just discard the data received (for spot mode)
    """
    pass

def ScanGrid(repetitions, used_dwell_time, used_escan, used_ccd, used_detector):
    """
    Uses the e-beam to scan the rectangular grid consisted of the given number 
    of spots and acquires the corresponding CCD image
    repetitions (tuple of ints): The number of CL spots are used
    used_dwell_time (float): Time to scan each spot #s
    used_escan (model.Emitter): The e-beam scanner
    used_ccd (model.DigitalCamera): The CCD
    used_detector (model.Detector): The electron detector
    returns (model.DataArray): 2D array containing the intensity of each pixel in 
                                the spotted optical image
            (List of tuples):  Coordinates of spots in electron image
            (Tuple of floats):    Scaling of electron image
    """
    detector = used_detector
    escan = used_escan
    ccd = used_ccd

    # Scanner setup
    scale = [(escan.resolution.range[1][0] - 1) / repetitions[0],
             (escan.resolution.range[1][1] - 1) / repetitions[1]]
    escan.scale.value = scale
    escan.resolution.value = repetitions
    escan.translation.value = (0, 0)
    if (used_dwell_time < escan.dwellTime.range[0]):
        escan.dwellTime.value = escan.dwellTime.range[0]
    elif (used_dwell_time > escan.dwellTime.range[1]):
        escan.dwellTime.value = escan.dwellTime.range[1]
    else:
        escan.dwellTime.value = used_dwell_time

    # CCD setup
    ccd.exposureTime.value = repetitions[0] * repetitions[1] * escan.dwellTime.value  # s
    binning = (1, 1)
    ccd.binning.value = binning
    ccd.resolution.value = (ccd.shape[0] // binning[0],
                            ccd.shape[1] // binning[1])

    detector.data.subscribe(_discard_data)
    # subscribe also for ccd instead of .get()

    # Wait for "exposureTime"
    try:
        # do nothing
        logging.debug("Scanning spot grid...")
        optical_image = ccd.data.get()
        logging.debug("Got CCD image...")
    finally:
        detector.data.unsubscribe(_discard_data)

    # Compute electron coordinates based on scale and repetitions
    electron_coordinates = []
    for i in xrange(repetitions[0]):
        for j in xrange(repetitions[1]):
            electron_coordinates.append((i * scale[0], j * scale[1]))
    
    return optical_image, electron_coordinates, scale
