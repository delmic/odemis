# -*- coding: utf-8 -*-
'''
Created on 5 Mar 2013

@author: Éric Piel

Copyright © 2013-2018 Éric Piel, Delmic

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
from Pyro4.errors import CommunicationError
from collections.abc import Iterable
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


def get_linux_version():
    """
    return (tuple of 3 int): major, minor, micro
    raise LookupError: if the version fails to find (eg: not a Linux kernel)
    """
    try:
        lv = os.uname()[2]  # version string
        sv = re.match(r"\d+\.\d+\.\d+", lv).group()  # get the raw version, without -XXX
        return tuple(int(s) for s in sv.split("."))
    except AttributeError:  # No uname, or no match
        raise LookupError("Failed to find Linux version")


# From http://code.activestate.com/recipes/286222/
_SCALE = {'KB': 2 ** 10, 'MB': 2 ** 20}

def _VmB(VmKey):
    """
    Read the memory usage for a given type
    Note: only supported on Linux
    return (int): memory used in bytes
    """
    proc_status = '/proc/%d/status' % os.getpid()
    # get pseudo file  /proc/<pid>/status
    try:
        t = open(proc_status)
        v = t.read()
        t.close()
    except Exception:
        raise NotImplementedError("Non POSIX system not supported")

    try:
        # get VmKey line e.g. 'VmRSS:  9999  kB\n ...'
        i = v.index(VmKey + ":")
        v = v[i:].split(None, 3)  # whitespaces, 4 parts
        if len(v) < 3:
            raise ValueError("Failed to find memory key %s" % (VmKey,))

        # convert to bytes
        return int(v[1]) * _SCALE[v[2].upper()]
    except (ValueError, TypeError, KeyError):
        raise NotImplementedError("System not reporting memory key %s" % (VmKey,))


def readMemoryUsage():
    """
    return (int): memory usage in bytes.
    raises:
        NotImpelementedError if OS is not supported
    """
    try:
        import psutil
        process = psutil.Process(os.getpid())
        if hasattr(process, "get_memory_info"):
            # Old API (v1.0 and below)
            mem = process.get_memory_info().rss
        else:
            # API for psutil v2+
            mem = process.memory_info().rss
        return mem
    except ImportError:
        return _VmB('VmRSS')


def estimateMoveDuration(distance, speed, accel):
    """
    Compute the theoretical duration of a move given the maximum speed and
    acceleration. It considers that the speed curve of the move will follow
    a trapezoidal profile: first acceleration, then maximum speed, and then
    deceleration.
    distance (0 <= float): distance that will be traveled (in m)
    speed (0 < float): maximum speed allowed (in m/s)
    accel (0 < float): acceleration and deceleration (in m/s²)
    return (0 <= float): time in s
    """
    if speed <= 0 or accel <= 0:
        raise ValueError("Speed and accel must be > 0, but got %g and %g" % (speed, accel))

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


DEFAULT_SPEED = 10e-6 # m/s
DEFAULT_ACCELERATION = 0.01 # m/s²

def guessActuatorMoveDuration(actuator, axis, distance, accel=DEFAULT_ACCELERATION):
    """
    actuator (Actuator): actuator object 
    axis (str): indicates along which axis the movement is. 
    distance (float): the move for which the time to be estimated  
    return (float >= 0): the estimated time (in s)
    """
    speed = None
    if not (hasattr(actuator, "axes") and isinstance(actuator.axes, dict)):
        raise ValueError("The component %s should be an actuator, but it is not." % actuator)  
    if not axis in actuator.axes:
        raise KeyError("The actuator component %s is expected to have %s axis, but it does not." % (actuator, axis))
    if model.hasVA(actuator, "speed"):
        speed = actuator.speed.value.get("z", None)
    if speed is None:
        speed = DEFAULT_SPEED  
    return estimateMoveDuration(distance, speed, accel) 


def checkLightBand(band):
    """
    Check that the given object looks like a light band. It should either be
    two float representing light wavelength in m, or a list of such tuple.
    band (object): should be tuple of floats or list of tuple of floats
    raise ValueError: if the band doesn't follow the convention
    """
    if not isinstance(band, Iterable) or len(band) == 0:
        raise ValueError("Band %r is not a (list of a) list of 2 floats" % (band,))
    # is it a list of list?
    if isinstance(band[0], Iterable):
        # => set of 2-tuples
        for sb in band:
            if len(sb) != 2:
                raise ValueError("Expected only 2 floats in band, found %d" % len(sb))
        band = tuple(band)
    else:
        # 2-tuple
        if len(band) != 2:
            raise ValueError("Expected only 2 floats in band, found %d" % len(band))
        band = (tuple(band),)

    # Check the values are min/max and in m: typically within nm (< µm!)
    max_val = 10e-6  # m
    for low, high in band:
        if low > high:
            raise ValueError("Min of band %s must be first in list" % (band,))
        if low < 0:
            raise ValueError("Band %s must be 2 positive value in meters" % (band,))
        if low > max_val or high > max_val:
            raise ValueError("Band %s contains very high values for light "
                             "wavelength, ensure the value is in meters." % (band,))

    # no error found

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
# TODO: support TERMINATING status?
def get_backend_status():
    try:
        # Fast path: if no back-end file, for sure, it is stopped.
        # The main goal is to avoid showing confusing error messages from Pyro.
        if not os.path.exists(model.BACKEND_FILE):
            return BACKEND_STOPPED

        model._core._microscope = None  # force reset of the microscope
        microscope = model.getMicroscope()
        if not microscope.ghosts.value:
            return BACKEND_RUNNING
        else:
            # Not all components are working => we are "starting" (or borked)
            return BACKEND_STARTING
    except (IOError, CommunicationError):
        if os.path.exists(model.BACKEND_FILE):
            logging.debug("No microscope found, it's sign the back-end is not responding")
            return BACKEND_DEAD
        else:
            logging.debug("Back-end %s file doesn't exists", model.BACKEND_FILE)
            return BACKEND_STOPPED
    except:
        logging.exception("Unresponsive back-end")
        return BACKEND_DEAD

    return BACKEND_DEAD  # Note: unreachable, but leave in case code will be changed
