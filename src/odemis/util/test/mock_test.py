#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 14 Jul 2025

@author: Éric Piel

Copyright © 2025, Éric Piel, Delmic

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

import unittest
import time

from odemis.util.mock import SimulatedAxis

class TestSimulatedAxis(unittest.TestCase):
    def test_initialization(self):
        axis = SimulatedAxis(position=5, speed=2, rng=(0, 10))
        self.assertEqual(axis.position, 5)
        self.assertEqual(axis.speed, 2)
        self.assertEqual(axis.rng, (0, 10))

    def test_initialization_out_of_range(self):
        axis = SimulatedAxis(position=20, rng=(0, 10))
        self.assertEqual(axis.position, 5)  # center of range

    def test_move_abs_within_range(self):
        axis = SimulatedAxis(position=0, speed=0.1, rng=(0, 10))
        dur = axis.move_abs(0.5)
        self.assertAlmostEqual(dur, 5)
        self.assertTrue(axis.is_moving())

        # During the move
        time.sleep(0.1)
        self.assertTrue(axis.is_moving())

        # After the move
        time.sleep(6.0)
        self.assertFalse(axis.is_moving())
        self.assertTrue(axis.is_at_target())
        self.assertEqual(axis.get_position(), 0.5)

        # Stopping has no effect after reaching target
        axis.stop()
        self.assertFalse(axis.is_moving())
        self.assertTrue(axis.is_at_target())
        self.assertEqual(axis.get_position(), 0.5)

    def test_move_abs_out_of_range(self):
        axis = SimulatedAxis(position=0, speed=10, rng=(0, 10))
        dur = axis.move_abs(15)
        self.assertEqual(axis.get_target_position(), 15)
        self.assertAlmostEqual(dur, 1.5)

        # During the move
        time.sleep(0.1)
        self.assertTrue(axis.is_moving())

        # After the move
        time.sleep(2.0)
        self.assertFalse(axis.is_moving())
        self.assertFalse(axis.is_at_target())
        self.assertEqual(axis.get_position(), 10)
        self.assertEqual(axis.get_target_position(), 10)  # as the current position

    def test_move_rel(self):
        axis = SimulatedAxis(position=2, speed=2, rng=(0, 10))
        dur = axis.move_rel(4)
        self.assertAlmostEqual(dur, 2)

        # After the move
        time.sleep(2.5)
        self.assertFalse(axis.is_moving())
        self.assertTrue(axis.is_at_target())
        self.assertEqual(axis.get_position(), 6)

    def test_stop(self):
        axis = SimulatedAxis(position=0, speed=1, rng=(0, 10))
        axis.move_abs(10)
        time.sleep(0.1)  # Allow some time for the move to start

        axis.stop()
        self.assertFalse(axis.is_moving())
        self.assertFalse(axis.is_at_target())
        self.assertTrue(0 < axis.get_position() < 10)


if __name__ == '__main__':
    unittest.main()
