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
from odemis.driver import smaract
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

CONFIG_3DOF = {"name": "3DOF",
        "role": "stage",
        "ref_on_init": True,
        "locator": "network:sn:MCS2-00001604",
        "speed": 0.1,
        "accel": 0.001,
        "axes": {
            'x': {
                'range': [-3e-3, 3e-3],
                'unit': 'm',
                'channel': 0,
            },
            'y': {
                'range': [-3e-3, 3e-3],
                'unit': 'm',
                'channel': 1,
            },
            'z': {
                'range': [-3e-3, 3e-3],
                'unit': 'm',
                'channel': 2,
            },
        },
}

if TEST_NOHW:
    CONFIG_3DOF['locator'] = 'fake'


class TestTMCS2(unittest.TestCase):
    """
    Tests cases for the SmarAct MCS2 controller
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = smaract.MCS2(**CONFIG_3DOF)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def test_out_of_range(self):
        """
        Test sending a position that is out of range.
        """
        pos = {'x': 1.5, 'y': 20, 'z': 0}
        with self.assertRaises(ValueError):
            self.dev.moveAbs(pos).result()

    def test_move_abs(self):
        pos1 = {'x': 0, 'y': 0, 'z': 0}
        pos2 = {'x':0, 'y': 0, 'z': 0}
        pos3 = {'x': 0, 'y': 0, 'z': 1e-3}

        self.dev.moveAbs(pos1).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        self.dev.moveAbs(pos2).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        self.dev.moveAbs(pos3).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos3, **COMP_ARGS)
        logging.debug(self.dev.position.value)

    def test_move_cancel(self):
        # Test cancellation by cancelling the future
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0}).result()
        new_pos = {'x':1e-3, 'y': 0, 'z': 1e-3}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.05)
        f.cancel()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

        # Test cancellation by stopping
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0}).result()
        new_pos = {'x':3e-3, 'y': 1e-3, 'z': 0.0007}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.05)
        self.dev.stop()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

    def test_move_rel(self):
        # Test relative moves
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 1e-3, 'y':-1e-3}
        self.dev.moveRel(shift).result()
        new_pos = self.dev.position.value

        test.assert_pos_almost_equal(smaract.add_coord(old_pos, shift), new_pos, **COMP_ARGS)

        # Test several relative moves and ensure they are queued up.
        old_pos = self.dev.position.value
        shift = {'z':-0.000001}
        self.dev.moveRel(shift)
        self.dev.moveRel(shift)
        self.dev.moveRel(shift).result()

        new_pos = smaract.add_coord(smaract.add_coord(smaract.add_coord(old_pos, shift), shift), shift)
        test.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)
