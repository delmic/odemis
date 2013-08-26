#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 26 Aug 2013

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from odemis import model
from odemis.model._components import DigitalCamera
import logging
import numpy
import unittest

logging.getLogger().setLevel(logging.DEBUG)

class TestCamera(unittest.TestCase):

    def setUp(self):
        self.comp = DigitalCamera("testcam", "ccd", transpose=[1, 2])
        self.comp._shape = (1024, 1024, 256)

    def testTransposeArg(self):
        """
        Check all the transpose related functions 
        """
        # transpose + shape => different types of cameras
        cams = [(None, (1024,)), # 0D CCD
                (None, (256, 512, 1024,)), # 2D CCD
                ([-2, 1], (256, 512, 1024,)), # rotated
                ([-1], (256, 1024,)), # 1D mirrored
                ([-2, 3, 1], (256, 24, 362, 1024,)), # 3D CCD
                ]
        for trp, shp in cams:
            cam = DigitalCamera("testcam", "ccd", transpose=trp)
            cam._shape = shp
            logging.info("Testing CCD with shape= %s, transpose= %s", shp, trp)
            
            cam_trp = cam.transpose # always contains a full version (not None)
            self.assertNotEqual(cam_trp, None)
            self.assertEqual(len(cam_trp), len(shp) - 1)
            self.assertEqual(len(cam_trp), len(set(cam_trp)))

            # Test shape
            shp_user = cam._transposeShapeToUser(shp)
            logging.info("Pretends shape = %s", shp_user)
            self.assertEqual(len(shp), len(shp_user))
            self.assertEqual(cam.shape, shp_user)

            # Test res
            res = list(shp[:-1])
            exp_res_user = list(shp_user[:-1])
            res_user = cam._transposeSizeToUser(res)
            logging.info("Pretends res = %s", res_user)
            self.assertEqual(exp_res_user, res_user)
            self.assertEqual(len(shp) - 1, len(res_user))
            self.assertTrue(all(v > 0 for v in res_user))

            res_back = cam._transposeSizeFromUser(res_user)
            self.assertEqual(res, res_back)

            # Test pos (and check type is respected)
            pos = tuple(v - 1 for v in shp[:-1]) # make it not too easy
            exp_pos_user = [v - 1 for v in shp_user[:-1]]
            for i, idx in enumerate(cam_trp):
                if idx < 0:
                    exp_pos_user[i] = 0 # the other extreme
            exp_pos_user = tuple(exp_pos_user)
            pos_user = cam._transposePosToUser(pos)
            logging.info("Pretends bottom right px is at %s", pos_user)
            self.assertEqual(exp_pos_user, pos_user)
            self.assertEqual(len(shp) - 1, len(pos_user))

            pos_back = cam._transposePosFromUser(pos_user)
            self.assertEqual(pos, pos_back)

#            # Test center-based positions
#            pc0 = (0,) * len(res) # should be constant
#            center = tuple(v / 2 for v in shp[:-1])
#            self.assertEqual(pc0, cam._transposeSizeToUser(pc0, origin=center))
#            self.assertEqual(pc0, cam._transposeSizeFromUser(pc0, origin=center))

            # Test DataArrays
            data = numpy.zeros(res, dtype="uint8")
            md = {model.MD_ACQ_DATE: 12}
            da = model.DataArray(data, md)
            pos0 = (0,) * len(res)
            pos0_user = cam._transposePosToUser(pos0)
            da[pos0] = 12
            da[pos] = 42
            da_user = cam._transposeDAToUser(da)
            self.assertEqual(da_user.shape, tuple(res_user))
            self.assertEqual(da_user[pos0_user], da[pos0])
            self.assertEqual(da_user[pos_user], da[pos])


class TestActuator(unittest.TestCase):


    def testInvertedArg(self):
        """
        Check all the invert related functions 
        """
        pass




if __name__ == "__main__":
    unittest.main()
