#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 24 March 2018

@author: Anders Muskens
Testing class for drivers.symphotime .

Copyright © 2018 Ander Muskens, Delmic

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
from odemis.driver import symphotime
import os
import pickle
import threading
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

'''
To run these tests, you must run the Symphotime simulator.

The simulator can be found at https://github.com/PicoQuant/SymPhoTime_Remote_Interface
The PQ_ServerSimulator application is a Win32 or x64 based exe that simulates a Symphotime server
By default, it listens on 127.0.0.1:6000 for a connection. Just launch the EXE with default settings
in order to run these tests. Typically the default measurement time is 5 s, and the tests expect this.

The time.sleep functions are used to give the server time to respond to requests. Otherwise, the assertions
will fail if they are executed immediately after commands.
'''

CONFIG_SCANNER = {
    "name": "Test scanner",
    "role": "scanner",
}

CONFIG_LIVE = {
    "name": "Live Detector",
    "role": "tc-detector-live",
}

# Config to use with the symphotime simulator
CONFIG_SYMPHOTIME_SIM = {
    "name": "Test Symphotime Controller",
    "role": "detector",
    "children": {
        "scanner": CONFIG_SCANNER,
        "detector-live": CONFIG_LIVE
        },
    "host": "localhost",
    }

MD = {
    model.MD_DESCRIPTION: "Measurement description",
    model.MD_PIXEL_SIZE: (5e-6, 5e-6),
    model.MD_DWELL_TIME: 5e-6
    }


class TestStatic(unittest.TestCase):

    def test_exception_pickling(self):
        """
        Check the exception can be pickled and unpickled (for Pyro4)
        """
        ex = symphotime.SPTError(2)
        p = pickle.dumps(ex)
        ep = pickle.loads(p)
        self.assertIsInstance(ep, symphotime.SPTError)
        self.assertEqual(str(ex), str(ep))


class TestSymphotime(unittest.TestCase):
    @classmethod
    def setUpClass(self):
        # note: The symphotime simulator must be running before we start the tests. 
        self.controller = symphotime.Controller(**CONFIG_SYMPHOTIME_SIM)
        self.controller.updateMetadata(MD)
        for c in self.controller.children.value:
            if c.role == CONFIG_SCANNER["role"]:
                self.scanner = c
            elif c.role == CONFIG_LIVE["role"]:
                self.det_live = c
            else:
                self.fail("Unexpected role %s" % (c.role,))

    @classmethod
    def tearDownClass(self):
        self.controller.terminate()

    def test_acquisition(self):
        # Test the starting and stopping of acquisition. 
        done = threading.Event()

        # ~ 4s of acquisition (theoretical)
        self.scanner.resolution.value = (64, 64)
        self.scanner.dwellTime.value = 1e-3
        self.scanner.bidirectional.value = False

        for filename in ["file1.ptu", "big$file.ptu"]:
            for directory in ["MyMeasurement", "TestGroup"]:
                done.clear()
                self.scanner.filename.value = filename
                self.scanner.directory.value = directory

                # This function will execute once the measurement completes.
                def callback(flow, array):
                    self.assertEqual(array, [[0]])
                    self.assertEqual(self.scanner.filename.value, filename)
                    self.assertEqual(self.scanner.directory.value, directory)
                    self.assertEqual(array.metadata[model.MD_DESCRIPTION], MD[model.MD_DESCRIPTION])
                    self.assertEqual(array.metadata[model.MD_PIXEL_SIZE], MD[model.MD_PIXEL_SIZE])
                    self.assertEqual(self.scanner.dwellTime.value, array.metadata[model.MD_DWELL_TIME])
                    # unsub
                    self.controller.data.unsubscribe(callback)
                    self.assertFalse(self.controller.isMeasuring())
                    done.set()

                # Start measuring
                self.controller.data.subscribe(callback)

                # Should start measuring now
                self.assertTrue(self.controller.isMeasuring())

                # Wait until the measurement completes
                done.wait(30)
                self.assertTrue(done.is_set(), "Timeout")
                self.assertFalse(self.controller.isMeasuring())

    def test_bad_filename(self):
        # Test if the filename will get an extension added.
        filename = "filename"
        self.scanner.filename.value = filename
        self.assertEqual(self.scanner.filename.value, filename + '.ptu')

        # Test that a filename that is to long is rejected.
        with self.assertRaises(ValueError):
            self.scanner.filename.value = 'a' * 256

        # Test if the PTU will be appended
        self.scanner.filename.value = "filename.ome.tiff"
        self.assertEqual(self.scanner.filename.value, "filename.ome.tiff.ptu")

    def test_stopping(self):
        # Start measuring, then immediately stop. Be sure that the measurement takes at least 5 seconds.
        # Try this several times

        # be sure we are in a non-measuring state
        self.controller.StopMeasurement()
        time.sleep(0.5)
        self.assertFalse(self.controller.isMeasuring())

        for i in range(1, 4):
            logging.debug("Start/Stop Trial %d", i)

            # This function will execute once the measurement completes.
            def callback(flow, array):
                self.fail("This is not supposed to run, since measurement is supposed to stop. ")

            # Start measuring, wait, then stop
            self.controller.data.subscribe(callback)
            time.sleep(0.5)
            self.assertTrue(self.controller.isMeasuring())
            self.controller.data.unsubscribe(callback)
            # Measurement should be stopped
            time.sleep(1.0)
            self.assertFalse(self.controller.isMeasuring())

    def test_multi_subscribers(self):
        # Test that the class functions properly when multiple subscribers are active.
        done1 = threading.Event()
        done2 = threading.Event()

        self.scanner.filename.value = "file.ptu"
        self.scanner.directory.value = "group"

        # be sure we are in a non-measuring state
        self.controller.StopMeasurement()
        time.sleep(0.5)
        self.assertFalse(self.controller.isMeasuring())

        # This function will execute once the measurement completes.
        def callback1(flow, array):
            self.assertEqual(array, [[0]])
            self.controller.data.unsubscribe(callback1)
            done1.set()

        def callback2(flow, array):
            self.assertEqual(array, [[0]])
            self.controller.data.unsubscribe(callback2)
            done2.set()

        # Start measuring
        self.controller.data.subscribe(callback1)
        time.sleep(0.1)
        self.controller.data.subscribe(callback2)

        # Should start measuring now
        self.assertTrue(self.controller.isMeasuring())

        # Wait until the measurement completes
        done1.wait(5)
        done2.wait(5)
        time.sleep(0.5)
        self.assertFalse(self.controller.isMeasuring())

    def test_multi_subscribers_live(self):
        # Test that the class functions properly when multiple subscribers are active.
        done1 = threading.Event()
        done2 = threading.Event()
        
        self.scanner.filename.value = "file.ptu"
        self.scanner.directory.value = "group"

        # be sure we are in a non-measuring state
        self.controller.StopMeasurement()
        time.sleep(0.5)
        self.assertFalse(self.controller.isMeasuring())

        # This function will execute once the measurement completes.
        def callback1(flow, array):
            self.det_live.data.unsubscribe(callback1)
            done1.set()

        def callback2(flow, array):
            self.det_live.data.unsubscribe(callback2)
            done2.set()

        # Start measuring
        self.det_live.data.subscribe(callback1)
        time.sleep(0.1)
        self.det_live.data.subscribe(callback2)

        # Should start measuring now
        self.assertTrue(self.controller.isMeasuring())

        # Wait until the measurement completes
        done1.wait(5)
        done2.wait(5)
        time.sleep(0.5)
        self.assertFalse(self.controller.isMeasuring())

    def test_live(self):
        # Test the live stream of the apd
        # Check that some count rates do get sent into the array of count_rates
        self.scanner.filename.value = "test"
        self.scanner.directory.value = "test"

        logging.debug("Test the Live detector")
        self.assertFalse(self.controller.isMeasuring())

        count_rates = []

        # Every time a new count rate is received, add it to the list.
        def live_callback(df, array):
            count_rates.append(array[0][0])
            logging.debug("Received new count rate %s, md=%s", array[0][0], array.metadata)

        # Start measuring
        self.det_live.data.subscribe(live_callback)
        # Should start measuring now
        self.assertTrue(self.controller.isMeasuring())

        # Wait until the measurement completes
        time.sleep(5.0)
        self.det_live.data.unsubscribe(live_callback)
        time.sleep(0.5)
        self.assertFalse(self.controller.isMeasuring())

        # Check that we did get some new count rates
        self.assertTrue(len(count_rates) > 0)

    def test_live_start(self):
        # Test function if the live stream is started while another measuremnet occurs.
        done = threading.Event()

        done.clear()
        self.scanner.filename.value = "test.ptu"
        self.scanner.directory.value = "test"

        # be sure we are in a non-measuring state
        self.controller.StopMeasurement()
        time.sleep(0.5)
        self.assertFalse(self.controller.isMeasuring())
        self._latest_live = None

        # Every time a new count rate is received, add it to the list.
        def live_callback(flow, da):
            self._latest_live = da
            logging.debug("Received new count rate %s", da[0][0])

        def measurement_callback(flow, da):
            self.controller.data.unsubscribe(measurement_callback)
            done.set()

        # Start measuring with the live first, then start a real acq
        logging.debug("Start live, then start regular")
        self.det_live.data.subscribe(live_callback)
        self.assertTrue(self.controller.isMeasuring())
        time.sleep(0.5)
        with self.assertRaises(RuntimeError):
            self.controller.data.subscribe(measurement_callback)

        self.assertTrue(self.controller.isMeasuring())

        # Wait a bit, then stop.
        time.sleep(5.0)
        self.det_live.data.unsubscribe(live_callback)
        self.assertEqual(self._latest_live.shape, (1, 1))
        md = self._latest_live.metadata
        # TODO: dwell time should be the period of the count rate update
        self.assertGreaterEqual(md[model.MD_DWELL_TIME],
                                self.scanner.dwellTime.value)
        self.assertEqual(md[model.MD_DET_TYPE], model.MD_DT_NORMAL)
        assert time.time() - 10 <= md[model.MD_ACQ_DATE] <= time.time()

        time.sleep(1.0)
        self.assertFalse(self.controller.isMeasuring())

        # Now try to start measuring the live after real measurement is running
        logging.debug("Start regular, then live.")
        self.controller.data.subscribe(measurement_callback)
        time.sleep(0.5)
        self.assertTrue(self.controller.isMeasuring())
        time.sleep(0.5)
        with self.assertRaises(RuntimeError):
            self.det_live.data.subscribe(live_callback)

        self.controller.data.unsubscribe(measurement_callback)

class TestOptionalDataRecord(unittest.TestCase):
    # Test OptionalDataRecord to_bytes function (to make sure it's compatible with python2 and python3)
    def test_to_bytes(self):
        dr = symphotime.OptionalDataRecord("Test", symphotime.PQ_OPT_DATATYPE_FLOAT, 0.1)
        self.assertIsInstance(dr.to_bytes(), bytes)

if __name__ == "__main__":
    unittest.main()
