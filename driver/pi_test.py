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
import time
import math

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
    
    def test_simple(self):
        cmdline = "dacontrol.py --port=%s --stage-x=0.01 --stage-y=-0.01" % PORT
        ret = dacontrol.main(cmdline.split())
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        
    def test_sync(self):
        # For moves big enough, sync should always take more time than async
        delta = 0.0001 # s
        
        cmdline = "dacontrol.py --port=%s --stage-x=100 --stage-y=-100" % PORT
        start = time.time()
        ret = dacontrol.main(cmdline.split())
        dur_async = time.time() - start
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        
        cmdline = "dacontrol.py --port=%s --stage-x=-100 --stage-y=100 --sync" % PORT 
        start = time.time()
        ret = dacontrol.main(cmdline.split())
        dur_sync = time.time() - start
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        
        self.assertGreater(dur_sync, dur_async - delta, "Sync should take more time than async.")

    def test_stop(self):
        cmdlinestop = "dacontrol.py --port=%s --stop" % PORT
        ret = dacontrol.main(cmdlinestop.split())
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdlinestop)
        
        cmdline = "dacontrol.py --port=%s --stage-x=100 --stage-y=-100" % PORT
        ret = dacontrol.main(cmdline.split())
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdline)
        
        cmdlinestop = "dacontrol.py --port=%s --stop" % PORT
        ret = dacontrol.main(cmdlinestop.split())
        self.assertEqual(ret, 0, "Error while trying to run '%s'" % cmdlinestop)
        
    def test_error_no_port(self):
        """
        It checks handling when no port argument is provided
        """
        cmdline = "dacontrol.py --stage-x=0.12"
        self.assertRaises(SystemExit, dacontrol.main, cmdline.split())     

    def test_error_command_line(self):
        """
        It checks handling when no port argument is provided
        """
        cmdline = "dacontrol.py --port=%s --stage-w=0.12" % PORT
        self.assertRaises(SystemExit, dacontrol.main, cmdline.split())       

CONFIG_RS_SECOM_1 = {'x': (0, 1), 'y': (0, 2)}
CONFIG_RS_SECOM_2 = {'x': (1, 1), 'y': (0, 1)}
class TestPIRedStone(unittest.TestCase):
    """
    Test directly the PIRedStone class.
    """

    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        ser = pi.PIRedStone.openSerialPort(PORT)
        bus = pi.PIRedStone(ser)
        adds = bus.scanNetwork()
        self.assertGreater(len(adds), 0)

    def test_move_circle(self):
        stage = pi.StageRedStone(PORT, CONFIG_RS_SECOM_2)
        radius = 100 * 1e-6 # m
        # each step has to be big enough so that each move is above imprecision
        steps = 100
        cur_pos = (0, 0)
        move = {}
        for i in xrange(steps):
            next_pos = (radius * math.cos(2 * math.pi * float(i) / steps),
                        radius * math.sin(2 * math.pi * float(i) / steps))
            move['x'] = next_pos[0] - cur_pos[0]
            move['y'] = next_pos[1] - cur_pos[1]
            print next_pos, move
            stage.moveRel(move, sync=True)
            cur_pos = next_pos

if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: