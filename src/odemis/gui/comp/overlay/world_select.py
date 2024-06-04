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
import cairo
import wx

import odemis.gui as gui
import odemis.util.units as units
from odemis.gui.comp.overlay.base import (EDIT_MODE_BOX, SEL_MODE_CREATE,
                                          SEL_MODE_EDIT, SelectionMixin,
                                          Vec, WorldOverlay)


class WorldSelectOverlay(WorldOverlay, SelectionMixin):

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        WorldOverlay.__init__(self, cnvs)
        SelectionMixin.__init__(self, colour, center, EDIT_MODE_BOX)

        self._p_start_pos = None
        self._p_end_pos = None

        self.position_label = self.add_label("", colour=(0.8, 0.8, 0.8), align=wx.ALIGN_RIGHT)

    @property
    def p_start_pos(self):
        return self._p_start_pos

    @p_start_pos.setter
    def p_start_pos(self, p_pos):
        self._p_start_pos = p_pos
        self._phys_to_view()

    @property
    def p_end_pos(self):
        return self._p_end_pos

    @p_end_pos.setter
    def p_end_pos(self, p_pos):
        self._p_end_pos = p_pos
        self._phys_to_view()

    # Selection clearing

    def clear_selection(self):
        """ Clear the current selection """
        SelectionMixin.clear_selection(self)
        self.p_start_pos = None
        self.p_end_pos = None

    def _view_to_phys(self):
        """ Update the physical position to reflect the view position """

        if self.select_v_start_pos and self.select_v_end_pos:
            offset = self.cnvs.get_half_buffer_size()
            psp = self.cnvs.view_to_phys(self.select_v_start_pos, offset)
            pep = self.cnvs.view_to_phys(self.select_v_end_pos, offset)
            self._p_start_pos = psp
            self._p_end_pos = pep

    def _phys_to_view(self):
        """ Update the view position to reflect the physical position """

        if self.p_start_pos and self.p_end_pos:
            offset = self.cnvs.get_half_buffer_size()
            vsp = self.cnvs.phys_to_view(self.p_start_pos, offset)
            vep = self.cnvs.phys_to_view(self.p_end_pos, offset)
            self.select_v_start_pos, self.select_v_end_pos = self._normalize_rect(vsp, vep)
            self._calc_edges()

    def get_physical_sel(self):
        """ Return the selected rectangle in physical coordinates

        :return: (tuple of 4 floats) Position in m

        """

        if self.p_start_pos and self.p_end_pos:
            p_pos = self.p_start_pos + self.p_end_pos
            return self._normalize_rect(p_pos)
        else:
            return None

    def set_physical_sel(self, rect):
        """ Set the selection using the provided physical coordinates

        rect (tuple of 4 floats): l, t, r, b positions in m

        """

        if rect is None:
            self.clear_selection()
        else:
            self.p_start_pos = rect[:2]
            self.p_end_pos = rect[2:4]

    def draw(self, ctx, shift=(0, 0), scale=1.0, line_width=4, dash=True):
        """ Draw the selection as a rectangle """

        if self.p_start_pos and self.p_end_pos:

            # FIXME: The following version of the code does not work. Update_projection is causing
            # the start position to be drawn at the top left of the buffer and the calculation of
            # the edges is all wrong.

            # translate the origin to the middle of the buffer
            # ctx.translate(*self.offset_b)
            #
            # # Important: We need to use the physical positions, in order to draw everything at the
            # # right scale.
            # b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos)
            # b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos)
            # b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)

            # Important: We need to use the physical positions, in order to draw
            # everything at the right scale.
            offset = self.cnvs.get_half_buffer_size()
            b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)

            self.update_projection(b_start_pos, b_end_pos, (shift[0], shift[1], scale))

            # logging.warning("%s %s", shift, phys_to_buffer_pos(shift))
            rect = (b_start_pos.x,
                    b_start_pos.y,
                    b_end_pos.x - b_start_pos.x,
                    b_end_pos.y - b_start_pos.y)

            # draws a light black background for the rectangle
            ctx.set_line_width(line_width)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.rectangle(*rect)
            ctx.stroke()

            # draws the dotted line
            ctx.set_line_width(line_width)
            if dash:
                ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            ctx.rectangle(*rect)
            ctx.stroke()

            self._debug_draw_edges(ctx, True)

            # Label
            if (self.selection_mode in (SEL_MODE_EDIT, SEL_MODE_CREATE) and
                    self.cnvs.view):
                w, h = (abs(s - e) for s, e in zip(self.p_start_pos, self.p_end_pos))
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
        if self.active.value:
            SelectionMixin._on_left_down(self, evt)
            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ End drag action if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            SelectionMixin._on_left_up(self, evt)
            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

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

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                elif self.hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZEWE)
                elif self.hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENS)
                elif self.hover:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
                else:
                    self.cnvs.reset_dynamic_cursor()
            else:
                self._view_to_phys()

            # TODO: Find a way to render the selection at the full frame rate. Right now it's not
            # possible, because we are drawing directly into the buffer, which might render slowly
            # anyway. What we would want, is that a world overlay that can visually change when the
            # mouse moves, draws into the view. After the motion is done, it should be rendered into
            # buffer.
            self.cnvs.request_drawing_update()
        else:
            WorldOverlay.on_motion(self, evt)

    # END Event Handlers
