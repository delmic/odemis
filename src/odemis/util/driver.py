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
import logging
import math
import os
import re
import sys
import threading
from collections.abc import Iterable

from Pyro4.errors import CommunicationError

from odemis import model, util


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


ATOL_LINEAR_POS = 100e-6  # m
ATOL_ROTATION_POS = 1e-3  # rad (~0.5°)


def estimateMoveDuration(distance, speed, accel):
    """
    Compute the theoretical duration of a move given the maximum speed and
    acceleration. It considers that the speed curve of the move will follow
    a trapezoidal profile: first acceleration, then maximum speed, and then
    deceleration.

    :param distance: (0 <= float) distance that will be traveled (in m)
    :param speed: (0 < float) maximum speed allowed (in m/s)
    :param accel: (0 < float) acceleration of the move, which is equal to the deceleration (in m/s²)
    return (0 <= float): time in s
    """
    if speed <= 0 or accel <= 0 or distance < 0:
        raise ValueError("Speed, accel and distance must be > 0, but got %g, %g and %g" % (speed, accel, distance))

    # Given the distance to be traveled, determine whether we have a
    # triangular or a trapezoidal motion profile.

    #  v ↑   ______________
    #    |  /¦            ¦\
    #    | / ¦            ¦ \
    #    |/  ¦            ¦  \
    #    ---------------------------→
    #    ¦ t1¦    t2      ¦t3 ¦     t
    #
    # s is the distance traveled in the triangular part
    # The profile is symmetrical therefore t1 = t3 and:
    # s1 = s3 = speed * t1 / 2
    # t1 = speed / accel
    # s = s1 + s3 = 2 * speed * t1 / 2 = speed * speed / accel = speed**2 / accel
    s = speed ** 2 / accel

    # if the total distance is larger than the distance of the triangular part,
    # the motion profile is trapezoidal
    if distance > s:
        t1 = t3 = speed / accel
        t2 = (distance - s) / speed
        return t1 + t2 + t3
    else:  # triangular
        #  v ↑    _
        #    |   /¦\
        #    |  / ¦ \
        #    | /  ¦  \
        #    -------------→
        #     ¦t1 ¦t2 ¦   t
        # Calculate the peak velocity, because for a triangular profile the max velocity might not be reached
        vp = math.sqrt(distance * accel)
        t1 = t2 = vp / accel
        return t1 + t2


def isNearPosition(current_pos, target_position, axes):
    """
    Check whether given axis is near stage target position
    :param current_pos: (dict) current position dict (axis -> value)
    :param target_position: (dict) target position dict (axis -> value)
    :param axes: (set) axes to compare values
    :return: True if the axis is near position, False otherwise
    :raises ValueError if axis is unknown
    """
    if not axes:
        logging.warning("Empty axes given.")
        return False

    rot_axes = {axis for axis in axes if axis[0] == 'r'}
    linear_axes = {axis for axis in axes if axis not in rot_axes}
    for axis in axes:
        current_value = current_pos[axis]
        target_value = target_position[axis]
        if axis in linear_axes:
            is_near = abs(target_value - current_value) < ATOL_LINEAR_POS
        elif axis in rot_axes:
            is_near = util.rot_almost_equal(current_value, target_value, atol=ATOL_ROTATION_POS)
        else:
            raise ValueError("Unknown axis value %s." % axis)
        if not is_near:
            return False
    return True


def isInRange(current_pos: dict, active_range: dict, axes: set):
    """
    Check if current position is within active range
    :param current_pos: (dict str->float) current position dict (axis -> value)
    :param active_range: (dict) imaging  active range (axis name → (min,max))
    :param axes: (set) axes to check values
    :return: True if position in active range, False otherwise
    """
    if not axes:
        logging.warning("Empty axes given.")
        return False
    for axis in axes:
        pos = current_pos[axis]
        axis_active_range = [r for r in active_range[axis]]
        # Add 1% margin for hardware slight errors
        margin = (axis_active_range[1] - axis_active_range[0]) * 0.01
        if not ((axis_active_range[0] - margin) <= pos <= (axis_active_range[1] + margin)):
            return False
    return True


DEFAULT_SPEED = 10e-6  # m/s
DEFAULT_ACCELERATION = 0.01  # m/s²


def guessActuatorMoveDuration(actuator, axis, distance, accel=DEFAULT_ACCELERATION):
    """
    Guess the speed of the axis of an actuator and estimate the duration of moving a certain distance.

    :param actuator: (Actuator) actuator object
    :param axis: (str) indicates along which axis the movement is.
    :param distance: (0 <= float) distance that will be traveled (in m)
    :param accel: (0 < float) acceleration of the move, which is equal to the deceleration (in m/s²)
    return (float >= 0): the estimated time (in s)
    """
    if not (hasattr(actuator, "axes") and isinstance(actuator.axes, dict)):
        raise ValueError("The component %s should be an actuator, but it is not." % actuator)
    if axis not in actuator.axes:
        raise KeyError("The actuator component %s is expected to have %s axis, but it does not." % (actuator, axis))

    speed = DEFAULT_SPEED
    if model.hasVA(actuator, "speed"):
        speed = actuator.speed.value.get(axis, DEFAULT_SPEED)

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
