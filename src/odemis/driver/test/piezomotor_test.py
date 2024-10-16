#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 18 Mar 2020

@author: Philip Winkler

Copyright © 2020, Philip Winkler, Delmic

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
import odemis
import logging
import os
import unittest
from collections import deque
import time

from odemis.driver.piezomotor import PMD401Bus

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing
TEST_NOHW = 1

if TEST_NOHW:
    PORT = "/dev/fake"
else:
    PORT = "/dev/ttyUSB*"

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
PMD_CONFIG = CONFIG_PATH + "hwtest/pmd401.pmd.tsv"

KWARGS_OPEN = dict(name="test", role="test", port=PORT,
                   axes={'x': {'axis_number': 1, 'speed': 0.001, 'closed_loop': False},
                         'y': {'axis_number': 2, 'speed': 0.001, 'closed_loop': False}},
                   param_file=PMD_CONFIG)

KWARGS_CLOSED = dict(name="test", role="test", port=PORT,
                     axes={'x': {'axis_number': 1, 'speed': 0.001, 'closed_loop': True},
                           'y': {'axis_number': 2, 'speed': 0.001, 'closed_loop': True}},
                     param_file=PMD_CONFIG)


class TestPMD401OpenLoop(unittest.TestCase):
    """
    Test the PMD401 class with open loop functionality.
    """

    def setUp(self):
        self.stage = PMD401Bus(**KWARGS_OPEN)

    def test_simple(self):
        """
        Test referencing, relative and absolute move. Wait for a move to finish before starting
        the next one.
        """
        self.stage.moveRelSync({'x': 0.001})  # start from nonzero position
        self.stage.reference({'x'}).result()
        # Reference twice in a row to check if it works from the limit position
        self.stage.reference({'x'}).result()

        self.stage.moveAbsSync({'x': 0.01})
        self.stage.moveAbsSync({'x': 0})

        self.stage.moveRelSync({'x': 0.01})
        self.stage.moveRelSync({'x': -0.01})

        # Small move
        self.stage.moveRelSync({'x': 1e-6})
        self.stage.moveRelSync({'x': -1e-6})

    def test_range(self):
        """
        Requesting a move outside the range should raise a ValueError.
        """
        with self.assertRaises(ValueError):
            self.stage.moveAbsSync({'x': self.stage.axes['x'].range[1] + 0.1})

    def test_queued(self):
        """
        Run a set of non-blocking moves.
        """
        self.stage.moveRel({'x': 0.001})  # referencing doesn't work at position 0
        self.stage.reference({'x'})
        self.stage.moveRel({'x': 0.01})
        self.stage.moveRel({'x': -0.01})
        self.stage.moveAbs({'x': 0.01})
        f = self.stage.moveAbs({'x': 0})
        f.result()
        self.assertTrue(f.done())

    def test_position(self):
        """
        Check that moving the stage updates the position
        """
        f = self.stage.moveAbs({'x': 0.001})
        f.result()
        self.assertTrue(f.done())
        self.assertEqual(self.stage.position.value["x"], 0.001)
        f = self.stage.moveRel({'x': 0.0001})
        f.result()
        self.assertTrue(f.done())
        self.assertEqual(self.stage.position.value["x"], 0.0011)

    def test_stop(self):
        """
        Test stopping while moving and referencing.
        """
        move = {'x': 1e-3}
        self.stage.moveRel(move)
        self.stage.stop()

        # Queued move
        self.stage.moveRel({'x': -0.01})
        self.stage.moveAbs({'x': 0})
        self.stage.stop()

        self.stage.reference({'x'})
        self.stage.stop()

        # Stop in the middle of the referencing procedure
        self.stage.moveAbsSync({'x': 0.005})  # start from nonzero position
        self.stage.reference({'x'})
        time.sleep(0.1)
        self.stage.stop()

        self.assertEqual(self.stage._executor._queue, deque([]))


class TestPMD401ClosedLoop(TestPMD401OpenLoop):
    """
    Test the PMD401 class with closed loop functionality.
    """

    def setUp(self):
        self.stage = PMD401Bus(**KWARGS_CLOSED)


if __name__ == '__main__':
    unittest.main()
