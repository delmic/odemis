# -*- coding: utf-8 -*-
"""
Created on Oct 29, 2025

@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

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

import numpy

from odemis.util.synthetic import ParabolicMirrorRayTracer


class TestParabolicMirrorRayTracer(unittest.TestCase):
    """Unit tests for the ParabolicMirrorRayTracer class."""

    def setUp(self):
        """Set up a tracer instance for use in all tests."""
        self.good_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.tracer = ParabolicMirrorRayTracer(good_pos=self.good_pos)

    def test_initialization_creates_valid_image(self):
        """
        Test that the constructor successfully creates an initial ray-traced image.
        """
        initial_img = self.tracer._last_img

        # Check type, shape, and dtype of the output image
        self.assertIsInstance(initial_img, numpy.ndarray)
        self.assertEqual(initial_img.shape, (self.tracer.resolution.value[1], self.tracer.resolution.value[0]))
        self.assertEqual(initial_img.dtype, numpy.uint16)

        # Check that the image is not empty/blank, indicating a successful trace
        self.assertTrue(
            numpy.sum(initial_img) > 0, "Initial image should not be all zeros."
        )

    def test_simulate_with_new_position_recalculates(self):
        """
        Test that calling simulate() with a new position generates a new, different image.
        """
        init_pos = {"x": 0.0, "y": 5e-6, "z": 0.0}
        initial_img = self.tracer.simulate(init_pos)

        # Define a new position with a slight misalignment
        new_pos = {"x": 0.0, "y": 10e-6, "z": 0.0}

        # Simulate with the new position
        new_img = self.tracer.simulate(new_pos)

        # Verify the new image has the correct properties
        self.assertEqual(new_img.shape, initial_img.shape)
        self.assertEqual(new_img.dtype, initial_img.dtype)

        # Verify the new image is different from the initial one
        are_equal = numpy.array_equal(initial_img, new_img)
        self.assertFalse(
            are_equal, "Simulating a new position should produce a different image."
        )

        # Verify the internal state has been updated
        self.assertEqual(self.tracer._last_pos, new_pos)

    def test_simulate_handles_raytracing_failure_gracefully(self):
        """
        Test that if ray tracing fails, the method returns the last successful image and logs a warning.
        We can induce a failure by providing a position far off-axis, causing math errors (e.g., divide by zero).
        """
        # Get the last known good image (from initialization)
        last_good_image = self.tracer._last_img.copy()

        # Define a position that is likely to cause a FloatingPointError during calculation
        failing_pos = {"x": 0.0, "y": 100.0, "z": 0.0}  # Extreme y-offset

        # Use assertLogs to capture logging output
        with self.assertLogs(level="WARNING") as log:
            # This call should fail internally and trigger the except block
            failed_img = self.tracer.simulate(failing_pos)

            # Check that the expected warning message was logged
            self.assertEqual(len(log.output), 1)
            self.assertIn("Using last image.", log.output[0])

        # The returned image should be the same as the last good image
        numpy.testing.assert_array_equal(
            last_good_image,
            failed_img,
            "On failure, the last good image should be returned.",
        )

        # The internal `_last_pos` should NOT be updated to the failing position
        self.assertNotEqual(
            self.tracer._last_pos,
            failing_pos,
            "On failure, _last_pos should not be updated.",
        )
        self.assertEqual(self.tracer._last_pos, self.good_pos)

    def test_constructor_raises_value_error_on_missing_keys(self):
        """
        Test that the constructor raises a ValueError if good_pos is missing required keys.
        """
        bad_pos_missing_z = {"x": 0.0, "y": 0.0}
        with self.assertRaisesRegex(
            ValueError, "good_pos must contain keys.*missing: {'z'}"
        ):
            ParabolicMirrorRayTracer(good_pos=bad_pos_missing_z)

        bad_pos_missing_all = {}
        with self.assertRaises(ValueError):
            ParabolicMirrorRayTracer(good_pos=bad_pos_missing_all)


if __name__ == "__main__":
    unittest.main()
