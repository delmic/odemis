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
import queue

import numpy

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


class TestMightyEBICDetector(unittest.TestCase):
    """
    Test case to test the instantiation of a driver for the Ephemeron MightyEBIC detector
    """
    @classmethod
    def setUpClass(cls):
        cls.ebic_det = ephemeron.MightyEbic("EBIC Controlbox",
                                            "ebic-detector",
                                            2,
                                            "opc.tcp://192.168.56.2:4840/mightyebic/server/",
                                            "http://opcfoundation.org/UA/")
        cls.acquired_data = None
        cls._data = queue.Queue()
        # time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        cls.ebic_det.terminate()

    @skip
    def test_scan_duration(self):
        # see if changing dependant property changes the scan time estimation as well

        dwell = self.ebic_det.dwellTime.value
        size = self.ebic_det.resolution.value
        dur = size[0] * size[1] * dwell
        logging.debug("expecting a %s s acquisition", dur)

        return dur

    @skip
    def test_dwell_time(self):
        # set a requested dt, as a user would do in the GUI and then see if the actual dt used
        # equals the requested one. Test also with very low and very high dt keep realistic


        # Dwell time should accept anything, but round down to the closest multiple of 100ns.
        self.ebic_det.dwellTime.value = 1e-6
        self.assertEqual(self.ebic_det.dwellTime.value, 1e-6)

        self.ebic_det.dwellTime.value = 1.16e-6
        self.assertAlmostEqual(self.ebic_det.dwellTime.value, 1.1e-6, delta=0.1e-9)

    def test_acquisition(self):
        # first check if the scan controller is idle
        self.assertEqual(self.ebic_det._opc_client.controller_state, ephemeron.STATE_NAME_IDLE)

        # set the repetition of the region of acquisition to 500 x 480, force it non-squared
        self.ebic_det.resolution.value = (500, 480)

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

    def test_acquisition_stop(self):
        # first check if the scan controller is idle
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

    @skip
    def test_request_very_high_resolution(self):
        # this should fire a timeout exception
        # take the max res (4096, 3072) of the e-beam as resolution to scan
        pass

    def receive_ebic_data(self, df, d):
        self._data.put(d)
        wl = d.metadata[model.MD_WL_LIST]
        if d.shape[0] != 1:
            logging.error("Shape is %s", d.shape)
        if d.shape[1] != len(wl):
            logging.error("Shape is %s but wl has len %d", d.shape, len(wl))
        logging.debug("Received data of shape %s with mean %s, max %s",
                      d.shape, d.mean(), d.max())

if __name__ == "__main__":
    unittest.main()
