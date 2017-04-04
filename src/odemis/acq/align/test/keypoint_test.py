#!/usr/bin/env python
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

import unittest
import logging
from odemis.acq.align import keypoint
import cv2
from odemis.util.conversion import get_img_transformation_md
from odemis.util.dataio import open_acquisition
import numpy
import cairo
import math
from odemis import model
from numpy.linalg import inv
from scipy import ndimage

imgs_folder = './src/odemis/acq/align/test/images/'


def preprocess(img, invert, flip, crop, gaussian_sigma, eqhis):
    '''
    The pre-processing function like the one on the alignment plugin
    img (DataArray): Input image
    invert (bool): Invert the brightness levels of the image
    flip (tuple(bool, bool)): Determine if the image should be flipped on the X and Y axis
    crop (tuple(t,b,l,r): Crop values in pixels
    gaussian_sigma (int): Blur intensity
    eqhis (bool): Determine if an histogram equalization should be executed
    return: Processed image
    '''
    metadata = img.metadata

    flip_x, flip_y = flip
    # flip on X axis
    if flip_x:
        img = img[:, ::-1]

    # flip on Y axis
    if flip_y:
        img = img[::-1, :]

    crop_top, crop_bottom, crop_left, crop_right = crop
    # remove the bar
    img = img[crop_top:img.shape[0] - crop_bottom, crop_left:img.shape[1] - crop_right]

    # Invert the image brightness
    if invert:
        mn = img.min()
        mx = img.max()
        img = mx + mn - img

    # equalize histogram
    if eqhis:
        if img.dtype == numpy.uint16:
            img = cv2.convertScaleAbs(img, alpha=(255.0/65535.0))
        img = cv2.equalizeHist(img)

    # blur the image using a gaussian filter
    img = ndimage.gaussian_filter(img, sigma=gaussian_sigma)

    # return a new DataArray with the metadata of the original image
    return  model.DataArray(img, metadata)


class TestKeypoint(unittest.TestCase):

    def test_image_pair(self):
        ''' Testing a pair of images
        '''
        # only one image will be used, but this structure helps to test
        # different images
        image_pairs = [
            (
                ('Slice69_stretched.tif', True, (False, True), (0, 0, 0, 0), 7),
                ('g_009_cropped.tif', False, (False, False), (0, 0, 0, 0), 5)
            ),
            '''(
                ('001_CBS_010.tif', False, (False, False), (0, 0, 0, 0), 0),
                ('20141014-113042_1.tif', False, (False, False), (0, 0, 0, 0), 0)
            ),
            (
                ('t3 DELPHI.tiff', False, (False, False), (0, 200, 0, 0), 3),
                ('t3 testoutA3.tif', False, (False, False), (0, 420, 0, 0), 3)
            )'''
        ]
        image_pair = image_pairs[0]
        # open the images
        tem_img = open_acquisition(imgs_folder + image_pair[0][0])[0].getData()
        sem_img = open_acquisition(imgs_folder + image_pair[1][0])[0].getData()
        # preprocess
        tem_img = preprocess(tem_img, image_pair[0][1], image_pair[0][2],
                image_pair[0][3], image_pair[0][4], True)
        sem_img = preprocess(sem_img, image_pair[1][1], image_pair[1][2],
                image_pair[1][3], image_pair[1][4], True)
        # execute the algorithm to find the transform between the images
        tmat, tem_kp, sem_kp = keypoint.FindTransform(tem_img, sem_img)

        # uncomment this if you want to see the keypoint images
        '''tem_painted_kp = cv2.drawKeypoints(tem_img, tem_kp, None, color=(0,255,0), flags=0)
        sem_painted_kp = cv2.drawKeypoints(sem_img, sem_kp, None, color=(0,255,0), flags=0)
        cv2.imwrite(imgs_folder + 'tem_kp.jpg', tem_painted_kp)
        cv2.imwrite(imgs_folder + 'sem_kp.jpg', sem_painted_kp)'''

        # uncomment this if you want to see the warped image
        '''warped_im = cv2.warpPerspective(tem_img, tmat, (sem_img.shape[1], sem_img.shape[0]))
        merged_im = cv2.addWeighted(sem_img, 0.5, warped_im, 0.5, 0.0)
        cv2.imwrite(imgs_folder + 'merged_with_warped.jpg', merged_im)'''

        tmetadata = get_img_transformation_md(tmat, tem_img, sem_img)
        # comparing based on a successful alignment validated from the warped image
        self.assertAlmostEqual(9.04656033782724e-07, tmetadata[model.MD_PIXEL_SIZE][0])
        self.assertAlmostEqual(1.4188154594260145e-06, tmetadata[model.MD_PIXEL_SIZE][1])
        self.assertAlmostEqual(0.1734484003427465, tmetadata[model.MD_ROTATION])
        self.assertAlmostEqual(0.0001793779203886272, tmetadata[model.MD_POS][0])
        self.assertAlmostEqual(-0.00015415625938805169, tmetadata[model.MD_POS][1])

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
        cr.arc(200, 150, 80, 0, 2*math.pi)
        cr.fill()

        cr.arc(400, 150, 70, 0, 2*math.pi)
        cr.fill()

        cr.arc(700, 180, 50, 0, 2*math.pi)
        cr.fill()

        cr.arc(200, 500, 80, 0, 2*math.pi)
        cr.fill()

        cr.arc(400, 600, 70, 0, 2*math.pi)
        cr.fill()

        cr.arc(600, 500, 50, 0, 2*math.pi)
        cr.fill()

        cr.arc(600, 500, 50, 0, 2*math.pi)
        cr.fill()

        cr.arc(500, 500, 350, 0, 2*math.pi)
        cr.set_line_width(5)
        cr.stroke()

        cr.arc(600, 500, 50, 0, 2*math.pi)
        cr.fill()

        # center circle
        cr.arc(500, 500, 5, 0, 2*math.pi)
        cr.fill()

        # rectangle
        cr.rectangle(600, 700, 200, 100)
        cr.fill()

        angle = 0.3
        scale = 0.7
        translation_x = 100
        translation_y = 50
        # generate a rotation/scale matrix, with the rotation centered on the center of the image
        rot_scale_mat = cv2.getRotationMatrix2D((500.0, 500.0), math.degrees(angle), scale)
        # generate the transformed image with scale and rotation
        timg = cv2.warpAffine(image, rot_scale_mat, (1000, 1000),\
                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        # generate a transformation matrix with translation
        translation_mat = numpy.float32([[1,0,translation_x],[0,1,translation_y]])
        # generate the transformed image with translation
        timg = cv2.warpAffine(timg, translation_mat, (1000, 1000),\
                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        # execute the matching algorithm, and find the transformation matrix between the original
        # and the transformed image
        tmat_odemis, _, _ = keypoint.FindTransform(timg, image)

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
        # the matching algorithm is not that accurate, so the values are approximated
        self.assertAlmostEqual(0.6989929643344752e-6, tmetadata[model.MD_PIXEL_SIZE][0])
        self.assertAlmostEqual(0.6992058385109082e-6, tmetadata[model.MD_PIXEL_SIZE][1])
        # 0.3 + 0.15
        self.assertAlmostEqual(0.44446353275792726, tmetadata[model.MD_ROTATION])
        # (100 + 35) * PS
        self.assertAlmostEqual(134.80058380419755e-06, tmetadata[model.MD_POS][0])
        # (-50 + 25) * PS
        self.assertAlmostEqual(-25.155552827377845e-06, tmetadata[model.MD_POS][1])
        # 0.0 (there's no shear on the image) + 0.15
        self.assertAlmostEqual(0.15725256343582006, tmetadata[model.MD_SHEAR])

        # uncomment this if you want to see the images used on this test
        '''
        warped_im = cv2.warpPerspective(timg, tmat_odemis, (timg.shape[1], timg.shape[0]),\
                borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        cv2.imwrite(imgs_folder + 'test.jpg', image)
        cv2.imwrite(imgs_folder + 'transformed_image.jpg', timg)
        cv2.imwrite(imgs_folder + 'rotated_opencv.jpg', warped_im)'''


if __name__ == '__main__':
    unittest.main()