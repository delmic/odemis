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


class PlayIconOverlay(base.ViewOverlay):
    """ Render Stream (play/pause) icons to the view """

    opacity = 0.8

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)
        self.pause = False  # if True: displayed
        self.play = 0  # opacity of the play icon
        self.colour = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, self.opacity)

    def hide_pause(self, hidden=True):
        """ Hide or show the pause icon """

        self.pause = not hidden
        if not self.pause:
            self.play = 1.0
        wx.CallAfter(self.cnvs.Refresh)

    def draw(self, ctx):
        if self.show:
            if self.pause:
                self._draw_pause(ctx)
            elif self.play:
                self._draw_play(ctx)
                if self.play > 0:
                    self.play -= 0.1  # a tenth less
                    # Force a refresh (without erase background), to cause a new draw
                    wx.CallLater(50, self.cnvs.Refresh, False)  # in 0.05 s
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
