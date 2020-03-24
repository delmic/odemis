#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
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
'''
from __future__ import division, print_function

import logging
import os
import unittest
from odemis.model import Axis
from odemis.driver.piezomotor import PMD401Bus
from collections import deque

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

class TestPMD401(unittest.TestCase):
    """
    Test the PMD401 class.
    """

    def setUp(self):
        if TEST_NOHW:
            port = "/dev/fake"
        else:
            port = "/dev/ttyUSB*"

        axes = {"x": {"axis_number": 1,
                      "mode": 1,
                      'wfm_stepsize': 5e-9},
                "y": {"axis_number": 2,
                      'wfm_stepsize': 5e-9},
                "z": {"axis_number": 3,
                      'wfm_stepsize': 5e-9}
                }
        self.stage = PMD401Bus("PM Control", "stage", port, axes)

    def test_simple(self):
        # For now, just test for any errors, e.g. TimeoutError due to improper handling of the
        # received messages

        # Relative move
        move = {'x': 0.01e-6, 'y': 0.01e-6}
        self.stage.moveRelSync(move)

        # Only one axis, negative value
        move = {'x': -0.01e-6}
        self.stage.moveRelSync(move)

        # Absolute move
        move = {'x': 0.01e-6, 'y': 0.01e-6}
        self.stage.moveAbsSync(move)

    def test_queued(self):
        # Big move
        move = {'x': 1e-6, 'y': 1e-6}
        self.stage.moveRel(move)
        # don't wait
        move = {'x': 0.01e-6, 'y': 0.01e-6}
        self.stage.moveRel(move)
        move = {'x': 0, 'y': 0}
        f = self.stage.moveAbs(move)
        f.result()
        self.assertTrue(f.done())

    def test_stop(self):
        # Big move
        move = {'x': 1e-6, 'y': 1e-6}
        self.stage.moveRel(move)
        # don't wait
        move = {'x': 0.01e-6, 'y': 0.01e-6}
        self.stage.moveRel(move)
        move = {'x': 0, 'y': 0}
        self.stage.moveAbs(move)

        self.stage.stop()
        self.assertEqual(self.stage._executor._queue, deque([]))


if __name__ == '__main__':
    unittest.main()
