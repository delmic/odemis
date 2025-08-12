# -*- coding: utf-8 -*-
"""
Created on 11 April 2025

@author: Nandish Patel

Gives ability to use Import region tool under Help > Development.

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
from enum import Enum
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
from odemis.gui.conf.file import AcquisitionConfig
from odemis.gui.cont.acquisition.fastem_acq import OVERVIEW_IMAGES_DIR
from odemis.gui.cont.fastem_project_grid import ROAColumnNames, TOAColumnNames
from odemis.gui.cont.fastem_project_grid_base import DEFAULT_PARENT
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


class RegionType(Enum):
    """Enum for region types."""
    TOA = "TOA"
    ROA = "ROA"


class RegionPage(WizardPageSimple):
    """Page to select the region, either TOA or ROA."""

    def __init__(self, parent):
        super().__init__(parent)
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Region type selection
        region_box = wx.StaticBox(panel, label="Select region type:")
        region_box_sizer = wx.StaticBoxSizer(region_box, wx.VERTICAL)
        self.region_radio_box = wx.RadioBox(
            panel,
            choices=[RegionType.TOA.value, RegionType.ROA.value],
            majorDimension=1,  # 1 column
            style=wx.RA_SPECIFY_COLS,
        )
        self.region_radio_box.SetSelection(0)  # Default to TOA
        region_box_sizer.Add(self.region_radio_box, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(region_box_sizer, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(main_sizer)
        main_sizer.Fit(panel)

    def validate(self) -> bool:
        """
        Validate the region type input.
        """
        region = self.region_radio_box.GetSelection()
        if region == wx.NOT_FOUND:
            show_validation_error("Please select a region type.")
            return False
        return True

    def get_region_type(self) -> str:
        return self.region_radio_box.GetStringSelection()


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
        self.scale_factor_lbl, self.scale_factor_ctrl = self.md_settings.add_text_field(
            "Scale factor",
            value="(1, 1)",
        )
        self.scale_factor_ctrl.SetToolTip("Enter the scale factor between the mask and the image in the format (x, y)")
        self.scale_factor_lbl.Hide()
        self.scale_factor_ctrl.Hide()

        self.pixel_size_lbl, self.pixel_size_ctrl = self.md_settings.add_text_field(
            model.MD_PIXEL_SIZE,
            value="(1.0e-6, 1.0e-6)",
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
        self.scale_factor_lbl.Show(show_image)
        self.scale_factor_ctrl.Show(show_image)

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

        image = tiff.open_data(path).content[0]

        scale = self.scale_factor_ctrl.GetValue().strip()
        if not scale:
            show_validation_error(f"Please enter the {self.scale_factor_lbl}.")
            return False
        scale = literal_eval(scale)

        self.pixel_size = (image.metadata[model.MD_PIXEL_SIZE][0] * scale[0],
                           image.metadata[model.MD_PIXEL_SIZE][1] * scale[1])
        self.center = image.metadata[model.MD_POS]
        self.shape = (int(image.shape[0] / scale[0]),
                      int(image.shape[1] / scale[1]))

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


class ImportRegionPlugin(Plugin):
    name = "Import Region"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the FASTEM main tab is present
        try:
            main_tab = main_app.main_data.getTabByName("fastem_main")
            self._pm = main_tab.project_manager_panel
            self._acqui_config = AcquisitionConfig()
        except LookupError:
            logging.debug("Not loading Import Region tool since main tab is not present.")
            return

        self._parent = self.main_app.GetTopWindow()

        self.addMenu("Help/Development/Import region", self._import_region)

    def _create_project(self) -> Tuple[str, str]:
        """
        Create a new project name and color for the imported regions.
        """
        active_projects = list(self._pm.active_project_ctrl.GetStrings())
        base_name = "Project-1"
        colour = generate_unique_color(list(self._pm.project_shape_colour.values()))

        if base_name in active_projects:
            base_name = make_unique_name(base_name, active_projects)

        return base_name, colour

    def _create_region_data(
            self,
            region_type: str,
            idx: int,
            contour: numpy.ndarray,
            image_data: dict,
            colour: str,
            scintillator: Scintillator,
    ) -> dict:
        """
        Creates a single region dictionary from a contour.

        :param region_type: Type of the region (TOA or ROA).
        :param idx: Index of the region.
        :param contour: Contour points.
        :param image_data: Image metadata.
        :param colour: Color for the region.
        :param scintillator: Scintillator object where the region needs to be imported.
        :raises ValueError: If the region type is unknown.
        :return: Dictionary containing region data.
        """
        polygon = Polygon(contour)
        # Higher tolerance for less polygon points
        simplified_polygon = polygon.simplify(5.0)
        physical_contour = image_data[model.MD_POS] + to_physical_space(
            numpy.array(simplified_polygon.exterior.coords),
            image_data["Shape"],
            image_data[model.MD_PIXEL_SIZE],
        )

        if region_type == RegionType.TOA.value:
            return self._create_toa_data(
                idx, physical_contour, colour, scintillator
            )
        elif region_type == RegionType.ROA.value:
            return self._create_roa_data(
                idx, physical_contour, colour, scintillator
            )
        else:
            raise ValueError(f"Unknown region type: {region_type}")

    def _create_toa_data(
            self,
            idx: int,
            physical_contour: numpy.ndarray,
            colour: str,
            scintillator: Scintillator,
    ) -> dict:
        """
        Creates a single TOA dictionary from a contour.

        :param idx: Index of the TOA.
        :param physical_contour: Contour points in physical space.
        :param colour: Color for the TOA.
        :param scintillator: Scintillator object where the TOA needs to be imported.
        :return: Dictionary containing TOA data.
        """
        return {
            "index": idx,
            "data": {
                TOAColumnNames.NAME.value: "TOA",
                TOAColumnNames.SLICE_IDX.value: idx,
                TOAColumnNames.POSX.value: 0,
                TOAColumnNames.POSY.value: 0,
                TOAColumnNames.SIZEX.value: "",
                TOAColumnNames.SIZEY.value: "",
                TOAColumnNames.ROT.value: 0,
                TOAColumnNames.CONTRAST.value: round(
                    self._pm.main_data.sed.contrast.value, 4
                ),
                TOAColumnNames.BRIGHTNESS.value: round(
                    self._pm.main_data.sed.brightness.value, 4
                ),
                TOAColumnNames.DWELL_TIME.value: round(
                    self._pm.project_settings_tab.dwell_time_sb_ctrl.GetValue() * 1e6, 4
                ),
                TOAColumnNames.FIELDS.value: "",
            },
            "roa": {
                "name": "TOA",
                "slice_index": idx,
                "hfw": self._pm.main_data.user_hfw_sb.value,
                "resolution": self._pm.main_data.user_resolution_sb.value,
                "shape": {
                    "name": f"TOA_{idx}",
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

    def _create_roa_data(
            self,
            idx: int,
            physical_contour: numpy.ndarray,
            colour: str,
            scintillator: Scintillator,
    ) -> dict:
        """
        Creates a single ROA dictionary from a contour.

        :param idx: Index of the ROA.
        :param physical_contour: Contour points in physical space.
        :param colour: Color for the ROA.
        :param scintillator: Scintillator object where the ROA needs to be imported.
        :return: Dictionary containing ROA data.
        """
        return {
            "index": idx,
            "data": {
                ROAColumnNames.NAME.value: "ROA",
                ROAColumnNames.SLICE_IDX.value: idx,
                ROAColumnNames.POSX.value: 0,
                ROAColumnNames.POSY.value: 0,
                ROAColumnNames.SIZEX.value: "",
                ROAColumnNames.SIZEY.value: "",
                ROAColumnNames.ROT.value: 0,
                ROAColumnNames.PARENT.value: DEFAULT_PARENT,
                ROAColumnNames.FIELDS.value: "",
            },
            "roa": {
                "name": "ROA",
                "slice_index": idx,
                "overlap": self._acqui_config.overlap,
                "shape": {
                    "name": f"ROA_{idx}",
                    "colour": colour,
                    "selected": False,
                    "cnvs_view_name": str(scintillator.number),
                    "type": PolygonOverlay.__name__,
                    "state": {
                        "p_points": physical_contour.tolist(),
                    },
                },
            },
            "parent_name": DEFAULT_PARENT,
        }

    def _create_project_data(
            self, sample_type: str, project_name: str, region_type: str, regions: list
    ) -> dict:
        """
        Creates project data from the sample type, project name, and regions.

        :param sample_type: Type of the sample.
        :param project_name: Name of the project.
        :param region_type: Type of the region (TOA or ROA).
        :param regions: List of regions data.
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
                        "roas": regions if region_type == RegionType.ROA.value else [],
                        "toas": regions if region_type == RegionType.TOA.value else [],
                    }
                }
            }
        }

    def _create_regions(self, region_type: str, image_data: dict, labels: numpy.ndarray) -> None:
        """
        Create project's regions using contours from the label mask.

        Extracts contours, simplifies them, converts to physical space, and adds them as TOAs.
        :param region_type: Type of the region (TOA or ROA).
        :param image_data: Dictionary containing image metadata.
        :param labels: Numpy array containing the label mask.
        :raises ValueError: If the region type is unknown.
        """
        self._pm.is_import_btn_pressed = True
        try:
            regions = []
            contours = []
            # Find contours per label to ensure each label is a separate region
            for label in numpy.unique(labels):
                if label == 0:
                    continue  # skip background if present
                mask = (labels == label)
                contours.extend(find_contours(mask.astype(numpy.uint8), level=0.5))
            project_name, colour = self._create_project()
            current_sample = self._pm.main_data.current_sample.value
            scintillator = current_sample.find_closest_scintillator(
                image_data[model.MD_POS]
            )

            for idx, contour in enumerate(contours):
                regions.append(
                    self._create_region_data(region_type, idx, contour, image_data, colour, scintillator)
                )

            project_data = self._create_project_data(
                current_sample.type, project_name, region_type, regions
            )
            self._pm.import_export_manager._apply_project_data(project_data)

            popup.show_message(
                wx.GetApp().main_frame,
                title="Import",
                message=f"{region_type}s successfully imported for sample type {current_sample.type}!",
                timeout=10.0,
                level=logging.INFO,
            )

        except Exception as ex:
            logging.exception(
                "Failure importing TOAs for %s",
                self._pm.main_data.current_sample.value.type,
            )
            show_error(f"Importing TOAs failed, raised exception {ex}.")
        finally:
            self._pm.is_import_btn_pressed = False

    def validate_wizard_page(self, evt):
        """Validate the wizard page before changing to the next page."""
        page = evt.GetPage()
        if page and evt.GetDirection():
            if not page.validate():
                # If validation fails, prevent page change
                evt.Veto()

    def _import_region(self):
        """Help/Development/Import region menu callback."""
        if self._pm.main_data.current_sample.value is None:
            show_error("Please select a sample carrier first.")
            return

        wizard = Wizard(self._parent, title="Import region")
        region_page = RegionPage(wizard)
        image_page = MetadataPage(wizard)
        npy_page = NpyPage(wizard)
        wizard.FitToPage(region_page)
        wizard.FitToPage(image_page)
        wizard.FitToPage(npy_page)

        WizardPageSimple.Chain(region_page, image_page)
        WizardPageSimple.Chain(image_page, npy_page)
        wizard.Bind(EVT_WIZARD_BEFORE_PAGE_CHANGED, self.validate_wizard_page)

        try:
            if wizard.RunWizard(region_page):
                region_type = region_page.get_region_type()
                image_data = image_page.get_data()
                labels = npy_page.get_labels()
                self._create_regions(region_type, image_data, labels)
            else:
                logging.debug("Import region wizard was cancelled.")
        finally:
            wizard.Destroy()
