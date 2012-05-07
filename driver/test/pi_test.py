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

from driver import pi

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyUSB0"

CONFIG_RS_SECOM_1 = {'x': (0, 0), 'y': (0, 1)}
CONFIG_RS_SECOM_2 = {'x': (1, 0), 'y': (0, 0)}
class TestPIRedStone(unittest.TestCase):
    """
    Test directly the PIRedStone class.
    """
    config = CONFIG_RS_SECOM_1
    
    def test_scan_low_level(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        adds = pi.PIRedStone.scan(PORT)
        self.assertGreater(len(adds), 0)
        
        ser = pi.PIRedStone.openSerialPort(PORT)    
        for add in adds:
            cont = pi.PIRedStone(ser, add)
            self.assertTrue(cont.selfTest(), "Controller self test failed.")
            
    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        devices = pi.StageRedStone.scan()
        self.assertGreater(len(devices), 0)
        
        for name, kwargs in devices:
            print "opening ", name
            stage = pi.StageRedStone(name, "stage", None, **kwargs)
            self.assertTrue(stage.selfTest(), "Controller self test failed.")
            
    def test_simple(self):
        stage = pi.StageRedStone("test", "stage", None, PORT, self.config)
        move = {'x':0.01e-6, 'y':0.01e-6}
        stage.moveRel(move)
        
    def test_sync(self):
        # For moves big enough, sync should always take more time than async
        delta = 0.0001 # s
        
        stage = pi.StageRedStone("test", "stage", None, PORT, self.config)
        move = {'x':100e-6, 'y':100e-6}
        
        start = time.time()
        f = stage.moveRel(move)
        dur_async = time.time() - start
        f.result() # wait
        self.assertTrue(f.done())
        
        move = {'x':-100e-6, 'y':-100e-6}
        start = time.time()
        f = stage.moveRel(move)
        f.result() # wait
        dur_sync = time.time() - start
        self.assertTrue(f.done())
        
        self.assertGreater(dur_sync, dur_async - delta, "Sync should take more time than async.")

    def test_speed(self):
        # For moves big enough, a 0.1m/s move should take approximately 100 times less time
        # than a 0.001m/s move 
        expected_ratio = 100
        delta_ratio = 2 # no unit 
        
        # fast move
        stage = pi.StageRedStone("test", "stage", None, PORT, self.config)
        stage.speed.value = {"x":0.1, "y":0.1}
        move = {'x':100e-6, 'y':100e-6}
        start = time.time()
        f = stage.moveRel(move)
        f.result()
        dur_fast = time.time() - start
        
        stage.speed.value = {"x":0.1/expected_ratio, "y":0.1/expected_ratio}
        move = {'x':-100e-6, 'y':-100e-6}
        start = time.time()
        f = stage.moveRel(move)
        f.result()
        dur_slow = time.time() - start
        
        ratio = dur_slow / dur_fast
        print "ratio of ", ratio 
        if ratio < expected_ratio / 2 or ratio > expected_ratio * 2:
            self.fail("Speed not consistent: ratio of " + str(ratio) + 
                         " instead of " + str(expected_ratio) + ".")

    def test_stop(self):
        stage = pi.StageRedStone("test", "stage", None, PORT, self.config)
        stage.stop()
        
        move = {'x':100e-6, 'y':100e-6}
        stage.moveRel(move)
        stage.stop()
        
    def test_queue(self):
        """
        Ask for several long moves in a row, and checks that nothing breaks
        """
        stage = pi.StageRedStone("test", "stage", None, PORT, self.config)
        move_forth = {'x':1e-3, 'y':1e-3}
        move_back = {'x':-1e-3, 'y':-1e-3}
        stage.speed.value = {"x":0.001, "y":0.001} # => 1s per move
        f0 = stage.moveRel(move_forth)
        f1 = stage.moveRel(move_back)
        f2 = stage.moveRel(move_forth)
        f3 = stage.moveRel(move_back)
        f0.result()
        f1.result()
        f2.result()
        f3.result()
        
    def test_cancel(self):
        stage = pi.StageRedStone("test", "stage", None, PORT, self.config)
        move = {'x':100e-6, 'y':100e-6}
        f = stage.moveRel(move)
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        
    def test_move_circle(self):
        stage = pi.StageRedStone("test", "stage", None, PORT, self.config)
        stage.speed.value = {"x":0.1, "y":0.1}
        radius = 100e-6 # m
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
            f = stage.moveRel(move)
            f.result() # wait
            cur_pos = next_pos

if __name__ == '__main__':
    unittest.main()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: