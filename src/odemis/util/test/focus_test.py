#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 24 January 2025

@author: Éric Piel

Copyright © 2025 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""

import unittest
import numpy

from odemis.util.focus import MeasureSpotsFocus, MeasureOpticalFocus


class TestMeasureOpticalFocus(unittest.TestCase):

    def test_simple(self):
        # Create a simple grayscale image
        image = numpy.random.randint(200, 3000, (101, 512), dtype=numpy.uint16)
        focus_level = MeasureOpticalFocus(image)
        self.assertIsInstance(focus_level, float)
        self.assertGreaterEqual(focus_level, 0)


class TestMeasureSpotsFocus(unittest.TestCase):

    def test_simple(self):
        # Create a simple grayscale image
        image = numpy.random.randint(200, 3000, (101, 512), dtype=numpy.uint16)
        focus_level = MeasureSpotsFocus(image)
        self.assertIsInstance(focus_level, float)
        self.assertGreaterEqual(focus_level, 0)

    def test_empty_image_uint8(self):
        # Create a completely black image => focus level should be 0
        image = numpy.zeros((2152, 3512), dtype=numpy.uint8)
        focus_level = MeasureSpotsFocus(image)
        self.assertIsInstance(focus_level, float)
        self.assertEqual(focus_level, 0)

    def test_uint32(self):
        image = numpy.random.randint(200, 100000, (101, 512), dtype=numpy.uint32)
        focus_level = MeasureSpotsFocus(image)
        self.assertIsInstance(focus_level, float)
        self.assertGreaterEqual(focus_level, 0)

    def test_high_variance_image(self):
        # Create an image with high variance
        image = numpy.zeros((200, 512), dtype=numpy.uint16)
        image[:] = 100 # background
        image[100:120, 300:320] = 1000  # white rectangle in the middle of the image
        focus_level = MeasureSpotsFocus(image)
        self.assertIsInstance(focus_level, float)
        self.assertGreater(focus_level, 1)  # Usually much higher than 1e12!


if __name__ == "__main__":
    unittest.main()
