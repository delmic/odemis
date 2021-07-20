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
from __future__ import division

from concurrent.futures._base import CancelledError
import logging
import numpy
from odemis import model
import odemis
from odemis.acq import acqmng
from odemis.util import test
import os
import time
import unittest
from unittest.case import skip

from odemis.acq.leech import ProbeCurrentAcquirer
import odemis.acq.path as path
import odemis.acq.stream as stream
from odemis.acq.acqmng import SettingsObserver, acquireZStack
from odemis.driver.tmcm import TMCLController
from odemis.util.comp import generate_zlevels

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"

class Fake0DDetector(model.Detector):
    """
    Imitates a probe current detector, but you need to send the data yourself (using
    comp.data.notify(d)
    """
    def __init__(self, name):
        model.Detector.__init__(self, name, "fakedet", parent=None)
        self.data = Fake0DDataFlow()
        self._shape = (float("inf"),)

class Fake0DDataFlow(model.DataFlow):
    """
    Mock object just sufficient for the ProbeCurrentAcquirer
    """
    def get(self):
        da = model.DataArray([1e-12], {model.MD_ACQ_DATE: time.time()})
        return da

class TestNoBackend(unittest.TestCase):
    # No backend, and only fake streams that don't generate anything

    # TODO
    pass

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
        f = acqmng.acquire(st.getProjections())
        data, e = f.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)

        thumb = acqmng.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        # let's do it a second time, "just for fun"
        f = acqmng.acquire(st.getProjections())
        data, e = f.result()
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)

        thumb = acqmng.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

    def test_metadata(self):
        """
        Check if extra metadata are saved
        """
        settings_obs = SettingsObserver(model.getComponents())
        self.ccd.binning.value = (1, 1)  # make sure we don't save the right metadata by accident
        detvas = {'exposureTime', 'binning', 'gain'}
        s1 = stream.FluoStream("fluo2", self.ccd, self.ccd.data,
                               self.light, self.light_filter, detvas=detvas)
        s2 = stream.BrightfieldStream("bf", self.ccd, self.ccd.data, self.light, detvas=detvas)

        # Set different binning values for each stream
        s1.detBinning.value = (2, 2)
        s2.detBinning.value = (4, 4)
        st = stream.StreamTree(streams=[s1, s2])
        f = acqmng.acquire(st.getProjections(), settings_obs=settings_obs)
        data, e = f.result()
        for s in data:
            self.assertTrue(model.MD_EXTRA_SETTINGS in s.metadata, "Stream %s didn't save extra metadata." % s)
        self.assertEqual(data[0].metadata[model.MD_EXTRA_SETTINGS][self.ccd.name]['binning'], [(2, 2), 'px'])
        self.assertEqual(data[1].metadata[model.MD_EXTRA_SETTINGS][self.ccd.name]['binning'], [(4, 4), 'px'])

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

        f = acqmng.acquire(st.getProjections())
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

        f = acqmng.acquire(st.getProjections())
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

    def test_metadata(self):
        """
        Check if extra metadata are saved
        """
        settings_obs = SettingsObserver(model.getComponents())

        detvas = {"binning", "exposureTime"}
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam, detvas=detvas)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.roi.value = (0, 0, 1, 1)
        specs.repetition.value = (2, 3)
        specs.detBinning.value = (2, specs.detBinning.value[1])
        specs.detExposureTime.value = 0.1

        specs2 = stream.SpectrumSettingsStream("test spec2", self.spec, self.spec.data, self.ebeam, detvas=detvas)
        sps2 = stream.SEMSpectrumMDStream("test sem-spec2", [sems, specs2])

        specs2.roi.value = (0, 0, 1, 1)
        specs2.repetition.value = (2, 3)
        specs2.detBinning.value = (4, specs2.detBinning.value[1])
        specs2.detExposureTime.value = 0.05

        f = acqmng.acquire([sps, sps2], settings_obs)
        data = f.result()

        spec1_data = data[0][1]
        spec2_data = data[0][3]
        self.assertEqual(spec1_data.metadata[model.MD_EXTRA_SETTINGS][self.spec.name]['binning'],
                         [(2, specs.detBinning.value[1]), 'px'])
        self.assertEqual(spec2_data.metadata[model.MD_EXTRA_SETTINGS][self.spec.name]['binning'],
                         [(4, specs2.detBinning.value[1]), 'px'])
        self.assertEqual(spec1_data.metadata[model.MD_EXTRA_SETTINGS][self.spec.name]['exposureTime'],
                         [0.1, 's'])
        self.assertEqual(spec2_data.metadata[model.MD_EXTRA_SETTINGS][self.spec.name]['exposureTime'],
                         [0.05, 's'])

    def test_sync_sem_ccd(self):
        """
        try acquisition with fairly complex SEM/CCD stream
        """
        # Create the streams and streamTree
        semsur = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        semars = stream.SEMARMDStream("test SEM/AR", [sems, ars])
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

        est_time = acqmng.estimateTime(st.getProjections())

        # prepare callbacks
        self.start = None
        self.end = None
        self.updates = 0
        self.done = 0

        # Run acquisition
        start = time.time()
        f = acqmng.acquire(st.getProjections())
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, e = f.result()
        dur = time.time() - start
        self.assertGreaterEqual(dur, est_time / 2) # Estimated time shouldn't be too small
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)
        self.assertEqual(len(data), num_ar + 2)

        thumb = acqmng.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1) # at least one update at end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(not f.cancelled())

        time.sleep(0.1)
        self.assertEqual(self.done, 1)

    def test_sync_path_guess(self):
        """
        try synchronized acquisition using the Optical Path Manager
        """
        # Create the streams and streamTree
        opmngr = path.OpticalPathManager(self.microscope)
        semsur = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam, opm=opmngr)
        semars = stream.SEMARMDStream("test SEM/AR", [sems, ars])
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam, opm=opmngr)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])
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

        est_time = acqmng.estimateTime(st.getProjections())

        # prepare callbacks
        self.start = None
        self.end = None
        self.updates = 0
        self.done = 0

        # Run acquisition
        start = time.time()
        f = acqmng.acquire(st.getProjections())
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, e = f.result()
        dur = time.time() - start
        self.assertGreaterEqual(dur, est_time / 2) # Estimated time shouldn't be too small
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)
        self.assertEqual(len(data), num_ar + 4)

        thumb = acqmng.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1) # at least one update at end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(not f.cancelled())

        # assert optical path configuration
        exp_pos = path.SPARC_MODES["spectral"][1]
        self.assertEqual(self.lenswitch.position.value, exp_pos["lens-switch"])
        self.assertEqual(self.spec_det_sel.position.value, exp_pos["spec-det-selector"])
        self.assertEqual(self.ar_spec_sel.position.value, exp_pos["ar-spec-selector"])

        time.sleep(0.1)
        self.assertEqual(self.done, 1)

    def test_leech(self):
        """
        try acquisition with leech
        """
        # Create the streams and streamTree
        semsur = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        semars = stream.SEMARMDStream("test SEM/AR", [sems, ars])
        st = stream.StreamTree(streams=[semsur, semars])

        pcd = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(pcd)
        sems.leeches.append(pca)
        semsur.leeches.append(pca)

        # SEM survey settings are via the current hardware settings
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        # SEM/AR settings are via the AR stream
        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        mx_brng = self.ccd.binning.range[1]
        binning = tuple(min(4, mx) for mx in mx_brng)  # try binning 4x4
        self.ccd.binning.value = binning
        self.ccd.exposureTime.value = 1  # s
        ars.repetition.value = (2, 3)
        num_ar = numpy.prod(ars.repetition.value)

        pca.period.value = 10  # Only at beginning and end

        est_time = acqmng.estimateTime(st.getProjections())

        # prepare callbacks
        self.start = None
        self.end = None
        self.updates = 0
        self.done = 0

        # Run acquisition
        start = time.time()
        f = acqmng.acquire(st.getProjections())
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, e = f.result()
        dur = time.time() - start
        self.assertGreaterEqual(dur, est_time / 2)  # Estimated time shouldn't be too small
        self.assertIsInstance(data[0], model.DataArray)
        self.assertIsNone(e)
        self.assertEqual(len(data), num_ar + 2)

        thumb = acqmng.computeThumbnail(st, f)
        self.assertIsInstance(thumb, model.DataArray)

        self.assertGreaterEqual(self.updates, 1)  # at least one update at end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(not f.cancelled())

        time.sleep(0.1)
        self.assertEqual(self.done, 1)

        for da in data:
            pcmd = da.metadata[model.MD_EBEAM_CURRENT_TIME]
            self.assertEqual(len(pcmd), 2)

    def on_done(self, future):
        logging.debug("On done called")
        self.done += 1

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

class CRYOSECOMTestCase(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            test.start_backend(ENZEL_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # create some streams connected to the backend
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.SEM_focuser = model.getComponent(role="ebeam-focus")
        cls.FM_focuser = model.getComponent(role="focus")

        cls.FM_focus_pos = 0.5e-6  # arbitrary current focus position
        cls.SEM_focus_pos = 0.1  # arbitrary current position

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        del cls.light
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

        self._nb_updates = 0
        self.streams = []

        self.FM_focuser.moveAbs({"z": self.FM_focus_pos}).result()
        self.SEM_focuser.moveAbs({"z": self.SEM_focus_pos}).result()

    @unittest.skip("Only zstack")
    def test_only_FM_streams_without_zstack(self):
        # create streams
        s1 = stream.FluoStream(
            "fluo1", self.ccd, self.ccd.data, self.light, self.light_filter
        )
        s1._focuser = self.FM_focuser
        s1.excitation.value = sorted(s1.excitation.choices)[0]
        s2 = stream.FluoStream(
            "fluo2", self.ccd, self.ccd.data, self.light, self.light_filter
        )
        s2._focuser = self.FM_focuser
        s2.excitation.value = sorted(s2.excitation.choices)[-1]
        self.streams = [s1, s2]

        # mock the zlevels dictionary
        zlevels = {}
        for s in self.streams:
            zlevels[s] = [s.focuser.position.value["z"]]

        # start the acquisition
        f = acqmng.acquireZStack(self.streams, zlevels)
        f.add_update_callback(self._on_progress_update)

        # get the data
        data, exp = f.result()
        self.assertIsNone(exp)

        for d in data:
            self.assertIsInstance(d, model.DataArray)
            # since no zstack, the center has 2 components
            self.assertEqual(len(d.metadata[model.MD_POS]), 2)
            # since no zstack, the pixel size has 2 components
            self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 2)

        # 2 streams, then 2 acquisitions
        self.assertEqual(len(data), 2)

        # 2 streams, 1 updates per stream, so 2 updates
        self.assertGreaterEqual(self._nb_updates, 2)

    def _on_progress_update(self, f, s, e):
        self._nb_updates += 1

    @unittest.skip("Only zstack")
    def test_only_SEM_streams_without_zstack(self):
        # create streams
        sems = stream.SEMStream("sem", self.sed, self.sed.data, self.ebeam)
        sems._focuser = self.SEM_focuser
        self.streams = [sems]

        # mock the zlevels dictionary
        zlevels = {}
        for s in self.streams:
            zlevels[s] = [s.focuser.position.value["z"]]

        # start the acquisition
        f = acqmng.acquireZStack(self.streams, zlevels)
        f.add_update_callback(self._on_progress_update)

        # get the data
        data, exp = f.result()
        self.assertIsNone(exp)

        for d in data:
            self.assertIsInstance(d, model.DataArray)
            # since no zstack, the center has 2 components
            self.assertEqual(len(d.metadata[model.MD_POS]), 2)
            # since no zstack, the pixel size has 2 components
            self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 2)

        # 1 streams, so 1 acquisitions
        self.assertEqual(len(data), 1)

        # 1 streams, 1 updates per stream, so 1 updates
        self.assertGreaterEqual(self._nb_updates, 1)

    @unittest.skip("Only zstack")
    def test_FM_and_SEM_without_zstack(self):
        # create streams
        s1 = stream.FluoStream(
            "fluo1", self.ccd, self.ccd.data, self.light, self.light_filter
        )
        s1._focuser = self.FM_focuser
        s1.excitation.value = sorted(s1.excitation.choices)[0]

        sems = stream.SEMStream("sem", self.sed, self.sed.data, self.ebeam)
        sems._focuser = self.SEM_focuser

        self.streams = [s1, sems]

        # mock the zlevels dictionary
        zlevels = {}
        for s in self.streams:
            zlevels[s] = [s.focuser.position.value["z"]]

        # start the acquisition
        f = acqmng.acquireZStack(self.streams, zlevels)
        f.add_update_callback(self._on_progress_update)

        # get the data
        data, exp = f.result()
        self.assertIsNone(exp)

        for d in data:
            self.assertIsInstance(d, model.DataArray)
            # since no zstack, the center has 2 components
            self.assertEqual(len(d.metadata[model.MD_POS]), 2)
            # since no zstack, the pixel size has 2 components
            self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 2)

        # 2 streams, so 2 acquisitions
        self.assertEqual(len(data), 2)

        # 2 streams, 2 updates per stream, so 2 updates
        self.assertGreaterEqual(self._nb_updates, 1)

    def test_only_FM_streams_with_zstack(self):
        # create streams
        s1 = stream.FluoStream(
            "fluo1", self.ccd, self.ccd.data, self.light, self.light_filter
        )
        s1._focuser = self.FM_focuser
        s1.excitation.value = sorted(s1.excitation.choices)[0]
        s2 = stream.FluoStream(
            "fluo2", self.ccd, self.ccd.data, self.light, self.light_filter
        )
        s2._focuser = self.FM_focuser
        s2.excitation.value = sorted(s2.excitation.choices)[-1]
        self.streams = [s1, s2]

        zlevels_list = generate_zlevels(self.FM_focuser, [-2e-6, 2e-6], 1e-6)
        zlevels = {}
        for s in self.streams:
            zlevels[s] = list(zlevels_list)

        # start the acquisition
        f = acqmng.acquireZStack(self.streams, zlevels)
        f.add_update_callback(self._on_progress_update)

        # get the data
        data, exp = f.result()
        self.assertIsNone(exp)

        for d in data:
            self.assertIsInstance(d, model.DataArray)
            # since zstack, the center has 3 components
            self.assertEqual(len(d.metadata[model.MD_POS]), 3)
            # since zstack, the pixel size has 3 components
            self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 3)

        # 2 streams, so 2 acquisitions
        self.assertEqual(len(data), 2)

        # 2 streams, 2 updates per stream, so 2 updates
        self.assertGreaterEqual(self._nb_updates, 2)

    def test_only_SEM_streams_with_zstack(self):
        sems = stream.SEMStream("sem", self.sed, self.sed.data, self.ebeam)
        sems._focuser = self.SEM_focuser
        self.streams = [sems]

        zlevels = {}
        for s in self.streams:
            zlevels[s] = [s.focuser.position.value["z"]]

        # start the acquisition
        f = acqmng.acquireZStack(self.streams, zlevels)
        f.add_update_callback(self._on_progress_update)

        data, exp = f.result()
        self.assertIsNone(exp)

        for d in data:
            self.assertIsInstance(d, model.DataArray)
            # even if zstack, it's only SEM, so the center has 2 components
            self.assertEqual(len(d.metadata[model.MD_POS]), 2)
            # even if zstack, it's SEM, so the pixel size has 2 components
            self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 2)

        # 1 streams, so 1 acquisitions
        self.assertEqual(len(data), 1)

        # 1 streams, 1 updates per stream, so 1 updates
        self.assertGreaterEqual(self._nb_updates, 1)

    def test_FM_and_SEM_with_zstack(self):
        s1 = stream.FluoStream(
            "fluo1", self.ccd, self.ccd.data, self.light, self.light_filter
        )
        s1._focuser = self.FM_focuser
        s1.excitation.value = sorted(s1.excitation.choices)[0]

        sems = stream.SEMStream("sem", self.sed, self.sed.data, self.ebeam)
        sems._focuser = self.SEM_focuser

        self.streams = [s1, sems]

        zlevels_list = generate_zlevels(self.FM_focuser, [-2e-6, 2e-6], 1e-6)
        zlevels = {}
        for s in self.streams:
            if isinstance(s, stream.FluoStream):
                zlevels[s] = list(zlevels_list)
            elif isinstance(s, stream.SEMStream):
                zlevels[s] = [s.focuser.position.value["z"]]

        # start the acquisition
        f = acqmng.acquireZStack(self.streams, zlevels)
        f.add_update_callback(self._on_progress_update)

        data, exp = f.result()
        self.assertIsNone(exp)

        for i, d in enumerate(data):
            self.assertIsInstance(d, model.DataArray)
            if d.ndim > 2 and d.shape[-3] > 1: # 3D data (FM)
                # if zstack, so the center has 3 components
                self.assertEqual(len(d.metadata[model.MD_POS]), 3)
                # if zstack, so the pixel size has 3 components
                self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 3)
            else:  # 2D data (SEM)
                # even if zstack, it's SEM, so the center has 2 components
                self.assertEqual(len(d.metadata[model.MD_POS]), 2)
                # even if zstack, it's SEM, so the pixel size has 2 components
                self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 2)

        # 2 streams, so 2 acquisitions
        self.assertEqual(len(data), 2)

        # 2 streams, 2 updates per stream, so >= 2 updates
        self.assertGreaterEqual(self._nb_updates, 2)

    def test_settings_observer_metadata_with_zstack(self):
        settings_observer = SettingsObserver(model.getComponents())
        vas = {"exposureTime"}
        s1 = stream.FluoStream(
            "FM", self.ccd, self.ccd.data, self.light, self.light_filter, detvas=vas)
        s1._focuser = self.FM_focuser
        s1.detExposureTime.value = 0.023  # 23 ms

        zlevels_list = generate_zlevels(self.FM_focuser, [-2e-6, 2e-6], 1e-6)
        zlevels = {s1: list(zlevels_list)}

        f = acquireZStack([s1], zlevels, settings_observer)
        # get the data
        data, exp = f.result()
        self.assertIsNone(exp)
        for d in data:
            self.assertTrue(model.MD_EXTRA_SETTINGS in d.metadata)
            # if zstack, so the center has 3 components
            self.assertEqual(len(d.metadata[model.MD_POS]), 3)
            # if zstack, so the pixel size has 3 components
            self.assertEqual(len(d.metadata[model.MD_PIXEL_SIZE]), 3)
        self.assertEqual(data[0].metadata[model.MD_EXTRA_SETTINGS]
                         ["Camera"]["exposureTime"], [0.023, "s"])

if __name__ == "__main__":
    unittest.main()
