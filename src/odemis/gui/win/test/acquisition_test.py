#-*- coding: utf-8 -*-

"""
Created on 30 Sep 2021

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
from odemis.util.driver import BACKEND_RUNNING
from odemis.gui.win.acquisition import OverviewAcquisitionDialog

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"

class TestgetROARectMethod(unittest.TestCase):
    """
    This test case is to test the method get_ROA_rect()
    """
    @classmethod
    def setUpClass(cls):
        if driver.get_backend_status() in driver.BACKEND_RUNNING:
            microscope = model.getMicroscope()
            # TODO once chamber PR of METEOR is merged, delete "cryo-secom" below.
            if microscope.role in ("cryo-secom", "enzel"):
                logging.info("There is ENZEL backend already running. It will be used.")
            else:
                logging.info("A running backend was found. It will be turned off, and the backend of ENZEL will be turned on.")
                try:
                    test.stop_backend()
                    test.start_backend(ENZEL_CONFIG)
                except Exception:
                    raise
        else:
            logging.info("ENZEL backend will be turned on.")
            try:
                test.start_backend(ENZEL_CONFIG)
            except Exception:
                raise

        # get the stage components
        cls.stage = model.getComponent(role="stage")
        cls.stage.reference().result()
        # cls.stage_bare = model.getComponent(role="stage-bare")

        # get the metadata
        stage_md = cls.stage.getMetadata()
        cls.tiling_rng = stage_md[model.MD_POS_ACTIVE_RANGE]

    @classmethod
    def tearDownClass(cls):
        if driver.get_backend_status() == BACKEND_RUNNING:
            test.stop_backend()

    def test_clipping_is_performed_same_width_and_height(self):
        # move the stage close to the bottom left corner of the active range (200 micrometers 
        # # away from the corner in x and y directions).
        self.stage.moveAbs({"x": self.tiling_rng["x"][0]+200e-6,
                            "y": self.tiling_rng["y"][0]+200e-6}).result()
        w = 600e-6
        h = 600e-6
        rect_pts = OverviewAcquisitionDialog.get_ROA_rect(w, h, self.stage.position.value, self.tiling_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection happened
        self.assertAlmostEqual(rect_pts[0], self.tiling_rng["x"][0])
        self.assertAlmostEqual(rect_pts[1], self.tiling_rng["y"][0])
        self.assertAlmostEqual(rect_pts[2], self.tiling_rng["x"][0] + 200e-6 + w/2)
        self.assertAlmostEqual(rect_pts[3], self.tiling_rng["y"][0] + 200e-6 + h/2)

    def test_clipping_is_performed_different_width_and_height(self):
        # move the stage close to the bottom left corner of the active range (200 micrometers 
        # # away from the corner in x and y directions).
        self.stage.moveAbs({"x": self.tiling_rng["x"][0]+200e-6,
                            "y": self.tiling_rng["y"][0]+200e-6}).result()
        w = 700e-6
        h = 600e-6
        rect_pts = OverviewAcquisitionDialog.get_ROA_rect(w, h, self.stage.position.value, self.tiling_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection happened
        self.assertAlmostEqual(rect_pts[0], self.tiling_rng["x"][0])
        self.assertAlmostEqual(rect_pts[1], self.tiling_rng["y"][0])
        self.assertAlmostEqual(rect_pts[2], self.tiling_rng["x"][0] + 200e-6 + w/2)
        self.assertAlmostEqual(rect_pts[3], self.tiling_rng["y"][0] + 200e-6 + h/2)

    def test_clipping_is_not_performed_same_width_and_height(self):
        # move the stage to some position away from the edges of the active
        # range so that clipping is not needed (i.e in the middle of the active range).
        self.stage.moveAbs({"x": (self.tiling_rng["x"][0] + self.tiling_rng["x"][1])/2,
                            "y": (self.tiling_rng["y"][0] + self.tiling_rng["y"][1])/2}).result()
        pos = self.stage.position.value
        w = 600e-6
        h = 600e-6
        rect_pts = OverviewAcquisitionDialog.get_ROA_rect(w, h, pos, self.tiling_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection area is the same as the tl and br of the requested area.
        # tl and br are found by the current position +/- half of the width and height
        self.assertAlmostEqual(rect_pts[0], pos["x"] - w/2)
        self.assertAlmostEqual(rect_pts[1], pos["y"] - h/2)
        self.assertAlmostEqual(rect_pts[2], pos["x"] + w/2)
        self.assertAlmostEqual(rect_pts[3], pos["y"] + h/2)

    def test_clipping_is_not_performed_different_width_and_height(self):
        # move the stage to some position away from the edges of the active
        # range so that clipping is not needed (i.e in the middle of the active range). 
        self.stage.moveAbs({"x": (self.tiling_rng["x"][0] + self.tiling_rng["x"][1])/2,
                            "y": (self.tiling_rng["y"][0] + self.tiling_rng["y"][1])/2}).result()
        pos = self.stage.position.value
        w = 500e-6
        h = 600e-6
        rect_pts = OverviewAcquisitionDialog.get_ROA_rect(w, h, pos, self.tiling_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection area is the same as the tl and br of the requested area.
        # tl and br are found by the current position +/- half of the width and height
        self.assertAlmostEqual(rect_pts[0], pos["x"] - w/2)
        self.assertAlmostEqual(rect_pts[1], pos["y"] - h/2)
        self.assertAlmostEqual(rect_pts[2], pos["x"] + w/2)
        self.assertAlmostEqual(rect_pts[3], pos["y"] + h/2)


if __name__ == "__main__":
    unittest.main()