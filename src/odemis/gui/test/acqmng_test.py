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
from odemis import model
from odemis.gui import instrmodel
from odemis.gui.acqmng import ProgressiveFuture, startAcquisition, \
    computeThumbnail
import logging
import odemis.gui.model.stream as stream
import subprocess
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

ODEMISD_CMD = "python2 -m odemis.odemisd.main"
SIM_CONFIG = "../../odemisd/test/optical-sim.odm.yaml"

class TestNoBackend(unittest.TestCase):
    # No backend, and only fake streams that don't generate anything


    def testProgressiveFuture(self):
        """
        Only tests a simple ProgressiveFuture
        """
        future = ProgressiveFuture()
        future.task_canceller = self.cancel_task
        self.cancelled = False
        self.past = None
        self.left = None

        now = time.time()
        # try to update progress
        future.set_end_time(now + 1)
        future.add_update_callback(self.on_progress_update)
        future.set_end_time(now + 2) # should say about 2 s left
        self.assertTrue(1.9 <= self.left and self.left < 2)
        self.assertLessEqual(self.past, 0)

        # "start" the task
        future.set_running_or_notify_cancel()
        self.assertTrue(0 <= self.past and self.past < 0.1)
        time.sleep(0.1)

        now = time.time()
        future.set_end_time(now + 1)
        self.assertTrue(0.9 <= self.left and self.left < 1)


        # try to cancel while running
        future.cancel()
        self.assertTrue(future.cancelled(), True)
        self.assertRaises(CancelledError, future.result, 1) # future.result(1) should fail
        self.assertEqual(self.left, 0)

    def cancel_task(self):
        self.cancelled = True

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left

class TestWithBackend(unittest.TestCase):
    # We don't need the whole GUI, but still a working backend is nice

    @classmethod
    def setUpClass(cls):
        # run the backend as a daemon
        # we cannot run it normally as the child would also think it's in a unittest
        cmdline = ODEMISD_CMD + " --log-level=2 --log-target=testdaemon.log --daemonize %s" % SIM_CONFIG
        ret = subprocess.call(cmdline.split())
        if ret != 0:
            logging.warning("Failed to start backend, will try anyway")
        time.sleep(1) # time to start

        # create some streams connected to the backend
        cls.microscope = model.getMicroscope()
        cls.imodel = instrmodel.MicroscopeGUIModel(cls.microscope)
        s1 = stream.FluoStream("fluo1",
                  cls.imodel.ccd, cls.imodel.ccd.data,
                  cls.imodel.light, cls.imodel.light_filter)
        s2 = stream.FluoStream("fluo2",
                  cls.imodel.ccd, cls.imodel.ccd.data,
                  cls.imodel.light, cls.imodel.light_filter)
        s3 = stream.BrightfieldStream("bf",
                  cls.imodel.ccd, cls.imodel.ccd.data,
                  cls.imodel.light)
        cls.streams = [s1, s2, s3]

    @classmethod
    def tearDownClass(cls):
#        cls.microscope.terminate()
        # end the backend
        cmdline = ODEMISD_CMD + " --kill"
        subprocess.call(cmdline.split())
        time.sleep(1) # time to stop

    def test_simple(self):
        # create a simple streamTree
        st = instrmodel.StreamTree(streams=[self.streams[0]])
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
        st = instrmodel.StreamTree(streams=[
                self.streams[0],
                instrmodel.StreamTree(streams=self.streams[1:3])
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
        st = instrmodel.StreamTree(streams=[
                self.streams[2],
                instrmodel.StreamTree(streams=self.streams[0:2])
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
