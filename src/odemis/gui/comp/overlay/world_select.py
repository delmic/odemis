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
from typing import Optional, List, Tuple

import cairo
import odemis.gui as gui
import odemis.util.units as units
import wx
from odemis.gui.comp.overlay.base import (EDIT_MODE_BOX, SEL_MODE_CREATE,
                                          SEL_MODE_EDIT, SEL_MODE_ROTATION,
                                          SelectionMixin, Vec, WorldOverlay,
                                          RectangleEditingMixin)


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


class RectanglePointsSelectOverlay(WorldOverlay, RectangleEditingMixin):
    """
    A class for creating a rectangular selection overlay based on points.

    This overlay allows defining a rectangular selection by clicking and dragging on the
    canvas. The selected rectangle can be manipulated by dragging its edges or rotating it.

    """
    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        WorldOverlay.__init__(self, cnvs)
        RectangleEditingMixin.__init__(self, colour, center)

        self.p_point1 = None
        self.p_point2 = None
        self.p_point3 = None
        self.p_point4 = None

        # Labels for the bottom and right side length of the rectangle
        # Call draw_side_labels to use them
        self._side1_label = self.add_label("", align=wx.ALIGN_RIGHT)
        self._side2_label = self.add_label("", align=wx.ALIGN_RIGHT)
        # Label for the rotation angle of the rectangle
        # Call draw_rotation_label to use it
        self._rotation_label = self.add_label("", align=wx.ALIGN_CENTRE_HORIZONTAL)

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
        """ Start drag action if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            RectangleEditingMixin._on_left_down(self, evt)
            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ End drag action if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            RectangleEditingMixin._on_left_up(self, evt)
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
        else:
            WorldOverlay.on_motion(self, evt)

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
            if (self.selection_mode in (SEL_MODE_EDIT, SEL_MODE_CREATE) and
                    self.cnvs.view):
                self.draw_side_labels(ctx, b_point1, b_point2, b_point3, b_point4)

            # Draw the rotation label
            if self.selection_mode == SEL_MODE_ROTATION:
                self.draw_rotation_label(ctx)

    # END Event Handlers
