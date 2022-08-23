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
import logging
import math
from odemis.driver import smaract
from odemis.util import testing
import os
import pickle
import time
import unittest

import odemis.model as model

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

COMP_ARGS = {
    "atol": 1e-7,
    "rtol": 1e-5,
    }

CONFIG_SMARTPOD = {"name": "SmarPod",
        "role": "test",
        # "locator": "usb:sn:MCS2-00001614",
        "locator": "network:sn:MCS2-00010357",
        # "hwmodel": 10074,  # CLS-32.17.1.D-S
        "hwmodel": 10077,  # CLS-32.1-D-SC
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
                'range': [-0.35, 0.35],
                'unit': 'rad',
            },
            'ry': {
                'range': [-0.35, 0.35],
                'unit': 'rad',
            },
            'rz': {
                'range': [-0.61, 0.61],
                'unit': 'rad',
            },
        },
        "ref_on_init": False,
        "speed": 0.004,  # m/s
        "accel": 0.004,  # m/s²
        "hold_time": 1,  # s
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
        # Same are ref_on_init, but blocks until the referencing is done
        cls.dev.reference(cls.dev.axes.keys()).result()

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def test_simple(self):
        print(self.dev.axes)

    def test_exception_pickling(self):
        """
        Check the exception can be pickled and unpickled (for Pyro4)
        """
        ex = smaract.SmarPodError(3)
        p = pickle.dumps(ex)
        ep = pickle.loads(p)
        self.assertIsInstance(ep, smaract.SmarPodError)

    def test_reference_cancel(self):
        # Test canceling referencing
        f = self.dev.reference(self.dev.axes.keys())
        time.sleep(0.1)
        f.cancel()

        for a, i in self.dev.referenced.value.items():
            self.assertFalse(i)

        # Run the referencing normally, it should work again
        f = self.dev.reference(self.dev.axes.keys())
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
        pos2 = {'x':-0.00102, 'y': 0, 'z': 0.0, 'rx': 0.0001, 'ry': 0.0001, 'rz': 0}
        pos3 = {'x': 0.00102, 'y':-0.00002, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}
        # test where not all axes are defined
        pos4 = {'x': 1e-3, 'rx': 1e-5, 'ry': 0, 'rz': 0}

        self.dev.moveAbs(pos1).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        self.dev.moveAbs(pos2).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        self.dev.moveAbs(pos3).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos3, **COMP_ARGS)
        self.dev.moveAbs(pos4).result()
        # add missing axes to do the comparison
        pos4['y'] = pos3['y']
        pos4['z'] = pos3['z']
        testing.assert_pos_almost_equal(self.dev.position.value, pos4, **COMP_ARGS)
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
        self.dev.reference(self.dev.axes.keys()).result()

        # Test relative moves
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 2e-3, 'y':-1e-3, 'ry':-0.0003, 'rz': 0}
        self.dev.moveRel(shift).result()
        new_pos = self.dev.position.value

        testing.assert_pos_almost_equal(smaract.add_coord(old_pos, shift), new_pos, **COMP_ARGS)

        # Test several relative moves and ensure they are queued up.
        old_pos = self.dev.position.value
        shift = {'x':-100e-6, 'z':-10e-6, 'rx': 0.0001, 'rz':-0.01}
        self.dev.moveRel(shift)
        self.dev.moveRel(shift)
        self.dev.moveRel(shift).result()

        new_pos = smaract.add_coord(smaract.add_coord(smaract.add_coord(old_pos, shift), shift), shift)
        testing.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)

    def test_pivot_set(self):
        # Check that the pivot position is available from the beginning
        old_pivot = self.dev.getMetadata()[model.MD_PIVOT_POS]
        self.assertEqual(old_pivot.keys(), {"x", "y", "z"})

        try:
            # Test setting the pivot to some value through metadata
            old_pos = self.dev.position.value
            new_pivot = {'x': 0.005, 'y': 0.005, 'z': 0.001}
            self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
            self.dev.moveRelSync({"x": 0})  # WARNING: this can cause a move!
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)

            old_pos = self.dev.position.value
            new_pivot = {'x': 0.001, 'y':-0.005, 'z': 0.001}
            self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
            self.dev.moveRelSync({"x": 0})  # WARNING: this can cause a move!
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
        finally:
            self.dev.updateMetadata({model.MD_PIVOT_POS: old_pivot})
            self.dev.moveRelSync({"x": 0})


CONFIG_5DOF = {"name": "5DOF",
        "role": "stage",
        "ref_on_init": True,
        "linear_speed": 0.001,  # m/s
        "rotary_speed": 0.001,  # rad/s
        "locator": "network:sn:MCS2-00001602",
        # "locator": "fake",
        "hold_time": 5,  # s
        "settle_time": 1,  # s
        "pos_deactive_after_ref": True,  # Not actually used as there is no MD_FAV_POS_DEACTIVE
        "inverted": ['z'],
        "axes": {
            'x': {
                'range': [-1.6e-2, 1.6e-2],
                'unit': 'm',
            },
            'y': {
                'range': [-1.5e-2, 1.5e-2],
                'unit': 'm',
            },
            'z': {
                'range': [-1.e-2, 0.002],
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

        while not cls.dev.referenced.value:
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def test_driver_software_version(self):
        """
        Checks whether the driver software version is valid
        """
        self.assertNotIn("Unknown (Odemis", self.dev.swVersion)

    def test_reference_cancel(self):

        # TODO: Still fails
        # Test canceling referencing
        f = self.dev.reference({"x"})
        time.sleep(0.1)
        f.cancel()

        for a, i in self.dev.referenced.value.items():
            self.assertFalse(i)

        f = self.dev.reference({"x"})
        f.result()

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

    def test_inverted(self):
        """
        The z axis is set to inverted.
        Determine if it is handled correctly.
        """
        pos1 = {'x': 0, 'y': 0, 'z':-1e-3, 'rx': 0, 'rz': 0}
        self.dev.moveAbs(pos1).result()

        pos_internal = self.dev.GetPose().asdict()
        self.assertAlmostEqual(pos_internal['z'], -pos1['z'])

    def test_out_of_range(self):
        """
        Test sending a position that is out of range.
        """
        pos = {'x': 1.5, 'y': 20, 'z': 0, 'rx': 0, 'rz': 0.0005}
        with self.assertRaises(ValueError):
            self.dev.moveAbs(pos).result()

    def test_unreachable_position_error(self):
        edge_move = {'x': 1.6e-2, 'y': 1.5e-2, 'z': -0.002, 'rx': 0, 'rz': 0}
        rot_move = {'rx': 0.001, 'rz': 0.001}
        zero_move = {'x': 0, 'y': 0, 'z': 0}

        # move the stage to the maximum range
        self.dev.moveAbs(edge_move).result()
        testing.assert_pos_almost_equal(self.dev.position.value, edge_move, match_all=False)
        # moving rx/rz would throw unreachable move exception
        with self.assertRaises(IndexError):
            self.dev.moveAbs(rot_move).result()
        # moving all linear axes from range then moving rx/rz would be fine
        self.dev.moveAbs(zero_move).result()
        testing.assert_pos_almost_equal(self.dev.position.value, zero_move, match_all=False)
        self.dev.moveAbs(rot_move).result()
        testing.assert_pos_almost_equal(self.dev.position.value, rot_move, match_all=False)

    def test_move_abs(self):
        pos1 = {'x': 0, 'y': 0, 'z': 0, 'rx': 0.001, 'rz': 0.001}
        pos2 = {'x':0, 'y': 0, 'z': 0, 'rx': 0, 'rz':0}
        pos3 = {'x': 3.5218e-4, 'y': 1.785e-5, 'z': 1e-3, 'rx':-1e-6, 'rz':-1.253e-6}
        # test where not all axes are defined
        pos4 = {'x': 1e-3, 'rx': 1e-5, 'rz': 0}

        self.dev.moveAbs(pos1).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        self.dev.moveAbs(pos2).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        self.dev.moveAbs(pos3).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos3, **COMP_ARGS)
        self.dev.moveAbs(pos4).result()
        # add missing axes to do the comparison
        pos4['y'] = pos3['y']
        pos4['z'] = pos3['z']
        testing.assert_pos_almost_equal(self.dev.position.value, pos4, **COMP_ARGS)

    def test_move_update_position(self):
        """
        Test to make sure the system updates the position as it moves
        """
        pos1 = {'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}
        pos2 = {'x': 2e-3, 'y': 2e-3, 'z': 2e-3, 'rx': 3e-4, 'rz':-1e-4}
        self.dev.moveAbs(pos1).result()
        time.sleep(0.1)
        f = self.dev.moveAbs(pos2)
        # wait and see if the position updated midway through the move. Should take 3 s in sim
        time.sleep(1.0)
        testing.assert_pos_not_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        testing.assert_pos_not_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        f.result()

    def test_move_cancel(self):
        # Test cancellation by cancelling the future
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}).result()
        new_pos = {'x': 0.003, 'y': 0, 'z': 0.0007, 'rx': 0.001, 'rz': 0.002}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.01)
        f.cancel()

        difference = new_pos['x'] - self.dev.position.value['x']
        self.assertNotEqual(round(difference, 4), 0)

        # Test cancellation by stopping
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}).result()
        new_pos = {'x': 0.003, 'y': 0, 'z': 0.0007, 'rx': 0.01, 'rz': 0.0001}
        f = self.dev.moveAbs(new_pos)
        time.sleep(0.1)
        self.dev.stop()

        testing.assert_pos_not_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)

    def test_move_rel(self):
        # Test relative moves
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 1e-3, 'y':-1e-3, 'rz': 0}
        self.dev.moveRel(shift).result()
        new_pos = self.dev.position.value

        testing.assert_pos_almost_equal(smaract.add_coord(old_pos, shift), new_pos, **COMP_ARGS)

        # Test several relative moves and ensure they are queued up.
        old_pos = self.dev.position.value
        shift = {'z':-0.000001, 'rx': 0.00001, 'rz':-0.00001}
        self.dev.moveRel(shift)
        self.dev.moveRel(shift)
        self.dev.moveRel(shift).result()

        new_pos = smaract.add_coord(smaract.add_coord(smaract.add_coord(old_pos, shift), shift), shift)
        testing.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)

    def test_pivot_set(self):
        # Check that the pivot position is available from the beginning
        old_pivot = self.dev.getMetadata()[model.MD_PIVOT_POS]
        try:
            # Test setting the pivot to some value through metadata
            old_pos = self.dev.position.value
            new_pivot = {'x': 0.05, 'y': 0.05, 'z': 0.01}
            self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
            self.dev.moveRelSync({"x": 0})  # WARNING: this can cause a move!
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)

            old_pos = self.dev.position.value
            new_pivot = {'x': 0.01, 'y':-0.05, 'z': 0.01}
            self.dev.updateMetadata({model.MD_PIVOT_POS: new_pivot})
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
            self.dev.moveRelSync({"x": 0})  # WARNING: this can cause a move!
            testing.assert_pos_almost_equal(old_pos, self.dev.position.value, **COMP_ARGS)
        finally:
            self.dev.updateMetadata({model.MD_PIVOT_POS: old_pivot})
            self.dev.moveRelSync({"x": 0})

    def test_reference_and_deactivate_move(self):
        # Set a deactive position and check to be sure that the controller moves to this location
        # after a reference move
        de_pos = {'x': 3.5801e-4, 'y': 0, 'z': 1e-3, 'rx':-1.2e-6, 'rz': 0.0}
        self.dev.updateMetadata({model.MD_FAV_POS_DEACTIVE: de_pos})

        f = self.dev.reference({"x"})
        f.result()

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

        testing.assert_pos_almost_equal(self.dev.position.value, de_pos, **COMP_ARGS)

    def test_move_and_settle(self):
        pos1 = {'x': 0, 'y': 0}
        pos2 = {'x': 1e-3, 'y': 1e-5}

        # Check that it's faster to move 2x in a raw, than waiting after the move,
        # because the settle time takes extra time between each move.
        self.dev.moveAbs(pos1).result()
        start_t = time.time()
        self.dev.moveAbs(pos2).result()
        self.dev.moveAbs(pos1).result()
        dur_serial = time.time() - start_t

        self.dev.moveAbs(pos1).result()
        start_t = time.time()
        self.dev.moveAbs(pos2)
        self.dev.moveAbs(pos1).result()
        dur_same_time = time.time() - start_t

        # Serialized move should take 1s more. To take into account "flux", we check it's at least 0.5s.
        self.assertGreater(dur_serial, dur_same_time + 0.5)


CONFIG_3DOF = {"name": "3DOF",
        "role": "stage",
        "ref_on_init": True,
        "locator": "network:sn:MCS2-00001604",
        # "locator": "fake",
        "speed": 0.01,
        "accel": 0.001,
        "hold_time": 1.0,
        "pos_deactive_after_ref": True,
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

        while not cls.dev.referenced.value:
            time.sleep(0.1)

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
        testing.assert_pos_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        self.dev.moveAbs(pos2).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        self.dev.moveAbs(pos3).result()
        testing.assert_pos_almost_equal(self.dev.position.value, pos3, **COMP_ARGS)
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

        testing.assert_pos_almost_equal(smaract.add_coord(old_pos, shift), new_pos, **COMP_ARGS)

        # Test several relative moves and ensure they are queued up.
        old_pos = self.dev.position.value
        shift = {'z':-0.000001}
        self.dev.moveRel(shift)
        self.dev.moveRel(shift)
        self.dev.moveRel(shift).result()

        new_pos = smaract.add_coord(smaract.add_coord(smaract.add_coord(old_pos, shift), shift), shift)
        testing.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)

    def test_reference_cancel(self):
        """Test canceling referencing"""
        axes = set(self.dev.axes.keys())
        # First, reference it
        f = self.dev.reference(axes)
        f.result()
        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

        f = self.dev.reference(axes)
        time.sleep(0.1)
        f.cancel()

        for a, i in self.dev.referenced.value.items():
            self.assertFalse(i)

    def test_reference(self):
        axes = set(self.dev.axes.keys())
        f = self.dev.reference(axes)
        f.result()

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

        # TODO: some hardware have fancy multi-marks, which means that the referencing
        # doesn't necessarily end-up at 0, and everytime the axis is referenced
        # it can end up at a different mark.
        # testing.assert_pos_almost_equal(self.dev.position.value, {'x': 0, 'y': 0, 'z': 0}, **COMP_ARGS)

        # Try again, after a move
        shift = {'x': 1e-3, 'y':-1e-3}
        self.dev.moveRel(shift).result()
        pos_move = dict(self.dev.position.value)

        f = self.dev.reference(axes)
        f.result()
        pos_refd = dict(self.dev.position.value)

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

        # Check that at least the position changed
        self.assertNotEqual(pos_move["x"], pos_refd["x"])
        # testing.assert_pos_almost_equal(self.dev.position.value, {'x': 0, 'y': 0, 'z': 0}, **COMP_ARGS)

    def test_reference_and_deactivate_move(self):
        # Set a deactive position and check to be sure that the controller moves to this location
        # after a reference move
        de_pos = {'x':0, 'y':-1.2e-4, 'z': 0}
        self.dev.updateMetadata({model.MD_FAV_POS_DEACTIVE: de_pos})

        f = self.dev.reference(set(self.dev.axes.keys()))
        f.result()

        testing.assert_pos_almost_equal(self.dev.position.value, de_pos, **COMP_ARGS)

    def test_auto_update_function(self):
        self.dev.moveAbs({"x": 0.0, "y": 0.0, "z": 0.0}).result()
        pos_before_move = self.dev.position.value
        rel_move = {"z": 5e-6}
        expected_pos = pos_before_move.copy()
        expected_pos["z"] += rel_move["z"]
        self.dev.moveRel(rel_move).result()
        # the position updater function is called every 1 sec, wait a bit more.
        time.sleep(2)
        pos_after_move = self.dev.position.value
        testing.assert_pos_almost_equal(expected_pos, pos_after_move, **COMP_ARGS)

CONFIG_Picoscale = {"name": "Stage Metrology",
                    "role": "metrology",
                    "ref_on_init": True,
                    "locator": "network:sn:PSC-00000178",
                    "channels": {'x1': 0, 'x2': 1},
                    "precision_mode": 0,
                    }


if TEST_NOHW:
    CONFIG_Picoscale['locator'] = 'fake'


class TestPicoscale(unittest.TestCase):
    """
    Test cases for the SmarAct Picoscale interferometer.
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = smaract.Picoscale(**CONFIG_Picoscale)
        # Wait until initialization is done
        while cls.dev.state.value == model.ST_STARTING:
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()

    def test_reference(self):
        f = self.dev.reference()
        f.result()

        for a, i in self.dev.referenced.value.items():
            self.assertTrue(i)

    def test_position(self):
        """
        Tests whether the position is updated every second.
        """
        self.pos_update = False

        def pos_listener(_):
            self.pos_update = True

        self.dev.position.subscribe(pos_listener)
        if TEST_NOHW:
            # New sensor position in simulator
            self.dev.core.positions[0] = 2.5e-6
        time.sleep(1.1)  # position should be updated every second
        self.assertTrue(self.pos_update)

        self.dev.position.unsubscribe(pos_listener)

    def test_reference_cancel(self):
        """
        Test cancelling at various stages of the referencing procedure.
        """
        f = self.dev.reference()
        time.sleep(0.2)
        f.cancel()
        time.sleep(0.1)  # it takes a little while until ._was_stopped is updated
        self.assertTrue(f.cancelled())

        f = self.dev.reference()
        time.sleep(1)
        f.cancel()
        time.sleep(0.1)
        self.assertTrue(f.cancelled())

        logging.debug("Referencing")
        f = self.dev.reference()
        time.sleep(5)
        logging.debug("Will request cancellation")
        f.cancel()
        time.sleep(0.1)
        self.assertTrue(f.cancelled())

        # Test queued futures
        f1 = self.dev.reference()
        f2 = self.dev.reference()
        f1.cancel()
        f2.cancel()
        time.sleep(0.1)
        self.assertEqual(len(self.dev._executor._queue), 0)

        # Test f.cancel()
        f = self.dev.reference()
        time.sleep(1)
        f.cancel()
        time.sleep(0.1)
        self.assertTrue(f.cancelled())
        for a, i in self.dev.referenced.value.items():
            self.assertFalse(i)


if __name__ == '__main__':
    unittest.main()
