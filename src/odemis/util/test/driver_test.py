# -*- coding: utf-8 -*-
'''
Created on 26 Apr 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

import logging
from odemis import model
from odemis.util import driver
from odemis.util.driver import getSerialDriver, reproduceTypedValue, \
    speedUpPyroConnect
import os
import subprocess
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(__file__) + "/../../../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "secom-sim.odm.yaml"

class TestDriver(unittest.TestCase):
    """
    Test the different functions of driver
    """
    def test_getSerialDriver(self):
        # very simple to fit any platform => just check it doesn't raise exception
        
        name = getSerialDriver("booo")
        self.assertEqual("Unknown", name)
    
    def test_reproduceTypedValue_good(self):
        """
        check various inputs and compare to expected output
        for values that should work
        """
        lva = model.ListVA([12, -3])
        # example value / input str / expected output
        tc = [(3, "-1561", -1561),
              (-9.3, "0.123", 0.123),
              (False, "true", True),
              ({"a": 12.5, "b": 3.}, "c:6,d:1.3", {"c": 6., "d":1.3}),
              ((-5, 0, 6), " 9, -8", (9, -8)), # we don't force to be the same size
              ((1.2, 0.0), "0, -8, -15e-3, 6.", (0.0, -8.0, -15e-3, 6.0)),
              ([1.2, 0.0], "0.1", [0.1]),
              (("cou", "bafd"), "aa,bb", ("aa", "bb")),
              # more complicated but nice to support for the user
              ((1200, 256), "256 x 256 px", (256, 256)),
              ((1.2, 256), " 21 x 0.2 m", (21, 0.2)),
              ([-5, 0, 6], "9,, -8", [9, -8]),
              ((1.2, 0.0), "", tuple()),
              (lva.value, "-1, 63, 12", [-1, 63, 12]), #NotifyingList becomes a list
              ((-5, 0, 6), "9.3, -8", (9, 3, -8)), # maybe this shouldn't work?
              # Note: we don't support SI prefixes
              (("cou",), "aa, c a", ("aa", " c a")), # TODO: need to see if spaces should be kept or trimmed
              ]

        for ex_val, str_val, expo in tc:
            out = reproduceTypedValue(ex_val, str_val)
            self.assertEqual(out, expo,
                 "Testing with %s / '%s' -> %s" % (ex_val, str_val, out))

    def test_reproduceTypedValue_bad(self):
        """
        check various inputs and compare to expected output
        for values that should raise an exception
        """
        # example value / input str
        tc = [(3, "-"),
              (-9, "0.123"),
              (False, "56"),
              ({"a": 12.5, "b": 3.}, "6,1.3"),
              (9.3, "0, 123"),
              ]

        for ex_val, str_val in tc:
            with self.assertRaises((ValueError, TypeError)):
                out = reproduceTypedValue(ex_val, str_val)

    def test_speedUpPyroConnect(self):
        need_stop = False
        if driver.get_backend_status() != driver.BACKEND_RUNNING:
            need_stop = True
            cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_CONFIG]
            ret = subprocess.call(cmd)
            if ret != 0:
                logging.error("Failed starting backend with '%s'", cmd)
            time.sleep(1) # time to start
        else:
            model._components._microscope = None # force reset of the microscope for next connection

        speedUpPyroConnect(model.getMicroscope())

        if need_stop:
            cmd = ODEMISD_CMD + ["--kill"]
            subprocess.call(cmd)

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
