#!/usr/bin/env python3
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
from PIL import Image
from io import BytesIO
import logging
from odemis import model
import odemis
from odemis.cli import main
from odemis.util import testing
import os
import re
import subprocess
import sys
import time
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

ODEMISCLI_CMD = [sys.executable, "-m", "odemis.cli.main"]
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"

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
            out = BytesIO()
            sys.stdout = out
            cmdline = "cli --help"
            ret = main.main(cmdline.split())
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s' returned %s" % (cmdline, ret))
        
        output = out.getvalue()
        self.assertTrue(b"optional arguments" in output)
    
#    @skip("Simple")
    def test_error_command_line(self):
        """
        It checks handling when wrong number of argument is given
        """
        try:
            cmdline = "cli --set-attr light power"
            ret = main.main(cmdline.split())
        except SystemExit as exc: # because it's handled by argparse
            ret = exc.code
        self.assertNotEqual(ret, 0, "trying to run erroneous '%s'" % cmdline)

#    @skip("Simple")
    def test_scan(self):
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = "cli --log-level=2 --scan"
            ret = main.main(cmdline.split())
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s' returned %s" % (cmdline, ret))
        
        output = out.getvalue()
        # AndorCam3 SimCam should be there for sure (if libandor3-dev is installed)
        # self.assertTrue("andorcam3.AndorCam3" in output)

        self.assertTrue(b"andorcam2.FakeAndorCam2" in output)

    def test_error_scan(self):
        try:
            cmdline = "cli --scan bar.foo"
            ret = main.main(cmdline.split())
        except SystemExit as exc: # because it's handled by argparse
            ret = exc.code
        self.assertNotEqual(ret, 0, "Wrongly succeeded trying to run scan with unknown class: '%s'" % cmdline)

#@skip("Simple")
class TestWithBackend(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise


    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

        # reset the logging (because otherwise it accumulates)
        if logging.root:
            del logging.root.handlers[:]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def tearDown(self):
        # Delete files created by tests
        if os.path.isfile("TÈßt.tiff"):
            os.remove("TÈßt.tiff")
        model._core._microscope = None # force reset of the microscope for next connection
        time.sleep(1) # time to stop

    def test_list(self):
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = "cli --list"
            ret = main.main(cmdline.split())
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        self.assertTrue(b"Light Engine" in output)
        self.assertTrue(b"Camera" in output)

    def test_list_no_dash(self):
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out

            cmdline = "cli --log-level 1 list"
            ret = main.main(cmdline.split())
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        output = out.getvalue()
        self.assertTrue(b"Light Engine" in output)
        self.assertTrue(b"Camera" in output)

    def test_check(self):
        try:
            cmdline = "cli --check"
            ret = main.main(cmdline.split())
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "Not detecting backend running")

    def test_list_prop(self):
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = ["cli", "--list-prop", "Light Engine"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        self.assertTrue(b"role" in output)
        self.assertTrue(b"swVersion" in output)
        self.assertTrue(b"power" in output)

    def test_encoding(self):
        """Check no problem happens due to unicode encoding to ascii"""
        f = open("test.txt", "w")
        cmd = ODEMISCLI_CMD + ["--list-prop", "Light Engine"]
        ret = subprocess.check_call(cmd, stdout=f)
        self.assertEqual(ret, 0, "trying to run %s" % cmd)
        f.close()
        os.remove("test.txt")
    
    def test_set_attr(self):
        # to read attribute power (which is a list of numbers)
        regex = re.compile(b"\spower.+ value: \[(.*?)\]")
        
        # read before
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = ["cli", "--list-prop", "Light Engine"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        # Parse each individual power value from the power list
        power_val_str = [val_str for val_str in str(regex.search(output).group(1)).split(",")]
        power = [float(re.search(r"([.0-9]+)", val).group(0)) for val in power_val_str]
        self.assertTrue(any(pw >= 0 for pw in power), "Power values should be bigger than 0")
        
        # set the new value
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = ["cli", "--set-attr", "Light Engine", "power", str([0.0 for _ in power])]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        # read the new value
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = ["cli", "--list-prop", "Light Engine"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        power_val_str = [val_str for val_str in str(regex.search(output).group(1)).split(",")]
        power = [float(re.search(r"([.0-9]+)", val).group(0)) for val in power_val_str]
        self.assertTrue(all(pw == 0 for pw in power), "Power values should be 0")
        
    def test_set_attr_dict(self):
        # set a dict, which is a bit complicated structure
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = ["cli", "--set-attr", "Sample Stage", "speed", "x: 0.5, y: 0.2"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
    
    def test_move(self):
        # TODO compare position VA
        # test move and also multiple move requests
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = ["cli", "--move", "Sample Stage", "x", "5", "--move", "Sample Stage", "y", "-0.2"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

    def test_position(self):
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out

            cmdline = ["cli", "--position", "Sample Stage", "x", "50e-6"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

    def test_position_deg_fail(self):
        """converting position into degrees shouldn't be possible for axes in meters"""
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out

            cmdline = ["cli", "--position", "Sample Stage", "x", "50e-6", "--degrees"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertNotEqual(ret, 0, "trying to run '%s' should fail" % cmdline)

    def test_reference(self):
        # On this simulated hardware, no component supports referencing, so
        # just check that referencing correctly reports this
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out

            cmdline = ["cli", "--reference", "Sample Stage", "x"]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertNotEqual(ret, 0, "Referencing should have failed with '%s'" % cmdline)

    def test_stop(self):
        try:
            # change the stdout
            out = BytesIO()
            sys.stdout = out
            
            cmdline = "cli --stop"
            ret = main.main(cmdline.split())
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
    
    def test_acquire(self):
        # utf-8 bytes on Python 2, unicode on Python 3
        picture_name = "TÈßt.tiff"
        size = (1024, 1024)
        
        # change resolution
        try:
            # "Andor SimCam" contains a space, so cut the line ourselves
            cmdline = ["cli", "--set-attr", "Camera", "resolution", "%d,%d" % size]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        # acquire (simulated) image
        try:
            # "Andor SimCam" contains a space, so cut the line ourselves
            cmdline = ["cli", "--acquire", "Camera", "--output=%s" % picture_name]
            ret = main.main(cmdline)
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        st = os.stat(picture_name) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(picture_name)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, size)
    
if __name__ == "__main__":
    unittest.main()
