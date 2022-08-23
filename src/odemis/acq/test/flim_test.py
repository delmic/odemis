# -*- coding: utf-8 -*-
'''
Created on 9 April 2018

@author: Anders Muskens

Copyright © 2018 Anders Muskens, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import numpy
from odemis import model
import odemis
from odemis.acq import stream
from odemis.model import MD_PIXEL_SIZE, MD_DWELL_TIME, MD_POS, MD_TIME_LIST
from odemis.util import testing
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_FLIM_CONFIG = CONFIG_PATH + "sim/secom-flim-spt-sim.odm.yaml"

'''
To run these tests, you must run the Symphotime simulator.

The simulator can be found at https://github.com/PicoQuant/SymPhoTime_Remote_Interface
The PQ_ServerSimulator application is a Win32 or x64 based exe that simulates a Symphotime server
By default, it listens on 127.0.0.1:6000 for a connection. Just launch the EXE with default settings
in order to run these tests.
'''


class TestFlim(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            # The nikonc driver needs omniorb which is not packaged in Ubuntu anymore
            from odemis.driver import nikonc
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping FLIM tests, cannot import nikonc driver."
                                    f"Got error: {err}")

        try:
            testing.start_backend(SECOM_FLIM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find confocal (light + laser-mirror) and FLIM (time-correlator) components
        cls.sft = model.getComponent(role="time-correlator")
        cls.tc_scanner = model.getComponent(role="tc-scanner")
        cls.ex_light = model.getComponent(role="light")
        cls.lscanner = model.getComponent(role="laser-mirror")
        cls.apd = model.getComponent(role="tc-detector")
        cls.tcdl = model.getComponent(role="tc-detector-live")
        cls.detector = model.getComponent(role="photo-detector0")

        cls.ex_light.period.value = 0.0001
        cls.ex_light.power.value = list(cls.ex_light.range[0])

    @classmethod
    def tearDownClass(cls):
        time.sleep(1.0)

        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def _on_image(self, im):
        # Is called when an image is received in the live setting stream
        logging.info("image with shape %s, md= %s" , im.shape, im.metadata)
        self._image = im

    def validate_scan_sim(self, das, repetition, dwelltime, pixel_size):
        self.assertIsNotNone(das)
        self.assertIsNotNone(das[0].metadata)
        self.assertEqual(das[0].shape[::-1], repetition)
        self.assertAlmostEqual(das[0].metadata[MD_DWELL_TIME], dwelltime)
        md_pxs = das[0].metadata[MD_PIXEL_SIZE]
        self.assertAlmostEqual(md_pxs[0], pixel_size)
        self.assertAlmostEqual(md_pxs[1], pixel_size)

        # Check MD_POS is remotely correct, by checking it's +- 10cm.
        pos = das[0].metadata[MD_POS]
        assert -0.1 < pos[0] < 0.1 and -0.1 < pos[1] < 0.1

    def test_flim_acq_simple(self):
        logging.debug("Testing acquisition")

        helper = stream.ScannedTCSettingsStream("FLIM settings", self.apd, self.ex_light, self.lscanner,
                                                self.sft, self.tc_scanner)
        # FIXME: for now, the ROI computation is very picky, and we need to
        # hard-code the pixel size to be sure that the ROI is as expected.
        helper.pixelSize.value = 3.90625e-07  # m
        remote = stream.ScannedRemoteTCStream("Remote", helper)
        # Configure values and test acquisition for several short dwell times.
        for dwellTime in (2e-5, 10e-5, 2e-3, 8e-3, 100e-3):
            helper.dwellTime.value = dwellTime  # seconds
            if dwellTime < 5e-3:
                rep = (64, 64)
            else:  # Too long, use a smaller repetition
                rep = (64, 1)

            helper.roi.value = (0, 0, 0.1, 0.1)
            helper.repetition.value = rep
            self.assertEqual(helper.repetition.value, rep)
            f = remote.acquire()
            time.sleep(0.1)
            # Check we didn't ask for too short dwell time, which the hardware
            # do not support.
            self.assertLessEqual(self.lscanner.dwellTime.value, dwellTime)
            # Check local VA's to see if they are set correctly while acquiring.
            self.assertTrue(any(pw > 0 for pw in self.ex_light.power.value))
            self.assertGreater(self.ex_light.period.value, 0)
            das = f.result()  # wait for the result. This blocks
            self.assertEqual(das, remote.raw)
            das = remote.raw
            time.sleep(0.3)
            self.validate_scan_sim(das, rep, helper.dwellTime.value,
                                   helper.pixelSize.value)
            time.sleep(0.3)

    def _validate_rep(self, s):
        # Check that repetition is always compatible with the scanner, which can
        # only do power of two.
        rep = s.repetition.value
        hw_rep = self.lscanner.resolution.clip(rep)
        self.assertEqual(hw_rep, rep)

        # Check the repetition ratio is the same as the roi ratio (ie pixel is square)
        roi = s.roi.value
        roi_ratio = (roi[2] - roi[0]) / (roi[3] - roi[1])
        rep_ratio = rep[0] / rep[1]
        self.assertAlmostEqual(roi_ratio, rep_ratio)

    def test_repetitions_and_roi(self):
        logging.debug("Testing repetitions and roi")

        helper = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light, self.lscanner,
                                                self.sft, self.tc_scanner)

        remote = stream.ScannedRemoteTCStream("remote", helper)

        helper.dwellTime.value = 1e-3  # seconds
        helper.roi.value = (0, 0, 0.1, 0.1)
        helper.repetition.value = (64, 64)
        self._validate_rep(helper)

        f = remote.acquire()
        time.sleep(0.1)
        f.cancel()

        self.assertEqual(helper.scanner.resolution.value, (64, 64))

        # Try "wrong" repetition values
        helper.pixelSize.value *= 1.1
        self._validate_rep(helper)
        helper.roi.value = (0.1, 0.1, 0.2, 0.3)
        self._validate_rep(helper)
        helper.repetition.value = (64, 64)
        self.assertEqual(helper.repetition.value, (64, 64))
        helper.repetition.value = (62, 63)
        self.assertEqual(helper.repetition.value, (64, 64))

        # Go from a small rep to a big rep (horizontally), and check it's accepted.
        # First, set a small ROI (but not too small), so that it's possible to
        # extend it by increasing the rep, and still keep the same area.
        helper.roi.value = (0.4, 0.4, 0.6, 0.6)
        self._validate_rep(helper)
        helper.repetition.value = (64, 64)
        self.assertEqual(helper.repetition.value, (64, 64))

        # ... and now, increase the rep.
        helper.repetition.value = (2047, 63)
        self.assertEqual(helper.repetition.value, (2048, 64))

        # Try acquiring with a non-square ROI
        helper.roi.value = (0, 0.3, 0.9, 0.5)
        helper.repetition.value = (256, 64)

        f = remote.acquire()
        time.sleep(0.1)
        f.cancel()

        self.assertEqual(helper.scanner.resolution.value, (256, 64))

        helper.roi.value = (0, 0, 0.1, 0.1)
        helper.repetition.value = (64, 64)
        self._validate_rep(helper)

        helper.repetition.value = (64, 1)
        self._validate_rep(helper)

        helper.repetition.value = (1, 64)
        self._validate_rep(helper)

    def test_cancelling(self):
        logging.debug("Testing cancellation")

        helper = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light, self.lscanner,
                                                self.sft, self.tc_scanner)

        remote = stream.ScannedRemoteTCStream("remote", helper)

        # Configure values and test acquisition for several dwell times.
        for dwellTime in (10e-3, 100e-3, 1):
            helper.dwellTime.value = dwellTime  # seconds
            helper.repetition.value = (64, 64)
            helper.roi.value = (0.15, 0.6, 0.8, 0.8)
            f = remote.acquire()
            time.sleep(0.5)
            f.cancel()
            self.assertTrue(f.cancelled())
            time.sleep(0.3)

    def test_setting_stream(self):

        helper = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light, self.lscanner,
                                                self.sft, self.tc_scanner)
        spots = stream.SpotScannerStream("spot", helper.tc_detector,
                                     helper.tc_detector.data, helper.scanner)
        # Test start and stop of the apd.
        self._image = None
        helper.image.subscribe(self._on_image)

        spots.should_update.value = True
        spots.is_active.value = True

        # shouldn't affect
        helper.roi.value = (0.15, 0.6, 0.8, 0.8)
        helper.repetition.value = (1, 1)

        helper.dwellTime.value = 0.1  # s
        helper.windowPeriod.value = 10  # s

        # Start acquisition
        helper.should_update.value = True
        helper.is_active.value = True

        # move spot
        time.sleep(8.0)
        spots.roi.value = (0.1, 0.3, 0.1, 0.3)
        time.sleep(2.0)
        spots.roi.value = (0.5, 0.2, 0.5, 0.2)
        time.sleep(2.0)

        # move spot
        helper.image.unsubscribe(self._on_image)
        helper.is_active.value = False
        spots.is_active.value = False

        self.assertIsNotNone(self._image, "No data received")
        self.assertIsInstance(self._image, model.DataArray)
        self.assertIsNotNone(self._image.metadata[MD_TIME_LIST], "No metadata")
        self.assertEqual(self._image.ndim, 1)
        self.assertGreaterEqual(self._image.shape[0], 10, "Not enough data.")
        # make sure there are times for each value.
        self.assertEqual(len(self._image.metadata[MD_TIME_LIST]), self._image.shape[0])

    def test_setting_stream_rep(self):
        """
        Check the ROI <> Repetition <> pixelSize connections work fine
        """

        helper = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light, self.lscanner,
                                                self.sft, self.tc_scanner)

        # Ask full ROI -> should have full ROI
        helper.roi.value = (0, 0, 1, 1)
        helper.repetition.value = (256, 256)
        numpy.testing.assert_almost_equal(helper.roi.value, (0, 0, 1, 1))
        self.assertEqual(helper.repetition.value, (256, 256))

        pxs256f = helper.pixelSize.value

        # Ask half ROI -> should have half the repetition (and same pixel size)
        helper.roi.value = (0.5, 0.5, 1, 1)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.5, 0.5, 1, 1))
        self.assertEqual(helper.repetition.value, (128, 128))
        self.assertAlmostEqual(helper.pixelSize.value, pxs256f)

        # Move the ROI around -> no change in repetition/pixel size
        helper.roi.value = (0.25, 0.25, 0.75, 0.75)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.25, 0.25, 0.75, 0.75))
        self.assertEqual(helper.repetition.value, (128, 128))
        self.assertAlmostEqual(helper.pixelSize.value, pxs256f)

        # Increase the ROI by "a little" -> as the laser-mirror doesn't support
        # just a little bit more repetition, there is in theory two options:
        # * the repetition should stay the same and pixel size increase
        # * the repetition increases, and pixel size decreases
        # Currently the implementation does the second option
        helper.roi.value = (0.20, 0.20, 0.80, 0.80)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.20, 0.20, 0.80, 0.80))
        self.assertEqual(helper.repetition.value, (256, 256))
        self.assertLess(helper.pixelSize.value, pxs256f)
        pxs256s = helper.pixelSize.value

        # Same thing but only change in one dimension (typical, from the GUI)
        # ROI X is twice smaller -> exactly twice less rep, that's easy
        helper.roi.value = (0.20, 0.20, 0.50, 0.80)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.20, 0.20, 0.50, 0.80))
        self.assertEqual(helper.repetition.value, (128, 256))
        self.assertAlmostEqual(helper.pixelSize.value, pxs256s)

        # Slightly decrease ROI X
        # -> rep stays the same (because the hardware rounds up), pxs stays the same.
        # ROI adjusted slightly on X (ideally it wouldn't), but size is the same
        helper.roi.value = (0.20, 0.20, 0.45, 0.80)
        self.assertEqual(helper.repetition.value, (128, 256))
        self.assertAlmostEqual(helper.pixelSize.value, pxs256s)
        roi = helper.roi.value
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        numpy.testing.assert_almost_equal(roi_size, (0.3, 0.6))

        # Move (back) => everything as requested
        helper.roi.value = (0.20, 0.20, 0.50, 0.80)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.20, 0.20, 0.50, 0.80))

        # Slightly increase ROI X
        # -> rep increases (on X), as the hardware rounds up, it actually doubles,
        # -> the ROI also doubles on X
        # while pixel size stays mostly the same
        helper.roi.value = (0.20, 0.20, 0.60, 0.80)
        self.assertEqual(helper.repetition.value, (256, 256))
        self.assertAlmostEqual(helper.pixelSize.value, pxs256s)
        roi = helper.roi.value
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        numpy.testing.assert_almost_equal(roi_size, (0.6, 0.6))

        # Decrease pixel size / 2 -> ROI stays the same, rep * 2
        orig_roi = helper.roi.value
        helper.pixelSize.value = pxs256s / 2
        self.assertAlmostEqual(helper.pixelSize.value, pxs256s / 2)
        self.assertEqual(helper.repetition.value, (512, 512))
        numpy.testing.assert_almost_equal(helper.roi.value, orig_roi)

        # Rep = 1 -> ROI should be square
        helper.roi.value = (0.15, 0.6, 0.8, 0.8)
        helper.repetition.value = (1, 1)
        self.assertEqual(helper.repetition.value, (1, 1))
        roi = helper.roi.value
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        numpy.testing.assert_almost_equal(roi_size[0], roi_size[0])

    def test_setting_stream_small_roi(self):
        """
        Check the small ROI can be acquire with large rep. In such case, the
        pixel size will be smaller than the scanner pixel size (ie, scale < 1).
        """

        helper = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light, self.lscanner,
                                                self.sft, self.tc_scanner)

        # Ask full ROI -> should have full ROI
        helper.roi.value = (0, 0, 1, 1)
        helper.repetition.value = (2048, 2048)
        numpy.testing.assert_almost_equal(helper.roi.value, (0, 0, 1, 1))
        self.assertEqual(helper.repetition.value, (2048, 2048))
        pxs_full = helper.pixelSize.value

        # quarter of the ROI (ie, half of each dim), still should be able to do 2048x2048
        helper.roi.value = (0.25, 0.25, 0.75, 0.75)
        helper.repetition.value = (2048, 2048)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.25, 0.25, 0.75, 0.75))
        self.assertEqual(helper.repetition.value, (2048, 2048))
        self.assertAlmostEqual(helper.pixelSize.value, pxs_full / 2)

    def test_setting_stream_tcd_live(self):
        """
        Test live setting stream acquisition with the tc_detector_live
        """
        helper = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light,
                         self.lscanner, self.sft, self.tc_scanner, self.tcdl)
        spots = stream.SpotScannerStream("spot", helper.tc_detector,
                                     helper.tc_detector.data, helper.scanner)
        # Test start and stop of the apd.
        self._image = None
        helper.image.subscribe(self._on_image)

        spots.should_update.value = True
        spots.is_active.value = True

        # shouldn't affect
        helper.roi.value = (0.15, 0.6, 0.8, 0.8)
        helper.repetition.value = (1, 1)

        helper.dwellTime.value = 0.1  # s
        helper.windowPeriod.value = 10  # s

        # Start acquisition
        helper.should_update.value = True
        helper.is_active.value = True

        # move spot
        time.sleep(8.0)
        spots.roi.value = (0.1, 0.3, 0.1, 0.3)
        time.sleep(2.0)
        spots.roi.value = (0.5, 0.2, 0.5, 0.2)
        time.sleep(2.0)

        # move spot
        helper.image.unsubscribe(self._on_image)
        helper.is_active.value = False
        spots.is_active.value = False

        self.assertIsNotNone(self._image, "No data received")
        self.assertIsInstance(self._image, model.DataArray)
        self.assertIsNotNone(self._image.metadata[MD_TIME_LIST], "No metadata")
        self.assertEqual(self._image.ndim, 1)
        # TODO: by default the simulator only sends data for 5s... every 1.5s => 3 data
        self.assertGreaterEqual(self._image.shape[0], 3, "Not enough data.")
        # make sure there are times for each value.
        self.assertEqual(len(self._image.metadata[MD_TIME_LIST]), self._image.shape[0])

    def test_rep_ss(self):
        """
        Test the RepetitionStream part of the ScannedTCSettingsStream,
        when used on a confocal microscope, and especially the NikonC2
        """
        # Create the stream
        tcs = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light, self.lscanner,
                                             self.sft, self.tc_scanner)
        self._image = None
        tcs.image.subscribe(self._on_image)

        self.assertEqual(tcs.repetition.range[1], self.lscanner.shape,
                         "Maximum repetition not equal to the hardware max resolution")

        sshape = self.lscanner.shape
        # For now, we assume the scanner has a square shape
        assert sshape[0] == sshape[1]

        # Check that the pixel size is properly computed
        # We assume there is no PIXEL_SIZE_COR

        # To compute the full FoV: pixel size * shape (shape == scale * res)
        fov = tuple(ps * sh for ps, sh in
                    zip(self.lscanner.pixelSize.value, self.lscanner.shape))

        # At full FoV, with the max resolution, it should be the same as PIXEL_SIZE
        tcs.roi.value = (0, 0, 1, 1)  # Full FoV
        tcs.repetition.value = tcs.repetition.range[1]
        pxs = tcs.pixelSize.value  # 1 float
        scan_pxs = self.lscanner.pixelSize.value  # 2 floats
        self.assertEqual(scan_pxs, (pxs, pxs))
        s_fov = pxs * tcs.repetition.value[0], pxs * tcs.repetition.value[1]
        self.assertEqual(fov, s_fov)

        # At full FoV, with rep = 1, 1, the pixelSize should be the same as the whole FoV
        tcs.roi.value = (0, 0, 1, 1)  # Full FoV
        tcs.repetition.value = (1, 1)
        assert tcs.roi.value == (0, 0, 1, 1)
        assert tcs.repetition.value == (1, 1)
        pxs = tcs.pixelSize.value  # 1 float
        self.assertEqual(fov, (pxs, pxs))


if __name__ == "__main__":
    unittest.main()
