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
import math
from typing import List, Optional

import wx
from wx.grid import EVT_GRID_CELL_CHANGING, GridCellFloatEditor, GridCellNumberEditor

import odemis.gui.model as guimod
from odemis import model
from odemis.gui.comp.fastem_roa import FastEMROA
from odemis.gui.cont.fastem_project_grid import RibbonColumnNames, SectionColumnNames
from odemis.gui.cont.fastem_project_grid_base import (
    DEFAULT_PARENT,
    EVT_GRID_ROW_CHANGED,
    Column,
    DynamicGridCellComboBoxEditor,
    GridBase,
    Row,
)
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main
from odemis.util import units
from odemis.util.filename import make_compliant_string


class SectionRow(Row):
    """Class representing a row in the section grid."""

    def __init__(self, data, roa, index=-1):
        super().__init__(data, roa, index)
        self.parent_name = model.StringVA(self.data[SectionColumnNames.PARENT.value])

    def to_dict(self) -> dict:
        """
        Converts the SectionRow instance to a dictionary representation.

        :return: (dict) Dictionary with the row's index, data, ROA, and parent name.
        """
        return {
            "index": self.index,
            "data": self.data,
            "roa": (
                self.roa.to_dict() if hasattr(self.roa, "to_dict") else str(self.roa)
            ),  # Assuming roa has a to_dict method or can be stringified
            "parent_name": self.parent_name.value,
        }

    @staticmethod
    def from_dict(section: dict, tab_data):
        """
        Creates a SectionRow instance from a dictionary representation.

        :param section: (dict) Dictionary containing the section data.
        :param tab_data: (MicroscopyGUIData) Data related to the tab.
        :return: (SectionRow) The newly created SectionRow instance.
        """
        index = int(section["index"])
        data = section["data"]
        parent_name = section["parent_name"]
        roa = FastEMROA.from_dict(section["roa"], tab_data)
        section_row = SectionRow(data, roa, index)
        section_row.parent_name.value = parent_name
        return section_row

    @staticmethod
    def find_next_slice_index(name: str, rows: List[Row]) -> int:
        """
        Finds the next available index for the given name in the list of rows.

        :param name: (str) The name for which the index is to be found.
        :param rows: (list) The list of existing rows.
        :return: (int) The next available index for the given name.
        """
        used_indices = {
            int(row.data[SectionColumnNames.SLICE_IDX.value])
            for row in rows
            if row.data[SectionColumnNames.NAME.value] == name
        }
        # Find the smallest missing index
        next_index = 0
        while next_index in used_indices:
            next_index += 1
        return next_index

    @staticmethod
    def is_unique_name_slice_idx(name: str, slice_idx: int, rows: List[Row]) -> bool:
        """
        Checks if the given index is unique for the specified name in the list of rows.

        :param name: (str) The name to check for uniqueness.
        :param slice_idx: (int) The index to check for uniqueness.
        :param rows: (list) The list of existing rows.
        :return: (bool) True if the index is unique for the given name, False otherwise.
        """
        for row in rows:
            if (
                row.data[SectionColumnNames.NAME.value] == name
                and row.data[SectionColumnNames.SLICE_IDX.value] == slice_idx
            ):
                return False
        return True

    def update_data(self):
        """
        Updates the row data based on the ROA.

        This method updates the position, size, and rotation data in the row
        based on the shape associated with the ROA.
        """
        posx, posy = self.roa.shape.get_position()
        sizex, sizey = self.roa.shape.get_size()
        sizex = units.readable_str(sizex, unit="m", sig=3)
        sizey = units.readable_str(sizey, unit="m", sig=3)
        self.data[SectionColumnNames.POSX.value] = round(posx, 9)
        self.data[SectionColumnNames.POSY.value] = round(posy, 9)
        self.data[SectionColumnNames.SIZEX.value] = sizex
        self.data[SectionColumnNames.SIZEY.value] = sizey
        self.data[SectionColumnNames.ROT.value] = round(
            math.degrees(self.roa.shape.rotation)
        )

    def on_cell_changing(self, new_value: str, row: int, col: int, grid: GridBase) -> str:
        """
        Handles changes in cell values and updates associated data and ROA.

        :param new_value: (str) The new value to set in the cell. The cell editor
            class will make sure that a user can only input value in the cell of
            the correct type.
        :param row: (int) The index of the row being modified.
        :param col: (int) The index of the column being modified.
        :param grid: (GridBase) The grid instance that contains the row.
        :return: (str) The value to set in the cell.
        """
        value_to_set = new_value
        rows = grid.rows
        col = grid.columns[col]
        if col.label == SectionColumnNames.NAME.value:
            value_to_set = make_compliant_string(value_to_set)
            if not value_to_set:
                value_to_set = "Section"
            current_slice_idx = self.data[RibbonColumnNames.SLICE_IDX.value]
            if not SectionRow.is_unique_name_slice_idx(
                value_to_set, current_slice_idx, rows
            ):
                new_slice_idx = SectionRow.find_next_slice_index(value_to_set, rows)
                self.data[RibbonColumnNames.SLICE_IDX.value] = new_slice_idx
                self.roa.slice_index.value = str(new_slice_idx)
                slice_index_col = grid.get_column_by_label(
                    RibbonColumnNames.SLICE_IDX.value
                )
                grid.SetCellValue(row, slice_index_col.index, str(new_slice_idx))
            self.data[SectionColumnNames.NAME.value] = value_to_set
            self.roa.shape.name.value = (
                f"{value_to_set}_{self.data[SectionColumnNames.SLICE_IDX.value]}"
            )
            self.roa.name.value = value_to_set
        elif col.label == SectionColumnNames.SLICE_IDX.value:
            current_name = self.data[RibbonColumnNames.NAME.value]
            if not SectionRow.is_unique_name_slice_idx(
                current_name, int(value_to_set), rows
            ):
                new_slice_idx = SectionRow.find_next_slice_index(current_name, rows)
                value_to_set = str(new_slice_idx)
            self.data[SectionColumnNames.SLICE_IDX.value] = int(value_to_set)
            self.roa.slice_index.value = int(value_to_set)
            self.roa.shape.name.value = (
                f"{self.data[SectionColumnNames.NAME.value]}_{value_to_set}"
            )
        elif col.label == SectionColumnNames.POSX.value:
            if not value_to_set:
                value_to_set = str(self.data[SectionColumnNames.POSX.value])
            else:
                posy = self.data[SectionColumnNames.POSY.value]
                self.data[SectionColumnNames.POSX.value] = posx = float(value_to_set)
                self.roa.shape.move_to((posx, posy))
                self.roa.shape.cnvs.request_drawing_update()
        elif col.label == SectionColumnNames.POSY.value:
            if not value_to_set:
                value_to_set = str(self.data[SectionColumnNames.POSY.value])
            else:
                posx = self.data[SectionColumnNames.POSX.value]
                self.data[SectionColumnNames.POSY.value] = posy = float(value_to_set)
                self.roa.shape.move_to((posx, posy))
                self.roa.shape.cnvs.request_drawing_update()
        elif col.label == SectionColumnNames.ROT.value:
            self.data[SectionColumnNames.ROT.value] = rot = int(value_to_set)
            self.roa.shape.set_rotation(math.radians(rot))
            self.roa.shape.cnvs.request_drawing_update()
        elif col.label == SectionColumnNames.PARENT.value:
            self.data[SectionColumnNames.PARENT.value] = value_to_set
            self.parent_name.value = value_to_set
        return value_to_set


class FastEMProjectSectionsTab(Tab):
    """Tab class for displaying and interacting with section data for a project."""

    def __init__(self, name, button, panel, main_frame, main_data, ribbons_grid):

        self.tab_data = guimod.MicroscopyGUIData(main_data)
        self.panel = panel
        super().__init__(name, button, panel, main_frame, self.tab_data)
        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.grid = GridBase(panel, size=panel.Parent.Size)
        self.columns = [
            Column(0, SectionColumnNames.NAME.value),
            Column(
                1,
                SectionColumnNames.SLICE_IDX.value,
                editor_cls=GridCellNumberEditor,
                editor_args={"min": 0, "max": 1000000},
            ),
            Column(
                2,
                SectionColumnNames.PARENT.value,
                editor_cls=DynamicGridCellComboBoxEditor,
                editor_args={"choices": [DEFAULT_PARENT]},
            ),
            Column(
                3,
                SectionColumnNames.POSX.value,
                editor_cls=GridCellFloatEditor,
                editor_args={"precision": 9},
            ),
            Column(
                4,
                SectionColumnNames.POSY.value,
                editor_cls=GridCellFloatEditor,
                editor_args={"precision": 9},
            ),
            Column(
                5,
                SectionColumnNames.SIZEX.value,
                editor_cls=GridCellFloatEditor,
                is_read_only=True,
            ),
            Column(
                6,
                SectionColumnNames.SIZEY.value,
                editor_cls=GridCellFloatEditor,
                is_read_only=True,
            ),
            Column(
                7,
                SectionColumnNames.ROT.value,
                editor_cls=GridCellNumberEditor,
                editor_args={"min": 0, "max": 360},
            ),
        ]
        self.grid.set_columns(self.columns)

        self.ribbons_grid = ribbons_grid
        self.ribbons_grid.Bind(
            EVT_GRID_CELL_CHANGING, self._on_ribbon_grid_cell_changing
        )
        self.ribbons_grid.Bind(EVT_GRID_ROW_CHANGED, self._on_ribbon_row_changed)

        self.panel.Bind(wx.EVT_SIZE, self._on_panel_size)

    def create_grid(self) -> GridBase:
        """
        Creates and configures a new grid for displaying data.

        :return: (GridBase) A configured instance of the grid.

        Note:
            The grid is not displayed immediately but is prepared for further manipulation
            or data population before being shown.
        """
        grid = GridBase(self.panel, size=self.panel.Parent.Size)
        grid.set_columns(self.columns)
        grid.Hide()
        return grid

    def update_ribbons_grid(self, ribbons_grid):
        """
        Updates the ribbons grid and binds relevant event handlers.

        :param ribbons_grid: (GridBase) The grid to be set as the ribbons grid.
        """
        self.ribbons_grid = ribbons_grid
        self.ribbons_grid.Bind(
            EVT_GRID_CELL_CHANGING, self._on_ribbon_grid_cell_changing
        )
        self.ribbons_grid.Bind(EVT_GRID_ROW_CHANGED, self._on_ribbon_row_changed)

    def _on_ribbon_grid_cell_changing(self, evt):
        """Handles changes in the ribbons grid cell values and updates the parent column in the section grid."""
        name_col = self.ribbons_grid.get_column_by_label(RibbonColumnNames.NAME.value)
        col = evt.GetCol()
        row = evt.GetRow()
        if name_col.index == col:
            current_value = self.ribbons_grid.GetCellValue(row, col)
            new_value = evt.GetString()
            self._update_parent_col(current_value, new_value)
        evt.Skip()

    def _on_ribbon_row_changed(self, evt):
        """Updates the parent column in the section grid when the ribbons grid row changes."""
        self._update_parent_col()
        evt.Skip()

    @call_in_wx_main
    def _update_parent_col(self, current_value: Optional[str] = None, new_value: Optional[str] = None):
        """
        Updates the parent column values in the section grid based on ribbon data.

        :param current_value: (str, optional) The current value of the parent column to update.
        :param new_value: (str, optional) The new value to set in the parent column.
        """
        ribbon_names = self.ribbons_grid.get_column_values_by_label(
            RibbonColumnNames.NAME.value
        )
        ribbon_slice_index = self.ribbons_grid.get_column_values_by_label(
            RibbonColumnNames.SLICE_IDX.value
        )
        ribbon_info = [
            f"{name}_{index}" for name, index in zip(ribbon_names, ribbon_slice_index)
        ]
        ribbon_info.append(DEFAULT_PARENT)
        parent_col = self.grid.get_column_by_label(SectionColumnNames.PARENT.value)
        parent_editors = self.grid.get_column_editors(parent_col.index)
        for row_idx, parent_editor in enumerate(parent_editors):
            parent_editor.SetParameters(",".join(sorted(ribbon_info)))
            self.grid.SetCellEditor(row_idx, parent_col.index, parent_editor)
            current_parent = self.grid.GetCellValue(row_idx, parent_col.index)
            if current_value and new_value and current_parent == current_value:
                self.grid.SetCellValue(row_idx, parent_col.index, new_value)
                self.grid.rows[row_idx].parent_name.value = new_value
            elif current_parent not in ribbon_info:
                self.grid.SetCellValue(row_idx, parent_col.index, DEFAULT_PARENT)
                self.grid.rows[row_idx].parent_name.value = DEFAULT_PARENT

    def _on_panel_size(self, evt):
        """Adjusts the grid size when the panel size changes."""
        self.grid.SetSize(self.panel.Parent.Size)
        self.grid.Layout()
        self.grid.Refresh()

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            self._initialized_after_show = True

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 4
        else:
            return None
