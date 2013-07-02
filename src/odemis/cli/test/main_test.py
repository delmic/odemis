#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 16 Jul 2012

@author: Éric Piel
Testing class for main.py of cli.

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
from odemis.cli import main
from unittest.case import skip
import Image
import StringIO
import logging
import odemis.model._components
import os
import re
import subprocess
import sys
import time
import unittest

ODEMISD_CMD = "python2 -m odemis.odemisd.main"
SIM_CONFIG = "../../odemisd/test/optical-sim.odm.yaml"
class TestWithoutBackend(unittest.TestCase):
    # all the test cases which don't need a backend running

    def setUp(self):
        # reset the logging (because otherwise it accumulates)
        if logging.root:
            del logging.root.handlers[:]
    
#    @skip("Simple")
    def test_help(self):
        """
        It checks handling help option
        """
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --help"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s' returned %d" % (cmdline, ret))
        
        output = out.getvalue()
        self.assertTrue("optional arguments" in output)
    
#    @skip("Simple")
    def test_error_command_line(self):
        """
        It checks handling when wrong number of argument is given
        """
        try:
            cmdline = "cli --set-attr Light power"
            ret = main.main(cmdline.split())
        except SystemExit, exc: # because it's handled by argparse
            ret = exc.code
        self.assertNotEqual(ret, 0, "trying to run erroneous '%s'" % cmdline)

#    @skip("Simple")
    def test_scan(self):
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --log-level=2 --scan"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s' returned %d" % (cmdline, ret))
        
        output = out.getvalue()
        # AndorCam3 SimCam should be there for sure
        self.assertTrue("andorcam3.AndorCam3" in output)
    
    def test_getFittestExporter(self):
        # filename, format
        tc = [("a/b/d.tiff", "TIFF"),
              ("a/b/d.ome.tiff", "TIFF"),
              ("a/b/d.h5", "HDF5"),
              ("a/b/d.b", "TIFF"), # fallback to tiff
              ("d.hdf5", "HDF5"),
              ]
        for input, exp_out in tc:
            exporter = main.getFittestExporter(input)
            out = exporter.FORMAT
            self.assertEqual(exp_out, out,
                 "getFittestExporter(%s) returned %s exporter" % (input, out))
    
#@skip("Simple")
class TestWithBackend(unittest.TestCase):
    def setUp(self):
        # reset the logging (because otherwise it accumulates)
        if logging.root:
            del logging.root.handlers[:]
            
        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmdline = ODEMISD_CMD + " --log-level=2 --log-target=testdaemon.log --daemonize %s" % SIM_CONFIG
        ret = subprocess.call(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(1) # time to start


    def tearDown(self):
        # end the backend
        cmdline = ODEMISD_CMD + " --kill"
        subprocess.call(cmdline.split())
        odemis.model._components._microscope = None # force reset of the microscope for next connection
        time.sleep(1) # time to stop

    def test_list(self):
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --list"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        self.assertTrue("Spectra" in output)
        self.assertTrue("Andor SimCam" in output)

    def test_check(self):
        try:
            cmdline = "cli --check"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "Not detecting backend running")

    def test_list_prop(self):
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --list-prop Spectra"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        self.assertTrue("role" in output)
        self.assertTrue("swVersion" in output)
        self.assertTrue("power" in output)
    
    def test_set_attr(self):
        # to read attribute power
        regex = re.compile("\spower\s.*value:\s*([.0-9]+)")
        
        # read before
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --list-prop Spectra"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        power = float(regex.search(output).group(1))
        self.assertGreaterEqual(power, 0, "power should be bigger than 0")   
        
        # set the new value
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --set-attr Spectra power 0"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        # read the new value
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --list-prop Spectra"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        power = float(regex.search(output).group(1))
        self.assertEqual(power, 0, "power should be 0")
        
    def test_set_attr_dict(self):
        # set a dict, which is a bit complicated structure
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --set-attr FakeRedStoneStage speed x:0.5,y:0.2"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
    
    def test_move(self):
        # TODO compare position VA
        # test move and also multiple move requests
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --move FakeRedStoneStage x 5 --move FakeRedStoneStage y -0.2"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
    
    def test_stop(self):
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --stop"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
    
    def test_acquire(self):
        picture_name = "test.tiff"
        size = (1024, 1024)
        
        # change resolution
        try:
            # "Andor SimCam" contains a space, so use \t as delimiter
            cmdline = "cli\t--set-attr\tAndor SimCam\tresolution\t%d,%d" % size
            ret = main.main(cmdline.split("\t"))
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        # acquire (simulated) image
        try:
            # "Andor SimCam" contains a space, so use \t as delimiter
            cmdline = "cli\t--acquire\tAndor SimCam\t--output=%s" % picture_name
            ret = main.main(cmdline.split("\t"))
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        st = os.stat(picture_name) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(picture_name)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, size)
    
if __name__ == "__main__":
    unittest.main()
