#!/usr/bin/env python3
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
from __future__ import division

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
    "atol": 1e-7,
    "rtol": 1e-5,
    }

CONFIG_SMARTPOD = {"name": "SmarPod",
        "role": "",
        "ref_on_init": True,
        "actuator_speed": 0.1,  # m/s
        "locator": "usb:ix:0",
        "axes": {
            'x': {
                'range': [-0.2, 0.2],
                'unit': 'm',
            },
            'y': {
                'range': [-0.2, 0.2],
                'unit': 'm',
            },
            'z': {
                'range': [-0.1, 0.1],
                'unit': 'm',
            },
            'rx': {
                'range': [-0.785, 0.785],
                'unit': 'rad',
            },
            'ry': {
                'range': [-0.785, 0.785],
                'unit': 'rad',
            },
            'rz': {
                'range': [-0.785, 0.785],
                'unit': 'rad',
            },
        },
}

if TEST_NOHW:
    CONFIG_SMARTPOD['locator'] = 'fake'


class TestSmarPod(unittest.TestCase):
    """
    Tests cases for the SmarPod actuator driver
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = smaract.SmarPod(**CONFIG_SMARTPOD)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

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

    def test_out_of_range(self):
        """
        Test sending a position that is out of range.
        """
        pos = {'x': 1.5, 'y': 20, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0.0005}
        with self.assertRaises(ValueError):
            self.dev.moveAbs(pos).result()

    def test_move_abs(self):
        pos1 = {'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0.0005}
        pos2 = {'x':-0.0102, 'y': 0, 'z': 0.0, 'rx': 0.0001, 'ry': 0.0001, 'rz': 0}
        pos3 = {'x': 0.0102, 'y':-0.00002, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}
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
        new_pos = {'x':0.01, 'y': 0, 'z': 0.0007, 'rx': 0.001, 'ry': 0.005, 'rz': 0.002}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.05)
        f.cancel()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

        # Test cancellation by stopping
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}).result()
        new_pos = {'x':0.0021, 'y': 0, 'z': 0.0007, 'rx': 0.01, 'ry': 0.005, 'rz': 0.0001}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.05)
        self.dev.stop()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

    def test_move_rel(self):
        # Test relative moves
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 0.01, 'y':-0.001, 'ry':-0.0003, 'rz': 0}
        self.dev.moveRel(shift).result()
        new_pos = self.dev.position.value

        test.assert_pos_almost_equal(smaract.add_coord(old_pos, shift), new_pos, **COMP_ARGS)

        # Test several relative moves and ensure they are queued up.
        old_pos = self.dev.position.value
        shift = {'z':-0.000001, 'rx': 0.00001, 'ry':-0.000001, 'rz':-0.00001}
        self.dev.moveRel(shift)
        self.dev.moveRel(shift)
        self.dev.moveRel(shift).result()

        new_pos = smaract.add_coord(smaract.add_coord(smaract.add_coord(old_pos, shift), shift), shift)
        test.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)


CONFIG_5DOF = {"name": "5DOF",
        "role": "stage",
        "ref_on_init": True,
        "linear_speed": 0.001,  # m/s
        "rotary_speed": 0.001,  # rad/s
        "locator": "network:sn:MCS2-00001602",
        # "locator": "fake",
        "hold_time": 5,  # s
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
        pos = {'x': 1.5, 'y': 20, 'z': 0, 'rx': 0, 'rz': 0.0005}
        with self.assertRaises(ValueError):
            self.dev.moveAbs(pos).result()

    def test_move_abs(self):
        pos1 = {'x': 0, 'y': 0, 'z': 0, 'rx': 0.001, 'rz': 0.001}
        pos2 = {'x':0, 'y': 0, 'z': 0, 'rx': 0, 'rz':0}
        pos3 = {'x': 3.5218e-4, 'y': 1.785e-5, 'z': 1e-3, 'rx':-1e-6, 'rz':-1.253e-6}
        # test where not all axes are defined
        pos4 = {'x': 1e-3, 'rx': 1e-5, 'rz': 0}

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

    def test_move_update_position(self):
        """
        Test to make sure the system updates the position as it moves
        """
        pos1 = {'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}
        pos2 = {'x': 3e-3, 'y': 3e-3, 'z': 3e-3, 'rx': 3e-4, 'rz':-1e-4}
        self.dev.moveAbs(pos1).result()
        time.sleep(0.1)
        f = self.dev.moveAbs(pos2)
        # wait and see if the position updated midway through the move. Should take 3 s in sim
        time.sleep(1.0)
        test.assert_pos_not_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        test.assert_pos_not_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        f.result()

    def test_move_cancel(self):
        # Test cancellation by cancelling the future
        # note: this test will fail with the simulator because it does not
        # simulate intermediate positions within a move.
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}).result()
        new_pos = {'x':0.003, 'y': 0, 'z': 0.0007, 'rx': 0.001, 'rz': 0.002}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.01)
        f.cancel()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

        # Test cancellation by stopping
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}).result()
        new_pos = {'x':2e-3, 'y': 0, 'z': 0.0007, 'rx': 0.01, 'rz': 0.0001}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.05)
        self.dev.stop()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

    def test_move_rel(self):
        # Test relative moves
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 1e-3, 'y':-1e-3, 'rz': 0}
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
        # Check that the pivot position is available from the beginning
        old_pivot = self.dev.getMetadata()[model.MD_PIVOT_POS]
        try:
            # Test setting the pivot to some value through metadata
            old_pos = self.dev.position.value
            new_pivot = {'x': 0.05, 'y': 0.05, 'z': 0.01}
            self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
            test.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
            self.dev.moveRelSync({"x": 0})  # WARNING: this can cause a move!
            test.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)

            old_pos = self.dev.position.value
            new_pivot = {'x': 0.01, 'y':-0.05, 'z': 0.01}
            self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
            test.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
            self.dev.moveRelSync({"x": 0})  # WARNING: this can cause a move!
            test.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
        finally:
            self.dev.updateMetadata({model.MD_PIVOT_POS: old_pivot})
            self.dev.moveRelSync({"x": 0})


CONFIG_3DOF = {"name": "3DOF",
        "role": "stage",
        "ref_on_init": True,
        "locator": "network:sn:MCS2-00001604",
        # "locator": "fake",
        "speed": 0.1,
        "accel": 0.001,
        "hold_time": 1.0,
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


class TestMCS2(unittest.TestCase):
    """
    Tests cases for the SmarAct MCS2 controller
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = smaract.MCS2(**CONFIG_3DOF)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def test_simple(self):
        self.assertEqual(set(self.dev.axes.keys()), {"x", "y", "z"})

    def test_out_of_range(self):
        """
        Test sending a position that is out of range.
        """
        pos = {'x': 1.5, 'y': 20, 'z': 0}
        with self.assertRaises(ValueError):
            self.dev.moveAbs(pos).result()

    def test_move_abs(self):
        pos1 = {'x': 0, 'y': 0, 'z': 0}
        pos2 = {'x':0, 'y':-1.2e-4, 'z': 0}
        pos3 = {'x': 0.643e-3, 'y': 0, 'z': 1e-3}

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
        f.cancel()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 5), 0)

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

    def test_reference_cancel(self):
        """Test canceling referencing"""
        # First, reference it
        f = self.dev.reference()
        f.result()
        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

        f = self.dev.reference()
        time.sleep(0.1)
        f.cancel()

        for a, i in self.dev.referenced.value.items():
            self.assertFalse(i)

    def test_reference(self):
        f = self.dev.reference()
        f.result()

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

        # TODO: some hardware have fancy multi-marks, which means that the referencing
        # doesn't necessarily end-up at 0, and everytime the axis is referenced
        # it can end up at a different mark.
        # test.assert_pos_almost_equal(self.dev.position.value, {'x': 0, 'y': 0, 'z': 0}, **COMP_ARGS)

        # Try again, after a move
        shift = {'x': 1e-3, 'y':-1e-3}
        self.dev.moveRel(shift).result()
        pos_move = dict(self.dev.position.value)

        f = self.dev.reference()
        f.result()
        pos_refd = dict(self.dev.position.value)

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

        # Check that at least the position changed
        self.assertNotEqual(pos_move["x"], pos_refd["x"])
        # test.assert_pos_almost_equal(self.dev.position.value, {'x': 0, 'y': 0, 'z': 0}, **COMP_ARGS)


if __name__ == '__main__':
    unittest.main()
