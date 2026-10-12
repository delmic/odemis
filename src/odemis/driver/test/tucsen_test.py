#!/usr/bin/env python3
"""
Created on 30 Aug 2025

@author: Éric Piel

Copyright © 2025 Éric Piel, Delmic

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
import os
import unittest

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam
from odemis import model
from odemis.driver import tucsen

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s",
                    force=True  # Overwrite the default logging set by importing other module (Py 3.8+)
                    )

# Export TEST_NOHW=1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

KWARGS = dict(name="camera", role="ccd", device=None, transp=[2, -1])
KWARGS_SIM = KWARGS.copy()
KWARGS_SIM["device"] = "fake"

if TEST_NOHW:
    KWARGS = KWARGS_SIM

class StaticTestTUCam(VirtualStaticTestCam, unittest.TestCase):
    camera_type = tucsen.TUCam
    camera_kwargs = KWARGS


# Inheritance order is important for setUp, tearDown
class TestTUCam(VirtualTestCam, unittest.TestCase):
    """
    Test directly the TUCam class.
    """
    camera_type = tucsen.TUCam
    camera_kwargs = KWARGS

    def test_resolution_rounding(self):
        self.camera.resolution.value = (199, 103)
        # horizontal res (== second dim as it's transposed) is rounded to multiple of 8
        # vertical res (== first dim as it's transposed) is rounded to multiple of 2
        self.assertEqual(self.camera.resolution.value, (198, 96))


class TestTUCamMaxRes(unittest.TestCase):
    """
    Test the max_res argument of TUCam.
    """
    def test_max_res(self):
        kwargs = KWARGS.copy()
        kwargs["max_res"] = [1000, 800]
        cam = tucsen.TUCam(**kwargs)
        try:
            self.assertEqual(cam.resolution.value, (1000, 800))
            self.assertEqual(cam.resolution.range[1], (1000, 800))
            cam.resolution.value = (1000, 800)
            self.assertEqual(cam.resolution.value, (1000, 800))

            # The sensor size is unchanged
            self.assertGreater(cam.getMetadata()[model.MD_SENSOR_SIZE][0], 1000)

            # At binning 2, the max is halved
            cam.binning.value = (2, 2)
            cam.resolution.value = (1000, 800)
            self.assertEqual(cam.resolution.value, (500, 400))
        finally:
            cam.terminate()

    def test_max_res_granularity(self):
        kwargs = KWARGS.copy()
        kwargs["max_res"] = [1001, 803]
        with self.assertRaises(ValueError):
            tucsen.TUCam(**kwargs)

    def test_max_res_invalid(self):
        kwargs = KWARGS.copy()
        kwargs["max_res"] = [100000, 100000]
        with self.assertRaises(ValueError):
            tucsen.TUCam(**kwargs)


if __name__ == '__main__':
    unittest.main()
