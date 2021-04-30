#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 31 May 2018

@author: Éric Piel

Copyright © 2018 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
from odemis import model
import odemis
from odemis.util import test
import os
import unittest
from odemis.driver.tmcm import TMCLController
from numpy import asarray, round

from odemis.util.comp import compute_scanner_fov, get_fov_rect, \
    compute_camera_fov, generate_zlevels

#logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"

class TestFoV(unittest.TestCase):

    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.stage = model.getComponent(role="stage")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_scanner_fov(self):
        # Move a little bit out of the origin, to make it less easy
        self.stage.moveAbsSync({"x": 10e-3, "y":-5e-3})
        
        fov = compute_scanner_fov(self.ebeam)
        rect = get_fov_rect(self.ebeam, fov)

        # Compare to the actual FoV of an acquired image
        im = self.sed.data.get()
        
        pxs_im = im.metadata[model.MD_PIXEL_SIZE]
        fov_im = im.shape[1] * pxs_im[0], im.shape[0] * pxs_im[1]
        self.assertEqual(fov, fov_im)
        center_im = im.metadata[model.MD_POS]
        rect_im = (center_im[0] - fov_im[0] / 2,
                   center_im[1] - fov_im[1] / 2 ,
                   center_im[0] + fov_im[0] / 2,
                   center_im[1] + fov_im[1] / 2)
        self.assertEqual(rect, rect_im)

    def test_camera_fov(self):
        # Move a little bit out of the origin, to make it less easy
        self.stage.moveAbsSync({"x": 1e-3, "y": 5e-3})

        fov = compute_camera_fov(self.ccd)
        rect = get_fov_rect(self.ccd, fov)

        # Compare to the actual FoV of an acquired image
        im = self.ccd.data.get()

        pxs_im = im.metadata[model.MD_PIXEL_SIZE]
        fov_im = im.shape[1] * pxs_im[0], im.shape[0] * pxs_im[1]
        self.assertEqual(fov, fov_im)
        center_im = im.metadata[model.MD_POS]
        rect_im = (center_im[0] - fov_im[0] / 2,
                   center_im[1] - fov_im[1] / 2 ,
                   center_im[0] + fov_im[0] / 2,
                   center_im[1] + fov_im[1] / 2)
        self.assertEqual(rect, rect_im)


class TestGenerateZlevels(unittest.TestCase):
    def setUp(self):
        self.focus = TMCLController(name="test_focus", role="focus",
                                         port="/dev/fake3",
                                         axes=["z"],
                                         ustepsize=[1e-6],
                                         rng=[[-3000e-6, 3000e-6], ],
                                         refproc="Standard")
        self.zMin = model.FloatContinuous(value=0, range=(-1000e-6, 0))
        self.zMax = model.FloatContinuous(value=0, range=(0, 1000e-6))
        self.zstep = model.FloatContinuous(value=0, range=(-100e-6, 100e-6))

    def test_zero_zstep(self):
        self.focus.position.value["z"] = 1300e-6
        self.zMin.value = -500e-6
        self.zMax.value = 500e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 0e-6
        self.assertRaises(ZeroDivisionError, generate_zlevels,
                          self.focus, self.zrange, self.zstep.value)

    def test_zmax_and_zmin_both_zeros(self):
        self.focus.position.value["z"] = 1300e-6
        self.zMin.value = -0e-6
        self.zMax.value = 0e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 10e-6
        self.assertRaises(ValueError, generate_zlevels,
                          self.focus, self.zrange, self.zstep.value)

    def test_zstep_greater_than_zmax_and_zmin(self):
        self.focus.position.value["z"] = 1300e-6
        self.zMin.value = -10e-6
        self.zMax.value = 10e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 70e-6
        actual = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        expected = asarray([-10e-6, 10e-6]) + self.focus.position.value["z"]
        self.assertListEqual(list(actual), list(expected))

    def test_normal_zlevels_output_with_positive_zstep(self):
        self.focus.position.value["z"] = 1000e-6
        self.zMax.value = 100e-6
        self.zMin.value = -250e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 50e-6
        expected = round(asarray([-250e-6, -200e-6, -150e-6, -100e-6, -50e-6,
                           0e-6, 50e-6, 100e-6]) + self.focus.position.value["z"], decimals=8)
        actual = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        self.assertListEqual(list(expected), list(actual))

    def test_normal_zlevels_output_with_negative_zstep(self):
        self.focus.position.value["z"] = 1000e-6
        self.zMax.value = 100e-6
        self.zMin.value = -250e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = -50e-6
        expected = round(asarray([-250e-6, -200e-6, -150e-6, -100e-6, -50e-6,
                           0e-6, 50e-6, 100e-6]) + self.focus.position.value["z"], decimals=8)
        actual = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        self.assertListEqual(
            sorted(list(expected), reverse=True), list(actual))

    def test_normal_zlevels_output_with_rounding_down(self):
        self.focus.position.value["z"] = 1000e-6
        self.zMax.value = 10e-6
        self.zMin.value = -10e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 6e-6
        expected = asarray([-10e-6, -3.33e-6, 3.33e-6, 10e-6]
                           ) + self.focus.position.value["z"]
        actual = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        self.assertListEqual(list(expected), list(actual))

    def test_normal_zlevels_output_with_rounding_up(self):
        self.focus.position.value["z"] = 1000e-6
        self.zMax.value = 24e-6
        self.zMin.value = -24e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 17e-6
        expected = asarray([-24e-6, -8e-6, 8e-6, 24e-6]) + \
            self.focus.position.value["z"]
        actual = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        self.assertListEqual(list(expected), list(actual))

    def test_large_number_of_levels(self):
        self.focus.position.value["z"] = 1000e-6
        self.zMax.value = 100e-6
        self.zMin.value = -100e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 0.5e-6
        output = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        self.assertEqual(len(output), 401)

    def test_clipping_zmin_on_actuator_lower_limit(self):
        self.focus.position.value["z"] = -2800e-6
        self.zMax.value = 0e-6
        self.zMin.value = -300e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 100e-6
        expected = asarray([-200e-6, -100e-6, 0e-6]) + \
            self.focus.position.value["z"]
        actual = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        self.assertListEqual(list(expected), list(actual))

    def test_clipping_zmax_on_actuator_upper_limit(self):
        self.focus.position.value["z"] = 2800e-6
        self.zMax.value = 300e-6
        self.zMin.value = 0e-6
        self.zrange = [self.zMin.value, self.zMax.value]
        self.zstep.value = 100e-6
        expected = asarray([0e-6, 100e-6, 200e-6]) + \
            self.focus.position.value["z"]
        actual = generate_zlevels(self.focus, self.zrange, self.zstep.value)
        self.assertListEqual(list(expected), list(actual))


if __name__ == "__main__":
    unittest.main()

