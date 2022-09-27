#-*- coding: utf-8 -*-
"""
:author:    Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

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
import wx.lib.wxcairo as wxcairo
from odemis.gui.util.conversion import wxcol_to_frgb


class Histogram(wx.PyControl):

    def __init__(self, parent, wid=wx.ID_ANY, size=(-1, -1),
                 pos=wx.DefaultPosition, style=wx.NO_BORDER, name="Histogram"):
        style |= wx.NO_FULL_REPAINT_ON_RESIZE
        super(Histogram, self).__init__(parent, wid, pos, size, style)

        if size == (-1, -1): # wxPython follows this too much to always do it
            self.SetMinSize((-1, 40))

        self._content_buffer = None
        self.content_list = []
        self.content_color = wxcol_to_frgb(self.GetForegroundColour())

        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_PAINT, self.OnPaint)

        self.OnSize(None)

    def _draw_content(self, ctx, width, height):
        # logging.debug("Plotting content background")
        line_width = width / len(self.content_list)
        ctx.set_line_width(line_width + 0.8)
        ctx.set_source_rgb(*self.content_color)

        for i, v in enumerate(self.content_list):
            x = (i + 0.5) * line_width
            self._draw_line(ctx, x, height, x, (1 - v) * height)

    def _draw_line(self, ctx, a, b, c, d):
        ctx.move_to(a, b)
        ctx.line_to(c, d)
        ctx.stroke()

    def SetContent(self, content_list):
        self.content_list = content_list
        self.UpdateContent()
        self.Refresh()

    def OnSize(self, event=None):
        self._content_buffer = wx.Bitmap(*self.ClientSize)
        self.UpdateContent()

    def OnPaint(self, event=None):
        # self.UpdateSelection()
        wx.BufferedPaintDC(self, self._content_buffer)

    def UpdateContent(self):
        dc = wx.MemoryDC()
        dc.SelectObject(self._content_buffer)
        dc.SetBackground(wx.Brush(self.BackgroundColour, wx.BRUSHSTYLE_SOLID))
        dc.Clear() # make sure you clear the bitmap!

        if len(self.content_list): # using len to be compatible with numpy arrays
            ctx = wxcairo.ContextFromDC(dc)
            width, height = self.ClientSize
            self._draw_content(ctx, width, height)
        del dc # need to get rid of the MemoryDC before Update() is called.
        self.Refresh(eraseBackground=False)
        self.Update()

    def SetForegroundColour(self, col):  # pylint: disable=W0221
        ret = super(Histogram, self).SetForegroundColour(col)
        self.content_color = wxcol_to_frgb(self.GetForegroundColour())
        return ret
