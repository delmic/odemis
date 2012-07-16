#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 16 Jul 2012

@author: Éric Piel
Testing class for main.py of cli.

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from cli import main
import StringIO
import re
import subprocess
import sys
import time
import unittest


ODEMISD_PATH = "../../odemisd/main.py"
SIM_CONFIG = "../../odemisd/test/optical-sim.odm.yaml"
class TestWithoutBackend(unittest.TestCase):
    # all the test cases which don't need a backend running
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
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        self.assertTrue("optional arguments" in output)
        
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
    
class TestWithBackend(unittest.TestCase):
    def setUp(self):
        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmdline = ODEMISD_PATH + " --log-level=2 --log-target=testdaemon.log --daemonize %s" % SIM_CONFIG
        ret = subprocess.call(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(1) # time to start


    def tearDown(self):
        # end the backend
        cmdline = ODEMISD_PATH + " --kill"
        subprocess.call(cmdline.split())
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
        self.assertTrue("Light" in output)
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
            
            cmdline = "cli --list-prop Light"
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
            
            cmdline = "cli --list-prop Light"
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
            
            cmdline = "cli --set-attr Light power 0"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        # read the new value
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "cli --list-prop Light"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        power = float(regex.search(output).group(1))
        self.assertEqual(power, 0, "power should be 0")
        
if __name__ == "__main__":
    unittest.main()