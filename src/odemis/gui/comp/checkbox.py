# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2015 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


The class in this module is an adaptation of http://wiki.wxpython.org/CreatingCustomControls

"""
from __future__ import division

import wx
from wx.lib.imageutils import grayOut
import odemis.gui.img.data as imgdata


class CheckBox(wx.PyControl):
    """
    A custom class that replicates some of the functionalities of wx.CheckBox, while being
    completely owner-drawn with a nice check bitmaps.

    """

    def __init__(self, parent, id=wx.ID_ANY, label="", pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.NO_BORDER, validator=wx.DefaultValidator,
                 name="CustomCheckBox"):
        """
        Default class constructor.

        @param parent: Parent window. Must not be None.
        @param id: CustomCheckBox identifier. A value of -1 indicates a default value.
        @param label: Text to be displayed next to the checkbox.
        @param pos: CustomCheckBox position. If the position (-1, -1) is specified
                    then a default position is chosen.
        @param size: CustomCheckBox size. If the default size (-1, -1) is specified
                     then a default size is chosen.
        @param style: not used in this demo, CustomCheckBox has only 2 state
        @param validator: Window validator.
        @param name: Window name.
        """

        # Ok, let's see why we have used wx.PyControl instead of wx.Control.
        # Basically, wx.PyControl is just like its wxWidgets counterparts
        # except that it allows some of the more common C++ virtual method
        # to be overridden in Python derived class. For CustomCheckBox, we
        # basically need to override DoGetBestSize and AcceptsFocusFromKeyboard

        wx.PyControl.__init__(self, parent, id, pos, size, style, validator, name)

        # Initialize our cool bitmaps
        self._bitmaps = None
        self.initialize_bitmaps()

        # wxPen used to draw the focus indicator
        self._focus_ind_pen = None

        # Initialize the focus pen colour/dashes, for faster drawing later
        self.initialize_colours()

        # By default, we start unchecked
        self._checked = False

        # Set the spacing between the check bitmap and the label to 3 by default.
        # This can be changed using SetSpacing later.
        self._spacing = 3
        self._hasFocus = False

        # Ok, set the wx.PyControl label, its initial size (formerly known an
        # SetBestFittingSize), and inherit the attributes from the standard
        # wx.CheckBox
        self.SetLabel(label)
        self.SetInitialSize(size)
        self.InheritAttributes()

        # Bind the events related to our control: first of all, we use a
        # combination of wx.BufferedPaintDC and an empty handler for
        # wx.EVT_ERASE_BACKGROUND (see later) to reduce flicker
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_ERASE_BACKGROUND, self.OnEraseBackground)

        # Then we want to monitor user clicks, so that we can switch our
        # state between checked and unchecked
        self.Bind(wx.EVT_LEFT_DOWN, self.OnMouseClick)
        if wx.Platform == '__WXMSW__':
            # MSW Sometimes does strange things...
            self.Bind(wx.EVT_LEFT_DCLICK,  self.OnMouseClick)

        # We want also to react to keyboard keys, namely the
        # space bar that can toggle our checked state
        self.Bind(wx.EVT_KEY_UP, self.OnKeyUp)

        # Then, we react to focus event, because we want to draw a small
        # dotted rectangle around the text if we have focus
        # This might be improved!!!
        self.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        self.Bind(wx.EVT_KILL_FOCUS, self.OnKillFocus)

    def initialize_bitmaps(self):
        """ Initializes the check bitmaps. """

        # We keep 4 bitmaps for CustomCheckBox, depending on the
        # checking state (Checked/UnCkecked) and the control
        # state (Enabled/Disabled).
        self._bitmaps = {
            "CheckedEnable": imgdata.getbtn_16x16Bitmap(),
            "UnCheckedEnable": imgdata.getbtn_16x16Bitmap(),
            "CheckedDisable": grayOut(imgdata.getbtn_16x16Image()),
            "UnCheckedDisable": grayOut(imgdata.getbtn_16x16Image())
        }

    def initialize_colours(self):
        """ Initializes the focus indicator pen. """

        text_ctrl = self.GetForegroundColour()

        if wx.Platform == "__WXMAC__":
            self._focus_ind_pen = wx.Pen(text_ctrl, 1, wx.SOLID)
        else:
            self._focus_ind_pen = wx.Pen(text_ctrl, 1, wx.USER_DASH)
            self._focus_ind_pen.SetDashes([1,1])
            self._focus_ind_pen.SetCap(wx.CAP_BUTT)

        self.SetBackgroundColour(self.Parent.GetBackgroundColour())

    def get_current_bitmap(self):
        """ Returns the appropriated bitmap depending on the checking state

        State is determined by (Checked/UnChecked) and the control state (Enabled/Disabled)

        """

        if self.IsEnabled():
            # So we are Enabled
            if self.IsChecked():
                # We are Checked
                return self._bitmaps["CheckedEnable"]
            else:
                # We are UnChecked
                return self._bitmaps["UnCheckedEnable"]
        else:
            # Poor CustomCheckBox, Disabled and ignored!
            if self.IsChecked():
                return self._bitmaps["CheckedDisable"]
            else:
                return self._bitmaps["UnCheckedDisable"]

    def SetLabel(self, label):
        """ Sets the CustomCheckBox text label and updates the control's size to exactly fit the
        label plus the bitmap.

        """

        if isinstance(label, wx.StaticText):
            label.Bind(wx.EVT_LEFT_UP, self.toggle)
        else:
            wx.PyControl.SetLabel(self, label)

            # The text label has changed, so we must recalculate our best size
            # and refresh ourselves.
            self.InvalidateBestSize()
            self.Refresh()

    def SetFont(self, font):
        """
        Sets the CustomCheckBox text font and updates the control's size to
        exactly fit the label plus the bitmap.

        """

        wx.PyControl.SetFont(self, font)

        # The font for text label has changed, so we must recalculate our best
        # size and refresh ourselves.
        self.InvalidateBestSize()
        self.Refresh()

    def DoGetBestSize(self):
        """ Overridden base class virtual.  Determines the best size of the control based on the
        label size, the bitmap size and the current font.

        """

        # Retrieve our properties: the text label, the font and the check
        # bitmap
        label = self.GetLabel()
        font = self.GetFont()
        bitmap = self.get_current_bitmap()

        if not font:
            # No font defined? So use the default GUI font provided by the system
            font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)

        # Set up a wx.ClientDC. When you don't have a dc available (almost
        # always you don't have it if you are not inside a wx.EVT_PAINT event),
        # use a wx.ClientDC (or a wx.MemoryDC) to measure text extents
        dc = wx.ClientDC(self)
        dc.SetFont(font)

        # Measure our label
        test_width, test_height = dc.GetTextExtent(label)

        # Retrieve the check bitmap dimensions
        bitmap_width, bitmap_height = bitmap.GetWidth(), bitmap.GetHeight()

        # Get the spacing between the check bitmap and the text
        spacing = self.GetSpacing()

        # Ok, we're almost done: the total width of the control is simply
        # the sum of the bitmap width, the spacing and the text width,
        # while the height is the maximum value between the text width and
        # the bitmap width
        total_width = bitmap_width + spacing + test_width
        total_height = max(test_height, bitmap_height)

        best = wx.Size(total_width, total_height)

        # Cache the best size so it doesn't need to be calculated again,
        # at least until some properties of the window change
        self.CacheBestSize(best)

        return best

    def AcceptsFocusFromKeyboard(self):
        """ Overridden base class virtual """
        # We can accept focus from keyboard, obviously
        return True

    def AcceptsFocus(self):
        """ Overridden base class virtual. """
        # It seems to me that wx.CheckBox does not accept focus with mouse
        # but please correct me if I am wrong!
        return False

    def HasFocus(self):
        """ Returns whether or not we have the focus. """

        # We just returns the _hasFocus property that has been set in the
        # wx.EVT_SET_FOCUS and wx.EVT_KILL_FOCUS event handlers.
        return self._hasFocus

    def SetForegroundColour(self, colour):
        """ Overridden base class virtual. """

        wx.PyControl.SetForegroundColour(self, colour)

        # We have to re-initialize the focus indicator per colour as it should
        # always be the same as the foreground colour
        self.initialize_colours()
        self.Refresh()

    def SetBackgroundColour(self, colour):
        """ Overridden base class virtual. """
        wx.PyControl.SetBackgroundColour(self, colour)

        # We have to refresh ourselves
        self.Refresh()

    def Enable(self, enable=True):
        """ Enables/Disables CustomCheckBox. """

        wx.PyControl.Enable(self, enable)

        # We have to refresh ourselves, as our state changed
        self.Refresh()

    def GetDefaultAttributes(self):
        """ Overridden base class virtual.

        By default we should use the same font/colour attributes as the native wx.CheckBox.

        """
        return wx.CheckBox.GetClassDefaultAttributes()

    def ShouldInheritColours(self):
        """
        Overridden base class virtual.  If the parent has non-default
        colours then we want this control to inherit them.
        """

        return True

    def SetSpacing(self, spacing):
        """ Sets a new spacing between the check bitmap and the text. """

        self._spacing = spacing

        # The spacing between the check bitmap and the text has changed,
        # so we must recalculate our best size and refresh ourselves.
        self.InvalidateBestSize()
        self.Refresh()

    def GetSpacing(self):
        """ Returns the spacing between the check bitmap and the text. """

        return self._spacing

    def GetValue(self):
        """ Returns the state of CustomCheckBox, True if checked, False otherwise. """
        return self._checked

    def IsChecked(self):
        """ This is just a maybe more readable synonym for GetValue: just as the latter,
        it returns True if the CustomCheckBox is checked and False otherwise.

        """
        return self._checked

    def toggle(self, evt):
        self.SendCheckBoxEvent()

    def SetValue(self, state):
        """ Sets the CustomCheckBox to the given state. This does not cause a
        wx.wxEVT_COMMAND_CHECKBOX_CLICKED event to get emitted.

        """

        self._checked = state

        # Refresh ourselves: the bitmap has changed
        self.Refresh()

    def OnKeyUp(self, event):
        """ Handles the wx.EVT_KEY_UP event for CustomCheckBox. """

        if event.GetKeyCode() == wx.WXK_SPACE:
            # The spacebar has been pressed: toggle our state
            self.SendCheckBoxEvent()
            event.Skip()
            return

        event.Skip()

    def OnSetFocus(self, event):
        """ Handles the wx.EVT_SET_FOCUS event for CustomCheckBox. """

        self._hasFocus = True

        # We got focus, and we want a dotted rectangle to be painted
        # around the checkbox label, so we refresh ourselves
        self.Refresh()

    def OnKillFocus(self, event):
        """ Handles the wx.EVT_KILL_FOCUS event for CustomCheckBox. """

        self._hasFocus = False

        # We lost focus, and we want a dotted rectangle to be cleared
        # around the checkbox label, so we refresh ourselves
        self.Refresh()

    def OnPaint(self, event):
        """ Handles the wx.EVT_PAINT event for CustomCheckBox. """

        # If you want to reduce flicker, a good starting point is to
        # use wx.BufferedPaintDC.
        dc = wx.BufferedPaintDC(self)

        # Is is advisable that you don't overcrowd the OnPaint event
        # (or any other event) with a lot of code, so let's do the
        # actual drawing in the Draw() method, passing the newly
        # initialized wx.BufferedPaintDC
        self.Draw(dc)

    def Draw(self, dc):
        """
        Actually performs the drawing operations, for the bitmap and
        for the text, positioning them centered vertically.
        """

        # Get the actual client size of ourselves
        width, height = self.GetClientSize()

        if not width or not height:
            # Nothing to do, we still don't have dimensions!
            return

        # Initialize the wx.BufferedPaintDC, assigning a background
        # colour and a foreground colour (to draw the text)
        back_colour = self.GetBackgroundColour()
        back_brush = wx.Brush(back_colour, wx.SOLID)
        dc.SetBackground(back_brush)
        dc.Clear()

        if self.IsEnabled():
            dc.SetTextForeground(wx.BLACK)
        else:
            dc.SetTextForeground(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))

        font = self.GetFont()
        font.SetPointSize(14)
        dc.SetFont(font)

        # Get the text label for the checkbox, the associated check bitmap
        # and the spacing between the check bitmap and the text
        label = self.GetLabel()
        bitmap = self.get_current_bitmap()
        spacing = self.GetSpacing()

        # Measure the text extent and get the check bitmap dimensions
        text_width, text_height = dc.GetTextExtent(label)
        bitmap_width, bitmap_height = bitmap.GetWidth(), bitmap.GetHeight()

        # Position the bitmap centered vertically
        bitmap_x_pos = 0
        bitmap_y_pos = (height - bitmap_height)/2

        # Position the text centered vertically
        text_x_pos = bitmap_width + spacing
        text_y_pos = (height - text_height)/2

        # Draw the bitmap on the DC
        dc.DrawBitmap(bitmap, bitmap_x_pos, bitmap_y_pos, True)

        # Draw the text
        if self.IsChecked():
            dc.DrawText(u"✓", 1, -3)

        # Let's see if we have keyboard focus and, if this is the case,
        # we draw a dotted rectangle around the text (Windows behavior,
        # I don't know on other platforms...)
        if self.HasFocus():
            # Yes, we are focused! So, now, use a transparent brush with
            # a dotted black pen to draw a rectangle around the text
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.SetPen(self._focus_ind_pen)
            dc.DrawRectangle(text_x_pos, text_y_pos, text_width, text_height)

    def OnEraseBackground(self, event):
        """ Handles the wx.EVT_ERASE_BACKGROUND event for CustomCheckBox. """

        # This is intentionally empty, because we are using the combination
        # of wx.BufferedPaintDC + an empty OnEraseBackground event to
        # reduce flicker
        pass

    def OnMouseClick(self, event):
        """ Handles the wx.EVT_LEFT_DOWN event for CustomCheckBox. """

        if not self.IsEnabled():
            # Nothing to do, we are disabled
            return

        self.SendCheckBoxEvent()
        event.Skip()

    def SendCheckBoxEvent(self):
        """ Actually sends the wx.wxEVT_COMMAND_CHECKBOX_CLICKED event. """

        # This part of the code may be reduced to a 3-liner code
        # but it is kept for better understanding the event handling.
        # If you can, however, avoid code duplication; in this case,
        # I could have done:
        #
        # self._checked = not self.IsChecked()
        # check_event = wx.CommandEvent(wx.wxEVT_COMMAND_CHECKBOX_CLICKED,
        #                              self.GetId())
        # check_event.SetInt(int(self._checked))
        if self.IsChecked():
            # We were checked, so we should become unchecked
            self._checked = False
            # Fire a wx.CommandEvent: this generates a
            # wx.wxEVT_COMMAND_CHECKBOX_CLICKED event that can be caught by the
            # developer by doing something like:
            # MyCheckBox.Bind(wx.EVT_CHECKBOX, self.OnCheckBox)
            check_event = wx.CommandEvent(wx.wxEVT_COMMAND_CHECKBOX_CLICKED, self.GetId())
            # Set the integer event value to 0 (we are switching to unchecked state)
            check_event.SetInt(0)
        else:
            # We were unchecked, so we should become checked
            self._checked = True
            check_event = wx.CommandEvent(wx.wxEVT_COMMAND_CHECKBOX_CLICKED, self.GetId())
            # Set the integer event value to 1 (we are switching to checked state)
            check_event.SetInt(1)

        # Set the originating object for the event (ourselves)
        check_event.SetEventObject(self)

        # Watch for a possible listener of this event that will catch it and
        # eventually process it
        self.GetEventHandler().ProcessEvent(check_event)

        # Refresh ourselves: the bitmap has changed
        self.Refresh()
