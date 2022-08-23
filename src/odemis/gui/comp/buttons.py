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

import logging
import math
import numpy
from odemis.gui import FG_COLOUR_HIGHLIGHT
from odemis.gui import img
from odemis.gui.util.img import wxImageScaleKeepRatio
import wx

import wx.lib.buttons as wxbuttons


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

    mltp (0<=float<=1): the smaller the darker. Values above 1 are possible, but
      if the result becomes > 255, it will overflow and appear dark instead of
      bright white.
    """
    data = numpy.asarray(image.GetDataBuffer())
    numpy.multiply(data, mltp, out=data, casting="unsafe")


class BtnMixin(object):
    """ Mixin class meant to be used with wx.lib.button's generic buttons """

    # Displacement of button text and icon when pushing the button (px)
    labelDelta = 1
    # Padding of button text and icon
    padding_x = 8
    padding_y = 1

    # Default button faces
    btns = {
        'font_size': {
            16: 8,
            24: 10,
            32: 11,
            48: 14,
        },
        'def': {
            'text_colour': "#1A1A1A",
            'text_col_dis': "#676767",
        },
        'blue': {
            'text_colour': wx.WHITE,
            'text_col_dis': "#AAAAAA",
        },
        'red': {
            'text_colour': wx.WHITE,
            'text_col_dis': "#AAAAAA",
        },
        'orange': {
            'text_colour': wx.WHITE,
            'text_col_dis': "#AAAAAA",
        },
    }

    def __init__(self, *args, **kwargs):
        """
        :param parent: (wx.Window) parent window
        :param bitmap: (wx.Bitmap) optional default button face. Is this is not provided, the
            height parameter must be set, and the default faces will be used.
        :param height: (int) optional height parameter that determines the button height and what
            button face is used to render the button. *Only* use this when button faces are not
            explicitly provided.
        :param face_colour: (str) optional name of the colour of the button faces to be used.
            Corresponds to the colour keys in the button faces dictionary.
        :param icon: (wx.Bitmap) icon to display on the button
        :param icon_on: (wx.Bitmap) optional icon to display on the button when it is pressed down
        :param size: (int, int) optional explicit button size. If not provided, the size of the
            default button face will be used.
        """

        # Set style defaults, hiding borders, and exactly fitting the button bitmaps
        kwargs['style'] = kwargs.get('style', 0) | wx.NO_BORDER | wx.BU_EXACTFIT

        # Get the bitmap, if one is provided
        bmpLabel = kwargs.get('bitmap', None)
        kwargs['bitmap'] = bmpLabel

        # Set the default size to that of the provided bitmap, if any
        if 'size' not in kwargs and bmpLabel:
            kwargs['size'] = bmpLabel.Size

        # Get the height, if provided
        self.height = kwargs.pop('height', None)

        # Only height or bitmap must be provided, not both
        if (self.height is None) == (bmpLabel is None):  # not Xor
            raise ValueError("Either 'height' or 'bitmap' must be provided! Not both or neither.")

        # Fetch the button face colour
        self.face_colour = kwargs.pop('face_colour', 'def') or 'def'

        self.bmpSelectedHover = None

        # Set the icons
        self.icon = kwargs.pop('icon', None)
        self.icon_on = kwargs.pop('icon_on', None)

        self.fg_colour_set = False

        # Call the super class constructor
        super(BtnMixin, self).__init__(*args, **kwargs)

        # Must be after the super, as GenButton set background to the default
        # system background
        self.SetBackgroundColour(self.Parent.GetBackgroundColour())

        if bmpLabel is None:
            self.bmpLabel = self._create_main_bitmap()

        # Clear the hovering attributes
        self.bmpHover = None
        self.hovering = None

        # Previous size, used to check if bitmaps should be recreated
        self.previous_size = self.Size

        # Set the font size to the default. This will be overridden if another font (size) is
        # defined in the XRC file
        if self.height:
            font = self.GetFont()
            # print font.GetNativeFontInfoDesc()
            font.SetPointSize(self.btns['font_size'][self.height])
            self.SetFont(font)

        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self._on_capture_lost)

    def set_face_colour(self, color):
        if color in self.btns:
            self.face_colour = color
            self._reset_bitmaps()
            self.Refresh()
        else:
            raise ValueError("Unknown button colour")

    def SetForegroundColour(self, color):
        super(BtnMixin, self).SetForegroundColour(color)
        self.fg_colour_set = True

    def SetIcon(self, icon):
        icon_set = self.icon is not None
        self.icon = icon

        if not icon_set:
            self.SetBestSize(self.DoGetBestSize())

        self.Refresh()

    def SetIconOn(self, icon_on):
        icon_set = self.icon_on is not None
        self.icon_on = icon_on

        if not icon_set:
            self.SetBestSize(self.DoGetBestSize())

        self.Refresh()

    def OnSize(self, evt):
        if self.Size != self.previous_size and self.height:
            self._reset_bitmaps()
            self.previous_size = self.Size

        super(BtnMixin, self).OnSize(evt)

    def SetLabel(self, label):
        super(BtnMixin, self).SetLabel(label)
        self.Refresh()

    def _GetLabelSize(self):
        """ Overridden method from Genbutton for determining the button size """

        if self.height:
            width = self.padding_x * 2
            height = self.height - 2  # Compensate for the 2px that the super class adds
        elif self.bmpLabel:
            width, height = self.bmpLabel.Size
        else:
            logging.warning("Guessing button size")
            width = height = 16

        label = self.GetLabel()

        if label:
            width += self.GetTextExtent(label)[0]

        if self.icon:
            width += (self.padding_x if label else 0) + self.icon.GetWidth()

        return width, height, True if label else False

    @staticmethod
    def _create_bitmap(img, size, bg_color):
        """ Create a full sized button from the provided button face

        img (wx.Image): the base image. It should have a width of 3 *
        'section_width' (in pixels). The first 'section_width' pixels determine
        the left edge, the 'section_width' last pixels the right edge, and the
        'section_width' pixels in the middle are stretched to fill the button.
        """
        # Note: Blit and StretchBlit were attempted first, but efforts to make
        # them work with alpha channels in the base images were unsuccessful.

        # TODO: just rely on transparency, and do not use the bg_color?
        # In such case, it should be fine to use numpy to concatenate the image

        section_width = 4
        # Base button face should have a width of 3 * 'section_width'
        btn_width, btn_height = size

        l = img.GetSubImage(
            (0, 0, section_width, btn_height)
        ).ConvertToBitmap()

        m = img.GetSubImage(
            (section_width, 0, section_width, btn_height)
        ).Rescale(
            max(btn_width - section_width * 2, 1),
            btn_height
        ).ConvertToBitmap()

        r = img.GetSubImage(
            (section_width * 2, 0, section_width, btn_height)
        ).ConvertToBitmap()

        dst_bmp = wx.Bitmap(btn_width, btn_height)
        dst_dc = wx.MemoryDC()
        dst_dc.SelectObject(dst_bmp)
        dst_dc.SetBrush(wx.Brush(bg_color))
        # Transparency issue (caused by the bitmap dst_bmp only accepting black or white as background)
        # There are 2 other solutions for fixing the transparency issue, but they only work on windows
        # the reason for -1, -1 and +2 on width and height is not known but it can be easily determined by:
        # 0,0,btn_width,btn_height is missing a pixel all around the rectangle
        # and -1,-1,btn_width,btn_height misses 2 bottom pixel rows and two right column rows
        # The other two ways (that work only on windows):
        # 1. using an wx.Image with alpha (initAlpha + transparent mask) for bg
        # 2. using fillcolour (on black)
        dst_dc.DrawRectangle(-1, -1, btn_width + 2, btn_height + 2)

        dst_dc.DrawBitmap(l, 0, 0, True)
        dst_dc.DrawBitmap(m, section_width, 0, True)
        dst_dc.DrawBitmap(r, btn_width - section_width, 0, True)

        return dst_bmp

    def _reset_bitmaps(self):
        if self.height and self.Size.x:
            self.bmpLabel = self._create_main_bitmap()
            self.bmpHover = self.bmpDisabled = self.bmpSelected = None

    def _getBtnImage(self, colour, height, state):
        """
        colour (str)
        height (int)
        state (str): "on" or "off"
        """
        if state == "off":
            fn = "button/btn_%s_%d.png" % (colour, height)
        else:
            fn = "button/btn_%s_%d_a.png" % (colour, height)
        return img.getImage(fn)

    def _getBtnBitmap(self, colour, height, state):
        return wx.Bitmap(self._getBtnImage(colour, height, state))

    def _create_main_bitmap(self):
        return self._create_bitmap(
            self._getBtnImage(self.face_colour, self.height, 'off'),
            (self.Size.x, self.height),
            self.GetBackgroundColour()
        )

    def _create_hover_bitmap(self):

        if not self.height:
            return self.bmpLabel

        image = self._getBtnImage(self.face_colour, self.height, 'off')
        darken_image(image, 1.1)
        return self._create_bitmap(
            image,
            (self.Size.x, self.height),
            self.GetBackgroundColour()
        )

    def _create_disabled_bitmap(self):
        if not self.height:
            return self.bmpLabel

        image = self._getBtnImage(self.face_colour, self.height, 'off')
        darken_image(image, 0.8)
        return self._create_bitmap(
            image,
            (self.Size.x, self.height or self.Size.y),
            self.GetBackgroundColour()
        )

    def _create_active_bitmap(self):

        if not self.height:
            return self.bmpLabel

        return self._create_bitmap(
           self._getBtnImage(self.face_colour, self.height, 'on'),
            (self.Size.x, self.height),
            self.GetBackgroundColour()
        )

    def InitOtherEvents(self):
        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)

    def OnEnter(self, evt):
        """ Event handler that fires when the mouse cursor enters the button """
        self.hovering = True
        self.Refresh()

    def OnLeave(self, evt):
        """ Event handler that fires when the mouse cursor leaves the button """
        self.hovering = False
        self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):

        bmp = self.bmpLabel

        if not self.IsEnabled():
            if not self.bmpDisabled:
                self.bmpDisabled = self._create_disabled_bitmap()
            # TODO: should have a different disabled image whether the button
            # up or down (selected), in case of Toggle button
            bmp = self.bmpDisabled
        elif not self.up:
            if not self.bmpSelected:
                self.bmpSelected = self._create_active_bitmap()
            if self.bmpSelectedHover and self.hovering:
                bmp = self.bmpSelectedHover
            else:
                bmp = self.bmpSelected
        elif self.hovering:
            if not self.bmpHover:
                self.bmpHover = self._create_hover_bitmap()
            bmp = self.bmpHover

        brush = self.GetBackgroundBrush(dc)
        brush.SetColour(self.Parent.BackgroundColour)
        dc.SetBackground(brush)
        dc.Clear()

        dc.DrawBitmap(bmp, 0, 0, True)
        self.DrawText(dc, width, height)

    def DrawText(self, dc, width, height, dx=0, dy=0, text_colour=None):

        # Determine font and font colour
        dc.SetFont(self.GetFont())

        if text_colour is None:
            if self.fg_colour_set:
                text_colour = self.GetForegroundColour()
            else:
                if self.IsEnabled():
                    text_colour = self.btns[self.face_colour]['text_colour']
                else:
                    text_colour = self.btns[self.face_colour]['text_col_dis']

        dc.SetTextForeground(text_colour)

        icon = None

        if not self.up and self.icon_on:
            icon = self.icon_on
        elif self.icon:
            icon = self.icon

        # Get the label text
        label = self.GetLabel()

        if not self.up:
            dx = dy = self.labelDelta

        if label:
            # Determine the size of the text
            text_width, text_height = dc.GetTextExtent(label)  # size of text

            if self.HasFlag(wx.ALIGN_CENTER):
                text_x = (width - text_width) // 2
            elif self.HasFlag(wx.ALIGN_RIGHT):
                text_x = width - text_width - self.padding_x
            else:
                text_x = self.padding_x

            if icon:
                if self.HasFlag(wx.ALIGN_CENTER):
                    half = (icon.GetWidth() + self.padding_x) // 2
                    icon_x = text_x - half
                    text_x += half
                elif self.HasFlag(wx.ALIGN_RIGHT):
                    icon_x = width - icon.GetWidth() - self.padding_x
                    text_x -= icon.GetWidth() + self.padding_x
                else:
                    icon_x = self.padding_x
                    text_x += icon_x + icon.GetWidth()

                icon_y = (height // 2) - (icon.GetHeight() // 2)
                dc.DrawBitmap(icon, icon_x + dx, icon_y + dy)

            # draw the text
            text_y = math.ceil((height - text_height) / 2)
            dc.DrawText(label, text_x + dx, text_y + dy)

        elif icon:
            if self.HasFlag(wx.ALIGN_CENTER):
                icon_x = width // 2 - icon.GetWidth() // 2
            elif self.HasFlag(wx.ALIGN_RIGHT):
                icon_x = width - self.padding_x - icon.GetWidth()
            else:
                icon_x = self.padding_x

            icon_y = (height // 2) - (icon.GetHeight() // 2)
            dc.DrawBitmap(icon, icon_x + dx, icon_y + dy)

    def Enable(self, enable=True):
        # Fixes a bug in GenButton: when it's disabled, OnLeftUp doesn't release
        # the mouse capture. So it'd be holding the mouse forever when doing:
        # Enable(True), OnLeftDown(), Enable(False), OnLeftUp().
        # => Release the capture when disabling the button
        # See ImageStateButton for example of safer OnLeftDown/Up()
        if not enable and enable != self.IsEnabled() and self.HasCapture():
            logging.debug("Button disabled while holding mouse capture")
            self.ReleaseMouse()
        super(BtnMixin, self).Enable(enable)

        # FIXME: if the button triggers the display of another window, there
        # is risk that clicking 1.5 time keeps the capture forever (at least on
        # Linux GTK 3, with wxPython 4.0.1). A 1.5 click means: process left down,
        # left up, (other window starts), left down, (other window appears).
        # Closing the other window with the keyboard brings back mouse control.
        # This doesn't seem to happen with pre-made dialogs, but it does for our
        # modal dialogs created from XRC.
        # No idea how to deal with this for now...

        # It's partly fixed in wxPython commit 883d093cda (released in v4.0.7),
        # by introducing the function just below. It avoids getting an assertion
        # error, but it's still possible to keep the capture.
    def _on_capture_lost(self, evt):
        # Can be deleted once we only support wxPython v4.0.7+ .
        logging.debug("Mouse capture lost: %s", evt)
        self.up = True
        self.Refresh()
        self.Update()


class ImageButton(BtnMixin, wxbuttons.GenBitmapButton):
    padding_x = 2


class ImageToggleButton(BtnMixin, wxbuttons.GenBitmapTextToggleButton):
    padding_x = 2


class ImageTextButton(BtnMixin, wxbuttons.GenBitmapTextButton):
    pass


class ImageTextToggleButton(BtnMixin, wxbuttons.GenBitmapTextToggleButton):

    def __init__(self, *args, **kwargs):
        self.active_colour = kwargs.pop("active_colour", None)
        super(ImageTextToggleButton, self).__init__(*args, **kwargs)

    def DrawText(self, dc, width, height, dx=0, dy=0):
        if self.active_colour and self.GetValue():
            text_colour = self.active_colour
        else:
            text_colour = None
        super(ImageTextToggleButton, self).DrawText(dc, width, height, dx=0, dy=0, text_colour=text_colour)


class ImageStateButton(ImageToggleButton):
    """
    Multi-state graphical button that can switch between any number of states/images.
    The default bitmap image is used for state None, and bmpSelected* contain a
    list of images for state 1 and more. The values the state can take is None and
    0 to the number of images - 1 in bmpSelected.
    """
    def __init__(self, *args, **kwargs):
        super(ImageStateButton, self).__init__(*args, **kwargs)
        self.state = None

    def DrawLabel(self, dc, width, height, dx=0, dy=0):

        if not self.IsEnabled():
            if not self.bmpDisabled:
                self.bmpDisabled = self._create_disabled_bitmap()
            bmp = self.bmpDisabled
        elif self.hovering:
            if self.state is None:
                if not self.bmpHover:
                    self.bmpHover = self._create_hover_bitmap()
                bmp = self.bmpHover
            else:
                if self.bmpSelectedHover:
                    bmp = self.bmpSelectedHover[self.state]
                else:
                    # TODO: create hover if not present
                    bmp = self.bmpSelected[self.state]
        else:
            logging.debug("Drawing for state %s", self.state)
            if self.state is None:
                bmp = self.bmpLabel
            elif not self.bmpSelected:
                logging.warning("No bmpSelected for ImageStateButton")
                bmp = self._create_active_bitmap()
            else:
                bmp = self.bmpSelected[self.state]

        brush = self.GetBackgroundBrush(dc)
        brush.SetColour(self.Parent.BackgroundColour)
        dc.SetBackground(brush)
        dc.Clear()

        dc.DrawBitmap(bmp, 0, 0, True)
        self.DrawText(dc, width, height)

    def OnLeftDown(self, event):
        if not self.IsEnabled():
            return
        self.saveUp = self.up
        self.saveState = self.state
        self.up = not self.up
        if self.state is None:
            self.state = 0
        elif self.state == len(self.bmpSelected) - 1:
            self.state = None
        else:
            self.state += 1
        self.nextState = self.state
        if self.HasCapture():
            # It seems that sometimes the capture is not released, at least
            # don't go too crazy in such case
            logging.warning("Mouse was already captured on left down")
        else:
            self.CaptureMouse()
        self.SetFocus()
        self.Refresh()

    def OnLeftUp(self, event):
        if self.HasCapture():
            self.ReleaseMouse()
            if not self.IsEnabled():
                return
            self.Refresh()
            if self.up != self.saveUp:
                self.Notify()

    def OnKeyDown(self, event):
        event.Skip()

    def OnMotion(self, event):
        if not self.IsEnabled():
            return
        if event.LeftIsDown() and self.HasCapture():
            x, y = event.Position
            w, h = self.ClientSize
            if 0 <= x < w and 0 <= y < h:
                if self.state != self.nextState:
                    self.state = self.nextState
                    self.up = not self.saveUp
                    self.Refresh()
            else:
                if self.state != self.saveState:
                    self.state = self.saveState
                    self.up = self.saveUp
                    self.Refresh()
            return
        event.Skip()

    def OnKeyUp(self, event):
        if self.hasFocus and event.GetKeyCode() == ord(" "):
            self.up = not self.up
            if self.state is None:
                self.state = 0
            elif self.state == len(self.bmpSelected) - 1:
                self.state = None
            else:
                self.state += 1
            self.Notify()
            self.Refresh()
        event.Skip()

    def SetState(self, state):
        if state is not None and not 0 <= state < len(self.bmpSelected):
            raise ValueError("State %s is invalid" % (state,))
        self.state = state
        self.Refresh()
    SetValue = SetState

    def GetState(self):
        """
        return (None or int): None if first image/state selected, otherwise
         0 to N-1 for the state corresponding the bmpSelected image
        """
        return self.state
    GetValue = GetState


class GraphicRadioButton(ImageTextToggleButton):
    """ Simple graphical button that can be used to construct radio button sets
    """

    def __init__(self, *args, **kwargs):
        self.value = kwargs.pop('value', None)
        if 'label' not in kwargs and self.value:
            kwargs['label'] = u"%g" % self.value
        super(GraphicRadioButton, self).__init__(*args, **kwargs)

    def OnLeftDown(self, event):
        """ This event handler is fired on left mouse button events, but it ignores those events
        if the button is already active.
        """
        if not self.IsEnabled() or not self.up:
            return
        self.saveUp = self.up
        self.up = not self.up
        if self.HasCapture():
            # It seems that sometimes the capture is not released, at least
            # don't go too crazy in such case
            logging.warning("Mouse was already captured on left down")
        else:
            self.CaptureMouse()
        self.SetFocus()
        self.Refresh()


class TabButton(GraphicRadioButton):
    """ Simple graphical tab switching button """

    labelDelta = 0

    def __init__(self, *args, **kwargs):

        kwargs['style'] = kwargs.get('style', 0) | wx.ALIGN_CENTER
        kwargs['bitmap'] = img.getBitmap("tab_inactive.png")

        super(TabButton, self).__init__(*args, **kwargs)

        self.bmpHover = img.getBitmap("tab_hover.png")
        self.bmpSelected = img.getBitmap("tab_active.png")
        self.bmpDisabled = img.getBitmap("tab_disabled.png")

        self.fg_color_normal = "#FFFFFF"
        self.fg_color_dis = "#E0E0E0"
        self.SetForegroundColour(self.fg_color_normal)

        self.highlighted = False

    def Enable(self, enable=True):
        if enable:
            if self.highlighted:
                self.SetForegroundColour(FG_COLOUR_HIGHLIGHT)
            else:
                self.SetForegroundColour(self.fg_color_normal)
        else:
            self.SetForegroundColour(self.fg_color_dis)
        return super(TabButton, self).Enable(enable)

    def highlight(self, on):
        """ Indicate a change to the button's related tab by visually altering it """
        self.highlighted = on

        f = self.GetFont()
        if on:
            f.SetWeight(wx.BOLD)
        else:
            f.SetWeight(wx.NORMAL)

        self.SetFont(f)
        self.Enable(self.IsEnabled())  # update label colour
        self.Refresh()

    def DrawLabel(self, *args, **kwargs):
        super(TabButton, self).DrawLabel(*args, **kwargs)


class ColourButton(ImageButton):
    """ An ImageButton that has a single colour background that can be altered """

    # The default colour for the colour button
    DEFAULT_COLOR = (0, 0, 0)

    def __init__(self, *args, **kwargs):
        # The initial color to display
        self.colour = kwargs.pop('colour', None) or self.DEFAULT_COLOR
        # Determine if a hover effect needs to be used
        self.use_hover = kwargs.pop('use_hover', False)

        kwargs['size'] = kwargs.get('size', None) or (18, 18)
        kwargs['bitmap'] = img.getBitmap("empty.png")

        super(ColourButton, self).__init__(*args, **kwargs)

        self.set_colour(self.colour)

    def _create_colour_bitmap(self):
        # TODO: Remove this little 'hack', which was put in place because of bg color issues,
        # resulting from the use of 'SetBackgroundStyle' in StreamPanelHeader
        if self.use_hover:
            bg_brush = wx.Brush(self.Parent.Parent.GetBackgroundColour())
        else:
            bg_brush = wx.Brush(self.Parent.GetBackgroundColour())

        brush = wx.Brush(self.colour)
        pen = wx.Pen(self.colour)

        bmp = wx.Bitmap(*self.Size)
        mdc = wx.MemoryDC()
        mdc.SelectObject(bmp)

        mdc.SetBackground(bg_brush)
        mdc.Clear()

        mdc.SetBrush(brush)
        mdc.SetPen(pen)
        mdc.DrawRectangle(4, 4, 10, 10)
        mdc.SelectObject(wx.NullBitmap)

        return bmp

    def set_colour(self, colour):
        """ Change the background colour of the button

        :param colour: (3-tuple of 0<=int<=255) RGB values

        """

        self.colour = colour

        self.bmpLabel = self.bmpSelected = self.bmpDisabled = self._create_colour_bitmap()

        if self.use_hover:
            bmp = wx.Bitmap(*self.Size)
            mdc = wx.MemoryDC()
            mdc.SelectObject(bmp)

            mdc.DrawBitmap(img.getBitmap("empty_h.png"), 0, 0)
            mdc.DrawBitmap(self.bmpLabel.GetSubBitmap((4, 4, 10, 10)), 4, 4)

            mdc.SelectObject(wx.NullBitmap)
            self.bmpHover = bmp
        else:
            self.bmpHover = self.bmpLabel

        self.Refresh()

    def get_colour(self):
        """ Get the current background colour of the button

        :rtype: (string) Hex colour value
        """
        return self.colour


class ViewButton(GraphicRadioButton):
    """ The ViewButton class describes a toggle button that has an image overlay
    that depicts a thumbnail view of the view panels.

    Since the aspect ratio of views is dynamic, the ratio of the ViewButton will
    also need to be dynamic, but it will be limited to the height only, so the
    width will remain static.

    """

    thumbnail_border = 2

    def __init__(self, *args, **kwargs):
        """
        :param parent: (wx.Window) parent window
        :param size: (int, int) button size
        """

        kwargs['bitmap'] = img.getBitmap("preview_block.png")

        super(ViewButton, self).__init__(*args, **kwargs)

        self.bmpHover = img.getBitmap("preview_block_a.png")
        self.bmpSelected = img.getBitmap("preview_block_a.png")
        self.bmpDisabled = img.getBitmap("preview_block.png")

        self.thumbnail_bmp = None

        # The number of pixels from the right that need to be kept clear so the
        # 'arrow pointer' is visible.
        self.pointer_offset = 16
        self.thumbnail_size = wx.Size()
        self._calc_overlay_size()

    def _calc_overlay_size(self):
        """ Calculate the size the thumbnail overlay  should be """
        btn_width, btn_height = img.getBitmap("preview_block_a.png").Size
        total_border = self.thumbnail_border * 2
        self.thumbnail_size.x = max(1, btn_width - total_border - self.pointer_offset)
        self.thumbnail_size.y = max(1, btn_height - total_border)

    def set_overlay_image(self, image):
        """ Scales and updates the overlay image on the ViewButton

        :param image: (wx.Image or None) Image to be displayed or a default stock image.

        """

        if image:
            # image doesn't have the same aspect ratio as the actual thumbnail
            # => rescale and crop on the center
            scaled_img = wxImageScaleKeepRatio(image, self.thumbnail_size, wx.IMAGE_QUALITY_HIGH)
        else:
            # black image
            scaled_img = wx.Image(*self.thumbnail_size)

        self.thumbnail_bmp = wx.Bitmap(scaled_img)
        self.Refresh()

    def DrawLabel(self, dc, width, height, dx=0, dy=0):
        """ Draw method called by the `OnPaint` event handler """

        # Draw the base button
        super(ViewButton, self).DrawLabel(dc, width, height, dx, dy)

        # Draw the overlay image
        if self.thumbnail_bmp is not None:

            # logging.debug("Painting overlay")
            dc.DrawBitmap(self.thumbnail_bmp,
                          self.thumbnail_border,
                          self.thumbnail_border,
                          False)


class PlusImageButton(ImageTextButton):
    """ This class describes a graphical button that has a plus icon to the left """

    labelDelta = 0

    def __init__(self, *args, **kwargs):

        kwargs['bitmap'] = img.getBitmap("overview_add.png")

        super(PlusImageButton, self).__init__(*args, **kwargs)

        self.SetForegroundColour(wx.WHITE)

        self.bmpSelected = img.getBitmap("overview_add_a.png")
        self.bmpHover = img.getBitmap("overview_add_h.png")
        btn_img = img.getImage("overview_add.png")
        darken_image(btn_img, 0.8)
        self.bmpDisabled = wx.Bitmap(btn_img)


class PopupImageButton(ImageTextButton):
    """ This class describes a graphical button with an associated popup menu """

    labelDelta = 0

    def __init__(self, *args, **kwargs):

        kwargs['bitmap'] = img.getBitmap("stream_add.png")

        super(PopupImageButton, self).__init__(*args, **kwargs)

        self.SetForegroundColour(wx.WHITE)

        self.bmpSelected = img.getBitmap("stream_add_a.png")
        self.bmpHover = img.getBitmap("stream_add_h.png")
        btn_img = img.getImage("stream_add.png")
        darken_image(btn_img, 0.8)
        self.bmpDisabled = wx.Bitmap(btn_img)

        self.choices = {}
        self.menu = wx.Menu()
        self.Bind(wx.EVT_BUTTON, self.show_menu)
        self.Disable()

    def set_choices(self, choices):
        """ Set the choices available to the user

        :param choices: {string: function reference,..}
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
        self.menu.Append(menu_item)
        self.Enable()

    def remove_choice(self, label):
        """ Remove the choice associated with the name `1abel` """
        menu_item, cb, ce = self.choices.pop(label)
        self.menu.Remove(menu_item)

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
