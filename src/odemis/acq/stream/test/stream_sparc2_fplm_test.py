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

import odemis
from odemis import model
from odemis.acq import stream
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2_FPLM_CONFIG = CONFIG_PATH + "sim/sparc2-fplm-sim.odm.yaml"


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




if __name__ == "__main__":
    unittest.main()
