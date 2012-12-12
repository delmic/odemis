# -*- coding: utf-8 -*-
'''
Created on 7 Dec 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.driver import spectrapro
import logging
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttySP" #"/dev/ttyUSB0"

CLASS = spectrapro.SpectraPro # use FakeSpectraPro if not hardware present
KWARGS = {"name": "test", "role": "spectrograph", "port": PORT}

#@unittest.skip("faster") 
class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_scan(self):
        # TODO skip if Fake
        devices = CLASS.scan()
        self.assertGreater(len(devices), 0)
        
        for name, kwargs in devices:
            print "opening ", name
            sem = CLASS(name, "spec", **kwargs)
            self.assertTrue(sem.selfTest(), "self test failed.")
        
    def test_creation(self):
        """
        Doesn't even try to acquire an image, just create and delete components
        """
        sp = CLASS(**KWARGS)
        
        self.assertGreater(len(sp.grating.choices), 0)
        
        self.assertTrue(sp.selfTest(), "self test failed.")
        sp.terminate()
    
    
class TestSP(unittest.TestCase):
    """
    Tests which need a component ready
    """
    
    def setUp(self):
        self.sp = CLASS(**KWARGS)
           
    def tearUp(self):
        pass
    
    def test_moverel(self):
        move = {'wavelength':1e-9} # +1nm => should be fast
        self.sp.moveRel(move)
        time.sleep(0.1) # wait for the move to finish
    
    def test_moveabs(self):
        pos = dict(self.sp.position.value)
        orig_pos = dict(pos)
        pos["wavelength"] -= 1e-9  # -1nm => should be fast
        self.sp.moveAbs(pos)
        time.sleep(0.1) # wait for the move to finish
        self.assertLess(self.sp.position.value["wavelength"], orig_pos["wavelength"])
    
    def test_grating(self):
        cg = self.sp.grating.value
        choices = self.sp.grating.choices
        self.assertGreater(len(choices), 0, "should have at least one grating")
        if len(choices) == 1:
            self.skipTest("only one grating choice, cannot test changing it")
        
        # just find one grating different from the current one
        for g in choices:
            if g != cg:
                newg = g
                break
        
        # if not exception, it's already pretty good
        self.sp.grating.value = newg
        self.assertEqual(self.sp.grating.value, newg)
        self.sp.grating.value = cg
    
    def test_sync(self):
        # For moves big enough, sync should always take more time than async
        delta = 0.0001 # s

        orig_pos = dict(self.sp.position.value)
        # two big separate positions that should be always acceptable        
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}
        self.sp.moveAbs(pos_1)        
        move = {'x':100e-6}
        start = time.time()
        f = stage.moveRel(move)
        dur_async = time.time() - start
        f.result()
        self.assertTrue(f.done())
        
        move = {'x':-100e-6}
        start = time.time()
        f = stage.moveRel(move)
        f.result() # wait
        dur_sync = time.time() - start
        self.assertTrue(f.done())
        
        self.assertGreater(dur_sync, max(0, dur_async - delta), "Sync should take more time than async.")
        
        move = {'x':100e-6}
        f = stage.moveRel(move)
        # timeout = 0.001s should be too short for such a long move
        self.assertRaises(futures.TimeoutError, f.result, timeout=0.001)    

    def test_stop(self):
        stage = pigcs.Bus("test", "stage", PORT, CONFIG_BUS_BASIC)
        stage.stop()
        
        move = {'x':100e-6}
        f = stage.moveRel(move)
        stage.stop()
        self.assertTrue(f.cancelled())
    
    def test_queue(self):
        """
        Ask for several long moves in a row, and checks that nothing breaks
        """
        stage = pigcs.Bus("test", "stage", PORT, CONFIG_BUS_BASIC)
        move_forth = {'x':1e-3}
        move_back = {'x':-1e-3}
        stage.speed.value = {"x":1e-3} # => 1s per move
        start = time.time()
        expected_time = 4 * move_forth["x"] / stage.speed.value["x"]
        f0 = stage.moveRel(move_forth)
        f1 = stage.moveRel(move_back)
        f2 = stage.moveRel(move_forth)
        f3 = stage.moveRel(move_back)
        
        # intentionally skip some sync (it _should_ not matter)
#        f0.result()
        f1.result()
#        f2.result()
        f3.result()
        
        dur = time.time() - start
        self.assertGreaterEqual(dur, expected_time)
    
    def test_cancel(self):
        stage = pigcs.Bus("test", "stage", PORT, CONFIG_BUS_BASIC)
        move_forth = {'x':1e-3}
        move_back = {'x':-1e-3}
        stage.speed.value = {"x":1e-3} # => 1s per move
        # test cancel during action
        f = stage.moveRel(move_forth)
        time.sleep(0.01) # to make sure the action is being handled
        self.assertTrue(f.running())
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        
        # test cancel in queue
        f1 = stage.moveRel(move_forth)
        f2 = stage.moveRel(move_back)
        f2.cancel()
        self.assertFalse(f1.done())
        self.assertTrue(f2.cancelled())
        self.assertTrue(f2.done())
        
        # test cancel after already cancelled
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        
        
        
        