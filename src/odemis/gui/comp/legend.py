#-*- coding: utf-8 -*-

"""
:author: Rinze de Laat
:copyright: © 2013 Rinze de Laat, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

import wx

from odemis.gui.comp.slider import Slider

class InfoLegend(wx.Panel):

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER):

        style = style | wx.NO_BORDER
        super(InfoLegend, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        # Merge slider
        # TODO: should be possible to use VAConnector
        self.mergeSlider = Slider(
                    self,
                    wx.ID_ANY,
                    50,
                    (0, 100),
                    size=(100, 12),
                    style=(wx.SL_HORIZONTAL |
                           wx.SL_AUTOTICKS |
                           wx.SL_TICKS |
                           wx.NO_BORDER))

        self.mergeSlider.SetBackgroundColour(parent.GetBackgroundColour())
        self.mergeSlider.SetForegroundColour("#4d4d4d")
        self.mergeSlider.SetToolTipString("Merge ratio")

class AxisLegend(wx.Panel):

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER):

        style = style | wx.NO_BORDER
        super(AxisLegend, self).__init__(parent, wid, pos, size, style)