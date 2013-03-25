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
from odemis import model
from odemis.driver import pvcam, semcomedi
from unittest.case import skip
import logging
import numpy
import sys
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

CLASS = pvcam.PVCam
# device can also be "rspiusb", but 0 is more generic
CONFIG_PVCAM = {"name": "camera", "role": "ccd", "device": 0} 

# arguments used for the creation of the SEM simulator
# Note that you need to run this line after a boot, for the simulator to work:
# sudo comedi_config /dev/comedi0 comedi_test 1000000,1000000

CONFIG_SED = {"name": "sed", "role": "sed", "channel":5, "limits": [-3, 3]}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[0, 5], [0, 5]],
                  "channels": [0,1], "settle_time": 10e-6, "hfw_nomag": 10e-3} 
CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0", 
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }

#@skip("simple")
class StaticTestPVCam(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = CONFIG_PVCAM

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
        cls.camera = cls.camera_type(**cls.camera_kwargs)
    
    @classmethod
    def tearDownClass(cls):
        cls.camera.terminate()

#@skip("simple")
class TestSynchronized(unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    @classmethod
    def setUpClass(cls):
        cls.ccd = CLASS(**CONFIG_PVCAM)
        cls.sem = semcomedi.SEMComedi(**CONFIG_SEM)
        
        for child in cls.sem.children:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearUpClass(cls):
        cls.ccd.terminate()
        cls.sem.terminate()
        
    def setUp(self):
        pass
    
    def tearUp(self):
        # just in case it failed
        self.ccd.data.subscribe(self.receive_ccd_image)
        self.sed.data.subscribe(self.receive_sem_data)
     
    def test_basic(self):
        """
        check the synchronization of the SEM with the CCD:
        The SEM scans a region and for each point, the CCD acquires one image.
        """
        # use large binning, to reduce the resolution
        self.ccd.binning.value = (1, self.ccd.binning.range[1][1]) 
        
        exp = 50e-3 #s
        # in practice, it takes up to 100ms to take an image of 50 ms exposure
        self.sem_size = (10, 10)
        self.ccd_size = self.ccd.resolution.value
        numbert = numpy.prod(self.sem_size)
        
        self.ccd.exposureTime.value = exp
        # magical formula to get a long enough dwell time.
        # works with PVCam, but is probably different with other drivers :-(
        readout = numpy.prod(self.ccd_size) / self.ccd.readoutRate.value + 0.01
        self.scanner.dwellTime.value = (exp + readout) * 1.1 + 0.06 # 60ms to account for the overhead and extra image acquisition 
        self.scanner.resolution.value = self.sem_size
        # pixel write/read setup is pretty expensive ~10ms
        expected_duration = numbert * (self.scanner.dwellTime.value + 0.01)
        
        self.sem_left = 1 # unsubscribe just after one
        self.ccd_left = numbert # unsubscribe after receiving

        self.ccd.data.synchronizedOn(self.scanner.newPosition)
        self.ccd.data.subscribe(self.receive_ccd_image)
        
        self.sed.data.subscribe(self.receive_sem_data)
        for i in range(10):
            # * 3 because it can be quite long to setup each pixel.
            time.sleep(expected_duration * 2 / 10)
            if self.sem_left == 0:
                break # just to make it quicker if it's quicker
         
        time.sleep(exp + readout)
        self.assertEqual(self.sem_left, 0)
        self.assertEqual(self.ccd_left, 0)
        self.ccd.data.synchronizedOn(None)
        
        # check we can still get data normally
        d = self.ccd.data.get()
        
        time.sleep(0.1)

    def receive_sem_data(self, dataflow, image):
        """
        callback for SEM df
        """
        self.assertEqual(image.shape, self.sem_size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.sem_left -= 1
        if self.sem_left <= 0:
            dataflow.unsubscribe(self.receive_sem_data)
    
    def receive_ccd_image(self, dataflow, image):
        """
        callback for CCD
        """
        self.assertEqual(image.shape, self.ccd_size[-1:-3:-1])
        self.ccd_left -= 1
        if self.ccd_left <= 0:
            dataflow.unsubscribe(self.receive_ccd_image)
            
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

