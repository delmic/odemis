# -*- coding: utf-8 -*-

"""
Created on 2013-03-28

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
import math
from abc import ABCMeta, abstractmethod

import cairo
import wx

import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
from odemis.gui.util.units import readable_str
from odemis.gui.util.conversion import hex_to_rgba, change_brightness

class Overlay(object):
    __metaclass__ = ABCMeta

    def __init__(self, base, label=None):
        """
        :param base: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        """

        self.base = base
        self.label = label

    def set_label(self, label):
        self.label = unicode(label)

    def write_label(self, ctx, vpos, label, flip=True):
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx.select_font_face(
                font.GetFaceName(),
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_NORMAL
        )
        ctx.set_font_size(font.GetPointSize())

        margin_x = 10

        _, _, width, height, _, _ = ctx.text_extents(label)
        x, y = vpos

        if flip:
            if x + width + margin_x > self.base.ClientSize.x:
                x = self.base.ClientSize[0] - width - margin_x

            if y + height + margin_x > self.base.ClientSize.y:
                y = self.base.ClientSize[1] - height
            elif y < height:
                y = height

        #t = font.GetPixelSize()
        ctx.set_source_rgba(0.0, 0.0, 0.0, 0.7)
        ctx.move_to(x, y)
        ctx.show_text(label)

        ctx.set_source_rgb(1.0, 1.0, 1.0)
        ctx.move_to(x + 1, y + 1)
        ctx.show_text(label)

    def _clip_viewport_pos(self, pos):
        """ Return the given pos, clipped by the base's viewport """

        pos.x = max(1, min(pos.x, self.base.ClientSize.x - 1))
        pos.y = max(1, min(pos.y, self.base.ClientSize.y - 1))

        return pos

    @abstractmethod
    def Draw(self, dc, shift=(0, 0), scale=1.0):
        pass

class ViewOverlay(Overlay):
    """ This class displays an overlay on the view port """
    pass

class WorldOverlay(Overlay):
    """ This class displays an overlay on the buffer """
    pass

class TextViewOverlay(ViewOverlay):
    """ This overlay draws the label text at the provided view position """
    def __init__(self, base, vpos=((10, 16))):
        super(TextViewOverlay, self).__init__(base)
        self.label = ""
        self.vpos = vpos

    def Draw(self, dc, shift=(0, 0), scale=1.0):
        if self.label:
            ctx = wx.lib.wxcairo.ContextFromDC(dc)
            self.write_label(ctx, self.vpos, self.label)

class CrossHairOverlay(ViewOverlay):
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
        tl = (self.center[0] - self.size,
              self.center[1] - self.size)
        br = (self.center[0] + self.size,
              self.center[1] + self.size)
        tl_s = canvas.world_to_buffer_pos(tl, shift, scale)
        br_s = canvas.world_to_buffer_pos(br, shift, scale)
        center = canvas.world_to_buffer_pos(self.center, shift, scale)

        # Draw black contrast cross first
        pen = wx.Pen(wx.BLACK)
        dc.SetPen(pen)
        dc.DrawLine(tl_s[0] + 1, center[1] + 1, br_s[0] + 1, center[1] + 1)
        dc.DrawLine(center[0] + 1, tl_s[1] + 1, center[0] + 1, br_s[1] + 1)

        dc.SetPen(self.pen)
        dc.DrawLine(tl_s[0], center[1], br_s[0], center[1])
        dc.DrawLine(center[0], tl_s[1], center[0], br_s[1])

class SelectionMixin(object):
    """ This Overlay class can be used to draw rectangular selection areas.
    These areas are always expressed in view port coordinates.
    Conversions to buffer and world coordinates should be done using subclasses.
    """

    hover_margin = 10 #px


    def __init__(self, sel_cur=None, color=gui.SELECTION_COLOR, center=(0, 0)):
        # The start and end points of the selection rectangle in view port
        # coordinates
        self.v_start_pos = None
        self.v_end_pos = None

        # The view port coordinates where a drag/edit originated
        self.edit_start_pos = None
        # What edge is being edited
        self.edit_edge = None

        self.dragging = False
        self.edit = False

        # This attribute can be used to see if the base has shifted or scaled
        self._last_shiftscale = None

        self.edges = {}

        self.color = hex_to_rgba(color)
        self.center = center

        self.scale = 1.0

    ##### selection methods  #####

    def start_selection(self, start_pos, scale):
        """ Start a new selection.

        :param start_pos: (wx.Point) Pixel coordinates where the selection
            starts
        """

        logging.debug("Starting selection at %s", start_pos)

        self.dragging = True
        self.scale = scale
        self.v_start_pos = self.v_end_pos = start_pos


    def update_selection(self, current_pos):
        """ Update the selection to reflect the given mouse position.

        :param current_pos: (wx.Point) Pixel coordinates of the current end
            point
        """

        #logging.debug("Updating selection to %s", current_pos)

        current_pos = self._clip_viewport_pos(current_pos)
        self.v_end_pos = current_pos

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

        self.v_start_pos = None
        self.v_end_pos = None

        self.edges = {}


    ##### END selection methods  #####


    ##### edit methods  #####

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
                self.v_start_pos.y = current_pos.y
            else:
                self.v_end_pos.y = current_pos.y
        else:
            if self.edit_edge == gui.HOVER_LEFT_EDGE:
                self.v_start_pos.x = current_pos.x
            else:
                self.v_end_pos.x = current_pos.x

    def stop_edit(self):
        """ End the selection edit """
        self.stop_selection()

    ##### END edit methods  #####


    def update_from_buffer(self, b_start_pos, b_end_pos, shiftscale):
        """ Update the view positions of the selection if the base view has
        shifted or scaled compared to the last time this method was called.
        """

        if self._last_shiftscale != shiftscale:
            logging.warn("Updating view position of selection")
            self._last_shiftscale = shiftscale

            self.v_start_pos = wx.Point(
                                    *self.base.buffer_to_view_pos(b_start_pos))
            self.v_end_pos = wx.Point(
                                    *self.base.buffer_to_view_pos(b_end_pos))
            self._calc_edges()

    def _calc_edges(self):
        """ Calculate the inner and outer edges of the selection according to
        the hover margin
        """

        logging.debug("Calculating selection edges")
        l, r = sorted([self.v_start_pos.x, self.v_end_pos.x])
        t, b = sorted([self.v_start_pos.y, self.v_end_pos.y])

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

    def is_hovering(self, vpos):  #pylint: disable=R0911
        """ Check if the given position is on/near a selection edge or inside
        the selection.

        :return: (bool) Return False if not hovering, or the type of hover
        """

        if self.edges:
            # If position outside outer box
            if not self.edges["o_l"] < vpos.x < self.edges["o_r"] or \
                not self.edges["o_t"] < vpos.y < self.edges["o_b"]:
                return False
            # If position inside inner box
            elif self.edges["i_l"] < vpos.x < self.edges["i_r"] and \
                self.edges["i_t"] < vpos.y < self.edges["i_b"]:
                logging.debug("Selection hover")
                return gui.HOVER_SELECTION
            elif vpos.x < self.edges["i_l"]:
                logging.debug("Left edge hover")
                return gui.HOVER_LEFT_EDGE
            elif vpos.x > self.edges["i_r"]:
                logging.debug("Right edge hover")
                return gui.HOVER_RIGHT_EDGE
            elif vpos.y < self.edges["i_t"]:
                logging.debug("Top edge hover")
                return gui.HOVER_TOP_EDGE
            elif vpos.y > self.edges["i_b"]:
                logging.debug("Bottom edge hover")
                return gui.HOVER_BOTTOM_EDGE

        return False

    def get_width(self):
        return abs(self.v_start_pos.x - self.v_end_pos.x)

    def get_height(self):
        return abs(self.v_start_pos.y - self.v_end_pos.y)

    def get_size(self):
        return (self.get_width(), self.get_height())

    def contains_selection(self):
        return None not in (self.v_start_pos, self.v_end_pos)

class ViewSelectOverlay(ViewOverlay, SelectionMixin):

    def __init__(self, base, label,
                 sel_cur=None,
                 color=gui.SELECTION_COLOR,
                 center=(0, 0)):

        super(ViewSelectOverlay, self).__init__(base, label)
        SelectionMixin.__init__(self, sel_cur, color, center)

    def Draw(self, dc, shift=(0, 0), scale=1.0):

        if self.v_start_pos and self.v_end_pos:
            #pylint: disable=E1103
            start_pos = self.v_start_pos
            end_pos = self.v_end_pos

            # logging.debug("Drawing from %s, %s to %s. %s", start_pos[0],
            #                                                start_pos[1],
            #                                                end_pos[0],
            #                                                end_pos[1] )

            ctx = wx.lib.wxcairo.ContextFromDC(dc)

            rect = (start_pos[0] + 0.5,
                    start_pos[1] + 0.5,
                    end_pos[0] - start_pos[0],
                    end_pos[1] - start_pos[1])

            # draws a light black background for the rectangle
            ctx.set_line_width(2)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.rectangle(*rect)
            ctx.stroke()

            # draws the dotted line
            ctx.set_line_width(1.5)
            ctx.set_dash([2,])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.color)
            ctx.rectangle(*rect)
            ctx.stroke()

            if self.dragging or True:
                msg = "{}: {} to {}, {} to {} unscaled".format(
                                            self.label,
                                            self.v_start_pos,
                                            self.v_end_pos,
                                            start_pos,
                                            end_pos)
                self.write_label(ctx, (10, 10), msg)

class WorldSelectOverlay(WorldOverlay, SelectionMixin):

    def __init__(self, base, label,
                 sel_cur=None,
                 color=gui.SELECTION_COLOR,
                 center=(0, 0)):

        super(WorldSelectOverlay, self).__init__(base, label)
        SelectionMixin.__init__(self, sel_cur, color, center)

        self.w_start_pos = None
        self.w_end_pos = None

    # Selection creation

    def start_selection(self, start_pos, scale):
        SelectionMixin.start_selection(self, start_pos, scale)
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

    # Selection clearing

    def clear_selection(self):
        SelectionMixin.clear_selection(self)
        self.w_start_pos = None
        self.w_end_pos = None

    def _center_view_origin(self, vpos):
        #view_size = self.base._bmp_buffer_size
        view_size = self.base.GetSize()
        return (vpos[0] - (view_size[0] / 2),
                vpos[1] - (view_size[1] / 2))

    def _calc_world_pos(self):
        """ Update the world position to reflect the view position
        """
        if self.v_start_pos and self.v_end_pos:
            offset = tuple(v / 2 for v in self.base._bmp_buffer_size)
            self.w_start_pos = self.base.view_to_world_pos(
                                            self.v_start_pos,
                                            offset)
            self.w_end_pos = self.base.view_to_world_pos(
                                            self.v_end_pos,
                                            offset)
    def _calc_view_pos(self):
        """ Update the view position to reflect the world position
        """
        if not self.w_start_pos or not self.w_end_pos:
            logging.warning("Asking to convert non-existing world positions")
            return
        offset = tuple(v / 2 for v in self.base._bmp_buffer_size)
        v_start = self.base.world_to_view_pos(self.w_start_pos, offset)
        self.v_start_pos = wx.Point(*v_start)
        v_end = self.base.world_to_view_pos(self.w_end_pos, offset)
        self.v_end_pos = wx.Point(*v_end)
        self._calc_edges() # TODO move to Mixin??

    def get_physical_sel(self):
        """
        return (tuple of 4 floats): position in m
        """
        if self.w_start_pos and self.w_end_pos:
            return (self.base.world_to_real_pos(self.w_start_pos) +
                    self.base.world_to_real_pos(self.w_end_pos))
        else:
            return None

    def set_physical_sel(self, rect):
        """
        rect (tuple of 4 floats): t, l, b, r positions in m
        """
        if rect is None:
            self.clear_selection()
        else:
            self.w_start_pos = self.base.real_to_world_pos(rect[:2])
            self.w_end_pos = self.base.real_to_world_pos(rect[2:4])
            self._calc_view_pos()

    def Draw(self, dc_buffer, shift=(0, 0), scale=1.0):

        if self.w_start_pos and self.w_end_pos:
            offset = tuple(v / 2 for v in self.base._bmp_buffer_size)
            b_start_pos = self.base.world_to_buffer_pos(
                                        self.w_start_pos,
                                        offset)

            b_end_pos = self.base.world_to_buffer_pos(
                                        self.w_end_pos,
                                        offset)

            # logging.debug("Drawing from %s, %s to %s. %s", b_start_pos[0],
            #                                                b_start_pos[1],
            #                                                b_end_pos[0],
            #                                                b_end_pos[1] )

            self.update_from_buffer(b_start_pos, b_end_pos, shift + (scale,))

            ctx = wx.lib.wxcairo.ContextFromDC(dc_buffer)


            #logging.warn("%s %s", shift, world_to_buffer_pos(shift))
            rect = (b_start_pos[0] + 0.5,
                    b_start_pos[1] + 0.5,
                    b_end_pos[0] - b_start_pos[0],
                    b_end_pos[1] - b_start_pos[1])

            # draws a light black background for the rectangle
            ctx.set_line_width(2.5)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.rectangle(*rect)
            ctx.stroke()

            # draws the dotted line
            ctx.set_line_width(2)
            ctx.set_dash([3,])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.color)
            ctx.rectangle(*rect)
            ctx.stroke()

            # Label
            if self.dragging or self.edit:
                # No need for size label
                if not self.base.microscope_view:
                    return

                w, h = self.base.selection_to_real_size(
                                            self.w_start_pos,
                                            self.w_end_pos
                )

                w = readable_str(w, 'm', sig=2)
                h = readable_str(h, 'm', sig=2)
                size_lbl = u"{} x {}".format(w, h)

                pos = (b_end_pos[0] + 5, b_end_pos[1] - 5)
                self.write_label(ctx, pos, size_lbl)

class FocusLineOverlay(ViewOverlay):
    """ This class describes an overlay that draws a vertical line over a
    canvas, showing a marker at the vertical value and a possible label """

    def __init__(self, base,
                 label="",
                 sel_cur=None,
                 color=gui.SELECTION_COLOR,
                 center=(0, 0)):

        super(FocusLineOverlay, self).__init__(base, label)
        self.color = hex_to_rgba(color)
        self.vposx = None
        self.vposy = None

        self.line_width = 2

    def set_position(self, pos):
        self.vposx = max(1, min(pos[0], self.base.ClientSize.x - self.line_width))
        self.vposy = max(1, min(pos[1], self.base.ClientSize.y - 1))

    def Draw(self, dc_buffer, shift=(0, 0), scale=1.0):
        ctx = wx.lib.wxcairo.ContextFromDC(dc_buffer)

        if self.vposy:
            r, g, b, a = change_brightness(self.color, -0.1)
            a = 0.5
            ctx.set_source_rgba(r, g, b, a)
            ctx.arc(self.vposx, self.vposy, 5.5, 0, 2*math.pi)
            ctx.fill()

        if self.vposx:
            # draws the dotted line
            ctx.set_line_width(self.line_width)
            ctx.set_dash([3,])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.color)
            ctx.move_to(self.vposx, 0)
            ctx.line_to(self.vposx, self.base.ClientSize[1])
            ctx.stroke()

            if self.label:
                vpos = (self.vposx + 5, self.vposy + 3)
                self.write_label(ctx, vpos, self.label)
