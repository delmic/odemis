#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel
Testing class for driver.andorcam2 .

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from abs_cam_test import VirtualTestCam, VirtualStaticTestCam
from driver import andorcam2
import logging
import unittest

logging.getLogger().setLevel(logging.DEBUG)

#@unittest.skip("simple")
class StaticTestAndorCam2(VirtualStaticTestCam, unittest.TestCase):
    camera_type = andorcam2.AndorCam2
    camera_args = ("camera", "test", None, 0)

# Inheritance order is important for setUp, tearDown
class TestAndorCam2(VirtualTestCam, unittest.TestCase):
    """
    Test directly the AndorCam2 class.
    """
    camera_type = andorcam2.AndorCam2
    camera_args = ("camera", "test", None, 0)
    
    @classmethod
    def setUpClass(cls):
        cls.camera = cls.camera_type(*cls.camera_args)
    
    @classmethod
    def tearUpClass(cls):
        cls.camera.terminate()
    
if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: