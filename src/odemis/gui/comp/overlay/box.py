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

import cairo
import odemis.util.conversion as conversion
import wx
from odemis import util
from odemis.gui.comp.overlay.base import WorldOverlay


class BoxOverlay(WorldOverlay):
    """
    Overlay showing a rectangle from the center of the view.
    Currently used only for the scan stage limits
    """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)

        # tlbr points compared to the center
        self.roi = None  #
        self.set_dimensions((-50e-6, -50e-6, 50e-6, 50e-6))  # m

        self.colour = conversion.hex_to_frgb("#FF0000")
        self.line_width = 1  # px
        self.dash_pattern = [2]

    def set_dimensions(self, roi):
        """ Set the dimensions of the rectangle """
        # Connect the provided VA to the overlay
        self.roi = util.normalize_rect(roi)
        wx.CallAfter(self.cnvs.request_drawing_update)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the selection as a rectangle and the repetition inside of that """

        # To make sure the line is drawn on a full pixel
        if self.line_width % 2:
            shift = 0.5
        else:
            shift = 0

        offset = self.cnvs.get_half_buffer_size()
        # Convert from abs to relative, as the ROI is from the center of the view
        cpos = self.cnvs.phys_to_view((0, 0))
        v_roi = (self.cnvs.phys_to_view(self.roi[:2]) +
                 self.cnvs.phys_to_view(self.roi[2:4]))
        v_width = v_roi[2] - v_roi[0], v_roi[3] - v_roi[1]
        rect = (offset[0] + (v_roi[0] - cpos[0]) + shift,
                offset[1] + (v_roi[1] - cpos[1]) + shift,
                v_width[0], v_width[1])

        # draws a light black background for the rectangle
        ctx.set_line_width(self.line_width + 1)
        ctx.set_source_rgba(0, 0, 0, 0.5)
        ctx.rectangle(*rect)
        ctx.stroke()

        # draws the dotted line
        ctx.set_line_width(self.line_width)
        ctx.set_dash(self.dash_pattern)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.set_source_rgba(*self.colour)
        ctx.rectangle(*rect)
        ctx.stroke()
