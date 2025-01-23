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
import json
import numpy
from odemis.acq.milling.patterns import RectanglePatternParameters, TrenchPatternParameters, MicroexpansionPatternParameters

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

class MillingPatternParamersTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pass

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        pass

    def test_rectangle_pattern_parameters(self):

        name = "Rectangle-1"
        width = 10e-6
        height = 10e-6
        depth = 10e-6
        rotation = 0
        center = (0, 0)
        scan_direction = "TopToBottom"

        rectangle_pattern = RectanglePatternParameters(
            name=name,
            width=width,
            height=height,
            depth=depth,
            rotation=rotation,
            center=center,
            scan_direction=scan_direction,
        )

        # test assignment
        self.assertEqual(rectangle_pattern.name.value, name)
        self.assertEqual(rectangle_pattern.width.value, width)
        self.assertEqual(rectangle_pattern.height.value, height)
        self.assertEqual(rectangle_pattern.depth.value, depth)
        self.assertEqual(rectangle_pattern.rotation.value, rotation)
        self.assertEqual(rectangle_pattern.center.value, center)
        self.assertEqual(rectangle_pattern.scan_direction.value, scan_direction)

        # test to_json
        rectangle_pattern_json = rectangle_pattern.to_json()
        self.assertEqual(rectangle_pattern_json["name"], name)
        self.assertEqual(rectangle_pattern_json["width"], width)
        self.assertEqual(rectangle_pattern_json["height"], height)
        self.assertEqual(rectangle_pattern_json["depth"], depth)
        self.assertEqual(rectangle_pattern_json["rotation"], rotation)
        self.assertEqual(rectangle_pattern_json["center_x"], 0)
        self.assertEqual(rectangle_pattern_json["center_y"], 0)
        self.assertEqual(rectangle_pattern_json["scan_direction"], scan_direction)
        self.assertEqual(rectangle_pattern_json["pattern"], "rectangle")

        # test from_json
        rectangle_pattern_from_json = RectanglePatternParameters.from_json(rectangle_pattern_json)
        self.assertEqual(rectangle_pattern_from_json.name.value, name)
        self.assertEqual(rectangle_pattern_from_json.width.value, width)
        self.assertEqual(rectangle_pattern_from_json.height.value, height)
        self.assertEqual(rectangle_pattern_from_json.depth.value, depth)
        self.assertEqual(rectangle_pattern_from_json.rotation.value, rotation)
        self.assertEqual(rectangle_pattern_from_json.center.value, center)
        self.assertEqual(rectangle_pattern_from_json.scan_direction.value, scan_direction)

        # test generate
        patterns = rectangle_pattern.generate()
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns, [rectangle_pattern])

    def test_trench_pattern_parameters(self):

        name = "Trench-1"
        width = 10e-6
        height = 10e-6
        depth = 10e-6
        spacing = 5e-6
        center = (0, 0)

        trench_pattern = TrenchPatternParameters(
            name=name,
            width=width,
            height=height,
            depth=depth,
            spacing=spacing,
            center=center,
        )

        # test assignment
        self.assertEqual(trench_pattern.name.value, name)
        self.assertEqual(trench_pattern.width.value, width)
        self.assertEqual(trench_pattern.height.value, height)
        self.assertEqual(trench_pattern.depth.value, depth)
        self.assertEqual(trench_pattern.spacing.value, spacing)
        self.assertEqual(trench_pattern.center.value, center)

        # test to_json
        trench_pattern_json = trench_pattern.to_json()
        self.assertEqual(trench_pattern_json["name"], name)
        self.assertEqual(trench_pattern_json["width"], width)
        self.assertEqual(trench_pattern_json["height"], height)
        self.assertEqual(trench_pattern_json["depth"], depth)
        self.assertEqual(trench_pattern_json["spacing"], spacing)
        self.assertEqual(trench_pattern_json["center_x"], 0)
        self.assertEqual(trench_pattern_json["center_y"], 0)
        self.assertEqual(trench_pattern_json["pattern"], "trench")

        # test from_json
        trench_pattern_from_json = TrenchPatternParameters.from_json(trench_pattern_json)
        self.assertEqual(trench_pattern_from_json.name.value, name)
        self.assertEqual(trench_pattern_from_json.width.value, width)
        self.assertEqual(trench_pattern_from_json.height.value, height)
        self.assertEqual(trench_pattern_from_json.depth.value, depth)
        self.assertEqual(trench_pattern_from_json.spacing.value, spacing)
        self.assertEqual(trench_pattern_from_json.center.value, center)

        # test generate
        patterns = trench_pattern.generate()
        self.assertEqual(len(patterns), 2)
        self.assertAlmostEqual(patterns[0].name.value, f"{name} (Upper)")
        self.assertAlmostEqual(patterns[0].width.value, width)
        self.assertAlmostEqual(patterns[0].height.value, height)
        self.assertAlmostEqual(patterns[0].depth.value, depth)
        self.assertAlmostEqual(patterns[0].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[0].center.value, (0, (spacing + height) / 2))
        self.assertEqual(patterns[0].scan_direction.value, "TopToBottom")

        self.assertAlmostEqual(patterns[1].name.value, f"{name} (Lower)")
        self.assertAlmostEqual(patterns[1].width.value, width)
        self.assertAlmostEqual(patterns[1].height.value, height)
        self.assertAlmostEqual(patterns[1].depth.value, depth)
        self.assertAlmostEqual(patterns[1].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[1].center.value, (0, -(spacing + height) / 2))
        self.assertEqual(patterns[1].scan_direction.value, "BottomToTop")

    def test_microexpansion_pattern_parameters(self):

        name = "Microexpansion-1"
        width = 1e-6
        height = 10e-6
        depth = 5e-6
        spacing = 20e-6
        center = (0, 0)


        microexpansion_pattern = MicroexpansionPatternParameters(
            name=name,
            width=width,
            height=height,
            depth=depth,
            spacing=spacing,
            center=center,
        )

        # test assignment
        self.assertEqual(microexpansion_pattern.name.value, name)
        self.assertEqual(microexpansion_pattern.width.value, width)
        self.assertEqual(microexpansion_pattern.height.value, height)
        self.assertEqual(microexpansion_pattern.depth.value, depth)
        self.assertEqual(microexpansion_pattern.spacing.value, spacing)
        self.assertEqual(microexpansion_pattern.center.value, center)


        # test to_json
        microexpansion_pattern_json = microexpansion_pattern.to_json()
        self.assertEqual(microexpansion_pattern_json["name"], name)
        self.assertEqual(microexpansion_pattern_json["width"], width)
        self.assertEqual(microexpansion_pattern_json["height"], height)
        self.assertEqual(microexpansion_pattern_json["depth"], depth)
        self.assertEqual(microexpansion_pattern_json["spacing"], spacing)
        self.assertEqual(microexpansion_pattern_json["center_x"], 0)
        self.assertEqual(microexpansion_pattern_json["center_y"], 0)
        self.assertEqual(microexpansion_pattern_json["pattern"], "microexpansion")

        # test from_json
        microexpansion_pattern_from_json = MicroexpansionPatternParameters.from_json(microexpansion_pattern_json)
        self.assertEqual(microexpansion_pattern_from_json.name.value, name)
        self.assertEqual(microexpansion_pattern_from_json.width.value, width)
        self.assertEqual(microexpansion_pattern_from_json.height.value, height)
        self.assertEqual(microexpansion_pattern_from_json.depth.value, depth)
        self.assertEqual(microexpansion_pattern_from_json.spacing.value, spacing)
        self.assertEqual(microexpansion_pattern_from_json.center.value, center)

        # test generate
        patterns = microexpansion_pattern.generate()
        self.assertEqual(len(patterns), 2)
        self.assertEqual(patterns[0].name.value, f"{name} (Left)")
        self.assertAlmostEqual(patterns[0].width.value, width)
        self.assertAlmostEqual(patterns[0].height.value, height)
        self.assertAlmostEqual(patterns[0].depth.value, depth)
        self.assertAlmostEqual(patterns[0].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[0].center.value, (-spacing/2, 0))
        self.assertEqual(patterns[0].scan_direction.value, "TopToBottom")
        self.assertEqual(patterns[1].name.value, f"{name} (Right)")
        self.assertAlmostEqual(patterns[1].width.value, width)
        self.assertAlmostEqual(patterns[1].height.value, height)
        self.assertAlmostEqual(patterns[1].depth.value, depth)
        self.assertAlmostEqual(patterns[1].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[1].center.value, (spacing/2, 0))
        self.assertEqual(patterns[1].scan_direction.value, "TopToBottom")
