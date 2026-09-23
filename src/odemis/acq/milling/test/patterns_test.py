# -*- coding: utf-8 -*-
"""
@author: Patrick Cleeve, Alexéy Ilyushkin

Copyright © 2025-2026 Patrick Cleeve, Alexéy Ilyushkin, Delmic

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
from odemis.acq.milling.patterns import (
    NotchPatternParameters,
    PATTERN_NAME_TO_CLASS,
    RectanglePatternParameters,
    TrenchPatternParameters,
    MicroexpansionPatternParameters,
    RulerPatternParameters,
    WaffleTrenchPatternParameters,
)

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
        self.spot_size_correction = 0.1e-6

        self.pattern = RectanglePatternParameters(
            name=self.name,
            width=self.width,
            height=self.height,
            depth=self.depth,
            rotation=self.rotation,
            center=self.center,
            scan_direction=self.scan_direction,
            spot_size_correction=self.spot_size_correction,
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
        with self.assertRaises(IndexError):
            self.pattern.spot_size_correction.value = -0.1e-6

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
        self.assertEqual(rectangle_pattern_dict["spot_size_correction"], self.spot_size_correction)
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
        self.assertEqual(rectangle_pattern_from_dict.spot_size_correction.value,
                         self.spot_size_correction)

        # Existing task files do not have a spot size correction and must retain the
        # previous behavior.
        del rectangle_pattern_dict["spot_size_correction"]
        rectangle_without_correction = RectanglePatternParameters.from_dict(rectangle_pattern_dict)
        self.assertEqual(rectangle_without_correction.spot_size_correction.value, 0.0)

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
        self.spot_size_correction = 0.1e-6

        self.pattern = TrenchPatternParameters(
            name=self.name,
            width=self.width,
            height=self.height,
            depth=self.depth,
            spacing=self.spacing,
            center=self.center,
            spot_size_correction=self.spot_size_correction,
        )

    def test_assignment(self):
        # test assignment
        self.assertEqual(self.pattern.name.value, self.name)
        self.assertEqual(self.pattern.width.value, self.width)
        self.assertEqual(self.pattern.height.value, self.height)
        self.assertEqual(self.pattern.depth.value, self.depth)
        self.assertEqual(self.pattern.spacing.value, self.spacing)
        self.assertEqual(self.pattern.center.value, self.center)
        self.assertEqual(self.pattern.spot_size_correction.value, self.spot_size_correction)

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
        self.assertEqual(trench_pattern_dict["spot_size_correction"], self.spot_size_correction)
        self.assertEqual(trench_pattern_dict["pattern"], "trench")

        # test from_dict
        trench_pattern_from_dict = TrenchPatternParameters.from_dict(trench_pattern_dict)
        self.assertEqual(trench_pattern_from_dict.name.value, self.name)
        self.assertEqual(trench_pattern_from_dict.width.value, self.width)
        self.assertEqual(trench_pattern_from_dict.height.value, self.height)
        self.assertEqual(trench_pattern_from_dict.depth.value, self.depth)
        self.assertEqual(trench_pattern_from_dict.spacing.value, self.spacing)
        self.assertEqual(trench_pattern_from_dict.center.value, self.center)
        self.assertEqual(trench_pattern_from_dict.spot_size_correction.value,
                         self.spot_size_correction)

        del trench_pattern_dict["spot_size_correction"]
        trench_without_correction = TrenchPatternParameters.from_dict(trench_pattern_dict)
        self.assertEqual(trench_without_correction.spot_size_correction.value, 0.0)

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
        self.assertEqual(patterns[0].spot_size_correction.value, self.spot_size_correction)

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
        self.spot_size_correction = 0.1e-6

        self.pattern = MicroexpansionPatternParameters(
            name=self.name,
            width=self.width,
            height=self.height,
            depth=self.depth,
            spacing=self.spacing,
            center=self.center,
            spot_size_correction=self.spot_size_correction,
        )

    def test_assignment(self):
        # test assignment
        self.assertEqual(self.pattern.name.value, self.name)
        self.assertEqual(self.pattern.width.value, self.width)
        self.assertEqual(self.pattern.height.value, self.height)
        self.assertEqual(self.pattern.depth.value, self.depth)
        self.assertEqual(self.pattern.spacing.value, self.spacing)
        self.assertEqual(self.pattern.center.value, self.center)
        self.assertEqual(self.pattern.spot_size_correction.value, self.spot_size_correction)

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
        self.assertEqual(microexpansion_pattern_dict["spot_size_correction"],
                         self.spot_size_correction)
        self.assertEqual(microexpansion_pattern_dict["pattern"], "microexpansion")

        # test from_dict
        microexpansion_pattern_from_dict = MicroexpansionPatternParameters.from_dict(microexpansion_pattern_dict)
        self.assertEqual(microexpansion_pattern_from_dict.name.value, self.name)
        self.assertEqual(microexpansion_pattern_from_dict.width.value, self.width)
        self.assertEqual(microexpansion_pattern_from_dict.height.value, self.height)
        self.assertEqual(microexpansion_pattern_from_dict.depth.value, self.depth)
        self.assertEqual(microexpansion_pattern_from_dict.spacing.value, self.spacing)
        self.assertEqual(microexpansion_pattern_from_dict.center.value, self.center)
        self.assertEqual(microexpansion_pattern_from_dict.spot_size_correction.value,
                         self.spot_size_correction)

        del microexpansion_pattern_dict["spot_size_correction"]
        microexpansion_without_correction = MicroexpansionPatternParameters.from_dict(
            microexpansion_pattern_dict
        )
        self.assertEqual(microexpansion_without_correction.spot_size_correction.value, 0.0)

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
        self.assertEqual(patterns[0].spot_size_correction.value, self.spot_size_correction)

        self.assertEqual(patterns[1].name.value, f"{self.name} (Right)")
        self.assertAlmostEqual(patterns[1].width.value, self.width)
        self.assertAlmostEqual(patterns[1].height.value, self.height)
        self.assertAlmostEqual(patterns[1].depth.value, self.depth)
        self.assertAlmostEqual(patterns[1].rotation.value, 0)
        numpy.testing.assert_array_almost_equal(patterns[1].center.value, (self.spacing, 0))
        self.assertEqual(patterns[1].scan_direction.value, "TopToBottom")


class RulerPatternParametersTestCase(unittest.TestCase):
    """Test ruler geometry, validation, and serialization."""

    def setUp(self) -> None:
        """Create a ruler used by each test."""
        self.pattern = RulerPatternParameters(
            width=2e-6, height=10e-6, depth=0.5e-6, spacing=10e-6,
            num_notches=21, center=(3e-6, -4e-6), name="Ruler-1")

    def test_alternating_notches(self) -> None:
        """Generate mirrored notches with alternating lengths."""
        rectangles = self.pattern.generate()
        self.assertEqual(len(rectangles), 42)
        expected_widths = [2e-6, 1.5e-6] * 10 + [2e-6]
        for index, (left, right) in enumerate(zip(rectangles[::2], rectangles[1::2])):
            self.assertIsInstance(left, RectanglePatternParameters)
            self.assertIsInstance(right, RectanglePatternParameters)
            self.assertAlmostEqual(left.width.value, expected_widths[index], places=15)
            self.assertEqual(left.width.value, right.width.value)
            self.assertEqual(left.height.value, right.height.value)
            self.assertEqual(left.center.value[1], right.center.value[1])
            # The inner edges stay aligned regardless of the notch length.
            self.assertAlmostEqual(left.center.value[0] + left.width.value / 2, -2e-6, places=15)
            self.assertAlmostEqual(right.center.value[0] - right.width.value / 2, 8e-6, places=15)
            for rectangle in (left, right):
                self.assertEqual(rectangle.depth.value, 0.5e-6)
                self.assertEqual(rectangle.rotation.value, 0)
                self.assertEqual(rectangle.to_dict()["pattern"], "rectangle")

        left = rectangles[::2]
        pitch = left[1].center.value[1] - left[0].center.value[1]
        self.assertAlmostEqual(left[0].height.value / pitch, 0.2)
        for previous, following in zip(left, left[1:]):
            self.assertAlmostEqual(following.center.value[1] - previous.center.value[1], pitch, places=15)
            self.assertGreater(following.center.value[1] - following.height.value / 2,
                               previous.center.value[1] + previous.height.value / 2)
        self.assertAlmostEqual(left[0].center.value[1] - left[0].height.value / 2, -9e-6, places=15)
        self.assertAlmostEqual(left[-1].center.value[1] + left[-1].height.value / 2, 1e-6, places=15)

    def test_count_and_scaling(self) -> None:
        """Honor the notch count and scale the complete ruler."""
        for count in (2, 6, 11, 17, 21, 1000):
            with self.subTest(count=count):
                self.pattern.num_notches.value = count
                rectangles = self.pattern.generate()
                self.assertEqual(len(rectangles), count * 2)
                extent = (rectangles[-1].center.value[1] + rectangles[-1].height.value / 2
                          - rectangles[0].center.value[1] + rectangles[0].height.value / 2)
                self.assertAlmostEqual(extent, self.pattern.height.value, places=15)

        before = self.pattern.generate()
        self.pattern.width.value *= 2
        self.pattern.height.value *= 2
        self.pattern.spacing.value *= 2
        center = self.pattern.center.value
        for old, new in zip(before, self.pattern.generate()):
            self.assertEqual(new.width.value, old.width.value * 2)
            self.assertEqual(new.height.value, old.height.value * 2)
            numpy.testing.assert_allclose(numpy.subtract(new.center.value, center),
                                          2 * numpy.subtract(old.center.value, center), atol=1e-15)

    def test_serialization(self) -> None:
        """Preserve ruler parameters and geometry after serialization."""
        self.pattern.spot_size_correction.value = 20e-9
        data = self.pattern.to_dict()
        restored = PATTERN_NAME_TO_CLASS[data["pattern"]].from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        self.assertEqual([p.to_dict() for p in restored.generate()],
                         [p.to_dict() for p in self.pattern.generate()])

    def test_missing_spot_size_correction_defaults_to_zero(self) -> None:
        """Load older ruler data without a spot size correction."""
        data = self.pattern.to_dict()
        del data["spot_size_correction"]
        legacy_pattern = RulerPatternParameters.from_dict(data)
        self.assertEqual(legacy_pattern.spot_size_correction.value, 0.0)

    def test_spot_size_correction_reaches_every_notch(self) -> None:
        """Pass the ruler correction to every generated rectangle."""
        self.pattern.spot_size_correction.value = 20e-9
        for rectangle in self.pattern.generate():
            self.assertEqual(rectangle.spot_size_correction.value, 20e-9)

    def test_spot_size_correction_preserves_desired_geometry(self) -> None:
        """Keep requested geometry independent of the correction value."""
        before = self.pattern.generate()
        self.pattern.spot_size_correction.value = 20e-9
        for original, corrected in zip(before, self.pattern.generate()):
            self.assertEqual(corrected.width.value, original.width.value)
            self.assertEqual(corrected.height.value, original.height.value)
            self.assertEqual(corrected.depth.value, original.depth.value)
            self.assertEqual(corrected.center.value, original.center.value)

    def test_negative_spot_size_correction_is_rejected(self) -> None:
        """Reject negative spot size corrections."""
        with self.assertRaises(IndexError):
            self.pattern.spot_size_correction.value = -1e-9

    def test_invalid_dimensions_and_count(self) -> None:
        """Reject counts and heights that cannot form valid rectangles."""
        for count in (0, 1, -1, 1001, 3.5):
            with self.subTest(count=count), self.assertRaises((IndexError, TypeError)):
                self.pattern.num_notches.value = count
        with self.assertRaises(ValueError):
            self.pattern.height.value = 10e-9
        self.assertEqual(self.pattern.height.value, 10e-6)
        self.pattern.height.value = 101e-9
        self.assertEqual(len(self.pattern.generate()), 42)
        with self.assertRaises(ValueError):
            self.pattern.num_notches.value = 22
        self.assertEqual(self.pattern.num_notches.value, 21)
        with self.assertRaises(ValueError):
            RulerPatternParameters(width=2e-6, height=10e-9, depth=1e-6, spacing=1e-6)


class NotchPatternParametersTestCase(unittest.TestCase):
    """Test the five-segment notch geometry."""

    def test_geometry_and_serialization(self) -> None:
        """Generate the open loop and preserve it through serialization."""
        pattern = NotchPatternParameters(
            width=3.5e-6, height=8.1e-6, depth=0.5e-6, gap=1.1e-6,
            thickness=0.2e-6, offset=-0.2e-6, center=(1e-6, -2e-6))
        rectangles = pattern.generate()

        self.assertEqual(len(rectangles), 5)
        expected_sizes = [
            (0.2e-6, 3.5e-6),
            (3.5e-6, 0.2e-6),
            (0.2e-6, 1.1e-6),
            (3.5e-6, 0.2e-6),
            (0.2e-6, 3.1e-6),
        ]
        for rectangle, expected_size in zip(rectangles, expected_sizes):
            self.assertAlmostEqual(rectangle.width.value, expected_size[0])
            self.assertAlmostEqual(rectangle.height.value, expected_size[1])
        self.assertLess(rectangles[0].center.value[0], rectangles[2].center.value[0])
        self.assertEqual(rectangles[0].center.value[0], rectangles[4].center.value[0])
        self.assertTrue(all(isinstance(rectangle, RectanglePatternParameters)
                            for rectangle in rectangles))

        pattern.mirrored.value = True
        mirrored = pattern.generate()
        for original, flipped in zip(rectangles, mirrored):
            self.assertAlmostEqual(flipped.center.value[0] - pattern.center.value[0],
                                   pattern.center.value[0] - original.center.value[0])

        restored = NotchPatternParameters.from_dict(pattern.to_dict())
        self.assertEqual(restored.to_dict(), pattern.to_dict())
        self.assertEqual([rectangle.to_dict() for rectangle in restored.generate()],
                         [rectangle.to_dict() for rectangle in mirrored])


class WaffleTrenchPatternParametersTestCase(unittest.TestCase):
    """Test asymmetric waffle trench geometry and serialization."""

    def test_geometry_and_serialization(self) -> None:
        """Generate two independently sized rectangles around the opening."""
        pattern = WaffleTrenchPatternParameters(
            top_width=22e-6, top_height=37e-6,
            bottom_width=20e-6, bottom_height=17e-6,
            depth=1e-6, spacing=3e-6, center=(1e-6, -2e-6))
        top, bottom = pattern.generate()

        self.assertEqual((top.width.value, top.height.value), (22e-6, 37e-6))
        self.assertEqual((bottom.width.value, bottom.height.value), (20e-6, 17e-6))
        self.assertAlmostEqual(
            top.center.value[1] - top.height.value / 2
            - (bottom.center.value[1] + bottom.height.value / 2),
            3e-6)
        self.assertEqual(top.scan_direction.value, "TopToBottom")
        self.assertEqual(bottom.scan_direction.value, "BottomToTop")
        self.assertEqual(
            WaffleTrenchPatternParameters.from_dict(pattern.to_dict()).to_dict(),
            pattern.to_dict())


if __name__ == '__main__':
    unittest.main()
