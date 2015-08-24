#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel
Testing class for driver.andorcam2 .

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
from __future__ import division

import logging
from odemis.driver import andorcam2
import os
import unittest
from unittest.case import skip

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, VirtualTestSynchronized


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

CLASS_SIM = andorcam2.FakeAndorCam2
CLASS = andorcam2.AndorCam2

KWARGS_SIM = dict(name="camera", role="ccd", device=0, transpose=[2, -1],
                  emgains=[[10e6, 1, 50], [1e6, 1, 150]],
                  image="andorcam2-fake-clara.tiff")
#                  image="andorcam2-fake-spots.h5")
KWARGS = dict(name="camera", role="ccd", device=0, transpose=[2, -1],
              emgains=[[10e6, 1, 50], [1e6, 1, 150]])

if TEST_NOHW:
    CLASS = CLASS_SIM
    KWARGS = KWARGS_SIM

#@skip("simple")
class StaticTestFake(VirtualStaticTestCam, unittest.TestCase):
    """
    Ensure we always test the fake version at least a bit
    """
    camera_type = andorcam2.FakeAndorCam2
    camera_kwargs = KWARGS_SIM


class TestFake(VirtualTestCam, unittest.TestCase):
    """
    Ensure we always test the fake version at least a bit
    """
    camera_type = CLASS_SIM
    camera_kwargs = KWARGS_SIM


#@skip("simple")
class StaticTestAndorCam2(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = KWARGS


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestAndorCam2(VirtualTestCam, unittest.TestCase):
    """
    Test directly the AndorCam2 class.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS


#@skip("simple")
class TestSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    camera_type = CLASS
    camera_kwargs = KWARGS


if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
