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

import odemis
from odemis import model
from odemis.acq.move import LOADING, IMAGING, getLoadingProgress, RTOL_PROGRESS
from odemis.acq.move import cryoTiltSample, cryoLoadSample
from odemis import util
from odemis.util import test

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
CRYO_SECOM_CONFIG = CONFIG_PATH + "sim/cryosecom-sim.yaml"

ATOL_STAGE = 1e-7


class TestCryoMove(unittest.TestCase):
    """
    Test cryoLoadSample and cryoTiltSample functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(CRYO_SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.stage = model.getComponent(role="stage")
        cls.focus = model.getComponent(role="focus")

        cls.stage_active = cls.stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        cls.stage_deactive = cls.stage.getMetadata()[model.MD_FAV_POS_DEACTIVE]
        cls.focus_deactive = cls.focus.getMetadata()[model.MD_FAV_POS_DEACTIVE]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_loading_and_imaging(self):
        """
        Test moving the sample stage to imaging position while it's in loading position and vice versa
        """
        stage = self.stage
        focus = self.focus
        # Get the stage to loading position
        f = cryoLoadSample(LOADING)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_deactive,
                                     atol=ATOL_STAGE)
        # Get the stage to imaging position
        f = cryoLoadSample(IMAGING)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_active, atol=ATOL_STAGE)
        # Switch back to loading position
        f = cryoLoadSample(LOADING)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, self.stage_deactive, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, self.focus_deactive, atol=ATOL_STAGE)

    def test_imaging_from_tilting(self):
        """
        Test moving the sample stage to imaging position while it's in tilting position
        """
        stage = self.stage
        focus = self.focus
        # Get the stage to tilting position and park focus
        f = cryoLoadSample(LOADING)  # Loading first, then Imaging
        f.result()
        f = cryoLoadSample(IMAGING)
        f.result()
        f = focus.moveAbs(self.focus_deactive)
        f.result()
        f = stage.moveAbs({'rx': 0.003, 'rz': 0.003})
        f.result()
        f = cryoTiltSample(rx=0, rz=0)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {'rx': 0, 'rz': 0}, match_all=False,
                                     atol=ATOL_STAGE)

    def test_tilting_from_imaging(self):
        """
        Test tilting the sample stage while the stage is in Imaging position
        """
        stage = self.stage
        focus = self.focus
        # Get the stage to imaging position and park focus
        f = cryoLoadSample(LOADING)
        f.result()
        f = cryoLoadSample(IMAGING)
        f.result()
        f = focus.moveAbs(self.focus_deactive)
        f.result()
        # Tilt the stage
        f = cryoTiltSample(rx=0.003, rz=0.003)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {'rx': 0.003, 'rz': 0.003}, match_all=False,
                                     atol=ATOL_STAGE)

    def test_tilting_from_loading(self):
        """
        Test it's not possible to do tilting movement while the stage is in Loading position
        """
        stage = self.stage
        f = cryoLoadSample(LOADING)
        f.result()
        with self.assertRaises(ValueError):
            f = cryoTiltSample(rx=0.003, rz=0.003)
            f.result()

    def test_cancel_loading(self):
        """
        Test cryoLoadSample movement cancellation is handled correctly
        """
        stage = self.stage
        f = cryoLoadSample(LOADING)
        f.result()
        f = cryoLoadSample(IMAGING)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        test.assert_pos_not_almost_equal(stage.position.value, self.stage_deactive,
                                         atol=ATOL_STAGE)

    def test_cancel_tilting(self):
        """
        Test cryoTiltSample movement cancellation is handled correctly
        """
        stage = self.stage
        focus = self.focus
        f = cryoLoadSample(LOADING)  # (Loading first, then Imaging)
        f.result()
        f = cryoLoadSample(IMAGING)
        f.result()
        f = focus.moveAbs(self.focus_deactive)
        f.result()
        f = cryoTiltSample(rx=0.003, rz=0.003)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        test.assert_pos_not_almost_equal(stage.position.value, {'rx': 0.003, 'rz': 0.003}, match_all=False,
                                         atol=ATOL_STAGE)

    def test_get_progress(self):
        """
        Test getLoadingProgress function behaves as expected
        """
        start_point = {'x': 0, 'y': 0, 'z': 0}
        end_point = {'x': 2, 'y': 2, 'z': 2}
        current_point = {'x': 1, 'y': 1, 'z': 1}
        progress = getLoadingProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))
        current_point = {'x': .998, 'y': .999, 'z': .999}  # slightly off the line
        progress = getLoadingProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))
        current_point = {'x': 3, 'y': 3, 'z': 3}  # away from the line
        progress = getLoadingProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)
        current_point = {'x': 1, 'y': 1, 'z': 3}  # away from the line
        progress = getLoadingProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)
        current_point = {'x': -1, 'y': 0, 'z': 0}  # away from the line
        progress = getLoadingProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)
