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
import odemis
from odemis.util import test
import os
import time
import unittest
from unittest.case import skip

import odemis.acq.stream as stream
import odemis.acq.path as path


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"

class TestNoBackend(unittest.TestCase):
    # No backend, and only fake streams that don't generate anything

    # TODO
    pass

# @skip("simple")
class SECOMTestCase(unittest.TestCase):
    # We don't need the whole GUI, but still a working backend is nice

    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            test.start_backend(SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # create some streams connected to the backend
        cls.microscope = model.getMicroscope()
        cls.ccd = model.getComponent(role="ccd")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

        s1 = stream.FluoStream("fluo1", cls.ccd, cls.ccd.data,
                               cls.light, cls.light_filter)
        s1.excitation.value = sorted(s1.excitation.choices)[0]
        s2 = stream.FluoStream("fluo2", cls.ccd, cls.ccd.data,
                               cls.light, cls.light_filter)
        s2.excitation.value = sorted(s2.excitation.choices)[-1]
        s3 = stream.BrightfieldStream("bf", cls.ccd, cls.ccd.data, cls.light)
        cls.streams = [s1, s2, s3]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_simple(self):
        # create a simple streamTree
        st = stream.StreamTree(streams=[self.streams[0]])
        f = acq.acquire(st.getStreams())
        data, e = f.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)

        thumb = acq.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        # let's do it a second time, "just for fun"
        f = acq.acquire(st.getStreams())
        data, e = f.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)

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
        self.start = None
        self.end = None
        self.updates = 0

        f = acq.acquire(st.getStreams())
        f.add_update_callback(self.on_progress_update)

        data, e = f.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)
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
        self.start = None
        self.end = None
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
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(f.cancelled())

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

#@skip("simple")
class SPARCTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SPARC_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.microscope = model.getMicroscope()
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lenswitch = model.getComponent(role="lens-switch")
        cls.spec_det_sel = model.getComponent(role="spec-det-selector")
        cls.ar_spec_sel = model.getComponent(role="ar-spec-selector")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

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
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        semars = stream.SEMARMDStream("test SEM/AR", sems, ars)
        st = stream.StreamTree(streams=[semsur, semars])

        # SEM survey settings are via the current hardware settings
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        # SEM/AR settings are via the AR stream
        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        mx_brng = self.ccd.binning.range[1]
        binning = tuple(min(4, mx) for mx in mx_brng) # try binning 4x4
        self.ccd.binning.value = binning
        self.ccd.exposureTime.value = 1 # s
        ars.repetition.value = (2, 3)
        num_ar = numpy.prod(ars.repetition.value)

        est_time = acq.estimateTime(st.getStreams())

        # prepare callbacks
        self.start = None
        self.end = None
        self.updates = 0
        self.done = 0

        # Run acquisition
        start = time.time()
        f = acq.acquire(st.getStreams())
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, e = f.result()
        dur = time.time() - start
        self.assertGreaterEqual(dur, est_time / 2) # Estimated time shouldn't be too small
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)
        self.assertEqual(len(data), num_ar + 2)

        thumb = acq.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1) # at least one update at end
        self.assertLessEqual(self.end, time.time())
        self.assertEqual(self.done, 1)
        self.assertTrue(not f.cancelled())

    def test_sync_path_guess(self):
        """
        try synchronized acquisition using the Optical Path Manager
        """
        # Create the streams and streamTree
        semsur = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        semars = stream.SEMARMDStream("test SEM/AR", sems, ars)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", sems, specs)
        st = stream.StreamTree(streams=[semsur, semars, sps])

        # SEM survey settings are via the current hardware settings
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        # SEM/AR/SPEC settings are via the AR stream
        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        specs.roi.value = (0.2, 0.2, 0.7, 0.7)
        mx_brng = self.ccd.binning.range[1]
        binning = tuple(min(4, mx) for mx in mx_brng) # try binning 4x4
        self.ccd.binning.value = binning
        self.ccd.exposureTime.value = 1 # s
        ars.repetition.value = (2, 3)
        specs.repetition.value = (3, 2)
        num_ar = numpy.prod(ars.repetition.value)

        est_time = acq.estimateTime(st.getStreams())

        # prepare callbacks
        self.start = None
        self.end = None
        self.updates = 0
        self.done = 0

        # Run acquisition
        start = time.time()
        opmngr = path.OpticalPathManager(self.microscope)
        f = acq.acquire(st.getStreams(), opm=opmngr)
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, e = f.result()
        dur = time.time() - start
        self.assertGreaterEqual(dur, est_time / 2) # Estimated time shouldn't be too small
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)
        self.assertEqual(len(data), num_ar + 4)

        thumb = acq.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1) # at least one update at end
        self.assertLessEqual(self.end, time.time())
        self.assertEqual(self.done, 1)
        self.assertTrue(not f.cancelled())
        
        # assert optical path configuration
        self.assertEqual(self.lenswitch.position.value, path.MODES["spectral"][1]["lens-switch"])
        self.assertEqual(self.spec_det_sel.position.value, path.MODES["spectral"][1]["spec-det-selector"])
        self.assertEqual(self.ar_spec_sel.position.value, path.MODES["spectral"][1]["ar-spec-selector"])

    def on_done(self, future):
        self.done += 1

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

if __name__ == "__main__":
    unittest.main()
