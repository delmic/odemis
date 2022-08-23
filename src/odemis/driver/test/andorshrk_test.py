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

from concurrent import futures
import logging
from odemis import model
from odemis.driver import andorshrk, andorcam2, pmtctrl
import os
import threading
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

KWARGS_SPG = dict(name="sr303", role="spectrograph", device=0)
KWARGS_SPG_SIM = dict(name="sr303", role="spectrograph", device="fake")
CLASS_CAM = andorcam2.AndorCam2
KWARGS_CAM = dict(name="idus", role="ccd", device=0, transpose=[-1, 2])
KWARGS_CAM_SIM = dict(name="idus", role="ccd", device="fake", transpose=[-1, 2])

CLASS = andorshrk.AndorSpec
KWARGS_SIM = dict(name="spectrometer", role="spectrometer",
                  children={"shamrock": KWARGS_SPG_SIM, "andorcam2": KWARGS_CAM_SIM})
KWARGS = dict(name="spectrometer", role="spectrometer",
              children={"shamrock": KWARGS_SPG, "andorcam2": KWARGS_CAM})

# For testing the Shamrock with direct connection to the PC
CLASS_SHRK = andorshrk.Shamrock
KWARGS_SHRK = dict(name="sr193", role="spectrograph", device=0)
KWARGS_SHRK_SIM = dict(name="sr193", role="spectrograph", device="fake",
                       slits={1: ["slit-in", "force_max"], 3: "slit-monochromator"},
                       bands={1: (230e-9, 500e-9), 3: (600e-9, 1253e-9), 5: "pass-through"},
                       drives_shutter=[1.57],
                       accessory="slitleds")

# Control unit used for PMT testing
KWARGS_PMT = dict(name="test", role="pmt", port="/dev/fake")
CLASS_PMT = pmtctrl.PMTControl

if TEST_NOHW:
    KWARGS = KWARGS_SIM
    KWARGS_CAM = KWARGS_CAM_SIM
    KWARGS_SHRK = KWARGS_SHRK_SIM

#@skip("simple")
class TestShamrockStatic(unittest.TestCase):
    def test_fake_independent(self):
        """
        Just makes sure we don't (completely) break Shamrock after an update
        """
        sp = CLASS_SHRK(**KWARGS_SHRK_SIM)

        self.assertGreater(len(sp.axes["grating"].choices), 0)
        sp.moveAbs({"wavelength": 300e-9})

        self.assertTrue(sp.selfTest(), "self test failed.")
        sp.terminate()

    def test_fake_with_ccd(self):
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
        sp.moveAbs({"wavelength": 300e-9})

        self.assertTrue(sp.selfTest(), "self test failed.")
        dev.terminate()


class SpectrographTestBaseClass:
    """
    Abstract class for testing the spectrograph.
    Subclass needs to inherit from unittest.TestCase too
      and to provide .spectrograph and .ccd.
    """

    def _move_to_non_mirror_grating(self):
        sp = self.spectrograph

        choices = sp.axes["grating"].choices
        if len(choices) <= 1:
            logging.debug("No grating choice, will not try to change it")
            return

        for g, desc in choices.items():
            if desc != "mirror":
                non_mirror_g = g
                sp.moveAbsSync({"grating": non_mirror_g})
                break

    def test_simple(self):
        """
        Just ensures that the device has all the VA it should
        """
        self.assertIn("wavelength", self.spectrograph.axes)

#    @skip("simple")
    def test_moverel(self):
        self._move_to_non_mirror_grating()
        orig_wl = self.spectrograph.position.value["wavelength"]
        move = {'wavelength': 1e-9}  # +1nm => should be fast
        f = self.spectrograph.moveRel(move)
        f.result()  # wait for the move to finish
        self.assertGreater(self.spectrograph.position.value["wavelength"], orig_wl)

#    @skip("simple")
    def test_moveabs(self):
        self._move_to_non_mirror_grating()
        orig_wl = self.spectrograph.position.value["wavelength"]
        new_wl = orig_wl + 1e-9  # 1nm => should be fast
        f = self.spectrograph.moveAbs({'wavelength': new_wl})
        f.result()  # wait for the move to finish
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)

        new_wl += 100e-9  # 100nm
        if new_wl > self.spectrograph.axes["wavelength"].range[1]:
            new_wl = orig_wl - 100e-9  # -100nm

        f = self.spectrograph.moveAbs({'wavelength': new_wl})
        f.result()  # wait for the move to finish
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)

#    @skip("simple")
    def test_fail_move(self):
        """
        Check that you cannot move more than allowed
        """
        sp = self.spectrograph
        self._move_to_non_mirror_grating()

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
        self._move_to_non_mirror_grating()
        cw = self.spectrograph.position.value["wavelength"]
        cg = self.spectrograph.position.value["grating"]
        logging.debug("cw = %s", cw)
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
        if new_wl > self.spectrograph.axes["wavelength"].range[1]:
            new_wl = cw - 10e-9  # -10nm
        f = self.spectrograph.moveAbs({"grating": cg, "wavelength": new_wl})
        f.result()
        self.assertEqual(self.spectrograph.position.value["grating"], cg)
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], new_wl)

#    @skip("simple")
    def test_sync(self):
        sp = self.spectrograph
        self._move_to_non_mirror_grating()

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
        self._move_to_non_mirror_grating()

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
        self._move_to_non_mirror_grating()

        pos_1 = {'wavelength': 300e-9}
        pos_2 = {'wavelength': 500e-9}

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
        self.assertTrue(f0.done())
#        f2.result()
        f3.result()
        self.assertTrue(f2.done())

        dur = time.time() - start
        self.assertGreaterEqual(dur, expected_time)

#    @skip("simple")
    def test_cancel(self):
        sp = self.spectrograph
        self._move_to_non_mirror_grating()

        pos_1 = {'wavelength': 300e-9}
        pos_2 = {'wavelength': 500e-9}

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

    def testFilterWheel(self):
        sp = self.spectrograph
        if "band" not in sp.axes:
            self.skipTest("No band axis, cannot test changing it")

        cur_pos = sp.position.value["band"]

        # don't change position
        f = sp.moveAbs({"band": cur_pos})
        f.result()

        self.assertEqual(sp.position.value["band"], cur_pos)

        # find a different position
        bands = sp.axes["band"]
        for p in bands.choices:
            if p != cur_pos:
                new_pos = p
                break
        else:
            self.fail("Failed to find a position different from %d" % cur_pos)

        f = sp.moveAbs({"band": new_pos})
        f.result()
        self.assertEqual(sp.position.value["band"], new_pos)

    def testFocus(self):
        sp = self.spectrograph
        if "focus" not in sp.axes:
            self.skipTest("No focus axis, cannot test changing it")

        orig_focus = sp.position.value["focus"]
        # check relative moves
        f1 = sp.moveRel({"focus": 10e-6})
        f2 = sp.moveRel({"focus": -5e-6})
        f2.result()
        self.assertTrue(f1.done())
        f1.result()
        self.assertAlmostEqual(sp.position.value["focus"], orig_focus + 5e-6)

        # check abs moves
        sp.moveAbs({"focus": orig_focus}).result()

    def test_calib(self):
        sp = self.spectrograph
        self._move_to_non_mirror_grating()

        # Try with a normal cw.
        # Most gratings (but mirrors) works well with 600 nm
        sp.moveAbs({"wavelength": 600e-9}).result()
        wl = sp.position.value['wavelength']
        npixels = 1320
        pxs = 6.5e-6 # m
        lt = sp.getPixelToWavelength(npixels, pxs)
        self.assertEqual(len(lt), npixels)
        self.assertTrue(lt[0] < wl < lt[-1])

        rng = sp.getOpeningToWavelength(10e-6)
        self.assertEqual(len(rng), 2)
        self.assertTrue(rng[0] < wl < rng[1])
        rng = sp.getOpeningToWavelength(10e-3)
        self.assertEqual(len(rng), 2)
        self.assertTrue(rng[0] < wl < rng[1])

        # Check it doesn't go too crazy at zero order
        sp.moveAbs({"wavelength": 0}).result()
        wl = sp.position.value['wavelength']
        self.assertLessEqual(wl, 10e-9)
        lt = sp.getPixelToWavelength(npixels, pxs)
        for w in lt:
            self.assertTrue(0 <= w <= 20e-9)

    def test_calib_async(self):
        """
        Check calling getPixelToWavelength() while the grating changes.
        This can happen surprisingly often as 1) changing grating is really long,
        and 2) typically the CCD settings are updated while the grating moves.
        """
        sp = self.spectrograph
        npixels = 1320
        pxs = 6.5e-6  # m

        self._px2wl = None
        def run_px2wl(wait_time):
            time.sleep(wait_time)
            logging.debug("Requesting px 2 wl")
            lt = sp.getPixelToWavelength(npixels, pxs)
            logging.debug("Got px 2 wl: %s", lt)
            self._px2wl = lt

        # Try to move to a real grating, with actual cw, so that it's more
        # likely that px2wl needs computation
        self._move_to_non_mirror_grating()
        sp.moveAbsSync({"wavelength": 300e-9})

        # just find one grating different from the current one
        cg = sp.position.value["grating"]
        choices = sp.axes["grating"].choices
        for g in choices:
            if g != cg:
                newg = g
                break

        # move grating
        f = sp.moveAbs({"grating": newg})
        t = threading.Thread(target=run_px2wl, args=(1,))
        t.start()
        f.result()

        # px2wl should be a list representing the wavelength for each pixel.
        # Can't really check more about the px2wl, as it might be empty because
        # the grating is a mirror.
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertIsInstance(self._px2wl, list)


class TestShamrock(SpectrographTestBaseClass, unittest.TestCase):
    """
    Test the Shamrock alone
    """

    @classmethod
    def setUpClass(cls):
        cls.spectrograph = CLASS_SHRK(**KWARGS_SHRK)

        # save position
        cls._orig_pos = cls.spectrograph.position.value

    @classmethod
    def tearDownClass(cls):
        # restore position
        f = cls.spectrograph.moveAbs(cls._orig_pos)
        f.result()  # wait for the move to finish

        cls.spectrograph.terminate()

    def test_multi_focus(self):
        """
        Test specific bug of the SR193 which causes it to improperly put the
        focus position back (based on grating + detector) in some cases.
        """
        sp = self.spectrograph
        sp.moveAbsSync({"wavelength": 0})

        focus_rng = sp.axes["focus"].range
        # Pretty much any step value within limits would work, but it's simpler
        # to read the log if they fit directly the actual steps
        # focus_step = (focus_rng[1] - focus_rng[0]) / 6
        fstep = sp._focus_step_size

        outputs = sp.axes["flip-out"].choices

        # Typically, it works fine if focus set in this order:
        # 1. detector 1 + grating 1
        # 2. detector 1 + grating 2
        # 3. detector 2 + grating 1
        sp.moveAbsSync({"flip-out": min(outputs), "grating": 1})
        logging.debug("d1g1, init focus @ %g", sp.position.value["focus"])
        sp.moveAbsSync({"focus": focus_rng[0] + 10 * fstep})
        f11 = sp.position.value["focus"]  # focus position rounded
        logging.debug("d1g1, focus @ %g", f11)

        sp.moveAbsSync({"flip-out": max(outputs), "grating": 1})
        logging.debug("d2g1, init focus @ %g", sp.position.value["focus"])
        sp.moveAbsSync({"focus": focus_rng[0] + 110 * fstep})
        f21 = sp.position.value["focus"]
        logging.debug("d2g1, focus @ %g", f21)

        sp.moveAbsSync({"flip-out": min(outputs), "grating": 2})
        logging.debug("d1g2, init focus @ %g", sp.position.value["focus"])
        sp.moveAbsSync({"focus": focus_rng[0] + 20 * fstep})
        f12 = sp.position.value["focus"]
        logging.debug("d1g2, focus @ %g", f12)

        logging.debug("Completed first focus movement")

        # Go back and check...
        sp.moveAbsSync({"flip-out": min(outputs), "grating": 1})
        self.assertEqual(f11, sp.position.value["focus"])

        sp.moveAbsSync({"flip-out": max(outputs), "grating": 1})
        self.assertEqual(f21, sp.position.value["focus"])

        sp.moveAbsSync({"flip-out": min(outputs), "grating": 2})
        self.assertEqual(f12, sp.position.value["focus"])

        # Typically, it goes wrong if focus set in this order:
        # 1. detector 2 + grating 1
        # 2. detector 2 + grating 2
        # 3. detector 1 + grating 1
        sp.moveAbsSync({"flip-out": max(outputs), "grating": 1})
        logging.debug("d2g1, init focus @ %g", sp.position.value["focus"])
        sp.moveAbsSync({"focus": focus_rng[0] + 115 * fstep})
        f21 = sp.position.value["focus"]  # focus position rounded
        logging.debug("d2g1, focus @ %g", f21)

        sp.moveAbsSync({"flip-out": max(outputs), "grating": 2})
        logging.debug("d2g2, init focus @ %g", sp.position.value["focus"])
        sp.moveAbsSync({"focus": focus_rng[0] + 125 * fstep})
        f22 = sp.position.value["focus"]
        logging.debug("d2g2, focus @ %g", f22)

        sp.moveAbsSync({"flip-out": min(outputs), "grating": 1})
        logging.debug("d1g1, init focus @ %g", sp.position.value["focus"])
        sp.moveAbsSync({"focus": focus_rng[0] + 15 * fstep})
        f11 = sp.position.value["focus"]
        logging.debug("d1g1, focus @ %g", f11)

        logging.debug("Completed focus second movement")

        # Go back and check... (first read everything, for debugging purpose)
        sp.moveAbsSync({"flip-out": max(outputs), "grating": 1})
        f21_actual = sp.position.value["focus"]
        logging.debug("d2g1, actual focus: %g", f21_actual)

        sp.moveAbsSync({"flip-out": max(outputs), "grating": 2})
        f22_actual = sp.position.value["focus"]
        logging.debug("d2g2, actual focus: %g", f22_actual)

        sp.moveAbsSync({"flip-out": min(outputs), "grating": 1})
        f11_actual = sp.position.value["focus"]
        logging.debug("d1g1, actual focus: %g", f11_actual)

        sp.moveAbsSync({"flip-out": min(outputs), "grating": 2})
        f12_actual = sp.position.value["focus"]
        logging.debug("d1g2, actual focus: %g", f12_actual)

        self.assertEqual(f21, f21_actual)
        self.assertEqual(f22, f22_actual)
        self.assertEqual(f11, f11_actual)

        # ideally, f12, would be "logically" based on the other values: f11 + (f22-f21)
        # self.assertEqual(f12_actual, f11 + (f22 - f21))


class TestShamrockAndCCD(SpectrographTestBaseClass, unittest.TestCase):
    """
    Test the Shamrock + AndorSpec class
    """

    @classmethod
    def setUpClass(cls):
        cls.spectrometer = CLASS(**KWARGS)
        for c in cls.spectrometer.children.value:
            if c.role == "spectrograph":
                cls.spectrograph = c
            elif c.role == "ccd":
                cls.ccd = c

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


class TestShamrockSlit(unittest.TestCase):
    """
    Tests for the spectrograph with slit led
    Subclass needs to inherit from unittest.TestCase too
      and to provide .spectrograph and .ccd.
    """

    @classmethod
    def setUpClass(cls):
        cls.pmt = CLASS_PMT(**KWARGS_PMT)
        cls.spectrograph = CLASS_SHRK(dependencies={"led_prot0": cls.pmt},
                                      slitleds_settle_time=1,
                                      **KWARGS_SHRK_SIM)

    def test_simple(self):
        """
        No move, protection should just follow what is requested
        """
        # The protection should be on when starting
        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

        # simulate turning it on
        self.pmt.protection.value = False
        self.spectrograph.protection.value = False

        self.assertFalse(self.pmt.protection.value)
        self.assertFalse(self.spectrograph.protection.value)

        # simulate turning it off
        self.pmt.protection.value = True
        self.spectrograph.protection.value = True

        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

    def test_move_no_acq(self):
        """
        Simulate moving the slit while no acquisition (so protection stays always on)
        """
        slit_rng = self.spectrograph.axes["slit-monochromator"].range

        f = self.spectrograph.moveAbs({"slit-monochromator": slit_rng[1]})

        time.sleep(0.01)
        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

        # Still on
        f.result()
        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

        f = self.spectrograph.moveAbs({"slit-monochromator": slit_rng[0]})

        time.sleep(0.01)
        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

        # Still on
        f.result()
        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

    def test_move_acq(self):
        """
        Simulate moving the slit while acquisition (so protection on when moving)
        """
        slit_rng = self.spectrograph.axes["slit-monochromator"].range

        # simulate turning it on
        self.pmt.protection.value = False
        self.spectrograph.protection.value = False

        f = self.spectrograph.moveAbs({"slit-monochromator": slit_rng[1]})

        time.sleep(0.01)
        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

        # Still on
        f.result()
        self.assertFalse(self.pmt.protection.value)
        self.assertFalse(self.spectrograph.protection.value)

        f = self.spectrograph.moveAbs({"slit-monochromator": slit_rng[0]})

        time.sleep(0.01)
        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)

        # Still on
        f.result()
        self.assertFalse(self.pmt.protection.value)
        self.assertFalse(self.spectrograph.protection.value)

        # simulate turning it off
        self.pmt.protection.value = True
        self.spectrograph.protection.value = True

        self.assertTrue(self.pmt.protection.value)
        self.assertTrue(self.spectrograph.protection.value)


if __name__ == '__main__':
    unittest.main()

#logging.getLogger().setLevel(logging.DEBUG)
#
#from odemis.driver import andorcam2, andorshrk
#import logging
#
#cam = andorcam2.AndorCam2(name="spectrometer", role="ccd", device=0)
#sp = andorshrk.Shamrock(name="test", role="spectrograph", device=0, path="/usr/local/etc/andor", parent=cam)
