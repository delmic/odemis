#-*- coding: utf-8 -*-
"""
@author: Éric Piel

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
"""

# Test module for model.Stream classes

import logging
import numpy
from odemis import model
from odemis.gui.model import stream
from odemis.util import driver
import os
import subprocess
import time
import unittest
from unittest.case import skip


logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
_frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(__file__) + "/../../../../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sparc-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "secom-sim.odm.yaml"

class FakeEBeam(model.Emitter):
    """
    Imitates an e-beam, sufficiently for the Streams 
    """
    def __init__(self, name):
        model.Emitter.__init__(self, name, "fakeebeam", parent=None)
        self._shape = (2048, 2048)
        self.resolution = model.ResolutionVA((256, 256), [(1, 1), self._shape])
        self.pixelSize = model.VigilantAttribute((1e-9, 1e-9), unit="m", readonly=True)
        self.magnification = model.FloatVA(1000.)

class StreamTestCase(unittest.TestCase):
    def assertTupleAlmostEqual(self, first, second, places=None, msg=None, delta=None):
        """
        check two tuples are almost equal (value by value)
        """
        for f, s in zip(first, second):
            self.assertAlmostEqual(f, s, places=places, msg=msg, delta=delta)


    def test_roi_rep_pxs_links(self):
        """
        Test the connections between .roi, .pixelSize and .repetition of a 
        SpectrumStream.
        """
        ebeam = FakeEBeam("ebeam")
        ss = stream.SpectrumStream("test spec", None, None, ebeam)

        # if roi is UNDEFINED, everything is left unchanged
        ss.roi.value = stream.UNDEFINED_ROI
        ss.pixelSize.value = 1e-8
        self.assertEqual(ss.pixelSize.value, 1e-8)
        ss.repetition.value = (100, 100)
        self.assertEqual(ss.repetition.value, (100, 100))
        self.assertEqual(ss.roi.value, stream.UNDEFINED_ROI)

        # for any value set in ROI, the new ROI value respects:
        # ROI = pixelSize * repetition / phy_size
        # ROI < (0,0,1,1)
        rois = [(0, 0, 1, 1), (0.1, 0.1, 0.8, 0.8), (0.00001, 0.1, 1, 0.2)]
        epxs = ebeam.pixelSize.value
        eshape = ebeam.shape
        phy_size = [epxs[0] * eshape[0], epxs[1] * eshape[1]] # max physical ROI
        for roi in rois:
            ss.roi.value = roi
            new_roi = ss.roi.value
            rep = ss.repetition.value
            pxs = ss.pixelSize.value 
            exp_roi_size = [rep[0] * pxs / phy_size[0],
                            rep[1] * pxs / phy_size[1]]
            roi_size = [new_roi[2] - new_roi[0], new_roi[3] - new_roi[1]]
            self.assertTupleAlmostEqual(roi_size, exp_roi_size,
                             msg="with roi = %s => %s" % (roi, new_roi))
            self.assertTrue(new_roi[0] >= 0 and new_roi[1] >= 0 and
                            new_roi[2] <= 1 and new_roi[3] <= 1,
                            "with roi = %s => %s" % (roi, new_roi))

        ss.pixelSize.value = ss.pixelSize.range[0] # needed to get the finest grain
        ss.roi.value = (0.3, 0.65, 0.5, 0.9)
        # changing one repetition dimension is always respected.
        rep = list(ss.repetition.value)
        rep[0] //= 2
        ss.repetition.value = rep
        self.assertEqual(ss.repetition.value[0], rep[0])
        rep = list(ss.repetition.value)
        rep[1] //= 2
        ss.repetition.value = rep
        self.assertEqual(ss.repetition.value[1], rep[1])

        # Changing 2 repetition dimensions at once respects at least one
        rep = [rep[0] * 2, int(round(rep[1] * 1.4))]
        ss.repetition.value = rep
        new_rep = list(ss.repetition.value)
        self.assertTrue(rep[0] == new_rep[0] or rep[1] == new_rep[1])

        # 1x1 repetition leads to a square ROI
        ss.repetition.value = (1, 1)
        new_roi = ss.roi.value
        roi_size = [new_roi[2] - new_roi[0], new_roi[3] - new_roi[1]]
        self.assertAlmostEqual(roi_size[0], roi_size[1])

        ss.pixelSize.value = ss.pixelSize.range[0]
        ss.roi.value = (0, 0, 1, 1)
        # Changing pixel size to the minimum leads to the smallest pixel size
        ss.pixelSize.value = ss.pixelSize.range[0]
        self.assertAlmostEqual(ss.pixelSize.value, max(ebeam.pixelSize.value))
        self.assertEqual(tuple(ss.repetition.value), ebeam.shape)


        # TODO: changing pixel size to a huge number leads to a 1x1 repetition

        # When changing both repetition dims, they are both respected
        ss.pixelSize.value = ss.pixelSize.range[0]
        ss.roi.value = (0.3, 0.65, 0.5, 0.6)
        ss.repetition.value = (3, 5)
        new_rep = (5, 6)
        ss.repetition.value = new_rep
        self.assertAlmostEqual(new_rep, ss.repetition.value)

        # Changing the SEM magnification updates the pixel size (iff the
        # magnification cannot be automatically linked to the actual SEM
        # magnification).
        old_rep = ss.repetition.value
        old_roi = ss.roi.value
        old_pxs = ss.pixelSize.value
        old_mag = ebeam.magnification.value
        ebeam.magnification.value = old_mag * 2
        new_pxs = ss.pixelSize.value
        new_mag = ebeam.magnification.value
        mag_ratio = new_mag / old_mag
        pxs_ratio = new_pxs / old_pxs
        self.assertAlmostEqual(mag_ratio, 1 / pxs_ratio)
        self.assertEqual(old_rep, ss.repetition.value)
        self.assertEqual(old_roi, ss.roi.value)

#@skip("test")
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
            self.skipTest("Running backend found")

#    @skip("simple")
    def test_progressive_future(self):
        """
        Test .acquire interface (should return a progressive future with updates)
        """
        self.image = None
        self.done = False
        self.updates = 0

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", sems, ars)

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4) # hopefully always supported

        # Long acquisition
        self.ccd.exposureTime.value = 0.2 # s
        ars.repetition.value = (2, 3)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data = f.result(timeout)
        self.assertEqual(len(data), num_ar + 1)
        self.assertEqual(data[0].shape, exp_shape)
        self.assertGreaterEqual(self.updates, 4) # at least a couple of updates
        self.assertEqual(self.left, 0)
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

        # short acquisition
        self.done = False
        self.updates = 0
        self.ccd.exposureTime.value = 0.02 # s
        ars.repetition.value = (5, 4)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data = f.result(timeout)
        self.assertEqual(len(data), num_ar + 1)
        self.assertEqual(data[0].shape, exp_shape)
        self.assertGreaterEqual(self.updates, 5) # at least a few updates
        self.assertEqual(self.left, 0)
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

#    @skip("simple")
    def test_sync_future_cancel(self):
        self.image = None

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", sems, ars)

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4) # hopefully always supported

        # Long acquisition
        self.updates = 0
        self.ccd.exposureTime.value = 0.2 # s
        ars.repetition.value = (2, 3)

        # Start acquisition
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(self.ccd.exposureTime.value) # wait a bit
        f.cancel()

        self.assertGreaterEqual(self.updates, 1) # at least at the end
        self.assertEqual(self.left, 0)
        self.assertTrue(f.cancelled())

        # short acquisition
        self.updates = 0
        self.ccd.exposureTime.value = 0.02 # s
        ars.repetition.value = (5, 4)

        # Start acquisition
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(self.ccd.exposureTime.value) # wait a bit
        f.cancel()

        self.assertGreaterEqual(self.updates, 1) # at least at the end
        self.assertEqual(self.left, 0)
        self.assertTrue(f.cancelled())

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

#    @skip("simple")
    def test_acq_ar(self):
        """
        Test short & long acquisition for AR
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", sems, ars)

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4) # hopefully always supported

        # Long acquisition (small rep to avoid being too long)
        # The acquisition method is different for time > 0.1 s, but we had bugs
        # with dwell time > 4s, so let's directly test both.
        self.ccd.exposureTime.value = 5 # s
        ars.repetition.value = (2, 3)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sems.raw) + len(ars.raw))
        self.assertEqual(len(sems.raw), 1)
        self.assertEqual(sems.raw[0].shape, exp_shape)
        self.assertEqual(len(ars.raw), num_ar)
        md = ars.raw[0].metadata
        self.assertIn(model.MD_POS, md)
        self.assertIn(model.MD_AR_POLE, md)

        # Short acquisition (< 0.1s)
        self.ccd.exposureTime.value = 0.03 # s
        ars.repetition.value = (30, 20)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sems.raw) + len(ars.raw))
        self.assertEqual(len(sems.raw), 1)
        self.assertEqual(sems.raw[0].shape, exp_shape)
        self.assertEqual(len(ars.raw), num_ar)
        md = ars.raw[0].metadata
        self.assertIn(model.MD_POS, md)
        self.assertIn(model.MD_AR_POLE, md)

#    @skip("simple")
    def test_acq_spec(self):
        """
        Test short & long acquisition for Spectrometer
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", sems, specs)

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        self.spec.exposureTime.value = 0.3 # s
        specs.repetition.value = (5, 6)
        exp_shape = specs.repetition.value[::-1]

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sems.raw) + len(specs.raw))
        self.assertEqual(len(sems.raw), 1)
        self.assertEqual(sems.raw[0].shape, exp_shape)
        self.assertEqual(len(specs.raw), 1)
        sshape = specs.raw[0].shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1) # should have at least 2 wavelengths
        sem_md = sems.raw[0].metadata
        spec_md = specs.raw[0].metadata
        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        self.assertAlmostEqual(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])

        # Short acquisition (< 0.1s)
        self.spec.exposureTime.value = 0.01 # s
        specs.repetition.value = (25, 60)
        exp_shape = specs.repetition.value[::-1]

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sems.raw) + len(specs.raw))
        self.assertEqual(len(sems.raw), 1)
        self.assertEqual(sems.raw[0].shape, exp_shape)
        self.assertEqual(len(specs.raw), 1)
        sshape = specs.raw[0].shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1) # should have at least 2 wavelengths
        sem_md = sems.raw[0].metadata
        spec_md = specs.raw[0].metadata
        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        self.assertAlmostEqual(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])



if __name__ == "__main__":
    unittest.main()
