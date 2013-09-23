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
from __future__ import division

import logging
import math
from abc import ABCMeta, abstractmethod
import cairo
import wx

import odemis.gui as gui
import odemis.gui.img.data as img
import odemis.gui.util.units as units

from odemis.gui.util.units import readable_str
from odemis.gui.util.conversion import hex_to_frgba, change_brightness


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

    def write_label(self, ctx, size, vpos, label, flip=True, align=wx.ALIGN_LEFT):
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

        if align == wx.ALIGN_RIGHT:
            x = x - width

        if flip:
            if x + width + margin_x > size.x:
                x = self.base.ClientSize[0] - width - margin_x
            elif x < margin_x:
                x = margin_x

            if y + height + margin_x > size.y:
                y = self.base.ClientSize[1] - height
            elif y < height:
                y = height

        #t = font.GetPixelSize()

        ctx.set_source_rgba(0.0, 0.0, 0.0, 0.7)
        ctx.move_to(x + 1, y + 1)
        ctx.show_text(label)

        ctx.set_source_rgb(1.0, 1.0, 1.0)
        ctx.move_to(x, y)
        ctx.show_text(label)

    def _clip_viewport_pos(self, pos):
        """ Return the given pos, clipped by the base's viewport
            TODO: Change into viewport method?
        """

        pos.x = max(1, min(pos.x, self.base.ClientSize.x - 1))
        pos.y = max(1, min(pos.y, self.base.ClientSize.y - 1))

        return pos

    def _clip_buffer_pos(self, pos):
        """ Return the given pos, clipped by the base's buffer
            TODO: Change into viewport method?
        """
        x = max(1, min(pos[0], self.base._bmp_buffer_size[0] - 1))
        y = max(1, min(pos[1], self.base._bmp_buffer_size[1] - 1))

        return x, y

class ViewOverlay(Overlay):
    """ This class displays an overlay on the view port.
    The Draw method has to be fast, because it's called after every
    refresh of the canvas. The center of the window is at 0,0 (and
    dragging doesn't affects that). """
    __metaclass__ = ABCMeta

    @abstractmethod
    def Draw(self, dc):
        pass

class WorldOverlay(Overlay):
    """ This class displays an overlay on the buffer. 
    It's updated only every time the entire buffer is redrawn."""
    __metaclass__ = ABCMeta

    @abstractmethod
    def Draw(self, dc, shift=(0, 0), scale=1.0):
        pass


class TextViewOverlay(ViewOverlay):
    """ This overlay draws the label text at the provided view position """
    def __init__(self, base, vpos=((10, 16))):
        super(TextViewOverlay, self).__init__(base, label="")
        self.vpos = vpos

    def Draw(self, dc):
        if self.label:
            ctx = wx.lib.wxcairo.ContextFromDC(dc)
            self.write_label(ctx, dc.GetSize(), self.vpos, self.label)

class CrossHairOverlay(ViewOverlay):
    def __init__(self, base,
                 color=gui.CROSSHAIR_COLOR, size=gui.CROSSHAIR_SIZE, center=(0, 0)):
        super(CrossHairOverlay, self).__init__(base)

        self.pen = wx.Pen(color)
        self.size = size
        self.center = center

    def Draw(self, dc):
        """
        Draws the crosshair
        dc (wx.DC)
        """
        center = self.center
        tl = (center[0] - self.size, center[1] - self.size)
        br = (center[0] + self.size, center[1] + self.size)

        # Draw black contrast cross first
        pen = wx.Pen(wx.BLACK)
        dc.SetPen(pen)
        dc.DrawLine(tl[0] + 1, center[1] + 1, br[0] + 1, center[1] + 1)
        dc.DrawLine(center[0] + 1, tl[1] + 1, center[0] + 1, br[1] + 1)

        dc.SetPen(self.pen)
        dc.DrawLine(tl[0], center[1], br[0], center[1])
        dc.DrawLine(center[0], tl[1], center[0], br[1])

class SpotModeOverlay(ViewOverlay):
    """ This overlay displays a circle marker in the center of
    the canvas, indicating that the spot mode has been activated.
    """
    def __init__(self, base):
        super(SpotModeOverlay, self).__init__(base)

        self.marker_bmp = img.getspot_markerBitmap()
        marker_size = self.marker_bmp.GetSize()
        self._marker_offset = (marker_size.GetWidth() // 2 - 1,
                               marker_size.GetHeight() // 2 - 1)
        self.center = (0, 0)

    def Draw(self, dc_buffer):
        dc_buffer.DrawBitmapPoint(
            self.marker_bmp,
            wx.Point(
                self.center[0] - self._marker_offset[0],
                self.center[1] - self._marker_offset[1]),
            useMask=False)

class StreamIconOverlay(ViewOverlay):
    """ This class can display various icon on the view to indicate the state of
    the Streams belonging to that view.
    """

    opacity = 0.8
    def __init__(self, base):
        super(StreamIconOverlay, self).__init__(base)
        self.pause = False # if True: displayed
        self.play = 0 # opacity of the play icon

        self.colour = hex_to_frgba(gui.FOREGROUND_COLOUR_HIGHLIGHT, self.opacity)

    def hide_pause(self, hidden=True):
        """
        Hides the pause icon (or not)
        hidden (boolean): if True, hides the icon (and display shortly a play
         icon). If False, shows the pause icon.
        """
        self.pause = not hidden
        if not self.pause:
            self.play = 1.0
        wx.CallAfter(self.base.Refresh)

    def Draw(self, dc_buffer):
        ctx = wx.lib.wxcairo.ContextFromDC(dc_buffer)

        if self.pause:
            self._draw_pause(ctx)
        elif self.play:
            self._draw_play(ctx)
            if self.play > 0:
                self.play -= 0.1 # a tenth less
                # Force a refresh (without erase background), to cause a new draw
                wx.CallLater(50, self.base.Refresh, False) # in 0.05 s
            else:
                self.play = 0

    def _get_dimensions(self):

        width = max(16, self.base.ClientSize.x / 10)
        height = width
        right = self.base.ClientSize.x
        bottom = self.base.ClientSize.y
        margin = self.base.ClientSize.x / 25

        return width, height, right, bottom, margin

    def _draw_play(self, ctx):

        width, height, right, _, margin = self._get_dimensions()

        half_height = height / 2

        x = right - margin - width + 0.5
        y = margin + 0.5

        ctx.set_line_width(1)
        ctx.set_source_rgba(
            *hex_to_frgba(gui.FOREGROUND_COLOUR_HIGHLIGHT, self.play))

        ctx.move_to(x, y)

        x = right - margin - 0.5
        y += half_height

        ctx.line_to(x, y)

        x = right - margin - width + 0.5
        y += half_height

        ctx.line_to(x, y)
        ctx.close_path()

        ctx.fill_preserve()

        ctx.set_source_rgba(0, 0, 0, self.play)
        ctx.stroke()

    def _draw_pause(self, ctx):

        width, height, right, _, margin = self._get_dimensions()

        bar_width = max(width / 3, 1)
        gap_width = max(width - (2 * bar_width), 1) - 0.5

        x = right - margin - bar_width + 0.5
        y = margin + 0.5

        ctx.set_line_width(1)

        ctx.set_source_rgba(*self.colour)
        ctx.rectangle(x, y, bar_width, height)

        x -= bar_width + gap_width
        ctx.rectangle(x, y, bar_width, height)

        ctx.set_source_rgba(*self.colour)
        ctx.fill_preserve()

        ctx.set_source_rgb(0, 0, 0)
        ctx.stroke()

class FocusOverlay(ViewOverlay):
    """ This overlay can be used to display the change in focus """
    def __init__(self, base):
        super(FocusOverlay, self).__init__(base)

        self.margin = 10
        self.line_width = 16
        self.shifts = [0, 0]

    def Draw(self, dc):
        """
        Draws the crosshair
        dc (wx.DC)
        """
        # TODO: handle displaying the focus 0 (horizontally)

        if self.shifts[1]:
            ctx = wx.lib.wxcairo.ContextFromDC(dc)
            ctx.set_line_width(10)
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(1.0, 1.0, 1.0, 0.8)

            x, y = self.base.ClientSize
            x -= self.margin - (self.line_width // 2)
            middle = y / 2

            shift = self.shifts[1] * 1e6 # typically within µm
            end_y = middle - (middle * (shift / (y / 2)))
            end_y = min(max(self.margin, end_y), y - self.margin)

            ctx.move_to(x, middle)
            ctx.line_to(x, end_y)
            ctx.stroke()
            self.write_label(
                ctx,
                dc.GetSize(),
                (x - 10, end_y),
                "focus %s" % units.readable_str(self.shifts[1], 'm', 2),
                flip=False,
                align=wx.ALIGN_RIGHT
            )

    def add_shift(self, shift, axis):
        """ Adds a value on the given axis, and updates the overlay
        shift (float): amount added to the current value (can be negative)
        axis (int): axis for which this happens
        """
        self.shifts[axis] += shift
        self.base.Refresh()

    def clear_shift(self):
        logging.debug("Clearing focus shift")
        self.shifts = [0, 0]
        self.base.Refresh()

class SelectionMixin(object):
    """ This mix-in class can be used on an Overlay to draw rectangular 
    selection areas. These areas are always expressed in view port coordinates.
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

        self.color = hex_to_frgba(color)
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

            # Make sure that the start and end positions are the top left and
            # bottom right respectively.
            start_x = min(self.v_start_pos.x, self.v_end_pos.x)
            start_y = min(self.v_start_pos.y, self.v_end_pos.y)
            end_x = max(self.v_start_pos.x, self.v_end_pos.x)
            end_y = max(self.v_start_pos.y, self.v_end_pos.y)

            self.v_start_pos = wx.Point(start_x, start_y)
            self.v_end_pos = wx.Point(end_x, end_y)

            self._calc_edges()
            self.dragging = False
            self.edit = False
            self.edit_edge = None


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

    ##### drag methods  #####

    def start_drag(self, start_pos):
        logging.debug("Starting selection drag")
        self.edit_start_pos = start_pos
        self.edit = True

    def update_drag(self, current_pos):
        # TODO: The drag range is currently limited by the location of the
        # mouse pointer, meaning that you cannot drag the cursor beyong the
        # edge of the canvas.
        # It might be better to limit the movement in such a way that no part
        # of the selection can be dragged off canvas. The commented part was a
        # first attempt at that, but it didn't work.
        current_pos = self._clip_viewport_pos(current_pos)
        diff = current_pos - self.edit_start_pos
        self.v_start_pos += diff
        self.v_end_pos += diff
        self.edit_start_pos = current_pos

        # diff = current_pos - self.edit_start_pos
        # new_start = self.v_start_pos + diff

        # if new_start == self._clip_viewport_pos(new_start):
        #     new_end = self.v_start_pos + diff
        #     if new_end == self._clip_viewport_pos(new_end):
        #         self.v_start_pos = new_start
        #         self.v_end_pos = new_end

        # self.edit_start_pos = current_pos


    def stop_drag(self):
        self.stop_selection()

    ##### END drag methods  #####

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

            if self.dragging:
                msg = "{}: {} to {}, {} to {} unscaled".format(
                                            self.label,
                                            self.v_start_pos,
                                            self.v_end_pos,
                                            start_pos,
                                            end_pos)
                self.write_label(ctx, dc.GetSize(), (10, 10), msg)

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
        #view_size = self.base._bmp_buffer_size
        view_size = self.base.GetSize()
        return (vpos[0] - (view_size[0] // 2),
                vpos[1] - (view_size[1] // 2))

    def _calc_world_pos(self):
        """ Update the world position to reflect the view position
        """
        if self.v_start_pos and self.v_end_pos:
            offset = tuple(v // 2 for v in self.base._bmp_buffer_size)
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
        offset = tuple(v // 2 for v in self.base._bmp_buffer_size)
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
            offset = tuple(v // 2 for v in self.base._bmp_buffer_size)
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
            if (self.dragging or self.edit) and self.base.microscope_view:
                w, h = self.base.selection_to_real_size(
                                            self.w_start_pos,
                                            self.w_end_pos
                )

                w = readable_str(w, 'm', sig=2)
                h = readable_str(h, 'm', sig=2)
                size_lbl = u"{} x {}".format(w, h)

                pos = (b_end_pos[0] + 5, b_end_pos[1] - 5)
                self.write_label(ctx, dc_buffer.GetSize(), pos, size_lbl)

FILL_GRID = 0
FILL_POINT = 1

class RepetitionSelectOverlay(WorldSelectOverlay):

    def __init__(self, base, label,
                 sel_cur=None,
                 color=gui.SELECTION_COLOR,
                 center=(0, 0)):

        super(RepetitionSelectOverlay, self).__init__(base, label)

        self.fill = None
        self.repitition = (0, 0)
        self.bmp = None

    def clear_fill(self):
        self.fill = None
        self.bmp = None

    def point_fill(self):
        self.fill = FILL_POINT

    def grid_fill(self):
        self.fill = FILL_GRID

    def set_repetition(self, repitition):
        self.repitition = repitition
        self.clear_fill()

    def Draw(self, dc_buffer, shift=(0, 0), scale=1.0):

        if self.w_start_pos and self.w_end_pos and self.fill is not None:
            ctx = wx.lib.wxcairo.ContextFromDC(dc_buffer)
            # Calculate the offset of the center of the buffer relative to the
            # top left op the buffer
            offset = tuple(v // 2 for v in self.base._bmp_buffer_size)

            # The start and end position, in buffer coordinates. The resoluting
            # values may extend beyond the actual buffer when zoomed in.
            b_start_pos = self.base.world_to_buffer_pos(
                                        self.w_start_pos,
                                        offset)
            b_end_pos = self.base.world_to_buffer_pos(
                                        self.w_end_pos,
                                        offset)

            logging.warn(
                "start and end buffer pos: %s %s",
                b_start_pos, b_end_pos)

            # Calculate the width and height in buffer pixels. Again, this may
            # be wider and higher than the actual buffer.
            width = b_end_pos[0] - b_start_pos[0]
            height = b_end_pos[1] - b_start_pos[1]

            logging.warn("width and height: %s %s", width, height)

            # Clip the start and end positions using the actual buffer size
            clipped_start_pos = self._clip_buffer_pos(b_start_pos)
            clipped_end_pos = self._clip_buffer_pos(b_end_pos)

            logging.warn(
                "clipped start and end: %s %s",
                clipped_start_pos,
                clipped_end_pos)

            # No need to render
            if 0 in self.repitition:
                return

            rep_x, rep_y = self.repitition

            # The start and end values we are drawing from and to.
            start_x, start_y = clipped_start_pos
            end_x, end_y = clipped_end_pos

            # The step size in pixels
            step_x = width // rep_x
            step_y = height // rep_y

            # Make the color more transparent, when there are more steps than
            # pixels
            r, g, b, _ = self.color
            if width < rep_x or height < rep_y:
                ctx.set_source_rgba(r, g, b, 0.5)
            else:
                ctx.set_source_rgba(r, g, b, 0.9)

            if self.fill == FILL_POINT:
                if not self.bmp:
                    # If we cannot fit enough 3x3 bitmaps into either direction,
                    # then we just fill a rectangle
                    if width // 3 < rep_x or height // 3 < rep_y:
                        logging.warn("simple fill")
                        ctx.rectangle(
                            start_x, start_y,
                            int(end_x - start_x), int(end_y - start_y))
                        ctx.fill()
                        ctx.stroke()
                    else:
                        half_step_x = step_x // 2
                        half_step_y = step_y // 2

                        # The number of repetitions that fits into the buffer
                        # clipped selection
                        buf_rep_x = (end_x - start_x) // step_x
                        buf_rep_y = (end_y - start_y) // step_y

                        logging.warn(
                                "Rendering %sx%s points",
                                buf_rep_x,
                                buf_rep_y
                        )

                        point = img.getdotBitmap()
                        point_dc = wx.MemoryDC()
                        point_dc.SelectObject(point)
                        point.SetMaskColour(wx.BLACK)

                        horz_dc = wx.MemoryDC()
                        horz_bmp = wx.EmptyBitmap(
                                        int(end_x - start_x), 3)
                        horz_dc.SelectObject(horz_bmp)
                        horz_dc.SetBackground(wx.BLACK_BRUSH)
                        horz_dc.Clear()

                        blit = horz_dc.Blit
                        for i in range(buf_rep_x):
                            x = i * step_x + half_step_x
                            blit(x, 0, 3, 3, point_dc, 0, 0)

                        total_dc = wx.MemoryDC()
                        self.bmp = wx.EmptyBitmap(
                                        int(end_x - start_x),
                                        int(end_y - start_y))
                        total_dc.SelectObject(self.bmp)
                        total_dc.SetBackground(wx.BLACK_BRUSH)
                        total_dc.Clear()

                        blit = total_dc.Blit
                        for j in range(buf_rep_y):
                            y = j * step_y + half_step_y
                            blit(0, y, int(end_x - start_x), 3, horz_dc, 0, 0)

                        self.bmp.SetMaskColour(wx.BLACK)

                else:
                    dc_buffer.DrawBitmapPoint(self.bmp,
                            wx.Point(int(start_x), int(start_y)),
                            useMask=True)

            elif self.fill == FILL_GRID:
                # The grid is easy to draw, no need for a cached bmp
                self.bmp = None

                # If there are more repititions in either direction than there
                # are pixels, just fill a semi transparent rectangle
                if 0 in (step_x, step_y):
                    ctx.rectangle(
                        start_x, start_y,
                        int(end_x - start_x), int(end_y - start_y))
                    ctx.fill()
                else:
                    ctx.set_line_width(1)
                    # ctx.set_antialias(cairo.ANTIALIAS_DEFAULT)

                    move_to = ctx.move_to
                    line_to = ctx.line_to

                    # The number of repetitions that fits into the buffer clipped
                    # selection
                    buf_rep_x = (end_x - start_x) // step_x
                    buf_rep_y = (end_y - start_y) // step_y

                    for i in range(1, buf_rep_x):
                        move_to(start_x + i * step_x, start_y)
                        line_to(start_x + i * step_x, end_y)

                    for i in range(1, buf_rep_y):
                        move_to(start_x, start_y + i * step_y)
                        line_to(end_x,  start_y + i * step_y)

                ctx.stroke()

        super(RepetitionSelectOverlay, self).Draw(dc_buffer, shift, scale)

class MarkingLineOverlay(ViewOverlay):

    def __init__(self, base,
                 label="",
                 sel_cur=None,
                 color=gui.SELECTION_COLOR,
                 center=(0, 0)):

        super(MarkingLineOverlay, self).__init__(base, label)
        self.color = hex_to_frgba(color)
        self.vposx = None
        self.vposy = None

        self.line_width = 2

    def set_position(self, pos):
        self.vposx = max(1, min(pos[0], self.base.ClientSize.x - self.line_width))
        self.vposy = max(1, min(pos[1], self.base.ClientSize.y - 1))

    def Draw(self, dc_buffer, shift=(0, 0), scale=1.0):
        ctx = wx.lib.wxcairo.ContextFromDC(dc_buffer)

        if self.vposy:
            r, g, b, a = change_brightness(self.color, -0.2)
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
                self.write_label(ctx, dc_buffer.GetSize(), vpos, self.label)

TOP_LEFT = 0
TOP_RIGHT = 1
BOTTOM_LEFT = 2
BOTTOM_RIGHT = 3

class DichotomyOverlay(ViewOverlay):
    """ This overlay allows the user to select a sequence of nested quadrants
    within the canvas. The quadrants are numbered 0 to 3, from the top left to
    the bottom right. The first quadrant is the biggest, with each subsequent
    quadrant being nested in the one before it.
    """

    def __init__(self, base, sequence_va, color=gui.SELECTION_COLOR):
        """ :param sequence_va: (ListVA) VA to store the sequence in
        """
        super(DichotomyOverlay, self).__init__(base)

        self.color = hex_to_frgba(color)
        # Color for quadrant that will expand the sequence
        self.hover_forw = hex_to_frgba(color, 0.5)
        # Color for quadrant that will cut the sequence
        self.hover_back = change_brightness(self.hover_forw, -0.2)

        self.sequence_va = sequence_va
        self.sequence_rect = []

        # This attribute is used to track the position of the mouse cursor.
        # The first value denotes the smallest quadrant (in size) in the
        # sequence and the second one the quadrant index number that will
        # be added if the mouse is clicked.
        # This value should be set to (None, None) if the mouse is outside the
        # canvas or when we are not interested in updating the sequence.
        self.hover_pos = (None, None)

        # maximum number of sub-quadrants (6->2**6 smaller than the whole area)
        self.max_len = 6

        # Bind event handlers
        self.base.Bind(wx.EVT_SIZE, self.on_size)
        self.base.Bind(wx.EVT_MOTION, self.on_mouse_motion)
        self.base.Bind(wx.EVT_LEFT_UP, self.on_mouse_button)
        self.base.Bind(wx.EVT_LEAVE_WINDOW, self.on_mouse_leave)

        self.sequence_va.subscribe(self.on_change, init=True)

        # Disabling the overlay will allow the event handlers to ignore events
        self.enabled = False

    def on_change(self, seq):

        if not all([0 <= v <= 3 for v in seq]):
            raise ValueError("Illegal quadrant values in sequence!")

        rect = 0, 0, self.base.ClientSize.x, self.base.ClientSize.y
        self.sequence_rect = [rect]

        for i, q in enumerate(seq):
            rect = self.index_to_rect(i, q)
            self.sequence_rect.append(rect)

        self.base.Refresh()

    def _reset(self):
        """ Reset all attributes to their default values and get the dimensions
        from the base canvas.
        """
        logging.debug("Reset")
        self.sequence_va.value = []

    def on_mouse_leave(self, evt):
        """ Event handler called when the mouse cursor leaves the canvas """

        # When the mouse cursor leaves the overlay, the current top quadrant
        # should be highlighted, so clear the hover_pos attribute.
        self.hover_pos = (None, None)
        if self.enabled:
            self.base.Refresh()

        evt.Skip()

    def enable(self, enable=True):
        """ Enable of disable the overlay """
        self.enabled = enable
        self.base.Refresh()

    def on_mouse_motion(self, evt):
        """ Mouse motion event handler """

        if self.enabled:
            self._updateHover(evt.GetPosition())
#        else:
        evt.Skip()

    def on_mouse_button(self, evt):
        """ Mouse button handler """
        evt.Skip() # FIXME
        if not self.enabled:
            return
            
        # If the mouse cursor is over a selectable quadrant
        if None not in self.hover_pos:
            idx, quad = self.hover_pos

            # If we are hovering over the 'top' quadrant, add it to the sequence
            if len(self.sequence_va.value) == idx:
                new_seq = self.sequence_va.value + [quad]
                new_seq = new_seq[:self.max_len] # cut if too long
            # Jump to the desired quadrant otherwise, cutting the sequence
            else:
                # logging.debug("Trim")
                new_seq = self.sequence_va.value[:idx] + [quad]
            self.sequence_va.value = new_seq

            self._updateHover(evt.GetPosition())

    def on_size(self, evt):
        """ Called when size of canvas changes
        """
        # Force the recomputation of rectangles
        self.on_change(self.sequence_va.value)

    def _updateHover(self, pos):
        idx, quad = self.quad_hover(pos)

        # Change the cursor into a hand if the quadrant being hovered over
        # can be selected. Use the default cursor otherwise
        if idx >= self.max_len:
            self.base.SetCursor(wx.STANDARD_CURSOR)
            idx, quad = (None, None)
        else:
            self.base.SetCursor(wx.StockCursor(wx.CURSOR_HAND))

        # Redraw only if the quadrant changed
        if self.hover_pos != (idx, quad):
            self.hover_pos = (idx, quad)
            self.base.Refresh()

    def quad_hover(self, vpos):
        """ Return the sequence index number of the rectangle at position vpos
        and the quadrant vpos is over inside that rectangle.

        :param vpos: (int, int) The viewport x,y hover position
        """

        # Loop over the rectangles, smallest one first
        for i, (x, y, w, h) in reversed(list(enumerate(self.sequence_rect))):
            if  x <= vpos.x <= x + w:
                if  y <= vpos.y <= y + h:
                    # If vpos is within the rectangle, we can determine the
                    # quadrant.
                    # Remember that the quadrants are numbered as follows:
                    #
                    # 0 | 1
                    # --+--
                    # 2 | 3

                    # Construct the quadrant number by starting with 0
                    quad = 0

                    # If the position is in the left half, add 1 to the quadrant
                    if vpos.x > x + w / 2:
                        quad += 1
                    # If the position is in the bottom half, add 2
                    if vpos.y > y + h / 2:
                        quad += 2

                    return i, quad

        return None, None

    def index_to_rect(self, idx, quad):
        """ Translate given rectangle and quadrant into a view rectangle
            :param idx: (int) The index number of the rectangle in sequence_rect
                that we are going to use as a base.
            :param quad: (int) The quadrant number
            :return: (int, int, int, int) Rectangle tuple of the form x, y, w, h
        """
        x, y, w, h = self.sequence_rect[idx]

        # The new rectangle will have half the size of the base one
        w = w / 2
        h = h / 2

        # If the quadrant is in the right half, construct x by adding half the
        # width to x position of the base rectangle.
        if quad in (TOP_RIGHT, BOTTOM_RIGHT):
            x += w

        # If the quadrant is in the bottom half, construct y by adding half the
        # height to the y position of the base rectangle.
        if quad in (BOTTOM_LEFT, BOTTOM_RIGHT):
            y += h

        return x, y, w, h


    def Draw(self, dc):

        if self.enabled:
            ctx = wx.lib.wxcairo.ContextFromDC(dc)

            ctx.set_source_rgba(*self.color)
            ctx.set_line_width(2)
            ctx.set_dash([2,])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)

            # Draw previous selections as dashed rectangles
            for rect in self.sequence_rect:
                # logging.debug("Drawing ", *args, **kwargs)
                ctx.rectangle(*rect)
            ctx.stroke()

            # If the mouse is over the canvas
            if None not in self.hover_pos:
                idx, quad = self.hover_pos

                # If the mouse is over the smallest selected quadrant
                if idx == len(self.sequence_va.value):
                    # Mark quadrant to be added
                    ctx.set_source_rgba(*self.hover_forw)
                    rect = self.index_to_rect(idx, quad)
                    ctx.rectangle(*rect)
                    ctx.fill()
                else:
                    # Mark higher quadrant to 'jump' to
                    ctx.set_source_rgba(*self.hover_back)
                    rect = self.index_to_rect(idx, quad)
                    ctx.rectangle(*rect)
                    ctx.fill()

                    # Mark current quadrant
                    ctx.set_source_rgba(*self.hover_forw)
                    ctx.rectangle(*self.sequence_rect[-1])
                    ctx.fill()

            # If the mouse is not over the canvas
            elif self.sequence_va.value and self.sequence_rect:
                # Mark the currently selected quadrant
                ctx.set_source_rgba(*self.hover_forw)
                ctx.rectangle(*self.sequence_rect[-1])
                ctx.fill()

