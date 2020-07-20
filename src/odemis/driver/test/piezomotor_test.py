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

        axes = {'x': {'axis_number': 1, 'speed': 0.001}}
        self.stage = PMD401Bus('test', 'test', port, axes)

    def test_simple(self):
        self.stage.moveRel({'x': 0.001})  # referencing doesn't work at position 0
        self.stage.reference({'x'}).result()

        self.stage._closed_loop['x'] = False
        self.stage.moveAbsSync({'x': 0.01})
        self.stage.moveAbsSync({'x': 0})
        self.stage._closed_loop['x'] = True
        self.stage.moveAbsSync({'x': 0.01})
        self.stage.moveAbsSync({'x': 0})

        self.stage._closed_loop['x'] = False
        self.stage.moveRelSync({'x': 0.01})
        self.stage.moveRelSync({'x': -0.01})
        self.stage._closed_loop['x'] = True
        self.stage.moveRelSync({'x': 0.01})
        self.stage.moveRelSync({'x': -0.01})

    def test_range(self):
        with self.assertRaises(ValueError):
            self.stage.moveAbsSync({'x': 2.4})

    def test_queued(self):
        self.stage._closed_loop['x'] = True
        self.stage.moveRel({'x': 0.001})  # referencing doesn't work at position 0
        self.stage.reference({'x'})
        self.stage.moveRel({'x': 0.01})
        self.stage.moveRel({'x': -0.01})
        self.stage.moveAbs({'x': 0.01})
        f = self.stage.moveAbs({'x': 0})
        f.result()
        self.assertTrue(f.done())

        self.stage._closed_loop['x'] = False
        self.stage.moveRel({'x': 0.001})  # referencing doesn't work at position 0
        self.stage.reference({'x'})
        self.stage.moveRel({'x': 0.01})
        self.stage.moveRel({'x': -0.01})
        self.stage.moveAbs({'x': 0.01})
        f = self.stage.moveAbs({'x': 0})
        f.result()
        self.assertTrue(f.done())

    def test_stop(self):
        # Big move
        move = {'x': 1e-3}
        self.stage.moveRel(move)
        # don't wait
        move = {'x': -0.01}
        self.stage.moveRel(move)
        move = {'x': 0}
        self.stage.moveAbs(move)

        self.stage.stop()
        self.assertEqual(self.stage._executor._queue, deque([]))


if __name__ == '__main__':
    unittest.main()
