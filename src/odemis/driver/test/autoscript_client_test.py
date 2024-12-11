#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 10 Dec 2024

Copyright © 2024 Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see
http://www.gnu.org/licenses/.
"""
import logging
import math
import os
import shutil
import time
import unittest

import numpy
from odemis import model

from odemis.driver import autoscript_client
from odemis.model import ProgressiveFuture, NotSettableError
from odemis.util import testing

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Accept three values for TEST_NOHW
# * TEST_NOHW = 1: not connected to anything => skip most of the tests
# * TEST_NOHW = sim: xtadapter/server_autoscript.py sim running on localhost
# * TEST_NOHW = 0 (or anything else): connected to the real hardware
TEST_NOHW = os.environ.get("TEST_NOHW", "sim")  # Default to sim testing

if TEST_NOHW == "sim":
    pass
elif TEST_NOHW == "0":
    TEST_NOHW = False
elif TEST_NOHW == "1":
    TEST_NOHW = True
else:
    raise ValueError("Unknown value of environment variable TEST_NOHW=%s" % TEST_NOHW)


# arguments used for the creation of basic components
CONFIG_SEM_SCANNER = {"name": "Electron-Beam", "role": "e-beam", "hfw_nomag": 0.25}
CONFIG_SEM_DETECTOR = {"name": "Electron-Detector", "role": "se-detector"}
CONFIG_SEM_FOCUS = {"name": "Electron-Focus", "role": "ebeam-focus"}
CONFIG_FIB_SCANNER = {"name": "Ion-Beam", "role": "ion-beam", "hfw_nomag": 0.25}
CONFIG_FIB_DETECTOR = {"name": "Ion-Detector", "role": "se-detector-ion"}
CONFIG_FIB_FOCUS = {"name": "Ion-Focus", "role": "ion-focus"}
CONFIG_STAGE = {"name": "Stage", "role": "stage-bare"} # MD?

CONFIG_FIBSEM = {"name": "FIBSEM", "role": "fibsem", 
                 "address": "192.168.6.5", 
                 "port": 4242,
                 "children": {
                     "sem-scanner": CONFIG_SEM_SCANNER,
                    "sem-detector": CONFIG_SEM_DETECTOR,
                    "sem-focus": CONFIG_SEM_FOCUS,
                    "fib-scanner": CONFIG_FIB_SCANNER,
                    "fib-detector": CONFIG_FIB_DETECTOR,
                    "fib-focus": CONFIG_FIB_FOCUS,
                    "stage": CONFIG_STAGE,
                 }}

if TEST_NOHW == "sim":
    CONFIG_FIBSEM["address"] = "localhost"

class TestMicroscope(unittest.TestCase):
    """
    Test communication with the server using the Microscope client class.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No simulator available.")

        cls.microscope = autoscript_client.SEM(**CONFIG_FIBSEM)

    @classmethod
    def tearDownClass(cls):
        cls.microscope.terminate()

    def setUp(self):
        pass

    def test_software_version(self):
        sw_version = self.microscope.get_software_version()
        self.assertTrue(isinstance(sw_version, str))
        self.assertTrue("autoscript" in sw_version.lower())

    def test_hardware_version(self):
        hw_version = self.microscope.get_hardware_version()
        self.assertTrue(isinstance(hw_version, str))
        self.assertTrue("delmic" in hw_version.lower())

### STAGE

### BEAM

### DETECTOR

### AUTOFUNCTIONS

### IMAGING

### MILLING