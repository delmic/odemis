# Get the streams
# Calculate the transformation matrix
# Get 4 values for each channel
# update the streams
# save it
# display in the analysis tab

# -*- coding: utf-8 -*-
'''
Created on 10 April 2017

@author: Guilherme Stiebler

Gives ability to automatically place a EM image so that it's aligned with
another one (already present).

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''
import copy

import cv2
import numpy
from libtiff import TIFF
from scipy.spatial.distance import cdist

from odemis import model
from odemis.acq.acqmng import SettingsObserver, acquireZStack
from odemis.acq.stream import StaticFluoStream, FluoStream
from odemis.gui.plugin import Plugin
from odemis.model import DataArray
from odemis.util.comp import generate_zlevels
from odemis.util.transform import AffineTransform


def read_tiff_image(file_path):
    tif = TIFF.open(file_path, mode='r')
    image = tif.read_image()
    return image


def process_image(image):
    # Check the number of channels in the image
    if len(image.shape) == 2:  # Grayscale image
        gray = image
    elif len(image.shape) == 3 and image.shape[2] == 3:  # BGR image
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("Unexpected image format!")

    # Apply threshold
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    # Ensure the binary image is of type CV_8UC1
    binary = binary.astype(numpy.uint8)
    # Find contours
    contours, _ = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    centers = []
    for contour in contours:
        # Calculate the bounding box for each contour
        x, y, w, h = cv2.boundingRect(contour)
        # Filter out small contours
        if w >= 10 and h >= 10:
            # Calculate moments for each contour
            M = cv2.moments(contour)
            if M['m00'] != 0:
                cX = int(M['m10'] / M['m00'])
                cY = int(M['m01'] / M['m00'])
                centers.append((cX, cY))
    return binary, centers


def find_corresponding_points(centers1, centers2, max_distance):
    """
    Find corresponding points in two sets of points that are within a specified maximum distance.

    Args:
        centers1 (np.ndarray): Nx2 array of points (x, y) in the first image.
        centers2 (np.ndarray): Mx2 array of points (x, y) in the second image.
        max_distance (float): Maximum allowable distance for point pairs.

    Returns:
        list of tuples: Corresponding point pairs (index1, index2).
    """
    # Calculate pairwise distances
    distances = cdist(centers1, centers2)

    # Find pairs within the max_distance
    pairs = numpy.argwhere(distances <= max_distance)

    return pairs


class ChromaticCorrectionPlugin(Plugin):
    name = "Chromatic Correction"
    __version__ = "0.1"
    __author__ = u"Karishma Kumar"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super(ChromaticCorrectionPlugin, self).__init__(microscope, main_app)
        self.addMenu("Data correction/Lateral chromatic correction", self.start)

    def start(self):
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        # stream_source  green
        self.new = []
        self.ref = []
        centers_ref = []
        centers_new = []
        levels = generate_zlevels(tab_data.main.focus,
                                  (-10e-06, 10e-06),
                                  5e-06)
        mip_image = read_tiff_image('/home/dev/Downloads/FOV1_green.tif')
        self.ref = mip_image
        _, centers_ref = process_image(mip_image)
        # zlevels = {stream: levels}
        non_static = [s for s in tab_data.streams.value
                      if isinstance(s, FluoStream)]
        zlevels = {s: levels for s in non_static}
        settings_observer = SettingsObserver(model.getComponents())
        acqui_task = acquireZStack(non_static, zlevels, settings_obs=settings_observer)
        das, e = acqui_task.result()
        for i, stream in enumerate(tab_data.streams.value):
            if isinstance(stream, StaticFluoStream):
                mip_image = DataArray(mip_image, copy.copy(das[0].metadata))
                # mip_image = numpy.amax(das, axis=0)
                # dataio.tiff.export(f"tiff_secondary{i}.tiff", mip_image)
                if stream.tint.value == (0, 255, 0):
                    self.ref = mip_image
                    # _, centers_ref = process_image(self.ref)
                else:
                    self.new.append(mip_image)
                    # _, centers_new = process_image(mip_image)
                    mip_image = read_tiff_image('/home/dev/Downloads/FOV1_red.tif')
                    _, centers_new = process_image(mip_image)
                    if True:  # self.ref:
                        # Find corresponding points
                        centers1 = numpy.array(centers_ref)
                        centers2 = numpy.array(centers_new)
                        max_distance = 10 * numpy.sqrt(2)

                        corresponding_pairs = find_corresponding_points(centers1, centers2, max_distance)

                        # Extract the matching points
                        points1 = centers1[corresponding_pairs[:, 0]]
                        points2 = centers2[corresponding_pairs[:, 1]]

                        # Estimate the transformation matrix
                        affine2 = AffineTransform.from_pointset(points1, points2)
                        # Create a 3x3 identity matrix
                        mat = numpy.eye(3)
                        mat[:2, :2] = affine2.matrix
                        mat[:2, 2] = affine2.translation
                        ref_mat = numpy.eye(3)
                        mat = ref_mat @ mat
                        # Translation is the last column
                        tx = mat[0, 2]
                        ty = mat[1, 2]
                        translation = numpy.array([tx, ty])

                        # Scale is the norm of the first two columns
                        sx = numpy.linalg.norm(mat[:2, 0])
                        sy = numpy.linalg.norm(mat[:2, 1])
                        scale2 = numpy.array([sx, sy])

                        # Compute rotation
                        rotation = numpy.arctan2(mat[1, 0] / sx, mat[0, 0] / sx)

                        # update metadata
                        # set MD_PIXEL_SIZE_COR to 1/scale2
                        stream.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = (1 / scale2[0], 1 / scale2[1])

                        # set MD_POS_COR to translation
                        # sem_pos = sem_stream.raw[0].metadata[model.MD_POS]
                        pixel_size = stream.raw[0].metadata[model.MD_PIXEL_SIZE]
                        stream.raw[0].metadata[model.MD_POS_COR] = (tx * pixel_size[0], -ty * pixel_size[1])

                        # set MD_ROTATION_COR to rotation
                        stream.raw[0].metadata[model.MD_ROTATION_COR] = -1  # rotation

                        # Add a new stream panel (removable)
                        analysis_tab = self.main_app.main_data.getTabByName('analysis')
                        aligned_stream = StaticFluoStream(stream.name.value, stream.raw[0])
                        scont = analysis_tab.stream_bar_controller.addStream(aligned_stream, add_to_view=True)
                        scont.stream_panel.show_remove_btn(True)
