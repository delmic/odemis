#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 12 May 2014

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
import math

import Pyro4
from concurrent.futures import CancelledError
import copy
import logging
from odemis import model
from odemis.dataio import hdf5
from odemis.driver import tescan
from odemis.util import testing, output_window
import os
import pickle
import threading
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# Note: there is a simulator, but it must be run in Windows (on a virtual machine)
# However, not everything behaves exactly as on the real hardware, so beware

# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed", "channel": 0, "detector": 0}
CONFIG_BSD = {"name": "bsd", "role": "bsd"}
CONFIG_STG = {"name": "stg", "role": "stage"}
CONFIG_CM = {"name": "camera", "role": "chamber-ccd"}
CONFIG_FOCUS = {"name": "focus", "role": "focus", "axes": ["z"]}
CONFIG_PRESSURE = {"name": "pressure", "role": "pressure"}
CONFIG_LIGHT = {"name": "light", "role": "chamber-light"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam",
                  "fov_range": [196.e-9, 25586.e-6]}
CONFIG_SEM = {"name": "sem", "role": "sem",
              "children": {"detector0": CONFIG_SED,
                           "scanner": CONFIG_SCANNER,
                           "stage": CONFIG_STG,
                           "focus": CONFIG_FOCUS,
                           # "camera": CONFIG_CM,
                           "pressure": CONFIG_PRESSURE},
              # change host ip address according to network settings
              "host": "192.168.56.11"
              }

# This one works with the Mira Simulator
CONFIG_SEM_NO_DET = {"name": "Mira", "role": "sem",
                     "children": {"scanner": CONFIG_SCANNER,
                                  "stage": CONFIG_STG,
                                  "focus": CONFIG_FOCUS,
                                  "light": CONFIG_LIGHT},
                     # change host ip address according to network settings
                     "host": "192.168.56.11"
                     }

# This one works with the Amber Simulator
CONFIG_SEM_AMBER = {"name": "Amber", "role": "sem",
                    "children": {"scanner": CONFIG_SCANNER,
                                 "stage": CONFIG_STG,
                                 "focus": CONFIG_FOCUS},
                    # change host ip address according to network settings
                    "host": "192.168.56.11"
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
            self.skipTest("No hardware present")

        sem = tescan.SEM(**CONFIG_SEM)
        self.assertEqual(len(sem.children.value), 6)

        for child in sem.children.value:
            if child.name == CONFIG_SED["name"]:
                sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                scanner = child
            elif child.name == CONFIG_STG["name"]:
                stage = child
            elif child.name == CONFIG_FOCUS["name"]:
                focus = child
            elif child.name == CONFIG_CM["name"]:
                camera = child
            elif child.name == CONFIG_PRESSURE["name"]:
                pressure = child

        self.assertEqual(len(scanner.resolution.value), 2)
        self.assertIsInstance(sed.data, model.DataFlow)

        self.assertTrue(sem.selfTest(), "SEM self test failed.")
        sem.terminate()

    def test_error(self):
        wrong_config = copy.deepcopy(CONFIG_SEM)
        wrong_config["children"]["scanner"]["channels"] = [1, 1]
        self.assertRaises(Exception, tescan.SEM, **wrong_config)

    def test_pickle(self):
        if TEST_NOHW:
            self.skipTest("No hardware present")

        try:
            os.remove("test")
        except OSError:
            pass
        daemon = Pyro4.Daemon(unixsocket="test")

        sem = tescan.SEM(daemon=daemon, **CONFIG_SEM)

        dump = pickle.dumps(sem, pickle.HIGHEST_PROTOCOL)
#        print "dump size is", len(dump)
        sem_unpickled = pickle.loads(dump)
        self.assertIsInstance(sem_unpickled.children, model.VigilantAttributeBase)
        self.assertEqual(sem_unpickled.name, sem.name)
        sem.terminate()
        daemon.shutdown()


class TestSEMRotateStage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            return
        cls.sem = tescan.SEM(**CONFIG_SEM_AMBER)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_STG["name"]:
                cls.stage = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.focus = child

    @classmethod
    def tearDownClass(cls):
        if TEST_NOHW:
            return

        cls.sem.terminate()
        time.sleep(3)

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No hardware present")

    def tearDown(self):
        pass

    def test_move_abs(self):
        """
        Test if it's possible to move the stage in absolute coordinates
        In addition also test the edge of the range of the rz axis
        """
        start_pos = self.stage.position.value.copy()
        new_pos = {"x": start_pos["x"] + 5e-4, "y": start_pos["y"] + 10e-4, "z": start_pos["z"] + 12e-5,
                   "rx": start_pos["rx"] - math.radians(2.5), "rz": start_pos["rz"] + math.radians(3.5)}

        f = self.stage.moveAbs(new_pos)
        f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, new_pos)

        # move exactly on the edge of the max range of the rz axis
        new_pos["rz"] = math.radians(360)
        f = self.stage.moveAbs(new_pos)
        f.result()
        new_pos["rz"] = 0.0
        # software will return 0 on abs position of 360°
        testing.assert_pos_almost_equal(self.stage.position.value, new_pos)

        # go back to the original starting point
        self.stage.moveAbsSync(start_pos)

    def test_move_rel(self):
        """
        Test if it's possible to move the stage with relative moves with regular move requests
        """
        start_pos = self.stage.position.value.copy()

        # test some of the linear axes and especially the newly added rotational axes
        f = self.stage.moveRel({"x": 2e-6, "y": 3e-6, "rx": math.radians(1), "rz": -math.radians(15)})
        f.result()

        self.assertNotEqual(self.stage.position.value, start_pos)

        f = self.stage.moveRel({"x": -2e-6, "y": -3e-6, "rx": -math.radians(1), "rz": math.radians(15)})
        f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, start_pos, atol=0.1e-6)

        # go back to the original starting point
        self.stage.moveAbsSync(start_pos)
        self.assertEqual(self.stage.position.value, start_pos)

    def test_move_out_of_range(self):
        """
        test if a relative move and an absolute move out of the range generate ValueErrors
        """
        start_pos = self.stage.position.value.copy()

        # test a relative move outside the range (more than max)
        axes = self.stage.axes
        with self.assertRaises(ValueError):
            self.stage.moveRel({"x": axes["x"].range[1] - axes["x"].range[0] + 10e-6})

        # test an absolute move outside the range (less than min)
        with self.assertRaises(ValueError):
            self.stage.moveAbsSync({"rx": math.radians(-90)})

        # go back to the original starting point
        self.stage.moveAbsSync(start_pos)

    def test_small_relative_movements(self):
        """
        Test to see if very small relative movements are picked up fast enough.
        In addition, test if shifting a couple of times back and forth will end up at the same starting location.
        """
        start_pos = self.stage.position.value.copy()

        # test some of the linear axes and especially the newly added rotational axes
        f = self.stage.moveRel({"z": 1e-6, "rx": math.radians(0.1), "rz": math.radians(0.1)})
        f.result()
        testing.assert_pos_not_almost_equal(self.stage.position.value, start_pos, atol=0.1e-6)

        new_pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"z": 1e-6, "rx": math.radians(0.1), "rz": math.radians(0.1)})
        f.result()
        testing.assert_pos_not_almost_equal(self.stage.position.value, new_pos, atol=0.1e-6)

        new_pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"z": 1e-6, "rx": math.radians(0.1), "rz": math.radians(0.1)})
        f.result()
        testing.assert_pos_not_almost_equal(self.stage.position.value, new_pos, atol=0.1e-6)

        # go back the same amount of movements and axes values
        new_pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"z": -1e-6, "rx": -math.radians(0.1), "rz": -math.radians(0.1)})
        f.result()
        testing.assert_pos_not_almost_equal(self.stage.position.value, new_pos, atol=0.1e-6)

        new_pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"z": -1e-6, "rx": -math.radians(0.1), "rz": -math.radians(0.1)})
        f.result()
        testing.assert_pos_not_almost_equal(self.stage.position.value, new_pos, atol=0.1e-6)

        new_pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"z": -1e-6, "rx": -math.radians(0.1), "rz": -math.radians(0.1)})
        f.result()
        testing.assert_pos_not_almost_equal(self.stage.position.value, new_pos, atol=0.1e-6)
        # test if after the last movement, the position is the same as the starting position
        testing.assert_pos_almost_equal(self.stage.position.value, start_pos, atol=0.1e-6)

    def test_overflow_and_underflow(self):
        """
        With a relative move starting from the absolute zero point move a bit under the range
        and afterward move a bit over the range. This is tested for the full rotational axis Rz only.
        """
        # start with going to the absolute zero position
        self.stage.moveAbsSync({"rz": 0.0})
        start_pos = self.stage.position.value.copy()

        # test if underflow of the range is picked up
        f = self.stage.moveRel({"rz": -math.radians(3)})
        f.result()
        self.assertLess(start_pos["rz"], self.stage.position.value["rz"])

        # test if overflow of the range is picked up
        f = self.stage.moveRel({"rz": math.radians(6)})
        f.result()
        self.assertGreater(self.stage.position.value["rz"], start_pos["rz"])

    def test_stop_big_move(self):
        """
        Test if it's possible to stop the stage while it's moving by using a big move.
        """
        start_pos = self.stage.position.value.copy()

        # check a big move and stop it
        f = self.stage.moveRel({"rz": math.radians(180)})
        f.result()

        # wait just a tiny bit and stop the movement
        time.sleep(0.2)
        self.stage.stop()

        self.assertNotEqual(self.stage.position.value, start_pos)


class BaseSEMTest(object):

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            return
        cls.sem = tescan.SEM(**cls.CONFIG_HW)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_STG["name"]:
                cls.stage = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.focus = child
            # Doesn't seem to work with the simulator
            elif child.name == CONFIG_CM["name"]:
                cls.camera = child
            elif child.name == CONFIG_PRESSURE["name"]:
                cls.pressure = child
            elif child.name == CONFIG_LIGHT["name"]:
                cls.light = child

    @classmethod
    def tearDownClass(cls):
        if TEST_NOHW:
            return

        cls.sem.terminate()
        time.sleep(3)

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No hardware present")

    def tearDown(self):
#        print gc.get_referrers(self.camera)
#        gc.collect()
        pass

    def test_probe_current(self):
        ebeam = self.scanner

        orig_probe_current = ebeam.probeCurrent.value
        pc_choices = sorted(ebeam.probeCurrent.choices)
        ebeam.probeCurrent.value = pc_choices[0]
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(pc_choices[0], ebeam.probeCurrent.value)

        # Reset
        ebeam.probeCurrent.value = orig_probe_current
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(orig_probe_current, ebeam.probeCurrent.value)

    def test_acceleration_voltage(self):
        ebeam = self.scanner

        orig_vol = ebeam.accelVoltage.value
        new_vol = 5000
        if orig_vol == new_vol:
            new_vol = 10000
        ebeam.accelVoltage.value = new_vol
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(new_vol, ebeam.accelVoltage.value)

        # Reset
        ebeam.accelVoltage.value = orig_vol
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(orig_vol, ebeam.accelVoltage.value)

    def test_blanker(self):
        """
        Check it's possible to blank/unblank
        """
        ebeam = self.scanner
        orig_blanked = ebeam.blanker.value
        new_blanked = not orig_blanked
        ebeam.blanker.value = new_blanked

        # self.assertEqual(new_blanked, ebeam.blanker.value)
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(new_blanked, ebeam.blanker.value)

        # Reset
        ebeam.blanker.value = orig_blanked
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(orig_blanked, ebeam.blanker.value)

    def test_external(self):
        """
        Test if it's possible to change external
        """
        ebeam = self.scanner
        orig_ext = ebeam.external.value
        new_ext = not orig_ext
        ebeam.external.value = new_ext

        # self.assertEqual(new_blanked, ebeam.blanker.value)
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(new_ext, ebeam.external.value)

        # Reset
        ebeam.external.value = orig_ext
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(orig_ext, ebeam.external.value)

    def test_move_rel(self):
        """
        Check it's possible to move the stage with relative moves
        """
        pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"x":2e-6, "y":3e-6})
        f.result()
        self.assertNotEqual(self.stage.position.value, pos)
        time.sleep(6)  # wait until .position is updated
        self.assertNotEqual(self.stage.position.value, pos)

        f = self.stage.moveRel({"x":-2e-6, "y":-3e-6})
        f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

        # Try a relative move outside of the range (less than min)
        axes = self.stage.axes
        # toofar = {"x": axes["x"].range[0] - pos["x"] - 10e-6}
        # f = self.stage.moveRel(toofar)
        # with self.assertRaises(ValueError):
        #     f.result()
        # testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)
        # time.sleep(6)
        # testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)
        #
        # # Try a relative move outside of the range (more than max)
        # toofar = {"y": axes["y"].range[1] - pos["y"] + 10e-6}
        # f = self.stage.moveRel(toofar)
        # with self.assertRaises(ValueError):
        #     f.result()
        # testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)
        # time.sleep(6)
        # testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

        # Move really too far (more than the range)
        with self.assertRaises(ValueError):
            f = self.stage.moveRel({"x":axes["x"].range[1] - axes["x"].range[0] + 10e-6})

    def test_move_abs(self):
        """
        Check it's possible to move the stage in absolute coordinates
        """
        p = self.stage.position.value.copy()
        subpos = self.stage.position.value.copy()
        subpos["x"] += 50e-6
        f = self.stage.moveAbs(subpos)
        f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, subpos)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, subpos)

        self.stage.moveAbsSync({"x": p["x"]})
        testing.assert_pos_almost_equal(self.stage.position.value, p)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, p)

    # @skip("not working with the newer simulator Tescan Essence v1.2.2")
    def test_stop(self):
        """
        Check it's possible to stop the stage while it's moving.
        """
        # TODO: make this method work again with Tescan Essence v1.2.2. will be addressed in separate PR
        pos = self.stage.position.value.copy()
        logging.info("Initial pos = %s", pos)
        f = self.stage.moveRel({"y":-50e-3})
        exppos = pos.copy()
        exppos["y"] += -50e-3

        time.sleep(0.5)  # abort after 0.5 s
        f.cancel()

        time.sleep(6)  # wait for position to update
        self.assertNotEqual(self.stage.position.value, pos)
        self.assertNotEqual(self.stage.position.value, exppos)

        f = self.stage.moveAbs(pos)  # Back to orig pos
        f.result()
        time.sleep(6)  # wait for position to update
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

        # Same thing, but using stop() method
        pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"y": 10e-3})
        time.sleep(0.5)
        self.stage.stop()

        with self.assertRaises(CancelledError):
            f.result()

        exppos = pos.copy()
        exppos["y"] += 10e-3
        self.assertNotEqual(self.stage.position.value, pos)
        self.assertNotEqual(self.stage.position.value, exppos)

        f = self.stage.moveAbs(pos)  # Back to orig pos
        f.result()
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)


class TestSEMNoDet(BaseSEMTest, unittest.TestCase):
    """
    Tests when connected for only controlling the settings, but no acquisition
    """
    CONFIG_HW = CONFIG_SEM_NO_DET

    def setUp(self):
        super(TestSEMNoDet, self).setUp()

    def test_light(self):
        """
        Test if it's possible to change chamber light
        """
        light = self.light
        orig_pwr = light.power.value
        if orig_pwr == light.power.range[0]:
            new_pwr = light.power.range[1]
        else:
            new_pwr = light.power.range[0]
        light.power.value = new_pwr
        self.assertEqual(light.power.value, list(new_pwr))

        time.sleep(1)
        # Reset
        light.power.value = orig_pwr
        self.assertEqual(light.power.value, orig_pwr)


# @skip("skip")
class TestSEM(BaseSEMTest, unittest.TestCase):
    """
    Tests which can share one SEM device
    """
    CONFIG_HW = CONFIG_SEM

    def setUp(self):
        super(TestSEM, self).setUp()

        # reset resolution and dwellTime
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = (512, 256)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        self.acq_dates = (set(), set())  # 2 sets of dates, one for each receiver
        self.acq_done = threading.Event()

    def compute_expected_duration(self):
        dwell = self.scanner.dwellTime.value
        settle = 5.e-6
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

    def test_blanker(self):
        # TODO: for now, with detectors it cannot be changed
        pass

    def test_acquire(self):
        self.scanner.dwellTime.value = 10e-6  # s
        expected_duration = self.compute_expected_duration()

        start = time.time()
        im = self.sed.data.get()
        hdf5.export("test.h5", model.DataArray(im))
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
        self.scanner.translation.value = (-1, 1)  # will be set back to 0,0 as it cannot move
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
        testing.assert_tuple_almost_equal(self.scanner.resolution.value, max_res, delta=1.1)
        self.assertEqual(self.scanner.translation.value, (0, 0))

        # Then, check metadata fits with the expectations
        center = (1e3, -2e3)  # m
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

    def test_acquire_flow(self):
        expected_duration = self.compute_expected_duration()

        number = 5
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        self.acq_done.wait(number * (2 + expected_duration * 1.1))  # 2s per image should be more than enough in any case

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
            time.sleep(i)
            self.sed.data.unsubscribe(self.receive_image)

        # now this one should work
        self.sed.data.subscribe(self.receive_image)
        time.sleep(expected_duration * 5)  # make sure we received at least one image
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
            time.sleep(expected_duration * 6)  # make sure we received at least one image
            self.sed.data.unsubscribe(self.receive_image)

        # if it has acquired a least 5 pictures we are already happy
        self.assertLessEqual(self.left, 10000)

    def onEvent(self):
        self.events += 1

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#         print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)
            self.acq_done.set()

if __name__ == "__main__":
    unittest.main()
