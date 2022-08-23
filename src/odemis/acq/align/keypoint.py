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

import logging
import numpy
import cv2
from scipy import ndimage
from odemis import model
from odemis.util import img

if int(cv2.__version__[0]) <= 2:
    cv2.ORB_create = cv2.ORB
    # Sift is not installed by default, check first if it's available
    if hasattr(cv2, 'SIFT'):
        cv2.SIFT_create = cv2.SIFT

# The brute-force matcher works in theory a bit better than the Flann-based one,
# but slower. In practice, it doesn't seem to show better results, and if they
# are many keypoints (eg, 2000) the slow-down can be a couple of seconds.
USE_BF = False  # Use BruteForce matcher
USE_KNN = True  # Use k-nearest neighbour matching method

# Missing defines from OpenCV
FLANN_INDEX_LINEAR = 0
FLANN_INDEX_KDTREE = 1
FLANN_INDEX_KMEANS = 2
FLANN_INDEX_LSH = 6

def FindTransform(ima, imb, fd_type=None):
    """
    ima(DataArray of shape YaXa with uint8): Image to be aligned
    imb(DataArray of shape YbXb with uint8): Base image
        Note that the shape doesn't have to be any relationship with the shape of the
        first dimension(doesn't even need to be the same ratio)
    fd_type(None or str): Feature detector type. Must be 'SIFT' or 'ORB'. ORB is faster,
        but SIFT usually has better results. If None, it will pick the best available.
    return (ndarray of shape 3, 3): transformation matrix to align the first image on the
        base image. (right column is translation)
    raises:
    ValueError: if no good transformation is found.
    """

    # Instantiate the feature detector and the matcher
    # TODO: try BRISK, AZAKE and other detectors?
    if fd_type is None:
        for fd in ("SIFT", "ORB"):
            if hasattr(cv2, "%s_create" % fd):
                fd_type = fd
                break

    if fd_type == "ORB":
        feature_detector = cv2.ORB_create()
        if USE_BF:
            matcher = cv2.BFMatcher(normType=cv2.NORM_HAMMING)
        else:
            index_params = dict(algorithm=FLANN_INDEX_LSH,
                                table_number=6,  # 12
                                key_size=12,  # 20
                                multi_probe_level=1)  # 2
            search_params = {}
            matcher = cv2.FlannBasedMatcher(index_params, search_params)
    elif fd_type == "SIFT":
        # Extra arguments for SIFT
#         contrastThreshold = 0.04
#         edgeThreshold = 10
#         sigma = 1.6  # TODO: no need for Gaussian as preprocess already does it?
        feature_detector = cv2.SIFT_create(nfeatures=2000)  # avoid going crazy on keypoints
        if USE_BF:
            matcher = cv2.BFMatcher(normType=cv2.NORM_L2)
        else:
            # Note: with KDTree, every call returns slightly different matches,
            # which is quite annoying for reproducibility
#             index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
            index_params = dict(algorithm=FLANN_INDEX_KMEANS)
            search_params = dict(checks=32)  # default value
            matcher = cv2.FlannBasedMatcher(index_params, search_params)
    else:
        raise ValueError("Unknown feature detector %s" % (fd_type,))

    logging.debug("Using feature detector %s", fd_type)

    # find and compute the descriptors
    ima_kp, ima_des = feature_detector.detectAndCompute(ima, None)
    imb_kp, imb_des = feature_detector.detectAndCompute(imb, None)
    logging.debug("Found %d and %d keypoints", len(ima_kp), len(imb_kp))

    # run the matcher of the detected features
    if USE_KNN:
        # For each keypoint, return up to k(=2) best ones in the other image
        matches = matcher.knnMatch(ima_des, imb_des, k=2)

        # store all the good matches as per Lowe's ratio test
        dist_ratio = 0.75
        selected_matches = [m[0] for m in matches
                            if len(m) == 2 and m[0].distance < m[1].distance * dist_ratio]
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
