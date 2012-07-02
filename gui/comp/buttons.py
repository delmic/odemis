# This module contains various custom button classes used throughout the odemis
# project.
#
# All these classes are supported within XRCED as long as the xmlh/delmic.py
# and xmlh/xh_delmic.py modules are available (e.g. through a symbolic link)
# in XRCED's plugin directory.

import wx
from wx.lib.buttons import GenBitmapButton, GenBitmapToggleButton, \
    GenBitmapTextToggleButton

class ImageButton(GenBitmapButton):
    """ Graphical button with hover effect.

    The background colour is set to it's parent's
    """

    labelDelta = 0

    def __init__(self, *args, **kwargs):

        if kwargs.has_key('style'):
            kwargs['style'] |= wx.NO_BORDER
        else:
            kwargs['style'] = wx.NO_BORDER

        self.background_parent = None
        if kwargs.has_key('background_parent'):
            self.background_parent = kwargs['background_parent']
            del kwargs['background_parent']

        GenBitmapButton.__init__(self, *args, **kwargs)

        if self.background_parent:
            self.SetBackgroundColour(self.background_parent.GetBackgroundColour())
        else:
            self.SetBackgroundColour(self.GetParent().GetBackgroundColour())

        self.bmpHover = None
        self.hovering = False

        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

    def OnEnter(self, evt):
        if self.bmpHover:
            self.hovering = True
            self.Refresh()

    def OnLeave(self, evt):
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def SetBitmaps(self, bmp_h=None):
        """ This method sets additional bitmaps for hovering and selection """
        if bmp_h:
            self.SetBitmapHover(bmp_h)

    def GetBitmapHover(self):
        return self.bmpHover

    def SetBitmapHover(self, bitmap):
        """Set bitmap to display when the button is hovered over"""
        self.bmpHover = bitmap

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        bmp = self.bmpLabel
        if self.hovering and self.bmpHover:
            bmp = self.bmpHover
        if self.bmpDisabled and not self.IsEnabled():
            bmp = self.bmpDisabled
        if self.bmpFocus and self.hasFocus:
            bmp = self.bmpFocus
        bw, bh = bmp.GetWidth(), bmp.GetHeight()
        if not self.up:
            dx = dy = self.labelDelta

        hasMask = bmp.GetMask() != None
        dc.DrawBitmap(bmp, (width - bw) / 2 + dx, (height - bh) / 2 + dy, hasMask)

    def InitColours(self):
        GenBitmapButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()
        else:
            self.faceDnClr = self.GetParent().GetBackgroundColour()

class ImageToggleButton(GenBitmapToggleButton):  #pylint: disable=R0901
    """ This class describes an image toggle button with hover effects """

    # The displacement of the button content when it is pressed down, in pixels
    labelDelta = 0

    def __init__(self, *args, **kwargs):

        kwargs['style'] = wx.NO_BORDER

        self.background_parent = None
        if kwargs.has_key('background_parent'):
            self.background_parent = kwargs['background_parent']
            del kwargs['background_parent']

        GenBitmapToggleButton.__init__(self, *args, **kwargs)

        if self.background_parent:
            self.SetBackgroundColour(self.background_parent.GetBackgroundColour())
        else:
            self.SetBackgroundColour(self.GetParent().GetBackgroundColour())

        self.bmpHover = None
        self.bmpSelectedHover = None
        self.hovering = False

        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

    def SetBitmaps(self, bmp_h=None, bmp_sel=None, bmp_sel_h=None):
        """ This method sets additional bitmaps for hovering and selection """
        if bmp_h:
            self.SetBitmapHover(bmp_h)
        if bmp_sel:
            self.SetBitmapSelected(bmp_sel)
        if bmp_sel_h:
            self.SetBitmapSelectedHover(bmp_sel_h)

    def GetBitmapHover(self):
        return self.bmpHover

    def SetBitmapHover(self, bitmap):
        """Set bitmap to display when the button is hovered over"""
        self.bmpHover = bitmap

    def GetBitmapSelectedHover(self):
        return self.bmpSelectedHover

    def SetBitmapSelectedHover(self, bitmap):
        self.bmpSelectedHover = bitmap

    def OnEnter(self, evt):
        if self.bmpHover:
            self.hovering = True
            self.Refresh()

    def OnLeave(self, evt):
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        bmp = self.bmpLabel
        if self.hovering and self.bmpHover:
            bmp = self.bmpHover
        if self.bmpDisabled and not self.IsEnabled():
            bmp = self.bmpDisabled
        if self.bmpFocus and self.hasFocus:
            bmp = self.bmpFocus
        if self.bmpSelected and not self.up:
            if self.hovering:
                bmp = self.bmpSelectedHover
            else:
                bmp = self.bmpSelected
        bw, bh = bmp.GetWidth(), bmp.GetHeight()
        if not self.up:
            dx = dy = self.labelDelta
        hasMask = bmp.GetMask() != None
        dc.DrawBitmap(bmp, (width - bw) / 2 + dx, (height - bh) / 2 + dy, hasMask)

    def InitColours(self):
        GenBitmapButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()
        else:
            self.faceDnClr = self.GetParent().GetBackgroundColour()

class ImageTextToggleButton(GenBitmapTextToggleButton):  #pylint: disable=R0901
    # The displacement of the button content when it is pressed down, in pixels
    labelDelta = 1

    def __init__(self, *args, **kwargs):

        kwargs['style'] = wx.NO_BORDER

        self.background_parent = None
        if kwargs.has_key('background_parent'):
            self.background_parent = kwargs['background_parent']
            del kwargs['background_parent']

        GenBitmapTextToggleButton.__init__(self, *args, **kwargs)

        self.bmpHover = None
        self.bmpSelectedHover = None
        self.hovering = False

        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

    def SetBitmaps(self, bmp_h=None, bmp_sel=None, bmp_sel_h=None):
        """ This method sets additional bitmaps for hovering and selection """
        if bmp_h:
            self.SetBitmapHover(bmp_h)
        if bmp_sel:
            self.SetBitmapSelected(bmp_sel)
        if bmp_sel_h:
            self.SetBitmapSelectedHover(bmp_sel_h)

    def GetBitmapHover(self):
        return self.bmpHover

    def SetBitmapHover(self, bitmap):
        """Set bitmap to display when the button is hovered over"""
        self.bmpHover = bitmap

    def GetBitmapSelectedHover(self):
        return self.bmpSelectedHover

    def SetBitmapSelectedHover(self, bitmap):
        self.bmpSelectedHover = bitmap

    def OnEnter(self, evt):
        if self.bmpHover:
            self.hovering = True
            self.Refresh()

    def OnLeave(self, evt):
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def InitColours(self):
        GenBitmapButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        bmp = self.bmpLabel
        if bmp is not None:     # if the bitmap is used
            if self.hovering and self.bmpHover:
                bmp = self.bmpHover
            if self.bmpDisabled and not self.IsEnabled():
                bmp = self.bmpDisabled
            if self.bmpFocus and self.hasFocus:
                bmp = self.bmpFocus
            if self.bmpSelected and not self.up:
                bmp = self.bmpSelected
            bw, bh = bmp.GetWidth(), bmp.GetHeight()
            if not self.up:
                dx = dy = self.labelDelta
            hasMask = bmp.GetMask() is not None
        else:
            bw = bh = 0     # no bitmap -> size is zero

        dc.SetFont(self.GetFont())
        if self.IsEnabled():
            dc.SetTextForeground(self.GetForegroundColour())
        else:
            dc.SetTextForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        label = self.GetLabel()
        tw, th = dc.GetTextExtent(label) # size of text
        if not self.up:
            dx = dy = self.labelDelta

        pos_x = (width - bw) / 2 + dx
        if bmp is not None:
            #dc.DrawBitmap(bmp, (width - bw) / 2 + dx, (height - bh) / 2 + dy, hasMask)
            dc.DrawBitmap(bmp, (width - bw) / 2, (height - bh) / 2, hasMask)

        # The text is right aligned for now

        pos_x = pos_x + bw - tw - 8 # 5 is padding
        dc.DrawText(label, pos_x, (height - th) / 2 + dy + 1)      # draw the text

    def InitColours(self):
        GenBitmapButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()
        else:
            self.faceDnClr = self.GetParent().GetBackgroundColour()

class PopupImageButton(ImageButton):

    def __init__(self, *args, **kwargs):
        ImageButton.__init__(self, *args, **kwargs)
        self.choices = None
        self.Bind(wx.EVT_BUTTON, self.show_menu)


    def set_choices(self, choices):
        self.choices = choices

    def show_menu(self, evt):

        if not self.choices:
            return

        class MenuPopup(wx.PopupTransientWindow):
            def __init__(self, parent, style):
                wx.PopupTransientWindow.__init__(self, parent, style)
                self.lb = wx.ListBox(self, -1)

                sz = self.lb.GetBestSize()

                width = parent.GetSize().GetWidth()
                height = sz.height + 10

                #sz.width -= wx.SystemSettings_GetMetric(wx.SYS_VSCROLL_X)
                self.lb.SetBackgroundColour("#DDDDDD")
                self.lb.SetSize((width, height))
                self.SetSize((width, height - 2))

                self.Bind(wx.EVT_LISTBOX, self.on_select)

            def on_select(self, evt):
                #print self.lb.GetStringSelection()
                self.Dismiss()
                self.OnDismiss()

            def ProcessLeftDown(self, evt):
                return False

            def OnDismiss(self):
                self.GetParent().hovering = False
                self.GetParent().Refresh()

            def SetChoices(self, choices):
                self.lb.Set(choices)

        win = MenuPopup(self, wx.SIMPLE_BORDER)
        win.SetChoices(self.choices)

        # Show the popup right below or above the button
        # depending on available screen space...
        btn = evt.GetEventObject()
        pos = btn.ClientToScreen((10, -5))
        sz = btn.GetSize()
        win.Position(pos, (0, sz[1]))

        win.Popup()


