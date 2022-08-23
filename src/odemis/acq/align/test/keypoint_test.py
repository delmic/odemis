#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 04 Mar 2017

@author: Guilherme Stiebler

Copyright © 2017 Guilherme Stiebler, Éric Piel, Delmic

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
import cairo
import logging
import math
import numpy
from numpy.linalg import inv
from odemis import model
from odemis.acq.align import keypoint
from odemis.util.conversion import get_img_transformation_md
from odemis.util.dataio import open_acquisition
import os
import unittest
import cv2
from odemis.acq.align.keypoint import preprocess


logging.getLogger().setLevel(logging.DEBUG)

IMG_PATH = os.path.join(os.path.dirname(__file__), "images")


class TestKeypoint(unittest.TestCase):

    def test_image_pair(self):
        ''' Testing a pair of images
        '''
        # WARNING: if opencv is not compiled with SIFT support (ie, only ORB
        # available), then this test case will fail.
        # FIXME: these two images are very hard, and any tiny change in the
        # algorithm or settings can cause the alignment to fail => not a good
        # test case
        # only one image will be used, but this structure helps to test
        # different images
        image_pairs = [
            (
                ('Slice69_stretched.tif', True, (False, True), (0, 0, 0, 0), 6),
                ('g_009_cropped.tif', False, (False, False), (0, 0, 0, 0), 3)
            ),
#             (
#                 ('001_CBS_010.tif', False, (False, False), (0, 0, 0, 0), 0),
#                 ('20141014-113042_1.tif', False, (False, False), (0, 0, 0, 0), 0)
#             ),
#             (
#                 ('t3 DELPHI.tiff', False, (False, False), (0, 200, 0, 0), 3),
#                 ('t3 testoutA3.tif', False, (False, False), (0, 420, 0, 0), 3)
#             )
        ]
        image_pair = image_pairs[0]
        # open the images
        tem_img = open_acquisition(os.path.join(IMG_PATH, image_pair[0][0]))[0].getData()
        sem_img = open_acquisition(os.path.join(IMG_PATH, image_pair[1][0]))[0].getData()
        # preprocess
        tem_img = preprocess(tem_img, image_pair[0][1], image_pair[0][2],
                             image_pair[0][3], image_pair[0][4], True)
        sem_img = preprocess(sem_img, image_pair[1][1], image_pair[1][2],
                             image_pair[1][3], image_pair[1][4], True)
        # execute the algorithm to find the transform between the images
        try:
            tmat, _, _, _, _ = keypoint.FindTransform(tem_img, sem_img)
        except ValueError:
            if not hasattr(cv2, 'SIFT') and not hasattr(cv2, 'SIFT_create'):
                self.skipTest("Test only works with SIFT, not with ORB.")
            else:
                raise AssertionError("Failed to find transform between images.")
        # uncomment this if you want to see the keypoint images
        '''tem_painted_kp = cv2.drawKeypoints(tem_img, tem_kp, None, color=(0,255,0), flags=0)
        sem_painted_kp = cv2.drawKeypoints(sem_img, sem_kp, None, color=(0,255,0), flags=0)
        cv2.imwrite(IMG_PATH + 'tem_kp.jpg', tem_painted_kp)
        cv2.imwrite(IMG_PATH + 'sem_kp.jpg', sem_painted_kp)'''

        # uncomment this if you want to see the warped image
        '''warped_im = cv2.warpPerspective(tem_img, tmat, (sem_img.shape[1], sem_img.shape[0]))
        merged_im = cv2.addWeighted(sem_img, 0.5, warped_im, 0.5, 0.0)
        cv2.imwrite(IMG_PATH + 'merged_with_warped.jpg', merged_im)'''

        tmetadata = get_img_transformation_md(tmat, tem_img, sem_img)
        logging.debug("Computed metadata = %s", tmetadata)
        # FIXME: these values are actually pretty bad
        # comparing based on a successful alignment validated from the warped image
#         self.assertAlmostEqual(8.7e-07, tmetadata[model.MD_PIXEL_SIZE][0], places=6)
#         self.assertAlmostEqual(1.25e-06, tmetadata[model.MD_PIXEL_SIZE][1], places=6)
#         self.assertAlmostEqual(0.085, tmetadata[model.MD_ROTATION], places=2)
#         self.assertAlmostEqual(0.000166, tmetadata[model.MD_POS][0], places=5)
#         self.assertAlmostEqual(-0.0001435, tmetadata[model.MD_POS][1], places=5)
#         self.assertAlmostEqual(0.035, tmetadata[model.MD_SHEAR], places=2)
#
        # Check that calling the function again with the same data returns the
        # same results (bug happens when using FLANN-KDtree matcher)
        for i in range(2):
            try:
                tmatn, _, _, _, _ = keypoint.FindTransform(tem_img, sem_img)
            except ValueError:
                if not hasattr(cv2, 'SIFT') and not hasattr(cv2, 'SIFT_create'):
                    self.skipTest("Test only works with SIFT, not with ORB.")
                else:
                    raise AssertionError("Failed to find transform between images.")
            tmetadatan = get_img_transformation_md(tmatn, tem_img, sem_img)
            logging.debug("Computed metadata = %s", tmetadatan)
            numpy.testing.assert_equal(tmatn, tmat)
            self.assertEqual(tmetadatan, tmetadata)

    def test_synthetic_images(self):
        ''' Testing the matching of a synthetic image. The image is generated with
        a rotation and scale, and then it checks if the matching algorithm
        came up with the same result
        '''
        # generate a syntyetic image
        image = numpy.zeros((1000, 1000, 4), dtype=numpy.uint8)
        surface = cairo.ImageSurface.create_for_data(image, cairo.FORMAT_ARGB32, 1000, 1000)
        cr = cairo.Context(surface)
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.paint()

        cr.set_source_rgb(0.0, 0.0, 0.0)

        # draw circles
        cr.arc(200, 150, 80, 0, 2 * math.pi)
        cr.fill()

        cr.arc(400, 150, 70, 0, 2 * math.pi)
        cr.fill()

        cr.arc(700, 180, 50, 0, 2 * math.pi)
        cr.fill()

        cr.arc(200, 500, 80, 0, 2 * math.pi)
        cr.fill()

        cr.arc(400, 600, 70, 0, 2 * math.pi)
        cr.fill()

        cr.arc(600, 500, 50, 0, 2 * math.pi)
        cr.fill()

        cr.arc(600, 500, 50, 0, 2 * math.pi)
        cr.fill()

        cr.arc(500, 500, 350, 0, 2 * math.pi)
        cr.set_line_width(5)
        cr.stroke()

        cr.arc(600, 500, 50, 0, 2 * math.pi)
        cr.fill()

        # center circle
        cr.arc(500, 500, 5, 0, 2 * math.pi)
        cr.fill()

        # rectangle
        cr.rectangle(600, 700, 200, 100)
        cr.fill()

        image = image[:, :, 0]

        angle = 0.3
        scale = 0.7
        translation_x = 100
        translation_y = 50
        # generate a rotation/scale matrix, with the rotation centered on the center of the image
        rot_scale_mat = cv2.getRotationMatrix2D((500.0, 500.0), math.degrees(angle), scale)
        # generate the transformed image with scale and rotation
        timg = cv2.warpAffine(image, rot_scale_mat, (1000, 1000),
                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        # generate a transformation matrix with translation
        translation_mat = numpy.float32([[1, 0, translation_x], [0, 1, translation_y]])
        # generate the transformed image with translation
        timg = cv2.warpAffine(timg, translation_mat, (1000, 1000),
                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))

        image = preprocess(image, False, (False, False), (0, 0, 0, 0), 0, True)
        timg = preprocess(timg, False, (False, False), (0, 0, 0, 0), 0, True)

        # execute the matching algorithm, and find the transformation matrix between the original
        # and the transformed image
        tmat_odemis, _, _, _, _ = keypoint.FindTransform(timg, image)

        timg_md = {}
        timg = model.DataArray(timg, timg_md)
        image_md = {
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
            model.MD_POS: (35e-6, 25e-6),
            model.MD_ROTATION: 0.15,
            model.MD_SHEAR: 0.15
        }
        image = model.DataArray(image, image_md)
        # use the invert matrix to get the original values
        tmetadata = get_img_transformation_md(inv(tmat_odemis), timg, image)
        logging.debug("Computed metadata = %s", tmetadata)
        # the matching algorithm is not that accurate, so the values are approximated
        self.assertAlmostEqual(0.7e-6, tmetadata[model.MD_PIXEL_SIZE][0], places=7)
        self.assertAlmostEqual(0.7e-6, tmetadata[model.MD_PIXEL_SIZE][1], places=7)
        # 0.3 + 0.15
        self.assertAlmostEqual(0.45, tmetadata[model.MD_ROTATION], places=1)
        # (100 + 35) * PS
        self.assertAlmostEqual(135e-06, tmetadata[model.MD_POS][0], places=5)
        # (-50 + 25) * PS
        self.assertAlmostEqual(-25e-06, tmetadata[model.MD_POS][1], places=5)
        # 0.0 (there's no shear on the image) + 0.15
        self.assertAlmostEqual(0.15, tmetadata[model.MD_SHEAR], places=1)

        # uncomment this if you want to see the images used on this test
        '''
        warped_im = cv2.warpPerspective(timg, tmat_odemis, (timg.shape[1], timg.shape[0]),\
                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        cv2.imwrite(IMG_PATH + 'test.jpg', image)
        cv2.imwrite(IMG_PATH + 'transformed_image.jpg', timg)
        cv2.imwrite(IMG_PATH + 'rotated_opencv.jpg', warped_im)'''

    def test_no_match(self):
        '''Testing the matching of two feature-less images'''
        image = numpy.zeros((1000, 1000), dtype=numpy.uint8) + 86
        timg = numpy.zeros((2000, 1000), dtype=numpy.uint16) + 2563

        image = preprocess(image, False, (False, False), (0, 0, 0, 0), 0, True)
        timg = preprocess(timg, False, (False, False), (0, 0, 0, 0), 0, True)

        with self.assertRaises(ValueError):
            tmat, _, _, _, _ = keypoint.FindTransform(timg, image)


if __name__ == '__main__':
    unittest.main()
