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
from typing import Optional, Tuple

import cairo

import odemis.gui as gui
from odemis.gui.comp.overlay._constants import LINE_WIDTH_THICK, LINE_WIDTH_THIN
from odemis.gui.comp.overlay.base import (
    SEL_MODE_EDIT,
    SEL_MODE_NONE,
    SEL_MODE_ROTATION,
    Vec,
)
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.util.conversion import frgba_to_hex, hex_to_frgba

# The circumference of an ellipse is divided into 72 arcs.
# This will result in an angle increment of 5 degrees.
NUM_ARCS = 72
ANGLE_INCREMENT = 2 * math.pi / NUM_ARCS


class EllipseState:
    def __init__(self, ellipse_overlay) -> None:
        self.p_point1 = ellipse_overlay.p_point1
        self.p_point2 = ellipse_overlay.p_point2
        self.p_point3 = ellipse_overlay.p_point3
        self.p_point4 = ellipse_overlay.p_point4

    def to_dict(self):
        """
        Convert the necessary class attributes and its values to a dict.
        This method can be used to gather data for creating a json file.
        """
        return {
            "p_point1": self.p_point1,
            "p_point2": self.p_point2,
            "p_point3": self.p_point3,
            "p_point4": self.p_point4,
        }

    @staticmethod
    def from_dict(state: dict, ellipse_overlay):
        """
        Use the dict keys and values to reconstruct the class from a json file.

        :param state: The dict containing the class attributes and its values as key value pairs.
                    to_dict() method must have been used previously to create this dict.
        :param ellipse_overlay: (EllipseOverlay) The overlay representing an ellipse.
        :returns: (EllipseState) reconstructed EllipseState class.
        """
        ellipse_state = EllipseState(ellipse_overlay)
        ellipse_state.p_point1 = Vec(state["p_point1"])
        ellipse_state.p_point2 = Vec(state["p_point2"])
        ellipse_state.p_point3 = Vec(state["p_point3"])
        ellipse_state.p_point4 = Vec(state["p_point4"])
        return ellipse_state


class EllipseOverlay(RectangleOverlay):
    """
    Overlay representing one ellipse.

    The ellipse is created and manipulated using four primary points (p_point1, p_point2, p_point3, p_point4)
    which define a bounding rectangle. The semi-major and semi-minor axes of the ellipse are derived
    from the distances between these points, effectively making the ellipse inscribed within this rectangle.

    For drawing, the ellipse is approximated using a series of small arcs, calculated based on the
    Ramanujan's approximation for the circumference of an ellipse. The approximation ensures that
    the ellipse appears smooth and accurate. The ellipse's position and orientation are determined
    by the bounding rectangle, allowing for intuitive interaction and manipulation.

    """
    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        :param cnvs: canvas for the overlay
        :param colour: (str) hex colour code for the ellipse
        """
        super().__init__(cnvs, colour)

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
    def from_dict(ellipse: dict, tab_data):
        """
        Use the dict keys and values to reconstruct the class from a json file.

        :param ellipse: The dict containing the class attributes and its values as key value pairs.
                    to_dict() method must have been used previously to create this dict.
        :param tab_data: The data corresponding to a GUI tab helpful while reconstructing the class.
        :returns: (EllipseOverlay) reconstructed EllipseOverlay class.
        """
        name = ellipse["name"]
        selected = ellipse["selected"]
        cnvs_view_name = ellipse["cnvs_view_name"]
        colour = ellipse["colour"]
        shape_cnvs = None
        for viewport in tab_data.viewports.value:
            if viewport.canvas.view.name.value == cnvs_view_name:
                shape_cnvs = viewport.canvas
                break
        if shape_cnvs is None:
            raise ValueError("Could not find shape canvas.")
        ellipse_overlay = EllipseOverlay(shape_cnvs)
        ellipse_overlay.name.value = name
        ellipse_overlay.selected.value = selected
        ellipse_overlay.is_created.value = True
        ellipse_overlay.colour = hex_to_frgba(colour)
        state_data = ellipse["state"]
        state = EllipseState.from_dict(state_data, ellipse_overlay)
        ellipse_overlay.restore_state(state)
        return ellipse_overlay

    def reset(self):
        """Reset the shape creation."""
        pass

    def copy(self):
        """
        :returns: (EllipseOverlay) a new instance of EllipseOverlay with necessary copied attributes.

        """
        shape = EllipseOverlay(self.cnvs)
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
        self.p_point1 += shift
        self.p_point2 += shift
        self.p_point3 += shift
        self.p_point4 += shift
        self._points = [p + shift for p in self._points]
        self._phys_to_view()
        self.points.value = self._points

    def set_rotation(self, target_rotation: float):
        """Set the rotation of the shape to a specific angle."""
        self._set_rotation(target_rotation)
        self._view_to_phys()
        self.cnvs.update_drawing()
        self.points.value = self._points

    def get_state(self) -> Optional[EllipseState]:
        """Get the current state of the shape."""
        # Only return the state if the ellipse creation is finished
        # By doing so avoid storing an undo action during ellipse creation
        if self.p_point1 != self.p_point3:
            return EllipseState(self)
        return None

    def restore_state(self, state: Optional[EllipseState]):
        """Restore the shape to a given state."""
        if state is not None:
            self.p_point1 = state.p_point1
            self.p_point2 = state.p_point2
            self.p_point3 = state.p_point3
            self.p_point4 = state.p_point4
            self._phys_to_view()
            self.calculate_ellipse_points()
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
        # Use rectangle points instead of circumference points to make the calculation faster
        # Also editing the ellipse edges is the same as editing the rectangle's edges
        rectangle_points = self.get_physical_sel()
        if rectangle_points:
            hover, _ = self.get_hover(v_point)
            return hover != gui.HOVER_NONE
        return False

    def on_left_up(self, evt):
        """
        Check if left click was in ellipse. If so, activate the overlay. Otherwise, deactivate.
        """
        is_mode_rotation_edit = self.selection_mode in (SEL_MODE_ROTATION, SEL_MODE_EDIT)
        # If the Diagonal points are not the same means the ellipse has been created
        if self.p_point1 != self.p_point3:
            # Activate/deactivate region
            self._view_to_phys()
            rectangle_points = self.get_physical_sel()
            if rectangle_points:
                self.is_created.value = True
                self.selected.value = self.check_point_proximity(evt.Position)
                # While selection mode is SEL_MODE_ROTATION or SEL_MODE_EDIT it can be that the hover
                # is outside the edge and left up event is called, update the points in this
                # corner case
                if self.selected.value or is_mode_rotation_edit:
                    self.set_physical_sel(rectangle_points)

        # SelectionMixin._on_left_up has some functionality which does not work here, so only call the parts
        # that we need
        self.clear_drag()
        self.selection_mode = SEL_MODE_NONE
        self.edit_hover = None

        # Set the points VA after drawing because draw() gathers the ellipse points
        self.cnvs.update_drawing()
        # While selection mode is SEL_MODE_ROTATION or SEL_MODE_EDIT it can be that the hover
        # is outside the edge and left up event is called, update the points VA in this
        # corner case
        if self.selected.value or is_mode_rotation_edit:
            self.points.value = self._points

    def calculate_ellipse_points(self):
        """Calculate the ellipse points."""
        self._points.clear()
        # Calculate center of the ellipse
        center_x = (self.p_point1.x + self.p_point3.x) / 2
        center_y = (self.p_point1.y + self.p_point3.y) / 2

        # Calculate side lengths to get semi-major and semi-minor axes
        side_length1 = math.hypot(self.p_point1.x - self.p_point2.x, self.p_point1.y - self.p_point2.y)
        side_length2 = math.hypot(self.p_point1.x - self.p_point4.x, self.p_point1.y - self.p_point4.y)

        # Semi-major and semi-minor axes
        a = side_length1 / 2
        b = side_length2 / 2

        # Calculate rotation angle
        rotation = math.atan2(self.p_point2.y - self.p_point1.y, self.p_point2.x - self.p_point1.x)

        if a + b:
            # Calculate the ellipse points
            for i in range(NUM_ARCS):
                angle = i * ANGLE_INCREMENT
                x = a * math.cos(angle) * math.cos(rotation) - b * math.sin(angle) * math.sin(rotation)
                y = a * math.cos(angle) * math.sin(rotation) + b * math.sin(angle) * math.cos(rotation)
                self._points.append(Vec(center_x + x, center_y + y))

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the selection as a ellipse."""
        line_width = LINE_WIDTH_THICK if self.selected.value else LINE_WIDTH_THIN

        if self.p_point1 and self.p_point2 and self.p_point3 and self.p_point4:
            # Important: We need to use the physical positions, in order to draw
            # everything at the right scale.
            offset = self.cnvs.get_half_buffer_size()
            b_point1 = Vec(self.cnvs.phys_to_buffer(self.p_point1, offset))
            b_point2 = Vec(self.cnvs.phys_to_buffer(self.p_point2, offset))
            b_point3 = Vec(self.cnvs.phys_to_buffer(self.p_point3, offset))
            b_point4 = Vec(self.cnvs.phys_to_buffer(self.p_point4, offset))

            self.update_projection(b_point1, b_point2, b_point3, b_point4, (shift[0], shift[1], scale))

            # draws the dotted line
            ctx.set_line_width(line_width)
            if self.dashed:
                ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)

            self.calculate_ellipse_points()
            for i, p_point in enumerate(self._points):
                b_point = self.cnvs.phys_to_buffer(p_point, offset)
                if i == 0:
                    ctx.move_to(*b_point)
                else:
                    ctx.line_to(*b_point)
            ctx.close_path()
            ctx.stroke()

            self._calc_edges()
            self.draw_edges(ctx, b_point1, b_point2, b_point3, b_point4)

            # show size label if ROA is selected
            if self.selected.value:
                self.draw_side_labels(ctx, b_point1, b_point2, b_point3, b_point4)

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
