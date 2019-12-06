# -*- coding: utf-8 -*-
'''
Created on 13 Jan 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Helper functions for unit tests
from __future__ import division, print_function

import logging
import numpy
from odemis import model, util
import odemis
from odemis.util import driver
import os
import resource
import subprocess
import sys
import time


# ODEMISD_CMD = ["/usr/bin/python2", "-m", "odemis.odemisd.main"]
# -m doesn't work when run from PyDev... not entirely sure why
ODEMISD_CMD = [sys.executable, os.path.dirname(odemis.__file__) + "/odemisd/main.py"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]


def setlimits():
    # Increase the maximum number of files openable, as needed if many remote
    # objects are created
    logging.info("Setting resource limit in child (pid %d)", os.getpid())
    resource.setrlimit(resource.RLIMIT_NOFILE, (3092, 3092))


def start_backend(config):
    """
    Start the backend
    config (str): path to the microscope config file
    raises:
        LookupError: if a backend is already running
        IOError: if backend failed to start
    """
    if driver.get_backend_status() in (driver.BACKEND_RUNNING, driver.BACKEND_STARTING):
        raise LookupError("A running backend is already found")

    logging.info("Starting backend with config file '%s'", config)

    # run the backend as a daemon
    # we cannot run it normally as the child would also think he's in a unittest
    cmd = ODEMISD_CMD + ODEMISD_ARG + [config]
    ret = subprocess.call(cmd, preexec_fn=setlimits)
    if ret != 0:
        raise IOError("Failed starting backend with '%s' (returned %d)" % (cmd, ret))

    time.sleep(3)  # give some time to the backend to start a little bit

    timeout = 30  # s timeout
    end = time.time() + timeout
    while time.time() < end:
        status = driver.get_backend_status()
        if status == driver.BACKEND_STARTING:
            logging.info("Backend is starting...")
            time.sleep(1)
        else:
            break
    else:
        raise IOError("Backend still starting after %d s" % (timeout,))

    if status != driver.BACKEND_RUNNING:
        raise IOError("Backend failed to start, now %s" % status)


def stop_backend():
    """
    Stop the backend and wait for it to be fully stopped
    """
    cmd = ODEMISD_CMD + ["--kill"]
    ret = subprocess.call(cmd)
    if ret != 0:
        raise IOError("Failed stopping backend with '%s' (returned %d)" % (cmd, ret))

    # wait for the backend to be fully stopped
    time.sleep(1) # time to stop
    end = time.time() + 15 # s timeout
    while time.time() < end:
        status = driver.get_backend_status()
        if status in (driver.BACKEND_RUNNING, driver.BACKEND_STARTING):
            logging.info("Backend is stopping...")
            time.sleep(1)
        else:
            break
    else:
        raise IOError("Backend still stopping after 15 s")

    model._core._microscope = None # force reset of the microscope for next connection

    if status != driver.BACKEND_STOPPED:
        raise IOError("Backend failed to stop, now %s" % status)


def assert_pos_almost_equal(actual, expected, *args, **kwargs):
    """
    Asserts that two stage positions have almost equal coordinates.
    """
    if set(expected.keys()) != set(actual.keys()):
        raise AssertionError("Dimensions of position do not match: %s != %s" %
                             (list(actual.keys()), list(expected.keys())))

    for k in expected.keys():
        if not util.almost_equal(actual[k], expected[k], *args, **kwargs):
            raise AssertionError("Position %s != %s" % (actual, expected))


def assert_array_not_equal(a, b, msg="Arrays are equal"):
    """
    Asserts that two numpy arrays are not equal (where NaN are considered equal)
    """
    # TODO: an option to check that the shapes are equal, but the values different?

    # Using numpy.any(a != b) would almost work, but NaN are always considered
    # unequal, so instead, use numpy's assert_array_equal to do the work.
    try:
        numpy.testing.assert_array_equal(a, b)
    except AssertionError:
        # Perfect, they are not equal
        return

    raise AssertionError(msg)
