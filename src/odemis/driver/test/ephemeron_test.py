#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 9 Oct 2024

Copyright © 2024-2025 Stefan Sneep & Éric Piel, Delmic

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
import asyncio
import logging
import os
import threading
import time
import unittest
from typing import Optional

import numpy

from odemis import model
from odemis.dataio import hdf5
from odemis.driver import ephemeron, semnidaq
from odemis.driver.ephemeron import MightyEBICSimulator

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

KWARGS_EBIC = {
    "name": "EBIC Scan Controller",
    "role": "ebic-detector",
    "channel": 0,
    # If testing is done with the MightyEBIC Software on a VM, use this
    # "url": "opc.tcp://192.168.56.2:4840/mightyebic/server/"
    "url": "opc.tcp://172.16.0.1:4840/mightyebic/server/"
}

if TEST_NOHW:
    KWARGS_EBIC["url"] = "fake"

# For the semnidaq driver
CONFIG_SED = {
    "name": "sed",
    "role": "sed",
    "channel": 0,
    # "channel": "ao0",  # Loopback from the AO0, for testing
    "limits": [-3, 6.2]
}

CONFIG_SCANNER = {
    "name": "scanner",
    "role": "ebeam",
    "channels": [0, 1],
    "max_res": [4096, 3072], # px, to force 4:3 ratio
    "limits": [[-2.2333, 2.2333], [-1.675, 1.675]],  # V
    "park": [-5, -5], # V
    "settle_time": 120e-6,  # s
    "scan_active_delay": 0.001,  # s
    "hfw_nomag": 0.112,
    "scanning_ttl": {
        3: [True, True, "external"],  # High when scanning, High when VA set to True
        4: [True, True, None],
    },
    "image_ttl": {
        "pixel": {
             "ports": [16],
             "inverted": [True],
        },
    },
}

CONFIG_SEM = {
    "name": "sem",
    "role": "sem",
    "device": "Dev1",
    "multi_detector_min_period": 2e-6,  # s,
    "children": {
        "scanner": CONFIG_SCANNER,
        "detector0": CONFIG_SED,
    }
}

class TestMightyEBICSyncAcq(unittest.TestCase):
    """
    Test case to test the MightyEBIC detector in a synchronous acquisition with the e-beam
    """
    @classmethod
    def setUpClass(cls):
        cls.ebic = ephemeron.MightyEBIC(**KWARGS_EBIC)

        cls.sem = semnidaq.AnalogSEM(**CONFIG_SEM)
        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearDownClass(cls):
        cls.ebic.terminate()
        cls.sem.terminate()

    def test_acquisition(self):
        res = (900, 700)  # X,Y
        dt = 10e-6  # s
        self.ebic.resolution.value = res
        self.ebic.dwellTime.value = dt
        act_dt_ebic = self.ebic.dwellTime.value
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = res
        self.scanner.dwellTime.value = act_dt_ebic
        act_dt_ebeam = self.scanner.dwellTime.value
        logging.debug("EBIC will use dt = %s, e-beam will use dt = %s", act_dt_ebic, act_dt_ebeam)
        assert act_dt_ebic <= act_dt_ebeam

        # Start acquisition
        # Add 10% to the scan time to account roughly for the "flyback time" and other scanning overhead
        expected_duration = res[0] * res[1] * act_dt_ebeam  * 1.1
        self.ebic_data = None
        self.ebeam_data = None
        self.ebic_received = threading.Event()
        self.ebeam_received = threading.Event()
        self.ebic.data.subscribe(self.receive_ebic_data)
        time.sleep(0.1)  # Gives a bit of time to be ready
        self.sed.data.subscribe(self.receive_ebeam_data)

        # Data should arrive approximately at the same time
        self.ebeam_received.wait(expected_duration * 1.5)  # 50% margin for time-out
        self.ebic_received.wait(10)  # Gives a bit of margin to the EBIC to receive the data
        assert self.ebeam_received.is_set() and self.ebic_received.is_set()

        assert self.ebeam_data.shape == self.ebic_data.shape

        # Simulate the acquisition code, and confirm the data can be stored
        self.ebic_data.metadata[model.MD_PIXEL_SIZE] = self.ebeam_data.metadata[model.MD_PIXEL_SIZE]
        self.ebic_data.metadata[model.MD_POS] = self.ebeam_data.metadata[model.MD_POS]
        hdf5.export("test_ebeam.h5", [self.ebeam_data, self.ebic_data])

    def receive_ebic_data(self, df, d: model.DataArray):
        logging.debug("Received EBIC data")
        self.ebic_data = d
        self.ebic_received.set()
        df.unsubscribe(self.receive_ebic_data)

    def receive_ebeam_data(self, df, d: model.DataArray):
        logging.debug("Received e-beam data")
        self.ebeam_data = d
        self.ebeam_received.set()
        df.unsubscribe(self.receive_ebeam_data)


class TestMightyEBICDetector(unittest.TestCase):
    """
    Test case to test the functionality of the driver for an Ephemeron MightyEBIC detector.
    """
    @classmethod
    def setUpClass(cls):
        cls.ebic_det = ephemeron.MightyEBIC(**KWARGS_EBIC)
        cls.acquired_data: Optional[model.DataArray] = None
        cls.dwell_time_values = [cls.ebic_det.dwellTime.range[0], 6e-6, 10e-6, 11.9e-6, 12e-6, 50e-6, 1.995e-3, 1.996e-3]

    @classmethod
    def tearDownClass(cls):
        cls.ebic_det.terminate()

    def test_dwell_time(self):
        """
        Test for setting the dwell time of the detector, and check that the actual value set
        is always less or equal to the requested value.
        """
        for dt in self.dwell_time_values:
            self.ebic_det.dwellTime.value = dt
            self.assertLessEqual(self.ebic_det.dwellTime.value, dt)

    def test_acquisition(self):
        """
        Test acquisitions with various dwell times and resolutions.
        Note: when testing on the real hardware, there should be a pixel trigger sent (eg, by using
        a waveform generator).
        """
        for i, dt in enumerate(self.dwell_time_values):
            # Set the resolution of the region of acquisition to be non-squared, and not (always) a
            # multiple of 2.
            if dt < 1e-3:
                # Dwell time is short, so we can afford a larger image
                res = (140, 500 + i)
            else:
                # Don't make it too long for the long dwell time
                res = (100 + i, 100)
            self.ebic_det.resolution.value = res
            self.ebic_det.dwellTime.value = dt

            act_dt = self.ebic_det.dwellTime.value
            exp_dur = res[0] * res[1] * act_dt # Very minimum duration that it should take
            logging.debug("Expected duration for %s px @ %s s: %s s", res, act_dt, exp_dur)

            start_time = time.time()
            da = self.ebic_det.data.get()
            duration = time.time() - start_time

            # check if the data is in the right shape
            self.assertEqual(da.shape[::-1], self.ebic_det.resolution.value)
            self.assertEqual(da.dtype.type, numpy.float64)
            self.assertGreater(da.metadata[model.MD_ACQ_DATE], start_time)  # Should be a tiny bit later
            self.assertGreater(duration, exp_dur)

    def test_request_very_high_resolution(self):
        """
        Check that a large resolution also works (4096 x 3072)
        """
        # set the resolution very high to force a time-out for the start method request on the server
        self.ebic_det.resolution.value = (4096, 3072)
        self.ebic_det.dwellTime.value = self.ebic_det.dwellTime.range[0]

        da = self.ebic_det.data.get()

        # check if the data is in the right shape
        self.assertEqual(da.shape[::-1], self.ebic_det.resolution.value)
        self.assertEqual(da.dtype.type, numpy.float64)

    def test_acquisition_stop(self):
        # set the resolution of the region of acquisition to 500 x 480, force it non-squared
        self.ebic_det.resolution.value = (500, 480)
        self.ebic_det.dwellTime.value = 100e-6
        # => ~20s

        self.acquired_data = None
        self.ebic_det.data.subscribe(self.receive_ebic_data)

        # stop the acquisition after a few seconds
        time.sleep(4)
        self.ebic_det.data.unsubscribe(self.receive_ebic_data)
        self.assertIsNone(self.acquired_data)  # there should be no data acquired

        # check if the state is changed to stopped
        time.sleep(1)
        self.assertEqual(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)

    def receive_ebic_data(self, df, d):
        self.acquired_data = d

    def test_disconnection(self):
        """
        Check the driver can automatically reconnect to the server after a disconnection.
        """
        # Can only be automatically tested via the simulator
        if not TEST_NOHW:
            self.skipTest("Cannot automatically check disconnection with real hardware")

        # Check it works fine before. Changing the dwell time requires (multiple) remote calls, so
        # it's a good check on the connection
        self.ebic_det.dwellTime.value = 10e-6
        self.assertLessEqual(self.ebic_det.dwellTime.value, 10e-6)

        # Simulate disconnection
        self.ebic_det._opc_server_sim.terminate()
        time.sleep(1)
        logging.debug("Restarting simulator")
        self.ebic_det._opc_server_sim = MightyEBICSimulator(self.ebic_det._url, self.ebic_det)
        time.sleep(1)

        self.ebic_det.dwellTime.value = 10e-6
        self.assertLessEqual(self.ebic_det.dwellTime.value, 10e-6)

if __name__ == "__main__":
    unittest.main()
