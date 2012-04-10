#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 10 apr 2012

@author: Éric Piel
Testing class for main.py of odemisd.

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from odemisd import main
import logging
import os
import unittest


SIM_CONFIG = "optical-sim.odm.yaml"

class TestCommandLine(unittest.TestCase):
    """
    This contains test cases for the command-line level of odemisd.
    """
    
    def setUp(self):
        # reset the logging (because otherwise it accumulates)
        if logging.root:
            del logging.root.handlers[:]
    
    def test_simple(self):
        cmdline = "odemisd --validate %s" % SIM_CONFIG
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
    def test_config_error(self):
        # each of this file has one or more error
        configs = ["syntax-error-1.odm.yaml",
                   "syntax-error-2.odm.yaml",
                   # Skipped: PyYaml is not able to detect this error :  http://pyyaml.org/ticket/128
                   #"syntax-error-3.odm.yaml",
                   "semantic-error-1.odm.yaml",
                   ]

        for config in configs:
            self.setUp()
            cmdline = "odemisd --validate %s" % config
            ret = main.main(cmdline.split())
            self.assertNotEqual(ret, 0, "no error detected in erroneous config "
                                "file '%s'" % config)
        
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
        st = os.stat("test.log") # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        os.remove("test.log")
        
    def test_help(self):
        """
        It checks handling help option
        """
        try:
            cmdline = "odemisd --help"
            ret = main.main(cmdline.split())
        except SystemExit, exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        
if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: