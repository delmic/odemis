#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 7 Feb 2014

Copyright © 2014 Kimon Tsitsikas, Delmic

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
import queue

import numpy

from odemis.driver.ephemeron import STATE_NAME_IDLE, STATE_NAME_TRIGGER, STATE_NAME_BUSY
from odemis import model
from odemis.driver import ephemeron
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)

# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed"}
CONFIG_BSD = {"name": "bsd", "role": "bsd"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam"}
CONFIG_FOCUS = {"name": "focus", "role": "ebeam-focus"}
CONFIG_SEM = {"name": "sem", "role": "sem", "image": "simsem-fake-output.h5",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER,
                           "focus": CONFIG_FOCUS}
             }

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing


class TestMightyEBICDetector(unittest.TestCase):
    """
    Test case to test the functionality of the driver for an Ephemeron MightyEBIC detector.
    """
    @classmethod
    def setUpClass(cls):
        if not TEST_NOHW:
            cls.ebic_det = ephemeron.MightyEBIC("EBIC Scan Controller",
                                                "ebic-detector",
                                                2,
                                                "opc.tcp://192.168.56.2:4840/mightyebic/server/",
                                                "http://opcfoundation.org/UA/")
        else:
            # EBIC detector with a simulated server
            cls.ebic_det = ephemeron.MightyEBIC("EBIC Scan Controller",
                                                "ebic-detector",
                                                2,
                                                "opc.tcp://localhost:4840/freeopcua/server/",
                                                "http://examples.freeopcua.github.io")
        cls.acquired_data = None
        cls.dwell_time_values = [1e-5, 2e-5, 5e-5]
        cls.resolution_values = [(140, 100), (300, 420), (2100, 2100)]

    @classmethod
    def tearDownClass(cls):
        cls.ebic_det.terminate()

    def test_scan_duration(self):
        """
        Test to see if changing dependent scan properties will change the scan time estimation as well.
        """
        base_st = 0.0

        for dt_num, dt in enumerate(self.dwell_time_values):
            for res_num, res in enumerate(self.resolution_values):
                st_req = self.ebic_det._opc_client.get_scan_time(dt, res[0], res[1])
                if res_num == 0:  # take the first scan time as base
                    base_st = st_req
                else:
                    self.assertGreater(st_req, base_st)


    def test_acquisition(self):
        """
        Test for running acquisitions for a list of set dwell times.
        """
        # check if the scan controller is ready
        self.assertEqual(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)

        for dt in self.dwell_time_values:
            # set the repetition of the region of acquisition to 140 x 100, force it non-squared
            self.ebic_det.resolution.value = (140, 100)
            self.ebic_det.dwellTime.value = dt

            start = time.time()
            self.acquired_data = self.ebic_det.data.get()
            end = time.time()
            self.assertGreater(end, start)

            # check if the state is changed to stopped, now we know there is data available
            self.assertTrue(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)
            self.assertIsNotNone(self.acquired_data)  # there should be data acquired

            # check if the data is in the right shape
            self.assertEqual(self.acquired_data.shape, self.ebic_det.resolution.value)
            self.assertTrue(self.acquired_data.dtype.type == numpy.float64)

    @skip("dependent on Ephemeron for this to work")
    def test_acquisition_stop(self):
        # check if the scan controller is ready
        self.assertEqual(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)

        # set the repetition of the region of acquisition to 500 x 480, force it non-squared
        self.ebic_det.resolution.value = (500, 480)

        # start acquisition an acquisition threaded, to be able to stop it before it ends
        start = time.time()
        self.ebic_det.data.subscribe(self.receive_ebic_data)

        # stop the acquisition after a few seconds
        time.sleep(4)
        self.ebic_det.data.unsubscribe(self.receive_ebic_data)
        end = time.time()
        self.assertGreater(end, start)

        # check if the state is changed to stopped, now we know there is data available
        self.assertTrue(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)
        self.assertIsNone(self.acquired_data)  # there should be no data acquired

    def test_request_very_high_resolution(self):
        """
        This test should fire a timeout exception in the acquisition thread which is handled.
        Take the max res (4096, 3072) of the e-beam as resolution to scan.
        """
        # check if the scan controller is ready
        self.assertEqual(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)

        # set the resolution very high to force a time-out for the start method request on the server
        self.ebic_det.resolution.value = (4096, 3072)

        # start acquisition an acquisition threaded, to be able to stop it before it ends
        start = time.time()

        # this should fire a TimeoutException
        self.ebic_det.data.subscribe(self.receive_ebic_data)
        self.ebic_det._acquisition_thread.join()

        end = time.time()
        self.assertGreater(end, start)

        # check if the data is in the right shape
        self.assertIsNone(self.acquired_data)  # there should be no data acquired

    def test_change_state(self):
        """
        Test for checking to change the state, this test is mainly for the simulated opcServer.
        """
        # check if the scan controller is ready
        self.assertTrue(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)

        self.ebic_det._opc_client.set_controller_state(STATE_NAME_TRIGGER)
        self.assertTrue(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_TRIGGER)
        time.sleep(5)

        self.ebic_det._opc_client.set_controller_state(STATE_NAME_BUSY)
        self.assertTrue(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_BUSY)
        time.sleep(5)

        # set the controller back to a ready state
        self.ebic_det._opc_client.set_controller_state(STATE_NAME_IDLE)
        self.assertTrue(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)

    def receive_ebic_data(self, df, d):
        pass


if __name__ == "__main__":
    unittest.main()
