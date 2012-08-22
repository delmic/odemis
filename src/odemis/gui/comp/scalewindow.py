#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 13 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import wx

import odemis.gui.units as units

#----------------------------------------------------------------------
# DC Drawing Options
#----------------------------------------------------------------------
# SM_NORMAL_DC Uses The Normal wx.PaintDC
# SM_BUFFERED_DC Uses The Double Buffered Drawing Style

SM_NORMAL_DC = 0
SM_BUFFERED_DC = 1

#----------------------------------------------------------------------
# BUFFERENDWINDOW Class
# This Class Has Been Taken From The wxPython Wiki, And Slightly
# Adapted To Fill My Needs. See:
#
# http://wiki.wxpython.org/index.cgi/DoubleBufferedDrawing
#
# For More Info About DC And Double Buffered Drawing.
#----------------------------------------------------------------------

class BufferedWindow(wx.Control):
    """
    A Buffered window class.

    To use it, subclass it and define a `Draw(DC)` method that takes a DC
    to draw to. In that method, put the code needed to draw the picture
    you want. The window will automatically be double buffered, and the
    screen will be automatically updated when a Paint event is received.

    When the drawing needs to change, you app needs to call the
    L{BufferedWindow.UpdateDrawing} method. Since the drawing is stored in a bitmap, you
    can also save the drawing to file by calling the
    `SaveToFile(self, file_name, file_type)` method.

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

        wx.Control.__init__(self, parent, id, pos=pos, size=size, style=style)
        self._bufferedstyle = bufferedstyle

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_ERASE_BACKGROUND, lambda x: None)

        # OnSize called to make sure the buffer is initialized.
        # This might result in OnSize getting called twice on some
        # platforms at initialization, but little harm done.
        # self.OnSize(None) # very annoying as it calls the methods before the init is done


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

        self.Width, self.Height = self.GetClientSizeTuple()

        # Make new off screen bitmap: this bitmap will always have the
        # current drawing in it, so it can be used to save the image to
        # a file, or whatever.

        # This seems required on MacOS, it doesn't like wx.EmptyBitmap with
        # size = (0, 0)
        # Thanks to Gerard Grazzini

        if "__WXMAC__" in wx.Platform:
            if self.Width == 0:
                self.Width = 1
            if self.Height == 0:
                self.Height = 1

        self._Buffer = wx.EmptyBitmap(self.Width, self.Height)
        self.UpdateDrawing()


    def UpdateDrawing(self):
        """
        This would get called if the drawing needed to change, for whatever reason.

        The idea here is that the drawing is based on some data generated
        elsewhere in the system. if that data changes, the drawing needs to
        be updated.
        """

        if self._bufferedstyle == SM_BUFFERED_DC:
            dc = wx.BufferedDC(wx.ClientDC(self), self._Buffer)
            self.Draw(dc)
        else:
            # update the buffer
            dc = wx.MemoryDC()
            dc.SelectObject(self._Buffer)

            self.Draw(dc)
            # update the screen
            wx.ClientDC(self).Blit(0, 0, self.Width, self.Height, dc, 0, 0)


class ScaleWindow(BufferedWindow):
    """
    Little control that display a horizontal scale for a given screen density
    """
    def __init__(self, *args, **kwargs):
        BufferedWindow.__init__(self, *args, **kwargs)
        #self.mpp = 0.00027 # a not too crazy number (my screen density)
        self.mpp = None # unknown
        self.MinSize = (120, 20) # we want at least a bit of space
        # This is called before the end of __init__()
        self.va = self.GetDefaultAttributes()
        self.nod = 4
        self.shift = 0
        self.significant = 1 # significant numbers to keep in the length


        self.gap = 3 # gap between line and text
        self.background_col = self.Parent.GetBackgroundColour()
        self.foreground_col = self.Parent.GetForegroundColour()
        self.line_wdith = 1

        # OnSize called to make sure the buffer is initialized.
        self.OnSize(None)

    def SetMPP(self, mpp):
        """
        Set the meters per pixel of the scale.
        mpp (float > 0): the mpp, or None if unknown (scale is empty)
        """
        if mpp <= 0:
            raise ZeroDivisionError()
        self.mpp = mpp
        self.UpdateDrawing()

    def GetLineWidth(self, dc):
        """
        Returns the size in pixel of the scale line and its actual size.
        The pixel size is always less than the width of the window minus margin
        minus space for 8 characters
        dc (wx.DC)
        return 2-tuple (int, float): pixel size, actual size (meter)
        """
        size = self.GetClientSize()
        maxWidth = size[0] - self.shift - dc.GetTextExtent(" 000mm")[0]
        maxWidth = max(1, maxWidth)
        maxActualWidth = maxWidth * self.mpp
        actualWidth = units.round_down_significant(maxActualWidth, self.significant)
        width = int(actualWidth / self.mpp)
        return (width, actualWidth)

    def Draw(self, dc):
        # return self.DrawGC(dc)
        nod = self.nod
        shift = self.shift # to accommodate for the pen width
        vmiddle = self.GetClientSize()[1] / 2


        self.background_col = self.Parent.GetBackgroundColour()
        self.foreground_col = self.Parent.GetForegroundColour()

        dc.SetBackgroundMode(wx.SOLID)
        dc.SetBackground(wx.Brush(self.background_col))
        dc.Clear()

        if not self.mpp: # unknown mpp => blank
            return

        dc.SetFont(self.GetFont()) # before GetLineWidth(), which needs it
        dc.SetTextForeground(self.foreground_col)
        dc.SetTextBackground(self.background_col)

        length, actual = self.GetLineWidth(dc)

        charSize = dc.GetTextExtent("M")
        height = self.gap + charSize[1] + self.nod
        main_line_y = vmiddle - (height /2) + nod

        dc.DrawText(units.to_string_si_prefix(actual) + "m",
                    0,
                    main_line_y + self.gap)

        pen = wx.Pen(self.foreground_col, self.line_wdith)
        pen.Cap = wx.CAP_PROJECTING
        dc.SetPen(pen)


        # main line
        lines = [(shift, main_line_y , shift + length, main_line_y )]
        # nods at each end
        lines += [(shift, main_line_y - nod, shift, main_line_y )]
        lines += [(shift + length, main_line_y - nod, shift + length, main_line_y )]
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
        # dc.SetBackgroundMode(wx.SOLID)
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

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: