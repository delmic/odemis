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

import numpy as np
import cv2
import math
from odemis import model

def FindTransform(ima, imb, fd_type='SIFT'):
    """
    ima(DataArray of shape YaXa with int or float): Image to be aligned
    imb(DataArray of shape YbXb with int or float): Base image
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
    if fd_type == 'ORB':
        feature_detector = cv2.ORB()
        matcher = cv2.BFMatcher()
    else:
        feature_detector = cv2.SIFT()
        FLANN_INDEX_KDTREE = 0
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        matcher = cv2.FlannBasedMatcher(index_params, search_params)

    # find and compute the descriptors
    ima_kp, ima_des = feature_detector.detectAndCompute(ima, None)
    imb_kp, imb_des = feature_detector.detectAndCompute(imb, None)

    # run the matcher of the detected features
    matches = matcher.knnMatch(ima_des, imb_des, k=2)

    # store all the good matches as per Lowe's ratio test.
    selected_matches = []
    for m, n in matches:
        if m.distance < 0.7 * n.distance:
            selected_matches.append(m)

    if len(selected_matches) < 5:
        raise ValueError("Less than 5 common features detected on the images")

    # get keypoints for selected matches
    selected_ima_kp = [list(ima_kp[m.queryIdx].pt) for m in selected_matches]
    selected_imb_kp = [list(imb_kp[m.trainIdx].pt) for m in selected_matches]

    selected_ima_kp = np.array([selected_ima_kp])
    selected_imb_kp = np.array([selected_imb_kp])

    # testing detecting the matching points automatically
    try:
        mat, mask = cv2.findHomography(selected_ima_kp, selected_imb_kp, cv2.RANSAC)
    except Exception:
        raise ValueError("The images does not match")

    if mat is None:
        raise ValueError("The images does not match")

    return mat, ima_kp, imb_kp
