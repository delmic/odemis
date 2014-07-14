# -*- coding: utf-8 -*-

"""
Created on 2014-01-25

@author: Rinze de Laat

Copyright © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

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
import wx.lib.wxcairo as wxcairo

from .base import ViewOverlay, DragMixin, SelectionMixin
import odemis.gui as gui
import odemis.gui.img.data as img
import odemis.model as model
import odemis.util.conversion as conversion
import odemis.util.units as units


class TextViewOverlay(ViewOverlay):
    """ This overlay draws the label text at the provided view position """

    def __init__(self, cnvs, vpos=((10, 16))):
        super(TextViewOverlay, self).__init__(cnvs)
        self.vpos = vpos

    def Draw(self, ctx):
        if self.labels:
            self._write_labels(ctx)


class CrossHairOverlay(ViewOverlay):
    def __init__(self, cnvs,
                 colour=gui.CROSSHAIR_COLOR, size=gui.CROSSHAIR_SIZE,
                 center=(0, 0)):
        super(CrossHairOverlay, self).__init__(cnvs)

        self.colour = conversion.hex_to_frgba(colour)
        self.size = size
        self.center = center

    def Draw(self, ctx):
        """ Draw a cross hair to the ctx Cairo context """

        ctx.save()

        center = self.cnvs.get_half_view_size()

        tl = (center[0] - self.size, center[1] - self.size)
        br = (center[0] + self.size, center[1] + self.size)

        ctx.set_line_width(1)

        ctx.set_source_rgba(0, 0, 0, 0.9)
        ctx.move_to(tl[0] + 1.5, center[1] + 1.5)
        ctx.line_to(br[0] + 1.5, center[1] + 1.5)
        ctx.move_to(center[0] + 1.5, tl[1] + 1.5)
        ctx.line_to(center[0] + 1.5, br[1] + 1.5)
        ctx.stroke()

        ctx.set_source_rgba(*self.colour)
        ctx.move_to(tl[0] + 0.5, center[1] + 0.5)
        ctx.line_to(br[0] + 0.5, center[1] + 0.5)
        ctx.move_to(center[0] + 0.5, tl[1] + 0.5)
        ctx.line_to(center[0] + 0.5, br[1] + 0.5)
        ctx.stroke()

        ctx.restore()


class SpotModeOverlay(ViewOverlay):
    """ This overlay displays a circle marker in the center of
    the canvas, indicating that the spot mode has been activated.
    """
    def __init__(self, cnvs):
        super(SpotModeOverlay, self).__init__(cnvs)

        self.marker_bmp = img.getspot_markerBitmap()
        marker_size = self.marker_bmp.GetSize()
        self._marker_offset = (marker_size.GetWidth() // 2 - 1, marker_size.GetHeight() // 2 - 1)
        self.center = self.cnvs.get_half_view_size()

    def Draw(self, ctx):
        # TODO: Replace the wxPython code with the following Cairo code
        # The problem with the code is that the fully transparent background of the image used
        # is *not* fully transparent.

        # img_surface = wxcairo.ImageSurfaceFromBitmap(self.marker_bmp)
        # ctx.set_source_rgba(0.0, 0.0, 0.0, 0.0) # transparent black
        # ctx.translate(self.center[0] - self._marker_offset[0],
        #               self.center[1] - self._marker_offset[1])
        # ctx.set_source_surface(img_surface)
        # ctx.paint()

        view_dc = wx.PaintDC(self.cnvs)
        view_dc.DrawBitmapPoint(
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

    def __init__(self, cnvs):
        super(StreamIconOverlay, self).__init__(cnvs)
        self.pause = False # if True: displayed
        self.play = 0  # opacity of the play icon

        self.colour = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, self.opacity)

    def hide_pause(self, hidden=True):
        """
        Hides the pause icon (or not)
        hidden (boolean): if True, hides the icon (and display shortly a play
         icon). If False, shows the pause icon.
        """
        self.pause = not hidden
        if not self.pause:
            self.play = 1.0
        wx.CallAfter(self.cnvs.Refresh)

    def Draw(self, ctx):
        if self.pause:
            self._draw_pause(ctx)
        elif self.play:
            self._draw_play(ctx)
            if self.play > 0:
                self.play -= 0.1 # a tenth less
                # Force a refresh (without erase background), to cause a new
                # draw
                wx.CallLater(50, self.cnvs.Refresh, False) # in 0.05 s
            else:
                self.play = 0

    def _get_dimensions(self):

        width = max(16, self.view_width / 10)
        height = width
        right = self.view_width
        bottom = self.view_height
        margin = self.view_width / 25

        return width, height, right, bottom, margin

    def _draw_play(self, ctx):

        width, height, right, _, margin = self._get_dimensions()

        half_height = height / 2

        x = right - margin - width + 0.5
        y = margin + 0.5

        ctx.set_line_width(1)
        ctx.set_source_rgba(
            *conversion.hex_to_frgba(
                gui.FG_COLOUR_HIGHLIGHT, self.play))

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
    def __init__(self, cnvs):
        super(FocusOverlay, self).__init__(cnvs)

        self.margin = 10
        self.line_width = 16
        self.shifts = [0, 0]

        self.focus_label = self.add_label("", align=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL)

    def Draw(self, ctx):
        """
        Draws the crosshair
        dc (wx.DC)
        """
        # TODO: handle displaying the focus 0 (horizontally)

        if self.shifts[1]:
            ctx.set_line_width(10)
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(1.0, 1.0, 1.0, 0.8)

            x, y = self.cnvs.ClientSize
            x -= self.margin + (self.line_width // 2)
            middle = y / 2

            shift = self.shifts[1] * 1e6 # typically within µm
            end_y = middle - (middle * (shift / (y / 2)))
            end_y = min(max(self.margin, end_y), y - self.margin)

            ctx.move_to(x, middle)
            ctx.line_to(x, end_y)
            ctx.stroke()

            lbl = "focus %s" % units.readable_str(self.shifts[1], 'm', 2)
            self.focus_label.text = lbl
            self.focus_label.pos = (x - 15, end_y)
            self._write_label(ctx, self.focus_label)

    def add_shift(self, shift, axis):
        """ Adds a value on the given axis, and updates the overlay
        shift (float): amount added to the current value (can be negative)
        axis (int): axis for which this happens
        """
        self.shifts[axis] += shift
        self.cnvs.Refresh()

    def clear_shift(self):
        logging.debug("Clearing focus shift")
        self.shifts = [0, 0]
        self.cnvs.Refresh()


class ViewSelectOverlay(ViewOverlay, SelectionMixin):
    #pylint: disable=W0221
    def __init__(self, cnvs,
                 sel_cur=None,
                 colour=gui.SELECTION_COLOUR,
                 center=(0, 0)):

        super(ViewSelectOverlay, self).__init__(cnvs)
        SelectionMixin.__init__(self, sel_cur, colour, center)

        self.position_label = self.add_label("")

    def Draw(self, ctx, shift=(0, 0), scale=1.0):

        if self.v_start_pos and self.v_end_pos:
            #pylint: disable=E1103
            start_pos = self.v_start_pos
            end_pos = self.v_end_pos

            # logging.debug("Drawing from %s, %s to %s. %s", start_pos[0],
            #                                                start_pos[1],
            #                                                end_pos[0],
            #                                                end_pos[1] )

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
            ctx.set_source_rgba(*self.colour)
            ctx.rectangle(*rect)
            ctx.stroke()

            self.position_label.pos = start_pos
            # FIXME: label text is always empty... what's the point of displaying it?
            # self.text = "kaas"

            # self._write_label(ctx, self.position_label)


HORIZONTAL = 1
VERTICAL = 2


class MarkingLineOverlay(ViewOverlay, DragMixin):
    """ Draw a vertical line at the given view position.
    This class can easily be extended to include a horizontal or horz/vert
    display mode.

    TODO: Added a way to have the lines track the mouse's x, y or a and y
    """
    def __init__(self, cnvs,
                 sel_cur=None,
                 colour=gui.SELECTION_COLOUR,
                 center=(0, 0),
                 orientation=HORIZONTAL):

        super(MarkingLineOverlay, self).__init__(cnvs)
        DragMixin.__init__(self)

        self.colour = conversion.hex_to_frgba(colour)

        self.v_posx = model.VigilantAttribute(None)
        self.v_posy = model.VigilantAttribute(None)

        self._x_label = self.add_label("", colour=self.colour)
        self._y_label = self.add_label("", colour=self.colour,
                                       align=wx.ALIGN_BOTTOM)

        self.orientation = orientation
        self.line_width = 2

    @property
    def x_label(self):
        return self._x_label

    @x_label.setter
    def x_label(self, lbl):
        self._x_label.text = lbl

    @property
    def y_label(self):
        return self._y_label

    @y_label.setter
    def y_label(self, lbl):
        self._y_label.text = lbl


    def clear(self):
        self.v_posx.value = None
        self.v_posy.value = None

    def on_left_down(self, evt):
        super(MarkingLineOverlay, self).on_left_down(evt)
        DragMixin.on_left_down(self, evt)
        self.colour = self.colour[:3] + (0.5,)
        self._set_to_mouse_x(evt)

    def on_left_up(self, evt):
        super(MarkingLineOverlay, self).on_left_up(evt)
        DragMixin.on_left_up(self, evt)
        self.colour = self.colour[:3] + (1.0,)
        self._set_to_mouse_x(evt)
        self.cnvs.Refresh()

    def on_motion(self, evt):
        super(MarkingLineOverlay, self).on_motion(evt)
        if self.left_dragging:
            self._set_to_mouse_x(evt)

    def _set_to_mouse_x(self, evt):
        """ Position the focus line at the position of the given mouse event """
        x, _ = evt.GetPositionTuple()
        # Clip x
        self.v_posx.value = max(min(self.cnvs.ClientSize.x, x), 1)

    def set_position(self, pos, label=None):
        self.v_posx.value = max(min(self.cnvs.ClientSize.x, pos[0]), 1)
        self.v_posy.value = max(1, min(pos[1], self.view_height - 1))
        self.label = label

    def Draw(self, ctx):
        ctx.set_line_width(self.line_width)
        ctx.set_dash([3,])
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.set_source_rgba(*self.colour)

        if (self.v_posx.value is not None and
            self.orientation & HORIZONTAL == HORIZONTAL):
            ctx.move_to(self.v_posx.value, 0)
            ctx.line_to(self.v_posx.value, self.cnvs.ClientSize[1])
            ctx.stroke()

        if (self.v_posy.value is not None and
            self.orientation & VERTICAL == VERTICAL):
            ctx.move_to(0, self.v_posy.value)
            ctx.line_to(self.cnvs.ClientSize[0], self.v_posy.value)
            ctx.stroke()

        if None not in (self.v_posy.value, self.v_posx.value):
            if self.x_label.text:
                self.x_label.pos = (self.v_posx.value + 5,
                                    self.cnvs.ClientSize.y)
                self._write_label(ctx, self.x_label)

            if self.y_label.text:
                yp = max(0, self.v_posy.value - 5) # Padding from line
                # Increase bottom margin if x label is close
                label_padding = 30 if self.v_posx.value < 50 else 0
                yn = min(self.cnvs.ClientSize.y - label_padding, yp)
                self.y_label.pos = (2, yn)
                self._write_label(ctx, self.y_label)

            r, g, b, a = conversion.change_brightness(self.colour, -0.2)
            a = 0.5
            ctx.set_source_rgba(r, g, b, a)
            ctx.arc(self.v_posx.value, self.v_posy.value, 5.5, 0, 2*math.pi)
            ctx.fill()


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

    def __init__(self, cnvs, sequence_va, colour=gui.SELECTION_COLOUR):
        """ :param sequence_va: (ListVA) VA to store the sequence in
        """
        super(DichotomyOverlay, self).__init__(cnvs)

        self.colour = conversion.hex_to_frgba(colour)
        # Color for quadrant that will expand the sequence
        self.hover_forw = conversion.hex_to_frgba(colour, 0.5)
        # Color for quadrant that will cut the sequence
        self.hover_back = conversion.change_brightness(self.hover_forw, -0.2)

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

        self.sequence_va.subscribe(self.on_change, init=True)

        # Disabling the overlay will allow the event handlers to ignore events
        self.enabled = False

    def on_change(self, seq):

        if not all([0 <= v <= 3 for v in seq]):
            raise ValueError("Illegal quadrant values in sequence!")

        rect = 0, 0, self.view_width, self.view_height
        self.sequence_rect = [rect]

        for i, q in enumerate(seq):
            rect = self.index_to_rect(i, q)
            self.sequence_rect.append(rect)

        self.cnvs.Refresh()

    def _reset(self):
        """ Reset all attributes to their default values and get the dimensions
        from the cnvs canvas.
        """
        logging.debug("Reset")
        self.sequence_va.value = []

    def on_leave(self, evt):
        """ Event handler called when the mouse cursor leaves the canvas """

        # When the mouse cursor leaves the overlay, the current top quadrant
        # should be highlighted, so clear the hover_pos attribute.
        self.hover_pos = (None, None)
        if self.enabled:
            self.cnvs.Refresh()

        evt.Skip()

    def enable(self, enable=True):
        """ Enable of disable the overlay """
        self.enabled = enable
        self.cnvs.Refresh()

    def on_motion(self, evt):
        """ Mouse motion event handler """

        if self.enabled:
            self._updateHover(evt.GetPosition())
        evt.Skip()

    def on_left_up(self, evt):
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
            self.cnvs.SetCursor(wx.STANDARD_CURSOR)
            idx, quad = (None, None)
        else:
            self.cnvs.SetCursor(wx.StockCursor(wx.CURSOR_HAND))

        # Redraw only if the quadrant changed
        if self.hover_pos != (idx, quad):
            self.hover_pos = (idx, quad)
            self.cnvs.Refresh()

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
                that we are going to use as a cnvs.
            :param quad: (int) The quadrant number
            :return: (int, int, int, int) Rectangle tuple of the form x, y, w, h
        """
        x, y, w, h = self.sequence_rect[idx]

        # The new rectangle will have half the size of the cnvs one
        w = w / 2
        h = h / 2

        # If the quadrant is in the right half, construct x by adding half the
        # width to x position of the cnvs rectangle.
        if quad in (TOP_RIGHT, BOTTOM_RIGHT):
            x += w

        # If the quadrant is in the bottom half, construct y by adding half the
        # height to the y position of the cnvs rectangle.
        if quad in (BOTTOM_LEFT, BOTTOM_RIGHT):
            y += h

        return x, y, w, h

    def Draw(self, ctx):

        if self.enabled:
            ctx.set_source_rgba(*self.colour)
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


class PolarOverlay(ViewOverlay):
    def __init__(self, cnvs):
        super(PolarOverlay, self).__init__(cnvs)

        self.canvas_padding = 0
        # self.cnvs.canDrag = False
        # Rendering attributes
        self.center_x = None
        self.center_y = None
        self.radius = None
        self.inner_radius = None
        self.tau = 2 * math.pi
        self.num_ticks = 6
        self.ticks = []

        self.ticksize = 10

        # Value attributes
        self.px, self.py = None, None
        self.tx, self.ty = None, None

        self.colour = conversion.hex_to_frgb(gui.SELECTION_COLOUR)
        self.colour_drag = conversion.hex_to_frgba(gui.SELECTION_COLOUR, 0.5)
        self.colour_highlight = conversion.hex_to_frgb(
                                            gui.FG_COLOUR_HIGHLIGHT)
        self.intensity_label = self.add_label(
                                    "", align=wx.ALIGN_CENTER_HORIZONTAL,
                                    colour=self.colour_highlight)

        self.phi = None             # Phi angle in radians
        self.phi_line_rad = None    # Phi drawing angle in radians (is phi -90)
        self.phi_line_pos = None    # End point in pixels of the Phi line
        self.phi_label = self.add_label("", colour=self.colour,
                           align=wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_BOTTOM)
        self.theta = None           # Theta angle in radians
        self.theta_radius = None    # Radius of the theta circle in pixels
        self.theta_label = self.add_label("", colour=self.colour,
                                          align=wx.ALIGN_CENTER_HORIZONTAL)
        self.intersection = None    # The intersection of the cirle and line in
                                    # pixels

        self.dragging = False

        # Calculate the characteristic values for the first time
        self.on_size()

    # Property Getters/Setters
    @property
    def phi_rad(self):
        return self.phi

    @phi_rad.setter
    def phi_rad(self, phi_rad):
        self.phi = phi_rad
        self._calculate_phi()
        self.cnvs.Refresh()

    @property
    def phi_deg(self):
        return math.degrees(self.phi)

    @phi_deg.setter
    def phi_deg(self, phi_deg):
        self.phi_rad = math.radians(phi_deg)


    @property
    def theta_rad(self):
        return self.theta

    @theta_rad.setter
    def theta_rad(self, theta_rad):
        self.theta = theta_rad
        self.theta_radius = (theta_rad / (math.pi / 2 )) * self.inner_radius
        self._calculate_theta()
        self.cnvs.Refresh()

    @property
    def theta_deg(self):
        return math.degrees(self.theta)

    @theta_deg.setter
    def theta_deg(self, theta_deg):
        self.theta_rad = math.radians(theta_deg)

    # END Property Getters/Setters

    def _calculate_phi(self, view_pos=None):
        """ Calcualte the Phi angle and the values to display the Phi line """

        if view_pos:
            vx, vy = view_pos
            dx, dy = vx - self.center_x, self.center_y - vy

            # Calculate the phi angle in radians
            # Atan2 gives the angle between the positive x axis and the point
            # dx,dy
            self.phi = math.atan2(dx, dy) % self.tau

        if self.phi:
            self.phi_line_rad = self.phi - math.pi / 2

            cos_phi_line = math.cos(self.phi_line_rad)
            sin_phi_line = math.sin(self.phi_line_rad)

            # Pixel to which to draw the Phi line to
            phi_x = self.center_x + self.radius * cos_phi_line
            phi_y = self.center_y + self.radius * sin_phi_line
            self.phi_line_pos = (phi_x, phi_y)

            # Calc Phi label pos

            # Calculate the view point on the line where to place the label
            if (self.theta_radius > self.inner_radius / 2):
                radius = self.inner_radius * 0.25
            else:
                radius = self.inner_radius * 0.75

            x = self.center_x + radius * cos_phi_line
            y = self.center_y + radius * sin_phi_line

            self.phi_label.text = u"φ %0.1f°" % math.degrees(self.phi)
            self.phi_label.deg = math.degrees(self.phi_line_rad)

            # Now we calculate a perpendicular offset to the Phi line where
            # we can plot the label. It is also determined if the label should
            # flip, depending on the angle.

            if self.phi < math.pi:
                ang = -math.pi / 2.0 # -45 deg
                self.phi_label.flip = False
            else:
                ang = math.pi / 2.0 # 45 deg
                self.phi_label.flip = True

            # Calculate a point further down the line that we will rotate
            # around the calculated label x,y. By translating (-x and -y) we
            # 'move' the origin to label x,y
            rx = (self.center_x - x) + (radius + 5) * cos_phi_line
            ry = (self.center_y - y) + (radius + 5) * sin_phi_line

            # Apply the rotation
            lx = rx * math.cos(ang) - ry * math.sin(ang)
            ly = rx * math.sin(ang) + ry * math.cos(ang)

            # Translate back to our original origin
            lx += x
            ly += y

            self.phi_label.pos = (lx, ly)

    def _calculate_theta(self, view_pos=None):
        """ Calculate the Theta angle and the values needed to display it. """
        if view_pos:
            vx, vy = view_pos
            dx, dy = vx - self.center_x, self.center_y - vy
            # Get the radius and the angle for Theta
            self.theta_radius = min(math.sqrt(dx * dx + dy * dy),
                                    self.inner_radius)
            self.theta = (math.pi / 2) * (self.theta_radius / self.inner_radius)
        elif self.theta:
            self.theta_radius = (self.theta / (math.pi / 2)) * self.inner_radius
        else:
            return

        # Calc Theta label pos
        x = self.center_x
        y = self.center_y + self.theta_radius + 3

        theta_str = u"θ %0.1f°" % math.degrees(self.theta)

        self.theta_label.text = theta_str
        self.theta_label.pos = (x, y)

    def _calculate_intersection(self):
        if None not in (self.phi_line_rad, self.theta_radius):
            # Calculate the intersecion between Phi and Theta
            x = self.center_x + self.theta_radius * math.cos(self.phi_line_rad)
            y = self.center_y + self.theta_radius * math.sin(self.phi_line_rad)
            self.intersection = (x, y)
        else:
            self.intersection = None

    def _calculate_display(self, view_pos=None):
        """ Calculate the values needed for plotting the Phi and Theta lines and
        labels

        If view_pos is not given, the current Phi and Theta angles will be used.
        """

        self._calculate_phi(view_pos)
        self._calculate_theta(view_pos)
        self._calculate_intersection()

        if (view_pos and
            0 < self.intersection[0] < self.cnvs.ClientSize.x and
            0 < self.intersection[1] < self.cnvs.ClientSize.y):
            # FIXME: Determine actual value here
            #self.intensity_label.text = ""
            pass

    def on_left_down(self, evt):
        self.dragging = True

    def on_left_up(self, evt):
        self.on_motion(evt)
        self.dragging = False
        self.cnvs.Refresh()

    def on_motion(self, evt):
        # Only change the values when the user is dragging
        if self.dragging:
            self._calculate_display(evt.GetPositionTuple())
            self.cnvs.Refresh()

    def on_size(self, evt=None):
        # Calculate the characteristic values
        self.center_x = self.cnvs.ClientSize.x / 2
        self.center_y = self.cnvs.ClientSize.y / 2
        self.inner_radius = min(self.center_x, self.center_y)
        self.radius = self.inner_radius + (self.ticksize / 1.5)
        self.ticks = []

        # Top middle
        for i in range(self.num_ticks):
            # phi needs to be rotated 90 degrees counter clockwise, otherwise
            # 0 degrees will be at the right side of the circle
            phi = (self.tau / self.num_ticks * i) - (math.pi / 2)
            deg = round(math.degrees(phi))

            cos = math.cos(phi)
            sin = math.sin(phi)

            # Tick start and end poiint (outer and inner)
            ox = self.center_x + self.radius * cos
            oy = self.center_y + self.radius * sin
            ix = self.center_x + (self.radius - self.ticksize) * cos
            iy = self.center_y + (self.radius - self.ticksize) * sin

            # Tick label positions
            lx = self.center_x + (self.radius + 5) * cos
            ly = self.center_y + (self.radius + 5) * sin

            label = self.add_label(
                      u"%d°" % (deg + 90),
                      (lx, ly),
                      colour=(0.8, 0.8, 0.8),
                      deg=deg - 90,
                      flip=True,
                      align=wx.ALIGN_CENTRE_HORIZONTAL|wx.ALIGN_BOTTOM)

            self.ticks.append((ox, oy, ix, iy, label))

        self._calculate_display()

    def Draw(self, ctx):
        ### Draw angle lines ###

        ctx.set_line_width(2.5)
        ctx.set_source_rgba(0, 0, 0, 0.2 if self.dragging else 0.5)

        if self.theta is not None:
            # Draw dark unerline azimuthal circle
            ctx.arc(self.center_x, self.center_y,
                    self.theta_radius, 0, self.tau)
            ctx.stroke()

        if self.phi is not None:
            # Draw dark unerline Phi line
            ctx.move_to(self.center_x, self.center_y)
            ctx.line_to(*self.phi_line_pos)
            ctx.stroke()

        # Light selection lines formatting
        ctx.set_line_width(2)
        ctx.set_dash([3,])

        if self.dragging:
            ctx.set_source_rgba(*self.colour_drag)
        else:
            ctx.set_source_rgb(*self.colour)

        if self.theta is not None:
            # Draw azimuthal circle
            ctx.arc(self.center_x, self.center_y,
                    self.theta_radius, 0, self.tau)
            ctx.stroke()

            self._write_label(ctx, self.theta_label)

        if self.phi is not None:
            # Draw Phi line
            ctx.move_to(self.center_x, self.center_y)
            ctx.line_to(*self.phi_line_pos)
            ctx.stroke()

            self._write_label(ctx, self.phi_label)

        ctx.set_dash([])

        ### Draw angle markings ###

        # Draw frame that covers everything outside the center circle
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ctx.set_source_rgb(0.2, 0.2, 0.2)

        ctx.rectangle(0, 0, self.cnvs.ClientSize.x, self.cnvs.ClientSize.y)
        ctx.arc(self.center_x, self.center_y, self.inner_radius, 0, self.tau)
        # mouse_inside = not ctx.in_fill(float(self.vx or 0), float(self.vy or 0))
        ctx.fill()

        # Draw Azimuth degree circle
        ctx.set_line_width(2)
        ctx.set_source_rgb(0.5, 0.5, 0.5)
        ctx.arc(self.center_x, self.center_y, self.radius, 0, self.tau)
        ctx.stroke()

        # Draw Azimuth degree ticks
        ctx.set_line_width(1)
        for sx, sy, lx, ly, _ in self.ticks:
            ctx.move_to(sx, sy)
            ctx.line_to(lx, ly)
        ctx.stroke()

        # Draw tick labels, ignore padding in this case
        pad, self.canvas_padding = self.canvas_padding, 0

        for _, _, _, _, label in self.ticks:
            self._write_label(ctx, label)

        self.canvas_padding = pad

        if self.intensity_label.text and self.intersection:
            ctx.set_source_rgb(*self.colour_highlight)
            ctx.arc(self.intersection[0], self.intersection[1], 3, 0, self.tau)
            ctx.fill()

            x, y = self.intersection
            y -= 18
            if y < 40:
                y += 40

            self.intensity_label.pos = (x, y)
            self._write_label(ctx, self.intensity_label)


class PointSelectOverlay(ViewOverlay):

    def __init__(self, cnvs):
        super(PointSelectOverlay, self).__init__(cnvs)
        # Prevent the cursor from resetting on clicks

        # Physical position of the last click
        self.p_pos = model.VigilantAttribute(None)

    def on_enter(self, evt):
        self.cnvs.previous_cursor = wx.CROSS_CURSOR
        self.cnvs.SetCursor(wx.CROSS_CURSOR)
        super(PointSelectOverlay, self).on_enter(evt)

    def on_leave(self, evt):
        self.cnvs.previous_cursor = None
        self.cnvs.SetCursor(wx.STANDARD_CURSOR)
        super(PointSelectOverlay, self).on_leave(evt)

    def on_left_up(self, evt):
        v_pos = evt.GetPositionTuple()
        w_pos = self.cnvs.view_to_world(v_pos, self.cnvs.get_half_view_size())
        self.p_pos.value = self.cnvs.world_to_physical_pos(w_pos)

    def Draw(self, ctx):
        pass


class HistoryOverlay(ViewOverlay):
    """ This overlay displays rectangles on locations that the microscope was previously positioned
    1at

    """

    def __init__(self, cnvs):
        super(HistoryOverlay, self).__init__(cnvs)

        self.colour = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.fade = True  # Fade older positions in the history list
        self.length = 20  # Number of positions to track
        self.history = []  # List of (center, size) tuples
        self.maker_size = 5

    def add_location(self, p_center, p_size=None):
        """ Add a view location to the history list

        :param p_center: Physical coordinates of the view center
        :param p_size: Physical size of the the view
        """
        # If the 'new' position is identical to the last one in the history, ignore
        if self.history and p_center == self.history[-1][0]:
            return

        # If max length reached, remove the oldest
        if len(self.history) == self.length:
            self.history.pop(0)

        self.history.append((p_center, p_size))

    def Draw(self, ctx):

        ctx.set_line_width(1)
        offset = self.cnvs.get_half_view_size()
        half_size = self.maker_size / 2.0

        for i, (p_topleft, _) in enumerate(self.history):
            alpha = i * (0.8 / len(self.history)) + 0.2 if self.fade else 1.0
            v_center = self.cnvs.world_to_view(self.cnvs.physical_to_world_pos(p_topleft), offset)
            ctx.set_source_rgba(self.colour[0], self.colour[1], self.colour[2], alpha)
            # Render rectangles of 3 pixels wide
            ctx.rectangle(int(v_center[0]) - half_size,
                          int(v_center[1]) - half_size,
                          self.maker_size,
                          self.maker_size)
            ctx.stroke()

