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
import logging
from odemis import model
import odemis
from odemis.acq import stream, acqmng
from odemis.util import testing
import os
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_LENS_CONFIG = CONFIG_PATH + "sim/secom-sim-lens-align.odm.yaml"

class TestOverlayStream(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SECOM_LENS_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

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
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    # @unittest.skip("skip")
    def test_overlay_stream(self):
        # Create the stream
        ovrl = stream.OverlayStream("test overlay", self.ccd, self.ebeam, self.sed)

        ovrl.dwellTime.value = 0.3
        ovrl.repetition.value = (4, 4)

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

#     @unittest.skip("skip")
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
        fs1.excitation.value = sorted(fs1.excitation.choices)[0]
        fs1.emission.value = sorted(fs1.emission.choices)[-1]
        fs2 = stream.FluoStream("test blue", self.ccd, self.ccd.data,
                                self.light, self.light_filter)
        fs2.excitation.value = sorted(fs2.excitation.choices)[-1]
        fs2.emission.value = sorted(fs2.emission.choices)[-1]
        self.ccd.exposureTime.value = 0.1 # s

        ovrl = stream.OverlayStream("overlay", self.ccd, self.ebeam, self.sed)
        ovrl.dwellTime.value = 0.3
        ovrl.repetition.value = (4, 4)

        streams = [sems, fs1, fs2, ovrl]
        est_time = acqmng.estimateTime(streams)

        sum_est_time = sum(s.estimateAcquisitionTime() for s in streams)
        self.assertGreaterEqual(est_time, sum_est_time)

        # prepare callbacks
        self.start = None
        self.end = None
        self.updates = 0
        self.done = 0

        # Run acquisition
        start = time.time()
        f = acqmng.acquire(streams)
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, e = f.result()
        dur = time.time() - start
        self.assertGreater(dur, est_time / 2) # Estimated time shouldn't be too small

        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e) # Check there was no exception
        self.assertEqual(len(data), len(streams) - 1)

        # No overlay correction metadata anywhere (it has all been merged)
        for d in data:
            for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
                self.assertNotIn(k, d.metadata)

        # thumb = acqmng.computeThumbnail(st, f)
        # self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1) # at least one update at end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(not f.cancelled())

        # make sure the callback had time to be called
        time.sleep(0.1)
        self.assertEqual(self.done, 1)

    def on_done(self, future):
        logging.debug("Acquisition done received")
        self.done += 1

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

if __name__ == "__main__":
    unittest.main()
