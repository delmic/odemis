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
import unittest
import os
import time
import PIL.Image as Image

import andorcam
import dacontrol

# the device to use to test (0 and 1 are normally always present) 
DEVICE=0

class TestDAControl(unittest.TestCase):
    """
    This contains test cases for the dacontrol command-line level.
    """
    picture_name = "test_andor.tiff"
    
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
        size = (2560, 2160)
        cmdline = ("dacontrol.py --device=%s --width=%d --height=%d --exp=0.01"
                   " --output=%s" % (DEVICE, size[0], size[1], self.picture_name)) 
        ret = dacontrol.main(cmdline.split())
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        
        st = os.stat(self.picture_name) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(self.picture_name)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, size)
        
    def test_exposure(self):
        """
        The command should take always longer than the exposure time.
        """
        exposure = 3 #s
        cmdline = ("dacontrol.py --device=%s --width=2560 --height=2160"
                   " --exp=%f --output=%s" % (DEVICE, exposure, self.picture_name))
        
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
        cmdline = ("dacontrol.py --list")
        ret = dacontrol.main(cmdline.split())
        
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
        cmdline = "dacontrol.py --device=%d --height=2160 --exp=0.01 --output=%s" % (DEVICE, self.picture_name) 
        self.assertRaises(SystemExit, dacontrol.main, cmdline.split())       
        
        
    def test_error_no_output(self):
        """
        It checks handling when no width argument is provided
        """
        cmdline = "dacontrol.py --device=%d --width=2560 --height=2160 --exp=0.01" % DEVICE 
        self.assertRaises(SystemExit, dacontrol.main, cmdline.split()) 

class TestAndorCam(unittest.TestCase):
    """
    Test directly the AndorCam class.
    """

    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        cameras = andorcam.AndorCam.scan()
        self.assertGreater(len(cameras), 0)

    def test_acquire(self):
        camera = andorcam.AndorCam(DEVICE)
        size = (2560, 2160)
        im, metadata = camera.acquire(size, 0.1)
        self.assertEqual(im.shape, size)
        self.assertIn("Exposure time", metadata)

if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: