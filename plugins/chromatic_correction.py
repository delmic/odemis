# -*- coding: utf-8 -*-
"""
Created on June 13, 2024

@author: Karishma Kumar

Gives ability to provide charomatic correction in x and y based on the given streams in the
localisation tab.

Copyright © 2024 Karishma Kumar, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import copy

import cv2
import numpy
import wx
import yaml
from scipy.spatial.distance import cdist

from odemis import model, dataio
from odemis.acq.acqmng import acquireZStack
from odemis.acq.stream import FluoStream
from odemis.gui.plugin import Plugin
from odemis.util.comp import generate_zlevels
from odemis.util.transform import AffineTransform

BEAD_DIAMETER = 10 * numpy.sqrt(2)  # the length of the square that fits in the bead circle


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
    # size of the square based on diameter
    length = BEAD_DIAMETER / numpy.sqrt(2)
    for contour in contours:
        # Calculate the bounding box for each contour
        x, y, w, h = cv2.boundingRect(contour)
        # Filter out small contours
        if w >= length and h >= length:
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

    :param centers1: (np.ndarray) Nx2 array of points (x, y) in the first image.
    :param centers2: (np.ndarray) Mx2 array of points (x, y) in the second image.
    :param max_distance: (float) Maximum allowable distance for point pairs.
    :returns:
        pairs: (list of tuples) Corresponding point pairs (index1, index2).
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

    def show_static_stream(self, filename: str, data: model.DataArray):
        """
        Save the given data, read and display in the analysis tab
        :param filename: filename of the data array
        :param data: value and metadata of the given data array
        """
        # save the image
        dataio.tiff.export(filename, data)
        # Read the saved images and display
        analysis_tab = self.main_app.main_data.getTabByName('analysis')
        self.main_app.main_data.tab.value = analysis_tab
        analysis_tab.load_data(filename, extend=True)

    def start(self):
        localization_tab = self.main_app.main_data.getTabByName("cryosecom-localization")
        tab_data = localization_tab.tab_data_model
        other_mip_streams = []
        reference_mip_stream = []
        levels = generate_zlevels(tab_data.main.focus,
                                  (-10e-06, 10e-06),
                                  5e-06)
        non_static = [s for s in tab_data.streams.value
                      if isinstance(s, FluoStream)]
        zlevels = {s: levels for s in non_static}
        settings_observer = tab_data.main.settings_obs
        acqui_task = acquireZStack(non_static, zlevels, settings_obs=settings_observer)
        das, e = acqui_task.result()

        if len(das) <= 1:
            box = wx.MessageDialog(self.main_app.main_frame,
                                   "Add minimum two streams. One of the streams must be green channel.",
                                   "Failed to do chromatic correction", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # Calculate Maximum Intensity projection for all the present channels in the stream panel
        for i, da in enumerate(das):
            mip_image = model.DataArray(numpy.amax(da, axis=0), copy.copy(da.metadata))
            dataio.tiff.export(f"{da.metadata[model.MD_DESCRIPTION]}_{i}", mip_image)
            # All the channels will be corrected according to a reference colored channel
            # green channel is selected as reference channel, Why? -> Ask Deniz
            if da.metadata[model.MD_USER_TINT] == (0, 255, 0):
                reference_mip_stream = mip_image
            else:
                other_mip_streams.append(mip_image)

        # Save and display the reference channel in the analysis tab
        self.show_static_stream(f"modified_{reference_mip_stream.metadata[model.MD_DESCRIPTION]}_ref",
                                reference_mip_stream)

        # Compute transformation between reference channel and other channels
        trans_per_channel = {}  # stores transformation values per channel
        _, centers_ref = process_image(reference_mip_stream)
        centers_ref = numpy.array(centers_ref)
        for i, im in enumerate(other_mip_streams):
            # Find corresponding points
            _, centers_new = process_image(im)
            centers_new = numpy.array(centers_new)
            max_distance = BEAD_DIAMETER
            corresponding_pairs = find_corresponding_points(centers_ref, centers_new, max_distance)

            # Extract the matching points
            points1 = centers_ref[corresponding_pairs[:, 0]]
            points2 = centers_new[corresponding_pairs[:, 1]]

            if len(corresponding_pairs) >= 4:
                # Estimate the transformation matrix
                affine = AffineTransform.from_pointset(points1, points2)
                mat = numpy.eye(3)
                mat[:2, :2] = affine.matrix
                mat[:2, 2] = affine.translation
                ref_mat = numpy.eye(3)
                mat = ref_mat @ mat

                # Scale is the norm of the first two columns
                sx = numpy.linalg.norm(mat[:2, 0])
                sy = numpy.linalg.norm(mat[:2, 1])
                scale2 = numpy.array([sx, sy])

                # Compute rotation
                rotation = numpy.arctan2(mat[1, 0] / sx, mat[0, 0] / sx)

                # compute scale
                scale = (1 / scale2[0], 1 / scale2[1])

                # compute shear
                a, b = mat[0, 0], mat[0, 1]
                c, d = mat[1, 0], mat[1, 1]
                shear = numpy.arctan2(a * c + b * d, scale[0] * scale[1])

                # compute translation
                tx = mat[0, 2]
                ty = mat[1, 2]
                pixel_size = im.metadata[model.MD_PIXEL_SIZE]
                translation = (tx * pixel_size[0], -ty * pixel_size[1])

                # stream settings
                input_wl = im.metadata[model.MD_IN_WL]
                output_wl = im.metadata[model.MD_OUT_WL]

                trans_per_channel[f"channel_{i}"] = {
                    "tint": list(im.metadata[model.MD_USER_TINT]),
                    "scale_cor": [float(scale[0]), float(scale[1])],
                    "translation_cor": [float(translation[0]), float(translation[1])],
                    "rotation_cor": float(rotation),
                    "shear_cor": float(shear),
                    "excitation": [float(input_wl[0]), float(input_wl[1])],
                    "emission": [float(output_wl[0]), float(output_wl[1])],
                    "exposure time": float(im.metadata[model.MD_EXP_TIME]),
                    "light power": float(im.metadata[model.MD_LIGHT_POWER])
                }

                # update metadata
                im.metadata.update({model.MD_PIXEL_SIZE_COR: scale})
                im.metadata.update({model.MD_POS_COR: translation})
                im.metadata.update({model.MD_ROTATION_COR: rotation})
                im.metadata.update({model.MD_SHEAR_COR: shear})

                # save the image and display
                self.show_static_stream(f"modified_{im.metadata[model.MD_DESCRIPTION]}_{i}", im)

        # TODO : decide the file location of transformation values
        with open('transformation_per_channel.yaml', 'w') as yaml_file:
            yaml.dump(trans_per_channel, yaml_file, default_flow_style=False)
