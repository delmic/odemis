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

import wx

import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.util.conversion as conversion


class HistoryOverlay(base.ViewOverlay):
    """ Display rectangles on locations that the microscope was previously positioned at """

    def __init__(self, cnvs, history_list_va):
        base.ViewOverlay.__init__(self, cnvs)

        self.trail_colour = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.pos_colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)
        self.fade = True  # Fade older positions in the history list
        self.history = history_list_va  # ListVA  of (center, size) tuples
        self.history.subscribe(self._on_history_update)

        self._merge_ratio = None

    def __str__(self):
        return "History (%d): \n" % len(self) + "\n".join([str(h) for h in self.history.value[-5:]])

    # # Event Handlers
    #
    # def on_enter(self, evt):
    #     base.ViewOverlay.on_enter(self, evt)
    #     self.cnvs.Refresh()
    #
    # def on_leave(self, evt):
    #     base.ViewOverlay.on_leave(self, evt)
    #     self.cnvs.Refresh()
    #
    # # END Event Handlers

    # TODO: might need rate limiter (but normally stage position is changed rarely)
    # TODO: Make the update of the canvas image the responsibility of the viewport
    def _on_history_update(self, _):
        wx.CallAfter(self.cnvs.request_drawing_update)

    def draw(self, ctx, scale=None, shift=None):
        """
        scale (0<float): ratio between the canvas pixel size and the pixel size
          of the drawing area. That's a trick to allow drawing both on the
          standard view and directly onto the thumbnail.
        shift (float, float): offset to add for positioning the drawing, when
          it is scaled
        """

        ctx.set_line_width(1)
        offset = self.cnvs.get_half_buffer_size()

        for i, (p_center, p_size) in enumerate(self.history.value):
            alpha = (i + 1) * (0.8 / len(self.history.value)) + 0.2 if self.fade else 1.0
            if self._merge_ratio is not None:
                alpha *= (1 - self._merge_ratio)
            v_center = self.cnvs.phys_to_view(p_center, offset)

            if scale:
                v_center = (shift[0] + v_center[0] * scale,
                            shift[1] + v_center[1] * scale)
                marker_size = (2, 2)
            elif p_size:
                marker_size = (int(p_size[0] * self.cnvs.scale),
                               int(p_size[0] * self.cnvs.scale))

                # Prevent the marker from becoming too small
                if marker_size[0] < 2 or marker_size[1] < 2:
                    marker_size = (3, 3)
            else:
                marker_size = (5, 5)

            if i < len(self.history.value) - 1:
                colour = self.trail_colour
            else:
                colour = self.pos_colour

            self._draw_rect(ctx, v_center, marker_size, colour, alpha)

    @staticmethod
    def _draw_rect(ctx, v_center, v_size, colour, alpha):

        ctx.set_source_rgba(0, 0, 0, alpha * 0.4)

        x = int(v_center[0] - v_size[0] / 2.0) + 0.5
        y = int(v_center[1] - v_size[1] / 2.0) + 0.5

        ctx.rectangle(x + 1, y + 1, v_size[0], v_size[1])
        ctx.stroke()

        ctx.set_source_rgba(colour[0], colour[1], colour[2], alpha)

        # Render rectangles of 3 pixels wide
        ctx.rectangle(x, y, v_size[0], v_size[1])
        ctx.stroke()

    def set_merge_ratio(self, merge_ratio):
        """
        Modifies the internal attribute _merge_ratio that controls the transparency
        of the history overlay.
        """
        self._merge_ratio = merge_ratio
        wx.CallAfter(self.cnvs.request_drawing_update)
