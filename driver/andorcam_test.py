#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel
Testing class for pi.py and dacontrol.py .

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import andorcam2
import andorcam3
import time
import unittest

class VirtualTestAndorCam(object):
    """
    Virtual class for all the (andor) cameras
    """

    # needs:
    # camera_type : class of the camera
    # camera_args : tuple of arguments to create a camera
        
    def test_scan(self):
        """
        Check that we can do a scan. It can pass only if we are
        connected to at least one camera.
        """
        cameras = self.camera_type.scan()
        self.assertGreater(len(cameras), 0)

    def test_acquire(self):
        camera = self.camera_type(*self.camera_args)
        self.size = camera.getSensorResolution()
        exposure = 0.1
        start = time.time()
        im, metadata = camera.acquire(self.size, exposure)
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn("Exposure time", metadata)
        
    def test_two_acquire(self):
        camera = self.camera_type(*self.camera_args)
        self.size = camera.getSensorResolution()
        exposure = 0.1
        start = time.time()
        im, metadata = camera.acquire(self.size, exposure)
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn("Exposure time", metadata)
        
        start = time.time()
        im, metadata = camera.acquire(self.size, exposure)
        duration = time.time() - start

        self.assertEqual(im.shape, self.size)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn("Exposure time", metadata)
        
    def test_acquire_flow(self):
        camera = self.camera_type(*self.camera_args)
        self.size = camera.getSensorResolution()
        number = 5
        self.received = 0
        camera.acquireFlow(self.receive_image, self.size, 0.01, num=number)
        camera.waitAcquireFlow()
        self.assertEqual(self.received, number)

    def receive_image(self, camera, image, metadata):
        """
        callback for acquireFlow of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size)
        self.assertIn("Exposure time", metadata)
        self.received += 1

# You have to pick only one of them to get it not crash
class TestAndorCam3(unittest.TestCase, VirtualTestAndorCam):
    """
    Test directly the AndorCam3 class.
    """
    camera_type = andorcam3.AndorCam3
    camera_args = (0,) # device
        
class TestAndorCam2(unittest.TestCase, VirtualTestAndorCam):
    """
    Test directly the AndorCam2 class.
    """
    camera_type = andorcam2.AndorCam2
    camera_args = (0,) # device
    
    # The SimCam of SDKv3 doesn't support binning, so let's just try on v2
    def test_binning(self):
        camera = self.camera_type(*self.camera_args)
        self.size = camera.getSensorResolution()
        exposure = 0.1
        start = time.time()
        im, metadata = camera.acquire((self.size[0]/2, self.size[1]/2), exposure, 2)
        duration = time.time() - start
    
        self.assertEqual(im.shape, (self.size[0]/2, self.size[1]/2))
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        self.assertIn("Exposure time", metadata)
     
if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: