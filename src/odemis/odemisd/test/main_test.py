#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 10 apr 2012

@author: Éric Piel
Testing class for main.py of odemisd.

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemisd import main
import StringIO
import logging
import os
import subprocess
import sys
import time
import unittest

ODEMISD_PATH = "../../odemisd/main.py"
SIM_CONFIG = "optical-sim.odm.yaml"

class TestCommandLine(unittest.TestCase):
    """
    This contains test cases for the command-line level of odemisd.
    """
    @classmethod
    def create_tests(cls):
        configs_pass = ["optical-sim.odm.yaml",
           "example-optical-odemisd-config.odm.yaml",
           "example-combined-actuator.odm.yaml",
           #"example-secom-odemisd-config.odm.yaml", # not all components exist yet
           ]

        i = 0
        for config in configs_pass:
            setattr(cls, "test_pass_%d" % i, cls.create_test_validate_pass(config))
            i += 1
    
        configs_error = ["syntax-error-1.odm.yaml",
                   "syntax-error-2.odm.yaml",
                   # Skipped: PyYaml is not able to detect this error :  http://pyyaml.org/ticket/128
                   #"syntax-error-3.odm.yaml",
                   "semantic-error-1.odm.yaml",
                   ]

        i = 0
        for config in configs_error:
            setattr(cls, "test_error_%d" % i, cls.create_test_validate_error(config))
            i += 1
    
    @staticmethod
    def create_test_validate_pass(filename):
        def test_validate_pass(self):
            cmdline = "odemisd --log-level=2 --validate %s" % filename
            ret = main.main(cmdline.split())
            self.assertEqual(ret, 0, "error detected in correct config "
                                "file '%s'" % filename)
        return test_validate_pass
      
    @staticmethod
    def create_test_validate_error(filename):
        def test_validate_error(self):
            cmdline = "odemisd --log-target=test.log --validate %s" % filename
            ret = main.main(cmdline.split())
            self.assertNotEqual(ret, 0, "no error detected in erroneous config "
                                "file '%s'" % filename)
            os.remove("test.log")
        return test_validate_error
          
    def setUp(self):
        # reset the logging (because otherwise it accumulates)
        if logging.root:
            del logging.root.handlers[:]
        
        # save the stdout in case it's modified
        # NOTE: it seems unittest does this already, but that's just in case
        self.saved_stdout = sys.stdout

    def tearDown(self):        
        sys.stdout = self.saved_stdout

    def test_error_command_line(self):
        """
        It checks handling when no config file is provided
        """
        try:
            cmdline = "odemisd --validate"
            ret = main.main(cmdline.split())
        except SystemExit, exc: # because it's handled by argparse
            ret = exc.code
        self.assertNotEqual(ret, 0, "trying to run erroneous '%s'" % cmdline)

    def test_log(self):
        cmdline = "odemisd --log-level=2 --log-target=test.log --validate %s" % SIM_CONFIG
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        # a log file?
        st = os.stat("test.log") # this tests also that the file is created
        self.assertGreater(st.st_size, 0)
        os.remove("test.log")
        
    def test_help(self):
        """
        It checks handling help option
        """
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out
            
            cmdline = "odemisd --help"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        output = out.getvalue()
        self.assertTrue("positional arguments" in output)
    
    def test_daemon(self):
        # First there should be not backend running
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")
        
        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmdline = ODEMISD_PATH + " --log-level=2 --log-target=testdaemon.log --daemonize %s" % SIM_CONFIG
        ret = subprocess.call(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        time.sleep(1) # give some time to start
        
        # now it should say it's running
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "command '%s' returned %d" % (cmdline, ret))
        
        # stop the backend
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
        time.sleep(1) # give some time to stop
        
        # backend should be stopped by now
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Back-end not stopped")
        
        os.remove("test.log")
        os.remove("testdaemon.log")
        
# extends the class fully at module 
TestCommandLine.create_tests()
                            
if __name__ == '__main__':
    unittest.main()


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: