# -*- coding: utf-8 -*-
"""
:created: 2024-02-02
:author: Nandish Patel
:copyright: © 2024 Nandish Patel, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""
from abc import ABCMeta, abstractmethod
import copy

import wx

from odemis import model, util
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui.comp.overlay.base import WorldOverlay

UNDEFINED_POS_SIZE = (0, 0)


class EditableShape(metaclass=ABCMeta):
    """
    This abstract EditableShape class forms the base for a series of classes that
    refer to shape tools like rectangle, ellipse, polygon and their functionality.
    """

    def __init__(self, cnvs):
        """:param cnvs: canvas passed by the shape's overlay and used to draw the shapes."""
        # States if the shape is selected
        # The VA's value should always be set using _set_value(selected, must_notify=True)
        # method because the points and/or coordinates should always be updated id the shape
        # was selected
        self.selected = model.BooleanVA(True)
        # list of nested points (y, x) representing the shape and whose value will be used
        # during ROA acquisition
        # FastEMROA.get_poly_field_indices expects list of nested tuples (y, x)
        self.points = model.ListVA()
        # Any shape can be represented by a bounding box and the coordinates (l, t, r, b) are
        # stored here
        self.coordinates = model.TupleVA(UNDEFINED_ROI)
        self._points = []
        # The position of shape i.e. the center of the shape's bounding box
        self.position = model.TupleVA(UNDEFINED_POS_SIZE)
        # The size of the shape's bounding box
        self.size = model.TupleVA(UNDEFINED_POS_SIZE)
        self.cnvs = cnvs
        self.selected.subscribe(self._on_selected)
        self.coordinates.subscribe(self._on_coordinates)

    def _on_selected(self, selected):
        """
        Callback for selected VA. Override this method in the shape's overlay if one does
        not want to set the points and/or coordinates value.

        """
        if selected:
            self.points.value = [(y, x) for x, y in self._points]
            self.coordinates.value = util.get_polygon_bbox(self._points)

    def shift_points(self, shift: tuple = (0, 0)):
        """
        Shift the points representing the shape.

        :param shift: (tuple) the shift in (x, y).

        """
        for idx, point in enumerate(self._points):
            self._points[idx] = (point[0] + shift[0], point[1] + shift[1])

    def _on_coordinates(self, coordinates):
        """
        Callback for coordinates VA. Override this method in the shape's overlay if one does
        not want to set the position and/or size value.

        """
        self.position.value = (
            (coordinates[0] + coordinates[2]) / 2,
            (coordinates[1] + coordinates[3]) / 2,
        )
        self.size.value = (
            abs(coordinates[0] - coordinates[2]),
            abs(coordinates[1] - coordinates[3]),
        )

    @abstractmethod
    def is_point_in_shape(self, point) -> bool:
        """
        Determine if the point is in the shape.

        :param: point: (tuple) The point in physical coordinates.

        :returns: (bool) whether the point is inside the shape or not.

        """
        pass

    @abstractmethod
    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the tool to given context."""
        pass


class ShapesOverlay(WorldOverlay):
    """
    Overlay that allows for the selection and deletion of a shape in physical coordinates.
    It can handle multiple shapes.
    """

    def __init__(self, cnvs, shape_cls, tool=None, tool_va=None):
        """
        :param cnvs: canvas for the overlay.
        :param shape_cls: (EditableShape) The shape class whose creation, editing and removal
            will be handled by this class.
        :param tool_va: (None or VA of value TOOL_*) New shapes can be created. If None, then
            no shape can be added by the user.
        """
        if not issubclass(shape_cls, EditableShape):
            raise ValueError("Not a subclass of EditableShape!")
        WorldOverlay.__init__(self, cnvs)
        self.shape_cls = shape_cls
        # VA which changes value upon new shape's creation
        self.new_shape = model.VigilantAttribute(None, readonly=True)
        self._selected_shape = None
        self._copy_key_pressed = False
        self._shapes = []
        if tool and tool_va:
            self.tool = tool
            tool_va.subscribe(self._on_tool, init=True)

    def clear(self):
        """Remove all shapes and update canvas."""
        self._activate_shapes(False)
        self._shapes.clear()

    def remove_shape(self, shape):
        """Remove the shape and update canvas."""
        if shape in self._shapes:
            shape.active.value = False
            self._shapes.remove(shape)
            self.cnvs.remove_world_overlay(shape)
            self.cnvs.request_drawing_update()

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CURSOR_CROSS)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def _activate_shapes(self, flag=True):
        """Activate or de-activate the shapes."""
        for shape in self._shapes:
            shape.active.value = flag

    def _on_tool(self, selected_tool):
        """Update the overlay when it's active and tools change."""
        if selected_tool == self.tool:
            self.active.value = True
            self._activate_shapes(True)
        else:
            self.active.value = False
            self._activate_shapes(False)
            self.cnvs.reset_default_cursor()

    def _get_shape(self, evt):
        """
        Find a shape corresponding to the given on_left_down event position.
        :return: the most appropriate shape.
            If no shape is found, it returns None.
        """
        if self._shapes:
            pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
            for shape in self._shapes[::-1]:
                if shape.is_point_in_shape(pos):
                    return shape
        return None

    def _create_new_shape(self):
        """Create a new shape."""
        shape = self.shape_cls(self.cnvs)
        self._shapes.append(shape)
        self.cnvs.add_world_overlay(shape)
        self.new_shape._set_value(shape, force_write=True)
        self._selected_shape = shape

    def _copy_shape(self, evt):
        """Copy a selected shape to evt.Position as the center."""
        # Create a new shape instance since copy.deepcopy() for shape_cls does not work
        shape = self.shape_cls(self.cnvs)
        shape._points = copy.deepcopy(self._selected_shape._points)
        # Find the shift to evt.Position
        pos = self._selected_shape.position.value
        current_pos = self.cnvs.view_to_phys(
            evt.Position, self.cnvs.get_half_buffer_size()
        )
        shift_x = current_pos[0] - pos[0]
        shift_y = current_pos[1] - pos[1]
        # Shift the shape
        shape.shift_points((shift_x, shift_y))
        # Handle copying polygon
        if hasattr(shape, "v_points") and hasattr(shape, "_finished"):
            # Create v_points list the same size as _points
            # update_projection() will update the correct v_points on draw()
            shape.v_points = [0] * len(shape._points)
            # Set ClickMixin finished flag
            shape._finished = True
        self._shapes.append(shape)
        self.cnvs.add_world_overlay(shape)
        self.new_shape._set_value(shape, force_write=True)
        self._selected_shape = shape
        self._selected_shape.active.value = True
        # Forcefully make the shape selected
        self._selected_shape.selected._set_value(True, must_notify=True)
        self._copy_key_pressed = False

    def on_left_down(self, evt):
        if not self.active.value:
            return super().on_left_down(evt)

        self.cnvs.set_default_cursor(wx.CURSOR_CROSS)
        # Copy a selected shape
        if self._copy_key_pressed and self._selected_shape:
            self._copy_shape(evt)
        # New or previously created shape
        else:
            self._selected_shape = self._get_shape(evt)
            if self._selected_shape is None:
                self._create_new_shape()
            self._selected_shape.active.value = True
            self._selected_shape.on_left_down(evt)
        WorldOverlay.on_left_down(self, evt)

    def on_char(self, evt):
        """Delete, unselect or copy the selected shape."""
        if not self.active.value:
            return super().on_char(evt)

        if self._selected_shape:
            if evt.GetKeyCode() == wx.WXK_DELETE:
                self.remove_shape(self._selected_shape)
            elif evt.GetKeyCode() == wx.WXK_ESCAPE:
                # Unselect the selected shape
                self._selected_shape.selected.value = False
                # Stop copying the selected shape
                self._copy_key_pressed = False
                self.cnvs.set_default_cursor(wx.CURSOR_CROSS)
                self.cnvs.request_drawing_update()
            elif evt.GetKeyCode() == wx.WXK_CONTROL_C:
                self._copy_key_pressed = True
                # Deselect the selected shape which will be copied
                self._selected_shape.selected.value = False
                self.cnvs.set_default_cursor(wx.CURSOR_BULLSEYE)
                self.cnvs.request_drawing_update()
        else:
            WorldOverlay.on_char(self, evt)

    def on_left_up(self, evt):
        if not self.active.value:
            return super().on_left_up(evt)

        self._selected_shape = self._get_shape(evt)
        if self._selected_shape:
            self._selected_shape.on_left_up(evt)
        else:
            WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw all the shapes."""
        for shape in self._shapes:
            shape.draw(
                ctx,
                shift,
                scale,
            )
