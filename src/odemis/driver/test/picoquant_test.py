#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
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
'''
from __future__ import division

import Pyro4
import copy
import logging
from odemis import model
from odemis.driver import simsem, picoquant
import os
import pickle
import threading
import time
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG_DET0 = {"name": "APD0", "role": "cl-detector"}
CONFIG_DET1 = {"name": "APD1", "role": "cl-detector2"}
CONFIG_PH = {"name": "HP300", "role": "time-correlator", "device": None,
             "children": {"detector0": CONFIG_DET0, "detector1": CONFIG_DET1}
            }

if TEST_NOHW:
    CONFIG_PH["device"] = "fake"


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

    def test_acquire_rawdet(self):
        for i in range(3):
            data = self.det0.data.get()
            self.assertEqual(data.shape, (1,))
            self.assertIn(model.MD_EXP_TIME, data.metadata)

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

if __name__ == "__main__":
    unittest.main()
