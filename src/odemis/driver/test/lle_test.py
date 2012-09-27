#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 20 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.driver import lle
import logging
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyUSB0" #"/dev/ttyLLE"


class TestFakeLLE(unittest.TestCase):
    """Ensures that FakeLLE also keeps working"""

    cls = lle.FakeLLE
    args = ("test", "light", PORT)
    
    def test_simple(self):
        dev = self.cls(*self.args)
        self.assertTrue(dev.selfTest(), "Device self-test failed.")
        
        # should start off
        self.assertEqual(dev.power.value, 0)
        
        # turn on green (1) to 50%
        dev.power.value = dev.power.range[1]
        em = dev.emissions.value
        em[1] = 0.5
        dev.emissions.value = em
        self.assertGreater(dev.emissions.value[1], 0)
              
        dev.terminate()  
    
class TestLLE(unittest.TestCase):

    cls = lle.LLE # use FakeLLE if no hardware
    args = ("test", "light", PORT)

#    @unittest.skip("don't have the hardware")
    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        devices = self.cls.scan()
        self.assertGreater(len(devices), 0)
        
        for name, kwargs in devices:
            print "opening ", name
            dev = self.cls(name, "light", **kwargs)
            self.assertTrue(dev.selfTest(), "Device self-test failed.")
            dev.terminate()

    def test_simple(self):
        dev = self.cls(*self.args)
        self.assertTrue(dev.selfTest(), "Device self-test failed.")
        
        # should start off
        self.assertEqual(dev.power.value, 0)
        
        # turn on green (1) to 50%
        dev.power.value = dev.power.range[1]
        em = dev.emissions.value
        em[1] = 0.5
        dev.emissions.value = em
        self.assertGreater(dev.emissions.value[1], 0)
              
        dev.terminate()  
    
    def test_multi(self):
        """simultaneous source activation"""
        dev = self.cls(*self.args)
        self.assertTrue(dev.selfTest(), "Device self-test failed.")
        
        # should start off
        self.assertEqual(dev.power.value, 0)
        
        # turn on 3 sources at the same time (which are possible)
        dev.power.value = dev.power.range[1]
        em = dev.emissions.value
        em[0] = 0.5
        em[2] = 0.7
        em[6] = 0.95
        dev.emissions.value = em
        self.assertEqual(dev.emissions.value, em)
        
        # turn on yellow source very strong => all the other ones should be shut
        em[4] = 1
        dev.emissions.value = em
        self.assertEqual(dev.emissions.value, [0, 0, 0, 0, 1, 0, 0])
        
        # turn on all the sources => at least one should be on
        dev.emissions.value = [1 for e in em]
        self.assertTrue(any(dev.emissions.value))
        
        dev.terminate()

    def test_cycle(self):
        """
        Test each emission source for 2 seconds at maximum intensity and then 1s
        at 30%.
        """
        dev = self.cls(*self.args)
        em = dev.emissions.value
        em = [0.0 for v in em]
        dev.power.value = dev.power.range[1]
        
        # can fully checked only by looking what the hardware is doing
        print "Starting emission source cycle..."
        for i in range(len(em)):
            print "Turning on wavelength %g" % dev.spectra.value[i][2]
            em[i] = 1
            dev.emissions.value = em
            time.sleep(1)
            self.assertEqual(dev.emissions.value, em)
            em[i] = 0.3
            dev.emissions.value = em
            time.sleep(1)
            self.assertEqual(dev.emissions.value, em)
            em[i] = 0
            dev.emissions.value = em
            self.assertEqual(dev.emissions.value, em)
            
        dev.terminate()

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()