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
from odemis.gui import img, BG_COLOUR_MAIN
from odemis.gui.util.conversion import change_brightness, wxcol_to_frgb
import wx


CAPTION_BAR_SIZE = (-1, 40)
CAPTION_PADDING_LEFT = 10
CAPTION_PADDING_RIGHT = 6
SCROLLBAR_WIDTH = 0


wxEVT_CAPTIONBAR = wx.NewEventType()
EVT_CAPTIONBAR = wx.PyEventBinder(wxEVT_CAPTIONBAR, 0)


class FoldPanelBar(wx.Panel):
    """ This window can be be used as a vertical side bar which may contain foldable sub panels
    created using the FoldPanelItem class.

    For proper scrolling, this window should be placed inside a Sizer inside a wx.ScrolledWindow.

    """

    def __init__(self, parent, id=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.TAB_TRAVERSAL | wx.NO_BORDER):

        wx.Panel.__init__(self, parent, id, pos, size, style)
        assert isinstance(self.Parent, wx.ScrolledWindow)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

        self.Bind(EVT_CAPTIONBAR, self.on_caption_press)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        global SCROLLBAR_WIDTH
        SCROLLBAR_WIDTH = wx.SystemSettings.GetMetric(wx.SYS_VSCROLL_X)

        assert isinstance(parent, wx.ScrolledWindow)

    def on_caption_press(self, evt):
        if evt.get_fold_status():
            evt.get_tag().collapse()
        else:
            evt.get_tag().expand()

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

    def Refresh(self, *args, **kwargs):
        wx.Panel.Refresh(self, *args, **kwargs)
        # self.Parent.Layout()
        self.Parent.FitInside()


class FoldPanelItem(wx.Panel):
    """ A foldable panel which should be placed inside a
    :py:class:`FoldPanelBar` object.

    This class uses a CaptionBar object as a clickable button which allows it
    to hide and show its content.

    The main layout mechanism used is a vertical BoxSizer. The adding and
    removing of child elements should be done using the sub window mutation
    methods.

    """

    def __init__(self, parent, id=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.TAB_TRAVERSAL | wx.NO_BORDER, label="",
                 collapsed=False, nocaption=False):

        wx.Panel.__init__(self, parent, id, pos, size, style)
        assert isinstance(parent, FoldPanelBar)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(main_sizer)

        self._caption_bar = None

        if not nocaption:
            self._caption_bar = CaptionBar(self, label, collapsed)
            main_sizer.Add(self._caption_bar, flag=wx.EXPAND | wx.BOTTOM, border=1)

        self._container = wx.Panel(self)
        self._container.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self._container_sizer = wx.BoxSizer(wx.VERTICAL)
        self._container.SetSizer(self._container_sizer)

        main_sizer.Add(self._container, flag=wx.EXPAND | wx.BOTTOM, border=1)

        self.Bind(EVT_CAPTIONBAR, self.on_caption_press)

    def on_caption_press(self, evt):
        evt.set_tag(self)
        evt.Skip()

    def collapse(self):
        self._caption_bar.collapse()
        self._container.Hide()
        self.Refresh()

    def expand(self):
        self._caption_bar.expand()
        self._container.Show()
        self.Refresh()

    def Show(self, show=True):
        wx.Panel.Show(self, show)
        self.Refresh()

    def Hide(self):
        self.Show(False)

    def is_expanded(self):
        return not self._caption_bar.is_collapsed()

    def has_vert_scrollbar(self):
        return self.Parent.has_vert_scrollbar()

    def Refresh(self, *args, **kwargs):
        """ Refresh the ScrolledWindow grandparent, so it and all it's
        children will get the appropriate size
        """
        self.Parent.Refresh()

    ##############################
    # Sub window mutations
    ##############################

    def add_item(self, item):
        """ Add a wx.Window or Sizer to the end of the panel """
        if item.Parent != self._container:
            item.Reparent(self._container)
        self._container_sizer.Add(item, flag=wx.EXPAND | wx.BOTTOM, border=1)
        self.Refresh()

    def insert_item(self, item, pos):
        """ Insert a wx.Window or Sizer into the panel at location `pos` """
        if item.Parent != self._container:
            item.Reparent(self._container)
        self._container_sizer.Insert(pos, item, flag=wx.EXPAND | wx.BOTTOM, border=1)
        self.Refresh()

    def remove_item(self, item):
        """ Remove the given item from the panel """
        for child in self._container.GetChildren():
            if child == item:
                child.Destroy()
                self.Refresh()
                return

    def remove_all(self):
        """ Remove all child windows and sizers from the panel """
        for child in self._container.GetChildren():
            child.Destroy()
        self.Refresh()

    def children_to_sizer(self):
        """ Move all the children into the main sizer

        This method is used by the XRC XML handler that constructs
        :py:class:`FoldPanelItem`
        objects, so the can just add children in the XRCed program, without
        worrying or knowing about the main (private) sizer of this class.

        """
        for child in self.GetChildren():
            if (child not in (self._caption_bar, self._container) and
                    not self._container_sizer.GetItem(child)):
                self.add_item(child)

        if self._caption_bar and self._caption_bar.is_collapsed():
            self.collapse()

        self._container_sizer.Layout()


class CaptionBar(wx.Window):
    """ A small button like header window that displays the :py:class:`FoldPanelItem`'s title and
    allows it to fold and unfold.

    """

    def __init__(self, parent, caption, collapsed):
        """
        :param parent: Parent window (FoldPanelItem)
        :param caption: Header caption (str)
        :param collapsed: Draw the CaptionBar collapsed or not (boolean)

        """

        wx.Window.__init__(self, parent, wx.ID_ANY, pos=(0, 0),
                           size=CAPTION_BAR_SIZE, style=wx.NO_BORDER)

        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        # FIXME: on wx4 with GTK2, the background is always redrawn anyway,
        # which causes flickering, especially as the default background colour is
        # white. As a workaround, we set a less white background.
        self.SetBackgroundColour(BG_COLOUR_MAIN)

        self._collapsed = collapsed  # The current state of the CaptionBar
        self._caption = caption
        self._mouse_hovering = False
        self._logo = None  # wx.Bitmap or None

        # Set Icons
        self._icon_size = wx.Size(16, 16)
        self._foldIcons = wx.ImageList(self._icon_size.x, self._icon_size.y)
        bmp = img.getBitmap("icon/arr_down.png")
        self._foldIcons.Add(bmp)
        bmp = img.getBitmap("icon/arr_right.png")
        self._foldIcons.Add(bmp)

        self.Bind(wx.EVT_PAINT, self.on_paint)

        if isinstance(self.Parent, FoldPanelItem):
            self.Bind(wx.EVT_MOUSE_EVENTS, self.on_mouse_event)

    def set_caption(self, caption):
        self._caption = caption

    def set_logo(self, logo):
        """
        logo (wx.Bitmap or None): bitmap to display on the right. If None, nothing
          will be shown
        """
        self._logo = logo

    def is_collapsed(self):
        """ Returns wether the status of the bar is expanded or collapsed. """
        return self._collapsed

    def collapse(self):
        """ Set the internal state of the CaptionBar as collapsed

        :note: This does not trigger a L{CaptionBarEvent} to be sent to the parent.

        """
        self._collapsed = True
        self.redraw_icon_bitmap()

    def expand(self):
        """ Set the internal state of the CaptionBar as expanded

        :note: This does not trigger a L{CaptionBarEvent} to be sent to the parent.

        """
        self._collapsed = False
        self.redraw_icon_bitmap()

    def on_paint(self, _):
        """ Handle the ``wx.EVT_PAINT`` event for L{CaptionBar} """
        dc = wx.PaintDC(self)
        win_rect = self.GetRect()

        self._draw_gradient(dc, win_rect)

        caption_font = self.Parent.GetFont()
        dc.SetFont(caption_font)

        if isinstance(self.Parent, FoldPanelItem):
            dc.SetTextForeground(self.Parent.GetForegroundColour())
        else:
            dc.SetTextForeground(self.GetForegroundColour())

        y_pos = (win_rect.GetHeight() - abs(caption_font.GetPixelSize().GetHeight())) // 2

        dc.DrawText(self._caption, CAPTION_PADDING_LEFT, y_pos)

        if self._logo:
            dc.DrawBitmap(self._logo,
                          self.Parent.Size.x
                           -self._logo.Width - 20  # 20 = extra padding for logo
                           -self._icon_size.x - CAPTION_PADDING_RIGHT,
                          (win_rect.Height - self._logo.Height) // 2)

        # Only draw the icon if it's part of a FoldPanelItem
        if isinstance(self.Parent, FoldPanelItem):
            # draw small icon, either collapsed or expanded
            # based on the state of the bar.
            index = self._collapsed

            x_pos = (self.Parent.Size.x - self._icon_size.x - CAPTION_PADDING_RIGHT)

            self._foldIcons.Draw(
                index, dc, x_pos,
                (win_rect.GetHeight() - self._icon_size.y) // 2,
                wx.IMAGELIST_DRAW_TRANSPARENT
            )

    def _draw_gradient(self, dc, rect):
        """ Draw a vertical gradient background, using the background colour as a starting point
        """

        if rect.height < 1 or rect.width < 1:
            return

        dc.SetPen(wx.TRANSPARENT_PEN)

        # calculate gradient coefficients

        bck_col = wxcol_to_frgb(self.Parent.GetBackgroundColour())
        if self._mouse_hovering:
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
            cur_col = (rf * 255, gf * 255, bf * 255)
            dc.SetBrush(wx.Brush(cur_col, wx.BRUSHSTYLE_SOLID))
            dc.DrawRectangle(rect.x, rect.y + (y - rect.y), rect.width, rect.height)
            rf = rf + rstep
            gf = gf + gstep
            bf = bf + bstep

    def on_mouse_event(self, event):
        """ Mouse event handler """
        send_event = False

        if event.LeftDown():
            # Treat all left-clicks on the caption bar as a toggle event
            send_event = True

        elif event.LeftDClick():
            send_event = True

        elif event.Entering():
            # calculate gradient coefficients
            self._mouse_hovering = True
            self.Refresh()

        elif event.Leaving():
            self._mouse_hovering = False
            self.Refresh()

        # send the collapse, expand event to the parent

        if send_event:
            event = CaptionBarEvent(wxEVT_CAPTIONBAR)
            event.SetId(self.GetId())
            event.SetEventObject(self)
            event.set_bar(self)
            self.GetEventHandler().ProcessEvent(event)
        else:
            event.Skip()

    def redraw_icon_bitmap(self):
        """ Redraws the icons (if they exists). """

        rect = self.GetRect()

        padding_right = CAPTION_PADDING_RIGHT

        if isinstance(self.Parent, FoldPanelItem) and not self.Parent.has_vert_scrollbar():
            padding_right += SCROLLBAR_WIDTH

        x_pos = self.Parent.Parent.Size.x - self._icon_size.x - padding_right

        rect.SetX(x_pos)
        rect.SetWidth(self._icon_size.x + padding_right)
        self.RefreshRect(rect)


class CaptionBarEvent(wx.PyCommandEvent):
    """ Custom event class containing extra data """

    def __init__(self, evt_type):
        wx.PyCommandEvent.__init__(self, evt_type)
        self._bar = None
        self._parent_foldbar = None

    def get_fold_status(self):
        return not self._bar.is_collapsed()

    def get_bar(self):
        """ Returns the selected L{CaptionBar}. """
        return self._bar

    def set_tag(self, tag):
        self._parent_foldbar = tag

    def get_tag(self):
        """ Returns the tag assigned to the selected L{CaptionBar}. """
        return self._parent_foldbar

    def set_bar(self, foldbar):
        self._bar = foldbar
