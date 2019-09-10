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
import odemis.model as model

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

COMP_ARGS = {
    "atol": 1e-3,
    "rtol": 1e-3,
    }

CONFIG_5DOF = {"name": "5DOF",
        "role": "stage",
        "ref_on_init": True,
        "linear_speed": 0.001,  # m/s
        "locator": "network:sn:MCS2-00001602",
        "axes": {
            'x': {
                'range': [-3e-3, 3e-3],
                'unit': 'm',
            },
            'y': {
                'range': [-3e-3, 3e-3],
                'unit': 'm',
            },
            'z': {
                'range': [-3e-3, 3e-3],
                'unit': 'm',
            },
            'rx': {
                'range': [-0.785, 0.785],
                'unit': 'rad',
            },
            'ry': {
                'range': [0,0],
                'unit': 'rad',
            },
            'rz': {
                'range': [-0.785, 0.785],
                'unit': 'rad',
            },
        },
}

if TEST_NOHW:
    CONFIG_5DOF['locator'] = 'fake'


class Test5DOF(unittest.TestCase):
    """
    Tests cases for the SmarAct MC controller
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = smaract.MC_5DOF(**CONFIG_5DOF)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def test_reference_cancel(self):

        # TODO: Still fails
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

    def test_out_of_range(self):
        """
        Test sending a position that is out of range.
        """
        pos = {'x': 1.5, 'y': 20, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0.0005}
        with self.assertRaises(ValueError):
            self.dev.moveAbs(pos).result()

    def test_move_abs(self):
        pos1 = {'x': 0, 'y': 0, 'z': 0, 'rx': 0.001, 'ry': 0, 'rz': 0.001}
        pos2 = {'x':0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz':0}
        pos3 = {'x': 3.5218e-4, 'y': 1.785e-5, 'z': 1e-3, 'rx':-1e-6, 'ry': 0, 'rz':-1.253e-6}
        # test where not all axes are defined
        pos4 = {'x': 1e-3, 'rx': 1e-5, 'ry': 0, 'rz': 0}

        self.dev.moveAbs(pos1).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        self.dev.moveAbs(pos2).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        self.dev.moveAbs(pos3).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos3, **COMP_ARGS)
        self.dev.moveAbs(pos4).result()
        # add missing axes to do the comparison
        pos4['y'] = pos3['y']
        pos4['z'] = pos3['z']
        test.assert_pos_almost_equal(self.dev.position.value, pos4, **COMP_ARGS)
        logging.debug(self.dev.position.value)

    def test_move_cancel(self):
        # Test cancellation by cancelling the future
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}).result()
        new_pos = {'x':0.001, 'y': 0, 'z': 0.0007, 'rx': 0.001, 'ry': 0, 'rz': 0.002}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.05)
        f.cancel()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

        # Test cancellation by stopping
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}).result()
        new_pos = {'x':2e-3, 'y': 0, 'z': 0.0007, 'rx': 0.01, 'ry': 0, 'rz': 0.0001}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.05)
        self.dev.stop()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

    def test_move_rel(self):
        # Test relative moves
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 1e-3, 'y':-1e-3, 'ry': 0, 'rz': 0}
        self.dev.moveRel(shift).result()
        new_pos = self.dev.position.value

        test.assert_pos_almost_equal(smaract.add_coord(old_pos, shift), new_pos, **COMP_ARGS)

        # Test several relative moves and ensure they are queued up.
        old_pos = self.dev.position.value
        shift = {'z':-0.000001, 'rx': 0.00001, 'rz':-0.00001}
        self.dev.moveRel(shift)
        self.dev.moveRel(shift)
        self.dev.moveRel(shift).result()

        new_pos = smaract.add_coord(smaract.add_coord(smaract.add_coord(old_pos, shift), shift), shift)
        test.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)

    def test_pivot_set(self):
        # Test setting the pivot to some value through metadata
        old_pos = self.dev.position.value
        new_pivot = {'x': 0.05, 'y': 0.05, 'z': 0.01}
        self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
        test.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)

        old_pos = self.dev.position.value
        new_pivot = {'x': 0.01, 'y':-0.05, 'z': 0.01}
        self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
        test.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)

