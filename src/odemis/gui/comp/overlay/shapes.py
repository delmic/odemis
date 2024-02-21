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
from typing import Union
import wx

from odemis import model
from odemis.gui.comp.overlay.base import WorldOverlay
from odemis.gui.comp.overlay.ellipse import EllipseOverlay
from odemis.gui.comp.overlay.rectangle import RectangleOverlay


class ShapesOverlay(WorldOverlay):
    """
    Overlay that allows for the selection and deletion of a shape in physical coordinates.
    It can handle multiple shapes.
    """

    def __init__(
        self, cnvs, shape_cls: Union[EllipseOverlay, RectangleOverlay], tool=None, tool_va=None
    ):
        """
        cnvs: canvas for the overlay.
        tool_va (None or VA of value TOOL_TOOL_RECTANGLE, TOOL_ELLIPSE): New shapes can be
        created. If None, then no shape can be added by the user.
        """
        WorldOverlay.__init__(self, cnvs)
        self.shape_cls = shape_cls
        # VA which changes value upon shape's overlay creation
        self.shape_overlay = model.VigilantAttribute(None, readonly=True)
        self._selected_shape = None
        self._shapes = []
        if tool and tool_va:
            self.tool = tool
            tool_va.subscribe(self._on_tool, init=True)

    def clear(self):
        """Remove all shapes and update canvas."""
        self._activate_shapes(False)
        self._shapes.clear()

    def remove_overlay(self, overlay):
        """Remove a shape's overlay and update canvas."""
        if overlay in self._shapes:
            overlay.active.value = False
            self._shapes.remove(overlay)
            self.cnvs.remove_world_overlay(overlay)
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
        Returns: the most appropriate shape.
            If no shape is found, it returns None.
        """
        if self._shapes:
            pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
            for shape in self._shapes[::-1]:
                if shape.is_point_in_overlay(pos):
                    return shape
        return None

    def on_left_down(self, evt):
        """Start drawing a shape if the overlay is active and there is no selected shape."""
        if not self.active.value:
            return super().on_left_down(evt)

        self._selected_shape = self._get_shape(evt)
        if self._selected_shape is None:
            shape = self.shape_cls(self.cnvs)
            self._shapes.append(shape)
            self.cnvs.add_world_overlay(shape)
            self.shape_overlay._set_value(shape, force_write=True)
            self._selected_shape = shape
        self._selected_shape.active.value = True
        self._selected_shape.on_left_down(evt)

    def on_char(self, evt):
        """Delete the selected shape."""
        if not self.active.value:
            return super().on_char(evt)

        if evt.GetKeyCode() == wx.WXK_DELETE:
            for shape in self._shapes:
                if shape.active.value:
                    self.remove_overlay(shape)
                    break
            self._selected_shape = None
        else:
            WorldOverlay.on_char(self, evt)

    def on_left_up(self, evt):
        """Stop drawing a shape."""
        if not self.active.value:
            return super().on_left_up(evt)

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
