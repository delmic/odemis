# -*- coding: utf-8 -*-
'''
Created on 10 Jan 2019

@author: Andries Effting

Copyright © 2019 Andries Effting, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import copy
import numpy
from numpy.linalg import LinAlgError
import unittest

from odemis.util.transform import (_rotation_matrix_from_angle,
                                   _rotation_matrix_to_angle,
                                   RigidTransform, SimilarityTransform,
                                   ScalingTransform, AffineTransform)

ROT45 = 0.25 * numpy.pi
ROT90 = 0.5 * numpy.pi
ROT135 = 0.75 * numpy.pi
ROT180 = numpy.pi

SQ05 = numpy.sqrt(0.5)
SQ2 = numpy.sqrt(2.)
S23 = numpy.array([2., 3.])
SQ2S23 = SQ2 * S23

T0 = numpy.array([0., 0.])
TX = numpy.array([1., 0.])
TY = numpy.array([0., 1.])
TXY = numpy.array([2., 3.])

RIGID_KNOWN_VALUES = [
    # (rotation, scale, shear, translation, [(x0, y0), (x1, y1), ...])
    (0., 1., 0., T0, [(0., 0.), (1., 0.), (0., 1.)]),
    (0., 1., 0., TX, [(1., 0.), (2., 0.), (1., 1.)]),
    (0., 1., 0., TY, [(0., 1.), (1., 1.), (0., 2.)]),
    (0., 1., 0., -TX, [(-1., 0.), (0., 0.), (-1., 1.)]),
    (0., 1., 0., -TY, [(0., -1.), (1., -1.), (0., 0.)]),
    (0., 1., 0., TXY, [(2., 3.), (3., 3.), (2., 4.)]),
    (-ROT90, 1., 0., T0, [(0., 0.), (0., -1.), (1., 0.)]),
    (-ROT90, 1., 0., TX, [(1., 0.), (1., -1.), (2., 0.)]),
    (-ROT90, 1., 0., TY, [(0., 1.), (0., 0.), (1., 1.)]),
    (-ROT90, 1., 0., -TX, [(-1., 0.), (-1., -1.), (0., 0.)]),
    (-ROT90, 1., 0., -TY, [(0., -1.), (0., -2.), (1., -1.)]),
    (-ROT90, 1., 0., TXY, [(2., 3.), (2., 2.), (3., 3.)]),
    (ROT90, 1., 0., T0, [(0., 0.), (0., 1.), (-1., 0.)]),
    (ROT90, 1., 0., TX, [(1., 0.), (1., 1.), (0., 0.)]),
    (ROT90, 1., 0., TY, [(0., 1.), (0., 2.), (-1., 1.)]),
    (ROT90, 1., 0., -TX, [(-1., 0.), (-1., 1.), (-2., 0.)]),
    (ROT90, 1., 0., -TY, [(0., -1.), (0., 0.), (-1., -1.)]),
    (ROT90, 1., 0., TXY, [(2., 3.), (2., 4.), (1., 3.)]),
    (ROT180, 1., 0., T0, [(0., 0.), (-1., 0.), (0., -1.)]),
    (ROT180, 1., 0., TX, [(1., 0.), (0., 0.), (1., -1.)]),
    (ROT180, 1., 0., TY, [(0., 1.), (-1., 1.), (0., 0.)]),
    (ROT180, 1., 0., -TX, [(-1., 0.), (-2., 0.), (-1., -1.)]),
    (ROT180, 1., 0., -TY, [(0., -1.), (-1., -1.), (0., -2.)]),
    (ROT180, 1., 0., TXY, [(2., 3.), (1., 3.), (2., 2.)]),
]

SIMILARITY_KNOWN_VALUES = copy.copy(RIGID_KNOWN_VALUES)
SIMILARITY_KNOWN_VALUES.extend([
    # (rotation, scale, shear, translation, [(x0, y0), (x1, y1), ...])
    (ROT45, SQ2, 0., T0, [(0., 0.), (1., 1.), (-1., 1.)]),
    (ROT45, SQ2, 0., TX, [(1., 0.), (2., 1.), (0., 1.)]),
    (ROT45, SQ2, 0., TY, [(0., 1.), (1., 2.), (-1., 2.)]),
    (ROT45, SQ2, 0., -TX, [(-1., 0.), (0., 1.), (-2., 1.)]),
    (ROT45, SQ2, 0., -TY, [(0., -1.), (1., 0.), (-1., 0.)]),
    (ROT45, SQ2, 0., TXY, [(2., 3.), (3., 4.), (1., 4.)]),
    (0., 2., 0., T0, [(0., 0.), (2., 0.), (0., 2.)]),
    (0., 2., 0., TX, [(1., 0.), (3., 0.), (1., 2.)]),
    (0., 2., 0., TY, [(0., 1.), (2., 1.), (0., 3.)]),
    (0., 2., 0., -TX, [(-1., 0.), (1., 0.), (-1., 2.)]),
    (0., 2., 0., -TY, [(0., -1.), (2., -1.), (0., 1.)]),
    (0., 2., 0., TXY, [(2., 3.), (4., 3.), (2., 5.)]),
    (-ROT90, 2., 0., T0, [(0., 0.), (0., -2.), (2., 0.)]),
    (-ROT90, 2., 0., TX, [(1., 0.), (1., -2.), (3., 0.)]),
    (-ROT90, 2., 0., TY, [(0., 1.), (0., -1.), (2., 1.)]),
    (-ROT90, 2., 0., -TX, [(-1., 0.), (-1., -2.), (1., 0.)]),
    (-ROT90, 2., 0., -TY, [(0., -1.), (0., -3.), (2., -1.)]),
    (-ROT90, 2., 0., TXY, [(2., 3.), (2., 1.), (4., 3.)]),
    (ROT90, 2., 0., T0, [(0., 0.), (0., 2.), (-2., 0.)]),
    (ROT90, 2., 0., TX, [(1., 0.), (1., 2.), (-1., 0.)]),
    (ROT90, 2., 0., TY, [(0., 1.), (0., 3.), (-2., 1.)]),
    (ROT90, 2., 0., -TX, [(-1., 0.), (-1., 2.), (-3., 0.)]),
    (ROT90, 2., 0., -TY, [(0., -1.), (0., 1.), (-2., -1.)]),
    (ROT90, 2., 0., TXY, [(2., 3.), (2., 5.), (0., 3.)]),
    (ROT180, 2., 0., T0, [(0., 0.), (-2., 0.), (0., -2.)]),
    (ROT180, 2., 0., TX, [(1., 0.), (-1., 0.), (1., -2.)]),
    (ROT180, 2., 0., TY, [(0., 1.), (-2., 1.), (0., -1.)]),
    (ROT180, 2., 0., -TX, [(-1., 0.), (-3., 0.), (-1., -2.)]),
    (ROT180, 2., 0., -TY, [(0., -1.), (-2., -1.), (0., -3.)]),
    (ROT180, 2., 0., TXY, [(2., 3.), (0., 3.), (2., 1.)]),
])

SCALING_KNOWN_VALUES = copy.copy(SIMILARITY_KNOWN_VALUES)
SCALING_KNOWN_VALUES.extend([
    # (rotation, scale, shear, translation, [(x0, y0), (x1, y1), ...])
    (0., S23, 0., T0, [(0., 0.), (2., 0.), (0., 3.)]),
    (0., S23, 0., TX, [(1., 0.), (3., 0.), (1., 3.)]),
    (0., S23, 0., TY, [(0., 1.), (2., 1.), (0., 4.)]),
    (0., S23, 0., -TX, [(-1., 0.), (1., 0.), (-1., 3.)]),
    (0., S23, 0., -TY, [(0., -1.), (2., -1.), (0., 2.)]),
    (0., S23, 0., TXY, [(2., 3.), (4., 3.), (2., 6.)]),
    (-ROT90, S23, 0., T0, [(0., 0.), (0., -2.), (3., 0.)]),
    (-ROT90, S23, 0., TX, [(1., 0.), (1., -2.), (4., 0.)]),
    (-ROT90, S23, 0., TY, [(0., 1.), (0., -1.), (3., 1.)]),
    (-ROT90, S23, 0., -TX, [(-1., 0.), (-1., -2.), (2., 0.)]),
    (-ROT90, S23, 0., -TY, [(0., -1.), (0., -3.), (3., -1.)]),
    (-ROT90, S23, 0., TXY, [(2., 3.), (2., 1.), (5., 3.)]),
    (ROT90, S23, 0., T0, [(0., 0.), (0., 2.), (-3., 0.)]),
    (ROT90, S23, 0., TX, [(1., 0.), (1., 2.), (-2., 0.)]),
    (ROT90, S23, 0., TY, [(0., 1.), (0., 3.), (-3., 1.)]),
    (ROT90, S23, 0., -TX, [(-1., 0.), (-1., 2.), (-4., 0.)]),
    (ROT90, S23, 0., -TY, [(0., -1.), (0., 1.), (-3., -1.)]),
    (ROT90, S23, 0., TXY, [(2., 3.), (2., 5.), (-1., 3.)]),
    (ROT180, S23, 0., T0, [(0., 0.), (-2., 0.), (0., -3.)]),
    (ROT180, S23, 0., TX, [(1., 0.), (-1., 0.), (1., -3.)]),
    (ROT180, S23, 0., TY, [(0., 1.), (-2., 1.), (0., -2.)]),
    (ROT180, S23, 0., -TX, [(-1., 0.), (-3., 0.), (-1., -3.)]),
    (ROT180, S23, 0., -TY, [(0., -1.), (-2., -1.), (0., -4.)]),
    (ROT180, S23, 0., TXY, [(2., 3.), (0., 3.), (2., 0.)]),
    (ROT45, SQ2S23, 0., T0, [(0., 0.), (2., 2.), (-3., 3.)]),
    (ROT45, SQ2S23, 0., TX, [(1., 0.), (3., 2.), (-2., 3.)]),
    (ROT45, SQ2S23, 0., TY, [(0., 1.), (2., 3.), (-3., 4.)]),
    (ROT45, SQ2S23, 0., -TX, [(-1., 0.), (1., 2.), (-4., 3.)]),
    (ROT45, SQ2S23, 0., -TY, [(0., -1.), (2., 1.), (-3., 2.)]),
    (ROT45, SQ2S23, 0., TXY, [(2., 3.), (4., 5.), (-1., 6.)]),
])

AFFINE_KNOWN_VALUES = copy.copy(SIMILARITY_KNOWN_VALUES)
AFFINE_KNOWN_VALUES.extend([
    (0., 1., 1., T0, [(0., 0.), (1., 0.), (1., 1.)]),
    (0., 1., 1., TX, [(1., 0.), (2., 0.), (2., 1.)]),
    (0., 1., 1., TY, [(0., 1.), (1., 1.), (1., 2.)]),
    (0., 1., 1., -TX, [(-1., 0.), (0., 0.), (0., 1.)]),
    (0., 1., 1., -TY, [(0., -1.), (1., -1.), (1., 0.)]),
    (0., 1., 1., TXY, [(2., 3.), (3., 3.), (3., 4.)]),
    (-ROT90, 1., 1., T0, [(0., 0.), (0., -1.), (1., -1.)]),
    (-ROT90, 1., 1., TX, [(1., 0.), (1., -1.), (2., -1.)]),
    (-ROT90, 1., 1., TY, [(0., 1.), (0., 0.), (1., 0.)]),
    (-ROT90, 1., 1., -TX, [(-1., 0.), (-1., -1.), (0., -1.)]),
    (-ROT90, 1., 1., -TY, [(0., -1.), (0., -2.), (1., -2.)]),
    (-ROT90, 1., 1., TXY, [(2., 3.), (2., 2.), (3., 2.)]),
    (ROT90, 1., 1., T0, [(0., 0.), (0., 1.), (-1., 1.)]),
    (ROT90, 1., 1., TX, [(1., 0.), (1., 1.), (0., 1.)]),
    (ROT90, 1., 1., TY, [(0., 1.), (0., 2.), (-1., 2.)]),
    (ROT90, 1., 1., -TX, [(-1., 0.), (-1., 1.), (-2., 1.)]),
    (ROT90, 1., 1., -TY, [(0., -1.), (0., 0.), (-1., 0.)]),
    (ROT90, 1., 1., TXY, [(2., 3.), (2., 4.), (1., 4.)]),
    (ROT180, 1., 1., T0, [(0., 0.), (-1., 0.), (-1., -1.)]),
    (ROT180, 1., 1., TX, [(1., 0.), (0., 0.), (-0., -1.)]),
    (ROT180, 1., 1., TY, [(0., 1.), (-1., 1.), (-1., 0.)]),
    (ROT180, 1., 1., -TX, [(-1., 0.), (-2., 0.), (-2., -1.)]),
    (ROT180, 1., 1., -TY, [(0., -1.), (-1., -1.), (-1., -2.)]),
    (ROT180, 1., 1., TXY, [(2., 3.), (1., 3.), (1., 2.)]),
    (ROT45, SQ2, 1., T0, [(0., 0.), (1., 1.), (0., 2.)]),
    (ROT45, SQ2, 1., TX, [(1., 0.), (2., 1.), (1., 2.)]),
    (ROT45, SQ2, 1., TY, [(0., 1.), (1., 2.), (0., 3.)]),
    (ROT45, SQ2, 1., -TX, [(-1., 0.), (0., 1.), (-1., 2.)]),
    (ROT45, SQ2, 1., -TY, [(0., -1.), (1., 0.), (0., 1.)]),
    (ROT45, SQ2, 1., TXY, [(2., 3.), (3., 4.), (2., 5.)]),
    (0., 2., 1., T0, [(0., 0.), (2., 0.), (2., 2.)]),
    (0., 2., 1., TX, [(1., 0.), (3., 0.), (3., 2.)]),
    (0., 2., 1., TY, [(0., 1.), (2., 1.), (2., 3.)]),
    (0., 2., 1., -TX, [(-1., 0.), (1., 0.), (1., 2.)]),
    (0., 2., 1., -TY, [(0., -1.), (2., -1.), (2., 1.)]),
    (0., 2., 1., TXY, [(2., 3.), (4., 3.), (4., 5.)]),
    (-ROT90, 2., 1., T0, [(0., 0.), (0., -2.), (2., -2.)]),
    (-ROT90, 2., 1., TX, [(1., 0.), (1., -2.), (3., -2.)]),
    (-ROT90, 2., 1., TY, [(0., 1.), (0., -1.), (2., -1.)]),
    (-ROT90, 2., 1., -TX, [(-1., 0.), (-1., -2.), (1., -2.)]),
    (-ROT90, 2., 1., -TY, [(0., -1.), (0., -3.), (2., -3.)]),
    (-ROT90, 2., 1., TXY, [(2., 3.), (2., 1.), (4., 1.)]),
    (ROT90, 2., 1., T0, [(0., 0.), (0., 2.), (-2., 2.)]),
    (ROT90, 2., 1., TX, [(1., 0.), (1., 2.), (-1., 2.)]),
    (ROT90, 2., 1., TY, [(0., 1.), (0., 3.), (-2., 3.)]),
    (ROT90, 2., 1., -TX, [(-1., 0.), (-1., 2.), (-3., 2.)]),
    (ROT90, 2., 1., -TY, [(0., -1.), (0., 1.), (-2., 1.)]),
    (ROT90, 2., 1., TXY, [(2., 3.), (2., 5.), (0., 5.)]),
    (ROT180, 2., 1., T0, [(0., 0.), (-2., 0.), (-2., -2.)]),
    (ROT180, 2., 1., TX, [(1., 0.), (-1., 0.), (-1., -2.)]),
    (ROT180, 2., 1., TY, [(0., 1.), (-2., 1.), (-2., -1.)]),
    (ROT180, 2., 1., -TX, [(-1., 0.), (-3., 0.), (-3., -2.)]),
    (ROT180, 2., 1., -TY, [(0., -1.), (-2., -1.), (-2., -3.)]),
    (ROT180, 2., 1., TXY, [(2., 3.), (0., 3.), (-0., 1.)]),
    (0., S23, 1., T0, [(0., 0.), (2., 0.), (2., 3.)]),
    (0., S23, 1., TX, [(1., 0.), (3., 0.), (3., 3.)]),
    (0., S23, 1., TY, [(0., 1.), (2., 1.), (2., 4.)]),
    (0., S23, 1., -TX, [(-1., 0.), (1., 0.), (1., 3.)]),
    (0., S23, 1., -TY, [(0., -1.), (2., -1.), (2., 2.)]),
    (0., S23, 1., TXY, [(2., 3.), (4., 3.), (4., 6.)]),
    (-ROT90, S23, 1., T0, [(0., 0.), (0., -2.), (3., -2.)]),
    (-ROT90, S23, 1., TX, [(1., 0.), (1., -2.), (4., -2.)]),
    (-ROT90, S23, 1., TY, [(0., 1.), (0., -1.), (3., -1.)]),
    (-ROT90, S23, 1., -TX, [(-1., 0.), (-1., -2.), (2., -2.)]),
    (-ROT90, S23, 1., -TY, [(0., -1.), (0., -3.), (3., -3.)]),
    (-ROT90, S23, 1., TXY, [(2., 3.), (2., 1.), (5., 1.)]),
    (ROT90, S23, 1., T0, [(0., 0.), (0., 2.), (-3., 2.)]),
    (ROT90, S23, 1., TX, [(1., 0.), (1., 2.), (-2., 2.)]),
    (ROT90, S23, 1., TY, [(0., 1.), (0., 3.), (-3., 3.)]),
    (ROT90, S23, 1., -TX, [(-1., 0.), (-1., 2.), (-4., 2.)]),
    (ROT90, S23, 1., -TY, [(0., -1.), (0., 1.), (-3., 1.)]),
    (ROT90, S23, 1., TXY, [(2., 3.), (2., 5.), (-1., 5.)]),
    (ROT180, S23, 1., T0, [(0., 0.), (-2., 0.), (-2., -3.)]),
    (ROT180, S23, 1., TX, [(1., 0.), (-1., 0.), (-1., -3.)]),
    (ROT180, S23, 1., TY, [(0., 1.), (-2., 1.), (-2., -2.)]),
    (ROT180, S23, 1., -TX, [(-1., 0.), (-3., 0.), (-3., -3.)]),
    (ROT180, S23, 1., -TY, [(0., -1.), (-2., -1.), (-2., -4.)]),
    (ROT180, S23, 1., TXY, [(2., 3.), (0., 3.), (-0., 0.)]),
    (ROT45, SQ2S23, 1., T0, [(0., 0.), (2., 2.), (-1., 5.)]),
    (ROT45, SQ2S23, 1., TX, [(1., 0.), (3., 2.), (0., 5.)]),
    (ROT45, SQ2S23, 1., TY, [(0., 1.), (2., 3.), (-1., 6.)]),
    (ROT45, SQ2S23, 1., -TX, [(-1., 0.), (1., 2.), (-2., 5.)]),
    (ROT45, SQ2S23, 1., -TY, [(0., -1.), (2., 1.), (-1., 4.)]),
    (ROT45, SQ2S23, 1., TXY, [(2., 3.), (4., 5.), (1., 8.)]),
    (0., 1., -1., T0, [(0., 0.), (1., 0.), (-1., 1.)]),
    (0., 1., -1., TX, [(1., 0.), (2., 0.), (0., 1.)]),
    (0., 1., -1., TY, [(0., 1.), (1., 1.), (-1., 2.)]),
    (0., 1., -1., -TX, [(-1., 0.), (0., 0.), (-2., 1.)]),
    (0., 1., -1., -TY, [(0., -1.), (1., -1.), (-1., 0.)]),
    (0., 1., -1., TXY, [(2., 3.), (3., 3.), (1., 4.)]),
    (-ROT90, 1., -1., T0, [(0., 0.), (0., -1.), (1., 1.)]),
    (-ROT90, 1., -1., TX, [(1., 0.), (1., -1.), (2., 1.)]),
    (-ROT90, 1., -1., TY, [(0., 1.), (0., 0.), (1., 2.)]),
    (-ROT90, 1., -1., -TX, [(-1., 0.), (-1., -1.), (-0., 1.)]),
    (-ROT90, 1., -1., -TY, [(0., -1.), (0., -2.), (1., 0.)]),
    (-ROT90, 1., -1., TXY, [(2., 3.), (2., 2.), (3., 4.)]),
    (ROT90, 1., -1., T0, [(0., 0.), (0., 1.), (-1., -1.)]),
    (ROT90, 1., -1., TX, [(1., 0.), (1., 1.), (0., -1.)]),
    (ROT90, 1., -1., TY, [(0., 1.), (0., 2.), (-1., 0.)]),
    (ROT90, 1., -1., -TX, [(-1., 0.), (-1., 1.), (-2., -1.)]),
    (ROT90, 1., -1., -TY, [(0., -1.), (0., 0.), (-1., -2.)]),
    (ROT90, 1., -1., TXY, [(2., 3.), (2., 4.), (1., 2.)]),
    (ROT180, 1., -1., T0, [(0., 0.), (-1., 0.), (1., -1.)]),
    (ROT180, 1., -1., TX, [(1., 0.), (0., 0.), (2., -1.)]),
    (ROT180, 1., -1., TY, [(0., 1.), (-1., 1.), (1., -0.)]),
    (ROT180, 1., -1., -TX, [(-1., 0.), (-2., 0.), (-0., -1.)]),
    (ROT180, 1., -1., -TY, [(0., -1.), (-1., -1.), (1., -2.)]),
    (ROT180, 1., -1., TXY, [(2., 3.), (1., 3.), (3., 2.)]),
    (ROT45, SQ2, -1., T0, [(0., 0.), (1., 1.), (-2., 0.)]),
    (ROT45, SQ2, -1., TX, [(1., 0.), (2., 1.), (-1., 0.)]),
    (ROT45, SQ2, -1., TY, [(0., 1.), (1., 2.), (-2., 1.)]),
    (ROT45, SQ2, -1., -TX, [(-1., 0.), (0., 1.), (-3., 0.)]),
    (ROT45, SQ2, -1., -TY, [(0., -1.), (1., 0.), (-2., -1.)]),
    (ROT45, SQ2, -1., TXY, [(2., 3.), (3., 4.), (0., 3.)]),
    (0., 2., -1., T0, [(0., 0.), (2., 0.), (-2., 2.)]),
    (0., 2., -1., TX, [(1., 0.), (3., 0.), (-1., 2.)]),
    (0., 2., -1., TY, [(0., 1.), (2., 1.), (-2., 3.)]),
    (0., 2., -1., -TX, [(-1., 0.), (1., 0.), (-3., 2.)]),
    (0., 2., -1., -TY, [(0., -1.), (2., -1.), (-2., 1.)]),
    (0., 2., -1., TXY, [(2., 3.), (4., 3.), (0., 5.)]),
    (-ROT90, 2., -1., T0, [(0., 0.), (0., -2.), (2., 2.)]),
    (-ROT90, 2., -1., TX, [(1., 0.), (1., -2.), (3., 2.)]),
    (-ROT90, 2., -1., TY, [(0., 1.), (0., -1.), (2., 3.)]),
    (-ROT90, 2., -1., -TX, [(-1., 0.), (-1., -2.), (1., 2.)]),
    (-ROT90, 2., -1., -TY, [(0., -1.), (0., -3.), (2., 1.)]),
    (-ROT90, 2., -1., TXY, [(2., 3.), (2., 1.), (4., 5.)]),
    (ROT90, 2., -1., T0, [(0., 0.), (0., 2.), (-2., -2.)]),
    (ROT90, 2., -1., TX, [(1., 0.), (1., 2.), (-1., -2.)]),
    (ROT90, 2., -1., TY, [(0., 1.), (0., 3.), (-2., -1.)]),
    (ROT90, 2., -1., -TX, [(-1., 0.), (-1., 2.), (-3., -2.)]),
    (ROT90, 2., -1., -TY, [(0., -1.), (0., 1.), (-2., -3.)]),
    (ROT90, 2., -1., TXY, [(2., 3.), (2., 5.), (0., 1.)]),
    (ROT180, 2., -1., T0, [(0., 0.), (-2., 0.), (2., -2.)]),
    (ROT180, 2., -1., TX, [(1., 0.), (-1., 0.), (3., -2.)]),
    (ROT180, 2., -1., TY, [(0., 1.), (-2., 1.), (2., -1.)]),
    (ROT180, 2., -1., -TX, [(-1., 0.), (-3., 0.), (1., -2.)]),
    (ROT180, 2., -1., -TY, [(0., -1.), (-2., -1.), (2., -3.)]),
    (ROT180, 2., -1., TXY, [(2., 3.), (0., 3.), (4., 1.)]),
    (0., S23, -1., T0, [(0., 0.), (2., 0.), (-2., 3.)]),
    (0., S23, -1., TX, [(1., 0.), (3., 0.), (-1., 3.)]),
    (0., S23, -1., TY, [(0., 1.), (2., 1.), (-2., 4.)]),
    (0., S23, -1., -TX, [(-1., 0.), (1., 0.), (-3., 3.)]),
    (0., S23, -1., -TY, [(0., -1.), (2., -1.), (-2., 2.)]),
    (0., S23, -1., TXY, [(2., 3.), (4., 3.), (0., 6.)]),
    (-ROT90, S23, -1., T0, [(0., 0.), (0., -2.), (3., 2.)]),
    (-ROT90, S23, -1., TX, [(1., 0.), (1., -2.), (4., 2.)]),
    (-ROT90, S23, -1., TY, [(0., 1.), (0., -1.), (3., 3.)]),
    (-ROT90, S23, -1., -TX, [(-1., 0.), (-1., -2.), (2., 2.)]),
    (-ROT90, S23, -1., -TY, [(0., -1.), (0., -3.), (3., 1.)]),
    (-ROT90, S23, -1., TXY, [(2., 3.), (2., 1.), (5., 5.)]),
    (ROT90, S23, -1., T0, [(0., 0.), (0., 2.), (-3., -2.)]),
    (ROT90, S23, -1., TX, [(1., 0.), (1., 2.), (-2., -2.)]),
    (ROT90, S23, -1., TY, [(0., 1.), (0., 3.), (-3., -1.)]),
    (ROT90, S23, -1., -TX, [(-1., 0.), (-1., 2.), (-4., -2.)]),
    (ROT90, S23, -1., -TY, [(0., -1.), (0., 1.), (-3., -3.)]),
    (ROT90, S23, -1., TXY, [(2., 3.), (2., 5.), (-1., 1.)]),
    (ROT180, S23, -1., T0, [(0., 0.), (-2., 0.), (2., -3.)]),
    (ROT180, S23, -1., TX, [(1., 0.), (-1., 0.), (3., -3.)]),
    (ROT180, S23, -1., TY, [(0., 1.), (-2., 1.), (2., -2.)]),
    (ROT180, S23, -1., -TX, [(-1., 0.), (-3., 0.), (1., -3.)]),
    (ROT180, S23, -1., -TY, [(0., -1.), (-2., -1.), (2., -4.)]),
    (ROT180, S23, -1., TXY, [(2., 3.), (0., 3.), (4., -0.)]),
    (ROT45, SQ2S23, -1., T0, [(0., 0.), (2., 2.), (-5., 1.)]),
    (ROT45, SQ2S23, -1., TX, [(1., 0.), (3., 2.), (-4., 1.)]),
    (ROT45, SQ2S23, -1., TY, [(0., 1.), (2., 3.), (-5., 2.)]),
    (ROT45, SQ2S23, -1., -TX, [(-1., 0.), (1., 2.), (-6., 1.)]),
    (ROT45, SQ2S23, -1., -TY, [(0., -1.), (2., 1.), (-5., 0.)]),
    (ROT45, SQ2S23, -1., TXY, [(2., 3.), (4., 5.), (-3., 4.)])
])


def _angle_diff(x, y):
    """
    Returns the signed difference between two angles, taking into account
    the branch cut at the negative x-axis.
    """
    return min(y - x, y - x + 2.0 * numpy.pi, y - x - 2.0 * numpy.pi, key=abs)


class RotationMatrixKnownValues(unittest.TestCase):
    known_values = [
        (-ROT180, numpy.array([(-1., 0.), (0., -1.)])),
        (-ROT135, SQ05 * numpy.array([(-1., 1.), (-1., -1.)])),
        (-ROT90, numpy.array([(0., 1.), (-1., 0.)])),
        (-ROT45, SQ05 * numpy.array([(1., 1.), (-1., 1.)])),
        (0., numpy.array([(1., 0.), (0., 1.)])),
        (ROT45, SQ05 * numpy.array([(1., -1.), (1., 1.)])),
        (ROT90, numpy.array([(0., -1.), (1., 0.)])),
        (ROT135, SQ05 * numpy.array([(-1., -1.), (1., -1.)])),
        (ROT180, numpy.array([(-1., 0.), (0., -1.)]))
    ]

    def test_rotation_matrix_to_angle_known_values(self):
        """
        _rotation_matrix_to_angle should give known result with known input.
        """
        for angle, matrix in self.known_values:
            result = _rotation_matrix_to_angle(matrix)
            self.assertAlmostEqual(_angle_diff(angle, result), 0.)

    def test_rotation_matrix_from_angle_known_values(self):
        """
        _rotation_matrix_from_angle should give known result with known input.
        """
        for angle, matrix in self.known_values:
            result = _rotation_matrix_from_angle(angle)
            numpy.testing.assert_array_almost_equal(matrix, result)


class RotationMatrixToAngleBadInput(unittest.TestCase):
    def test_wrong_dimension(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the number of
        dimensions of the array is other than 2.
        """
        for s in [(), (2,), (2, 2, 2)]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle,
                              numpy.zeros(s))

    def test_not_square(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        not square.
        """
        for s in [(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle,
                              numpy.zeros(s))

    def test_not_2d(self):
        """
        _rotation_matrix_to_angle should fail when the matrix is not a 2-D
        matrix.
        """
        for s in (1, 3):
            self.assertRaises(NotImplementedError, _rotation_matrix_to_angle,
                              numpy.eye(s))

    def test_not_orthogonal(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        not orthogonal.
        """
        for matrix in [numpy.array([(0., 0.), (0., 0.)]),
                       numpy.array([(1., 0.), (1., 0.)]),
                       numpy.array([(1., 1.), (0., 0.)]),
                       numpy.array([(0., 1.), (0., 1.)]),
                       numpy.array([(0., 0.), (1., 1.)])]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle, matrix)

    def test_improper_rotation(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        an improper rotation (contains a reflection).
        """
        for matrix in [numpy.array([(1., 0.), (0., -1.)]),
                       numpy.array([(-1., 0.), (0., 1.)]),
                       numpy.array([(0., 1.), (1., 0.)]),
                       numpy.array([(0., -1.), (-1., 0.)])]:
            self.assertRaises(LinAlgError, _rotation_matrix_to_angle, matrix)


class RotationMatrixProperties(unittest.TestCase):
    def test_rotation_matrix_properties(self):
        """
        Test that the rotation matrix is a 2x2 square orthogonal matrix, with
        determinant equal to 1.
        """
        for angle in numpy.pi * numpy.linspace(-1., 1., 1000):
            matrix = _rotation_matrix_from_angle(angle)
            self.assertEqual(matrix.shape, (2, 2))
            numpy.testing.assert_array_almost_equal(numpy.dot(matrix.T, matrix), numpy.eye(2))
            self.assertAlmostEqual(numpy.linalg.det(matrix), 1.)


class RotationMatrixRoundTripCheck(unittest.TestCase):
    def test_roundtrip(self):
        """
        _rotation_matrix_to_angle(_rotation_matrix_from_angle(angle)) == angle
        for all angles.
        """
        for angle in numpy.pi * numpy.linspace(-1., 1., 1000):
            matrix = _rotation_matrix_from_angle(angle)
            result = _rotation_matrix_to_angle(matrix)
            self.assertAlmostEqual(angle, result)


class RigidTransformKnownValues(unittest.TestCase):

    def test_rigid_transform_matrix_known_values(self):
        for rotation, _, _, _, _ in RIGID_KNOWN_VALUES:
            tform = RigidTransform(rotation=rotation)
            matrix = _rotation_matrix_from_angle(rotation)
            numpy.testing.assert_array_almost_equal(matrix, tform.transformation_matrix)

    def test_rigid_transform_from_pointset_known_values(self):
        """
        RigidTransform.from_pointset should return known result with known
        input for rigid transformations.
        """
        src = RIGID_KNOWN_VALUES[0][-1]
        for rotation, _, _, translation, dst in RIGID_KNOWN_VALUES:
            tform = RigidTransform.from_pointset(src, dst)
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            numpy.testing.assert_array_almost_equal(translation, tform.translation)

    def test_rigid_transform_apply_known_values(self):
        """
        RigidTransform should return known result with known input for rigid
        transformations.
        """
        src = RIGID_KNOWN_VALUES[0][-1]
        for rotation, _, _, translation, dst in RIGID_KNOWN_VALUES:
            tform = RigidTransform(rotation=rotation, translation=translation)
            numpy.testing.assert_array_almost_equal(dst, tform(src))

    def test_rigid_transform_inverse_known_values(self):
        """
        RigidTransform.inverse should return known result with known input for
        rigid transformations.
        """
        src = RIGID_KNOWN_VALUES[0][-1]
        for rotation, _, _, translation, dst in RIGID_KNOWN_VALUES:
            tform = RigidTransform(rotation=rotation,
                                   translation=translation).inverse()
            numpy.testing.assert_array_almost_equal(src, tform(dst))


class SimilarityTransformKnownValues(unittest.TestCase):

    def test_similarity_transform_matrix_known_values(self):
        for rotation, scale, _, _, _ in SIMILARITY_KNOWN_VALUES:
            tform = SimilarityTransform(rotation=rotation, scale=scale)
            R = _rotation_matrix_from_angle(rotation)
            matrix = scale * R
            numpy.testing.assert_array_almost_equal(matrix, tform.transformation_matrix)

    def test_similarity_transform_from_pointset_umeyama(self):
        """
        SimilarityTransform.from_pointset should return the known results for
        the specific known input as described in the paper by Umeyama.
        """
        src = numpy.array([(0., 0.), (1., 0.), (0., 2.)])
        dst = numpy.array([(0., 0.), (-1., 0.), (0., 2.)])
        tform = SimilarityTransform.from_pointset(src, dst)
        numpy.testing.assert_array_almost_equal(tform.rotation_matrix,
                                      numpy.array([(0.832, 0.555),
                                                   (-0.555, 0.832)]),
                                      decimal=3)
        self.assertAlmostEqual(tform.scale, 0.721, places=3)
        numpy.testing.assert_array_almost_equal(tform.translation,
                                      numpy.array([-0.800, 0.400]))

    def test_similarity_transform_from_pointset_known_values(self):
        """
        SimilarityTransform.from_pointset should return known result with known
        input for similarity transformations.
        """
        src = SIMILARITY_KNOWN_VALUES[0][-1]
        for rotation, scale, _, translation, dst in SIMILARITY_KNOWN_VALUES:
            tform = SimilarityTransform.from_pointset(src, dst)
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            self.assertAlmostEqual(scale, tform.scale)
            numpy.testing.assert_array_almost_equal(translation, tform.translation)

    def test_similarity_transform_apply_known_values(self):
        """
        SimilarityTransform should return known result with known input for
        similarity transformations.
        """
        src = SIMILARITY_KNOWN_VALUES[0][-1]
        for rotation, scale, _, translation, dst in SIMILARITY_KNOWN_VALUES:
            tform = SimilarityTransform(rotation=rotation, scale=scale,
                                        translation=translation)
            numpy.testing.assert_array_almost_equal(dst, tform(src))

    def test_similarity_transform_inverse_known_values(self):
        """
        SimilarityTransform.inverse should return known result with known input
        for similarity transformations.
        """
        src = SIMILARITY_KNOWN_VALUES[0][-1]
        for rotation, scale, _, translation, dst in SIMILARITY_KNOWN_VALUES:
            tform = SimilarityTransform(rotation=rotation, scale=scale,
                                        translation=translation).inverse()
            numpy.testing.assert_array_almost_equal(src, tform(dst))


class ScalingTransformKnownValues(unittest.TestCase):

    def test_scaling_transform_matrix_known_values(self):
        for rotation, scale, shear, _, _ in SCALING_KNOWN_VALUES:
            tform = ScalingTransform(rotation=rotation, scale=scale)
            R = _rotation_matrix_from_angle(rotation)
            S = scale * numpy.eye(2)
            matrix = numpy.dot(R, S)
            numpy.testing.assert_array_almost_equal(matrix, tform.transformation_matrix)

    def test_scaling_transform_from_pointset_known_values(self):
        """
        ScalingTransform.from_pointset should return known result with known
        input for scaling transformations.
        """
        src = SCALING_KNOWN_VALUES[0][-1]
        for rotation, scale, _, translation, dst in SCALING_KNOWN_VALUES:
            tform = ScalingTransform.from_pointset(src, dst)
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            numpy.testing.assert_array_almost_equal(scale, tform.scale)
            numpy.testing.assert_array_almost_equal(translation, tform.translation)

    def test_scaling_transform_from_pointset_non_negative_scaling(self):
        """
        ScalingTransform.from_pointset should not return a negative value for
        the scaling when there is a reflection in the point set for scaling
        transformations.
        """
        src = numpy.array([(0., 0.), (1., 0.), (0., 2.)])
        dst = numpy.array([(0., 0.), (-1., 0.), (0., 2.)])
        tform = ScalingTransform.from_pointset(src, dst)
        self.assertTrue(numpy.all(tform.scale > 0.))

    def test_scaling_transform_apply_known_values(self):
        """
        ScalingTransform should return known result with known input for
        scaling transformations.
        """
        src = SCALING_KNOWN_VALUES[0][-1]
        for rotation, scale, _, translation, dst in SCALING_KNOWN_VALUES:
            tform = ScalingTransform(rotation=rotation, scale=scale,
                                     translation=translation)
            numpy.testing.assert_array_almost_equal(dst, tform(src))

    def test_scaling_transform_inverse_known_values(self):
        """
        ScalingTransform.inverse should return known result with known input
        for scaling transformations.
        """
        src = SCALING_KNOWN_VALUES[0][-1]
        for rotation, scale, _, translation, dst in SCALING_KNOWN_VALUES:
            tform = ScalingTransform(rotation=rotation, scale=scale,
                                     translation=translation).inverse()
            numpy.testing.assert_array_almost_equal(src, tform(dst))


class AffineTransformKnownValues(unittest.TestCase):

    def test_affine_transform_matrix_known_values(self):
        for rotation, scale, shear, _, _ in AFFINE_KNOWN_VALUES:
            tform = AffineTransform(rotation=rotation, scale=scale,
                                    shear=shear)
            R = _rotation_matrix_from_angle(rotation)
            S = scale * numpy.eye(2)
            L = numpy.array([(1., shear), (0., 1.)])
            matrix = numpy.dot(numpy.dot(R, S), L)
            numpy.testing.assert_array_almost_equal(matrix, tform.transformation_matrix)

    def test_affine_transform_from_pointset_known_values(self):
        """
        AffineTransform.from_pointset should return known result with known
        input for affine transformations.
        """
        src = AFFINE_KNOWN_VALUES[0][-1]
        for rotation, scale, shear, translation, dst in AFFINE_KNOWN_VALUES:
            tform = AffineTransform.from_pointset(src, dst)
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            numpy.testing.assert_array_almost_equal(scale, tform.scale)
            self.assertAlmostEqual(shear, tform.shear)
            numpy.testing.assert_array_almost_equal(translation, tform.translation)

    def test_affine_transform_from_pointset_non_negative_scaling(self):
        """
        AffineTransform.from_pointset should not return a negative value for
        the scaling when there is a reflection in the point set for affine
        transformations.
        """
        src = numpy.array([(0., 0.), (1., 0.), (0., 2.)])
        dst = numpy.array([(0., 0.), (-1., 0.), (0., 2.)])
        tform = AffineTransform.from_pointset(src, dst)
        self.assertTrue(numpy.all(tform.scale > 0.))

    def test_affine_transform_apply_known_values(self):
        """
        AffineTransform should return known result with known input for affine
        transformations.
        """
        src = AFFINE_KNOWN_VALUES[0][-1]
        for rotation, scale, shear, translation, dst in AFFINE_KNOWN_VALUES:
            tform = AffineTransform(rotation=rotation, scale=scale,
                                    shear=shear, translation=translation)
            numpy.testing.assert_array_almost_equal(dst, tform(src))

    def test_affine_transform_inverse_known_values(self):
        """
        AffineTransform.inverse should return known result with known input for
        affine transformations.
        """
        src = AFFINE_KNOWN_VALUES[0][-1]
        for rotation, scale, shear, translation, dst in AFFINE_KNOWN_VALUES:
            tform = AffineTransform(rotation=rotation, scale=scale,
                                    shear=shear,
                                    translation=translation).inverse()
            numpy.testing.assert_array_almost_equal(src, tform(dst))


if __name__ == '__main__':
    unittest.main()
