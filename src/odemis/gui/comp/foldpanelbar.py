# -*- coding: utf-8 -*-

"""

:author: Rinze de Laat
:copyright: © 2012 Rinze de Laat, Delmic

.. license::
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

from odemis.gui.img.data import getarr_rightBitmap, getarr_downBitmap
from odemis.gui.util.conversion import change_brightness, wxcol_to_frgb
import wx


CAPTION_BAR_SIZE = (-1, 40)
CAPTION_PADDING_LEFT = 10
CAPTION_PADDING_RIGHT = 6
SCROLLBAR_WIDTH = 0


wxEVT_CAPTIONBAR = wx.NewEventType()
EVT_CAPTIONBAR = wx.PyEventBinder(wxEVT_CAPTIONBAR, 0)


class FoldPanelBar(wx.Panel):
    """ This window can be be used as a vertical side bar which may contain
    foldable sub panels created from the FoldPanelItem class.

    For proper scrolling, this window should be placed inside a Sizer inside a
    wx.ScrolledWindow.

    """

    def __init__(self, parent, id= -1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.TAB_TRAVERSAL | wx.NO_BORDER):

        wx.Panel.__init__(self, parent, id, pos, size, style)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

        self.Bind(EVT_CAPTIONBAR, self.OnPressCaption)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        global SCROLLBAR_WIDTH
        SCROLLBAR_WIDTH = wx.SystemSettings_GetMetric(wx.SYS_VSCROLL_X)

        assert isinstance(parent, wx.ScrolledWindow)


    def OnPressCaption(self, evt):
        if evt.GetFoldStatus():
            evt.GetTag().Collapse()
        else:
            evt.GetTag().Expand()

    def has_vert_scrollbar(self):
        size = self.Parent.GetSize()
        vsize = self.Parent.GetVirtualSize()

        return vsize[1] > size[1]

    def has_horz_scrollbar(self):
        size = self.Parent.GetSize()
        vsize = self.Parent.GetVirtualSize()

        return vsize[0] > size[0]

    def OnSize(self, evt):
        self.SetSize(self.Parent.GetVirtualSize())
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
    """ A foldable panel which should be placed inside a
    :py:class:`FoldPanelBar` object.

    This class uses a CaptionBar object as a clickable button which allows it
    to hide and show its content.

    The main layout mechanism used is a vertical BoxSizer. The adding and
    removing of child elements should be done using the sub window mutation
    methods.

    """

    def __init__(self, parent, id= -1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.TAB_TRAVERSAL | wx.NO_BORDER, label="",
                 collapsed=False, nocaption=False):

        wx.Panel.__init__(self, parent, id, pos, size, style)

        self.grandparent = self.Parent.Parent
        assert isinstance(self.grandparent, wx.ScrolledWindow)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

        if not nocaption:
            self.caption_bar = CaptionBar(self, label, collapsed)
            self._sizer.Add(self.caption_bar,
                            flag=wx.EXPAND | wx.BOTTOM,
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

    def Show(self, show=True):
        wx.Panel.Show(self, show)
        self._refresh()

    def Hide(self):
        self.Show(False)

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
        """ Add a wx.Window or Sizer to the end of the panel """
        self._sizer.Add(item,
                        flag=wx.EXPAND | wx.BOTTOM,
                        border=1)
        self._refresh()

    def insert_item(self, item, pos):
        """ Insert a wx.Window or Sizer into the panel at location `pos` """
        self._sizer.Insert(pos + 1, item,
                           flag=wx.EXPAND | wx.BOTTOM,
                           border=1)

    def remove_item(self, item):
        """ Remove the given item from the panel """
        for child in self.GetChildren():
            if child == item:
                child.Destroy()
                self._refresh()
                return

    def remove_all(self):
        """ Remove all child windows and sizers from the panel """
        for child in self.GetChildren():
            if not isinstance(child, CaptionBar):
                child.Destroy()
        self._refresh()

    def children_to_sizer(self):
        """ Move all the children into the main sizer

        This method is used by the XRC XML handler that constructs
        :py:class:`FoldPanelItem`
        objects, so the can just add children in the XRCed program, without
        worrying or knowing about the main (private) sizer of this class.

        """
        for child in self.GetChildren():
            if not self._sizer.GetItem(child):
                self._sizer.Add(child,
                                flag=wx.EXPAND | wx.BOTTOM,
                                border=1)

        if hasattr(self, 'caption_bar') and self.caption_bar.IsCollapsed():
            self.Collapse()


class CaptionBar(wx.Window):
    """ A small button like header window that displays the
    :py:class:`FoldPanelItem`'s title and allows it to fold/unfold.

    """

    def __init__(self, parent, caption, collapsed):
        """
        :param parent: Parent window (FoldPanelItem)
        :param caption: Header caption (str)
        :param collapsed: Draw the CaptionBar collapsed or not (boolean)

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
        if hasattr(self.Parent, "grandparent"):
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

        if hasattr(self.Parent, "grandparent"):
            dc.SetTextForeground(self.parent.GetForegroundColour())
        else:
            dc.SetTextForeground(self.GetForegroundColour())
        #dc.SetTextForeground("#000000")

        y_pos = (wndRect.GetHeight() - \
                abs(caption_font.GetPixelSize().GetHeight())) / 2

        dc.DrawText(self._caption, CAPTION_PADDING_LEFT, y_pos)

        # draw small icon, either collapsed or expanded
        # based on the state of the bar. If we have any bmp's

        index = self._collapsed

        if hasattr(self.Parent, "grandparent"):
            x_pos = self.Parent.grandparent.GetSize().GetWidth() - \
                    self._iconWidth - CAPTION_PADDING_RIGHT
        else:
            x_pos = 10

        if (hasattr(self.Parent, "has_vert_scrollbar") and
            self.Parent.has_vert_scrollbar()):
            x_pos -= SCROLLBAR_WIDTH

        if hasattr(self.Parent, "grandparent"):
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

        bck_col = wxcol_to_frgb(self.parent.GetBackgroundColour())
        if self._mouse_is_over:
            col1 = change_brightness(bck_col, 0.15)
            col2 = change_brightness(bck_col, 0.10)
        else:
            col1 = change_brightness(bck_col, 0.10)
            col2 = bck_col

        r1, g1, b1 = col1
        r2, g2, b2 = col2
        rstep = (r2 - r1) / rect.height
        gstep = (g2 - g1) / rect.height
        bstep = (b2 - b1) / rect.height

        rf, gf, bf = col1
        for y in range(rect.y, rect.y + rect.height):
            currCol = (rf * 255, gf * 255, bf * 255)
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
    """ Custom event class containing extra data """

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
        """ Returns the tag assigned to the selected L{CaptionBar}. """
        return self._parent_foldbar


    def SetBar(self, foldbar):
        self._bar = foldbar

