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
import odemis.gui.comp.canvas as canvas
from odemis.gui.util.units import readable_str
from ..util.conversion import hex_to_rgba

class Overlay(object):

    def __init__(self, base, label=None):
        """
        :param base: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        """

        self.base = base
        self.label = label

    @classmethod
    def _clip(cls, tl, br, btl, bbr):
        """ Generic clipping method clipping the rectangle descibred by tuples
        tl/br by bounding box btl/bbr.

        It's important to realise that the tl and br parameters might be
        switched in the function call, so we need to sort them before clipping.
        """

        if None not in (tl, br, btl, bbr):
            # Make sure that tl is actually top left
            ttl = (min(tl[0], br[0]), min(tl[1], br[1]))
            tbr = (max(tl[0], br[0]), max(tl[1], br[1]))
            tl, br = ttl, tbr

            # logging.warn("%s %s %s %s", tl[0] >= bbr[0], br[0] <= btl[0], br[1] <= btl[1], tl[1] >= bbr[1])

            # When the selection is completely outside the bounding box
            if tl[0] >= bbr[0] or br[0] <= btl[0] or br[1] <= btl[1] or tl[1] >= bbr[1]:
                return None

            tl = (max(tl[0], btl[0]), max(tl[1], btl[1]))
            br = (min(br[0], bbr[0]), min(br[1], bbr[1]))

        return tl, br

    @classmethod
    def write_label(cls, ctx, vpos, label):
        ctx.select_font_face(
                "Courier",
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_NORMAL
        )
        ctx.set_font_size(12)

        ctx.set_source_rgb(0.0, 0.0, 0.0)
        ctx.move_to(vpos[0] + 5, vpos[1] - 10)
        ctx.show_text(label)

        ctx.set_source_rgb(1.0, 1.0, 1.0)
        ctx.move_to(vpos[0] + 6, vpos[1] - 9)
        ctx.show_text(label)

    def _clip_viewport_pos(self, pos):
        """ Return the given pos, clipped by the base's viewport """

        pos.x = max(1, min(pos.x, self.base.ClientSize.x - 1))
        pos.y = max(1, min(pos.y, self.base.ClientSize.y - 1))

        return pos


class ViewOverlay(Overlay):
    """ This class displays an overlay on the view port """
    pass

class WorldOverlay(Overlay):
    """ This class displays an overlay on the buffer """
    pass

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
        #super(SelectionOverlay, self).__init__(base)

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


    def _calc_edges(self):
        """ Calculate the inner and outer edges of the selection according to
        the hover margin
        """

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

    def is_hovering(self, pos):  #pylint: disable=R0911
        """ Check if the given position is on/near a selection edge or inside
        the selection.

        :return: (bool) Return False if not hovering, or the type of hover
        """

        if self.edges:
            # If position outside outer box
            if not self.edges["o_l"] < pos.x < self.edges["o_r"] or \
                not self.edges["o_t"] < pos.y < self.edges["o_b"]:
                return False
            # If position inside inner box
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

            # logging.debug("Drawing from %s, %s to %s. %s", start_pos.x,
            #                                                start_pos.y,
            #                                                end_pos.x,
            #                                                end_pos.y )

            ctx = wx.lib.wxcairo.ContextFromDC(dc)

            ctx.set_line_width(1.5)
            ctx.set_source_rgba(0, 0, 0, 1)

            #logging.warn("%s %s", shift, world_to_buffer_pos(shift))

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
                                            self.v_start_pos,
                                            self.v_end_pos,
                                            start_pos,
                                            end_pos)

                ctx.select_font_face(
                    "Courier",
                    cairo.FONT_SLANT_NORMAL,
                    cairo.FONT_WEIGHT_NORMAL
                )
                ctx.set_font_size(12)

                #buf_pos = self.v_to_buffer_pos((9, 19))

                ctx.set_source_rgb(0.0, 0.0, 0.0)
                ctx.move_to(9, 19)
                ctx.show_text(msg)
                ctx.set_source_rgb(1.0, 1.0, 1.0)
                ctx.move_to(10, 20)
                ctx.show_text(msg)

class WorldSelectOverlay(WorldOverlay, SelectionMixin):

    def __init__(self, base, label,
                 sel_cur=None,
                 color=gui.SELECTION_COLOR,
                 center=(0, 0)):

        super(WorldSelectOverlay, self).__init__(base, label)
        SelectionMixin.__init__(self, sel_cur, color, center)

        self.w_start_pos = None
        self.w_end_pos = None

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

        w_clipped = self._clip(
                        self.w_start_pos,
                        self.w_end_pos,
                        *self.base.world_image_area)

        if w_clipped:
            self.w_start_pos, self.w_end_pos = w_clipped


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

        if self.v_start_pos and self.v_end_pos:
            offset = tuple(v / 2 for v in self.base._bmp_buffer_size)
            self.w_start_pos = self.base.view_to_world_pos(
                                            self.v_start_pos,
                                            offset)
            self.w_end_pos = self.base.view_to_world_pos(
                                            self.v_end_pos,
                                            offset)

    def get_world_selection_pos(self):
        if self.w_start_pos and self.w_end_pos:
            return self.w_start_pos + self.w_end_pos
        else:
            return None

    def get_real_selection_size(self):
        return u"{0:0.2f}x{0:0.2f}".format(*self.w_end_pos)

    def Draw(self, dc, shift=(0, 0), scale=1.0):

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

            ctx = wx.lib.wxcairo.ContextFromDC(dc)


            ctx.set_line_width(1.5)
            ctx.set_source_rgba(0, 0, 0, 1)

            #logging.warn("%s %s", shift, world_to_buffer_pos(shift))

            rect = (b_start_pos[0] + 0.5,
                    b_start_pos[1] + 0.5,
                    b_end_pos[0] - b_start_pos[0],
                    b_end_pos[1] - b_start_pos[1])

            ctx.rectangle(*rect)

            ctx.stroke()

            ctx.set_line_width(1)
            ctx.set_dash([1.5,])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)

            ctx.set_source_rgba(*self.color)
            ctx.rectangle(*rect)

            ctx.stroke()

            # No need for size label
            if not self.base.microscope_view:
                return

            # Label
            # stream = self.base.microscope_view.stream_tree.streams[0]
            # emm = stream.emitter
            # sel = tuple([e * self.base.scale for e in self.w_end_pos])

            w = abs(self.w_start_pos[0] - self.w_end_pos[0]) * self.base.scale
            w = readable_str(w * self.base.microscope_view.mpp.value, 'm')

            h = abs(self.w_start_pos[1] - self.w_end_pos[1]) * self.base.scale
            h = readable_str(h * self.base.microscope_view.mpp.value, 'm')

            size_lbl = u"{} x {}".format(w, h)

            msg =  u"{}".format(size_lbl)

            if self.dragging:
                self.write_label(ctx, b_end_pos, msg)
            else:
                self.write_label(ctx, b_start_pos, msg)

            # if self.dragging:
            #     #ctx.translate(-view_size[0] / 2, -view_size[1] / 2)
            #     msg = """{}
            #              view: {} x {}
            #              buffer: {} x {}
            #              world: {} x {}
            #              center: {}
            #              offset: {}
            #              scale: {}
            #              shift: {},
            #           """.format(
            #                 self.label,
            #                 self.v_start_pos, self.v_end_pos,
            #                 start_pos, end_pos,
            #                 self.w_start_pos, self.w_end_pos,
            #                 self.base.buffer_center_world_pos,
            #                 offset,
            #                 scale,
            #                 shift)

            #     ctx.select_font_face(
            #         "Courier",
            #         cairo.FONT_SLANT_NORMAL,
            #         cairo.FONT_WEIGHT_NORMAL
            #     )
            #     ctx.set_font_size(12)

            #     #buf_pos = self.b_to_buffer_pos((9, 19))

            #     x = 9
            #     y = 19
            #     for line in [l.strip() for l in msg.splitlines()]:
            #         ctx.set_source_rgb(0.0, 0.0, 0.0)
            #         buffer_pos = self.base.view_to_buffer_pos((x, y))
            #         ctx.move_to(*buffer_pos)
            #         ctx.show_text(line)
            #         ctx.set_source_rgb(1.0, 1.0, 1.0)
            #         ctx.move_to(buffer_pos[0] + 1, buffer_pos[1] + 1)
            #         ctx.show_text(line)
            #         y += 20
