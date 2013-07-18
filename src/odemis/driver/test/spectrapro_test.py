# -*- coding: utf-8 -*-
'''
Created on 7 Dec 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from concurrent import futures
from odemis.driver import spectrapro
from unittest.case import skip, skipIf
import logging
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttySP" #"/dev/ttyUSB0"

CLASS = spectrapro.FakeSpectraPro # use FakeSpectraPro if no hardware present
KWARGS = {"name": "test", "role": "spectrograph", "port": PORT}

#@unittest.skip("faster")
class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    @skipIf(CLASS == spectrapro.FakeSpectraPro, "Scanning cannot work without real hardware")
    def test_scan(self):
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

    def test_fake(self):
        """
        Just makes sure we don't (completely) break FakeSpectraPro after an update
        """
        sp = spectrapro.FakeSpectraPro(**KWARGS)

        self.assertGreater(len(sp.grating.choices), 0)
        sp.moveAbs({"wavelength":300e-9})

        self.assertTrue(sp.selfTest(), "self test failed.")
        sp.terminate()

class TestSP(unittest.TestCase):
    """
    Tests which need a component ready
    """

    def setUp(self):
        self.sp = CLASS(**KWARGS)
        self.orig_pos = dict(self.sp.position.value)

    def tearDown(self):
        # move back to the original position
        f = self.sp.moveAbs(self.orig_pos)
        f.result()

    def test_moverel(self):
        move = {'wavelength':1e-9} # +1nm => should be fast
        f = self.sp.moveRel(move)
        f.result() # wait for the move to finish
        self.assertGreater(self.sp.position.value["wavelength"], self.orig_pos["wavelength"])

    def test_moveabs(self):
        pos = dict(self.sp.position.value)
        pos["wavelength"] += 1e-9  # 1nm => should be fast
        f = self.sp.moveAbs(pos)
        f.result() # wait for the move to finish
        self.assertGreater(self.sp.position.value["wavelength"], self.orig_pos["wavelength"])

    def test_fail_move(self):
        """
        Check that you cannot move more than allowed
        """
        # wrong axis
        with self.assertRaises(LookupError):
            pos = {"boo": 0}
            f = self.sp.moveAbs(pos)
            f.result()

        # absolute (easy)
        with self.assertRaises(ValueError):
            pos = {"wavelength": self.sp.ranges["wavelength"][1] + 1e-9}
            f = self.sp.moveAbs(pos)
            f.result()

        # big relative (easy)
        with self.assertRaises(ValueError):
            pos = {"wavelength":-(self.sp.ranges["wavelength"][1] + 1e-9)}
            f = self.sp.moveRel(pos)
            f.result() # wait for the move to finish

        # small relative (harder)
        # move very close from the edge
        pos = {"wavelength": self.sp.ranges["wavelength"][1] - 1e-9}
        f = self.sp.moveAbs(pos) # don't even wait for it to be done
        with self.assertRaises(ValueError):
            pos = {"wavelength": 5e-9} # a bit after the edge
            f = self.sp.moveRel(pos)
            f.result() # will fail here normally


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

        # two big separate positions that should be always acceptable
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}
        f = self.sp.moveAbs(pos_1)
        f.result()
        start = time.time()
        f = self.sp.moveAbs(pos_2)
        dur_async = time.time() - start
        f.result()
        self.assertTrue(f.done())

        start = time.time()
        f = self.sp.moveAbs(pos_1)
        f.result()
        dur_sync = time.time() - start
        self.assertTrue(f.done())

        self.assertGreater(dur_sync, max(0, dur_async - delta), "Sync should take more time than async.")

        # test timeout
        f = self.sp.moveRel(pos_2)
        # timeout = 0.001s should be too short for such a long move
        self.assertRaises(futures.TimeoutError, f.result, timeout=0.001)

    def test_stop(self):
        self.sp.stop()

        # two big separate positions that should be always acceptable
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}
        f = self.sp.moveAbs(pos_1)
        f.result()
        f = self.sp.moveAbs(pos_2)
        self.sp.stop()
        self.assertTrue(f.done() or f.cancelled()) # the current task cannot be cancelled on this hardware

    def test_queue(self):
        """
        Ask for several long moves in a row, and checks that nothing breaks
        """
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}

        # mesure how long it takes to do one move
        f = self.sp.moveAbs(pos_1)
        f.result()
        start = time.time()
        f = self.sp.moveAbs(pos_2)
        dur = time.time() - start

        expected_time = (4 * dur) * 0.9 # a bit less (90%) to take care of randomness
        start = time.time()
        f0 = self.sp.moveAbs(pos_1)
        f1 = self.sp.moveAbs(pos_2)
        f2 = self.sp.moveAbs(pos_1)
        f3 = self.sp.moveAbs(pos_2)

        # intentionally skip some sync (it _should_ not matter)
#        f0.result()
        f1.result()
#        f2.result()
        f3.result()

        dur = time.time() - start
        self.assertGreaterEqual(dur, expected_time)

    def test_cancel(self):
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}

        f = self.sp.moveAbs(pos_1)
        # cancel during action is not supported so don't try
        f.result()
        self.assertTrue(f.done())

        # test cancel in queue
        f1 = self.sp.moveAbs(pos_2)
        f2 = self.sp.moveAbs(pos_1)
        f2.cancel()
        self.assertFalse(f1.done())
        time.sleep(0.02) # make sure the command is started
        self.assertTrue(f1.running())
        self.assertTrue(f2.cancelled())
        self.assertTrue(f2.done())

        # test cancel after already cancelled
        f2.cancel()
        self.assertTrue(f2.cancelled())
        self.assertTrue(f2.done())

        f1.result()

if __name__ == '__main__':
    unittest.main()
