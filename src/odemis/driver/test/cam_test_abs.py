# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel
Abstract class for testing digital camera in general.

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# This is not a real test case, but just a stub to be used for each camera driver.

from odemis import model
import time
import unittest
import gc
#gc.set_debug(gc.DEBUG_LEAK | gc.DEBUG_STATS)

class VirtualStaticTestCam(object):
    """
    For tests which don't need a camera ready
    """
    # needs:
    # camera_type : class of the camera
    # camera_args : tuple of arguments to create a camera
    camera_type = None
    # name, role, children...
    camera_args = ("camera", "test", None)
    
    def test_scan(self):
        """
        Check that we can do a scan. It can pass only if we are
        connected to at least one camera.
        """
        cameras = self.camera_type.scan()
        self.assertGreater(len(cameras), 0)
    

# It doesn't inherit from TestCase because it should not be run by itself
class VirtualTestCam(object):
    """
    Virtual class for all the (andor) cameras
    """
    
    # needs:
    # camera_type : class of the camera
    # camera_args : tuple of arguments to create a camera
    camera_type = None
    # name, role, children...
    camera_args = ("camera", "test", None)
    camera = None
    
    # doesn't work as it's not a TestCase
#    @classmethod
#    def setUpClass(cls):
#        cls.camera = cls.camera_type(*cls.camera_args)
    
#    @classmethod
#    def tearDownClass(cls):
#        cls.camera.terminate()
 
    def setUp(self):
        # reset size and binning
        if isinstance(self.camera.binning.value, tuple):
            self.camera.binning.value = (1, 1)
        else:
            self.camera.binning.value = 1
        self.size = self.camera.shape[0:2]
        self.camera.resolution.value = self.size
        self.acq_dates = (set(), set()) # 2 sets of dates, one for each receiver 
           
    def tearDown(self):
#        print gc.get_referrers(self.camera)
#        gc.collect()
        pass
    
#    @unittest.skip("simple")
    def test_temp(self):
        if (not hasattr(self.camera, "targetTemperature") or 
            not isinstance(self.camera.targetTemperature, model.VigilantAttributeBase)):
            self.skipTest("Camera doesn't support setting temperature")
        
        ttemp = self.camera.targetTemperature.value
        self.assertTrue(-300 < ttemp and ttemp < 100)
        self.camera.targetTemperature.value = self.camera.targetTemperature.range[0]
        self.assertEqual(self.camera.targetTemperature.value, self.camera.targetTemperature.range[0])
    
#    @unittest.skip("simple")
    def test_acquire(self):
        self.assertEqual(len(self.camera.shape), 3)
        exposure = 0.1
        self.camera.exposureTime.value = exposure
        
        start = time.time()
        im = self.camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[-1:-3:-1])
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
        
#    @unittest.skip("simple")
    def test_two_acquire(self):
        exposure = 0.1
        
        # just to check it works
        if isinstance(self.camera.binning.value, tuple):
            self.camera.binning.value = (1, 1)
        else:
            self.camera.binning.value = 1
        
        self.camera.exposureTime.value = exposure
        
        start = time.time()
        im = self.camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[-1:-3:-1])
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
        
        # just to check it still works
        if isinstance(self.camera.binning.value, tuple):
            self.camera.binning.value = (1, 1)
        else:
            self.camera.binning.value = 1
            
        start = time.time()
        im = self.camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[-1:-3:-1])
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)

#    @unittest.skip("simple")
    def test_acquire_flow(self):
        exposure = 0.1
        self.camera.exposureTime.value = exposure
        
        number = 5
        self.left = number
        self.camera.data.subscribe(self.receive_image)
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case
        
        self.assertEqual(self.left, 0)

#    @unittest.skip("simple")
    def test_data_flow_with_va(self):
        exposure = 1.0 # long enough to be sure we can change VAs before the end
        self.camera.exposureTime.value = exposure
        
        number = 3
        self.left = number
        self.camera.data.subscribe(self.receive_image)
        
        # change the attribute
        time.sleep(exposure)
        self.camera.exposureTime.value = exposure/2        
        # should just not raise any exception
        
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case
        
        self.assertEqual(self.left, 0)

#    @unittest.skip("not implemented")
    def test_df_subscribe_get(self):
        exposure = 1.0 # long enough to be sure we can do a get before the end
        self.camera.exposureTime.value = exposure
        
        number = 3
        self.left = number
        self.camera.data.subscribe(self.receive_image)
        
        # change the attribute
        time.sleep(exposure)
        self.camera.exposureTime.value = exposure/2
        # should just not raise any exception
        
        # get one image: probably the first one from the subscribe (without new exposure)
        im = self.camera.data.get()
        
        # get a second image (this one must be generated with the new settings)
        start = time.time()
        im = self.camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[-1:-3:-1])
        self.assertGreaterEqual(duration, exposure/2, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
        
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case
        
        self.assertEqual(self.left, 0)
    
#    @unittest.skip("simple")
    def test_df_double_subscribe(self):
        exposure = 1.0 # long enough to be sure we can do a get before the end
        number, number2 = 3, 5
        self.camera.exposureTime.value = exposure
        
        self.left = number
        self.camera.data.subscribe(self.receive_image)
        
        time.sleep(exposure)
        self.left2 = number2
        self.camera.data.subscribe(self.receive_image2)
        
        for i in range(number + number2):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case
        
        # check that at least some images are shared?
        common_dates = self.acq_dates[0] & self.acq_dates[1]
        self.assertGreater(len(common_dates), 0, "No common dates between %r and %r" %
                           (self.acq_dates[0], self.acq_dates[1]))
        
        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

#    @unittest.skip("simple")
    def test_df_alternate_sub_unsub(self):
        """
        Test the dataflow on a quick cycle subscribing/unsubscribing
        Andorcam3 had a real bug causing deadlock in this scenario
        """ 
        exposure = 0.1 # s
        number = 5
        self.camera.exposureTime.value = exposure
        
        self.left = 10000 + number # don't unsubscribe automatically
        
        for i in range(number):
            self.camera.data.subscribe(self.receive_image)
        
            time.sleep(1 + exposure) # make sure we received at least one image
            self.camera.data.unsubscribe(self.receive_image)

        # if it has acquired a least 5 pictures we are already happy
        self.assertLessEqual(self.left, 10000)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_EXP_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)


    def receive_image2(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_EXP_TIME, image.metadata)
        self.acq_dates[1].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image in 2"
        self.left2 -= 1
        if self.left2 <= 0:
            dataflow.unsubscribe(self.receive_image2)

#    @unittest.skip("simple")
    def test_binning(self):
        if isinstance(self.camera.binning.value, tuple):
            self.camera.binning.value = (1, 1)
            max_binning = self.camera.binning.range[1] 
            new_binning = (2, 2)
            if new_binning >= max_binning:
                # if there is no binning 2, let's not try
                self.skipTest("Camera doesn't support binning")
        else:
            binnings = self.camera.binning.choices
            self.camera.binning.value = 1
            self.assertIn(1, binnings)
            # The SimCam of SDKv3 doesn't support binning, so let's just try on v2
            if not 2 in binnings:
                # if there is no binning 2, there is no binning at all
                assert(len(binnings) == 1)
                self.skipTest("Camera doesn't support binning")
            new_binning = 2
        
        # binning should automatically resize the image
        prev_size = self.camera.resolution.value
        self.camera.binning.value = new_binning
        self.assertNotEqual(self.camera.resolution.value, prev_size)
        
        # ask for the whole image
        self.size = (self.camera.shape[0] / 2, self.camera.shape[1] / 2)
        self.camera.resolution.value = self.size
        exposure = 0.1
        self.camera.exposureTime.value = exposure
        
        start = time.time()
        im = self.camera.data.get()
        duration = time.time() - start
    
        self.assertEqual(im.shape, self.size[-1:-3:-1]) # TODO a small size diff is fine if bigger than requested
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
        self.assertEqual(im.metadata[model.MD_BINNING], new_binning)
        
#    @unittest.skip("simple")
    def test_aoi(self):
        """
        Check sub-area acquisition works
        """
        self.size = (self.camera.shape[0]/2, self.camera.shape[1]/2)
        exposure = 0.1

        self.camera.resolution.value = self.size
        if self.camera.resolution.value == self.camera.shape[:2]:
            # cannot divide the size by 2? Then it probably doesn't support AOI
            self.skipTest("Camera doesn't support area of interest")
        
        self.camera.exposureTime.value = exposure
        start = time.time()
        im = self.camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[-1:-3:-1])
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
        
#    @unittest.skip("simple")
    def test_error(self):
        """
        Errors should raise an exception but still allow to access the camera afterwards
        """
        # empty resolution
        try:
            self.camera.resolution.value = (self.camera.shape[0], 0) # 0 px should be too small
            self.fail("Empty resolution should fail")
        except:
            pass # good!
        
        # null and negative exposure time
        try:
            self.camera.exposureTime.value = 0.0 # 0 is too short
            self.fail("Null exposure time should fail")
        except:
            pass # good!
        
        try:
            self.camera.exposureTime.value = -1.0 # negative
            self.fail("Negative exposure time should fail")
        except:
            pass # good!

