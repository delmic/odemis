# -*- coding: utf-8 -*-
"""
Created on 8 Jan 2014

@author: kimon

Copyright © 2013-2014 Éric Piel & Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from __future__ import division

import logging
import numpy
import math
import cv2
import scipy.ndimage as ndimage
import scipy.ndimage.filters as filters

from odemis import model
from odemis.dataio import hdf5
from scipy import misc
from numpy import unravel_index

def GuessAnchorRegion(whole_img, sample_region):
    """
    It detects a region with clean edges, proper for drift measurements. This region 
    must not overlap with the sample that is to be scanned due to the danger of 
    contamination.
    whole_img (ndarray): 2d array with the whole SEM image
    sample_region (tuple of 4 floats): roi of the sample in order to avoid overlap
    returns (tuple of 4 floats): roi of the anchor region
    """
    # Drift correction region shape
    dc_shape = (50, 50)

    # Properly modified image for cv2.Canny
    uint8_img = misc.bytescale(whole_img)
    hdf5.export("uint8_img.h5", model.DataArray(uint8_img), thumbnail=None)

    # Generates black/white image that contains only the edges
    cannied_img = cv2.Canny(uint8_img, 100, 200)
    hdf5.export("cannied.h5", model.DataArray(cannied_img), thumbnail=None)

    # Mask the sample_region plus a margin equal to the half of dc region and
    # a margin along the edges of the whole image again equal to the half of
    # the anchor region. Thus we keep pixels that we can use as center of our
    # anchor region knowing that it will not overlap with the sample region
    # and it will not be outside of bounds
    masked_img = cannied_img
    masked_img[sample_region[0] * whole_img.shape[1] - (dc_shape[0] / 2):sample_region[2] * whole_img.shape[1] + (dc_shape[0] / 2),
               sample_region[1] * whole_img.shape[0] - (dc_shape[1] / 2):sample_region[3] * whole_img.shape[0] + (dc_shape[1] / 2)].fill(0)
    masked_img[0:(dc_shape[0] / 2), :].fill(0)
    masked_img[:, 0:(dc_shape[1] / 2)].fill(0)
    masked_img[masked_img.shape[0] - (dc_shape[0] / 2):masked_img.shape[0], :].fill(0)
    masked_img[:, masked_img.shape[1] - (dc_shape[1] / 2):masked_img.shape[1]].fill(0)
    hdf5.export("masked.h5", model.DataArray(masked_img), thumbnail=None)

    # Find indices of edge pixels
    occurrences_indices = numpy.where(masked_img == masked_img.max())
    X = numpy.matrix(occurrences_indices[0]).T
    Y = numpy.matrix(occurrences_indices[1]).T
    occurrences = numpy.hstack([X, Y])

    # If there is such a pixel outside of the sample region and there is enough 
    # space according to dc_shape, use the masked image and calculate the anchor
    # region roi
    if len(occurrences) > 0:
        # [x, y] = [occurrences[0]]
        anchor_roi = ((occurrences[0, 0] - (dc_shape[1] / 2)) / whole_img.shape[1],
                      (occurrences[0, 1] - (dc_shape[0] / 2)) / whole_img.shape[0],
                      (occurrences[0, 0] + (dc_shape[1] / 2)) / whole_img.shape[1],
                      (occurrences[0, 1] + (dc_shape[0] / 2)) / whole_img.shape[0])
        
        print anchor_roi
        cannied_img = cv2.Canny(uint8_img, 100, 200)
        anchor_img = cannied_img[anchor_roi[0] * whole_img.shape[1]:anchor_roi[2] * whole_img.shape[1],
                                 anchor_roi[1] * whole_img.shape[0]:anchor_roi[3] * whole_img.shape[0]]
        hdf5.export("anchor_roi.h5", model.DataArray(anchor_img), thumbnail=None)
    else:
        return

    return anchor_roi



