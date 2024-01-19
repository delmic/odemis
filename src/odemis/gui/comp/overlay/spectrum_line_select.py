# -*- coding: utf-8 -*-


"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014-2021 Rinze de Laat, Éric Piel, Philip Winkler, Delmic

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
import odemis.gui as gui
import odemis.util.conversion as conversion
import wx
from odemis.gui.comp.overlay.base import EDIT_MODE_POINT,PixelDataMixin, Vec
from odemis.gui.comp.overlay.world_select import WorldSelectOverlay
from odemis.util import clip_line
from odemis.util.raster import rasterize_line


class LineSelectOverlay(WorldSelectOverlay):
    """ Selection overlay that allows for the selection of a line in physical coordinates"""

    def __init__(self, cnvs):
        WorldSelectOverlay.__init__(self, cnvs)
        self.edit_mode = EDIT_MODE_POINT

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if None not in (self.p_start_pos, self.p_end_pos) and self.p_start_pos != self.p_end_pos:
            # Pixel radius of the start marker
            start_radius = 3
            arrow_size = 12

            offset = self.cnvs.get_half_buffer_size()
            # Calculate buffer start and end positions
            b_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_start = (b_pos[0] - 0.5, b_pos[1] - 0.5)
            b_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_end = (b_pos[0] + 0.5, b_pos[1] + 0.5)
            self.update_projection(b_start, b_end, tuple(shift) + (scale,))

            # Calculate unit vector
            dx, dy = (b_start[0] - b_end[0],
                      b_start[1] - b_end[1])

            length = math.hypot(dx, dy) or 0.000001
            udx, udy = dx / length, dy / length  # Normalized vector

            # Rotate over 60 and -60 degrees
            ax = udx * math.sqrt(3) / 2 - udy / 2
            ay = udx / 2 + udy * math.sqrt(3) / 2
            bx = udx * math.sqrt(3) / 2 + udy / 2
            by = -udx / 2 + udy * math.sqrt(3) / 2

            # The two lower corners of the arrow head
            b_arrow_1 = (b_end[0] + arrow_size * ax, b_end[1] + arrow_size * ay)
            b_arrow_2 = (b_end[0] + arrow_size * bx, b_end[1] + arrow_size * by)

            # Connection point for the line at the base of the arrow
            b_arrow_con = ((b_arrow_1[0] + b_arrow_2[0]) / 2.0,
                           (b_arrow_1[1] + b_arrow_2[1]) / 2.0)

            # Calculate the connection to the start circle
            rad = math.atan2(b_start[1] - b_end[1], b_start[0] - b_end[0])
            y_offset = start_radius * math.sin(rad)
            x_offset = start_radius * math.cos(rad)
            b_circle_con = (b_start[0] - x_offset, b_start[1] - y_offset)

            # Draws a black background for the line
            ctx.set_line_width(3)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.move_to(*b_circle_con)
            ctx.line_to(*b_arrow_con)
            ctx.stroke()

            # Draw the dotted line
            ctx.set_line_width(2)
            ctx.set_dash([3])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            if self.hover == gui.HOVER_LINE and not self.dragging:
                ctx.set_source_rgba(*self.highlight)
            else:
                ctx.set_source_rgba(*self.colour)
            ctx.move_to(*b_circle_con)
            ctx.line_to(*b_arrow_con)
            ctx.stroke()

            # Draw start circle
            ctx.set_dash([])
            ctx.set_line_width(3.5)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.arc(b_start[0], b_start[1], start_radius, 0, 2 * math.pi)
            ctx.stroke_preserve()

            if self.hover == gui.HOVER_START and not self.dragging:
                ctx.set_source_rgba(*self.highlight)
            else:
                ctx.set_source_rgba(*self.colour)

            ctx.set_line_width(1.5)
            ctx.arc(b_start[0], b_start[1], start_radius, 0, 2 * math.pi)
            ctx.stroke()

            # Draw arrow head
            ctx.set_dash([])
            ctx.set_line_width(2)
            ctx.move_to(*b_end)
            ctx.line_to(*b_arrow_1)
            ctx.line_to(*b_arrow_2)
            ctx.close_path()

            # Dark border
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.stroke_preserve()

            # Colour fill
            if self.hover == gui.HOVER_END and not self.dragging:
                ctx.set_source_rgba(*self.highlight)
            else:
                ctx.set_source_rgba(*self.colour)
            ctx.fill()

            self._debug_draw_edges(ctx, True)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover in (gui.HOVER_START, gui.HOVER_END, gui.HOVER_LINE):
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_PENCIL)
            else:
                self._view_to_phys()

            # TODO: Find a way to render the selection at the full frame rate. Right now it's not
            # possible, because we are drawing directly into the buffer, which might render slowly
            # anyway. What we would want, is that a world overlay that can visually change when the
            # mouse moves, draws into the view. After the motion is done, it should be rendered into
            # buffer.
            self.cnvs.request_drawing_update()
        else:
            WorldSelectOverlay.on_motion(self, evt)


class SpectrumLineSelectOverlay(LineSelectOverlay, PixelDataMixin):
    """
    Selection overlay that allows for the selection of a line in physical coordinates
    and displays a specific point/circle over this line (if requested).
    """

    def __init__(self, cnvs):
        LineSelectOverlay.__init__(self, cnvs)
        PixelDataMixin.__init__(self)

        self.start_pixel = (None, None)
        self.end_pixel = (None, None)

        self._selected_line_va = None
        self._selected_width_va = None
        self._selected_pixel_va = None

        self._width_colour = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, 0.5)
        self._pixel_colour = conversion.hex_to_frgba(gui.FG_COLOUR_EDIT, 0.5)

    def connect_selection(self, selection_va, width_va, pixel_va=None):
        """ Connect the overlay to an external selection VA so it can update itself on value changes

        Args:
            selection_va: (VA)((int, int), (int, int)) position of the start and end pixels
            width_va: (VA)(int) the width of the selection line
            pixel_va: (VA) (int, int) a pixel on the on the selected line

        """

        self.clear_selection()

        if self._selected_line_va:
            self._selected_line_va.unsubscribe(self._on_selection)
        if self._selected_width_va:
            self._selected_width_va.unsubscribe(self._on_width)
        if self._selected_pixel_va:
            self._selected_pixel_va.unsubscribe(self._on_pix_selection)

        self._selected_line_va = selection_va
        self._selected_width_va = width_va
        self._selected_pixel_va = pixel_va

        self._selected_line_va.subscribe(self._on_selection, init=True)
        self._selected_width_va.subscribe(self._on_width, init=False)
        if pixel_va:
            self._selected_pixel_va.subscribe(self._on_pix_selection, init=False)

    def _on_selection(self, selected_line):
        """ Update the overlay when it's active and the line changes """

        if selected_line and self.active.value:
            self.start_pixel, self.end_pixel = selected_line

            if (None, None) not in selected_line:
                v_pos = self.data_pixel_to_view(self.start_pixel)
                self.drag_v_start_pos = self.select_v_start_pos = Vec(v_pos)

                v_pos = self.data_pixel_to_view(self.end_pixel)
                self.drag_v_end_pos = self.select_v_end_pos = Vec(v_pos)

                self._view_to_phys()

            wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_pix_selection(self, _):
        """ Update the overlay when it's active and the pixel changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_width(self, _):
        """ Update the overlay when it's active and the line width changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def get_selection_points(self, pixel):
        """ Calculate the points around the given point according to the selection width

        Args:
            pixel: (int, int) the selected data pixel at the center

        Returns:
            [(int, int)]: List of (int, int) coordinates

        """

        if pixel is None or None in pixel:
            return []

        if self._selected_width_va.value == 1:
            return [pixel]

        x, y = pixel
        radius = self._selected_width_va.value / 2
        w, h = self._data_resolution
        points = []

        for px in range(max(0, int(x - radius)), min(int(x + radius) + 1, w)):
            for py in range(max(0, int(y - radius)), min(int(y + radius) + 1, h)):
                if math.hypot(x - px, y - py) <= radius:
                    points.append((px, py))

        return points

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        # If no valid selection is made, do nothing...
        if None in (self.p_start_pos, self.p_end_pos) or self.p_start_pos == self.p_end_pos:
            return

        if (None, None) in (self.start_pixel, self.end_pixel):
            return

        points = rasterize_line(self.start_pixel, self.end_pixel, self._selected_width_va.value)
        # Clip points
        w, h = self._data_resolution
        points = [p for p in points if 0 <= p[0] < w and 0 <= p[1] < h]

        selected_pixel = self._selected_pixel_va.value if self._selected_pixel_va else None
        selected_pixels = self.get_selection_points(selected_pixel)

        for point in set(points):
            if point in selected_pixels:
                ctx.set_source_rgba(*self._pixel_colour)
            else:
                ctx.set_source_rgba(*self._width_colour)
            rect = self.pixel_to_rect(point, scale)
            ctx.rectangle(*rect)
            ctx.rectangle(*rect)
            ctx.fill()

        LineSelectOverlay.draw(self, ctx, shift, scale)

    def on_left_down(self, evt):
        """ Start drawing a selection line if the overlay is active """

        if self.active.value:
            v_pos = evt.Position
            if self.is_over_pixel_data(v_pos):
                LineSelectOverlay.on_left_down(self, evt)
                self._snap_to_pixel()
        else:
            LineSelectOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ Stop drawing a selection line if the overlay is active """

        if self.active.value:
            self._snap_to_pixel()
            LineSelectOverlay.on_left_up(self, evt)

            # Clip the line, so it will fit inside the pixel data
            sx, sy, ex, ey = clip_line(0, self._data_resolution[1] - 1,
                                       self._data_resolution[0] - 1, 0,
                                       self.start_pixel[0], self.start_pixel[1],
                                       self.end_pixel[0], self.end_pixel[1])
            self.start_pixel = sx, sy
            self.end_pixel = ex, ey

            if self.start_pixel == self.end_pixel:
                self.start_pixel = self.end_pixel = (None, None)
                self.clear_selection()

            if self._selected_line_va:
                self._selected_line_va.value = (self.start_pixel, self.end_pixel)
            if self._selected_pixel_va:
                # Also set the pixel to something valid
                self._selected_pixel_va.value = self.start_pixel
        else:
            LineSelectOverlay.on_left_up(self, evt)

    def _snap_to_pixel(self):
        """ Snap the current start and end view positions to the center of the closest data pixels
        """
        if self.select_v_start_pos:
            self.start_pixel = self.view_to_data_pixel(self.select_v_start_pos)
            v_pos = self.data_pixel_to_view(self.start_pixel)
            self.drag_v_start_pos = self.select_v_start_pos = Vec(v_pos)
        else:
            self.start_pixel = (None, None)

        if self.select_v_end_pos:
            self.end_pixel = self.view_to_data_pixel(self.select_v_end_pos)
            v_pos = self.data_pixel_to_view(self.end_pixel)
            self.drag_v_end_pos = self.select_v_end_pos = Vec(v_pos)
        else:
            self.end_pixel = (None, None)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """

        if self.active.value:
            v_pos = evt.Position
            if self.is_over_pixel_data(v_pos):
                LineSelectOverlay.on_motion(self, evt)
                # Little test for real time spectrum display, which was too slow, as expected
                # self._snap_to_pixel()
                # if None not in (self.start_pixel, self.end_pixel):
                #     if self._selected_line_va.value != (self.start_pixel, self.end_pixel):
                #         self._selected_line_va.value = (self.start_pixel, self.end_pixel)
            else:
                self.cnvs.reset_dynamic_cursor()
        else:
            LineSelectOverlay.on_motion(self, evt)
