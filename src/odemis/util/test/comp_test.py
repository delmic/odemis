#!/usr/bin/env python
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

from odemis.util.comp import compute_scanner_fov, get_fov_rect, \
    compute_camera_fov

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


if __name__ == "__main__":
    unittest.main()

