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
import time
import unittest

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam
from odemis.driver import tucsen

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s",
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


class TestCallBack(unittest.TestCase):
    """
    Test the TUCam callback mechanism.
    """

    def on_frame(self, array):
        self._call_counts += 1
        logging.debug(f"Callback received frame of shape {array.shape} and dtype {array.dtype}")

    def test_callback_registration(self):
        if TEST_NOHW:
            dll = tucsen.FakeTUCamDLL()
        else:
            dll = tucsen.TUCamDLL()
        dll.open_camera(0)
        dll.set_exposure_time(0.5)

        self._call_counts = 0
        dll.register_data_callback(self.on_frame)
        logging.debug("Starting capture for callback test...")
        dll.start_capture()

        time.sleep(3)
        dll.end_capture()
        dll.register_data_callback(None)

        self.assertGreaterEqual(self._call_counts, 3, "Callback was not called during capture")

        dll.close_camera()
        dll.TUCAM_Api_Uninit()


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
        # vertical res (== first dim as it's transposed) is accepted as is
        self.assertEqual(self.camera.resolution.value, (199, 96))

if __name__ == '__main__':
    unittest.main()
