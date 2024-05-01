# -*- coding: utf-8 -*-
"""
:created: 2024-02-07
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
import logging
import math

import cairo
import wx

import odemis.gui as gui
from odemis.gui.comp.overlay._constants import LINE_WIDTH_THICK, LINE_WIDTH_THIN
from odemis.gui.comp.overlay.base import SEL_MODE_ROTATION, LineEditingMixin, Vec, WorldOverlay
from odemis.gui.comp.overlay.shapes import EditableShape
import odemis.util.units as units
from odemis.util.raster import point_in_polygon


class PolygonOverlay(WorldOverlay, LineEditingMixin, EditableShape):
    """Overlay representing one polygon."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        :param: cnvs: canvas for the overlay.
        :param: colour (str): hex colour code for the polygon.
        """
        WorldOverlay.__init__(self, cnvs)
        LineEditingMixin.__init__(self, colour)
        EditableShape.__init__(self, cnvs)

        self._label = self.add_label("", align=wx.ALIGN_CENTRE_HORIZONTAL)
        self._rotation_label = self.add_label("", align=wx.ALIGN_CENTRE_HORIZONTAL)
        self.v_point.subscribe(self._on_v_point)

    def copy(self):
        """
        :returns: (PolygonOverlay) a new instance of PolygonOverlay with necessary copied attributes.

        """
        shape = PolygonOverlay(self.cnvs)
        shape.colour = self.colour
        shape.v_points = self.v_points.copy()
        shape._finished = self._finished
        shape._points = self._points.copy()
        shape._phys_to_view()
        shape.points.value = shape._points
        return shape

    def move_to(self, pos):
        """Move the shape's center to a physical position."""
        current_pos  = self.get_position()
        shift = (pos[0] - current_pos[0], pos[1] - current_pos[1])
        self._points = [p + shift for p in self._points]
        self._phys_to_view()
        self.points.value = self._points

    def _on_v_point(self, point):
        """Callback for a new value v_point in ClickMixin."""
        offset = self.cnvs.get_half_buffer_size()
        p_point = Vec(self.cnvs.view_to_phys(point, offset))
        self._points.append(p_point)

    def _view_to_phys(self):
        if len(self.v_points) == len(self._points):
            offset = self.cnvs.get_half_buffer_size()
            for idx, point in enumerate(self.v_points):
                self._points[idx] = Vec(self.cnvs.view_to_phys(point, offset))

    def _phys_to_view(self):
        if len(self._points) == len(self.v_points):
            offset = self.cnvs.get_half_buffer_size()
            for idx, point in enumerate(self._points):
                self.v_points[idx] = Vec(self.cnvs.phys_to_view(point, offset))

    def is_point_in_shape(self, point):
        # A polygon should have atleast 2 points after on_right_up
        if len(self._points) > 2:
            return point_in_polygon(point, self._points)
        return False

    def on_left_down(self, evt):
        if self.active.value and self.selected.value:
            LineEditingMixin._on_left_down(self, evt)
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            is_rotation = self.selection_mode == SEL_MODE_ROTATION
            LineEditingMixin._on_left_up(self, evt)
            if self.right_click_finished:
                self._phys_to_view()
                offset = self.cnvs.get_half_buffer_size()
                p_point = Vec(self.cnvs.view_to_phys(evt.Position, offset))
                self.selected.value = self.is_point_in_shape(p_point)
                # The rotation point is outside the shape and cannot be captured by selection VA
                # Also update the points VA if the selection mode is SEL_MODE_ROTATION
                if self.selected.value or is_rotation:
                    self.points.value = self._points
            self.cnvs.update_drawing()
        WorldOverlay.on_left_up(self, evt)

    def on_right_down(self, evt):
        if self.active.value:
            LineEditingMixin._on_right_down(self, evt)
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_right_down(self, evt)

    def on_right_up(self, evt):
        if self.active.value:
            LineEditingMixin._on_right_up(self, evt)
            self._view_to_phys()
            if len(self._points) <= 2:
                logging.warning("Cannot create a polygon for less than 3 points.")
                self.reset_click_mixin()
                self._points.clear()
            # Set initial value
            self.points.value = self._points
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_right_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.selected.value:
            LineEditingMixin._on_motion(self, evt)
            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                elif self.hover == gui.HOVER_EDGE:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
                elif self.hover == gui.HOVER_ROTATION:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_MAGNIFIER)
                else:
                    self.cnvs.reset_dynamic_cursor()
            else:
                self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_motion(self, evt)

    def draw_edges(self, ctx):
        # Draw the edit and rotation points
        b_rotation = Vec(self.cnvs.view_to_buffer(self.v_rotation))
        ctx.set_dash([])
        ctx.set_line_width(1)
        ctx.set_source_rgba(0.1, 0.5, 0.8, 0.8)  # Dark blue-green
        ctx.arc(b_rotation.x, b_rotation.y, 4, 0, 2 * math.pi)
        ctx.fill()
        offset = self.cnvs.get_half_buffer_size()
        for point in self._points:
            b_pos = self.cnvs.phys_to_buffer(point, offset)
            ctx.arc(b_pos[0], b_pos[1], 4, 0, 2 * math.pi)
            ctx.fill()
        ctx.stroke()

    def draw_rotation_label(self, ctx):
        self._rotation_label.text = units.readable_str(math.degrees(self.rotation), "°", sig=4)
        self._rotation_label.pos = self.cnvs.view_to_buffer(self.center)
        self._rotation_label.background = (0, 0, 0)  # black
        self._rotation_label.draw(ctx)

    def draw(self, ctx, shift=(0, 0), scale=1.0, line_width=4, dash=True):
        """Draw the selection as a polygon"""
        if self._points:
            offset = self.cnvs.get_half_buffer_size()

            # draws the dotted line
            if (self.active.value and self.selected.value):
                line_width = LINE_WIDTH_THICK
            else:
                line_width = LINE_WIDTH_THIN

            ctx.set_line_width(line_width)
            if dash:
                ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)

            # draw the polygon
            shiftscale = (shift[0], shift[1], scale)
            for idx, point in enumerate(self._points):
                b_pos = self.cnvs.phys_to_buffer(point, offset)
                self.update_projection(idx, b_pos, shiftscale)
                if idx == 0:
                    ctx.move_to(*b_pos)
                else:
                    ctx.line_to(*b_pos)
            self.last_shiftscale = shiftscale

            # if the polygon creation is not finished in ClickMixin
            if not self.right_click_finished:
                # draw the line to current position of the cursor
                p_last_point = self._points[-1]
                b_last_point = self.cnvs.phys_to_buffer(p_last_point, offset)
                p_current_pos = self.cnvs.view_to_phys(self.v_pos, offset)
                b_current_pos = self.cnvs.phys_to_buffer(p_current_pos, offset)
                ctx.move_to(*b_last_point)
                ctx.line_to(*b_current_pos)
                ctx.stroke()

                # label creation
                # unit vector for physical coordinates
                dx, dy = p_current_pos[0] - p_last_point[0], p_current_pos[1] - p_last_point[1]

                # unit vector for buffer (pixel) coordinates
                dpx, dpy = b_current_pos[0] - b_last_point[0], b_current_pos[1] - b_last_point[1]

                phi = math.atan2(dx, dy) % (2 * math.pi)  # phi angle in radians

                # Find the side length by calculating the Euclidean distance
                length = math.hypot(dx, dy)  # side length in physical coordinates
                pixel_length = math.hypot(dpx, dpy)  # side length in pixels

                self._label.deg = math.degrees(phi + (math.pi / 2))  # angle of the side label

                # Distance display with 3 digits
                size_lbl = units.readable_str(length, "m", sig=3)
                self._label.text = size_lbl

                # Display length in the middle of the side and determine whether to flip the label or not,
                # depending on the angle.
                l_pos = (
                    (b_last_point[0] + b_current_pos[0]) / 2,
                    (b_last_point[1] + b_current_pos[1]) / 2,
                )
                self._label.flip = 0 < phi < math.pi

                pos = Vec(l_pos[0], l_pos[1])
                self._label.pos = pos

                # If the side is smaller than 1 pixel, make it seem as 1 point (1 pixel) and decrease the font size to 5pt.
                # Only the move area of the side is available, without the option of editing the start, end positions.
                if pixel_length <= 1:
                    self._label.font_size = 5
                else:
                    if pixel_length < 40:  # about the length of the side
                        self._label.font_size = 9
                    else:
                        self._label.font_size = 14
                self._label.background = (0, 0, 0)  # background
                self._label.draw(ctx)
            else:
                ctx.close_path()
                ctx.stroke()
                self._calc_edges()
                self.draw_edges(ctx)
                # Draw the rotation label
                if self.selection_mode == SEL_MODE_ROTATION:
                    self.draw_rotation_label(ctx)
