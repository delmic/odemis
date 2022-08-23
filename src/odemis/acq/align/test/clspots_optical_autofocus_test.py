# -*- coding: utf-8 -*-
"""
Created on 1 May 2019

@author: Thera Pals

Copyright © 2019-2022 Thera Pals, Delmic

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
import numpy
import os
import unittest

import odemis
from odemis import model
from odemis.acq import align
from odemis.util import testing, timeout

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

logging.getLogger().setLevel(logging.INFO)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
CLSPOTS_SIM_CONFIG = CONFIG_PATH + "sim/clspots-optical-autofocus-sim.odm.yaml"
CLSPOTS_CONFIG = CONFIG_PATH + "hwtest/clspots-optical-autofocus.odm.yaml"


class TestAutofocusSim(unittest.TestCase):
    """
    Test simulated auto focus for CL spots.
    """

    @classmethod
    def setUpClass(cls):
        testing.start_backend(CLSPOTS_SIM_CONFIG)

        # find components by their role
        cls.diagnostic_cam = model.getComponent(role="diagnostic-ccd")
        cls.stage = model.getComponent(role="stage")

    @timeout(1000)
    def test_autofocus_opt(self):
        """
        Test AutoFocus for a certain starting position and good focus position.
        """
        # set the position where the image is in focus.
        good_focus = 68e-6
        self.diagnostic_cam.updateMetadata({model.MD_FAV_POS_ACTIVE: {"z": good_focus}})
        # Move the stage so that the image is out of focus
        center_position = 17e-6
        self.stage.moveAbs({"z": center_position}).result()
        # Run auto focus
        future_focus = align.autofocus.CLSpotsAutoFocus(self.diagnostic_cam, self.stage)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        # Check that the auto focus converged to the correct position and the stage moved to the correct position
        numpy.testing.assert_allclose(foc_pos, good_focus, atol=1e-6)
        numpy.testing.assert_allclose(self.stage.position.value["z"], foc_pos, atol=1e-6)

    def test_focus_at_boundaries(self):
        """
        Test that the good focus is also found at the boundaries of the range of the stage.
        """
        # set the position where the image is in focus.
        good_focus = 0
        self.diagnostic_cam.updateMetadata({model.MD_FAV_POS_ACTIVE: {"z": good_focus}})
        # Move the stage so that the image is out of focus
        center_position = 32e-6
        self.stage.moveAbs({"z": center_position}).result()
        # Run auto focus
        future_focus = align.autofocus.CLSpotsAutoFocus(self.diagnostic_cam, self.stage)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        # Check that the auto focus converged to the correct position and the stage moved to the correct position
        numpy.testing.assert_allclose(foc_pos, good_focus, atol=1e-6)
        numpy.testing.assert_allclose(self.stage.position.value["z"], foc_pos, atol=1e-6)

        # set the position where the image is in focus.
        good_focus = 100e-6
        self.diagnostic_cam.updateMetadata({model.MD_FAV_POS_ACTIVE: {"z": good_focus}})
        # Move the stage so that the image is out of focus
        center_position = 17e-6
        self.stage.moveAbs({"z": center_position}).result()
        # Run auto focus
        future_focus = align.autofocus.CLSpotsAutoFocus(self.diagnostic_cam, self.stage)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        # Check that the auto focus converged to the correct position and the stage moved to the correct position
        numpy.testing.assert_allclose(foc_pos, good_focus, atol=1e-6)
        numpy.testing.assert_allclose(self.stage.position.value["z"], foc_pos, atol=1e-6)

    @unittest.skip("Skip, very slow.")
    def test_autofocus_different_starting_positions(self):
        """
        Test auto focus of CL spots for a thousand random starting and good focus positions.
        """
        for k in range(500):
            # Move the stage to a random starting position.
            start_position = numpy.random.randint(100) * 1e-6
            self.stage.moveAbs({"z": start_position}).result()
            # Set the good focus to a random value.
            good_focus = numpy.random.randint(100) * 1e-6
            logging.debug("start position {}, good focus {}".format(start_position, good_focus))
            self.diagnostic_cam.updateMetadata({model.MD_FAV_POS_ACTIVE: {"z": good_focus}})
            # run auto focus
            future_focus = align.autofocus.CLSpotsAutoFocus(self.diagnostic_cam, self.stage)
            foc_pos, foc_lev = future_focus.result(timeout=900)
            logging.debug("found focus at {} good focus at {}".format(foc_pos, good_focus))
            numpy.testing.assert_allclose(foc_pos, good_focus, atol=1e-6)


class TestAutofocusHW(unittest.TestCase):
    """
    Test auto focus functions for CL spots with hardware.
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            raise unittest.SkipTest('No HW present. Skipping tests.')

        testing.start_backend(CLSPOTS_CONFIG)

        # find components by their role
        cls.diagnostic_cam = model.getComponent(role="diagnostic-ccd")
        cls.stage = model.getComponent(role="stage")
        cls._optimal_focus = 40e-6  # update with actual value

    def test_autofocus_optical(self):
        """
        Test AutoFocus for a certain starting position and good focus position.
        """
        # Move the stage so that the image is out of focus
        center_position = 74e-6
        self.stage.moveAbs({"z": center_position}).result()
        numpy.testing.assert_allclose(self.stage.position.value["z"], center_position, atol=1e-7)
        # run autofocus
        future_focus = align.AutoFocus(self.diagnostic_cam, None, self.stage)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        # Test if the correct focus position was found.
        logging.debug("found focus at {} good focus at {}".format(foc_pos, self._optimal_focus))
        numpy.testing.assert_allclose(foc_pos, self._optimal_focus, atol=0.5e-6)

    def test_autofocus_optical_multiple_runs(self):
        """
        Test that the correct focus position is found for the range of 0 to 100 micrometers as starting position.
        """
        # Move the stage so that the image is out of focus
        results = []
        for start_position in range(100):
            try:
                start_position = start_position * 1e-6
                logging.debug("start pos {}".format(start_position))
                # move the stage to the start position.
                self.stage.moveAbs({"z": start_position}).result()
                numpy.testing.assert_allclose(self.stage.position.value["z"], start_position, atol=1e-6)
                # run autofocus
                future_focus = align.AutoFocus(self.diagnostic_cam, None, self.stage)
                foc_pos, foc_lev = future_focus.result(timeout=900)
                # Test if the correct focus position was found.
                logging.debug("found focus at {} good focus at {}".format(foc_pos, self._optimal_focus))
                result = numpy.allclose(foc_pos, self._optimal_focus, atol=1e-6)
                numpy.testing.assert_allclose(self.stage.position.value["z"], foc_pos, atol=1e-6)
                results.append(result)
            except Exception as e:
                logging.debug("{}".format(e))
                continue
        # Fail if one of the runs did not result in the finding of the correct position.
        self.assertTrue(numpy.all(results))

    def test_autofocus_optical_start_at_good_focus(self):
        """
        Test if the correct focus position is found when starting at the correct focus position.
        """
        # Move the stage so that the image is out of focus
        start_position = self._optimal_focus
        self.stage.moveAbs({"z": start_position}).result()
        # check that it moved to the correct starting position
        numpy.testing.assert_allclose(self.stage.position.value["z"], start_position, atol=1e-7)
        # Run autofocus
        future_focus = align.AutoFocus(self.diagnostic_cam, None, self.stage)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        # Test that the correct focus has been found.
        logging.debug("found focus at {} good focus at {}".format(foc_pos, self._optimal_focus))
        numpy.testing.assert_allclose(foc_pos, self._optimal_focus, atol=0.5e-6)


if __name__ == '__main__':
    unittest.main()
