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
from odemis.acq import stream, path, leech
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase, roi_to_phys
from odemis.util import testing

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
TIME_CORRELATOR_CONFIG = CONFIG_PATH + "sim/sparc2-time-correlator-sim.odm.yaml"


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


if __name__ == "__main__":
    unittest.main()
