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
from typing import List

import wx
from wx.grid import GridCellFloatEditor, GridCellNumberEditor

import odemis.gui.model as guimod
from odemis.gui.comp.fastem_roa import FastEMROA
from odemis.gui.cont.fastem_project_grid import RibbonColumnNames
from odemis.gui.cont.fastem_project_grid_base import Column, GridBase, Row
from odemis.gui.cont.tabs.tab import Tab
from odemis.util import units
from odemis.util.filename import make_compliant_string


class RibbonRow(Row):
    """Class representing a row in the ribbon grid."""

    def __init__(self, data, roa, index=-1):
        super().__init__(data, roa, index)

    def to_dict(self) -> dict:
        """
        Converts the RibbonRow instance to a dictionary format.

        :return: (dict) The row data in dictionary format.
        """
        return {
            "index": self.index,
            "data": self.data,
            "roa": (
                self.roa.to_dict() if hasattr(self.roa, "to_dict") else {}
            ),  # Assuming roa has a to_dict method or can be stringified
            "parent_name": self.parent_name,
        }

    @staticmethod
    def from_dict(ribbon: dict, tab_data):
        """
        Creates a RibbonRow instance from a dictionary.

        :param ribbon: (dict) Dictionary containing the row data.
        :param tab_data: (MicroscopyGUIData) Data related to the tab.
        :return: (RibbonRow) The created RibbonRow instance.
        """
        index = int(ribbon["index"])
        data = ribbon["data"]
        parent_name = ribbon["parent_name"]
        roa = FastEMROA.from_dict(ribbon["roa"], tab_data)
        ribbon_row = RibbonRow(data, roa, index)
        ribbon_row.parent_name = parent_name
        return ribbon_row

    @staticmethod
    def find_next_slice_index(name: str, rows: List[Row]) -> int:
        """
        Finds the next available index for the given name in the list of rows.

        :param name: (str) The name for which the index is to be found.
        :param rows: (list) The list of existing rows.
        :return: (int) The next available index for the given name.
        """
        used_indices = {
            int(row.data[RibbonColumnNames.SLICE_IDX.value])
            for row in rows
            if row.data[RibbonColumnNames.NAME.value] == name
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
                row.data[RibbonColumnNames.NAME.value] == name
                and row.data[RibbonColumnNames.SLICE_IDX.value] == slice_idx
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
        self.data[RibbonColumnNames.POSX.value] = round(posx, 9)
        self.data[RibbonColumnNames.POSY.value] = round(posy, 9)
        self.data[RibbonColumnNames.SIZEX.value] = sizex
        self.data[RibbonColumnNames.SIZEY.value] = sizey
        self.data[RibbonColumnNames.ROT.value] = round(
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
        if col.label == RibbonColumnNames.NAME.value:
            value_to_set = make_compliant_string(value_to_set)
            if not value_to_set:
                value_to_set = "Ribbon"
            current_slice_idx = self.data[RibbonColumnNames.SLICE_IDX.value]
            if not RibbonRow.is_unique_name_slice_idx(
                value_to_set, current_slice_idx, rows
            ):
                new_slice_idx = RibbonRow.find_next_slice_index(value_to_set, rows)
                self.data[RibbonColumnNames.SLICE_IDX.value] = new_slice_idx
                self.roa.slice_index.value = new_slice_idx
                slice_index_col = grid.get_column_by_label(
                    RibbonColumnNames.SLICE_IDX.value
                )
                grid.SetCellValue(row, slice_index_col.index, str(new_slice_idx))
            self.data[RibbonColumnNames.NAME.value] = value_to_set
            self.roa.shape.name.value = (
                f"{value_to_set}_{self.data[RibbonColumnNames.SLICE_IDX.value]}"
            )
            self.roa.name.value = value_to_set
        elif col.label == RibbonColumnNames.SLICE_IDX.value:
            current_name = self.data[RibbonColumnNames.NAME.value]
            if not RibbonRow.is_unique_name_slice_idx(
                current_name, int(value_to_set), rows
            ):
                new_slice_idx = RibbonRow.find_next_slice_index(current_name, rows)
                value_to_set = str(new_slice_idx)
            self.data[RibbonColumnNames.SLICE_IDX.value] = int(value_to_set)
            self.roa.slice_index.value = int(value_to_set)
            self.roa.shape.name.value = (
                f"{self.data[RibbonColumnNames.NAME.value]}_{value_to_set}"
            )
        elif col.label == RibbonColumnNames.POSX.value:
            if not value_to_set:
                value_to_set = str(self.data[RibbonColumnNames.POSX.value])
            else:
                posy = self.data[RibbonColumnNames.POSY.value]
                self.data[RibbonColumnNames.POSX.value] = posx = float(value_to_set)
                self.roa.shape.move_to((posx, posy))
                self.roa.shape.cnvs.request_drawing_update()
        elif col.label == RibbonColumnNames.POSY.value:
            if not value_to_set:
                value_to_set = str(self.data[RibbonColumnNames.POSY.value])
            else:
                posx = self.data[RibbonColumnNames.POSX.value]
                self.data[RibbonColumnNames.POSY.value] = posy = float(value_to_set)
                self.roa.shape.move_to((posx, posy))
                self.roa.shape.cnvs.request_drawing_update()
        elif col.label == RibbonColumnNames.ROT.value:
            self.data[RibbonColumnNames.ROT.value] = rot = int(value_to_set)
            self.roa.shape.set_rotation(math.radians(rot))
            self.roa.shape.cnvs.request_drawing_update()
        return value_to_set


class FastEMProjectRibbonsTab(Tab):
    """Tab class for displaying and interacting with ribbon data for a project."""

    def __init__(self, name, button, panel, main_frame, main_data):

        self.tab_data = guimod.MicroscopyGUIData(main_data)
        self.panel = panel
        super().__init__(name, button, panel, main_frame, self.tab_data)
        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.grid = GridBase(panel, size=panel.Parent.Size)
        self.columns = [
            Column(0, RibbonColumnNames.NAME.value),
            Column(
                1,
                RibbonColumnNames.SLICE_IDX.value,
                editor_cls=GridCellNumberEditor,
                editor_args={"min": 0, "max": 1000000},
            ),
            Column(
                2,
                RibbonColumnNames.POSX.value,
                editor_cls=GridCellFloatEditor,
                editor_args={"precision": 9},
            ),
            Column(
                3,
                RibbonColumnNames.POSY.value,
                editor_cls=GridCellFloatEditor,
                editor_args={"precision": 9},
            ),
            Column(
                4,
                RibbonColumnNames.SIZEX.value,
                editor_cls=GridCellFloatEditor,
                is_read_only=True,
            ),
            Column(
                5,
                RibbonColumnNames.SIZEY.value,
                editor_cls=GridCellFloatEditor,
                is_read_only=True,
            ),
            Column(
                6,
                RibbonColumnNames.ROT.value,
                editor_cls=GridCellNumberEditor,
                editor_args={"min": 0, "max": 360},
            ),
        ]
        self.grid.set_columns(self.columns)

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

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            self._initialized_after_show = True

    def _on_panel_size(self, evt):
        """Adjusts the grid size when the panel size changes."""
        self.grid.SetSize(self.panel.Parent.Size)
        self.grid.Layout()
        self.grid.Refresh()

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 3
        else:
            return None
