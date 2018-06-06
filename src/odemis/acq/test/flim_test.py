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
from __future__ import division

import logging
from odemis import model
import odemis
from odemis.acq import stream
from odemis.model._metadata import MD_PIXEL_SIZE, MD_DWELL_TIME, MD_POS, MD_ACQ_DATE
from odemis.util import test
import os
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_FLIM_CONFIG = CONFIG_PATH + "sim/secom-flim-sim.odm.yaml"

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
            test.start_backend(SECOM_FLIM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.sft = model.getComponent(role="time-correlator")
        cls.tc_scanner = model.getComponent(role="tc-scanner")
        cls.ex_light = model.getComponent(role="light")
        cls.lscanner = model.getComponent(role="laser-mirror")
        cls.apd = model.getComponent(role="tc-detector")
        cls.tcdl = model.getComponent(role="tc-detector-live")
        cls.detector = model.getComponent(role="photo-detector0")

        cls.ex_light.period.value = 0.0001
        em = [0] * len(cls.ex_light.emissions.value)
        cls.ex_light.emissions.value = em
        cls.ex_light.power.value = 0.25

    @classmethod
    def tearDownClass(cls):
        time.sleep(1.0)

        if cls.backend_was_running:
            return
        test.stop_backend()

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
            self.assertTrue(any(em > 0) for em in self.ex_light.emissions.value)
            self.assertGreater(self.ex_light.power.value, 0)
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

        helper.repetition.value = (2047, 63)
        self.assertEqual(helper.repetition.value, (2048, 64))

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
        self.assertIsNotNone(self._image.metadata[MD_ACQ_DATE], "No metadata")
        self.assertEqual(self._image.ndim, 1)
        self.assertGreaterEqual(self._image.shape[0], 10, "Not enough data.")
        # make sure there are times for each value.
        self.assertEqual(len(self._image.metadata[MD_ACQ_DATE]), self._image.shape[0])

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
        self.assertIsNotNone(self._image.metadata[MD_ACQ_DATE], "No metadata")
        self.assertEqual(self._image.ndim, 1)
        # TODO: by default the simulator only sends data for 5s... every 1.5s => 3 data
        self.assertGreaterEqual(self._image.shape[0], 3, "Not enough data.")
        # make sure there are times for each value.
        self.assertEqual(len(self._image.metadata[MD_ACQ_DATE]), self._image.shape[0])

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
        scan_pxs = self.lscanner.pixelSize.value  # 2 floats
        self.assertEqual(fov, (pxs, pxs))


if __name__ == "__main__":
    unittest.main()

