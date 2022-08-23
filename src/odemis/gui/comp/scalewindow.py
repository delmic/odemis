# -*- coding: utf-8 -*-
"""
Created on 13 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
from odemis.util import units
import wx


#----------------------------------------------------------------------
# DC Drawing Options
#----------------------------------------------------------------------
SM_NORMAL_DC = 0 # Uses The Normal wx.PaintDC
SM_BUFFERED_DC = 1 # Uses The Double Buffered Drawing Style

#----------------------------------------------------------------------
# BufferedWindow Class
# This Class Has Been Taken From The wxPython Wiki, And Slightly
# Adapted To Fill My Needs. See:
# http://wiki.wxpython.org/DoubleBufferedDrawing
# For More Info About DC And Double Buffered Drawing.
#----------------------------------------------------------------------

class BufferedWindow(wx.Control):
    """
    A Buffered window class.

    To use it, subclass it and define a `Draw(DC)` method that takes a DC
    to draw to. In that method, put the code needed to draw the picture
    you want. The window will automatically be double buffered, and the
    screen will be automatically updated when a Paint event is received.

    When the drawing needs to change, your app needs to call the
    BufferedWindow.update_drawing() method.

    This is a wx.Control for the main reason that it gets the right colour
    and font.
    """

    def __init__(self, parent, id=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.NO_FULL_REPAINT_ON_RESIZE, bufferedstyle=SM_BUFFERED_DC):
        """
        Default class constructor.

        :param `parent`: parent window. Must not be ``None``;
        :param `id`: window identifier. A value of -1 indicates a default value;
        :param `pos`: the control position. A value of (-1, -1) indicates a default position,
         chosen by either the windowing system or wxPython, depending on platform;
        :param `size`: the control size. A value of (-1, -1) indicates a default size,
         chosen by either the windowing system or wxPython, depending on platform;
        :param `style`: the window style;
        :param `bufferedstyle`: if set to ``SM_BUFFERED_DC``, double-buffering will
         be used.
        """
        style |= wx.NO_BORDER

        wx.Control.__init__(self, parent, id, pos=pos, size=size, style=style)
        self._bufferedstyle = bufferedstyle

        # Initialise the buffer to "something". It will be updated as soon as
        # OnSize is called
        self._Buffer = wx.Bitmap(1, 1)

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_ERASE_BACKGROUND, lambda x: None)

    def Draw(self, dc):
        """
        This method should be overridden when sub-classed.

        :param `dc`: an instance of `wx.DC`.
        """
        pass


    def OnPaint(self, event):
        """
        Handles the ``wx.EVT_PAINT`` event for L{BufferedWindow}.

        :param `event`: a `wx.PaintEvent` event to be processed.
        """

        if self._bufferedstyle == SM_BUFFERED_DC:
            dc = wx.BufferedPaintDC(self, self._Buffer)
        else:
            dc = wx.PaintDC(self)
            dc.DrawBitmap(self._Buffer, 0, 0)


    def OnSize(self, event):
        """
        Handles the ``wx.EVT_SIZE`` event for L{BufferedWindow}.

        :param `event`: a `wx.SizeEvent` event to be processed.
        """
        Width, Height = self.GetClientSize()
        Width = max(Width, 1)
        Height = max(Height, 1)

        # Make new off screen bitmap: this bitmap will always have the
        # current drawing in it, so it can be used to save the image to
        # a file, or whatever.
        self._Buffer = wx.Bitmap(Width, Height)
        self.update_drawing()

    def update_drawing(self):
        """
        This would get called if the drawing needed to change, for whatever reason.

        The idea here is that the drawing is based on some data generated
        elsewhere in the system. if that data changes, the drawing needs to
        be updated.
        """
        dc = wx.MemoryDC(self._Buffer)
        self.Draw(dc)
        del dc # need to get rid of the MemoryDC before Update() is called.
        self.Refresh(eraseBackground=False)
        self.Update()


class ScaleWindow(BufferedWindow):
    """
    Little control that display a horizontal scale for a given screen density
    """
    def __init__(self, *args, **kwargs):
        BufferedWindow.__init__(self, *args, **kwargs)
        self.mpp = None  # unknown
        self.nod = 4  # height of the nods (the end of the scale)
        self.significant = 1  # significant numbers to keep in the length

        self.gap = 3  # gap between line and text
        self.line_width = 1

        # we want at least a bit of space for the text + line
        self.MinSize = (40, 13 + self.gap + self.nod)

    def SetMPP(self, mpp):
        """
        Set the meters per pixel of the scale.
        mpp (float > 0): the mpp, or None if unknown (scale is empty)
        """
        if mpp <= 0:
            raise ZeroDivisionError()
        self.mpp = mpp
        self.update_drawing()

    def GetLineWidth(self, dc):
        """
        Returns the size in pixel of the scale line and its actual size.
        The pixel size is always less than the width of the window minus margin
        dc (wx.DC)
        return 2-tuple (int, float): pixel size, actual size (meter)
        """
        size = self.GetClientSize()
        maxWidth = max(5, size[0] - 3)
        maxActualWidth = maxWidth * self.mpp
        actualWidth = units.round_down_significant(maxActualWidth, self.significant)
        width = int(round(actualWidth / self.mpp))
        return width, actualWidth

    def Draw(self, dc):
        # return self.DrawGC(dc)
        nod = self.nod
        vmiddle = self.GetClientSize()[1] // 2

        background_col = self.Parent.GetBackgroundColour()
        foreground_col = self.Parent.GetForegroundColour()

        dc.SetBackgroundMode(wx.BRUSHSTYLE_SOLID)
        dc.SetBackground(wx.Brush(background_col))
        dc.Clear()

        if not self.mpp: # unknown mpp => blank
            return

        dc.SetFont(self.GetFont())
        dc.SetTextForeground(foreground_col)
        dc.SetTextBackground(background_col)

        length, actual = self.GetLineWidth(dc)

        # Draw the text below
        charSize = dc.GetTextExtent("M")
        height = self.gap + charSize[1] + self.nod
        main_line_y = vmiddle - (height // 2) + self.nod

        dc.DrawText(units.readable_str(actual, "m", sig=2),
                    0, main_line_y + self.gap)

        # Draw the scale itself
        pen = wx.Pen(foreground_col, self.line_width)
        pen.Cap = wx.CAP_PROJECTING # how to draw the border of the lines
        dc.SetPen(pen)

        # main line
        lines = [(0, main_line_y, length, main_line_y)]
        # nods at each end
        lines += [(0, main_line_y - nod, 0, main_line_y)]
        lines += [(length, main_line_y - nod, length, main_line_y)]
        dc.DrawLineList(lines)

    def DrawGC(self, dc):
        """
        same as Draw(), but using GraphicsContext, (i.e. HW accelerated and antialiased)
        Experimental!
        """

        raise NotImplementedError()

        # margin = 5
        # nod = 3
        # vmiddle = self.Height / 2
        # # not sure how to do this with GC
        # dc.SetBackgroundMode(wx.BRUSHSTYLE_SOLID)
        # dc.SetBackground(wx.Brush(self.va.colBg))
        # dc.Clear()
        # #gc.Clear(self.va.colBg) # doesn't actual exist


        # #gr = wx.GraphicsRenderer.GetDefaultRenderer()
        # #gc = gr.CreateContext(dc)
        # gc = wx.GraphicsContext.Create(dc)


        # pen = gc.CreatePen(wx.Pen(wx.BLACK, 2))
        # gc.SetPen(pen)

        # gc.DrawLines([(margin, vmiddle), (self.Width - margin, vmiddle)])
        # gc.DrawLines([(margin, vmiddle - nod), (margin, vmiddle + nod)])
        # gc.DrawLines([(self.Width - margin, vmiddle - nod), (self.Width - margin, vmiddle + nod)])
        # # could use strokelines

