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
import PIL.Image as Image
import StringIO
import andorcam
import andorcam2
import dacontrol
import os
import sys
import time
import unittest

class TestDAControl(unittest.TestCase):
    """
    This contains test cases for the dacontrol command-line level.
    """
    picture_name = "test_andor.tiff"
    # the device to use to test 
    #Andor SDK v3: 30 and 31 are normally always present
    #Andor SDK v2: 20 
    device = 30
    
    if 20 <= device and device <= 29:
        # Clara
        size = (1392, 1040)
    else:
        # Neo / Simcam
        size = (2560, 2160)
    
    def setUp(self):
        # clean up, just in case it was still there
        try:
            os.remove(self.picture_name)
        except:
            pass
        
    def tearDown(self):
        # clean up
        try:
            os.remove(self.picture_name)
        except:
            pass

    def test_simple(self):
        cmdline = ("dacontrol.py --device=%s --width=%d --height=%d --exp=0.01"
                   " --output=%s" % (self.device, self.size[0], self.size[1], self.picture_name)) 
        ret = dacontrol.main(cmdline.split())
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        
        st = os.stat(self.picture_name) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(self.picture_name)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, self.size)
        
    def test_exposure(self):
        """
        The command should take always longer than the exposure time.
        """
        exposure = 2 #s
        cmdline = ("dacontrol.py --device=%s --width=%d --height=%d"
                   " --exp=%f --output=%s" % (self.device, self.size[0], self.size[1],
                                              exposure, self.picture_name))
        
        start = time.time() 
        ret = dacontrol.main(cmdline.split())
        duration = time.time() - start
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %d." % (duration, exposure))
        
        st = os.stat(self.picture_name) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(self.picture_name)
        self.assertEqual(im.format, "TIFF")
        
    def test_list(self):
        """
        Check --list => should at least return a couple of lines
        """
        # need to observe the stdout 
        saved_stdout = sys.stdout
        try:
            out = StringIO.StringIO()
            sys.stdout = out
            cmdline = ("dacontrol.py --list")
            ret = dacontrol.main(cmdline.split())
        finally:
            sys.stdout = saved_stdout
            
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        assert len(out.getvalue().split("\n")) > 1
        
    def test_error_no_device(self):
        """
        It checks handling when no device argument is provided
        """
        cmdline = ("dacontrol.py --width=2560 --height=2160"
                   " --exp=0.01 --output=%s" % (self.picture_name)) 
        self.assertRaises(SystemExit, dacontrol.main, cmdline.split())     

    def test_error_command_line(self):
        """
        It checks handling when no width argument is provided
        """
        cmdline = "dacontrol.py --device=%d --height=2160 --exp=0.01 --output=%s" % (self.device, self.picture_name) 
        self.assertRaises(SystemExit, dacontrol.main, cmdline.split())       
        
    def test_error_no_output(self):
        """
        It checks handling when no width argument is provided
        """
        cmdline = "dacontrol.py --device=%d --width=2560 --height=2160 --exp=0.01" % self.device 
        self.assertRaises(SystemExit, dacontrol.main, cmdline.split()) 


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
    camera_type = andorcam.AndorCam3
    camera_args = (0,) # device
        
class TestAndorCam2(unittest.TestCase, VirtualTestAndorCam):
    """
    Test directly the AndorCam2 class.
    """
    camera_type = andorcam2.AndorCam2
    camera_args = (0,) # device
     
if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: