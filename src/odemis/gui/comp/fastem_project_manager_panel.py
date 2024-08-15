# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2024 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import json
import logging
import math
import random
from colorsys import hls_to_rgb, rgb_to_hls
from functools import partial
from typing import Union

import wx

from odemis.acq.fastem import FastEMROA
from odemis.gui import SELECTION_COLOUR, img
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.conf.file import AcquisitionConfig
from odemis.gui.cont.fastem_grid_base import DEFAULT_PARENT
from odemis.gui.cont.fastem_project_tree import FastEMTreeNode, NodeType
from odemis.gui.cont.tabs.fastem_project_ribbons_tab import (
    FastEMProjectRibbonsTab,
    RibbonColumnNames,
    RibbonRow,
)
from odemis.gui.cont.tabs.fastem_project_roas_tab import (
    FastEMProjectROAsTab,
    ROAColumnNames,
    ROARow,
)
from odemis.gui.cont.tabs.fastem_project_sections_tab import (
    FastEMProjectSectionsTab,
    SectionColumnNames,
    SectionRow,
)
from odemis.gui.cont.tabs.fastem_project_settings_tab import FastEMProjectSettingsTab
from odemis.gui.cont.tabs.tab_bar_controller import TabController
from odemis.gui.model import TOOL_ELLIPSE, TOOL_POLYGON, TOOL_RECTANGLE
from odemis.gui.util import call_in_wx_main
from odemis.util import units
from odemis.util.conversion import hex_to_frgba, hex_to_rgb
from odemis.util.filename import make_unique_name

FASTEM_PROJECT_COLOURS = [
    "#0000ff",
    "#00ffff",
    "#ffff00",
    "#ff00ff",
    "#ff00bf",
    "#ff0000",
]


def rgb_to_hex(rgb_color):
    """
    Converts an RGB color to its hexadecimal string representation.

    :param rgb_color: (tuple) A tuple containing the RGB values (each between 0 and 1).

    :return: (str) The hexadecimal string representation of the color.
    """
    return "#{:02x}{:02x}{:02x}".format(
        int(rgb_color[0] * 255), int(rgb_color[1] * 255), int(rgb_color[2] * 255)
    )


def generate_unique_color(existing_colors):
    """
    Generates a unique color that does not clash with existing colors.

    :param existing_colors: (list) A list of existing color hex strings.

    :return: (str) A unique color in hexadecimal format.
    """
    existing_colors.append(SELECTION_COLOUR)
    existing_rgb_colors = [hex_to_rgb(color) for color in existing_colors]
    existing_hls_colors = [
        rgb_to_hls(*[x / 255.0 for x in rgb]) for rgb in existing_rgb_colors
    ]

    def is_unique(hls_color):
        return all(
            abs(hls_color[0] - existing[0]) > 0.1
            or abs(hls_color[1] - existing[1]) > 0.1
            or abs(hls_color[2] - existing[2]) > 0.1
            for existing in existing_hls_colors
        )

    unique_color_found = False
    while not unique_color_found:
        new_hue = random.random()  # Random hue value between 0 and 1
        new_lightness = random.uniform(
            0.3, 0.7
        )  # Random lightness value between 0.3 and 0.7 for good visibility
        new_saturation = random.uniform(
            0.5, 1.0
        )  # Random saturation value between 0.5 and 1.0 for vibrant colors
        new_color_hls = (
            new_hue,
            new_lightness,
            new_saturation,
        )  # Fixed lightness and saturation
        if is_unique(new_color_hls):
            unique_color_found = True
            new_color_rgb = hls_to_rgb(*new_color_hls)
            new_color_hex = rgb_to_hex(new_color_rgb)
            return new_color_hex


class ProjectManagerImportExport:
    """
    Handles the import and export of project data for the FastEM project manager.

    :param project_manager: (FastEMProjectManagerPanel) The project manager instance to import/export data for.
    """

    def __init__(self, project_manager):
        self.project_manager = project_manager

    def get_filepath_from_filedialog(
        self, message: str, style: int
    ) -> Union[str, None]:
        """
        Get the JSON file's path from wx.FileDialog.

        :param: message: (str) The message to be shown in the filedialog.
        :param: style: (int) The style of the filedialog.

        :return: Union[str, None]: The chosen JSON file's path, if cancel is pressed return None.
        """
        with wx.FileDialog(
            parent=self.project_manager.panel,
            message=message,
            wildcard="JSON files (*.json)|*.json",
            style=style,
        ) as filedialog:
            if filedialog.ShowModal() == wx.ID_CANCEL:
                return  # the user changed their mind
            return filedialog.GetPath()

    def export_to_file(self, filepath):
        """
        Exports the current sample's project data to a JSON file.

        :param filepath: (str) The path to the file where data will be exported.
        """
        if not filepath.endswith(".json"):
            filepath += ".json"
        data = self._collect_project_data()
        with open(filepath, "w") as file:
            json.dump(data, file, indent=4)
        wx.MessageBox(
            f"Projects successfully exported for sample type {self.project_manager.main_data.current_sample.value.type}!",
            "Export",
            wx.OK | wx.ICON_INFORMATION,
        )

    def import_from_file(self, filepath):
        """
        Imports a sample's project data from a JSON file.

        :param filepath: (str) The path to the file from which data will be imported.
        """
        with open(filepath, "r") as file:
            data = json.load(file)
        self._apply_project_data(data)
        wx.MessageBox(
            f"Projects successfully imported for sample type {self.project_manager.main_data.current_sample.value.type}!",
            "Import",
            wx.OK | wx.ICON_INFORMATION,
        )

    def _collect_project_data(self):
        """
        Collects the current project data into a dictionary for exporting.

        :return: (dict) The data organized by sample type and projects.
        """
        projects = {}
        for project in self.project_manager.tab_data.projects_tree.get_all_nodes():
            if project.type == NodeType.PROJECT:
                project_data = {
                    "settings": self.project_manager.tab_data.project_settings_data.value[
                        project.name
                    ],
                    "ribbons": [],
                    "sections": [],
                    "roas": [],
                }
                for ribbon in project.find_nodes_by_type(NodeType.RIBBON):
                    project_data["ribbons"].append(ribbon.row.to_dict())
                for section in project.find_nodes_by_type(NodeType.SECTION):
                    project_data["sections"].append(section.row.to_dict())
                for roa in project.find_nodes_by_type(NodeType.ROA):
                    project_data["roas"].append(roa.row.to_dict())
                projects[project.name] = project_data
        return {
            self.project_manager.main_data.current_sample.value.type: {
                "projects": projects,
            }
        }

    def _apply_project_data(self, data: dict):
        """
        Applies the imported sample's projects data to the project manager.

        :param data: (dict) The data to be applied.
        :raises ValueError: If the data is not compatible with the current sample type.
        """
        sample_type = self.project_manager.main_data.current_sample.value.type
        sample_data = data.get(sample_type, None)
        if sample_data is None:
            raise ValueError(f"The JSON file is not for {sample_type}")
        projects_data = sample_data.get("projects", {})

        for project_name, project_data in projects_data.items():
            project_name = project_name.strip()
            if len(project_name) == 0:
                raise ValueError("Encountered an empty Project name")
            # Apply project settings
            project_settings = project_data.get("settings", None)
            if project_settings is None:
                raise ValueError(f"{project_name} does not contain settings key")
            # Add project to the combobox
            active_project_ctrl = self.project_manager.active_project_ctrl
            active_projects = active_project_ctrl.GetStrings()
            if project_name in active_projects:
                project_name = make_unique_name(project_name, active_projects)
            active_project_ctrl.Append(project_name)
            self.project_manager.tab_data.projects_tree.add_child(
                FastEMTreeNode(project_name, NodeType.PROJECT)
            )

            project_colour = set()

            self.project_manager.tab_data.project_settings_data.value[project_name] = (
                project_settings
            )
            project_node = self.project_manager.tab_data.projects_tree.find_node(
                project_name
            )

            # Add ROAs
            roa_rows = []
            for roa in project_data.get("roas", []):
                roa_row = ROARow.from_dict(roa, self.project_manager.tab_data)
                roa_rows.append(roa_row)
                project_colour.add(roa["roa"]["shape"]["colour"])
                roa_node = FastEMTreeNode(
                    f"{roa_row.roa.name.value}_{roa_row.roa.slice_index.value}",
                    NodeType.ROA,
                    roa_row,
                )
                project_node.add_child(roa_node)
                self.project_manager.tab_data.shapes.value.append(roa_row.roa.shape)

            # Add sections
            sections_row = []
            for section in project_data.get("sections", []):
                section_row = SectionRow.from_dict(
                    section, self.project_manager.tab_data
                )
                sections_row.append(section_row)
                project_colour.add(section["roa"]["shape"]["colour"])
                section_node = FastEMTreeNode(
                    f"{section_row.roa.name.value}_{section_row.roa.slice_index.value}",
                    NodeType.SECTION,
                    section_row,
                )
                project_node.add_child(section_node)
                self.project_manager.tab_data.shapes.value.append(section_row.roa.shape)

            # Add ribbons
            for ribbon in project_data.get("ribbons", []):
                ribbon_row = RibbonRow.from_dict(ribbon, self.project_manager.tab_data)
                project_colour.add(ribbon["roa"]["shape"]["colour"])
                ribbon_node = FastEMTreeNode(
                    f"{ribbon_row.roa.name.value}_{ribbon_row.roa.slice_index.value}",
                    NodeType.RIBBON,
                    ribbon_row,
                )
                project_node.add_child(ribbon_node)
                self.project_manager.tab_data.shapes.value.append(ribbon_row.roa.shape)

            if len(project_colour) == 1:
                self.project_manager.project_shape_colour[project_name] = (
                    project_colour.pop()
                )

        # Set and notify the parent again to be reflected in FastEMProjectTreeCtrl
        for roa_row in roa_rows:
            if roa_row.parent_name.value != DEFAULT_PARENT:
                roa_row.parent_name._set_value(
                    roa_row.parent_name.value, must_notify=True
                )

        # Set and notify the parent again to be reflected in FastEMProjectTreeCtrl
        for section_row in sections_row:
            if section_row.parent_name.value != DEFAULT_PARENT:
                section_row.parent_name._set_value(
                    section_row.parent_name.value, must_notify=True
                )

        if projects_data:
            for viewport in self.project_manager.tab_data.viewports.value:
                viewport.canvas.request_drawing_update()
                viewport.canvas.Refresh()


class DetachedProjectManagerFrame(wx.Frame):
    """
    Frame to display the project manager panel in a detached window.

    :param pos: (wx.Point) The position where the frame should appear.
    :param label: (str) The title of the detached frame.
    :param project_manager: (FastEMProjectManagerPanel) The project manager instance to detach.
    """

    def __init__(self, pos, label, project_manager):
        super().__init__(None, title=label, pos=pos, style=wx.DEFAULT_FRAME_STYLE)

        self.project_manager = project_manager
        project_manager.panel.Reparent(self)

        # Create a toolbar with a reattach button
        self.Bind(wx.EVT_CLOSE, self.on_reattach_button)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(project_manager.panel, 0, wx.EXPAND | wx.ALL)

        self.Bind(wx.EVT_SIZE, self.on_size)

        self.SetSizerAndFit(sizer)
        self.Maximize()

    def on_size(self, evt):
        """Adjusts the size of the project manager panel when the detached frame is resized."""
        self.project_manager.panel.SetSize(self.Size)
        self.project_manager.panel.Layout()
        self.project_manager.panel.Refresh()

    def on_reattach_button(self, evt):
        """Reattaches the detached project manager panel back to its original parent."""
        self.project_manager.panel.Reparent(self.project_manager.panel_parent)
        self.project_manager.panel.SetSize(self.project_manager.panel_parent.Size)
        self.project_manager.panel_header.Enable(True)
        self.project_manager.panel_parent.Show(True)
        self.project_manager.show_btn.SetIcon(
            img.getBitmap("icon/ico_chevron_down.png")
        )
        self.project_manager.detached = False
        self.project_manager.panel_parent.Layout()
        self.project_manager.panel_parent.Refresh()
        self.project_manager.main_frame.Layout()
        self.project_manager.main_frame.Refresh()
        self.Destroy()


class FastEMProjectManagerPanel:
    """
    Manages the project manager panel, handling user interactions and
    managing project-related data and settings.

    :param panel: (wx.Panel) The main panel of the project manager.
    :param tab_data: The data associated with FastEMMainTab tab.
    :param main_frame: (wx.Frame) The main application frame.
    :param header_pnl: (wx.Panel) The panel header for the project manager.
    :param show_btn: (wx.Button) Button to show the project manager.
    :param detach_btn: (wx.Button) Button to detach the project manager from its parent.
    :param toolbar: The Toolbar panel which houses all tools.
    """

    def __init__(
        self, panel, tab_data, main_frame, header_pnl, show_btn, detach_btn, toolbar
    ):
        self.tab_data = tab_data
        self.main_data = tab_data.main
        self.panel = panel
        self.panel_parent = panel.Parent
        self.panel_header = header_pnl
        self.main_frame = main_frame
        self.show_btn = show_btn
        self.toolbar = toolbar
        self.previous_shapes = set(tab_data.shapes.value.copy())
        self.original_project = tab_data.current_project.value
        self.tab_data.projects_tree.add_child(
            FastEMTreeNode(tab_data.current_project.value, NodeType.PROJECT)
        )

        self.projects_panel = SettingsPanel(
            panel.active_project_panel, size=panel.active_project_panel.Size
        )
        _, self.active_project_ctrl = (
            self.projects_panel.add_combobox_with_buttons_control(
                "Active project", value=self.tab_data.current_project.value
            )
        )
        self.active_project_add_button_ctrl = self.active_project_ctrl.add_btn
        self.active_project_delete_button_ctrl = self.active_project_ctrl.delete_btn
        self.active_project_ctrl.Append(self.tab_data.current_project.value)
        self.active_project_ctrl.Bind(wx.EVT_SET_FOCUS, self._on_active_project)
        self.active_project_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_active_project)
        self.active_project_ctrl.Bind(wx.EVT_COMBOBOX, self._on_active_project)
        self.active_project_add_button_ctrl.Bind(
            wx.EVT_BUTTON, self._on_active_project_add_button_ctrl
        )
        self.active_project_delete_button_ctrl.Bind(
            wx.EVT_BUTTON, self._on_active_project_delete_button_ctrl
        )

        self.panel_parent.Bind(wx.EVT_SIZE, self.on_parent_panel_size)
        panel.pnl_project_tabs.Bind(wx.EVT_SIZE, self.on_pnl_project_tabs_size)
        panel.pnl_active_project.Bind(wx.EVT_SIZE, self.on_pnl_active_project_size)
        project_settings_panel = wx.Panel(
            panel.pnl_project_tabs,
            size=panel.pnl_project_tabs.Size,
            name="project_settings_panel",
        )
        self.project_settings_tab = FastEMProjectSettingsTab(
            "fastem_project_settings",
            panel.btn_tab_settings,
            project_settings_panel,
            main_frame,
            self.main_data,
            tab_data,
        )

        project_ribbons_panel = wx.Panel(
            panel.pnl_project_tabs,
            size=panel.pnl_project_tabs.Size,
            name="project_ribbons_panel",
        )
        self.project_ribbons_tab = FastEMProjectRibbonsTab(
            "fastem_project_ribbons",
            panel.btn_tab_ribbons,
            project_ribbons_panel,
            main_frame,
            self.main_data,
        )

        project_sections_panel = wx.Panel(
            panel.pnl_project_tabs,
            size=panel.pnl_project_tabs.Size,
            name="project_sections_panel",
        )
        self.project_sections_tab = FastEMProjectSectionsTab(
            "fastem_project_sections",
            panel.btn_tab_sections,
            project_sections_panel,
            main_frame,
            self.main_data,
            self.project_ribbons_tab.grid,
        )

        project_roas_panel = wx.Panel(
            panel.pnl_project_tabs,
            size=panel.pnl_project_tabs.Size,
            name="project_roas_panel",
        )
        self.project_roas_tab = FastEMProjectROAsTab(
            "fastem_project_roas",
            panel.btn_tab_roas,
            project_roas_panel,
            main_frame,
            self.main_data,
            self.project_sections_tab.grid,
        )

        self.project_tab_controller = TabController(
            [
                self.project_settings_tab,
                self.project_ribbons_tab,
                self.project_sections_tab,
                self.project_roas_tab,
            ],
            tab_data.active_project_tab,
            main_frame,
            self.main_data,
            self.project_settings_tab,
        )

        self.project_shape_colour = {
            tab_data.current_project.value: FASTEM_PROJECT_COLOURS[0]
        }
        self.import_export_manager = ProjectManagerImportExport(self)

        self.is_active_project_delete_button_pressed = False
        self.is_import_btn_pressed = False
        self.detached_frame = None
        self.detached = False
        self._shape_points_sub_callback = {}

        detach_btn.SetToolTip(
            "Detach the project manager panel. Close the detached panel to re-attach to the main frame."
        )
        detach_btn.Bind(wx.EVT_BUTTON, self._on_detach_button)
        panel.btn_move_up.SetToolTip(
            "Move selected row/s up. Row/s can be selected pressing Ctrl + clicking the row label."
        )
        panel.btn_move_up.Bind(wx.EVT_BUTTON, self._on_move_up)
        panel.btn_move_down.SetToolTip(
            "Move selected row/s down. Row/s can be selected pressing Ctrl + clicking the row label."
        )
        panel.btn_move_down.Bind(wx.EVT_BUTTON, self._on_move_down)
        panel.btn_delete.SetToolTip(
            "Delete selected row/s. Row/s can be selected pressing Ctrl + clicking the row label."
        )
        panel.btn_delete.Bind(wx.EVT_BUTTON, self._on_delete)
        panel.btn_export.SetToolTip(
            "Export the current sample's projects data to a JSON file."
        )
        panel.btn_export.Bind(wx.EVT_BUTTON, self._on_btn_export)
        panel.btn_import.SetToolTip("Import a sample's projects data from a JSON file.")
        panel.btn_import.Bind(wx.EVT_BUTTON, self._on_btn_import)
        tab_data.active_project_tab.subscribe(self._on_active_project_tab, init=True)
        tab_data.shapes.subscribe(self._on_shapes, init=True)
        tab_data.current_project.subscribe(self._on_current_project)
        self.main_data.is_acquiring.subscribe(self._on_is_acquiring)

    @call_in_wx_main
    def _on_is_acquiring(self, is_acquiring):
        """
        Enables or disables the project manager panel based on acquisition state.

        :param is_acquiring: (bool) Flag indicating if acquisition is in progress.
        """
        self.panel.Enable(not is_acquiring)

    def _on_btn_export(self, _):
        """
        Handles the export button click event to export current sample's projects data to a JSON file.

        :raises Exception: If an error occurs during the export process.
        """
        filepath = self.import_export_manager.get_filepath_from_filedialog(
            message="Export Projects data to a JSON file",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )

        if not filepath:
            return

        try:
            self.import_export_manager.export_to_file(filepath)
        except Exception as ex:
            logging.exception(
                "Failure exporting Projects for %s",
                self.main_data.current_sample.value.type,
            )
            wx.MessageBox(
                f"Exporting Projects data to {filepath} failed, raised exception {ex}.",
                "Error",
                wx.OK | wx.ICON_ERROR,
            )

    def _on_btn_import(self, _):
        """
        Handles the import button click event to import a sample's projects data from a JSON file.

        :raises Exception: If an error occurs during the import process.
        """
        filepath = self.import_export_manager.get_filepath_from_filedialog(
            message="Import Projects from a JSON file",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )

        if not filepath:
            return

        self.is_import_btn_pressed = True
        try:
            self.import_export_manager.import_from_file(filepath)
        except Exception as ex:
            logging.exception(
                "Failure importing Projects for %s",
                self.main_data.current_sample.value.type,
            )
            wx.MessageBox(
                f"Importing Projects from {filepath} failed, raised exception {ex}.",
                "Error",
                wx.OK | wx.ICON_ERROR,
            )
        finally:
            self.is_import_btn_pressed = False

    def _on_move_up(self, evt):
        """Moves selected row/s up in the grid."""
        active_project_tab = self.tab_data.active_project_tab.value
        if active_project_tab == self.project_ribbons_tab:
            rows = self.project_ribbons_tab.grid.GetSelectedRows()
            self.project_ribbons_tab.grid.move_rows_up(rows)
        elif active_project_tab == self.project_sections_tab:
            rows = self.project_sections_tab.grid.GetSelectedRows()
            self.project_sections_tab.grid.move_rows_up(rows)
        elif active_project_tab == self.project_roas_tab:
            rows = self.project_roas_tab.grid.GetSelectedRows()
            self.project_roas_tab.grid.move_rows_up(rows)
        project_node = self.tab_data.projects_tree.find_node(
            self.tab_data.current_project.value
        )
        project_node.sort_children_recursively()

    def _on_move_down(self, evt):
        """Moves selected row/s down in the grid."""
        active_project_tab = self.tab_data.active_project_tab.value
        if active_project_tab == self.project_ribbons_tab:
            rows = self.project_ribbons_tab.grid.GetSelectedRows()
            self.project_ribbons_tab.grid.move_rows_down(rows)
        elif active_project_tab == self.project_sections_tab:
            rows = self.project_sections_tab.grid.GetSelectedRows()
            self.project_sections_tab.grid.move_rows_down(rows)
        elif active_project_tab == self.project_roas_tab:
            rows = self.project_roas_tab.grid.GetSelectedRows()
            self.project_roas_tab.grid.move_rows_down(rows)
        project_node = self.tab_data.projects_tree.find_node(
            self.tab_data.current_project.value
        )
        project_node.sort_children_recursively()

    def _on_delete(self, evt):
        """Deletes the selected row/s from the grid and the project tree."""
        project_node = self.tab_data.projects_tree.find_node(
            self.tab_data.current_project.value
        )
        active_project_tab = self.tab_data.active_project_tab.value
        if active_project_tab == self.project_ribbons_tab:
            rows = self.project_ribbons_tab.grid.GetSelectedRows()
            for row in rows:
                row_obj = self.project_ribbons_tab.grid.rows[row]
                project_node.delete_node_by_shape(row_obj.roa.shape)
            self.project_ribbons_tab.grid.delete_rows(rows)
        elif active_project_tab == self.project_sections_tab:
            rows = self.project_sections_tab.grid.GetSelectedRows()
            for row in rows:
                row_obj = self.project_sections_tab.grid.rows[row]
                project_node.delete_node_by_shape(row_obj.roa.shape)
            self.project_sections_tab.grid.delete_rows(rows)
        elif active_project_tab == self.project_roas_tab:
            rows = self.project_roas_tab.grid.GetSelectedRows()
            for row in rows:
                row_obj = self.project_roas_tab.grid.rows[row]
                project_node.delete_node_by_shape(row_obj.roa.shape)
            self.project_roas_tab.grid.delete_rows(rows)
        self.tab_data.projects_tree.print_tree()

    def _enable_tools(self, enable):
        """
        Enables or disables the drawing tools on the toolbar.

        :param enable: (bool) Flag to enable or disable the tools.
        """
        self.toolbar.enable_button(TOOL_RECTANGLE, enable)
        self.toolbar.enable_button(TOOL_ELLIPSE, enable)
        self.toolbar.enable_button(TOOL_POLYGON, enable)

    def _on_current_project(self, project):
        """
        Updates the UI and enables/disables project-related buttons based on the selected project.

        :param project: (str) The name of the currently selected project.
        """
        enable = len(project) > 0
        self.panel.btn_move_up.Enable(enable)
        self.panel.btn_move_down.Enable(enable)
        self.panel.btn_delete.Enable(enable)
        self.panel.pnl_project_tabbuttons.Enable(enable)
        self.panel.pnl_project_tabs.Enable(enable)
        self._enable_tools(enable)

    def _on_shape_points(self, points, shape):
        """
        Updates the shape's color, handles shape points, and manages the project tree based on the active tab.

        :param shape: (EditableShape) The shape object which was created.
        """
        colour = self.project_shape_colour[self.tab_data.current_project.value]
        shape.colour = hex_to_frgba(colour)
        if shape in self._shape_points_sub_callback:
            shape.points.unsubscribe(self._shape_points_sub_callback[shape])

        if len(points) == 0:
            shape.cnvs.remove_shape(shape)
            if shape in self._shape_points_sub_callback:
                del self._shape_points_sub_callback[shape]
        else:
            posx, posy = shape.get_position()
            sizex, sizey = shape.get_size()
            sizex = units.readable_str(sizex, unit="m", sig=3)
            sizey = units.readable_str(sizey, unit="m", sig=3)
            acqui_conf = AcquisitionConfig()
            roa = FastEMROA(shape, self.main_data, overlap=acqui_conf.overlap)
            project_node = self.tab_data.projects_tree.find_node(
                self.tab_data.current_project.value
            )
            if self.tab_data.active_project_tab.value == self.project_ribbons_tab:
                ribbon_name = "Ribbon"
                ribbon_slice_index = 0
                if not RibbonRow.is_unique_name_slice_idx(
                    ribbon_name, ribbon_slice_index, self.project_ribbons_tab.grid.rows
                ):
                    ribbon_slice_index = RibbonRow.find_next_slice_index(
                        ribbon_name, self.project_ribbons_tab.grid.rows
                    )
                roa.slice_index.value = ribbon_slice_index
                roa.name.value = ribbon_name
                shape.name.value = f"{ribbon_name}_{ribbon_slice_index}"
                row_data = {
                    RibbonColumnNames.NAME.value: ribbon_name,
                    RibbonColumnNames.SLICE_IDX.value: ribbon_slice_index,
                    RibbonColumnNames.POSX.value: posx,
                    RibbonColumnNames.POSY.value: posy,
                    RibbonColumnNames.SIZEX.value: sizex,
                    RibbonColumnNames.SIZEY.value: sizey,
                    RibbonColumnNames.ROT.value: int(math.degrees(shape.rotation)),
                }
                row = RibbonRow(row_data, roa)
                self.project_ribbons_tab.grid.add_row(row)
                project_node.add_child(
                    FastEMTreeNode(shape.name.value, NodeType.RIBBON, row)
                )
            elif self.tab_data.active_project_tab.value == self.project_sections_tab:
                section_name = "Section"
                section_slice_index = 0
                if not SectionRow.is_unique_name_slice_idx(
                    section_name,
                    section_slice_index,
                    self.project_sections_tab.grid.rows,
                ):
                    section_slice_index = SectionRow.find_next_slice_index(
                        section_name, self.project_sections_tab.grid.rows
                    )
                roa.slice_index.value = section_slice_index
                roa.name.value = section_name
                shape.name.value = f"{section_name}_{section_slice_index}"
                row_data = {
                    SectionColumnNames.NAME.value: section_name,
                    SectionColumnNames.SLICE_IDX.value: section_slice_index,
                    SectionColumnNames.POSX.value: posx,
                    SectionColumnNames.POSY.value: posy,
                    SectionColumnNames.SIZEX.value: sizex,
                    SectionColumnNames.SIZEY.value: sizey,
                    SectionColumnNames.ROT.value: int(math.degrees(shape.rotation)),
                    SectionColumnNames.PARENT.value: DEFAULT_PARENT,
                }
                row = SectionRow(row_data, roa)
                self.project_sections_tab.grid.add_row(row)
                self.project_sections_tab._update_parent_col()
                project_node.add_child(
                    FastEMTreeNode(shape.name.value, NodeType.SECTION, row)
                )
            elif self.tab_data.active_project_tab.value == self.project_roas_tab:
                roa_name = "ROA"
                roa_slice_index = 0
                if not SectionRow.is_unique_name_slice_idx(
                    roa_name, roa_slice_index, self.project_roas_tab.grid.rows
                ):
                    roa_slice_index = SectionRow.find_next_slice_index(
                        roa_name, self.project_roas_tab.grid.rows
                    )
                roa.slice_index.value = roa_slice_index
                roa.name.value = roa_name
                shape.name.value = f"{roa_name}_{roa_slice_index}"
                row_data = {
                    ROAColumnNames.NAME.value: roa_name,
                    ROAColumnNames.SLICE_IDX.value: roa_slice_index,
                    ROAColumnNames.POSX.value: posx,
                    ROAColumnNames.POSY.value: posy,
                    ROAColumnNames.SIZEX.value: sizex,
                    ROAColumnNames.SIZEY.value: sizey,
                    ROAColumnNames.ROT.value: int(math.degrees(shape.rotation)),
                    ROAColumnNames.PARENT.value: DEFAULT_PARENT,
                    ROAColumnNames.FIELDS.value: "",
                }
                row = ROARow(row_data, roa)
                self.project_roas_tab.grid.add_row(row)
                self.project_roas_tab._update_parent_col()
                project_node.add_child(
                    FastEMTreeNode(shape.name.value, NodeType.ROA, row)
                )

    def _on_shapes(self, shapes):
        """Handles updates to the shapes in the project, managing additions and deletions."""
        # Convert lists to sets
        new_shapes = set(shapes.copy())

        added_shape = new_shapes - self.previous_shapes
        removed_shape = self.previous_shapes - new_shapes

        self.previous_shapes = new_shapes

        if self.is_import_btn_pressed:
            return

        if len(added_shape) == 1:
            shape = added_shape.pop()
            logging.debug("Shape creation in progress.")
            # If shape has been named already, it means that import button was pressed
            if shape.name.value:
                self._on_shape_points(shape.points.value, shape)
            else:
                sub_callback = partial(self._on_shape_points, shape=shape)
                self._shape_points_sub_callback[shape] = sub_callback
                shape.points.subscribe(sub_callback)
        if len(removed_shape) == 1:
            shape = removed_shape.pop()
            # Handle wx.WXK_DELETE pressed in ShapesOverlay
            if not self.is_active_project_delete_button_pressed:
                logging.debug("Shape deletion in progress.")
                self.tab_data.projects_tree.delete_node_by_shape(shape)
                row = self.project_ribbons_tab.grid.get_row_by_shape(shape)
                if row:
                    self.project_ribbons_tab.grid.delete_row(row.index)
                row = self.project_sections_tab.grid.get_row_by_shape(shape)
                if row:
                    self.project_sections_tab.grid.delete_row(row.index)
                row = self.project_roas_tab.grid.get_row_by_shape(shape)
                if row:
                    self.project_roas_tab.grid.delete_row(row.index)
            if shape in self._shape_points_sub_callback:
                del self._shape_points_sub_callback[shape]

    def clear_grid(self):
        """Clears the grid for all project tabs (ribbons, sections, ROAs)."""
        self.project_ribbons_tab.grid.clear()
        self.project_sections_tab.grid.clear()
        self.project_roas_tab.grid.clear()

    def update_project_shape_colour(self, project_name):
        """
        Updates or assigns a color to the shapes associated with a given project.

        :param project_name: (str) The name of the project whose shapes' colors are to be updated.
        """
        if len(self.project_shape_colour) < len(FASTEM_PROJECT_COLOURS):
            self.project_shape_colour[project_name] = FASTEM_PROJECT_COLOURS[
                len(self.project_shape_colour)
            ]
        else:
            self.project_shape_colour[project_name] = generate_unique_color(
                list(self.project_shape_colour.values())
            )

    def update_grid_for_project(self, project_name):
        """
        Updates the grid for a given project, populating it with the project's data.

        :param project_name: (str) The name of the project to update the grid for.
        """
        self.clear_grid()
        project_node = self.tab_data.projects_tree.find_node(project_name)
        ribbons = project_node.find_nodes_by_type(NodeType.RIBBON)
        sections = project_node.find_nodes_by_type(NodeType.SECTION)
        roas = project_node.find_nodes_by_type(NodeType.ROA)
        self.project_ribbons_tab.grid.rows = [0] * len(ribbons)
        self.project_sections_tab.grid.rows = [0] * len(sections)
        self.project_roas_tab.grid.rows = [0] * len(roas)
        for idx, roa in enumerate(roas):
            roa.row.index = idx
            self.project_roas_tab.grid.add_row(roa.row)
        for idx, section in enumerate(sections):
            section.row.index = idx
            self.project_sections_tab.grid.add_row(section.row)
        for idx, ribbon in enumerate(ribbons):
            ribbon.row.index = idx
            self.project_ribbons_tab.grid.add_row(ribbon.row)

    def _on_active_project_add_button_ctrl(self, evt):
        """Handles the event when the '+' button is pressed, adding a new project."""
        self.is_active_project_delete_button_pressed = False
        value = wx.GetTextFromUser(
            "Enter new project:",
            parent=self.active_project_add_button_ctrl,
            default_value=make_unique_name(
                "Project-1", self.active_project_ctrl.GetStrings()
            ),
        )
        value = value.strip()
        ctrl = self.active_project_ctrl
        if value:
            if value not in ctrl.GetStrings():
                ctrl.Append(value)
                self.tab_data.projects_tree.add_child(
                    FastEMTreeNode(value, NodeType.PROJECT)
                )
                self.update_project_shape_colour(value)
            ctrl.SetValue(value)
            self.update_grid_for_project(value)
            self.tab_data.current_project.value = value

    def _on_active_project_delete_button_ctrl(self, evt):
        """Handles the event when the 'Bin' button is pressed, removing a project."""
        self.is_active_project_delete_button_pressed = True
        ctrl = self.active_project_ctrl
        value = ctrl.GetValue()
        if value in ctrl.GetStrings():
            ctrl.Delete(ctrl.GetStrings().index(value))
            project_node = self.tab_data.projects_tree.find_node(value)
            ribbons = project_node.find_nodes_by_type(NodeType.RIBBON)
            sections = project_node.find_nodes_by_type(NodeType.SECTION)
            roas = project_node.find_nodes_by_type(NodeType.ROA)
            for ribbon in ribbons:
                ribbon.row.roa.shape.cnvs.remove_shape(ribbon.row.roa.shape)
                ribbon.row.roa.shape = None
            for section in sections:
                section.row.roa.shape.cnvs.remove_shape(section.row.roa.shape)
                section.row.roa.shape = None
            for roa in roas:
                roa.row.roa.shape.cnvs.remove_shape(roa.row.roa.shape)
                roa.row.roa.shape = None
            self.tab_data.projects_tree.delete_node(value)
            del self.tab_data.project_settings_data.value[value]
            del self.project_shape_colour[value]
            ctrl.SetValue("")
            self.tab_data.current_project.value = ""
            self.clear_grid()

    def _on_active_project(self, evt):
        """Handles changes in the active project, including selection and renaming."""
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        if evt.GetEventType() == wx.EVT_SET_FOCUS.typeId:
            self.original_project = ctrl.GetValue().strip()
        # Name change, no need to clear grid
        elif evt.GetEventType() == wx.EVT_TEXT_ENTER.typeId:
            if ctrl.GetStrings():
                value = ctrl.GetValue().strip()
                if self.original_project and self.original_project in ctrl.GetStrings():
                    if value and value not in ctrl.GetStrings():
                        idx = ctrl.FindString(self.original_project)
                        project_node = self.tab_data.projects_tree.find_node(
                            self.original_project
                        )
                        project_node.rename(value)
                        settings_data = self.tab_data.project_settings_data.value.pop(
                            self.original_project
                        )
                        colour = self.project_shape_colour.pop(self.original_project)
                        ctrl.SetString(idx, value)
                        self.tab_data.project_settings_data.value[value] = settings_data
                        self.project_shape_colour[value] = colour
                        self.tab_data.current_project.value = value
                        self.original_project = value
                        return
                ctrl.SetValue(value)
                self.tab_data.current_project.value = value
        elif evt.GetEventType() == wx.EVT_COMBOBOX.typeId:
            value = ctrl.GetValue()
            if value != self.tab_data.current_project.value:
                self.update_grid_for_project(value)
                self.tab_data.current_project.value = value

    @call_in_wx_main
    def _on_active_project_tab(self, active_tab):
        """
        Updates the UI based on the currently active project tab.

        :param active_tab: (Tab) The currently active tab in the project manager.
        """
        if active_tab == self.project_ribbons_tab:
            self.project_ribbons_tab.grid.Layout()
            self.project_ribbons_tab.grid.Refresh()
        elif active_tab == self.project_sections_tab:
            self.project_sections_tab.grid.Layout()
            self.project_sections_tab.grid.Refresh()
        elif active_tab == self.project_roas_tab:
            self.project_roas_tab.grid.Layout()
            self.project_roas_tab.grid.Refresh()
        enable = active_tab != self.project_settings_tab
        self.panel.btn_move_up.Enable(enable)
        self.panel.btn_move_down.Enable(enable)
        self.panel.btn_delete.Enable(enable)
        self._enable_tools(enable)

    def on_parent_panel_size(self, evt):
        """Adjusts the project manager panel size when the parent panel is resized."""
        if not self.detached:
            self.panel.SetSize(self.panel_parent.Size)
            self.panel.Layout()
            self.panel.Refresh()

    def on_pnl_active_project_size(self, evt):
        """Adjusts the active project panel size when the panel is resized."""
        self.panel.active_project_panel.SetSize(self.panel.pnl_active_project.Size)
        self.panel.active_project_panel.Layout()
        self.panel.active_project_panel.Refresh()
        self.panel.btn_panel.Layout()
        self.panel.btn_panel.Refresh()

    def on_pnl_project_tabs_size(self, evt):
        """Adjusts the size of the project tabs when the panel is resized."""
        self.project_settings_tab.panel.SetSize(self.panel.pnl_project_tabs.Size)
        self.project_ribbons_tab.panel.SetSize(self.panel.pnl_project_tabs.Size)
        self.project_sections_tab.panel.SetSize(self.panel.pnl_project_tabs.Size)
        self.project_roas_tab.panel.SetSize(self.panel.pnl_project_tabs.Size)
        self.project_settings_tab.panel.Layout()
        self.project_settings_tab.panel.Refresh()
        self.project_ribbons_tab.panel.Layout()
        self.project_ribbons_tab.panel.Refresh()
        self.project_sections_tab.panel.Layout()
        self.project_sections_tab.panel.Refresh()
        self.project_roas_tab.panel.Layout()
        self.project_roas_tab.panel.Refresh()
        self.panel.btn_panel.Layout()
        self.panel.btn_panel.Refresh()

    def _on_detach_button(self, evt):
        """Detaches the project manager panel from its parent."""
        if not self.detached:
            pos = self.panel.GetScreenPosition()
            self.detached_frame = DetachedProjectManagerFrame(
                pos, "Project Manager", self
            )
            self.detached_frame.Show()
            self.panel_parent.Show(False)
            self.show_btn.SetIcon(img.getBitmap("icon/ico_chevron_up.png"))
            self.detached = True
            self.panel_header.Enable(False)
            self.panel_parent.Layout()
            self.main_frame.Layout()
