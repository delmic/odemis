# -*- coding: utf-8 -*-
'''
Created on 5 Mar 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

from Pyro4.errors import CommunicationError
import collections
import logging
import math
from odemis import model
import os
import re
import sys
import threading


def getSerialDriver(name):
    """
    return (string): the name of the serial driver used for the given port
    """
    # In linux, can be found as link of /sys/class/tty/tty*/device/driver
    if sys.platform.startswith('linux'):
        path = ("/sys/class/tty/" + os.path.basename(os.path.realpath(name))
                + "/device/driver")
        try:
            return os.path.basename(os.readlink(path))
        except OSError:
            return "Unknown"
    else:
        # TODO: Windows version
        return "Unknown"


def estimateMoveDuration(distance, speed, accel):
    """
    Compute the theoretical duration of a move given the maximum speed and
    acceleration. It considers that the speed curve of the move will follow
    a trapezoidal profile: first acceleration, then maximum speed, and then
    deceleration.
    distance (0 <= float): distance that will be travelled (in m)
    speed (0 < float): maximum speed allowed (in m/s)
    accel (0 < float): acceleration and deceleration (in m²/s)
    return (0 <= float): time in s
    """
    # Given the distance to be traveled, determine whether we have a
    # triangular or a trapezoidal motion profile.
    A = (2 * accel) / (accel ** 2)
    s = 0.5 * A * speed ** 2
    if distance > s:
        t1 = speed / accel
        t2 = (distance - s) / speed
        t3 = speed / accel
        return t1 + t2 + t3
    else:
        vp = math.sqrt(2.0 * distance / A)
        t1 = vp / accel
        t2 = vp / accel
        return t1 + t2

# String -> VA conversion helper

def boolify(s):
    if s == 'True' or s == 'true':
        return True
    if s == 'False' or s == 'false':
        return False
    raise ValueError('Not a boolean value: %s' % s)


def reproduceTypedValue(real_val, str_val):
    """
    Tries to convert a string to the type of the given value
    real_val (object): example value with the type that must be converted to
    str_val (string): string that will be converted
    return the value contained in the string with the type of the real value
    raises
      ValueError() if not possible to convert
      TypeError() if type of real value is not supported
    """
    if isinstance(real_val, bool):
        return boolify(str_val)
    elif isinstance(real_val, int):
        return int(str_val)
    elif isinstance(real_val, float):
        return float(str_val)
    elif isinstance(real_val, basestring):
        return str_val
    elif isinstance(real_val, dict): # must be before iterable
        if len(real_val) > 0:
            key_real_val = real_val.keys()[0]
            value_real_val = real_val[key_real_val]
        else:
            logging.warning("Type of attribute is unknown, using string")
            sub_real_val = ""
            value_real_val = ""

        dict_val = {}
        for sub_str in str_val.split(','):
            item = sub_str.split(':')
            if len(item) != 2:
                raise ValueError("Cannot convert '%s' to a dictionary item" % item)
            key = reproduceTypedValue(key_real_val, item[0]) # TODO Should warn if len(item) != 2
            value = reproduceTypedValue(value_real_val, item[1])
            dict_val[key] = value
        return dict_val
    elif isinstance(real_val, collections.Iterable):
        if len(real_val) > 0:
            sub_real_val = real_val[0]
        else:
            logging.warning("Type of attribute is unknown, using string")
            sub_real_val = ""

        # Try to be open-minded if the sub-type is a number (so that things like
        # " 3 x 5 px" returns (3, 5)
        if isinstance(sub_real_val, (int, long)):
            pattern = "[+-]?[\d]+" # ex: -15
        elif isinstance(sub_real_val, float):
            pattern = "[+-]?[\d.]+(?:[eE][+-]?[\d]+)?" # ex: -156.41e-9
        else:
            pattern = "[^,]+"

        iter_val = []
        for sub_str in re.findall(pattern, str_val):
            iter_val.append(reproduceTypedValue(sub_real_val, sub_str))
        final_val = type(real_val)(iter_val) # cast to real type
        return final_val

    raise TypeError("Type %r is not supported to convert %s" % (type(real_val), str_val))


# Special trick functions for speeding up Pyro start-up
def _speedUpPyroVAConnect(comp):
    """
    Ensures that all the VAs of the component will be quick to access
    comp (Component)
    """
    # Force the creation of the connection
    # If the connection already exists it's very fast, otherwise, we wait
    # for the connection to be created in a separate thread

    for name, va in model.getVAs(comp).items():
        t = threading.Thread(name="Connection to VA %s.%s" % (comp.name, name),
                             target=va._pyroBind)
        t.daemon = True
        t.start()

def speedUpPyroConnect(comp):
    """
    Ensures that all the children of the component will be quick to access.
    It does nothing but speed up later access.
    comp (Component)
    """
    # each connection is pretty fast (~10ms) but when listing all the VAs of
    # all the components, it can easily add up to 1s if done sequentially.

    def bind_obj(obj):
#        logging.debug("binding comp %s", obj.name)
        obj._pyroBind()
        speedUpPyroConnect(obj)

    _speedUpPyroVAConnect(comp)

    for child in comp.children.value:
        t = threading.Thread(name="Connection to %s" % child.name, target=bind_obj, args=(child,))
        t.start()


BACKEND_RUNNING = "RUNNING"
BACKEND_STARTING = "STARTING"
BACKEND_DEAD = "DEAD"
BACKEND_STOPPED = "STOPPED"
def get_backend_status():
    try:
        model._core._microscope = None # force reset of the microscope
        microscope = model.getMicroscope()
        if not microscope.ghosts.value:
            return BACKEND_RUNNING
        else:
            # Not all components are working => we are "starting" (or borked)
            return BACKEND_STARTING
    except (IOError, CommunicationError):
        logging.info("Failed to find microscope")
        if os.path.exists(model.BACKEND_FILE):
            return BACKEND_DEAD
        else:
            logging.info("Back-end %s file doesn't exists", model.BACKEND_FILE)
            return BACKEND_STOPPED
    except:
        logging.exception("Unresponsive back-end")
        return BACKEND_DEAD

    return BACKEND_DEAD
