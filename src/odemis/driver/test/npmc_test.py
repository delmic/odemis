#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on Jun 21, 2018

@author: Anders Muskens

Copyright © 2018 Anders Muskens, Delmic

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
from odemis.driver import npmc
import odemis.model as model
from odemis.util import testing
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# arguments used for the creation of basic components
CONFIG = {"name": "Delay Stage",
          "role": "delay-stage",
          "port": "/dev/ttyUSB0",  # Adjust for the real hardware
          "axes": {
                'x': {
                    'number': 1,
                    'range': [0, 0.604644],
                    'unit': 'm',
                    'conv_factor': 1000,  # from internal unit mm to unit m
                },
           },
}

COMP_ARGS = {
    "atol": 1e-3,
    "rtol": 1e-3,
    }

# arguments used for the creation of basic components
if TEST_NOHW:
    CONFIG["port"] = "/dev/fake"
    CONFIG["axes"]['z'] = {
                    'number': 3,
                    'range': [0, 3.14],
                    'unit': 'rad',
                    'conv_factor': 10000,  # from internal unit mm to unit rad
                }


def add_coord(pos1, pos2):
    """
    Adds two coordinate dictionaries together and returns a new coordinate dictionary.
    pos1: dict (axis name str) -> (float)
    pos2: dict (axis name str) -> (float)
    Returns ret
        dict (axis name str) -> (float)
    """
    ret = pos1.copy()
    for an, v in pos2.items():
        ret[an] += v

    return ret


def subtract_coord(pos1, pos2):
    """
    Subtracts two coordinate dictionaries together and returns a new coordinate dictionary.
    pos1: dict (axis name str) -> (float)
    pos2: dict (axis name str) -> (float)
    Returns ret
        dict (axis name str) -> (float)
    """
    ret = pos1.copy()
    for an, v in pos2.items():
        ret[an] -= v

    return ret


class TestNPMC(unittest.TestCase):
    """
    Tests cases for the Newport ESP 301 actuator driver
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = npmc.ESP(**CONFIG)
        cls.start_position = cls.dev.position.value

    @classmethod
    def tearDownClass(cls):
        cls.dev.updateMetadata({model.MD_POS_COR: {'x': 0}})
        cls.dev.terminate()  # free up socket.

    def test_simple(self):
        """
        Test just that the connection worked
        """
        self.assertIn("x", self.dev.axes)
        self.assertIn("x", self.dev.position.value)
        self.assertIn("ESP", self.dev.swVersion)

    def test_position_abs(self):
        """
        Test moving to an absolute position.
        """
        exp_pos = self.dev.position.value.copy()
        for pos in (0, 0.1, 0.15, 0.5, 0):
            new_pos = {'x': pos}
            f = self.dev.moveAbs(new_pos)
            f.result()
            exp_pos["x"] = pos
            testing.assert_pos_almost_equal(self.dev.position.value, exp_pos, **COMP_ARGS)

    def test_position_rel(self):
        """
        Test moving to a relative position.
        """
        # Start at the origin to prevent hitting the ends
        self.dev.moveAbs({'x': 0.3}).result()

        # Test another relative move
        for shift in (0, 0.12, -0.12):
            new_shift = {'x': shift}
            old_pos = self.dev.position.value
            f = self.dev.moveRel(new_shift)
            f.result()
            testing.assert_pos_almost_equal(add_coord(old_pos, new_shift), self.dev.position.value, **COMP_ARGS)

    def _getRealPosition(self):
        """
        Gets the real position from the controller itself. (no offset, but with a conversion factor)
        returns:
            dict of axis name str -> float
        """
        real_pos = {}
        dev = self.dev

        for ax_n, i in dev._axis_map.items():
            real_pos[ax_n] = dev.GetPosition(i) / dev._axis_conv_factor[i]

        return real_pos

    def test_offset(self):
        # Start near the origin
        self.dev.moveAbs({'x': 0.2}).result()
        logging.info("POSITION = %s", self.dev.position.value)

        # Bad offset
        with self.assertRaises(ValueError):
            self.dev.updateMetadata({model.MD_POS_COR: 5})

        # Test offset #1
        old_pos = self.dev.position.value
        offset_1 = {'x': 0.05}
        self.dev.updateMetadata({model.MD_POS_COR: offset_1})
        testing.assert_pos_almost_equal(subtract_coord(old_pos, offset_1), self.dev.position.value, **COMP_ARGS)

        # Test offset #2
        old_pos = self._getRealPosition()
        offset_2 = {'x':0.3}
        self.dev.updateMetadata({model.MD_POS_COR: offset_2})
        exp_pos = subtract_coord(old_pos, offset_2)
        testing.assert_pos_almost_equal(exp_pos, self.dev.position.value, **COMP_ARGS)

        # now move to the origin
        new_pos = {'x': 0}
        f = self.dev.moveAbs(new_pos)
        f.result()
        exp_pos = self.dev.position.value.copy()
        exp_pos["x"] = 0
        testing.assert_pos_almost_equal(exp_pos, self.dev.position.value, **COMP_ARGS)
        exp_real = self.dev.position.value.copy()
        exp_real.update(offset_2)
        testing.assert_pos_almost_equal(exp_real, self._getRealPosition(), **COMP_ARGS)

        # Remove the offset
        self.dev.updateMetadata({model.MD_POS_COR: {'x': 0}})

    def test_error_handling(self):
        """
        Test the error handling when it hits a limit
        """
        with self.assertRaises(ValueError):
            self.dev.moveAbs({'x':-1000}).result()

        with self.assertRaises(npmc.ESPError):
            self.dev.LockKeypad(500)
            self.dev.checkError()

    def test_cancellation(self):
        """
        Test queuing up moves and cancelling
        """
        # start at origin
        self.dev.moveAbs({'x': 0}).result()

        for pos in (0.4, 0.5):
            new_pos = {'x': pos}
            f = self.dev.moveAbs({'x': 0.15})
            time.sleep(0.01)
            f.cancel()

            # make sure the position is not the new position
            difference = new_pos['x'] - self.dev.position.value['x']
            self.assertNotEqual(round(difference, 4), 0)

    def test_multimove(self):
        """
        Test running multimoves
        """

        # start at origin
        self.dev.moveAbs({'x': 0}).result()
        orig_pos = self.dev.position.value

        f1 = self.dev.moveRel({'x': 0.15})
        time.sleep(0.02)
        f2 = self.dev.moveRel({'x':-0.15})

        f2.result()
        testing.assert_pos_almost_equal(orig_pos, self.dev.position.value, **COMP_ARGS)

    def test_reference(self):
        """
        Test referencing
        """

        # Test cancellation of referencing
        self.dev.updateMetadata({model.MD_POS_COR: {'x': 0}})
        self.dev.moveAbs({'x': 0.5}).result()
        f = self.dev.reference({'x'})
        time.sleep(0.01)
        f.cancel()
        self.assertFalse(self.dev.referenced.value['x'])
        self.assertNotEqual(self.dev.position.value['x'], 0)

        # TODO: NOTE!!! If homing aborts, you MUST run referencing again. Otherwise,
        # the controller will be stuck and won't do anything!

        # Test proper referencing
        self.assertFalse(self.dev.referenced.value['x'])
        self.dev.reference({'x'}).result()
        self.assertTrue(self.dev.referenced.value['x'])
        # The new position should be the origin (0)
        self.assertAlmostEqual(self.dev.position.value["x"], 0, places=3)


if __name__ == "__main__":
    unittest.main()
