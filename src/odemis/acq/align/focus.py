# -*- coding: utf-8 -*-
"""
Created on 11 Apr 2014

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

import numpy
import threading
from scipy import signal

_acq_lock = threading.Lock()
_ccd_done = threading.Event()

MAX_STEPS_NUMBER = 3  # Max steps to perform autofocus

def MeasureFocus(image):
    """
    Given an image, focus measure is calculated using the modified Laplacian
    method. See http://www.sayonics.com/publications/pertuz_PR2013.pdf
    image (model.DataArray): Optical image
    returns (float):    The focus level of the optical image
    """
    m = numpy.array([[-1], [2], [-1]])
    Lx = signal.correlate(image, m, 'valid')
    Ly = signal.correlate(image, m.reshape(-1, 1), 'valid')
    Fm = numpy.abs(Lx) + abs(Ly)
    Fm = numpy.mean(Fm)

    return Fm


def AutoFocus(ccd, focus):
    """
    Iteratively acquires an optical image, measures its focus level and adjusts 
    the optical focus with respect to the focus level. 
    ccd (model.DigitalCamera): The CCD
    focus (model.CombinedActuator): The optical focus
    returns (float):    Focus position #m
    """
    # Determine focus direction
    step = 15e-6
    image = ccd.data.get()
    fm_cur = MeasureFocus(image)
    f = focus.moveRel({"z": step})
    f.result()
    image = ccd.data.get()
    fm_test = MeasureFocus(image)
    if fm_cur > fm_test:
        sign = -1
    else:
        sign = 1
    
    # Move the lens in the correct direction until focus measure is decreased
    step = 5e-6
    fm_old, fm_new = fm_test, fm_test
    steps = 0
    while fm_old <= fm_new:
        if steps >= MAX_STEPS_NUMBER:
            break
        fm_old = fm_new
        f = focus.moveRel({"z":sign * step})
        f.result()
        image = ccd.data.get()
        fm_new = MeasureFocus(image)
        steps += 1
    focus.moveRel({"z":-sign * step})
    f.result()

    # Perform binary search in the interval containing the last two lens
    # positions
    step = step / 2
    f = focus.moveRel({"z":sign * step})
    f.result()
    while step >= 0.5e-6:
        step = step / 2
        image = ccd.data.get()
        fm_new = MeasureFocus(image)
        if fm_new < fm_old:
            sign = -sign
            f = focus.moveRel({"z":sign * step})
        else:
            f = focus.moveRel({"z":sign * step})
        f.result()
        fm_old = fm_new

    return focus.position.value

