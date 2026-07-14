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
import time
import unittest
from abc import ABC

import numpy

import odemis
from odemis import model
from odemis.acq import stream, leech
from odemis.util import testing
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
        self.elapsed = None
        self.remaining = None
        self._images = []

    # Called when the acquisition future is completed or updates the progress
    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, elapsed_time, remaining_time):
        self.elapsed = elapsed_time
        self.remaining = remaining_time
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
        self.assertAlmostEqual(self.remaining, 0, delta=0.1)
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
        self.assertAlmostEqual(self.remaining, 0, delta=0.1)
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
        self.assertAlmostEqual(self.remaining, 0, delta=0.1)
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
        self.assertAlmostEqual(self.remaining, 0, delta=0.1)
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
            self.assertLess(pos[0] + sstage_to_abs_shift[0], roi_rng[1][0])
            self.assertGreater(pos[1] + sstage_to_abs_shift[1], roi_rng[0][1])
            self.assertLess(pos[1] + sstage_to_abs_shift[1], roi_rng[1][1])

    def on_progress_update_stage(self, _, elapsed_time, remaining_time):
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
