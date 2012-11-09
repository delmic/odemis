#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 6 Nov 2012

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from odemis import model
from odemis.driver import semcomedi
import comedi
import logging
import time
import unittest

"""
If you don't have a real DAQ comedi device, you can create one that can still 
pass all the tests by doing this:
sudo modprobe comedi comedi_num_legacy_minors=4
sudo modprobe comedi_test
sudo chmod a+rw /dev/comedi0
sudo comedi_config /dev/comedi0 comedi_test 1000000,1000000
"""

logging.getLogger().setLevel(logging.DEBUG)
comedi.comedi_loglevel(3)

# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed", "channel":5, "limits": [-3, 3]}
CONFIG_BSD = {"name": "bsd", "role": "bsd", "channel":6, "limits": [-0.1, 0.2]}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[0, 5], [0, 5]],
                  "channels": [0,1], "settle_time": 10e-6} 
CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0", 
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }

CONFIG_SEM2 = {"name": "sem", "role": "sem", "device": "/dev/comedi0", 
              "children": {"detector0": CONFIG_SED, "detector1": CONFIG_BSD, "scanner": CONFIG_SCANNER}
              }
#@unittest.skip("simple")
class TestSEMStatic(unittest.TestCase):
    """
    Tests which don't need a SEM component ready
    """
    def test_scan(self):
        devices = semcomedi.SEMComedi.scan()
        self.assertGreater(len(devices), 0)
        
        for name, kwargs in devices:
            print "opening ", name
            sem = semcomedi.SEMComedi("test", "sem", **kwargs)
            self.assertTrue(sem.selfTest(), "SEM self test failed.")
        
    def test_creation(self):
        """
        Doesn't even try to acquire an image, just create and delete components
        """
        sem = semcomedi.SEMComedi(**CONFIG_SEM)
        self.assertEqual(len(sem.children), 2)
        
        for child in sem.children:
            if child.name == CONFIG_SED["name"]:
                sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                scanner = child
        
        self.assertEqual(len(scanner.resolution.value), 2)
        self.assertIsInstance(sed.data, model.DataFlow)
        
        self.assertTrue(sem.selfTest(), "SEM self test failed.")
        sem.terminate()
    
    def test_error(self):
        wrong_config = dict(CONFIG_SEM)
        wrong_config["device"] = "/dev/comdeeeee"
        self.assertRaises(Exception, semcomedi.SEMComedi, None, wrong_config)
    
#@unittest.skip("simple")
class TestSEM(unittest.TestCase):
    """
    Tests which can share one SEM device
    """
    @classmethod
    def setUpClass(cls):
        cls.sem = semcomedi.SEMComedi(**CONFIG_SEM)
        
        for child in cls.sem.children:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearUpClass(cls):
        cls.sem.terminate()

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.resolution.value = [256, 256]
        self.size = tuple(self.scanner.resolution.value)
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        self.acq_dates = (set(), set()) # 2 sets of dates, one for each receiver
           
    def tearUp(self):
#        print gc.get_referrers(self.camera)
#        gc.collect()
        pass
    
#    @unittest.skip("simple")
    def test_acquire(self):
        dwell = 10e-6 # s
        self.scanner.dwellTime.value = dwell
        expected_duration = self.size[0] * self.size[1] * dwell
        
        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)
    
#    @unittest.skip("simple")
    def test_acquire_flow(self):
        dwell = self.scanner.dwellTime.value
        expected_duration = self.size[0] * self.size[1] * dwell
        
        number = 5
        self.left = number
        self.sed.data.subscribe(self.receive_image)
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + expected_duration) # 2s per image should be more than enough in any case
        
        self.assertEqual(self.left, 0)

#    @unittest.skip("simple")
    def test_acquire_with_va(self):
        """
        Change some settings before and while acquiring
        """
        dwell = self.scanner.dwellTime.range[0] * 2
        self.scanner.dwellTime.value = dwell
        self.scanner.resolution.value = self.scanner.resolution.range[1] # test big image
        self.size = tuple(self.scanner.resolution.value)
        expected_duration = self.size[0] * self.size[1] * dwell
        print expected_duration
        
        number = 3
        self.left = number
        self.sed.data.subscribe(self.receive_image)
        
        # change the attribute
        time.sleep(expected_duration)
        dwell = self.scanner.dwellTime.range[0]
        self.scanner.dwellTime.value = dwell
        expected_duration = self.size[0] * self.size[1] * dwell
                
        # should just not raise any exception
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + expected_duration) # 2s per image should be more than enough in any case
        
        self.assertEqual(self.left, 0)

    
    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size)
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)

#@unittest.skip("simple")
class TestSEM2(unittest.TestCase):
    """
    Tests which can share one SEM device with 2 detectors
    """
    @classmethod
    def setUpClass(cls):
        cls.sem = semcomedi.SEMComedi(**CONFIG_SEM2)
        
        for child in cls.sem.children:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_BSD["name"]:
                cls.bsd = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearUpClass(cls):
        cls.sem.terminate()

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.resolution.value = [256, 256]
        self.size = tuple(self.scanner.resolution.value)
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        self.acq_dates = (set(), set()) # 2 sets of dates, one for each receiver
           
    def tearUp(self):
        pass

#    @unittest.skip("simple")
    def test_acquire_two_flows(self):
        dwell = self.scanner.dwellTime.value
        expected_duration = self.size[0] * self.size[1] * dwell
        number, number2 = 3, 5
        
        self.left = number
        self.sed.data.subscribe(self.receive_image)
        
        time.sleep(expected_duration) # make sure we'll start asynchronously
        self.left2 = number2
        self.bsd.data.subscribe(self.receive_image2)
        
        for i in range(number + number2):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + expected_duration) # 2s per image should be more than enough in any case
        
        # check that at least some images were acquired simultaneously
        common_dates = self.acq_dates[0] & self.acq_dates[1]
        self.assertGreater(len(common_dates), 0, "No common dates between %r and %r" %
                           (self.acq_dates[0], self.acq_dates[1]))
        
        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)
    
    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size)
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)

    def receive_image2(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size)
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[1].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left2 -= 1
        if self.left2 <= 0:
            dataflow.unsubscribe(self.receive_image2)

if __name__ == "__main__":
    unittest.main()
