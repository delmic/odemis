# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel
Abstract class for testing digital camera in general.

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
# This is not a real test case, but just a stub to be used for each camera driver.

import model
import time
import unittest
import gc
gc.set_debug(gc.DEBUG_LEAK | gc.DEBUG_STATS)

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
    
    
#    @classmethod
#    def setUpClass(cls):
#        cls.camera = cls.camera_type(*cls.camera_args)
    
#    @classmethod
#    def tearUpClass(cls):
#        cls.camera.terminate()
    
    def tearUp(self):
#        print gc.get_referrers(self.camera)
#        gc.collect()
        pass
        
    
    @unittest.skip("to be separated")
    def test_scan(self):
        """
        Check that we can do a scan. It can pass only if we are
        connected to at least one camera.
        """
        cameras = self.camera_type.scan()
        self.assertGreater(len(cameras), 0)

#    @unittest.skip("simple")
    def test_acquire(self):
        camera = self.camera
        self.size = camera.shape[0:2]
        exposure = 0.1

        camera.resolution.value = self.size
        camera.exposureTime.value = exposure
        
        start = time.time()
        im = camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
#        camera.terminate()
        
#    @unittest.skip("simple")
    def test_two_acquire(self):
#        camera = self.camera_type(*self.camera_args)
        camera = self.camera
        self.size = camera.shape[0:2]
        exposure = 0.1
        camera.binning.value = 1 # just to check it works
        camera.resolution.value = self.size
        camera.exposureTime.value = exposure
        
        start = time.time()
        im = camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
        
        camera.binning.value = 1 # just to check it still works
        start = time.time()
        im = camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
#        camera.terminate()
    
#    @unittest.skip("not implemented")
    def test_acquire_flow(self):
#        self.camera = self.camera_type(*self.camera_args)
        self.size = self.camera.shape[:2]
        exposure = 0.1
        self.camera.resolution.value = self.size
        self.camera.exposureTime.value = exposure
        
        number = 5
        self.left = number
        self.camera.data.subscribe(self.receive_image)
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2) # 2s per image should be more than enough in any case
        
        self.assertEqual(self.left, 0)
#        self.camera.terminate()

    def receive_image(self, dataflow, image):
        """
        callback for acquireFlow of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size)
        self.assertIn(model.MD_EXP_TIME, image.metadata)
        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)

#    @unittest.skip("simple")
    def test_binning(self):
        camera = self.camera
        
        binnings = camera.binning.choices
        self.assertIn(1, binnings)
        # The SimCam of SDKv3 doesn't support binning, so let's just try on v2
        if not 2 in binnings:
            # if there is no binning 2, there is no binning at all
            assert(len(binnings) == 1)
#            camera.terminate()
            self.skipTest("Camera doesn't support binning")
        
        # binning should automatically resize the image
        prev_size = camera.resolution.value
        camera.binning.value = 2
        
        # ask for the whole image
        self.size = (camera.shape[0] / 2, camera.shape[1] / 2)
        camera.resolution.value = self.size
        exposure = 0.1
        camera.exposureTime.value = exposure
        
        start = time.time()
        im = camera.acquireOne()
        duration = time.time() - start
    
        self.assertEqual(im.shape, self.size) # TODO a small size diff is fine if bigger than requested
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
#        camera.terminate()
        
#    @unittest.skip("simple")
    def test_aoi(self):
        """
        Check sub-area acquisition works
        """
        camera = self.camera
        self.size = (camera.shape[0]/2, camera.shape[1]/2)
        exposure = 0.1

        camera.resolution.value = self.size
        if camera.resolution.value == camera.shape[:2]:
            # cannot divide the size by 2? Then it probably doesn't support AOI
#            camera.terminate()
            self.skipTest("Camera doesn't support area of interest")
        
        camera.exposureTime.value = exposure
        start = time.time()
        im = camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)
#        camera.terminate()
        
#    @unittest.skip("simple")
    def test_error(self):
        """
        Errors should raise an exception but still allow to access the camera afterwards
        """
        camera = self.camera
        # reset
        camera.resolution.value = camera.shape[:2]
        
        
        # empty resolution
        try:
            camera.resolution.value = (camera.shape[0], 0) # 0 px should be too small
            self.fail("Empty resolution should fail")
        except:
            pass # good!
        
        # null and negative exposure time
        try:
            camera.exposureTime.value = 0.0 # 0 is too short
            self.fail("Null exposure time should fail")
        except:
            pass # good!
        
        try:
            camera.exposureTime.value = -1.0 # negative
            self.fail("Negative exposure time should fail")
        except:
            pass # good!

#        camera.terminate()