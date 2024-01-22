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
import wx

import odemis.gui.comp.overlay.base as base
import odemis.util.units as units


class FocusOverlay(base.ViewOverlay):
    """ Display the focus modification indicator """

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)

        self.margin = 10
        self.line_width = 16
        self.shifts = [None, None]  # None or float (m)
        self.ppm = (5e6, 5e6)  # px/m, conversion ratio m -> px

        self.focus_label = self.add_label("", align=wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)

    def draw(self, ctx):
        # TODO: Both focuses at the same time, or 'snap' to horizontal/vertical on first motion?

        ctx.set_line_width(10)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.set_source_rgba(1.0, 1.0, 1.0, 0.8)

        x, y = self.cnvs.ClientSize

        # Horizontal
        if self.shifts[0] is not None:
            y -= self.margin + (self.line_width // 2)
            middle = x / 2

            # don't display extremely small values, which are due to accumulation
            # of floating point error
            shiftm = self.shifts[0]
            if abs(shiftm) < 1e-12:
                shiftm = 0
            shift = shiftm * self.ppm[0]
            end_x = middle + (middle * (shift / (x / 2)))
            end_x = min(max(self.margin, end_x), x - self.margin)

            ctx.move_to(middle, y)
            ctx.line_to(end_x, y)
            ctx.stroke()

            lbl = "focus %s" % units.readable_str(shiftm, 'm', 2)
            self.focus_label.text = lbl
            self.focus_label.pos = (end_x, y - 15)

        # Vertical
        if self.shifts[1] is not None:
            x -= self.margin + (self.line_width // 2)
            middle = y / 2

            # don't display extremely small values, which are due to accumulation
            # of floating point error
            shiftm = self.shifts[1]
            if abs(shiftm) < 1e-12:
                shiftm = 0
            shift = shiftm * self.ppm[1]
            end_y = middle - (middle * (shift / (y / 2)))
            end_y = min(max(self.margin, end_y), y - self.margin)

            ctx.move_to(x, middle)
            ctx.line_to(x, end_y)
            ctx.stroke()

            lbl = "focus %s" % units.readable_str(shiftm, 'm', 2)
            self.focus_label.text = lbl
            self.focus_label.pos = (x - 15, end_y)
            self.focus_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

    def add_shift(self, shift, axis):
        """ Adds a value on the given axis and updates the overlay

        shift (float): amount added to the current value (can be negative)
        axis (int): axis for which this happens

        """
        if self.shifts[axis] is None:
            self.shifts[axis] = shift
        else:
            self.shifts[axis] += shift
        self.cnvs.Refresh()

    def clear_shift(self):
        logging.debug("Clearing focus shift")
        self.shifts = [None, None]
        self.cnvs.Refresh()
