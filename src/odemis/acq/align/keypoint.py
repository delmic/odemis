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

from __future__ import division

import logging
import numpy
import cv2
from scipy import ndimage
from odemis import model
from odemis.util import img


USE_BF = True  # Use BruteForce matcher
USE_KNN = True  # Use k-nearest neighbour matching method

def FindTransform(ima, imb, fd_type="ORB"):
    """
    ima(DataArray of shape YaXa with uint8): Image to be aligned
    imb(DataArray of shape YbXb with uint8): Base image
        Note that the shape doesn't have to be any relationship with the shape of the
        first dimension(doesn't even need to be the same ratio)
    fd_type(string): Feature detector type. Must be 'SIFT' or 'ORB'. ORB is faster,
        but SIFT usually has better results.
    return (ndarray of shape 3, 3): transformation matrix to align the first image on the
        base image. (right column is translation)
    raises:
    ValueError: if no good transformation is found.
    """

    # Instantiate the feature detector and the matcher
    # The brute force matcher used for ORB can be used also for SIFT,
    # but the opposite is not true. The Flann matcher cannot be used on ORB.
    # A Flann based matcher is used for SIFT because it is faster than the brute force
    # used on ORB, and it also showed better results on the tests.
    # TODO: in theory, the brute force matcher is supposed to give better results
    # (although a little slower)
    # TODO: try BRISK?
    if fd_type == "ORB":
        feature_detector = cv2.ORB()
        if USE_BF:
            matcher = cv2.BFMatcher(normType=cv2.NORM_HAMMING)
        else:
            FLANN_INDEX_LSH = 6
            index_params = dict(algorithm=FLANN_INDEX_LSH,
                                table_number=6,  # 12
                                key_size=12,  # 20
                                multi_probe_level=1)  # 2
            search_params = dict(checks=50)
            matcher = cv2.FlannBasedMatcher(index_params, search_params)
    elif fd_type == "SIFT":
        feature_detector = cv2.SIFT()
        if USE_BF:
            matcher = cv2.BFMatcher(normType=cv2.NORM_L2)
        else:
            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
            search_params = dict(checks=50)
            matcher = cv2.FlannBasedMatcher(index_params, search_params)
    else:
        raise ValueError("Unknown feature detector %s" % (fd_type,))

    # find and compute the descriptors
    ima_kp, ima_des = feature_detector.detectAndCompute(ima, None)
    imb_kp, imb_des = feature_detector.detectAndCompute(imb, None)
    logging.debug("Found %d and %d keypoints", len(ima_kp), len(imb_kp))

    # run the matcher of the detected features
    if USE_KNN:
        # For each keypoint, return up to k(=2) best ones in the other image
        matches = matcher.knnMatch(ima_des, imb_des, k=2)

        # store all the good matches as per Lowe's ratio test
        selected_matches = [m[0] for m in matches
                            if len(m) == 2 and m[0].distance < m[1].distance * 0.75]
    else:
        # For each keypoint, pick the closest one in the other image
        matches = matcher.match(ima_des, imb_des)

        # Pick up to the best 10 matches
        min_dist = 100  # almost random value
        selected_matches = [m for m in matches if m.distance < min_dist]
        selected_matches.sort(key=lambda m: m.distance)
        selected_matches = selected_matches[:10]

    logging.debug("Found %d matches and %d good ones", len(matches), len(selected_matches))
    if len(selected_matches) < 5:
        raise ValueError("Less than 5 common features (%d) detected on the images" %
                         (len(selected_matches),))

    # get keypoints for selected matches
    selected_ima_kp = [list(ima_kp[m.queryIdx].pt) for m in selected_matches]
    selected_imb_kp = [list(imb_kp[m.trainIdx].pt) for m in selected_matches]
    selected_ima_kp = numpy.array([selected_ima_kp])
    selected_imb_kp = numpy.array([selected_imb_kp])

    ima_mkp = [ima_kp[m.queryIdx] for m in selected_matches]
    imb_mkp = [imb_kp[m.trainIdx] for m in selected_matches]

    # testing detecting the matching points automatically
    try:
        mat, mask = cv2.findHomography(selected_ima_kp, selected_imb_kp, cv2.RANSAC)
    except Exception:
        raise ValueError("The images does not match")

    if mat is None:
        raise ValueError("The images does not match")

    return mat, ima_kp, imb_kp, ima_mkp, imb_mkp


def preprocess(im, invert, flip, crop, gaussian_sigma, eqhis):
    '''
    Typical preprocessing steps needed before performing keypoint matching
    im (DataArray): Input image
    invert (bool): Invert the brightness levels of the image
    flip (tuple(bool, bool)): Determine if the image should be flipped on the X and Y axis
    crop (tuple(t,b,l,r): Crop values in pixels
    gaussian_sigma (int): Blur intensity
    eqhis (bool): If True, an histogram equalisation is performed (and data type)
      is set to uint8
    return (DataArray of same shape): Processed image
    '''
    try:
        metadata = im.metadata
    except AttributeError:
        metadata = {}

    flip_x, flip_y = flip
    # flip on X axis
    if flip_x:
        im = im[:, ::-1]

    # flip on Y axis
    if flip_y:
        im = im[::-1, :]

    crop_top, crop_bottom, crop_left, crop_right = crop
    # remove the bar
    im = im[crop_top:im.shape[0] - crop_bottom, crop_left:im.shape[1] - crop_right]

    # Invert the image brightness
    if invert:
        # mn = im.min()
        mx = im.max()
        im = mx - im

    # equalize histogram
    if eqhis:
        if im.dtype != numpy.uint8:
            # OpenCV histogram equalisation only works on uint8 data
            rgb_im = img.DataArray2RGB(im)
            im = rgb_im[:, :, 0]
        im = cv2.equalizeHist(im)

    # blur the image using a gaussian filter
    if gaussian_sigma:
        im = ndimage.gaussian_filter(im, sigma=gaussian_sigma)

    # return a new DataArray with the metadata of the original image
    return model.DataArray(im, metadata)
