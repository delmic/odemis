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
from odemis.acq.move import FM_IMAGING, MicroscopePostureManager
from odemis.util import testing
from odemis.util.linalg import generate_triangulation_points

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"


class RoiAutofocusTestCase(unittest.TestCase):
    """
    Unit test for the roi_autofocus module
    """

    @classmethod
    def setUpClass(cls):
        testing.start_backend(METEOR_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.meteor_manager = MicroscopePostureManager(microscope=cls.microscope)

        cls.ccd = model.getComponent(role="ccd")
        cls.focus = model.getComponent(role="focus")
        cls.stage = model.getComponent(role="stage")

        # Switch to FM imaging as the focus position will be its "good" position
        # and simulator will autofocus in (almost) focussed images
        cls.meteor_manager.cryoSwitchSamplePosition(FM_IMAGING).result()

        # Assumes the stage is referenced
        cls.init_pos = (cls.stage.position.value["x"], cls.stage.position.value["y"])
        cls.width, cls.height = cls.ccd.shape[:2]
        cls.px_size = cls.ccd.getMetadata().get(model.MD_PIXEL_SIZE)

        # Assume initial focus position is good
        # The initial position comes from manual focussing the centre of overview image
        current_focus = cls.focus.position.value["z"]  # assume manual focus before running the script
        # Allow a range of +/- 30 µm around the focused position
        cls.focus_range = (current_focus - 30.0e-6, current_focus + 30.0e-6)

    def test_autofocus_in_roi(self):
        """
        Tests the autofocus in roi is running and returns the focus points:
        """
        init_pos = self.init_pos
        px_size = self.px_size
        width = self.width
        height = self.height
        n_focus_points = (3, 3)
        overlap = 0.2
        confidence_level = 0.8  # focus points below this confidence level will be discarded

        xmin = init_pos[0] - (1 - overlap) * n_focus_points[0] / 2 * px_size[0] * width
        ymin = init_pos[1] - (1 - overlap) * n_focus_points[1] / 2 * px_size[1] * height
        xmax = init_pos[0] + (1 - overlap) * n_focus_points[0] / 2 * px_size[0] * width
        ymax = init_pos[1] + (1 - overlap) * n_focus_points[1] / 2 * px_size[1] * height

        bbox = (xmin, ymin, xmax, ymax)
        max_distance = 100e-06  # in m
        focus_points = generate_triangulation_points(max_distance, bbox)

        f = autofocus_in_roi(bbox, self.stage, self.ccd, self.focus, self.focus_range, focus_points,
                             confidence_level)

        # Test if the autofocus in roi is running
        time.sleep(0.1)
        self.assertTrue(f.running())

        focus_points = f.result()
        # There should be a minimum of 3 returned focus points to fit a plane for re-focusing
        self.assertGreaterEqual(len(focus_points), 3)
        # One focus point has three coordinates x, y and z
        self.assertEqual(len(focus_points[0]), 3)

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
        n_focus_points = (3, 3)
        overlap = 0.2
        confidence_level = 0.8  # focus points below this confidence level will be discarded

        xmin = init_pos[0] - (1 - overlap) * n_focus_points[0] / 2 * px_size[0] * width
        ymin = init_pos[1] - (1 - overlap) * n_focus_points[1] / 2 * px_size[1] * height
        xmax = init_pos[0] + (1 - overlap) * n_focus_points[0] / 2 * px_size[0] * width
        ymax = init_pos[1] + (1 - overlap) * n_focus_points[1] / 2 * px_size[1] * height

        bbox = (xmin, ymin, xmax, ymax)
        max_distance = 100e-06  # in m
        focus_points = generate_triangulation_points(max_distance, bbox)

        f = autofocus_in_roi(bbox, self.stage, self.ccd, self.focus, self.focus_range, focus_points,
                             confidence_level)

        # Test cancelling of autofocus in roi
        time.sleep(0.1)
        self.assertTrue(f.running())

        # cancel the autofocussing after 0.1 second
        time.sleep(0.1)
        f.cancel()

        # check if the autofocussing is cancelled
        time.sleep(0.1)
        self.assertTrue(f.cancelled())
        with self.assertRaises(futures.CancelledError):
            f.result()

    def test_estimate_autofocus_in_roi(self):
        """
        Tests time estimation of autofocus in roi
        """
        n_focus_points = 9
        min_time = n_focus_points * estimateAutoFocusTime(self.ccd, None, self.focus, rng_focus=self.focus_range)
        estimated_time = estimate_autofocus_in_roi_time(n_focus_points, self.ccd, self.focus, self.focus_range)
        self.assertGreaterEqual(estimated_time, min_time)
