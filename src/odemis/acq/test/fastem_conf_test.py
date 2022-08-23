# -*- coding: utf-8 -*-
"""
Created on 03 December 2021

@author: Sabrina Rossberger, Éric Piel

Copyright © 2021-2022 Sabrina Rossberger, Delmic

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
import math
import os
import unittest

import odemis
from odemis import model
from odemis.acq import fastem_conf
from odemis.acq.fastem_conf import OVERVIEW_MODE, LIVESTREAM_MODE, MEGAFIELD_MODE
from odemis.util import img, testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# * TEST_NOHW = 1: use simulator (asm/sam and xt adapter simulators need to be running)
# * TEST_NOHW = 0: connected to the real hardware (backend needs to be running)
# technolution_asm_simulator/simulator2/run_the_simulator.py
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default is HW testing

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
FASTEM_CONFIG = CONFIG_PATH + "sim/fastem-sim-asm.odm.yaml"


class TestFASTEMConfig(unittest.TestCase):
    """Test the FASTEM scanner configuration."""

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            testing.start_backend(FASTEM_CONFIG)

        cls.scanner = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.multibeam = model.getComponent(role="multibeam")
        cls.mppc = model.getComponent(role="mppc")

        # change the rotation for single and multibeam mode to check the correct one is selected depending on the mode
        cls.scanner.updateMetadata({model.MD_SINGLE_BEAM_ROTATION: math.radians(5),
                                    model.MD_MULTI_BEAM_ROTATION: math.radians(10),
                                    model.MD_FIELD_FREE_POS_SHIFT: [90.0e-6, 50.0e-6]})

    def test_configure_scanner_overview(self):
        """Check that for the overview mode, the correct HW settings are set on the respective scanner VAs."""

        fastem_conf.configure_scanner(self.scanner, OVERVIEW_MODE)

        self.assertFalse(self.scanner.multiBeamMode.value)
        self.assertFalse(self.scanner.external.value)
        self.assertFalse(self.scanner.blanker.value)
        self.assertFalse(self.scanner.immersion.value)
        self.assertGreater(self.scanner.horizontalFoV.value, 1.e-3)  # should be big FoV for overview
        self.assertEqual(self.scanner.rotation.value, math.radians(5))

        scanner_md = self.scanner.getMetadata()

        # check that the MD_POS_COR is correctly set for overview imaging.
        self.assertListEqual(scanner_md[model.MD_FIELD_FREE_POS_SHIFT], scanner_md[model.MD_POS_COR])

        # check rotation set is also stored in MD as rotation correction
        self.assertEqual(scanner_md[model.MD_ROTATION_COR], math.radians(5))

        # acquire an image and check the MD is correct: ROTATION - ROTATION_COR == 0
        image = self.sed.data.get()
        self.assertAlmostEqual(image.metadata[model.MD_ROTATION] - image.metadata[model.MD_ROTATION_COR], 0)
        # merge the MD -> automatically calculates rotation - rotation_cor -> puts result on rotation in MD
        img.mergeMetadata(image.metadata)
        self.assertAlmostEqual(image.metadata[model.MD_ROTATION], 0)

    def test_configure_scanner_live(self):
        """Check that for the live mode, the correct HW settings are set on the respective scanner VAs."""

        fastem_conf.configure_scanner(self.scanner, LIVESTREAM_MODE)

        self.assertFalse(self.scanner.multiBeamMode.value)
        self.assertFalse(self.scanner.external.value)
        self.assertFalse(self.scanner.blanker.value)
        self.assertTrue(self.scanner.immersion.value)
        self.assertEqual(self.scanner.rotation.value, math.radians(5))

        scanner_md = self.scanner.getMetadata()
        # check that the MD_POS_COR is set to [0, 0] for live stream imaging.
        self.assertListEqual([0, 0], scanner_md[model.MD_POS_COR])

    def test_configure_scanner_megafield(self):
        """Check that for megafield mode, the correct HW settings are set on the respective scanner VAs."""

        fastem_conf.configure_scanner(self.scanner, MEGAFIELD_MODE)

        self.assertTrue(self.scanner.multiBeamMode.value)
        self.assertTrue(self.scanner.external.value)
        self.assertFalse(self.scanner.blanker.value)
        self.assertTrue(self.scanner.immersion.value)
        self.assertLess(self.scanner.horizontalFoV.value, 0.1e-3)  # should be smaller for megafield than for overview
        self.assertEqual(self.scanner.rotation.value, math.radians(10))

        # Changes in the rotation on the scanner (e-beam) should be reflected in the MD on the multibeam.
        # check that rotation correction is 0 or not existing for megafield imaging using the ASM/SAM
        self.assertEqual(self.multibeam.getMetadata().get(model.MD_ROTATION_COR, 0), 0)
        # check that rotation is the same as was specified for the scanner (e-beam) for megafield imaging
        self.assertEqual(self.multibeam.getMetadata()[model.MD_ROTATION], math.radians(10))

        # check that the MD_POS_COR is set to [0, 0] for live stream imaging.
        self.assertListEqual([0, 0], self.scanner.getMetadata()[model.MD_POS_COR])


if __name__ == "__main__":
    unittest.main()
