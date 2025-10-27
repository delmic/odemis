# -*- coding: utf-8 -*-
"""
Created on 16 October 2025

@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

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
import random
import time
import unittest
from concurrent.futures import CancelledError

import odemis
from odemis import model
from odemis.acq.align.mirror import mirror_alignment
from odemis.util import testing

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2_MIRROR_CONFIG = CONFIG_PATH + "sim/sparc2-mirror-alignment-sim.odm.yaml"


class TestMirrorAlignment(unittest.TestCase):
    """
    Test mirror alignment functions
    """

    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC2_MIRROR_CONFIG)

        # find components by their role
        cls.mirror = model.getComponent(role="mirror")
        cls.mirror_xy = model.getComponent(role="mirror-xy")
        cls.stage = model.getComponent(role="stage")
        cls.ccd = model.getComponent(role="ccd")
        cls.mirror_sim = model.getComponent(name="Mirror Position Simulator")
        cls.aligned_pos = cls.mirror_sim.getMetadata()[model.MD_FAV_POS_ACTIVE]

    @classmethod
    def tearDownClass(cls):
        testing.stop_backend()

    def setUp(self):
        self.stage.moveAbs({"z": self.aligned_pos["z"]}).result()
        self.mirror_xy.moveAbs({"x": self.aligned_pos["x"], "y": self.aligned_pos["y"]}).result()

    def test_alignment_success_rate(self):
        """Require at least 80% of random misalignments to realign successfully."""
        n_tests = 10
        rng = 30e-6  # ±30 µm range
        success_threshold = 20000  # Minimum acceptable intensity
        min_pass_rate = 0.8        # Require 80% success

        passed = 0

        random.seed(25)

        for i in range(n_tests):
            dl = random.uniform(-rng, rng)
            ds = random.uniform(-rng, rng)
            dz = random.uniform(-rng, rng)

            logging.info(
                f"[{i+1:02d}/{n_tests}] Testing misalignment: "
                f"dl={dl:.1e}, ds={ds:.1e}, dz={dz:.1e}"
            )

            # Apply misalignment
            self.mirror.moveRel({"l": dl, "s": ds}).result()
            self.stage.moveRel({"z": dz}).result()
            time.sleep(1)

            # Run alignment
            f = mirror_alignment(self.mirror, self.stage, self.ccd, max_iter=200, stop_early=False)
            try:
                f.result()
            except CancelledError:
                logging.warning(
                    f"Alignment cancelled for dl={dl}, ds={ds}, dz={dz}"
                )
                continue

            # Evaluate final intensity
            img = self.ccd.data.get(asap=False)
            intensity = int(img.max())
            logging.info(f"Final intensity = {intensity:.1f}")

            if intensity >= success_threshold:
                passed += 1
            else:
                logging.warning(
                    f"Low intensity ({intensity:.1f}) for dl={dl:.1e}, ds={ds:.1e}, dz={dz:.1e}"
                )

            # Reset for next test
            self.setUp()
            time.sleep(1)

        # Compute success rate
        pass_rate = passed / n_tests
        logging.info(f"Alignment success rate: {pass_rate*100:.1f}% ({passed}/{n_tests})")

        # Final assertion
        self.assertGreaterEqual(
            pass_rate,
            min_pass_rate,
            f"Alignment success rate below {min_pass_rate*100:.0f}% ({pass_rate*100:.1f}%)",
        )


if __name__ == '__main__':
    unittest.main()
