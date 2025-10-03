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

import wx

import odemis.gui as gui
import odemis.util.conversion as conversion
from odemis.gui.comp.overlay.base import DragMixin, PixelDataMixin, WorldOverlay, cairo_polygon


class PixelSelectOverlay(WorldOverlay, PixelDataMixin, DragMixin):
    """ Selection overlay that allows the selection of a pixel in a data set """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)
        PixelDataMixin.__init__(self)
        DragMixin.__init__(self)

        self._selected_pixel_va = None
        self._selected_width_va = None

        self.colour = conversion.hex_to_frgba(gui.SELECTION_COLOUR, 0.5)
        self.select_color = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, 0.5)

    def connect_selection(self, selection_va, width_va):

        if self._selected_pixel_va:
            self._selected_pixel_va.unsubscribe(self._on_selection)
        if self._selected_width_va:
            self._selected_width_va.unsubscribe(self._on_width)

        self._selected_pixel_va = selection_va
        self._selected_width_va = width_va

        self._selected_pixel_va.subscribe(self._on_selection, init=True)
        self._selected_width_va.subscribe(self._on_width, init=False)

    def _on_selection(self, _):
        """ Update the overlay when it's active and the line changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_width(self, _):
        """ Update the overlay when it's active and the line width changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def _deactivate(self):
        """ Clear the hover pixel when the overlay is deactivated """
        self._pixel_pos = None
        WorldOverlay._deactivate(self)
        wx.CallAfter(self.cnvs.request_drawing_update)

    # Event handlers

    def on_leave(self, evt):

        if self.active.value:
            self._pixel_pos = None
            wx.CallAfter(self.cnvs.request_drawing_update)

        WorldOverlay.on_leave(self, evt)

    def on_motion(self, evt):
        """ Update the current mouse position """

        if self.active.value:
            v_pos = evt.Position
            PixelDataMixin._on_motion(self, evt)
            DragMixin._on_motion(self, evt)

            if self.data_properties_are_set and self.is_over_pixel_data(v_pos):
                self.cnvs.set_dynamic_cursor(wx.CROSS_CURSOR)

                # Cache the current data pixel position
                old_pixel_pos = self._pixel_pos
                self._pixel_pos = self.view_to_data_pixel(evt.Position)

                if self._pixel_pos != old_pixel_pos:
                    if self.is_over_pixel_data() and self.left_dragging:
                        self._selected_pixel_va.value = self._pixel_pos
                        logging.debug("Pixel %s selected", self._selected_pixel_va.value)
                    self.cnvs.update_drawing()
            else:
                self.cnvs.reset_dynamic_cursor()
        else:
            WorldOverlay.on_motion(self, evt)

    def on_left_down(self, evt):
        if self.active.value:
            if self.data_properties_are_set:
                DragMixin._on_left_down(self, evt)

        WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ Set the selected pixel, if a pixel position is known """

        if self.active.value:
            if self._pixel_pos and self.is_over_pixel_data():
                if self._selected_pixel_va.value != self._pixel_pos:
                    self._selected_pixel_va.value = self._pixel_pos
                    self.cnvs.update_drawing()
                    logging.debug("Pixel %s selected", self._selected_pixel_va.value)
            DragMixin._on_left_up(self, evt)

        WorldOverlay.on_left_up(self, evt)

    # END Event handlers

    def selection_points(self, point):
        """ Calculate the surounding points around the given point according to the selection width

        TODO: Duplicate code from SpectrumLineOverlay, so...

        """

        if None in point:
            return []

        if self._selected_width_va.value == 1:
            return [point]

        x, y = point
        radius = self._selected_width_va.value / 2
        w, h = self._data_resolution
        points = []

        for px in range(max(0, int(x - radius)), min(int(x + radius) + 1, w)):
            for py in range(max(0, int(y - radius)), min(int(y + radius) + 1, h)):
                if math.hypot(x - px, y - py) <= radius:
                    points.append((px, py))

        return points

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        # If a selection VA is assigned...
        if self._selected_pixel_va:
            # Draw the selected pixel (yellow)
            if self._selected_pixel_va.value not in (None, (None, None)):
                ctx.set_source_rgba(*self.select_color)
                for point in self.selection_points(self._selected_pixel_va.value):
                    corners = self.pixel_to_rect(point, scale)
                    cairo_polygon(ctx, corners)
                ctx.fill()

            # Draw the hover pixel (blue)
            if self._pixel_pos and self.is_over_pixel_data() and not self.dragging:
                ctx.set_source_rgba(*self.colour)
                for point in self.selection_points(self._pixel_pos):
                    corners = self.pixel_to_rect(point, scale)
                    cairo_polygon(ctx, corners)
                ctx.fill()
