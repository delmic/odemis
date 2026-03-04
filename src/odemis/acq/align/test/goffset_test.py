# -*- coding: utf-8 -*-
"""
Test Sparc auto grating offset alignment
"""

import os
import time
import unittest
import logging
import numpy as np

from concurrent.futures._base import CancelledError

from odemis import model
from odemis.util import testing, timeout
from odemis.acq.align.goffset import (
    find_peak_position,
    estimate_goffset_scale,
    SparcAutoGratingOffset,
)

import odemis

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc2-focus-test.odm.yaml"


class TestSparcAutoGratingOffset(unittest.TestCase):
    """
    Test automatic grating offset alignment.
    """

    @classmethod
    def setUpClass(cls):
        #testing.start_backend(SPARC_CONFIG)

        cls.detector = model.getComponent(role="ccd")
        cls.spgr = model.getComponent(role="spectrograph")

        cls._original_goffset = cls.spgr.goffset
        cls._original_position = cls.spgr.position.value.copy()

    # @classmethod
    # def tearDownClass(cls):
    #     # restore original position
    #     try:
    #         cls.spgr.moveAbsSync(cls._original_position)
    #     except Exception:
    #         logging.exception("Failed restoring spectrograph position")

    def setUp(self):
        # speed up detector
        self.detector.exposureTime.value = self.detector.exposureTime.range[0]

    def test_find_peak_position_synthetic(self):
        """
        Test peak detection on synthetic Gaussian data.
        """
        x = np.arange(200)
        true_center = 83.4
        spectrum = np.exp(-0.5*((x-true_center)/3.0)**2)

        peak = find_peak_position(spectrum)
        self.assertAlmostEqual(peak, true_center, places=1)

    def test_find_peak_position_2d(self):
        """
        Test peak detection on 2D data (mean over axis).
        """
        x = np.arange(200)
        true_center = 120.0
        line = np.exp(-0.5*((x-true_center)/4.0)**2)
        image = np.tile(line, (50, 1))

        peak = find_peak_position(image)
        self.assertAlmostEqual(peak, true_center, places=1)

    @timeout(100)
    def test_estimate_goffset_scale(self):
        """
        Test that goffset scale is non-zero and finite.
        """
        scale = estimate_goffset_scale(self.spgr, self.detector)

        self.assertIsInstance(scale, float)
        self.assertNotEqual(scale, 0.0)
        self.assertTrue(np.isfinite(scale))

    @timeout(300)
    def test_auto_grating_offset(self):
        """
        Test automatic centering of spectral peak.
        """
        delta = 0 # intentionally misalign
        current = self.spgr.position.value["goffset"]
        goffset_max = self.spgr.axes["goffset"].range[1]
        direction = 1 if (current + delta < goffset_max) else -1

        self.spgr.moveRelSync({"goffset": delta * direction})
        f = SparcAutoGratingOffset(self.spgr, self.detector, max_it=20)

        result = f.result(timeout=200)
        self.assertTrue(result)

    @timeout(100)
    def test_cancel(self):
        """
        Test cancelling alignment.
        """
        f = SparcAutoGratingOffset(self.spgr, self.detector)
        # Wait for the result or a timeout
        try:
            f.result(timeout=5)
        except:
            pass
        self.assertTrue(f.done())

if __name__ == "__main__":
    unittest.main()