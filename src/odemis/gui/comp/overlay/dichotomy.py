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

import cairo
import logging
from odemis.gui.util.conversion import change_brightness
import wx

import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.util.conversion as conversion


class DichotomyOverlay(base.ViewOverlay):
    """ This overlay allows the user to select a sequence of nested quadrants
    within the canvas. The quadrants are numbered 0 to 3, from the top left to
    the bottom right. The first quadrant is the biggest, with each subsequent
    quadrant being nested in the one before it.
    """

    TOP_LEFT = 0
    TOP_RIGHT = 1
    BOTTOM_LEFT = 2
    BOTTOM_RIGHT = 3

    def __init__(self, cnvs, sequence_va, colour=gui.SELECTION_COLOUR):
        """ :param sequence_va: (ListVA) VA to store the sequence in
        """
        base.ViewOverlay.__init__(self, cnvs)

        self.colour = conversion.hex_to_frgba(colour)
        # Color for quadrant that will expand the sequence
        self.hover_forw = conversion.hex_to_frgba(colour, 0.5)
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

        self.sequence_va.subscribe(self.on_sequence_change, init=True)

        # Disabling the overlay will allow the event handlers to ignore events
        self.active.value = False

    def on_sequence_change(self, seq):

        if not all(0 <= v <= 3 for v in seq):
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

    # Event Handlers

    def on_leave(self, evt):
        """ Event handler called when the mouse cursor leaves the canvas """

        if self.active.value:
            # When the mouse cursor leaves the overlay, the current top quadrant
            # should be highlighted, so clear the hover_pos attribute.
            self.hover_pos = (None, None)
            self.cnvs.Refresh()
        else:
            base.ViewOverlay.on_leave(self, evt)

    def on_motion(self, evt):
        """ Mouse motion event handler """

        if self.active.value:
            self._update_hover(evt.GetPosition())
        else:
            base.ViewOverlay.on_motion(self, evt)

    def on_left_down(self, evt):
        """ Prevent the left mouse button event from propagating when the overlay is active"""
        if not self.active.value:
            base.ViewOverlay.on_motion(self, evt)

    def on_dbl_click(self, evt):
        """ Prevent the double click event from propagating if the overlay is active"""
        if not self.active.value:
            base.ViewOverlay.on_dbl_click(self, evt)

    def on_left_up(self, evt):
        """ Mouse button handler """

        if self.active.value:
            # If the mouse cursor is over a selectable quadrant
            if None not in self.hover_pos:
                idx, quad = self.hover_pos

                # If we are hovering over the 'top' quadrant, add it to the sequence
                if len(self.sequence_va.value) == idx:
                    new_seq = self.sequence_va.value + [quad]
                    new_seq = new_seq[:self.max_len]  # cut if too long
                # Jump to the desired quadrant otherwise, cutting the sequence
                else:
                    # logging.debug("Trim")
                    new_seq = self.sequence_va.value[:idx] + [quad]
                self.sequence_va.value = new_seq

                self._update_hover(evt.GetPosition())
        else:
            base.ViewOverlay.on_leave(self, evt)

    def on_size(self, evt):
        """ Called when size of canvas changes
        """
        # Force the re-computation of rectangles
        self.on_sequence_change(self.sequence_va.value)
        base.ViewOverlay.on_size(self, evt)

    # END Event Handlers

    def _update_hover(self, pos):
        idx, quad = self.quad_hover(pos)

        # Change the cursor into a hand if the quadrant being hovered over
        # can be selected. Use the default cursor otherwise
        if idx is None:
            self.cnvs.reset_dynamic_cursor()
            idx, quad = None, None
        else:
            self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)

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
            if x <= vpos.x <= x + w:
                if y <= vpos.y <= y + h:
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
        w /= 2
        h /= 2

        # If the quadrant is in the right half, construct x by adding half the
        # width to x position of the cnvs rectangle.
        if quad in (self.TOP_RIGHT, self.BOTTOM_RIGHT):
            x += w

        # If the quadrant is in the bottom half, construct y by adding half the
        # height to the y position of the cnvs rectangle.
        if quad in (self.BOTTOM_LEFT, self.BOTTOM_RIGHT):
            y += h

        return x, y, w, h

    def draw(self, ctx):

        ctx.set_source_rgba(*self.colour)
        ctx.set_line_width(2)
        ctx.set_dash([2])
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
