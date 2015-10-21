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

from odemis.acq.stream import UNDEFINED_ROI
import odemis.gui as gui
from odemis.gui.comp.overlay.base import Vec, WorldOverlay, SelectionMixin, DragMixin, \
    PixelDataMixin, SEL_MODE_EDIT, SEL_MODE_CREATE, EDIT_MODE_BOX, EDIT_MODE_POINT
import odemis.gui.img.data as img
from odemis.gui.util.raster import rasterize_line
from odemis.model import TupleVA
from odemis.util import clip_line
import odemis.util.conversion as conversion
import odemis.util.units as units


class WorldSelectOverlay(WorldOverlay, SelectionMixin):

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        WorldOverlay.__init__(self, cnvs)
        SelectionMixin.__init__(self, colour, center, EDIT_MODE_BOX)

        self._w_start_pos = None
        self._w_end_pos = None

        self.position_label = self.add_label("", colour=(0.8, 0.8, 0.8), align=wx.ALIGN_RIGHT)

    @property
    def w_start_pos(self):
        return self._w_start_pos

    @w_start_pos.setter
    def w_start_pos(self, w_pos):
        self._w_start_pos = w_pos
        self._world_to_view()

    @property
    def w_end_pos(self):
        return self._w_end_pos

    @w_end_pos.setter
    def w_end_pos(self, w_pos):
        self._w_end_pos = w_pos
        self._world_to_view()

    # Selection clearing

    def clear_selection(self):
        """ Clear the current selection """
        SelectionMixin.clear_selection(self)
        self.w_start_pos = None
        self.w_end_pos = None

    def _view_to_world(self):
        """ Update the world position to reflect the view position """

        if self.select_v_start_pos and self.select_v_end_pos:
            wsp = self.cnvs.view_to_world(self.select_v_start_pos, self.offset_b)
            wep = self.cnvs.view_to_world(self.select_v_end_pos, self.offset_b)
            self.w_start_pos = wsp
            self.w_end_pos = wep

    def _world_to_view(self):
        """ Update the view position to reflect the world position """

        if self.w_start_pos and self.w_end_pos:
            vsp = self.cnvs.world_to_view(self.w_start_pos, self.offset_b)
            vep = self.cnvs.world_to_view(self.w_end_pos, self.offset_b)
            self.select_v_start_pos = Vec(vsp)
            self.select_v_end_pos = Vec(vep)
            self._calc_edges()

    def get_physical_sel(self):
        """ Return the selected rectangle in physical coordinates

        :return: (tuple of 4 floats) Position in m

        """

        if self.w_start_pos and self.w_end_pos:
            p_pos = (self.cnvs.world_to_physical_pos(self.w_start_pos) +
                     self.cnvs.world_to_physical_pos(self.w_end_pos))
            return self._normalize_rect(p_pos)
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
            w_pos = self._normalize_rect(w_pos)
            self.w_start_pos = w_pos[:2]
            self.w_end_pos = w_pos[2:4]
            self._world_to_view()

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the selection as a rectangle """

        if self.w_start_pos and self.w_end_pos:

            # translate the origin to the middle of the buffer
            ctx.translate(*self.offset_b)

            # Important: We need to use the world positions, in order to draw everything at the
            # right scale.
            b_start_pos = self.cnvs.world_to_buffer(self.w_start_pos)
            b_end_pos = self.cnvs.world_to_buffer(self.w_end_pos)
            b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)

            self.update_projection(b_start_pos, b_end_pos, shift + (scale,))

            # logging.warn("%s %s", shift, world_to_buffer_pos(shift))
            rect = (b_start_pos.x,
                    b_start_pos.y,
                    b_end_pos.x - b_start_pos.x,
                    b_end_pos.y - b_start_pos.y)

            # draws a light black background for the rectangle
            ctx.set_line_width(4)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.rectangle(*rect)
            ctx.stroke()

            # draws the dotted line
            ctx.set_line_width(2)
            ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            ctx.rectangle(*rect)
            ctx.stroke()

            self._debug_draw_edges(ctx, True)

            # Label
            if (self.selection_mode in (SEL_MODE_EDIT, SEL_MODE_CREATE) and
                    self.cnvs.microscope_view):
                w, h = self.cnvs.selection_to_real_size(self.w_start_pos, self.w_end_pos)
                w = units.readable_str(w, 'm', sig=2)
                h = units.readable_str(h, 'm', sig=2)
                size_lbl = u"{} x {}".format(w, h)

                pos = Vec(b_end_pos.x - 8, b_end_pos.y + 5)

                self.position_label.pos = pos
                self.position_label.text = size_lbl
                self._write_labels(ctx)

    # Event Handlers

    def on_left_down(self, evt):
        """ Start drag action if enabled, otherwise call super method so event will propagate """
        if self.active:
            SelectionMixin._on_left_down(self, evt)
            self._view_to_world()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ End drag action if enabled, otherwise call super method so event will propagate """
        if self.active:
            SelectionMixin._on_left_up(self, evt)
            self._view_to_world()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_enter(self, evt):
        if self.active:
            self.cnvs.set_default_cursor(wx.CURSOR_CROSS)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENESW)  # = closed hand
                elif self.hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZEWE)
                elif self.hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENS)
                elif self.hover:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
                else:
                    self.cnvs.reset_dynamic_cursor()
            else:
                self._view_to_world()

            # Fixme: Find a way to render the selection at the full frame rate. Right now it's not
            # possible, because we are drawing directly into the buffer, which might render slowly
            # anyway. What we would want, is that a world overlay that can visually change when the
            # mouse moves, draws into the view. After the motion is done, it should be rendered into
            # buffer.
            self.cnvs.request_drawing_update()
        else:
            WorldOverlay.on_motion(self, evt)

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
        WorldSelectOverlay.__init__(self, cnvs, colour)

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
        WorldSelectOverlay.on_left_up(self, evt)

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
        b_pos = self._normalize_rect(b_pos)
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
        b_pos = self._normalize_rect(b_pos)
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
        """ Draw the selection as a rectangle and the repetition inside of that """

        mode_cache = self.selection_mode

        if self.w_start_pos and self.w_end_pos and 0 not in self.repetition:
            if self.fill == self.FILL_POINT:
                self._draw_points(ctx)
                self.selection_mode = SEL_MODE_EDIT
            elif self.fill == self.FILL_GRID:
                self._draw_grid(ctx)
                self.selection_mode = SEL_MODE_EDIT

        WorldSelectOverlay.draw(self, ctx, shift, scale)
        self.selection_mode = mode_cache


class SpotModeOverlay(WorldOverlay, DragMixin):
    """ Render the spot mode indicator in the center of the view

    If a position is provided, the spot will be drawn there.

    If the overlay is activated, the user can use the mouse cursor to select a position

    """

    def __init__(self, cnvs, spot_va=None):
        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)

        self.colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)
        self.highlight = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)

        # Rendering attributes
        self._sect_count = 4
        self._gap = 0.15
        self._sect_width = 2.0 * math.pi / self._sect_count
        self._spot_radius = 12

        # Spot position as a percentage (x, y) where x and y [0..1]
        self.r_pos = spot_va or TupleVA((0.5, 0.5))
        self.r_pos.subscribe(self.on_spot_change)
        self.w_pos = None

    def on_spot_change(self, _):
        self._r_to_w()

    def on_size(self, evt):
        self._r_to_w()
        WorldOverlay.on_size(self, evt)

    def _w_to_r(self):
        if self.w_pos is None:
            self.r_pos.value = (0.5, 0.5)
        else:
            # Since converting to a ratio possibly involves clipping, the w_pos is also updated
            self.w_pos, self.r_pos.value = self.cnvs.convert_spot_phys_to_ratio(self.w_pos)

    def _r_to_w(self):
        try:
            phys_spot = self.cnvs.convert_spot_ratio_to_phys(self.r_pos.value)
            self.w_pos = self.cnvs.physical_to_world_pos(phys_spot)
        except (TypeError, KeyError):
            self.w_pos = None

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if self.w_pos is None:
            return

        start = -0.5 * math.pi

        r, g, b = self.highlight

        ctx.translate(*self.offset_b)
        bx, by = self.cnvs.world_to_buffer(self.w_pos)

        width = self._spot_radius / 6.0

        ctx.new_sub_path()  # to ensure it doesn't draw a line from the previous point

        for i in range(self._sect_count):
            ctx.set_line_width(width)

            ctx.set_source_rgba(0, 0, 0, 0.6)
            ctx.arc(bx + 1, by + 1,
                    self._spot_radius,
                    start + self._gap,
                    start + self._sect_width - self._gap)
            ctx.stroke()

            ctx.set_source_rgb(r, g, b)
            ctx.arc(bx, by,
                    self._spot_radius,
                    start + self._gap,
                    start + self._sect_width - self._gap)
            ctx.stroke()

            start += self._sect_width

        width = self._spot_radius / 3.5
        radius = self._spot_radius * 0.6

        ctx.set_line_width(width)

        ctx.set_source_rgba(0, 0, 0, 0.6)
        ctx.arc(bx + 1, by + 1, radius, 0, 2 * math.pi)
        ctx.stroke()

        ctx.set_source_rgb(r, g, b)
        ctx.arc(bx, by, radius, 0, 2 * math.pi)
        ctx.stroke()

    def on_left_down(self, evt):
        if self.active:
            DragMixin._on_left_down(self, evt)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active:
            DragMixin._on_left_up(self, evt)
            offset = self.cnvs.get_half_buffer_size()
            self.w_pos = self.cnvs.view_to_world(evt.GetPositionTuple(), offset)
            self._w_to_r()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active and self.left_dragging:
            offset = self.cnvs.get_half_buffer_size()
            self.w_pos = self.cnvs.view_to_world(evt.GetPositionTuple(), offset)
            self._w_to_r()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_enter(self, evt):
        if self.active:
            self.cnvs.set_default_cursor(wx.CROSS_CURSOR)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def activate(self):
        self._r_to_w()
        WorldOverlay.activate(self)

    def deactivate(self):
        self.w_pos = None
        WorldOverlay.deactivate(self)


class LineSelectOverlay(WorldSelectOverlay):
    """ Selection overlay that allows for the selection of a line in world coordinates"""

    def __init__(self, cnvs):
        WorldSelectOverlay.__init__(self, cnvs)
        self.edit_mode = EDIT_MODE_POINT

    @property
    def length(self):
        if None in (self.w_start_pos, self.w_end_pos):
            return 0
        else:
            x1, y1 = self.w_start_pos
            x2, y2 = self.w_end_pos
            return math.hypot(x2-x1, y2-y1)

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if None not in (self.w_start_pos, self.w_end_pos) and self.w_start_pos != self.w_end_pos:
            # Pixel radius of the start marker
            start_radius = 3
            arrow_size = 12

            ctx.translate(*self.offset_b)
            # Calculate buffer start and end positions
            b_pos = self.cnvs.world_to_buffer(self.w_start_pos)
            b_start = (b_pos[0] - 0.5, b_pos[1] - 0.5)
            b_pos = self.cnvs.world_to_buffer(self.w_end_pos)
            b_end = (b_pos[0] + 0.5, b_pos[1] + 0.5)
            self.update_projection(b_start, b_end, shift + (scale,))

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
            if self.hover == gui.HOVER_END and not self.dragging:
                ctx.set_source_rgba(*self.highlight)
            else:
                ctx.set_source_rgba(*self.colour)
            ctx.fill()

            self._debug_draw_edges(ctx, True)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover in (gui.HOVER_START, gui.HOVER_END, gui.HOVER_LINE):
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
                else:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_PENCIL)
            else:
                self._view_to_world()

            # Fixme: Find a way to render the selection at the full frame rate. Right now it's not
            # possible, because we are drawing directly into the buffer, which might render slowly
            # anyway. What we would want, is that a world overlay that can visually change when the
            # mouse moves, draws into the view. After the motion is done, it should be rendered into
            # buffer.
            self.cnvs.request_drawing_update()
        else:
            WorldSelectOverlay.on_motion(self, evt)


class SpectrumLineSelectOverlay(LineSelectOverlay, PixelDataMixin):
    """
    Selection overlay that allows for the selection of a line in world coordinates
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
        """
        self.clear_selection()
        self._selected_line_va = selection_va
        self._selected_width_va = width_va
        self._selected_line_va.subscribe(self._on_selection, init=True)
        self._selected_width_va.subscribe(self._on_width, init=False)
        self._selected_pixel_va = pixel_va

    def _on_selection(self, selected_line):
        """ Event handler that requests a redraw when the selected line changes """

        if selected_line and self.active:
            self.start_pixel, self.end_pixel = selected_line

            if (None, None) not in selected_line:
                v_pos = self.data_pixel_to_view(self.start_pixel)
                self.drag_v_start_pos = self.select_v_start_pos = Vec(v_pos)

                v_pos = self.data_pixel_to_view(self.end_pixel)
                self.drag_v_end_pos = self.select_v_end_pos = Vec(v_pos)

                self._view_to_world()

            wx.CallAfter(self.cnvs.update_drawing)

    def _on_width(self, _):
        if self.active:
            wx.CallAfter(self.cnvs.update_drawing)

    def selection_points(self, point):
        """ Calculate the surrounding points around the given point according to the selection width
        """

        if point is None or None in point:
            return []

        if self._selected_width_va.value == 1:
            return [point]

        x, y = point
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
        if None in (self.w_start_pos, self.w_end_pos) or self.w_start_pos == self.w_end_pos:
            return

        if (None, None) in (self.start_pixel, self.end_pixel):
            return

        points = rasterize_line(self.start_pixel, self.end_pixel, self._selected_width_va.value)
        # Clip points
        w, h = self._data_resolution
        points = [p for p in points if 0 <= p[0] < w and 0 <= p[1] < h]

        selected_pixel = self._selected_pixel_va.value if self._selected_pixel_va else None
        selected_pixels = self.selection_points(selected_pixel)

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

        if self.active:
            v_pos = evt.GetPositionTuple()
            if self.is_over_pixel_data(v_pos):
                LineSelectOverlay.on_left_down(self, evt)
                self._snap_to_pixel()
        else:
            LineSelectOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ Stop drawing a selection line if the overlay is active """

        if self.active:
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

            self._selected_line_va.value = (self.start_pixel, self.end_pixel)
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

        if self.active:
            v_pos = evt.GetPositionTuple()
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


class PixelSelectOverlay(WorldOverlay, PixelDataMixin, DragMixin):
    """ Selection overlay that allows the selection of a pixel in a data set """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)
        PixelDataMixin.__init__(self)
        DragMixin.__init__(self)

        self._selected_pixel_va = None
        self._selected_width_va = None

        self.colour = conversion.hex_to_frgba(gui.SELECTION_COLOUR, 0.5)
        self.select_color = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, 0.5)

    def connect_selection(self, selection_va, width_va):
        self._selected_pixel_va = selection_va
        self._selected_width_va = width_va
        self._selected_pixel_va.subscribe(self._on_selection, init=True)
        self._selected_width_va.subscribe(self._on_width, init=False)

    def _on_selection(self, _):
        """ Event handler that requests a redraw when the selected line changes """
        wx.CallAfter(self.cnvs.update_drawing)

    def _on_width(self, _):
        wx.CallAfter(self.cnvs.update_drawing)

    def deactivate(self):
        """ Clear the hover pixel when the overlay is deactivated """
        self._pixel_pos = None
        WorldOverlay.deactivate(self)
        wx.CallAfter(self.cnvs.update_drawing)

    # Event handlers

    def on_leave(self, evt):

        if self.active:
            self._pixel_pos = None
            wx.CallAfter(self.cnvs.update_drawing)

        WorldOverlay.on_leave(self, evt)

    def on_motion(self, evt):
        """ Update the current mouse position """

        if self.active:
            v_pos = evt.GetPositionTuple()
            PixelDataMixin._on_motion(self, evt)
            DragMixin._on_motion(self, evt)

            if self.data_properties_are_set and self.is_over_pixel_data(v_pos):
                self.cnvs.set_dynamic_cursor(wx.CROSS_CURSOR)

                # Cache the current data pixel position
                old_pixel_pos = self._pixel_pos
                self._pixel_pos = self.view_to_data_pixel(evt.GetPositionTuple())

                if self._pixel_pos != old_pixel_pos:
                    if self.is_over_pixel_data() and self.left_dragging:
                        self._selected_pixel_va.value = self._pixel_pos
                        logging.debug("Pixel %s selected", self._selected_pixel_va.value)
                    self.cnvs.update_drawing()
            else:
                self.cnvs.reset_dynamic_cursor()
        else:
            WorldOverlay.on_motion(self, evt)

    def on_left_down(self, evt):
        if self.active:
            if self.data_properties_are_set:
                DragMixin._on_left_down(self, evt)

        WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ Set the selected pixel, if a pixel position is known """

        if self.active:
            if self._pixel_pos and self.is_over_pixel_data():
                if self._selected_pixel_va.value != self._pixel_pos:
                    self._selected_pixel_va.value = self._pixel_pos
                    self.cnvs.update_drawing()
                    logging.debug("Pixel %s selected", self._selected_pixel_va.value)
            DragMixin._on_left_up(self, evt)

        WorldOverlay.on_left_up(self, evt)

    # END Event handlers

    def selection_points(self, point):
        """ Calculate the surounding points around the given point according to the selection width

        TODO: Duplicate code from SpectrumLineOverlay, so...

        """

        if None in point:
            return []

        if self._selected_width_va.value == 1:
            return [point]

        x, y = point
        radius = self._selected_width_va.value / 2
        w, h = self._data_resolution
        points = []

        for px in range(max(0, int(x - radius)), min(int(x + radius) + 1, w)):
            for py in range(max(0, int(y - radius)), min(int(y + radius) + 1, h)):
                if math.hypot(x - px, y - py) <= radius:
                    points.append((px, py))

        return points

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        # If a selection VA is assigned...
        if self._selected_pixel_va:
            if (
                self._pixel_pos and
                self._selected_pixel_va.value != self._pixel_pos and
                self.is_over_pixel_data()
            ):

                for point in self.selection_points(self._pixel_pos):
                    rect = self.pixel_to_rect(point, scale)

                    ctx.set_source_rgba(*self.colour)
                    ctx.rectangle(*rect)
                    ctx.fill()

            if self._selected_pixel_va.value not in (None, (None, None)):

                for point in self.selection_points(self._selected_pixel_va.value):
                    rect = self.pixel_to_rect(point, scale)

                    ctx.set_source_rgba(*self.select_color)
                    ctx.rectangle(*rect)
                    ctx.fill()


class PointsOverlay(WorldOverlay):
    """ Overlay showing the available points and allowing the selection of one of them """

    MAX_DOT_RADIUS = 25.5
    MIN_DOT_RADIUS = 3.5

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)

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

        WorldOverlay.on_left_up(self, evt)

    def on_wheel(self, evt):
        """ Clear the hover when the canvas is zooming """
        if self.active:
            self.cursor_over_point = None
            self.b_hover_box = None

        WorldOverlay.on_wheel(self, evt)

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

        WorldOverlay.on_motion(self, evt)

    def _calc_choices(self):
        """ Create a mapping between world coordinates and physical points

        The minimum physical distance between points is also calculated
        """

        logging.debug("Calculating choices as buffer positions")

        self.choices = {}

        # Translate physical to buffer coordinates
        physical_points = [c for c in self.point.choices if None not in c]

        if len(physical_points) > 1:
            for p_point in physical_points:
                w_x, w_y = self.cnvs.physical_to_world_pos(p_point)
                self.choices[(w_x, w_y)] = p_point

            # normally all the points are uniformly distributed, so just need to
            # look at the distance from the first point
            p0 = physical_points[0]

            def distance(p):
                return math.hypot(p[0] - p0[0], p[1] - p0[1])

            min_dist = min(distance(p) for p in physical_points[1:])
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

            ctx.new_sub_path()
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

            # Draw hit boxes (for debugging purposes)
            # ctx.set_line_width(1)
            # ctx.set_source_rgb(1.0, 1.0, 1.0)
            # ctx.rectangle(b_x - self.dot_size * 0.95,
            #               b_y - self.dot_size * 0.95,
            #               self.dot_size * 1.9,
            #               self.dot_size * 1.9)
            # ctx.stroke()

        self.cursor_over_point = w_cursor_over


class MirrorArcOverlay(WorldOverlay, DragMixin):
    """ Overlay showing a mirror arc that the user can position over a mirror camera feed """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)

        self.colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)

        # Values are derived from technical drawing, in mm

        # The mirror is cut horizontally just above the symmetry line
        self.symmetry_offset_y = 0.5e-3
        # The radius of the circle shaped edge facing the detector
        self.parabole_cut_radius = 11.5e-3

        self.mirror_height = self.parabole_cut_radius - self.symmetry_offset_y

        # The focus distance of the parabola (i.e. the origin)
        focus_x = 2.5e-3
        # The distance from the symmetry line  of the parabola to the center of the hole
        self.hole_y = (focus_x * 2)
        # The radius of the hole through which the electron beam enters
        self.hole_radius = 0.3e-3

        # The number of radians to remove from the left and right of the semi-circle
        self.rad_offset = math.atan2(self.symmetry_offset_y, self.parabole_cut_radius)

        # The world position of the hole in the mirror
        self.hole_pos_w = Vec(0, 0)

    def set_hole_position(self, hole_pos_w):
        """ Set the center of the mirror ihole n world coordinates """
        self.hole_pos_w = Vec(hole_pos_w)
        self.cnvs.update_drawing()

    def get_hole_position(self):
        return self.hole_pos_w

    def on_left_down(self, evt):
        if self.active:
            DragMixin._on_left_down(self, evt)
            self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENESW)  # = closed hand
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_enter(self, evt):
        if self.active:
            self.cnvs.set_default_cursor(wx.CURSOR_HAND)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def on_left_up(self, evt):
        if self.active:
            DragMixin._on_left_up(self, evt)
            # Convert the final delta value to world coordinates and add it to the hole position
            self.hole_pos_w += self.cnvs.buffer_to_world(self.delta_v)
            self.clear_drag()
            self.cnvs.update_drawing()
            self.cnvs.reset_dynamic_cursor()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active and self.left_dragging:
            DragMixin._on_motion(self, evt)
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        # Move the origin from the top left to the center of the buffer
        ctx.translate(*self.offset_b)

        # DEBUG Lines Buffer Center
        # ctx.set_line_width(1)
        # ctx.set_source_rgba(1.0, 0.0, 0.0, 0.5)
        #
        # ctx.move_to(0.5, -30 + 0.5)
        # ctx.line_to(0.5, 30 + 0.5)
        #
        # ctx.move_to(-30 + 0.5, 0.5)
        # ctx.line_to(30 + 0.5, 0.5)
        #
        # ctx.stroke()
        # END DEBUG Lines Buffer Center

        if self.cnvs.flip == wx.VERTICAL:
            ctx.transform(cairo.Matrix(1.0, 0.0, 0.0, -1.0))
            hole_offset = scale * (Vec(self.hole_pos_w.x, -self.hole_pos_w.y) + (0, self.hole_y))
            hole_offset += (self.delta_v.x, -self.delta_v.y)
        else:
            hole_offset = scale * (self.hole_pos_w + (0, self.hole_y))
            hole_offset += self.delta_v

        ctx.translate(*hole_offset)

        # Align the center of the Arc with the center of the buffer (The overlay itself is drawn
        # with the parabola symmetry line on y=0)

        # Calculate base line position

        base_start_w = Vec(-self.parabole_cut_radius * 1.1, -self.symmetry_offset_y)
        base_end_w = Vec(self.parabole_cut_radius * 1.1, -self.symmetry_offset_y)
        base_start_b = scale * base_start_w
        base_end_b = scale * base_end_w

        # Calculate cross line

        cross_start_w = Vec(0, -self.symmetry_offset_y + 1e-3)
        cross_end_w = Vec(0, -self.symmetry_offset_y - 1e-3)
        cross_start_b = scale * cross_start_w
        cross_end_b = scale * cross_end_w

        # Calculate Mirror Arc

        mirror_radius_b = scale * self.parabole_cut_radius
        arc_rads = (math.pi + self.rad_offset, 2 * math.pi - self.rad_offset)

        # Calculate mirror hole

        hole_radius_b = self.hole_radius * scale
        hole_pos_b = Vec(0, -scale * self.hole_y)

        for lw, colour in [(4, (0.0, 0.0, 0.0, 0.5)), (2, self.colour)]:
            ctx.set_line_width(lw)
            ctx.set_source_rgba(*colour)

            # Draw base line

            ctx.move_to(*base_start_b)
            ctx.line_to(*base_end_b)
            ctx.stroke()

            # Draw cross line
            ctx.move_to(*cross_start_b)
            ctx.line_to(*cross_end_b)
            ctx.stroke()

            # Draw mirror arc

            ctx.arc(0, 0, mirror_radius_b, *arc_rads)
            ctx.stroke()

            # Draw mirror hole

            ctx.arc(hole_pos_b.x, hole_pos_b.y, hole_radius_b, 0, 2 * math.pi)
            ctx.stroke()

        # DEBUG Lines Mirror Center
        # ctx.set_line_width(1)
        # ctx.set_source_rgba(0.0, 1.0, 0.0, 0.5)
        #
        # ctx.move_to(0.5, -self.symmetry_offset_y * scale + 0.5)
        # ctx.line_to(0.5, -self.parabole_cut_radius * scale + 0.5)
        #
        # ctx.move_to(-hole_radius_b * 2 + 0.5, hole_pos_b.y + 0.5)
        # ctx.line_to(hole_radius_b * 2 + 0.5, hole_pos_b.y + 0.5)
        # ctx.stroke()
        # END DEBUG Lines Mirror Center
