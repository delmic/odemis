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

import logging
import math

import odemis.gui as gui
import odemis.util.conversion as conversion
import wx
from odemis.gui.comp.overlay.base import WorldOverlay


class PointsOverlay(WorldOverlay):
    """ Overlay showing the available points and allowing the selection of one of them """

    MAX_DOT_RADIUS = 25.5
    MIN_DOT_RADIUS = 3.5

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)

        # A VA tracking the selected point
        self.point = None
        # The possible choices for point as a physical coordinates
        self.choices = set()

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
        """
        Set the available points and connect to the given point VA
        point_va (VA of tuple of float, or None)
        """
        # Connect the provided VA to the overlay
        self.point = point_va
        if self.point:
            self.point.subscribe(self._on_point_selected)
            self._calc_choices()
            self.cnvs.view.mpp.subscribe(self._on_mpp, init=True)
        else:
            self.cnvs.view.mpp.unsubscribe(self._on_mpp)

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
        if self.active.value:
            # Clear the hover when the canvas was dragged
            if self.cursor_over_point and not self.cnvs.was_dragged:
                self.point.value = self.cursor_over_point
                logging.debug("Point %s selected", self.point.value)
                self.cnvs.update_drawing()
            elif self.cnvs.was_dragged:
                self.cursor_over_point = None
                self.b_hover_box = None

        WorldOverlay.on_left_up(self, evt)

    def on_wheel(self, evt):
        """ Clear the hover when the canvas is zooming """
        if self.active.value:
            self.cursor_over_point = None
            self.b_hover_box = None

        WorldOverlay.on_wheel(self, evt)

    def on_motion(self, evt):
        """ Detect when the cursor hovers over a dot """
        if self.active.value:
            if not self.cnvs.left_dragging and self.choices:
                v_x, v_y = evt.Position
                b_x, b_y = self.cnvs.view_to_buffer((v_x, v_y))
                offset = self.cnvs.get_half_buffer_size()

                b_hover_box = None

                for p_pos in self.choices:
                    b_box_x, b_box_y = self.cnvs.phys_to_buffer(p_pos, offset)

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
        """ Prepares the choices and compute the minimum physical distance
         between points
        """
        choices = [c for c in self.point.choices if None not in c]
        if len(choices) > 1:
            # normally all the points are uniformly distributed, so just need to
            # look at the distance from the first point
            p0 = choices[0]

            def distance(p):
                return math.hypot(p[0] - p0[0], p[1] - p0[1])

            min_dist = min(distance(p) for p in choices[1:])
        else:
            # can't compute the distance => pick something typical
            min_dist = 100e-9  # m

        self.choices = frozenset(choices)
        self.min_dist = min_dist / 2  # radius

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if not self.choices or not self.active.value:
            return

        if self.b_hover_box:
            b_l, b_t, b_r, b_b = self.b_hover_box

        p_cursor_over = None
        offset = self.cnvs.get_half_buffer_size()

        for p_pos in self.choices:
            b_x, b_y = self.cnvs.phys_to_buffer(p_pos, offset)

            ctx.new_sub_path()
            ctx.arc(b_x, b_y, self.dot_size, 0, 2 * math.pi)

            # If the mouse is hovering over a dot (and we are not dragging)
            if (self.b_hover_box and (b_l <= b_x <= b_r and b_t <= b_y <= b_b) and
                    not self.cnvs.was_dragged):
                p_cursor_over = p_pos
                ctx.set_source_rgba(*self.select_colour)
            elif self.point.value == p_pos:
                ctx.set_source_rgba(*self.select_colour)
            else:
                ctx.set_source_rgba(*self.dot_colour)

            ctx.fill()

            ctx.arc(b_x, b_y, 2.0, 0, 2 * math.pi)
            ctx.set_source_rgb(0.0, 0.0, 0.0)
            ctx.fill()

            ctx.arc(b_x, b_y, 1.5, 0, 2 * math.pi)
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

        self.cursor_over_point = p_cursor_over
