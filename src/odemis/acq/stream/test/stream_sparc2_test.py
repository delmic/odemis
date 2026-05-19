#-*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2013-2026 Éric Piel, Delmic

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

import logging
import os
import time
import unittest

import numpy

import odemis
from odemis import model
from odemis.acq import stream, leech
from odemis.acq.leech import ProbeCurrentAcquirer
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase, roi_to_phys

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2_CONFIG = CONFIG_PATH + "sim/sparc2-sim-scanner.odm.yaml"


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


if __name__ == "__main__":
    unittest.main()
