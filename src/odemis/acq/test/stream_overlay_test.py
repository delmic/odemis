# -*- coding: utf-8 -*-
"""
:created: 14 Mar 2014
:author: kimon
:copyright: © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License version 2 as published
    by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""
from __future__ import division

import logging
from odemis import model, acq
from odemis.acq import stream
from odemis.util import driver
import os
import subprocess
import time
import odemis
import unittest


logging.getLogger().setLevel(logging.DEBUG)

# ODEMISD_CMD = ["/usr/bin/python2", "-m", "odemis.odemisd.main"]
# -m doesn't work when run from PyDev... not entirely sure why
ODEMISD_CMD = ["/usr/bin/python2", os.path.dirname(odemis.__file__) + "/odemisd/main.py"]
ODEMISD_ARG = ["--log-level=2" , "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_LENS_CONFIG = CONFIG_PATH + "secom-sim-lens-align.odm.yaml" # 7x7

class TestOverlayStream(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_LENS_CONFIG]
        # FIXME: give an informative warning when the comedi module has not been loaded
        print os.environ
        ret = subprocess.call(cmd)

        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)

        time.sleep(1) # time to start

        # find components by their role
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

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

    # @unittest.skip("skip")
    def test_overlay_stream(self):
        # Create the stream
        ovrl = stream.OverlayStream("test overlay", self.ccd, self.ebeam, self.sed)

        ovrl.dwellTime.value = 0.3
        ovrl.repetition.value = (7, 7)

        f = ovrl.acquire()
        das = f.result()
        cor_md = das[0].metadata
        for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
            self.assertIn(k, cor_md)

        # Try to cancel
        f = ovrl.acquire()
        time.sleep(1)
        f.cancel()
        self.assertTrue(f.cancelled())

    def test_acq_fine_align(self):
        """
        try acquisition with SEM + Optical + overlay streams
        """
        # Create the streams
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # SEM settings are via the current hardware settings
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        fs1 = stream.FluoStream("test orange", self.ccd, self.ccd.data,
                                self.light, self.light_filter)
        fs1.excitation.value = fs1.excitation.range[0] + 5e-9
        fs1.emission.value = fs1.emission.range[0] + 5e-9
        fs2 = stream.FluoStream("test blue", self.ccd, self.ccd.data,
                                self.light, self.light_filter)
        fs2.excitation.value = fs2.excitation.range[1] - 5e-9
        fs2.emission.value = fs2.emission.range[1] - 5e-9
        self.ccd.exposureTime.value = 0.1 # s

        ovrl = stream.OverlayStream("overlay", self.ccd, self.ebeam, self.sed)
        ovrl.dwellTime.value = 0.3
        ovrl.repetition.value = (7, 7)

        streams = [sems, fs1, fs2, ovrl]
        est_time = acq.estimateTime(streams)

        sum_est_time = sum(s.estimateAcquisitionTime() for s in streams)
        self.assertGreaterEqual(est_time, sum_est_time)

        # prepare callbacks
        self.past = None
        self.left = None
        self.updates = 0
        self.done = 0

        # Run acquisition
        start = time.time()
        f = acq.acquire(streams)
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data = f.result()
        dur = time.time() - start
        self.assertGreater(dur, est_time / 2) # Estimated time shouldn't be too small

        self.assertIsInstance(data[0], model.DataArray)
        self.assertEqual(len(data), len(streams) - 1)

        # No overlay correction metadata anywhere (it has all been merged)
        for d in data:
            for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
                self.assertNotIn(k, d.metadata)

        # thumb = acq.computeThumbnail(st, f)
        # self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1) # at least one update at end
        self.assertEqual(self.left, 0)
        self.assertEqual(self.done, 1)
        self.assertTrue(not f.cancelled())

    def on_done(self, future):
        self.done += 1

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

if __name__ == "__main__":
    unittest.main()
