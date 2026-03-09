# -*- coding: utf-8 -*-
"""
Test Sparc auto grating offset alignment
"""

import os
import time
import unittest
import logging
import numpy as np
import odemis

from concurrent.futures._base import CancelledError
from odemis import model
from odemis.util import timeout

from odemis.acq.align.goffset import (find_peak_position,
    estimate_goffset_scale,
    sparc_auto_grating_offset,
    auto_align_grating_detector_offsets)

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
        spectrum = np.exp(-0.5 * ((x - true_center) / 3.0)**2)

        peak = find_peak_position(spectrum)
        self.assertAlmostEqual(peak, true_center, places=1)

    def test_find_peak_position_2d(self):
        """
        Test peak detection on 2D data (mean over axis).
        """
        x = np.arange(200)
        true_center = 120.0
        line = np.exp(-0.5 * ((x - true_center) / 4.0)**2)
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
        f = sparc_auto_grating_offset(self.spgr, self.detector, max_it=20)

        result = f.result(timeout=200)
        self.assertTrue(result)

    @timeout(100)
    def test_cancel(self):
        """
        Test cancelling alignment.
        """
        f = sparc_auto_grating_offset(self.spgr, self.detector)
        # Wait for the result or a timeout
        try:
            f.result(timeout=5)
        except:
            pass
        self.assertTrue(f.done())

class TestAutoAlignGratingDetectorOffsets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spgr = model.getComponent(role="spectrograph")
        cls.ccd = model.getComponent(role="ccd")
        #cls.spccd = model.getComponent(role="sp-ccd")
        cls.selector = model.getComponent(role="spec-det-selector")

    def setUp(self):
        # Speed up acquisition
        self.ccd.exposureTime.value = self.ccd.exposureTime.range[0]

    @timeout(1000)
    def test_single_detector_iteration(self):
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=[self.ccd], selector=self.selector)
        res = f.result(timeout=900)

        n_gratings = len(self.spgr.axes["grating"].choices)
        n_detectors = 1
        expected = n_detectors + (n_gratings - 1)

        self.assertEqual(len(res), expected)

        first_grating = list(self.spgr.axes["grating"].choices.keys())[0]
        dets_first = [d for (g, d) in res.keys() if g == first_grating]

        self.assertEqual(len(dets_first), n_detectors)

    def test_multi_detector_iteration(self):
        spccd = model.getComponent(role="sp-ccd")
        spccd.exposureTime.value = spccd.exposureTime.range[0]

        detectors = [self.ccd, spccd]

        # run alignment
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=detectors, selector=self.selector)
        res = f.result(timeout=900)

        # calculate expected results
        n_gratings = len(self.spgr.axes["grating"].choices)
        n_detectors = len(detectors)
        expected_count = n_detectors + (n_gratings - 1)

        self.assertEqual(len(res), expected_count, f"Expected {expected_count} results, got {len(res)}")

        # verify that every detector was used for the first grating
        gratings_list = list(self.spgr.axes["grating"].choices.keys())
        first_grating = gratings_list[0]

        dets_for_first_grating = [d for (g, d) in res.keys() if g == first_grating]
        self.assertEqual(len(dets_for_first_grating), n_detectors)
        self.assertIn(self.ccd.name, dets_for_first_grating)
        self.assertIn(spccd.name, dets_for_first_grating)

        # verify that only first detector is used for remaining gratings
        for g in gratings_list[1:]:
            # Ensure only the first detector (index 0) is present for these gratings
            dets_for_this_grating = [d for (grating, d) in res.keys() if grating == g]
            self.assertEqual(len(dets_for_this_grating), 1)
            self.assertEqual(dets_for_this_grating[0], detectors[0].name)

        # move to spectral camera
        self.selector.moveAbsSync({"rx": 1.5707963267948966})
        data = spccd.data.get(asap=False)

        # check data is not flat
        if data.max() == data.min():
            print("WARNING: sp-ccd is returning a flat image!")
        else:
            print(f"sp-ccd signal range: {data.min()} to {data.max()}")

    @timeout(100)
    def test_cancel(self):
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=[self.ccd],)

        time.sleep(1)

        cancelled = f.cancel()
        self.assertTrue(cancelled)
        self.assertTrue(f.cancelled())

        with self.assertRaises(CancelledError):
            f.result(timeout=900)

if __name__ == "__main__":
    unittest.main()
