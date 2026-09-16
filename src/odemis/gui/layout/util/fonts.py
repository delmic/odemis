# -*- coding: utf-8 -*-
"""
Copyright © 2026 Delmic

This file is part of Odemis.

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


def set_font(
    window: wx.Window,
    point_size: int,
    weight: int = wx.FONTWEIGHT_NORMAL,
) -> None:
    """Set font size and weight while preserving the native font family.

    :param window: Window whose font is updated.
    :param point_size: Font point size.
    :param weight: wx font weight.
    """
    font = window.GetFont()
    font.SetPointSize(point_size)
    font.SetWeight(weight)
    window.SetFont(font)
