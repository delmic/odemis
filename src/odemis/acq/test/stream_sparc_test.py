#-*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2013-2025 Éric Piel, Delmic

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

# Test SPARC steams for settings and acquisition
# There are many test cases due to the many variations, in multiple dimensions.
# Scanner variation:
# * "Basic" e-beam scanner
# * "HwSync" e-beam scanner: each e-beam position sends (via a cable) a trigger to the camera
# * "Vector" e-beam scanner: the driver supports an arbitrary list of positions (which can be used for
# * Scanning stage: Beam doesn't move, sample does. (can be dedicated stage or sample stage)
#
# Detector type:
# * CL intensity: voltage read synchronously with the SE
# * EBIC: voltage read synchronously with the SE
# * Counting monochromator: ticks counted synchronously with the SE
# * Spectrum: CCD vertically binned acquiring one image per beam position
# * AR: full CCD with 2 angles dimensions, acquiring one image per beam position
# * Angular Spectrum: CCD with spectrum + angle dimension acquiring one image per beam position
# * Time correlator: 1 dimension for the time, acquiring one image per beam position
# * Temporal spectrum: CCD with spectrum + time dimension, acquiring one image per beam position
#
# Acquisition options:
# * Fuzzing (only for "acquiring one image per beam position" detectors)
# * Rotation
# * Drift correction (and other leeches)


import logging
import math
import os
import sys
import time
import unittest
from abc import ABC

import numpy

import odemis
from odemis import model
from odemis.acq import stream, path, leech
from odemis.acq.leech import ProbeCurrentAcquirer
from odemis.acq.stream import POL_POSITIONS
from odemis.util import testing, find_closest, comp
from odemis.util.testing import assert_pos_almost_equal

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"

SPARC_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"
SPARC2_CONFIG = CONFIG_PATH + "sim/sparc2-sim-scanner.odm.yaml"
SPARC2POL_CONFIG = CONFIG_PATH + "sim/sparc2-polarizer-sim.odm.yaml"
SPARC2STREAK_CONFIG = CONFIG_PATH + "sim/sparc2-streakcam-sim.odm.yaml"
SPARC2_4SPEC_CONFIG = CONFIG_PATH + "sim/sparc2-4spec-sim.odm.yaml"
SPARC2_INDE_EBIC_CONFIG = CONFIG_PATH + "sim/sparc2-independent-ebic-sim.odm.yaml"
SPARC2_FPLM_CONFIG = CONFIG_PATH + "sim/sparc2-fplm-sim.odm.yaml"
TIME_CORRELATOR_CONFIG = CONFIG_PATH + "sim/sparc2-time-correlator-sim.odm.yaml"
SPARC2_HWSYNC_CONFIG = CONFIG_PATH + "sim/sparc2-nidaq-sim.odm.yaml"
SPARC2_VECTOR_SSTAGE_CONFIG = CONFIG_PATH + "sim/sparc2-nidaq-scan-stage-sim.odm.yaml"


def roi_to_phys(repst):
    """
    Compute the (expected) physical position of a stream ROI
    repst (RepetitionStream): the repetition stream with ROI
    return:
        pos (tuple of 2 floats): physical position of the center
        pxs (tuple of 2 floats): pixel size in m
        res (tuple of ints): number of pixels
    """
    res = repst.repetition.value
    pxs = (repst.pixelSize.value,) * 2

    # To compute pos, we need to convert the ROI to physical coordinates
    roi = repst.roi.value
    roi_center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

    emt = repst.emitter
    try:
        sem_center = emt.getMetadata()[model.MD_POS]
    except KeyError:
        # no stage => pos is always 0,0
        sem_center = (0, 0)
    # TODO: pixelSize will be updated when the SEM magnification changes,
    # so we might want to recompute this ROA whenever pixelSize changes so
    # that it's always correct (but maybe not here in the view)
    sem_width = (emt.shape[0] * emt.pixelSize.value[0],
                 emt.shape[1] * emt.pixelSize.value[1])
    # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
    pos = (sem_center[0] + sem_width[0] * (roi_center[0] - 0.5),
           sem_center[1] - sem_width[1] * (roi_center[1] - 0.5))

    logging.debug("Expecting pos %s, pxs %s, res %s", pos, pxs, res)
    return pos, pxs, res

ROLE_TO_ATTR = {
    "e-beam": "ebeam",
    "se-detector": "sed",
    "bs-detector": "bsd",
    "ebic-detector": "ebic",
    "cl-detector": "cl",
    "ccd": "ccd",
    "ccd0": "ccd",  # Should never be at the same time as ccd
    "spectrometer": "spec",
    "spectrometer0": "spec",   # Should never be at the same time as spectrometer
    "spectrograph": "spgp",
    "monochromator": "mnchr",
    "stage": "stage",
    "scan-stage": "scan_stage",
    "cl-filter": "filter",
    "time-correlator": "time_correlator",
    "streak-ccd": "streak_ccd",
    "streak-unit": "streak_unit",
    "streak-delay": "streak_delay",
    "pol-analyzer": "analyzer",
    "light": "light",
}


class BaseSPARCTestCase(unittest.TestCase, ABC):
    simulator_config = None  # To be set by subclasses
    capabilities = set()     # Supported acquisition types

    @classmethod
    def setUpClass(cls):
        if cls.simulator_config is None:
            raise unittest.SkipTest("Abstract class test, not to be run")
        testing.start_backend(cls.simulator_config)

        # Find components
        cls.microscope = model.getMicroscope()
        components = model.getComponents()

        for c in components:
            if c.role is None:
                continue

            try:
                attrname = ROLE_TO_ATTR[c.role]
                setattr(cls, attrname, c)
            except KeyError:
                pass

    def skipIfNotSupported(self, *features):
        if not set(features) <= self.capabilities:
            self.skipTest(f"Simulator does not support \"{', '.join(features)}\" acquisition")

    def setUp(self):
        self.done = False
        self.updates = 0
        self.start = None
        self.end = None
        self._images = []

    # Called when the acquisition future is completed or updates the progress
    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

    # Called when a stream.image is changed
    def _on_image(self, im):
        self._images.append(im)

    def test_progressive_future(self):
        self.skipIfNotSupported("ar")
        self.image = None

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4)  # hopefully always supported

        # Long acquisition
        ars.detExposureTime.value = 0.2  # s
        ars.repetition.value = (2, 3)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 5 + 3 * sas.estimateAcquisitionTime()
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, exp = f.result(timeout)
        self.assertIsNone(exp)
        self.assertEqual(len(data), num_ar + 1)
        self.assertEqual(data[0].shape, exp_shape)
        self.assertGreaterEqual(self.updates, 4)  # at least a couple of updates
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

        # short acquisition
        self.done = False
        self.updates = 0
        ars.detExposureTime.value = 0.02  # s
        ars.repetition.value = (5, 4)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 5 + 3 * sas.estimateAcquisitionTime()
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data, exp = f.result(timeout)
        self.assertIsNone(exp)
        self.assertEqual(len(data), num_ar + 1)
        self.assertEqual(data[0].shape, exp_shape)
        self.assertGreaterEqual(self.updates, 5)  # at least a few updates
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

    def test_sync_future_cancel(self):
        self.skipIfNotSupported("ar")
        self.image = None

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4)  # hopefully always supported

        # Long acquisition
        self.updates = 0
        ars.detExposureTime.value = 0.2  # s
        ars.repetition.value = (2, 3)

        # Start acquisition
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(0.3)  # wait a bit
        f.cancel()

        self.assertGreaterEqual(self.updates, 1)  # at least at the end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(f.cancelled())

        # short acquisition
        self.updates = 0
        ars.detExposureTime.value = 0.02  # s
        ars.repetition.value = (5, 4)

        # Start acquisition
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(0.03)  # wait a bit
        f.cancel()

        self.assertGreaterEqual(self.updates, 1)  # at least at the end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(f.cancelled())

    def test_acq_ar(self):
        """
        Test short & long acquisition for AR
        """
        self.skipIfNotSupported("ar")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.roi.value = (0.2, 0.1, 0.6, 0.8)
        self.ccd.binning.value = (4, 4)  # hopefully always supported

        # Long acquisition (small rep to avoid being too long)
        # We had bugs with dwell time > 4s, so test something really long
        ars.detExposureTime.value = 5  # s
        ars.repetition.value = (2, 3)
        num_ar = numpy.prod(ars.repetition.value)
        exp_pos, exp_pxs, exp_res = roi_to_phys(ars)
        phys_roi = (exp_pos[0] - (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] - (exp_pxs[1] * exp_res[1] / 2),
                    exp_pos[0] + (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] + (exp_pxs[1] * exp_res[1] / 2),
                    )

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sas.raw))
        sem_da = sas.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        ar_das = sas.raw[1:]
        self.assertEqual(len(ar_das), num_ar)
        for d in ar_das:
            md = d.metadata
            self.assertIn(model.MD_POS, md)
            self.assertIn(model.MD_AR_POLE, md)
            pos = md[model.MD_POS]
            self.assertTrue(phys_roi[0] <= pos[0] <= phys_roi[2] and
                            phys_roi[1] <= pos[1] <= phys_roi[3],
                            "Position %s not in expected ROI %s" % (pos, phys_roi))

        # Short acquisition (< 0.1s)
        ars.detExposureTime.value = 0.03  # s
        ars.repetition.value = (30, 20)
        exp_pos, exp_pxs, exp_res = roi_to_phys(ars)
        phys_roi = (exp_pos[0] - (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] - (exp_pxs[1] * exp_res[1] / 2),
                    exp_pos[0] + (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] + (exp_pxs[1] * exp_res[1] / 2),
                    )

        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 2.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        self.assertEqual(len(data), len(sas.raw))
        sem_da = sas.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        ar_das = sas.raw[1:]
        self.assertEqual(len(ar_das), num_ar)
        for d in ar_das:
            md = d.metadata
            self.assertIn(model.MD_POS, md)
            self.assertIn(model.MD_AR_POLE, md)
            pos = md[model.MD_POS]
            self.assertTrue(phys_roi[0] <= pos[0] <= phys_roi[2] and
                            phys_roi[1] <= pos[1] <= phys_roi[3])

    def test_acq_ar_sub_px_drift(self):
        """
        Test AR acquisition with sub-pixel drift correction (ie, the drift estimation is measured
        multiple times for each e-beam pixel, and hence the AR data has to be acquired via the
        integrator)
        """
        self.skipIfNotSupported("ar")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        # Drift corrector with anchor region
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4)  # hopefully always supported

        # Long acquisition => Should have 5 drift measurements per pixel
        ars.detExposureTime.value = 5  # s
        ars.repetition.value = (4, 6)
        num_ar = numpy.prod(ars.repetition.value)
        exp_pos, exp_pxs, exp_res = roi_to_phys(ars)
        phys_roi = (exp_pos[0] - (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] - (exp_pxs[1] * exp_res[1] / 2),
                    exp_pos[0] + (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] + (exp_pxs[1] * exp_res[1] / 2),
                    )

        # Start acquisition
        timeout = 1 + 2.5 * sas.estimateAcquisitionTime()
        start = time.time()
        for l in sems.leeches:
            l.series_start()
        f = sas.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        for l in sems.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sas.raw))
        sem_da = sas.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        ar_das = sas.raw[1:-1]
        self.assertEqual(len(ar_das), num_ar)
        for d in ar_das:
            md = d.metadata
            self.assertIn(model.MD_POS, md)
            self.assertIn(model.MD_AR_POLE, md)
            pos = md[model.MD_POS]
            self.assertTrue(phys_roi[0] <= pos[0] <= phys_roi[2] and
                            phys_roi[1] <= pos[1] <= phys_roi[3],
                            "Position %s not in expected ROI %s" % (pos, phys_roi))

        anchor_da = sas.raw[-1]  # Normally the anchor data is the last data
        self.assertGreaterEqual(anchor_da.shape[-4], 1)

    def test_acq_spec(self):
        """
        Test short & long acquisition for Spectrometer
        """
        self.skipIfNotSupported("spec")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        im0 = specs.image.value
        start = time.time()
        f = sps.acquire()

        # Check if there is a live update in the setting stream.
        time.sleep(3)  # Wait long enough so that there is a new image
        im1 = specs.image.value
        self.assertIsInstance(im1, model.DataArray)
        testing.assert_array_not_equal(im0, im1)
        time.sleep(6)
        im2 = specs.image.value
        testing.assert_array_not_equal(im1, im2)

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.repetition.value = (25, 60)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        timeout = 1 + 2.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

    def test_acq_fuz(self):
        """
        Test short & long acquisition with fuzzing for Spectrometer
        """
        self.skipIfNotSupported("spec")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])
        specs.fuzzing.value = True

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        logging.debug("Will wait up to %g s", timeout)
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.repetition.value = (25, 60)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition (needs large timeout because currently the e-beam
        # scan tends to have a large overhead.
        timeout = 1 + 2.5 * sps.estimateAcquisitionTime()
        logging.debug("Will wait up to %g s", timeout)
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_mn(self):
        """
        Test short & long acquisition for SEM MD
        """
        self.skipIfNotSupported("monochromator")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.MonochromatorSettingsStream("test",
                                                 self.mnchr, self.mnchr.data, self.ebeam,
                                                 emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        mcs.roi.value = (0.2, 0.2, 0.5, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 5
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-3  # s

        mcs.repetition.value = (5, 7)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result()
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        # No SEM data with monochromator (currently), so only PMT + anchor
        self.assertEqual(len(data), 2)
        mcsd = None
        for d in data:
            if d.ndim >= 4:
                # anchor
                # TODO: check anchor
                self.assertGreaterEqual(d.shape[-4], 1)
            else:
                self.assertEqual(d.shape, exp_res[::-1])
                if model.MD_OUT_WL in d.metadata:  # monochromator data
                    mcsd = d
        # sms.raw should be the same as data
        self.assertEqual(len(data), len(sms.raw))
        md = mcsd.metadata
        numpy.testing.assert_allclose(md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(md[model.MD_PIXEL_SIZE], exp_pxs)

        # Now same thing but with more pixels
        mcs.roi.value = (0.1, 0.1, 0.8, 0.8)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06

        mcs.repetition.value = (30, 40)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result()
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        # No SEM data with monochromator (currently), so only PMT + anchor
        self.assertEqual(len(data), 2)
        mcsd = None
        for d in data:
            if d.ndim >= 4:
                # anchor
                # TODO: check anchor
                self.assertGreaterEqual(d.shape[-4], 2)
            else:
                self.assertEqual(d.shape, exp_res[::-1])
                if model.MD_OUT_WL in d.metadata:  # monochromator data
                    mcsd = d
        # sms.raw should be the same as data
        self.assertEqual(len(data), len(sms.raw))
        md = mcsd.metadata
        self.assertIn(model.MD_POS, md)
        numpy.testing.assert_allclose(md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_cl(self):
        """
        Test short & long acquisition for SEM MD CL intensity
        """
        self.skipIfNotSupported("cl")
        # create axes
        axes = {"filter": ("band", self.filter)}

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("test",
                                      self.cl, self.cl.data, self.ebeam,
                                      axis_map=axes,
                                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        # # Test acquisition with leech failure => it should just go on as if the
        # # leech had not been used.
        # mcs.roi.value = (0, 0.2, 0.3, 0.6)
        # dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        # dc.period.value = 5
        # dc.roi.value = stream.UNDEFINED_ROI
        # dc.dwellTime.value = 1e-06
        # sems.leeches.append(dc)
        #
        # # dwell time of sems shouldn't matter
        # mcs.emtDwellTime.value = 1e-6  # s
        #
        # # Start acquisition
        # timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        # start = time.time()
        # f = sms.acquire()
        #
        # # wait until it's over
        # data = f.result(timeout)
        # dur = time.time() - start
        # logging.debug("Acquisition took %g s", dur)
        # self.assertTrue(f.done())
        # self.assertEqual(len(data), len(sms.raw))

        sems.tint.value = (0, 255, 0)  # Green colour

        # Now, proper acquisition
        mcs.roi.value = (0, 0.2, 0.3, 0.6)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s

        mcs.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape
        self.assertEqual(len(sms.raw), 2)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])

        # Now same thing but with more pixels and drift correction
        mcs.roi.value = (0.3, 0.1, 1.0, 0.8)
        mcs.tint.value = (255, 0, 0)  # Red colour
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        mcs.repetition.value = (3000, 4000)
        b0, b1 = list(mcs.axisFilter.choices)[:2]
        mcs.axisFilter.value = b0
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        timeout = 1 + 2.5 * sms.estimateAcquisitionTime()
        start = time.time()
        dc.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dc.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))
        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)

        # Tint should be stored in USER_TINT metadata
        self.assertEqual(sem_md[model.MD_USER_TINT], (0, 255, 0))  # from .tint
        self.assertEqual(cl_md[model.MD_USER_TINT], (255, 0, 0))  # from .tint

    def test_acq_cl_rotated(self):
        """
        Test short & long acquisition for SEM MD CL intensity with rotation
        """
        self.skipIfNotSupported("cl")  # FIXME: only if vector scan supported?
        # create axes
        axes = {"filter": ("band", self.filter)}

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("test",
                                      self.cl, self.cl.data, self.ebeam,
                                      axis_map=axes,
                                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        # Now, proper acquisition
        mcs.roi.value = (0.25, 0.2, 0.55, 0.6)
        exp_rot = math.radians(10)
        mcs.rotation.value = exp_rot

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s

        mcs.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape
        self.assertEqual(len(sms.raw), 2)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], cl_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(cl_md[model.MD_ROTATION], exp_rot)
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])

        # Now same thing but with more pixels and drift correction
        mcs.roi.value = (0.3, 0.15, 0.9, 0.85)
        mcs.rotation.value = exp_rot
        mcs.tint.value = (255, 0, 0)  # Red colour
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        mcs.repetition.value = (3000, 3500)
        b0, b1 = list(mcs.axisFilter.choices)[:2]
        mcs.axisFilter.value = b0
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        timeout = 1 + 2.5 * sms.estimateAcquisitionTime()
        start = time.time()
        dc.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dc.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))
        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], cl_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(cl_md[model.MD_ROTATION], exp_rot)
        self.assertEqual(cl_md[model.MD_USER_TINT], (255, 0, 0))  # from .tint

    def test_acq_cl_cancel(self):
        """
        Test cancelling acquisition for SEM MD CL intensity
        """
        self.skipIfNotSupported("cl")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("test",
                                      self.cl, self.cl.data, self.ebeam,
                                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 100
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s

        mcs.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # Let it run for a short while and stop
        for i in range(4):
            time.sleep(2)
            f.cancel()
            time.sleep(0.1)
            for l in sms.leeches:
                l.series_start()
            f = sms.acquire()

        # Finally acquire something really, and check it worked
        data, exp = f.result(timeout)
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_cl_only(self):
        """
        Test short & long acquisition for SEM MD CL intensity, without SE stream
        """
        self.skipIfNotSupported("cl")
        # Create the stream
        mcs = stream.CLSettingsStream("test",
                                      self.cl, self.cl.data, self.ebeam,
                                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [mcs])

        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 100
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        mcs.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s

        mcs.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 2)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        cl_md = sms.raw[0].metadata
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_cl_se_ebic(self):
        """
        Test short & long acquisition for SE CL and EBIC acquisition simultaneously
        """
        self.skipIfNotSupported("ebic", "cl")
        # create axes
        axes = {"filter": ("band", self.filter)}

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("testCL",
                                      self.cl, self.cl.data, self.ebeam,
                                      axis_map=axes,
                                      emtvas={"dwellTime", })
        mcs_two = stream.EBICSettingsStream("testEBIC",
                                      self.ebic, self.ebic.data, self.ebeam,
                                      emtvas={"dwellTime", })

        sms = stream.SEMMDStream("test sem-md", [sems, mcs, mcs_two])

        # Now, proper acquisition
        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        mcs.tint.value = (255, 0, 0)  # Red colour
        mcs_two.roi.value = (0, 0.2, 0.3, 0.6)
        mcs_two.tint.value = (0, 255, 0)  # Green colour

        # On the simulator, 3µs is the minimum dwell time that is accepted with 3 detectors
        # (dwell time of sems shouldn't matter)
        mcs.emtDwellTime.value = 3e-6  # s
        mcs_two.emtDwellTime.value = 3e-6  # s

        mcs.repetition.value = (500, 700)
        mcs_two.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        logging.debug("Estimating %g s", sms.estimateAcquisitionTime())
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and the two CLs should have the same shape
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        self.assertEqual(sms.raw[2].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        cl_two_md = sms.raw[2].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(cl_two_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_two_md[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(cl_md[model.MD_USER_TINT], (255, 0, 0))  # from .tint
        self.assertEqual(cl_two_md[model.MD_USER_TINT], (0, 255, 0))  # from .tint
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])

        # Now same thing but with more pixels and drift correction
        mcs.roi.value = (0.3, 0.1, 1.0, 0.8)
        mcs_two.roi.value = (0.3, 0.1, 1.0, 0.8)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        mcs.repetition.value = (3000, 4000)
        b0, b1 = list(mcs.axisFilter.choices)[:2]
        mcs.axisFilter.value = b0
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        mcs_two.repetition.value = (3000, 4000)

        # Start acquisition
        timeout = 1 + 2.5 * sms.estimateAcquisitionTime()
        start = time.time()
        dc.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dc.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))
        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 4)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        self.assertEqual(sms.raw[2].shape, exp_res[::-1])
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        cl_two_md = sms.raw[2].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(cl_two_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_two_md[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(cl_md[model.MD_USER_TINT], (255, 0, 0))  # from .tint
        self.assertEqual(cl_two_md[model.MD_USER_TINT], (0, 255, 0))  # from .tint

    def test_acq_spec_rotated(self):
        """
        Test short & long acquisition for Spectrometer with rotation
        """
        self.skipIfNotSupported("spec", "vector")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        # Warning: this test cases relies on the andorcam2 simulator, which simulates HW trigger
        # by assuming the trigger is immediately received.

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)
        specs.rotation.value = math.radians(10)
        exp_rot = specs.rotation.value

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        im0 = specs.image.value
        start = time.time()
        f = sps.acquire()

        # Check if there is a live update in the setting stream.
        time.sleep(3)  # Wait long enough so that there is a new image
        im1 = specs.image.value
        self.assertIsInstance(im1, model.DataArray)
        testing.assert_array_not_equal(im0, im1)
        time.sleep(6)
        im2 = specs.image.value
        testing.assert_array_not_equal(im1, im2)

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], spec_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(spec_md[model.MD_ROTATION], exp_rot)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.05  # s
        specs.repetition.value = (17, 20)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        timeout = 1 + 2.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], spec_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(spec_md[model.MD_ROTATION], exp_rot)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

    def test_acq_spec_rotated_fuzzy(self):
        """
        Test acquisition for Spectrometer with rotation and fuzzing
        """
        self.skipIfNotSupported("spec", "vector")  # Rotation only supported on vector scan
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        # Warning: this test cases relies on the andorcam2 simulator, which simulates HW trigger
        # by assuming the trigger is immediately received.

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)
        specs.rotation.value = math.radians(50)
        specs.fuzzing.value = True
        exp_rot = specs.rotation.value

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.1  # s
        specs.repetition.value = (17, 10)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        timeout = 1 + 3.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]

        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)

        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], spec_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(spec_md[model.MD_ROTATION], exp_rot)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

    # Scan stage tests
    def test_scan_stage_wrapper(self):
        """
        Simple test case to check if the scan stage wrapper works like expected.
        This wrapper setup is needed when there is no dedicated (physical) scan stage
        """
        self.skipIfNotSupported("scan-stage", "spec")
        # Create the SEM and Spectrum streams
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        specs_sstage = stream.SpectrumSettingsStream("test spec",
                                              self.spec,
                                              self.spec.data,
                                              self.ebeam,
                                              sstage=self.scan_stage)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs_sstage])

        if self.stage.name in self.scan_stage.affects.value:
            # Scan stage is actually the standard stage
            # Check that it works even when not at 0,0 of the sample stage
            posc = {"x": -1e-3, "y": 2e-3}
            sstage_to_abs_shift = (0, 0)
        else:  # Dedicated scan-stage
            # Move the stage to the center
            posc = {"x": sum(self.scan_stage.axes["x"].range) / 2,
                    "y": sum(self.scan_stage.axes["y"].range) / 2}
            stage_pos = self.stage.position.value
            sstage_to_abs_shift = (stage_pos["x"] - posc["x"],
                                   stage_pos["y"] - posc["y"])
        self.scan_stage.moveAbsSync(posc)

        # set stage scanning to true
        specs_sstage.useScanStage.value = True

        # Keep ROI minimal to keep the acquisition time as low as possible with a low repetition
        #   roi ~ 19um x 28um
        #   rep 4, 6
        #   rectangular shape
        specs_sstage.roi.value = (0.492, 0.49, 0.508, 0.51)
        specs_sstage.repetition.value = (4, 6)

        # determine the ROI's physical centre position, number of pixels and pixel size
        exp_cpos, exp_pxsize, exp_pxnum = roi_to_phys(specs_sstage)
        # determine the range of the ROI by calculating the physical size
        rng_topleft = (exp_cpos[0] - ((exp_pxsize[0] * exp_pxnum[0]) / 2),
                       exp_cpos[1] - ((exp_pxsize[1] * exp_pxnum[1]) / 2))
        rng_bottomright = (exp_cpos[0] + ((exp_pxsize[0] * exp_pxnum[0]) / 2),
                           exp_cpos[1] + ((exp_pxsize[1] * exp_pxnum[1]) / 2))
        roi_rng = (rng_topleft, rng_bottomright)

        self.stage_positions = set()
        self.ebeam_positions = set()
        self.ebeam_start_pos = self.ebeam.translation.value
        self.sstage_start_pos = self.scan_stage.position.value

        # Run the acquisition
        f = sps.acquire()
        # Use progress update to track the position of the stage/ebeam: the progress is normally
        # updated after every image acquired, so if everything goes as expected, the stage has moved
        # after every progress update.
        # As there is no drift correction, the ebeam should never move.
        f.add_update_callback(self.on_progress_update_stage)

        self.data, exp = f.result()
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        # Wait a tiny bit to ensure the progress update has been processed
        time.sleep(0.1)

        # check if the stage has moved instead of the e-beam
        self.assertGreater(len(self.stage_positions), 4)
        self.assertEqual(len(self.ebeam_positions), 1)

        # check if the sem data is of the right shape
        sem_da = self.data[0]
        self.assertEqual(sem_da.shape, exp_pxnum[::-1])

        # check if the spectrum data is of the right shape
        sp_da = self.data[1]
        self.assertEqual(len(sp_da.shape), 5)

        # check if the centre position of the metadata matches both the streams
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        self.assertAlmostEqual(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        # check if the spectrum centre pos is comparable with the expected centre pos of the ROI
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_cpos, atol=1e-18)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxsize)

        # check if the centre pixel positions in stage_positions fall within the range of the selected ROI
        for pos in self.stage_positions:
            self.assertGreater(pos[0] + sstage_to_abs_shift[0], roi_rng[0][0])
            self.assertLess(pos[0] + sstage_to_abs_shift[1], roi_rng[1][0])
            self.assertGreater(pos[1] + sstage_to_abs_shift[0], roi_rng[0][1])
            self.assertLess(pos[1] + sstage_to_abs_shift[1], roi_rng[1][1])

    def on_progress_update_stage(self, _, start, end):
        sstage_pos = self.scan_stage.position.value
        self.stage_positions.add((sstage_pos["x"], sstage_pos["y"]))
        self.ebeam_positions.add(self.ebeam.translation.value)

    def test_scan_stage_wrapper_noccd(self):
        """
        start a scan with a detector which is not of type ccd (should fail)
        """
        self.skipIfNotSupported("scan-stage", "ebic")
        # use the component EBIC as a detector in this case
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        specs_sstage = stream.SpectrumSettingsStream("test spec",
                                                     self.ebic,
                                                     self.ebic.data,
                                                     self.ebeam,
                                                     sstage=self.scan_stage)

        # check if passing a non-ccd detector in specs_sstage raises a ValueError
        with self.assertRaises(ValueError):
            stream.SEMSpectrumMDStream("test sem-spec", [sems, specs_sstage])

    def test_roi_out_of_stage_limits(self):
        """
        Check if a ROI at the edge of the stage limits generates an out of range
        exception for the ROI dimensions.
        """
        self.skipIfNotSupported("scan-stage", "spec")
        # Create the SEM and Spectrum streams
        sems = stream.SEMStream("test sem cl", self.sed, self.sed.data, self.ebeam)
        specs_sstage = stream.SpectrumSettingsStream("test spec",
                                              self.spec,
                                              self.spec.data,
                                              self.ebeam,
                                              sstage=self.scan_stage)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs_sstage])

        # set stage scanning to true
        specs_sstage.useScanStage.value = True

        # get stage limits (rng)
        xrng_stage = self.stage.axes["x"].range
        yrng_stage = self.stage.axes["y"].range

        # do a quick out of range check
        f = self.stage.moveAbs({"x": xrng_stage[0] - 1e-6, "y": yrng_stage[0] - 1e-6})
        with self.assertRaises(ValueError):
            f.result()

        # go to the top-left (scan-stage) limit
        self.stage.moveAbsSync({"x": xrng_stage[0], "y": yrng_stage[0]})

        # create a rectangular shaped ROI with out-of-range dimensions
        specs_sstage.roi.value = (0, 0, 0.02, 0.025)
        specs_sstage.repetition.value = (4, 6)

        # Acquisition should fail
        with self.assertRaises(ValueError):
            # In practice, it fails by immediately returning an exception, and no data,
            # but failing even before should be fine too.
            f = sps.acquire()
            data, exp = f.result()
            if exp:
                raise exp

        # Move back the stage to the center
        self.stage.moveAbsSync({"x": 0.0, "y": 0.0})

    def test_acq_spec_sstage(self):
        """
        Test spectrum acquisition with scan stage.
        """
        self.skipIfNotSupported("scan-stage", "spec")

        # Zoom in to make sure the ROI is not too big physically
        self.ebeam.horizontalFoV.value = 200e-6

        if self.stage.name in self.scan_stage.affects.value:
            # Scan stage is actually the standard stage
            # Check that it works even when not at 0,0 of the sample stage
            posc = {"x": -1e-3, "y": 2e-3}
        else:  # Dedicated scan-stage
            # Move the stage to the center
            posc = {"x": sum(self.scan_stage.axes["x"].range) / 2,
                    "y": sum(self.scan_stage.axes["y"].range) / 2}

        self.scan_stage.moveAbsSync(posc)

        # Create the streams
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data,
                                              self.ebeam, sstage=self.scan_stage,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.useScanStage.value = True

        if "vector" in self.capabilities:
            exp_rot = math.radians(10)
        else:
            exp_rot = 0
        specs.rotation.value = exp_rot

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.pixelSize.value = 1e-6
        specs.roi.value = (0.25, 0.45, 0.6, 0.7)
        specs.repetition.value = (5, 6)
        specs.detExposureTime.value = 0.3  # s
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], spec_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(spec_md[model.MD_ROTATION], exp_rot)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

        # Check the stage is back to initial position
        pos = self.scan_stage.position.value
        assert_pos_almost_equal(pos, posc, atol=100e-9)

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.pixelSize.value = 1e-6
        specs.repetition.value = (25, 30)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], spec_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(spec_md[model.MD_ROTATION], exp_rot)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

    def test_acq_spec_sstage_cancel(self):
        """
        Test canceling spectrum acquisition with scan stage.
        """
        self.skipIfNotSupported("scan-stage", "spec")
        # Zoom in to make sure the ROI is not too big physically
        self.ebeam.horizontalFoV.value = 200e-6

        # Move the stage to the center
        posc = {"x": sum(self.scan_stage.axes["x"].range) / 2,
                "y": sum(self.scan_stage.axes["y"].range) / 2}
        self.scan_stage.moveAbsSync(posc)

        # Create the streams
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data,
                                              self.ebeam, sstage=self.scan_stage,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.useScanStage.value = True

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.pixelSize.value = 1e-6
        specs.roi.value = (0.25, 0.45, 0.6, 0.7)
        specs.repetition.value = (5, 6)
        specs.detExposureTime.value = 0.3  # s

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        f = sps.acquire()

        # Wait a bit and cancel
        time.sleep(estt / 2)
        f.cancel()
        time.sleep(0.1)

        # Check the stage is back to initial position
        pos = self.scan_stage.position.value
        assert_pos_almost_equal(pos, posc, atol=100e-9)

        # Check it still works after cancelling
        specs.detExposureTime.value = 0.01  # s
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        self.assertAlmostEqual(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_spec_sstage_all(self):
        """
        Test spectrum acquisition with scan stage, fuzzying, and drift correction.
        """
        self.skipIfNotSupported("scan-stage", "spec")
        # Zoom in to make sure the ROI is not too big physically
        self.ebeam.horizontalFoV.value = 200e-6

        # Move the stage to the center
        posc = {"x": sum(self.scan_stage.axes["x"].range) / 2,
                "y": sum(self.scan_stage.axes["y"].range) / 2}
        self.scan_stage.moveAbsSync(posc)

        # Create the streams
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data,
                                              self.ebeam, sstage=self.scan_stage,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.useScanStage.value = True
        specs.fuzzing.value = True

        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.8, 0.5, 0.9, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        if "vector" in self.capabilities:
            exp_rot = math.radians(10)
        else:
            exp_rot = 0
        specs.rotation.value = exp_rot

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.pixelSize.value = 1e-6
        specs.roi.value = (0.25, 0.45, 0.6, 0.7)
        specs.repetition.value = (5, 6)
        specs.detExposureTime.value = 0.3  # s
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        for l in sps.leeches:
            l.series_start()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        for l in sps.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata

        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], spec_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(spec_md[model.MD_ROTATION], exp_rot)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

        # Check the stage is back to its initial position
        pos = self.scan_stage.position.value
        assert_pos_almost_equal(pos, posc, atol=100e-9)

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.pixelSize.value = 1e-6
        specs.repetition.value = (25, 30)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        for l in sps.leeches:
            l.series_start()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        for l in sps.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata

        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(sem_md[model.MD_ROTATION], spec_md[model.MD_ROTATION])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(spec_md[model.MD_ROTATION], exp_rot)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")



# SPARC v1
class SPARCTestCase(BaseSPARCTestCase):
    simulator_config = SPARC_CONFIG
    capabilities = {"ar", "spec", "monochromator"}

    def test_count(self):
        cs = stream.CameraCountStream("test count", self.spec, self.spec.data, self.ebeam)
        self.spec.exposureTime.value = 0.1
        exp = self.spec.exposureTime.value
        res = self.spec.resolution.value
        rot = numpy.prod(res) / self.spec.readoutRate.value
        dur = exp + rot
        cs.windowPeriod.value = 15 * dur

        # at start, no data => empty window
        window = cs.image.value
        self.assertEqual(len(window), 0)

        # acquire for a few seconds
        cs.should_update.value = True
        cs.is_active.value = True

        time.sleep(5 * dur)
        # Should have received at least a few data, and max 5
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(2 <= len(window) <= 5, len(window))
        self.assertEqual(window.ndim, 1)
        dates = window.metadata[model.MD_TIME_LIST]
        self.assertLess(-cs.windowPeriod.value - dur, dates[0])
        numpy.testing.assert_array_equal(dates, sorted(dates))

        time.sleep(15 * dur)
        # Should have received enough data to fill the window
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(10 <= len(window) <= 16, len(window))

        time.sleep(5 * dur)
        # Window should stay long enough
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(10 <= len(window) <= 16, len(window))
        dates = window.metadata[model.MD_TIME_LIST]
        self.assertLess(-cs.windowPeriod.value - dur, dates[0])
        numpy.testing.assert_array_equal(dates, sorted(dates))

        cs.is_active.value = False


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


class SPARC2TestCase(BaseSPARCTestCase):
    """
    Tests to be run with a (simulated) SPARCv2, with dedicated scan-stage
    """
    simulator_config = SPARC2_CONFIG
    capabilities = {"cl", "ar", "spec", "scan-stage"}
    #  DEBUG
    # def test_scan_stage_wrapper(self):
    #     super().test_scan_stage_wrapper()

    def test_acq_spec_leech(self):
        """
        Test Spectrometer acquisition with ProbeCurrentAcquirer (leech)
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        pcd = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(pcd)
        sems.leeches.append(pca)

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        exp_pos, exp_pxs, exp_res = roi_to_phys(specs)
        pca.period.value = 0.6  # ~every second pixel

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

        pcmd = spec_md[model.MD_EBEAM_CURRENT_TIME]
        self.assertGreater(len(pcmd), 5 * 6 / 2)

    def test_acq_cl_leech(self):
        """
        Test acquisition for SEM MD CL intensity + 2 leeches
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("test",
                                      self.cl, self.cl.data, self.ebeam,
                                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        # TODO update unit tests to test for a cornerecase: where leech is set far after update_interval and
        #  therefore a non rectangular block may be scanned (case where leech and update interval run semi in
        #  phase/out of phase). Test assembleLiveData if finsishes the  current line. After which a new rectangular block
        #  is scanned instead of running into an error

        pcd = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(pcd)
        sems.leeches.append(pca)

        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 100
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s
        mcs.repetition.value = (500, 700)
        pca.period.value = 4900e-6  # ~every 10 lines
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime() + (0.3 * 700 / 10)
        logging.debug("Expecting acquisition of %g s", timeout)
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)

        pcmd = cl_md[model.MD_EBEAM_CURRENT_TIME]
        self.assertGreater(len(pcmd), 500 / 10)


class SPARC2TestCaseStageWrapper(BaseSPARCTestCase):
    """
    This test case is specifically targeting the use of a stage wrapper to
    enable stage scanning with the SEM sample stage.
    """
    simulator_config = SPARC2_4SPEC_CONFIG
    capabilities = {"ar", "spec", "ebic", "scan-stage"}


# Skip if ubuntu is 20.04 or lower, as nidaqmx does not work there
# Check using the python version, because that's easier than checking the OS version
@unittest.skipIf(sys.version_info < (3, 9), "nidaqmx does not work for Ubuntu 20.04 or lower")
class SPARC2ScanStageVectorTestCase(BaseSPARCTestCase):
    """
    Tests to be run with a (simulated) SPARCv2 with a scan stage and vector scanning.
    """
    simulator_config = SPARC2_VECTOR_SSTAGE_CONFIG
    capabilities = {"cl", "spec", "vector", "scan-stage"}


class SPARC2StreakCameraTestCase(BaseSPARCTestCase):
    """
    Tests to be run with a (simulated) SPARCv2 equipped with a streak camera
    for temporal spectral measurements.
    """
    simulator_config = SPARC2STREAK_CONFIG
    capabilities = {"ebic", "streakcam"}  # For speed, skip: "cl", "ar", "ccd"

    def setUp(self):
        super().setUp()
        # Wait a bit for the simulator to be "ready" again (as it's not a very good simulator)
        # Otherwise, if immediately stopping & starting, the simulator may generate an old image,
        # very early.
        time.sleep(2)

    def test_streak_live_stream(self):  # TODO  this one has still exposureTime
        """ Test playing TemporalSpectrumSettingsStream
        and check shape and MD for image received are correct."""

        # Create the settings stream
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        streaks.image.subscribe(self._on_image)

        # shouldn't affect
        streaks.roi.value = (0.15, 0.6, 0.8, 0.8)
        streaks.repetition.value = (5, 6)

        # set GUI VAs
        streaks.detExposureTime.value = 0.5  # s
        streaks.detBinning.value = (2, 2)  # TODO check with real HW
        streaks.detStreakMode.value = True
        streaks.detTimeRange.value = find_closest(0.000000005, self.streak_unit.timeRange.choices)
        streaks.detMCPGain.value = 0  # Note: cannot set any other value here as stream is inactive

        # update stream (live)
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        # Disable the protections
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        # Confirm they actually are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        time.sleep(2)
        streaks.is_active.value = False

        self.assertGreater(len(self._images), 0, "No temporal spectrum received after 2s")
        self.assertIsInstance(self._images[-1], model.DataArray)
        # .image should be a 2D temporal spectrum
        self.assertEqual(self._images[-1].shape[1::-1], streaks.detResolution.value)
        # check if metadata is correctly stored
        md = self._images[-1].metadata
        self.assertIn(model.MD_WL_LIST, md)
        self.assertIn(model.MD_TIME_LIST, md)

        # check raw image is a DataArray with right shape and MD
        self.assertIsInstance(streaks.raw[0], model.DataArray)
        self.assertEqual(streaks.raw[0].shape[1::-1], streaks.detResolution.value)
        self.assertIn(model.MD_TIME_LIST, streaks.raw[0].metadata)
        self.assertIn(model.MD_WL_LIST, streaks.raw[0].metadata)

        # Check the streak-cam protection is activated: MCPGain = 0 and shutter active
        self.assertTrue(self.streak_unit.shutter.value)
        self.assertEqual(self.streak_unit.MCPGain.value, 0)

        streaks.image.unsubscribe(self._on_image)

    def test_streakcam_stream(self):
        """Test playing StreakCamStream and check shape and MD for image received are correct."""

        # Create the settings stream
        streaks = stream.StreakCamStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                         self.ebeam, self.streak_unit, self.streak_delay,
                                         detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                         streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        streaks.image.subscribe(self._on_image)

        # set GUI VAs
        streaks.detExposureTime.value = 0.2  # s
        streaks.detBinning.value = (2, 2)
        streaks.detStreakMode.value = True
        streaks.detTimeRange.value = find_closest(0.000000005, self.streak_unit.timeRange.choices)
        streaks.detMCPGain.value = 0  # Note: cannot set any other value here as stream is inactive

        # update stream (live)
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        # Disable the protections
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        # Confirm they actually are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        # Disable the streak mode => it should trigger the protection
        streaks.detStreakMode.value = False
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertTrue(self.streak_unit.shutter.value)

        # Re-enable the settings: it should be allowed
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        # Confirm they actually are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        # Re-enable the streak mode: it should not change the settings (because this way is safe)
        streaks.detStreakMode.value = True
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        time.sleep(2)
        streaks.is_active.value = False

        self.assertGreater(len(self._images), 0,"No temporal spectrum received after 2s")
        self.assertIsInstance(self._images[-1], model.DataArray)
        # .image should be a 2D temporal spectrum
        self.assertEqual(self._images[-1].shape[1::-1], streaks.detResolution.value)
        # check if metadata is correctly stored
        md = self._images[-1].metadata
        self.assertIn(model.MD_WL_LIST, md)
        self.assertIn(model.MD_TIME_LIST, md)

        # check raw image is a DataArray with right shape and MD
        self.assertIsInstance(streaks.raw[0], model.DataArray)
        self.assertEqual(streaks.raw[0].shape[1::-1], streaks.detResolution.value)
        self.assertIn(model.MD_TIME_LIST, streaks.raw[0].metadata)
        self.assertIn(model.MD_WL_LIST, streaks.raw[0].metadata)

        # Check the streak-cam protection is activated: MCPGain = 0 and shutter active
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertTrue(self.streak_unit.shutter.value)

        streaks.image.unsubscribe(self._on_image)

    def test_streak_gui_vas(self):
        """ Test playing TemporalSpectrumSettingsStream
        and check that settings are correctly applied."""

        # Create the settings stream
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        # shouldn't affect
        streaks.roi.value = (0.15, 0.6, 0.8, 0.8)
        streaks.repetition.value = (5, 6)

        ###inactive stream######################################################################################
        # set GUI VAs
        streaks.detExposureTime.value = 0.5  # s
        streaks.detBinning.value = (4, 4)  # TODO check with real HW
        streaks.detStreakMode.value = True
        streaks.detTimeRange.value = find_closest(0.000000005, self.streak_unit.timeRange.choices)
        streaks.detMCPGain.value = 0  # Note: cannot set any other value here as stream is inactive

        # set HW VAs to position different from GUI VAs
        self.streak_ccd.exposureTime.value = 0.3  # s
        self.streak_ccd.binning.value = (2, 2)  # TODO runtimeError however values are set correctly in HPDTA
        self.streak_unit.streakMode.value = False
        self.streak_unit.timeRange.value = find_closest(0.000000001, self.streak_unit.timeRange.choices)
        self.streak_unit.MCPGain.value = 2

        # while stream is not active, HW should not move, therefore
        # check VAs connected to GUI did not trigger VAs listening to HW
        self.assertNotEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertNotEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        self.assertNotEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)
        self.assertNotEqual(streaks.detTimeRange.value, self.streak_unit.timeRange.value)
        self.assertNotEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        ###active stream######################################################################################
        # update stream (live)
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        # set value to higher value only possible if stream is active
        # hack to check HW VA was updated
        streaks.detMCPGain.value = 1

        time.sleep(0.1)  # some time to set the HW VAs

        # GUI VA and HW VA should be the same when acquiring or playing the stream
        # stream got active, HW VA should be same as GUI VA
        # check streak VA connected to GUI shows same value as streak VA listening to HW
        self.assertEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        # the order of setting the HWVAs is TimeRange, StreakMode, MCPGain
        # MCPGain last as otherwise set to zero due to safety functionality in driver
        self.assertEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)
        self.assertEqual(streaks.detTimeRange.value, self.streak_unit.timeRange.value)
        self.assertEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        # change VAs --> HW VAs should change as stream is still active
        streaks.detExposureTime.value = 0.1  # s
        streaks.detBinning.value = (2, 2)  # TODO check with real HW
        time.sleep(0.1)
        # check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        streaks.detMCPGain.value = 3
        time.sleep(0.1)
        # check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        streaks.detMCPGain.value = 4
        streaks.detStreakMode.value = False
        # test MCP gain is 0 when changing .streakMode
        time.sleep(0.1)
        self.assertEqual(streaks.detMCPGain.value, 0)  # GUI VA should be 0 after changing .streakMode
        self.assertEqual(self.streak_unit.MCPGain.value, 0)  # HW VA should be 0 after changing .streakMode
        # check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)

        # set value unequal 0 and then pause stream for checking whether GUI VA keeps value,
        # but HW VA is set to 0 when stream is inactive/paused.
        streaks.detMCPGain.value = 6
        # double check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        ###inactive stream######################################################################################
        # deactivate stream
        streaks.is_active.value = False
        time.sleep(0.1)

        # check MCPGain HW VA is zero when stream is inactive but GUI VA keeps the previous value
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertNotEqual(streaks.detMCPGain.value, 0)
        # check GUI VA do not show same values as HW VAs
        self.assertNotEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        streaks.detMCPGain.value = 4
        time.sleep(0.1)
        # check GUI VA do not show same values as HW VAs
        self.assertNotEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)
        # value > current MCPGain GUI value while stream is not active shouldn't be possible
        # also checks if .MCPGain.range has updated
        with self.assertRaises(IndexError):
            streaks.detMCPGain.value = 5

        # change GUI VAs --> HW VAs should not update as stream is inactive
        streaks.detExposureTime.value = 0.2  # s
        streaks.detBinning.value = (4, 4)  # TODO check with real HW
        time.sleep(0.1)
        # check GUI VA do not show same values as HW VAs
        self.assertNotEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertNotEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        # Change the settings VA, while not playing -> no effect on the hardware
        streaks.detStreakMode.value = True
        self.assertNotEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)

        # change .streakMode from True to False -> MCPGain GUI VA should be 0
        streaks.detStreakMode.value = False
        time.sleep(0.1)
        self.assertEqual(streaks.detMCPGain.value, 0)  # GUI VA should be 0 after changing .streakMode
        # value > current MCPGain GUI value while stream is not active shouldn't be possible
        # also checks if .MCPGain.range has been updated
        with self.assertRaises(IndexError):
            streaks.detMCPGain.value = 1

        #########################################################################################
        # checks that the order of setting the VAs when stream gets active is correct
        # (MCPGain should be last)

        # update stream (live) to change MCPGain
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        streaks.detMCPGain.value = 5
        time.sleep(0.1)

        # inactivate stream
        streaks.is_active.value = False

        # set GUI VAs
        streaks.detMCPGain.value = 3
        # check .MCPGain HW VA = 0
        self.assertEqual(self.streak_unit.MCPGain.value, 0)

        # update stream (live) to change MCPGain
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        # check MCPGain is not 0 as set last when stream gets active
        self.assertNotEqual(streaks.detMCPGain.value, 0)
        # checks that HW VA and GUI VA are equal when stream active
        self.assertEqual(self.streak_unit.MCPGain.value, streaks.detMCPGain.value)

        # inactivate stream
        streaks.is_active.value = False

    def test_streak_stream_va_integrated_images(self):
        """ Test playing TemporalSpectrumSettingsStream
        and check that images are correctly integrated when
        an exposure time (integration time) is requested,
        which is longer than the detector is capable of."""

        # Create the settings stream without "exposureTime" VA
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        # shouldn't affect
        streaks.roi.value = (0.15, 0.6, 0.8, 0.8)
        streaks.repetition.value = (5, 6)

        ###inactive stream######################################################################################
        # set stream VA
        streaks.integrationTime.value = 2.0  # s

        # set HW VA to position different from stream VA
        self.streak_ccd.exposureTime.value = 0.3  # s

        # while stream is not active, HW should not move, therefore
        # check stream VA did not trigger HW VA to change
        self.assertNotEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)

        ###active stream######################################################################################
        # update stream (live, uses SettingsStream, CCDSettingsStream, RepetitionStream, LiveStream, Stream (_base.py))
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True
        self.assertEqual(len(streaks.raw), 0)  # empty list of raw images when stream deactivated

        # HW VA should be updated with the correct value when acquiring or playing the stream
        # check explicit values of stream and HW VA
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 2)

        # change stream VA --> HW VAs should change as stream is still active
        streaks.integrationTime.value = 4.0  # s
        time.sleep(streaks.integrationTime.value + 0.5)
        # check stream VA shows not the same value as the HW VA
        self.assertNotEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 4)
        self.assertEqual(streaks.integrationCounts.value, 4)

        # change stream VA --> HW VAs should change as stream is still active
        streaks.integrationTime.value = 0.9  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.streak_ccd.exposureTime.value, 0.9)
        self.assertEqual(streaks.integrationTime.value, 0.9)
        self.assertEqual(streaks.integrationCounts.value, 1)

        # change stream VA --> HW VAs should change as stream is still active
        streaks.integrationTime.value = 1.0  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 1)
        self.assertEqual(streaks.integrationCounts.value, 1)

        streaks.integrationTime.value = 4.0  # s
        time.sleep(0.1)

        ###inactive stream######################################################################################
        # deactivate stream
        streaks.is_active.value = False
        time.sleep(0.1)

        # check stream and HW VA still shows the same value as before and are different from each other
        self.assertNotEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 4)

    def test_streak_acq_live_update(self):
        """Test if live update works during acquisition with streak camera"""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True

        streaks.detExposureTime.value = 0.01  # 10ms
        streaks.roi.value = (0.1, 0.1, 0.8, 0.8)
        streaks.repetition.value = (10, 12)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        acq_time = stss.estimateAcquisitionTime()
        timeout = 1.5 * acq_time
        logging.debug("Expecting an acquisition of %s s", acq_time)
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # Check if there is a live update in the setting stream.
        # (also works in the simulator, thanks to the noise in the simulated image)
        time.sleep(1.0)
        im1 = streaks.image.value
        self.assertFalse(f.done())

        time.sleep(2.5)  # Live update happens every 2s
        self.assertFalse(f.done()) # It should still be live, so that it keeps updating
        im2 = streaks.image.value

        # wait until it's over
        data, exp = f.result(timeout)
        self.assertIsNone(exp)

        # Check if the image changed (live update is working)
        testing.assert_array_not_equal(im1, im2)

    def test_streak_acq(self):
        """Test acquisition with streak camera"""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        streaks.detExposureTime.value = 0.1  # 100ms
        # Disable the protections
        streaks.detMCPGain.value = 5
        streaks.detShutter.value = False

        # # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (10, 5)
        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        # Confirm protections are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertTrue(self.streak_unit.shutter.value)

        # check if number of images in the received data (sem image + temporal spectrum images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(stss.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired temporal spectrum images matches the number of ebeam positions
        ts_da = stss.raw[1]  # temporal spectrum data array
        shape = ts_da.shape
        self.assertEqual(shape[3] * shape[4], num_ts)
        # len of shape should be 5: CTZYX
        self.assertEqual(len(shape), 5)

        # check if metadata is correctly stored
        md = ts_da.metadata
        self.assertIn(model.MD_STREAK_TIMERANGE, md)
        self.assertIn(model.MD_STREAK_MCPGAIN, md)
        self.assertIn(model.MD_STREAK_MODE, md)
        self.assertIn(model.MD_TRIGGER_DELAY, md)
        self.assertIn(model.MD_TRIGGER_RATE, md)
        self.assertIn(model.MD_POS, md)  # check the corresponding SEM pos is there
        self.assertIn(model.MD_PIXEL_SIZE, md)  # check the corresponding SEM pos is there
        self.assertIn(model.MD_WL_LIST, md)
        self.assertIn(model.MD_TIME_LIST, md)

        md = sem_da.metadata
        self.assertIn(model.MD_PIXEL_SIZE, md)
        self.assertIn(model.MD_POS, md)

        # start same acquisition again and check acquisition does not timeout due to sync failures
        timeout2 = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data, exp = f.result(timeout2)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

    def test_streak_acq_leech(self):
        """
        Test acquisition for SEM + temporal spectrum acquisition + 1 leech (drift correction).
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True

        streaks.detExposureTime.value = 1  # 1s
        # # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (10, 5)

        streaks.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1  # s
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()

        for l in stss.leeches:
            l.series_start()

        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data, exp = f.result(timeout)

        for l in streaks.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        # check if number of images in the received data (sem image + temporal spectrum images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(stss.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired temporal spectrum images matches the number of ebeam positions
        ts_da = stss.raw[1]  # temporal spectrum data array
        shape = ts_da.shape
        self.assertEqual(shape[3] * shape[4], num_ts)
        # len of shape should be 5: CTZYX
        self.assertEqual(len(shape), 5)

        # check last image in .raw has a time axis greater than 1
        # TODO this is always the case for temporalSpetrum, copied that from ar acq, why is time axis there greater 1?
        temporalSpectrum_drift = ts_da  # temporal spectrum data array
        self.assertGreaterEqual(temporalSpectrum_drift.shape[-4], 2)
        # TODO how to test that drift correction worked actually?

    def test_streak_acq_integrated_images(self):
        """Test acquisition with streak camera with a long exposure time
        (integration time), so image integration is necessary."""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        sems.emtDwellTime.value = 1e-06

        # set a baseline, which does not effect data, but needed later to verify baseline is handled correctly
        self.streak_ccd.updateMetadata({model.MD_BASELINE: 0})

        # set stream VAs
        streaks.integrationTime.value = 2  # s
        # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (2, 4)  # results in (2, 3)
        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        ts_da = data[1]  # temporal spectrum data array
        shape = ts_da.shape
        # check that the number of acquired temporal spectrum images matches the number of ebeam position
        self.assertEqual(shape[3] * shape[4], num_ts)

        # check if number of images in the received data (sem image + temporal spectrum images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(stss.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check if metadata is correctly stored
        md = ts_da.metadata
        self.assertAlmostEqual(md[model.MD_EXP_TIME], streaks.integrationTime.value)
        self.assertIn(model.MD_INTEGRATION_COUNT, md)
        # check that HW exp time * numberOfImages = integration time
        self.assertAlmostEqual(self.streak_ccd.exposureTime.value * md[model.MD_INTEGRATION_COUNT],
                         streaks.integrationTime.value)

        # check the dtype is correct
        self.assertEqual(ts_da.dtype, numpy.uint32)

        time.sleep(2)
        # do a second acquisition with longer exp time and check values are bigger due to integration
        streaks.integrationTime.value = 2.5  # s

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data2, exp = f.result(timeout)
        self.assertIsNone(exp)
        ts_da2 = data2[1]  # temporal spectrum data array

        # test that the values in the second acquisition are greater (integrationCount greater than first acq)
        numpy.testing.assert_array_less(ts_da, ts_da2)

        # check background subtraction
        streaks.integrationTime.value = 2  # s
        self.streak_ccd.updateMetadata({model.MD_BASELINE: 100})

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data3, exp = f.result(timeout)
        self.assertIsNone(exp)
        ts_da3 = data3[1]  # temporal spectrum data array

        # check baseline is not multiplied by integrationCount (we keep only one baseline level for integrated img)
        self.assertEqual(ts_da3.metadata[model.MD_BASELINE], 100)
        # test that the baseline is actually removed compared to same acquisition without baseline
        numpy.testing.assert_array_less(ts_da3, ts_da)

    def test_streak_acq_integrated_images_leech(self):
        """Test acquisition with streak camera with a long exposure time
        (integration time), so image integration is necessary and one leech (drift correction)."""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        sems.emtDwellTime.value = 1e-06

        # set stream VAs
        streaks.integrationTime.value = 2  # s
        # The maximum exposure time of the streak-ccd is 1s => 2 images are integrated
        assert streaks.integrationCounts.value == 2
        streaks.roi.value = (0, 0.2, 0.4, 0.8)
        streaks.repetition.value = (3, 5)  # results in (2, 4)

        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1  # s  so should run leech for sub acquisitions (between integrating 2 images)
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()

        for l in stss.leeches:
            l.series_start()

        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data, exp = f.result(timeout)

        for l in streaks.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        ts_da = data[1]  # temporal spectrum data array
        shape = ts_da.shape
        # check that the number of acquired temporal spectrum images matches the number of ebeam position
        self.assertEqual(shape[3] * shape[4], num_ts)

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired temporal spectrum images matches the number of ebeam positions
        ts_da = stss.raw[1]  # temporal spectrum data array
        shape = ts_da.shape
        self.assertEqual(shape[3] * shape[4], num_ts)
        # len of shape should be 5: CTZYX
        self.assertEqual(len(shape), 5)

        # check last image in .raw has a time axis greater than 1 (last image is the drift correction image)
        temporalSpectrum_drift = ts_da[-1]  # drift correction image
        self.assertGreaterEqual(temporalSpectrum_drift.shape[-4], 2)


# Skip if ubuntu is 20.04 or lower, as nidaqmx does not work there
# Check using the python version, because that's easier than checking the OS version
@unittest.skipIf(sys.version_info < (3, 9), "nidaqmx does not work for Ubuntu 20.04 or lower")
class SPARC2HwSyncTestCase(BaseSPARCTestCase):
    """
    Tests to be run with a (simulated) SPARCv2 using a hardware trigger between the
    e-beam scanner and the CCD/spectrometer.
    """
    simulator_config = SPARC2_HWSYNC_CONFIG
    capabilities = {"ar", "spec", "hwsync", "vector"}


class SPARC2PolAnalyzerTestCase(BaseSPARCTestCase):
    """
    Tests to be run with a (simulated) SPARCv2
    """
    simulator_config = SPARC2POL_CONFIG
    capabilities = {"ar", "polarimetry"}

    def test_acq_arpol(self):
        """
        Test short acquisition for AR with polarization analyzer component
        """
        self.skipIfNotSupported("ar", "polarimetry")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # No exposureTime VA => integrationTime will be provided
        ars = stream.ARSettingsStream("test ar with analyzer", self.ccd, self.ccd.data,
                                      self.ebeam, analyzer=self.analyzer)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        list_positions = list(ars.polarization.choices) + ["acquireAllPol"]

        # to test each polarization position acquired sequentially in a single acq
        ars.acquireAllPol.value = False

        for pos in list_positions:
            if pos == "acquireAllPol":
                # to test all polarization position acquired in one acquisition
                ars.acquireAllPol.value = True
                # set pos to random pol pos from list as "acquireAllPol" is not a valid choice
                ars.polarization.value = "vertical"
            else:
                ars.polarization.value = pos

            ars.integrationTime.value = 0.5  # s
            # TODO use fixed repetition value -> set ROI?
            ars.repetition.value = (1, 1)
            num_ar = numpy.prod(ars.repetition.value)
            exp_pos, exp_pxs, exp_res = roi_to_phys(ars)

            # Start acquisition
            # estimated acquisition time should be accurate with less than 50% margin + 1 extra second
            timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
            start = time.time()
            im0 = ars.image.value
            f = sas.acquire()

            if pos == "acquireAllPol":
                # Check if there is a live update in the setting stream.
                # Only checked with multiple polarization, as otherwise, there is
                # only one image anyway.
                for i in range(10):  # Wait long enough so that there is a new image
                    time.sleep(3)
                    im1 = ars.image.value
                    if im1 is not im0:
                        logging.debug("Got image update after %d iteration", i)
                        break
                else:
                    self.fail("Live image hasn't been updated")

                self.assertIsInstance(im1, model.DataArray)

                time.sleep(timeout / 3)  # Long enough so that a different polarization has been acquired
                im2 = ars.image.value
                logging.debug("New live image is of shape %s", im2.shape)
                # Check if the image changed (live update is working)
                testing.assert_array_not_equal(im1, im2)

            # sas.raw: array containing as first entry the sem scan image for the scanning positions,
            # rest are ar images
            # data: array should contain same images as sas.raw

            # wait until it's over
            data, exp = f.result(timeout)
            dur = time.time() - start
            logging.debug("Acquisition took %g s", dur)
            self.assertTrue(f.done())
            self.assertIsNone(exp)

            # check if number of images in the received data (sem image + ar images) is the same as
            # number of images stored in raw
            self.assertEqual(len(data), len(sas.raw))

            # check that sem data array has same shape as expected for the scanning positions of ebeam
            sem_da = sas.raw[0]  # sem data array for scanning positions
            self.assertEqual(sem_da.shape, exp_res[::-1])

            # check that number of angle resolved images is same as the total number of ebeam positions
            # include if multiple polarization images are required per ebeam position
            ar_das = sas.raw[1:]  # angle resolved data arrays
            if ars.acquireAllPol.value:
                self.assertEqual(len(ar_das), num_ar * 6)
            else:
                self.assertEqual(len(ar_das), num_ar)

            # check if metadata is correctly stored
            if ars.acquireAllPol.value:
                # check for each of the 6 polarization positions
                for i in range(6):
                    for d in ar_das[num_ar * i: num_ar * (i + 1)]:
                        md = d.metadata
                        # check if model.MD_POL_MODE is in metadata
                        self.assertIn(model.MD_POL_MODE, md)
                        self.assertIn(model.MD_POL_POS_LINPOL, md)
                        self.assertIn(model.MD_POL_POS_QWP, md)
                        # check that each image has correct polarization position
                        self.assertEqual(md[model.MD_POL_MODE], POL_POSITIONS[i])
            else:
                for d in ar_das:
                    md = d.metadata
                    # check if model.MD_POL_MODE is in metadata
                    self.assertIn(model.MD_POL_MODE, md)
                    self.assertIn(model.MD_POL_POS_LINPOL, md)
                    self.assertIn(model.MD_POL_POS_QWP, md)
                    # check that each image has correct polarization position
                    self.assertEqual(md[model.MD_POL_MODE], pos)

    def test_acq_arpol_leech(self):
        """
        Test acquisition for SEM AR POL intensity + 1 leech
        """
        self.skipIfNotSupported("ar", "polarimetry")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # No exposureTime VA => integrationTime will be provided
        ars = stream.ARSettingsStream("test ar with analyzer", self.ccd, self.ccd.data,
                                      self.ebeam, analyzer=self.analyzer)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.polarization.value = "vertical"
        ars.acquireAllPol.value = False
        ars.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1  # s
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        ars.integrationTime.value = 1  # s
        ars.repetition.value = (2, 3)  # TODO use fixed repetition value -> set ROI?
        assert ars.repetition.value == (2, 3)
        exp_pos, exp_pxs, exp_res = roi_to_phys(ars)

        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin + 1 extra second
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        logging.debug("Expecting acquisition of %g s", timeout)
        start = time.time()

        for l in sas.leeches:
            l.series_start()

        f = sas.acquire()

        # wait until it's over
        data, exp = f.result(timeout)

        for l in sas.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        # check if number of images in the received data (sem image + ar images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(sas.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = sas.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that number of angle resolved images is same as the total number of ebeam positions
        # include if multiple polarization images are required per ebeam position
        ar_das = sas.raw[1:-1]  # angle resolved data arrays

        self.assertEqual(len(ar_das), num_ar)

        # check last image in .raw has a time axis greater than 1 (last image is drift correction image)
        ar_drift = sas.raw[-1]  # drift correction image
        self.assertGreaterEqual(ar_drift.shape[-4], 2)

    def test_arpol_ss(self):
        """ Test ARSettingsStream """
        self.skipIfNotSupported("ar", "polarimetry")
        # Create the stream
        ars = stream.ARSettingsStream("test",
                                      self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer,
                                      detvas={"exposureTime", "readoutRate", "binning", "resolution"})

        # shouldn't affect
        ars.roi.value = (0.15, 0.6, 0.8, 0.8)
        ars.repetition.value = (5, 6)
        ars.detExposureTime.value = 0.1  # s

        # set analyzer to position different from polarization VA connected to GUI
        f = self.analyzer.moveAbs({"pol": "rhc"})
        f.result()
        ars.polarization.value = "lhc"
        # while stream is not active, HW should not move, therefore
        # check polarization VA connected to GUI did not trigger position VA listening to HW
        self.assertNotEqual(ars.polarization.value, self.analyzer.position.value["pol"])

        # Start acquisition
        ars.should_update.value = True
        # activate stream, optical path should be corrected immediately (no need to wait)
        ars.is_active.value = True

        # stream got active, HW should move now
        # check polarization VA connected to GUI shows same value as position VA listening to HW
        self.assertEqual(ars.polarization.value, self.analyzer.position.value["pol"])

        # change VA --> polarization analyzer should move as stream active
        ars.polarization.value = "vertical"
        time.sleep(7)
        # check polarization VA connected to GUI shows same value as position VA listening to HW
        self.assertEqual(ars.polarization.value, self.analyzer.position.value["pol"])

        # deactivate stream
        ars.is_active.value = False

        # change VA --> polarization analyzer should move as stream not active
        ars.polarization.value = "horizontal"
        time.sleep(2)
        # check polarization VA connected to GUI did not trigger position VA listening to HW
        self.assertNotEqual(ars.polarization.value, self.analyzer.position.value["pol"])

    def test_ar_stream_va_integrated_images(self):
        """ Test playing ARSettingsStream
        and check that images are correctly integrated when
        an exposure time (integration time) is requested,
        which is longer than the detector is capable of."""

        self.skipIfNotSupported("ar", "polarimetry")
        # Create the settings stream without "exposureTime" VA
        ars = stream.ARSettingsStream("test ar integrate images",
                                      self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer,
                                      detvas={"readoutRate", "binning", "resolution"})

        # shouldn't affect
        ars.roi.value = (0.15, 0.6, 0.8, 0.8)
        ars.repetition.value = (5, 6)

        ###inactive stream######################################################################################
        # set stream VA
        ars.integrationTime.value = 11.0  # s

        # set HW VA to position different from stream VA
        self.ccd.exposureTime.value = 0.3  # s

        # while stream is not active, HW should not move, therefore
        # check stream VA did not trigger HW VA to change
        self.assertNotEqual(ars.integrationTime.value, self.ccd.exposureTime.value)

        ###active stream######################################################################################
        # update stream (live, uses SettingsStream, CCDSettingsStream, RepetitionStream, LiveStream, Stream (_base.py))
        ars.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        ars.is_active.value = True
        self.assertEqual(len(ars.raw), 0)  # empty list of raw images when stream deactivated

        # HW VA should be updated with the correct value when acquiring or playing the stream
        # check explicit values of stream and HW VA
        self.assertEqual(self.ccd.exposureTime.value, ars.integrationTime.value / ars.integrationCounts.value)
        self.assertEqual(ars.integrationTime.value, 11)

        # change stream VA --> HW VAs should change as stream is still active
        ars.integrationTime.value = 12.0  # s
        time.sleep(ars.integrationTime.value + 0.5)
        # check stream VA shows not the same value as the HW VA
        self.assertNotEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.ccd.exposureTime.value, ars.integrationTime.value / ars.integrationCounts.value)
        self.assertEqual(ars.integrationTime.value, 12)
        self.assertEqual(ars.integrationCounts.value, 2)

        # change stream VA --> HW VAs should change as stream is still active
        ars.integrationTime.value = 0.9  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.ccd.exposureTime.value, 0.9)
        self.assertEqual(ars.integrationTime.value, 0.9)
        self.assertEqual(ars.integrationCounts.value, 1)

        # change stream VA --> HW VAs should change as stream is still active
        ars.integrationTime.value = 1.0  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.ccd.exposureTime.value, 1)
        self.assertEqual(ars.integrationTime.value, 1)
        self.assertEqual(ars.integrationCounts.value, 1)

        ars.integrationTime.value = 12.0  # s
        time.sleep(0.1)

        ###inactive stream######################################################################################
        # deactivate stream
        ars.is_active.value = False
        time.sleep(0.1)

        # check stream and HW VA still shows the same value as before and are different from each other
        self.assertNotEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        self.assertEqual(self.ccd.exposureTime.value, ars.integrationTime.value / ars.integrationCounts.value)
        self.assertEqual(ars.integrationTime.value, 12)

    def test_ar_acq_integrated_images(self):
        """Test acquisition with camera with a long exposure time
        (integration time), so image integration is necessary."""
        self.skipIfNotSupported("ar", "polarimetry")

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # Create without "exposureTime" VA => integrationTime VA
        ars = stream.ARSettingsStream("test ar integrate images",
                                      self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer,
                                      detvas={"readoutRate", "binning", "resolution"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        # For the integration code to get activated, we need a CCD maximum
        # exposure time less than the integrationTime.
        self.assertLess(self.ccd.exposureTime.range[1], 11)

        ars.acquireAllPol.value = False
        sems.emtDwellTime.value = 1e-06

        # set a baseline, which does not effect data, but needed later to verify baseline is handled correctly
        self.ccd.updateMetadata({model.MD_BASELINE: 0})

        # set stream VAs
        ars.integrationTime.value = 11  # s
        # TODO use fixed repetition value -> set ROI?
        ars.repetition.value = (1, 1)
        num_ar = numpy.prod(ars.repetition.value)  # number of expected ar images
        exp_pos, exp_pxs, exp_res = roi_to_phys(ars)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # sas.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second to rest are ar images
        # data: array should contain same images as sas.raw

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        ar_da = data[1:]  # angle resolved data arrays
        # check that the number of acquired ar images matches the number of ebeam position
        self.assertEqual(len(ar_da), num_ar)

        # check if number of images in the received data (sem image + ar images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(sas.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = sas.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check if metadata is correctly stored
        md = ar_da[0].metadata
        self.assertEqual(md[model.MD_EXP_TIME], ars.integrationTime.value)
        self.assertIn(model.MD_INTEGRATION_COUNT, md)
        # check that HW exp time * numberOfImages = integration time
        self.assertAlmostEqual(self.ccd.exposureTime.value * md[model.MD_INTEGRATION_COUNT],
                               ars.integrationTime.value)

        # check the dtype is increased (from uint16), to contain the sum
        self.assertEqual(ar_da[0].dtype, numpy.uint32)

        # do a second acquisition with shorter exp time and check values are smaller (integrationCount smaller)
        ars.integrationTime.value = 1  # s

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data2, exp = f.result(timeout)
        ar_da2 = data2[1:]  # angle resolved data arrays
        self.assertIsNone(exp)

        # test that the values in the second acquisition are smaller
        numpy.testing.assert_array_less(ar_da2[0], ar_da[0])

        # check background subtraction
        ars.integrationTime.value = 11  # s
        self.ccd.updateMetadata({model.MD_BASELINE: 100})

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data3, exp = f.result(timeout)
        ar_da3 = data3[1:]  # angle resolved data arrays
        self.assertIsNone(exp)

        # check baseline is not multiplied by integrationCount (we keep only one baseline level for integrated img)
        self.assertEqual(ar_da3[0].metadata[model.MD_BASELINE], 100)
        # test that the baseline is actually removed compared to same acquisition without baseline
        self.assertLess(ar_da3[0].mean(), ar_da[0].mean())

    def test_ar_acq_integrated_images_leech(self):
        """Test acquisition with camera with a long exposure time
        (integration time), so image integration is necessary and one leech (drift correction)."""

        self.skipIfNotSupported("ar", "polarimetry")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})

        # Create without "exposureTime" VA => integrationTime VA
        ars = stream.ARSettingsStream("test ar integrate images",
                                      self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer,
                                      detvas={"readoutRate", "binning", "resolution"})

        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        sems.emtDwellTime.value = 1e-06

        # set stream VAs
        ars.integrationTime.value = 11  # s
        ars.polarization.value = "vertical"
        ars.acquireAllPol.value = False
        ars.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 5  # s
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        ars.repetition.value = (2, 1)  # TODO use fixed repetition value -> set ROI?
        exp_pos, exp_pxs, exp_res = roi_to_phys(ars)
        num_ar = numpy.prod(ars.repetition.value)  # number of expected ar images

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        start = time.time()

        for l in sas.leeches:
            l.series_start()

        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # sas.raw: array containing as first entry the sem scan image for the scanning positions,
        # from second to rest are ar images
        # data: array should contain same images as sas.raw

        # wait until it's over
        data, exp = f.result(timeout)

        for l in ars.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        ar_da = data[1:-1]  # angle resolved data arrays
        # check that the number of acquired angle resolved images matches the number of ebeam position
        self.assertEqual(len(ar_da), num_ar)

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = sas.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired ar images in raw matches the number of ebeam positions
        ar_da = sas.raw[1:-1]  # angle resolved data arrays
        self.assertEqual(len(ar_da), num_ar)

        # check last image in .raw has a time axis greater than 1  (last image is drift correction image)
        ar_drift = sas.raw[-1]  # drift correction image
        self.assertGreaterEqual(ar_drift.shape[-4], 2)


class SPARC2TestCaseFPLM(BaseSPARCTestCase):
    """
    This test case is specifically targeting the FPLM systems, with PL acquisition
    """
    simulator_config = SPARC2_FPLM_CONFIG
    capabilities = {"spec", "fplm"}

    def test_spec_light_ss(self):
        """ Test SpectrumSettingsStream with a light source """
        self.skipIfNotSupported("spec", "fplm")
        # Create the stream
        specs = stream.SpectrumSettingsStream("test",
                                              self.spec, self.spec.data, self.ebeam,
                                              light=self.light,
                                              detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        specs.image.subscribe(self._on_image)

        # shouldn't affect
        specs.roi.value = (0.15, 0.6, 0.8, 0.8)
        specs.repetition.value = (5, 6)

        specs.detExposureTime.value = 0.3  # s

        # Light has only one channel, so it's easy to handle
        self.assertEqual(self.light.power.value, [0])  # Should start off
        light_pwr = self.light.power.range[1][0]  # max
        specs.power.value = light_pwr

        # Start acquisition
        specs.should_update.value = True
        specs.is_active.value = True

        time.sleep(2)
        # The light should be on
        self.assertEqual(self.light.power.value, [light_pwr])

        specs.is_active.value = False

        self.assertGreater(len(self._images), 0, "No spectrum received after 2s")
        self.assertIsInstance(self._images[0], model.DataArray)
        # .image should be a 1D spectrum
        self.assertEqual(self._images[0].shape, (specs.detResolution.value[0],))

        # The light should be off
        self.assertEqual(self.light.power.value, [0])

        specs.image.unsubscribe(self._on_image)

    def test_acq_spec_light(self):
        """
        Test acquisition for Spectrometer with input light
        """
        self.skipIfNotSupported("spec", "fplm")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              light=self.light,
                                              detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 2s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        # exp_pos, exp_pxs, exp_res = roi_to_phys(specs)

        # Light has only one channel, so it's easy to handle
        self.assertEqual(self.light.power.value, [0])  # Should start off
        light_pwr = self.light.power.range[1][0]  / 2 # half the power
        specs.power.value = light_pwr

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        time.sleep(2)  # Wait long enough so that it really started
        # The light should be on
        self.assertEqual(self.light.power.value, [light_pwr])

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sps.raw))

        # The light should be off
        self.assertEqual(self.light.power.value, [0])

        # There should be metadata about the light
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        spec_md = sp_da.metadata
        self.assertAlmostEqual(spec_md[model.MD_LIGHT_POWER], light_pwr)
        self.assertIsInstance(spec_md[model.MD_IN_WL], tuple)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")


class SPARC2TestCaseIndependentDetector(BaseSPARCTestCase):
    """
    This test case is specifically targeting the IndependentEBICStream
    """
    simulator_config = SPARC2_INDE_EBIC_CONFIG
    capabilities = {"cl", "ebic-inde"}

    def test_ebic_live_stream(self):
        """
        Test live IndependentEBICStream, and especially, that it can provide continuous
        images, although the hardware doesn't support continuous acquisition.
        """
        self.skipIfNotSupported("ebic-inde")
        ebics = stream.IndependentEBICStream("test ebic", self.ebic, self.ebic.data,
                                             self.ebeam, self.sed.data,
                                             emtvas={"dwellTime"})

        self._images = []
        ebics.image.subscribe(self._on_image)

        # Changing the RoI + repetition updates (increases) the pixel size, which leads to a smaller
        # resolution of the full FoV (used during live stream)
        ebics.roi.value = (0.15, 0.6, 0.8, 0.8)
        ebics.repetition.value = (15, 26)

        # set GUI VAs
        ebics.emtDwellTime.value = 0.5e-3  # s

        # update stream (live)
        ebics.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        ebics.is_active.value = True

        # Wait long enough that *several* (~5) images are acquired
        time.sleep(4)
        ebics.is_active.value = False

        # Wait for the stream image projection to be complete
        time.sleep(0.2)

        nb_images = len(self._images)
        self.assertGreater(nb_images, 2, "Not several EBIC images received after 4s")
        self.assertIsInstance(self._images[0], model.DataArray)
        # check if metadata is correctly stored
        md = self._images[0].metadata
        self.assertIn(model.MD_POS, md)
        self.assertIn(model.MD_PIXEL_SIZE, md)

        # check raw image is a DataArray with right shape and MD
        self.assertIsInstance(ebics.raw[0], model.DataArray)
        self.assertIn(model.MD_POS, ebics.raw[0].metadata)
        self.assertIn(model.MD_PIXEL_SIZE, ebics.raw[0].metadata)

        # Check we really don't receive any images anymore
        time.sleep(2)
        self.assertEqual(nb_images, len(self._images), "EBIC images received after stopping live stream")

        ebics.image.unsubscribe(self._on_image)

    def test_ebic_acq(self):
        self.skipIfNotSupported("ebic-inde")
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        ebics = stream.IndependentEBICStream("test ebic", self.ebic, self.ebic.data,
                                             self.ebeam, self.sed.data,
                                             emtvas={"dwellTime"})
        sms = stream.SEMMDStream("test sem-md", [sems, ebics])

        # Now, proper acquisition
        ebics.roi.value = (0, 0.2, 0.3, 0.6)

        # dwell time of sems shouldn't matter
        ebics.emtDwellTime.value = 2e-6  # s

        ebics.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = roi_to_phys(ebics)

        # Start acquisition
        timeout = 5 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and EBIC should have the same shape
        self.assertEqual(len(sms.raw), 2)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        ebic_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], ebic_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], ebic_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(ebic_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(ebic_md[model.MD_PIXEL_SIZE], exp_pxs)

        # Now same thing but with more pixels and drift correction
        ebics.roi.value = (0.3, 0.1, 1.0, 0.8)
        ebics.tint.value = (255, 0, 0)  # Red colour
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 3e-06
        sems.leeches.append(dc)

        ebics.repetition.value = (1200, 1100)
        exp_pos, exp_pxs, exp_res = roi_to_phys(ebics)

        # Start acquisition
        timeout = 1 + 2.5 * sms.estimateAcquisitionTime()
        start = time.time()
        dc.series_start()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        self.assertIsNone(exp)
        dc.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sms.raw))
        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        ebic_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], ebic_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], ebic_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(ebic_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(ebic_md[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(ebic_md[model.MD_USER_TINT], (255, 0, 0))  # from .tint

    def test_acq_cl_se_ebic(self):
        """
        Test acquisition for SE+CL+EBIC acquisition simultaneously (aka "folded stream")
        """
        self.skipIfNotSupported("ebic-inde")
        # create axes
        axes = {"filter": ("band", self.filter)}

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("testCL",
                                      self.cl, self.cl.data, self.ebeam,
                                      axis_map=axes,
                                      emtvas={"dwellTime", })
        ebics = stream.IndependentEBICStream("test ebic", self.ebic, self.ebic.data,
                                             self.ebeam, self.sed.data,
                                             emtvas={"dwellTime"})

        sms = stream.SEMMDStream("test sem-md", [sems, mcs, ebics])

        # Now, proper acquisition
        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        mcs.tint.value = (255, 0, 0)  # Red colour
        ebics.roi.value = (0, 0.2, 0.3, 0.6)
        ebics.tint.value = (0, 255, 0)  # Green colour

        # EBIC stream can adjust quite a lot the dwell time => use that value to configure the CL stream
        ebics.emtDwellTime.value = 10e-6  # s
        mcs.emtDwellTime.value = ebics.emtDwellTime.value

        mcs.repetition.value = (500, 700)
        ebics.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = roi_to_phys(mcs)

        # Start acquisition
        logging.debug("Estimating %g s", sms.estimateAcquisitionTime())
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        f = sms.acquire()

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and the two CLs should have the same shape
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        self.assertEqual(sms.raw[2].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        cl_two_md = sms.raw[2].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        numpy.testing.assert_allclose(cl_two_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_two_md[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(cl_md[model.MD_USER_TINT], (255, 0, 0))  # from .tint
        self.assertEqual(cl_two_md[model.MD_USER_TINT], (0, 255, 0))  # from .tint
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])


class TimeCorrelatorTestCase(BaseSPARCTestCase):
    """
    Tests the SEMTemporalMDStream.
    """
    simulator_config = TIME_CORRELATOR_CONFIG
    capabilities = {"time-correlator"}  # Skip: "ar", "spec", "cl"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.optmngr = path.OpticalPathManager(cls.microscope)

        # Wait extra time for the referencing at init
        # (during referencing the shutters are force closed, so the acquisition
        # goes faster because the shutters can't open anyway, which is not realistic)
        time.sleep(10)

    def test_tc_acquisition(self):
        """
        Test the output of a simple acquisition and one with subpixel drift correction.
        """
        self.skipIfNotSupported("time-correlator")
        tc_stream = stream.ScannedTemporalSettingsStream(
            "Time Correlator",
            self.time_correlator,
            self.time_correlator.data,
            self.ebeam,
            detvas={"dwellTime"},
        )
        sem_stream = stream.SpotSEMStream("Ebeam", self.sed, self.sed.data, self.ebeam)
        sem_tc_stream = stream.SEMTemporalMDStream("SEM Time Correlator",
                                                   [sem_stream, tc_stream])

        # randomly picked value, to simulate previous value
        self.ebeam.dwellTime.value = 0.042

        sem_tc_stream.roi.value = (0, 0, 0.1, 0.2)
        tc_stream.repetition.value = (5, 10)
        # Note: due to the shutters, the acquisition is slower, but in reality
        # the dwell time would be >> 1s.
        tc_stream.detDwellTime.value = 5e-3
        f = sem_tc_stream.acquire()
        data, exp = f.result()
        self.assertIsNone(exp)

        self.assertEqual(len(data), 2)  # 1 array for se, the other for tc data
        for d in data:
            md = d.metadata
            # Last two dimensions correspond to y, x repetition value
            self.assertEqual(d.shape[-1], 5)
            self.assertEqual(d.shape[-2], 10)

            if d.ndim >= 3:
                self.assertEqual(d.shape[-3], 1)  # Z
                # T should be the length of the time-correlator
                if model.MD_TIME_LIST in md:
                    self.assertGreater(d.shape[-4], 100)
                    self.assertEqual(d.shape[-4], len(md[model.MD_TIME_LIST]))
                else:
                    self.assertEqual(d.shape[-4], 1)
                self.assertEqual(d.shape[-5], 1)  # C

            self.assertAlmostEqual(md[model.MD_PIXEL_SIZE][0], tc_stream.pixelSize.value)
            self.assertAlmostEqual(md[model.MD_PIXEL_SIZE][1], tc_stream.pixelSize.value)
            self.assertAlmostEqual(md[model.MD_DWELL_TIME], self.time_correlator.dwellTime.value)

        # Sub-pixel drift correction
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        dc.period.value = 1
        sem_stream.leeches.append(dc)

        tc_stream.repetition.value = (1, 2)
        tc_stream.detDwellTime.value = 2

        for l in sem_stream.leeches:
            l.series_start()

        f = sem_tc_stream.acquire()

        time.sleep(0.1)
        # Dwell time on detector and emitter should be reduced by 1/2
        self.assertEqual(self.time_correlator.dwellTime.value, 1)
        # SEM dwell time might be either 1s, or the drift correction dwell time
        self.assertIn(self.ebeam.dwellTime.value, (1, 1e-6))
        data, exp = f.result()
        for l in sem_stream.leeches:
            l.series_complete(data)
        self.assertIsNone(exp)

        self.assertEqual(len(data), 3)  # additional anchor region data array
        self.assertEqual(data[0].shape[-1], 1)
        self.assertEqual(data[0].shape[-2], 2)
        self.assertEqual(data[1].shape[-1], 1)
        self.assertEqual(data[1].shape[-2], 2)

    def test_tc_acquisition_fuz(self):
        """
        Test the output of an acquisition with fuzzing
        """
        self.skipIfNotSupported("time-correlator")
        tc_stream = stream.ScannedTemporalSettingsStream(
            "Time Correlator",
            self.time_correlator,
            self.time_correlator.data,
            self.ebeam,
            detvas={"dwellTime"},
        )
        sem_stream = stream.SpotSEMStream("Ebeam", self.sed, self.sed.data, self.ebeam)
        sem_tc_stream = stream.SEMTemporalMDStream("SEM Time Correlator",
                                                   [sem_stream, tc_stream])

        # randomly picked value, to simulate previous value
        self.ebeam.dwellTime.value = 0.042

        sem_tc_stream.roi.value = (0, 0, 0.1, 0.2)
        tc_stream.repetition.value = (5, 10)
        tc_stream.fuzzing.value = True
        # Note: due to the shutters, the acquisition is slower, but in reality
        # the dwell time would be >> 1s.
        tc_stream.detDwellTime.value = 5e-3

        exp_pos, exp_pxs, exp_res = roi_to_phys(tc_stream)

        f = sem_tc_stream.acquire()
        data, exp = f.result()
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        self.assertEqual(len(data), len(sem_tc_stream.raw))
        sem_da = sem_tc_stream.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        tc_da = sem_tc_stream.raw[1]
        sem_res = sem_da.shape
        sshape = tc_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[-4], 100)  # Should have at least 100 time points
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = tc_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_tc_acq_live_update(self):
        """
        Test if live update works for the time correlator
        """
        self.skipIfNotSupported("time-correlator")
        # Create the stream
        tc_stream = stream.ScannedTemporalSettingsStream(
            "Time Correlator",
            self.time_correlator,
            self.time_correlator.data,
            self.ebeam,
            detvas={"dwellTime"},
        )
        sem_stream = stream.SpotSEMStream("Ebeam", self.sed, self.sed.data, self.ebeam)
        sem_tc_stream = stream.SEMTemporalMDStream("SEM Time Correlator",
                                                   [sem_stream, tc_stream])

        sem_tc_stream.roi.value = (0, 0, 0.1, 0.2)
        tc_stream.repetition.value = (5, 3)
        self.time_correlator.dwellTime.value = 1  # s
        f = sem_tc_stream.acquire()

        # Check if there is a live update in the setting stream.
        time.sleep(3)
        im1 = tc_stream.image.value
        time.sleep(6)
        im2 = tc_stream.image.value

        # wait until it's over
        data, exp = f.result()
        self.assertIsNone(exp)

        # Check if the image changed (live update is working)
        testing.assert_array_not_equal(im1, im2)


class SettingsStreamsTestCase(unittest.TestCase):
    """
    Tests of the *SettingsStreams, to be run with a (simulated) 4-detector SPARC
    Mostly the creation of streams and live view.
    """

    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC_CONFIG)

        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.mnchr = model.getComponent(role="monochromator")
        cls.spgp = model.getComponent(role="spectrograph")
        cls.cl = model.getComponent(role="cl-detector")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.filter = model.getComponent(role="filter")

        mic = model.getMicroscope()
        cls.optmngr = path.OpticalPathManager(mic)

    def setUp(self):
        self._image = None

    def _on_image(self, im):
        self._image = im

    def test_spec_ss(self):
        """ Test SpectrumSettingsStream """
        # Create the stream
        specs = stream.SpectrumSettingsStream("test",
                                              self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        specs.image.subscribe(self._on_image)

        # shouldn't affect
        specs.roi.value = (0.15, 0.6, 0.8, 0.8)
        specs.repetition.value = (5, 6)

        specs.detExposureTime.value = 0.3  # s

        # Start acquisition
        specs.should_update.value = True
        specs.is_active.value = True

        time.sleep(2)
        specs.is_active.value = False

        self.assertIsNotNone(self._image, "No spectrum received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        # .image should be a 1D spectrum
        self.assertEqual(self._image.shape, (specs.detResolution.value[0],))

        specs.image.unsubscribe(self._on_image)

        # TODO
        # change center wavelength and try again

    def test_repetitions_and_roi(self):
        logging.debug("Testing repetitions and roi")

        helper = stream.SpectrumSettingsStream("test",
                                               self.spec, self.spec.data, self.ebeam,
                                               detvas={"exposureTime", "readoutRate", "binning", "resolution"})

        # Check it follows what we ask
        helper.pixelSize.value = helper.pixelSize.range[0]
        helper.roi.value = (0.1, 0.2, 0.3, 0.4)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.1, 0.2, 0.3, 0.4), decimal=2)

        helper.repetition.value = (64, 64)
        self.assertEqual(helper.repetition.value, (64, 64))

        # As the shape ratio of the ebeam is 4:3, a "square" ROI is not a square rep
        eshape = self.ebeam.shape
        eratio = eshape[0] / eshape[1]
        helper.repetition.value = (512, 512)
        helper.roi.value = (0, 0, 1, 1)
        rep = helper.repetition.value
        rep_ratio = rep[0] / rep[1]
        self.assertAlmostEqual(eratio, rep_ratio, places=1)
        self.assertTrue(all(0 <= v <= 1 for v in helper.roi.value),
                        "Roi values are not within 0 and 1: %s" % (helper.roi.value,))

        # Again, but with some smaller ROI
        helper.roi.value = (0.4, 0.4, 0.6, 0.6)
        rep = helper.repetition.value
        rep_ratio = rep[0] / rep[1]
        self.assertAlmostEqual(eratio, rep_ratio, places=1)

        # When asking for a square area, the ROI compensates for the non-square eshape
        helper.repetition.value = (1, 1)
        roi = helper.roi.value
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        roi_ratio = roi_size[0] / roi_size[1]
        self.assertAlmostEqual(eratio, 1 / roi_ratio, places=2)

        # The following changes would cause the RoI[1] to be negative (by a very tiny value).
        # => Make sure the values are always strictly within 0 and 1.
        e_fov = comp.compute_scanner_fov(self.ebeam)
        helper.pixelSize.value = 30 * e_fov[0] / 1024
        helper.roi.value = (0, 0, 1, 1)
        helper.roi.value = (0.1, 0.1, 0.8, 0.8)
        helper.repetition.value = (2, 3)
        self.assertTrue(all(0 <= v <= 1 for v in helper.roi.value),
                        "Roi values are not within 0 and 1: %s" % (helper.roi.value,))


    def test_cancel_active(self):
        """
        Test stopping the stream before it's done preparing
        """
        specs = stream.SpectrumSettingsStream("test",
                                              self.spec, self.spec.data, self.ebeam, opm=self.optmngr,
                                              detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        specs.image.subscribe(self._on_image)

        # Make sure the optical math is not in the right place, so that it takes
        # some time to prepare the stream
        self.optmngr.setPath("ar").result()

        specs.detExposureTime.value = 0.3  # s

        # Start acquisition
        specs.should_update.value = True
        specs.is_active.value = True

        # Wait just a tiny bit and stop => the optical path and acquisition should not continue
        time.sleep(0.1)
        specs.is_active.value = False

        self.assertIsNone(self._image, "Spectrum received immediately")

        # Make sure the optical path has time to be finished for the spectrometer
        self.optmngr.setPath("spectral").result()
        time.sleep(1)

        self.assertIsNone(self._image, "Spectrum received after stopping the stream")

        specs.image.unsubscribe(self._on_image)

    def test_mnchr_ss(self):
        """ Tests MonochromatorSettingsStream """
        # Create the stream and a SpotStream (to drive the ebeam)
        mcs = stream.MonochromatorSettingsStream("test",
                                                 self.mnchr, self.mnchr.data, self.ebeam,
                                                 emtvas={"dwellTime", })
        spots = stream.SpotSEMStream("spot", self.sed, self.sed.data, self.ebeam)

        mcs.image.subscribe(self._on_image)

        # start spot mode
        spots.should_update.value = True
        spots.is_active.value = True

        # shouldn't affect
        mcs.roi.value = (0.15, 0.6, 0.8, 0.8)
        mcs.repetition.value = (5, 6)

        mcs.emtDwellTime.value = 0.01  # s
        mcs.windowPeriod.value = 10  # s

        # Start live acquisition
        mcs.should_update.value = True
        mcs.is_active.value = True

        # move spot
        time.sleep(0.2)
        spots.roi.value = (0.1, 0.3, 0.1, 0.3)
        time.sleep(0.2)
        spots.roi.value = (0.5, 0.2, 0.5, 0.2)
        time.sleep(1)
        mcs.is_active.value = False

        self.assertIsNotNone(self._image, "No data received after 1.4s")
        self.assertIsInstance(self._image, model.DataArray)
        self.assertEqual(self._image.ndim, 1)
        self.assertGreater(self._image.shape[0], 50)  # we could hope 200 samples
        dates = self._image.metadata[model.MD_TIME_LIST]
        self.assertGreater(dates[0] + mcs.windowPeriod.value, dates[-1])
        numpy.testing.assert_array_equal(dates, sorted(dates))

        # TODO: run a SpotStream and try again
        spots.is_active.value = False

        mcs.image.unsubscribe(self._on_image)

        # TODO
        # change center wavelength and try again

    def test_ar_ss(self):
        """ Test ARSettingsStream """
        # Create the stream
        ars = stream.ARSettingsStream("test",
                                      self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        ars.image.subscribe(self._on_image)

        # shouldn't affect
        ars.roi.value = (0.15, 0.6, 0.8, 0.8)
        ars.repetition.value = (5, 6)

        ars.detExposureTime.value = 0.3  # s

        # Start acquisition
        ars.should_update.value = True
        ars.is_active.value = True

        time.sleep(2)
        ars.is_active.value = False

        self.assertIsNotNone(self._image, "No AR image received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        exp_shape = ars.detResolution.value[::-1] + (3,)
        self.assertEqual(self._image.shape, exp_shape)

        # Try changing the binning in the mean time
        ars.should_update.value = True
        ars.is_active.value = True
        time.sleep(0.2)
        ars.detBinning.value = (2, 2)

        time.sleep(2)
        ars.is_active.value = False

        self.assertIsNotNone(self._image, "No AR image received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        exp_shape = ars.detResolution.value[::-1] + (3,)
        self.assertEqual(self._image.shape, exp_shape)

        ars.image.unsubscribe(self._on_image)

    def test_cl_ss(self):
        """ Test CLSettingsStream, with one hardware axis """

        # create axes
        axes = {"filter": ("band", self.filter)}  # Will create a .axisFilter VA

        # Create the stream
        cls = stream.CLSettingsStream("test",
                                      self.cl, self.cl.data, self.ebeam,
                                      axis_map=axes,
                                      emtvas={"dwellTime", })  # note: not "scale", "resolution"
        cls.image.subscribe(self._on_image)

        # shouldn't affect
        cls.roi.value = (0.15, 0.6, 0.8, 0.8)
        # cls.repetition.value = (5, 6) # changes the pixelSize

        cls.emtDwellTime.value = 10e-6  # s
        cls.pixelSize.value *= 10

        # Get 2 allowed positions on the filter, with b1 being *NOT* the current value
        b0, b1 = list(cls.axisFilter.choices)[:2]
        if b1 == self.filter.position.value["band"]:
            # if b1 happens to be the current value, swap with b0
            b0, b1 = b1, b0

        logging.debug("Testing with positions %s and %s", b0, b1)
        # test that the actuator does not move when the local axis VA updates and the stream is inactive
        cls.axisFilter.value = b1
        time.sleep(3.0)
        self.assertNotEqual(cls.axisFilter.value, self.filter.position.value["band"])

        # Start acquisition => axis should go to the current value
        cls.should_update.value = True
        cls.is_active.value = True
        time.sleep(3.0)  # Axis can be long to move
        logging.debug("Filter is now at %s", self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, b1)

        # test that the actuator does move when the local axis VA updates and the stream is active
        cls.axisFilter.value = b0
        time.sleep(3.0)
        logging.debug("Filter is now at %s", self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, b0)

        # resolution is only updated after starting acquisition
        res = self.ebeam.resolution.value
        exp_time = cls.emtDwellTime.value * numpy.prod(res)
        st = exp_time * 1.5 + 0.1
        logging.info("Will wait for the acquisition for %f s", st)
        time.sleep(st)
        cls.is_active.value = False

        self.assertIsNotNone(self._image, "No CL image received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        exp_shape = res[::-1] + (3,)
        self.assertEqual(self._image.shape, exp_shape)

        # Check the scale of the ebeam is correctly updated when changing pixelSize
        cls.is_active.value = True
        old_scale = self.ebeam.scale.value[0]
        ratio = 2.08  # almost random value
        cls.pixelSize.value *= ratio
        time.sleep(0.1)
        new_scale = self.ebeam.scale.value[0]
        cls.is_active.value = False
        self.assertAlmostEqual(new_scale / old_scale, ratio)

        cls.image.unsubscribe(self._on_image)


if __name__ == "__main__":
    unittest.main()
