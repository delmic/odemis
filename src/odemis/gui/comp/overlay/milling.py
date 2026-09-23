# -*- coding: utf-8 -*-
"""Milling rectangles with labels shared by a whole pattern.

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

This file is part of Odemis, licensed under the GNU General Public License v2.
See the LICENSE.txt file for details.
"""

from typing import Any, Tuple

import cairo
import wx

from odemis.gui.comp.overlay._constants import MILLING_LABEL_BACKGROUND_OPACITY
from odemis.gui.comp.overlay.rectangle import RectangleOverlay
from odemis.gui.comp.overlay.shapes import ShapesOverlay
from odemis.gui.layout import theme
from odemis.util.conversion import hex_to_frgba


class MillingShapesOverlay(ShapesOverlay):
    """Draw pattern labels at physical positions, above the milling rectangles."""

    def __init__(self, cnvs: Any) -> None:
        """Initialize an overlay for milling rectangles and shared labels.

        :param cnvs: Canvas that owns the overlay.
        """
        super().__init__(cnvs, shape_cls=RectangleOverlay)
        self._pattern_labels = []

    def add_pattern_label(self, text: str, p_pos: Tuple[float, float]) -> None:
        """Place a shared label just above the pattern's top center.

        :param text: Label text.
        :param p_pos: Label position in physical coordinates, in meters.
        """
        label = self.add_label(
            text, align=wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_BOTTOM,
            background=hex_to_frgba(theme.viewport_background, MILLING_LABEL_BACKGROUND_OPACITY))
        self._pattern_labels.append((label, p_pos))

    def clear_labels(self) -> None:
        """Remove shape labels and shared pattern labels."""
        super().clear_labels()
        self._pattern_labels.clear()

    def draw(self, ctx: cairo.Context, shift: Tuple[float, float] = (0, 0),
             scale: float = 1.0) -> None:
        """Draw all shapes and shared pattern labels.

        :param ctx: Cairo drawing context.
        :param shift: Buffer coordinate offset.
        :param scale: Canvas scale factor.
        """
        super().draw(ctx, shift, scale)
        offset = self.cnvs.get_half_buffer_size()
        for label, p_pos in self._pattern_labels:
            x, y = self.cnvs.phys_to_buffer(p_pos, offset)
            label.pos = (x, y - 8)
            label.draw(ctx)
