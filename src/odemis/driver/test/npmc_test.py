#!/usr/bin/env python
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

from __future__ import division

import logging
from odemis.driver import npmc
import odemis.model as model
from odemis.util import test
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

C = 2.99792458e11  # speed of light in mm/s

# arguments used for the creation of basic components
CONFIG = {"name": "Delay Stage",
          "role": "delay-stage",
          "port": "/dev/fake",
          # "port": "/dev/ttyUSB0", Use this for the real hardware
          "axes": {
                'x': {
                    'number': 1,
                    'range': [-1, 1],
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


def add_coord(pos1, pos2):
    """
    Adds two coordinate dictionaries together and returns a new coordinate dictionary.
    pos1: dict (axis name str) -> (float)
    pos2: dict (axis name str) -> (float)
    Returns ret
        dict (axis name str) -> (float)
    """
    ret = {}
    for an, v in pos1.items():
        if an in pos2.keys():
            ret[an] = v + pos2[an]

    return ret


def subtract_coord(pos1, pos2):
    """
    Subtracts two coordinate dictionaries together and returns a new coordinate dictionary.
    pos1: dict (axis name str) -> (float)
    pos2: dict (axis name str) -> (float)
    Returns ret
        dict (axis name str) -> (float)
    """
    ret = {}
    for an, v in pos1.items():
        if an in pos2.keys():
            ret[an] = v - pos2[an]

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
        cls.dev.moveAbs(cls.start_position)
        cls.dev.terminate()  # free up socket.

    def test_position_abs(self):
        """
        Test moving to an absolute position.
        """

        for pos in (0, 0.1, 0.15, -0.1, 0):
            new_pos = {'x': pos}
            f = self.dev.moveAbs(new_pos)
            f.result()
            test.assert_pos_almost_equal(self.dev.position.value, new_pos, **COMP_ARGS)

    def test_position_rel(self):
        """
        Test moving to a relative position.
        """

        # Start at the origin to prevent hitting the ends
        self.dev.moveAbs({'x': 0}).result()

        # Test another relative move
        for shift in (0, 0.12, -0.12):
            new_shift = {'x': shift}
            old_pos = self.dev.position.value
            f = self.dev.moveRel(new_shift)
            f.result()
            test.assert_pos_almost_equal(add_coord(old_pos, new_shift), self.dev.position.value, **COMP_ARGS)

    def test_offset(self):
        # Start at the origin to prevent hitting the ends
        self.dev.moveAbs({'x': 0}).result()

        # Bad offset
        with self.assertRaises(ValueError):
            self.dev.updateMetadata({model.MD_POS_COR: 5})

        # Test offset #1
        old_pos = self.dev.position.value
        offset_1 = {'x': 0.05}
        self.dev.updateMetadata({model.MD_POS_COR: offset_1})
        test.assert_pos_almost_equal(subtract_coord(old_pos, offset_1), self.dev.position.value, **COMP_ARGS)

        # Test offset #2
        old_pos = self.dev.GetRealPosition()
        offset_2 = {'x':-0.05}
        self.dev.updateMetadata({model.MD_POS_COR: offset_2})
        test.assert_pos_almost_equal(subtract_coord(old_pos, offset_2), self.dev.position.value, **COMP_ARGS)

        # now move to the origin
        new_pos = {'x': 0}
        f = self.dev.moveAbs(new_pos)
        f.result()
        test.assert_pos_almost_equal(offset_2, self.dev.GetRealPosition(), **COMP_ARGS)

        # Remove the offset
        self.dev.updateMetadata({model.MD_POS_COR: {'x': 0}})

    def test_error_handling(self):
        """
        Test the error handling when it hits a limit
        """
        with self.assertRaises(npmc.ESPError):
            self.dev.moveAbs({'x': 1}).result()
        self.dev.moveAbs({'x': 0}).result()

    def test_cancellation(self):
        """
        Test queuing up moves and cancelling
        """
        # start at origin
        self.dev.moveAbs({'x': 0}).result()

        for pos in (0.15, -0.15, 0.2, -0.1):
            new_pos = {'x': pos}
            f = self.dev.moveAbs({'x': 0.15})
            time.sleep(0.02)
            f.cancel()

            # make sure the posiiton is not the new position
            difference = new_pos['x'] - self.dev.position.value['x']
            self.assertNotEqual(round(difference, 4), 0)

    def test_multimove(self):
        """
        Test running multimoves
        """

        # start at origin
        self.dev.moveAbs({'x': 0}).result()

        f1 = self.dev.moveRel({'x': 0.15})
        time.sleep(0.02)
        f2 = self.dev.moveRel({'x':-0.15})

        f2.result()
        test.assert_pos_almost_equal({'x': 0}, self.dev.position.value, **COMP_ARGS)


if __name__ == "__main__":
    unittest.main()

