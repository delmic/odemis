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
from cam_test_abs import VirtualTestCam, VirtualStaticTestCam
from odemis import model
from odemis.driver import andorcam2, semcomedi
from unittest.case import skip
import logging
import numpy
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

CLASS = andorcam2.AndorCam2 # use FakeAndorCam2 if you don't have the hardware
KWARGS = {"name": "camera", "role": "ccd", "device": 0} 

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
class StaticTestFake(VirtualStaticTestCam, unittest.TestCase):
    """
    Ensure we always test the fake version at least a bit
    """
    camera_type = andorcam2.FakeAndorCam2
    camera_kwargs = KWARGS

#@skip("simple")
class StaticTestAndorCam2(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = KWARGS

# Inheritance order is important for setUp, tearDown
# @skip("simple")
class TestAndorCam2(VirtualTestCam, unittest.TestCase):
    """
    Test directly the AndorCam2 class.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS
    
    @classmethod
    def setUpClass(cls):
        cls.camera = cls.camera_type(**cls.camera_kwargs)
    
    @classmethod
    def tearDownClass(cls):
        cls.camera.terminate()

# @skip("simple")
class TestSynchronized(unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    @classmethod
    def setUpClass(cls):
        cls.ccd = CLASS(**KWARGS)
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
        # Currently only tested on software simulated camera
        start = time.time()
        # use large binning, to reduce the resolution
        self.ccd.binning.value = (4, self.ccd.binning.range[1][1]) 
        
        exp = 50e-3 #s
        # in practice, it takes up to 500ms to take an image of 50 ms exposure
        self.sem_size = (10, 10)
        self.ccd_size = self.ccd.resolution.value
        numbert = numpy.prod(self.sem_size)
        
        self.ccd.exposureTime.value = exp
        # magical formula to get a long enough dwell time.
        # works with PVCam and Andorcam, but is probably different with other drivers :-(
        readout = numpy.prod(self.ccd_size) / self.ccd.readoutRate.value
        # it seems with the iVac, 20ms is enough to account for the overhead and extra image acquisition
        self.scanner.dwellTime.value = (exp + readout) * 1.1 + 0.02 
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
        
        logging.info("Took %g s", self.end_time - start)
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
            self.end_time = time.time()
            

if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
