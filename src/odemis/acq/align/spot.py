# -*- coding: utf-8 -*-
"""
Created on 14 Apr 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

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

import threading
import coordinates
import math
import operator
from odemis import model

_acq_lock = threading.Lock()
_ccd_done = threading.Event()

MAX_STEPS_NUMBER = 3  # Max steps to perform alignment

def FindSpot(image):
    """
    This function detects the spot and calculates and returns the coordinates of
    its center. The algorithms for spot detection and center calculation are 
    similar to the ones that are used in Fine alignment.
    image (model.DataArray): Optical image
    returns (tuple of floats):    The spot center coordinates
    """
    subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(image, (1, 1), image.shape[0] / 2)
    spot_coordinates = coordinates.FindCenterCoordinates(subimages)
    optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)
    return optical_coordinates[0]


def CenterSpot(ccd, stage):
    """
    Iteratively acquires an optical image, finds the coordinates of the spot 
    (center) and moves the stage to this position. Repeats until the found 
    coordinates are at the center of the optical image or a maximum number of 
    steps is reached.
    ccd (model.DigitalCamera): The CCD
    stage (model.CombinedActuator): The stage
    returns (boolean):    True if spot is at the center 
    """
    print stage.position.value

    image = ccd.data.get()

    # Center of optical image
    pixelSize = image.metadata[model.MD_PIXEL_SIZE]
    center_pxs = ((image.shape[0] / 2),
                 (image.shape[1] / 2))

    # Coordinates of found spot
    spot_pxs = FindSpot(image)

    tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
    tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
    dist = math.hypot(*tab)

    # Epsilon distance below which the lens is considered centered. The worse of:
    # * 2 pixels (because the CCD resolution cannot give us better)
    # * 1 µm (because that's the best resolution of our actuators)
    err_mrg = max(2 * ccd.pixelSize.value[0], 1e-06)  # m
    steps = 0
    
    # Stop once spot is found on the center of the optical image
    while dist > err_mrg:
        # Or once max number of steps is reached
        if steps >= MAX_STEPS_NUMBER:
            return False

        x, y = tab[0], tab[1]
        # Move to the found spot
        move_abs = {"a":spot_pxs,
                    "b":spot_b}
        print "move"
        print type(stage)
        f = stage.moveAbs(move_abs)
        f.result()
        image = ccd.data.get()
        spot_pxs = FindSpot(image)
        tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
        tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
        dist = math.hypot(*tab)
        steps += 1

    return True
