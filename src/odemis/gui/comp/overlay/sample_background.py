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

import math
from typing import Dict, Tuple

import wx
from odemis.gui.comp.overlay.base import WorldOverlay


class SampleBackgroundOverlay(WorldOverlay):
    """
    Overlay to display the contour of samples (aka TEM grid)
    Displays a list of circles and, at low zoom levels, also show their name in the middle .
    """

    def __init__(self, cnvs, samples: Dict[str, Tuple[float, float]], sample_radius: float):
        """
        cnvs (Canvas): canvas for the overlay
        sample: Dict of sample name -> center position (X/Y in m)
        sample_radius: the radius of a sample in m (all samples are shown with the same radius)
        """
        super().__init__(cnvs)
        self._colour = (0.0, 0.42, 0.8, 1.0)  # Blue
        self._samples = samples
        self._radius = sample_radius
        self._labels = {}
        for name in self._samples:
            self._labels[name] = self.add_label(name,
                                                align = wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL,
                                                colour=self._colour[:3],  # no transparency, to support .opacity
                                                background=(0, 0, 0, 0.5)  # Black semi-transparent
                                                )

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw the background image by displaying all circles
        :param ctx: cairo context from the canvas
        :param shift (float, float): physical coordinates of the center of the canvas buffer
        :param scale (float > 0): the ratio between the size of a feature in pixels and its actual size, in px/m
        """
        if not self.show:
            return

        # If the FoV is bigger than the diameter, the user is probably not looking at
        # specific image, and more looking at the broad view => show the name
        # Make it about the same size as the diameter (so dependent on the zoom).
        # If it's too small, no need to show.
        # To smoothen the transition shown/not shown use transparency between FoV = diameter (full transparent)
        # to FoV = 4* diameter (full opaque)
        fov_px = self.cnvs.view_width  # px
        radius_px = self._radius * scale
        label_size = radius_px / 2  # height (approximatively in px) => a quarter of the diameter
        min_opaq = radius_px * 2  # fully transparent => 0
        max_opaq = radius_px * 2 * 8  # fully opaque => 1
        label_opacity = min(max(0, (fov_px - min_opaq) / (max_opaq - min_opaq)), 1)

        # Draw the circles (and update the label info)
        for name, center in self._samples.items():
            label = self._labels[name]
            offset = self.cnvs.get_half_buffer_size()
            b_center = self.cnvs.phys_to_buffer((center[0], center[1]), offset)
            label.pos = b_center
            label.font_size = label_size
            label.opacity = label_opacity

            ctx.set_source_rgba(*self._colour)
            ctx.set_line_width(2)  # px
            ctx.new_sub_path()  # avoid connecting the (unknown) current point to the beginning of the circle
            ctx.arc(b_center[0], b_center[1], radius_px, 0, 2 * math.pi)
            ctx.stroke()

        # Show the labels, if it's useful: not too zoomed in, nor too zoomed out
        if self.cnvs.view_width > radius_px * 2 and label_size >= 4:
            self._write_labels(ctx)
