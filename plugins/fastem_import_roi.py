# -*- coding: utf-8 -*-
"""
Created on 11 April 2025

@author: Nandish Patel

Gives ability to use Import ROIs tool under Help > Development.

Copyright © 2025 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import logging
from ast import literal_eval
from typing import Tuple

import numpy
import wx
from shapely.geometry import Polygon
from skimage.measure import find_contours
from wx.adv import EVT_WIZARD_BEFORE_PAGE_CHANGED, Wizard, WizardPageSimple

from odemis import model
from odemis.dataio import tiff
from odemis.gui.comp import popup
from odemis.gui.comp.fastem_project_manager_panel import generate_unique_color
from odemis.gui.comp.fastem_user_settings_panel import (
    DWELL_TIME_MULTI_BEAM,
    DWELL_TIME_SINGLE_BEAM,
    HFW,
    IMMERSION,
    PIXEL_SIZE,
    RESOLUTION,
)
from odemis.gui.comp.overlay.polygon import PolygonOverlay
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.cont.acquisition.fastem_acq import OVERVIEW_IMAGES_DIR
from odemis.gui.cont.fastem_project_grid import ROIColumnNames
from odemis.gui.model.main_gui_data import Scintillator
from odemis.gui.plugin import Plugin
from odemis.util.filename import make_unique_name
from odemis.util.transform import to_physical_space

METADATA_SOURCE = {
    "labels": ["Extract metadata from image (.tiff) file", "Enter metadata manually"],
    "choices": [1, 2],
    "style": wx.CB_READONLY,
}


def show_validation_error(message: str):
    """
    Show an validation error message box.
    :param message: The error message to display.
    """
    wx.MessageBox(message, "Validation Error", wx.ICON_ERROR)


def show_error(message: str):
    """
    Show an error message box.
    :param message: The error message to display.
    """
    wx.MessageBox(message, "Error", wx.ICON_ERROR | wx.OK)


class MetadataPage(WizardPageSimple):
    """Page to select the metadata source or enter the metadata manually."""

    def __init__(self, parent):
        super().__init__(parent)
        self.md_source = None
        self.pixel_size = None
        self.center = None
        self.shape = None

        self.md_settings = SettingsPanel(self, size=self.Parent.Size)
        _, self.md_source_ctrl = self.md_settings.add_combobox_control(
            "Metadata source:", conf=METADATA_SOURCE
        )
        self.md_source_ctrl.Bind(wx.EVT_COMBOBOX, self.on_md_source_selected)
        self.md_settings.add_divider()

        self.image_lbl, self.image_ctrl = self.md_settings.add_file_button(
            "Select an image",
            value=OVERVIEW_IMAGES_DIR,
            wildcard="Image files (*.tiff)|*.tiff",
            btn_label="Browse",
        )
        self.image_lbl.Hide()
        self.image_ctrl.Hide()

        self.pixel_size_lbl, self.pixel_size_ctrl = self.md_settings.add_text_field(
            model.MD_PIXEL_SIZE,
            value="(1, 1)",
        )
        self.pixel_size_ctrl.SetToolTip("Enter the pixel size in the format (x, y)")
        self.pixel_size_lbl.Hide()
        self.pixel_size_ctrl.Hide()
        self.center_lbl, self.center_ctrl = self.md_settings.add_text_field(
            model.MD_POS,
            value="(0, 0)",
        )
        self.center_ctrl.SetToolTip("Enter the center position in the format (x, y)")
        self.center_lbl.Hide()
        self.center_ctrl.Hide()
        self.shape_lbl, self.shape_ctrl = self.md_settings.add_text_field(
            "Shape",
            value="(100, 100)",
        )
        self.shape_ctrl.SetToolTip("Enter the image shape in the format (x, y)")
        self.shape_lbl.Hide()
        self.shape_ctrl.Hide()

    def on_md_source_selected(self, evt):
        """
        Event handler for the metadata source selection.
        """
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        value = ctrl.GetValue()
        idx = METADATA_SOURCE["labels"].index(value)
        self.md_source = METADATA_SOURCE["choices"][idx]

        # Show/hide controls based on the selected metadata source
        show_image = self.md_source == 1
        show_md = self.md_source == 2
        self.image_lbl.Show(show_image)
        self.image_ctrl.Show(show_image)

        self.pixel_size_lbl.Show(show_md)
        self.pixel_size_ctrl.Show(show_md)
        self.center_lbl.Show(show_md)
        self.center_ctrl.Show(show_md)
        self.shape_lbl.Show(show_md)
        self.shape_ctrl.Show(show_md)
        self.md_settings.Layout()
        self.md_settings.Refresh()

    def validate(self) -> bool:
        """
        Validate the metadata input.
        """
        try:
            if self.md_source == 1:
                return self._validate_image_md()
            elif self.md_source == 2:
                return self._validate_manual_md()
        except Exception as ex:
            logging.exception("Validation error in MetadataPage")
            show_validation_error(f"Error loading metadata: {ex}")
            return False

    def _validate_image_md(self) -> bool:
        """
        Validate the metadata from the selected image file.
        """
        path = self.image_ctrl.GetValue()
        if not path:
            show_validation_error("Please select an image file.")
            return False

        image = tiff.read_data(path)[0]
        self.pixel_size = image.metadata[model.MD_PIXEL_SIZE]
        self.center = image.metadata[model.MD_POS]
        self.shape = image.shape

        return self._validate_structures()

    def _validate_manual_md(self) -> bool:
        """
        Validate the metadata entered manually.
        """
        inputs = {
            model.MD_PIXEL_SIZE: self.pixel_size_ctrl,
            model.MD_POS: self.center_ctrl,
            "Shape": self.shape_ctrl,
        }

        for label, ctrl in inputs.items():
            value = ctrl.GetValue().strip()
            if not value:
                show_validation_error(f"Please enter the {label}.")
                return False

        self.pixel_size = literal_eval(self.pixel_size_ctrl.GetValue().strip())
        self.center = literal_eval(self.center_ctrl.GetValue().strip())
        self.shape = literal_eval(self.shape_ctrl.GetValue().strip())

        return self._validate_structures()

    def _validate_structures(self) -> bool:
        """
        Validate the structures of pixel size, center, and shape.
        """
        for name, value, expected_len in [
            (model.MD_PIXEL_SIZE, self.pixel_size, 2),
            (model.MD_POS, self.center, 2),
            ("Shape", self.shape, 2),
        ]:
            if not isinstance(value, tuple) or len(value) != expected_len:
                show_validation_error(
                    f"{name} must be a tuple of {expected_len} values."
                )
                return False
            if not all(isinstance(i, (int, float)) for i in value):
                show_validation_error(f"{name} values must be integers or floats.")
                return False

        return True

    def get_data(self) -> dict:
        return {
            model.MD_PIXEL_SIZE: self.pixel_size,
            model.MD_POS: self.center,
            "Shape": self.shape,
        }


class NpyPage(WizardPageSimple):
    """Page to select a .npy file and load the labels."""

    def __init__(self, parent):
        super().__init__(parent)
        self.labels = numpy.array([])

        self.npy_settings = SettingsPanel(self, size=self.Parent.Size)
        _, self.npy_ctrl = self.npy_settings.add_file_button(
            "Select a .npy file",
            value=OVERVIEW_IMAGES_DIR,
            wildcard="NumPy arrays (*.npy)|*.npy",
            btn_label="Browse",
        )

    def validate(self) -> bool:
        """
        Validate the .npy file input.
        """
        npy_path = self.npy_ctrl.GetValue()
        if not npy_path:
            show_validation_error("Please select a .npy file.")
            return False
        try:
            self.labels = numpy.load(npy_path)
        except Exception as ex:
            logging.exception("Validation error in NpyPage")
            show_validation_error(f"Error loading .npy file: {ex}")
            return False
        return True

    def get_labels(self) -> numpy.ndarray:
        return self.labels


class ImportROIPlugin(Plugin):
    name = "Import ROIs"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the FASTEM main tab is present
        try:
            main_tab = main_app.main_data.getTabByName("fastem_main")
            self._pm = main_tab.project_manager_panel
        except LookupError:
            logging.debug("Not loading Import ROIs tool since main tab is not present.")
            return

        self._parent = self.main_app.GetTopWindow()

        self.addMenu("Help/Development/Import ROIs", self._import_rois)

    def _create_project(self) -> Tuple[str, str]:
        """
        Create a new project name and color for the imported ROIs.
        """
        active_projects = list(self._pm.active_project_ctrl.GetStrings())
        base_name = "Project-1"
        colour = generate_unique_color(list(self._pm.project_shape_colour.values()))

        if base_name in active_projects:
            base_name = make_unique_name(base_name, active_projects)

        return base_name, colour

    def _create_roi_data(
        self,
        idx: int,
        contour: numpy.ndarray,
        image_data: dict,
        colour: str,
        scintillator: Scintillator,
    ) -> dict:
        """
        Creates a single ROI dictionary from a contour.

        :param idx: Index of the ROI.
        :param contour: Contour points.
        :param image_data: Image metadata.
        :param colour: Color for the ROI.
        :param scintillator: Scintillator object where the ROI needs to be imported.
        :return: Dictionary containing ROI data.
        """
        polygon = Polygon(contour)
        # Higher tolerance for less polygon points
        simplified_polygon = polygon.simplify(10.0)
        physical_contour = image_data[model.MD_POS] + to_physical_space(
            numpy.array(simplified_polygon.exterior.coords),
            image_data["Shape"],
            image_data[model.MD_PIXEL_SIZE],
        )

        return {
            "index": idx,
            "data": {
                ROIColumnNames.NAME.value: "ROI",
                ROIColumnNames.SLICE_IDX.value: idx,
                ROIColumnNames.POSX.value: 0,
                ROIColumnNames.POSY.value: 0,
                ROIColumnNames.SIZEX.value: "",
                ROIColumnNames.SIZEY.value: "",
                ROIColumnNames.ROT.value: 0,
                ROIColumnNames.CONTRAST.value: round(
                    self._pm.main_data.sed.contrast.value, 4
                ),
                ROIColumnNames.BRIGHTNESS.value: round(
                    self._pm.main_data.sed.brightness.value, 4
                ),
                ROIColumnNames.DWELL_TIME.value: round(
                    self._pm.project_settings_tab.dwell_time_sb_ctrl.GetValue() * 1e6, 4
                ),
                ROIColumnNames.FIELDS.value: "",
            },
            "roa": {
                "name": "ROI",
                "slice_index": idx,
                "hfw": self._pm.main_data.user_hfw_sb.value,
                "resolution": self._pm.main_data.user_resolution_sb.value,
                "shape": {
                    "name": f"ROI_{idx}",
                    "colour": colour,
                    "selected": False,
                    "cnvs_view_name": str(scintillator.number),
                    "type": PolygonOverlay.__name__,
                    "state": {
                        "p_points": physical_contour.tolist(),
                    },
                },
            },
        }

    def _create_project_data(
        self, sample_type: str, project_name: str, rois: list
    ) -> dict:
        """
        Creates project data from the sample type, project name, and ROIs.

        :param sample_type: Type of the sample.
        :param project_name: Name of the project.
        :param rois: List of ROIs data.
        :return: Dictionary containing project data.
        """
        return {
            sample_type: {
                "projects": {
                    project_name: {
                        "settings": {
                            DWELL_TIME_MULTI_BEAM: self._pm.project_settings_tab.dwell_time_mb_ctrl.GetValue(),
                            DWELL_TIME_SINGLE_BEAM: self._pm.project_settings_tab.dwell_time_sb_ctrl.GetValue(),
                            HFW: self._pm.project_settings_tab.hfw_ctrl.GetValue(),
                            RESOLUTION: self._pm.project_settings_tab.resolution_ctrl.GetValue(),
                            IMMERSION: self._pm.project_settings_tab.immersion_mode_ctrl.GetValue(),
                            PIXEL_SIZE: self._pm.project_settings_tab.pixel_size_ctrl.GetValue(),
                        },
                        "ribbons": [],
                        "sections": [],
                        "roas": [],
                        "rois": rois,
                    }
                }
            }
        }

    def _create_rois(self, image_data: dict, labels: numpy.ndarray) -> None:
        """
        Create project's ROIs using contours from the label mask.

        Extracts contours, simplifies them, converts to physical space, and adds them as ROIs.
        :param image_data: Dictionary containing image metadata.
        :param labels: Numpy array containing the label mask.
        """
        self._pm.is_import_btn_pressed = True
        try:
            contours = find_contours(labels, level=0.5)
            project_name, colour = self._create_project()
            current_sample = self._pm.main_data.current_sample.value
            scintillator = current_sample.find_closest_scintillator(
                image_data[model.MD_POS]
            )

            rois = [
                self._create_roi_data(idx, contour, image_data, colour, scintillator)
                for idx, contour in enumerate(contours)
            ]

            project_data = self._create_project_data(
                current_sample.type, project_name, rois
            )
            self._pm.import_export_manager._apply_project_data(project_data)

            popup.show_message(
                wx.GetApp().main_frame,
                title="Import",
                message=f"ROIs successfully imported for sample type {current_sample.type}!",
                timeout=10.0,
                level=logging.INFO,
            )

        except Exception as ex:
            logging.exception(
                "Failure importing ROIs for %s",
                self._pm.main_data.current_sample.value.type,
            )
            show_error(f"Importing ROIs failed, raised exception {ex}.")
        finally:
            self._pm.is_import_btn_pressed = False

    def validate_wizard_page(self, evt):
        """Validate the wizard page before changing to the next page."""
        page = evt.GetPage()
        if page and evt.GetDirection():
            if not page.validate():
                # If validation fails, prevent page change
                evt.Veto()

    def _import_rois(self):
        """Help/Development/Import ROIs menu callback."""
        if self._pm.main_data.current_sample.value is None:
            show_error("Please select a sample carrier first.")
            return

        wizard = Wizard(self._parent, title="Import ROIs")
        image_page = MetadataPage(wizard)
        npy_page = NpyPage(wizard)
        wizard.FitToPage(image_page)
        wizard.FitToPage(npy_page)

        WizardPageSimple.Chain(image_page, npy_page)
        wizard.Bind(EVT_WIZARD_BEFORE_PAGE_CHANGED, self.validate_wizard_page)

        try:
            if wizard.RunWizard(image_page):
                image_data = image_page.get_data()
                labels = npy_page.get_labels()
                self._create_rois(image_data, labels)
            else:
                logging.debug("Import ROIs wizard was cancelled.")
        finally:
            wizard.Destroy()
