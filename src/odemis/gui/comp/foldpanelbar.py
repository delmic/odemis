# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import wx
from wx.lib.agw.aui.aui_utilities import StepColour

from odemis.gui.log import log
from odemis.gui.img.data import getarr_rightBitmap, getarr_downBitmap


CAPTION_BAR_SIZE = (-1, 40)
CAPTION_PADDING_LEFT = 10
CAPTION_PADDING_RIGHT = 10
SCROLLBAR_WIDTH = 0


wxEVT_CAPTIONBAR = wx.NewEventType()
EVT_CAPTIONBAR = wx.PyEventBinder(wxEVT_CAPTIONBAR, 0)


class FoldPanelBar(wx.Panel):
    def __init__(self, parent, id= -1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.TAB_TRAVERSAL | wx.NO_BORDER):

        wx.Panel.__init__(self, parent, id, pos, size, style)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

        self.Bind(EVT_CAPTIONBAR, self.OnPressCaption)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        global SCROLLBAR_WIDTH
        SCROLLBAR_WIDTH = wx.SystemSettings_GetMetric(wx.SYS_VSCROLL_X) - 3

        assert isinstance(parent, wx.ScrolledWindow)


    def OnPressCaption(self, evt):
        if evt.GetFoldStatus():
            evt.GetTag().Collapse()
        else:
            evt.GetTag().Expand()

    def has_vert_scrollbar(self):
        size = self.Parent.GetSize()
        vsize = self.Parent.GetVirtualSize()

        return vsize[0] < size[0]

    def has_horz_scrollbar(self):
        size = self.Parent.GetSize()
        hsize = self.Parent.GetVirtualSize()

        return hsize[1] < size[1]

    def OnSize(self, evt):

        evt.Skip()

    ##############################
    # Fold panel items mutations
    ##############################

    def add_item(self, item):
        """ Add a foldpanel item to the bar """
        assert isinstance(item, FoldPanelItem)
        self._sizer.Add(item, flag=wx.EXPAND)
        self.Parent.Layout()
        self.Parent.FitInside()

    def remove_item(self, item):
        assert isinstance(item, FoldPanelItem)

        for child in self.GetChildren():
            if child == item:
                child.Destroy()
                self.Parent.Layout()
                self.Parent.FitInside()
                return

    def create_and_add_item(self, label, collapsed):
        item = FoldPanelItem(self, label=label, collapsed=collapsed)
        self.add_item(item)
        return item


class FoldPanelItem(wx.Panel):
    def __init__(self, parent, id=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.TAB_TRAVERSAL | wx.NO_BORDER, label="",
                 collapsed=False):

        wx.Panel.__init__(self, parent, id, pos, size, style)

        self.grandparent = self.Parent.Parent
        assert isinstance(self.grandparent, wx.ScrolledWindow)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

        self.caption_bar = CaptionBar(self, label, collapsed)
        self._sizer.Add(self.caption_bar,
                        flag=wx.EXPAND|wx.BOTTOM,
                        border=1)

        self.Bind(EVT_CAPTIONBAR, self.OnPressCaption)

    def OnPressCaption(self, evt):
        evt.SetTag(self)
        evt.Skip()

    def GetCaptionBar(self):
        return self.caption_bar

    def Collapse(self):
        self.caption_bar.Collapse()
        first = True
        for child in self.GetChildren():
            if not first:
                child.Hide()
            first = False

        self._refresh()

    def Expand(self):
        self.caption_bar.Expand()
        first = True
        for child in self.GetChildren():
            if not first:
                child.Show()
            first = False

        self._refresh()

    def IsExpanded(self):
        return not self.caption_bar.IsCollapsed()

    def has_vert_scrollbar(self):
        return self.Parent.has_vert_scrollbar()

    def _refresh(self):
        """ Refresh the ScrolledWindow grandparent, so it and all it's
        children will get the appropriate size
        """
        self.grandparent.Layout()
        self.grandparent.FitInside()

    ##############################
    # Sub window mutations
    ##############################

    def add_item(self, item):
        self._sizer.Add(item,
                        flag=wx.EXPAND|wx.BOTTOM,
                        border=1)
        self._refresh()

    def insert_item(self, item, pos):
        self._sizer.Insert(pos + 1, item,
                           flag=wx.EXPAND|wx.BOTTOM,
                           border=1)

    def remove_item(self, item):
        for child in self.GetChildren():
            if child == item:
                child.Destroy()
                self._refresh()
                return

    def remove_all(self):
        for child in self.GetChildren():
            if not isinstance(child, CaptionBar):
                child.Destroy()
        self._refresh()

    def children_to_sizer(self):
        """ Move all the children into the main sizer
        """
        for child in self.GetChildren():
            if not self._sizer.GetItem(child):
                self._sizer.Add(child,
                                flag=wx.EXPAND|wx.BOTTOM,
                                border=1)

        if self.caption_bar.IsCollapsed():
            self.Collapse()


class CaptionBar(wx.Window):

    def __init__(self, parent, caption, collapsed):
        """

        """

        wx.Window.__init__(self, parent, wx.ID_ANY, pos=(0, 0),
                           size=CAPTION_BAR_SIZE, style=wx.NO_BORDER)

        self._controlCreated = False

        self.parent = parent

        self._collapsed = collapsed

        self._iconWidth, self._iconHeight = 16, 16
        self._foldIcons = wx.ImageList(self._iconWidth, self._iconHeight)

        bmp = getarr_downBitmap()
        self._foldIcons.Add(bmp)
        bmp = getarr_rightBitmap()
        self._foldIcons.Add(bmp)

        self._caption = caption

        self._controlCreated = True

        self._mouse_is_over = False

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_MOUSE_EVENTS, self.OnMouseEvent)
        # self.Bind(wx.EVT_CHAR, self.OnChar)



    def set_caption(self, caption):
        self._caption = caption

    def IsCollapsed(self):
        """ Returns wether the status of the bar is expanded or collapsed. """

        return self._collapsed

    def Collapse(self):
        """
        This sets the internal state/representation to collapsed.

        :note: This does not trigger a L{CaptionBarEvent} to be sent to the
         parent.
        """
        self._collapsed = True
        self.RedrawIconBitmap()


    def Expand(self):
        """
        This sets the internal state/representation to expanded.

        :note: This does not trigger a L{CaptionBarEvent} to be sent to the
         parent.
        """
        self._collapsed = False
        self.RedrawIconBitmap()


    def OnPaint(self, event):
        """
        Handles the ``wx.EVT_PAINT`` event for L{CaptionBar}.

        :param `event`: a `wx.PaintEvent` event to be processed.
        """

        if not self._controlCreated:
            event.Skip()
            return

        dc = wx.PaintDC(self)
        wndRect = self.GetRect()

        #self.FillCaptionBackground(dc)


        dc.SetPen(wx.TRANSPARENT_PEN)

        # draw simple rectangle
        dc.SetBrush(wx.Brush(self.parent.GetBackgroundColour(), wx.SOLID))
        dc.DrawRectangleRect(wndRect)

        self._draw_gradient(dc, wndRect)


        caption_font = self.parent.GetFont()
        dc.SetFont(caption_font)

        dc.SetTextForeground(self.parent.GetForegroundColour())
        #dc.SetTextForeground("#000000")

        y_pos = (wndRect.GetHeight() - \
                abs(caption_font.GetPixelSize().GetHeight())) / 2

        dc.DrawText(self._caption, CAPTION_PADDING_LEFT, y_pos)

        # draw small icon, either collapsed or expanded
        # based on the state of the bar. If we have any bmp's

        index = self._collapsed

        x_pos = self.Parent.grandparent.GetSize().GetWidth() - \
                self._iconWidth - CAPTION_PADDING_RIGHT

        if not self.Parent.has_vert_scrollbar():
            x_pos -= SCROLLBAR_WIDTH

        self._foldIcons.Draw(index, dc, x_pos,
                             (wndRect.GetHeight() - self._iconHeight) / 2,
                             wx.IMAGELIST_DRAW_TRANSPARENT)


    def _draw_gradient(self, dc, rect):
        """ Draw a vertical gradient background, using the background colour
        as a starting point.
        """

        if  rect.height < 1 or rect.width < 1:
            return

        dc.SetPen(wx.TRANSPARENT_PEN)

        # calculate gradient coefficients

        if self._mouse_is_over:
            col1 = StepColour(self.parent.GetBackgroundColour(), 115)
            col2 = StepColour(self.parent.GetBackgroundColour(), 110)
        else:
            col1 = StepColour(self.parent.GetBackgroundColour(), 110)
            col2 = StepColour(self.parent.GetBackgroundColour(), 100)



        r1, g1, b1 = int(col1.Red()), int(col1.Green()), int(col1.Blue())
        r2, g2, b2 = int(col2.Red()), int(col2.Green()), int(col2.Blue())

        flrect = float(rect.height)

        rstep = float((r2 - r1)) / flrect
        gstep = float((g2 - g1)) / flrect
        bstep = float((b2 - b1)) / flrect

        rf, gf, bf = 0, 0, 0

        for y in range(rect.y, rect.y + rect.height):
            currCol = (r1 + rf, g1 + gf, b1 + bf)

            dc.SetBrush(wx.Brush(currCol, wx.SOLID))
            dc.DrawRectangle(rect.x,
                             rect.y + (y - rect.y),
                             rect.width,
                             rect.height)
            rf = rf + rstep
            gf = gf + gstep
            bf = bf + bstep



    def OnMouseEvent(self, event):
        """ Mouse event handler """
        send_event = False

        if event.LeftDown():
            # Treat all left-clicks on the caption bar as a toggle event
            send_event = True

        elif event.LeftDClick():
            send_event = True

        elif event.Entering():
            # calculate gradient coefficients
            self._mouse_is_over = True
            self.Refresh()

        elif event.Leaving():
            self._mouse_is_over = False
            self.Refresh()

        # send the collapse, expand event to the parent

        if send_event:
            event = CaptionBarEvent(wxEVT_CAPTIONBAR)
            event.SetId(self.GetId())
            event.SetEventObject(self)
            event.SetBar(self)
            self.GetEventHandler().ProcessEvent(event)
        else:
            event.Skip()


    def RedrawIconBitmap(self):
        """ Redraws the icons (if they exists). """

        rect = self.GetRect()

        padding_right = CAPTION_PADDING_RIGHT

        if not self.Parent.has_vert_scrollbar():
            padding_right += SCROLLBAR_WIDTH

        x_pos = self.Parent.grandparent.GetSize().GetWidth() - \
                self._iconWidth - padding_right

        rect.SetX(x_pos)
        rect.SetWidth(self._iconWidth + padding_right)
        self.RefreshRect(rect)


class CaptionBarEvent(wx.PyCommandEvent):
    def __init__(self, evtType):
        wx.PyCommandEvent.__init__(self, evtType)


    def GetFoldStatus(self):
        return not self._bar.IsCollapsed()


    def GetBar(self):
        """ Returns the selected L{CaptionBar}. """
        return self._bar


    def SetTag(self, tag):
        self._parent_foldbar = tag


    def GetTag(self):
        """ Returnsthe tag assigned to the selected L{CaptionBar}. """
        return self._parent_foldbar


    def SetBar(self, foldbar):
        self._bar = foldbar

