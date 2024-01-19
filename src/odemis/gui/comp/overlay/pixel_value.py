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

import numpy
from odemis.gui.comp.overlay.base import Label, Vec, ViewOverlay
import wx

import odemis.gui as gui
import odemis.util.conversion as conversion
import odemis.util.units as units


class PixelValueOverlay(ViewOverlay):
    """ Render the raw value of a selected pixel in the spatial view """

    def __init__(self, cnvs, view):
        ViewOverlay.__init__(self, cnvs, view)

        self._v_pos = None
        self._p_pos = None  
        self.view = view
        self._raw_value = None

        self.colour = conversion.hex_to_frgba(gui.FG_COLOUR_LEGEND)
        self.background_colour = conversion.hex_to_frgba(gui.BG_COLOUR_MAIN)
        self._label = Label(
            "",
            pos=(0, 0),
            font_size=14,
            flip=True,
            align=wx.ALIGN_CENTRE_HORIZONTAL,
            colour=self.colour,
            opacity=1.0,
            deg=None,
            background=self.background_colour
        )

        self._label.text = ""

    def _activate(self):
        # Read the mouse position, so that the value under the cursor can be
        # immediately shown (instead of waiting for the user to move the mouse).
        # If the mouse is outside of the view, it's fine, it'll just show the
        # corresponding value under the mouse, even if it's not displayed.
        self._v_pos = self.cnvs.ScreenToClient(wx.GetMousePosition())
        super()._activate()

    def on_leave(self, evt):
        """ Event handler called when the mouse cursor leaves the canvas """
        if not self.active.value:
            return super(ViewOverlay, self).on_leave(evt)
        else:
            self._v_pos = None
            self._p_pos = None
            self.cnvs.Refresh()

    def on_motion(self, evt):
        """ Update the display of the raw pixel value based on the current mouse position """
        if not self.active.value:
            return super(ViewOverlay, self).on_motion(evt)

        # Whatever happens, we don't keep the event, but pass it to any other interested listener.
        evt.Skip()

        # If the canvas is being dragged, the image position cannot be directly queried,
        # and anyway the cursor is above the same pixel all the time, so no update.
        if hasattr(self.cnvs, "left_dragging") and self.cnvs.left_dragging:
            return

        self._v_pos = Vec(evt.Position)
        self.cnvs.Refresh()

    def _draw_legend(self, stream):
        """ Get the pixel coordinates and the raw pixel value given a projection """
        try:
            pixel_pos = stream.getPixelCoordinates(self._p_pos)
        except LookupError:  # No data
            return None

        if pixel_pos:
            name = stream.name.value
            raw_value = stream.getRawValue(pixel_pos)
            # In case of integers the significant number is None, no need to round the raw value
            sig = None if isinstance(raw_value, (int, numpy.integer)) else 6
            raw = units.readable_str(raw_value, sig=sig)
            # The unicode for the arrow is not available in the current Cairo version
            return u"%s (%d, %d) -> %s" % (name, pixel_pos[0], pixel_pos[1], raw)
        else:
            return None

    def draw(self, ctx):
        """ Display the stream name, pixel coordinates and pixel raw value for each projection of the spatial view """
        # get the projections of the current view
        streams = self.view.stream_tree.getProjections()

        # a rough estimation of the width and height margins in order to get the position of the pixel raw value display
        margin_w, margin_h = 5, 20

        if self._v_pos:
            self._p_pos = self.cnvs.view_to_phys(self._v_pos, self.cnvs.get_half_buffer_size())
            self._label.colour = self.colour
            self._label.align = wx.ALIGN_RIGHT
            for stream in streams:
                # For SparcARCanvas which supports .flip. Note that this is a
                # kind-of crude version of flipping. The image is actually flipped
                # on its center position, which is not the same as mirroring the
                # whole view. However, on this canvas, the canvas is always at 0,
                # with the image at the center, so this works.
                # TODO: support flip generically.
                flip = self.cnvs.flip if hasattr(self.cnvs, "flip") else 0
                if flip & wx.VERTICAL:
                    self._p_pos = self._p_pos[0], -self._p_pos[1]
                if flip & wx.HORIZONTAL:
                    self._p_pos = -self._p_pos[0], self._p_pos[1]

                view_pos = self.view_width - margin_w, self.view_height - margin_h
                self._label.pos = Vec(view_pos[0], view_pos[1])

                text = self._draw_legend(stream)
                if text is not None:
                    self._label.text = text
                    self._label.draw(ctx)

                    # the legend for the next projection is displayed above the last displayed legend
                    # and a margin_r is roughly estimated and added for a better representation
                    margin_r = 5
                    margin_h += (self._label.text_size[1] + margin_r)

            # Display physical position at the top
            pos = units.readable_str(self._p_pos, "m", sig=6)
            text = f"Position: {pos}"
            view_pos = self.view_width - margin_w, self.view_height - margin_h
            self._label.pos = Vec(view_pos[0], view_pos[1])
            self._label.text = text
            self._label.draw(ctx)
