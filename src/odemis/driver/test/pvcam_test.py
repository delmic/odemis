# -*- coding: utf-8 -*-
'''
Created on 25 Feb 2013

@author: piel

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
import logging
from odemis.driver import pvcam
import os
import unittest
from unittest.case import skip

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, VirtualTestSynchronized


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CLASS = pvcam.PVCam
# device can also be "rspiusb", but 0 is more generic
CONFIG_PVCAM = dict(name="camera", role="ccd", device=0, transpose=[2, -1])

#@skip("simple")
class StaticTestPVCam(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = CONFIG_PVCAM

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            # Technically, we don't need the hardware, but at least the lipvcam is needed,
            # and it's not available on 64 bits.
            raise unittest.SkipTest("No simulator available")
        super().setUpClass()


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestPVCam(VirtualTestCam, unittest.TestCase):
    """
    Test directly the PVCam class.
    """
    camera_type = CLASS
    camera_kwargs = CONFIG_PVCAM

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest("No simulator available")
        super().setUpClass()


#@skip("simple")
class TestSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    camera_type = CLASS
    camera_kwargs = CONFIG_PVCAM

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest("No simulator available")
        super().setUpClass()


if __name__ == '__main__':
    unittest.main()


# Debug:
#import logging
#from odemis.driver import pvcam_h as pv
#from odemis.driver import pvcam
#logging.getLogger().setLevel(logging.DEBUG)
#p = pvcam.PVCam("cam", "test", 0)
##p.exp_check_status()
##p.pvcam.pl_exp_abort(p._handle, pv.CCS_HALT)
##p.pvcam.pl_cam_get_diags(p._handle)
##p.targetTemperature.value = 21
##p.readoutRate.choices
##d = p.data.get()
##print d.shape
#p.exposureTime.value = 0.1 # 0.75 is the threshold
#def got(df, d):
#    print d.shape
#
#p.data.subscribe(got)
#p.readoutRate.value = max(p.readoutRate.choices)
#p.readoutRate.value = min(p.readoutRate.choices)
#p.data.unsubscribe(got)
#p.set_param(pv.PARAM_SPDTAB_INDEX, 1)
#p.set_param(pv.PARAM_GAIN_INDEX, 1)
#p.pvcam.pl_cam_close(p._handle)
#p._handle = p.cam_open(p._name, pv.OPEN_EXCLUSIVE)
#
#p._setStaticSettings()
#p.Reinitialize()
