#-*- coding: utf-8 -*-
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

# This module contains various custom button classes used throughout the odemis
# project.
#
# All these classes are supported within XRCED as long as the xmlh/delmic.py
# and xmlh/xh_delmic.py modules are available (e.g. through a symbolic link)
# in XRCED's plugin directory.

from __future__ import division
from wx.lib.buttons import GenBitmapButton, GenBitmapToggleButton, \
    GenBitmapTextToggleButton, GenBitmapTextButton
import logging
import odemis.gui.img.data as img
import wx


def resize_bmp(btn_size, bmp):
    """ Resize the bitmap image so it will match the given button size

    :param btn_size: Size tuple (w, h)
    :param bmp: Bitmap image (wx.Bitmap)
    :rtype: wx.Bitmap
    """

    if btn_size:
        btn_width, _ = btn_size
        img_width, img_height = bmp.GetSize()

        if btn_width > 0 and img_width != btn_width:
            logging.debug("Resizing button bmp from %s to %s",
                      bmp.GetSize(),
                      btn_size)
            new_img = bmp.ConvertToImage()
            return new_img.Rescale(btn_width, img_height).ConvertToBitmap()

    return bmp


def DarkenImage(anImage):
    """
    Convert the given image (in place) to a grayed-out
    version, appropriate for a 'disabled' appearance.
    """

    if anImage.HasAlpha():
        alpha = anImage.GetAlphaData()
    else:
        alpha = None

    data = [ord(d) for d in list(anImage.GetData())]

    for i in range(0, len(data), 3):
        pixel = (data[i], data[i + 1], data[i + 2])
        pixel = tuple([int(p * 0.4)  for p in pixel])
        for x in range(3):
            data[i + x] = pixel[x]
    anImage.SetData(''.join([chr(d) for d in data]))
    if alpha:
        anImage.SetAlphaData(alpha)

def SetBitmapLabel(self, bitmap, createOthers=True):
    """
    Set the bitmap to display normally.
    This is the only one that is required. If
    createOthers is True, then the other bitmaps
    will be generated on the fly.  Currently,
    only the disabled bitmap is generated.
    """
    self.bmpLabel = bitmap
    if bitmap is not None and createOthers:
        image = wx.ImageFromBitmap(bitmap)
        DarkenImage(image)
        self.SetBitmapDisabled(wx.BitmapFromImage(image))


GenBitmapButton.SetBitmapLabel = SetBitmapLabel

class ImageButton(GenBitmapButton):
    """ Graphical button with hover effect.

    The background colour is set to that of its direct parent, or to the
    background colour of an explicitly defined window called background_parent.
    """

    labelDelta = 0

    def __init__(self, *args, **kwargs):
        """ If the background_parent keyword argument is provided, it will be
        used to determine the background colour of the button. Otherwise, the
        direct parent will be used.

        :param parent: (wx.Window) parent window
        :param id: (int) button id (optional)
        :param bitmap: (wx.Bitmap) default button face. Use `SetBitmaps` to set
                the other faces (e.g. hover, active)
        :param pos: (int, int)) button position
        :param size: (int, int) button size
        :param background_parent: (wx.Window) any parent higher up in the
                hierarchy from which to pick the background colour. (optional)
        :param label_delta: (int) the number of pixels to move button text down
                and to the right when it is pressed, to create an indentation
                effect. (This is used by subclasses that allow text to be
                displayed)

        """

        if kwargs.has_key('style'):
            kwargs['style'] |= wx.NO_BORDER
        else:
            kwargs['style'] = wx.NO_BORDER

        self.background_parent = kwargs.pop('background_parent', None)
        self.labelDelta = kwargs.pop('label_delta', 0)

        # Fit the bmp if needed
        # Resizing should always be minimal, so distortion is minimum

        # If the bmp arg is provided (which is the 3rd one: parent, id, bmp)

        bmp = args[2] if len(args) >= 3 else kwargs.get('bitmap', None)
        size = args[4] if len(args) >= 5 else kwargs.get('size', None)

        if bmp:
            if size and size != (-1, -1):
                args = list(args)
                # Resize and replace original bmp
                if len(args) >= 3:
                    args[2] = resize_bmp(size, bmp)
                else:
                    kwargs['bitmap'] = resize_bmp(size, bmp)
            else:
                # Set the size of the button to match the bmp
                if len(args) >= 5:
                    args[4] = bmp.GetSize()
                else:
                    kwargs['size'] = bmp.GetSize()

        GenBitmapButton.__init__(self, *args, **kwargs)

        if self.background_parent:
            self.SetBackgroundColour(
                self.background_parent.GetBackgroundColour()
            )
        else:
            self.SetBackgroundColour(self.GetParent().GetBackgroundColour())

        self.bmpHover = None
        self.hovering = False

        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

    def SetBitmaps(self, bmp_h=None, bmp_sel=None):
        """ This method sets additional bitmaps for hovering and selection """

        if bmp_h:
            bmp_h = resize_bmp(self.GetSize(), bmp_h)
            self.SetBitmapHover(bmp_h)
        if bmp_sel:
            bmp_sel = resize_bmp(self.GetSize(), bmp_sel)
            self.SetBitmapSelected(bmp_sel)

    def SetBitmapHover(self, bitmap):
        """ Set bitmap to display when the button is hovered over"""
        self.bmpHover = bitmap

    def GetBitmapHover(self):
        """ Return the hover bitmap

        :rtype: wx.Bitmap
        """
        return self.bmpHover

    def OnEnter(self, evt):
        """ Event handler that fires when the mouse cursor enters the button """
        if self.bmpHover:
            self.hovering = True
            self.Refresh()

    def OnLeave(self, evt):
        """ Event handler that fires when the mouse cursor leaves the button """
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        """ Label drawing method called by the OnPaint event handler """

        bmp = self.bmpLabel
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

        hasMask = bmp.GetMask() != None
        dc.DrawBitmap(bmp, (width - bw) // 2, (height - bh) // 2, hasMask)

    def InitColours(self):
        """ Needed for correct background coloration """
        GenBitmapButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()
        else:
            self.faceDnClr = self.GetParent().GetBackgroundColour()

    def SetLabelDelta(self, delta):
        """ Change the label delta value

        :param delta: (int) number of pixels to move the text down and to the
            right when the button is activated.
        """
        self.labelDelta = delta

class ImageTextButton(GenBitmapTextButton):
    """ Graphical button with text and hover effect.



    The text can be align using the following styles:
    wx.ALIGN_LEFT, wx.ALIGN_CENTER, wx.ALIGN_RIGHT.

    Left alignment is the default.
    """
    # The displacement of the button content when it is pressed down, in pixels
    labelDelta = 1

    # THe x and y padding in pixels.
    padding_x = 8
    padding_y = 1

    def __init__(self, *args, **kwargs):
        """
        :param parent: (wx.Window) parent window
        :param id: (int) button id (optional)
        :param bitmap: (wx.Bitmap) default button face. Use `SetBitmaps` to set
                       the other faces (e.g. hover, active)
        :param pos: (int, int)) button position
        :param size: (int, int) button size
        :param background_parent: (wx.Window) any parent higher up in the
                hierarchy from which to pick the background colour. (optional)
        :param label_delta: (int) the number of pixels to move button text down
                and to the right when it is pressed, to create an indentation
                effect. (This is used by subclasses that allow text to be
                displayed)
        :param rescale: (bool) if set to True and the button has a size, the
                background image will be scaled to fit the button.
        """
        kwargs['style'] = kwargs.get('style', 0) | wx.NO_BORDER

        self.labelDelta = kwargs.pop('label_delta', 0)
        self.rescale = kwargs.pop('rescale', False)

        self.background_parent = kwargs.pop('background_parent', None)

        # Fit the bmp if needed
        # Resizing should always be minimal, so distortion is minimum

        # If the bmp arg is provided (which is the 3rd one: parent, id, bmp)

        bmp = args[2] if len(args) >= 3 else kwargs.get('bitmap', None)
        size = args[4] if len(args) >= 5 else kwargs.get('size', None)

        if bmp:
            if size and size != (-1, -1):
                args = list(args)
                # Resize and replace original bmp
                if len(args) >= 3:
                    args[2] = resize_bmp(size, bmp)
                else:
                    kwargs['bitmap'] = resize_bmp(size, bmp)
            else:
                # Set the size of the button to match the bmp
                if len(args) >= 5:
                    args[4] = bmp.GetSize()
                else:
                    kwargs['size'] = bmp.GetSize()

        GenBitmapTextButton.__init__(self, *args, **kwargs)

        self.bmpHover = None
        self.hovering = False

        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

    def SetBitmaps(self, bmp_h=None, bmp_sel=None):
        """ This method sets additional bitmaps for hovering and selection """
        if bmp_h:
            bmp_h = resize_bmp(self.GetSize(), bmp_h)
            self.SetBitmapHover(bmp_h)
        if bmp_sel:
            bmp_sel = resize_bmp(self.GetSize(), bmp_sel)
            self.SetBitmapSelected(bmp_sel)

    def SetBitmapHover(self, bitmap):
        """ Set bitmap to display when the button is hovered over"""
        self.bmpHover = bitmap

    def GetBitmapHover(self):
        """ Return the hover bitmap

        :rtype: wx.Bitmap
        """
        return self.bmpHover

    def SetLabel(self, label):  #pylint: disable=W0221
        GenBitmapTextButton.SetLabel(self, label)
        # FIXME: should be fixed into GenBitmapTextButton => opened ticket
        # #15032
        # http://trac.wxwidgets.org/ticket/15032
        self.Refresh() # force to redraw the image

    def OnEnter(self, evt):
        """ Event handler that fires when the mouse cursor enters the button """
        if self.bmpHover:
            self.hovering = True
            self.Refresh()

    def OnLeave(self, evt):
        """ Event handler that fires when the mouse cursor leaves the button """
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        """ Label drawing method called by the OnPaint event handler """

        bmp = self.bmpLabel

        # If one or more bitmaps are defined...
        if bmp is not None:
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
        # no bitmap -> size is zero
        else:
            bw = bh = 0

        # Determine font and font colour
        dc.SetFont(self.GetFont())
        if self.IsEnabled():
            dc.SetTextForeground(self.GetForegroundColour())
        else:
            dc.SetTextForeground(
                wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
            )

        # Get the label text
        label = self.GetLabel()

        # Determine the size of the text
        tw, th = dc.GetTextExtent(label) # size of text
        if not self.up:
            dx = dy = self.labelDelta

        # Calculate the x position for the given background bitmap
        # The bitmap will be center within the button.
        pos_x = (width - bw) // 2 + dx
        if bmp is not None:
            #Background bitmap is centered
            dc.DrawBitmap(bmp, (width - bw) // 2, (height - bh) // 2, hasMask)


        if self.HasFlag(wx.ALIGN_CENTER):
            pos_x = pos_x + (bw - tw) // 2
        elif self.HasFlag(wx.ALIGN_RIGHT):
            pos_x = pos_x + bw - tw - self.padding_x
        else:
            pos_x = pos_x + self.padding_x

        # draw the text
        dc.DrawText(label, pos_x, (height - th) // 2 + dy + self.padding_y)

    def InitColours(self):
        """ Needed for correct background coloration """
        GenBitmapTextButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()
        else:
            self.faceDnClr = self.GetParent().GetBackgroundColour()

class ImageToggleButton(GenBitmapToggleButton):  #pylint: disable=R0901
    """ Graphical toggle button with a hover effect. """

    # The displacement of the button content when it is pressed down, in pixels
    labelDelta = 0

    def __init__(self, *args, **kwargs):
        """
        :param parent: (wx.Window) parent window
        :param id: (int) button id (optional)
        :param bitmap: (wx.Bitmap) default button face. Use `SetBitmaps` to set
                the other faces (e.g. hover, active)
        :param pos: (int, int)) button position
        :param size: (int, int) button size

        :param background_parent: (wx.Window) any parent higher up in the
                hierarchy from which to pick the background colour. (optional)
        :param label_delta: (int) the number of pixels to move button text down
                and to the right when it is pressed, to create an indentation
                effect. (This is used by subclasses that allow text to be
                displayed)

        """
        kwargs['style'] = wx.NO_BORDER
        self.labelDelta = kwargs.pop('label_delta', 0)
        self.background_parent = kwargs.pop('background_parent', None)

        # Fit the bmp if needed
        # Resizing should always be minimal, so distortion is minimum

        # If the bmp arg is provided (which is the 3rd one: parent, id, bmp)

        bmp = args[2] if len(args) >= 3 else kwargs.get('bitmap', None)
        size = args[4] if len(args) >= 5 else kwargs.get('size', None)

        if bmp:
            if size and size != (-1, -1):
                args = list(args)
                # Resize and replace original bmp
                if len(args) >= 3:
                    args[2] = resize_bmp(size, bmp)
                else:
                    kwargs['bitmap'] = resize_bmp(size, bmp)
            else:
                # Set the size of the button to match the bmp
                if len(args) >= 5:
                    args[4] = bmp.GetSize()
                else:
                    kwargs['size'] = bmp.GetSize()

        GenBitmapToggleButton.__init__(self, *args, **kwargs)

        if self.background_parent:
            self.SetBackgroundColour(
                self.background_parent.GetBackgroundColour()
            )
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
            bmp_h = resize_bmp(self.GetSize(), bmp_h)
            self.SetBitmapHover(bmp_h)
        if bmp_sel:
            bmp_sel = resize_bmp(self.GetSize(), bmp_sel)
            self.SetBitmapSelected(bmp_sel)

        if bmp_sel_h:
            bmp_sel_h = resize_bmp(self.GetSize(), bmp_sel_h)
            self.SetBitmapSelectedHover(bmp_sel_h)
        elif bmp_sel:
            bmp_sel = resize_bmp(self.GetSize(), bmp_sel)
            self.SetBitmapSelectedHover(bmp_sel)

    def SetBitmapHover(self, bitmap):
        """ Set bitmap to display when the button is hovered over"""
        self.bmpHover = bitmap

    def GetBitmapHover(self):
        """ Return the hover bitmap

        :rtype: wx.Bitmap
        """
        return self.bmpHover

    def SetBitmapSelectedHover(self, bitmap):
        """ Set bitmap to display when the button is hovered over while selected
        """
        self.bmpSelectedHover = bitmap

    def GetBitmapSelectedHover(self):
        """ Return the hover-over-selected-button bitmap

        :rtype: wx.Bitmap
        """
        return self.bmpSelectedHover

    def OnEnter(self, evt):
        """ Event handler that fires when the mouse cursor enters the button """
        if self.bmpHover:
            self.hovering = True
            self.Refresh()

    def OnLeave(self, evt):
        """ Event handler that fires when the mouse cursor leaves the button """
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        """ Label drawing method called by the OnPaint event handler """

        bmp = self.bmpLabel
        if self.hovering and self.bmpHover:
            bmp = self.bmpHover
        if self.bmpDisabled and not self.IsEnabled():
            bmp = self.bmpDisabled
        if self.bmpFocus and self.hasFocus:
            bmp = self.bmpFocus
        if self.bmpSelected and not self.up:
            if self.hovering and self.bmpSelectedHover:
                bmp = self.bmpSelectedHover
            else:
                bmp = self.bmpSelected
        bw, bh = bmp.GetWidth(), bmp.GetHeight()
        if not self.up:
            dx = dy = self.labelDelta
        hasMask = bmp.GetMask() != None
        dc.DrawBitmap(bmp, (width - bw) // 2, (height - bh) // 2, hasMask)

    def InitColours(self):
        """ Needed for correct background coloration """
        GenBitmapButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()
        else:
            self.faceDnClr = self.GetParent().GetBackgroundColour()

class ImageTextToggleButton(GenBitmapTextToggleButton):
    """ Graphical toggle button with text and a hover effect. """

    # The displacement of the button content when it is pressed down, in pixels
    labelDelta = 1
    padding_x = 8
    padding_y = 1

    def __init__(self, *args, **kwargs):
        """
        :param parent: (wx.Window) parent window
        :param id: (int) button id (optional)
        :param bitmap: (wx.Bitmap) default button face. Use `SetBitmaps` to set
                the other faces (e.g. hover, active)
        :param pos: (int, int)) button position
        :param size: (int, int) button size

        :param background_parent: (wx.Window) any parent higher up in the
                hierarchy from which to pick the background colour. (optional)
        :param label_delta: (int) the number of pixels to move button text down
                and to the right when it is pressed, to create an indentation
                effect. (This is used by subclasses that allow text to be
                displayed)
        """

        kwargs['style'] = kwargs.get('style', 0) | wx.NO_BORDER

        self.labelDelta = kwargs.pop('label_delta', 0)
        self.background_parent = kwargs.pop('background_parent', None)

        # Fit the bmp if needed
        # Resizing should always be minimal, so distortion is minimum

        # If the bmp arg is provided (which is the 3rd one: parent, id, bmp)

        bmp = args[2] if len(args) >= 3 else kwargs.get('bitmap', None)
        size = args[4] if len(args) >= 5 else kwargs.get('size', None)

        if bmp:
            if size and size != (-1, -1):
                args = list(args)
                # Resize and replace original bmp
                if len(args) >= 3:
                    args[2] = resize_bmp(size, bmp)
                else:
                    kwargs['bitmap'] = resize_bmp(size, bmp)
            else:
                # Set the size of the button to match the bmp
                if len(args) >= 5:
                    args[4] = bmp.GetSize()
                else:
                    kwargs['size'] = bmp.GetSize()

        GenBitmapTextToggleButton.__init__(self, *args, **kwargs)

        self.bmpHover = None
        self.bmpSelectedHover = None
        self.hovering = False

        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

    def SetBitmaps(self, bmp_h=None, bmp_sel=None, bmp_sel_h=None):
        """ This method sets additional bitmaps for hovering and selection """
        if bmp_h:
            bmp_h = resize_bmp(self.GetSize(), bmp_h)
            self.SetBitmapHover(bmp_h)
        if bmp_sel:
            bmp_sel = resize_bmp(self.GetSize(), bmp_sel)
            self.SetBitmapSelected(bmp_sel)
        if bmp_sel_h:
            bmp_sel_h = resize_bmp(self.GetSize(), bmp_sel_h)
            self.SetBitmapSelectedHover(bmp_sel_h)

    def SetBitmapHover(self, bitmap):
        """ Set bitmap to display when the button is hovered over"""
        self.bmpHover = bitmap

    def GetBitmapHover(self):
        """ Return the hover bitmap

        :rtype: wx.Bitmap
        """
        return self.bmpHover

    def SetBitmapSelectedHover(self, bitmap):
        """ Set bitmap to display when the button is hovered over while selected
        """
        self.bmpSelectedHover = bitmap

    def GetBitmapSelectedHover(self):
        """ Return the hover-over-selected-button bitmap

        :rtype: wx.Bitmap
        """
        return self.bmpSelectedHover


    def OnEnter(self, evt):
        """ Event handler that fires when the mouse cursor enters the button """
        if self.bmpHover:
            self.hovering = True
            self.Refresh()

    def OnLeave(self, evt):
        """ Event handler that fires when the mouse cursor leaves the button """
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        """ Label drawing method called by the OnPaint event handler """

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
            dc.SetTextForeground(
                wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
            )

        label = self.GetLabel()
        tw, th = dc.GetTextExtent(label) # size of text
        if not self.up:
            dx = dy = self.labelDelta

        pos_x = (width - bw) // 2 + dx
        if bmp is not None:
            dc.DrawBitmap(bmp, (width - bw) // 2, (height - bh) // 2, hasMask)

        if self.HasFlag(wx.ALIGN_CENTER):
            pos_x = pos_x + (bw - tw) // 2
        elif self.HasFlag(wx.ALIGN_RIGHT):
            pos_x = pos_x + bw - tw - self.padding_x
        else:
            pos_x = pos_x + self.padding_x

        dc.DrawText(label, pos_x, (height - th) // 2 + dy + 1) # draw the text

    def InitColours(self):
        """ Needed for correct background coloration """
        GenBitmapButton.InitColours(self)
        if self.background_parent:
            self.faceDnClr = self.background_parent.GetBackgroundColour()
        else:
            self.faceDnClr = self.GetParent().GetBackgroundColour()

class ViewButton(ImageTextToggleButton):
    """ The ViewButton class describes a toggle button that has an image overlay
    that depicts a thumbnail view of one of the view panels.

    Since the aspect ratio of views is dynamic, the ratio of the ViewButton will
    also need to be dynamic, but it will be limited to the height only, so the
    width will remain static.

    """

    def __init__(self, *args, **kwargs):
        """
        :param parent: (wx.Window) parent window
        :param id: (int) button id (optional)
        :param bitmap: (wx.Bitmap) default button face. Use `SetBitmaps` to set
                the other faces (e.g. hover, active)
        :param pos: (int, int)) button position
        :param size: (int, int) button size

        :param background_parent: (wx.Window) any parent higher up in the
                hierarchy from which to pick the background colour. (optional)
        :param label_delta: (int) the number of pixels to move button text down
                and to the right when it is pressed, to create an indentation
                effect. (This is used by subclasses that allow text to be
                displayed)
        """
        ImageTextToggleButton.__init__(self, *args, **kwargs)

        self.overlay_bitmap = None

        # The number of pixels from the right that need to be kept clear so the
        # 'arrow pointer' is visible.
        self.pointer_offset = 16

        # The border that will be kept clear.
        self.overlay_border = 5

        self.overlay_width = None
        self.overlay_height = None

        self._calc_overlay_size()

    def _calc_overlay_size(self):
        width, height = self.GetSize()
        overlay_border_size = self.overlay_border * 2
        self.overlay_width = width - overlay_border_size - self.pointer_offset
        self.overlay_height = height - overlay_border_size

    def OnLeftDown(self, event):
        """ This event handler is fired on left mouse button events, but it
        ignores those events if the button is already active.
        """
        if not self.IsEnabled() or not self.up:
            logging.debug("ViewButton already active")
            return
        self.saveUp = self.up
        self.up = not self.up
        self.CaptureMouse()
        self.SetFocus()
        self.Refresh()

    def set_overlay(self, image):
        """ Changes the preview image of the button

        :param image: (wx.Image or None) Image to be displayed or a default
                stock image.
        """
        size_tn = self.overlay_width, self.overlay_height
        if image:
            # image doesn't have the same aspect ratio as the actual thumbnail
            # => rescale and crop on the center
            # Rescale to have the smallest axis as big as the thumbnail
            rsize = list(size_tn)
            if (size_tn[0] / image.Width) > (size_tn[1] / image.Height):
                rsize[1] = int(image.Height * (size_tn[0] / image.Width))
            else:
                rsize[0] = int(image.Width * (size_tn[1] / image.Height))
            sim = image.Scale(*rsize, quality=wx.IMAGE_QUALITY_HIGH)

            # crop to the right shape
            lt = ((size_tn[0] - sim.Width) // 2, (size_tn[1] - sim.Height) // 2)
            sim.Resize(size_tn, lt)
        else:
            # black image
            sim = wx.EmptyImage(*size_tn)

        self.overlay_bitmap = wx.BitmapFromImage(sim)
        self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        """ Draw method called by the `OnPaint` event handler """
        ImageTextToggleButton.DrawLabel(self, dc, width, height, dx, dy)

        if self.overlay_bitmap is not None:
            #logging.debug("Painting overlay")
            dc.DrawBitmap(self.overlay_bitmap,
                          self.overlay_border,
                          self.overlay_border,
                          True)


class TabButton(ImageTextToggleButton):
    """ Simple graphical tab switching button """

    def __init__(self, *args, **kwargs):
        ImageTextToggleButton.__init__(self, *args, **kwargs)

        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)

        self.fg_color_cache = "#FFFFFF"

    def highlight(self, on):
        if on:
            self.fg_color_cache = self.GetForegroundColour()
            self.SetForegroundColour("#FFFFFF")
        else:
            self.SetForegroundColour(self.fg_color_cache)

    def on_focus(self, evt):
        self.highlight(True)
        evt.Skip()

    def on_kill_focus(self, evt):
        self.highlight(False)
        evt.Skip()

    def OnLeftDown(self, event):
        """ This event handler is fired on left mouse button events, but it
        ignores those events if the button is already active.
        """
        if not self.IsEnabled() or not self.up:
            return
        self.saveUp = self.up
        self.up = not self.up
        self.CaptureMouse()
        self.SetFocus()
        self.Refresh()


class GraphicRadioButton(ImageTextToggleButton):
    """ Simple graphical button that can be used to construct radio button sets
    """

    def __init__(self, *args, **kwargs):
        self.value = kwargs.pop('value')
        if not kwargs.get('label', False):
            kwargs['label'] = u"%g" % self.value
        ImageTextToggleButton.__init__(self, *args, **kwargs)

    def OnLeftDown(self, event):
        """ This event handler is fired on left mouse button events, but it
        ignores those events if the button is already active.
        """
        if not self.IsEnabled() or not self.up:
            return
        self.saveUp = self.up
        self.up = not self.up
        self.CaptureMouse()
        self.SetFocus()
        self.Refresh()

class ColourButton(ImageButton):
    """ An ImageButton that has a single colour  background that can be altered.
    """

    # The default colour for the colour button
    DEFAULT_COLOR = (0, 0, 0)

    def __init__(self, *args, **kwargs):
        self.colour = kwargs.pop('colour', None) or self.DEFAULT_COLOR
        self.use_hover = kwargs.pop('use_hover', False)
        ImageButton.__init__(self, *args, **kwargs)
        self.set_colour(self.colour)

    def set_colour(self, colour):
        """ Change the background colour of the button.

            :param colour: (3-tuple of 0<=int<=255) RGB values
        """

        if colour:
            self.colour = colour

        BMP_EMPTY = img.getemptyBitmap()

        brush = wx.Brush(self.colour)
        pen = wx.Pen(self.colour)
        bmp = BMP_EMPTY.GetSubBitmap(
                    wx.Rect(0, 0, BMP_EMPTY.GetWidth(), BMP_EMPTY.GetHeight()))
        mdc = wx.MemoryDC()
        mdc.SelectObject(bmp)
        mdc.SetBrush(brush)
        mdc.SetPen(pen)
        mdc.DrawRectangle(4, 4, 10, 10)
        mdc.SelectObject(wx.NullBitmap)

        self.SetBitmapLabel(bmp)

        if self.use_hover:
            BMP_EMPTY_H = img.getempty_hBitmap()
            bmp = BMP_EMPTY_H.GetSubBitmap(
                    wx.Rect(0, 0, BMP_EMPTY.GetWidth(), BMP_EMPTY.GetHeight()))
            mdc = wx.MemoryDC()
            mdc.SelectObject(bmp)
            mdc.SetBrush(brush)
            mdc.SetPen(pen)
            mdc.DrawRectangle(4, 4, 10, 10)
            mdc.SelectObject(wx.NullBitmap)

            self.SetBitmaps(bmp)

        self.Refresh()

    def get_colour(self):
        """ Get the current background colour of the button

        :rtype: (string) Hex colour value
        """
        return self.colour

class PopupImageButton(ImageTextButton):
    """ This class describes a grahical button with an associated popup menu """
    def __init__(self, *args, **kwargs):
        ImageTextButton.__init__(self, *args, **kwargs)
        self.choices = {}
        self.menu = wx.Menu()
        self.Bind(wx.EVT_BUTTON, self.show_menu)

    def set_choices(self, choices):
        """ Set the choices available to the user

        :param choices: [(string, function reference),..]
        """
        for label, callback in choices.items():
            self.add_choice(label, callback)

    def add_choice(self, label, callback, check_enabled=None):
        """ Add a labeled action to the popup button.

        :param label: Name to be shown in the menu
        :param callback: Function/method to run upon selection
        :param check_enabled: Function/method that returns True if the
            menu item should be enabled.

        """

        menu_id = wx.NewId()
        menu_item = wx.MenuItem(self.menu, menu_id, label)
        self.menu.Bind(wx.EVT_MENU, self.on_action_select, id=menu_id)
        self.choices[label] = (menu_item, callback, check_enabled)
        self.menu.AppendItem(menu_item)

    def remove_choice(self, label):
        """ Remove the choice associated with the name `1abel` """
        menu_item, cb, ce = self.choices.pop(label)
        self.menu.RemoveItem(menu_item)

    def show_menu(self, evt):
        """ Show the popup menu, when there are choices available. """

        if not self.choices:
            logging.debug("*NOT* Showing PopupImageButton menu, no choices")
            return

        logging.debug("Showing PopupImageButton menu")

        for menu_item, _, check_enabled in self.choices.values():
            menu_item.Enable(check_enabled() if check_enabled else True)

        self.PopupMenu(self.menu, (0, self.GetSize().GetHeight()))

        # Force the roll-over effect to go away
        if self.bmpHover:
            self.hovering = False
            self.Refresh()

    def on_action_select(self, evt):
        """ When an action is selected, call the linked callback function """
        event_id = evt.GetId()

        for label, (menu_item, callback, _) in self.choices.items():
            if menu_item.GetId() == event_id:
                logging.debug("Performing %s callback", label)
                callback()
