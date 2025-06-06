#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 20 July 2025

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

import math
import os
import unittest

import numpy
import numpy.testing

import odemis
from odemis import model
from odemis.acq.scan import generate_scan_vector, vector_data_to_img, generate_scan_pixel_ttl
from odemis.util import testing

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2_HWSYNC_CONFIG = CONFIG_PATH + "sim/sparc2-nidaq-sim.odm.yaml"


class MockScanner:
    def __init__(self, shape, settle_time):
        self.shape = shape
        self.settleTime = settle_time
        self.pixelSize = model.TupleContinuous((1e-6, 1e-6),
                                               range=((1e-9, 1e-9), (1e-3, 1e-3)),
                                               unit="m",
                                               readonly=True)

class TestGenerateScanVector(unittest.TestCase):
    def setUp(self):
        self.scanner = MockScanner(shape=(768, 512), settle_time=10e-6)

    def test_simple(self):
        """
        Test the generation of a scan vector which scan the whole FoV at the maximum resolution.
        """
        res = (768, 512)
        roi = (0, 0, 1.0, 1.0)  # Full area of the scanner
        rotation = 0.0  # rad
        dwell_time = 1e-6  # s

        scan_vector, margin, md_cor = generate_scan_vector(
            self.scanner, res, roi, rotation, dwell_time
        )

        # Check output shape: (N, 2), N = res[0] * res[1] + margin * res[1]
        expected_points = res[1] * (res[0] + margin)
        self.assertEqual(scan_vector.shape, (expected_points, 2))
        # position of the first and last pixels (which has to take into account the shift due to the center of the pixel)
        half_width = ((self.scanner.shape[0] - 1) / 2,
                      (self.scanner.shape[1] - 1) / 2)
        numpy.testing.assert_equal(scan_vector[0], [-half_width[0], -half_width[1]])  # Center of the first pixel

        self.assertEqual(margin, 10)  # 10µs settle time / 1µs dwell time = 10 pixels margin
        self.assertIn(model.MD_PIXEL_SIZE_COR, md_cor)
        self.assertIn(model.MD_POS_COR, md_cor)
        self.assertEqual(md_cor[model.MD_PIXEL_SIZE_COR], (1, 1))  # Max res, so no pixel size change
        self.assertEqual(md_cor[model.MD_POS_COR], (0, 0))  # Full FoV, so no translation
        self.assertEqual(md_cor[model.MD_ROTATION_COR], -rotation)

    def test_rotation(self):
        res = (768, 512)
        roi = (0, 0, 1.0, 1.0)  # Full area of the scanner
        rotation = math.pi / 2  # rad, 90°
        dwell_time = 1e-6  # s

        scan_vector, margin, md_cor = generate_scan_vector(
            self.scanner, res, roi, rotation, dwell_time
        )
        self.assertEqual(md_cor[model.MD_ROTATION_COR], -rotation)

        # Check output shape: (N, 2), N = res[0] * res[1] + margin * res[1]
        expected_points = res[1] * (res[0] + margin)
        self.assertEqual(scan_vector.shape, (expected_points, 2))
        # position of the first and last pixels (which has to take into account the shift due to the center of the pixel)
        # 90° rotation counter-clockwise means the first pixel is now at the bottom-left corner
        half_width = ((self.scanner.shape[0] - 1) / 2,
                      (self.scanner.shape[1] - 1) / 2)
        numpy.testing.assert_almost_equal(scan_vector[0], [-half_width[1], half_width[0]])  # Center of the first pixel

    def test_margin_calculation(self):
        res = (768, 512)
        roi = (0, 0, 1.0, 1.0)  # Full area of the scanner
        rotation = 0

        # Large settleTime should increase margin
        self.scanner.settleTime = 100e-6
        dwell_time = 1e-6  # s
        scan_vector, margin, md_cor = generate_scan_vector(
            self.scanner, res, roi, rotation, dwell_time
        )
        self.assertEqual(margin, 100)  # 100µs settle time / 1µs dwell time = 100 pixels margin

        # only 1 pixel => no margin
        res = (1, 512)
        roi = (0, 0, 1/768, 1.0)
        scan_vector, margin, md_cor = generate_scan_vector(
            self.scanner, res, roi, rotation, dwell_time
        )
        self.assertEqual(margin, 0)

        # 2 pixels @ 1 µs => ~13% of a settle time => 1 pixel margin
        res = (2, 512)
        roi = (0, 0, 2 / 768, 1.0)
        scan_vector, margin, md_cor = generate_scan_vector(
            self.scanner, res, roi, rotation, dwell_time
        )
        self.assertEqual(margin, 1)

        # 2 pixels @ 100 µs => ~0.13% of a settle time < 1% => 0 pixel margin
        res = (2, 512)
        roi = (0, 0, 2 / 768, 1.0)
        scan_vector, margin, md_cor = generate_scan_vector(
            self.scanner, res, roi, rotation, self.scanner.settleTime
        )
        self.assertEqual(margin, 0)

        # Half width => flyback distance is half => half the margin (100 / 2 = 50)
        res = (384, 512)
        roi = (0, 0, 0.5, 1.0)  # Half area of the scanner in X
        scan_vector, margin, md_cor = generate_scan_vector(
            self.scanner, res, roi, rotation, dwell_time
        )
        self.assertEqual(margin, 50)

    def test_pixel_ttl(self):
        """
        Validate generate_scan_pixel_ttl
        """
        # Without margin, should return precisely 2 times more values than there are pixels,
        # and half of them should be high.
        res = (128, 37)
        ttls = generate_scan_pixel_ttl(self.scanner, res, margin=0)
        self.assertEqual(len(ttls), res[0] * res[1] * 2)
        numpy.testing.assert_array_equal(ttls[::2], True)  # First half of each pixel is high
        numpy.testing.assert_array_equal(ttls[1::2], False)  # Second half is low

        # With margin, should return 2 * (res[0] + margin) * res[1] values, and the same amount of
        # values high
        margin = 10
        ttls = generate_scan_pixel_ttl(self.scanner, res, margin)
        self.assertEqual(len(ttls), (res[0] + margin) * res[1] * 2)
        self.assertEqual(numpy.sum(ttls), res[0] * res[1])  # As many high values as there are pixels


class TestVectorAcquisition(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC2_HWSYNC_CONFIG)

        # Find CCD & SEM components
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.cl = model.getComponent(role="cl-detector")
        cls.spec = model.getComponent(role="spectrometer")

    def test_2d_img_as_vector(self):
        """
        Test basic acquisition of a full FoV image, using the vector scanning
        """
        res = (768, 512)
        roi = (0, 0, 1.0, 1.0)  # Full area of the scanner
        rotation = 0  # rad
        dwell_time = 1e-6  # s

        scan_vector, margin, md_cor = generate_scan_vector(
            self.ebeam, res, roi, rotation, dwell_time
        )

        self.ebeam.dwellTime.value = dwell_time
        self.ebeam.scanPath.value = scan_vector

        raw_data = self.sed.data.get()

        img = vector_data_to_img(raw_data, res, margin, md_cor)
        self.assertEqual(img.shape, (res[1], res[0]))
        self.assertIn(img.dtype, (numpy.uint16, numpy.int16))

        img_md = img.metadata

        ebeam_md = self.ebeam.getMetadata()
        ebeam_pxs = self.ebeam.pixelSize.value
        ebeam_max_res = self.ebeam.shape[:2]
        ebeam_fov = ebeam_pxs[0] * ebeam_max_res[0], ebeam_pxs[1] * ebeam_max_res[1]
        exp_pxs = (ebeam_fov[0] / res[0], ebeam_fov[1] / res[1])
        testing.assert_tuple_almost_equal(img_md[model.MD_PIXEL_SIZE], exp_pxs)
        testing.assert_tuple_almost_equal(img_md[model.MD_POS], ebeam_md[model.MD_POS])  # Full FoV, so no translation
        self.assertEqual(img_md[model.MD_ROTATION], rotation)

    def test_2d_img_rotated(self):
        """
        Test basic acquisition of a full FoV image with rotation, using the vector scanning
        """
        res = (768, 512)
        roi = (0.3, 0.3, 0.7, 0.7)  # Smaller than the whole FoV, to fit even with the rotation
        rotation = math.radians(30)
        dwell_time = 1e-6  # s

        scan_vector, margin, md_cor = generate_scan_vector(
            self.ebeam, res, roi, rotation, dwell_time
        )

        self.ebeam.dwellTime.value = dwell_time
        self.ebeam.scanPath.value = scan_vector

        raw_data = self.sed.data.get()

        img = vector_data_to_img(raw_data, res, margin, md_cor)
        self.assertEqual(img.shape, (res[1], res[0]))
        self.assertIn(img.dtype, (numpy.uint16, numpy.int16))

        img_md = img.metadata

        ebeam_md = self.ebeam.getMetadata()
        ebeam_pxs = self.ebeam.pixelSize.value
        ebeam_max_res = self.ebeam.shape[:2]
        ebeam_fov = ebeam_pxs[0] * ebeam_max_res[0], ebeam_pxs[1] * ebeam_max_res[1]
        fov_ratio = roi[2] - roi[0], roi[3] - roi[1]
        exp_pxs = (fov_ratio[0] * ebeam_fov[0] / res[0], fov_ratio[1] * ebeam_fov[1] / res[1])
        testing.assert_tuple_almost_equal(img_md[model.MD_PIXEL_SIZE], exp_pxs)
        # RoI centered on FoV, so no translation
        testing.assert_tuple_almost_equal(img_md[model.MD_POS], ebeam_md[model.MD_POS])
        self.assertEqual(img_md[model.MD_ROTATION], rotation)


if __name__ == "__main__":
    unittest.main()
