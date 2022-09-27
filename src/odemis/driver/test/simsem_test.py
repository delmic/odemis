#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 7 Feb 2014

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import Pyro4
import copy
import logging
from odemis import model
from odemis.driver import simsem
from odemis.util import testing
import os
import pickle
import threading
import time
import unittest
from unittest.case import skip

import numpy

logging.getLogger().setLevel(logging.DEBUG)

# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed"}
CONFIG_BSD = {"name": "bsd", "role": "bsd"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam"}
CONFIG_FOCUS = {"name": "focus", "role": "ebeam-focus"}
CONFIG_SEM = {"name": "sem", "role": "sem", "image": "simsem-fake-output.h5",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER,
                           "focus": CONFIG_FOCUS}
              }


class TestSEMStatic(unittest.TestCase):
    """
    Tests which don't need a SEM component ready
    """
    def test_creation(self):
        """
        Doesn't even try to acquire an image, just create and delete components
        """
        sem = simsem.SimSEM(**CONFIG_SEM)
        self.assertEqual(len(sem.children.value), 3)

        for child in sem.children.value:
            if child.name == CONFIG_SED["name"]:
                sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                scanner = child

        self.assertEqual(len(scanner.resolution.value), 2)
        self.assertIsInstance(sed.data, model.DataFlow)

        self.assertTrue(sem.selfTest(), "SEM self test failed.")
        sem.terminate()

    def test_error(self):
        wrong_config = copy.deepcopy(CONFIG_SEM)
        wrong_config["device"] = "/dev/comdeeeee"
        self.assertRaises(Exception, simsem.SimSEM, **wrong_config)

        wrong_config = copy.deepcopy(CONFIG_SEM)
        wrong_config["children"]["scanner"]["channels"] = [1, 1]
        self.assertRaises(Exception, simsem.SimSEM, **wrong_config)

    def test_pickle(self):
        try:
            os.remove("testds")
        except OSError:
            pass
        daemon = Pyro4.Daemon(unixsocket="testds")

        sem = simsem.SimSEM(daemon=daemon, **CONFIG_SEM)

        dump = pickle.dumps(sem, pickle.HIGHEST_PROTOCOL)
#        print "dump size is", len(dump)
        sem_unpickled = pickle.loads(dump)
        self.assertIsInstance(sem_unpickled.children, model.VigilantAttributeBase)
        self.assertEqual(sem_unpickled.name, sem.name)
        sem.terminate()
        daemon.shutdown()

class TestSEM(unittest.TestCase):
    """
    Tests which can share one SEM device
    """
    @classmethod
    def setUpClass(cls):
        cls.sem = simsem.SimSEM(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.focus = child

    @classmethod
    def tearDownClass(cls):
        cls.sem.terminate()
        time.sleep(3)

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = (512, 256)
        self.scanner.blanker.value = False
        self.scanner.power.value = True
        self.sed.bpp.value = max(self.sed.bpp.choices)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        self.acq_dates = (set(), set()) # 2 sets of dates, one for each receiver
        self.acq_done = threading.Event()

    def tearDown(self):
#        print gc.get_referrers(self.camera)
#        gc.collect()
        pass

    def compute_expected_duration(self):
        dwell = self.scanner.dwellTime.value
        settle = 5.e-6
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

    def test_acquire_full(self):
        self.scanner.resolution.value = self.scanner.resolution.range[1]
        self.size = self.scanner.resolution.value
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

    def test_acquire(self):
        self.scanner.dwellTime.value = 10e-6 # s
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

    def test_blanker(self):
        self.scanner.dwellTime.value = 1e-6  # s
        expected_duration = self.compute_expected_duration()
        self.scanner.blanker.value = True

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))

        numpy.testing.assert_array_less(im, 100)

    def test_power(self):
        self.scanner.dwellTime.value = 1e-6  # s
        expected_duration = self.compute_expected_duration()
        self.scanner.power.value = False

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))

        numpy.testing.assert_array_equal(im, 0)

    def test_acquire_8bpp(self):
        self.sed.bpp.value = 8
        self.scanner.dwellTime.value = 10e-6  # s
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)
        self.assertEqual(im.metadata[model.MD_BPP], 8)

    def test_hfv(self):
        orig_pxs = self.scanner.pixelSize.value
        orig_hfv = self.scanner.horizontalFoV.value
        self.scanner.horizontalFoV.value = orig_hfv / 2

        self.assertAlmostEqual(orig_pxs[0] / 2, self.scanner.pixelSize.value[0])

    def test_small_res(self):
        for i in range(8):
            s = 1 + i * 1.1
            for j in range(5):
                r = int(2 ** j * 1.1)
                self.scanner.scale.value = (s, s)
                self.scanner.resolution.value = (r, r)

                im = self.sed.data.get()

                self.assertEqual(im.shape, (r, r),
                                 "Scale = %g, res = %s gives shape %s" % (s, (r, r), im.shape)
                                 )

    def test_roi(self):
        """
        check that .translation and .scale work
        """

        # First, test simple behaviour on the VA
        # max resolution
        max_res = self.scanner.resolution.range[1]
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = max_res
        self.scanner.translation.value = (-1, 1) # will be set back to 0,0 as it cannot move
        self.assertEqual(self.scanner.translation.value, (0, 0))

        # scale up
        self.scanner.scale.value = (16, 16)
        exp_res = (max_res[0] // 16, max_res[1] // 16)
        testing.assert_tuple_almost_equal(self.scanner.resolution.value, exp_res)
        self.scanner.translation.value = (-1, 1)
        self.assertEqual(self.scanner.translation.value, (0, 0))

        # translate
        exp_res = (max_res[0] // 32, max_res[1] // 32)
        self.scanner.resolution.value = exp_res
        self.scanner.translation.value = (-1, 1)
        testing.assert_tuple_almost_equal(self.scanner.resolution.value, exp_res)
        self.assertEqual(self.scanner.translation.value, (-1, 1))

        # change scale to some float
        self.scanner.resolution.value = (max_res[0] // 16, max_res[1] // 16)
        self.scanner.scale.value = (1.5, 2.3)
        exp_res = (max_res[0] // 1.5, max_res[1] // 2.3)
        testing.assert_tuple_almost_equal(self.scanner.resolution.value, exp_res)
        self.assertEqual(self.scanner.translation.value, (0, 0))

        self.scanner.scale.value = (1, 1)
        testing.assert_tuple_almost_equal(self.scanner.resolution.value, max_res, delta=1.1)
        self.assertEqual(self.scanner.translation.value, (0, 0))

        # Then, check metadata fits with the expectations
        center = (1e3, -2e3) #m
        # simulate the information on the position (normally from the mdupdater)
        self.scanner.updateMetadata({model.MD_POS: center})

        self.scanner.resolution.value = max_res
        self.scanner.scale.value = (16, 16)
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]

        # normal acquisition
        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])
        testing.assert_tuple_almost_equal(im.metadata[model.MD_POS], center)

        # translate a bit
        # reduce the size of the image so that we can have translation
        self.scanner.resolution.value = (max_res[0] // 32, max_res[1] // 32)
        self.scanner.translation.value = (-1.26, 10)  # px
        pxs = self.scanner.pixelSize.value
        exp_pos = (center[0] + (-1.26 * pxs[0]),
                   center[1] - (10 * pxs[1]))  # because translation Y is opposite from physical one

        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])
        testing.assert_tuple_almost_equal(im.metadata[model.MD_POS], exp_pos)

        # only one point
        self.scanner.resolution.value = (1, 1)
        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])
        testing.assert_tuple_almost_equal(im.metadata[model.MD_POS], exp_pos)

    @skip("faster")
    def test_acquire_high_osr(self):
        """
        small resolution, but large osr, to force acquisition not by whole array
        """
        self.scanner.resolution.value = (256, 200)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] * 1000
        expected_duration = self.compute_expected_duration()  # about 1 min

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[-1:-3:-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

    def test_long_dwell_time(self):
        """
        one pixel only, but long dwell time (> 4s), which means it uses
        duplication rate.
        """
        self.scanner.resolution.value = self.scanner.resolution.range[0]
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = 10 # DPR should be 3
        expected_duration = self.compute_expected_duration()  # same as dwell time

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

    def test_acquire_long_short(self):
        """
        test being able to cancel image acquisition if dwell time is too long
        """
        self.scanner.resolution.value = (256, 200)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] * 100
        expected_duration_l = self.compute_expected_duration()  # about 5 s

        self.left = 1
        start = time.time()

        # acquire one long, and change to a short time
        self.sed.data.subscribe(self.receive_image)
        # time.sleep(0.1) # make sure it has started
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] # shorten
        expected_duration_s = self.compute_expected_duration()
        # unsub/sub should always work, as long as there is only one subscriber
        self.sed.data.unsubscribe(self.receive_image)
        self.sed.data.subscribe(self.receive_image)

        self.acq_done.wait(2 + expected_duration_l * 1.1)
        duration = time.time() - start

        self.assertTrue(self.acq_done.is_set())
        self.assertGreaterEqual(duration, expected_duration_s, "Error execution took %f s, less than exposure time %f." % (duration, expected_duration_s))
        self.assertLess(duration, expected_duration_l, "Execution took %f s, as much as the long exposure time %f." % (duration, expected_duration_l))

    def test_acquire_flow(self):
        expected_duration = self.compute_expected_duration()

        number = 5
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        self.acq_done.wait(number * (2 + expected_duration * 1.1))  # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)

    def test_acquire_with_va(self):
        """
        Change some settings before and while acquiring
        """
        dwell = self.scanner.dwellTime.range[0] * 2
        self.scanner.dwellTime.value = dwell
        self.scanner.resolution.value = self.scanner.resolution.range[1] # test big image
        self.size = self.scanner.resolution.value
        expected_duration = self.compute_expected_duration()

        number = 3
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        # change the attribute
        time.sleep(expected_duration)
        dwell = self.scanner.dwellTime.range[0]
        self.scanner.dwellTime.value = dwell
        expected_duration = self.compute_expected_duration()

        self.acq_done.wait(number * (2 + expected_duration * 1.1))  # 2s per image should be more than enough in any case

        self.sed.data.unsubscribe(self.receive_image) # just in case it failed
        self.assertEqual(self.left, 0)

    def test_df_fast_sub_unsub(self):
        """
        Test the dataflow on a very fast cycle subscribing/unsubscribing
        SEMComedi had a bug causing the threads not to start again
        """
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        number = 10
        expected_duration = self.compute_expected_duration()

        self.left = 10000 # don't unsubscribe automatically

        for i in range(number):
            self.sed.data.subscribe(self.receive_image)
            time.sleep(0.001 * i)
            self.sed.data.unsubscribe(self.receive_image)

        # now this one should work
        self.sed.data.subscribe(self.receive_image)
        time.sleep(expected_duration * 2)  # make sure we received at least one image
        self.sed.data.unsubscribe(self.receive_image)

        self.assertLessEqual(self.left, 10000 - 1)

    def test_df_alternate_sub_unsub(self):
        """
        Test the dataflow on a quick cycle subscribing/unsubscribing
        Andorcam3 had a real bug causing deadlock in this scenario
        """
        self.scanner.dwellTime.value = 10e-6
        number = 5
        expected_duration = self.compute_expected_duration()

        self.left = 10000 + number # don't unsubscribe automatically

        for i in range(number):
            self.sed.data.subscribe(self.receive_image)
            time.sleep(expected_duration * 1.2) # make sure we received at least one image
            self.sed.data.unsubscribe(self.receive_image)

        # if it has acquired a least 5 pictures we are already happy
        self.assertLessEqual(self.left, 10000)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)
            self.acq_done.set()

    def test_focus(self):
        """
        Check it's possible to change the focus
        """
        pos = self.focus.position.value
        f = self.focus.moveRel({"z": 1e-3}) # 1 mm
        f.result()
        self.assertNotEqual(self.focus.position.value, pos)
        self.sed.data.get()

        f = self.focus.moveRel({"z":-10e-3}) # 10 mm
        f.result()
        self.assertNotEqual(self.focus.position.value, pos)
        self.sed.data.get()

        # restore original position
        f = self.focus.moveAbs(pos)
        f.result()
        self.assertEqual(self.focus.position.value, pos)


class TestSEMDrift(TestSEM):
    """
    Tests with the drift period (and smaller resolution)
    """

    @classmethod
    def setUpClass(cls):
        cls.sem = simsem.SimSEM(drift_period=0.1, **CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.focus = child

    def test_shift(self):
        """
        check that .shift works
        This only works on the "drifting" version because on this version the
        max resolution is limited compared to the full image, so a shift is possible.
        """
        # First, test simple behaviour on the VA
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = self.scanner.resolution.range[1]
        self.scanner.horizontalFoV.value = self.scanner.horizontalFoV.range[0]
        self.scanner.shift.value = (0, 0)
        self.assertEqual(self.scanner.shift.value, (0, 0))

        # normal acquisition
        im_no_shift = self.sed.data.get()
        self.assertEqual(im_no_shift.shape, self.scanner.resolution.value[-1::-1])

        # shift a bit
        self.scanner.shift.value = (-1.26e-6, 3e-6)  # m
        im_small_shift = self.sed.data.get()
        testing.assert_array_not_equal(im_no_shift, im_small_shift)

        # shift min/max
        self.scanner.shift.value = self.scanner.shift.range[0][1], self.scanner.shift.range[1][0]
        im_big_shift = self.sed.data.get()
        testing.assert_array_not_equal(im_no_shift, im_big_shift)


if __name__ == "__main__":
    unittest.main()
