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

    def _on_image(self, im):
        # Is called when an image is received in the live setting stream
        logging.info("image: dim %s" , im.shape)
        self._image = im

    def validate_scan_sim(self, remote, repetition, dwelltime, pixel_size):
        self.assertIsNotNone(remote.raw)
        self.assertIsNotNone(remote.raw[0].metadata)
        self.assertEqual(remote.raw[0].shape[::-1], repetition)
        self.assertEqual(remote.raw[0].metadata[MD_DWELL_TIME], dwelltime)
        # self.assertEqual(remote.raw[0].metadata[MD_PIXEL_SIZE], pixel_size)
        
        # Check MD_POS is remotely correct, by checking it's +- 10cm.
        pos = remote.raw[0].metadata[MD_POS]
        assert -0.1 < pos[0] < 0.1 and -0.1 < pos[1] < 0.1

    def test_flim_acq_simple(self):
        logging.debug("Testing acquisition")

        helper = stream.ScannedTCSettingsStream('Stream', self.detector, self.ex_light, self.lscanner,
                                                self.sft, self.apd, self.tc_scanner)

        remote = stream.ScannedRemoteTCStream("remote", helper)
        # Configure values and test acquisition for several short dwell times.
        for dwellTime in (2e-5, 10e-5, 2e-3, 8e-3, 100e-3):
            helper.dwellTime.value = dwellTime  # seconds
            if dwellTime < 5e-3:
                rep = (64, 64)
            else:  # Too long, use a smaller repetition
                rep = (64, 1)

            helper.roi.value = (0, 0, 0.1, 0.1)
            helper.repetition.value = rep
            f = remote.acquire()
            time.sleep(0.1)
            # Check we didn't ask for too short dwell time, which the hardware
            # do not support.
            self.assertLessEqual(self.lscanner.dwellTime.value, dwellTime)
            # Check local VA's to see if they are set correctly while acquiring. 
            self.assertTrue(any(em > 0) for em in self.ex_light.emissions.value)
            self.assertGreater(self.ex_light.power.value, 0)
            self.assertGreater(self.ex_light.period.value, 0)
            f.result()  # wait for the result. This blocks
            time.sleep(0.3)
            self.validate_scan_sim(remote, rep, helper.dwellTime.value,
                                   helper.time_correlator.getMetadata()[MD_PIXEL_SIZE])
            time.sleep(0.3)

    def test_repetitions_and_roi(self):
        logging.debug("Testing repetitions and roi")

        helper = stream.ScannedTCSettingsStream('Stream', self.detector, self.ex_light, self.lscanner,
                                                self.sft, self.apd, self.tc_scanner)
        
        remote = stream.ScannedRemoteTCStream("remote", helper)

        helper.dwellTime.value = 1e-3  # seconds
        helper.roi.value = (0, 0, 0.1, 0.1)
        helper.repetition.value = (64, 64)
        
        f = remote.acquire()
        time.sleep(0.1)
        f.cancel()
        
        self.assertEqual(helper.scanner.resolution.value, (64, 64))
        
        helper.roi.value = (0, 0.3, 0.9, 0.5)
        helper.repetition.value = (256, 64)

        f = remote.acquire()
        time.sleep(0.1)
        f.cancel()

        self.assertEqual(helper.scanner.resolution.value, (256, 64))

    def test_cancelling(self):
        logging.debug("Testing cancellation")

        helper = stream.ScannedTCSettingsStream('Stream', self.detector, self.ex_light, self.lscanner,
                                                self.sft, self.apd, self.tc_scanner)

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

        helper = stream.ScannedTCSettingsStream('Stream', self.detector, self.ex_light, self.lscanner,
                                                self.sft, self.apd, self.tc_scanner)
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


if __name__ == "__main__":
    unittest.main()

