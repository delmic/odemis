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
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase, roi_to_phys

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2_INDE_EBIC_CONFIG = CONFIG_PATH + "sim/sparc2-independent-ebic-sim.odm.yaml"


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


if __name__ == "__main__":
    unittest.main()
