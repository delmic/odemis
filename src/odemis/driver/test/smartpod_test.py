#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on Apr 17, 2019

@author: Anders Muskens
Copyright © 2019 Anders Muskens, Delmic

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
import logging
import os
import time
import unittest
from odemis.driver import smartpod
from odemis.util import test

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

COMP_ARGS = {
    "atol": 1e-3,
    "rtol": 1e-3,
    }

CONFIG = {"name": "SmartPod",
        "role": "",
        "locator": "usb:ix:0",
        # "locator": "fake",
        "options":"",
        "axes": {
            'x': {
                'range': [-1, 1],
                'unit': 'm',
            },
            'y': {
                'range': [-1, 1],
                'unit': 'm',
            },
            'z': {
                'range': [-1, 1],
                'unit': 'm',
            },
            'theta_x': {
                'range': [0, 3.1415],
                'unit': 'rad',
            },
            'theta_y': {
                'range': [0, 3.1415],
                'unit': 'rad',
            },
            'theta_z': {
                'range': [0, 3.1415],
                'unit': 'rad',
            },
        },
}


class TestSmartPod(unittest.TestCase):
    """
    Tests cases for the SmartPod actuator driver
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = smartpod.SmartPod(**CONFIG)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()  # free up socket.
        
    def test_reference_cancel(self):
        # Test canceling referencing
        f = self.dev.reference()
        time.sleep(0.1)
        f.cancel()

        for a, i in self.dev.referenced.value.items():
            self.assertFalse(i)

        f = self.dev.reference()
        f.result()

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

    def test_stop(self):
        self.dev.moveAbs({'x':0.00, 'y':-0.0002, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0})
        time.sleep(0.5)
        logging.debug(self.dev.position.value)
        self.dev.stop()

    def test_move_abs(self):
        self.dev.SetSpeed(0.1)

        pos1 = {'x': 0, 'y': 0, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}
        pos2 = {'x':-0.0102, 'y': 0, 'z': 0.0, 'theta_x': 2.0, 'theta_y': 0, 'theta_z': 0}
        pos3 = {'x': 0.0102, 'y':-0.00002, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}

        self.dev.moveAbs(pos1).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        self.dev.moveAbs(pos2).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        self.dev.moveAbs(pos3).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos3, **COMP_ARGS)
        logging.debug(self.dev.position.value)

    def test_move_cancel(self):
        self.dev.SetSpeed(0.1)
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}).result()
        new_pos = {'x':-0.0102, 'y': 0, 'z': 0.0007, 'theta_x': 0.01, 'theta_y': 0.005, 'theta_z': 0.478}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.1)
        f.cancel()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

    def test_move_rel(self):
        self.dev.SetSpeed(0.1)
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 0.01, 'y': 0, 'z': 0, 'theta_x': 0.001, 'theta_y': 0, 'theta_z': 0}
        self.dev.moveRel(shift).result()
        new_pos = self.dev.position.value

        test.assert_pos_almost_equal(smartpod.add_coord(old_pos, shift), new_pos, **COMP_ARGS)

        # Test several relative moves and ensure they are queued up.
        old_pos = self.dev.position.value
        shift = {'x': 0.00001, 'y': 0.00001, 'z':-0.000001, 'theta_x': 0.00001, 'theta_y':-0.000001, 'theta_z':-0.00001}
        self.dev.moveRel(shift)
        self.dev.moveRel(shift)
        self.dev.moveRel(shift).result()

        new_pos = smartpod.add_coord(smartpod.add_coord(smartpod.add_coord(old_pos, shift), shift), shift)
        test.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)
