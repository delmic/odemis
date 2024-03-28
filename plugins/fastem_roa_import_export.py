# -*- coding: utf-8 -*-
"""
Created on 13 December 2023

@author: Nandish Patel

Gives ability to use Import ROAs, Export ROAs tool under Help > Development.

Copyright © 2023 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import csv
from enum import Enum
import logging
from typing import List, Union
import wx

from odemis import util
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.gui.plugin import Plugin


class CSVFieldnames(Enum):
    PROJECT_NAME = "project_name"
    ROA_NAME = "roa_name"
    ROA_COORDINATES_L = "roa_coordinates_l"  # xmin (left)
    ROA_COORDINATES_T = "roa_coordinates_t"  # ymin (top)
    ROA_COORDINATES_R = "roa_coordinates_r"  # xmax (right)
    ROA_COORDINATES_B = "roa_coordinates_b"  # ymax (bottom)
    ROC_2_NAME = "roc_2_name"
    ROC_2_COORDINATES_L = "roc_2_coordinates_l"  # xmin (left)
    ROC_2_COORDINATES_T = "roc_2_coordinates_t"  # ymin (top)
    ROC_2_COORDINATES_R = "roc_2_coordinates_r"  # xmax (right)
    ROC_2_COORDINATES_B = "roc_2_coordinates_b"  # ymax (bottom)
    ROC_3_NAME = "roc_3_name"
    ROC_3_COORDINATES_L = "roc_3_coordinates_l"  # xmin (left)
    ROC_3_COORDINATES_T = "roc_3_coordinates_t"  # ymin (top)
    ROC_3_COORDINATES_R = "roc_3_coordinates_r"  # xmax (right)
    ROC_3_COORDINATES_B = "roc_3_coordinates_b"  # ymax (bottom)

    @staticmethod
    def tolist() -> List[str]:
        """CSVFieldnames enum class's list of values."""
        return [c.value for c in CSVFieldnames]


class ImportExportROAPlugin(Plugin):
    name = "Import Export ROA"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the FASTEM acquisition tab is present
        try:
            fastem_main_tab = main_app.main_data.getTabByName("fastem_main")
            self._acquisition_tab = fastem_main_tab.acquisition_tab
            self._cnvs = fastem_main_tab.vp.canvas
        except LookupError:
            logging.debug(
                "Not loading Import Export ROAs tool since acquisition tab is not present."
            )
            return

        self._project_list_controller = self._acquisition_tab._project_list_controller
        self._parent = self.main_app.GetTopWindow()

        self.addMenu("Help/Development/Import ROAs", self._import_roas)
        self.addMenu("Help/Development/Export ROAs", self._export_roas)

    def _get_csv_filepath_from_filedialog(self, message: str, style: int) -> Union[str, None]:
        """
        Get the CSV file's path from wx.FileDialog.

        :param: message: (str) The message to be shown in the filedialog.
        :param: style: (int) The style of the filedialog.

        :return: Union[str, None]: The chosen CSV file's path, if cancel is pressed return None.
        """
        with wx.FileDialog(
            parent=self._parent,
            message=message,
            wildcard="CSV files (*.csv)|*.csv",
            style=style,
        ) as filedialog:
            if filedialog.ShowModal() == wx.ID_CANCEL:
                return  # the user changed their mind
            return filedialog.GetPath()

    def _get_data_from_csv_file(self, filepath: str) -> dict:
        """Get data from the CSV file in a structured manner.

        :param: filepath: (str) The absolute CSV file's path.

        :returns: (dict) having a structure {"project_name": [roa]} where roa is a dict
            containing roa's, roc_2's, roc_3's name and coordinates respectively.

        """
        with open(filepath, newline="") as csvfile:
            data = {}
            reader = csv.DictReader(csvfile)
            for row in reader:
                roas = {}
                for fieldname in CSVFieldnames:
                    if fieldname == CSVFieldnames.PROJECT_NAME:
                        project_name = row[fieldname.value]
                    else:
                        roas[fieldname.value] = row[fieldname.value]
                if project_name in data:
                    data[project_name].append(roas)
                else:
                    data[project_name] = [roas]
            return data

    def _update_project_list(self, data: dict) -> None:
        """Update the project list based on data from the CSV file.

        :param: data: (dict) having a structure {"project_name": [roa]} where roa is a dict
            containing roa's, roc_2's, roc_3's name and coordinates respectively.

        """
        for project_name, roas in data.items():
            project_ctrl = self._project_list_controller._add_project(None, name=project_name)
            for roa in roas:
                roa_name = roa[CSVFieldnames.ROA_NAME.value]
                roa_coordinates = (
                    float(roa[CSVFieldnames.ROA_COORDINATES_L.value]),
                    float(roa[CSVFieldnames.ROA_COORDINATES_T.value]),
                    float(roa[CSVFieldnames.ROA_COORDINATES_R.value]),
                    float(roa[CSVFieldnames.ROA_COORDINATES_B.value]),
                )
                roc_2_name = roa[CSVFieldnames.ROC_2_NAME.value]
                roc_2_coordinates = (
                    float(roa[CSVFieldnames.ROC_2_COORDINATES_L.value]),
                    float(roa[CSVFieldnames.ROC_2_COORDINATES_T.value]),
                    float(roa[CSVFieldnames.ROC_2_COORDINATES_R.value]),
                    float(roa[CSVFieldnames.ROC_2_COORDINATES_B.value]),
                )
                roc_3_name = roa[CSVFieldnames.ROC_3_NAME.value]
                roc_3_coordinates = (
                    float(roa[CSVFieldnames.ROC_3_COORDINATES_L.value]),
                    float(roa[CSVFieldnames.ROC_3_COORDINATES_T.value]),
                    float(roa[CSVFieldnames.ROC_3_COORDINATES_R.value]),
                    float(roa[CSVFieldnames.ROC_3_COORDINATES_B.value]),
                )
                project_ctrl.regions_calib_2.value[int(roc_2_name)].name.value = roc_2_name
                project_ctrl.regions_calib_2.value[int(roc_2_name)].coordinates.value = (
                    roc_2_coordinates
                )
                project_ctrl.regions_calib_3.value[int(roc_3_name)].name.value = roc_3_name
                project_ctrl.regions_calib_3.value[int(roc_3_name)].coordinates.value = (
                    roc_3_coordinates
                )
                # Create and add a rectangle overlay
                rectangle_overlay = RectangleOverlay(self._cnvs)
                self._cnvs.add_world_overlay(rectangle_overlay)
                rectangle_overlay.active.value = True
                project_ctrl.add_roa(rectangle_overlay, name=roa_name)
                # Finally assign the rectangle coordinates value to draw it
                # FastEMROA.get_poly_field_indices expects list of nested tuples (y, x)
                points = [
                    (roa_coordinates[0], roa_coordinates[1]),  # xmin, ymin
                    (roa_coordinates[2], roa_coordinates[1]),  # xmax, ymin
                    (roa_coordinates[0], roa_coordinates[3]),  # xmin, ymax
                    (roa_coordinates[2], roa_coordinates[3]),  # xmax, ymax
                ]
                rectangle_overlay.points.value = points
                rectangle_overlay.set_physical_sel(roa_coordinates)
            self._cnvs.reset_default_cursor()

    def _write_data_to_csv_file(self, filepath: str) -> None:
        """
        Write the data in the acquisition tab's project list controller containing
        information about the project name and its ROAs to the CSV file.

        :param: filepath: (str) The absolute CSV file's path where data must be written.

        """
        if not filepath.endswith(".csv"):
            filepath += ".csv"
        with open(filepath, "w", newline="") as csvfile:
            data = {}
            writer = csv.DictWriter(csvfile, fieldnames=CSVFieldnames.tolist())
            writer.writeheader()
            project_ctrls = self._project_list_controller.project_ctrls
            for project_ctrl in project_ctrls.keys():
                data[CSVFieldnames.PROJECT_NAME.value] = project_ctrl.model.name.value
                roas = project_ctrl.model.roas.value
                for roa in roas:
                    data[CSVFieldnames.ROA_NAME.value] = roa.name.value
                    # Convert points to coordinates (xmin, ymin, xmax, ymax) or (l, t, r, b)
                    roa_coordinates = util.get_polygon_bbox(roa.points.value)
                    data[CSVFieldnames.ROA_COORDINATES_L.value] = roa_coordinates[0]
                    data[CSVFieldnames.ROA_COORDINATES_T.value] = roa_coordinates[1]
                    data[CSVFieldnames.ROA_COORDINATES_R.value] = roa_coordinates[2]
                    data[CSVFieldnames.ROA_COORDINATES_B.value] = roa_coordinates[3]
                    roc_2 = roa.roc_2.value
                    data[CSVFieldnames.ROC_2_NAME.value] = roc_2.name.value
                    roc_2_coordinates = roc_2.coordinates.value
                    data[CSVFieldnames.ROC_2_COORDINATES_L.value] = roc_2_coordinates[0]
                    data[CSVFieldnames.ROC_2_COORDINATES_T.value] = roc_2_coordinates[1]
                    data[CSVFieldnames.ROC_2_COORDINATES_R.value] = roc_2_coordinates[2]
                    data[CSVFieldnames.ROC_2_COORDINATES_B.value] = roc_2_coordinates[3]
                    roc_3 = roa.roc_3.value
                    data[CSVFieldnames.ROC_3_NAME.value] = roc_3.name.value
                    roc_3_coordinates = roc_3.coordinates.value
                    data[CSVFieldnames.ROC_3_COORDINATES_L.value] = roc_3_coordinates[0]
                    data[CSVFieldnames.ROC_3_COORDINATES_T.value] = roc_3_coordinates[1]
                    data[CSVFieldnames.ROC_3_COORDINATES_R.value] = roc_3_coordinates[2]
                    data[CSVFieldnames.ROC_3_COORDINATES_B.value] = roc_3_coordinates[3]
                    writer.writerow(data)

    def _import_roas(self):
        """Help/Development/Import ROAs menu callback."""
        filepath = self._get_csv_filepath_from_filedialog(
            message="Import ROAs from a CSV file",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )

        if not filepath:
            return

        try:
            data = self._get_data_from_csv_file(filepath)
            self._update_project_list(data)
            self._cnvs.request_drawing_update()
        except Exception as ex:
            logging.exception("Failure importing ROAs")
            wx.MessageBox(
                f"Importing ROAs from {filepath} failed, raised exception {ex}.",
                "Error",
                wx.OK | wx.ICON_ERROR,
            )

    def _export_roas(self):
        """Help/Development/Export ROAs menu callback."""
        filepath = self._get_csv_filepath_from_filedialog(
            message="Export ROAs to a CSV file",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )

        if not filepath:
            return

        try:
            self._write_data_to_csv_file(filepath)
        except Exception as ex:
            logging.exception("Failure exporting ROAs")
            wx.MessageBox(
                f"Exporting ROAs to {filepath} failed, raised exception {ex}.",
                "Error",
                wx.OK | wx.ICON_ERROR,
            )
