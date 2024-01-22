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

import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.util.conversion as conversion

# Shape type of CenteredLineOverlay
CROSSHAIR, HORIZONTAL_LINE, VERTICAL_LINE = 0, 1, 2


class CenteredLineOverlay(base.ViewOverlay):
    """ Render a static line (horizontal, vertical, crosshair) around the center of the view """

    def __init__(self, cnvs, colour=gui.CROSSHAIR_COLOR, size=gui.CROSSHAIR_SIZE, shape=CROSSHAIR,
                 thickness=gui.CENTERED_LINE_THICKNESS):
        base.ViewOverlay.__init__(self, cnvs)

        self.colour = conversion.hex_to_frgba(colour)
        self.size = size
        self.thickness = thickness
        self.shape = shape
        if self.shape not in (CROSSHAIR, HORIZONTAL_LINE, VERTICAL_LINE):
            raise ValueError("Unknown shape input {}.".format(self.shape))

    def _draw_vertical_line(self, ctx, center, size, colour, thickness):
        """
        Draw vertical line around the center point
        """
        top = center[1] - size
        bottom = center[1] + size

        ctx.set_line_width(thickness)

        # Draw shadow
        ctx.set_source_rgba(0, 0, 0, 0.9)
        ctx.move_to(center[0] + 1.5, top + 1.5)
        ctx.line_to(center[0] + 1.5, bottom + 1.5)
        ctx.stroke()

        # Draw cross hair
        ctx.set_source_rgba(*colour)
        ctx.move_to(center[0] + 0.5, top + 0.5)
        ctx.line_to(center[0] + 0.5, bottom + 0.5)
        ctx.stroke()

    def _draw_horizontal_line(self, ctx, center, size, colour, thickness):
        """
        Draw horizontal line around the center point
        """
        left = center[0] - size
        right = center[0] + size

        ctx.set_line_width(thickness)

        # Draw shadow
        ctx.set_source_rgba(0, 0, 0, 0.9)
        ctx.move_to(left + 1.5, center[1] + 1.5)
        ctx.line_to(right + 1.5, center[1] + 1.5)
        ctx.stroke()

        # Draw cross hair
        ctx.set_source_rgba(*colour)
        ctx.move_to(left + 0.5, center[1] + 0.5)
        ctx.line_to(right + 0.5, center[1] + 0.5)
        ctx.stroke()

    def draw_crosshair(self, ctx, center, size, colour, thickness):
        """
        Draw cross hair given Cairo context and center position
        """
        self._draw_horizontal_line(ctx, center, size, colour, thickness)
        self._draw_vertical_line(ctx, center, size, colour, thickness)

    def draw(self, ctx):
        """ Draw a cross hair to the Cairo context """
        center = self.cnvs.get_half_view_size()
        if self.shape is CROSSHAIR:
            self.draw_crosshair(ctx, center, size=self.size, colour=self.colour, thickness=self.thickness)
        elif self.shape is HORIZONTAL_LINE:
            self._draw_horizontal_line(ctx, center, size=center[0], colour=self.colour, thickness=self.thickness)
        elif self.shape is VERTICAL_LINE:
            self._draw_vertical_line(ctx, center, size=center[1], colour=self.colour, thickness=self.thickness)
