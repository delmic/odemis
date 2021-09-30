#-*- coding: utf-8 -*-

"""
<<<<<<< HEAD
Created on 30 Sep 2021
=======
Created on 8 October 2018
>>>>>>> 53f675a41... add test file (not finished yet)

@author: Mahmood Barazi

Copyright © 2021 Mahmood Barazi, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import unittest
import os
import odemis
import logging
from odemis.util import test, driver
from odemis import model
from odemis.util.driver import BACKEND_RUNNING, BACKEND_STARTING
<<<<<<< HEAD
from odemis.acq.move import transformFromSEMToMeteor
=======
from odemis.acq.move import LOADING, cryoSwitchSamplePosition, transformFromSEMToMeteor
>>>>>>> 53f675a41... add test file (not finished yet)
from odemis.gui.win.acquisition import OverviewAcquisitionDialog

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"

class TestgetROARectMethod(unittest.TestCase):
    """
    This test case is to test the method get_ROA_rect()
    """
    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(METEOR_CONFIG)
        except LookupError:
            logging.info("There is already running backend. It will be turned off, and the backend of METEOR will be turned on.")
            test.stop_backend()
            test.start_backend(METEOR_CONFIG)
        except Exception:
            raise

        # get the stage components
        cls.stage = model.getComponent(role="stage")
        cls.stage_bare = model.getComponent(role="stage-bare")

        # get the metadata
        stage_md = cls.stage.getMetadata()
        stage_bare_md = cls.stage_bare.getMetadata()
        cls.stage_grid1_sem = stage_bare_md[model.MD_SAMPLE_CENTERS]["GRID 1"]
        cls.tiling_rng = stage_md[model.MD_POS_ACTIVE_RANGE]

    @classmethod
    def tearDownClass(cls):
        if driver.get_backend_status in [BACKEND_STARTING, BACKEND_RUNNING]:
            test.stop_backend()

    def test_clipping_is_performed(self):
        # move the stage close to the bottom left corner of the active range (200 micrometers 
        # # away from the corner in x and y directions).
        self.stage.moveAbs({"x": self.tiling_rng["x"][0]+200e-6,
                            "y": self.tiling_rng["y"][0]+200e-6}).result()
        w = 600e-6
        h = 600e-6
        rect_pts = OverviewAcquisitionDialog.get_ROA_rect(w, h, self.stage.position.value, self.tiling_rng)
        # check if the intersection is
        self.assertAlmostEqual(rect_pts[0], self.tiling_rng["x"][0])
        self.assertAlmostEqual(rect_pts[1], self.tiling_rng["y"][0])
        self.assertAlmostEqual(rect_pts[2], self.tiling_rng["x"][0] + 200e-6 + w/2)
        self.assertAlmostEqual(rect_pts[3], self.tiling_rng["y"][0] + 200e-6 + h/2)

    def test_clipping_is_not_performed(self):
        # move the stage to some position away from the edges of the active
        # range so that clipping is not needed. 
        stage_grid1_fm = transformFromSEMToMeteor(self.stage_grid1_sem, self.stage_bare)
        self.stage_bare.moveAbs(stage_grid1_fm).result()
        pos = self.stage.position.value
        w = 600e-6
        h = 600e-6
        rect_pts = OverviewAcquisitionDialog.get_ROA_rect(w, h, pos, self.tiling_rng)
        # check if the intersection area is the same as the tl and br
        # tl and br are found by the current position +/- half of the width and height
        self.assertAlmostEqual(rect_pts[0], pos["x"] - w/2)
        self.assertAlmostEqual(rect_pts[1], pos["y"] - h/2)
        self.assertAlmostEqual(rect_pts[2], pos["x"] + w/2)
        self.assertAlmostEqual(rect_pts[3], pos["y"] + h/2)


if __name__ == "__main__":
    unittest.main()