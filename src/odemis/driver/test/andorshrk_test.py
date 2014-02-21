# -*- coding: utf-8 -*-
'''
Created on 21 Feb 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division

import logging
from odemis.driver import andorcam2
import time
import unittest
from unittest.case import skip, skipIf


logging.getLogger().setLevel(logging.DEBUG)


#CLASS_SPG = andorshrk.Shamrock
KWARGS_SPG = dict(name="test", role="spectrograph", device=0)
CLASS_CAM = andorcam2.AndorSpec
KWARGS_CAM = dict(name="spectrometer", role="ccd", device=0, transpose=[-1, 2],
                  children={"shamrock": KWARGS_SPG})


#@skip("only simulated")
class TestCompositedSpectrometer(unittest.TestCase):
    """
    Test the CompositedSpectrometer class
    """

    @classmethod
    def setUpClass(cls):
        cls.spectrometer = CLASS_CAM(**KWARGS_CAM)
        for c in cls.spectrometer.children:
            if c.role == "spectrograph":
                cls.spectrograph = c
                break
        else:
            cls.fail("Couldn't find spectrograph child")

        #save position
        cls._orig_pos = cls.spectrograph.position.value

    @classmethod
    def tearDownClass(cls):
        # restore position
        f = cls.spectrograph.moveAbs(cls._orig_pos)
        f.result() # wait for the move to finish

        cls.spectrometer.terminate()
        cls.spectrograph.terminate()

    def setUp(self):
        # save basic VA
        self._orig_binning = self.spectrometer.binning.value
        self._orig_res = self.spectrometer.resolution.value

    def tearDown(self):
        # put back VAs
        self.spectrometer.binning.value = self._orig_binning
        self.spectrometer.resolution.value = self._orig_res

    @skip("simple")
    def test_simple(self):
        """
        Just ensures that the device has all the VA it should
        """
        self.assertTrue(isinstance(self.spectrometer.binning.value, tuple))
        self.assertEqual(self.spectrometer.resolution.value[1], 1)
        self.assertEqual(len(self.spectrometer.shape), 3)
        self.assertGreaterEqual(self.spectrometer.shape[0], self.spectrometer.shape[1])
        self.assertGreater(self.spectrometer.exposureTime.value, 0)

    @skip("simple")
    def test_moverel(self):
        orig_wl = self.spectrograph.position.value["wavelength"]
        move = {'wavelength': 1e-9} # +1nm => should be fast
        f = self.spectrograph.moveRel(move)
        f.result() # wait for the move to finish
        self.assertGreater(self.spectrograph.position.value["wavelength"], orig_wl)

    def test_moveabs(self):
        orig_wl = self.spectrograph.position.value["wavelength"]
        new_wl = orig_wl + 1e-9  # 1nm => should be fast
        f = self.spectrograph.moveAbs({'wavelength': new_wl})
        f.result() # wait for the move to finish
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)

        new_wl += 100e-9  # 100nm
        f = self.spectrograph.moveAbs({'wavelength': new_wl})
        f.result() # wait for the move to finish
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)


    # TODO: test wavelength + grating

    @skip("simple")
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
            pos = {"wavelength": self.sp.axes["wavelength"].range[1] + 1e-9}
            f = self.sp.moveAbs(pos)
            f.result()

        # big relative (easy)
        with self.assertRaises(ValueError):
            pos = {"wavelength":-(self.sp.axes["wavelength"].range[1] + 1e-9)}
            f = self.sp.moveRel(pos)
            f.result() # wait for the move to finish

        # small relative (harder)
        # move very close from the edge
        pos = {"wavelength": self.sp.axes["wavelength"].range[1] - 1e-9}
        f = self.sp.moveAbs(pos) # don't even wait for it to be done
        with self.assertRaises(ValueError):
            pos = {"wavelength": 5e-9} # a bit after the edge
            f = self.sp.moveRel(pos)
            f.result() # will fail here normally


    def test_grating(self):
        cg = self.spectrograph.position.value["grating"]
        choices = self.spectrograph.axes["grating"].choices
        self.assertGreater(len(choices), 0, "should have at least one grating")
        if len(choices) == 1:
            self.skipTest("only one grating choice, cannot test changing it")

        # just find one grating different from the current one
        for g in choices:
            if g != cg:
                newg = g
                break

        # if not exception, it's already pretty good
        f = self.spectrograph.moveAbs({"grating": newg})
        f.result()
        self.assertEqual(self.spectrograph.position.value["grating"], newg)

    @skip("simple")
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

    @skip("simple")
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

    @skip("simple")
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

    @skip("simple")
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


    @skip("simple")
    def test_acquisition(self):
        exp = 0.1 #s
        self.spectrometer.exposureTime.value = exp

        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)

        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)


#class TestStatic(unittest.TestCase):
#    """
#    Tests which don't need a component ready
#    """
#    @skipIf(CLASS == spectrapro.FakeSpectraPro, "Scanning cannot work without real hardware")
#    def test_scan(self):
#        devices = CLASS.scan()
#        self.assertGreater(len(devices), 0)
#
#        for name, kwargs in devices:
#            print "opening ", name
#            sem = CLASS(name, "spec", **kwargs)
#            self.assertTrue(sem.selfTest(), "self test failed.")
#
#    def test_creation(self):
#        """
#        Doesn't even try to acquire an image, just create and delete components
#        """
#        sp = CLASS(**KWARGS)
#
#        self.assertGreater(len(sp.axes["grating"].choices), 0)
#
#        self.assertTrue(sp.selfTest(), "self test failed.")
#        sp.terminate()
#
#    def test_fake(self):
#        """
#        Just makes sure we don't (completely) break FakeSpectraPro after an update
#        """
#        sp = spectrapro.FakeSpectraPro(**KWARGS)
#
#        self.assertGreater(len(sp.axes["grating"].choices), 0)
#        sp.moveAbs({"wavelength":300e-9})
#
#        self.assertTrue(sp.selfTest(), "self test failed.")
#        sp.terminate()


if __name__ == '__main__':
    unittest.main()

from odemis.driver import andorcam2, andorshrk
import logging
logging.getLogger().setLevel(logging.DEBUG)

cam = andorcam2.AndorCam2(name="spectrometer", role="ccd", device=0)
sp = andorshrk.Shamrock(name="test", role="spectrograph", device=0, path="/usr/local/etc/andor", parent=cam)
