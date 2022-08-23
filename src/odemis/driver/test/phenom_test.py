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
from odemis.driver import phenom
from odemis.model import HwError
from odemis.util import testing
import os
import pickle
import threading
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")

# If no hardware, we pretty much cannot test anything :-(
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# logging.getLogger().setLevel(logging.DEBUG)
# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed"}
CONFIG_BSD = {"name": "bsd", "role": "bsd"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam"}
CONFIG_FOCUS = {"name": "focus", "role": "ebeam-focus", "axes": ["z"]}
CONFIG_NC_FOCUS = {"name": "navcam-focus", "role": "overview-focus", "axes": ["z"]}
CONFIG_STAGE = {"name": "stage", "role": "stage"}
CONFIG_NAVCAM = {"name": "camera", "role": "overview-ccd"}
CONFIG_PRESSURE = {"name": "pressure", "role": "chamber"}
CONFIG_SEM = {"name": "sem", "role": "sem", "host": "http://Phenom-MVE0215801135.local:8888",
              "username": "Delmic", "password": "4XE1947GKP9B",
              "children": {"detector": CONFIG_SED, "scanner": CONFIG_SCANNER,
                           "stage": CONFIG_STAGE, "focus": CONFIG_FOCUS,
                           "navcam": CONFIG_NAVCAM, "navcam-focus": CONFIG_NC_FOCUS,
                           "pressure": CONFIG_PRESSURE}
              }

# @skip("skip")
class TestSEMStatic(unittest.TestCase):
    """
    Tests which don't need a SEM component ready
    """
    def test_creation(self):
        """
        Doesn't even try to acquire an image, just create and delete components
        """
        if TEST_NOHW:
            self.skipTest("TEST_NOHW set, cannot test Phenom")

        sem = phenom.SEM(**CONFIG_SEM)
        self.assertEqual(len(sem.children.value), 7)

        for child in sem.children.value:
            if child.name == CONFIG_SED["name"]:
                sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                scanner = child

        self.assertEqual(len(scanner.resolution.value), 2)
        self.assertIsInstance(sed.data, model.DataFlow)

        self.assertTrue(sem.selfTest(), "SEM self test failed.")
        sem.terminate()

    def test_hw_error(self):
        """
        Check it raises HwError in case of wrong IP address
        """
        wrong_config = copy.deepcopy(CONFIG_SEM)
        wrong_config["host"] = "http://Phenom-MVE0123456789.local:8888"
        self.assertRaises(HwError, phenom.SEM, **wrong_config)

    def test_pickle(self):
        if TEST_NOHW:
            self.skipTest("TEST_NOHW set, cannot test Phenom")

        try:
            os.remove("test")
        except OSError:
            pass
        daemon = Pyro4.Daemon(unixsocket="test")

        sem = phenom.SEM(daemon=daemon, **CONFIG_SEM)

        dump = pickle.dumps(sem, pickle.HIGHEST_PROTOCOL)
#        print "dump size is", len(dump)
        sem_unpickled = pickle.loads(dump)
        self.assertIsInstance(sem_unpickled.children, model.VigilantAttributeBase)
        self.assertEqual(sem_unpickled.name, sem.name)
        sem.terminate()
        daemon.shutdown()


# @skip("skip")
class TestSEM(unittest.TestCase):
    """
    Tests which can share one SEM device
    """
    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            return
        cls.sem = phenom.SEM(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.focus = child
            elif child.name == CONFIG_STAGE["name"]:
                cls.stage = child
            elif child.name == CONFIG_NAVCAM["name"]:
                cls.camera = child
            elif child.name == CONFIG_NC_FOCUS["name"]:
                cls.navcam_focus = child
            elif child.name == CONFIG_PRESSURE["name"]:
                cls.pressure = child

    @classmethod
    def tearDownClass(cls):
        if TEST_NOHW:
            return
        cls.sem.terminate()
        time.sleep(3)

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("TEST_NOHW set, cannot test Phenom")

        # reset resolution and dwellTime
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = (512, 512)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        self.acq_dates = (set(), set())  # 2 sets of dates, one for each receiver
        self.acq_done = threading.Event()

        # TODO: a way to group tests, so that all the ones that need to be in
        # SEM mode are together, and all the one for navcam are together?
        f = self.pressure.moveAbs({"vacuum":1e-02})  # move to SEM
        f.result()

    def tearDown(self):
#        print gc.get_referrers(self.camera)
#        gc.collect()
        pass

    def compute_expected_duration(self):
        dwell = self.scanner.dwellTime.value
        settle = 5.e-4
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

#     @skip("skip")
    def test_acquire(self):
        self.scanner.dwellTime.value = 10e-6  # s
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start
        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))
        self.assertIn(model.MD_DWELL_TIME, im.metadata)

    def test_hfv(self):
        orig_pxs = self.scanner.pixelSize.value
        orig_hfv = self.scanner.horizontalFoV.value
        self.scanner.horizontalFoV.value = orig_hfv / 2

        self.assertAlmostEqual(orig_pxs[0] / 2, self.scanner.pixelSize.value[0])

    def test_roi(self):
        """
        check that .translation and .scale work
        """
        # max resolution
        max_res = self.scanner.resolution.range[1]

        # shift
        exp_res = (max_res[0] // 2, max_res[1] // 2)
        self.scanner.resolution.value = exp_res
        self.scanner.translation.value = (-1, 1)
        testing.assert_tuple_almost_equal(self.scanner.resolution.value, exp_res)
        self.assertEqual(self.scanner.translation.value, (-1, 1))
        self.scanner.translation.value = (0, 0)

        self.scanner.resolution.value = max_res
        self.scanner.scale.value = (2, 2)
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]

        # normal acquisition
        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])

        # shift a bit
        # reduce the size of the image so that we can have translation
        self.scanner.translation.value = (-10, 10)  # px
        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])

        # only one point
        self.scanner.resolution.value = (1, 1)
        im = self.sed.data.get()
        self.assertEqual(im.shape, self.scanner.resolution.value[-1::-1])

#     @skip("faster")
    def test_acquire_high_osr(self):
        """
        small resolution, but large osr, to force acquisition not by whole array
        """
        self.scanner.resolution.value = (256, 300)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] * 100
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
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[1]  # DPR should be 3
        expected_duration = self.compute_expected_duration()  # same as dwell time

        start = time.time()
        im = self.sed.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.size[::-1])
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))

    def test_acquire_long_short(self):
        """
        test being able to cancel image acquisition if dwell time is too long
        """
        self.scanner.resolution.value = (256, 300)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0] * 100
        expected_duration_l = self.compute_expected_duration()  # about 5 s

        self.left = 1
        start = time.time()

        # acquire one long, and change to a short time
        self.sed.data.subscribe(self.receive_image)
        # time.sleep(0.1) # make sure it has started
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]  # shorten
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
        dwell = self.scanner.dwellTime.range[0] * 4
        self.scanner.dwellTime.value = dwell
        self.scanner.resolution.value = self.scanner.resolution.range[1]  # test big image
        self.size = self.scanner.resolution.value
        expected_duration = self.compute_expected_duration()
        logging.debug("Expecting duration of %g s per acq", expected_duration)

        number = 3
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        time.sleep(expected_duration * 0.5)  # half-way acquiring
        # change few attributes
        dwell = self.scanner.dwellTime.range[0] * 2
        self.scanner.dwellTime.value = dwell
        self.scanner.spotSize.value = 2.5
        self.scanner.accelVoltage.value = 6000
        time.sleep(expected_duration * 0.7)  # make sure the first acquisition is over
        expected_duration = self.compute_expected_duration()
        logging.debug("Expecting duration of %g s per acq", expected_duration)

        # Note: the Phenom is very slow to reconfigure ~2s for changing the scan
        self.acq_done.wait(number * (4 + expected_duration * 1.1))  # 4s per image for extra margin

        self.sed.data.unsubscribe(self.receive_image)  # just in case it failed
        self.assertEqual(self.left, 0)

    def test_df_fast_sub_unsub(self):
        """
        Test the dataflow on a very fast cycle subscribing/unsubscribing
        SEMComedi had a bug causing the threads not to start again
        """
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        number = 10
        expected_duration = self.compute_expected_duration()

        self.left = 10000  # don't unsubscribe automatically

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

        self.left = 10000 + number  # don't unsubscribe automatically

        for i in range(number):
            self.sed.data.subscribe(self.receive_image)
            time.sleep(expected_duration * 5)  # make sure we received at least one image
            self.sed.data.unsubscribe(self.receive_image)

        # if it has acquired a least 5 pictures we are already happy
        self.assertLessEqual(self.left, 10000)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        logging.info("Received an image of shape %s", image.shape)
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)
            self.acq_done.set()

    def discard_image(self, dataflow, image):
        """
        do nothing
        """
        pass

#     @skip("skip")
    def test_focus(self):
        """
        Check it's possible to change the focus
        """
        pos = self.focus.position.value
        f = self.focus.moveRel({"z":0.1e-3})
        f.result()
        self.assertNotEqual(self.focus.position.value, pos)
#         self.sed.data.get()

        f = self.focus.moveRel({"z":-0.3e-3})
        f.result()
        self.assertNotEqual(self.focus.position.value, pos)
#         self.sed.data.get()

        # restore original position
        f = self.focus.moveAbs(pos)
        f.result()
        self.assertAlmostEqual(self.focus.position.value["z"], pos["z"], 5)

#     @skip("skip")
    def test_move(self):
        """
        Check it's possible to move the stage
        """
        pos = self.stage.position.value
        f = self.stage.moveRel({"x":-100e-6, "y":-100e-6})  # 1 mm
        f.result()

        # FIXME: this should fail
        self.assertNotEqual(self.stage.position.value, pos)

        time.sleep(1)
        f = self.stage.moveRel({"x":100e-6, "y":100e-6})  # 1 mm
        f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, pos)

#     @skip("skip")
    def test_navcam(self):
        """
        Check it's possible to acquire a navcam image
        """
        f = self.pressure.moveAbs({"vacuum":1e04})  # move to NavCam
        f.result()
        # Exposure time is fixed, time is mainly spent on the image transfer
        expected_duration = 0.5  # s
        start = time.time()
        img = self.camera.data.get()
        duration = time.time() - start
        self.assertGreaterEqual(duration, expected_duration, "Error execution took %f s, less than exposure time %d." % (duration, expected_duration))

#     @skip("skip")
    def test_navcam_focus(self):
        """
        Check it's possible to change the overview focus
        """
        f = self.pressure.moveAbs({"vacuum":1e04})  # move to NavCam
        f.result()
        pos = self.navcam_focus.position.value
        f = self.navcam_focus.moveRel({"z":0.1e-3})  # 1 mm
        f.result()
        self.assertNotEqual(self.navcam_focus.position.value, pos)
        time.sleep(1)

        # restore original position
        f = self.navcam_focus.moveAbs(pos)
        f.result()

        testing.assert_pos_almost_equal(self.navcam_focus.position.value, pos)

#     @skip("skip")
    def test_pressure(self):
        """
        Check it's possible to change the pressure state
        """
        f = self.pressure.moveAbs({"vacuum":1e-02})  # move to SEM
        f.result()
        new_pos = self.pressure.position.value["vacuum"]
        self.assertEqual(1e-02, new_pos)
        f = self.pressure.moveAbs({"vacuum":1e05})  # Unload
        f.result()
        new_pos = self.pressure.position.value["vacuum"]
        self.assertEqual(1e05, new_pos)
        f = self.pressure.moveAbs({"vacuum":1e04})  # move to NavCam
        f.result()
        new_pos = self.pressure.position.value["vacuum"]
        self.assertEqual(1e04, new_pos)

#     @skip("skip")
    def test_grid_scanning(self):
        # Dwell time is hard-coded when doing grid scanning
        self.scanner.dwellTime.value = 100e-6  # s
        last_res = self.scanner.resolution.value
        self.scanner.resolution.value = (4, 4)
        self.size = self.scanner.resolution.value

        self.sed.data.subscribe(self.discard_image)
        # grid scanning for 5 seconds
        time.sleep(5)
        self.sed.data.unsubscribe(self.discard_image)
        self.assertEqual(self.scanner.resolution.value, (4, 4))
        self.scanner.resolution.value = last_res
        self.size = self.scanner.resolution.value

#     @skip("skip")
    def test_sample_holder(self):
        """
        Check it's possible to read the current sample holder ID
        and it raises a ValueError when wrong code is provided
        """
        sh = self.pressure.sampleHolder.value
        self.assertNotEqual((None, None), sh)
        # Try to register holder with wrong code
        self.assertRaises(ValueError, self.pressure.registerSampleHolder, "wrongCode")

#     @skip("skip")
    def test_auto_contrast(self):
        """
        Check it's possible to apply AutoContrast
        """
        f = self.sed.applyAutoContrast()
        f.result()


if __name__ == "__main__":
    unittest.main()
