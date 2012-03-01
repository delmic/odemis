#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 1 mar 2012

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
import sys
import time

import pi
import dacontrol

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyUSB0"
            
class TestDAControl(unittest.TestCase):
    """
    This contains test cases for the dacontrol command-line level.
    """
    
    def setUp(self):
        pass
            
    def tearDown(self):
        pass

    def test_simple(self):
        cmdline = "dacontrol.py --port=%s --stage-x=0.01 --stage-x=-0.01" % PORT
        ret = dacontrol.main(cmdline.split())
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        
    def test_sync(self):
        pass

    def test_error_command_line(self):
        """
        It checks handling when no port argument is provided
        """
        cmdline = "dacontrol.py --stage-x=0.12"
        self.assertRaises(SystemExit, dacontrol.main, (cmdline.split()))       

class TestPIRedStone(unittest.TestCase):
    """
    Test directly the PIRedStone class.
    """

    def test_speed_acquisition(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        ser = pi.PIRedStone.openSerialPort(PORT)
        bus = pi.PIRedStone(ser)
        adds = bus.scanNetwork()
        self.assertGreater(len(adds), 0)

if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: