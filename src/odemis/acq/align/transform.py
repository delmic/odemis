# -*- coding: utf-8 -*-
"""
Created on 29 Nov 2013

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

import numpy
import math

def CalculateTransform(optical_coordinates, electron_coordinates):
    """
    Returns the translation, scaling and rotation for the optical and electron image coordinates.
    optical_coordinates (List of tuples): Coordinates of spots in optical image
    electron_coordinates (List of tuples): Coordinates of spots in electron image
    returns translation (Tuple of 2 floats), 
            scaling (Float), 
            rotation (Float): Transformation parameters
    """
    # Create numpy arrays out of the coordinate lists
    optical_array = numpy.array(optical_coordinates)
    electron_array = numpy.array(electron_coordinates)

    # Make matrix X
    list_len = len(electron_coordinates)  # We assume that both lists have the same length
    x_array = numpy.zeros(shape=(2 * list_len, 4))
    x_array[0:list_len, 2].fill(1)
    x_array[0:list_len, 0:2] = optical_array
    x_array[list_len:2 * list_len, 3].fill(1)
    x_array[list_len:2 * list_len, 0] = optical_array[:, 1]
    x_array[list_len:2 * list_len, 1] = -optical_array[:, 0]

    # Make matrix U
    u_array = numpy.zeros(shape=(2 * list_len, 1))
    u_array[0: list_len, 0] = electron_array[:, 0]
    u_array[list_len: 2 * list_len, 0] = electron_array[:, 1]

    # Calculate matrix R, R = X\U
    r_array, resid, rank, s = numpy.linalg.lstsq(x_array, u_array)

    translation_x = r_array[2][0]
    translation_y = r_array[3][0]
    scaling = 1 / math.sqrt((r_array[1][0] ** 2) + (r_array[0][0] ** 2))
    rotation = (180 / math.pi) * math.atan2(-r_array[1][0], r_array[0][0])

    return (translation_x, translation_y), scaling, rotation
