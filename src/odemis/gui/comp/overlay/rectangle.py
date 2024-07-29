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
import math
from typing import Optional, List, Tuple

import cairo
import wx

import odemis.gui as gui
from odemis.gui.comp.overlay.base import (SEL_MODE_ROTATION, SEL_MODE_NONE,
                                          DragMixin, SelectionMixin,
                                          Vec, RectangleEditingMixin,
                                          Label, WorldOverlay)
from odemis.gui.comp.overlay._constants import LINE_WIDTH_THIN, LINE_WIDTH_THICK
from odemis.gui.comp.overlay.shapes import EditableShape
import odemis.util.units as units


class RectangleState:
    def __init__(self, rectangle_overlay) -> None:
        self.p_point1 = rectangle_overlay.p_point1
        self.p_point2 = rectangle_overlay.p_point2
        self.p_point3 = rectangle_overlay.p_point3
        self.p_point4 = rectangle_overlay.p_point4


class RectangleOverlay(EditableShape, RectangleEditingMixin, WorldOverlay):
    """
    A class for creating a rectangular selection overlay based on points.

    It allows defining a rectangular selection by clicking and dragging on the canvas.
    The selected rectangle can be manipulated by dragging its edges or rotating it.

    """
    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        EditableShape.__init__(self, cnvs)
        RectangleEditingMixin.__init__(self, colour, center)
        # RectangleOverlay has attributes and methods of the "WorldOverlay" interface.
        # However, WorldOverlay's __init__() is not called here because mouse events
        # (such as EVT_LEFT_DOWN, EVT_LEFT_UP, etc.) are managed by ShapesOverlay's canvas.
        # ShapesOverlay oversees RectangleOverlays, thereby preventing the redundant processing
        # of mouse events by both ShapesOverlay and RectangleOverlay.
        # If users need to use RectangleOverlay independently of ShapesOverlay, they can
        # explicitly initialize WorldOverlay to manage its own mouse events.

        self.p_point1 = None
        self.p_point2 = None
        self.p_point3 = None
        self.p_point4 = None

        # Labels for the bottom and right side length of the rectangle
        # Call draw_side_labels to use them
        self._side1_label = self._label = Label(
            text="",
            pos=(0, 0),
            font_size=12,
            flip=True,
            align=wx.ALIGN_RIGHT,
            colour=(1.0, 1.0, 1.0),  # default to white
            opacity=1.0,
            deg=None,
            background=None
        )
        self._side2_label = Label(
            text="",
            pos=(0, 0),
            font_size=12,
            flip=True,
            align=wx.ALIGN_RIGHT,
            colour=(1.0, 1.0, 1.0),  # default to white
            opacity=1.0,
            deg=None,
            background=None
        )
        # Label for the rotation angle of the rectangle
        # Call draw_rotation_label to use it
        self._rotation_label = Label(
            text="",
            pos=(0, 0),
            font_size=12,
            flip=True,
            align=wx.ALIGN_CENTRE_HORIZONTAL,
            colour=(1.0, 1.0, 1.0),  # default to white
            opacity=1.0,
            deg=None,
            background=None
        )

    def copy(self):
        """
        :returns: (RectangleOverlay) a new instance of RectangleOverlay with necessary copied attributes.

        """
        shape = RectangleOverlay(self.cnvs)
        shape.colour = self.colour
        shape.restore_state(self.get_state())
        return shape

    def move_to(self, pos):
        """Move the shape's center to a physical position."""
        current_pos  = self.get_position()
        shift = (pos[0] - current_pos[0], pos[1] - current_pos[1])
        self.p_point1 += shift
        self.p_point2 += shift
        self.p_point3 += shift
        self.p_point4 += shift
        self._phys_to_view()
        self._points = self.get_physical_sel()
        self.points.value = self._points

    def get_state(self) -> Optional[RectangleState]:
        """Get the current state of the shape."""
        # Only return the state if the rectangle creation is finished
        # By doing so avoid storing an undo action during rectangle creation
        if self.p_point1 != self.p_point3:
            return RectangleState(self)
        return None

    def restore_state(self, state: RectangleState):
        """Restore the shape to a given state."""
        self.p_point1 = state.p_point1
        self.p_point2 = state.p_point2
        self.p_point3 = state.p_point3
        self.p_point4 = state.p_point4
        self._phys_to_view()
        self._points = self.get_physical_sel()
        self.points.value = self._points

    def check_point_proximity(self, v_point):
        """
        Determine if the view point is in the proximity of the shape.

        Proximity is defined as either:
        - Near the edges of the shape or on the rotation knob.
        - Inside the shape itself.

        :param: v_point: The point in view coordinates.
        :returns: whether the view point is near the edges or on the rotation knob
            or inside the shape.
        """
        if self._points:
            hover, _ = self.get_hover(v_point)
            return hover != gui.HOVER_NONE
        return False

    # Selection clearing

    def clear_selection(self):
        """ Clear the current selection """
        SelectionMixin.clear_selection(self)
        self.p_point1 = None
        self.p_point2 = None
        self.p_point3 = None
        self.p_point4 = None

    def _view_to_phys(self):
        """ Update the physical position to reflect the view position """
        offset = self.cnvs.get_half_buffer_size()
        if self.v_point1:
            self.p_point1 = Vec(self.cnvs.view_to_phys(self.v_point1, offset))
        if self.v_point2:
            self.p_point2 = Vec(self.cnvs.view_to_phys(self.v_point2, offset))
        if self.v_point3:
            self.p_point3 = Vec(self.cnvs.view_to_phys(self.v_point3, offset))
        if self.v_point4:
            self.p_point4 = Vec(self.cnvs.view_to_phys(self.v_point4, offset))

    def _phys_to_view(self):
        """ Update the view position to reflect the physical position """
        offset = self.cnvs.get_half_buffer_size()
        if self.p_point1:
            self.v_point1 = Vec(self.cnvs.phys_to_view(self.p_point1, offset))
        if self.p_point2:
            self.v_point2 = Vec(self.cnvs.phys_to_view(self.p_point2, offset))
        if self.p_point3:
            self.v_point3 = Vec(self.cnvs.phys_to_view(self.p_point3, offset))
        if self.p_point4:
            self.v_point4 = Vec(self.cnvs.phys_to_view(self.p_point4, offset))
        self._calc_edges()

    def get_physical_sel(self):
        """ Return the selected rectangle in physical coordinates

        :return: (list of 4 tuples) Position in m

        """

        if self.p_point1 and self.p_point2 and self.p_point3 and self.p_point4:
            return [self.p_point1, self.p_point2, self.p_point3, self.p_point4]
        return None

    def set_physical_sel(self, rectangle_points: Optional[List[Tuple[float, float]]]):
        """ Set the selection using the provided physical coordinates

        rect (list of 4 tuples): x, y position in m

        """

        if rectangle_points is None:
            self.clear_selection()
        else:
            self.p_point1 = Vec(rectangle_points[0])
            self.p_point2 = Vec(rectangle_points[1])
            self.p_point3 = Vec(rectangle_points[2])
            self.p_point4 = Vec(rectangle_points[3])
            self._phys_to_view()

    # Event Handlers

    def on_left_down(self, evt):
        """
        Similar to the same function in SelectionMixin, but only starts a selection, if .coordinates is undefined.
        If a rectangle has already been selected for this overlay, any left click outside this reactangle will be ignored.
        """
        # Start editing / dragging if the overlay is selected
        if self.selected.value:
            DragMixin._on_left_down(self, evt)

            if self.left_dragging:
                hover, idx = self.get_hover(self.drag_v_start_pos)
                if not hover:
                    # Clicked outside selection
                    if (
                        len(self.points.value) == 0
                    ):  # that's different from SelectionMixin
                        # Create new selection
                        self.start_selection()
                elif hover == gui.HOVER_SELECTION:
                    # Clicked inside selection or near line, so start dragging
                    self.start_drag()
                elif hover == gui.HOVER_ROTATION:
                    self.start_rotation()
                else:
                    # Clicked on an edit point (e.g. an edge or start or end point), so edit
                    self.start_edit(hover, idx)

            self._view_to_phys()
            self.cnvs.update_drawing()

    def on_left_up(self, evt):
        """
        Check if left click was in rectangle. If so, select the overlay. Otherwise, unselect.
        """
        # If the Diagonal points are not the same means the rectangle has been created
        if self.p_point1 != self.p_point3:
            # Activate/deactivate region
            self._view_to_phys()
            self._points = self.get_physical_sel()
            if self._points:
                self.selected.value = self.check_point_proximity(evt.Position)
                if self.selected.value:
                    self.set_physical_sel(self._points)
                    self.points.value = self._points

        # SelectionMixin._on_left_up has some functionality which does not work here, so only call the parts
        # that we need
        self.clear_drag()
        self.selection_mode = SEL_MODE_NONE
        self.edit_hover = None

        self.cnvs.update_drawing()  # Line width changes in .draw when .selected is changed

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.selected.value:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                elif self.hover == gui.HOVER_LINE:
                    if self.hover_direction == gui.HOVER_DIRECTION_NS:
                        self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENS)
                    elif self.hover_direction == gui.HOVER_DIRECTION_EW:
                        self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZEWE)
                elif self.hover == gui.HOVER_ROTATION:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_MAGNIFIER)
                elif self.hover == gui.HOVER_EDGE:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
                else:
                    self.cnvs.reset_dynamic_cursor()
            else:
                self._view_to_phys()

            self.cnvs.update_drawing()

    def draw_side_labels(self, ctx, b_point1: Vec, b_point2: Vec, b_point3: Vec, b_point4: Vec):
        points = {
            self.p_point1: b_point1,
            self.p_point2: b_point2,
            self.p_point3: b_point3,
            self.p_point4: b_point4,
        }
        p_xmin_ymin = min(points.keys(), key=lambda p: (p.x + p.y))
        p_xmax_ymin = max(points.keys(), key=lambda p: (p.x - p.y))
        p_xmax_ymax = max(points.keys(), key=lambda p: (p.x + p.y))
        b_xmin_ymin = points[p_xmin_ymin]
        b_xmax_ymin = points[p_xmax_ymin]
        b_xmax_ymax = points[p_xmax_ymax]

        side1_length = math.sqrt(
            (p_xmax_ymin.x - p_xmin_ymin.x) ** 2 + (p_xmax_ymin.y - p_xmin_ymin.y) ** 2
        )
        side1_length = units.readable_str(side1_length, "m", sig=2)
        side1_angle = math.atan2(
            (b_xmin_ymin.y - b_xmax_ymin.y), (b_xmin_ymin.x - b_xmax_ymin.x)
        )

        side2_length = math.sqrt(
            (p_xmax_ymin.x - p_xmax_ymax.x) ** 2 + (p_xmax_ymin.y - p_xmax_ymax.y) ** 2
        )
        side2_length = units.readable_str(side2_length, "m", sig=2)
        side2_angle = math.atan2(
            (b_xmax_ymax.y - b_xmax_ymin.y), (b_xmax_ymax.x - b_xmax_ymin.x)
        )

        self._side1_label.pos = Vec(
            (b_xmax_ymin.x + b_xmin_ymin.x) / 2 + 8,
            (b_xmax_ymin.y + b_xmin_ymin.y) / 2 + 8,
        )
        self._side1_label.text = side1_length
        self._side1_label.background = (0, 0, 0)  # black
        self._side1_label.deg = math.degrees(side1_angle)
        self._side1_label.draw(ctx)

        self._side2_label.pos = Vec(
            (b_xmax_ymax.x + b_xmax_ymin.x) / 2 + 8,
            (b_xmax_ymax.y + b_xmax_ymin.y) / 2 + 8,
        )
        self._side2_label.text = side2_length
        self._side2_label.background = (0, 0, 0)  # black
        self._side2_label.deg = math.degrees(side2_angle)
        self._side2_label.draw(ctx)

    def draw_rotation_label(self, ctx):
        self._rotation_label.text = units.readable_str(math.degrees(self.rotation), "°", sig=4)
        self._rotation_label.pos = self.cnvs.view_to_buffer(self.v_center)
        self._rotation_label.background = (0, 0, 0)  # black
        self._rotation_label.draw(ctx)

    def draw_edges(self, ctx, b_point1: Vec, b_point2: Vec, b_point3: Vec, b_point4: Vec):
        mid_point12 = Vec((b_point1.x + b_point2.x) / 2, (b_point1.y + b_point2.y) / 2)
        mid_point23 = Vec((b_point2.x + b_point3.x) / 2, (b_point2.y + b_point3.y) / 2)
        mid_point34 = Vec((b_point3.x + b_point4.x) / 2, (b_point3.y + b_point4.y) / 2)
        mid_point41 = Vec((b_point4.x + b_point1.x) / 2, (b_point4.y + b_point1.y) / 2)

        # Draw the edit and rotation points
        b_rotation = Vec(self.cnvs.view_to_buffer(self.v_rotation))
        ctx.set_dash([])
        ctx.set_line_width(1)
        ctx.set_source_rgba(0.1, 0.5, 0.8, 0.8)  # Dark blue-green
        ctx.arc(b_rotation.x, b_rotation.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(mid_point12.x, mid_point12.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(mid_point23.x, mid_point23.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(mid_point34.x, mid_point34.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(mid_point41.x, mid_point41.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point1.x, b_point1.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point2.x, b_point2.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point3.x, b_point3.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point4.x, b_point4.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.stroke()

    def draw(self, ctx, shift=(0, 0), scale=1.0, line_width=4, dash=True):
        """ Draw the selection as a rectangle """
        line_width = LINE_WIDTH_THICK if self.selected.value else LINE_WIDTH_THIN

        if self.p_point1 and self.p_point2 and self.p_point3 and self.p_point4:

            offset = self.cnvs.get_half_buffer_size()
            b_point1 = Vec(self.cnvs.phys_to_buffer(self.p_point1, offset))
            b_point2 = Vec(self.cnvs.phys_to_buffer(self.p_point2, offset))
            b_point3 = Vec(self.cnvs.phys_to_buffer(self.p_point3, offset))
            b_point4 = Vec(self.cnvs.phys_to_buffer(self.p_point4, offset))

            self.update_projection(b_point1, b_point2, b_point3, b_point4, (shift[0], shift[1], scale))

            # draws the dotted line
            ctx.set_line_width(line_width)
            if dash:
                ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            ctx.move_to(*b_point1)
            ctx.line_to(*b_point2)
            ctx.line_to(*b_point3)
            ctx.line_to(*b_point4)
            ctx.close_path()
            ctx.stroke()

            self._calc_edges()
            self.draw_edges(ctx, b_point1, b_point2, b_point3, b_point4)

            # Side labels
            if self.selected.value:
                self.draw_side_labels(ctx, b_point1, b_point2, b_point3, b_point4)

            # Draw the rotation label
            if self.selection_mode == SEL_MODE_ROTATION:
                self.draw_rotation_label(ctx)
