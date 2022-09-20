#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 21 Apr 2016
Copyright © 2016 Éric Piel, Delmic
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
import copy
import logging
from odemis import model
from odemis.driver import picoquant, simulated
import os
import time
import unittest
from odemis.driver import actuator

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG_SYNC = {"name": "Sync", "role": "cl-detector"}
CONFIG_DET0 = {"name": "APD0", "role": "cl-detector"}
CONFIG_DET1 = {"name": "APD1", "role": "cl-detector2"}

CONFIG_PH = {
    "name": "HP300",
    "role": "time-correlator",
    "device": None,
    "disc_volt": [0.1, 0.1],
    "zero_cross": [1e-3, 1e-3],
    "children": {"detector0": CONFIG_DET0, "detector1": CONFIG_DET1},
}

if TEST_NOHW:
    CONFIG_PH["device"] = "fake"

CONFIG_HH = {
    "name": "HH400",
    "role": "time-correlator",
    "device": None,
    "sync_dv": 50e-3,
    "sync_zc": 10e-3,
    "disc_volt": [30e-3],
    "zero_cross": [10e-3],
    "children": {"detector0": CONFIG_SYNC, "detector1": CONFIG_DET1},
}

if TEST_NOHW:
    CONFIG_HH["device"] = "fake"


class TestPH300Static(unittest.TestCase):
    """
    Tests which don't need a PH300 ready
    """

    def test_fake(self):
        """
        Test that the simulator also works
        """
        sim_config = copy.deepcopy(CONFIG_PH)
        sim_config["device"] = "fake"
        dev = picoquant.PH300(**sim_config)

        # self.assertEqual(len(dev.resolution.value), 1)
        self.assertIsInstance(dev.data, model.DataFlow)

        dev.terminate()

    def test_error(self):
        wrong_config = copy.deepcopy(CONFIG_PH)
        wrong_config["device"] = "NOTAGOODSN"
        self.assertRaises(Exception, picoquant.PH300, **wrong_config)


class TestHH400Static(unittest.TestCase):
    """
    Tests which don't need a HH400 ready
    """

    def test_fake(self):
        """
        Test that the simulator also works
        """
        sim_config = copy.deepcopy(CONFIG_HH)
        sim_config["device"] = "fake"
        dev = picoquant.HH400(**sim_config)

        # self.assertEqual(len(dev.resolution.value), 1)
        self.assertIsInstance(dev.data, model.DataFlow)

        dev.terminate()

    def test_error(self):
        wrong_config = copy.deepcopy(CONFIG_HH)
        wrong_config["device"] = "NOTAGOODSN"
        self.assertRaises(Exception, picoquant.HH400, **wrong_config)


class TestPH300(unittest.TestCase):
    """
    Tests which can share one PH300 device
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = picoquant.PH300(**CONFIG_PH)

        for child in cls.dev.children.value:
            if child.name == CONFIG_DET0["name"]:
                cls.det0 = child
            elif child.name == CONFIG_DET1["name"]:
                cls.det1 = child

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()
        time.sleep(1)

    def test_acquire_get(self):
        dt = self.dev.dwellTime.range[0]
        self.dev.dwellTime.value = dt
        exp_shape = self.dev.shape[-2::-1]
        df = self.dev.data
        for i in range(3):
            data = df.get()
            self.assertEqual(data.shape, exp_shape)
            self.assertEqual(data.metadata[model.MD_DWELL_TIME], dt)
            self.dev.dwellTime.value = dt * 2
            dt = self.dev.dwellTime.value

    def test_acquire_sub(self):
        """Test the subscription"""
        dt = 1  # 1s
        df = self.dev.data
        self.dev.dwellTime.value = dt
        exp_shape = self.dev.shape[-2::-1]

        self._cnt = 0
        self._lastdata = None
        df.subscribe(self._on_det)
        time.sleep(8)  # consider some time for opening/ closing shutters in subclass
        df.unsubscribe(self._on_det)
        self.assertGreater(self._cnt, 3)
        self.assertEqual(self._lastdata.shape, exp_shape)

    def _on_det(self, df, data):
        self._cnt += 1
        self._lastdata = data

    def test_va(self):
        """Test changing VA"""
        dt = self.dev.dwellTime.range[0]
        self.dev.dwellTime.value = dt
        df = self.dev.data

        print(self.dev.pixelDuration.choices)
        for i, pxdr in zip(range(1, 5), self.dev.pixelDuration.choices):
            self.dev.pixelDuration.value = pxdr
            pxd = self.dev.pixelDuration.value
            self.assertGreaterEqual(pxd, pxdr)

            so = -10e-9 * i
            self.dev.syncOffset.value = so
            self.assertAlmostEqual(self.dev.syncOffset.value, so)

            data = df.get()
            self.assertEqual(data.metadata[model.MD_DWELL_TIME], dt)
            tl = data.metadata[model.MD_TIME_LIST]
            self.assertAlmostEqual(tl[0], so)
            self.assertAlmostEqual(tl[1] - tl[0], pxd)
            self.assertEqual(len(tl), data.shape[1])
            self.assertEqual(len(tl), self.dev.resolution.value[0])

        for i in self.dev.syncDiv.choices:
            self.dev.syncDiv.value = i
            self.assertEqual(self.dev.syncDiv.value, i)

    def test_acquire_rawdet(self):
        for i in range(3):
            data = self.det0.data.get()
            self.assertEqual(data.shape, (1,))
            self.assertIn(model.MD_DWELL_TIME, data.metadata)

        # Test the subscription
        self._cnt = 0
        self._lastdata = None
        self.det0.data.subscribe(self._on_rawdet)
        time.sleep(2)
        self.det0.data.unsubscribe(self._on_rawdet)
        self.assertGreater(self._cnt, 10)  # Should be 10Hz => ~20
        self.assertEqual(self._lastdata.shape, (1,))

    def _on_rawdet(self, df, data):
        self._cnt += 1
        self._lastdata = data


class TestHH400(unittest.TestCase):
    """
    Tests which can share one HH400 device
    """

    # TODO
    @classmethod
    def setUpClass(cls):
        cls.dev = picoquant.HH400(**CONFIG_HH)

        for child in cls.dev.children.value:
            if child.name == CONFIG_SYNC["name"]:
                cls.det0 = child
            elif child.name == CONFIG_DET1["name"]:
                cls.det1 = child

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()
        time.sleep(1)

    def test_acquire_get(self):
        dt = self.dev.dwellTime.range[0]
        self.dev.dwellTime.value = dt
        exp_shape = self.dev.shape[-2::-1]
        df = self.dev.data
        for i in range(3):
            data = df.get()
            self.assertEqual(data.shape, exp_shape)
            self.assertEqual(data.metadata[model.MD_DWELL_TIME], dt)
            self.dev.dwellTime.value = dt * 2
            dt = self.dev.dwellTime.value

    def test_acquire_sub(self):
        """Test the subscription"""
        dt = 1  # 1s
        df = self.dev.data
        self.dev.dwellTime.value = dt
        exp_shape = self.dev.shape[-2::-1]

        self._cnt = 0
        self._lastdata = None
        df.subscribe(self._on_det)
        time.sleep(8)  # consider some time for opening/ closing shutters in subclass
        df.unsubscribe(self._on_det)
        self.assertGreater(self._cnt, 3)
        self.assertEqual(self._lastdata.shape, exp_shape)

    def _on_det(self, df, data):
        self._cnt += 1
        self._lastdata = data

    def test_va(self):
        """Test changing VA"""
        dt = self.dev.dwellTime.range[0]
        self.dev.dwellTime.value = dt
        df = self.dev.data

        print(self.dev.pixelDuration.choices)
        for i, pxdr in zip(range(1, 5), self.dev.pixelDuration.choices):
            self.dev.pixelDuration.value = pxdr
            pxd = self.dev.pixelDuration.value
            self.assertGreaterEqual(pxd, pxdr)

            so = -10e-9 * i
            self.dev.syncChannelOffset.value = so
            self.assertAlmostEqual(self.dev.syncChannelOffset.value, so)

            data = df.get()
            self.assertEqual(data.metadata[model.MD_DWELL_TIME], dt)
            tl = data.metadata[model.MD_TIME_LIST]
            self.assertAlmostEqual(tl[0], so)
            self.assertAlmostEqual(tl[1] - tl[0], pxd)
            self.assertEqual(len(tl), data.shape[1])
            self.assertEqual(len(tl), self.dev.resolution.value[0])

        for i in self.dev.syncDiv.choices:
            self.dev.syncDiv.value = i
            self.assertEqual(self.dev.syncDiv.value, i)

    def test_acquire_rawdet(self):
        for i in range(3):
            data = self.det0.data.get()
            self.assertEqual(data.shape, (1,))
            self.assertIn(model.MD_DWELL_TIME, data.metadata)

        # Test the subscription
        self._cnt = 0
        self._lastdata = None
        self.det0.data.subscribe(self._on_rawdet)
        time.sleep(2)
        self.det0.data.unsubscribe(self._on_rawdet)
        self.assertGreater(self._cnt, 10)  # Should be 10Hz => ~20
        self.assertEqual(self._lastdata.shape, (1,))

    def _on_rawdet(self, df, data):
        self._cnt += 1
        self._lastdata = data


PH300_KWARGS = dict(
    name="Time Correlator",
    role="time-correlator",
    device=None,
    shutter_axes={"shutter0": ["x", 0, 1], "shutter1": ["x", 0, 1]},
)

if TEST_NOHW:
    PH300_KWARGS["device"] = "fake"

HH400_KWARGS = dict(
    name="Time Correlator",
    role="time-correlator",
    device=None,
    shutter_axes={"shutter0": ["x", 0, 1], "shutter1": ["x", 0, 1]},
)

if TEST_NOHW:
    HH400_KWARGS["device"] = "fake"


class TestPH300_Shutters(TestPH300):
    """
    Tests PH300 with shutters.
    """

    @classmethod
    def setUpClass(cls):
        cls.tc_act = simulated.Stage(
            "stage",
            "",
            ["shutter0", "shutter1"],
            {"shutter0": (0, 1), "shutter1": (0, 1)},
        )
        cls.shutter0 = actuator.MultiplexActuator(
            "Shutter 0", "shutter0", {"x": cls.tc_act}, {"x": "shutter0"}
        )
        cls.shutter1 = actuator.MultiplexActuator(
            "Shutter 1", "shutter1", {"x": cls.tc_act}, {"x": "shutter1"}
        )

        cls.dev = picoquant.PH300(
            children={"detector0": CONFIG_DET0, "detector1": CONFIG_DET1},
            dependencies={"shutter0": cls.shutter0, "shutter1": cls.shutter1},
            **PH300_KWARGS
        )

        for child in cls.dev.children.value:
            if child.name == CONFIG_DET0["name"]:
                cls.det0 = child
            elif child.name == CONFIG_DET1["name"]:
                cls.det1 = child

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()
        time.sleep(1)

    def test_shutters(self):
        # When acquiring, the shutters should open and close automatically once the acquisition is done
        self._cnt = 0
        self._lastdata = None
        self.tc_act.speed.value = {
            "shutter0": 10,
            "shutter1": 10,
        }  # shutters are much faster than a stage
        self.dev.data.subscribe(self._on_rawdet)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 1)
        self.assertEqual(self.tc_act.position.value["shutter1"], 1)
        self.dev.data.unsubscribe(self._on_rawdet)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)
        # Acquire on one detector alone and check if the right shutter opens
        self.det0.data.subscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 1)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)
        self.det0.data.unsubscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)

        self.det1.data.subscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 1)
        self.det1.data.unsubscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)


class TestHH400_Shutters(TestHH400):
    """
    Tests HH400 with shutters.
    """

    @classmethod
    def setUpClass(cls):
        cls.tc_act = simulated.Stage(
            "stage",
            "",
            ["shutter0", "shutter1"],
            {"shutter0": (0, 1), "shutter1": (0, 1)},
        )
        cls.shutter0 = actuator.MultiplexActuator(
            "Shutter 0", "shutter0", {"x": cls.tc_act}, {"x": "shutter0"}
        )
        cls.shutter1 = actuator.MultiplexActuator(
            "Shutter 1", "shutter1", {"x": cls.tc_act}, {"x": "shutter1"}
        )

        cls.dev = picoquant.HH400(
            children={"detector0": CONFIG_SYNC, "detector1": CONFIG_DET1},
            dependencies={"shutter0": cls.shutter0, "shutter1": cls.shutter1},
            **HH400_KWARGS
        )

        for child in cls.dev.children.value:
            if child.name == CONFIG_SYNC["name"]:
                cls.det0 = child
            elif child.name == CONFIG_DET1["name"]:
                cls.det1 = child

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()
        time.sleep(1)

    def test_shutters(self):
        # When acquiring, the shutters should open and close automatically once the acquisition is done
        self._cnt = 0
        self._lastdata = None
        self.tc_act.speed.value = {
            "shutter0": 10,
            "shutter1": 10,
        }  # shutters are much faster than a stage
        self.dev.data.subscribe(self._on_rawdet)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 1)
        self.assertEqual(self.tc_act.position.value["shutter1"], 1)
        self.dev.data.unsubscribe(self._on_rawdet)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)
        # Acquire on one detector alone and check if the right shutter opens
        self.det0.data.subscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 1)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)
        self.det0.data.unsubscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)

        self.det1.data.subscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 1)
        self.det1.data.unsubscribe(self._on_det)
        time.sleep(1)
        self.assertEqual(self.tc_act.position.value["shutter0"], 0)
        self.assertEqual(self.tc_act.position.value["shutter1"], 0)


if __name__ == "__main__":
    unittest.main()
