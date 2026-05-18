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
from odemis.acq.stream import POL_POSITIONS
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase, roi_to_phys
from odemis.util import testing

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2POL_CONFIG = CONFIG_PATH + "sim/sparc2-polarizer-sim.odm.yaml"


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


if __name__ == "__main__":
    unittest.main()
