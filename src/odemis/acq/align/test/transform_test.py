# -*- coding: utf-8 -*-
'''
Created on 29 Nov 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import numpy as np
from numpy.linalg import LinAlgError
import unittest

from odemis.acq.align.transform import CalculateTransform, Transform

logging.getLogger().setLevel(logging.DEBUG)

ROT45 = 0.25 * np.pi
ROT90 = 0.5 * np.pi
ROT135 = 0.75 * np.pi
ROT180 = np.pi

SQ05 = np.sqrt(0.5)
SQ2 = np.sqrt(2.)
S23 = np.array([2., 3.])
SQ2S23 = SQ2 * S23

T0 = np.array([0., 0.])
TX = np.array([1., 0.])
TY = np.array([0., 1.])
TXY = np.array([2., 3.])

KNOWN_VALUES = [
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
]


# @unittest.skip("skip")
class TestTransformationParams(unittest.TestCase):
    """
    Test TransformationParams functions
    """

    def test_calculate_transform(self):
        """
        Test CalculateTransform, compare to precomputed values
        """
        optical_coordinates = [(4.8241, 3.2631), (5.7418, 4.5738), (5.2170, 1.0348), (8.8879, 6.2774)]
        electron_coordinates = [(0, 1), (0, 2), (1, 0), (1, 4)]

        (translation_x, translation_y), (scaling_x, scaling_y), rotation = CalculateTransform(optical_coordinates,
                                                                                              electron_coordinates)
        np.testing.assert_almost_equal((translation_x, translation_y, scaling_x, scaling_y, rotation),
                                       (1.3000132631489385, 2.3999740720548788, 1.60000617, 1.60000617, 0.61085922))

    # TODO: Calculate optical coordinates given the electron coordinates and translation, rotation and scale values
    #        and test.


def _angle_diff(x, y):
    """
    Returns the signed difference between two angles, taking into account
    the branch cut at the negative x-axis.
    """
    return min(y - x, y - x + 2.0 * np.pi, y - x - 2.0 * np.pi, key=abs)


class RotationMatrixKnownValues(unittest.TestCase):
    known_values = [
        (-ROT180, np.array([(-1., 0.), (0., -1.)])),
        (-ROT135, SQ05 * np.array([(-1., 1.), (-1., -1.)])),
        (-ROT90, np.array([(0., 1.), (-1., 0.)])),
        (-ROT45, SQ05 * np.array([(1., 1.), (-1., 1.)])),
        (0., np.array([(1., 0.), (0., 1.)])),
        (ROT45, SQ05 * np.array([(1., -1.), (1., 1.)])),
        (ROT90, np.array([(0., -1.), (1., 0.)])),
        (ROT135, SQ05 * np.array([(-1., -1.), (1., -1.)])),
        (ROT180, np.array([(-1., 0.), (0., -1.)]))
    ]

    def test_rotation_matrix_to_angle_known_values(self):
        """
        _rotation_matrix_to_angle should give known result with known input.
        """
        for angle, matrix in self.known_values:
            result = Transform._rotation_matrix_to_angle(matrix)
            self.assertAlmostEqual(_angle_diff(angle, result), 0.)

    def test_rotation_matrix_from_angle_known_values(self):
        """
        _rotation_matrix_from_angle should give known result with known input.
        """
        for angle, matrix in self.known_values:
            result = Transform._rotation_matrix_from_angle(angle)
            np.testing.assert_array_almost_equal(matrix, result)


class RotationMatrixToAngleBadInput(unittest.TestCase):
    def test_wrong_dimension(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the number of
        dimensions of the array is other than 2.
        """
        for s in [(), (2,), (2, 2, 2)]:
            self.assertRaises(LinAlgError, Transform._rotation_matrix_to_angle,
                              np.zeros(s))

    def test_not_square(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        not square.
        """
        for s in [(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)]:
            self.assertRaises(LinAlgError, Transform._rotation_matrix_to_angle,
                              np.zeros(s))

    def test_not_2d(self):
        """
        _rotation_matrix_to_angle should fail when the matrix is not a 2-D
        matrix.
        """
        for s in ((1, 1), (3, 3)):
            self.assertRaises(NotImplementedError,
                              Transform._rotation_matrix_to_angle,
                              np.zeros(s))

    def test_not_orthogonal(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        not orthogonal.
        """
        for matrix in [np.array([(0., 0.), (0., 0.)]),
                       np.array([(1., 0.), (1., 0.)]),
                       np.array([(1., 1.), (0., 0.)]),
                       np.array([(0., 1.), (0., 1.)]),
                       np.array([(0., 0.), (1., 1.)])]:
            self.assertRaises(LinAlgError, Transform._rotation_matrix_to_angle,
                              matrix)

    def test_improper_rotation(self):
        """
        _rotation_matrix_to_angle should raise LinAlgError when the matrix is
        an improper rotation (contains a reflection).
        """
        for matrix in [np.array([(1., 0.), (0., -1.)]),
                       np.array([(-1., 0.), (0., 1.)]),
                       np.array([(0., 1.), (1., 0.)]),
                       np.array([(0., -1.), (-1., 0.)])]:
            self.assertRaises(LinAlgError, Transform._rotation_matrix_to_angle,
                              matrix)


class RotationMatrixProperties(unittest.TestCase):
    def test_rotation_matrix_properties(self):
        """
        Test that the rotation matrix is a 2x2 square orthogonal matrix, with
        determinant equal to 1.
        """
        for angle in np.pi * np.linspace(-1., 1., 1000):
            matrix = Transform._rotation_matrix_from_angle(angle)
            self.assertEqual(matrix.shape, (2, 2))
            np.testing.assert_array_almost_equal(np.dot(matrix.T, matrix),
                                                 np.eye(2))
            self.assertAlmostEqual(np.linalg.det(matrix), 1.)


class RotationMatrixRoundTripCheck(unittest.TestCase):
    def test_roundtrip(self):
        """
        _rotation_matrix_to_angle(_rotation_matrix_from_angle(angle)) == angle
        for all angles
        """
        for angle in np.pi * np.linspace(-1., 1., 1000):
            matrix = Transform._rotation_matrix_from_angle(angle)
            result = Transform._rotation_matrix_to_angle(matrix)
            self.assertAlmostEqual(angle, result)


class RigidTransformKnownValues(unittest.TestCase):

    def test_rigid_transform_from_pointset_known_values(self):
        """
        Transform.from_pointset should return known result with known input for
        rigid transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if np.ndim(scaling) != 0 or scaling != 1. or shear != 0.:
                continue
            tform = Transform.from_pointset(src, dst, method='rigid')
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            self.assertEqual(1., tform.scaling)
            self.assertEqual(0., tform.shear)
            np.testing.assert_array_almost_equal(translation,
                                                 tform.translation)

    def test_rigid_transform_apply_known_values(self):
        """
        Transform.apply should return known result with known input for rigid
        transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if np.ndim(scaling) != 0 or scaling != 1. or shear != 0.:
                continue
            tform = Transform(rotation=rotation, translation=translation)
            np.testing.assert_array_almost_equal(dst, tform.apply(src))

    def test_rigid_transform_inverse_known_values(self):
        """
        Transform.inverse should return known result with known input for rigid
        transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if np.ndim(scaling) != 0 or scaling != 1. or shear != 0.:
                continue
            tform = Transform(rotation=rotation,
                              translation=translation).inverse()
            np.testing.assert_array_almost_equal(src, tform.apply(dst))


class SimilarityTransformKnownValues(unittest.TestCase):

    def test_similarity_transform_from_pointset_umeyama(self):
        """
        Transform.from_pointset should return the known results for the
        specific known input as described in the paper by Umeyama.
        """
        src = np.array([(0., 0.), (1., 0.), (0., 2.)])
        dst = np.array([(0., 0.), (-1., 0.), (0., 2.)])
        tform = Transform.from_pointset(src, dst, method='similarity')
        np.testing.assert_array_almost_equal(tform.rotation_matrix,
                                             np.array([(0.832, 0.555),
                                                       (-0.555, 0.832)]),
                                             decimal=3)
        self.assertAlmostEqual(tform.scaling, 0.721, places=3)
        self.assertEqual(0., tform.shear)
        np.testing.assert_array_almost_equal(tform.translation,
                                             np.array([-0.800, 0.400]))

    def test_similarity_transform_from_pointset_known_values(self):
        """
        Transform.from_pointset should return known result with known input for
        similarity transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if np.ndim(scaling) != 0 or shear != 0.:
                continue
            tform = Transform.from_pointset(src, dst, method='similarity')
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            self.assertAlmostEqual(scaling, tform.scaling)
            self.assertEqual(0., tform.shear)
            np.testing.assert_array_almost_equal(translation,
                                                 tform.translation)

    def test_similarity_transform_apply_known_values(self):
        """
        Transform.apply should return known result with known input for
        similarity transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if np.ndim(scaling) != 0 or shear != 0.:
                continue
            tform = Transform(rotation=rotation, scaling=scaling,
                              translation=translation)
            np.testing.assert_array_almost_equal(dst, tform.apply(src))

    def test_similarity_transform_inverse_known_values(self):
        """
        Transform.inverse should return known result with known input for
        similarity transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if np.ndim(scaling) != 0 or shear != 0.:
                continue
            tform = Transform(rotation=rotation, scaling=scaling,
                              translation=translation).inverse()
            np.testing.assert_array_almost_equal(src, tform.apply(dst))


class ScalingTransformKnownValues(unittest.TestCase):

    def test_scaling_transform_from_pointset_known_values(self):
        """
        Transform.from_pointset should return known result with known input for
        scaling transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if shear != 0.:
                continue
            tform = Transform.from_pointset(src, dst, method='scaling')
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            np.testing.assert_array_almost_equal(scaling, tform.scaling)
            self.assertEqual(0., tform.shear)
            np.testing.assert_array_almost_equal(translation,
                                                 tform.translation)

    def test_scaling_transform_from_pointset_non_negative_scaling(self):
        """
        Transform.from_pointset should not return a negative value for the
        scaling when there is a reflection in the point set for scaling
        transformations.
        """
        src = np.array([(0., 0.), (1., 0.), (0., 2.)])
        dst = np.array([(0., 0.), (-1., 0.), (0., 2.)])
        tform = Transform.from_pointset(src, dst, method='scaling')
        self.assertTrue(np.all(tform.scaling > 0.))

    def test_scaling_transform_apply_known_values(self):
        """
        Transform.apply should return known result with known input for scaling
        transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if shear != 0.:
                continue
            tform = Transform(rotation=rotation, scaling=scaling,
                              translation=translation)
            np.testing.assert_array_almost_equal(dst, tform.apply(src))

    def test_scaling_transform_inverse_known_values(self):
        """
        Transform.inverse should return known result with known input for
        scaling transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            if shear != 0.:
                continue
            tform = Transform(rotation=rotation, scaling=scaling,
                              translation=translation).inverse()
            np.testing.assert_array_almost_equal(src, tform.apply(dst))


class AffineTransformKnownValues(unittest.TestCase):

    def test_affine_transform_from_pointset_known_values(self):
        """
        Transform.from_pointset should return known result with known input for
        affine transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            tform = Transform.from_pointset(src, dst, method='affine')
            self.assertAlmostEqual(0., _angle_diff(rotation, tform.rotation))
            np.testing.assert_array_almost_equal(scaling, tform.scaling)
            self.assertAlmostEqual(shear, tform.shear)
            np.testing.assert_array_almost_equal(translation,
                                                 tform.translation)

    def test_affine_transform_from_pointset_non_negative_scaling(self):
        """
        Transform.from_pointset should not return a negative value for the
        scaling when there is a reflection in the point set for affine
        transformations.
        """
        src = np.array([(0., 0.), (1., 0.), (0., 2.)])
        dst = np.array([(0., 0.), (-1., 0.), (0., 2.)])
        tform = Transform.from_pointset(src, dst, method='affine')
        self.assertTrue(np.all(tform.scaling > 0.))

    def test_affine_transform_apply_known_values(self):
        """
        Transform.apply should return known result with known input for affine
        transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            tform = Transform(rotation=rotation, scaling=scaling,
                              shear=shear, translation=translation)
            np.testing.assert_array_almost_equal(dst, tform.apply(src))

    def test_affine_transform_inverse_known_values(self):
        """
        Transform.inverse should return known result with known input for
        affine transformations.
        """
        src = KNOWN_VALUES[0][-1]
        for rotation, scaling, shear, translation, dst in KNOWN_VALUES:
            tform = Transform(rotation=rotation, scaling=scaling, shear=shear,
                              translation=translation).inverse()
            np.testing.assert_array_almost_equal(src, tform.apply(dst))


if __name__ == '__main__':
    unittest.main()
