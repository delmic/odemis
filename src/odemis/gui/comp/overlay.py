# -*- coding: utf-8 -*-

"""
Created on 2013-03-28

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging

import cairo
import wx

import odemis.gui as gui
from .canvas import WorldToBufferPoint
from ..util.conversion import hex_to_rgba

class Overlay(object):

    def __init__(self, base):
        """
        :param base: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        """

        self.base = base

    def _clip_viewport_pos(self, pos):
        """ Return the given pos, clipped by the base's viewport """

        pos.x = max(1, min(pos.x, self.base.ClientSize.x - 1))
        pos.y = max(1, min(pos.y, self.base.ClientSize.y - 1))

        return pos

    def view_to_buffer_pos(self, view_pos):
        margin = ((self.base._bmp_buffer_size[0] - self.base.ClientSize[0]) / 2,
                  (self.base._bmp_buffer_size[1] - self.base.ClientSize[1]) / 2)

        return (view_pos[0] + margin[0], view_pos[1] + margin[1])

class StaticOverlay(Overlay):
    pass

class RelativeOverlay(Overlay):
    pass

class CrossHairOverlay(StaticOverlay):
    def __init__(self, base,
                 color=gui.CROSSHAIR_COLOR, size=gui.CROSSHAIR_SIZE, center=(0, 0)):
        super(CrossHairOverlay, self).__init__(base)

        self.pen = wx.Pen(color)
        self.size = size
        self.center = center

    def Draw(self, dc, shift=(0, 0), scale=1.0):
        """
        Draws the crosshair
        dc (wx.DC)
        shift (2-tuple float): shift for the coordinate conversion
        scale (float): scale for the coordinate conversion
        """
        dc.SetPen(self.pen)

        tl = (self.center[0] - self.size,
              self.center[1] - self.size)
        br = (self.center[0] + self.size,
              self.center[1] + self.size)
        tl_s = WorldToBufferPoint(tl, shift, scale)
        br_s = WorldToBufferPoint(br, shift, scale)
        center = WorldToBufferPoint(self.center, shift, scale)

        dc.DrawLine(tl_s[0], center[1], br_s[0], center[1])
        dc.DrawLine(center[0], tl_s[1], center[0], br_s[1])


class SelectionMixin(object):

    def __init__(self):

        self.vp_start_pos = None
        self.vp_end_pos = None

        self.edges = {}

    def start_selection(self, start_pos, scale):
        """ Start a new selection.

        :param start_pos: (wx.Point) Pixel coordinates where the selection
            starts
        """

        logging.debug("Starting selection at %s", start_pos)

        self.dragging = True
        self.scale = scale
        self.vp_start_pos = self.vp_end_pos = start_pos


    def update_selection(self, current_pos):
        """ Update the selection to reflect the given mouse position.

        :param current_pos: (wx.Point) Pixel coordinates of the current end
            point
        """

        logging.debug("Updating selection to %s", current_pos)

        current_pos = self._scale_point(self._clip_viewport_pos(current_pos))
        self.vp_end_pos = current_pos

    def stop_selection(self):
        """ End the creation of the current selection """

        logging.debug("Stopping selection")

        if max(self.get_height(), self.get_width()) < gui.SELECTION_MINIMUM:
            logging.debug("Selection too small")
            self.clear_selection()
        else:
            self._calc_edges()
            self.dragging = False
            self.edit = False

    def clear_selection(self):
        """ Clear the selection """
        logging.debug("Clearing selections")
        self.dragging = False
        self.edit = False

        self.vp_start_pos = None
        self.vp_end_pos = None

        self.edges = {}

    def _calc_edges(self):
        """ Calculate the inner and outer edges of the selection according to
        the hover margin
        """

        l, r = sorted([self.vp_start_pos.x, self.vp_end_pos.x])
        t, b = sorted([self.vp_start_pos.y, self.vp_end_pos.y])

        i_l, o_r, i_t, o_b = [v + self.hover_margin for v in [l, r, t, b]]
        o_l, i_r, o_t, i_b = [v - self.hover_margin for v in [l, r, t, b]]

        self.edges = {
            "i_l": i_l,
            "o_r": o_r,
            "i_t": i_t,
            "o_b": o_b,
            "o_l": o_l,
            "i_r": i_r,
            "o_t": o_t,
            "i_b": i_b
        }

class SelectionOverlay(Overlay, SelectionMixin):
    """ This overlay is for the selection of a rectangular arrea.
    """

    hover_margin = 10 #px


    def __init__(self, base, label,
                 sel_cur=None, color=gui.SELECTION_COLOR, center=(0, 0)):
        super(SelectionOverlay, self).__init__(base)
        SelectionMixin.__init__(self)

        self.label = label

        self.color = hex_to_rgba(color)
        self.center = center

        self.scale = 1.0



        self.edit_start_pos = None
        self.edit_edge = None

        self.dragging = False
        self.edit = False

        # # Dictionary containing values for the inner and outer edges
        # self.edges = {}

    # Creating a new selection

    def _scale_point(self, point):
        point.x = int(point.x / self.scale)
        point.y = int(point.y / self.scale)
        return point

    def _unscale_point(self, point, scale):
        point.x = int(point.x * scale)
        point.y = int(point.y * scale)
        return point






    # Edit existing selection

    def start_edit(self, start_pos, edge):
        """ Start an edit to the current selection """
        logging.debug("Starting edit of edge %s at %s", edge, start_pos)
        self.edit_start_pos = start_pos
        self.edit_edge = edge
        self.edit = True

    def update_edit(self, current_pos):
        """ Adjust the selection according to the given position and the current
        edit action
        """
        current_pos = self._clip_viewport_pos(current_pos)

        logging.debug("Moving selection to %s", current_pos)

        if self.edit_edge in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
            if self.edit_edge == gui.HOVER_TOP_EDGE:
                self.vp_start_pos.y = current_pos.y
            else:
                self.vp_end_pos.y = current_pos.y
        else:
            if self.edit_edge == gui.HOVER_LEFT_EDGE:
                self.vp_start_pos.x = current_pos.x
            else:
                self.vp_end_pos.x = current_pos.x

    def stop_edit(self):
        """ End the selection edit """
        self.stop_selection()



    def is_hovering(self, pos):  #pylint: disable=R0911
        """ Check if the given position is on/near a selection edge or inside
        the selection.

        :return: (bool) Return False if not hovering, or the type of hover
        """

        if self.edges:
            if not self.edges["o_l"] < pos.x < self.edges["o_r"] or \
                not self.edges["o_t"] < pos.y < self.edges["o_b"]:
                return False
            elif self.edges["i_l"] < pos.x < self.edges["i_r"] and \
                self.edges["i_t"] < pos.y < self.edges["i_b"]:
                logging.debug("Selection hover")
                return gui.HOVER_SELECTION
            elif pos.x < self.edges["i_l"]:
                logging.debug("Left edge hover")
                return gui.HOVER_LEFT_EDGE
            elif pos.x > self.edges["i_r"]:
                logging.debug("Right edge hover")
                return gui.HOVER_RIGHT_EDGE
            elif pos.y < self.edges["i_t"]:
                logging.debug("Top edge hover")
                return gui.HOVER_TOP_EDGE
            elif pos.y > self.edges["i_b"]:
                logging.debug("Bottom edge hover")
                return gui.HOVER_BOTTOM_EDGE

        return False

    def get_width(self):
        return abs(self.vp_start_pos.x - self.vp_end_pos.x)

    def get_height(self):
        return abs(self.vp_start_pos.y - self.vp_end_pos.y)

    def get_size(self):
        return (self.get_width(), self.get_height())

    def Draw(self, dc, shift=(0, 0), scale=1.0):

        if self.vp_start_pos and self.vp_end_pos:
            #pylint: disable=E1103

            start_pos = self._unscale_point(self.vp_start_pos, scale)
            end_pos = self._unscale_point(self.vp_end_pos, scale)

            # logging.debug("Drawing from %s, %s to %s. %s", start_pos.x,
            #                                                start_pos.y,
            #                                                end_pos.x,
            #                                                end_pos.y )

            ctx = wx.lib.wxcairo.ContextFromDC(dc)

            ctx.set_line_width(1.5)
            ctx.set_source_rgba(0, 0, 0, 1)

            #logging.warn("%s %s", shift, WorldToBufferPoint(shift))

            #start_pos.x, start_pos.y = 100, 100
            #end_pos.x, end_pos.y = 200, 200

            rect = (start_pos.x + 0.5,
                    start_pos.y + 0.5,
                    end_pos.x - start_pos.x,
                    end_pos.y - start_pos.y)

            ctx.rectangle(*rect)

            ctx.stroke()

            ctx.set_line_width(1)
            ctx.set_dash([1.5,])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)

            ctx.set_source_rgba(*self.color)
            ctx.rectangle(*rect)

            ctx.stroke()

            if self.dragging or True:
                msg = "{}: {} to {}, {} to {} unscaled".format(
                                            self.label,
                                            self.vp_start_pos,
                                            self.vp_end_pos,
                                            start_pos,
                                            end_pos)

                ctx.select_font_face(
                    "Courier",
                    cairo.FONT_SLANT_NORMAL,
                    cairo.FONT_WEIGHT_NORMAL
                )
                ctx.set_font_size(12)

                #buf_pos = self.vp_to_buffer_pos((9, 19))

                ctx.set_source_rgb(0.0, 0.0, 0.0)
                ctx.move_to(9, 19)
                ctx.show_text(msg)
                ctx.set_source_rgb(1.0, 1.0, 1.0)
                ctx.move_to(10, 20)
                ctx.show_text(msg)

