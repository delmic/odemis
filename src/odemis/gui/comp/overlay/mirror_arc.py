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
from odemis import model
from odemis.gui.comp.overlay.base import DragMixin, Vec, WorldOverlay


class MirrorArcOverlay(WorldOverlay, DragMixin):
    """ Overlay showing a mirror arc that the user can position over a mirror camera feed """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)

        self.colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)

        # The phys position of the hole in the mirror (starts with a non-used VA)
        self.hole_pos_va = model.TupleContinuous((0.0, 0.0), ((-1.0, -1.0), (1.0, 1.0)))

        # Mirror arc rendering parameters
        self.flipped = False
        self.parabole_cut_radius = None
        self.cut_offset_y = None
        self.mirror_height = None
        self.rad_offset = None
        self.hole_diam = None
        self.hole_y = None

        # Default values using the standard mirror size, in m
        self.set_mirror_dimensions(2.5e-3, 13.25e-3, 0.5e-3, 0.6e-3)

    def set_mirror_dimensions(self, parabola_f, cut_x, cut_offset_y, hole_diam):
        """
        Updates the dimensions of the mirror
        parabola_f (float): focal length of the parabola in m.
         If < 0, the drawing will be flipped (ie, with the circle towards the top)
        cut_x (float): cut of the parabola from the origin
        cut_offset_y (float): Distance from the center of the circle that is cut
           horizontally in m. (Also called "focus distance")
        hole_diam (float): diameter the hole in the mirror in m
        """
        self.flipped = (cut_offset_y < 0) # focus_dist - the vertical mirror cutoff can be positive or negative
        # The mirror is cut horizontally just above the symmetry line
        self.cut_offset_y = abs(cut_offset_y)

        # The radius of the circle shaped edge facing the detector
        # We don't care about cut_x, but "cut_y"
        # Use the formula y = x²/4f
        self.parabole_cut_radius = 2 * math.sqrt(parabola_f * cut_x)

        self.mirror_height = self.parabole_cut_radius - self.cut_offset_y
        # The number of radians to remove from the left and right of the semi-circle
        self.rad_offset = math.atan2(self.cut_offset_y, self.parabole_cut_radius)

        # The radius of the hole through which the electron beam enters
        self.hole_diam = hole_diam
        # The distance from the symmetry line  of the parabola to the center of the hole
        self.hole_y = (parabola_f * 2)

        self.cnvs.request_drawing_update()

    def set_hole_position(self, hole_pos_va):
        """
        Set the VA containing the coordinates of the center of the mirror
         (in physical coordinates)
        """
        self.hole_pos_va = hole_pos_va
        self.cnvs.request_drawing_update()

    def on_left_down(self, evt):
        if self.active.value:
            DragMixin._on_left_down(self, evt)
            self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CURSOR_HAND)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            DragMixin._on_left_up(self, evt)
            # Convert the final delta value to physical coordinates and add it to the hole position
            d = self.cnvs.buffer_to_phys(self.delta_v)
            hole_pos_p = Vec(self.hole_pos_va.value) + Vec(d)
            self.hole_pos_va.value = (hole_pos_p.x, hole_pos_p.y)
            self.clear_drag()
            self.cnvs.update_drawing()
            self.cnvs.reset_dynamic_cursor()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.left_dragging:
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

        hole_pos_p = Vec(self.hole_pos_va.value)

        if (self.cnvs.flip == wx.VERTICAL) != self.flipped:  # XOR
            ctx.transform(cairo.Matrix(1.0, 0.0, 0.0, -1.0))
            hole_offset = scale * (hole_pos_p + (0, -self.hole_y))
            hole_offset += (self.delta_v.x, -self.delta_v.y)
        else:
            hole_offset = scale * (Vec(hole_pos_p.x, -hole_pos_p.y) + (0, -self.hole_y))
            hole_offset += self.delta_v

        ctx.translate(*hole_offset)

        # Align the center of the Arc with the center of the buffer (The overlay itself is drawn
        # with the parabola symmetry line on y=0)

        # Calculate base line position
        base_start_w = Vec(-self.parabole_cut_radius * 1.1, self.cut_offset_y)
        base_end_w = Vec(self.parabole_cut_radius * 1.1, self.cut_offset_y)
        base_start_b = scale * base_start_w
        base_end_b = scale * base_end_w

        # Calculate cross line
        cross_start_w = Vec(0, self.cut_offset_y + 1e-3)
        cross_end_w = Vec(0, self.cut_offset_y - 1e-3)
        cross_start_b = scale * cross_start_w
        cross_end_b = scale * cross_end_w

        # Calculate Mirror Arc
        mirror_radius_b = scale * self.parabole_cut_radius
        arc_rads = (2 * math.pi + self.rad_offset, math.pi - self.rad_offset)

        # Calculate mirror hole
        hole_radius_b = (self.hole_diam / 2) * scale
        hole_pos_b = Vec(0, scale * self.hole_y)

        # Do it twice: once the shadow, then the real image
        for lw, colour in ((4, (0.0, 0.0, 0.0, 0.5)), (2, self.colour)):
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
        # ctx.move_to(0, self.cut_offset_y * scale)
        # ctx.line_to(0, self.parabole_cut_radius * scale)
        #
        # ctx.move_to(-hole_radius_b * 2, hole_pos_b.y)
        # ctx.line_to(hole_radius_b * 2, hole_pos_b.y)
        # ctx.stroke()
        # END DEBUG Lines Mirror Center
