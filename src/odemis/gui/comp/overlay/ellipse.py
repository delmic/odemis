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

import cairo

from odemis import model
import odemis.util.units as units
import odemis.gui as gui
from odemis.gui.comp.overlay.base import Vec
from odemis.gui.comp.overlay.rectangle import RectangleOverlay

NUM_ARCS_FACTOR = 3


class EllipseOverlay(RectangleOverlay):
    """Overlay representing one ellipse."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        cnvs: canvas for the overlay
        colour (str): hex colour code for the ellipse
        """
        super().__init__(cnvs, colour)
        self.points = model.ListVA()
        self._points = []

    def on_left_up(self, evt):
        """
        Check if left click was in ellipse. If so, activate the overlay. Otherwise, deactivate.
        """
        super().on_left_up(evt)
        if not self.locked.value:
            self.points.value = self._points

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the selection as a ellipse. Exactly the same as parent function except that
        it has an adaptive line width (wider if the overlay is active) and it always shows the
        size label of the selected ellipse."""
        self._points.clear()
        line_width = 5 if self.active.value else 2

        if self.p_start_pos and self.p_end_pos:
            # Important: We need to use the physical positions, in order to draw
            # everything at the right scale.
            offset = self.cnvs.get_half_buffer_size()
            b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)
            self.update_projection(b_start_pos, b_end_pos, (shift[0], shift[1], scale))

            # draws the dotted line
            ctx.set_line_width(line_width)
            ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            # Calculate the center of the ellipse
            ellipse_center_x = (b_start_pos.x + b_end_pos.x) / 2
            ellipse_center_y = (b_start_pos.y + b_end_pos.y) / 2
            # Semi-major axis
            a = abs(b_start_pos.x - b_end_pos.x) / 2
            # Semi-minor axis
            b = abs(b_start_pos.y - b_end_pos.y) / 2
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
                    x = ellipse_center_x + a * math.cos(angle)
                    y = ellipse_center_y + b * math.sin(angle)
                    if i == 0:
                        ctx.move_to(x, y)
                    else:
                        ctx.line_to(x, y)
                    p_x, p_y = self.cnvs.buffer_to_phys((x, y), offset)
                    self._points.append((p_y, p_x))
                ctx.close_path()
                ctx.stroke()

            # show size label if ROA is selected
            if self.active.value:
                w, h = (abs(s - e) for s, e in zip(self.p_start_pos, self.p_end_pos))
                w = units.readable_str(w, "m", sig=2)
                h = units.readable_str(h, "m", sig=2)
                size_lbl = "{} x {}".format(w, h)

                pos = Vec(b_end_pos.x - 8, b_end_pos.y + 5)

                self.position_label.pos = pos
                self.position_label.text = size_lbl
                self.position_label.colour = (1, 1, 1)  # label white
                self.position_label.background = (0.7, 0.7, 0.7, 0.8)  # background grey
                self._write_labels(ctx)
