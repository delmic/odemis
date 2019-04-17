# -*- coding: utf-8 -*-
'''
Created on 17 April 2019

@author: Anders Muskens

Copyright © 2012-2019 Anders Muskens, Delmic

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
from __future__ import division
from concurrent.futures import CancelledError, TimeoutError

import os
import logging
from ctypes import *

from odemis import model
from odemis.util import driver
from odemis.model import HwError, CancellableFuture, CancellableThreadPoolExecutor, isasync

class SmartPodDLL(CDLL):
    """
    Subclass of CDLL specific to andor library, which handles error codes for
    all the functions automatically.
    It works by setting a default _FuncPtr.errcheck.
    """
    
    # Defines
    SMARPOD_SENSORS_DISABLED = 0
    SMARPOD_SENSORS_ENABLED = 1
    SMARPOD_SENSORS_POWERSAVE = 2

    def __init__(self):
        if os.name == "nt":
            raise NotImplemented("Windows not yet supported")
            # WinDLL.__init__(self, "atmcd32d.dll")  # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libsmarpod.so", RTLD_GLOBAL)
            self.major = c_uint()
            self.minor = c_uint()
            self.update = c_uint()
            self.Smarpod_GetDLLVersion(byref(self.major), byref(self.minor), byref(self.update))
            logging.debug("using SmarPod library version %u.%u.%u", self.major.value, self.minor.value, self.update.value)


class SmartPod(model.Actuator):
    
    def __init__(self, name, role, locator, options, axes=None, **kwargs):

        if len(axes) == 0:
            raise ValueError("Needs at least 1 axis.")

        self.core = SmartPodDLL()

        # Not to be mistaken with axes which is a simple public view
        self._axis_map = {}  # axis name -> axis number used by controller
        axes_def = {}  # axis name -> Axis object
        self._locator = c_char_p(locator)
        self._options = c_char_p(options)

        for axis_name, axis_par in axes.items():
            # Unpack axis parameters from the definitions in the YAML
            try:
                axis_num = axis_par['number']
            except KeyError:
                raise ValueError("Axis %s must have a number to identify it. " % (axis_name,))

            try:
                axis_range = axis_par['range']
            except KeyError:
                logging.info("Axis %s has no range. Assuming (-1, 1)", axis_name)
                axis_range = (-1, 1)

            try:
                axis_unit = axis_par['unit']
            except KeyError:
                logging.info("Axis %s has no unit. Assuming m", axis_name)
                axis_unit = "m"

            self._axis_map[axis_name] = axis_num

            ad = model.Axis(canAbs=True, unit=axis_unit, range=axis_range)
            axes_def[axis_name] = ad
            
            
        # Connect to the device
        self._id = c_uint()
        self.core.Smarpod_Open(byref(self._id), c_uint(10001), pointer(self._locator), pointer(self._options))
        self.core.Smarpod_SetSensorMode(byref(self._id), SmartPodDLL.SMARPOD_SENSORS_ENABLED)
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

