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
from typing import Optional

import cairo

from odemis.util.raster import point_in_polygon
import odemis.gui as gui
from odemis.gui.comp.overlay._constants import LINE_WIDTH_THICK, LINE_WIDTH_THIN
from odemis.gui.comp.overlay.base import SEL_MODE_NONE, SEL_MODE_ROTATION, Vec, WorldOverlay
from odemis.gui.comp.overlay.rectangle import RectangleOverlay

# The circumference of an ellipse is divided by this factor to calculate number of
# arcs to be drawn. This increases the angle of an arc and reduces the number of
# points on the circumference of the ellipse. A factor value of 3 is choosen because
# the cicumference is calculated in buffer coordinates which has values greater than
# 1 and even with the reduced number of points on the circumference the ellipse is
# still visible.
NUM_ARCS_FACTOR = 3


class EllipseState:
    def __init__(self, ellipse_overlay) -> None:
        self.p_point1 = ellipse_overlay.p_point1
        self.p_point2 = ellipse_overlay.p_point2
        self.p_point3 = ellipse_overlay.p_point3
        self.p_point4 = ellipse_overlay.p_point4
        self._points = ellipse_overlay._points.copy()


class EllipseOverlay(RectangleOverlay):
    """Overlay representing one ellipse."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        :param cnvs: canvas for the overlay
        :param colour: (str) hex colour code for the ellipse
        """
        super().__init__(cnvs, colour)

    def copy(self):
        """
        :returns: (EllipseOverlay) a new instance of EllipseOverlay with necessary copied attributes.

        """
        shape = EllipseOverlay(self.cnvs)
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
        self._points = [p + shift for p in self._points]
        self._phys_to_view()
        self.points.value = self._points

    def get_state(self) -> Optional[EllipseState]:
        """Get the current state of the shape."""
        # Only return the state if the ellipse creation is finished
        # By doing so avoid storing an undo action during ellipse creation
        if self.p_point1 != self.p_point3:
            return EllipseState(self)
        return None

    def restore_state(self, state: EllipseState):
        """Restore the shape to a given state."""
        self.p_point1 = state.p_point1
        self.p_point2 = state.p_point2
        self.p_point3 = state.p_point3
        self.p_point4 = state.p_point4
        self._points = state._points
        self._phys_to_view()
        self.points.value = self._points

    def is_point_in_shape(self, point):
        # Use rectangle points instead of circumference points to make the calculation faster
        # Also editing the ellipse edges is the same as editing the rectangle's edges
        self._view_to_phys()
        rectangle_points = self.get_physical_sel()
        if rectangle_points:
            return point_in_polygon(point, rectangle_points)
        return False

    def on_left_up(self, evt):
        """
        Check if left click was in ellipse. If so, activate the overlay. Otherwise, deactivate.
        """
        if self.active.value:
            is_rotation = self.selection_mode == SEL_MODE_ROTATION
            # If the Diagonal points are not the same means the rectangle has been created
            if self.p_point1 != self.p_point3:
                # Activate/deactivate region
                self._view_to_phys()
                rectangle_points = self.get_physical_sel()
                if rectangle_points:
                    pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
                    self.selected.value = point_in_polygon(pos, rectangle_points)
                    # The rotation point is outside the shape and cannot be captured by selection VA
                    # Also update the physical selection if the selection mode is SEL_MODE_ROTATION
                    if self.selected.value or is_rotation:
                        self.set_physical_sel(rectangle_points)

            # SelectionMixin._on_left_up has some functionality which does not work here, so only call the parts
            # that we need
            self.clear_drag()
            self.selection_mode = SEL_MODE_NONE
            self.edit_hover = None

            # Set the points VA after drawing because draw() gathers the points
            self.cnvs.update_drawing()
            # The rotation point is outside the shape and cannot be captured by selection VA
            # Update the points VA if the selection mode is SEL_MODE_ROTATION
            if self.selected.value or is_rotation:
                self.points.value = self._points
        WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0, dash=True):
        """Draw the selection as a ellipse."""
        self._points.clear()
        flag = self.active.value and self.selected.value
        line_width = LINE_WIDTH_THICK if flag else LINE_WIDTH_THIN

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
            if dash:
                ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            # Calculate the center of the ellipse
            ellipse_center_x = (b_point1.x + b_point3.x) / 2
            ellipse_center_y = (b_point1.y + b_point3.y) / 2
            # Calculate the side lengths to find the semi-major and semi-minor axis
            side_length1 = math.hypot(b_point1.x - b_point2.x, b_point1.y - b_point2.y)
            side_length2 = math.hypot(b_point1.x - b_point4.x, b_point1.y - b_point4.y)
            # Fixed semi-major axis
            a = side_length1 / 2
            # Fixed semi-minor axis
            b = side_length2 / 2

            rotation = math.atan2((b_point2.y - b_point1.y), (b_point2.x - b_point1.x))
            if a + b:
                # Calculate the circumference of the ellipse using Ramanujan's approximation
                h = ((a - b) ** 2) / ((a + b) ** 2)
                circumference = math.pi * (a + b) * (1 + (3 * h) / (10 + math.sqrt(4 - 3 * h)))
                # Determine the number of arcs based on the circumference of the ellipse
                num_arcs = max(int(circumference / NUM_ARCS_FACTOR), 4)  # Ensure minimum of 4 arcs
                # Divide the ellipse into multiple arcs and draw each arc
                angle_increment = 2 * math.pi / num_arcs
                for i in range(num_arcs):
                    angle = i * angle_increment
                    x = a * math.cos(angle) * math.cos(rotation) - b * math.sin(angle) * math.sin(rotation)
                    y = a * math.cos(angle) * math.sin(rotation) + b * math.sin(angle) * math.cos(rotation)
                    point = (ellipse_center_x + x, ellipse_center_y + y)
                    if i == 0:
                        ctx.move_to(*point)
                    else:
                        ctx.line_to(*point)
                    p_point = self.cnvs.buffer_to_phys(point, offset)
                    self._points.append(Vec(p_point))
                ctx.close_path()
                ctx.stroke()

                self._calc_edges()
                self.draw_edges(ctx, b_point1, b_point2, b_point3, b_point4)

            # show size label if ROA is selected
            if flag:
                self.draw_side_labels(ctx, b_point1, b_point2, b_point3, b_point4)

            # Draw the rotation label
            if self.selection_mode == SEL_MODE_ROTATION:
                self.draw_rotation_label(ctx)
