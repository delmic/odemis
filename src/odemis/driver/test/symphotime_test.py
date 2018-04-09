#!/usr/bin/env python
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

from __future__ import division

import logging
from odemis import model
from odemis.driver import symphotime
import threading
import unittest
import os
import time

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

'''
To run these tests, you must run the Symphotime simulator.

The simulator can be found at https://github.com/PicoQuant/SymPhoTime_Remote_Interface
The PQ_ServerSimulator application is a Win32 or x64 based exe that simulates a Symphotime server
By default, it listens on 127.0.0.1:6000 for a connection. Just launch the EXE with default settings
in order to run these tests.
'''

CONFIG_SCANNER = {
    "name": "Test scanner",
    "role": "scanner",
}

# Config to use with the symphotime simulator
CONFIG_SYMPHOTIME_SIM = {
    "name": "Test Symphotime Controller",
    "role": "detector",
    "children": { "scanner": CONFIG_SCANNER },
    "host": "localhost",
    }

MD = {
    model.MD_DESCRIPTION: "Measurement description",
    model.MD_PIXEL_SIZE: (5e-6, 5e-6),
    model.MD_DWELL_TIME: 5e-6
    }

class TestSymphotime(unittest.TestCase):
    @classmethod
    def setUpClass(self):
        # note: The symphotime simulator must be running before we start the tests. 
        self.controller = symphotime.Controller(**CONFIG_SYMPHOTIME_SIM)
        self.controller.updateMetadata(MD)

    @classmethod
    def tearDownClass(self):
        self.controller.terminate()
    
    def test_acquisition(self):
        # Test the starting and stopping of acquisition. 
        done = threading.Event()
        
        for filename in ["file1.ptu", "big$file.ptu"]:
            for directory in ["MyMeasurement", "TestGroup"]:
                done.clear()
                self.controller.scanner.filename.value = filename
                self.controller.scanner.directory.value = directory

                # This function will execute once the measurement completes.
                def callback(flow, array):
                    self.assertEqual(array, [[0]])
                    self.assertEqual(self.controller.scanner.filename.value, filename)
                    self.assertEqual(self.controller.scanner.directory.value, directory)
                    self.assertEqual(array.metadata[model.MD_DESCRIPTION], MD[model.MD_DESCRIPTION])
                    self.assertEqual(array.metadata[model.MD_PIXEL_SIZE], MD[model.MD_PIXEL_SIZE])
                    self.assertEqual(self.controller.scanner.dwellTime.value, array.metadata[model.MD_DWELL_TIME])
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
                self.assertFalse(self.controller.isMeasuring())

    def test_bad_filename(self):
        # Test if the filename will get an extension added.
        filename = "filename"
        self.controller.scanner.filename.value = filename
        self.assertEqual(self.controller.scanner.filename.value, filename + '.ptu')

        # Test that a filename that is to long is rejected.
        with self.assertRaises(ValueError):
            self.controller.scanner.filename.value = 'a' * 256

        # Test if the PTU will be appended
        self.controller.scanner.filename.value = "filename.ome.tiff"
        self.assertEqual(self.controller.scanner.filename.value, "filename.ome.tiff.ptu")

    def test_stopping(self):
        # Start measuring, then immediately stop. Be sure that the measurement takes at least 5 seconds.
        # Try this several times

        for i in range(1, 6):
            logging.debug("Start/Stop Trial %d", i)

            # This function will execute once the measurement completes.
            def callback(flow, array):
                self.fail("This is not supposed to run, since measurement is supposed to stop. ")

            # Start measuring, wait, then stop
            self.controller.data.subscribe(callback)
            self.assertTrue(self.controller.isMeasuring())
            self.controller.data.unsubscribe(callback)
            # Measurement should be stopped
            time.sleep(2.0)
            self.assertFalse(self.controller.isMeasuring())


if __name__ == "__main__":
    unittest.main()
