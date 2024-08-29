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
import logging
from abc import ABCMeta, abstractmethod
from functools import partial
from typing import List

import wx
from wx.grid import (
    EVT_GRID_CELL_CHANGING,
    EVT_GRID_CELL_LEFT_CLICK,
    EVT_GRID_LABEL_LEFT_CLICK,
    Grid,
    GridCellAttr,
    GridCellBoolEditor,
    GridCellBoolRenderer,
    GridCellEditor,
    GridCellTextEditor,
)

from odemis.gui import FG_COLOUR_DIS, FG_COLOUR_EDIT

wxEVT_GRID_ROW_CHANGED = wx.NewEventType()
EVT_GRID_ROW_CHANGED = wx.PyEventBinder(wxEVT_GRID_ROW_CHANGED, 1)

DEFAULT_PARENT = "None"


class DynamicGridCellComboBoxEditor(GridCellEditor):
    """
    A custom GridCellEditor that uses wx.ComboBox for dynamic choices.
    """

    def __init__(self, choices: list):
        super().__init__()
        self.default_choice = DEFAULT_PARENT
        if self.default_choice not in choices:
            self.choices = choices.append(self.default_choice)
        else:
            self.choices = choices
        self._control = None
        self._row = -1
        self._col = -1
        self._grid = None

    def Create(self, parent, id, evtHandler):
        """
        Create the wx.ComboBox control for the editor.
        """
        self._control = wx.ComboBox(
            parent, id=id, choices=self.choices, style=wx.CB_READONLY
        )
        self.SetControl(self._control)

    def SetParameters(self, params):
        """
        Update the choices for the editor.
        """
        self.choices = [choice.strip() for choice in params.split(",")]
        if self._control:
            self._control.SetItems(self.choices)
            # Update current value if it's not in the new choices
            current_value = self._control.GetValue()
            if current_value not in self.choices:
                self._control.SetValue(self.default_choice)

    def Show(self, show, attr=None):
        """
        Show the editor control.
        """
        if self._control:
            self._control.Show(show=show)

    def Hide(self):
        """
        Hide the editor control.
        """
        if self._control:
            self._control.Hide()

    def Reset(self):
        """
        Reset the editor control.
        """
        if self._control:
            self._control.SetValue(self.default_choice)

    def BeginEdit(self, row, col, grid):
        """
        BeginEdit(row, col, grid)

        Fetch the value from the table and prepare the edit control to begin
        editing.
        """
        # Store the row, column, and grid for later use
        self._row = row
        self._col = col
        self._grid = grid

        # Get the current value of the cell and set it in the editor control
        value = grid.GetCellValue(row, col)
        if self._control:
            self._control.SetValue(value)

    def EndEdit(self, row, col, grid, old_value):
        """
        EndEdit(row, col, grid, old_value)

        End editing the cell.
        """
        if not self._control:
            return None

        new_value = self._control.GetValue()

        # Always return new_value, regardless of whether it has changed
        return new_value

    def ApplyEdit(self, row, col, grid):
        """
        ApplyEdit(row, col, grid)

        Effectively save the changes in the grid.
        """
        if not self._control:
            return None

        # Fetch the new value from the editor control and set it in the grid
        new_value = self._control.GetValue()
        grid.SetCellValue(row, col, new_value)


class RowChangedEvent(wx.PyCommandEvent):
    """
    Event triggered when a row in the grid has changed.

    :param id: (int) The identifier for the event.
    :param row: (int) The index of the row that has changed.
    """

    def __init__(self, id, row):
        wx.PyCommandEvent.__init__(self, wxEVT_GRID_ROW_CHANGED, id)
        self.row = row

    def GetRow(self):
        """
        Retrieves the index of the row that has changed.

        :return: (int) The index of the row.
        """
        return self.row


class Column:
    """
    Represents a column in the grid.

    :param index: (int) The index of the column.
    :param label: (str) The label of the column.
    :param editor_cls: The editor class used for this column (default is GridCellTextEditor).
    :param editor_args: (dict) Arguments to be passed to the editor class (default is an empty dictionary).
    :param is_read_only: (bool) Whether the column is read-only (default is False).
    """

    def __init__(
        self,
        index,
        label,
        editor_cls=GridCellTextEditor,
        editor_args={},
        is_read_only=False,
    ):
        self.index = index
        self.label = label
        self.editor_cls = editor_cls
        self.editor_args = editor_args
        self.is_read_only = is_read_only


class Row(metaclass=ABCMeta):
    """Abstract base class for representing a row in the grid."""

    def __init__(self, data: dict, roa, index=-1):
        """
        :param data: (dict) A dictionary of data for the row, where keys are column labels.
        :param roa: (FastEMROA) A FastEMROA associated with the row.
        :param index: (int) The index of the row (default is -1).
        """
        self.index = index
        self.data = data
        self.roa = roa
        self.parent_name = None

    def get_value(self, col_label):
        """
        Retrieves the value of a cell in the row.

        :param col_label: (str) The label of the column to retrieve the value from.
        :return: The value of the cell in the specified column.
        """
        return self.data.get(col_label)

    def set_value(self, col_label, value):
        """
        Sets the value of a cell in the row.

        :param col_label: (str) The label of the column to set the value in.
        :param value: The value to set in the specified column.
        """
        self.data[col_label] = value

    @abstractmethod
    def on_cell_changing(self, new_value, row, col, grid):
        """
        Called when a cell's value is about to change.

        :param new_value: (str) The new value to be set in the cell.
        :param row: (int) The index of the row in the grid.
        :param col: (int) The index of the column in the grid.
        :param grid: (Grid) The grid instance where the change is occurring.
        """
        pass

    @abstractmethod
    def update_data(self):
        """
        Updates the row's data.
        """
        pass

    @abstractmethod
    def to_dict(self):
        """
        Converts the row's data to a dictionary representation.

        :return: (dict) The dictionary representation of the row's data.
        """
        pass

    @staticmethod
    @abstractmethod
    def from_dict(row: dict, tab_data):
        """
        Creates an instance of the row from a dictionary representation.

        :param row: (dict) The dictionary containing row data.
        :param tab_data: (MicroscopyGUIData) Additional data or context required for creation.
        :return: An instance of the row.
        """
        pass

    @staticmethod
    @abstractmethod
    def find_next_slice_index(name, rows):
        """
        Finds the next available index for the given name in the list of rows.

        :param name: (str) The name for which the index is to be found.
        :param rows: (list) The list of existing rows.
        :return: (int) The next available index for the given name.
        """
        pass

    @staticmethod
    @abstractmethod
    def is_unique_name_slice_idx(name, slice_idx, rows):
        """
        Checks if the given index is unique for the specified name in the list of rows.

        :param name: (str) The name to check for uniqueness.
        :param slice_idx: (int) The index to check for uniqueness.
        :param rows: (list) The list of existing rows.
        :return: (bool) True if the index is unique for the given name, False otherwise.
        """
        pass


class GridBase(Grid):
    """A grid component with advanced row and column management features."""

    def __init__(self, parent, size):
        """
        Initializes the grid with the given parent and size.

        :param parent: The parent window or control for this grid.
        :param size: The size of the grid.
        """
        super().__init__(parent, size=size)
        self.columns: List[Column] = []
        self.rows: List[Row] = []
        self.initialized = False  # Flag to track if grid has been initialized
        self._row_shape_points_sub_callback = {}
        self._row_shape_selected_sub_callback = {}
        self.Bind(EVT_GRID_CELL_CHANGING, self._on_cell_changing)
        self.Bind(EVT_GRID_LABEL_LEFT_CLICK, self._on_label_left_click)
        # Bind the cell click event
        self.Bind(EVT_GRID_CELL_LEFT_CLICK, self._on_cell_left_click)

    def _on_cell_left_click(self, evt):
        """Handles the event when a cell is left-clicked."""
        row, col = evt.GetRow(), evt.GetCol()
        editor = self.GetCellEditor(row, col)

        # ROAColumnNames.FIELDS's editor_cls is GridCellBoolEditor which is a checkbox
        # The checking and unchecking behaviour of the checkbox by GridCellBoolEditor is not as expected
        # Veto the left click event for the checkbox and handle it manually to toggle the checkbox
        if isinstance(editor, GridCellBoolEditor):
            current_value = self.GetCellValue(row, col)
            # Toggle the checkbox manually
            # For the GridCellBoolEditor checked value is "1" and unchecked value is ""
            new_value = "1" if current_value == "" else ""
            row_obj = self.rows[row]
            row_obj.on_cell_changing(new_value, row, col, self)
            self.SetCellValue(row, col, new_value)
            evt.Veto()
        evt.Skip()

    def clear(self):
        """Clears the grid, deleting all rows and columns, and resetting internal state."""
        self.ClearGrid()
        # Delete all rows and columns
        num_rows = self.GetNumberRows()

        if num_rows > 0:
            self.DeleteRows(0, num_rows)

        self.rows.clear()
        self._row_shape_points_sub_callback.clear()
        self._row_shape_selected_sub_callback.clear()
        self.AutoSizeColumns()

    def _deselect_row_shapes(self):
        """Deselects all row shapes."""
        for row in self.rows:
            row.roa.shape.selected.value = False

    def _on_label_left_click(self, evt):
        """Handles the event when a column label is left-clicked."""
        row, col = evt.GetRow(), evt.GetCol()
        if col == -1:
            row_obj = self.rows[row]
            row_obj.roa.shape.selected.value = True
            row_obj.roa.shape.cnvs.request_drawing_update()
        evt.Skip()

    def _on_cell_changing(self, evt):
        """Handles the event when a cell's value is about to change."""
        row, col = evt.GetRow(), evt.GetCol()
        current_value = self.GetCellValue(row, col)
        new_value = evt.GetString()
        col_obj = self.columns[col]
        row_obj = self.rows[row]
        logging.debug(
            f"({row}, {col}) {row_obj.roa.shape.name.value} -> {col_obj.label} value changing from {current_value} to {new_value}"
        )
        value_to_set = row_obj.on_cell_changing(new_value, row, col, self)
        if value_to_set != new_value:
            self.SetCellValue(row, col, value_to_set)
            # Vetoe the user cell change if the new_value needs to manipulated
            evt.Veto()
        else:
            evt.Skip()

    def update_row(self, row: Row):
        """Updates the specified row with new values and editors."""
        for col in self.columns:
            if col.editor_cls != type(self.GetCellEditor(row.index, col.index)):
                editor = col.editor_cls(**col.editor_args)
                self.SetCellEditor(row.index, col.index, editor)
                if col.editor_cls == GridCellBoolEditor:
                    self.SetCellRenderer(row.index, col.index, GridCellBoolRenderer())
                    self.SetCellAlignment(
                        row.index, col.index, wx.ALIGN_CENTER, wx.ALIGN_CENTER
                    )
            value = row.get_value(col.label)
            self.SetCellValue(row.index, col.index, str(value))
        self.AutoSizeColumns()

    def set_columns(self, columns: List[Column]):
        """
        Set up columns in the grid.
        :param columns: List of Column objects.
        """
        if self.initialized:
            return  # Avoid re-initializing the grid

        n_cols = len(columns)
        self.CreateGrid(0, n_cols)
        self.columns = [0] * n_cols

        for col in columns:
            self.columns[col.index] = col
            self.SetColLabelValue(col.index, col.label)
            attr = GridCellAttr()
            if col.is_read_only:
                attr.SetReadOnly(True)
                attr.SetTextColour(FG_COLOUR_DIS)
            else:
                attr.SetTextColour(FG_COLOUR_EDIT)
            self.SetColAttr(col.index, attr)

        self.AutoSizeColumns()
        self.Refresh()  # Ensure the grid refreshes after setting columns
        self.initialized = True

    def _on_row_shape_points(self, _, row):
        """Handles updates to row shape points."""
        row.update_data()
        self.update_row(row)
        self.Refresh()

    def _on_row_shape_selected(self, selected, row):
        """
        Handles changes to row shape selection.

        :param selected: Boolean indicating if the row shape is selected.
        :param row: The Row object whose shape selection has changed.
        """
        if selected:
            self.SelectRow(row.index, addToSelected=True)
        else:
            self.DeselectRow(row.index)

    def add_row(self, row: Row):
        """
        Adds a new row to the grid.

        :param row: The Row object to add.
        """
        if len(row.data.keys()) != self.GetNumberCols():
            raise ValueError("Row data length does not match number of columns")

        self.AppendRows(1)
        if row.index == -1:
            row.index = self.GetNumberRows() - 1
            self.rows.insert(row.index, row)
            self.SelectRow(row.index, addToSelected=True)
        else:
            self.rows[row.index] = row

        row_shape_points_sub_callback = partial(self._on_row_shape_points, row=row)
        self._row_shape_points_sub_callback[row] = row_shape_points_sub_callback
        row.roa.shape.points.subscribe(row_shape_points_sub_callback)
        row_shape_selected_sub_callback = partial(self._on_row_shape_selected, row=row)
        self._row_shape_selected_sub_callback[row] = row_shape_selected_sub_callback
        row.roa.shape.selected.subscribe(row_shape_selected_sub_callback)
        self.update_row(row)
        event = RowChangedEvent(self.GetId(), row.index)
        wx.PostEvent(self, event)

    def get_column_values_by_label(self, label):
        """
        Gets all values from a column identified by the column label.

        :param label: The label of the column.
        :return: List of values from the column.
        """
        col = self.get_column_by_label(label)
        if col is None:
            raise ValueError(f"Column with label '{label}' not found.")
        return [
            self.GetCellValue(row, col.index) for row in range(self.GetNumberRows())
        ]

    def get_column_values(self, index):
        """
        Gets all values from a column identified by its index.

        :param index: The index of the column.
        :return: List of values from the column.
        """
        if index < 0 or index >= len(self.columns):
            return []
        return [self.GetCellValue(row, index) for row in range(self.GetNumberRows())]

    def get_column_editors(self, index):
        """
        Gets the editors for a column identified by its index.

        :param index: The index of the column.
        :return: List of editors for the column.
        """
        if index < 0 or index >= len(self.columns):
            return []
        return [self.GetCellEditor(row, index) for row in range(self.GetNumberRows())]

    def get_column_by_label(self, label):
        """
        Gets the index of a column by its label.

        :param label: The label of the column.
        :return: The Column object if found, otherwise None.
        """
        for col in self.columns:
            if col.label == label:
                return col
        return None

    def get_row_by_shape(self, shape):
        """
        Gets a row by its shape.

        :param shape: The shape to search for.
        :return: The Row object if found, otherwise None.
        """
        for row in self.rows:
            if row.roa.shape == shape:
                return row
        return None

    def swap_rows(self, row1, row2):
        """
        Swaps the contents of two rows, including cell editors if they exist.

        :param row1: Index of the first row.
        :param row2: Index of the second row.
        """
        for col in range(self.GetNumberCols()):
            value1 = self.GetCellValue(row1, col)
            value2 = self.GetCellValue(row2, col)
            self.SetCellValue(row1, col, value2)
            self.SetCellValue(row2, col, value1)

            # Swap the editors if they exist
            editor1 = self.GetCellEditor(row1, col)
            editor2 = self.GetCellEditor(row2, col)
            self.SetCellEditor(row1, col, editor2)
            self.SetCellEditor(row2, col, editor1)

        row1_obj = self.rows[row1]
        row2_obj = self.rows[row2]
        row1_obj.index = row2
        row2_obj.index = row1
        self.rows[row1_obj.index] = row1_obj
        self.rows[row2_obj.index] = row2_obj

    def delete_row(self, index):
        """
        Deletes a row from the grid and updates internal state.

        :param index: Index of the row to delete.
        """
        if index < 0 or index >= len(self.rows):
            return  # Out of range, do nothing
        self.DeleteRows(pos=index, numRows=1)
        row = self.rows.pop(index)
        for idx, remaining_row in enumerate(self.rows):
            remaining_row.index = idx
        row.roa.shape.points.unsubscribe(row.roa.on_points)
        row.roa.shape.points.unsubscribe(self._row_shape_points_sub_callback[row])
        row.roa.shape.selected.unsubscribe(self._row_shape_selected_sub_callback[row])
        row.roa.shape.cnvs.remove_shape(row.roa.shape)
        row.roa.shape = None
        del self._row_shape_points_sub_callback[row]
        del self._row_shape_selected_sub_callback[row]
        del row
        event = RowChangedEvent(self.GetId(), None)
        wx.PostEvent(self, event)

    def move_rows_up(self, rows):
        """
        Moves the specified rows up by one position.

        :param rows: List of row indices to move.
        """
        for row in sorted(rows):
            if row > 0:
                self.swap_rows(row, row - 1)

    def move_rows_down(self, rows):
        """
        Moves the specified rows down by one position.

        :param rows: List of row indices to move.
        """
        for row in sorted(rows, reverse=True):
            if row < self.GetNumberRows() - 1:
                self.swap_rows(row, row + 1)

    def delete_rows(self, rows):
        """
        Deletes the specified rows from the grid.

        :param rows: List of row indices to delete.
        """
        for row in sorted(rows, reverse=True):
            self.delete_row(row)
