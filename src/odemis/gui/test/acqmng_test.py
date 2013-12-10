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
from odemis import model
from odemis.gui.acqmng import startAcquisition, computeThumbnail
from odemis.util import driver
import os
import subprocess
import time
import unittest

import odemis.gui.model as guimodel
import odemis.gui.model.stream as stream


logging.getLogger().setLevel(logging.DEBUG)

path = os.path.dirname(os.path.realpath(__file__))
os.chdir(path)

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(__file__) + "/../../../../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sparc-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "secom-sim.odm.yaml"

class TestNoBackend(unittest.TestCase):
    # No backend, and only fake streams that don't generate anything

    # TODO
    pass

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
        # TODO: we actually don't need a GUI data model to set-up streams
        # => could be removed once acquisition is outside of .gui
        cls.main_model = guimodel.MainGUIData(cls.microscope)
        s1 = stream.FluoStream("fluo1",
                  cls.main_model.ccd, cls.main_model.ccd.data,
                  cls.main_model.light, cls.main_model.light_filter)
        s2 = stream.FluoStream("fluo2",
                  cls.main_model.ccd, cls.main_model.ccd.data,
                  cls.main_model.light, cls.main_model.light_filter)
        s3 = stream.BrightfieldStream("bf",
                  cls.main_model.ccd, cls.main_model.ccd.data,
                  cls.main_model.light)
        cls.streams = [s1, s2, s3]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._components._microscope = None # force reset of the microscope for next connection
        time.sleep(1) # time to stop

    def setUp(self):
        if self.backend_was_running:
            raise unittest.SkipTest("Running backend found")

    def test_simple(self):
        # create a simple streamTree
        st = stream.StreamTree(streams=[self.streams[0]])
        f = startAcquisition(st.getStreams())
        data = f.result()
        self.assertIsInstance(data[0], model.DataArray)

        thumb = computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        # let's do it a second time, "just for fun"
        f = startAcquisition(st.getStreams())
        data = f.result()
        self.assertIsInstance(data[0], model.DataArray)

        thumb = computeThumbnail(st, f)
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

        f = startAcquisition(st.getStreams())
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

        f = startAcquisition(st.getStreams())
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
        cls.main_model = guimodel.MainGUIData(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if not cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._components._microscope = None # force reset of the microscope for next connection
        time.sleep(1) # time to stop

    def setUp(self):
        if self.backend_was_running:
            raise unittest.SkipTest("Running backend found")

    def test_sync_sem_ccd(self):
        """
        try acquisition with fairly complex SEM/CCD stream
        """
        pass

if __name__ == "__main__":
    unittest.main()
