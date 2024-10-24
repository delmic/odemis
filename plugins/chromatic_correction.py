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
from concurrent.futures._base import CancelledError
from datetime import datetime
from typing import Dict, Any

import numpy
import wx
import yaml
from scipy.spatial.distance import cdist

from odemis import model, dataio
from odemis.acq.acqmng import acquireZStack
from odemis.acq.stream import FluoStream
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import get_home_folder, call_in_wx_main
from odemis.model import DataArray
from odemis.util.comp import generate_zlevels
from odemis.util.linalg import ranges_overlap
from odemis.util.spot import MaximaFind
from odemis.util.transform import AffineTransform, alt_transformation_matrix_to_implicit

# Below values are based on one experiment dataset, needs more testing
# Diameter might need to be adapted if the sample or the objective is changed
# Objective of 50x|0.5 na was used
# Actual bead size used was 200nm
ACTUAL_BEAD_DIAMETER = 200e-9  # m
# Based on diffraction, bead size in the image appears bigger which is used in calculation for finding and matching
# bead centers. The actual bead diameter is multiplied by a scaling factor
# The scaling is based on the provided dataset
SCALE = 5.5
# The maximum tolerance accepted to find corresponding beads in two images
# TOLERANCE is multiplied with the bead diameter (in pixels) seen in the image
TOLERANCE = 1.5
# Maximum number of beads to find in the given image
BEAD_QUANTITY = 15
# Settings to acquire z stack of given channels
Z_RANGE = (-10e-06, 10e-06)  # m
Z_STEP_SIZE = 50e-09  # m
REF_EMISSION = [500.e-9, 530.e-9]  # m, green channel wavelength


def find_corresponding_points(centers_1: numpy.ndarray, centers_2: numpy.ndarray, max_distance: float) -> numpy.ndarray:
    """
    Find corresponding points in two sets of points that are within a specified maximum distance.
    :param centers_1: (in pixels) Nx2 array of points (x, y) in the first image.
    :param centers_2: (in pixels) Mx2 array of points (x, y) in the second image.
    :param max_distance: (float in pixels) Maximum allowable distance to match corresponding points in first image to
     second image
    :returns:
        pairs: Corresponding point pairs (index1, index2).
    """
    # Calculate pairwise distances
    distances = cdist(centers_1, centers_2)

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


def get_file_location(filename: str, ext: str) -> str:
    """Get the file location by adding the directory and date time"""
    dir_path = os.path.join(get_home_folder(), "Documents/Chromatic aberration correction")
    os.makedirs(dir_path, exist_ok=True)
    current_date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_location = os.path.join(dir_path, f"{current_date}_{filename}.{ext}")
    return file_location


def convert_transformation_to_dict(scale: [float, float], translation: [float, float], rotation: float,
                                   shear: float) -> Dict[str, Any]:
    """
    Convert transformation parameters to dictionary.
    :param scale: scale correction
    :param translation: translation correction
    :param rotation: rotation correction
    :param shear: shear correction
    :return: transformation values of the given data
    """
    # To enable chromatic correction, add MD_CHROMATIC_COR
    # under the filter wheel component of the microscope file
    correction_dict = {
        model.MD_PIXEL_SIZE_COR: [float(scale[0]), float(scale[1])],
        model.MD_POS_COR: [float(translation[0]), float(translation[1])],
        model.MD_ROTATION_COR: float(rotation),
        model.MD_SHEAR_COR: float(shear)
    }

    return correction_dict


def get_chromatic_correction_dict(im: DataArray, points_ref: numpy.ndarray, points_im: numpy.ndarray) -> Dict[str, Any]:
    """
    Get the chromatic correction dictionary for the given data array.
    :param im: static image values including the metadata
    :param points_ref: (x, y) location of features in reference data in pixels
    :param points_im: (x, y) location of features in given data by im in pixels
    :return: transformation values of the given data
    """
    affine = AffineTransform.from_pointset(points_ref, points_im)
    # Estimate the transformation matrix
    # With odemis.util.transform, affine parameters like scale, rotation and shear can be calculated in three
    # different ways.
    # 1) using affine transform using its attributes, such as affine.shear, affine.scale and affine.rotation. However,
    # shear is assumed to be only in y direction, the scale is defined such that it has equal affect in x and y. Due to
    # these definitions affine transform attributes are not suitable for this application
    # 2) alt_transformation_matrix_to_implicit(affine.matrix, "RSL") using the RSL form. When there is shear in the
    # data, this method does not provide the desired result
    # 3) desired result is given be alt_transformation_matrix_to_implicit(affine.matrix, "RSU") using the RSU form as
    # the shear is defined in x direction, scale is defined for both x and y, rotation is defined w.r.t to x direction.
    # This is the way Odemis reads the correction metadata and is suitable for this application
    scale, rotation, shear = alt_transformation_matrix_to_implicit(affine.matrix, "RSU")
    translation = affine.translation
    # Convert translation from pixels to m/physical units
    # negative sign is used to invert the y-axis in image coordinates which is pointing downwards
    # to the physical axis which is pointing upwards
    pixel_size = im.metadata[model.MD_PIXEL_SIZE]
    translation = [translation[0] * pixel_size[0], - translation[1] * pixel_size[1]]

    correction_dict = convert_transformation_to_dict(scale, translation, rotation, shear)
    logging.debug(f"Chromatic transformation: {correction_dict}")

    return correction_dict


class ChromaticCorrectionPlugin(Plugin):
    name = "Chromatic Correction"
    __version__ = "1.0"
    __author__ = "Karishma Kumar"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        if "cryosecom-localization" in main_app.main_data.tab.choices.values():
            super().__init__(microscope, main_app)
            # Green channel is selected as reference channel, as it lies in the middle of the spectrum
            # All the other channels will be corrected according to the reference colored channel
            self.dlg = None
            self.acqui_task = None
            self.cancelled = False  # tracks the cancellation event invoked by the user
            self.addMenu("Data correction/Lateral chromatic correction", self.open_dialog_window)

    def display_static_stream(self, file_location: str, data: model.DataArray):
        """
        Save the given data, read and display in the analysis tab
        :param file_location: file_location of the data array
        :param data: value and metadata of the given data array
        """
        # Save the image
        dataio.tiff.export(file_location, data)
        # Read the saved images and display
        analysis_tab = self.main_app.main_data.getTabByName('analysis')
        self.main_app.main_data.tab.value = analysis_tab
        analysis_tab.load_data(file_location, extend=True)

    def find_and_display_transformation(self, mip_das_dict: Dict[float, DataArray], max_dist_pxl: int,
                                        ref_em_index: float) -> (Dict[str, Dict[str, Any]], str):
        """
        Compute affine transformation between a reference channel and another coloured channel. If successful in
        correction, display the corrected acquired streams in the Analysis tab.
        :param mip_das_dict: data arrays identified with filter band position
        :param max_dist_pxl: maximum distance to find the beads in the given image
        :param ref_em_index: reference channel identifier in the given data arrays
        :return: transformation values of each channel iterated over filter band position
         which is in radians and status of chromatic correction completion
        """
        transformation_dict = {model.MD_CHROMATIC_COR: {},
                               "Extra Information": {}}
        pos_ref = MaximaFind(mip_das_dict[ref_em_index], BEAD_QUANTITY, max_dist_pxl)
        # Introduce default values for reference channel
        transformation_dict[model.MD_CHROMATIC_COR][ref_em_index] = convert_transformation_to_dict(scale=[1, 1],
                                                                                                   translation=[0, 0],
                                                                                                   rotation=0, shear=0)
        mip_das_dict[ref_em_index].metadata.update(transformation_dict[model.MD_CHROMATIC_COR][ref_em_index])

        # Update the metadata and display all channels in the Analysis Tab
        channels_nu = []
        for band_pos, da in mip_das_dict.items():
            # The transformation values are only needed for channels other than the reference channel
            # as the transformation values for reference channel is set to default
            if band_pos != ref_em_index:
                pos = MaximaFind(da, BEAD_QUANTITY, max_dist_pxl)
                # Extract the matching points #ndarray nx2
                corresponding_pairs = find_corresponding_points(pos_ref, pos, max_dist_pxl * TOLERANCE)

                if len(corresponding_pairs) < 4:
                    channels_nu.append(da.metadata[model.MD_OUT_WL].join)
                    continue

                points1 = pos_ref[corresponding_pairs[:, 0]]
                points2 = pos[corresponding_pairs[:, 1]]

                # Compute transformation between reference channel and other channel
                transformation_dict[model.MD_CHROMATIC_COR][band_pos] = get_chromatic_correction_dict(da, points1,
                                                                                                      points2)
                da.metadata.update(transformation_dict[model.MD_CHROMATIC_COR][band_pos])

            # Extra information saved for tracking/debugging purpose
            input_wl = da.metadata[model.MD_IN_WL]
            output_wl = da.metadata[model.MD_OUT_WL]
            transformation_dict["Extra Information"][band_pos] = {
                "tint": list(da.metadata[model.MD_USER_TINT]),
                "excitation": [float(input_wl[0]), float(input_wl[1])],
                "emission": [float(output_wl[0]), float(output_wl[1])],
                "exposure time": float(da.metadata[model.MD_EXP_TIME]),
                "light power": float(da.metadata[model.MD_LIGHT_POWER])
            }

            # Save the modified image and display
            file_location = get_file_location(
                f"{da.metadata[model.MD_DESCRIPTION]}_{da.metadata[model.MD_OUT_WL]}_modified_metadata",
                "ome.tiff")
            self.display_static_stream(file_location, da)

        if len(channels_nu) > 0:
            all_channels_nu = ",".join(channels_nu)
            txt = ('The chromatic correction did not happen for channel with emission %s. Skipping this channel.\n\n'
                   'Alternatively, navigate to location with more beads or '
                   'change the bead diameter in the script'
                   % all_channels_nu)
            logging.warning(
                f"Chromatic correction unsuccessful for channel with {all_channels_nu}")
        else:
            txt = 'Chromatic correction is completed'

        return transformation_dict, txt

    def open_dialog_window(self):
        """Opens a dialog window which shows start button if all the specific requirements are present
        otherwise suggests changing the settings"""
        localization_tab = self.main_app.main_data.getTabByName("cryosecom-localization")
        tab_data = localization_tab.tab_data_model
        all_emission = [fms.emission.value for fms in
                        tab_data.streams.value if isinstance(fms, FluoStream)]
        # reference channel should be present in the present streams
        check_ref_channel = any(ranges_overlap(REF_EMISSION, emission) for emission in
                                all_emission)
        # No two streams should have same emission
        unique_elements = set(map(tuple, all_emission))
        check_unique_channels = len(unique_elements) == len(all_emission)

        if (len(tab_data.streams.value) > 1) and check_ref_channel and check_unique_channels:
            self.dlg = AcquisitionDialog(self, self.name,
                                         ("Ready to start chromatic aberration correction on the given channel.\n"
                                          "The process will take about 5-10 minutes\n\n"
                                          "z stack range is {} m\n"
                                          "z step size of {} m".format(Z_RANGE, Z_STEP_SIZE)))
            self.dlg.Bind(wx.EVT_CLOSE, self.on_close)
            self.dlg.addButton("Start", self._on_start, face_colour='blue')

            self.dlg.ShowModal()
            self.dlg.Destroy()
        else:
            txt = ("Troubleshooting :\n\n1. Add minimum of two streams.\n2. One of the streams must be green channel.\n"
                   "3. No two streams should be the same.")
            box = wx.MessageDialog(self.main_app.main_frame,
                                   txt, self.name, wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()

    @call_in_wx_main
    def on_close(self, dlg):
        self.dlg.buttons[0].SetLabel("Cancelling...")
        self.dlg.buttons[0].Disable()
        self.dlg.lbl_description.SetLabel("Cancellation in progress\nMay take some time to "
                                          "cancel the already running processes")
        self.dlg.EndModal(wx.ID_CANCEL)
        if self.acqui_task:
            self.acqui_task.cancel()
        logging.debug("Dialog closed")

    def _on_start(self, dlg):
        """Start or cancel the correction method when the button is clicked"""
        # If correction is running, cancel it, otherwise start one
        if dlg.buttons[0].Label == "Start":
            self.cancelled = False
            self._start_chromatic_correction(dlg)
        else:
            self.on_close(None)

    @call_in_wx_main
    def _on_acquisition_done(self, future):
        try:
            das, _ = future.result()
            mip_das_dict = {}  # keys are filter band positions and values are mip data arrays
            ref_em_pos = None  # reference emission position of the reference channel
            for da in das:
                mip_image = model.DataArray(numpy.amax(da, axis=0), copy.copy(da.metadata))
                file_location = get_file_location(f"{da.metadata[model.MD_DESCRIPTION]}_{da.metadata[model.MD_OUT_WL]}",
                                                  "ome.tiff")
                dataio.tiff.export(file_location, mip_image)
                em_index = self.streams_list[0]._emission_to_idx[tuple(da.metadata[model.MD_OUT_WL])]
                mip_das_dict[em_index] = mip_image
                if ranges_overlap(REF_EMISSION, da.metadata[model.MD_OUT_WL]):
                    ref_em_pos = em_index

            self.dlg.lbl_description.SetLabel(
                "In progress\n - Computing transformation between reference and other channels")
            max_dist = ACTUAL_BEAD_DIAMETER * SCALE  # size of bead in the image
            # convert m to pixel units
            max_dist_pxl = int(
                max_dist / mip_das_dict[ref_em_pos].metadata[model.MD_PIXEL_SIZE][0])

            # Find transformation values per channel
            if len(mip_das_dict) > 1:
                transformation_dict, status = self.find_and_display_transformation(mip_das_dict, max_dist_pxl,
                                                                                   ref_em_pos)
                self.dlg.lbl_description.SetLabel(status)
            else:
                logging.debug(f"Chromatic aberration failed to execute due to incompatible data: {mip_das_dict}")
                return

            # Save the transformation if it was estimated in channels other than the reference channel
            if len(transformation_dict[model.MD_CHROMATIC_COR]) > 1:
                file_location = get_file_location("lens_chromatic_correction", "odm.yaml")
                with open(file_location, 'w') as yaml_file:
                    yaml.dump(transformation_dict, yaml_file, default_flow_style=False, sort_keys=False)

        except CancelledError:
            logging.debug("Chromatic aberration is cancelled during Z stack acquisition")
            return

    def _start_chromatic_correction(self, dlg):
        # Change the Start button to Close
        dlg.buttons[0].SetLabel("Close")
        localization_tab = self.main_app.main_data.getTabByName("cryosecom-localization")
        tab_data = localization_tab.tab_data_model
        self.dlg.lbl_description.SetLabel("In progress\n - Acquiring z stack of the given channels")
        # Acquire z levels for the given streams
        levels = generate_zlevels(tab_data.main.focus, Z_RANGE, Z_STEP_SIZE)
        self.streams_list = [s for s in tab_data.streams.value if isinstance(s, FluoStream)]
        zlevels = {stream: levels for stream in self.streams_list}
        settings_observer = tab_data.main.settings_obs
        self.acqui_task = acquireZStack(self.streams_list, zlevels, settings_obs=settings_observer)
        # Compute transformation parameters between the given channels
        self.acqui_task.add_done_callback(self._on_acquisition_done)
