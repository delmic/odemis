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
import logging
from odemis.gui.win.acquisition import OverviewAcquisitionDialog

logging.getLogger().setLevel(logging.DEBUG)


class TestClipTilingArea(unittest.TestCase):
    """
    This test case is to test the method clip_tiling_area_to_range()
    """
    def test_clipping_is_performed_same_width_and_height(self):
        # move the stage close to the bottom left corner of the active range (200 micrometers 
        # away from the corner in x and y directions).
        w = 600e-6
        h = 600e-6

        ovv_rng = {'x': [-1e-3, 1e-3], 'y': [-1.1e-3, 1.1e-3]}
        pos = {"x": ovv_rng["x"][0] + 200e-6,
               "y": ovv_rng["y"][0] + 200e-6}
        rect_pts = OverviewAcquisitionDialog.clip_tiling_area_to_range(w, h, pos, ovv_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection happened
        self.assertAlmostEqual(rect_pts[0], ovv_rng["x"][0])
        self.assertAlmostEqual(rect_pts[1], ovv_rng["y"][0])
        self.assertAlmostEqual(rect_pts[2], ovv_rng["x"][0] + 200e-6 + w / 2)
        self.assertAlmostEqual(rect_pts[3], ovv_rng["y"][0] + 200e-6 + h / 2)

    def test_clipping_is_performed_different_width_and_height(self):
        # move the stage close to the bottom left corner of the active range (200 micrometers 
        # away from the corner in x and y directions).
        w = 700e-6
        h = 600e-6

        ovv_rng = {'x': [-1e-3, 1e-3], 'y': [-1.1e-3, 1.1e-3]}
        pos = {"x": ovv_rng["x"][0] + 200e-6,
               "y": ovv_rng["y"][0] + 200e-6}
        rect_pts = OverviewAcquisitionDialog.clip_tiling_area_to_range(w, h, pos, ovv_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection happened
        self.assertAlmostEqual(rect_pts[0], ovv_rng["x"][0])
        self.assertAlmostEqual(rect_pts[1], ovv_rng["y"][0])
        self.assertAlmostEqual(rect_pts[2], ovv_rng["x"][0] + 200e-6 + w / 2)
        self.assertAlmostEqual(rect_pts[3], ovv_rng["y"][0] + 200e-6 + h / 2)

    def test_clipping_is_not_performed_same_width_and_height(self):
        # move the stage to some position away from the edges of the active
        # range so that clipping is not needed (i.e in the middle of the active range).
        w = 600e-6
        h = 600e-6

        ovv_rng = {'x': [-1e-3, 1e-3], 'y': [-1.1e-3, 1.1e-3]}
        pos = {"x": sum(ovv_rng["x"]) / 2,
               "y": sum(ovv_rng["y"]) / 2}
        rect_pts = OverviewAcquisitionDialog.clip_tiling_area_to_range(w, h, pos, ovv_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection area is the same as the tl and br of the requested area.
        # tl and br are found by the current position +/- half of the width and height
        self.assertAlmostEqual(rect_pts[0], pos["x"] - w / 2)
        self.assertAlmostEqual(rect_pts[1], pos["y"] - h / 2)
        self.assertAlmostEqual(rect_pts[2], pos["x"] + w / 2)
        self.assertAlmostEqual(rect_pts[3], pos["y"] + h / 2)

    def test_clipping_is_not_performed_different_width_and_height(self):
        # move the stage to some position away from the edges of the active
        # range so that clipping is not needed (i.e in the middle of the active range). 
        w = 500e-6
        h = 600e-6

        ovv_rng = {'x': [-1e-3, 1e-3], 'y': [-1.1e-3, 1.1e-3]}
        pos = {"x": sum(ovv_rng["x"]) / 2,
               "y": sum(ovv_rng["y"]) / 2}
        rect_pts = OverviewAcquisitionDialog.clip_tiling_area_to_range(w, h, pos, ovv_rng)
        # Note: the return rect_pts is LBRT assuming y axis upwards, or LTRB assuming y axis downwards.
        # check if the intersection area is the same as the tl and br of the requested area.
        # tl and br are found by the current position +/- half of the width and height
        self.assertAlmostEqual(rect_pts[0], pos["x"] - w / 2)
        self.assertAlmostEqual(rect_pts[1], pos["y"] - h / 2)
        self.assertAlmostEqual(rect_pts[2], pos["x"] + w / 2)
        self.assertAlmostEqual(rect_pts[3], pos["y"] + h / 2)


if __name__ == "__main__":
    unittest.main()
