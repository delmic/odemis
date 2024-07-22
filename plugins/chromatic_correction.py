# -*- coding: utf-8 -*-
"""
Created on June 13, 2024

@author: Karishma Kumar

Gives ability to provide chromatic correction in x and y based on the given streams in the
localisation tab for Meteor. It corrects for chromatic aberration caused due to lenses and the optical path by
aligning different coloured channels with a reference coloured channel. For e.g. a bead emitting fluoroscence in all
channels would be displayed at the same location in all channels after chromatic correction.

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
import logging
import os

import numpy
import wx
import yaml
from scipy.spatial.distance import cdist

import odemis.gui.conf.file
from odemis import model, dataio
from odemis.acq.acqmng import acquireZStack
from odemis.acq.stream import FluoStream
from odemis.gui.plugin import Plugin
from odemis.util.comp import generate_zlevels
from odemis.util.spot import MaximaFind
from odemis.util.transform import AffineTransform

# Value is based on one experiment dataset, needs more testing
# Diameter might need to be adapted if the sample or the objective is changed
# Objective of 50x|0.5 na is used
# Actual bead size used was 200nm
ACTUAL_BEAD_DIAMETER = 200 * 1e-9  # m
# Based on diffraction, bead size in the image appears bigger which is used in calculation for finding and matching
# bead centers. The actual bead diameter is multiplied by a scaling factor
# The scaling is based on the provided dataset
SCALE = 5.5
# Maximum number of beads to find in the given image
BEAD_QUANTITY = 15


def find_corresponding_points(centers1, centers2, max_distance):
    """
    Find corresponding points in two sets of points that are within a specified maximum distance.
    :param centers1: (numpy.ndarray) Nx2 array of points (x, y) in the first image.
    :param centers2: (numpy.ndarray) Mx2 array of points (x, y) in the second image.
    :param max_distance: (float in pixels) Maximum allowable distance for point pairs.
    :returns:
        pairs: (numpy.ndarray) Corresponding point pairs (index1, index2).
    """
    # Calculate pairwise distances
    distances = cdist(centers1, centers2)

    # Find pairs within the max_distance
    all_pairs = numpy.argwhere(distances <= max_distance)
    sorted_pairs = sorted(all_pairs, key=lambda x: distances[x[0], x[1]])

    matched_1 = set()
    matched_2 = set()
    pairs = []

    for i, j in sorted_pairs:
        if i not in matched_1 and j not in matched_2:
            pairs.append([i, j])
            matched_1.add(i)
            matched_2.add(j)

    return numpy.array(pairs)


class ChromaticCorrectionPlugin(Plugin):
    name = "Chromatic Correction"
    __version__ = "1.0"
    __author__ = "Karishma Kumar"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        if "cryosecom-localization" in main_app.main_data.tab.choices.values():
            super().__init__(microscope, main_app)
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

    def compute_transformation(self, ref_channel, channels, max_distance):
        """
        Compute affine transformation between a reference channel and another coloured channel.
        :param ref_channel: (model.DataArray) The reference channels to which other channels are aligned
        :param channels: (list of model.DataArray) The channels are transformed to align with reference channel
        :param max_distance: (float in meters) The maximum distance accepted to match two same locations in between
         reference channel and channels
        """
        trans_per_channel = {model.MD_CHROMATIC_COR: {}}  # stores transformation values per channel
        dist_pxl = int(max_distance / ref_channel.metadata[model.MD_PIXEL_SIZE][0])  # convert m to pixel units
        centers_ref = MaximaFind(ref_channel, BEAD_QUANTITY, dist_pxl)

        for i, im in enumerate(channels):
            # Find corresponding points
            centers_new = MaximaFind(im, BEAD_QUANTITY, dist_pxl)
            corresponding_pairs = find_corresponding_points(centers_ref, centers_new, dist_pxl)

            # Extract the matching points
            points1 = centers_ref[corresponding_pairs[:, 0]]
            points2 = centers_new[corresponding_pairs[:, 1]]

            if len(corresponding_pairs) < 4:
                txt = ('The chromatic correction did not happen for channel with %s. Skipping this channel.\n\n'
                       'Alternatively, navigate to location with more beads or change the bead diameter in the script'
                       % im.metadata[model.MD_DESCRIPTION])
                box = wx.MessageDialog(self.main_app.main_frame,
                                       txt, "Partial failure of chromatic correction", wx.OK)
                box.ShowModal()
                box.Destroy()

            else:
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

                try:
                    fl_band = im.metadata[model.MD_EXTRA_SETTINGS]['Filter Wheel']['position'][0]['band']
                    # The below metadata dictionary will be added to the microscope file using input command
                    # under the filter wheel component, only if the metadata has Pixel size cor, Centre position cor,
                    # Rotation cor, and Shear cor for a minimum one filter wheel band position
                    trans_per_channel[model.MD_CHROMATIC_COR][fl_band] = {
                        "Pixel size cor": [float(scale[0]), float(scale[1])],
                        "Centre position cor": [float(translation[0]), float(translation[1])],
                        "Rotation cor": float(rotation),
                        "Shear cor": float(shear),
                        # Extra information saved for tracking/debugging purpose
                        "tint": list(im.metadata[model.MD_USER_TINT]),
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
                    self.show_static_stream(f"modified_{im.metadata[model.MD_DESCRIPTION]}_{i}.ome.tiff", im)
                except (ValueError, KeyError) as ex:
                    logging.error("Failed to compute chromatic aberration correction yaml file due to: %s", ex)

        return trans_per_channel

    def start(self):
        localization_tab = self.main_app.main_data.getTabByName("cryosecom-localization")
        tab_data = localization_tab.tab_data_model
        other_mip_streams = []
        reference_mip_stream = []

        if len(tab_data.streams.value) <= 1:
            box = wx.MessageDialog(self.main_app.main_frame,
                                   "Add minimum two streams. One of the streams must be green channel.",
                                   "Failed to do chromatic correction", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        levels = generate_zlevels(tab_data.main.focus,
                                  (-10e-06, 10e-06),
                                  5e-06)
        non_static = [s for s in tab_data.streams.value
                      if isinstance(s, FluoStream)]
        zlevels = {s: levels for s in non_static}
        settings_observer = tab_data.main.settings_obs
        acqui_task = acquireZStack(non_static, zlevels, settings_obs=settings_observer)
        das, e = acqui_task.result()

        # Calculate Maximum Intensity projection for all the present channels in the stream panel
        for i, da in enumerate(das):
            mip_image = model.DataArray(numpy.amax(da, axis=0), copy.copy(da.metadata))
            dataio.tiff.export(f"{da.metadata[model.MD_DESCRIPTION]}_{i}.ome.tiff", mip_image)
            # All the channels will be corrected according to a reference colored channel
            # green channel is selected as reference channel, as it lies in the middle of the spectrum
            if da.metadata[model.MD_USER_TINT] == (0, 255, 0):
                reference_mip_stream = mip_image
            else:
                other_mip_streams.append(mip_image)

        # Save and display the reference channel in the analysis tab
        self.show_static_stream(f"modified_{reference_mip_stream.metadata[model.MD_DESCRIPTION]}_ref.ome.tiff",
                                reference_mip_stream)

        # Compute transformation between reference channel and other channels
        max_distance = ACTUAL_BEAD_DIAMETER * SCALE  # size of bead in the image
        trans_per_channel = self.compute_transformation(reference_mip_stream, other_mip_streams, max_distance)

        if trans_per_channel:
            file_location = os.path.join(odemis.gui.conf.file.CONF_PATH, "lens-chromatic-correction.odm.yaml")
            with open(file_location, 'w') as yaml_file:
                yaml.dump(trans_per_channel, yaml_file, default_flow_style=False, sort_keys=False)
