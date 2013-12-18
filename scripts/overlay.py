# -*- coding: utf-8 -*-
"""
Created on 17 Dec 2013

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

import logging
import numpy
from odemis import model
from odemis.dataio import hdf5
from odemis.model._dataflow import MD_PIXEL_SIZE, MD_POS
from odemis.acq.align import images, coordinates, transform
import sys
import threading
import time
import operator

logging.getLogger().setLevel(logging.DEBUG)

if __name__ == '__main__':
    
    if len(sys.argv) != 5:
        logging.error("Must be called with exactly 4 arguments")
        exit(1)
    repetitions = (int(sys.argv[1]), int(sys.argv[2]))
    dwell_time = float(sys.argv[3])
    max_allowed_diff = float(sys.argv[4])  # Maximum allowed difference in electron coordinates

    escan = None
    detector = None
    ccd = None
    # find components by their role
    for c in model.getComponents():
        if c.role == "e-beam":
            escan = c
        elif c.role == "se-detector":
            detector = c
        elif c.role == "ccd":
            ccd = c
    if not all([escan, detector, ccd]):
        logging.error("Failed to find all the components")
        raise KeyError("Not all components found")

    optical_image, electron_coordinates, electron_scale = images.ScanGrid(repetitions, dwell_time, escan, ccd, detector)

    ############## TO BE REMOVED ON TESTING##############
    grid_data = hdf5.read_data("real_optical.h5")
    C, T, Z, Y, X = grid_data[0].shape
    grid_data[0].shape = Y, X
    optical_image = grid_data[0]
    #####################################################

    subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(optical_image, repetitions)
    spot_coordinates = coordinates.FindCenterCoordinates(subimages)
    optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

    # TODO: Make function for scale calculation
    sorted_coordinates = sorted(optical_coordinates, key=lambda tup: tup[1])
    optical_scale = sorted_coordinates[0][0] - sorted_coordinates[1][0]
    scale = electron_scale[0] / optical_scale

    known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, scale, max_allowed_diff)

    (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_estimated_coordinates)
    final_optical = coordinates._TransformCoordinates(known_estimated_coordinates, (calc_translation_y, calc_translation_x), -calc_rotation, calc_scaling)


