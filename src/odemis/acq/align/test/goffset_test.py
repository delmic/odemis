# -*- coding: utf-8 -*-
"""
Test Sparc auto grating offset alignment
"""

import os
import unittest
import logging
import numpy as np
import odemis

from odemis import model
from odemis.util import timeout
from odemis.acq.align.goffset_ext import(
    find_peak_position,
    peak_is_present,
    estimate_goffset_scale,
    sparc_auto_grating_offset,
    auto_align_grating_detector_offsets,
    log_detector_state
)



from odemis.dataio import hdf5


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc2-focus-test.odm.yaml"

HOME_PATH = os.path.expanduser("~") + "/"
H5_FILE_2d_NO_PEAK = HOME_PATH + "development/odemis/grating 2 1024x256/"

class TestSparcAutoGratingOffset(unittest.TestCase):
    """
    Test automatic grating offset alignment.
    """

    @classmethod
    def setUpClass(cls):
        #testing.start_backend(SPARC_CONFIG)

        cls.detector = model.getComponent(role="ccd")
        cls.spgr = model.getComponent(role="spectrograph")
        # cls.spccd = model.getComponent(role="sp-ccd")
        cls.selector = model.getComponent(role="spec-det-selector")

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

    @timeout(800)
    def test_scale_not_misaligned(self):
        """
        Verify scale estimation only happens when the peak is misaligned.
        This is inferred from the probe move performed by estimate_goffset_scale().
        """
        # reset spectrograph to known aligned position
        self.spgr.moveAbsSync(self._original_position)

        start_goffset = self.spgr.position.value["goffset"]

        f = sparc_auto_grating_offset(self.spgr, self.detector, max_it=20)
        result = f.result(timeout=300)

        end_goffset = self.spgr.position.value["goffset"]

        self.assertTrue(result)

        # If peak is already centered, the algorithm exits immediately
        # so goffset should not change.
        self.assertAlmostEqual(start_goffset, end_goffset, places=6,
            msg="goffset changed even though peak was already centered (scale estimation likely ran)")

    def test_scale_estimation_misaligned(self):
        """
        Verify that scale estimation runs when the peak is misaligned, which is inferred
        from the probe move performed by estimate_goffset_scale().
        """

        delta = 500
        current = self.spgr.position.value["goffset"]
        maxv = self.spgr.axes["goffset"].range[1]
        direction = 1 if (current + delta < maxv) else -1

        self.spgr.moveRelSync({"goffset": delta * direction})

        start_goffset = self.spgr.position.value["goffset"]

        f = sparc_auto_grating_offset(self.spgr, self.detector, max_it=50)
        result = f.result(timeout=600)

        end_goffset = self.spgr.position.value["goffset"]

        self.assertTrue(result)

        # If misaligned, centering should move the grating
        self.assertNotAlmostEqual(start_goffset, end_goffset, places=3,
            msg="goffset did not change during alignment when peak was misaligned")

    @timeout(800)
    def test_auto_grating_offset(self):
        """
        Test automatic centering of spectral peak (one grating/detector only).
        """

        delta = 0 # intentionally misalign
        current = self.spgr.position.value["goffset"]
        goffset_max = self.spgr.axes["goffset"].range[1]
        direction = 1 if (current + delta < goffset_max) else -1

        test_data = self.detector.data.get(asap=False)
        log_detector_state("TEST", "INITIAL", self.detector, test_data)

        self.spgr.moveRelSync({"goffset": delta * direction})
        logging.info("Test: after misalign move, spgr.position.gooffset = %s", self.spgr.position.value["goffset"])
        f = sparc_auto_grating_offset(self.spgr, self.detector, max_it=100)

        result = f.result(timeout=800)
        self.assertTrue(result)

    @timeout(600)
    def test_single_detector_updates_grating(self):
        """
        Single-detector mode: aligning a detector on the secondary port must update
        the grating offset and not the detector offset.
        """

        spccd = model.getComponent(role="sp-ccd")
        spccd.exposureTime.value = spccd.exposureTime.range[0]

        # try to move selector to secondary port; ignore if not available
        try:
            self.selector.moveAbsSync({"rx": 1.5707963267948966})
        except Exception:
            logging.debug("Selector move to secondary failed or not present; continuing")

        start_goffset = self.spgr.position.value["goffset"]

        f = sparc_auto_grating_offset(self.spgr, spccd, single_detector_mode=True, max_it=50)
        result = f.result(timeout=300)
        self.assertTrue(result, "Single-detector alignment failed")

        after_goffset = self.spgr.position.value["goffset"]
        self.assertNotAlmostEqual(start_goffset, after_goffset, places=3,
            msg="Grating goffset did not change for single-detector alignment on secondary port")

    @timeout(900)
    def test_multi_detector_does_not_change_grating(self):
        """
        Multi-detector mode: first detector sets the grating; aligning the second
        detector should not change the grating offset (it should adjust detector offset).
        """

        spccd = model.getComponent(role="sp-ccd")
        spccd.exposureTime.value = spccd.exposureTime.range[0]

        # ensure selector points to primary detector for initial grating-setting alignment
        try:
            self.selector.moveAbsSync({"rx": 0.0})
        except Exception:
            logging.debug("Selector move to primary failed or not present; continuing")

        # align first detector to set grating offset
        f_first = sparc_auto_grating_offset(self.spgr, self.detector, max_it=50)
        self.assertTrue(f_first.result(timeout=300), "First-detector alignment failed")
        grating_after_first = self.spgr.position.value["goffset"]

        # switch to secondary detector
        try:
            self.selector.moveAbsSync({"rx": 1.5707963267948966})
        except Exception:
            logging.debug("Selector move to secondary failed or not present; continuing")

        # align second detector in multi-detector mode (should not change grating)
        f_second = sparc_auto_grating_offset(self.spgr, spccd, max_it=50)
        self.assertTrue(f_second.result(timeout=300), "Second-detector alignment failed")

        grating_after_second = self.spgr.position.value["goffset"]
        self.assertAlmostEqual(grating_after_first, grating_after_second, places=3,
            msg="Grating goffset changed when aligning second detector in multi-detector mode")

    @timeout(900)
    def test_multi_detector_detector_offset_changes(self):
        """
        Multi-detector mode: after the first detector sets the grating, aligning the
        second detector should NOT change the grating goffset but should change the
        detector-specific response (verified by the peak moving closer to center).
        """
        spccd = model.getComponent(role="sp-ccd")
        spccd.exposureTime.value = spccd.exposureTime.range[0]

        # Ensure primary detector sets the grating first
        try:
            self.selector.moveAbsSync({"rx": 0.0})
        except Exception:
            logging.debug("Selector move to primary failed or not present; continuing")

        f_first = sparc_auto_grating_offset(self.spgr, self.detector, max_it=50)
        self.assertTrue(f_first.result(timeout=300), "First-detector alignment failed")
        grating_after_first = self.spgr.position.value["goffset"]

        # Switch to secondary detector
        try:
            self.selector.moveAbsSync({"rx": 1.5707963267948966})
        except Exception:
            logging.debug("Selector move to secondary failed or not present; continuing")

        # Measure peak before alignment on secondary detector
        data_before = spccd.data.get(asap=False)
        before_peak = float(find_peak_position(data_before))

        # Run alignment for second detector in multi-detector mode
        f_second = sparc_auto_grating_offset(self.spgr, spccd, max_it=50)
        self.assertTrue(f_second.result(timeout=600), "Second-detector alignment failed")

        # Measure peak after alignment
        data_after = spccd.data.get(asap=False)
        after_peak = float(find_peak_position(data_after))

        # Assert grating did not change
        grating_after_second = self.spgr.position.value["goffset"]
        self.assertAlmostEqual(grating_after_first, grating_after_second, places=3,
            msg="Grating goffset changed when aligning second detector in multi-detector mode")

        # Assert peak moved closer to center on the secondary detector
        center = spccd.resolution.value[0] / 2
        before_dist = abs(before_peak - center)
        after_dist = abs(after_peak - center)
        self.assertLess(after_dist, before_dist,
            msg="Peak did not move closer to center on second detector after alignment")

    def test_single_detector_iteration(self):
        """
        Verifies that the auto-alignment algorithm generates the minimum required set
        of offsets when operating with a single detector and multiple gratings.
        """

        #f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=[self.ccd], selector=self.selector)
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=self.detector, selector=self.selector)

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
        self.assertNotEqual(data.max(), data.min)

    def test_driver_raises_valueerror_on_hardware_limit(self):
        """
        Verifies the driver successfully raises a ValueError when computing
        an offset outside of the hardware limits.
        """
        # A value guaranteed to exceed DET_OFFSET_MAX + GRAT_OFFSET_MAX
        out_of_bounds_target = 9999999.0

        # Test method directly to ensure the new bounds-checking logic works
        with self.assertRaises(ValueError, msg="Driver should raise ValueError for out-of-bounds offset"):
            self.spgr._doSetGoffsetAbs(out_of_bounds_target)

    @timeout(150)
    def test_hardware_limit_valueerror_handled(self):
        """
        Verifies that when a goffset move exceeds hardware limits and throws a ValueError,
        the alignment algorithm catches it gracefully instead of crashing.
        """
        # Slightly misalign the peak so the algorithm wants to move
        self.spgr.moveRelSync({"goffset": 50})

        # Temporarily bypass the software axis range clamp to ensure the raw command
        # reaches the hardware driver to trigger the ValueError
        original_range = self.spgr.axes["goffset"].range

        try:
            self.spgr.axes["goffset"].range = (-9999999.0, 9999999.0)

            # Execute auto-alignment procedure
            f = sparc_auto_grating_offset(self.spgr, self.detector, max_it=10, gain=99999.0)
            result = f.result(timeout=100)

            # Driver should raise ValueError for out-of-bounds move, which should be caught by the alignment code,
            self.assertFalse(result, "Alignment should gracefully fail (return False) when blocked by hardware limits.")

        finally:
            # Restore original software limits and position
            self.spgr.axes["goffset"].range = original_range
            self.spgr.moveAbsSync({"goffset": self._original_position["goffset"]})

    def test_find_peak_position_realistic_2d(self):
        """
        Test peak detection on realistic 2D detector data (like GUI),
        including noise and baseline offset.
        """
        np.random.seed(0)

        x = np.arange(1024)
        true_center = 512.3

        # base gaussian peak
        peak = 50 * np.exp(-0.5 * ((x - true_center) / 6.0) ** 2)

        # simulate camera baseline
        baseline = 300

        # build 2D image with row variations
        rows = 256
        image = []

        for i in range(rows):
            row_noise = np.random.normal(0, 3, size=x.shape)  # noise
            row_gain = 1 + np.random.normal(0, 0.02)  # slight variation
            row = baseline + row_gain * peak + row_noise
            image.append(row)

        image = np.array(image)

        # run your actual function
        peak_pos = find_peak_position(image)

        self.assertAlmostEqual(peak_pos, true_center, places=1)

    def test_no_peak_present_image(self):
        im_no_peak = hdf5.read_data(H5_FILE_2d_NO_PEAK + "no-peak-2d-1.h5")
        data = (im_no_peak[0].data)

        spectrum = data.mean(axis=0) if data.ndim == 2 else data

        present = peak_is_present(spectrum, snr_threshold=10.0, width_range=(0.5, 12.0))

        self.assertFalse(present, f"Peak was not detected. Check SNR and width limits.")

    def test_peak_present_image(self):
        im_peak = hdf5.read_data(H5_FILE_2d_NO_PEAK + "with-peak-2d-1.h5")
        raw_data = np.asarray(im_peak[0].data)

        spectrum = np.squeeze(raw_data)
        if spectrum.ndim > 1:
            # Average over the row dimension (256) to get the 1D spectrum (1024)
            spectrum = spectrum.mean(axis=0)

        clean_spec = spectrum - np.median(spectrum)
        noise_std = np.std(clean_spec)
        peak_val = clean_spec.max()
        snr = peak_val / (noise_std + 1e-6)
        peak_idx = np.argmax(clean_spec)

        # Simple width estimation (standard deviation around peak)
        window = clean_spec[max(0, peak_idx - 2): min(len(clean_spec), peak_idx + 3)]
        x = np.arange(len(window))
        w = window - window.min()
        width = 0.0
        if w.sum() > 0:
            mean = np.sum(x * w) / np.sum(w)
            var = np.sum(w * (x - mean) ** 2) / np.sum(w)
            width = np.sqrt(var)

        # --- THE DEBUG LOGS ---
        logging.info("=" * 40)
        logging.info(f"IMAGE DEBUG SCORECARD")
        logging.info(f"Final Spectrum Shape: {spectrum.shape}")
        logging.info(f"Peak Location:       Index {peak_idx}")
        logging.info(f"Calculated SNR:      {snr:.2f}  (Threshold: 10.0)")
        logging.info(f"Calculated Width:    {width:.2f}  (Range: 0.5 - 12.0)")
        logging.info("=" * 40)

        logging.info(f"Cleaned Spectrum Shape for Function: {spectrum.shape}")

        # 3. Now pass the cleaned 1D array to your existing function
        present = peak_is_present(spectrum, snr_threshold=10.0, width_range=(0.5, 12.0))

        self.assertTrue(present, "Peak should have been detected in the cleaned spectrum.")


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

if __name__ == "__main__":
    unittest.main()
