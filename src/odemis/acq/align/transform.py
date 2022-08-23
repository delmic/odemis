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

import numpy
import math

def CalculateTransform(optical_coordinates, electron_coordinates, skew=False):
    """
    Returns the translation, scaling and rotation for the optical and electron image coordinates.
    optical_coordinates (List of tuples): Coordinates of spots in optical image
    electron_coordinates (List of tuples): Coordinates of spots in electron image
    skew (boolean): If True, also compute scaling ratio and shear
    returns translation (Tuple of 2 floats),
            scaling (Tuple of 2 floats),
            rotation (Float): Transformation parameters
            shear (Float):
    """
    # Create numpy arrays out of the coordinate lists
    optical_array = numpy.array(optical_coordinates)
    electron_array = numpy.array(electron_coordinates)

    # Make matrix X
    list_len = len(electron_coordinates)  # We assume that both lists have the same length
    if optical_array.shape[0] != list_len:
        raise ValueError("Mismatch between the number of expected and found coordinates.")

    if skew is False:
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
        r_array, resid, rank, s = numpy.linalg.lstsq(x_array, u_array, rcond=-1)  # TODO: use rcond=None when supporting numpy 1.14+
        # if r_array[1][0] == 0:
        #    r_array[1][0] = 1
        translation_x = -r_array[2][0]
        translation_y = -r_array[3][0]
        scaling_x = 1 / math.sqrt((r_array[1][0] ** 2) + (r_array[0][0] ** 2))
        scaling_y = 1 / math.sqrt((r_array[1][0] ** 2) + (r_array[0][0] ** 2))
        rotation = math.atan2(-r_array[1][0], r_array[0][0])

        return (translation_x, translation_y), (scaling_x, scaling_y), rotation
    else:
        # Calculate including shear
        x_array = numpy.zeros(shape=(list_len, 3))
        x_array[0:list_len, 2].fill(1)
        x_array[0:list_len, 0:2] = optical_array

        # Make matrix U
        u_array = electron_array

        # We know that X*T=U
        t_inv, resid, rank, s = numpy.linalg.lstsq(x_array, u_array, rcond=-1)  # TODO: use rcond=None when supporting numpy 1.14+
        translation_xy = t_inv[2, :]
        theta = math.atan2(t_inv[1, 0], t_inv[1, 1])
        scaling_x = t_inv[0, 0] * math.cos(theta) - t_inv[0, 1] * math.sin(theta)
        scaling_y = math.sqrt(math.pow(t_inv[1, 0], 2) + math.pow(t_inv[1, 1], 2))
        shear = (t_inv[0, 0] * math.sin(theta) + t_inv[0, 1] * math.cos(theta)) / scaling_x

        # change values for return values
        translation_xy_ret = -translation_xy
        scaling_ret = (1 / scaling_x + 1 / scaling_y) / 2
        theta_ret = -theta
        scaling_xy_ret = (1 / scaling_x) / scaling_ret - 1
        shear_ret = -shear

        return (translation_xy_ret[0], translation_xy_ret[1]), (scaling_ret, scaling_ret), theta_ret, scaling_xy_ret, shear_ret
