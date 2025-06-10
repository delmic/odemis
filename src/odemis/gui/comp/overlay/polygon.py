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
from typing import Optional, Tuple

import cairo
import wx

import odemis.gui as gui
import odemis.util.units as units
from odemis.gui.comp.overlay._constants import LINE_WIDTH_THICK, LINE_WIDTH_THIN
from odemis.gui.comp.overlay.base import (
    SEL_MODE_ROTATION,
    SEL_MODE_EDIT,
    Label,
    LineEditingMixin,
    Vec,
    WorldOverlay,
)
from odemis.gui.comp.overlay.shapes import EditableShape
from odemis.util.conversion import frgba_to_hex, hex_to_frgba


class PolygonState:
    def __init__(self, polygon_overlay) -> None:
        self._finished = polygon_overlay._finished
        self._points = polygon_overlay._points.copy()

    def to_dict(self):
        """
        Convert the necessary class attributes and its values to a dict.
        This method can be used to gather data for creating a json file.
        """
        return {
            "p_points": self._points,
        }

    @staticmethod
    def from_dict(state: dict, polygon_overlay):
        """
        Use the dict keys and values to reconstruct the class from a json file.

        :param state: The dict containing the class attributes and its values as key value pairs.
                    to_dict() method must have been used previously to create this dict.
        :param polygon_overlay: (PolygonOverlay) The overlay representing a polygon.
        :returns: (PolygonState) reconstructed PolygonState class.
        """
        polygon_state = PolygonState(polygon_overlay)
        polygon_state._finished = True
        points  = state["p_points"]
        points = [Vec(point) for point in points]
        polygon_state._points = points
        return polygon_state

class PolygonOverlay(EditableShape, LineEditingMixin, WorldOverlay):
    """Overlay representing one polygon."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        :param: cnvs: canvas for the overlay.
        :param: colour (str): hex colour code for the polygon.
        """
        EditableShape.__init__(self, cnvs)
        LineEditingMixin.__init__(self, colour)
        # PolygonOverlay has attributes and methods of the "WorldOverlay" interface.
        # However, WorldOverlay's __init__() is not called here because mouse events
        # (such as EVT_LEFT_DOWN, EVT_LEFT_UP, etc.) are managed by ShapesOverlay's canvas.
        # ShapesOverlay oversees PolygonOverlays, thereby preventing the redundant processing
        # of mouse events by both ShapesOverlay and PolygonOverlay.
        # If users need to use PolygonOverlay independently of ShapesOverlay, they can
        # explicitly initialize WorldOverlay to manage its own mouse events.

        self.dashed = False

        self._label = Label(
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
        self._name_label = Label(
            text=self.name.value,
            pos=(0, 0),
            font_size=12,
            flip=True,
            align=wx.ALIGN_CENTRE_HORIZONTAL,
            colour=(1.0, 1.0, 1.0),  # default to white
            opacity=1.0,
            deg=None,
            background=None
        )
        self.v_point.subscribe(self._on_v_point)

    def to_dict(self) -> dict:
        """
        Convert the necessary class attributes and its values to a dict.
        This method can be used to gather data for creating a json file.
        """
        state = self.get_state()
        state_dict = state.to_dict() if state is not None else {}
        return {
            "name": self.name.value,
            "colour": frgba_to_hex(self.colour),
            "selected": self.selected.value,
            "cnvs_view_name": self.cnvs.view.name.value,
            "type": self.__class__.__name__,
            "state": state_dict,
        }

    @staticmethod
    def from_dict(polygon: dict, tab_data):
        """
        Use the dict keys and values to reconstruct the class from a json file.

        :param polygon: The dict containing the class attributes and its values as key value pairs.
                    to_dict() method must have been used previously to create this dict.
        :param tab_data: The data corresponding to a GUI tab helpful while reconstructing the class.
        :returns: (PolygonOverlay) reconstructed PolygonOverlay class.
        """
        name = polygon["name"]
        selected = polygon["selected"]
        cnvs_view_name = polygon["cnvs_view_name"]
        colour = polygon["colour"]
        shape_cnvs = None
        for viewport in tab_data.viewports.value:
            if viewport.canvas.view.name.value == cnvs_view_name:
                shape_cnvs = viewport.canvas
                break
        if shape_cnvs is None:
            raise ValueError("Could not find shape canvas")
        polygon_overlay = PolygonOverlay(shape_cnvs)
        polygon_overlay.name.value = name
        polygon_overlay.selected.value = selected
        polygon_overlay.is_created.value = True
        polygon_overlay.colour = hex_to_frgba(colour)
        state_data = polygon["state"]
        state = PolygonState.from_dict(state_data, polygon_overlay)
        polygon_overlay.restore_state(state)
        return polygon_overlay

    def reset(self):
        """Reset the shape creation."""
        self.reset_click_mixin()
        self._points.clear()
        self.is_created.value = False

    def copy(self):
        """
        :returns: (PolygonOverlay) a new instance of PolygonOverlay with necessary copied attributes.

        """
        shape = PolygonOverlay(self.cnvs)
        shape.colour = self.colour
        shape.name.value = self.name.value
        shape.dashed = self.dashed
        shape.restore_state(self.get_state())
        shape.is_created.value = True
        return shape

    def move_to(self, pos: Tuple[float, float]):
        """Move the shape's center to a physical position."""
        current_pos  = self.get_position()
        shift = (pos[0] - current_pos[0], pos[1] - current_pos[1])
        self._points = [p + shift for p in self._points]
        self._phys_to_view()
        self.points.value = self._points

    def set_rotation(self, target_rotation: float):
        """Set the rotation of the shape to a specific angle."""
        self._set_rotation(target_rotation)
        self._view_to_phys()
        self._phys_to_view()
        self.points.value = self._points

    def get_state(self) -> Optional[PolygonState]:
        """Get the current state of the shape."""
        # Only return the state if the polygon creation is finished
        # By doing so avoid storing an undo action during polygon creation
        if self.right_click_finished:
            return PolygonState(self)
        return None

    def restore_state(self, state: Optional[PolygonState]):
        """Restore the shape to a given state."""
        if state is not None:
            self._finished = state._finished
            self._points = state._points
            # The v_points will be calculated by _phys_to_view()
            self.v_points = [None] * len(self._points)
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
        # A polygon should have atleast 2 points after on_right_up
        if self.right_click_finished:
            if len(self._points) > 2:
                hover, _ = self.get_hover(v_point)
                return hover != gui.HOVER_NONE
            return False
        return True

    def on_left_down(self, evt):
        if self.selected.value or not self.is_created.value:
            LineEditingMixin._on_left_down(self, evt)
            self.cnvs.update_drawing()

    def on_left_up(self, evt):
        is_mode_rotation_edit = self.selection_mode in (SEL_MODE_ROTATION, SEL_MODE_EDIT)
        LineEditingMixin._on_left_up(self, evt)
        if self.right_click_finished:
            self._phys_to_view()
            self.selected.value = self.check_point_proximity(evt.Position)
            # While selection mode is SEL_MODE_ROTATION or SEL_MODE_EDIT it can be that the hover
            # is outside the edge and left up event is called, update the points VA in this
            # corner case
            if self.selected.value or is_mode_rotation_edit:
                self.points.value = self._points
        self.cnvs.update_drawing()

    def on_right_down(self, evt):
        LineEditingMixin._on_right_down(self, evt)
        self.cnvs.update_drawing()

    def on_right_up(self, evt):
        LineEditingMixin._on_right_up(self, evt)
        self._view_to_phys()
        if len(self._points) <= 2:
            logging.warning("Cannot create a polygon for less than 3 points.")
            self.reset()
        else:
            self.is_created.value = True
        # Set initial value
        self.points.value = self._points
        self.cnvs.update_drawing()

    def on_motion(self, evt):
        if self.selected.value:
            LineEditingMixin._on_motion(self, evt)
            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                elif self.hover == gui.HOVER_EDGE:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
                elif self.hover == gui.HOVER_ROTATION:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_MAGNIFIER)
                else:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_CROSS)
            else:
                self._view_to_phys()
            self.cnvs.update_drawing()

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
        self._rotation_label.pos = self.cnvs.view_to_buffer(self.v_center)
        self._rotation_label.background = (0, 0, 0)  # black
        self._rotation_label.draw(ctx)

    def draw_name_label(self, ctx):
        self._name_label.text = self.name.value
        self._name_label.pos = self.cnvs.view_to_buffer(self.v_center)
        self._name_label.background = (0, 0, 0)  # black
        self._name_label.draw(ctx)

    def draw(self, ctx, shift=(0, 0), scale=1.0, line_width=4):
        """Draw the selection as a polygon"""
        if self._points:
            offset = self.cnvs.get_half_buffer_size()

            # draws the dotted line
            line_width = LINE_WIDTH_THICK if self.selected.value else LINE_WIDTH_THIN

            ctx.set_line_width(line_width)
            if self.dashed:
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
                # calculate the center explicitly for ShapesOverlay _get_shape function, if polygon creation is not finished
                self._calc_center()
                # also calculate the rotation explicitly, if polygon creation is not finished
                self._calc_rotation()
            else:
                ctx.close_path()
                ctx.stroke()
                self._calc_edges()
                self.draw_edges(ctx)
                # Draw the rotation label or name label at the center
                if self.selection_mode == SEL_MODE_ROTATION:
                    self.draw_rotation_label(ctx)
                else:
                    self.draw_name_label(ctx)

                # Draw the grid rectangles
                if self.fill_grid.value and self.grid_rects:
                    for p_start_pos, p_end_pos in self.grid_rects:
                        b_start_pos = Vec(self.cnvs.phys_to_buffer(p_start_pos, offset))
                        b_end_pos = Vec(self.cnvs.phys_to_buffer(p_end_pos, offset))
                        rect = (b_start_pos.x,
                                b_start_pos.y,
                                b_end_pos.x - b_start_pos.x,
                                b_end_pos.y - b_start_pos.y)
                        ctx.rectangle(*rect)
                    ctx.stroke()
