# -*- coding: utf-8 -*-
'''
Created on 25 Feb 2013

@author: piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam
from odemis.driver import pvcam
from unittest.case import skip
import logging
import unittest

logging.getLogger().setLevel(logging.DEBUG)

#@skip("simple")
class StaticTestPVCam(VirtualStaticTestCam, unittest.TestCase):
    camera_type = pvcam.PVCam
    camera_args = ("camera", "test", 0)

# Inheritance order is important for setUp, tearDown
class TestPVCam(VirtualTestCam, unittest.TestCase):
    """
    Test directly the PVCam class.
    """
    camera_type = pvcam.PVCam
    # name, role, device number
    camera_args = ("camera", "test", 0)

    @classmethod
    def setUpClass(cls):
        cls.camera = cls.camera_type(*cls.camera_args)
    
    @classmethod
    def tearDownClass(cls):
        cls.camera.terminate()
        
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

