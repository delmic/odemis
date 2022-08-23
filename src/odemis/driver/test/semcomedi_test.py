#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 6 Nov 2012

Copyright © 2012-2015 Éric Piel, Delmic

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
from odemis import model
from odemis.driver import semcomedi
from odemis.util import testing
import Pyro4
import comedi
import copy
import logging
import numpy
import os
import pickle
import threading
import time
import unittest
import gc


# If you don't have a real DAQ comedi device, you can create one that can still
# pass all the tests by doing this:
# sudo modprobe comedi comedi_num_legacy_minors=4
# sudo modprobe comedi_test
# sudo chmod a+rw /dev/comedi0
# sudo comedi_config /dev/comedi0 comedi_test 1000000,1000000
#
# Be aware that comedi_test might crash the system while running those tests (much
# less likely with kernels >= 3.5).


logging.getLogger().setLevel(logging.DEBUG)
#comedi.comedi_loglevel(3)

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed", "channel":5, "limits": [-3, 3]}
CONFIG_BSD = {"name": "bsd", "role": "bsd", "channel":6, "limits": [0.2, -0.1]}
CONFIG_CNT = {"name": "cnt", "role": "cnt", "source":0}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam",
                  "limits": [[-5, 5], [3, -3]],
                  "channels": [0, 1],
                  "max_res": [4096, 3072],  # 4:3 ratio
                  "settle_time": 10e-6,
                  "hfw_nomag": 10e-3,
                  "park": [8, 8],
                  "scanning_ttl": {4: True, 2: [True, "external"], 3: [False, "blanker", True]}
                  }
CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }

CONFIG_SEM2 = {"name": "sem", "role": "sem", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED, "detector1": CONFIG_BSD, "scanner": CONFIG_SCANNER}
              }

KWARGS_SEM_CNT = {"name": "sem", "role": "sem", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED, "counter0": CONFIG_CNT, "scanner": CONFIG_SCANNER}
              }

#@unittest.skip("simple")
class TestSEMStatic(unittest.TestCase):
    """
    Tests which don't need a SEM component ready
    """
    def test_scan(self):
        devices = semcomedi.SEMComedi.scan()
        self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print("Opening device %s, %s" % (name, kwargs["device"]))
            sem = semcomedi.SEMComedi("test", "sem", **kwargs)
            self.assertTrue(sem.selfTest(), "SEM self test failed.")
            sem.terminate()

            # Needed to properly clean-up everything and not have issue when
            # starting another SEMComedi
            del sem
            gc.collect()

    def test_creation(self):
        """
        Doesn't even try to acquire an image, just create and delete components
        """
        sem = semcomedi.SEMComedi(**CONFIG_SEM)
        self.assertEqual(len(sem.children.value), 2)

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
        self.assertRaises(Exception, semcomedi.SEMComedi, **wrong_config)

        wrong_config = copy.deepcopy(CONFIG_SEM)
        wrong_config["children"]["scanner"]["channels"] = [1, 1]
        self.assertRaises(Exception, semcomedi.SEMComedi, **wrong_config)

    def test_pickle(self):
        try:
            os.remove("test")
        except OSError:
            pass
        daemon = Pyro4.Daemon(unixsocket="test")

        sem = semcomedi.SEMComedi(daemon=daemon, **CONFIG_SEM)

        dump = pickle.dumps(sem, pickle.HIGHEST_PROTOCOL)
#        print "dump size is", len(dump)
        sem_unpickled = pickle.loads(dump)
        self.assertIsInstance(sem_unpickled.children, model.VigilantAttributeBase)
        self.assertEqual(sem_unpickled.name, sem.name)
        # self.assertEqual(len(sem_unpickled.children.value), 2)
        sem.terminate()
        daemon.shutdown()

    def test_generate_scan(self):
        """
        Test the _generate_scan_array static method of the Scanner
        """
        # minY, maxY, minX, maxX
        limits = numpy.array([[30320, 35215], [40943, 24592]], dtype="uint16")
        shape = (256, 512)
        margin = 1
        scan_pos = semcomedi.Scanner._generate_scan_array(shape, limits, margin)

        # should have 2 values for each pixel
        self.assertEqual(scan_pos.shape, (shape[0], shape[1] + margin, 2))
        # should be monotone in both dimensions
        vecx = (scan_pos[0, :, 1]).astype("float64") # use float to allow negative values
        diffx = vecx[0:-2] - vecx[1:-1]
        if limits[1, 0] <= limits[1, 1]:
            comp = diffx <= 0 # must be increasing
        else:
            comp = diffx >= 0 # must be decreasing
        self.assertTrue(comp.all())

#@unittest.skip("simple")
class TestSEM(unittest.TestCase):
    """
    Tests which can share one SEM device
    """
    @classmethod
    def setUpClass(cls):
        cls.sem = semcomedi.SEMComedi(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearDownClass(cls):
        cls.sem.terminate()
        time.sleep(3)

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = (512, 256)
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
        settle = self.scanner.settleTime
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

    def test_ttl(self):
        # Just check the VA are created and support changing the values
        for v in (True, False, None):
            self.scanner.external.value = v
            self.scanner.blanker.value = v

    def test_magnification(self):
        pxs_orig = self.scanner.pixelSize.value
        mag_orig = self.scanner.magnification.value

        self.assertEqual(pxs_orig[0], pxs_orig[1])

        # Mag x 2 => pixel size / 2
        self.scanner.magnification.value *= 2
        self.assertAlmostEqual(mag_orig * 2, self.scanner.magnification.value)
        new_pxs = self.scanner.pixelSize.value
        self.assertAlmostEqual(pxs_orig[1] / 2, new_pxs[0])
        self.assertEqual(new_pxs[0], new_pxs[1])

        self.scanner.magnification.value = mag_orig

#     @unittest.skip("simple")
    def test_acquire(self):
        self.scanner.dwellTime.value = 10e-6 # s
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

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

        # shift
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
        testing.assert_tuple_almost_equal(self.scanner.resolution.value, max_res, delta=2.1)
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

        # shift a bit
        # reduce the size of the image so that we can have translation
        self.scanner.resolution.value = (max_res[0] // 32, max_res[1] // 32)
        self.scanner.translation.value = (-1.26, 10) # px
        pxs = self.scanner.pixelSize.value
        exp_pos = (center[0] + (-1.26 * pxs[0]),
                   center[1] - (10 * pxs[1])) # because translation Y is opposite from physical one

        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])
        testing.assert_tuple_almost_equal(im.metadata[model.MD_POS], exp_pos)

        # only one point
        self.scanner.resolution.value = (1,1)
        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])
        testing.assert_tuple_almost_equal(im.metadata[model.MD_POS], exp_pos)


#     @unittest.skip("simple")
    def test_osr(self):
        """
        Checks that find_best_oversampling_rate always finds something appropriate
        The period/osr should always give something close from the maximum scanning
        rate of the AI device.
        """
        # values to test
        periods = [0.8e-6,
                   1e-6,
                   3e-6,
                   1e-5,
                   7.3278e-05,
                   6.68952e-06,
                   0.000365129,
                   0.000579224,
                   23,
                   ]
        min_ai_period = self.sem._min_ai_periods[1]
        for p in periods:
            period, osr, dpr = self.sem.find_best_oversampling_rate(p)
            ai_period = (period / dpr) / osr
            self.assertLess(ai_period, min_ai_period * 5,
                            "Got osr=%d, while expected something around %s"
                            % (osr, (period / dpr) / min_ai_period))
            if p <= ((2 ** 32 - 1) / 1e9):
                self.assertEqual(dpr, 1)
            else:
                self.assertGreater(dpr, 1)

#     @unittest.skip("too long")
    def test_acquire_high_osr(self):
        """
        small resolution, but large osr, to force acquisition not by whole array
        """
        self.scanner.resolution.value = (256, 200)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] * 1000
        expected_duration = self.compute_expected_duration() # about 1 min

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
        expected_duration = self.compute_expected_duration() # same as dwell time

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

    def test_very_long_dwell_time(self):
        """
        one pixel only, but long dwell time (> 30s), which means it uses
        duplication rate and per dpr acquisition.
        """
        self.scanner.resolution.value = (2, 2)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = 33 # DPR should be 8 and each pixel acquisition > max_bufsz
        expected_duration = self.compute_expected_duration() # same as dwell time

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)


#     @unittest.skip("too long")
    def test_acquire_long_short(self):
        """
        test being able to cancel image acquisition if dwell time is too long
        """
        self.scanner.resolution.value = (256, 200)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] * 100
        expected_duration_l = self.compute_expected_duration() # about 5 s

        self.left = 1
        start = time.time()

        # acquire one long, and change to a short time
        self.sed.data.subscribe(self.receive_image)
        time.sleep(0.1) # make sure it has started
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] # shorten
        expected_duration_s = self.compute_expected_duration()
        # unsub/sub should always work, as long as there is only one subscriber
        self.sed.data.unsubscribe(self.receive_image)
        self.sed.data.subscribe(self.receive_image)

        self.acq_done.wait(2 + expected_duration_l * 1.1)
        duration = time.time() - start

        self.assertTrue(self.acq_done.is_set())
        self.assertGreaterEqual(duration, expected_duration_s, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration_s))
        self.assertLess(duration, expected_duration_l, "Execution took %f s, as much as the long exposure time %d." % (duration, expected_duration_l))

#     @unittest.skip("simple")
    def test_acquire_flow(self):
        expected_duration = self.compute_expected_duration()

        number = 5
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        self.acq_done.wait(number * (2 + expected_duration * 1.1)) # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)

#     @unittest.skip("simple")
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

        self.acq_done.wait(number * (2 + expected_duration * 1.1)) # 2s per image should be more than enough in any case

        self.sed.data.unsubscribe(self.receive_image) # just in case it failed
        self.assertEqual(self.left, 0)

#     @unittest.skip("simple")
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
        time.sleep(expected_duration * 2) # make sure we received at least one image
        self.sed.data.unsubscribe(self.receive_image)

        self.assertLessEqual(self.left, 10000 - 1)

#     @unittest.skip("simple")
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

    def test_sync_flow(self):
        """
        Acquire a dataflow with a softwareTrigger
        """
        expected_duration = self.compute_expected_duration()

        # set softwareTrigger on the first detector to be subscribed
        self.sed.data.synchronizedOn(self.sed.softwareTrigger)

        self.left = 3
        self.sed.data.subscribe(self.receive_image)

        self.sed.softwareTrigger.notify()  # start acquiring
        self.sed.softwareTrigger.notify()  # should be queued up for next acquisition

        # wait enough for the 2 acquisitions
        time.sleep(2 * (2 + expected_duration * 1.1))  # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 1)

        # remove synchronisation
        self.sed.data.synchronizedOn(None)  # => should immediately start another acquisition

        # wait for last acq
        self.acq_done.wait(2 + expected_duration * 1.1)

        self.assertEqual(self.left, 0)

#     @unittest.skip("simple")
    def test_new_position_event(self):
        """
        check the new position works at least when the frequency is not too high
        """
        self.scanner.dwellTime.value = 1e-3
        self.size = (10, 10)
        self.scanner.resolution.value = self.size
        numbert = numpy.prod(self.size)
        # pixel write/read setup is pretty expensive ~10ms
        expected_duration = self.compute_expected_duration() + numbert * 0.01

        self.left = 1 # unsubscribe just after one
        self.events = 0 # reset

        # simulate the synchronizedOn() method of a DataFlow
        self.scanner.newPosition.subscribe(self)

        self.sed.data.subscribe(self.receive_image)
        for i in range(10):
            # * 2 because it can be quite long to setup each pixel.
            time.sleep(expected_duration * 2 / 10)
            if self.left == 0:
                break # just to make it quicker if it's quicker

        self.assertEqual(self.left, 0)

        # Note: there could be slightly more events if the next acquisition starts,
        # and that's kind of ok (although it's better to be able to stop the
        # acquisition immediately after receiving the right number of images)
        self.assertEqual(self.events, numbert)

        self.scanner.newPosition.unsubscribe(self)
        self.sed.data.get()
        time.sleep(0.1)
        self.assertEqual(self.events, numbert)

    def onEvent(self):
        """
        Called by the SEM when a new position happens
        """
        self.events += 1

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


# @unittest.skip("simple")
class TestSEM2(unittest.TestCase):
    """
    Tests which can share one SEM device with 2 detectors
    """
    @classmethod
    def setUpClass(cls):
        cls.sem = semcomedi.SEMComedi(**CONFIG_SEM2)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_BSD["name"]:
                cls.bsd = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearDownClass(cls):
        cls.sem.terminate()

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.resolution.value = (256, 200)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        self.acq_dates = (set(), set()) # 2 sets of dates, one for each receiver

    def tearDown(self):
        pass

    def compute_expected_duration(self):
        dwell = self.scanner.dwellTime.value
        settle = self.scanner.settleTime
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

#    @unittest.skip("simple")
    def test_acquire_two_flows(self):
        """
        Simple acquisition with two dataflows acquiring (more or less)
        simultaneously
        """
        expected_duration = self.compute_expected_duration()
        number, number2 = 3, 5

        self.left = number
        self.sed.data.subscribe(self.receive_image)

        time.sleep(expected_duration) # make sure we'll start asynchronously
        self.left2 = number2
        self.bsd.data.subscribe(self.receive_image2)

        for i in range(number + number2):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + expected_duration * 1.1) # 2s per image should be more than enough in any case

        # check that at least some images were acquired simultaneously
        common_dates = self.acq_dates[0] & self.acq_dates[1]
        self.assertGreater(len(common_dates), 0, "No common dates between %r and %r" %
                           (self.acq_dates[0], self.acq_dates[1]))

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

    def test_acquire_two_sync_flows(self):
        """
        Acquire with two dataflows, with one synchronised, so that the scanning
        is software triggered, and each pair of acquisition correspond to the
        same scan.
        """
        expected_duration = self.compute_expected_duration()

        # set softwareTrigger on the first detector to be subscribed
        self.sed.data.synchronizedOn(self.sed.softwareTrigger)

        self.left = 10  # just for safety
        self.sed.data.subscribe(self.receive_image)

        time.sleep(expected_duration)  # make sure it would have time to start
        self.left2 = 10
        self.bsd.data.subscribe(self.receive_image2)

        for i in range(3):
            self.sed.softwareTrigger.notify()
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                self.fail("One detector already received too many acquisitions")
            time.sleep(2 + expected_duration * 1.1)  # 2s per image should be more than enough in any case

        self.bsd.data.unsubscribe(self.receive_image2)
        self.sed.data.unsubscribe(self.receive_image)  # synchronized DF last

        # remove synchronisation
        self.sed.data.synchronizedOn(None)

        self.assertEqual(len(self.acq_dates[0]), 3)
        self.assertEqual(len(self.acq_dates[1]), 3)

        # check all images were acquired simultaneously
        common_dates = self.acq_dates[0] & self.acq_dates[1]
        self.assertEqual(len(common_dates), 3, "Dates not all common between %r and %r" %
                         (self.acq_dates[0], self.acq_dates[1]))

    def test_acquire_two_sync_flows_fast(self):
        """
        Acquire with two dataflows, with one synchronised, only at the beginning.
        So that after the init, acquisition doesn't wait for the software trigger.
        """
        expected_duration = self.compute_expected_duration()

        # set softwareTrigger on the first detector to be subscribed
        self.sed.data.synchronizedOn(self.sed.softwareTrigger)

        self.left = 3
        self.sed.data.subscribe(self.receive_image)

        time.sleep(expected_duration)  # make sure it would have time to start
        self.left2 = 3
        self.bsd.data.subscribe(self.receive_image2)

        # trigger and immediately remove synchronisation
        self.sed.softwareTrigger.notify()
        self.sed.data.synchronizedOn(None)

        for i in range(3):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + expected_duration * 1.1)  # 2s per image should be more than enough in any case

        self.assertEqual(len(self.acq_dates[0]), 3)
        self.assertEqual(len(self.acq_dates[1]), 3)

        # check all images were acquired simultaneously
        common_dates = self.acq_dates[0] & self.acq_dates[1]
        self.assertEqual(len(common_dates), 3, "Dates not all common between %r and %r" %
                         (self.acq_dates[0], self.acq_dates[1]))

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

    def receive_image2(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[1].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left2 -= 1
        if self.left2 <= 0:
            dataflow.unsubscribe(self.receive_image2)


# @unittest.skip("simple")
class TestSEMCounter(unittest.TestCase):
    """
    Tests of a SEM device with 1 analog and 1 counting detector
    """
    @classmethod
    def setUpClass(cls):
        cls.sem = semcomedi.SEMComedi(**KWARGS_SEM_CNT)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_CNT["name"]:
                cls.cnt = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearDownClass(cls):
        cls.sem.terminate()

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.resolution.value = (25, 20)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = 1e-3  # 1 ms is pretty fast for the counter
        self.acq_dates = (set(), set())  # 2 sets of dates, one for each receiver

    def tearDown(self):
        pass

    def compute_expected_duration(self):
        dwell = self.scanner.dwellTime.value
        settle = self.scanner.settleTime
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

#     @unittest.skip("simple")
    def test_acquire_cnt(self):
        self.scanner.dwellTime.value = 100e-3
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.cnt.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

#     @unittest.skip("simple")
    def test_acquire_long_dt(self):
        # Dwell time above 0.83s cannot be handled by one command only, so dpr
        # is required
        self.scanner.dwellTime.value = 3  # s
        self.scanner.resolution.value = (3, 5)
        self.size = self.scanner.resolution.value
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.cnt.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

#    @unittest.skip("simple")
    def test_acquire_two_flows(self):
        """
        Simple acquisition with two dataflows acquiring (more or less)
        simultaneously
        """
        expected_duration = self.compute_expected_duration()
        number, number2 = 3, 5

        self.left = number
        self.sed.data.subscribe(self.receive_image)

        time.sleep(expected_duration)  # make sure we'll start asynchronously
        self.left2 = number2
        # now, only the counter will generate (true) data
        self.cnt.data.subscribe(self.receive_image2)

        for i in range(number + number2):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + expected_duration * 1.1)  # 2s per image should be more than enough in any case

        # check that at least some images were acquired simultaneously
        common_dates = self.acq_dates[0] & self.acq_dates[1]
        self.assertGreater(len(common_dates), 0, "No common dates between %r and %r" %
                           (self.acq_dates[0], self.acq_dates[1]))

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        # it's ok (for now) that the SED returns empty array
        if image.shape == (0,):
            print("Received empty array")
        else:
            self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)

    def receive_image2(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[1].add(image.metadata[model.MD_ACQ_DATE])
        #print "Received an image %s" % (image,)
        self.left2 -= 1
        if self.left2 <= 0:
            dataflow.unsubscribe(self.receive_image2)


if __name__ == "__main__":
    unittest.main()


# For testing
#def receive(dataflow, data):
#    print "received image of ", data.shape
#
# import odemis.driver.semcomedi as semcomedi
#import numpy
#import logging
#import odemis.driver.comedi_simple as comedi
#import time
#logging.getLogger().setLevel(logging.DEBUG)
#comedi.loglevel(3)
#CONFIG_SED = {"name": "sed", "role": "sed", "channel":5, "limits": [-3, 3]}
# CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[0, 5], [0, 5]], "channels": [0,1], "settle_time": 10e-6, "hfw_nomag": 10e-3}
#CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0", "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER} }
#d = semcomedi.SEMComedi(**CONFIG_SEM)
#sr = d._scanner
#sr.dwellTime.value = 10e-6
#dr = d._detectors["detector0"]
#dr.data.subscribe(receive)
#time.sleep(5)
#dr.data.unsubscribe(receive)
#time.sleep(1)
#dr.data.subscribe(receive)
#time.sleep(2)
#dr.data.unsubscribe(receive)
#
#r = d._get_data([0, 1], 0.01, 3)
#w = numpy.array([[1],[2],[3],[4]], dtype=float)
#d.write_data([0], 0.01, w)
#scanned = [300, 300]
#scanned = [1000, 1000]
#limits = numpy.array([[0, 5], [0, 5]], dtype=float)
#margin = 2
#s = semcomedi.Scanner._generate_scan_array(scanned, limits, margin)
##d.write_data([0, 1], 100e-6, s)
#r = d.write_read_data_phys([0, 1], [5, 6], 10e-6, s)
#v=[]
#for a in r:
#    v.append(d._scan_result_to_array(a, scanned, margin))
#
#import pylab
#pylab.plot(r[0])
#pylab.show()
#
#pylab.plot(rr[:,0])
#pylab.imshow(v[0])
