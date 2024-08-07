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
from collections import deque, namedtuple
from enum import Enum
import logging
from typing import Tuple, List, Union, Deque, Optional

import numpy
import wx
from scipy.spatial import cKDTree

from odemis import model, util
from odemis.gui.comp.overlay.base import WorldOverlay, Vec

# The number of undo actions stored in the stack
UNDO_STACK_DEPTH = 25
# Named tuple for elements stored in undo and redo stacks
ShapeState = namedtuple("ShapeState", ["shape", "state", "action"])
# Enum class to store ShapeState's action
Action = Enum("Action", ["EDIT", "CREATE", "DELETE"])


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
        # The points VA is set to _points if the shape is selected
        self.points = model.ListVA()
        # The shape's center in view coordinates
        self.v_center = Vec((0, 0))
        # Useful for internal points manipulation
        self._points: List[Vec] = []
        self.cnvs = cnvs

    def get_bounding_box(self) -> Tuple[float, float, float, float]:
        """Get the shape's bounding box."""
        return util.get_polygon_bbox(self._points)

    def get_position(self) -> Tuple[float, float]:
        """Get the shape's position."""
        xmin, ymin, xmax, ymax = self.get_bounding_box()
        return ((xmin + xmax) / 2, (ymin + ymax) / 2)

    def get_size(self) -> Tuple[float, float]:
        """Get the shape's size."""
        xmin, ymin, xmax, ymax = self.get_bounding_box()
        return (abs(xmin - xmax), abs(ymin - ymax))

    @abstractmethod
    def check_point_proximity(self, v_point: Tuple[float, float]) -> bool:
        """
        Determine if the view point is in the proximity of the shape.

        :param: v_point: The point in view coordinates.
        :returns: whether the view point is in proximity of the shape.
        """
        pass

    @abstractmethod
    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the tool to given context."""
        pass

    @abstractmethod
    def copy(self):
        """
        :returns: (EditableShape) a new instance of EditableShape with necessary copied attributes.

        """
        pass

    @abstractmethod
    def move_to(self, pos: Union[Tuple[float, float], Vec]):
        """Move the shape's center to a physical position."""
        pass

    @abstractmethod
    def get_state(self):
        """Get the current state of the shape."""
        pass

    @abstractmethod
    def restore_state(self, state):
        """Restore the shape to a given state."""
        pass

    def on_left_down(self, evt):
        evt.Skip()

    def on_left_up(self, evt):
        evt.Skip()

    def on_right_down(self, evt):
        evt.Skip()

    def on_right_up(self, evt):
        evt.Skip()

    def on_motion(self, evt):
        evt.Skip()


class ShapesOverlay(WorldOverlay):
    """
    Overlay that allows for the selection and deletion of a shape in physical coordinates.
    The shape's events and drawing are handled by its WorldOverlay. It can handle multiple shapes.
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
        # True if latest action created a shape (for undo and redo)
        self._is_new_shape = False
        self._selected_shape = None
        self._shape_to_copy = None
        self._shapes: List[EditableShape] = []
        # History of shape's states
        # Stack is a Tuple[EditableShape, Dict[IntEnum, Any], bool] of the shape, its state and
        # a flag stating if it was newly created
        self._undo_stack: Deque[ShapeState] = deque(maxlen=UNDO_STACK_DEPTH)
        self._redo_stack: Deque[ShapeState] = deque(maxlen=UNDO_STACK_DEPTH)
        self._undo_action = False
        self._redo_action = False
        if tool and tool_va:
            self.tool = tool
            tool_va.subscribe(self._on_tool, init=True)

    def clear(self):
        """Remove all shapes and update canvas."""
        self._shapes.clear()

    def remove_shape(self, shape):
        """Remove the shape and update canvas."""
        if shape in self._shapes:
            self._shapes.remove(shape)
            self.cnvs.request_drawing_update()

    def add_shape(self, shape):
        """Add the shape and update canvas."""
        if shape not in self._shapes:
            self._shapes.append(shape)
            self.new_shape._set_value(shape, force_write=True)
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

    def _deselect_shapes(self):
        """Deselect shapes except the selected shape."""
        for shape in self._shapes:
            if shape.selected.value and shape != self._selected_shape:
                shape.selected.value = False

    def _on_tool(self, selected_tool):
        """Update the overlay when it's active and tools change."""
        self.active.value = selected_tool == self.tool

    def _get_shape(self, v_pos: Tuple[float, float]) -> Optional[EditableShape]:
        """
        Find the shape corresponding to the given view position based on proximity.

        It tries to find the shape whose center is closest to the view position and then checks if
        the view position is in the proximity of that shape to confirm it.

        Note:
        This method considers only 4 shapes with centers closest to the view position. In general
        the user should always click as close as possbile to the center of the desired shape. If there
        are overlapping shapes it is advised to click in an area of the desired shape that is not part
        of the intersection of overlapping shapes. Given the above note, this heuristic might fail
        in some corner cases.

        :param v_pos: The position in view coordinates.
        :return: The desired shape, or None if no shape is found.
        """
        if self._shapes:
            v_centers = numpy.array([shape.v_center for shape in self._shapes])
            v_centers_kdtree = cKDTree(v_centers)
             # Query the 4 nearest centers to the given view position
            distances, indices = v_centers_kdtree.query(numpy.array(v_pos), k=4)
            for distance, index in zip(distances, indices):
                # Distances are sorted by nearest first
                # If a distance is infinite, no need to check further as remaining distances will be infinite
                if numpy.isinf(distance):
                    return None
                shape = self._shapes[index]
                if shape.check_point_proximity(v_pos):
                    return shape
        return None

    def _create_new_shape(self):
        """Create a new shape."""
        shape = self.shape_cls(self.cnvs)
        self._shapes.append(shape)
        self.new_shape._set_value(shape, force_write=True)
        return shape

    def _copy_shape(self, v_pos: Tuple[float, float]):
        """Copy a selected shape to a view position as the center."""
        # Copy the shape
        shape = self._shape_to_copy.copy()
        self._shapes.append(shape)
        self.new_shape._set_value(shape, force_write=True)
        # Move the copied shape to a view position
        p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
        shape.move_to(p_pos)
        return shape

    def on_left_down(self, evt):
        if not self.active.value:
            return super().on_left_down(evt)

        self._is_new_shape = False
        # Copy the selected shape by pressing Ctrl + C
        if self._shape_to_copy:
            # Update the selected shape as the newly copied shape
            # whose state can then be appended to undo stack
            self._selected_shape = self._copy_shape(evt.Position)
            self._is_new_shape = True
        # New or previously created shape
        else:
            self._selected_shape = self._get_shape(evt.Position)
            if self._selected_shape is None:
                self._selected_shape = self._create_new_shape()
                self._is_new_shape = True
            self._selected_shape.on_left_down(evt)
            self._deselect_shapes()
        WorldOverlay.on_left_down(self, evt)

    def on_char(self, evt):
        """Delete, unselect or copy the selected shape."""
        if not self.active.value:
            return super().on_char(evt)

        if evt.GetKeyCode() == wx.WXK_CONTROL_Z:
            # NOTE There is no key code such as WXK_SHIFT_CONTROL_Z
            # when Ctrl + Shift + Z is pressed, GetKeyCode() returns WXK_CONTROL_Z
            # in addition to that one can check ShiftDown() flag for the Shift key
            # Ctrl + Shift + Z
            if evt.ShiftDown():
                self._redo_action = True
                self.redo()
            # Ctrl + Z
            else:
                self._undo_action = True
                self.undo()
        elif self._selected_shape:
            if evt.GetKeyCode() == wx.WXK_DELETE:
                state = self._selected_shape.get_state()
                if state:
                    shape_state = ShapeState(self._selected_shape, state, Action.DELETE)
                    self._undo_stack.append(shape_state)
                    self._redo_stack.clear()  # Clear redo stack when a shape's state is saved
                    self.remove_shape(shape_state.shape)
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

        if self._selected_shape:
            self._selected_shape.on_left_up(evt)
            state = self._selected_shape.get_state()
            if state:
                action = Action.CREATE if self._is_new_shape else Action.EDIT
                shape_state = ShapeState(self._selected_shape, state, action)
                if not self._undo_stack or self._undo_stack[-1] != shape_state:
                    self._undo_stack.append(shape_state)
                    self._redo_stack.clear()  # Clear redo stack when a shape's state is saved
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_right_down(self, evt):
        if not self.active.value:
            return super().on_right_down(evt)

        if self._selected_shape:
            self._selected_shape.on_right_down(evt)
        else:
            WorldOverlay.on_right_down(self, evt)

    def on_right_up(self, evt):
        if not self.active.value:
            return super().on_right_up(evt)

        if self._selected_shape:
            self._selected_shape.on_right_up(evt)
        else:
            WorldOverlay.on_right_up(self, evt)

    def on_motion(self, evt):
        if not self.active.value:
            return super().on_motion(evt)

        if self._selected_shape:
            self._selected_shape.on_motion(evt)
        else:
            WorldOverlay.on_motion(self, evt)

    def undo(self):
        """Undo the last action."""
        if not self._undo_stack:
            logging.info(
                "No undo action for %s at %s.", self.__class__.__name__, hex(id(self))
            )
            return
        # If an edit was just made (detected by an empty redo stack) or if a redo action was just performed,
        # we need to revert to the state before the lastest one. Otherwise, we revert to the latest state.
        if not self._redo_stack or self._redo_action:
            self._redo_action = False
            shape_state = self._undo_stack.pop()
            self._redo_stack.append(shape_state)
            if shape_state.action == Action.CREATE:
                self.remove_shape(shape_state.shape)
                return
            elif shape_state.action == Action.DELETE:
                self.add_shape(shape_state.shape)
                return
        if self._undo_stack:
            shape_state = self._undo_stack.pop()
            self._redo_stack.append(shape_state)
            shape_state.shape.restore_state(shape_state.state)
            if shape_state.action == Action.CREATE:
                self.remove_shape(shape_state.shape)
            elif shape_state.action == Action.DELETE:
                self.add_shape(shape_state.shape)
            self.cnvs.request_drawing_update()

    def redo(self):
        """Redo the last undone action."""
        if not self._redo_stack:
            logging.info(
                "No redo action for %s at %s.", self.__class__.__name__, hex(id(self))
            )
            return
        # If an undo action was just performed, we need to revert to the state before the lastest one.
        # Otherwise, we revert to the latest state.
        if self._undo_action:
            self._undo_action = False
            shape_state = self._redo_stack.pop()
            self._undo_stack.append(shape_state)
            if shape_state.action == Action.CREATE:
                self.add_shape(shape_state.shape)
                return
            elif shape_state.action == Action.DELETE:
                self.remove_shape(shape_state.shape)
                return
        if self._redo_stack:
            shape_state = self._redo_stack.pop()
            self._undo_stack.append(shape_state)
            shape_state.shape.restore_state(shape_state.state)
            if shape_state.action == Action.CREATE:
                self.add_shape(shape_state.shape)
            elif shape_state.action == Action.DELETE:
                self.remove_shape(shape_state.shape)
            self.cnvs.request_drawing_update()

    def draw(self, ctx, shift=(0, 0), scale=1.0, dash=False):
        """Draw all the shapes."""
        for shape in self._shapes:
            shape.draw(
                ctx,
                shift,
                scale,
                dash=dash,
            )
