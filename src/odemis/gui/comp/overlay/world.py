# -*- coding: utf-8 -*-

"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

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

from __future__ import division

import logging
import math

import cairo
import wx

from .base import WorldOverlay, SelectionMixin, DragMixin
from odemis.acq.stream import UNDEFINED_ROI
import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.gui.img.data as img
import odemis.util.conversion as conversion
import odemis.util.units as units


class WorldSelectOverlay(WorldOverlay, SelectionMixin):

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        super(WorldSelectOverlay, self).__init__(cnvs)
        SelectionMixin.__init__(self, colour, center)

        self.w_start_pos = None
        self.w_end_pos = None

        self.position_label = self.add_label("", colour=(0.8, 0.8, 0.8))

    def set_world_start(self, w_start_pos):
        self.w_start_pos = w_start_pos
        self._calc_view_pos()

    def set_world_end(self, w_end_pos):
        self.w_end_pos = w_end_pos
        self._calc_view_pos()

    # Selection creation

    def start_selection(self, start_pos):
        SelectionMixin.start_selection(self, start_pos)
        self._calc_world_pos()

    def update_selection(self, current_pos):
        SelectionMixin.update_selection(self, current_pos)
        self._calc_world_pos()

    def stop_selection(self):
        """ End the creation of the current selection """
        SelectionMixin.stop_selection(self)
        self._calc_world_pos()

    # Selection modification

    def start_edit(self, start_pos, edge):
        SelectionMixin.start_edit(self, start_pos, edge)
        self._calc_world_pos()

    def update_edit(self, current_pos):
        SelectionMixin.update_edit(self, current_pos)
        self._calc_world_pos()

    def stop_edit(self):
        SelectionMixin.stop_edit(self)
        self._calc_world_pos()

    # Selection dragging

    def start_drag(self, start_pos):
        SelectionMixin.start_drag(self, start_pos)
        self._calc_world_pos()

    def update_drag(self, current_pos):
        SelectionMixin.update_drag(self, current_pos)
        self._calc_world_pos()

    def stop_drag(self):
        SelectionMixin.stop_drag(self)
        self._calc_world_pos()

    # Selection clearing

    def clear_selection(self):
        SelectionMixin.clear_selection(self)
        self.w_start_pos = None
        self.w_end_pos = None

    def _center_view_origin(self, vpos):
        w, h = self.cnvs.GetSize()
        return vpos[0] - (w // 2), vpos[1] - (h // 2)

    def _calc_world_pos(self):
        """ Update the world position to reflect the view position """
        if self.v_start_pos and self.v_end_pos:
            offset = [v // 2 for v in self.cnvs.buffer_size]
            w_pos = (self.cnvs.view_to_world(self.v_start_pos, offset) +
                     self.cnvs.view_to_world(self.v_end_pos, offset))
            w_pos = list(self._normalize(w_pos))
            self.w_start_pos = w_pos[:2]
            self.w_end_pos = w_pos[2:4]

    def _calc_view_pos(self):
        """ Update the view position to reflect the world position """
        if not self.w_start_pos or not self.w_end_pos:
            logging.warning("Asking to convert non-existing world positions")
            return
        offset = [v // 2 for v in self.cnvs.buffer_size]
        v_pos = (self.cnvs.world_to_view(self.w_start_pos, offset) +
                 self.cnvs.world_to_view(self.w_end_pos, offset))
        v_pos = list(self._normalize(v_pos))
        self.v_start_pos = v_pos[:2]
        self.v_end_pos = v_pos[2:4]
        self._calc_edges()

    def get_physical_sel(self):
        """ Return the selected rectangle in physical coordinates

        return (tuple of 4 floats): position in m

        """

        if self.w_start_pos and self.w_end_pos:
            p_pos = (self.cnvs.world_to_physical_pos(self.w_start_pos) +
                     self.cnvs.world_to_physical_pos(self.w_end_pos))
            return self._normalize(p_pos)
        else:
            return None

    def set_physical_sel(self, rect):
        """ Set the selection using the provided physical coordinates

        rect (tuple of 4 floats): t, l, b, r positions in m

        """

        if rect is None:
            self.clear_selection()
        else:
            w_pos = (self.cnvs.physical_to_world_pos(rect[:2]) +
                     self.cnvs.physical_to_world_pos(rect[2:4]))
            w_pos = self._normalize(w_pos)
            self.w_start_pos = w_pos[:2]
            self.w_end_pos = w_pos[2:4]
            self._calc_view_pos()

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if self.w_start_pos and self.w_end_pos:
            ctx.save()

            # Important: We need to use the world positions, in order to draw everything at the
            # right scale.
            offset = self.cnvs.get_half_buffer_size()
            b_pos = (self.cnvs.world_to_buffer(self.w_start_pos, offset) +
                     self.cnvs.world_to_buffer(self.w_end_pos, offset))
            b_pos = self._normalize(b_pos)
            self.update_from_buffer(b_pos[:2], b_pos[2:4], shift + (scale,))

            # logging.warn("%s %s", shift, world_to_buffer_pos(shift))
            rect = (b_pos[0] + 0.5, b_pos[1] + 0.5,
                    b_pos[2] - b_pos[0], b_pos[3] - b_pos[1])

            # draws a light black background for the rectangle
            ctx.set_line_width(4)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.rectangle(*rect)
            ctx.stroke()

            # draws the dotted line
            ctx.set_line_width(2)
            ctx.set_dash([3])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            ctx.rectangle(*rect)
            ctx.stroke()

            # Label
            if (self.selection_mode in (base.SEL_MODE_EDIT, base.SEL_MODE_CREATE) and
                    self.cnvs.microscope_view):
                w, h = self.cnvs.selection_to_real_size(self.w_start_pos, self.w_end_pos)
                w = units.readable_str(w, 'm', sig=2)
                h = units.readable_str(h, 'm', sig=2)
                size_lbl = u"{} x {}".format(w, h)

                pos = (b_pos[2] + 8, b_pos[3] - 10)

                self.position_label.pos = pos
                self.position_label.text = size_lbl
                self._write_labels(ctx)
            ctx.restore()

    # Event Handlers

    def on_left_down(self, evt):
        """ Start drag action if enabled, otherwise call super method so event will propagate """
        if self.active:
            super(WorldSelectOverlay, self)._on_left_down(evt)
            self.cnvs.update_drawing()
        else:
            super(WorldSelectOverlay, self).on_left_down(evt)

    def on_left_up(self, evt):
        """ End drag action if enabled, otherwise call super method so event will propagate """
        if self.active:
            super(WorldSelectOverlay, self)._on_left_up(evt)
            self.cnvs.update_drawing()
        else:
            super(WorldSelectOverlay, self).on_left_up(evt)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active:
            self._on_motion(evt)
            # Fixme: Find a way to render the selection at the full frame rate. Right now it's not
            # possible, because we are drawing directly into the buffer, which might render slowly
            # anyway. What we would want, is that a world overlay that can visually change when the
            # mouse moves, draws into the view. After the motion is done, it should be rendered into
            # buffer.
            self.cnvs.request_drawing_update()
        else:
            super(WorldSelectOverlay, self).on_motion(evt)

    # END Event Handlers


class RepetitionSelectOverlay(WorldSelectOverlay):
    """
    Same as world selection overlay, but can also display a repetition over it.
    The type of display for the repetition is set by the .fill and repetition
    attributes. You must redraw the canvas for it to be updated.
    """

    FILL_NONE = 0
    FILL_GRID = 1
    FILL_POINT = 2

    def __init__(self, cnvs, roa=None, colour=gui.SELECTION_COLOUR):
        super(RepetitionSelectOverlay, self).__init__(cnvs, colour)

        self._fill = self.FILL_NONE
        self._repetition = (0, 0)
        self._roa = roa
        if roa:
            self._roa.subscribe(self.on_roa, init=True)

        self._bmp = None  # used to cache repetition with FILL_POINT
        # ROI for which the bmp is valid
        self._bmp_bpos = (None, None, None, None)

    @property
    def fill(self):
        return self._fill

    @fill.setter
    def fill(self, val):
        assert(val in [self.FILL_NONE, self.FILL_GRID, self.FILL_POINT])
        self._fill = val
        self._bmp = None

    @property
    def repetition(self):
        return self._repetition

    @repetition.setter
    def repetition(self, val):
        assert(len(val) == 2)
        self._repetition = val
        self._bmp = None

    def on_roa(self, roa):
        """ Update the ROA overlay with the new roa VA data

        roi (tuple of 4 floats): top, left, bottom, right position relative to the SEM image

        """
        phys_rect = self.cnvs.convert_roi_ratio_to_phys(roa)
        self.set_physical_sel(phys_rect)
        wx.CallAfter(self.cnvs.request_drawing_update)

    def on_left_up(self, evt):
        super(RepetitionSelectOverlay, self).on_left_up(evt)

        if self._roa:
            if self.active:
                if self.get_size() != (None, None):
                    phys_rect = self.get_physical_sel()
                    rel_rect = self.cnvs.convert_roi_phys_to_ratio(phys_rect)

                    # Update VA. We need to unsubscribe to be sure we don't received
                    # intermediary values as the VA is modified by the stream further on, and
                    # VA don't ensure the notifications are ordered (so the listener could
                    # receive the final value, and then our requested ROI value).
                    self._roa.unsubscribe(self.on_roa)
                    self._roa.value = rel_rect
                    self._roa.subscribe(self.on_roa, init=True)
                else:
                    self._roa.value = UNDEFINED_ROI

        else:
            logging.warn("Expected ROA not found!")

    def _draw_points(self, ctx):
        # Calculate the offset of the center of the buffer relative to the
        # top left op the buffer
        offset = self.cnvs.get_half_buffer_size()

        # The start and end position, in buffer coordinates. The return
        # values may extend beyond the actual buffer when zoomed in.
        b_pos = (self.cnvs.world_to_buffer(self.w_start_pos, offset) +
                 self.cnvs.world_to_buffer(self.w_end_pos, offset))
        b_pos = self._normalize(b_pos)
        # logging.debug("start and end buffer pos: %s", b_pos)

        # Calculate the width and height in buffer pixels. Again, this may
        # be wider and higher than the actual buffer.
        width = b_pos[2] - b_pos[0]
        height = b_pos[3] - b_pos[1]

        # logging.debug("width and height: %s %s", width, height)

        # Clip the start and end positions using the actual buffer size
        start_x, start_y = self.cnvs.clip_to_buffer(b_pos[:2])
        end_x, end_y = self.cnvs.clip_to_buffer(b_pos[2:4])

        # logging.debug(
        #     "clipped start and end: %s", (start_x, start_y, end_x, end_y))

        rep_x, rep_y = self.repetition

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        if width // 3 < rep_x or height // 3 < rep_y:
            # If we cannot fit enough 3 bitmaps into either direction,
            # then we just fill a semi transparent rectangle
            logging.debug("simple fill")
            r, g, b, _ = self.colour
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.rectangle(
                start_x, start_y,
                int(end_x - start_x), int(end_y - start_y))
            ctx.fill()
            ctx.stroke()
        else:
            # check whether the cache is still valid
            cl_pos = (start_x, start_y, end_x, end_y)
            if not self._bmp or self._bmp_bpos != cl_pos:
                # Cache the image as it's quite a lot of computations
                half_step_x = step_x / 2
                half_step_y = step_y / 2

                # The number of repetitions that fits into the buffer
                # clipped selection
                buf_rep_x = int((end_x - start_x) / step_x)
                buf_rep_y = int((end_y - start_y) / step_y)

                # TODO: need to take into account shift, like drawGrid
                logging.debug("Rendering %sx%s points", buf_rep_x, buf_rep_y)

                point = img.getdotBitmap()
                point_dc = wx.MemoryDC()
                point_dc.SelectObject(point)
                point.SetMaskColour(wx.BLACK)

                horz_dc = wx.MemoryDC()
                horz_bmp = wx.EmptyBitmap(int(end_x - start_x), 3)
                horz_dc.SelectObject(horz_bmp)
                horz_dc.SetBackground(wx.BLACK_BRUSH)
                horz_dc.Clear()

                blit = horz_dc.Blit
                for i in range(buf_rep_x):
                    x = i * step_x + half_step_x
                    blit(x, 0, 3, 3, point_dc, 0, 0)

                total_dc = wx.MemoryDC()
                self._bmp = wx.EmptyBitmap(int(end_x - start_x), int(end_y - start_y))
                total_dc.SelectObject(self._bmp)
                total_dc.SetBackground(wx.BLACK_BRUSH)
                total_dc.Clear()

                blit = total_dc.Blit
                for j in range(buf_rep_y):
                    y = j * step_y + half_step_y
                    blit(0, y, int(end_x - start_x), 3, horz_dc, 0, 0)

                self._bmp.SetMaskColour(wx.BLACK)
                self._bmp_bpos = cl_pos

            self.cnvs.dc_buffer.DrawBitmapPoint(
                self._bmp,
                wx.Point(int(start_x), int(start_y)),
                useMask=True
            )

    def _draw_grid(self, ctx):
        # Calculate the offset of the center of the buffer relative to the
        # top left op the buffer
        offset = self.cnvs.get_half_buffer_size()

        # The start and end position, in buffer coordinates. The return
        # values may extend beyond the actual buffer when zoomed in.
        b_pos = (self.cnvs.world_to_buffer(self.w_start_pos, offset) +
                 self.cnvs.world_to_buffer(self.w_end_pos, offset))
        b_pos = self._normalize(b_pos)
        # logging.debug("start and end buffer pos: %s", b_pos)

        # Calculate the width and height in buffer pixels. Again, this may
        # be wider and higher than the actual buffer.
        width = b_pos[2] - b_pos[0]
        height = b_pos[3] - b_pos[1]

        # logging.debug("width and height: %s %s", width, height)

        # Clip the start and end positions using the actual buffer size
        start_x, start_y = self.cnvs.clip_to_buffer(b_pos[:2])
        end_x, end_y = self.cnvs.clip_to_buffer(b_pos[2:4])

        # logging.debug("clipped start and end: %s", (start_x, start_y, end_x, end_y))

        rep_x, rep_y = self.repetition

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        r, g, b, _ = self.colour

        # If there are more repetitions in either direction than there
        # are pixels, just fill a semi transparent rectangle
        if width < rep_x or height < rep_y:
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.rectangle(
                start_x, start_y,
                int(end_x - start_x), int(end_y - start_y))
            ctx.fill()
        else:
            ctx.set_source_rgba(r, g, b, 0.9)
            ctx.set_line_width(1)
            # ctx.set_antialias(cairo.ANTIALIAS_DEFAULT)

            # The number of repetitions that fits into the buffer clipped
            # selection
            buf_rep_x = int(round((end_x - start_x) / step_x))
            buf_rep_y = int(round((end_y - start_y) / step_y))
            buf_shift_x = (b_pos[0] - start_x) % step_x
            buf_shift_y = (b_pos[1] - start_y) % step_y

            for i in range(1, buf_rep_x):
                ctx.move_to(start_x - buf_shift_x + i * step_x, start_y)
                ctx.line_to(start_x - buf_shift_x + i * step_x, end_y)

            for i in range(1, buf_rep_y):
                ctx.move_to(start_x, start_y - buf_shift_y + i * step_y)
                ctx.line_to(end_x, start_y - buf_shift_y + i * step_y)

            ctx.stroke()

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        mode_cache = self.selection_mode

        if self.w_start_pos and self.w_end_pos and 0 not in self.repetition:
            ctx.save()
            if self.fill == self.FILL_POINT:
                self._draw_points(ctx)
                self.selection_mode = base.SEL_MODE_EDIT
            elif self.fill == self.FILL_GRID:
                self._draw_grid(ctx)
                self.selection_mode = base.SEL_MODE_EDIT
            ctx.restore()

        super(RepetitionSelectOverlay, self).draw(ctx, shift, scale)
        self.selection_mode = mode_cache


class LineSelectOverlay(WorldSelectOverlay):
    """ Selection overlay that allows for the selection of a line in world coordinates"""

    def __init__(self, cnvs):
        super(LineSelectOverlay, self).__init__(cnvs)

    @property
    def length(self):
        if None in (self.w_start_pos, self.w_end_pos):
            return 0
        else:
            x1, y1 = self.w_start_pos
            x2, y2 = self.w_end_pos
            return math.hypot(x2-x1, y2-y1)

    def _calc_world_pos(self):
        """ Update the world position to reflect the view position """

        if None not in (self.v_start_pos, self.v_end_pos):
            offset = self.cnvs.get_half_buffer_size()
            self.w_start_pos = self.cnvs.view_to_world(self.v_start_pos, offset)
            self.w_end_pos = self.cnvs.view_to_world(self.v_end_pos, offset)

            # FIXME: Translate to integer values
        #     self._selected_line.value = (self.w_start_pos, self.w_end_pos)
        # else:
        #     self._selected_line.value = None

    @staticmethod
    def _normalize(rect):
        """ Lines don't need to be normalized """
        return rect

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if None not in (self.w_start_pos, self.w_end_pos) and self.w_start_pos != self.w_end_pos:

            ctx.save()

            # Pixel radius of the start marker
            start_radius = 3
            arrow_size = 12

            # Important: We need to use the world positions, in order to draw everything at the
            # right scale.
            offset = self.cnvs.get_half_buffer_size()
            # Calculate buffer start and end positions
            b_pos = self.cnvs.world_to_buffer(self.w_start_pos, offset)
            b_start = (b_pos[0] - 0.5, b_pos[1] - 0.5)
            b_pos = self.cnvs.world_to_buffer(self.w_end_pos, offset)
            b_end = (b_pos[0] + 0.5, b_pos[1] + 0.5)

            # Calculate unit vector
            dx, dy = (self.w_start_pos[0] - self.w_end_pos[0],
                      self.w_start_pos[1] - self.w_end_pos[1])
            length = math.sqrt(dx*dx + dy*dy) or 0.000001
            udx, udy = dx / length, dy / length  # Normalized vector

            # Rotate over 60 and -60 degrees
            ax = udx * math.sqrt(3) / 2 - udy / 2
            ay = udx / 2 + udy * math.sqrt(3)/2
            bx = udx * math.sqrt(3)/2 + udy / 2
            by = -udx / 2 + udy * math.sqrt(3)/2

            # The two lower corners of the arrow head
            b_arrow_1 = (b_end[0] + arrow_size * ax, b_end[1] + arrow_size * ay)
            b_arrow_2 = (b_end[0] + arrow_size * bx, b_end[1] + arrow_size * by)

            # Connection point for the line at the base of the arrow
            b_arrow_con = ((b_arrow_1[0] + b_arrow_2[0]) / 2.0,
                           (b_arrow_1[1] + b_arrow_2[1]) / 2.0)

            # Calculate the connection to the start circle
            rad = math.atan2(b_start[1] - b_end[1],  b_start[0] - b_end[0])
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

            ctx.set_source_rgba(*self.colour)
            ctx.set_line_width(1.5)
            ctx.arc(b_start[0], b_start[1], start_radius, 0, 2*math.pi)
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
            ctx.set_source_rgba(*self.colour)
            ctx.fill()

            ctx.restore()

    def _calc_edges(self):
        """ Calculate the hit boxes for the start and end point """
        if None not in (self.v_start_pos, self.v_end_pos):
            rect = self.v_start_pos + self.v_end_pos
            s_l, s_t, e_l, e_t = [v - self.hover_margin for v in rect]
            s_r, s_b, e_r, e_b = [v + self.hover_margin for v in rect]

            self.edges = {
                "s_l": s_l,
                "s_t": s_t,
                "s_r": s_r,
                "s_b": s_b,
                "e_l": e_l,
                "e_t": e_t,
                "e_r": e_r,
                "e_b": e_b
            }
        else:
            self.edges = {}

    def is_hovering(self, vpos):
        """ Check if the given position is on/near the start or end of the line

        :return: (bool) Return False if not hovering, or the type of hover

        """

        if self.edges:
            if self.edges["s_l"] < vpos[0] < self.edges["s_r"]:
                if self.edges["s_t"] < vpos[1] < self.edges["s_b"]:
                    return gui.HOVER_START
            elif self.edges["e_l"] < vpos[0] < self.edges["e_r"]:
                if self.edges["e_t"] < vpos[1] < self.edges["e_b"]:
                    return gui.HOVER_END

        return None

    def _on_motion(self, evt):
        v_pos = evt.GetPositionTuple()
        hover = self.is_hovering(v_pos)

        if not evt.Dragging():
            if hover in (gui.HOVER_START, gui.HOVER_END):
                self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
            else:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_PENCIL)
        else:
            super(LineSelectOverlay, self)._on_motion(evt)

    def update_edit(self, current_pos):
        """ Adjust the selection according to the given position and the current edit action """
        current_pos = self.cnvs.clip_to_viewport(current_pos)

        if self.edit_edge == gui.HOVER_START:
            self.v_start_pos = current_pos
            self._calc_world_pos()
        elif self.edit_edge == gui.HOVER_END:
            self.v_end_pos = current_pos
            self._calc_world_pos()

        self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENWSE)

    def stop_edit(self):
        super(LineSelectOverlay, self).stop_edit()
        self.cnvs.reset_dynamic_cursor()
        self.cnvs.update_drawing()

    # def set_line_va(self, selected_line_va):
    #     """ Set the line VA """
    #     self._selected_line = selected_line_va
    #     self._selected_line.subscribe(self._selection_made, init=True)
    #
    # def _selection_made(self, selected_line):
    #     """ Event handler that requests a redraw when the selected pixel changes """
    #     if selected_line is not None:
    #         self.w_start_pos, self.w_end_pos = selected_line
    #         self.cnvs.update_drawing()


class SpectrumMixin(object):

    def __init__(self):
        # The current position of the mouse cursor in view coordinates
        self._mouse_vpos = None

        # External values
        self._mpp = None  # Meter per pixel
        self._physical_center = None  # in meter (float, float)
        self._resolution = None  # Pixels in linked data (int, int)
        self._selection = None  # TupleVA (int, int)

        # Calculated values
        self._topleft_wpos = None  # in world units (float, float)
        self._pixel_wsize = None  # cnvs size of the pixel block (float, float)
        self._pixel_pos = None  # position of the current pixel (int, int)

    def set_values(self, mpp, physical_center, resolution, selection_va):
        """ Set the values needed for mapping mouse positions to pixel coordinates

        :param mpp: (float) Size of the pixels in meters
        :param physical_center: (float, float) The center of the pixel data in
            physical coordinates.
        :param resolution: (int, int) The width and height of the pixel data

        """

        if len(physical_center) != 2:
            raise ValueError("Illegal values for SpectrumLineSelectOverlay")

        msg = "Setting mpp to %s, physical center to %s and resolution to %s"
        logging.debug(msg, mpp, physical_center, resolution)

        self._mpp = mpp
        self._physical_center = physical_center
        self._resolution = resolution

        self._selection = selection_va
        self._selection.subscribe(self._selection_made, init=True)

        self._calc_core_values()

    def _selection_made(self, _):
        """ Event handler that requests a redraw when the selected line changes """
        self.cnvs.update_drawing()

    def _calc_core_values(self):
        """ Calculate the core values that only change when the external values change. """

        if self.values_are_set():
            # Get the physical size of the external data
            physical_size = (self._resolution[0] * self._mpp,
                             self._resolution[1] * self._mpp)
            # Physical half width and height
            p_w = physical_size[0] / 2.0
            p_h = physical_size[1] / 2.0

            # Get the top left corner of the external data
            # Remember that in physical coordinates, up is positive!
            phys_topleft = (self._physical_center[0] - p_w,
                            self._physical_center[1] + p_h)

            self._topleft_wpos = self.cnvs.physical_to_world_pos(phys_topleft)

            logging.debug("Physical top left of PixelSelectOverlay: %s",
                          self._physical_center)

            # Calculate the size, in meters, of each pixel.
            # This size, together with the view's scale, will be used to
            # calculate the actual (int, int) size, before rendering
            # Note: Since the mpwu is always 1 (and will like be removed at a
            # later stage), the physical and world sizes are the same!
            self._pixel_wsize = (physical_size[0] / self._resolution[0],
                                 physical_size[1] / self._resolution[1])

    def values_are_set(self):
        """ Returns True if all needed values are set """
        return None not in (self._mpp, self._physical_center, self._resolution, self._selection)

    def is_over_pixel_data(self):
        """ Check if the mouse cursor is over an area containing pixel data """

        if self._mouse_vpos:
            offset = self.cnvs.get_half_buffer_size()
            wpos = self.cnvs.view_to_world(self._mouse_vpos, offset)
            # FIXME: This works because world units are on a 1:1 scale with
            # physical units.
            physical_size = (self._resolution[0] * self._mpp,
                             self._resolution[1] * self._mpp)
            if 0 <= wpos[0] - self._topleft_wpos[0] <= physical_size[0]:
                if 0 <= wpos[1] - self._topleft_wpos[1] <= physical_size[1]:
                    return True

        return False

    def view_to_pixel(self):
        """ Translate a view coordinate into a data pixel coordinate

        The pixel coordinates have their 0,0 origin at the top left.

        """

        if self._mouse_vpos:
            # The offset, in pixels, to the center of the world coordinates
            offset = self.cnvs.get_half_buffer_size()
            wpos = self.cnvs.view_to_world(self._mouse_vpos, offset)

            # Calculate the distance to the top left in world units
            dist = (wpos[0] - self._topleft_wpos[0],
                    wpos[1] - self._topleft_wpos[1])

            # Calculate overlay pixels, (0,0) is top left.
            self._pixel_pos = (int(dist[0] / self._mpp),
                               int(dist[1] / self._mpp))

            # lbl = "Pixel {},{}, pos {:10.8f},{:10.8f}, dist {:10.8f},{:10.8f}"
            # self.label =  lbl.format(
            #                 *(pixel + (ppx, ppy) + dist))

    def pixel_to_rect(self, pixel, scale):
        """ Return a rectangle, in buffer coordinates, describing the current
        pixel.

        :param scale: (float) The scale to draw the pixel at.
        :return: (top, left, width, height)
        """
        # First we calculate the position of the top left in buffer pixels
        # Note the Y flip again, since were going from pixel to physical
        # coordinates
        offset_x = pixel[0] * self._mpp
        offset_y = pixel[1] * self._mpp

        w_top_left = (self._topleft_wpos[0] + offset_x,
                      self._topleft_wpos[1] + offset_y)

        offset = self.cnvs.get_half_buffer_size()

        # No need for an explicit Y flip here, since `physical_to_world_pos`
        # takes care of that
        b_top_left = self.cnvs.world_to_buffer(w_top_left, offset)

        b_width = (self._pixel_wsize[0] * scale + 0.5,
                   self._pixel_wsize[1] * scale + 0.5)

        return b_top_left + b_width


class SpectrumLineSelectOverlay(LineSelectOverlay, SpectrumMixin):

    def __init__(self, cnvs):
        super(SpectrumLineSelectOverlay, self).__init__(cnvs)
        SpectrumMixin.__init__(self)

    def on_motion(self, evt):
        super(SpectrumLineSelectOverlay, self).on_motion(evt)
        self._mouse_vpos = evt.GetPositionTuple()

    def _on_motion(self, evt):
        v_pos = evt.GetPositionTuple()
        hover = self.is_hovering(v_pos)
        over_data = self.is_over_pixel_data()

        if not evt.Dragging():
            if hover in (gui.HOVER_START, gui.HOVER_END):
                self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
            elif over_data:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_PENCIL)
            else:
                self.cnvs.reset_dynamic_cursor()
        else:
            super(LineSelectOverlay, self)._on_motion(evt)

    def on_left_down(self, evt):
        """ Start drag action if enabled, otherwise call super method so event will propagate """
        over_data = self.is_over_pixel_data()

        if self.active and over_data:
            super(SpectrumLineSelectOverlay, self)._on_left_down(evt)
            self.cnvs.update_drawing()

    def _calc_world_pos(self):
        """ Update the world position to reflect the view position """

        if None not in (self.v_start_pos, self.v_end_pos):
            offset = self.cnvs.get_half_buffer_size()
            print self.view_to_pixel()
            self.w_start_pos = self.cnvs.view_to_world(self.v_start_pos, offset)
            self.w_end_pos = self.cnvs.view_to_world(self.v_end_pos, offset)

            # FIXME: Translate to integer values
        #     self._selected_line.value = (self.w_start_pos, self.w_end_pos)
        # else:
        #     self._selected_line.value = None


class PixelSelectOverlay(WorldOverlay, SpectrumMixin, DragMixin):
    """ Selection overlay that allows the selection of a pixel in a data set """

    def __init__(self, cnvs):
        super(PixelSelectOverlay, self).__init__(cnvs)
        SpectrumMixin.__init__(self)
        DragMixin.__init__(self)

        # # External values
        # self._mpp = None  # Meter per pixel
        # self._physical_center = None  # in meter (float, float)
        # self._resolution = None  # Pixels in linked data (int, int)
        # self._selected_pixel = None  # TupleVA (int, int)

        # # Calculated values
        # self._topleft_wpos = None  # in world units (float, float)
        # self._pixel_wsize = None  # cnvs size of the pixel block (float, float)
        # self._pixel_pos = None  # position of the current pixel (int, int)

        self.colour = conversion.hex_to_frgba(gui.SELECTION_COLOUR, 0.5)
        self.select_color = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, 0.5)

    def deactivate(self):
        """ Clear the hover pixel when the overlay is deactivated """
        self._pixel_pos = None
        super(PixelSelectOverlay, self).deactivate()
        self.cnvs.update_drawing()

    # Event handlers

    def on_motion(self, evt):
        """ Update the current mouse position """

        if self.active:
            if self.values_are_set():
                self.cnvs.set_dynamic_cursor(wx.CROSS_CURSOR)
                self._mouse_vpos = evt.GetPositionTuple()
                old_pixel_pos = self._pixel_pos
                self.view_to_pixel()
                if self._pixel_pos != old_pixel_pos:
                    if self.is_over_pixel_data() and self.left_dragging:
                        self._selection.value = self._pixel_pos
                        logging.debug("Pixel %s selected", self._selection.value)
                    self.cnvs.update_drawing()
            else:
                self.cnvs.reset_dynamic_cursor()

        super(PixelSelectOverlay, self).on_motion(evt)

    def on_left_down(self, evt):

        if self.active:
            if self.values_are_set():
                super(PixelSelectOverlay, self)._on_left_down(evt)
        else:
            super(PixelSelectOverlay, self).on_left_down(evt)

    def on_left_up(self, evt):
        """ Set the selected pixel, if a pixel position is known """

        if self.active:
            if self._pixel_pos and self.is_over_pixel_data():
                if self._selection.value != self._pixel_pos:
                    self._selection.value = self._pixel_pos
                    self.cnvs.update_drawing()
                    logging.debug("Pixel %s selected", self._selection.value)
            super(PixelSelectOverlay, self)._on_left_up(evt)

        # Call the super class,
        super(PixelSelectOverlay, self).on_left_up(evt)

    # END Event handlers

    # def is_over_pixel_data(self):
    #     """ Check if the mouse cursor is over an area containing pixel data """
    #
    #     if self._mouse_vpos:
    #         offset = self.cnvs.get_half_buffer_size()
    #         wpos = self.cnvs.view_to_world(self._mouse_vpos, offset)
    #         # FIXME: This works because world units are on a 1:1 scale with
    #         # physical units.
    #         physical_size = (self._resolution[0] * self._mpp,
    #                          self._resolution[1] * self._mpp)
    #         if 0 <= wpos[0] - self._topleft_wpos[0] <= physical_size[0]:
    #             if 0 <= wpos[1] - self._topleft_wpos[1] <= physical_size[1]:
    #                 return True
    #
    #     return False

    # def set_values(self, mpp, physical_center, resolution, selected_pixel_va):
    #     """ Set the values needed for mapping mouse positions to pixel coordinates
    #
    #     :param mpp: (float) Size of the pixels in meters
    #     :param physical_center: (float, float) The center of the pixel data in
    #         physical coordinates.
    #     :param resolution: (int, int) The width and height of the pixel data
    #
    #     """
    #
    #     if len(physical_center) != 2:
    #         raise ValueError("Illegal values for PixelSelectOverlay")
    #
    #     msg = "Setting mpp to %s, physical center to %s and resolution to %s"
    #     logging.debug(msg, mpp, physical_center, resolution)
    #     self._mpp = mpp
    #     self._physical_center = physical_center
    #     self._resolution = resolution
    #
    #     self._selected_pixel = selected_pixel_va
    #     self._selected_pixel.subscribe(self._selection_made, init=True)
    #
    #     self._calc_core_values()

    # def _selection_made(self, _):
    #     """ Event handler that requests a redraw when the selected pixel changes """
    #     self.cnvs.update_drawing()

    # def values_are_set(self):
    #     """ Returns True if all needed values are set """
    #     return None not in (self._mpp,
    #                         self._physical_center,
    #                         self._resolution,
    #                         self._selected_pixel)

    # def _calc_core_values(self):
    #     """ Calculate the core values that only change when the external values change. """
    #
    #     if self.values_are_set():
    #         # Get the physical size of the external data
    #         physical_size = (self._resolution[0] * self._mpp,
    #                          self._resolution[1] * self._mpp)
    #         # Physical half width and height
    #         p_w = physical_size[0] / 2.0
    #         p_h = physical_size[1] / 2.0
    #
    #         # Get the top left corner of the external data
    #         # Remember that in physical coordinates, up is positive!
    #         phys_topleft = (self._physical_center[0] - p_w,
    #                         self._physical_center[1] + p_h)
    #
    #         self._topleft_wpos = self.cnvs.physical_to_world_pos(phys_topleft)
    #
    #         logging.debug("Physical top left of PixelSelectOverlay: %s",
    #                       self._physical_center)
    #
    #         # Calculate the size, in meters, of each pixel.
    #         # This size, together with the view's scale, will be used to
    #         # calculate the actual (int, int) size, before rendering
    #         # Note: Since the mpwu is always 1 (and will like be removed at a
    #         # later stage), the physical and world sizes are the same!
    #         self._pixel_wsize = (physical_size[0] / self._resolution[0],
    #                              physical_size[1] / self._resolution[1])

    # def view_to_pixel(self):
    #     """ Translate a view coordinate into a data pixel coordinate
    #
    #     The pixel coordinates have their 0,0 origin at the top left.
    #
    #     """
    #
    #     if self._mouse_vpos:
    #         # The offset, in pixels, to the center of the world coordinates
    #         offset = self.cnvs.get_half_buffer_size()
    #         wpos = self.cnvs.view_to_world(self._mouse_vpos, offset)
    #
    #         # Calculate the distance to the top left in world units
    #         dist = (wpos[0] - self._topleft_wpos[0],
    #                 wpos[1] - self._topleft_wpos[1])
    #
    #         # Calculate overlay pixels, (0,0) is top left.
    #         self._pixel_pos = (int(dist[0] / self._mpp),
    #                            int(dist[1] / self._mpp))
    #
    #         # lbl = "Pixel {},{}, pos {:10.8f},{:10.8f}, dist {:10.8f},{:10.8f}"
    #         # self.label =  lbl.format(
    #         #                 *(pixel + (ppx, ppy) + dist))
    #
    # def pixel_to_rect(self, pixel, scale):
    #     """ Return a rectangle, in buffer coordinates, describing the current
    #     pixel.
    #
    #     :param scale: (float) The scale to draw the pixel at.
    #     :return: (top, left, width, height)
    #     """
    #     # First we calculate the position of the top left in buffer pixels
    #     # Note the Y flip again, since were going from pixel to physical
    #     # coordinates
    #     offset_x = pixel[0] * self._mpp
    #     offset_y = pixel[1] * self._mpp
    #
    #     w_top_left = (self._topleft_wpos[0] + offset_x,
    #                   self._topleft_wpos[1] + offset_y)
    #
    #     offset = self.cnvs.get_half_buffer_size()
    #
    #     # No need for an explicit Y flip here, since `physical_to_world_pos`
    #     # takes care of that
    #     b_top_left = self.cnvs.world_to_buffer(w_top_left, offset)
    #
    #     b_width = (self._pixel_wsize[0] * scale + 0.5,
    #                self._pixel_wsize[1] * scale + 0.5)
    #
    #     return b_top_left + b_width

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        if self._selection:
            if (self._pixel_pos and self._selection.value != self._pixel_pos and
                    self.is_over_pixel_data()):

                rect = self.pixel_to_rect(self._pixel_pos, scale)
                if rect:
                    ctx.set_source_rgba(*self.colour)
                    ctx.rectangle(*rect)
                    ctx.fill()

            if self._selection.value not in (None, (None, None)):
                rect = self.pixel_to_rect(self._selection.value, scale)

                if rect:
                    ctx.set_source_rgba(*self.select_color)
                    ctx.rectangle(*rect)
                    ctx.fill()


class PointsOverlay(WorldOverlay):
    """ Overlay showing the available points and allowing the selection of one of them """

    MAX_DOT_RADIUS = 25.5
    MIN_DOT_RADIUS = 3.5

    def __init__(self, cnvs):
        super(PointsOverlay, self).__init__(cnvs)

        # A VA tracking the selected point
        self.point = None
        # The possible choices for point as a world pos => point mapping
        self.choices = {}

        self.min_dist = None

        # Appearance
        self.point_colour = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.select_colour = conversion.hex_to_frgba(gui.FG_COLOUR_EDIT, 0.5)
        self.dot_colour = (0, 0, 0, 0.1)
        # The float radius of the dots to draw
        self.dot_size = self.MIN_DOT_RADIUS
        # None or the point over which the mouse is hovering
        self.cursor_over_point = None
        # The box over which the mouse is hovering, or None
        self.b_hover_box = None

    def set_point(self, point_va):
        """ Set the available points and connect to the given point VA """
        # Connect the provided VA to the overlay
        self.point = point_va
        self.point.subscribe(self._on_point_selected)
        self._calc_choices()
        self.cnvs.microscope_view.mpp.subscribe(self._on_mpp, init=True)

    def _on_point_selected(self, _):
        """ Update the overlay when a point has been selected """
        self.cnvs.repaint()

    def _on_mpp(self, mpp):
        """ Calculate the values dependant on the mpp attribute
        (i.e. when the zoom level of the canvas changes)
        """
        self.dot_size = max(min(self.MAX_DOT_RADIUS, self.min_dist / mpp), self.MIN_DOT_RADIUS)

    # Event Handlers

    def on_left_down(self, evt):
        super(PointsOverlay, self).on_left_down(evt)

    def on_left_up(self, evt):
        """ Set the selected point if the mouse cursor is hovering over one """
        if self.active:
            # Clear the hover when the canvas was dragged
            if self.cursor_over_point and not self.cnvs.was_dragged:
                self.point.value = self.choices[self.cursor_over_point]
                logging.debug("Point %s selected", self.point.value)
                self.cnvs.update_drawing()
            elif self.cnvs.was_dragged:
                self.cursor_over_point = None
                self.b_hover_box = None

        super(PointsOverlay, self).on_left_up(evt)

    def on_wheel(self, evt):
        """ Clear the hover when the canvas is zooming """
        if self.active:
            self.cursor_over_point = None
            self.b_hover_box = None

        super(PointsOverlay, self).on_wheel(evt)

    def on_motion(self, evt):
        """ Detect when the cursor hovers over a dot """
        if self.active:
            if not self.cnvs.left_dragging and self.choices:
                v_x, v_y = evt.GetPositionTuple()
                b_x, b_y = self.cnvs.view_to_buffer((v_x, v_y))
                offset = self.cnvs.get_half_buffer_size()

                b_hover_box = None

                for w_pos in self.choices.keys():
                    b_box_x, b_box_y = self.cnvs.world_to_buffer(w_pos, offset)

                    if abs(b_box_x - b_x) <= self.dot_size and abs(b_box_y - b_y) <= self.dot_size:
                        # Calculate box in buffer coordinates
                        b_hover_box = (b_box_x - self.dot_size,
                                       b_box_y - self.dot_size,
                                       b_box_x + self.dot_size,
                                       b_box_y + self.dot_size)
                        break

                if self.b_hover_box != b_hover_box:
                    self.b_hover_box = b_hover_box
                    self.cnvs.repaint()

            if self.cursor_over_point:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
            else:
                self.cnvs.reset_dynamic_cursor()

        super(PointsOverlay, self).on_motion(evt)

    def _calc_choices(self):
        """ Create a mapping between world coordinates and physical points

        The minimum physical distance between points is also calculated
        """

        logging.debug("Calculating choices as buffer positions")

        self.choices = {}
        min_dist = 0

        def distance(p1, p2):
            return math.sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)

        # Translate physical to buffer coordinates

        physical_points = [c for c in self.point.choices if None not in c]

        if len(physical_points) > 1:
            for p_point in physical_points:
                w_x, w_y = self.cnvs.physical_to_world_pos(p_point)
                self.choices[(w_x, w_y)] = p_point
                min_dist = min(distance(p_point, d) for d in physical_points if d != p_point)
        else:
            # can't compute the distance => pick something typical
            min_dist = 100e-9  # m

            if len(physical_points) == 1:
                w_x, w_y = self.cnvs.physical_to_world_pos(physical_points[0])
                self.choices[(w_x, w_y)] = physical_points[0]

        self.min_dist = min_dist / 2.0  # get radius

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if not self.choices or not self.active:
            return

        if self.b_hover_box:
            b_l, b_t, b_r, b_b = self.b_hover_box

        w_cursor_over = None
        offset = self.cnvs.get_half_buffer_size()

        for w_pos in self.choices.keys():
            b_x, b_y = self.cnvs.world_to_buffer(w_pos, offset)

            ctx.move_to(b_x, b_y)
            ctx.arc(b_x, b_y, self.dot_size, 0, 2*math.pi)

            # If the mouse is hovering over a dot (and we are not dragging)
            if (self.b_hover_box and (b_l <= b_x <= b_r and b_t <= b_y <= b_b) and
                    not self.cnvs.was_dragged):
                w_cursor_over = w_pos
                ctx.set_source_rgba(*self.select_colour)
            elif self.point.value == self.choices[w_pos]:
                ctx.set_source_rgba(*self.select_colour)
            else:
                ctx.set_source_rgba(*self.dot_colour)

            ctx.fill()

            ctx.arc(b_x, b_y, 2.0, 0, 2*math.pi)
            ctx.set_source_rgb(0.0, 0.0, 0.0)
            ctx.fill()

            ctx.arc(b_x, b_y, 1.5, 0, 2*math.pi)
            ctx.set_source_rgb(*self.point_colour)
            ctx.fill()

            # Draw hitboxes (for debugging purposes)
            # ctx.set_line_width(1)
            # ctx.set_source_rgb(1.0, 1.0, 1.0)

            # ctx.rectangle(b_x - self.dot_size * 0.95,
            #               b_y - self.dot_size * 0.95,
            #               self.dot_size * 1.9,
            #               self.dot_size * 1.9)

            # ctx.stroke()

        self.cursor_over_point = w_cursor_over
