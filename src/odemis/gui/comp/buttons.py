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

# This module contains various custom button classes used throughout the odemis
# project.
#
# All these classes are supported within XRCED as long as the xmlh/delmic.py
# and xmlh/xh_delmic.py modules are available (e.g. through a symbolic link)
# in XRCED's plugin directory.

from __future__ import division

import logging
from wx.lib import imageutils
from odemis.gui import FG_COLOUR_HIGHLIGHT
from odemis.gui.img import data as imgdata
from odemis.gui.util import img
import wx
from wx.lib.buttons import GenBitmapButton, GenBitmapToggleButton, GenBitmapTextToggleButton, \
    GenBitmapTextButton, __ToggleMixin

import odemis.gui.img.data as imgdata


def resize_bmp(btn_size, bmp):
    """ Resize the bitmap image so it will match the given button size

    :param btn_size: Size tuple (w, h)
    :param bmp: Bitmap image (wx.Bitmap)
    :rtype: wx.Bitmap
    """

    if btn_size:
        btn_width, _ = btn_size
        img_width, img_height = bmp.GetSize()

        if 0 < btn_width != img_width:
            logging.debug("Resizing button bmp from %s to %s", bmp.GetSize(), btn_size)
            new_img = bmp.ConvertToImage()
            return new_img.Rescale(btn_width, img_height).ConvertToBitmap()

    return bmp


def darken_image(image, mltp=0.5):
    """  Darken the given image

    The image is darkened (in place) to a grayed-out version, appropriate for a 'disabled'
    appearance.

    """

    if image.HasAlpha():
        alpha = image.GetAlphaData()
    else:
        alpha = None

    data = [ord(d) for d in list(image.GetData())]

    for i in range(0, len(data), 3):
        pixel = (data[i], data[i + 1], data[i + 2])
        pixel = tuple([int(p * mltp) for p in pixel])
        for x in range(3):
            data[i + x] = pixel[x]

    image.SetData(''.join([chr(d) for d in data]))

    if alpha:
        image.SetAlphaData(alpha)


class BtnMixin(object):

    labelDelta = 1
    padding_x = 8
    padding_y = 1

    btns = {
        16: {
            'on': imgdata.getbtn_16Bitmap,
            'off': imgdata.getbtn_16_aBitmap,
        },
        24: {
            'on': imgdata.getbtn_24Bitmap,
            'off': imgdata.getbtn_24_aBitmap,
        },
        32: {
            'on': imgdata.getbtn_32Bitmap,
            'off': imgdata.getbtn_32_aBitmap,
        },
        48: {
            'on': imgdata.getbtn_48Bitmap,
            'off': imgdata.getbtn_48_aBitmap,
        },
    }

    def __init__(self, *args, **kwargs):
        self.height = kwargs.pop('height', 32)
        kwargs['style'] = kwargs.get('style', 0) | wx.NO_BORDER | wx.BU_EXACTFIT
        kwargs['bitmap'] = None
        self.icon = kwargs.pop('icon', None)

        super(BtnMixin, self).__init__(*args, **kwargs)

        self.bmpHover = None
        self.hovering = None
        self.previous_size = (0, 0)

    def SetIcon(self, icon):
        self.icon = icon
        self.SetBestSize(self.DoGetBestSize())
        self.Refresh()

    def OnSize(self, evt):
        if self.Size != self.previous_size:
            self._assign_bitmaps()
            self.previous_size = self.Size

    def _GetLabelSize(self):
        """ used internally """

        width = self.padding_x * 2
        height = self.height - 2

        label = self.GetLabel()

        if label:
            width += self.GetTextExtent(label)[0]

        if self.icon:
            width += (self.padding_x if label else 0) + self.icon.GetWidth()

        return width, height, True if label else False

    @staticmethod
    def _create_bitmap(bmp, size, bg_color):
        btn_width, btn_height = size

        new_img = bmp.ConvertToImage()
        l = new_img.GetSubImage((0, 0, 3, btn_height)).ConvertToBitmap()
        m = new_img.GetSubImage((3, 0, 3, btn_height)).Rescale(btn_width - 6,
                                                               btn_height).ConvertToBitmap()
        r = new_img.GetSubImage((6, 0, 3, btn_height)).ConvertToBitmap()

        src_dc = wx.MemoryDC()
        src_dc.SelectObjectAsSource(bmp)

        dst_bmp = wx.EmptyBitmap(btn_width, 48)
        dst_dc = wx.MemoryDC()
        dst_dc.SelectObject(dst_bmp)
        dst_dc.SetBackground(wx.Brush(bg_color))
        dst_dc.Clear()

        dst_dc.DrawBitmap(l, 0, 0, True)
        dst_dc.DrawBitmap(m, 3, 0, True)
        dst_dc.DrawBitmap(r, btn_width - 3, 0)

        return dst_bmp

    def _assign_bitmaps(self):
        bg_color = self.Parent.GetBackgroundColour()
        size = (self.Size.x, self.height)

        self.bmpLabel = self._create_bitmap(self.btns[self.height]['on'](), size, bg_color)

        image = imgdata.getbtn_48Image()
        darken_image(image, 1.2)
        self.bmpHover = self._create_bitmap(wx.BitmapFromImage(image), size, bg_color)

        image = imgdata.getbtn_48Image()
        darken_image(image)
        self.bmpDisabled = self._create_bitmap(wx.BitmapFromImage(image), size, bg_color)

        self.bmpSelected = self._create_bitmap(self.btns[self.height]['off'](), size, bg_color)

    def InitOtherEvents(self):
        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

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

        bmp = self.bmpLabel

        if self.bmpDisabled and not self.IsEnabled():
            bmp = self.bmpDisabled
        if self.bmpFocus and self.hasFocus:
            bmp = self.bmpFocus
        if self.bmpSelected and not self.up:
            bmp = self.bmpSelected

        dc.DrawBitmap(bmp, 0, 0)

        self.DrawIco(dc, width, height)
        self.DrawText(dc, width, height)

    def DrawIco(self, dc, width, height, dx=0, dy=0):
        if self.icon:
            bw, bh = self.Size
            if not self.up:
                dx = dy = self.labelDelta
            pos_x = (width - bw) // 2 + dx
            pos_x += self.icon.GetWidth() + self.padding_x
            pos_y = (height // 2) - (self.icon.GetHeight() // 2) - 2
            dc.DrawBitmap(self.icon, self.padding_x + dx, pos_y + dy)

    def DrawText(self, dc, width, height, dx=0, dy=0):
        # Determine font and font colour
        dc.SetFont(self.GetFont())

        if self.IsEnabled():
            dc.SetTextForeground("#d4d4d4")
        else:
            dc.SetTextForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        # Get the label text
        label = self.GetLabel()

        if label:
            bw, bh = self.Size

            # Determine the size of the text
            tw, th = dc.GetTextExtent(label)  # size of text

            if not self.up:
                dx = dy = self.labelDelta

            # Calculate the x position for the given background bitmap
            # The bitmap will be center within the button.
            pos_x = (width - bw) // 2 + dx

            if self.icon:
                pos_x += self.icon.GetWidth() + self.padding_x

            if self.HasFlag(wx.ALIGN_CENTER):
                pos_x += (bw - tw) // 2
            elif self.HasFlag(wx.ALIGN_RIGHT):
                pos_x += bw - tw - self.padding_x
            else:
                pos_x += self.padding_x

            # draw the text
            dc.DrawText(label, pos_x, (height - th) // 2 + dy - 2)


class NImageButton(BtnMixin, GenBitmapButton):
    pass


class NImageToggleButton(BtnMixin, GenBitmapTextToggleButton):
    pass


class NImageTextButton(BtnMixin, GenBitmapTextButton):
   pass


class NImageTextToggleButton(BtnMixin, GenBitmapTextToggleButton):
   pass


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

    def SetLabel(self, label):
        GenBitmapTextButton.SetLabel(self, label)
        # FIXME: should be fixed into GenBitmapTextButton => opened ticket
        # #15032
        # http://trac.wxwidgets.org/ticket/15032
        self.Refresh()  # force to redraw the image

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
        tw, th = dc.GetTextExtent(label)  # size of text
        if not self.up:
            dx = dy = self.labelDelta

        # Calculate the x position for the given background bitmap
        # The bitmap will be center within the button.
        pos_x = (width - bw) // 2 + dx
        if bmp is not None:
            # Background bitmap is centered
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


class ImageToggleButton(GenBitmapToggleButton):
    """ Graphical toggle button with a hover effect. """

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

    def SetToggle(self, toggle):
        # Avoid wxPyDeadObject errors. Investigate further. Probably needs to
        # be handled in VigilantAttributeConnector. See comments there.
        if isinstance(self, ImageToggleButton):
            GenBitmapToggleButton.SetToggle(self, toggle)

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
        if bmp is not None:  # if the bitmap is used
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
            has_mask = bmp.GetMask() is not None
        else:
            bw = bh = 0  # no bitmap -> size is zero

        dc.SetFont(self.GetFont())
        if self.IsEnabled():
            dc.SetTextForeground(self.GetForegroundColour())
        else:
            dc.SetTextForeground(
                wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
            )

        label = self.GetLabel()
        tw, th = dc.GetTextExtent(label)  # size of text
        if not self.up:
            dx = dy = self.labelDelta

        pos_x = (width - bw) // 2 + dx
        if bmp is not None:
            dc.DrawBitmap(bmp, (width - bw) // 2, (height - bh) // 2, has_mask)

        if self.HasFlag(wx.ALIGN_CENTER):
            pos_x = pos_x + (bw - tw) // 2
        elif self.HasFlag(wx.ALIGN_RIGHT):
            pos_x = pos_x + bw - tw - self.padding_x
        else:
            pos_x = pos_x + self.padding_x

        dc.DrawText(label, pos_x, (height - th) // 2 + dy + 1)  # draw the text

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

        self.thumbnail_bmp = None

        # The number of pixels from the right that need to be kept clear so the
        # 'arrow pointer' is visible.
        self.pointer_offset = 16

        # The border that will be kept clear.
        self.thumbnail_border = 2
        self.thumbnail_size = wx.Size()

        self._calc_overlay_size()

    def _calc_overlay_size(self):
        btn_width, btn_height = self.GetSize()
        total_border = self.thumbnail_border * 2
        self.thumbnail_size.x = btn_width - total_border - self.pointer_offset
        self.thumbnail_size.y = btn_height - total_border

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

    def set_overlay_image(self, image):
        """ Scales and updates the image on the button

        :param image: (wx.Image or None) Image to be displayed or a default stock image.

        """

        if image:
            # image doesn't have the same aspect ratio as the actual thumbnail
            # => rescale and crop on the center
            scaled_img = img.wxImageScaleKeepRatio(image, self.thumbnail_size,
                                                   wx.IMAGE_QUALITY_HIGH)
        else:
            # black image
            scaled_img = wx.EmptyImage(*self.thumbnail_size)

        self.thumbnail_bmp = wx.BitmapFromImage(scaled_img)
        self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        """ Draw method called by the `OnPaint` event handler """
        ImageTextToggleButton.DrawLabel(self, dc, width, height, dx, dy)

        if self.thumbnail_bmp is not None:
            # logging.debug("Painting overlay")
            dc.DrawBitmap(self.thumbnail_bmp,
                          self.thumbnail_border,
                          self.thumbnail_border,
                          True)


class TabButton(ImageTextToggleButton):
    """ Simple graphical tab switching button """

    def __init__(self, *args, **kwargs):
        ImageTextToggleButton.__init__(self, *args, **kwargs)

        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)

        self.fg_color_def = "#E5E5E5"
        self.fg_color_high = "#FFFFFF"
        self.fg_color_notify = FG_COLOUR_HIGHLIGHT

        self.notification = False

    def _highlight(self, on):
        if on:
            if self.notification:
                self.SetForegroundColour(self.fg_color_notify)
            else:
                self.SetForegroundColour(self.fg_color_high)
        else:
            if self.notification:
                self.SetForegroundColour(self.fg_color_notify)
            else:
                self.SetForegroundColour(self.fg_color_def)

    def on_focus(self, evt):
        if self.notification:
            self.notification = False
        self._highlight(True)
        evt.Skip()

    def on_kill_focus(self, evt):
        self._highlight(False)
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

    def notify(self, on):
        """ Indicate a change to the button's related tab by visually altering it """

        f = self.GetFont()

        if on:
            self.SetForegroundColour(self.fg_color_notify)
            f.SetWeight(wx.BOLD)
            self.notification = True
        else:
            self.SetForegroundColour(self.fg_color_def)
            f.SetWeight(wx.NORMAL)
            self.notification = False

        self.SetFont(f)
        self.Refresh()


class GraphicRadioButton(ImageTextToggleButton):
    """ Simple graphical button that can be used to construct radio button sets
    """

    def __init__(self, *args, **kwargs):
        self.value = kwargs.pop('value', None)
        if 'label' not in kwargs:
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

        BMP_EMPTY = imgdata.getemptyBitmap()

        brush = wx.Brush(self.colour)
        pen = wx.Pen(self.colour)
        bmp = BMP_EMPTY.GetSubBitmap(wx.Rect(0, 0, BMP_EMPTY.GetWidth(), BMP_EMPTY.GetHeight()))
        mdc = wx.MemoryDC()
        mdc.SelectObject(bmp)
        mdc.SetBrush(brush)
        mdc.SetPen(pen)
        mdc.DrawRectangle(4, 4, 10, 10)
        mdc.SelectObject(wx.NullBitmap)

        self.SetBitmapLabel(bmp)

        if self.use_hover:
            BMP_EMPTY_H = imgdata.getempty_hBitmap()
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
        self.Disable()

    def set_choices(self, choices):
        """ Set the choices available to the user

        :param choices: [(string, function reference),..]
        """
        if choices:
            self.Enable()
        else:
            self.Disable()

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
        self.Enable()

    def remove_choice(self, label):
        """ Remove the choice associated with the name `1abel` """
        menu_item, cb, ce = self.choices.pop(label)
        self.menu.RemoveItem(menu_item)

        if not self.choices:
            self.Disable()

    def show_menu(self, evt):
        """ Show the popup menu, when there are choices available. """
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
