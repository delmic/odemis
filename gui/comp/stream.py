# -*- coding: utf-8 -*-

""" This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and
manipulate various data streams coming from the microscope.


@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.

"""

# import logging as log

import wx
import wx.combo

import odemis.gui.img.data as img

from odemis.gui.log import log
from odemis.gui.comp.buttons import ImageButton, ImageToggleButton, \
    ImageTextToggleButton, ColourButton
from odemis.gui.comp.text import SuggestTextCtrl, IntegerTextCtrl, \
    UnitIntegerCtrl
from odemis.gui.util.conversion import wave2hex

TEST_STREAM_LST = ["Aap", u"nöot", "noot", "mies", "kees", "vuur",
                   "quantummechnica", "Repelsteeltje", "", "XXX", "a", "aa",
                   "aaa", "aaaa", "aaaaa", "aaaaaa", "aaaaaaa"]

# Short-cuts to button icons

BMP_ARR_DOWN = img.getarr_downBitmap()
BMP_ARR_IRGHT = img.getarr_rightBitmap()

BMP_REM = img.getico_rem_strBitmap()
BMP_REM_H = img.getico_rem_str_hBitmap()

BMP_EYE_CLOSED = img.getico_eye_closedBitmap()
BMP_EYE_CLOSED_H = img.getico_eye_closed_hBitmap()
BMP_EYE_OPEN = img.getico_eye_openBitmap()
BMP_EYE_OPEN_H = img.getico_eye_open_hBitmap()

BMP_PAUSE = img.getico_pauseBitmap()
BMP_PAUSE_H = img.getico_pause_hBitmap()
BMP_PLAY = img.getico_playBitmap()
BMP_PLAY_H = img.getico_play_hBitmap()

BMP_CONTRAST = img.getbtn_contrastBitmap()
BMP_CONTRAST_A = img.getbtn_contrast_aBitmap()


class Slider(wx.Slider):
    """ This custom Slider class was implemented so it would not capture
    mouse wheel events, which were causing problems when the user wanted
    to scroll through the main fold panel bar.
    """

    def __init__(self, *args, **kwargs):
        wx.Slider.__init__(self, *args, **kwargs)
        self.Bind(wx.EVT_MOUSEWHEEL, self.pass_to_scollwin)

    def pass_to_scollwin(self, evt):
        """ This event handler prevents anything from happening to the Slider on
        MOUSEWHEEL events and passes the event on to any parent ScrolledWindow
        """

        # Find the parent ScolledWindow
        win = self.Parent
        while win and not isinstance(win, wx.ScrolledWindow):
            win = win.Parent

        # If a ScrolledWindow was found, pass on the event
        if win:
            win.GetEventHandler().ProcessEvent(evt)


class Expander(wx.PyControl):
    """ An Expander is a header/button control at the top of a StreamPanelEntry.
    It provides a means to expand or collapse the StreamPanelEntry, as wel as a label
    and various buttons offering easy access to much used functionality.

    Structure:

        + Expander
        |-- ImageButton     (remove stream button)
        |-- StaticText / SuggestTextCtrl (stream label)
        |-- ImageToggleButton (show/hide button)
        |-- ImageToggleButton (capture/pause button)

    The triangular fold icons are drawn in a separate routine.

    """

    def __init__(self, parent, label="", wid=wx.ID_ANY, pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.NO_BORDER):
        wx.PyControl.__init__(self, parent, wid, pos, size, style)

        # This style *needs* to be set on in MS Windows
        self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        self._parent = parent
        self._label = label
        self._label_ctrl = None

        self._label_color = parent.GetForegroundColour()

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # ===== Fold icons

        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(BMP_ARR_DOWN)
        self._foldIcons.Add(BMP_ARR_IRGHT)

        # ===== Remove button

        self._btn_rem = ImageButton(self, -1, BMP_REM, (10, 8), (18, 18),
                                    background_parent=parent)
        self._btn_rem.SetBitmaps(BMP_REM_H)
        self._btn_rem.SetToolTipString("Remove stream")

        # ===== Visibility button

        self._btn_vis = ImageToggleButton(self, -1, BMP_EYE_CLOSED, (10, 8),
                                          (18, 18), background_parent=parent)
        self._btn_vis.SetBitmaps(BMP_EYE_CLOSED_H, BMP_EYE_OPEN, BMP_EYE_OPEN_H)
        self._btn_vis.SetToolTipString("Show/hide stream")

        # ===== Play button

        self._btn_play = ImageToggleButton(self, -1, BMP_PAUSE, (10, 8),
                                           (18, 18), background_parent=parent)
        self._btn_play.SetBitmaps(BMP_PAUSE_H, BMP_PLAY, BMP_PLAY_H)
        self._btn_play.SetToolTipString("Capture stream")


        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        self._sz.Add(self._btn_rem, 0, wx.ALL | wx.ALIGN_CENTRE_VERTICAL, 8)
        self._sz.AddStretchSpacer(1)
        self._sz.Add(self._btn_vis, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)
        self._sz.Add(self._btn_play, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 72)

        self.SetSizer(self._sz)

        self._sz.Fit(self)
        self.Layout()


    def DoGetBestSize(self, *args, **kwargs):
        """ Return the best size, which is the width of the parent and the
        height or the content (determined through the sizer).
        """
        width, dummy = self._parent.GetSize()
        dummy, height = self._sz.GetSize()

        return wx.Size(width, height)

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for the Expander class.
        :param `event`: a `wx.SizeEvent` event to be processed.
        """
        width = self._parent.GetSize().GetWidth()
        self.SetSize((width, -1))
        self.Layout()
        self.Refresh()

    def OnDrawExpander(self, dc):
        """ This method draws the expand/collapse icons.
        It needs to be called from the parent's paint event
        handler.
        """
        wndRect = self.GetRect()
        drw = wndRect.GetRight() - 16 - 15
        self._foldIcons.Draw(self._parent._collapsed, dc, drw,
                             (wndRect.GetHeight() - 16) / 2,
                             wx.IMAGELIST_DRAW_TRANSPARENT)

    def get_label(self):
        return self._label

class FixedExpander(Expander):
    """ Expander for FixedStreamPanelEntrys """

    def __init__(self, parent, label, wid=wx.ID_ANY):
        Expander.__init__(self, parent, label, wid)

        self._label_ctrl = wx.StaticText(self, -1, label)
        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1,
                        wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

class CustomExpander(Expander):
    """ Expander for CustomStreamPanelEntrys """

    def __init__(self, parent, label, wid=wx.ID_ANY):
        Expander.__init__(self, parent, label, wid)

        self.stream_color = None
        self._btn_color = ColourButton(self, -1,
                                size=(18,18),
                                colour=self.stream_color,
                                background_parent=parent,
                                use_hover=True)

        self._btn_color.SetToolTipString("Select colour")

        self._sz.Insert(2, self._btn_color, 0,
                        wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

        self._label_ctrl = SuggestTextCtrl(self, id= -1, value=label)
        self._label_ctrl.SetChoices(TEST_STREAM_LST)
        self._label_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self._label_ctrl.SetForegroundColour("#2FA7D4")

        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1,
                        wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

    def set_stream_color(self, color=None):
        """ Update the color button to reflect the provided color """
        self._btn_color.set_color(color)

    def get_stream_color(self):
        return self._btn_color.get_color()

class StreamPanelEntry(wx.PyPanel):
    """ The StreamPanelEntry super class, a special case collapsible pane.

    The StreamPanelEntry consists of the following widgets:

        + StreamPanelEntry
        |--- FixedExpander
        |--+ Panel
           |-- ImageTextToggleButton
           |-- StaticText
           |-- Slider
           |-- IntegerTextCtrl
           |-- StaticText
           |-- Slider
           |-- IntegerTextCtrl

    The expander_class class attribute contains the Expander subclass to be
    used when constructing an object.

    Most of the component's construction is done in the finalize() method, so
    we can allow for a delay. This is necessary when construction the component
    through an XML handler.
    """

    expander_class = FixedExpander

    def __init__(self, parent, wid=wx.ID_ANY, label="no label",
                 pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, agwStyle=0,
                 validator=wx.DefaultValidator, name="CollapsiblePane",
                 collapsed=True):

        wx.PyPanel.__init__(self, parent, wid, pos, size, style, name)

        self._label = label
        self._collapsed = True
        self._agwStyle = agwStyle | wx.CP_NO_TLW_RESIZE #|wx.CP_GTK_EXPANDER


        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.Hide()

        self._gbs = wx.GridBagSizer(3, 5)
        self._gbs.AddGrowableCol(1, 1)
        self._panel.SetSizer(self._gbs)

        self._expander = None

        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # Process our custom 'collapsed' parameter.
        if not collapsed:
            self.collapse(collapsed)

    # ==== Event Handlers

    def on_remove(self, evt):
        log.debug("Removing stream panel '%s'" % self._expander.get_label())
        fpb_item = self.Parent
        self.Destroy()
        fpb_item.Layout()

    def on_visibility(self, evt):
        if self._expander._btn_vis.up:
            log.debug("Hide stream")
        else:
            log.debug("Show stream")

    def on_play(self, evt):
        if self._expander._btn_play.up:
            log.debug("Pause stream")
        else:
            log.debug("Update stream")

    # END ==== Event Handlers

    def get_state(self):
        state = {}
        state['label'] = self._label
        state['visible'] = self._expander._btn_vis.GetValue()
        state['capturing'] = self._expander._btn_play.GetValue()

    def collapse(self, collapse=True):
        """ Collapses or expands the pane window.
        """

        if self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse
        self.Thaw()

        # update button label
        # NB: this must be done after updating our "state"
        #self._expander.SetLabel(self._label)

        #self.on_status_change(self.GetBestSize())

    def finalize(self):
        """ This method builds all the child controls
        A delay was needed in order for all the settings to be loaded from the
        XRC file (i.e. Font and background/foregroung colors).
        """

        # ====== Add an expander button

        self.set_button(self.expander_class(self, self._label))
        self._sz.Add(self._expander, 0, wx.EXPAND)


        self._expander.Bind(wx.EVT_PAINT, self.on_draw_expander)
        if wx.Platform == "__WXMSW__":
            self._expander.Bind(wx.EVT_LEFT_DCLICK, self.on_button)

        self._expander.Bind(wx.EVT_LEFT_UP, self.OnToggle)

        # ====== Build panel controls

        self._panel.SetBackgroundColour(self.GetBackgroundColour())
        self._panel.SetForegroundColour("#DDDDDD")
        self._panel.SetFont(self.GetFont())

        # ====== Top row, auto contrast toggle button

        self._btn_auto_contrast = ImageTextToggleButton(self._panel, -1,
                                                BMP_CONTRAST, label="Auto",
                                                size=(68, 26))
        self._btn_auto_contrast.SetBitmaps(bmp_sel=BMP_CONTRAST_A)
        self._btn_auto_contrast.SetForegroundColour("#000000")
        self._gbs.Add(self._btn_auto_contrast, (0, 0), flag=wx.LEFT, border=34)


        # ====== Second row, brightness label, slider and value

        lbl_brightness = wx.StaticText(self._panel, -1, "brightness:")
        self._gbs.Add(lbl_brightness, (1, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
                      border=34)

        self._sld_brightness = Slider(
            self._panel, -1, 128, 0, 255, (30, 60), (-1, 10),
            wx.SL_HORIZONTAL)

        self._gbs.Add(self._sld_brightness, (1, 1), flag=wx.EXPAND)


        self._txt_brightness = IntegerTextCtrl(self._panel, -1,
                str(self._sld_brightness.GetValue()),
                style=wx.NO_BORDER,
                size=(30, -1),
                min_val=self._sld_brightness.GetMin(),
                max_val=self._sld_brightness.GetMax())
        self._txt_brightness.SetForegroundColour("#2FA7D4")
        self._txt_brightness.SetBackgroundColour(self.GetBackgroundColour())

        self._gbs.Add(self._txt_brightness, (1, 2),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)

        # ====== Third row, brightness label, slider and value

        lbl_contrast = wx.StaticText(self._panel, -1, "contrast:")
        self._gbs.Add(lbl_contrast, (2, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)

        self._sld_contrast = Slider(
            self._panel, -1, 128, 0, 255, (30, 60), (-1, 10),
            wx.SL_HORIZONTAL)

        self._gbs.Add(self._sld_contrast, (2, 1), flag=wx.EXPAND)


        self._txt_contrast = IntegerTextCtrl(self._panel, -1,
                str(self._sld_contrast.GetValue()),
                style=wx.NO_BORDER,
                size=(30, -1),
                min_val=self._sld_contrast.GetMin(),
                max_val=self._sld_contrast.GetMax())
        self._txt_contrast.SetForegroundColour("#2FA7D4")
        self._txt_contrast.SetBackgroundColour(self.GetBackgroundColour())

        self._gbs.Add(self._txt_contrast, (2, 2),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)

        # ==== Bind events

        # Expander

        self._expander._btn_rem.Bind(wx.EVT_BUTTON, self.on_remove)
        self._expander._btn_vis.Bind(wx.EVT_BUTTON, self.on_visibility)
        self._expander._btn_play.Bind(wx.EVT_BUTTON, self.on_play)

        # Panel controls

        self._sld_brightness.Bind(wx.EVT_COMMAND_SCROLL, self.on_brightness_slide)
        self._txt_brightness.Bind(wx.EVT_TEXT_ENTER, self.on_brightness_entered)
        self._txt_brightness.Bind(wx.EVT_CHAR, self.on_brightness_key)

        self._sld_contrast.Bind(wx.EVT_COMMAND_SCROLL, self.on_contrast_slide)
        self._txt_contrast.Bind(wx.EVT_TEXT_ENTER, self.on_contrast_entered)
        self._txt_contrast.Bind(wx.EVT_CHAR, self.on_contrast_key)

        self._btn_auto_contrast.Bind(wx.EVT_BUTTON, self.on_toggle_autocontrast)



    def on_toggle_autocontrast(self, evt):
        enabled = not self._btn_auto_contrast.GetToggle()

        self._sld_brightness.Enable(enabled)
        self._txt_brightness.Enable(enabled)

        self._sld_contrast.Enable(enabled)
        self._txt_contrast.Enable(enabled)

    def on_brightness_key(self, evt):
        key = evt.GetKeyCode()

        if key in (wx.WXK_UP, wx.WXK_DOWN):
            self._sld_brightness.SetValue(self._txt_brightness.GetValue())

        evt.Skip()


    def on_brightness_entered(self, evt):
        self._sld_brightness.SetValue(int(self._txt_brightness.GetValue()))
        evt.Skip()

    def on_brightness_slide(self, evt):
        self._txt_brightness.SetValue(str(self._sld_brightness.GetValue()))

    def on_contrast_entered(self, evt):
        self._sld_contrast.SetValue(int(self._txt_contrast.GetValue()))
        evt.Skip()

    def on_contrast_slide(self, evt):
        self._txt_contrast.SetValue(str(self._sld_contrast.GetValue()))

    def on_contrast_key(self, evt):
        key = evt.GetKeyCode()

        if key in (wx.WXK_UP, wx.WXK_DOWN):
            self._sld_contrast.SetValue(self._txt_contrast.GetValue())

        evt.Skip()

    def OnToggle(self, evt):
        """ Toggle the StreamPanelEntry

        Only toggle the view when the click was within the right 15% of the
        Expander, otherwise ignore it.
        """
        w = evt.GetEventObject().GetSize().GetWidth()

        if evt.GetX() > w * 0.85:
            self.collapse(not self._collapsed)
            # this change was generated by the user - send the event
            ev = wx.CollapsiblePaneEvent(self, self.GetId(), self._collapsed)
            self.GetEventHandler().ProcessEvent(ev)
        evt.Skip()

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for StreamPanelEntry
        """
        self.Layout()

    def on_button(self, event):
        """ Handles the wx.EVT_BUTTON event for StreamPanelEntry
        """

        if event.GetEventObject() != self._expander:
            event.Skip()
            return

        self.collapse(not self._collapsed)

        # this change was generated by the user - send the event
        ev = wx.CollapsiblePaneEvent(self, self.GetId(), self._collapsed)
        self.GetEventHandler().ProcessEvent(ev)

    def get_panel(self):
        """ Returns a reference to the pane window. Use the returned `wx.Window`
        as the parent of widgets to make them part of the collapsible area.
        """
        return self._panel

    def set_button(self, button):
        """ Assign a new expander button to the stream panel.

        :param `button`: can be the standard `wx.Button` or any of the generic
         implementations which live in `wx.lib.buttons`.
        """

        if self._expander:
            self._sz.Replace(self._expander, button)
            self.Unbind(wx.EVT_BUTTON, self._expander)
            self._expander.Destroy()

        self._expander = button
        self.SetLabel(button.GetLabel())
        self.Bind(wx.EVT_BUTTON, self.on_button, self._expander)

        if self._panel:
            self._expander.MoveBeforeInTabOrder(self._panel)
        self.Layout()


    def get_button(self):
        """ Returns the button associated with StreamPanelEntry. """
        return self._expander

    def on_draw_expander(self, event):
        """ Handles the ``wx.EVT_PAINT`` event for the stream panel.
        :note: This is a drawing routine to paint the GTK-style expander.
        """

        dc = wx.AutoBufferedPaintDC(self._expander)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        self._expander.OnDrawExpander(dc)

    def Layout(self, *args, **kwargs):
        """ Layout the StreamPanelEntry. """

        if not self._expander or not self._panel or not self._sz:
            return False     # we need to complete the creation first!

        oursz = self.GetSize()

        # move & resize the button and the static line
        self._sz.SetDimension(0, 0, oursz.GetWidth(),
                              self._sz.GetMinSize().GetHeight())
        self._sz.Layout()

        if not self._collapsed:
            # move & resize the container window
            yoffset = self._sz.GetSize().GetHeight()
            self._panel.SetDimensions(0, yoffset, oursz.x, oursz.y - yoffset)

            # this is very important to make the pane window layout show
            # correctly
            self._panel.Show()
            self._panel.Layout()

        return True

    def DoGetBestSize(self, *args, **kwargs):
        """ Gets the size which best suits the window: for a control, it would
        be the minimal size which doesn't truncate the control, for a panel -
        the same size as it would have after a call to `Fit()`.
        """

        # do not use GetSize() but rather GetMinSize() since it calculates
        # the required space of the sizer
        sz = self._sz.GetMinSize()

        # when expanded, we need more space
        if not self._collapsed:
            pbs = self._panel.GetBestSize()
            sz.width = max(sz.GetWidth(), pbs.x)
            sz.height = sz.y + pbs.y

        return sz

    # def Layout(self):
    #     pcp.PyCollapsiblePane.Layout(self)
    #     #self._panel.SetBackgroundColour(self.GetBackgroundColour())


class FixedStreamPanelEntry(StreamPanelEntry): #pylint: disable=R0901
    """ A pre-defined stream panel """

    expander_class = FixedExpander

    def __init__(self, *args, **kwargs):
        StreamPanelEntry.__init__(self, *args, **kwargs)

    def finalize(self):
        StreamPanelEntry.finalize(self)

class CustomStreamPanelEntry(StreamPanelEntry): #pylint: disable=R0901
    """ A stream panel which can be altered by the user """

    expander_class = CustomExpander

    def __init__(self, *args, **kwargs):
        StreamPanelEntry.__init__(self, *args, **kwargs)

        self._excitation = "200"
        self._emission = "200"

    def on_remove(self, evt):
        self._expander._label_ctrl.Destroy()
        StreamPanelEntry.on_remove(self, evt)

    def on_color_click(self, evt):
        # Remove the hover effect
        self._expander._btn_color.OnLeave(evt)

        dlg = wx.ColourDialog(self)

        if dlg.ShowModal() == wx.ID_OK:
            data = dlg.GetColourData()
            color_str = data.GetColour().GetAsString(wx.C2S_HTML_SYNTAX)
            log.debug("Colour %s selected", color_str)
            self._expander.set_stream_color(color_str)

    def finalize(self):
        """ The CustomStreamPanelEntry has a few extra controls in addition to the
        ones defined in the StreamPanelEntry class:

        + CustomStreamPanelEntry
        |--- CustomExpander
        |--+ Panel
           |-- .
           |-- .
           |-- .
           |-- StaticText
           |-- UnitIntegerCtrl
           |-- StaticText
           |-- UnitIntegerCtrl

        """
        StreamPanelEntry.finalize(self)

        lbl_excitation = wx.StaticText(self._panel, -1, "excitation:")
        self._gbs.Add(lbl_excitation, (3, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)

        self._txt_excitation = UnitIntegerCtrl(self._panel, -1, self._excitation,
                style=wx.NO_BORDER,
                size=(50, -1), min_val=200, max_val=1000, unit='nm')
        self._txt_excitation.SetForegroundColour("#2FA7D4")
        self._txt_excitation.SetBackgroundColour(self.GetBackgroundColour())

        self._txt_excitation.Bind(wx.EVT_TEXT, self.on_excitation_text)


        self._gbs.Add(self._txt_excitation, (3, 1),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)


        self._btn_excitation = ColourButton(self._panel, -1,
                                     size=(18,18),
                                     colour=wave2hex(self._excitation),
                                     background_parent=self._panel)
        self._btn_excitation.SetToolTipString("Wavelength colour")

        self._gbs.Add(self._btn_excitation, (3, 2),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)



        lbl_emission = wx.StaticText(self._panel, -1, "emission:")
        self._gbs.Add(lbl_emission, (4, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)

        self._txt_emission = UnitIntegerCtrl(self._panel, -1, self._emission,
                style=wx.NO_BORDER,
                size=(50, -1), min_val=200, max_val=1000, unit='nm')
        self._txt_emission.SetForegroundColour("#2FA7D4")
        self._txt_emission.SetBackgroundColour(self.GetBackgroundColour())

        self._txt_emission.Bind(wx.EVT_TEXT, self.on_emission_text)

        self._gbs.Add(self._txt_emission, (4, 1),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)

        self._btn_emission = ColourButton(self._panel, -1,
                                     size=(18,18),
                                     colour=wave2hex(self._emission),
                                     background_parent=self._panel)
        self._btn_emission.SetToolTipString("Wavelength colour")

        self._gbs.Add(self._btn_emission, (4, 2),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)



        self._expander._btn_color.Bind(wx.EVT_BUTTON, self.on_color_click)

    def on_excitation_text(self, evt):
        log.debug("Excitation changed")
        obj = evt.GetEventObject()
        colour = wave2hex(obj.GetValue())
        log.debug("Changing color to %s", colour)
        self._btn_excitation.set_colour(colour)


    def on_emission_text(self, evt):
        log.debug("Emission changed")
        obj = evt.GetEventObject()
        colour = wave2hex(obj.GetValue())
        log.debug("Changing color to %s", colour)
        self._btn_emission.set_colour(colour)

class StreamPanel(wx.Panel):
    """docstring for StreamPanelEntry"""

    def __init__(self):

        pre = wx.PrePanel()
        # the Create step is done later by XRC.
        self.PostCreate(pre)
        self.Bind(wx.EVT_WINDOW_CREATE, self.OnCreate)

    def OnCreate(self, event):
        self.Unbind(wx.EVT_WINDOW_CREATE)
        # Do all extra initialization here