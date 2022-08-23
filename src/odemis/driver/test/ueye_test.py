#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 31 Jun 2016

@author: Éric Piel
Testing class for driver.ueye .

Copyright © 2012 Éric Piel, Delmic

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
from odemis.driver import ueye
import os
import unittest
from unittest.case import skip

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, VirtualTestSynchronized


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CLASS = ueye.Camera
KWARGS = dict(name="camera", role="ccd", device=None, transp=[2, -1])


class StaticTestUEye(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = KWARGS


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestUEye(VirtualTestCam, unittest.TestCase):
    """
    Test directly the UEye class.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No camera HW present. Skipping tests.')

        super(TestUEye, cls).setUpClass()

    @classmethod
    def tearDownClass(cls):
        if TEST_NOHW:
            return
        super(TestUEye, cls).tearDownClass()

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No simulator available")
        VirtualTestCam.setUp(self)


# @skip("simple")
class TestSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    camera_type = CLASS
    camera_kwargs = KWARGS

    @classmethod
    def setUpClass(cls):
        if not TEST_NOHW:
            VirtualTestCam.setUpClass()

    @classmethod
    def tearDownClass(self):
        if not TEST_NOHW:
            VirtualTestCam.tearDown(self)

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No simulator available")
        VirtualTestCam.setUp(self)


if __name__ == '__main__':
    unittest.main()
