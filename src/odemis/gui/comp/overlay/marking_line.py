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
import math
from odemis.gui.util.conversion import change_brightness
import wx

import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.model as model
import odemis.util.conversion as conversion
import odemis.util.units as units


class MarkingLineOverlay(base.ViewOverlay, base.DragMixin):
    """ Draw a vertical line at the given view position

    Provides a .val VA indicating the selected position by the user (using mouse).
    """

    HORIZONTAL = 1
    VERTICAL = 2

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, orientation=None, map_y_from_x=False):
        """
        map_y_from_x (bool): If True, the Y coordinate of the value will be
          based on the data, obtained via cnvs.val_x_to_val(), and .val will
          contain None as Y => 1D movement.
          If False, both X and Y will be based on the mouse position (2D movement).
        """

        base.ViewOverlay.__init__(self, cnvs)
        base.DragMixin.__init__(self)

        self.label = None
        self.colour = conversion.hex_to_frgba(colour)
        self.map_y_from_x = map_y_from_x

        # highlighted position (in the data format, but not necessarily part of the data)
        self.val = model.VigilantAttribute(None)  # tuple (X, Y) or None

        self._x_label = self.add_label("", colour=self.colour)
        self._y_label = self.add_label("", colour=self.colour, align=wx.ALIGN_BOTTOM)

        self.orientation = orientation or self.HORIZONTAL
        self.label_orientation = self.orientation

        self.line_width = 2

    @property
    def x_label(self):
        return self._x_label

    @x_label.setter
    def x_label(self, lbl):
        if self.label_orientation & self.VERTICAL:
            self._x_label.text = lbl

    @property
    def y_label(self):
        return self._y_label

    @y_label.setter
    def y_label(self, lbl):
        self._y_label.text = lbl

    def clear_labels(self):
        self.val.value = None

    def hide_x_label(self):
        self.label_orientation = self.HORIZONTAL

    # Event Handlers

    def on_left_down(self, evt):
        if self.active.value:
            base.DragMixin._on_left_down(self, evt)
            self.colour = self.colour[:3] + (0.5,)
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            base.DragMixin._on_left_up(self, evt)
            self.colour = self.colour[:3] + (1.0,)
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.left_dragging:
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_motion(self, evt)

    # END Event Handlers

    def _store_event_pos(self, evt):
        """ Position the focus line at the position of the given mouse event """
        x, y = evt.Position
        x = max(1, min(self.view_width, x))
        if self.map_y_from_x:
            # Y will be automatically mapped at drawing
            val = self.cnvs.pos_x_to_val_x(x, snap=False), None
        else:
            y = max(1, min(self.view_height, y))
            val = self.cnvs.pos_to_val((x, y))

        self.val.value = val

    def draw(self, ctx):
        val = self.val.value
        if val is not None and self.cnvs.range_x is not None and self.cnvs.range_y is not None:
            if self.map_y_from_x:
                # Maps Y and also snap X to the closest X value in the data
                val = self.cnvs.val_x_to_val(val[0])
            v_pos = self.cnvs.val_to_pos(val)

            if hasattr(self.cnvs, "display_xrange"):
                h_draw = self.cnvs.display_xrange[0] <= val[0] <= self.cnvs.display_xrange[1]
                v_draw = self.cnvs.display_yrange[0] <= val[1] <= self.cnvs.display_yrange[1]
            else:
                h_draw = True
                v_draw = True

            # If it's a big number: use at least all the characters needed to show the number (IOW, round to an int).
            # If it's a small number: show 4 significant digits.
            sig_x = max(4, math.ceil(math.log10(abs(val[0]))))
            sig_y = max(4, math.ceil(math.log10(abs(val[1]))))
            self.x_label = units.readable_str(val[0], self.cnvs.unit_x, sig_x)
            self.y_label = units.readable_str(val[1], self.cnvs.unit_y, sig_y)

            ctx.set_line_width(self.line_width)
            ctx.set_dash([3])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)

            # v_posx, v_posy = self.v_pos.value
            if self.orientation & self.VERTICAL and h_draw:
                ctx.move_to(v_pos[0], 0)
                ctx.line_to(v_pos[0], self.cnvs.ClientSize.y)
                ctx.stroke()

            if self.orientation & self.HORIZONTAL and v_draw:
                ctx.move_to(0, v_pos[1])
                ctx.line_to(self.cnvs.ClientSize.x, v_pos[1])
                ctx.stroke()

            if self.x_label.text:
                self.x_label.pos = (v_pos[0] + 5, self.cnvs.ClientSize.y)
                self.x_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

            if self.y_label.text:
                yp = max(2, v_pos[1] - 5)  # Padding from line
                # Increase bottom margin if x label is close
                label_padding = 30 if v_pos[0] < 50 else 0
                yn = min(self.view_height - label_padding, yp)
                self.y_label.pos = (3, yn)
                self.y_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

            r, g, b, a = change_brightness(self.colour, -0.2)
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.arc(v_pos[0], v_pos[1], 5.5, 0, 2 * math.pi)
            ctx.fill()
