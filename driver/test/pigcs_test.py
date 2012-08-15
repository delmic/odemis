#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 14 Aug 2012

@author: Éric Piel
Testing class for pi.py and dacontrol.py .

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from driver import pigcs
import logging
import os
import unittest

logging.getLogger().setLevel(logging.INFO)


if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyUSB0"

CONFIG_BUS_BASIC = {"x":(1, 1, False)} 
CONFIG_CTRL_BASIC = (1, {1: False})

class TestController(unittest.TestCase):
    """
    directly test the low level class
    """
    

    def test_scan(self):
       addresses = pigcs.Controller.scan(PORT)
    
    def test_move(self):
        #ser = Controller.openSerialPort("/dev/ttyUSB0")
        #ctrl = Controller(ser, 1, {1: False})
        #
        ##print ctrl.moveRel(1, 0.01)
        #ctrl._updateSpeedAccel(1)
        #print ctrl.GetErrorNum()
        ##print ctrl.isMoving(set([1]))
        #ctrl._sendOrderCommand("OSM 1 1000.0\n")
        #print ctrl.GetErrorNum()
        #print ctrl.GetStatus()
        #print ctrl.isMoving(set([1]))
        #print ctrl.GetErrorNum()
        #time.sleep(1)
        #print ctrl.isMoving(set([1]))
        pass
    
    def test_timeout(self):
        ser = pigcs.Controller.openSerialPort(PORT)
        ctrl = pigcs.Controller(ser, *CONFIG_CTRL_BASIC)
        
        self.assertIn("Physik Instrumente", ctrl.GetIdentification())
        self.assertTrue(ctrl.IsReady())
        ctrl._sendOrderCommand("\x24") # known to fail
        # the next command is going to have to use recovery from timeout
        self.assertTrue(ctrl.IsReady())
        self.assertEqual(0, ctrl.GetErrorNum())
        

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()