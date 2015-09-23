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
from operator import xor
import wx
import wx.lib.buttons as wxbuttons

from odemis.gui import FG_COLOUR_HIGHLIGHT
from odemis.gui.util.img import wxImageScaleKeepRatio
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
            16: {
                'off': imgdata.btn_def_16,
                'on': imgdata.btn_def_16_a,
            },
            24: {
                'off': imgdata.btn_def_24,
                'on': imgdata.btn_def_24_a,
            },
            32: {
                'off': imgdata.btn_def_32,
                'on': imgdata.btn_def_32_a,
            },
            48: {
                'off': imgdata.btn_def_48,
                'on': imgdata.btn_def_48_a,
            },
        },
        'blue': {
            'text_colour': wx.WHITE,
            'text_col_dis': "#AAAAAA",
            16: {
                'off': imgdata.btn_blue_16,
                'on': imgdata.btn_blue_16_a,
            },
            24: {
                'off': imgdata.btn_blue_24,
                'on': imgdata.btn_blue_24_a,
            },
            32: {
                'off': imgdata.btn_blue_32,
                'on': imgdata.btn_blue_32_a,
            },
            48: {
                'off': imgdata.btn_blue_48,
                'on': imgdata.btn_blue_48_a,
            },
        },
    }

    def __init__(self, *args, **kwargs):
        """
        :param parent: (wx.Window) parent window
        :param bitmap: (wx.Bitmap) optional default button face. Is this is not provided, the
            height parameter must be set, and the default faces will be used.
        :param height: (int) optional height parameter that determines the button height and what
            button face is used to render the button. *Only* use this when button faces are not
            explicity provided.
        :param face_colour: (str) optional name of the colour of the button faces to be used.
            Corresponds to the colour keys in the button faces dictionary.
        :param icon: (wx.Bitmap) con to display on the button
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
        if not xor(self.height is None, bmpLabel is None):
            raise ValueError("Either 'height' or 'bitmap' must be provided! Not both of neither.")

        # Fetch the button face colour
        self.face_colour = kwargs.pop('face_colour', 'def') or 'def'

        self.bmpSelectedHover = None

        # Set the icons
        self.icon = kwargs.pop('icon', None)
        self.icon_on = kwargs.pop('icon_on', None)

        # Call the super class constructor
        super(BtnMixin, self).__init__(*args, **kwargs)

        # Clear the hovering attributes
        self.bmpHover = None
        self.hovering = None

        # Previous size, used to check if bitmaps should be recreated
        self.previous_size = (0, 0)
        self.colour_set = False

        # Set the font size to the default. This will be overridden if another font (size) is
        # defined in the XRC file
        if self.height:
            font = self.GetFont()
            # print font.GetNativeFontInfoDesc()
            font.SetPointSize(self.btns['font_size'][self.height])
            self.SetFont(font)

    def SetForegroundColour(self, color):
        super(BtnMixin, self).SetForegroundColour(color)
        self.colour_set = True

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
    def _create_bitmap(bmp, size, bg_color):
        """ Create a full sized button from the provided button face

        The base buttons should have a width of 3 * 'section_width' (in pixels). The first
        'section_width' pixels determine the left edage, the 'section_width' last pixels the
        right edge, and the 'section_width' pixels in the middle are stretched to fill the button.

        :Note:
            Blit and StretchBlit were attempted first, but efforts to make them work with alpha
            channels in the base images were unsuccessful.

        """

        section_width = 4
        # Base button face should have a width of 3 * 'section_width'
        btn_width, btn_height = size

        new_img = bmp.ConvertToImage()

        l = new_img.GetSubImage(
            (0, 0, section_width, btn_height)
        ).ConvertToBitmap()

        m = new_img.GetSubImage(
            (section_width, 0, section_width, btn_height)
        ).Rescale(
            btn_width - section_width * 2,
            btn_height
        ).ConvertToBitmap()

        r = new_img.GetSubImage(
            (section_width * 2, 0, section_width, btn_height)
        ).ConvertToBitmap()

        src_dc = wx.MemoryDC()
        src_dc.SelectObjectAsSource(bmp)

        dst_bmp = wx.EmptyBitmap(btn_width, btn_height)
        dst_dc = wx.MemoryDC()
        dst_dc.SelectObject(dst_bmp)
        dst_dc.SetBackground(wx.Brush(bg_color))
        dst_dc.Clear()

        dst_dc.DrawBitmap(l, 0, 0, True)
        dst_dc.DrawBitmap(m, section_width, 0, True)
        dst_dc.DrawBitmap(r, btn_width - section_width, 0)

        return dst_bmp

    def _reset_bitmaps(self):
        if self.height:
            self.bmpLabel = self._create_main_bitmap()
            self.bmpHover = self.bmpDisabled = self.bmpSelected = None

    def _create_main_bitmap(self):
        return self._create_bitmap(
            self.btns[self.face_colour][self.height]['off'].GetBitmap(),
            (self.Size.x, self.height),
            self.Parent.GetBackgroundColour()
        )

    def _create_hover_bitmap(self):

        if not self.height:
            return self.bmpLabel

        image = self.btns[self.face_colour][self.height]['off'].GetImage()
        darken_image(image, 1.1)
        return self._create_bitmap(
            wx.BitmapFromImage(image),
            (self.Size.x, self.height),
            self.Parent.GetBackgroundColour()
        )

    def _create_disabled_bitmap(self):
        if not self.height:
            return self.bmpLabel

        image = self.btns[self.face_colour][self.height]['off'].GetBitmap().ConvertToImage()
        darken_image(image, 0.8)
        return self._create_bitmap(
            wx.BitmapFromImage(image),
            (self.Size.x, self.height or self.Size.y),
            self.Parent.GetBackgroundColour()
        )

    def _create_active_bitmap(self):

        if not self.height:
            return self.bmpLabel

        return self._create_bitmap(
            self.btns[self.face_colour][self.height]['on'].GetBitmap(),
            (self.Size.x, self.height),
            self.Parent.GetBackgroundColour()
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

        self.DrawIco(dc, width, height)
        self.DrawText(dc, width, height)

    def DrawIco(self, dc, width, height, dx=0, dy=0):

        if not self.up and self.icon_on:
            icon = self.icon_on
        elif self.icon:
            icon = self.icon
        else:
            return

        bw, bh = self.Size
        if not self.up:
            dx = dy = self.labelDelta

        pos_x = (width - bw) // 2 + dx
        pos_x += icon.GetWidth() + self.padding_x
        pos_y = (height // 2) - (icon.GetHeight() // 2)
        dc.DrawBitmap(icon, self.padding_x + dx, pos_y + dy)

    def DrawText(self, dc, width, height, dx=0, dy=0):
        # Determine font and font colour
        dc.SetFont(self.GetFont())

        if self.colour_set:
            text_colour = self.GetForegroundColour()
        else:
            if self.IsEnabled():
                text_colour = self.btns[self.face_colour]['text_colour']
            else:
                text_colour = self.btns[self.face_colour]['text_col_dis']

        dc.SetTextForeground(text_colour)

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
                pos_x = (bw - tw) // 2
            elif self.HasFlag(wx.ALIGN_RIGHT):
                pos_x = bw - tw - self.padding_x
            else:
                pos_x += self.padding_x

            # draw the text
            dc.DrawText(label, pos_x, (height - th) // 2 + dy)


class ImageButton(BtnMixin, wxbuttons.GenBitmapButton):
    pass


class ImageToggleButtonImageButton(BtnMixin, wxbuttons.GenBitmapTextToggleButton):
    pass


class ImageTextButton(BtnMixin, wxbuttons.GenBitmapTextButton):
    pass


class ImageTextToggleButton(BtnMixin, wxbuttons.GenBitmapTextToggleButton):
    pass


class GraphicRadioButton(ImageTextToggleButton):
    """ Simple graphical button that can be used to construct radio button sets
    """

    def __init__(self, *args, **kwargs):
        self.value = kwargs.pop('value', None)
        if 'label' not in kwargs and self.value:
            kwargs['label'] = u"%g" % self.value
        ImageTextToggleButton.__init__(self, *args, **kwargs)

    def OnLeftDown(self, event):
        """ This event handler is fired on left mouse button events, but it ignores those events
        if the button is already active.
        """
        if not self.IsEnabled() or not self.up:
            return
        self.saveUp = self.up
        self.up = not self.up
        self.CaptureMouse()
        self.SetFocus()
        self.Refresh()


class TabButton(GraphicRadioButton):
    """ Simple graphical tab switching button """

    labelDelta = 0

    def __init__(self, *args, **kwargs):

        kwargs['style'] = kwargs.get('style', 0) | wx.ALIGN_CENTER
        kwargs['bitmap'] = imgdata.tab_inactive.Bitmap

        super(TabButton, self).__init__(*args, **kwargs)

        self.bmpHover = imgdata.tab_hover.Bitmap
        self.bmpSelected = imgdata.tab_active.Bitmap
        self.bmpDisabled = imgdata.tab_hover.Bitmap

        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)

        self.fg_color_def = "#E5E5E5"
        self.SetForegroundColour(self.fg_color_def)
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

    def highlight(self, on):
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
        kwargs['bitmap'] = imgdata.empty.Bitmap

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

        bmp = wx.EmptyBitmap(*self.Size)
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
            bmp = wx.EmptyBitmap(*self.Size)
            mdc = wx.MemoryDC()
            mdc.SelectObject(bmp)

            mdc.DrawBitmap(imgdata.empty_h.Bitmap, 0, 0)
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

        kwargs['bitmap'] = imgdata.preview_block.Bitmap

        super(ViewButton, self).__init__(*args, **kwargs)

        self.bmpHover = imgdata.preview_block_a.Bitmap
        self.bmpSelected = imgdata.preview_block_a.Bitmap
        self.bmpDisabled = imgdata.preview_block.Bitmap

        self.thumbnail_bmp = None

        # The number of pixels from the right that need to be kept clear so the
        # 'arrow pointer' is visible.
        self.pointer_offset = 16
        self.thumbnail_size = wx.Size()
        self._calc_overlay_size()

    def _calc_overlay_size(self):
        """ Calculate the size the thumbnail overlay  should be """
        btn_width, btn_height = imgdata.preview_block_a.Bitmap.Size
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
            scaled_img = wx.EmptyImage(*self.thumbnail_size)

        self.thumbnail_bmp = wx.BitmapFromImage(scaled_img)
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


class PopupImageButton(ImageTextButton):
    """ This class describes a grahical button with an associated popup menu """

    labelDelta = 0

    def __init__(self, *args, **kwargs):

        kwargs['bitmap'] = imgdata.stream_add.Bitmap

        super(PopupImageButton, self).__init__(*args, **kwargs)

        self.SetForegroundColour(wx.WHITE)

        self.bmpSelected = imgdata.stream_add_a.Bitmap
        self.bmpHover = imgdata.stream_add_h.Bitmap
        img = imgdata.stream_add.GetImage()
        darken_image(img, 0.8)
        self.bmpDisabled = wx.BitmapFromImage(img)

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
