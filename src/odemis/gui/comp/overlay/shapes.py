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
from typing import Tuple

import wx

from odemis import model, util
from odemis.gui.comp.overlay.base import WorldOverlay

class EditableShape(metaclass=ABCMeta):
    """
    This abstract EditableShape class forms the base for a series of classes that
    refer to shape tools like rectangle, ellipse, polygon and their functionality.
    """

    def __init__(self, cnvs):
        """:param cnvs: canvas passed by the shape's overlay and used to draw the shapes."""
        # States if the shape is selected
        self.selected = model.BooleanVA(True)
        # list of nested points (x, y) representing the shape and whose value will be used
        # during ROA acquisition
        self.points = model.ListVA()
        self.cnvs = cnvs

    def get_bounding_box(self) -> Tuple[float, float, float, float]:
        """Get the shape's bounding box."""
        return util.get_polygon_bbox(self.points.value)

    def get_position(self) -> Tuple[float, float]:
        """Get the shape's position."""
        xmin, ymin, xmax, ymax = self.get_bounding_box()
        return ((xmin + xmax) / 2, (ymin + ymax) / 2)

    def get_size(self) -> Tuple[float, float]:
        """Get the shape's size."""
        xmin, ymin, xmax, ymax = self.get_bounding_box()
        return (abs(xmin - xmax), abs(ymin - ymax))

    @abstractmethod
    def is_point_in_shape(self, point: Tuple[float, float]) -> bool:
        """
        Determine if the point is in the shape.

        :param: point: The point in physical coordinates.

        :returns: whether the point is inside the shape or not.

        """
        pass

    @abstractmethod
    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the tool to given context."""
        pass

    @abstractmethod
    def copy_shape(self, shape, shift: Tuple[float, float]):
        """
        Copy a shape's attributes and shift it.
        :param shape: the shape whose specific attributes needs to be copied.
        :param shift: the shift in (x, y).
        """
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
        self._shape_to_copy = None
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
        self._shapes.append(shape)
        self.cnvs.add_world_overlay(shape)
        self.new_shape._set_value(shape, force_write=True)
        # Find the shift to evt.Position
        pos = self._shape_to_copy.get_position()
        current_pos = self.cnvs.view_to_phys(
            evt.Position, self.cnvs.get_half_buffer_size()
        )
        shift_x = current_pos[0] - pos[0]
        shift_y = current_pos[1] - pos[1]
        # Copy the shape and shift it
        shape.copy_shape(self._shape_to_copy, (shift_x, shift_y))

    def on_left_down(self, evt):
        """Start drawing a shape if the overlay is active and there is no selected shape."""
        if not self.active.value:
            return super().on_left_down(evt)

        # Copy a selected shape
        if self._shape_to_copy:
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

        if self._selected_shape and self._selected_shape.selected.value:
            if evt.GetKeyCode() == wx.WXK_DELETE:
                self.remove_shape(self._selected_shape)
            elif evt.GetKeyCode() == wx.WXK_ESCAPE:
                # Unselect the selected shape
                self._selected_shape.selected.value = False
                # Stop copying the shape
                self._shape_to_copy = None
                self.cnvs.set_default_cursor(wx.CURSOR_CROSS)
                self.cnvs.request_drawing_update()
            elif evt.GetKeyCode() == wx.WXK_CONTROL_C:
                # Deselect the selected shape which will be copied
                self._selected_shape.selected.value = False
                self._shape_to_copy = self._selected_shape
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
