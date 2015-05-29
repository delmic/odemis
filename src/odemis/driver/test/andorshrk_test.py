# -*- coding: utf-8 -*-
'''
Created on 21 Feb 2014

@author: Éric Piel

Copyright © 2014-2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division

from concurrent import futures
import logging
from odemis import model
from odemis.driver import andorshrk, andorcam2
import os
import time
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

KWARGS_SPG = dict(name="sr303", role="spectrograph", device=0)
KWARGS_SPG_SIM = dict(name="sr303", role="spectrograph", device="fake")
CLASS_CAM = andorcam2.AndorCam2
KWARGS_CAM = dict(name="idus", role="ccd", device=0, transpose=[-1, 2])
KWARGS_CAM_SIM = dict(name="idus", role="ccd", device="fake", transpose=[-1, 2])

CLASS = andorshrk.AndorSpec
KWARGS_SIM = dict(name="spectrometer", role="ccd",
              children={"shamrock": KWARGS_SPG_SIM, "andorcam2": KWARGS_CAM_SIM})
KWARGS = dict(name="spectrometer", role="ccd",
              children={"shamrock": KWARGS_SPG, "andorcam2": KWARGS_CAM})

# For testing the Shamrock with direct connection to the PC
CLASS_SHRK = andorshrk.Shamrock
KWARGS_SHRK = dict(name="sr193", role="spectrograph", device=0)
KWARGS_SHRK_SIM = dict(name="sr193", role="spectrograph", device="fake")

if TEST_NOHW:
    KWARGS = KWARGS_SIM
    KWARGS_CAM = KWARGS_CAM_SIM
    KWARGS_SHRK = KWARGS_SHRK_SIM

#@skip("simple")
class TestShamrockStatic(unittest.TestCase):
    def test_fake(self):
        """
        Just makes sure we don't (completely) break Shamrock after an update
        """
        dev = CLASS(**KWARGS_SIM)
        self.assertEqual(len(dev.children.value), 2)

        for c in dev.children.value:
            if c.role == "spectrograph":
                sp = c
                break

        self.assertGreater(len(sp.axes["grating"].choices), 0)
        sp.moveAbs({"wavelength":300e-9})

        self.assertTrue(sp.selfTest(), "self test failed.")
        dev.terminate()

class TestSpectrograph(object):

    def tearDown(self):
        # restore position
        f = self.spectrograph.moveAbs(self._orig_pos)
        f.result()  # wait for the move to finish

    def test_simple(self):
        """
        Just ensures that the device has all the VA it should
        """
        self.assertIn("wavelength", self.spectrograph.axes)

#    @skip("simple")
    def test_moverel(self):
        orig_wl = self.spectrograph.position.value["wavelength"]
        move = {'wavelength': 1e-9}  # +1nm => should be fast
        f = self.spectrograph.moveRel(move)
        f.result()  # wait for the move to finish
        self.assertGreater(self.spectrograph.position.value["wavelength"], orig_wl)

#    @skip("simple")
    def test_moveabs(self):
        orig_wl = self.spectrograph.position.value["wavelength"]
        new_wl = orig_wl + 1e-9  # 1nm => should be fast
        f = self.spectrograph.moveAbs({'wavelength': new_wl})
        f.result()  # wait for the move to finish
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)

        new_wl += 100e-9  # 100nm
        f = self.spectrograph.moveAbs({'wavelength': new_wl})
        f.result()  # wait for the move to finish
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)


#    @skip("simple")
    def test_fail_move(self):
        """
        Check that you cannot move more than allowed
        """
        sp = self.spectrograph
        # wrong axis
        with self.assertRaises(ValueError):
            pos = {"boo": 0}
            f = sp.moveAbs(pos)
            f.result()

        # absolute (easy)
        with self.assertRaises(ValueError):
            pos = {"wavelength": sp.axes["wavelength"].range[1] + 1e-9}
            f = sp.moveAbs(pos)
            f.result()

        # big relative (easy)
        with self.assertRaises(ValueError):
            pos = {"wavelength":-(sp.axes["wavelength"].range[1] + 1e-9)}
            f = sp.moveRel(pos)
            f.result()  # wait for the move to finish

        # small relative (harder)
        # move very close from the edge
        pos = {"wavelength": sp.axes["wavelength"].range[1] - 1e-9}
        f = sp.moveAbs(pos)  # don't even wait for it to be done
        with self.assertRaises(ValueError):
            pos = {"wavelength": 5e-9}  # a bit after the edge
            f = sp.moveRel(pos)
            f.result()  # will fail here normally

        # wrong grating
        with self.assertRaises(ValueError):
            pos = {"grating":-1}  # normally no grating is ever named -1
            f = sp.moveAbs(pos)  # will fail here normally
            f.result()

#    @skip("simple")
    def test_grating(self):
        cw = self.spectrograph.position.value["wavelength"]
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

        # Go back to the original grating, and change wavelength, to test both
        # changes simultaneously
        new_wl = cw + 10e-9  # +10nm
        f = self.spectrograph.moveAbs({"grating": cg, "wavelength": new_wl})
        f.result()
        self.assertEqual(self.spectrograph.position.value["grating"], cg)
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)

#    @skip("simple")
    def test_sync(self):
        sp = self.spectrograph
        # For moves big enough, sync should always take more time than async
        delta = 0.0001  # s

        # two big separate positions that should be always acceptable
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}
        f = sp.moveAbs(pos_1)
        f.result()
        start = time.time()
        f = sp.moveAbs(pos_2)
        dur_async = time.time() - start
        f.result()
        self.assertTrue(f.done())

        start = time.time()
        f = sp.moveAbs(pos_1)
        f.result()
        dur_sync = time.time() - start
        self.assertTrue(f.done())

        self.assertGreater(dur_sync, max(0, dur_async - delta), "Sync should take more time than async.")

        # test timeout
        f = sp.moveRel(pos_2)
        # timeout = 0.001s should be too short for such a long move
        self.assertRaises(futures.TimeoutError, f.result, timeout=0.001)

#    @skip("simple")
    def test_stop(self):
        sp = self.spectrograph
        sp.stop()

        # two big separate positions that should be always acceptable
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}
        f = sp.moveAbs(pos_1)
        f.result()
        f = sp.moveAbs(pos_2)
        sp.stop()
        self.assertTrue(f.done() or f.cancelled())  # the current task cannot be cancelled on this hardware

#    @skip("simple")
    def test_queue(self):
        """
        Ask for several long moves in a row, and checks that nothing breaks
        """
        sp = self.spectrograph
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}

        # mesure how long it takes to do one move
        f = sp.moveAbs(pos_1)
        f.result()
        start = time.time()
        f = sp.moveAbs(pos_2)
        dur = time.time() - start

        expected_time = (4 * dur) * 0.9  # a bit less (90%) to take care of randomness
        start = time.time()
        f0 = sp.moveAbs(pos_1)
        f1 = sp.moveAbs(pos_2)
        f2 = sp.moveAbs(pos_1)
        f3 = sp.moveAbs(pos_2)

        # intentionally skip some sync (it _should_ not matter)
#        f0.result()
        f1.result()
#        f2.result()
        f3.result()

        dur = time.time() - start
        self.assertGreaterEqual(dur, expected_time)

#    @skip("simple")
    def test_cancel(self):
        sp = self.spectrograph
        pos_1 = {'wavelength':300e-9}
        pos_2 = {'wavelength':500e-9}

        f = sp.moveAbs(pos_1)
        # cancel during action is not supported so don't try
        f.result()
        self.assertTrue(f.done())

        # test cancel in queue
        f1 = sp.moveAbs(pos_2)
        f2 = sp.moveAbs(pos_1)
        f2.cancel()
        self.assertFalse(f1.done())
        time.sleep(0.02)  # make sure the command is started
        self.assertTrue(f1.running())
        self.assertTrue(f2.cancelled())
        self.assertTrue(f2.done())

        # test cancel after already cancelled
        f2.cancel()
        self.assertTrue(f2.cancelled())
        self.assertTrue(f2.done())

        f1.result()


class TestShamrock(TestSpectrograph, unittest.TestCase):
    """
    Test the Shamrock alone
    """

    @classmethod
    def setUpClass(cls):
        cls.ccd = CLASS_CAM(**KWARGS_CAM)
        cls.spectrograph = CLASS_SHRK(children={"ccd": cls.ccd}, **KWARGS_SHRK)

        # save position
        cls._orig_pos = cls.spectrograph.position.value

    @classmethod
    def tearDownClass(cls):
        # restore position
        f = cls.spectrograph.moveAbs(cls._orig_pos)
        f.result()  # wait for the move to finish

        cls.spectrograph.terminate()


class TestShamrockAndCCD(TestSpectrograph, unittest.TestCase):
    """
    Test the Shamrock + AndorSpec class
    """

    @classmethod
    def setUpClass(cls):
        cls.spectrometer = CLASS(**KWARGS)
        for c in cls.spectrometer.children.value:
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

    def setUp(self):
        # save basic VA
        self._orig_binning = self.spectrometer.binning.value
        self._orig_res = self.spectrometer.resolution.value

    def tearDown(self):
        self.spectrometer.data.unsubscribe(self.count_data)
        # put back VAs
        self.spectrometer.binning.value = self._orig_binning
        self.spectrometer.resolution.value = self._orig_res
        super(TestShamrockAndCCD, self).tearDown()

#    @skip("simple")
    def test_simple(self):
        """
        Just ensures that the device has all the VA it should
        """
        self.assertTrue(isinstance(self.spectrometer.binning.value, tuple))
        self.assertEqual(self.spectrometer.resolution.value[1], 1)
        self.assertEqual(len(self.spectrometer.shape), 3)
        self.assertGreaterEqual(self.spectrometer.shape[0], self.spectrometer.shape[1])
        self.assertGreater(self.spectrometer.exposureTime.value, 0)
        self.assertIn("wavelength", self.spectrograph.axes)

#    @skip("simple")
    def test_acquisition(self):
        exp = 0.1 #s
        self.spectrometer.exposureTime.value = exp

        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        self.assertIn(model.MD_WL_LIST, data.metadata)
        wl_list = data.metadata[model.MD_WL_LIST]
        self.assertEqual(len(wl_list), data.shape[-1])

        self.spectrometer.binning.value = (4, 1)
        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        self.assertIn(model.MD_WL_LIST, data.metadata)
        wl_list = data.metadata[model.MD_WL_LIST]
        self.assertEqual(len(wl_list), data.shape[-1])


    def test_acq_and_move(self):
        # There is a limitation in the iDus hardware which prevents from sending
        # commands to the SR303i while acquisition is on going. This test checks
        # that our workaround works.

        exp = 0.1 #s
        self.spectrometer.exposureTime.value = exp

        # start acquiring endlessly
        self.count = 0
        self.spectrometer.data.subscribe(self.count_data)
        time.sleep(0.5)

        orig_wl = self.spectrograph.position.value["wavelength"]
        new_wl = orig_wl + 100e-9  # 100nm
        f = self.spectrograph.moveAbs({'wavelength': new_wl})
        f.result() # wait for the move to finish
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)

        time.sleep(0.5)
        self.spectrometer.data.unsubscribe(self.count_data)

        self.assertGreater(self.count, 2)

    def count_data(self, df, data):
        self.count += 1

if __name__ == '__main__':
    unittest.main()

#logging.getLogger().setLevel(logging.DEBUG)
#
#from odemis.driver import andorcam2, andorshrk
#import logging
#
#cam = andorcam2.AndorCam2(name="spectrometer", role="ccd", device=0)
#sp = andorshrk.Shamrock(name="test", role="spectrograph", device=0, path="/usr/local/etc/andor", parent=cam)
