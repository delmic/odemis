# -*- coding: utf-8 -*-
'''
Created on 6 Feb 2013

@author: piel

Copyright © 2013 Éric Piel, Delmic

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
from concurrent.futures._base import CancelledError
import logging
import numpy
from odemis import model, acq
from odemis.util import driver
import os
import subprocess
import time
import unittest
from unittest.case import skip

import odemis.acq.stream as stream


logging.getLogger().setLevel(logging.DEBUG)

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(__file__) + "/../../../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sparc-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "secom-sim.odm.yaml"

class TestNoBackend(unittest.TestCase):
    # No backend, and only fake streams that don't generate anything

    # TODO
    pass

#@skip("simple")
class SECOMTestCase(unittest.TestCase):
    # We don't need the whole GUI, but still a working backend is nice

    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_CONFIG]
        ret = subprocess.call(cmd)
        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)
        time.sleep(1) # time to start

        # create some streams connected to the backend
        cls.microscope = model.getMicroscope()
        for comp in model.getComponents():
            if comp.role == "ccd":
                cls.ccd = comp
            elif comp.role == "spectrometer":
                cls.spec = comp
            elif comp.role == "e-beam":
                cls.ebeam = comp
            elif comp.role == "se-detector":
                cls.sed = comp
            elif comp.role == "light":
                cls.light = comp
            elif comp.role == "filter":
                cls.light_filter = comp

        s1 = stream.FluoStream("fluo1", cls.ccd, cls.ccd.data,
                               cls.light, cls.light_filter)
        s2 = stream.FluoStream("fluo2", cls.ccd, cls.ccd.data,
                               cls.light, cls.light_filter)
        s2.excitation.value = s2.excitation.range[1]
        s3 = stream.BrightfieldStream("bf", cls.ccd, cls.ccd.data, cls.light)
        cls.streams = [s1, s2, s3]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._core._microscope = None # force reset of the microscope for next connection
        time.sleep(1) # time to stop

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_simple(self):
        # create a simple streamTree
        st = stream.StreamTree(streams=[self.streams[0]])
        f = acq.acquire(st.getStreams())
        data = f.result()
        self.assertIsInstance(data[0], model.DataArray)

        thumb = acq.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        # let's do it a second time, "just for fun"
        f = acq.acquire(st.getStreams())
        data = f.result()
        self.assertIsInstance(data[0], model.DataArray)

        thumb = acq.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

    def test_progress(self):
        """
        Check we get some progress updates
        """
        # create a little complex streamTree
        st = stream.StreamTree(streams=[
                self.streams[0],
                stream.StreamTree(streams=self.streams[1:3])
                ])
        self.past = None
        self.left = None
        self.updates = 0

        f = acq.acquire(st.getStreams())
        f.add_update_callback(self.on_progress_update)

        data = f.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertGreaterEqual(self.updates, 3) # at least one update per stream

    def test_cancel(self):
        """
        try a bit the cancelling possibility
        """
        # create a little complex streamTree
        st = stream.StreamTree(streams=[
                self.streams[2],
                stream.StreamTree(streams=self.streams[0:2])
                ])
        self.past = None
        self.left = None
        self.updates = 0
        self.done = False

        f = acq.acquire(st.getStreams())
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(0.5) # make sure it's started
        self.assertTrue(f.running())
        f.cancel()

        self.assertRaises(CancelledError, f.result, 1)
        self.assertGreaterEqual(self.updates, 1) # at least one update at cancellation
        self.assertEqual(self.left, 0)
        self.assertTrue(self.done)
        self.assertTrue(f.cancelled())


    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

#@skip("simple")
class SPARCTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SPARC_CONFIG]
        ret = subprocess.call(cmd)
        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)
        time.sleep(1) # time to start

        # Find CCD & SEM components
        cls.microscope = model.getMicroscope()
        for comp in model.getComponents():
            if comp.role == "ccd":
                cls.ccd = comp
            elif comp.role == "spectrometer":
                cls.spec = comp
            elif comp.role == "e-beam":
                cls.ebeam = comp
            elif comp.role == "se-detector":
                cls.sed = comp
            elif comp.role == "light":
                cls.light = comp
            elif comp.role == "filter":
                cls.light_filter = comp

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._core._microscope = None # force reset of the microscope for next connection
        time.sleep(1) # time to stop

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_sync_sem_ccd(self):
        """
        try acquisition with fairly complex SEM/CCD stream
        """
        # Create the streams and streamTree
        semsur = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        semars = stream.SEMARMDStream("test SEM/AR", sems, ars)
        st = stream.StreamTree(streams=[semsur, semars])

        # SEM survey settings are via the current hardware settings
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        # SEM/AR settings are via the AR stream
        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4) # hopefully always supported
        self.ccd.exposureTime.value = 1 # s
        ars.repetition.value = (2, 3)
        num_ar = numpy.prod(ars.repetition.value)

        est_time = acq.estimateTime(st.getStreams())

        # prepare callbacks
        self.past = None
        self.left = None
        self.updates = 0
        self.done = False

        # Run acquisition
        start = time.time()
        f = acq.acquire(st.getStreams())
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data = f.result()
        dur = time.time() - start
        self.assertGreaterEqual(dur, est_time / 2) # Estimated time shouldn't be too small
        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data), num_ar + 2)

        thumb = acq.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1) # at least one update at end
        self.assertEqual(self.left, 0)
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1
if __name__ == "__main__":
    unittest.main()
