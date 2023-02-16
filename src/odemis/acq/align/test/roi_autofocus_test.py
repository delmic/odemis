# -*- coding: utf-8 -*-
"""
Copyright © 2020 Delmic
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
from concurrent import futures

import odemis
from odemis import model
from odemis.acq.align.autofocus import estimateAutoFocusTime
from odemis.acq.align.roi_autofocus import autofocus_in_roi, estimate_autofocus_in_roi_time
from odemis.util import testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

TEST_NOHW = os.environ.get("TEST_NOHW", "1")  # Default to simulation

if TEST_NOHW == "0":
    TEST_NOHW = False
elif TEST_NOHW == "1":
    TEST_NOHW = True
else:
    raise ValueError("Unknown value of environment variable TEST_NOHW=%s" % TEST_NOHW)

# CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
# METEOR_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"
METEOR_CONFIG = "/home/dev/development/odemis/install/linux/usr/share/odemis/sim/meteor-sim.odm.yaml"


class RoiAutofocusTestCase(unittest.TestCase):
    """
    Unit test for the roi_autofocus module
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            testing.start_backend(METEOR_CONFIG)

        # create some streams connected to the backend
        # cls.microscope = model.getMicroscope()
        cls.ccd = model.getComponent(role="ccd")
        cls.focus = model.getComponent(role="focus")
        cls.stage = model.getComponent(role="stage")

        # Assumes the stage is referenced
        cls.init_pos = (cls.stage.position.value["x"], cls.stage.position.value["y"])
        cls.width, cls.height = cls.ccd.shape[:2]
        cls.px_size = cls.ccd.getMetadata().get(model.MD_PIXEL_SIZE)

        # Assume initial focus position is good
        # The initial position comes from manual focussing the centre of overview image
        current_focus = cls.focus.position.value["z"]  # assume manual focus before running the script
        # Allow a range of +/- 30 µm around the focused position
        cls.focus_range = (current_focus - 30.0e-6, current_focus + 30.0e-6)

    @classmethod
    def tearDownClass(cls):
        logging.debug("moving back to initial position")
        cls.stage.moveAbs({"x": cls.init_pos[0], "y": cls.init_pos[1]}).result()

    def test_autofocus_in_roi(self):
        """
        Tests the autofus in roi is running and returns the focus points:
        """
        init_pos = self.init_pos
        px_size = self.px_size
        width = self.width
        height = self.height
        n_tiles = (3, 3)
        overlap = 0.2

        xmin = init_pos[0] - (1 - overlap) * n_tiles[0] / 2 * px_size[0] * width
        ymin = init_pos[1] - (1 - overlap) * n_tiles[1] / 2 * px_size[1] * height
        xmax = init_pos[0] + (1 - overlap) * n_tiles[0] / 2 * px_size[0] * width
        ymax = init_pos[1] + (1 - overlap) * n_tiles[1] / 2 * px_size[1] * height

        bbox = (xmin, ymin, xmax, ymax)

        f = autofocus_in_roi(bbox, self.stage, self.ccd, self.focus, self.focus_range, n_tiles[0], n_tiles[1], 0)

        # Test if the autofocus in roi is running
        time.sleep(2)
        self.assertTrue(f.running())

        # The returned focus points should be minimum three
        focus_points = f.result()
        self.assertGreaterEqual(len(focus_points), 3)

        # Test if the autofocus task is done
        self.assertTrue(f.done())

    def test_cancel_autofocus_in_roi(self):
        """
        Tests cancelling of autofocus
        """
        init_pos = self.init_pos
        px_size = self.px_size
        width = self.width
        height = self.height
        n_tiles = (3, 3)
        overlap = 0.2

        xmin = init_pos[0] - (1 - overlap) * n_tiles[0] / 2 * px_size[0] * width
        ymin = init_pos[1] - (1 - overlap) * n_tiles[1] / 2 * px_size[1] * height
        xmax = init_pos[0] + (1 - overlap) * n_tiles[0] / 2 * px_size[0] * width
        ymax = init_pos[1] + (1 - overlap) * n_tiles[1] / 2 * px_size[1] * height

        bbox = (xmin, ymin, xmax, ymax)

        f = autofocus_in_roi(bbox, self.stage, self.ccd, self.focus, self.focus_range, n_tiles[0], n_tiles[1], 0) # TODO add proper confidence value

        # Test cancelling of autofocus in roi
        time.sleep(2)
        self.assertTrue(f.running())

        # cancel the autofocussing after 5 seconds
        time.sleep(5)
        f.cancel()

        # check if the autofocussing is cancelled
        time.sleep(2)
        self.assertTrue(f.cancelled())
        with self.assertRaises(futures.CancelledError):
            f.result()

    def test_estimate_autofocus_in_roi(self):
        """
        Tests time estimation of autofocus in roi
        """
        n_tiles = (3, 3)
        min_time = n_tiles[0] * n_tiles[1] * estimateAutoFocusTime(self.ccd, None)
        estimated_time = estimate_autofocus_in_roi_time(n_tiles[0], n_tiles[1], self.ccd)
        self.assertGreaterEqual(estimated_time, min_time)