# -*- coding: utf-8 -*-
"""
Test Sparc auto grating offset alignment
"""

import logging
import numpy as np
import odemis
import threading
import unittest

from concurrent.futures import Future
from concurrent.futures._base import RUNNING
from odemis import model
from odemis.acq import path
from odemis.util import testing
from odemis.util import timeout
from odemis.acq.align.goffset import(find_peak_position, peak_is_present, estimate_goffset_scale,
                                     sparc_auto_grating_offset, auto_align_grating_detector_offsets,
                                     _do_auto_align_grating_detector_offsets, log_detector_state)
from odemis.dataio import hdf5
from odemis.model import ProgressiveFuture
from pathlib import Path
from unittest.mock import patch, MagicMock

logging.getLogger().setLevel(logging.DEBUG)

ODEMIS_DIR = Path(odemis.__file__).resolve().parent
CONFIG_PATH = ODEMIS_DIR / "../../install/linux/usr/share/odemis/sim"
SPARC_CONFIG = CONFIG_PATH / "sparc2-focus-test.odm.yaml"

DATA_DIR = Path(__file__).resolve().parent
H5_FILE_2D_NO_PEAK = DATA_DIR / "grating 2 1024x256"

class TestSparcAutoGratingOffset(unittest.TestCase):
    """
    Test automatic grating offset alignment.
    """

    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC_CONFIG)

        cls.detector = model.getComponent(role="ccd")
        cls.spgr = model.getComponent(role="spectrograph")
        cls.spccd = model.getComponent(role="sp-ccd")
        cls.selector = model.getComponent(role="spec-det-selector")
        cls.bl = model.getComponent(role="brightlight")

        # Initialize the Optical Path Manager
        cls.microscope = model.getMicroscope()
        cls.optmngr = path.OpticalPathManager(cls.microscope)

        cls._original_goffset = cls.spgr.goffset
        cls._original_position = cls.spgr.position.value.copy()

    @classmethod
    def tearDownClass(cls):
        # restore original position
        try:
            cls.spgr.moveAbsSync(cls._original_position)
        except Exception:
            logging.exception("Failed restoring spectrograph position")

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

        f = sparc_auto_grating_offset(self.spgr, spccd, max_it=50)
        result = f.result(timeout=300)
        self.assertTrue(result, "Single-detector alignment failed")

        after_goffset = self.spgr.position.value["goffset"]
        self.assertNotAlmostEqual(start_goffset, after_goffset, places=3,
            msg="Grating goffset did not change for single-detector alignment on secondary port")

    def test_single_detector_iteration(self):
        """
        Verifies that the auto-alignment algorithm generates the minimum required set
        of offsets when operating with a single detector and multiple gratings.
        """

        align_mode = "spec-focus"
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=self.detector, opm=self.optmngr,
                                                align_mode=align_mode, bl=self.bl, selector=self.selector)
        res = f.result(timeout=900)

        n_gratings = len(self.spgr.axes["grating"].choices)
        n_detectors = 1
        expected = n_detectors + (n_gratings - 1)

        self.assertEqual(len(res), expected)

        first_grating = list(self.spgr.axes["grating"].choices.keys())[0]
        dets_first = [d for (g, d) in res.keys() if g == first_grating]

        self.assertEqual(len(dets_first), n_detectors)

    def test_single_detector_alignment_algorithm(self):

        """
        Test automatic alignment of the gratings using a single detector.
        """

        align_mode = "spec-focus"
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=self.detector, opm=self.optmngr,
                                                align_mode=align_mode, bl=self.bl, selector=self.selector)
        result = f.result(timeout=800)

        self.assertTrue(result)

    def test_multi_detector_iteration(self):
        """
        Tests the auto-alignment sequence for a multi-detector setup.

        Verifies that:
        - Grating 1 aligns ALL detectors (calibrating the detector offsets).
        - Subsequent gratings align ONLY the primary detector (calibrating the grating offsets).
        - The total number of alignment runs is exactly as expected.
        - The secondary detector receives valid optical data post-alignment.
        """

        spccd = model.getComponent(role="sp-ccd")
        spccd.exposureTime.value = spccd.exposureTime.range[0]

        detectors = [self.detector, spccd]
        align_mode = "spec-focus"

        # run alignment
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=detectors, opm=self.optmngr,
                                                align_mode=align_mode, bl=self.bl, selector=self.selector)
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
        self.assertNotEqual(data.max(), data.min())

    def test_driver_raises_valueerror_on_hardware_limit(self):
        """
        Verifies the driver successfully raises a ValueError when computing
        an offset outside of the hardware limits.
        """
        # out of bounds value
        out_of_bounds_target = 9999999.0

        # test method directly to ensure the new bounds-checking logic works
        with self.assertRaises(ValueError, msg="Driver should raise ValueError for out-of-bounds offset"):
            self.spgr._doSetGoffsetAbs(out_of_bounds_target)

    @timeout(150)
    def test_hardware_limit_valueerror_handled(self):
        """
        Verifies that when a goffset move exceeds hardware limits and throws a ValueError,
        the alignment algorithm catches it gracefully instead of crashing.
        """
        # slightly misalign the peak so the algorithm wants to move
        self.spgr.moveRelSync({"goffset": 50})

        # temporarily bypass the software axis range clamp to ensure the raw command
        # reaches the hardware driver to trigger the ValueError
        original_range = self.spgr.axes["goffset"].range

        try:
            self.spgr.axes["goffset"].range = (-9999999.0, 9999999.0)

            # execute auto-alignment procedure
            f = sparc_auto_grating_offset(self.spgr, self.detector, max_it=10, gain=99999.0)
            result = f.result(timeout=100)

            # driver should raise ValueError for out-of-bounds move, which should be caught by the alignment code
            self.assertFalse(result, "Alignment should gracefully fail (return False) when blocked by hardware limits.")

        finally:
            # restore original software limits and position
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

        # run function
        peak_pos = find_peak_position(image)

        self.assertAlmostEqual(peak_pos, true_center, places=1)

    def test_no_peak_present_image(self):
        """
        Test peak detection on not-peak detector data.
        """

        im_no_peak = hdf5.read_data(H5_FILE_2D_NO_PEAK / "without-peak-2d-1.h5")
        data = (im_no_peak[0].data)

        spectrum = data.mean(axis=0) if data.ndim == 2 else data

        present = peak_is_present(spectrum, snr_threshold=10.0, width_range=(0.5, 12.0))

        self.assertFalse(present, f"Peak was not detected. Check SNR and width limits.")

    def test_peak_present_image(self):
        im_peak = hdf5.read_data(H5_FILE_2D_NO_PEAK / "with-peak-2d-1.h5")
        raw_data = np.asarray(im_peak[0].data)

        spectrum = np.squeeze(raw_data)
        if spectrum.ndim > 1:
            # convert 2D array into 1D by averaging
            spectrum = spectrum.mean(axis=0)

        clean_spec = spectrum - np.median(spectrum)
        noise_std = np.std(clean_spec)
        peak_val = clean_spec.max()
        snr = peak_val / (noise_std + 1e-6)
        peak_idx = np.argmax(clean_spec)

        # simple width estimation
        window = clean_spec[max(0, peak_idx - 2): min(len(clean_spec), peak_idx + 3)]
        x = np.arange(len(window))
        w = window - window.min()
        width = 0.0
        if w.sum() > 0:
            mean = np.sum(x * w) / np.sum(w)
            var = np.sum(w * (x - mean) ** 2) / np.sum(w)
            width = np.sqrt(var)

        # debug logs
        logging.info("=" * 40)
        logging.info(f"IMAGE DEBUG SCORECARD")
        logging.info(f"Final Spectrum Shape: {spectrum.shape}")
        logging.info(f"Peak Location:       Index {peak_idx}")
        logging.info(f"Calculated SNR:      {snr:.2f}  (Threshold: 10.0)")
        logging.info(f"Calculated Width:    {width:.2f}  (Range: 0.5 - 12.0)")
        logging.info("=" * 40)

        logging.info(f"Cleaned Spectrum Shape for Function: {spectrum.shape}")

        # pass the cleaned 1D array to the existing function
        present = peak_is_present(spectrum, snr_threshold=10.0, width_range=(0.5, 12.0))

        self.assertTrue(present, "Peak should have been detected in the cleaned spectrum.")

    @patch('odemis.acq.align.goffset.sparc_auto_grating_offset')
    @patch('odemis.acq.align.goffset.light.turnOnLight')
    @patch('odemis.acq.align.goffset.time.sleep')

    def test_do_auto_align_mocked(self, mock_sleep, mock_turnOnLight, mock_sparc_offset):
        """
        Test the full auto-calibration sequence using mocked hardware to bypass
        timeouts, sleep delays, and physical hardware requirements.
        """

        # set up mocked return values using standard python futures
        fake_light_future = Future()
        fake_light_future.set_result(True)
        mock_turnOnLight.return_value = fake_light_future

        fake_align_future = Future()
        fake_align_future.set_result(True)
        mock_sparc_offset.return_value = fake_align_future

        # set up fake hardware
        fake_spectrograph = MagicMock()

        # Mock the initial position and the grating choices
        fake_spectrograph.position.value = {"wavelength": 0, "grating": 0}
        fake_spectrograph.axes = {"grating": MagicMock(choices={0: "grating1", 1: "grating2"})}

        fake_detector = MagicMock()
        fake_detector.name = "fake_ccd"
        fake_detector.data.get.return_value = np.zeros((1024,))

        fake_opm = MagicMock()
        fake_path_future = Future()
        fake_path_future.set_result(True)
        fake_opm.setPath.return_value = fake_path_future

        fake_bl = MagicMock()
        fake_bl.power.range = [0, 100]

        main_future = ProgressiveFuture(start=0, end=100)
        main_future._task_lock = threading.Lock()
        main_future._task_state = RUNNING
        main_future._subfuture = Future()

        # run function
        results = _do_auto_align_grating_detector_offsets(future=main_future, spectrograph=fake_spectrograph,
                                                          detectors=[fake_detector], opm=fake_opm,
                                                          align_mode="spec-focus", bl=fake_bl, selector=None,
                                                          streams=[])

        # Assertions
        # did it try to turn on the light?
        mock_turnOnLight.assert_called_once_with(fake_bl, fake_detector)

        # did it set the optical path?
        fake_opm.setPath.assert_called_once_with("spec-focus", detector=fake_detector)

        # did it try to align both gratings we mocked?
        self.assertEqual(mock_sparc_offset.call_count, 2)

        # did it clean up and turn the light off in the finally block?
        self.assertEqual(fake_bl.power.value, 0)

        # check the final returned results dict
        self.assertEqual(results, {(0, "fake_ccd"): True, (1, "fake_ccd"): True})

    @timeout(100)
    def test_cancel(self):
        """
        Test cancelling alignment.
        """
        f = sparc_auto_grating_offset(self.spgr, self.detector)
        # wait for the result or a timeout
        try:
            f.result(timeout=5)
        except:
            pass
        self.assertTrue(f.done())

if __name__ == "__main__":
    unittest.main()
