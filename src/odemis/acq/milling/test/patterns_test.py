# -*- coding: utf-8 -*-
"""
@author: Patrick Cleeve

Copyright © 2024, Delmic

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
import logging
import unittest
import numpy
from odemis.acq.milling.patterns import RectanglePatternParameters, TrenchPatternParameters, MicroexpansionPatternParameters

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

class RectanglePatternParametersTestCase(unittest.TestCase):

    def setUp(self):
        self.name = "Rectangle-1"
        self.width = 10e-6
        self.height = 10e-6
        self.depth = 10e-6
        self.rotation = 0
        self.center = (0, 0)
        self.scan_direction = "TopToBottom"

        self.pattern = RectanglePatternParameters(
            name=self.name,
            width=self.width,
            height=self.height,
            depth=self.depth,
            rotation=self.rotation,
            center=self.center,
            scan_direction=self.scan_direction,
        )

    def test_assignment(self):

        # test assignment
        self.assertEqual(self.pattern.name.value, self.name)
        self.assertEqual(self.pattern.width.value, self.width)
        self.assertEqual(self.pattern.height.value, self.height)
        self.assertEqual(self.pattern.depth.value, self.depth)
        self.assertEqual(self.pattern.rotation.value, self.rotation)
        self.assertEqual(self.pattern.center.value, self.center)
        self.assertEqual(self.pattern.scan_direction.value, self.scan_direction)

    def test_dict(self):
        # test to_dict
        rectangle_pattern_dict = self.pattern.to_dict()
        self.assertEqual(rectangle_pattern_dict["name"], self.name)
        self.assertEqual(rectangle_pattern_dict["width"], self.width)
        self.assertEqual(rectangle_pattern_dict["height"], self.height)
        self.assertEqual(rectangle_pattern_dict["depth"], self.depth)
        self.assertEqual(rectangle_pattern_dict["rotation"], self.rotation)
        self.assertEqual(rectangle_pattern_dict["center_x"], 0)
        self.assertEqual(rectangle_pattern_dict["center_y"], 0)
        self.assertEqual(rectangle_pattern_dict["scan_direction"], self.scan_direction)
        self.assertEqual(rectangle_pattern_dict["pattern"], "rectangle")

        # test from_dict
        rectangle_pattern_from_dict = RectanglePatternParameters.from_dict(rectangle_pattern_dict)
        self.assertEqual(rectangle_pattern_from_dict.name.value, self.name)
        self.assertEqual(rectangle_pattern_from_dict.width.value, self.width)
        self.assertEqual(rectangle_pattern_from_dict.height.value, self.height)
        self.assertEqual(rectangle_pattern_from_dict.depth.value, self.depth)
        self.assertEqual(rectangle_pattern_from_dict.rotation.value, self.rotation)
        self.assertEqual(rectangle_pattern_from_dict.center.value, self.center)
        self.assertEqual(rectangle_pattern_from_dict.scan_direction.value, self.scan_direction)

    def test_generate(self):
        # test generate
        patterns = self.pattern.generate()
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns, [self.pattern])

class TrenchPatternParametersTestCase(unittest.TestCase):

    def setUp(self):
        self.name = "Trench-1"
        self.width = 10e-6
        self.height = 10e-6
        self.depth = 10e-6
        self.spacing = 5e-6
        self.center = (0, 0)

        self.pattern = TrenchPatternParameters(
            name=self.name,
            width=self.width,
            height=self.height,
            depth=self.depth,
            spacing=self.spacing,
            center=self.center,
        )

    def test_assignment(self):

        # test assignment
        self.assertEqual(self.pattern.name.value, self.name)
        self.assertEqual(self.pattern.width.value, self.width)
        self.assertEqual(self.pattern.height.value, self.height)
        self.assertEqual(self.pattern.depth.value, self.depth)
        self.assertEqual(self.pattern.spacing.value, self.spacing)
        self.assertEqual(self.pattern.center.value, self.center)

    def test_dict(self):
        # test to_dict
        trench_pattern_dict = self.pattern.to_dict()
        self.assertEqual(trench_pattern_dict["name"], self.name)
        self.assertEqual(trench_pattern_dict["width"], self.width)
        self.assertEqual(trench_pattern_dict["height"], self.height)
        self.assertEqual(trench_pattern_dict["depth"], self.depth)
        self.assertEqual(trench_pattern_dict["spacing"], self.spacing)
        self.assertEqual(trench_pattern_dict["center_x"], 0)
        self.assertEqual(trench_pattern_dict["center_y"], 0)
        self.assertEqual(trench_pattern_dict["pattern"], "trench")

        # test from_dict
        trench_pattern_from_dict = TrenchPatternParameters.from_dict(trench_pattern_dict)
        self.assertEqual(trench_pattern_from_dict.name.value, self.name)
        self.assertEqual(trench_pattern_from_dict.width.value, self.width)
        self.assertEqual(trench_pattern_from_dict.height.value, self.height)
        self.assertEqual(trench_pattern_from_dict.depth.value, self.depth)
        self.assertEqual(trench_pattern_from_dict.spacing.value, self.spacing)
        self.assertEqual(trench_pattern_from_dict.center.value, self.center)

    def test_generate(self):
        # test generate
        patterns = self.pattern.generate()
        self.assertEqual(len(patterns), 2)
        self.assertEqual(patterns[0].name.value, f"{self.name} (Upper)")
        self.assertAlmostEqual(patterns[0].width.value, self.width)
        self.assertAlmostEqual(patterns[0].height.value, self.height)
        self.assertAlmostEqual(patterns[0].depth.value, self.depth)
        self.assertAlmostEqual(patterns[0].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[0].center.value, (0, (self.spacing + self.height) / 2))
        self.assertEqual(patterns[0].scan_direction.value, "TopToBottom")

        self.assertEqual(patterns[1].name.value, f"{self.name} (Lower)")
        self.assertAlmostEqual(patterns[1].width.value, self.width)
        self.assertAlmostEqual(patterns[1].height.value, self.height)
        self.assertAlmostEqual(patterns[1].depth.value, self.depth)
        self.assertAlmostEqual(patterns[1].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[1].center.value, (0, -(self.spacing + self.height) / 2))
        self.assertEqual(patterns[1].scan_direction.value, "BottomToTop")



class MicroexpansionPatternParametersTestCase(unittest.TestCase):

    def setUp(self):
        self.name = "Microexpansion-1"
        self.width = 1e-6
        self.height = 10e-6
        self.depth = 5e-6
        self.spacing = 20e-6
        self.center = (0, 0)

        self.pattern = MicroexpansionPatternParameters(
            name=self.name,
            width=self.width,
            height=self.height,
            depth=self.depth,
            spacing=self.spacing,
            center=self.center,
        )

    def test_assignment(self):

        # test assignment
        self.assertEqual(self.pattern.name.value, self.name)
        self.assertEqual(self.pattern.width.value, self.width)
        self.assertEqual(self.pattern.height.value, self.height)
        self.assertEqual(self.pattern.depth.value, self.depth)
        self.assertEqual(self.pattern.spacing.value, self.spacing)
        self.assertEqual(self.pattern.center.value, self.center)

    def test_dict(self):
        # test to_dict
        microexpansion_pattern_dict = self.pattern.to_dict()
        self.assertEqual(microexpansion_pattern_dict["name"], self.name)
        self.assertEqual(microexpansion_pattern_dict["width"], self.width)
        self.assertEqual(microexpansion_pattern_dict["height"], self.height)
        self.assertEqual(microexpansion_pattern_dict["depth"], self.depth)
        self.assertEqual(microexpansion_pattern_dict["spacing"], self.spacing)
        self.assertEqual(microexpansion_pattern_dict["center_x"], 0)
        self.assertEqual(microexpansion_pattern_dict["center_y"], 0)
        self.assertEqual(microexpansion_pattern_dict["pattern"], "microexpansion")

        # test from_dict
        microexpansion_pattern_from_dict = MicroexpansionPatternParameters.from_dict(microexpansion_pattern_dict)
        self.assertEqual(microexpansion_pattern_from_dict.name.value, self.name)
        self.assertEqual(microexpansion_pattern_from_dict.width.value, self.width)
        self.assertEqual(microexpansion_pattern_from_dict.height.value, self.height)
        self.assertEqual(microexpansion_pattern_from_dict.depth.value, self.depth)
        self.assertEqual(microexpansion_pattern_from_dict.spacing.value, self.spacing)
        self.assertEqual(microexpansion_pattern_from_dict.center.value, self.center)

    def test_generate(self):
        # test generate
        patterns = self.pattern.generate()
        self.assertEqual(len(patterns), 2)
        self.assertEqual(patterns[0].name.value, f"{self.name} (Left)")
        self.assertAlmostEqual(patterns[0].width.value, self.width)
        self.assertAlmostEqual(patterns[0].height.value, self.height)
        self.assertAlmostEqual(patterns[0].depth.value, self.depth)
        self.assertAlmostEqual(patterns[0].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[0].center.value, (-self.spacing, 0))
        self.assertEqual(patterns[0].scan_direction.value, "TopToBottom")

        self.assertEqual(patterns[1].name.value, f"{self.name} (Right)")
        self.assertAlmostEqual(patterns[1].width.value, self.width)
        self.assertAlmostEqual(patterns[1].height.value, self.height)
        self.assertAlmostEqual(patterns[1].depth.value, self.depth)
        self.assertAlmostEqual(patterns[1].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[1].center.value, (self.spacing, 0))
        self.assertEqual(patterns[1].scan_direction.value, "TopToBottom")
