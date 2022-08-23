# -*- coding: utf-8 -*-
"""
Created on 13 Jan 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
# Helper functions for unit tests
import logging
import numpy
import odemis
from odemis import model, util
from odemis.util import driver
from odemis.odemisd import modelgen 
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
    Start the backend by checking the currently running backend. Basically 2 cases/scenarios:
        1. If no running backend => run the requested one.
        2. If there is running backend =>
            a. If the running backend is same as requested one => do nothing
            b. If the running backend is different from the requested one => stop the running, and run the requested one.
    In case the backend fails to start a IOError will be raised.
    config (str): path to the microscope config file.
    """
    # check if a backend is running
    if driver.get_backend_status() in (driver.BACKEND_RUNNING, driver.BACKEND_STARTING):
        current_model = model.getMicroscope().model
        try:
            req_model = modelgen.Instantiator(open(config)).ast
        except Exception as exp:
            raise ValueError(exp)
        if current_model == req_model:
            logging.info("Backend for %s already running", config)
            return
        else:
            logging.info("There is a backend running already, it will be turned off, and the backend \
                                %s will be run instead.", config)
            stop_backend()
            run_backend(config)

    # check if no backend is running
    else:
        run_backend(config)


def run_backend(config):
    """
    Run the backend based on the passed config yaml file.
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
    time.sleep(1)  # time to stop
    end = time.time() + 15  # s timeout
    while time.time() < end:
        status = driver.get_backend_status()
        if status in (driver.BACKEND_RUNNING, driver.BACKEND_STARTING):
            logging.info("Backend is stopping...")
            time.sleep(1)
        else:
            break
    else:
        raise IOError("Backend still stopping after 15 s")

    model._core._microscope = None  # force reset of the microscope for next connection

    if status != driver.BACKEND_STOPPED:
        logging.warning("Backend failed to stop, will run 'sudo odemis-stop' to kill it.")
        # TODO this is a temporary fix for stopping the back-end. When a better solution is found, remove this.
        try:
            ret = subprocess.run(["sudo", "odemis-stop"], timeout=60)
        except subprocess.TimeoutExpired as err:
            raise TimeoutError(f"Timeout while tying to stop odemis backend, with error: {err}")

        if ret.returncode != 0:
            status = driver.get_backend_status()
            raise IOError("Backend failed to stop, now %s" % status)


def assert_pos_almost_equal(actual, expected, match_all=True, *args, **kwargs):
    """
    Asserts that two stage positions have almost equal coordinates.
    :param match_all: (bool) if False, only the expected keys are checked, and actual can have more keys
    """
    if match_all and set(expected.keys()) != set(actual.keys()):
        raise AssertionError("Dimensions of position do not match: %s != %s" %
                             (list(actual.keys()), list(expected.keys())))

    for k in expected.keys():
        if not util.almost_equal(actual[k], expected[k], *args, **kwargs):
            raise AssertionError("Position (%s differs) %s != %s" % (k, actual, expected))


def assert_pos_not_almost_equal(actual, expected, match_all=True, *args, **kwargs):
    """
    Asserts that two stage positions do not have almost equal coordinates. This means at least one of the axes has a
    different value.
    :param match_all: (bool) if False, only the expected keys are checked, and actual can have more keys
    """
    if match_all and set(expected.keys()) != set(actual.keys()):
        raise AssertionError("Dimensions of position do not match: %s != %s" %
                             (list(actual.keys()), list(expected.keys())))

    # Check that at least one of the axes not equal
    for k in expected.keys():
        if not util.almost_equal(actual[k], expected[k], *args, **kwargs):
            return
    # Otherwise coordinates are almost equal
    raise AssertionError("Position %s == %s" % (actual, expected))


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


def assert_tuple_almost_equal(first, second, places=None, msg=None, delta=None):
    """
    Check two tuples are almost equal (value by value)
    """
    if places is not None and delta is not None:
        raise TypeError("Specify delta or places not both.")

    assert len(first) == len(second), "Tuples are not of equal length. " + msg

    if places:
        atol = 10 ** -places
    elif delta:
        atol = delta
    else:
        atol = 1e-7  # if both places and delta are None set atol to 1e-7
    numpy.testing.assert_allclose(first, second, rtol=0, atol=atol, err_msg=msg)
